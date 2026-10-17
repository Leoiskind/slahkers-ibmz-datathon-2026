#!/usr/bin/env python3
"""Merge two HSP journey CSVs, removing overlapping journeys and auditing conflicts.

Usage:
    python merge_hsp_csv.py original.csv new.csv
    python merge_hsp_csv.py original.csv new.csv -o merged.csv --prefer new

Identity follows the extractor: (station_crs, peak, service_date, train_id).
All original columns are preserved. When duplicates disagree, the preferred file
wins, and both records are written to the conflict report.
"""
import argparse
import csv
from collections import Counter
from pathlib import Path

FIELDS = [
    'source', 'station_crs', 'station_name', 'hub_crs', 'peak', 'service_date',
    'train_id', 'operator', 'sched_time', 'actual_time', 'lateness_min',
    'outcome', 'failed', 'cancel_reason', 'minutes_to_hub',
    'scheduled_journey_min', 'stops_between', 'stops_before', 'sched_hour',
    'planned_cancel_flag', 'origin_scheduled', 'origin_actual',
    'hub_scheduled', 'hub_actual', 'data_quality_note',
]
KEY = ('station_crs', 'peak', 'service_date', 'train_id')


def read_csv(path):
    with path.open(newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        missing = [field for field in FIELDS if field not in headers]
        if missing:
            raise ValueError(f'{path} missing columns: {", ".join(missing)}')
        extra = [field for field in headers if field not in FIELDS]
        if extra:
            print(f'Note: ignoring extra columns in {path}: {extra}')
        for line, raw in enumerate(reader, start=2):
            if None in raw:
                raise ValueError(f'{path}:{line} contains more fields than headers')
            row = {field: (raw.get(field) or '').strip() for field in FIELDS}
            if not any(row.values()):
                continue
            key = tuple(row[field] for field in KEY)
            if not all(key):
                raise ValueError(f'{path}:{line} has missing journey identifier: {key}')
            yield line, row


def write_csv(path, headers, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('original', type=Path, help='Original HSP CSV')
    parser.add_argument('new', type=Path, help='New HSP CSV from reverse run')
    parser.add_argument('-o', '--output', type=Path, default=Path('journeys_hsp_merged.csv'))
    parser.add_argument('--prefer', choices=('original', 'new'), default='original',
                        help='Which dataset wins when overlapping rows differ (default: original)')
    parser.add_argument('--conflicts', type=Path, default=Path('hsp_merge_conflicts.csv'))
    args = parser.parse_args()

    originals = list(read_csv(args.original))
    additions = list(read_csv(args.new))
    index = {}
    origin_by_key = {}
    counters = Counter()
    conflicts = []

    for origin, data in (('original', originals), ('new', additions)):
        for line, row in data:
            key = tuple(row[k] for k in KEY)
            if key not in index:
                index[key] = row
                origin_by_key[key] = (origin, line)
                counters[f'unique_from_{origin}'] += 1
                continue

            counters['overlapping_rows'] += 1
            existing = index[key]
            if existing == row:
                counters['identical_overlaps'] += 1
                continue

            counters['conflicting_overlaps'] += 1
            previous_origin, previous_line = origin_by_key[key]
            winner = args.prefer if args.prefer in (origin, previous_origin) else previous_origin
            changed = [col for col in FIELDS if existing[col] != row[col]]
            conflicts.append({
                **{field: row[field] for field in KEY},
                'first_file': previous_origin,
                'first_line': previous_line,
                'second_file': origin,
                'second_line': line,
                'differing_columns': ';'.join(changed),
                'kept_from': winner,
                **{f'first_{field}': existing[field] for field in FIELDS},
                **{f'second_{field}': row[field] for field in FIELDS},
            })
            if winner == origin:
                index[key] = row
                origin_by_key[key] = (origin, line)

    sorted_rows = sorted(index.values(), key=lambda row: (
        row['station_crs'], row['service_date'], row['peak'], row['train_id']))
    conflict_fields = [*KEY, 'first_file', 'first_line', 'second_file', 'second_line',
                       'differing_columns', 'kept_from',
                       *(f'first_{field}' for field in FIELDS),
                       *(f'second_{field}' for field in FIELDS)]
    if args.output.resolve() in (args.original.resolve(), args.new.resolve()):
        parser.error('Output must differ from both input files to prevent overwriting input data')
    if args.conflicts.resolve() in (args.original.resolve(), args.new.resolve(), args.output.resolve()):
        parser.error('Conflict report must have its own filename')
    write_csv(args.output, FIELDS, sorted_rows)
    write_csv(args.conflicts, conflict_fields, conflicts)
    print(f'Original input rows:      {len(originals)}')
    print(f'New input rows:           {len(additions)}')
    print(f'Overlapping rows:         {counters["overlapping_rows"]}')
    print(f'  Identical:              {counters["identical_overlaps"]}')
    print(f'  Different:              {counters["conflicting_overlaps"]}')
    print(f'Merged unique journeys:   {len(sorted_rows)}')
    print(f'Merged CSV:               {args.output}')
    print(f'Conflict audit:           {args.conflicts}')


if __name__ == '__main__':
    main()
