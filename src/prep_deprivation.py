"""Filter the national postcode deprivation lookup down to a list of local authorities.

Unlike filter_csv.py (raw substring match), this matches the 'LAD name (2024)' column
exactly, so 'Bury' cannot match unrelated rows. Settings come from config.yaml:
  paths.deprivation_source (zip or csv), paths.deprivation_lookup (output),
  deprivation_lads (list of LAD names).

Run:  python -m src.prep_deprivation
"""
import pandas as pd

from src.config import ROOT, load_config

LAD_COL = "LAD name (2024)"


def main():
    cfg = load_config()
    src = ROOT / cfg["paths"]["deprivation_source"]
    out = cfg["paths"]["deprivation_lookup"]
    lads = set(cfg["deprivation_lads"])
    out.parent.mkdir(parents=True, exist_ok=True)
    kept, first = 0, True
    for chunk in pd.read_csv(src, chunksize=200_000, dtype=str):
        sub = chunk[chunk[LAD_COL].isin(lads)]
        sub.to_csv(out, mode="w" if first else "a", header=first, index=False)
        kept, first = kept + len(sub), False
    found = set(pd.read_csv(out, usecols=[LAD_COL], dtype=str)[LAD_COL])
    print(f"{kept} postcodes -> {out}")
    if found != lads:
        print(f"WARNING: LADs requested but not found: {sorted(lads - found)}")


if __name__ == "__main__":
    main()
