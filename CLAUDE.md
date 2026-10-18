# CLAUDE.md: Transport Inequality Auditor

> Context for Claude Code and teammates. Read all of it before writing code or giving advice.
> Detailed method documents live in `docs/`; this file is the summary and the rules.
> Last updated: 18 October 2026, after the datathon. **The project is complete and deployed on IBM LinuxONE.** Sections 3–9 describe what was built; the planning documents in `docs/notes/` record the original design.

---

## 1. Event and goal

- **Event:** IBM Z Datathon 2026 (Shooting Stars Foundation with IBM), University of Manchester hub. **Team:** The SLahkers. **Theme:** Wildcard (Beyond the Obvious), under "AI for Good".
- **Hacking window:** 09:00 17 October to about 10:00 18 October (UK). **Finished:** the pipeline was built, deployed and run on LinuxONE, and the video and submission were completed during the event.
- **Submission (Hack2Skill, done):** video of 3 minutes or less (problem → supporting data → solution and demo → **how it uses IBM Z**: tech stack, LinuxONE instance, model running → impact), GitHub repo link, title `[The SLahkers_IBM Z Datathon 2026 Project]`, links to every open-source library and dataset. Optional: architecture flowchart, prototype.
- **Judging (10 points each):** community value and theme fit · novelty · **technical design and use of LinuxONE and AI/ML** (screenshots as evidence) · execution and pitch.
- **Rules:** all code written during the event; don't copy IBM lab code or other projects; link every library and dataset; check with the organisers (Discord `#ibm-z-sme-helpdesk`) what's allowed for AI-assisted code.

---

## 2. The project

**Question:** *"Who does the railway let down?"* Does rail serve deprived communities in Greater Manchester worse than wealthy ones?

**Users:** **councillors, campaigners and journalists.**

**Product: a station evidence pack**
- ranked table of stations: failure rate, cancellation rate, deprivation, position-adjusted residual, all with intervals
- a **Granite-written plain-English brief** per station, citing only computed numbers, with caveats
- the main chart: deprivation (x) vs morning failure rate (y)
- *(optional)* Granite answering questions about the results

**Video "product moment":** a councillor looks up their local station and gets a fair, evidence-based summary.

**Messaging is fixed for each possible outcome. Don't tune the method to get a particular result.**
- **Gap found:** "Trains serving more deprived areas fail more often, and X% of the gap remains after accounting for network position."
- **No clear gap:** "No clear evidence of unequal service in this sample; here's a tool to keep checking, station by station."
- **The reverse:** reported honestly.
- Language is always correlational ("associated with", "suggests"), never causal.

**Scope:** Greater Manchester, National Rail only, as a proof of concept. The pipeline is config-driven so it works for any English city. **Metrolink trams and buses are excluded**; say so explicitly.

---

## 3. Methods (summary; details in `docs/`)

### 3.1 Deprivation per station → `docs/notes/deprivation_method.md`
- **As built (`src/geo.py`):** 2011 LSOA nearest-station matches within 1.5 km (`data/processed/lsoa_station_matches.csv`), joined to IMD 2025 (96% of LSOAs join). Every Greater Manchester station is scored (83 in the analysis; SRN has no score). The planned design follows.
- Unit: **LSOA (2021)**. No postcodes and no postcodes.io.
- Each LSOA's **population-weighted centroid** is assigned to its **nearest station among ALL Greater Manchester stations** (plus a ~3 km buffer and the hubs). Kept if that station is sampled and within **1.5 km**. No double counting.
- `deprivation_pct = 1 − imd_rank / max_rank` (**higher = more deprived**; read `max_rank` from the file).
- Per station: **`dep_score`** = mean `deprivation_pct` (**main**); `dep_share_top20` = share of LSOAs in deciles 1–2 (headline); `dep_median`; `n_lsoa` (flag < 3); optional `dep_score_popw` (Census TS001); `dep_score_own_lsoa`.
- Avoid the "Barriers to Housing and Services" domain (circular with transport access).
- Sensitivity checks: radius 1 km and 2 km, population weighting, own LSOA, overlapping circles.

### 3.2 Service quality per station: hub method → `docs/notes/service_quality_method.md`
- **Hubs:** MAN (Piccadilly), MCO (Oxford Road), MCV (Victoria), DGT (Deansgate). Never sampled as origins.
- Hub decided **per train**: morning = **first** hub after the origin; evening = **last** hub before the origin. Trains reaching no hub are excluded.
- **Morning:** scheduled **arrival at the first hub 07:00–09:59**; lateness measured **at the hub**.
- **Evening:** scheduled **departure from the last hub 16:00–18:59**; lateness measured **at arrival at the origin**.
- Weekdays only, no bank holidays; public timetable times; planned cancellations excluded but counted.
- **Outcome per train:** `cancelled` (cancelled, **skipped the origin**, or terminated before the measuring stop) · `late` (≥ **5 min**) · `on_time` (< 5) · `missing` (excluded, counted). Early arrivals count as 0 minutes.
- **Per station, per peak:** **`failure_rate`** = (late + cancelled) / scheduled (**main measure, morning**); `cancel_rate`; `late_rate`; `mean_delay_min`; `p90_delay_min`; `delay_per_30` (both peaks); `n_scheduled` (flag < 30); `n_missing`.
- Uncertainty: per-station 95% bootstrap **resampling service dates**.
- 10 minutes is a sensitivity check only.
- **HSP data (agreed 17 Oct, `src/journeys.py`):** an HSP-recorded train with no actual time at the measuring stop is `cancelled` if it has a `late_canc_reason` or no actuals anywhere; otherwise `missing`. Trains **absent** from HSP are not counted: HSP omits many runs that did run (RTT check, 5 Oct: 37 of 38 absent trains ran; HSP holds ~72% of timetabled trains). All rates are "among trains HSP recorded". Coverage is not clearly related to deprivation (`hsp_coverage.parquet`).

### 3.3 Model: risk adjustment → `docs/notes/model_method.md`, code in `src/models.py`
- **Purpose:** expected failure rate given **position only** (like expected goals in football). Residual = observed − expected.
- **Rows:** one per train (station × day × peak). **Target:** `failed` = late ≥ 5 or cancelled.
- **Features:** `minutes_to_hub` (scheduled), `stops_between`, `stops_before`, `sched_hour`, `rain_mm`, `wind_kmh`.
- **Never features:** station ID, deprivation, operator, upstream delay, actual times or outcomes (blocked in code via `FORBIDDEN_FEATURES`).
- **Cross-validation grouped by STATION** (5 folds; out-of-fold predictions). Grouping by date let models learn each station's own reliability.
- **Models:** **logistic regression = main** (best recovery of a planted effect in synthetic tests, with only ~25 stations); XGBoost = robustness check (falls back to sklearn `HistGradientBoostingClassifier`); **Keras 32→16→1 = IBM Z accelerator demonstration**.
- No class rebalancing; fixed seeds; small models; check **calibration** (Brier score and curve) and ROC AUC, never plain accuracy.
- One model per peak; morning first.

### 3.4 Main tests (fixed before seeing results)
1. **Raw:** Spearman(`dep_score`, morning `failure_rate`) across stations.
2. **Position-adjusted:** Spearman(`dep_score`, morning residual from the main model).
- Both with a **95% bootstrap interval resampling stations** (2,000 resamples). Positive ρ = more deprived areas get worse service.
- Communication only: compare the most and least deprived thirds of stations (not an extra test).
- Everything else is supporting or sensitivity.

### 3.5 Stations analysed
- **As built:** all Greater Manchester stations with HSP-recorded peak trains to or from a hub (83 in the morning analysis, 5,040 morning trains, 5–16 October 2026). This replaces the planned sample of 20–30.
- `possible_hubs` and stopping patterns come from the real Network Rail timetable (`src/timetable.py`).

---

## 4. Data sources (as used)

| Source | Used for | Code |
|---|---|---|
| **National Rail HSP** via https://raildata.org.uk/ (docs https://wiki.openraildata.com/index.php?title=HSP) | Actual times, lateness and cancellations, 5–16 Oct 2026 (**main source**) | `scripts/hsp_extract*.py`, `scripts/merge_hsp_csv.py`, `src/journeys.py` |
| **Network Rail SCHEDULE feed** (https://wiki.openraildata.com/index.php/SCHEDULE) | Planned timetable, stopping patterns, HSP coverage | `src/timetable.py` |
| **IMD 2025** (https://deprivation.communities.gov.uk/download-all) | Deprivation per LSOA | `src/geo.py`, `src/prep_deprivation.py` |
| **ONS LSOA centroids** (https://geoportal.statistics.gov.uk/) | LSOA → nearest station within 1.5 km | `data/processed/lsoa_station_matches.csv` |
| **Open-Meteo historical** (https://open-meteo.com/en/docs/historical-weather-api, no key) | Hourly rain and wind per station | `src/weather.py` |
| **Realtime Trains API (Next Generation)**, `https://data.rtt.io`, token from https://api-portal.rtt.io/ | **Validation only**: checked HSP for 5 Oct (37 of 38 trains absent from HSP actually ran) | `src/rtt.py` |

**Realtime Trains notes:** the legacy api.rtt.io is shut down. Our account is limited to 10 requests per minute, 100 per hour and 1,000 per day. **Never put the token in a web page or the repo.**

Not used: NaPTAN (the timetable's TIPLOC/CRS data was enough), Census population weighting, ORR station usage, Maven mock data (synthetic tests use `src/synthetic.py`, `source=mock`).

---

## 5. Pipeline and file contracts

Each stage reads the previous stage's file via `src/tableio.py` (`read_table`/`write_table`): every table is written as `.parquet` **and** `.csv`, and read from the CSV when pyarrow is missing (the s390x IBM container has no pyarrow). Never call `pd.read_parquet`/`to_parquet` directly. Everything goes under `data/`, which is committed for now (so the VM gets it via `git pull`). External calls are cached in `data/cache/` and safe to rerun.

| Stage | Module | Output | Key columns |
|---|---|---|---|
| Config | `config.yaml`, `src/config.py` | — | stations, `possible_hubs`, peaks, dates, threshold, `primary_measure`, deprivation settings |
| Timetable | `src/timetable.py` | `data/processed/timetable_trains.parquet` | one row per planned train × day × peak × GM origin, stopping-pattern features |
| Deprivation | `src/geo.py` | `data/processed/station_deprivation.parquet` | `station_crs, dep_score, n_lsoa, …` |
| Weather | `src/weather.py` | `data/processed/weather_hourly.parquet` | `station_crs, ts, rain_mm, wind_kmh` |
| Trains | `src/journeys.py` (from the HSP extract `data/processed/train_data.csv`) | `data/processed/journeys.parquet`, `hsp_coverage.parquet` | `station_crs, hub_crs, peak, service_date, outcome, failed, lateness_min, minutes_to_hub, stops_between, stops_before, rain_mm, wind_kmh, …` |
| Models and tests | `src/models.py` | `models/<version>/<platform>/<peak>/…` | `metadata.json`, `oof_predictions.csv`, `station_residuals_<model>.csv`, model files |
| Evidence pack | `src/evidence.py` | `data/processed/station_quality.{parquet,csv}`, `report_inputs/*.json`, `docs/figures/deprivation_vs_failure_morning.png` | per station × peak measures + date-bootstrap intervals |
| Briefs | `src/brief.py` | `data/processed/briefs/<level>/<name>.md` (area / borough / station) | Granite text + the numbers it was given |
| Orchestration | `run_all.py` | — | runs every stage in order |

---

## 6. Platform: IBM LinuxONE (deployed)

- **VM:** LinuxONE Community Cloud (event code `SSF2026`), **s390x, big-endian**, 3 CPU cores, about 4 GB free RAM. We ran in the IBM-provided Python environment (venv), which already has pandas, scikit-learn, XGBoost, TensorFlow with **`ibm-zdnn-plugin` 2.3.0**, and `llama-cpp-python`. IBM's Jupyter Lab container (`registry.linuxone.cloud.marist.edu/l1cc/jupyterlab-image-s390x`) works too.
- **No pyarrow on s390x:** `src/tableio.py` reads the committed CSV copies.
- **Accelerator:** the NNPA on-chip AI accelerator (`grep -o nnpa /proc/cpuinfo` → `nnpa`). TensorFlow's zDNN plugin routes supported ops to it. We only used it for the Keras inference benchmark.
- **Granite:** the VM ships only a 350M *base* model (`granite-4.0-h-350m-base-be.f16.gguf`), which doesn't follow instructions. We used **Granite 3.3 2B Instruct, big-endian GGUF** (`taronaeo/Granite-3.3-2B-Instruct-BE-GGUF`, Q4_K_M) in `/home/ibm-user/`, set in `config.yaml` on the VM only (never committed).
- **Profiles** (`--profile`): `local` for laptops; `ibmz` adds Keras, the NNPA check, the zDNN version and a benchmark. Outputs are versioned per platform: `models/<version>/<platform>/<peak>/`.
- **Retrain on each platform; don't copy binary model files between architectures.**
- **Only one teammate runs the VM.** Everyone else pushes to GitHub and the VM pulls (`git stash && git pull && git stash pop` keeps the local `model_path`).
- Step-by-step instructions: README → "Running on IBM LinuxONE". Run records: `docs/ibmz_evidence/`.

---

## 7. Granite reports

- Runs on the VM via `llama-cpp-python` with our own code (`src/brief.py`); nothing is copied from IBM's labs.
- **Input:** JSON of precomputed numbers from `src/evidence.py` (area, borough or station), including caveats. Granite never calculates.
- **Generation:** chat completion at temperature 0, falling back to plain completion. Reports under 15 words are rejected.
- **Automatic check:** every number in the output must appear in the input; failures are regenerated once, then saved with `needs_review: true`.
- **Speed:** about 1 token per second on 3 cores, so about 12 minutes per report. Generate only what's needed; all ~188 reports would take more than a day.
- **Human review is mandatory.** The area morning report passed the number check but called 30% "higher than" 32%. See `docs/ibmz_evidence/granite_area_morning_review.md`.

---

## 8. Repo layout

```
├── CLAUDE.md  README.md  config.yaml  requirements.txt  run_all.py
├── .env.example            # RTT/HSP placeholders; real .env is gitignored
├── src/                    # pipeline (all used by run_all.py)
│   ├── config.py  tableio.py (parquet/CSV I/O)  timetable.py  geo.py  prep_deprivation.py
│   ├── weather.py  journeys.py  models.py  synthetic.py  evidence.py  brief.py  rtt.py (validation)
├── scripts/                # HSP extraction: hsp_extract.py, hsp_extract_fast.py, export_from_cache.py, merge_hsp_csv.py, cleaner.py
├── tests/                  # test_models_synthetic.py, test_brief.py
├── docs/
│   ├── ibmz_evidence/      # LinuxONE run: metadata, Granite log, human review
│   ├── figures/            # charts
│   └── notes/              # planning documents (may differ from what was built)
├── legacy/                 # superseded code, not imported anywhere
├── notebooks/
├── data/                   # raw/, processed/ (.parquet + .csv), cache/, used_for_processing/ (committed)
└── models/                 # gitignored
```

---

## 9. Final status (18 October 2026)

| Part | Status |
|---|---|
| Timetable, deprivation, weather, HSP journeys (5–16 Oct) | ✅ Done |
| Models + main tests (logreg primary, XGBoost check, Keras demo) | ✅ Done on the laptop **and** on LinuxONE; results identical |
| Result | **No clear gap**: morning raw ρ −0.12 [−0.35, +0.12], adjusted ρ −0.14 [−0.37, +0.08]; 83 stations, 5,040 trains; most vs least deprived third 30% vs 32% failed |
| IBM Z deployment | ✅ s390x, NNPA present, zDNN 2.3.0, Keras inference ~125k rows/s (morning), ~104k (evening) |
| Keras accuracy | ❌ AUC ≈ 0.48: demo only, never used for results |
| Granite 3.3 2B Instruct (big-endian) | ✅ Area morning report generated on LinuxONE (735 s) and human-corrected |
| Video and Hack2Skill submission | ✅ Done |
| ⚠️ Security | HSP credentials were committed in `api_requester_temp` (commits 047dd92, 555354e). The password must be rotated; history was not rewritten |

---

## 10. Decisions taken

1. **IBM Z story:** a continuously updating auditor. The whole pipeline runs on LinuxONE, and Granite writes reports on the same machine, so the data never leaves it. The nightly job is future work.
2. **Ethics:** results describe how *the service* treats areas, not the areas themselves; language is always correlational; Granite text is always human-reviewed.
3. **Weather:** per station, by the hour of the scheduled time at that station.
4. **Not done:** the coverage measure (residents more than 1.5 km from any station), Granite Q&A, and the sensitivity checks (1 km and 2 km radius, 10-minute threshold).

---

## 11. Future work

1. Rotate the leaked HSP password, if not already done.
2. A nightly LinuxONE scheduled job that adds each day's trains and refreshes the results.
3. An automatic direction-word check for Granite reports, and a larger instruct model.
4. Tune or drop the Keras network, and measure a CPU-only baseline before claiming any speed-up.
5. 2021 population-weighted centroids, the sensitivity checks, and other cities.

---

## 12. Instructions for Claude Code

- **Don't invent** API endpoints, parameters, column names or versions. Check real responses, downloaded files or linked docs, and ask if unsure.
- **Don't change fixed decisions** (main measure, main tests, threshold, model choice, feature list, cross-validation grouping) without the team agreeing. Flag concerns instead.
- Code must run on **Python 3.12** on both **x86 and s390x**, using the container's preinstalled libraries where possible. Flag any new dependency and add it to `requirements.txt`.
- Reusable logic goes in `src/` as small, readable functions a teammate can explain. Notebooks only call `src/`.
- **Cache every external call**; respect rate limits; make everything safe to rerun.
- **Never commit** models, `.env`, tokens or credentials. `data/` is committed for now: never cache a response that contains a token. Read secrets from environment variables.
- **Don't copy code from IBM's demo notebooks** or other projects.
- Mock data always carries `source=mock` and a logged warning.
- Statistics: precision, recall, ROC AUC and calibration for classifiers (never plain accuracy); report uncertainty; correlational language only.
- Granite only receives precomputed numbers and never calculates.
- Keep the README's data and library links list up to date.
