"""Read/write pipeline tables as Parquet when pyarrow is available, CSV otherwise.

Why: the IBM LinuxONE container (s390x) has no pyarrow build, so pd.read_parquet fails there.
Every table is therefore written twice (``x.parquet`` + ``x.csv``) on machines that have pyarrow,
and read from whichever works. On the VM everything runs from the CSV copies.

CSV loses column types, so ``read_table`` restores them:
  * code-like columns (CRS codes, train ids, HHMM times, labels) stay as strings, so "0712"
    keeps its leading zero and a code such as "NAN" is never read as missing;
  * ``ts`` columns become datetimes; ``service_date`` becomes ``datetime.date`` (as Parquet gives);
  * everything else is converted to numbers / booleans where every value allows it.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

_STR_SUFFIXES = ("_crs", "_hhmm", "_uid", "_code", "_name", "_id", "_reason", "_indicator")
_STR_NAMES = {"headcode", "source", "peak", "outcome", "borough", "deprivation_third",
              "lsoa", "lsoa11cd", "lsoa21cd", "rid", "platform", "version"}


def parquet_available() -> bool:
    try:
        import pyarrow  # noqa: F401
        return True
    except ImportError:
        try:
            import fastparquet  # noqa: F401
            return True
        except ImportError:
            return False


def _csv_path(path: Path) -> Path:
    return Path(path).with_suffix(".csv")


def table_exists(path) -> bool:
    """True if the Parquet file (when readable here) or its CSV copy exists."""
    path = Path(path)
    return (parquet_available() and path.exists()) or _csv_path(path).exists()


def _is_str_col(name: str) -> bool:
    n = name.lower()
    return n in _STR_NAMES or n.endswith(_STR_SUFFIXES)


def _restore_types(df: pd.DataFrame) -> pd.DataFrame:
    for c in df.columns:
        if _is_str_col(c):
            continue
        col = df[c].replace("", np.nan)
        if c == "service_date":
            df[c] = pd.to_datetime(col).dt.date
            continue
        if c == "ts" or c.endswith("_ts"):
            df[c] = pd.to_datetime(col)
            continue
        vals = set(col.dropna().unique())
        if vals and vals <= {"True", "False"}:
            df[c] = col.map({"True": True, "False": False})
            continue
        try:
            df[c] = pd.to_numeric(col)
        except (ValueError, TypeError):
            df[c] = col
    return df


def read_table(path, columns=None) -> pd.DataFrame:
    """Read ``path`` (a .parquet path); fall back to the .csv copy if Parquet can't be read."""
    path = Path(path)
    if parquet_available() and path.exists():
        return pd.read_parquet(path, columns=columns)
    csv = _csv_path(path)
    if not csv.exists():
        raise FileNotFoundError(f"neither {path.name} (readable) nor {csv.name} found in {path.parent}")
    df = pd.read_csv(csv, dtype=str, keep_default_na=False, usecols=columns)
    return _restore_types(df)


def write_table(df: pd.DataFrame, path) -> None:
    """Write ``path`` as Parquet (if possible) and always a .csv copy next to it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if parquet_available():
        df.to_parquet(path, index=False)
    else:
        log.info("pyarrow not available: writing %s only", _csv_path(path).name)
    df.to_csv(_csv_path(path), index=False)
