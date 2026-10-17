"""Real model input from the HSP extract: data/processed/journeys.parquet.

Input: data/processed/train_data.csv (scripts/hsp_extract.py + merge_hsp_csv.py: one row per
HSP-recorded train x station x peak, hub method from CLAUDE.md) and the cached HSP
serviceDetails it was built from.

Outcomes (target `failed` = late >= 5 min OR cancelled):
  - HSP rows with an actual time keep their late / on_time outcome.
  - HSP rows with NO actual time at the measuring stop are `cancelled` if the train carries a
    late_canc_reason or has no actual times anywhere (full or part cancellation); otherwise
    they stay `missing` and are excluded (counted).
  - Trains ABSENT from HSP are not counted at all: HSP omits many runs that did run
    (checked against RTT, 5 Oct: 37 of 38 absent trains ran), so absence is not cancellation.
  So every rate is "among trains HSP recorded".

Position features and scheduled times come from the matched timetable row
(data/processed/timetable_trains.parquet), so they are defined exactly as in the synthetic test.
Weather is joined with src.weather.add_weather.

Also writes data/processed/hsp_coverage.parquet (share of timetabled trains HSP recorded, per
station x peak) and prints Spearman(dep_score, coverage) as a sampling-bias check.

Run:  python -m src.journeys
"""
import glob
import json
import logging

import pandas as pd

from src.config import ROOT, load_config

log = logging.getLogger(__name__)
HSP_CSV = "data/processed/train_data.csv"
HSP_DETAILS = "data/used_for_processing/cache/hsp/serviceDetails"
PEAKS = {"am": "morning", "pm": "evening"}
KEY = ["station_crs", "peak", "service_date", "hub_crs", "origin_hhmm", "hub_hhmm"]


def load_details(rids):
    """Cached HSP serviceDetails for the given RIDs: rid -> attributes dict."""
    found = {}
    for f in glob.glob(str(ROOT / HSP_DETAILS / "*.json")):
        with open(f, encoding="utf-8") as fh:
            a = json.load(fh).get("serviceAttributesDetails") or {}
        if str(a.get("rid")) in rids:
            found[str(a["rid"])] = a
    return found


def classify_missing(row, details):
    """('cancelled', reason) or ('missing', '') for a row with no actual time."""
    a = details.get(row["train_id"])
    if not a:
        return "missing", ""
    locs = a.get("locations") or []
    reasons = [L.get("late_canc_reason") for L in locs if L.get("late_canc_reason")]
    ran_somewhere = any(L.get("actual_ta") or L.get("actual_td") for L in locs)
    if reasons or not ran_somewhere:
        return "cancelled", reasons[0] if reasons else ""
    return "missing", ""


def load_hsp():
    h = pd.read_csv(ROOT / HSP_CSV, dtype=str, encoding="utf-8-sig")
    h["peak"] = h["peak"].map(PEAKS)
    h["service_date"] = pd.to_datetime(h["service_date"]).dt.date
    h["origin_hhmm"] = h["origin_scheduled"].str.zfill(4)
    h["hub_hhmm"] = h["hub_scheduled"].str.zfill(4)
    miss = h["outcome"] == "missing"
    details = load_details(set(h.loc[miss, "train_id"]))
    cls = h[miss].apply(classify_missing, axis=1, details=details, result_type="expand")
    h.loc[miss, "outcome"], h.loc[miss, "cancel_reason"] = cls[0], cls[1]
    log.info("HSP rows %d; no-actual rows %d -> cancelled %d, still missing %d (details found for %d)",
             len(h), int(miss.sum()), int((cls[0] == "cancelled").sum()), int((cls[0] == "missing").sum()),
             len(details))
    canc = h[h["outcome"] == "cancelled"]
    log.info("cancelled by date: %s", canc["service_date"].astype(str).value_counts().sort_index().to_dict())
    return h


def timetable_window(cfg, dates):
    t = pd.read_parquet(cfg["paths"]["data_dir"] / "processed" / "timetable_trains.parquet")
    t = t[t["service_date"].isin(dates)].copy()
    morning = t["peak"] == "morning"
    t["origin_hhmm"] = t["sched_from_hhmm"].where(morning, t["sched_to_hhmm"])
    t["hub_hhmm"] = t["sched_to_hhmm"].where(morning, t["sched_from_hhmm"])
    return t


def coverage(hsp, tt, dep):
    from src.models import spearman_boot
    n_tt = tt.groupby(["station_crs", "peak"]).size().rename("n_timetabled")
    n_h = hsp.merge(tt[KEY], on=KEY).groupby(["station_crs", "peak"]).size().rename("n_hsp")
    cov = pd.concat([n_tt, n_h], axis=1).fillna(0).reset_index()
    cov["coverage"] = cov["n_hsp"] / cov["n_timetabled"]
    for peak, g in cov.merge(dep[["station_crs", "dep_score"]], on="station_crs").groupby("peak"):
        t = spearman_boot(g["dep_score"], g["coverage"], 2000, 0)
        log.info("coverage check (%s): mean %.2f; Spearman(dep_score, coverage) %+.2f [%+.2f, %+.2f], %d stations",
                 peak, g["coverage"].mean(), t["rho"], t["lo"], t["hi"], t["n_stations"])
    return cov


def build_journeys(cfg):
    from src.weather import add_weather
    proc = cfg["paths"]["data_dir"] / "processed"
    hsp = load_hsp()
    tt = timetable_window(cfg, set(hsp["service_date"]))
    dep = pd.read_parquet(proc / "station_deprivation.parquet")
    coverage(hsp, tt, dep).to_parquet(proc / "hsp_coverage.parquet", index=False)

    still_missing = hsp["outcome"] == "missing"
    log.info("excluding %d rows still missing (by station x peak: %d groups)", int(still_missing.sum()),
             hsp[still_missing].groupby(["station_crs", "peak"]).ngroups)
    hsp = hsp[~still_missing]
    keep_tt = KEY + ["sched_from_hhmm", "sched_to_hhmm", "sched_hub_hhmm",
                     "minutes_to_hub", "stops_between", "stops_before"]
    hsp = hsp.drop(columns=["minutes_to_hub", "stops_between", "stops_before", "sched_hour"])
    j = hsp.merge(tt[keep_tt], on=KEY, how="inner")
    log.info("matched to timetable: %d of %d rows (%.1f%%)", len(j), len(hsp), 100 * len(j) / len(hsp))

    hub = j["sched_hub_hhmm"].astype(str).str.zfill(4)
    j["sched_hour"] = hub.str[:2].astype(int) + hub.str[2:].astype(int) / 60
    j["lateness_min"] = pd.to_numeric(j["lateness_min"])
    j["failed"] = j["outcome"].isin(["late", "cancelled"])
    j["source"] = "hsp"
    weather = proc / "weather_hourly.parquet"
    if weather.exists():
        j = add_weather(j, pd.read_parquet(weather))
    j = j.drop(columns=["origin_hhmm", "hub_hhmm"])
    j.to_parquet(proc / "journeys.parquet", index=False)

    print(f"journeys: {len(j)} trains, {j.station_crs.nunique()} stations, "
          f"{j.service_date.nunique()} weekdays -> {proc / 'journeys.parquet'}")
    s = j.groupby("peak").agg(trains=("failed", "size"), late=("outcome", lambda o: (o == "late").mean()),
                              cancelled=("outcome", lambda o: (o == "cancelled").mean()),
                              failed=("failed", "mean"))
    print(s.round(3).to_string())
    print("Rates are among trains HSP recorded; trains absent from HSP are not counted.")
    return j


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    build_journeys(load_config())


if __name__ == "__main__":
    main()
