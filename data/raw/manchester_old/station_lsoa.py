
import pandas as pd
import numpy as np
from scipy.spatial import cKDTree

# =====================================
# CONFIGURATION
# =====================================

STATIONS_FILE = "stations.csv"
LSOAS_FILE = "lsoas.csv"

OUTPUT_MATCHED = "lsoa_station_matches.csv"
OUTPUT_UNMATCHED = "lsoa_unmatched.csv"

MAX_DISTANCE_KM = 1.5
EARTH_RADIUS_KM = 6371.0088


# =====================================
# HAVERSINE DISTANCE (KM)
# =====================================

def haversine(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(
        np.radians, [lat1, lon1, lat2, lon2]
    )

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        np.sin(dlat / 2) ** 2
        + np.cos(lat1) * np.cos(lat2)
        * np.sin(dlon / 2) ** 2
    )

    return 2 * EARTH_RADIUS_KM * np.arcsin(
        np.sqrt(np.clip(a, 0, 1))
    )


# =====================================
# CONVERT LAT/LONG TO 3D COORDINATES
# =====================================

def to_xyz(lat, lon):
    lat = np.radians(np.asarray(lat))
    lon = np.radians(np.asarray(lon))

    x = np.cos(lat) * np.cos(lon)
    y = np.cos(lat) * np.sin(lon)
    z = np.sin(lat)

    return np.column_stack((x, y, z))


# =====================================
# LOAD DATA
# =====================================

stations = pd.read_csv(
    STATIONS_FILE,
    dtype={"STATION": str}
)

lsoas = pd.read_csv(
    LSOAS_FILE,
    dtype={"LSOA": str}
)

from pyproj import Transformer

# Convert British National Grid to WGS84
transformer = Transformer.from_crs(
    "EPSG:27700",
    "EPSG:4326",
    always_xy=True
)

# LSOA LONG currently contains Easting
# LSOA LAT currently contains Northing

lsoas["LONG"], lsoas["LAT"] = transformer.transform(
    lsoas["LONG"].to_numpy(),
    lsoas["LAT"].to_numpy()
)

print(lsoas[["LONG", "LAT"]].head())

# Ensure coordinates are numeric
for df in [stations, lsoas]:
    df["LAT"] = pd.to_numeric(df["LAT"], errors="coerce")
    df["LONG"] = pd.to_numeric(df["LONG"], errors="coerce")

    if df[["LAT", "LONG"]].isna().any().any():
        raise ValueError("Missing or invalid coordinates found.")

    if (
        (~df["LAT"].between(-90, 90)).any()
        or (~df["LONG"].between(-180, 180)).any()
    ):
        raise ValueError("Coordinates outside valid lat/long ranges.")


# =====================================
# BUILD SPATIAL INDEX
# =====================================

station_xyz = to_xyz(
    stations["LAT"],
    stations["LONG"]
)

lsoa_xyz = to_xyz(
    lsoas["LAT"],
    lsoas["LONG"]
)

tree = cKDTree(station_xyz)

# Convert geographical radius to 3D chord distance
radius = 2 * np.sin(
    MAX_DISTANCE_KM / (2 * EARTH_RADIUS_KM)
)

# Find every nearby station for each LSOA
nearby_stations = tree.query_ball_point(
    lsoa_xyz,
    r=radius + 1e-12
)


# =====================================
# MATCH LSOAs TO STATIONS
# =====================================

matched_rows = []
unmatched_rows = []

for i, station_indices in enumerate(nearby_stations):

    lsoa = lsoas.iloc[i]
    matched = False

    for j in station_indices:

        station = stations.iloc[j]

        distance = haversine(
            lsoa["LAT"],
            lsoa["LONG"],
            station["LAT"],
            station["LONG"]
        )

        if distance <= MAX_DISTANCE_KM:
            matched_rows.append({
                "LSOA": lsoa["LSOA"],
                "STATION": station["STATION"],
                "DISTANCE": round(float(distance), 4),
                "LSOA_LAT": lsoa["LAT"],
                "LSOA_LONG": lsoa["LONG"],
                "STATION_LAT": station["LAT"],
                "STATION_LONG": station["LONG"]
            })

            matched = True

    if not matched:
        unmatched_rows.append({
            "LSOA": lsoa["LSOA"],
            "LONG": lsoa["LONG"],
            "LAT": lsoa["LAT"]
        })


# =====================================
# EXPORT RESULTS
# =====================================

matched_columns = [
    "LSOA",
    "STATION",
    "DISTANCE",
    "LSOA_LAT",
    "LSOA_LONG",
    "STATION_LAT",
    "STATION_LONG"
]

unmatched_columns = [
    "LSOA",
    "LONG",
    "LAT"
]

matched_df = pd.DataFrame(
    matched_rows,
    columns=matched_columns
)

unmatched_df = pd.DataFrame(
    unmatched_rows,
    columns=unmatched_columns
)

matched_df = matched_df.sort_values(
    ["STATION", "DISTANCE", "LSOA"]
).reset_index(drop=True)

matched_df.to_csv(OUTPUT_MATCHED, index=False)
unmatched_df.to_csv(OUTPUT_UNMATCHED, index=False)


# =====================================
# SUMMARY
# =====================================

print("Processing complete!")
print(f"Total stations: {len(stations):,}")
print(f"Total LSOAs: {len(lsoas):,}")
print(f"Total LSOA-station matches: {len(matched_df):,}")
print(f"Unique matched LSOAs: {matched_df['LSOA'].nunique():,}")
print(f"Unmatched LSOAs: {len(unmatched_df):,}")
print()
print(f"Saved: {OUTPUT_MATCHED}")
print(f"Saved: {OUTPUT_UNMATCHED}")