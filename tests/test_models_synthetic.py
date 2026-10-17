"""Planted-effect smoke test: SYNTHETIC outcomes on the real GM timetable.

1. effect > 0: the main model's position-adjusted rho must be positive with a CI above 0.
2. effect = 0: adjusted rho should be near 0.
3. A forbidden feature (e.g. dep_score) must be rejected.

Needs data/processed/timetable_trains.parquet (python -m src.timetable) and
station_deprivation.parquet (python -m src.geo).
Run:  python tests/test_models_synthetic.py      (or: pytest tests/)
"""
import logging
import sys
import tempfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.config import load_config  # noqa: E402
from src.models import ModelConfig, check_features, run_peak  # noqa: E402
from src.synthetic import make_outcomes  # noqa: E402
from src.weather import add_weather  # noqa: E402

N_DATES = 20  # a sample of weekdays keeps the test quick


def _data(effect, seed):
    proc = load_config()["paths"]["data_dir"] / "processed"
    trains = pd.read_parquet(proc / "timetable_trains.parquet")
    dates = sorted(trains["service_date"].unique())[-N_DATES:]
    trains = trains[trains["service_date"].isin(dates) & (trains["peak"] == "morning")]
    dep = pd.read_parquet(proc / "station_deprivation.parquet")
    if (proc / "weather_hourly.parquet").exists():
        trains = add_weather(trains, pd.read_parquet(proc / "weather_hourly.parquet"))
    return make_outcomes(trains, dep, effect=effect, seed=seed), dep


def _adjusted(effect, seed=0):
    journeys, dep = _data(effect, seed)
    cfg = ModelConfig(seed=seed, n_boot=500, models=("logreg", "xgb"))
    with tempfile.TemporaryDirectory() as tmp:
        meta = run_peak(journeys, dep, "morning", cfg, tmp, "test")
    return meta


def test_planted_effect_is_recovered():
    meta = _adjusted(effect=0.5)
    t = meta["tests"]["logreg"]["adjusted"]
    assert meta["metrics"]["logreg"]["roc_auc"] > 0.5
    assert t["rho"] > 0.3 and t["lo"] > 0, t


def test_no_effect_gives_no_signal():
    t = _adjusted(effect=0.0)["tests"]["logreg"]["adjusted"]
    assert abs(t["rho"]) < 0.3, t


def test_forbidden_feature_is_rejected():
    try:
        check_features(["minutes_to_hub", "dep_score"])
    except ValueError:
        return
    raise AssertionError("dep_score was accepted as a feature")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"PASS {name}")
