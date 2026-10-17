"""Turn the journey-level train table into one row of metrics per origin station.

Inputs  (data/processed/): service_level.parquet (src.schema), station_area.parquet (src.geo),
                           weather_hourly.parquet (src.weather, optional)
Outputs (data/processed/): service_enriched.parquet  journey rows + peak label + weather (for models)
                           station_metrics.parquet   one row per origin: quality + quantity + deprivation
Run:  python -m src.metrics

Definitions
  late            : not cancelled and delay_min >= late_threshold_min (config)
  late_rate       : late / not-cancelled trains
  cancel_rate     : cancelled / scheduled trains
  norm_delay      : mean of delay_min / (scheduled journey minutes / 30), not-cancelled trains
  trains_per_hour : scheduled trains per service day per hour, summed over the configured peak windows
  effective_tph   : trains_per_hour * (1 - cancel_rate)  (service actually delivered)
  *_lo / *_hi     : 95% bootstrap interval over trains (resampled with a fixed seed)
"""
import logging

import numpy as np
import pandas as pd
from scipy import stats

from src import schema
from src.config import load_config

log = logging.getLogger(__name__)
N_BOOT = 1000
SEED = 42


def _hours(window):
    to_h = lambda s: int(s[:2]) + int(s[2:]) / 60
    return to_h(window["to_time"]) - to_h(window["from_time"])


def label_peaks(df, peaks):
    """Add `peak` (name of the config window containing sched_dep, else 'other')."""
    tod = df["sched_dep"].dt.hour * 60 + df["sched_dep"].dt.minute
    df = df.copy()
    df["peak"] = "other"
    for name, w in peaks.items():
        lo = int(w["from_time"][:2]) * 60 + int(w["from_time"][2:])
        hi = int(w["to_time"][:2]) * 60 + int(w["to_time"][2:])
        df.loc[(tod >= lo) & (tod < hi), "peak"] = name
    return df


def add_weather(df, weather):
    """Join rain/wind/temp at the origin station for the hour of scheduled departure."""
    df = df.copy()
    df["hour"] = df["sched_dep"].dt.floor("h")
    w = weather.rename(columns={"crs": "origin", "ts": "hour"})
    out = df.merge(w, on=["origin", "hour"], how="left")
    if len(out) != len(df):
        raise ValueError("weather join changed the number of rows (duplicate weather hours?)")
    log.info("weather matched for %d of %d journeys", out["rain_mm"].notna().sum(), len(out))
    return out.drop(columns="hour")


def _boot_ci(values, stat=np.mean, rng=None):
    values = np.asarray(values, dtype=float)
    values = values[~np.isnan(values)]
    if len(values) < 2:
        return (np.nan, np.nan)
    rng = rng or np.random.default_rng(SEED)
    idx = rng.integers(0, len(values), size=(N_BOOT, len(values)))
    boots = stat(values[idx], axis=1)
    return tuple(np.percentile(boots, [2.5, 97.5]))


def origin_metrics(g, cfg):
    """Metrics for one origin's journeys."""
    thr = cfg["late_threshold_min"]
    ran = g[~g["cancelled"]]
    late = (ran["delay_min"] >= thr).astype(float)
    journey_min = (ran["sched_arr"] - ran["sched_dep"]).dt.total_seconds() / 60
    norm = ran["delay_min"] / (journey_min / 30)

    n_days = max(g["date"].nunique(), 1)
    # only count windows that actually have trains for this origin (a window with none is not "zero service")
    peak_hours = sum(_hours(w) for name, w in cfg["peaks"].items() if (g["peak"] == name).any())
    tph = len(g) / n_days / peak_hours
    cancel = g["cancelled"].astype(float)

    row = {
        "n_trains": len(g), "n_cancelled": int(g["cancelled"].sum()), "n_days": n_days,
        "late_rate": late.mean() if len(late) else np.nan,
        "cancel_rate": cancel.mean(),
        "mean_delay_min": ran["delay_min"].mean(),
        "norm_delay": norm.replace([np.inf, -np.inf], np.nan).mean(),
        "trains_per_hour": tph,
        "effective_tph": tph * (1 - cancel.mean()),
        "pct_with_origin_delay": g["origin_delay_min"].notna().mean(),
    }
    row["late_rate_lo"], row["late_rate_hi"] = _boot_ci(late)
    row["cancel_rate_lo"], row["cancel_rate_hi"] = _boot_ci(cancel)
    row["mean_delay_lo"], row["mean_delay_hi"] = _boot_ci(ran["delay_min"])
    if "rain_mm" in g and g["rain_mm"].notna().any():
        row["wet_share"] = (g["rain_mm"] > 0.1).mean()
    return pd.Series(row)


def build_station_metrics(cfg, service, area):
    service = label_peaks(service, cfg["peaks"])
    other = (service["peak"] == "other").sum()
    if other:
        log.warning("%d journeys fall outside the configured peak windows", other)
    # Inbound only: station metrics describe journeys that START at a configured origin.
    # (Outbound evening rows have the hub as `origin`; they are kept in service_enriched for models.)
    origins = {o["crs"] for o in cfg["origins"]}
    service = service[service["origin"].isin(origins)]
    m = service.groupby("origin").apply(origin_metrics, cfg=cfg, include_groups=False)
    m = m.reset_index().rename(columns={"origin": "crs"})
    a = area[area["role"] == "origin"].drop(columns=["role", "lat", "lon", "lsoa_name_api"],
                                            errors="ignore")
    out = m.merge(a, on="crs", how="left")
    if out["imd_decile"].isna().any():
        log.warning("no deprivation data for: %s", out.loc[out["imd_decile"].isna(), "crs"].tolist())
    missing = set(a["crs"]) - set(m["crs"])
    if missing:
        log.warning("configured origins with no train data: %s", sorted(missing))
    out["source"] = service["source"].iloc[0] if service["source"].nunique() == 1 else "mixed"
    return out


def correlate(station_metrics, x="imd_rank",
              ys=("late_rate", "cancel_rate", "mean_delay_min", "norm_delay",
                  "trains_per_hour", "effective_tph")):
    """Spearman correlation of deprivation against each measure, with a bootstrap interval
    over stations. imd_rank: 1 = most deprived, so a POSITIVE correlation with a bad outcome
    (e.g. late_rate) means less deprived areas do worse; negative means deprived areas do worse.
    Exploratory only: with few stations the interval will be wide."""
    rng = np.random.default_rng(SEED)
    rows = []
    for y in ys:
        d = station_metrics[[x, y]].dropna()
        if len(d) < 4:
            rows.append({"measure": y, "n": len(d), "rho": np.nan, "lo": np.nan, "hi": np.nan})
            continue
        if d[x].nunique() < 2 or d[y].nunique() < 2:
            rows.append({"measure": y, "n": len(d), "rho": np.nan, "lo": np.nan, "hi": np.nan})
            continue
        rho = stats.spearmanr(d[x], d[y]).statistic
        boots = []
        for _ in range(N_BOOT):
            s = d.iloc[rng.integers(0, len(d), len(d))]
            if s[x].nunique() > 1 and s[y].nunique() > 1:
                boots.append(stats.spearmanr(s[x], s[y]).statistic)
        lo, hi = np.percentile(boots, [2.5, 97.5]) if boots else (np.nan, np.nan)
        rows.append({"measure": y, "n": len(d), "rho": rho, "lo": lo, "hi": hi})
    return pd.DataFrame(rows)


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cfg = load_config()
    proc = cfg["paths"]["data_dir"] / "processed"
    service = schema.conform(pd.read_parquet(proc / "service_level.parquet"))
    area = pd.read_parquet(proc / "station_area.parquet")
    if (service["source"] == "mock").any():
        log.warning("MOCK DATA: outputs are for pipeline testing only, never real results")

    enriched = label_peaks(service, cfg["peaks"])
    weather_path = proc / "weather_hourly.parquet"
    if weather_path.exists():
        enriched = add_weather(enriched, pd.read_parquet(weather_path))
    enriched.to_parquet(proc / "service_enriched.parquet", index=False)

    sm = build_station_metrics(cfg, enriched, area)
    sm.to_parquet(proc / "station_metrics.parquet", index=False)
    print(f"station_metrics: {len(sm)} origins -> {proc / 'station_metrics.parquet'}")
    print(correlate(sm).round(3).to_string(index=False))


if __name__ == "__main__":
    main()
