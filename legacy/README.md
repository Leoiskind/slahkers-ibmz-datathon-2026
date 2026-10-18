# Legacy code (not used by the pipeline)

Kept for reference. Nothing in `run_all.py`, `src/` or `tests/` imports these files.

| File | What it was |
|---|---|
| `src/metrics.py`, `src/schema.py`, `src/trains.py` | The first design (postcode deprivation + a generic "service level" schema), replaced by `src/journeys.py`, `src/evidence.py` and the LSOA method in `src/geo.py` |
| `src/hsp.py` | Early HSP fetch-and-cache client; the data was extracted with `scripts/hsp_extract.py` instead |
| `scripts/filter_csv.py` | One-off helper to filter a CSV by keyword |
| `scripts/hsp_request_example.py` | Notes on how to query the HSP API. `USERNAME`/`PASSWORD` are deliberately blank: never fill them in a committed file (use `.env`) |
