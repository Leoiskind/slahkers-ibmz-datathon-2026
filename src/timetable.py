"""Planned timetable for every Greater Manchester station <-> hub journey in the peak windows,
from Network Rail's SCHEDULE full extract (open data, OGL).

Download (signed in to https://publicdatafeeds.networkrail.co.uk/):
  /ntrod/CifFileAuthenticate?type=CIF_ALL_FULL_DAILY&day=toc-full  -> data/raw/timetable/toc-full.gz
Format: one JSON record per line: JsonTimetableV1 (header), TiplocV1 (location codes incl. CRS),
JsonAssociationV1 (ignored), JsonScheduleV1 (one train schedule with a date range and run days).

STP indicators: P permanent, O overlay, N new short-term, C planned cancellation. On each date
an O/N/C schedule for a train UID takes precedence over its P schedule. The extract only holds
schedules still in force, so short-term changes on PAST dates (engineering works etc.) are missing:
a train removed for works on a past date still appears here as planned.

Output: data/processed/timetable_trains.parquet (+ .csv): one row per planned train x day x peak x
GM origin. Join to HSP on (service_date, from_crs, to_crs, sched_from_hhmm, sched_to_hhmm)
= HSP (from_loc, to_loc, gbtt_ptd, gbtt_pta): planned rows with no HSP match = likely cancellations.

Run:  python -m src.timetable [--from 2026-05-18] [--to 2026-10-16]
"""
import argparse
import datetime as dt
import gzip
import json
import logging

import pandas as pd

from src.config import ROOT, load_config

log = logging.getLogger(__name__)
# England & Wales bank holidays 2026 (weekdays only matter: we use weekday peaks)
BANK_HOLIDAYS = {dt.date(2026, 1, 1), dt.date(2026, 4, 3), dt.date(2026, 4, 6), dt.date(2026, 5, 4),
                 dt.date(2026, 5, 25), dt.date(2026, 8, 31), dt.date(2026, 12, 25), dt.date(2026, 12, 28)}
NOT_TRAINS = {"BR", "BS", "SS"}  # replacement bus, service bus, ship


# ---------------------------------------------------------------- parse

def _public(t):
    """CIF public times are 'HHMM'; null or '0000' means no public call."""
    return t if t and t != "0000" else None


def _calls(locations, tiploc_crs):
    """Public calls only: [(crs, public_arrival, public_departure)]."""
    calls = []
    for L in locations or []:
        arr, dep = _public(L.get("public_arrival")), _public(L.get("public_departure"))
        crs = tiploc_crs.get(L.get("tiploc_code"))
        if crs and (arr or dep):
            calls.append((crs, arr, dep))
    return calls


def parse_extract(path, gm_crs, hubs):
    """Stream the extract; keep schedules that publicly call at a hub and at a GM station,
    plus every planned-cancellation (C) record. Returns a list of small dicts."""
    tiploc_crs, kept, n = {}, [], 0
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.startswith('{"JsonAssociationV1"'):
                continue
            rec = json.loads(line)
            if "TiplocV1" in rec:
                t = rec["TiplocV1"]
                if t.get("crs_code"):
                    tiploc_crs[t["tiploc_code"]] = t["crs_code"]
                continue
            s = rec.get("JsonScheduleV1")
            if not s or s.get("transaction_type") != "Create":
                continue
            n += 1
            stp = s["CIF_stp_indicator"]
            seg = s.get("schedule_segment") or {}
            base = {"uid": s["CIF_train_uid"], "stp": stp,
                    "start": s["schedule_start_date"], "end": s["schedule_end_date"],
                    "days": s["schedule_days_runs"]}
            if stp == "C":
                kept.append(base)
                continue
            if seg.get("CIF_train_category") in NOT_TRAINS:
                continue
            calls = _calls(seg.get("schedule_location"), tiploc_crs)
            codes = {c for c, _, _ in calls}
            if codes & hubs and codes & (gm_crs - hubs):
                kept.append({**base, "operator_code": s.get("atoc_code"),
                             "headcode": seg.get("signalling_id"),
                             "category": seg.get("CIF_train_category"), "calls": calls})
            if n % 100000 == 0:
                log.info("  %d schedules read, %d kept", n, len(kept))
    log.info("%d schedules read, %d kept (incl. planned cancellations); %d TIPLOCs with CRS",
             n, len(kept), len(tiploc_crs))
    return kept


# ---------------------------------------------------------------- expand

def _runs_on(s, d):
    return s["start"] <= d.isoformat() <= s["end"] and s["days"][d.weekday()] == "1"


def _minutes(a, b):
    return (int(b[:2]) * 60 + int(b[2:4])) - (int(a[:2]) * 60 + int(a[2:4]))


def journeys(calls, hubs, peaks):
    """For one calling pattern: every (peak, origin, hub) journey whose hub time is in the window.
    Morning = first hub after the origin (arrival); evening = last hub before the origin (departure)."""
    out = []
    in_win = lambda t, w: t and w["from"] <= f"{t[:2]}:{t[2:4]}" <= w["to"]
    for i, (o, o_arr, o_dep) in enumerate(calls):
        if o in hubs:
            continue
        nxt = next((j for j in range(i + 1, len(calls)) if calls[j][0] in hubs), None)
        if nxt is not None and o_dep and in_win(calls[nxt][1], peaks["morning"]):
            out.append(("morning", o, calls[nxt][0], o, calls[nxt][0], o_dep, calls[nxt][1],
                        calls[nxt][1], nxt - i - 1, i))
        prv = next((j for j in range(i - 1, -1, -1) if calls[j][0] in hubs), None)
        if prv is not None and o_arr and in_win(calls[prv][2], peaks["evening"]):
            out.append(("evening", o, calls[prv][0], calls[prv][0], o, calls[prv][2], o_arr,
                        calls[prv][2], i - prv - 1, i))
    return out


def expand(schedules, dates, gm_crs, hubs, peaks):
    by_uid = {}
    for s in schedules:
        by_uid.setdefault(s["uid"], []).append(s)
    by_uid = {u: v for u, v in by_uid.items() if any("calls" in s for s in v)}

    rows = []
    for d in dates:
        for uid, scheds in by_uid.items():
            valid = [s for s in scheds if _runs_on(s, d)]
            if not valid:
                continue
            short = [s for s in valid if s["stp"] in "CON"]
            chosen = max(short, key=lambda s: s["start"]) if short else valid[0]
            planned_cancel = chosen["stp"] == "C"
            if planned_cancel:  # keep the pattern it would have run, flagged
                chosen = next((s for s in valid if s["stp"] == "P" and "calls" in s), None)
                if chosen is None:
                    continue
            if "calls" not in chosen:
                continue
            for (peak, origin, hub, frm, to, t_from, t_to, t_hub, between, before) in \
                    journeys(chosen["calls"], hubs, peaks):
                if origin not in gm_crs:
                    continue
                rows.append({
                    "source": "timetable", "service_date": d, "peak": peak,
                    "station_crs": origin, "hub_crs": hub, "from_crs": frm, "to_crs": to,
                    "train_uid": uid, "headcode": chosen.get("headcode"),
                    "operator_code": chosen.get("operator_code"), "stp_indicator": chosen["stp"],
                    "planned_cancel": planned_cancel,
                    "sched_from_hhmm": t_from[:4], "sched_to_hhmm": t_to[:4], "sched_hub_hhmm": t_hub[:4],
                    "minutes_to_hub": _minutes(t_from, t_to),
                    "stops_between": between, "stops_before": before,
                })
    return pd.DataFrame(rows)


def weekdays(start, end):
    days = pd.date_range(start, end, freq="D").date
    return [d for d in days if d.weekday() < 5 and d not in BANK_HOLIDAYS]


# ---------------------------------------------------------------- CLI

def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cfg = load_config()
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="date_from", default=cfg["dates"]["from"])
    ap.add_argument("--to", dest="date_to", default=cfg["dates"]["to"])
    args = ap.parse_args()

    tt = cfg["timetable"]
    gm = pd.read_csv(ROOT / tt["gm_stations_csv"])
    hubs = {h["crs"] for h in cfg["hubs"]}
    gm_crs = set(gm["Station Code"]) | hubs

    cache = cfg["paths"]["data_dir"] / "cache" / "timetable" / "gm_schedules.json"
    if cache.exists():
        schedules = json.loads(cache.read_text(encoding="utf-8"))
        log.info("using parsed schedules from %s", cache)
    else:
        log.info("parsing %s (about 3.5 GB unpacked, a few minutes)", tt["extract"])
        schedules = parse_extract(ROOT / tt["extract"], gm_crs, hubs)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(schedules), encoding="utf-8")

    earliest = min(s["start"] for s in schedules if s["stp"] == "P")
    date_from = max(args.date_from, earliest)
    if date_from != args.date_from:
        log.warning("extract only covers schedules from %s: starting there, not %s", earliest, args.date_from)
    dates = weekdays(date_from, args.date_to)
    log.info("expanding %d weekdays %s .. %s", len(dates), dates[0], dates[-1])

    df = expand(schedules, dates, gm_crs, hubs, cfg["hub_peaks"])
    df = df.sort_values(["station_crs", "service_date", "peak", "sched_hub_hhmm"]).reset_index(drop=True)
    out = cfg["paths"]["data_dir"] / "processed" / "timetable_trains.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    df.to_csv(out.with_suffix(".csv"), index=False)
    print(f"timetable_trains: {len(df)} rows, {df.station_crs.nunique()} GM stations, "
          f"{df.service_date.nunique()} weekdays -> {out} (+ .csv)")
    print(f"planned cancellations (STP C): {int(df.planned_cancel.sum())}")


if __name__ == "__main__":
    main()
