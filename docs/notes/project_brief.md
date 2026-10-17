# Project Brief: Transport Inequality Auditor

**Team:** The SLahkers
**Event:** IBM Z Datathon 2026, University of Manchester hub (17–18 October 2026)
**Theme:** Wildcard (Beyond the Obvious), under the overall "AI for Good" mission
**Repo:** https://github.com/Leoiskind/slahkers-ibmz-datathon-2026

> This file gives an AI coding assistant (or a new teammate) the full context for the project. Read all of it before writing code.

---

## 1. The idea in one paragraph

We are auditing whether the railway serves deprived communities in Greater Manchester worse than wealthy ones. For a set of origin stations, we measure how reliably trains get passengers **into Manchester city centre** (lateness, cancellations, skipped stops). We then join that with official deprivation data for the area around each station. Predictive models estimate reliability and separate the effect of a station's **position on the line** from everything else. IBM's Granite LLM turns the numbers into a plain-English evidence brief for councillors, campaigners and the public. Everything runs on IBM LinuxONE (IBM Z), using its on-chip AI accelerator where possible.

**Headline question:** *"Who does the railway let down?"*

---

## 2. Scope (proof of concept)

- **Region:** Greater Manchester only, as a proof of concept. The pipeline must work for any city by changing a config file.
- **Destination hub:** Manchester city centre stations: Piccadilly (MAN), Oxford Road (MCO), Victoria (MCV), Deansgate (DGT). *(Verify CRS codes against NaPTAN.)*
- **Origin stations:** 10–15 stations spread across the deprivation range and across lines (north, south, east and west). Include matched pairs: stations at similar distances or journey times but with different deprivation levels. Candidates to check: Wilmslow, Hale, Bramhall (affluent); Rochdale, Oldham-area stations, Wigan, Salford-area stations (more deprived).
- **Time window:** the last 3–6 months, weekdays. Focus on morning-peak inbound and evening-peak outbound journeys.
- **Lateness threshold:** 5 minutes (configurable).
- **All of the above lives in one shared config file** (`config.yaml` or `src/config.py`). Never hard-code station lists in notebooks.

---

## 3. Methodology (important)

### The position bias problem
Delays build up along a line. Stations near the end of a route will look worse just because of where they are, and outer stations are often in poorer towns. A naive comparison would blame deprivation for something that is partly geography. We handle this explicitly.

### Two separate questions, both reported
1. **Delay experienced:** what reliability do passengers at this station actually get? Accumulated delay *is* part of their experience, so we don't remove it here.
2. **Delay beyond what network position explains:** after controlling for position, is the station still worse?

### Metrics
| Metric | How | Answers |
|---|---|---|
| Inbound arrival lateness, normalised | Lateness on arrival at the Manchester hub per 30 minutes of scheduled journey time | How reliable is my commute? |
| Delay added per segment | Delay at stop B minus delay at stop A, from HSP per-stop times | Where is delay created? (removes the knock-on effect) |
| Cancellation and skip rate | Share of scheduled calls at the station that were cancelled or skipped (HSP `late_canc_reason` and location-level cancellations) | How often am I stranded? End-of-line stations may be skipped to recover the timetable, which is a key mechanism to test. |
| Position-adjusted residual | Actual minus expected lateness, where expected comes from a model using position features only (distance from origin, stops already made, journey length) | Is this station worse than its position explains? |
| Weighted impact | Above metrics × station usage (ORR) and × % households without a car (Census) | How many people does it hurt, and how dependent are they on rail? |

### Controls
- **Weather** (Open-Meteo): so a stormy week doesn't distort comparisons between lines.
- **Time of day and day of week.**
- **Direction:** inbound and outbound are analysed separately.

### Honesty about uncertainty
With 10–15 stations, correlations are uncertain. Show the spread or bootstrapped confidence intervals, and say "suggests", not "proves". Use precision, recall and ROC AUC for any late/not-late classifier, never plain accuracy, because most trains are on time.

### Data leakage
Only use information available at prediction time. For example, a train's delay at earlier stops may be used to predict later stops, but never its actual final arrival.

---

## 4. Data sources and APIs

Only **HSP requires registration**. Everything else is a free download or a keyless API.

| # | Source | Access | Provides | Used for |
|---|---|---|---|---|
| 1 | **HSP: Historic Service Performance** (National Rail, Darwin-powered) | Register on the **Rail Data Marketplace**: https://raildata.org.uk/ → subscribe to HSP → accept terms. The endpoint and credentials are on the product page. Docs: https://wiki.openraildata.com/index.php?title=HSP | Per train: scheduled vs actual arrival and departure at each station (`gbtt_pta`/`gbtt_ptd` vs `actual_ta`/`actual_td`), and `late_canc_reason`. Two calls: **serviceMetrics** (by origin/destination CRS, time window, date range, day type, tolerance → matching train IDs) and **serviceDetails** (by train ID → per-stop times). Periods of up to 1 year. | Core delay and cancellation data. **Slow and queried route by route**, so cache every response to disk and never re-query the same thing. Attribution to National Rail is required. |
| 2 | **English Indices of Deprivation 2025** | https://deprivation.communities.gov.uk/download-all (CSV) or https://www.gov.uk/government/statistics/english-indices-of-deprivation-2025 | IMD score, rank and decile per LSOA (small area) | The deprivation side of the audit. Use 2025 only; it is not comparable with 2019. |
| 3 | **NaPTAN** (station locations) | https://ckan.publishing.service.gov.uk/dataset/national-public-transport-access-nodes-naptan8 | Station coordinates and CRS codes | Linking stations to areas; CRS codes for HSP queries |
| 4 | **ORR estimates of station usage** | https://dataportal.orr.gov.uk/station-usage (Table 1410, CSV, 2024–25) | Annual entries and exits per station (check columns for CRS and local authority) | Weighting impact by passenger numbers |
| 5 | **postcodes.io** | https://postcodes.io/ (free, no key; reverse geocode by lat/lon) | Nearest postcode → LSOA code | Station → LSOA → IMD join |
| 6 | **Open-Meteo historical weather** | https://open-meteo.com/en/docs/historical-weather-api (free, no key) | Hourly rain, wind, temperature by location and date | Weather control variable |
| 7 *(optional)* | **Census 2021 TS045: Car or van availability** | https://www.ons.gov.uk/census (by LSOA) | % households with no car | Measures dependence on rail |
| 8 *(optional)* | **ORR performance statistics** | https://dataportal.orr.gov.uk/ | Official punctuality by operator | Sanity check and context |
| Fallback | **Maven Analytics "UK Train Rides"** | Maven data playground | **Mock** National Rail–style journey, delay and cancellation data | Building and testing the pipeline only while HSP access is pending. **Never present it as real results.** |

### How the data joins
```
HSP (train × station: scheduled vs actual)
   │  CRS code
   ▼
NaPTAN (station → lat/lon) ──postcodes.io──► LSOA code
   │                                            │
ORR usage (station → passengers)     IMD 2025 (LSOA → deprivation)
   │                                 Census TS045 (LSOA → % no car)
   ▼                                            │
station-level metrics × passengers × deprivation ◄┘
   + Open-Meteo weather (by location and hour) as a control
```

---

## 5. Platform and environment

- **Runs on:** IBM LinuxONE Community Cloud VM (Ubuntu, **s390x** architecture, **big-endian**).
- **Main environment:** IBM's Jupyter Lab container `registry.linuxone.cloud.marist.edu/l1cc/jupyterlab-image-s390x:latest`, run with Docker on port 38888. The repo is cloned into `~/shared`, which the container mounts at `/home/jovyan/shared`.
- **Python:** 3.12 (matching the container). Local laptops use a Python 3.12 venv with `requirements.txt`.
- **Container already includes:** pandas 3.x, numpy, scipy, scikit-learn, XGBoost, TensorFlow 2.18.1 / Keras, `ibm-zdnn-plugin` (routes TensorFlow ops to the on-chip accelerator), `llama-cpp-python` built with zDNN, and the **IBM Granite 3.3 2B Instruct** model in big-endian GGUF form, as used by the "Loan Approval" demo.
- **s390x caveats:** some pip packages lack s390x wheels and may fail to build. Install inside the container with `!{sys.executable} -m pip install <pkg>` and test early. Fallbacks: Flask instead of FastAPI, CSV instead of Parquet, `xml.etree` instead of `lxml`. Never load model files or binary data saved on a little-endian machine without checking them.
- **Accelerator check:** `grep -o nnpa /proc/cpuinfo`. Only claim acceleration in the video if this shows `nnpa`.

---

## 6. Tech stack and responsibilities

| Layer | Technology | Responsible for |
|---|---|---|
| Ingestion | Python `requests` | Calling HSP, postcodes.io and Open-Meteo, with on-disk caching of every response |
| Storage | Parquet (`pyarrow`), or CSV as fallback, in `~/shared/data` (gitignored) | Raw and processed datasets passed between pipeline stages |
| Processing | pandas, NumPy | Joins, delay calculations, per-segment delay, feature building |
| Baseline model | XGBoost | Fast, explainable reliability and lateness model |
| Main model | TensorFlow/Keras with `ibm-zdnn-plugin` | Neural model whose inference runs on the IBM Z AI accelerator (NNPA); benchmark it for the "why IBM Z" evidence |
| Evaluation | scikit-learn metrics | Precision, recall, ROC AUC, MAE, bootstrap confidence intervals |
| Charts | matplotlib / seaborn | Analysis charts and video visuals |
| Generative AI | IBM Granite 3.3 2B via `llama-cpp-python` | Plain-English evidence briefs **from precomputed numbers only** |
| Serving | FastAPI + Uvicorn (Flask fallback) | API: station → metrics + brief |
| Front end | Single HTML page, optionally with Leaflet | Map of stations coloured by reliability and deprivation; click a station to see its brief |
| Packaging | Docker (s390x image built on the VM) | Containerised API for the deployment and security evidence |
| Collaboration | GitHub, `nbstripout`, one notebook per person, shared code in `src/` | Teamwork without notebook merge conflicts |

### Granite usage rules
- Compute every statistic in pandas first and pass exact values in the prompt. The model must not do arithmetic or introduce numbers that aren't in the prompt.
- Keep prompts short and structured, e.g. "Here are the figures: [table]. Write 3 sentences for a local councillor. Include the caveat about line position. Do not add any numbers not in the table."
- Spot-check outputs for invented facts. The demo brief must be one we've verified.
- Write our own prompting and loading code. **Do not copy code from IBM's Loan Approval demo.**

---

## 7. Repo structure

```
├── config.yaml            # station list, hub, date range, thresholds (single source of truth)
├── data/                  # gitignored (raw/, processed/, cache/)
├── notebooks/             # one notebook per person or task, e.g. 01_hsp_ingest_<name>.ipynb
├── src/
│   ├── __init__.py
│   ├── config.py          # loads config.yaml
│   ├── hsp.py             # HSP client with caching
│   ├── geo.py             # NaPTAN + postcodes.io → LSOA → IMD/Census join
│   ├── weather.py         # Open-Meteo client
│   ├── metrics.py         # lateness, segment delay, skip rate, residuals
│   ├── models.py          # XGBoost baseline, Keras model, NNPA benchmark
│   ├── brief.py           # Granite prompt building and generation
│   └── api.py             # FastAPI app
├── web/index.html         # demo front end
├── Dockerfile
├── requirements.txt
├── .env.example           # HSP credentials placeholders (real .env is gitignored)
└── README.md
```

Notebooks import from `src/` using `%load_ext autoreload` and `%autoreload 2`.

---

## 8. Rules and constraints

- **Originality:** all project code must be written during the event (from 09:00 on 17 October 2026). No reused projects, and no copying IBM lab code. Open-source libraries are allowed **but every one used must be linked in the README and the submission.** Check with the organisers (Discord) what is allowed for AI-assisted code.
- **Secrets:** API credentials and the Jupyter token go in `.env`, never in git.
- **Data:** never commit datasets, caches or model files.
- **Notebooks:** strip outputs before committing (`nbstripout --install`).
- **Attribution:** credit National Rail as the HSP data source. Cite IMD, ONS, ORR and Open-Meteo.

---

## 9. Deliverables (Hack2Skill submission)

1. **Video, maximum 3 minutes (required):** problem → supporting data → solution and demo → how it uses IBM Z (show the LinuxONE VM, `uname -m` = s390x, Docker, Jupyter, the NNPA plugin check and benchmark, Granite running on the box) → impact and scalability.
2. **GitHub repo link (required).**
3. **Project title:** `[The SLahkers_IBM Z Datathon 2026 Project]` (team name must match registration exactly).
4. **Links to all open-source libraries and datasets used.**
5. **Optional:** architecture flowchart and prototype link.

**Judging (10 points each):** community value and theme fit · novelty · technical design and use of LinuxONE and AI/ML · execution and pitch.

**Target:** everything submittable, including the video, by **tonight, 17 October**. The hard close is the morning of 18 October; treat tonight as the real deadline.

---

## 10. Plan and roles

| Role | Owns |
|---|---|
| 1 | Rail Data Marketplace and HSP access; `src/hsp.py` with caching; bulk pulls for the chosen stations |
| 2 | `src/geo.py`: NaPTAN, postcodes.io, IMD 2025, ORR usage, Census join; final station list (with role 3) |
| 3 | `src/weather.py`, `src/metrics.py`: delay metrics, position bias controls, statistics |
| 4 | `src/models.py`: XGBoost baseline, Keras model, NNPA benchmark |
| 5 | `src/brief.py`, `src/api.py`, `web/index.html`, Dockerfile |
| 6 | Research and story: video script, charts, flowchart, screenshots, editing |

**Milestones**
- **12:30:** HSP access confirmed (or fallback chosen); config with station list committed.
- **15:00:** HSP data for all stations cached; station → LSOA → IMD table done.
- **18:00:** metrics and baseline model working end to end; first Granite brief generated.
- **21:00:** position-adjusted analysis, Keras/NNPA benchmark, API and map, Docker.
- **Late tonight:** video recorded and edited; submission complete.

---

## 11. Open questions and risks

- **HSP approval time is unknown.** If it isn't approved quickly, build on the Maven mock data and ask the organisers on Discord for alternatives.
- **HSP is slow and route by route.** Keep the station list small and cache everything.
- **Check the VM can reach external APIs** (`curl` from the VM to raildata, postcodes.io, open-meteo).
- **Package installs on s390x:** test FastAPI, Uvicorn, pyarrow and requests in the container in the first hour.
- **Small sample of stations:** present findings as exploratory, with uncertainty shown.

## 12. Notes for an AI coding assistant

- Don't invent API endpoints, parameters or column names. Check the linked docs, the actual API responses, or the downloaded files, and ask if unsure.
- Prefer simple, readable code that a teammate can explain in the video.
- Put reusable logic in `src/` with small functions; keep notebooks for exploration.
- Every external call must be cached to `data/cache/` and be safe to re-run.
- Keep everything compatible with Python 3.12 on s390x and the preinstalled container libraries.
