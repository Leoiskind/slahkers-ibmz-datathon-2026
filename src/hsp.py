"""HSP client with on-disk caching.

DONE: authenticated, cached requests (fetch_metrics / fetch_details). Request body fields
come from api_requester_temp.py (from_loc, to_loc, from_time, to_time, from_date, to_date, days).
TODO (needs one real response, do not guess the JSON shape): flatten cached responses
into the common schema in build_service_level().
"""
import hashlib
import json
import time

import requests

from src.config import hsp_credentials, load_config


def _cached_post(endpoint, body, cache_dir, retries=3):
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(json.dumps([endpoint, body], sort_keys=True).encode()).hexdigest()[:16]
    f = cache_dir / f"{key}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    base, user, pw = hsp_credentials()
    for attempt in range(retries):
        r = requests.post(f"{base.rstrip('/')}/{endpoint}", json=body, auth=(user, pw), timeout=60)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        f.write_text(r.text, encoding="utf-8")
        time.sleep(0.5)  # be polite: HSP is slow and rate limited
        return r.json()
    r.raise_for_status()


def fetch_metrics(cfg, origin, dest, window):
    """serviceMetrics for one origin/hub/peak window over the configured dates."""
    body = {"from_loc": origin, "to_loc": dest,
            "from_time": window["from_time"], "to_time": window["to_time"],
            "from_date": cfg["dates"]["from"], "to_date": cfg["dates"]["to"],
            "days": cfg["dates"]["days"]}
    return _cached_post("serviceMetrics", body, cfg["paths"]["data_dir"] / "cache" / "hsp" / "metrics")


def fetch_details(cfg, rid):
    """serviceDetails for one train id."""
    return _cached_post("serviceDetails", {"rid": rid},
                        cfg["paths"]["data_dir"] / "cache" / "hsp" / "details")


def build_service_level(cfg):
    raise NotImplementedError(
        "HSP flatten is not written yet: run fetch_metrics/fetch_details once with real "
        "credentials, inspect a cached JSON in data/cache/hsp/, then implement this.")


if __name__ == "__main__":
    cfg = load_config()
    o, h = cfg["origins"][0]["crs"], cfg["hubs"][0]["crs"]
    print(json.dumps(fetch_metrics(cfg, o, h, cfg["peaks"]["morning_inbound"]), indent=2)[:2000])
