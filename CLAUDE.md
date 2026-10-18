# CLAUDE.md: Transport Inequality Auditor

> Context for Claude Code and teammates. Read all of it before writing code or giving advice.
> Detailed method documents live in `docs/`; this file is the summary and the rules.
> Last updated: 17 October 2026, 17:15.

---

## 1. Event and goal

- **Event:** IBM Z Datathon 2026 (Shooting Stars Foundation with IBM), University of Manchester hub. **Team:** The SLahkers. **Theme:** Wildcard (Beyond the Obvious), under "AI for Good".
- **Hacking window:** 09:00 17 October to about 10:00 18 October (UK). **Our deadline: everything submitted tonight, 17 October.**
  - **Feature freeze 20:30.** After that: results, README, video and submission only.
  - **Video work starts by 21:00.**
- **Submission (Hack2Skill):** video of 3 minutes or less (problem → supporting data → solution and demo → **how it uses IBM Z**: tech stack, LinuxONE instance, model running → impact), GitHub repo link, title `[The SLahkers_IBM Z Datathon 2026 Project]`, links to every open-source library and dataset. Optional: architecture flowchart, prototype.
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

### 3.1 Deprivation per station → `docs/deprivation_method.md`
- Unit: **LSOA (2021)**. No postcodes and no postcodes.io.
- Each LSOA's **population-weighted centroid** is assigned to its **nearest station among ALL Greater Manchester stations** (plus a ~3 km buffer and the hubs). Kept if that station is sampled and within **1.5 km**. No double counting.
- `deprivation_pct = 1 − imd_rank / max_rank` (**higher = more deprived**; read `max_rank` from the file).
- Per station: **`dep_score`** = mean `deprivation_pct` (**main**); `dep_share_top20` = share of LSOAs in deciles 1–2 (headline); `dep_median`; `n_lsoa` (flag < 3); optional `dep_score_popw` (Census TS001); `dep_score_own_lsoa`.
- Avoid the "Barriers to Housing and Services" domain (circular with transport access).
- Sensitivity checks: radius 1 km and 2 km, population weighting, own LSOA, overlapping circles.

### 3.2 Service quality per station: hub method → `docs/service_quality_method.md`
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

### 3.3 Model: risk adjustment → `docs/model_method.md`, code in `src/models.py`
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

### 3.5 Station sample
- Compute `dep_score` for **every** Greater Manchester station first (no API calls).
- Choose **20–30 origins** across deprivation fifths and lines (north, south, east, west), including matched pairs (similar journey time, different deprivation).
- `possible_hubs` per origin filled from **real timetable data**, not memory.

---

## 4. Data sources

| Source | Access | Used for | Status |
|---|---|---|---|
| **Realtime Trains API (Next Generation)** | Base `https://data.rtt.io`, bearer token from https://api-portal.rtt.io/. Spec: https://realtimetrains.github.io/api-specification/specification/main.yml. **The legacy api.rtt.io is shut down.** | Train times, lateness, cancellations | **Expected main source.** Check access. |
| **HSP** (National Rail, via https://raildata.org.uk/) | Subscription; docs https://wiki.openraildata.com/index.php?title=HSP | Longer history, if credentials arrive | No credentials yet |
| **NaPTAN** | https://ckan.publishing.service.gov.uk/dataset/national-public-transport-access-nodes-naptan8 | Station coordinates and CRS codes | To download |
| **LSOA 2021 population-weighted centroids** | ONS Open Geography Portal (https://geoportal.statistics.gov.uk/) | Catchments | To download |
| **IMD 2025** | https://deprivation.communities.gov.uk/download-all | Deprivation | Postcode-level file in hand → deduplicate to one row per LSOA, or download the LSOA file |
| **Open-Meteo historical** | https://open-meteo.com/en/docs/historical-weather-api (no key) | Weather feature | Tested |
| *(optional)* Census 2021 TS001 / TS045 | ONS | Population weighting / % no car | Not started |
| *(optional)* ORR station usage | https://dataportal.orr.gov.uk/station-usage | Impact weighting | Not started |
| **Maven "UK Train Rides"** | Maven data playground | **MOCK ONLY**, for pipeline testing; `source=mock`; never presented as results | Not on machine |

**Realtime Trains details (verify every field on a real response before parsing):**
- Endpoints: `/api/info` (check `historyRestrictToDays`, default 14), `/gb-nr/location` (`code`, `filterFrom`, `filterTo`, `timeFrom`, `timeTo`, max 23 h 59 min window), `/gb-nr/service` (`identity` + `departureDate`).
- Fields: `scheduleAdvertised`, `realtimeActual`, `realtimeAdvertisedLateness`, `isCancelled`, `cancellationReasonCode`, `reasons`.
- Limits: published defaults are 30/minute, 750/hour, 9,000/day, but **our account is limited to 10/minute, 100/hour, 1,000/day**. **Never put the token in a web page or the repo.**
- **Morning:** query each possible hub with `filterFrom=<origin>`; keep each train's earliest hub arrival. **Evening:** hub with `filterTo=<origin>`, plus origin with `filterFrom=<hub>`, matched on train identity. About 3 requests per origin per day.
- Scheduled times and stopping patterns repeat on weekdays: fetch `/gb-nr/service` for **one day per regular train** and reuse for `minutes_to_hub`, `stops_between` and `stops_before`.

---

## 5. Pipeline and file contracts

Each stage reads the previous stage's file via `src/tableio.py` (`read_table`/`write_table`): every table is written as `.parquet` **and** `.csv`, and read from the CSV when pyarrow is missing (the s390x IBM container has no pyarrow). Never call `pd.read_parquet`/`to_parquet` directly. Everything goes under `data/`, which is committed for now (so the VM gets it via `git pull`). External calls are cached in `data/cache/` and safe to rerun.

| Stage | Module | Output | Key columns |
|---|---|---|---|
| Config | `config.yaml`, `src/config.py` | — | stations, `possible_hubs`, peaks, dates, threshold, `primary_measure`, deprivation settings |
| Stations | `src/geo.py` | `data/processed/stations.parquet` | `station_crs, name, lat, lon, sampled, is_hub` |
| Catchments | `src/geo.py` | `data/processed/lsoa_station_assignment.parquet` | `lsoa_code, station_crs, distance_m` |
| Deprivation | `src/geo.py` | `data/processed/station_deprivation.parquet` | `station_crs, dep_score, dep_share_top20, dep_median, n_lsoa, …` |
| Trains | `src/rtt.py` / `src/hsp.py` / Maven adapter | `data/processed/journeys.parquet` | `source, station_crs, hub_crs, peak, service_date, train_id, operator, sched_time, actual_time, lateness_min, outcome, failed, cancel_reason, minutes_to_hub, stops_between, stops_before, planned_cancel_flag` |
| Weather | `src/weather.py` | joined onto journeys | `rain_mm, wind_kmh` (location and hours to be decided) |
| Quality | `src/metrics.py` | `data/processed/station_quality.parquet` | per station × peak measures + intervals |
| Models and tests | `src/models.py` | `models/<version>/<platform>/<peak>/…` | `metadata.json`, `oof_predictions.csv`, `station_residuals_<model>.csv`, model files |
| Briefs | `src/brief.py` | `data/processed/briefs/<station_crs>.md` | Granite text + the numbers it was given |
| Orchestration | `run_all.py` | — | runs every stage in order |

---

## 6. Platform: IBM LinuxONE

- **VM:** LinuxONE Community Cloud (event code `SSF2026`), **s390x, big-endian**, Ubuntu with Python 3.12 but **no pip on the host**. Use IBM's container.
- **Container:** `registry.linuxone.cloud.marist.edu/l1cc/jupyterlab-image-s390x:latest`, Docker on port 38888, `~/shared` ↔ `/home/jovyan/shared`. Repo cloned at `~/shared/slahkers-ibmz-datathon-2026`. Install extras in a notebook with `!{sys.executable} -m pip install <pkg>`.
- **Preinstalled:** pandas 3.x, numpy, scipy, scikit-learn, XGBoost, TensorFlow 2.18.1 with **`ibm-zdnn-plugin`**, and `llama-cpp-python` (zDNN build) with **Granite 3.3 2B Instruct** (big-endian GGUF, as used by the Loan Approval lab).
- **Accelerator:** the NNPA on-chip AI accelerator; the TensorFlow plugin routes supported ops automatically. **Check `grep -o nnpa /proc/cpuinfo` before claiming acceleration.**
- **Deployment profiles** (`ModelConfig(profile=...)`): `local` for laptops and servers; `ibmz` for the official run (adds the NNPA check, zDNN version and benchmark). Outputs are versioned per platform: `models/<version>/<platform>/<peak>/`.
- **Retrain on each platform; don't copy binary model files between architectures.** Prefer text formats (XGBoost JSON).
- **Only one teammate runs the service on the VM.** Everyone else pushes to GitHub and the VM pulls.
- **Screenshot as you go:** `uname -m` (s390x), the `nnpa` check, `docker ps`, Jupyter on the VM, the Keras benchmark, Granite generating a brief on the VM.

---

## 7. Granite briefs

- Runs on the VM via `llama-cpp-python`. Load the model the way the container's setup does, but **write our own code**.
- **Input:** a small JSON of precomputed numbers per station (dep percentile, `dep_share_top20`, failure, late and cancellation rates with intervals, residual, rank among stations, `n_scheduled`, caveats).
- **Prompt rules:** 3–4 sentences for a councillor; include the line-position caveat; "do not add any numbers not in the input"; temperature 0.
- **Automatic check:** every number in the output must appear in the input; otherwise flag or regenerate.
- **Human review** before any brief appears in the video.

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

## 9. Status (18 October, 01:30)

| Part | Status |
|---|---|
| Timetable, deprivation (2011 LSOA nearest-station × IMD 2025), weather, HSP journeys (5–16 Oct) | ✅ Done |
| Models + main tests (logreg primary, XGBoost check, Keras demo) | ✅ Done on laptop **and** LinuxONE; results identical |
| Result | **No clear gap**: morning raw ρ −0.12 [−0.35, +0.12], adjusted ρ −0.14 [−0.37, +0.08] |
| IBM Z evidence | ✅ s390x, NNPA present, zDNN 2.3.0, Keras inference ~125k rows/s (`docs/ibmz_evidence/`) |
| Keras accuracy | ❌ AUC ≈ 0.48: demo only, never used for results |
| Granite 3.3 2B Instruct (big-endian) | ✅ runs on the VM, ~12 min/report; **drafts need human correction** (it reversed a comparison) |
| CSV fallback for s390x (no pyarrow) | ✅ `src/tableio.py` |
| ⚠️ Security | HSP credentials were committed in `api_requester_temp` (commits 047dd92, 555354e). **Rotate the password**; history not rewritten |

---

## 10. Open decisions (settle quickly)

1. **IBM Z story:** recommended framing is a **continuously updating auditor**: a nightly scheduled job on the VM pulls the previous day's trains and refreshes the results, plus secure public-sector analytics, scale and accelerated AI on the platform.
2. **Coverage measure (recommended, cheap):** the share of residents in the most deprived vs least deprived LSOAs living more than 1.5 km from any station. It addresses the blind spot that areas with no station are invisible to the main analysis.
3. **Ethics:** frame results as how *the service* treats areas, not judgements on the areas; decide whether to name operators; confirm data licences allow public display and publishing derived tables.
4. **Weather:** one location for Greater Manchester or per station; which hours.
5. **Optional Granite Q&A** over the results (good demo moment).
6. **Project name and one-sentence pitch.**

---

## 11. Next steps (priority order)

1. Rotate the leaked HSP password; update `.env` files.
2. Review the GTO Granite report (as for the area report); paste `granite.log` into `docs/ibmz_evidence/`.
3. Screenshots into `docs/ibmz_evidence/`.
4. Video (≤ 3 min) and Hack2Skill submission before the 10:00 close.
5. Later: automatic direction check for Granite text; tune or drop Keras; 2021 population-weighted centroids; more weekdays via a nightly LinuxONE job.

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
