# Handoff: Transport Inequality Auditor (The SLahkers)

_Written 17 Oct 2026, 23:36. Branch `main` at `ad79a7a`, pushed. Read `CLAUDE.md` for the rules and methods._

## 1. Where we are

The **analysis pipeline is finished and runs end to end on real data** (`python run_all.py`, about 100 seconds, everything cached). **What's left is the IBM Z / Granite run on the VM, then the video and submission.**

| Stage | Module | Output | Status |
|---|---|---|---|
| Timetable | `src/timetable.py` | `data/processed/timetable_trains.parquet` (129k peak journeys, 84 stations, 18 May – 16 Oct) | ✅ (matches RTT on 121/125 trains) |
| Deprivation | `src/geo.py` | `station_deprivation.parquet` (90 stations, IMD 2025) | ✅ |
| Weather | `src/weather.py` | `weather_hourly.parquet` (hourly rain and wind, 91 stations) | ✅ |
| Real trains | `scripts/hsp_extract.py` → `src/journeys.py` | `journeys.parquet` (9,359 trains, 84 stations, 5–16 Oct) | ✅ |
| Models + tests | `src/models.py` | `models/<version>/<platform>/<peak>/` | ✅ logreg + XGBoost locally · ⏳ **Keras never run** (needs the VM) |
| Evidence pack | `src/evidence.py` | `station_quality.csv`, `report_inputs/*.json` (188), `docs/figures/deprivation_vs_failure_morning.png` | ✅ |
| Granite reports | `src/brief.py` | `data/processed/briefs/{area,borough,station}/` | ✅ dry run · ⏳ **Granite never run** (needs the VM) |
| Orchestration | `run_all.py` | — | ✅ |
| Tests | `tests/test_models_synthetic.py`, `tests/test_brief.py` | — | ✅ all pass |
| RTT client | `src/rtt.py` | partial cache | Used for validation only (our quota is 10/min, 100/h, 1000/day) |

## 2. The result (fixed message; don't change it)

> **"No clear evidence of unequal service in this sample; here's a tool to keep checking, station by station."**

Morning peak, logistic regression (the fixed primary model), 83 stations, 5,040 trains:

| Test | ρ | 95% CI |
|---|---|---|
| Raw: deprivation vs failure rate | −0.12 | −0.35 to +0.12 |
| Position-adjusted: deprivation vs residual | −0.14 | −0.37 to +0.08 |

XGBoost and the evening peak agree. Most vs least deprived third (communication only, not a test): 30% vs 32% of morning trains failed. Logreg AUC 0.63 (Brier 0.201 vs baseline 0.210); XGBoost AUC 0.76.

**The model isn't tested on its own training data.** Every reported number uses out-of-fold predictions, with folds grouped by station, so each station is predicted by a model that never saw it. The final models trained on all the data are only saved for reuse and the IBM Z benchmark.

## 3. Decisions made today (and why)

1. **Target = late ≥ 5 min OR cancelled, among trains HSP recorded.**
   - HSP rows with no actual time count as **cancelled** if the train has a `late_canc_reason` or no actual times anywhere. That gave 294 cancellations; RTT confirms the one we could check (GTO 08:25, 5 Oct).
   - Trains **absent** from HSP are **not** counted. HSP holds only about 72% of timetabled trains, and of 38 absent trains RTT shows 37 ran.
   - HSP coverage isn't clearly related to deprivation (morning ρ −0.19, CI −0.39 to +0.02), stored in `hsp_coverage.parquet`.
2. **HSP lateness is trustworthy:** it agrees with RTT on late vs on-time for 43 of 43 trains, never more than 1 minute apart.
3. **Deprivation:** each LSOA is assigned to its *nearest* station within 1.5 km (Cam's `lsoa_station_matches.csv`, which uses 2011 LSOA codes). 96% join to IMD 2025; the rest are LSOA code changes. The fuller CLAUDE.md method (2021 population-weighted centroids, 3 km buffer) is **not** done.
4. **Weather:** hourly, per station, at the train's scheduled time at that station (Open-Meteo, free, cached).
5. **Granite never calculates.** Python computes every number; `check_numbers` flags any report containing a number not in its input (`needs_review: true`).
6. **Data is committed to git** (so the VM gets it via `git pull`), except the 130 MB raw timetable download (`data/raw/timetable/`, gitignored), which `src.timetable` doesn't need thanks to its cache.

## 4. To do next (in order)

1. **VM run** (one person). In the IBM container:
   ```bash
   cd ~/shared/slahkers-ibmz-datathon-2026 && git pull
   ```
   Set `granite.model_path` in `config.yaml` to the Granite 3.3 2B GGUF in the container, then:
   ```bash
   python run_all.py --profile ibmz
   ```
   - This is the **first run of Keras and Granite**. Expect possible errors; the likely spots are `KerasModel` and `load_granite` in `src/brief.py`.
   - Quick Granite demo first: `python -m src.brief --level area`, then `python -m src.brief --only GTO`.
   - Check `metadata.json` → `ibmz.nnpa_in_cpuinfo` is `true` **before claiming acceleration**.
2. **Screenshots:** `uname -m` (s390x), `grep -o nnpa /proc/cpuinfo`, `docker ps`, Jupyter on the VM, the Keras benchmark (`ibmz` block in `metadata.json`), and Granite generating a report.
3. **Human review** of the Granite reports before any appear in the video. Start with `briefs/area/area_morning.md` and the station you'll demo.
4. **Video (≤ 3 min):** problem → data → demo (a councillor looks up their station's brief) → IBM Z (screenshots) → impact (the "keep checking" tool, a nightly job on LinuxONE).
5. **Submit on Hack2Skill:** title `[The SLahkers_IBM Z Datathon 2026 Project]`, repo link, video. The data and library links are in the README.

## 5. Caveats to say out loud (video / README)

- Associations, not causes. 10 weekdays only; 9 Oct looks like a disruption day.
- Rates cover HSP-recorded trains only (about 72% of the timetable).
- Folds split by station, not date, so AUC isn't a forecast of next week.
- SRN has no deprivation score. 2 stations have fewer than 3 LSOAs. Bury has no National Rail station. Metrolink and buses are excluded.

## 6. Known loose ends (not needed for submission)

- `CLAUDE.md` §4 still lists RTT limits as 30/750/9,000; ours are **10/100/1,000**.
- `src/metrics.py`, `src/schema.py`, `src/trains.py` and the old functions in `src/geo.py` belong to the old postcode/HSP design and are unused by `run_all.py`.
- Burnage is `BUA` in `config.yaml` origins; the real code is **`BNA`** (affects only the old RTT origin list).
- Ideas for later: more weekdays via a nightly LinuxONE job; the timetable + HSP approach for longer history; the coverage measure (residents far from any station; CLAUDE.md §10.2).
