"""Realtime Trains (RTT) client: every scheduled train between our origins and the hubs,
INCLUDING cancelled ones (HSP only lists trains that ran).

API: https://data.rtt.io, spec https://realtimetrains.github.io/api-specification/specification/main.yml
Token: RTT_TOKEN in .env is a refresh token, exchanged for short-life access tokens.
History is limited to `historyRestrictToDays` (14 on our account), so only recent weekdays.

Per weekday:
  morning  GET /gb-nr/location?code=<hub>&filterFrom=<origin>   hub ARRIVAL 07:00-09:59 (per origin x hub)
  evening  GET /gb-nr/location?code=<hub>&filterTo=<origin>     hub DEPARTURE 16:00-18:59 (per origin x hub)
           GET /gb-nr/location?code=<origin>                    origin arrivals, to measure evening lateness
Plus one GET /gb-nr/service per distinct train identity (its calling pattern; reused for every day).
Every response is cached under data/cache/rtt/, so reruns are free and resumable.

Output: data/processed/rtt_trains.parquet (+ .csv), one row per train x day x peak.
HSP join key: (service_date, from_crs, to_crs, sched_from_hhmm, sched_to_hhmm) = HSP (from_loc,
to_loc, gbtt_ptd, gbtt_pta). RTT rows with no HSP match are the cancellations HSP leaves out.

Run:  python -m src.rtt --info      check token + history limit
      python -m src.rtt --probe     one location + one service call, saved for inspection
      python -m src.rtt             full pull + flatten
      python -m src.rtt --flatten   flatten from the cache only (no network)
"""
import argparse
import datetime as dt
import hashlib
import json
import logging
import time

import pandas as pd
import requests

from src.config import load_config, rtt_token
from src.tableio import read_table, table_exists, write_table
from src.timetable import BANK_HOLIDAYS

log = logging.getLogger(__name__)
_last_call = [0.0]
_access = {"token": None, "until": dt.datetime.min.replace(tzinfo=dt.timezone.utc)}
_stats = {"network": 0, "cache": 0}
EVENING_ORIGIN_WINDOW = ("16:00", "21:59")  # origin arrivals for trains leaving the hub 16:00-18:59


# ---------------------------------------------------------------- HTTP

def _access_token(cfg):
    """RTT_TOKEN is a long-life refresh token: swap it for a short-life access token
    via /api/get_access_token, reusing it until a minute before `validUntil`."""
    now = dt.datetime.now(dt.timezone.utc)
    if _access["token"] is None or now > _access["until"] - dt.timedelta(minutes=1):
        r = requests.get(cfg["rtt"]["base_url"].rstrip("/") + "/api/get_access_token",
                         headers={"Authorization": f"Bearer {rtt_token()}"}, timeout=30)
        if r.status_code == 401:
            raise RuntimeError("RTT rejected RTT_TOKEN when exchanging it for an access token")
        r.raise_for_status()
        d = r.json()
        _access["token"] = d["token"]
        _access["until"] = dt.datetime.fromisoformat(d["validUntil"])
    return _access["token"]


def _cache_dir(cfg, *parts):
    d = cfg["paths"]["data_dir"].joinpath("cache", "rtt", *parts)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_file(cfg, path, params):
    key = hashlib.sha1(json.dumps([path, params], sort_keys=True).encode()).hexdigest()[:16]
    return _cache_dir(cfg, path.strip("/").replace("/", "_")) / f"{key}.json"


def _get(cfg, path, params, use_cache=True, offline=False, retries=5):
    """GET with bearer auth, on-disk cache, request spacing and quota handling.
    Returns parsed JSON, {} for 204 No Content (no services), or None if offline and uncached."""
    f = _cache_file(cfg, path, params)
    if use_cache and f.exists():
        _stats["cache"] += 1
        return json.loads(f.read_text(encoding="utf-8"))
    if offline:
        return None

    url = cfg["rtt"]["base_url"].rstrip("/") + path
    for attempt in range(retries):
        headers = {"Authorization": f"Bearer {_access_token(cfg)}"}
        wait = cfg["rtt"]["min_interval_s"] - (time.monotonic() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.monotonic()
        try:
            r = requests.get(url, params=params, headers=headers, timeout=60)
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt == retries - 1:
                raise
            log.warning("network error (%s), retrying in %ss", type(e).__name__, 15 * 2 ** attempt)
            time.sleep(15 * 2 ** attempt)
            continue
        if r.status_code == 429:
            delay = int(r.headers.get("Retry-After", 60))
            log.warning("rate limited, sleeping %ss", delay)
            time.sleep(delay)
            continue
        if r.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 401:
            _access["token"] = None  # expired early: refresh once and retry
            if attempt:
                raise RuntimeError("RTT returned 401 even with a fresh access token")
            continue
        r.raise_for_status()
        data = {} if r.status_code == 204 else r.json()
        f.write_text(json.dumps(data), encoding="utf-8")
        _stats["network"] += 1
        _respect_quota(r.headers)
        return data
    r.raise_for_status()


def _respect_quota(headers):
    day = headers.get("X-RateLimit-Remaining-Day")
    if day is not None and int(day) < 5:
        raise RuntimeError(f"RTT daily quota nearly used up ({day} left); rerun tomorrow (cache keeps progress)")
    hour = headers.get("X-RateLimit-Remaining-Hour")
    if hour is not None and int(hour) < 5:
        log.warning("hourly quota low (%s left): pausing 10 minutes", hour)
        time.sleep(600)


# ---------------------------------------------------------------- fetch

def api_info(cfg):
    """Entitlements and history limit (never cached)."""
    info = _get(cfg, "/api/info", {}, use_cache=False)
    (_cache_dir(cfg) / "api_info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    return info


def service_dates(history_days, today=None):
    """Weekdays (no bank holidays) from `history_days` ago up to yesterday."""
    today = today or dt.date.today()
    days = [today - dt.timedelta(days=i) for i in range(history_days, 0, -1)]
    return [d for d in days if d.weekday() < 5 and d not in BANK_HOLIDAYS]


def _window(date, start, end):
    return {"timeFrom": f"{date}T{start}:00", "timeTo": f"{date}T{end}:00"}


def fetch_hub(cfg, hub, origin, date, peak, offline=False):
    """Trains at `hub` in the peak window that call at `origin` before (morning) or after
    (evening) the hub."""
    w = cfg["hub_peaks"][peak]
    params = {"code": hub, **_window(date, w["from"], w["to"])}
    params["filterFrom" if peak == "morning" else "filterTo"] = origin
    return _get(cfg, "/gb-nr/location", params, offline=offline)


def fetch_origin_evening(cfg, origin, date, offline=False):
    """Every train at `origin` in the evening, used to read arrival lateness there."""
    params = {"code": origin, **_window(date, *EVENING_ORIGIN_WINDOW)}
    return _get(cfg, "/gb-nr/location", params, offline=offline)


def fetch_service(cfg, identity, departure_date, offline=False):
    """Full calling pattern of one train on one day."""
    return _get(cfg, "/gb-nr/service", {"identity": identity, "departureDate": str(departure_date)},
                offline=offline)


def pull(cfg, dates, offline=False):
    """Fetch (or read from cache) every response needed. Returns
    (hub_rows, origin_lineups, services): raw JSON keyed for flatten()."""
    origins = [o["crs"] for o in cfg["origins"]]
    hubs = [h["crs"] for h in cfg["hubs"]]
    hub_rows, origin_lineups, services = [], {}, {}
    total = len(dates) * len(origins) * (2 * len(hubs) + 1)
    done = 0
    for date in dates:
        for origin in origins:
            for peak in ("morning", "evening"):
                for hub in hubs:
                    d = fetch_hub(cfg, hub, origin, date, peak, offline) or {}
                    for s in d.get("services") or []:
                        hub_rows.append({"date": date, "peak": peak, "origin": origin, "hub": hub, "s": s})
                    done += 1
            origin_lineups[(date, origin)] = fetch_origin_evening(cfg, origin, date, offline) or {}
            done += 1
        log.info("%s done (%d/%d location calls; %d network, %d cached)",
                 date, done, total, _stats["network"], _stats["cache"])

    # one calling pattern per identity: reused for every day it runs
    first_seen = {}
    for r in hub_rows:
        m = r["s"]["scheduleMetadata"]
        first_seen.setdefault(m["identity"], m["departureDate"])
    log.info("%d distinct trains: fetching calling patterns", len(first_seen))
    for i, (ident, dep_date) in enumerate(first_seen.items(), 1):
        d = fetch_service(cfg, ident, dep_date, offline)
        if d:
            services[ident] = d.get("service") or {}
        if i % 50 == 0:
            log.info("  %d/%d services", i, len(first_seen))
    return hub_rows, origin_lineups, services


# ---------------------------------------------------------------- flatten

def _crs(loc):
    codes = (loc or {}).get("shortCodes") or []
    return codes[0] if codes else None


def _hhmm(ts):
    return ts[11:13] + ts[14:16] if ts else None


def _minutes(a, b):
    if not a or not b:
        return None
    return (dt.datetime.fromisoformat(b) - dt.datetime.fromisoformat(a)).total_seconds() / 60


def _pattern(service, origin, hub, peak):
    """From a calling pattern: scheduled times at origin and hub, and stop counts."""
    calls = [(_crs(L["location"]), L["temporalData"]) for L in service.get("locations") or []]
    codes = [c for c, _ in calls]
    if origin not in codes or hub not in codes:
        return {}
    i = codes.index(origin)
    if peak == "morning":  # first visit to the hub after the origin
        after = [k for k in range(i + 1, len(codes)) if codes[k] == hub]
    else:                  # last visit to the hub before the origin
        after = [k for k in range(i) if codes[k] == hub][-1:]
    if not after:
        return {}
    j = after[0]
    t_o, t_h = calls[i][1], calls[j][1]
    if peak == "morning":
        sched_from = (t_o.get("departure") or {}).get("scheduleAdvertised")
        sched_to = (t_h.get("arrival") or {}).get("scheduleAdvertised")
    else:
        sched_from = (t_h.get("departure") or {}).get("scheduleAdvertised")
        sched_to = (t_o.get("arrival") or {}).get("scheduleAdvertised")
    return {"sched_from": sched_from, "sched_to": sched_to,
            "minutes_to_hub": _minutes(sched_from, sched_to),
            "stops_between": abs(j - i) - 1,
            "stops_before": i}


def _reasons(s, side):
    texts = [r.get("shortText") for r in s.get("reasons") or [] if r.get("shortText")]
    return side.get("cancellationReasonCode"), "; ".join(texts) or None


def _outcome(cancelled, lateness, actual, threshold):
    if cancelled:
        return "cancelled"
    if actual is None or lateness is None:
        return "missing"
    return "late" if max(lateness, 0) >= threshold else "on_time"


def flatten(cfg, hub_rows, origin_lineups, services):
    threshold = cfg["late_threshold_min"]
    origin_arr = {}  # (date, origin, uniqueIdentity) -> (temporalData, service)
    for (date, origin), d in origin_lineups.items():
        for s in d.get("services") or []:
            origin_arr[(date, origin, s["scheduleMetadata"]["uniqueIdentity"])] = (s["temporalData"], s)

    rows = []
    for r in hub_rows:
        s, peak, origin, hub = r["s"], r["peak"], r["origin"], r["hub"]
        m, t = s["scheduleMetadata"], s["temporalData"]
        at_hub = t.get(cfg["hub_peaks"][peak]["at_hub"]) or {}
        sched_hub = at_hub.get("scheduleAdvertised")
        w = cfg["hub_peaks"][peak]
        if not sched_hub or not (w["from"] <= sched_hub[11:16] <= w["to"]):
            continue  # listed in the window but its hub arrival/departure falls outside it
        pat = _pattern(services.get(m["identity"], {}), origin, hub, peak)
        hub_cancelled = bool(at_hub.get("isCancelled")) or t.get("displayAs") == "CANCELLED"

        if peak == "morning":  # measured at the hub
            measured, measured_s, display = at_hub, s, t.get("displayAs")
            cancelled = hub_cancelled
        else:                  # measured on arrival at the origin
            o_t, o_s = origin_arr.get((r["date"], origin, m["uniqueIdentity"]), ({}, s))
            measured, measured_s, display = o_t.get("arrival") or {}, o_s, o_t.get("displayAs")
            cancelled = hub_cancelled or bool(measured.get("isCancelled")) or display == "CANCELLED"
        code, text = _reasons(measured_s, measured if measured.get("isCancelled") else at_hub)
        lateness = measured.get("realtimeAdvertisedLateness")

        rows.append({
            "source": "rtt", "service_date": r["date"], "peak": peak,
            "station_crs": origin, "hub_crs": hub,
            "from_crs": origin if peak == "morning" else hub,
            "to_crs": hub if peak == "morning" else origin,
            "unique_identity": m["uniqueIdentity"], "identity": m["identity"],
            "headcode": m.get("trainReportingIdentity"), "operator_code": (m.get("operator") or {}).get("code"),
            "in_passenger_service": m.get("inPassengerService"),
            "train_origin": "; ".join(o["location"]["description"] for o in s.get("origin") or []),
            "train_destination": "; ".join(o["location"]["description"] for o in s.get("destination") or []),
            "sched_hub_time": sched_hub,
            "sched_from_hhmm": _hhmm(pat.get("sched_from")), "sched_to_hhmm": _hhmm(pat.get("sched_to")),
            "sched_measure_time": measured.get("scheduleAdvertised"),
            "actual_measure_time": measured.get("realtimeActual"),
            "lateness_min": lateness,
            "is_cancelled": cancelled, "display_as": display,
            "cancel_reason_code": code, "reason_text": text,
            "minutes_to_hub": pat.get("minutes_to_hub"),
            "stops_between": pat.get("stops_between"), "stops_before": pat.get("stops_before"),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # one row per train per peak: first hub reached (morning) / last hub left (evening)
    df = df.sort_values("sched_hub_time")
    morning = df[df.peak == "morning"].drop_duplicates(["unique_identity", "station_crs"], keep="first")
    evening = df[df.peak == "evening"].drop_duplicates(["unique_identity", "station_crs"], keep="last")
    df = pd.concat([morning, evening]).sort_values(["station_crs", "peak", "sched_hub_time"])
    df["outcome"] = [_outcome(c, l, a, threshold) for c, l, a in
                     zip(df.is_cancelled, df.lateness_min, df.actual_measure_time)]
    df["failed"] = df.outcome.isin(["late", "cancelled"])
    return df.reset_index(drop=True)


# ---------------------------------------------------------------- CLI

def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--info", action="store_true", help="print /api/info and stop")
    ap.add_argument("--probe", action="store_true", help="one location + one service call")
    ap.add_argument("--flatten", action="store_true", help="flatten from cache only, no network")
    args = ap.parse_args()
    cfg = load_config()

    if args.flatten:
        info = json.loads((_cache_dir(cfg) / "api_info.json").read_text(encoding="utf-8"))
    else:
        info = api_info(cfg)
    if args.info:
        print(json.dumps(info, indent=2))
        return
    days = info["credentials"].get("historyRestrictToDays", 14)
    if args.probe:
        date = service_dates(3)[-1]
        origin, hub = cfg["origins"][0]["crs"], cfg["hubs"][0]["crs"]
        loc = fetch_hub(cfg, hub, origin, date, "morning")
        out = _cache_dir(cfg) / "probe_location.json"
        out.write_text(json.dumps(loc, indent=2), encoding="utf-8")
        print(f"{origin}->{hub} {date}: {len(loc.get('services') or [])} services -> {out}")
        if loc.get("services"):
            m = loc["services"][0]["scheduleMetadata"]
            svc = fetch_service(cfg, m["identity"], m["departureDate"])
            out = _cache_dir(cfg) / "probe_service.json"
            out.write_text(json.dumps(svc, indent=2), encoding="utf-8")
            print(f"service {m['identity']} -> {out}")
        return

    # history window is "the last N days"; stay one day inside it so the oldest day isn't cut off
    dates = service_dates(days - 1)
    log.info("pulling %d weekdays: %s .. %s", len(dates), dates[0], dates[-1])
    df = flatten(cfg, *pull(cfg, dates, offline=args.flatten))
    out = cfg["paths"]["data_dir"] / "processed" / "rtt_trains.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    write_table(df, out)
    print(f"rtt_trains: {len(df)} rows -> {out} (+ .csv); {_stats['network']} network calls, "
          f"{_stats['cache']} cached")
    if len(df):
        print(df.groupby(["station_crs", "peak"]).outcome.value_counts().unstack(fill_value=0).to_string())


if __name__ == "__main__":
    main()
