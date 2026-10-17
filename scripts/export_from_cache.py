#!/usr/bin/env python3
"""Export HSP journeys from existing cache ONLY. Does not make network requests.

Run from the same working directory as your original extractor, with the same
station file, date range, cache location and HSP_BASE_URL.
"""
import argparse
import csv
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import re

HUBS = ('MAN', 'MCO', 'MCV', 'DGT')
AM_START, AM_END = '0700', '0959'
PM_START, PM_END = '1600', '1859'
FIELDS = ['source', 'station_crs', 'station_name', 'hub_crs', 'peak', 'service_date',
          'train_id', 'operator', 'sched_time', 'actual_time', 'lateness_min',
          'outcome', 'failed', 'cancel_reason', 'minutes_to_hub', 'scheduled_journey_min',
          'stops_between', 'stops_before', 'sched_hour', 'planned_cancel_flag',
          'origin_scheduled', 'origin_actual', 'hub_scheduled', 'hub_actual',
          'data_quality_note']


def minute(hhmm):
    if hhmm is None:
        return None
    s = str(hhmm).strip().replace(':', '')
    if not re.fullmatch(r'\d{4}', s):
        return None
    h, m = int(s[:2]), int(s[2:])
    return h * 60 + m if 0 <= h < 24 and 0 <= m < 60 else None


def forward_delta(start, end):
    a, b = minute(start), minute(end)
    return None if a is None or b is None else (b - a) % 1440


def delay_minutes(scheduled, actual):
    a, b = minute(scheduled), minute(actual)
    if a is None or b is None:
        return None
    return max(0, (b - a + 720) % 1440 - 720)


def date_chunks(first, last, chunk_days=7):
    current = first
    while current <= last:
        stop = min(last, current + timedelta(days=chunk_days - 1))
        yield current, stop
        current = stop + timedelta(days=1)


def load_stations(path):
    with open(path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        if not {'Station Code', 'Station Name'}.issubset(reader.fieldnames or []):
            raise ValueError('Station CSV needs Station Code and Station Name columns')
        entries = []
        for row in reader:
            crs = (row['Station Code'] or '').strip().upper()
            if not crs or crs in HUBS:
                continue
            if not re.fullmatch(r'[A-Z0-9]{3}', crs):
                raise ValueError(f'Invalid CRS: {crs!r}')
            entries.append((crs, (row['Station Name'] or '').strip()))
    return list(dict.fromkeys(entries))


def metrics_payload(origin, hub, peak, start, end):
    f, t = (origin, hub) if peak == 'am' else (hub, origin)
    from_time, to_time = ('0000', '1159') if peak == 'am' else ('1200', '2359')
    return {'from_loc': f, 'to_loc': t, 'from_time': from_time, 'to_time': to_time,
            'from_date': str(start), 'to_date': str(end), 'days': 'WEEKDAY',
            'tolerance': ['5', '10']}


def cache_path(cache_root, base_url, endpoint, payload):
    key = hashlib.sha256(json.dumps([base_url, endpoint, payload], sort_keys=True).encode()).hexdigest()
    return cache_root / endpoint / (key + '.json')


def metric_rids(response):
    services = response.get('Services', response.get('services', []))
    if not isinstance(services, list):
        raise ValueError('Unexpected serviceMetrics response')
    ids = set()
    for entry in services:
        attrs = entry.get('serviceAttributesMetrics', {})
        rids = attrs.get('rids', [])
        if isinstance(rids, str):
            rids = [rids]
        ids.update(str(rid) for rid in (rids or []) if rid)
    return ids


def normalize_detail(response):
    d = response.get('serviceAttributesDetails')
    if not isinstance(d, dict) or not isinstance(d.get('locations'), list):
        raise ValueError('Unexpected serviceDetails response shape')
    return d


def scheduled_at(loc, direction):
    if direction == 'arrival':
        return loc.get('gbtt_pta') or loc.get('gbtt_ptd')
    return loc.get('gbtt_ptd') or loc.get('gbtt_pta')


def actual_at(loc, direction):
    if direction == 'arrival':
        return loc.get('actual_ta') or loc.get('actual_td')
    return loc.get('actual_td') or loc.get('actual_ta')


def build_one(detail, origin, name, peak, late_threshold=5, excluded_dates=frozenset()):
    locs = detail['locations']
    origin_indices = [i for i, s in enumerate(locs) if str(s.get('location', '')).upper() == origin]
    options = []
    for oi in origin_indices:
        for hi, stop in enumerate(locs):
            hub = str(stop.get('location', '')).upper()
            if hub not in HUBS:
                continue
            if peak == 'am' and oi < hi:
                options.append((oi, hi))
            if peak == 'pm' and hi < oi:
                options.append((oi, hi))
    if not options:
        return None
    oi = min(oi for oi, hi in options)
    hubs_for_origin = [hi for o, hi in options if o == oi]
    hi = min(hubs_for_origin) if peak == 'am' else max(hubs_for_origin)
    os_, hs = locs[oi], locs[hi]
    hub = str(hs['location']).upper()
    selector = scheduled_at(hs, 'arrival' if peak == 'am' else 'departure')
    sel_m = minute(selector)
    start, end = (AM_START, AM_END) if peak == 'am' else (PM_START, PM_END)
    if sel_m is None or not (minute(start) <= sel_m <= minute(end)):
        return None
    measure = hs if peak == 'am' else os_
    measure_s = scheduled_at(measure, 'arrival')
    measure_a = actual_at(measure, 'arrival')
    if not measure_s:
        return None
    journey_s = ((scheduled_at(os_, 'departure'), scheduled_at(hs, 'arrival'))
                 if peak == 'am' else
                 (scheduled_at(hs, 'departure'), scheduled_at(os_, 'arrival')))
    journey_len = forward_delta(*journey_s)
    if journey_len is None or journey_len == 0 or journey_len > 360:
        return None
    d = delay_minutes(measure_s, measure_a)
    reason = str(measure.get('late_canc_reason') or '')
    note = ''
    if d is not None:
        outcome = 'late' if d >= late_threshold else 'on_time'
    elif any(str(measure.get(k, '')).lower() in ('true', 'cancelled', 'canceled', '1')
             for k in ('isCancelled', 'cancelled', 'is_cancelled')):
        outcome = 'cancelled'
    else:
        outcome = 'missing'
        note = 'No actual arrival; HSP does not prove cancellation without explicit status'
    first, last = (oi, hi) if peak == 'am' else (hi, oi)
    date_str = detail.get('date_of_service', '')
    if not date_str:
        return None
    service_day = date.fromisoformat(date_str[:10])
    if service_day.weekday() >= 5 or str(service_day) in excluded_dates:
        return None
    return {
        'source': 'hsp', 'station_crs': origin, 'station_name': name, 'hub_crs': hub,
        'peak': peak, 'service_date': str(service_day), 'train_id': str(detail.get('rid', '')),
        'operator': detail.get('toc_code', ''), 'sched_time': measure_s, 'actual_time': measure_a or '',
        'lateness_min': d if d is not None else '', 'outcome': outcome,
        'failed': 1 if outcome in ('late', 'cancelled') else 0 if outcome == 'on_time' else '',
        'cancel_reason': reason if outcome == 'cancelled' else '',
        'minutes_to_hub': journey_len, 'scheduled_journey_min': journey_len,
        'stops_between': max(0, last - first - 1), 'stops_before': oi,
        'sched_hour': int(selector[:2]), 'planned_cancel_flag': '',
        'origin_scheduled': scheduled_at(os_, 'departure' if peak == 'am' else 'arrival') or '',
        'origin_actual': actual_at(os_, 'departure' if peak == 'am' else 'arrival') or '',
        'hub_scheduled': scheduled_at(hs, 'arrival' if peak == 'am' else 'departure') or '',
        'hub_actual': actual_at(hs, 'arrival' if peak == 'am' else 'departure') or '',
        'data_quality_note': note,
    }


def load_env(path):
    if not path.is_file():
        return
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].strip()
        if '=' not in line:
            continue
        key, value = line.split('=', 1)
        key, value = key.strip(), value.strip()
        if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', key):
            continue
        if value.startswith(('"', "'")) and value.endswith(value[0]):
            value = value[1:-1]
        else:
            value = value.split(' #', 1)[0].strip()
        os.environ.setdefault(key, value)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stations', default='stations.csv')
    p.add_argument('--start', default='2026-10-05')
    p.add_argument('--end', default='2026-10-16')
    p.add_argument('--cache', default='data/cache/hsp')
    p.add_argument('--output', default='data/processed/journeys_hsp.csv')
    p.add_argument('--audit', default='data/processed/hsp_cache_export_audit.csv')
    p.add_argument('--base-url', default=None, help='Same HSP_BASE_URL used by original extractor')
    p.add_argument('--env-file', default=None, help='Path to .env (default: script parent directory/.env)')
    p.add_argument('--chunk-days', type=int, default=7)
    args = p.parse_args()
    if args.chunk_days <= 0:
        p.error('--chunk-days must be positive')
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    if end < start:
        p.error('--end must be on or after --start')
    env_path = Path(args.env_file) if args.env_file else Path(__file__).resolve().parent.parent / '.env'
    load_env(env_path)
    base_url = (args.base_url or os.getenv('HSP_BASE_URL', 'https://hsp-prod.rockshore.net/api/v1')).rstrip('/')
    cache = Path(args.cache)
    stations = load_stations(args.stations)
    found, audits = {}, []
    missing_metrics, missing_details, corrupt_files = 0, set(), 0
    detail_cache = {}
    for origin, name in stations:
        rid_by_peak = {'am': set(), 'pm': set()}
        for peak in ('am', 'pm'):
            for hub in HUBS:
                for a, b in date_chunks(start, end, args.chunk_days):
                    payload = metrics_payload(origin, hub, peak, a, b)
                    path = cache_path(cache, base_url, 'serviceMetrics', payload)
                    status, count = 'not_cached', ''
                    if path.is_file():
                        try:
                            rids = metric_rids(json.loads(path.read_text(encoding='utf-8')))
                            rid_by_peak[peak].update(rids)
                            status, count = 'cached', len(rids)
                        except (OSError, ValueError, TypeError, AttributeError) as e:
                            status = f'error: {str(e)[:150]}'
                            corrupt_files += 1
                    else:
                        missing_metrics += 1
                    audits.append({**payload, 'matched_rids': count, 'status': status})
        selected = 0
        for peak, rids in rid_by_peak.items():
            for rid in sorted(rids):
                if rid not in detail_cache:
                    path = cache_path(cache, base_url, 'serviceDetails', {'rid': rid})
                    if not path.is_file():
                        missing_details.add(rid)
                        detail_cache[rid] = None
                    else:
                        try:
                            detail_cache[rid] = normalize_detail(json.loads(path.read_text(encoding='utf-8')))
                        except (OSError, ValueError, TypeError, AttributeError) as e:
                            print(f'ERROR cached details RID={rid}: {e}')
                            detail_cache[rid] = None
                            corrupt_files += 1
                detail = detail_cache[rid]
                if detail is None:
                    continue
                try:
                    row = build_one(detail, origin, name, peak)
                    if row:
                        found[(origin, peak, row['service_date'], rid)] = row
                        selected += 1
                except (ValueError, TypeError, KeyError, IndexError) as e:
                    print(f'ERROR building RID={rid} station={origin}: {e}')
                    corrupt_files += 1
        print(f'{origin} {name}: {selected} selected rows', flush=True)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replace avoids leaving a partly written CSV.
    tmp = out.with_name(out.name + '.tmp')
    with tmp.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(sorted(found.values(), key=lambda r: (
            r['station_crs'], r['service_date'], r['peak'], r['train_id'])))
    tmp.replace(out)
    audit = Path(args.audit)
    audit.parent.mkdir(parents=True, exist_ok=True)
    with audit.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=[
            'from_loc', 'to_loc', 'from_time', 'to_time', 'from_date', 'to_date',
            'days', 'tolerance', 'matched_rids', 'status'])
        writer.writeheader()
        writer.writerows(audits)
    print(f'Wrote {len(found)} journeys to {out} (NO API calls)')
    print(f'Uncached metrics requests: {missing_metrics}; uncached detail RIDs: {len(missing_details)}; malformed results: {corrupt_files}')
    print(f'Cache audit: {audit}')
    print('WARNING: This is a partial dataset. HSP may omit fully cancelled trains.')
    if missing_metrics == len(audits) and audits:
        print('WARNING: No matching metrics cache found. Check --cache, dates, --base-url and --chunk-days.')

if __name__ == '__main__':
    main()
