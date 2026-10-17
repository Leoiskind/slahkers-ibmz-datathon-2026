
import pandas as pd
import numpy as np

# 1. Load the raw data
df = pd.read_csv("train_data.csv")

print("Original shape:", df.shape)

# 2. Remove columns that contain no information
empty_columns = [
    col for col in df.columns
    if df[col].isna().all()
]
df = df.drop(columns=empty_columns)

print("Removed completely empty columns:", empty_columns)

# 3. Remove columns that are not model inputs
# Keep station/date/peak metadata for downstream analysis. - check this
exclude_columns = [
    "source",
    "station_name",
    "operator",
    "train_id",
    "actual_time",
    "lateness_min",
    "outcome",
    "origin_scheduled",
    "origin_actual",
    "hub_scheduled",
    "hub_actual",
    "data_quality_note",
]

df = df.drop(
    columns=exclude_columns,
    errors="ignore",
)

# 4. Standardise text fields
for col in ["station_crs", "hub_crs", "peak"]:
    df[col] = df[col].astype("string").str.strip()

df["peak"] = df["peak"].str.lower()

# 5. Parse dates
df["service_date"] = pd.to_datetime(
    df["service_date"], errors="coerce"
)

# 6. Validate the binary target
# Do not convert unknown outcomes into successful services.
target = pd.to_numeric(df["failed"], errors="coerce")

invalid_target = ~target.isin([0, 1]) & target.notna()

if invalid_target.any():
    raise ValueError("Unexpected values found in failed.")

df["failed"] = target

# 7. Validate scheduled time and derive scheduled hour
time_value = pd.to_numeric(
    df["sched_time"], errors="coerce"
)

hours = np.floor(time_value / 100)
minutes = time_value % 100

valid_time = (
    time_value.notna()
    & time_value.between(0, 2359)
    & (time_value % 1 == 0)
    & (minutes < 60)
)

df["sched_hour"] = np.where(
    valid_time, hours, np.nan
)

# 8. Convert numeric features
numeric_columns = [
    "minutes_to_hub",
    "stops_between",
    "stops_before",
    "sched_hour",
]

for col in numeric_columns:
    df[col] = pd.to_numeric(df[col], errors="coerce")

# 9. Check duplicate records
# Do not remove rows based only on station/date/peak.
print("Exact duplicate rows:", df.duplicated().sum())

# 10. Remove rows with invalid essential metadata
df = df.dropna(
    subset=["station_crs", "service_date", "peak"]
).copy()

# 11. Validate the expected peak labels
unexpected_peaks = set(df["peak"].dropna().unique()) - {"am", "pm"}

if unexpected_peaks:
    raise ValueError(
        f"Unexpected peak labels: {unexpected_peaks}"
    )

# 12. Check feature availability
required_features = [
    "minutes_to_hub",
    "stops_between",
    "stops_before",
    "sched_hour",
    "rain_mm",
    "wind_kmh",
]

missing_features = [
    col for col in required_features
    if col not in df.columns
]

if missing_features:
    print(
        "Features still required from data enrichment:",
        missing_features,
    )

# 13. Save the cleaned data, retaining unknown targets
df.to_csv("cleaned_journeys.csv", index=False)

# 14. Create a separate dataset with known targets
# Suitable for subsequent model preprocessing once
# all required features have been added.
known_target = df.dropna(subset=["failed"]).copy()
known_target["failed"] = known_target["failed"].astype(int)

known_target.to_csv(
    "cleaned_journeys_known_target.csv",
    index=False,
)

print("Cleaned dataset:", df.shape)
print("Known-target dataset:", known_target.shape)
print("Unknown target records:", df["failed"].isna().sum())