"""Hourly weather per GM station from the Open-Meteo historical archive (keyless, CC BY 4.0),
cached on disk. One call per station returns every hour of the date range.

Stations: every GM station in cfg["timetable"]["gm_stations_csv"].
Dates: first..last service_date in data/processed/timetable_trains.parquet (override with --from/--to).
Output: data/processed/weather_hourly.parquet: station_crs, ts, rain_mm, wind_kmh.
add_weather() joins it onto trains by station + hour of the scheduled time AT THAT STATION.

Run:  python -m src.weather [--from 2026-05-18] [--to 2026-10-16]
"""
import argparse
import json
import logging

import pandas as pd
import requests

from src.config import ROOT, load_config

log = logging.getLogger(__name__)
URL = "https://archive-api.open-meteo.com/v1/archive"
HOURLY = "precipitation,wind_speed_10m"


def fetch_station(crs, lat, lon, start, end, cache_dir):
    cache_dir.mkdir(parents=True, exist_ok=True)
    f = cache_dir / f"{crs}_{start}_{end}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8")), False
    r = requests.get(URL, timeout=60, params={
        "latitude": lat, "longitude": lon, "start_date": start, "end_date": end,
        "hourly": HOURLY, "timezone": "Europe/London", "wind_speed_unit": "kmh"})
    r.raise_for_status()
    f.write_text(r.text, encoding="utf-8")
    return r.json(), True


def build_weather(cfg, start=None, end=None):
    proc = cfg["paths"]["data_dir"] / "processed"
    if not (start and end):
        dates = pd.read_parquet(proc / "timetable_trains.parquet", columns=["service_date"])["service_date"]
        start, end = start or str(dates.min()), end or str(dates.max())
    stations = pd.read_csv(ROOT / cfg["timetable"]["gm_stations_csv"])
    cache = cfg["paths"]["data_dir"] / "cache" / "weather"
    frames, calls = [], 0
    for s in stations.itertuples(index=False):
        d, new = fetch_station(s[0], s.Latitude, s.Longitude, start, end, cache)
        calls += new
        u = d["hourly_units"]
        if (u["precipitation"], u["wind_speed_10m"]) != ("mm", "km/h"):
            raise ValueError(f"unexpected Open-Meteo units: {u}")
        h = d["hourly"]
        frames.append(pd.DataFrame({"station_crs": s[0], "ts": pd.to_datetime(h["time"]),
                                    "rain_mm": h["precipitation"], "wind_kmh": h["wind_speed_10m"]}))
    out = pd.concat(frames, ignore_index=True)
    missing = int(out[["rain_mm", "wind_kmh"]].isna().any(axis=1).sum())
    out.to_parquet(proc / "weather_hourly.parquet", index=False)
    print(f"weather: {len(out)} hourly rows, {out.station_crs.nunique()} stations, {start}..{end}, "
          f"{missing} hours missing; {calls} network calls -> {proc / 'weather_hourly.parquet'}")
    return out


def add_weather(journeys, weather):
    """Add rain_mm, wind_kmh for the hour of the scheduled time at the train's GM station:
    morning = departure from the origin (sched_from_hhmm), evening = arrival there (sched_to_hhmm)."""
    df = journeys.copy()
    hhmm = df["sched_from_hhmm"].where(df["peak"] == "morning", df["sched_to_hhmm"]).astype(str).str.zfill(4)
    df["ts"] = pd.to_datetime(df["service_date"].astype(str)) + pd.to_timedelta(hhmm.str[:2].astype(int), unit="h")
    out = df.merge(weather[["station_crs", "ts", "rain_mm", "wind_kmh"]], on=["station_crs", "ts"], how="left")
    if len(out) != len(df):
        raise ValueError("weather join changed the number of rows (duplicate weather hours?)")
    log.info("weather matched for %d of %d trains", out["rain_mm"].notna().sum(), len(out))
    return out.drop(columns="ts")


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start")
    ap.add_argument("--to", dest="end")
    args = ap.parse_args()
    build_weather(load_config(), args.start, args.end)


if __name__ == "__main__":
    main()
