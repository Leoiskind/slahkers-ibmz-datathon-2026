#!/usr/bin/env python3
"""HSP station-to-Manchester-hub historic journey extractor.

HSP serviceMetrics returns matched RIDs, not a full scheduled timetable.
Consequently fully cancelled/omitted trains may be missing: do NOT treat this
file as a complete cancellation-rate denominator without timetable validation.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import csv
from datetime import date, datetime, timedelta
import os
from pathlib import Path
import random
import re
import sys
import time

import requests

# ---- EDITABLE CONSTANTS ----
STATIONS_FILE = "stations.csv"  # CSV headers below
STATION_CODE = "Station Code"
STATION_NAME = "Station Name"
HUBS = ("MAN", "MCO", "MCV", "DGT")
START_DATE = "2026-10-05"
END_DATE = "2026-10-16"
LATE_THRESHOLD_MIN = 5
AM_START, AM_END = "0700", "0959"
PM_START, PM_END = "1600", "1859"
OUTPUT_CSV = "data/processed/journeys_hsp.csv"
AUDIT_CSV = "data/processed/hsp_query_audit.csv"
BASE_URL = os.getenv("HSP_BASE_URL", "https://hsp-prod.rockshore.net/api/v1").rstrip("/")
# Default: .env one directory above this script (override with HSP_ENV_FILE).
ENV_FILE = Path(os.getenv("HSP_ENV_FILE", str(Path(__file__).resolve().parent.parent / ".env")))
RETRY_COUNT = 5
REQUEST_PAUSE_SEC = 0.2
CHUNK_DAYS = 14  # larger queries reduce call count; lower if API times out
SAVE_EVERY = 100  # append after each 100 selected train rows
# Optional: public holiday dates to exclude (YYYY-MM-DD). Populate for your region.
EXCLUDED_DATES = set()
# ----------------------------

FIELDS = ["source", "station_crs", "station_name", "hub_crs", "peak", "service_date",
          "train_id", "operator", "sched_time", "actual_time", "lateness_min",
          "outcome", "failed", "cancel_reason", "minutes_to_hub", "scheduled_journey_min",
          "stops_between", "stops_before", "sched_hour", "planned_cancel_flag",
          "origin_scheduled", "origin_actual", "hub_scheduled", "hub_actual",
          "data_quality_note"]


def minute(hhmm):
    if hhmm is None:
        return None
    s = str(hhmm).strip().replace(":", "")
    if not re.fullmatch(r"\d{4}", s):
        return None
    h, m = int(s[:2]), int(s[2:])
    return h * 60 + m if 0 <= h < 24 and 0 <= m < 60 else None


def forward_delta(start, end):
    """Minutes between two schedule times, allowing passage through midnight."""
    a, b = minute(start), minute(end)
    return None if a is None or b is None else (b - a) % 1440


def delay_minutes(scheduled, actual):
    a, b = minute(scheduled), minute(actual)
    if a is None or b is None:
        return None
    # Nearest-day signed difference handles slightly early arrivals and midnight.
    return max(0, (b - a + 720) % 1440 - 720)


def date_chunks(first, last, chunk_days=CHUNK_DAYS):
    current = first
    while current <= last:
        stop = min(last, current + timedelta(days=chunk_days - 1))
        yield current, stop
        current = stop + timedelta(days=1)


def load_stations(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not {STATION_CODE, STATION_NAME}.issubset(reader.fieldnames or []):
            raise ValueError(f"CSV must contain {STATION_CODE} and {STATION_NAME}")
        entries = []
        for row in reader:
            crs = (row[STATION_CODE] or "").strip().upper()
            if not crs:
                continue
            if not re.fullmatch(r"[A-Z0-9]{3}", crs):
                raise ValueError(f"Invalid CRS station code: {crs!r}")
            if crs in HUBS:
                print(f"Skipping hub {crs} (hubs cannot be study origins)", file=sys.stderr)
                continue
            entries.append((crs, (row[STATION_NAME] or "").strip()))
    return list(dict.fromkeys(entries))


def load_parent_env(path=ENV_FILE):
    """Load a basic KEY=VALUE .env without overwriting exported variables."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", key):
            continue
        if value.startswith(("\"", "'")) and value.endswith(value[0]):
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        os.environ.setdefault(key, value)


class RateLimiter:
    """Shared minimum spacing between outgoing requests."""
    def __init__(self, per_second):
        self.interval = 1.0 / per_second if per_second > 0 else 0.0
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            delay = max(0.0, self.next_at - now)
            self.next_at = max(now, self.next_at) + self.interval
        if delay:
            time.sleep(delay)


class HSP:
    def __init__(self, rps=1.0):
        username, password = os.getenv("HSP_USERNAME"), os.getenv("HSP_PASSWORD")
        if not username or not password:
            raise RuntimeError("Set HSP_USERNAME and HSP_PASSWORD environment variables")
        self.base_url = os.getenv("HSP_BASE_URL", BASE_URL).rstrip("/")
        self.auth = (username, password)
        self.local = threading.local()
        self.limiter = RateLimiter(rps)
        self.counter_lock = threading.Lock()
        self.network_requests = 0

    def session(self):
        if not hasattr(self.local, "session"):
            session = requests.Session()
            session.auth = self.auth
            session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
            self.local.session = session
        return self.local.session

    def post(self, endpoint, payload):
        # No disk reads or writes: every request goes to the HSP API.
        url = f"{self.base_url}/{endpoint}"
        for attempt in range(RETRY_COUNT):
            try:
                self.limiter.wait()
                with self.counter_lock:
                    self.network_requests += 1
                resp = self.session().post(url, json=payload, timeout=(15, 120))
                if resp.status_code in (401, 403):
                    raise RuntimeError(f"HSP {resp.status_code}: credentials or entitlement rejected at {url}")
                if resp.status_code in (429, 500, 502, 503, 504):
                    err = requests.HTTPError(f"HTTP {resp.status_code}")
                    err.response = resp
                    raise err
                resp.raise_for_status()
                return resp.json()
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
                if attempt == RETRY_COUNT - 1:
                    raise RuntimeError(f"HSP request failed {endpoint}, payload={payload}: {exc}") from exc
                retry_after = None
                if isinstance(exc, requests.HTTPError) and exc.response is not None:
                    raw = exc.response.headers.get("Retry-After", "")
                    if raw.isdigit():
                        retry_after = min(120, int(raw))
                time.sleep(retry_after if retry_after is not None else min(45, 2 ** (attempt + 1) + random.random()))
        raise AssertionError("unreachable")


def metric_rids(response):
    services = response.get("Services", response.get("services", []))
    if not isinstance(services, list):
        raise ValueError("Unexpected serviceMetrics response: Services is not a list")
    ids = set()
    for entry in services:
        attrs = entry.get("serviceAttributesMetrics", {})
        rids = attrs.get("rids", [])
        if isinstance(rids, str):
            rids = [rids]
        for rid in rids or []:
            if rid:
                ids.add(str(rid))
    return ids


def normalize_detail(response):
    d = response.get("serviceAttributesDetails")
    if not isinstance(d, dict) or not isinstance(d.get("locations"), list):
        raise ValueError("Unexpected serviceDetails response shape")
    return d


def scheduled_at(loc, direction):
    return loc.get("gbtt_pta") or loc.get("gbtt_ptd") if direction == "arrival" else loc.get("gbtt_ptd") or loc.get("gbtt_pta")


def actual_at(loc, direction):
    return loc.get("actual_ta") or loc.get("actual_td") if direction == "arrival" else loc.get("actual_td") or loc.get("actual_ta")


def build_one(detail, origin, name, peak):
    """A train is selected once, using first AM hub / last PM hub in stop order."""
    locs = detail["locations"]
    origin_indices = [i for i, s in enumerate(locs) if str(s.get("location", "")).upper() == origin]
    options = []
    for oi in origin_indices:
        for hi, stop in enumerate(locs):
            hub = str(stop.get("location", "")).upper()
            if hub not in HUBS:
                continue
            if peak == "am" and oi < hi:
                # Arrival at first reachable hub following boarding.
                options.append((oi, hi))
            if peak == "pm" and hi < oi:
                # Departure from last hub before arriving home.
                options.append((oi, hi))
    if not options:
        return None
    # One origin stop per train is normal; select first hub after it for AM,
    # last hub before it for PM; avoid duplicate journeys for repeated calls.
    oi = min(oi for oi, hi in options)
    hubs_for_origin = [hi for o, hi in options if o == oi]
    hi = min(hubs_for_origin) if peak == "am" else max(hubs_for_origin)
    os_, hs = locs[oi], locs[hi]
    hub = str(hs["location"]).upper()
    selector = scheduled_at(hs, "arrival" if peak == "am" else "departure")
    sel_m = minute(selector)
    start, end = (AM_START, AM_END) if peak == "am" else (PM_START, PM_END)
    if sel_m is None or not (minute(start) <= sel_m <= minute(end)):
        return None
    measure = hs if peak == "am" else os_
    measure_s = scheduled_at(measure, "arrival")
    measure_a = actual_at(measure, "arrival")
    if not measure_s:
        # Public schedule absent: can't measure this selected journey reliably.
        return None
    journey_s = (scheduled_at(os_, "departure"), scheduled_at(hs, "arrival")) if peak == "am" else (scheduled_at(hs, "departure"), scheduled_at(os_, "arrival"))
    journey_len = forward_delta(*journey_s)
    if journey_len is None or journey_len == 0 or journey_len > 360:
        return None
    d = delay_minutes(measure_s, measure_a)
    # late_canc_reason can describe a delay, NOT necessarily a cancellation.
    # Do not infer cancellation solely from this code.
    reason = str(measure.get("late_canc_reason") or "")
    note = ""
    if d is not None:
        outcome = "late" if d >= LATE_THRESHOLD_MIN else "on_time"
    elif any(str(measure.get(k, "")).lower() in ("true", "cancelled", "canceled", "1") for k in ("isCancelled", "cancelled", "is_cancelled")):
        outcome = "cancelled"
    else:
        outcome = "missing"
        note = "No actual arrival; HSP does not prove cancellation without explicit status"
    first, last = (oi, hi) if peak == "am" else (hi, oi)
    date_str = detail.get("date_of_service", "")
    if not date_str:
        return None
    service_day = date.fromisoformat(date_str[:10])
    if service_day.weekday() >= 5 or str(service_day) in EXCLUDED_DATES:
        return None
    return {
        "source": "hsp", "station_crs": origin, "station_name": name, "hub_crs": hub,
        "peak": peak, "service_date": str(service_day), "train_id": str(detail.get("rid", "")),
        "operator": detail.get("toc_code", ""), "sched_time": measure_s, "actual_time": measure_a or "",
        "lateness_min": d if d is not None else "", "outcome": outcome,
        "failed": 1 if outcome in ("late", "cancelled") else 0 if outcome == "on_time" else "",
        "cancel_reason": reason if outcome == "cancelled" else "",
        "minutes_to_hub": journey_len, "scheduled_journey_min": journey_len,
        "stops_between": max(0, last - first - 1),
        "stops_before": oi, "sched_hour": int(selector[:2]),
        "planned_cancel_flag": "",  # HSP alone does not reliably expose this.
        "origin_scheduled": scheduled_at(os_, "departure" if peak == "am" else "arrival") or "",
        "origin_actual": actual_at(os_, "departure" if peak == "am" else "arrival") or "",
        "hub_scheduled": scheduled_at(hs, "arrival" if peak == "am" else "departure") or "",
        "hub_actual": actual_at(hs, "arrival" if peak == "am" else "departure") or "",
        "data_quality_note": note,
    }


def metrics_payload(origin, hub, peak, start, end):
    f, t = (origin, hub) if peak == "am" else (hub, origin)
    # Wide departure/arrival windows. Peak selection happens ONLY after fetching
    # serviceDetails. HSP from_time/to_time interpretation should be validated.
    from_time, to_time = ("0000", "1159") if peak == "am" else ("1200", "2359")
    return {"from_loc": f, "to_loc": t, "from_time": from_time, "to_time": to_time,
            "from_date": str(start), "to_date": str(end), "days": "WEEKDAY", "tolerance": ["5", "10"]}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stations", default=STATIONS_FILE)
    ap.add_argument("--start", default=START_DATE)
    ap.add_argument("--end", default=END_DATE)
    ap.add_argument("--output", default=OUTPUT_CSV)
    ap.add_argument("--test", action="store_true", help="last origin, first hub, one date")
    ap.add_argument("--workers", type=int, default=4, help="Concurrent requests (default: 4)")
    ap.add_argument("--rps", type=float, default=1.0, help="Maximum outgoing requests/sec (default: 1)")
    ap.add_argument("--chunk-days", type=int, default=14, help="Days per metrics query (default: 14)")
    ap.add_argument("--save-every", type=int, default=SAVE_EVERY,
                    help="Append CSV after this many selected train rows (default: 100)")
    args = ap.parse_args()
    if args.workers < 1 or args.rps <= 0 or args.chunk_days < 1 or args.save_every < 1:
        ap.error("--workers, --chunk-days, --rps, --save-every must be positive")
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    if end < start:
        ap.error("--end must be on or after --start")
    stations = load_stations(args.stations)[::-1]  # last station first
    if not stations:
        ap.error("No non-hub origin stations found")
    if args.test:
        stations, end = stations[:1], start
    load_parent_env()
    hsp = HSP(rps=args.rps)
    hubs = HUBS[:1] if args.test else HUBS
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    audit = Path(AUDIT_CSV)
    audit.parent.mkdir(parents=True, exist_ok=True)
    errors = 0
    total_rows = 0
    total_metrics = 0
    total_details = 0
    pending = []
    audit_fields = ["from_loc", "to_loc", "from_time", "to_time", "from_date", "to_date", "days", "tolerance", "matched_rids", "status"]

    def flush_rows(writer, handle):
        nonlocal total_rows
        if not pending:
            return
        writer.writerows(pending)
        total_rows += len(pending)
        pending.clear()
        handle.flush()
        os.fsync(handle.fileno())
        print(f"  saved {total_rows} train rows -> {output}", flush=True)

    # Output is overwritten at startup, then appended and fsynced in batches.
    # If interrupted, previously written batches remain available on disk.
    with output.open("w", newline="", encoding="utf-8") as out_f, \
         audit.open("w", newline="", encoding="utf-8") as audit_f:
        writer = csv.DictWriter(out_f, fieldnames=FIELDS)
        writer.writeheader()
        out_f.flush()
        audit_writer = csv.DictWriter(audit_f, fieldnames=audit_fields)
        audit_writer.writeheader()
        audit_f.flush()
        print(f"Processing {len(stations)} stations in REVERSE CSV order; no API caching; "
              f"save every {args.save_every} train rows; {args.rps:g} req/s cap", flush=True)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for idx, (origin, name) in enumerate(stations, 1):
                jobs = [(origin, name, peak, hub, metrics_payload(origin, hub, peak, a, b))
                        for peak in ("am", "pm") for hub in hubs
                        for a, b in date_chunks(start, end, args.chunk_days)]
                print(f"[{idx}/{len(stations)}] {origin} {name}: {len(jobs)} metrics queries", flush=True)

                def run_metrics(job):
                    return metric_rids(hsp.post("serviceMetrics", job[4]))

                rid_context = {}
                futures = {pool.submit(run_metrics, job): job for job in jobs}
                for fut in as_completed(futures):
                    origin_, name_, peak, hub, payload = futures[fut]
                    try:
                        rids = fut.result()
                        record = {**payload, "matched_rids": len(rids), "status": "ok"}
                        for rid in rids:
                            rid_context.setdefault(rid, set()).add((origin_, name_, peak))
                    except Exception as exc:
                        errors += 1
                        record = {**payload, "matched_rids": "", "status": str(exc)[:250]}
                        print(f"ERROR metrics {origin_} {hub} {peak}: {exc}", file=sys.stderr)
                    audit_writer.writerow(record)
                    total_metrics += 1
                audit_f.flush()
                os.fsync(audit_f.fileno())

                def run_detail(rid):
                    return normalize_detail(hsp.post("serviceDetails", {"rid": rid}))

                seen_rows = set()
                futures = {pool.submit(run_detail, rid): rid for rid in sorted(rid_context)}
                for fut in as_completed(futures):
                    rid = futures[fut]
                    total_details += 1
                    try:
                        detail = fut.result()
                        for origin_, name_, peak in sorted(rid_context[rid]):
                            row = build_one(detail, origin_, name_, peak)
                            if row and start <= date.fromisoformat(row["service_date"]) <= end:
                                key = (origin_, peak, row["service_date"], rid)
                                if key not in seen_rows:
                                    seen_rows.add(key)
                                    pending.append(row)
                                    if len(pending) >= args.save_every:
                                        flush_rows(writer, out_f)
                    except Exception as exc:
                        errors += 1
                        print(f"ERROR detail RID={rid}: {exc}", file=sys.stderr)
                # Flush partial batch at every station boundary as well.
                flush_rows(writer, out_f)
                print(f"  completed {origin}: {len(seen_rows)} rows, "
                      f"HTTP requests {hsp.network_requests}", flush=True)

    print(f"Finished: {total_rows} train rows in {output}; {total_metrics} metrics, "
          f"{total_details} details, {hsp.network_requests} HTTP attempts; {errors} errors")
    print("CAUTION: HSP may omit fully cancelled trains. Reconcile with a timetable before training cancellation labels.")
    if errors:
        sys.exit(2)


if __name__ == "__main__":
    main()
