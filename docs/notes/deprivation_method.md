# Deprivation per Station: Method

**Project:** Transport Inequality Auditor (The SLahkers, IBM Z Datathon 2026)
**Status:** agreed method. It replaces the earlier grid-sampling / postcodes.io approach.
**Purpose:** give each sampled station one deprivation score that represents the people living around it, to be compared with that station's rail service quality.


> **As built:** `src/geo.py` uses **2011 LSOA nearest-station matches** within 1.5 km (`data/processed/lsoa_station_matches.csv`) joined to IMD 2025 (96% join), not 2021 population-weighted centroids. **All 83 Greater Manchester stations** in the HSP data are scored rather than a sample. The sensitivity checks (1 km and 2 km radius, population weighting) were not run.

---

## 1. Summary

Each station is scored by the deprivation of the **neighbourhoods (LSOAs) whose population centre lies within 1.5 km of it and nearer to it than to any other station**. The main score is the **mean IMD 2025 rank of those LSOAs**, flipped to a 0–1 percentile where **higher means more deprived**. A companion score gives the **share of those LSOAs in England's most deprived 20%**.

Everything is calculated from downloaded files. **No APIs are used** and there are no rate limits.

> **In one sentence for the video or README:** "We included every neighbourhood whose population centre is within 1.5 km of a station and closer to it than to any other station, and averaged their official deprivation ranks."

---

## 2. Key decisions and why

| Decision | Choice | Why |
|---|---|---|
| Unit of geography | **LSOA (2021)**, not postcodes | IMD is calculated per LSOA, so postcodes add no deprivation information. LSOAs are population-controlled (about 1,000–3,000 residents each), so counting LSOAs approximates counting people. It's also standard practice and easy to repeat for any city. |
| Locating an LSOA | **Population-weighted centroid** | Represents where the LSOA's residents actually live, so slivers of a neighbourhood at the catchment edge don't count. |
| Catchment radius | **1.5 km** (straight-line) | A reasonable walking and short-trip distance to a station. Checked at 1 km and 2 km. |
| Overlap between stations | **Nearest-station assignment** against **all** Greater Manchester stations | Each LSOA belongs to exactly one station, so nothing is double counted and catchments don't overlap. Comparing against all stations, not just our sample, stops an LSOA being credited to a sampled station when residents would actually use a closer unsampled one. |
| Weighting | **Each LSOA counts once** (main); Census population-weighted (check) | LSOAs have similar populations, so equal weighting approximates population weighting without needing another dataset. |
| Deprivation index | **Overall IMD 2025** | The standard, best-known measure. 2025 isn't comparable with 2019, so don't mix them. |
| Excluded index | **Barriers to Housing and Services domain** | Partly based on distance to services, which overlaps with transport access, so it would make the analysis partly circular. |

### What we decided against
- **Nearest postcode only:** a single point; stations often sit on main roads or industrial land unlike the surrounding residential area.
- **Fixed overlapping circles:** neighbouring stations share areas, which makes their scores artificially similar and overstates certainty.
- **Grid sampling with postcodes.io (area-weighted):** weights by land rather than people, and needs many API calls.
- **Counting unique postcodes:** postcodes aren't population-controlled (1 to about 100 addresses each), and blocks of flats often share one postcode, which can under-count dense housing.

---

## 3. Data

| Dataset | Source | What we use | Notes |
|---|---|---|---|
| **Station locations** | NaPTAN: https://ckan.publishing.service.gov.uk/dataset/national-public-transport-access-nodes-naptan8 | CRS code, name, latitude and longitude for **every** rail station in and around Greater Manchester | Include the city-centre hub stations (Piccadilly, Oxford Road, Victoria, Deansgate) and stations just outside the county boundary, so edge LSOAs are assigned correctly. |
| **LSOA population-weighted centroids (2021)** | ONS Open Geography Portal: https://geoportal.statistics.gov.uk/ (search "LSOA December 2021 population weighted centroids") | LSOA code, centroid coordinates | Must be **2021** LSOAs to match IMD 2025. Coordinates may be British National Grid (easting and northing) or lat/lon; check and convert consistently. |
| **English Indices of Deprivation 2025** | https://deprivation.communities.gov.uk/download-all · https://www.gov.uk/government/statistics/english-indices-of-deprivation-2025 | IMD rank and decile per LSOA | Our existing postcode-level file also works if deduplicated to one row per LSOA. Rank 1 = most deprived. Take `max_rank` from the file; don't hard-code it. |
| *(Optional)* **Census 2021 population by LSOA (TS001)** | ONS / Nomis: https://www.ons.gov.uk/census | Usual residents per LSOA | Only for the population-weighted sensitivity check. |

All raw files go in `data/raw/` (gitignored). Note the download date and dataset version in the README.

---

## 4. Method, step by step

### Step 1: Load stations
- Load all rail stations in Greater Manchester plus a buffer of about 3 km beyond its boundary (from NaPTAN).
- Add a `sampled` flag (True for our 15–20 study stations, from `config.yaml`).
- Hub stations are included in the full list but are **not** sampled.

### Step 2: Load LSOA centroids
- Load the 2021 population-weighted centroids and filter to Greater Manchester (the 10 boroughs) plus the same buffer.

### Step 3: Assign each LSOA to one station
For each LSOA centroid:
1. Compute the distance to **every** station (haversine, in metres).
2. Find the **nearest** station and that distance `d`.
3. **Keep** the LSOA if the nearest station is `sampled` **and** `d ≤ radius_m` (1,500 m).
4. Otherwise drop it. It belongs to an unsampled station, or to no station within range.

Result: a table `lsoa_code, station_crs, distance_m`, with **each LSOA appearing at most once**.

### Step 4: Join deprivation
- Join each kept LSOA to IMD 2025 (`imd_rank`, `imd_decile`).
- **Check: every kept LSOA must join.** Any non-match usually means mixed 2011 and 2021 codes. Fix it; don't drop the rows silently.

### Step 5: Score each station

Define, per LSOA:
```
deprivation_pct = 1 - (imd_rank / max_rank)      # 0 = least deprived, 1 = most deprived
most_deprived_20 = imd_decile in {1, 2}
```

Per station:
| Output column | Definition | Role |
|---|---|---|
| `dep_score` | Mean of `deprivation_pct` over the station's LSOAs | **Main score** (used in the correlation) |
| `dep_share_top20` | Share of the station's LSOAs with `most_deprived_20` | **Headline companion** for the brief and video ("46% of the neighbourhoods around this station are among England's most deprived fifth") |
| `dep_median` | Median of `deprivation_pct` | Supporting detail |
| `n_lsoa` | Number of LSOAs assigned | Reliability indicator |
| `dep_score_popw` | Population-weighted mean using Census TS001 *(optional)* | Sensitivity check |
| `dep_score_own_lsoa` | `deprivation_pct` of the LSOA the station sits in (or the one with the nearest centroid) | Sensitivity check / comparison |

> **Direction convention:** in all outputs, **higher = more deprived**. A positive correlation with late rate therefore means "more deprived areas get worse service". State this in the README and on every chart.

### Step 6: Save
- `data/processed/station_deprivation.parquet` (or CSV): one row per sampled station with the columns above.
- `data/processed/lsoa_station_assignment.parquet`: the LSOA → station table (useful for maps and checks).

---

## 5. Sensitivity checks

Run these and report whether the conclusions change:

| Check | What changes |
|---|---|
| Radius 1 km and 2 km | Catchment size |
| Population-weighted (`dep_score_popw`) | Weighting |
| Station's own LSOA only (`dep_score_own_lsoa`) | Catchment definition |
| Overlapping circles (no nearest-station assignment) | Overlap handling |

If the station ranking and the correlation with service quality are similar across these, the result is **robust to the method**, which is a strong line for the video. If one check changes things a lot, investigate that station and mention it honestly.

---

## 6. Quality checks before using the scores

- [ ] Every sampled station has at least one LSOA. **Flag stations with `n_lsoa` < 3** as less reliable.
- [ ] Every kept LSOA joined to IMD 2025 (no missing ranks).
- [ ] No LSOA is assigned to more than one station.
- [ ] Hub stations aren't in the sample but are in the full station list.
- [ ] A map of LSOA centroids coloured by assigned station looks sensible (no LSOA assigned across a river or motorway to a far station when a near one exists).
- [ ] Spot-check 2–3 stations against local knowledge (e.g. an affluent suburb should score low).

---

## 7. Caveats (state these in the README and brief)

- **Straight-line distance**, not walking distance. Rivers, motorways and railway lines can make the real walk longer.
- **Nearest-station assumption.** Some people travel further for a faster or direct train.
- **Equal LSOA weighting** assumes similar populations. That's true by design, but not exact.
- **Small catchments** (few LSOAs) give less reliable scores.
- **Ecological measure.** The score describes *areas*, not the individual passengers on a given train.
- **IMD is relative.** It ranks areas against each other; it doesn't measure absolute poverty.

---

## 8. Config (`config.yaml`)

```yaml
deprivation:
  radius_m: 1500
  sensitivity_radii_m: [1000, 2000]
  boundary_buffer_m: 3000
  imd_edition: "2025"
  lsoa_vintage: "2021"
  top_deprived_deciles: [1, 2]
  primary_score: "dep_score"        # fixed before looking at results
  population_weighting: false       # true only for the sensitivity check
```

**The main score (`dep_score`) is fixed here before any correlation is run.** Don't change it after seeing the results.

---

## 9. Attribution

- Contains National Statistics data © Crown copyright and database right (ONS Open Geography Portal; Census 2021).
- English Indices of Deprivation 2025, Ministry of Housing, Communities and Local Government.
- NaPTAN, Department for Transport.
