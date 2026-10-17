"""SYNTHETIC outcomes on the REAL timetable, for testing the model pipeline only.

Each planned train in data/processed/timetable_trains.parquet gets a fake `failed` (late or
cancelled) drawn from a known logistic model:

  logit P(failed) = b0 + 0.4 z(minutes_to_hub) + 0.3 z(stops_before) + 0.2 z(stops_between)
                    + 0.3 [hub time 08:00-08:59 or 17:00-17:59]
                    + station noise N(0, 0.25) + day noise N(0, 0.3)
                    + effect * z(dep_score)          <- the PLANTED deprivation effect

b0 is solved so the overall failure rate is `base_rate`. With effect > 0 the model's
residuals should correlate positively with dep_score; with effect = 0 they should not.
Every row has source="synthetic". NEVER present these numbers as results.
"""
import logging

import numpy as np
import pandas as pd
from scipy.special import expit

log = logging.getLogger(__name__)


def _z(x):
    x = x.astype(float)
    return (x - x.mean()) / (x.std() or 1.0)


def make_outcomes(trains, dep, effect=0.5, base_rate=0.15, seed=0):
    """Return `trains` (stations with a dep_score only) plus sched_hour, failed, source."""
    rng = np.random.default_rng(seed)
    df = trains.merge(dep[["station_crs", "dep_score"]], on="station_crs", how="inner").copy()
    hub_time = df["sched_hub_hhmm"].astype(str).str.zfill(4)
    df["sched_hour"] = hub_time.str[:2].astype(int) + hub_time.str[2:].astype(int) / 60

    stations = df["station_crs"].unique()
    dates = df["service_date"].unique()
    st_noise = dict(zip(stations, rng.normal(0, 0.25, len(stations))))
    day_noise = dict(zip(dates, rng.normal(0, 0.3, len(dates))))
    peak_hour = df["sched_hour"].floordiv(1).isin([8, 17]).astype(float)
    dep_z = df["station_crs"].map(dict(zip(dep["station_crs"], _z(dep["dep_score"]))))

    eta = (0.4 * _z(df["minutes_to_hub"]) + 0.3 * _z(df["stops_before"])
           + 0.2 * _z(df["stops_between"]) + 0.3 * peak_hour
           + df["station_crs"].map(st_noise) + df["service_date"].map(day_noise)
           + effect * dep_z).to_numpy()
    lo, hi = -10.0, 5.0  # solve b0 so mean(P) = base_rate
    for _ in range(60):
        b0 = (lo + hi) / 2
        lo, hi = (b0, hi) if expit(b0 + eta).mean() < base_rate else (lo, b0)
    p = expit(b0 + eta)
    df["failed"] = rng.random(len(df)) < p
    df["source"] = "synthetic"
    df = df.drop(columns=["dep_score"])
    log.warning("SYNTHETIC outcomes (effect=%.2f, seed=%d, %d trains, failure rate %.3f): "
                "pipeline testing only, never results", effect, seed, len(df), df["failed"].mean())
    return df
