"""Hourly weather per station from Open-Meteo (keyless), cached on disk.

Reads data/processed/stations.parquet (from src.geo), writes
data/processed/weather_hourly.parquet: crs, ts, rain_mm, wind_kph, temp_c.

Run:  python -m src.weather
"""
import json

import pandas as pd
import requests

from src.config import load_config

URL = "https://archive-api.open-meteo.com/v1/archive"
HOURLY = "precipitation,wind_speed_10m,temperature_2m"


def fetch_station(crs, lat, lon, start, end, cache_dir):
    cache_dir.mkdir(parents=True, exist_ok=True)
    f = cache_dir / f"{crs}_{start}_{end}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    r = requests.get(URL, timeout=60, params={
        "latitude": lat, "longitude": lon, "start_date": start, "end_date": end,
        "hourly": HOURLY, "timezone": "Europe/London"})
    r.raise_for_status()
    f.write_text(r.text, encoding="utf-8")
    return r.json()


def build_weather(cfg):
    stations = pd.read_parquet(cfg["paths"]["data_dir"] / "processed" / "stations.parquet")
    cache = cfg["paths"]["data_dir"] / "cache" / "weather"
    frames = []
    for s in stations.itertuples():
        h = fetch_station(s.crs, s.lat, s.lon, cfg["dates"]["from"], cfg["dates"]["to"], cache)["hourly"]
        frames.append(pd.DataFrame({
            "crs": s.crs, "ts": pd.to_datetime(h["time"]),
            "rain_mm": h["precipitation"], "wind_kph": h["wind_speed_10m"],
            "temp_c": h["temperature_2m"]}))
    out = pd.concat(frames, ignore_index=True)
    path = cfg["paths"]["data_dir"] / "processed" / "weather_hourly.parquet"
    out.to_parquet(path, index=False)
    print(f"weather: {len(out)} rows, {out['crs'].nunique()} stations -> {path}")
    return out


if __name__ == "__main__":
    build_weather(load_config())
