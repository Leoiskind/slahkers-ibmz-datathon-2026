"""Station evidence pack: every number the reports use, computed here (Granite never calculates).

Inputs:  data/processed/journeys.parquet, station_deprivation.parquet,
         models/<version>/<platform>/<peak>/{station_residuals_logreg.csv, metadata.json}
Outputs: data/processed/station_quality.{parquet,csv}   one row per station x peak
         data/processed/report_inputs/*.json            area / borough / station inputs for src.brief
         docs/figures/deprivation_vs_failure_morning.png  the main chart

Intervals: per-station 95% bootstrap resampling SERVICE DATES (CLAUDE.md 3.2).
Conclusion: chosen by rule from the fixed main test (morning, logreg, position-adjusted).

Run:  python -m src.evidence [--version v1-2026-10-17]
"""
import argparse
import json
import logging
import re

import numpy as np
import pandas as pd

from src.config import ROOT, load_config
from src.models import platform_tag

log = logging.getLogger(__name__)
N_BOOT, SEED = 2000, 0
MAIN = ("morning", "logreg", "adjusted")
CONCLUSIONS = {
    "gap_found": "Trains serving more deprived areas fail more often, and {gap_remaining_pct}% of the gap "
                 "remains after accounting for network position.",
    "no_clear_gap": "No clear evidence of unequal service in this sample; here's a tool to keep checking, "
                    "station by station.",
    "reverse": "In this sample, trains serving less deprived areas fail more often than those serving "
               "more deprived areas.",
}
CAVEATS = [
    "Results are associations, not proof of cause.",
    "Rates cover only trains recorded in National Rail's Historical Service Performance data; "
    "trains it did not record are not counted.",
    "The data covers 10 weekdays (5 to 16 October 2026), so figures can move with more data.",
    "A train counts as failed if it was 5 or more minutes late or was cancelled.",
    "Metrolink trams and buses are not included.",
]
LINE_CAVEAT = ("A station's reliability depends partly on its line and position on the network, "
               "which the expected rate accounts for only approximately.")


# ---------------------------------------------------------------- inputs

def latest_model_dir(version=None):
    base = ROOT / "models"
    if version:
        return base / version / platform_tag()
    dirs = [d for d in base.glob("v*") if not d.name.endswith("-synthetic") and (d / platform_tag()).exists()]
    if not dirs:
        raise SystemExit("no real model run found: run python -m src.models --peak both first")
    return max(dirs, key=lambda d: d.stat().st_mtime) / platform_tag()


def boroughs():
    s = pd.read_csv(ROOT / "data/raw/manchester_old/manchester_stations.csv")
    crs = s["Station (and code)"].str.extract(r"\(([A-Z]{3})\)\s*$")[0]
    return dict(zip(crs, s["Borough"]))


# ---------------------------------------------------------------- station table

def date_bootstrap(g, rng):
    """95% interval of the failure rate, resampling service dates."""
    by_day = g.groupby("service_date")["failed"].agg(["sum", "size"])
    f, n = by_day["sum"].to_numpy(float), by_day["size"].to_numpy(float)
    idx = rng.integers(0, len(n), size=(N_BOOT, len(n)))
    rates = f[idx].sum(1) / n[idx].sum(1)
    return np.percentile(rates, [2.5, 97.5])


def station_table(journeys, dep, model_dir):
    rng = np.random.default_rng(SEED)
    rows = []
    for (crs, peak), g in journeys.groupby(["station_crs", "peak"]):
        ran = g[g["outcome"] != "cancelled"]
        lo, hi = date_bootstrap(g, rng)
        rows.append({
            "station_crs": crs, "station_name": g["station_name"].iloc[0], "peak": peak,
            "n_scheduled": len(g), "n_days": g["service_date"].nunique(),
            "failure_rate": g["failed"].mean(), "failure_ci_lo": lo, "failure_ci_hi": hi,
            "late_rate": (g["outcome"] == "late").mean(), "cancel_rate": (g["outcome"] == "cancelled").mean(),
            "mean_delay_min": ran["lateness_min"].mean(), "p90_delay_min": ran["lateness_min"].quantile(0.9),
        })
    t = pd.DataFrame(rows)
    res = pd.concat([pd.read_csv(model_dir / p / "station_residuals_logreg.csv").assign(peak=p)
                     for p in ("morning", "evening")])
    t = t.merge(res[["station_crs", "peak", "expected", "residual"]], on=["station_crs", "peak"], how="left")
    t = t.merge(dep[["station_crs", "dep_score", "dep_share_top20", "n_lsoa"]], on="station_crs", how="left")
    t["dep_percentile"] = t["station_crs"].map(dict(zip(dep["station_crs"], dep["dep_score"].rank(pct=True) * 100)))
    t["borough"] = t["station_crs"].map(boroughs())
    t["low_n"] = t["n_scheduled"] < 30
    t["low_n_lsoa"] = t["n_lsoa"] < 3
    for peak, g in t.groupby("peak"):
        t.loc[g.index, "rank_worst"] = g["failure_rate"].rank(ascending=False, method="min")
        t.loc[g.index, "rank_worst_adjusted"] = g["residual"].rank(ascending=False, method="min")
        t.loc[g.index, "deprivation_third"] = pd.qcut(g["dep_score"], 3, labels=["least", "middle", "most"])
    return t


# ---------------------------------------------------------------- report inputs

def pct(x):
    return None if pd.isna(x) else int(round(100 * x))


def conclusion(tests):
    t = tests[MAIN[1]][MAIN[2]]
    code = "gap_found" if t["lo"] > 0 else "reverse" if t["hi"] < 0 else "no_clear_gap"
    return code, t


def area_input(t, journeys, meta, peak):
    g, j = t[(t.peak == peak) & t.dep_score.notna()], journeys[journeys.peak == peak]
    code, main = conclusion(meta["tests"]) if peak == MAIN[0] else (None, None)
    thirds = {third: j[j.station_crs.isin(g[g.deprivation_third == third].station_crs)]
              for third in ("most", "least")}
    ok = g[~g.low_n].sort_values("residual")
    tests = meta["tests"]["logreg"]
    out = {
        "level": "area", "area": "Greater Manchester", "peak": peak,
        "stations": int(j.station_crs.nunique()), "stations_in_deprivation_tests": int(len(g)),
        "trains": int(len(j)), "weekdays": int(j.service_date.nunique()),
        "failure_rate_pct": pct(j.failed.mean()), "late_rate_pct": pct((j.outcome == "late").mean()),
        "cancel_rate_pct": pct((j.outcome == "cancelled").mean()),
        "raw_test": {"rho": round(tests["raw"]["rho"], 2), "ci": [round(tests["raw"]["lo"], 2), round(tests["raw"]["hi"], 2)]},
        "adjusted_test": {"rho": round(tests["adjusted"]["rho"], 2),
                          "ci": [round(tests["adjusted"]["lo"], 2), round(tests["adjusted"]["hi"], 2)]},
        "most_deprived_third_failure_pct": pct(thirds["most"].failed.mean()),
        "least_deprived_third_failure_pct": pct(thirds["least"].failed.mean()),
        "worse_than_expected": ok.tail(3).station_name.iloc[::-1].tolist(),
        "better_than_expected": ok.head(3).station_name.tolist(),
        "caveats": CAVEATS + [LINE_CAVEAT],
    }
    if code:
        out["conclusion_code"] = code
        gap = None
        if code == "gap_found":  # share of the raw third-vs-third gap left after position adjustment
            raw = thirds["most"].failed.mean() - thirds["least"].failed.mean()
            adj = g[g.deprivation_third == "most"].residual.mean() - g[g.deprivation_third == "least"].residual.mean()
            gap = pct(adj / raw) if raw > 0 else None
        out["conclusion"] = CONCLUSIONS[code].format(gap_remaining_pct=gap)
    else:
        out["note"] = "The main test is the morning peak; this evening summary is supporting evidence."
    return out


def borough_inputs(t, journeys, peak):
    g, j = t[t.peak == peak], journeys[journeys.peak == peak]
    gm = j.failed.mean()
    for name, b in g.dropna(subset=["borough"]).groupby("borough"):
        jb = j[j.station_crs.isin(b.station_crs)]
        ranked = b.sort_values("failure_rate")
        yield f"borough_{re.sub(r'[^a-z]+', '_', name.lower())}_{peak}", {
            "level": "borough", "borough": name, "peak": peak, "stations": int(len(b)), "trains": int(len(jb)),
            "failure_rate_pct": pct(jb.failed.mean()), "late_rate_pct": pct((jb.outcome == "late").mean()),
            "cancel_rate_pct": pct((jb.outcome == "cancelled").mean()),
            "greater_manchester_failure_rate_pct": pct(gm),
            "difference_from_greater_manchester_points": pct(jb.failed.mean()) - pct(gm),
            "most_reliable_station": ranked.station_name.iloc[0],
            "most_reliable_station_failure_pct": pct(ranked.failure_rate.iloc[0]),
            "least_reliable_station": ranked.station_name.iloc[-1],
            "least_reliable_station_failure_pct": pct(ranked.failure_rate.iloc[-1]),
            "caveats": CAVEATS + ["With few stations per borough there is no separate statistical test "
                                  "for a borough; compare with the Greater Manchester result."],
        }


def station_inputs(t, peak):
    g = t[t.peak == peak]
    for r in g.itertuples():
        extra = []
        if r.low_n:
            extra.append(f"Only {r.n_scheduled} trains were recorded, so these figures are uncertain.")
        if r.low_n_lsoa is True:
            extra.append("Few neighbourhoods lie within 1.5 km of this station, so its deprivation figure is uncertain.")
        yield f"station_{r.station_crs}_{peak}", {
            "level": "station", "station": r.station_name, "crs": r.station_crs, "borough": r.borough,
            "peak": peak, "trains": int(r.n_scheduled), "weekdays": int(r.n_days),
            "failure_rate_pct": pct(r.failure_rate), "failure_ci_pct": [pct(r.failure_ci_lo), pct(r.failure_ci_hi)],
            "late_rate_pct": pct(r.late_rate), "cancel_rate_pct": pct(r.cancel_rate),
            "expected_failure_pct": pct(r.expected), "difference_from_expected_points": (
                None if pd.isna(r.residual) else pct(r.failure_rate) - pct(r.expected)),
            "rank_worst": None if pd.isna(r.rank_worst) else int(r.rank_worst), "of_stations": int(len(g)),
            "deprivation_percentile": None if pd.isna(r.dep_percentile) else int(round(r.dep_percentile)),
            "share_of_nearby_areas_in_most_deprived_20pct": pct(r.dep_share_top20),
            "caveats": CAVEATS + [LINE_CAVEAT] + extra,
        }


# ---------------------------------------------------------------- chart

def main_chart(t, meta, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    g = t[(t.peak == "morning") & t.dep_score.notna()]
    fig, ax = plt.subplots(figsize=(8, 5.5))
    for low, alpha in ((False, 0.9), (True, 0.3)):
        s = g[g.low_n == low]
        ax.errorbar(s.dep_score * 100, s.failure_rate * 100,
                    yerr=[(s.failure_rate - s.failure_ci_lo) * 100, (s.failure_ci_hi - s.failure_rate) * 100],
                    fmt="o", ms=5, alpha=alpha, capsize=2, color="#2a6f97",
                    label="fewer than 30 trains" if low else "station (95% interval over days)")
    raw, adj = meta["tests"]["logreg"]["raw"], meta["tests"]["logreg"]["adjusted"]
    ax.set_xlabel("Deprivation around the station (mean IMD percentile; higher = more deprived)")
    ax.set_ylabel("Morning trains late 5+ min or cancelled (%)")
    ax.set_title(f"Greater Manchester rail, morning peak, 5-16 Oct 2026 ({len(g)} stations)\n"
                 f"Spearman raw {raw['rho']:+.2f} [{raw['lo']:+.2f}, {raw['hi']:+.2f}]   "
                 f"position-adjusted {adj['rho']:+.2f} [{adj['lo']:+.2f}, {adj['hi']:+.2f}]", fontsize=10)
    ax.legend(loc="upper left", fontsize=8, frameon=False)
    ax.grid(alpha=0.2)
    fig.text(0.01, 0.01, "Data: National Rail HSP (recorded trains only), IMD 2025, Network Rail timetable. "
             "Associations only.", fontsize=7, color="grey")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------- run

def build_evidence(cfg, version=None):
    proc = cfg["paths"]["data_dir"] / "processed"
    journeys = pd.read_parquet(proc / "journeys.parquet")
    dep = pd.read_parquet(proc / "station_deprivation.parquet")
    model_dir = latest_model_dir(version)
    metas = {p: json.loads((model_dir / p / "metadata.json").read_text(encoding="utf-8"))
             for p in ("morning", "evening")}
    log.info("using model outputs from %s", model_dir)

    t = station_table(journeys, dep, model_dir)
    t.to_parquet(proc / "station_quality.parquet", index=False)
    t.round(4).to_csv(proc / "station_quality.csv", index=False)

    out = proc / "report_inputs"
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("*.json"):
        f.unlink()
    n = 0
    for peak in ("morning", "evening"):
        items = [(f"area_{peak}", area_input(t, journeys, metas[peak], peak))]
        items += list(borough_inputs(t, journeys, peak)) + list(station_inputs(t, peak))
        for name, payload in items:
            (out / f"{name}.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            n += 1
    main_chart(t, metas["morning"], ROOT / "docs/figures/deprivation_vs_failure_morning.png")

    area = json.loads((out / "area_morning.json").read_text(encoding="utf-8"))
    print(f"station_quality: {len(t)} rows ({t.station_crs.nunique()} stations, "
          f"{t.borough.nunique()} boroughs) -> {proc / 'station_quality.parquet'}")
    print(f"report inputs: {n} JSON files -> {out}")
    print(f"conclusion ({area['conclusion_code']}): {area['conclusion']}")
    print(f"most vs least deprived third, morning failure: {area['most_deprived_third_failure_pct']}% vs "
          f"{area['least_deprived_third_failure_pct']}% (communication only, not a test)")
    print("chart -> docs/figures/deprivation_vs_failure_morning.png")
    return t


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", help="model version folder, e.g. v1-2026-10-17 (default: newest real run)")
    args = ap.parse_args()
    build_evidence(load_config(), args.version)


if __name__ == "__main__":
    main()
