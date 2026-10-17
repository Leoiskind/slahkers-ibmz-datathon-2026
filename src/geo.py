"""Station deprivation.

CURRENT: build_station_deprivation() (LSOA method) -> data/processed/station_deprivation.parquet
LEGACY (postcode method, superseded): build_stations() / build_station_area() below:
Stage 1: station coordinates (config lat/lon, else a NaPTAN-style CSV) -> nearest
         postcode via postcodes.io (cached) -> data/processed/stations.parquet
Stage 2: join postcode to the deprivation lookup -> data/processed/station_area.parquet

Run:  python -m src.geo      (builds station_deprivation)
"""
import json
import logging

import pandas as pd
import requests

from src.config import ROOT, load_config

log = logging.getLogger(__name__)
POSTCODES_IO = "https://api.postcodes.io/postcodes"


def norm_postcode(pc):
    """'M1 2DT' / 'm12dt' -> 'M12DT' (the lookup file stores postcodes with no space)."""
    return str(pc).upper().replace(" ", "")


def station_coords(cfg):
    """Return one row per configured station (origins + hubs) with lat/lon.

    Coordinates come from the station's own `lat`/`lon` in config.yaml if given,
    otherwise from `paths.stations_csv` (columns set by `stations_csv_columns`).
    """
    rows = [dict(s, role=role) for role, key in (("origin", "origins"), ("hub", "hubs"))
            for s in cfg[key]]
    df = pd.DataFrame(rows)
    for col in ("lat", "lon"):
        if col not in df:
            df[col] = float("nan")
    missing = df["lat"].isna() | df["lon"].isna()
    if missing.any():
        path = cfg["paths"].get("stations_csv")
        if not path:
            raise ValueError("No lat/lon in config for: "
                             f"{list(df.loc[missing, 'crs'])} and paths.stations_csv is not set")
        cols = cfg["paths"]["stations_csv_columns"]  # {crs:, lat:, lon:}
        ref = pd.read_csv(cfg["paths"]["data_dir"].parent / path)
        ref = ref.rename(columns={cols["crs"]: "crs", cols["lat"]: "ref_lat", cols["lon"]: "ref_lon"})
        ref = ref.drop_duplicates("crs")[["crs", "ref_lat", "ref_lon"]]
        df = df.merge(ref, on="crs", how="left")
        df["lat"] = df["lat"].fillna(df["ref_lat"])
        df["lon"] = df["lon"].fillna(df["ref_lon"])
        df = df.drop(columns=["ref_lat", "ref_lon"])
        unmatched = df[df["lat"].isna()]["crs"].tolist()
        if unmatched:
            raise ValueError(f"CRS codes not found in stations_csv (check them): {unmatched}")
    return df


def reverse_geocode(lat, lon, cache_dir):
    """Nearest postcode record for a point, cached on disk (safe to re-run)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    f = cache_dir / f"{lat:.5f}_{lon:.5f}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    r = requests.get(POSTCODES_IO, params={"lat": lat, "lon": lon, "limit": 1}, timeout=30)
    r.raise_for_status()
    result = r.json().get("result")
    if not result:
        raise ValueError(f"postcodes.io found no postcode near {lat},{lon}")
    f.write_text(json.dumps(result[0]), encoding="utf-8")
    return result[0]


def build_stations(cfg):
    df = station_coords(cfg)
    cache = cfg["paths"]["data_dir"] / "cache" / "postcodes"
    recs = [reverse_geocode(r.lat, r.lon, cache) for r in df.itertuples()]
    df["postcode"] = [norm_postcode(r["postcode"]) for r in recs]
    df["lsoa_name_api"] = [r.get("lsoa21") for r in recs]
    out = cfg["paths"]["data_dir"] / "processed" / "stations.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    log.info("stations: %d rows -> %s", len(df), out)
    return df


def build_station_area(cfg, stations):
    """Join each station's postcode to the deprivation lookup. Fails if any origin is missing."""
    lookup = pd.read_csv(cfg["paths"]["deprivation_lookup"])
    lookup["pc"] = lookup["Postcode"].map(norm_postcode)
    imd = "LSOA containing postcode - Index of Multiple Deprivation (IMD) "
    keep = {
        "pc": "postcode",
        "LSOA code (2021)": "lsoa_code",
        "LSOA name (2021)": "lsoa_name",
        "LAD name (2024)": "lad_name",
        imd + "Rank (where 1 is most deprived)": "imd_rank",
        imd + "Decile (where 1 is most deprived 10% of LSOAs)": "imd_decile",
    }
    for dom in ("Income", "Employment", "Health Deprivation and Disability", "Crime",
                "Education, Skills and Training", "Living Environment"):
        col = f"LSOA containing postcode - {dom} Decile (where 1 is most deprived 10% of LSOAs)"
        keep[col] = "decile_" + dom.split(",")[0].split()[0].lower()
    lookup = lookup[list(keep)].rename(columns=keep)
    out = stations.merge(lookup, on="postcode", how="left")
    unmatched = out[out["role"].eq("origin") & out["lsoa_code"].isna()]
    log.info("station_area: %d stations, %d origins unmatched", len(out), len(unmatched))
    if len(unmatched):
        raise ValueError(
            "Origin postcodes not in the deprivation lookup (outside its area?):\n"
            + unmatched[["crs", "name", "postcode"]].to_string(index=False))
    path = cfg["paths"]["data_dir"] / "processed" / "station_area.parquet"
    out.to_parquet(path, index=False)
    return out


IMD_RANK = "LSOA containing postcode - Index of Multiple Deprivation (IMD) Rank (where 1 is most deprived)"
IMD_DECILE = "LSOA containing postcode - Index of Multiple Deprivation (IMD) Decile (where 1 is most deprived 10% of LSOAs)"


def national_max_rank(cfg):
    """Largest IMD rank in the national file (= number of LSOAs in England)."""
    src = ROOT / cfg["paths"]["deprivation_source"]
    return max(int(c[IMD_RANK].max()) for c in pd.read_csv(src, usecols=[IMD_RANK], chunksize=500_000))


def build_station_deprivation(cfg):
    """LSOA method (docs: deprivation_method): each LSOA is assigned to its NEAREST station
    within 1.5 km (Cam's lsoa_station_matches.csv lists every station within 1.5 km, so keep
    the nearest), joined to IMD 2025. deprivation_pct = 1 - rank/max_rank (higher = more deprived).
    Note: the matches use 2011 LSOA codes, IMD uses 2021; codes that changed don't join (logged)."""
    m = pd.read_csv(ROOT / cfg["paths"]["lsoa_station_matches"])
    near = m.sort_values("DISTANCE").drop_duplicates("LSOA")
    imd = pd.read_csv(cfg["paths"]["deprivation_lookup"], usecols=["LSOA code (2021)", IMD_RANK, IMD_DECILE])
    imd = imd.drop_duplicates("LSOA code (2021)").rename(
        columns={"LSOA code (2021)": "LSOA", IMD_RANK: "imd_rank", IMD_DECILE: "imd_decile"})
    j = near.merge(imd, on="LSOA", how="inner")
    log.info("LSOAs: %d nearest-station matches, %d joined to IMD (%.1f%%)",
             len(near), len(j), 100 * len(j) / len(near))
    max_rank = national_max_rank(cfg)
    j["deprivation_pct"] = 1 - j["imd_rank"] / max_rank
    dep = j.groupby("STATION").agg(
        dep_score=("deprivation_pct", "mean"),
        dep_median=("deprivation_pct", "median"),
        dep_share_top20=("imd_decile", lambda d: (d <= 2).mean()),
        n_lsoa=("LSOA", "size"),
    ).reset_index().rename(columns={"STATION": "station_crs"})
    dep["low_n_lsoa"] = dep["n_lsoa"] < 3
    dep["max_rank"] = max_rank
    out = cfg["paths"]["data_dir"] / "processed" / "station_deprivation.parquet"
    dep.to_parquet(out, index=False)
    log.info("station_deprivation: %d stations (%d with < 3 LSOAs), max_rank %d -> %s",
             len(dep), int(dep["low_n_lsoa"].sum()), max_rank, out)
    return dep


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cfg = load_config()
    dep = build_station_deprivation(cfg)
    print(dep.sort_values("dep_score", ascending=False).round(3).to_string(index=False))


if __name__ == "__main__":
    main()
