"""The one table every train-data source must produce: data/processed/service_level.parquet.

One row per train journey (one origin -> one hub). Sources (HSP, Maven mock) are adapters
that return this shape; all later stages (metrics, models, api) read only this.
Per-stop fields are optional: HSP fills them, the Maven mock leaves them null.
"""
import pandas as pd

REQUIRED = {
    "rid": "string",                  # unique train/journey id
    "date": "datetime64[ns]",         # service date
    "origin": "string",               # CRS
    "dest": "string",                 # CRS of the hub
    "sched_dep": "datetime64[ns]",
    "sched_arr": "datetime64[ns]",
    "actual_arr": "datetime64[ns]",   # NaT if cancelled
    "delay_min": "float64",           # actual_arr - sched_arr in minutes, NaN if cancelled
    "cancelled": "bool",
    "reason": "string",               # delay/cancel reason code or text, may be null
    "source": "string",               # 'hsp' or 'mock'
}
OPTIONAL = {
    "stops_made": "Int64",              # HSP only
    "origin_delay_min": "float64",      # delay at the first stop (upstream-delay feature), HSP only
    "prev_stop_delay_min": "float64",   # HSP only
}


def conform(df):
    """Check required columns exist, add missing optional ones as null, cast dtypes."""
    missing = set(REQUIRED) - set(df.columns)
    if missing:
        raise ValueError(f"service_level is missing columns: {sorted(missing)}")
    for col, dtype in OPTIONAL.items():
        if col not in df:
            df[col] = pd.Series([None] * len(df), index=df.index, dtype=dtype)
    df = df[list(REQUIRED) + list(OPTIONAL)].astype({**REQUIRED, **OPTIONAL})
    if df["rid"].duplicated().any():
        raise ValueError("duplicate rid values in service_level")
    return df
