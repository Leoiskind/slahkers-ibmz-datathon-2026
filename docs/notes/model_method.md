# Position-Adjustment Model: Decisions, Justification and Deployment

**Project:** Transport Inequality Auditor (The SLahkers, IBM Z Datathon 2026)
**Status:** agreed design (17 October 2026). Companion to `deprivation_method.md` and `service_quality_method.md`.
**Code:** `src/models.py` · smoke test: `tests/test_models_synthetic.py`

---

## 1. What the model is for

The model answers one question: **"Given where this station sits on the network, how often would we expect its trains to fail?"**

It is a **risk-adjustment** model, not a forecasting tool. It works like **expected goals (xG)** in football, or hospitals comparing actual deaths with deaths expected given how ill their patients were:

```
expected failure rate = what the station's position predicts
residual              = observed failure rate − expected failure rate
```

A positive residual means a station's trains fail **more than its position on the network explains**. We then test whether residuals are higher at more deprived stations.

---

## 2. Decisions and why

### 2.1 Task: supervised binary classification of individual trains
| Decision | Why |
|---|---|
| **One row per train** (per station, per day, per peak), not per station | Thousands of rows instead of ~25; lets the model learn how position affects individual journeys. |
| **Target `failed`** = late ≥ 5 min **or** cancelled | Matches the main measure (failure rate) exactly, so expected and observed rates are directly comparable. Cancellations are included because a cancelled train is a failure for passengers. |
| **Output = probability** | Averaging probabilities per station gives an expected failure *rate*. |
| **One model per peak** | Position features mean different things in each direction (morning: delay builds *after* the passenger boards; evening: delay builds *before* they arrive home). Separate models are cleaner to explain. Morning is the main test. |

### 2.2 Features: position only
| Used | Meaning |
|---|---|
| `minutes_to_hub` | Scheduled minutes between origin and hub (direction depends on peak) |
| `stops_between` | Scheduled stops between origin and hub |
| `stops_before` | Stops the train made before the origin |
| `sched_hour` | Scheduled time of day |
| `rain_mm`, `wind_kmh` | Day-level weather |

All are **scheduled** values or conditions outside the station's control. Actual times are never used, because they contain the delay we're predicting (data leakage).

| Never used | Why |
|---|---|
| **Deprivation** | It's what we're testing. Including it would remove the effect we're looking for. |
| **Station name or ID** | The model would learn each station's own reliability and absorb the differences we want to measure. |
| **Operator** | How the line is run, not geography. Tested separately as a sensitivity check. |
| **Upstream delay** | An outcome of how the line is run, not a fixed fact; controlling for it could hide unequal treatment. |
| **Actual times, lateness, outcome** | Leakage. |

`src/models.py` blocks these at runtime (`FORBIDDEN_FEATURES`).

### 2.3 Validation: cross-validation grouped by station
| Decision | Why |
|---|---|
| **Out-of-fold predictions for every train** | Every train's expected probability comes from a model that never saw it. In-sample predictions would make residuals artificially small. |
| **Folds grouped by station** (5 folds) | **Discovered during testing.** Most position features are nearly constant per station, so a model can effectively *recognise* individual stations and learn their own failure rate, absorbing the inequality we're testing for. Grouping by station means each station's expected rate is learned **only from other stations**. |
| Date is kept for Keras early stopping only | The neural network holds out its latest training date as validation, so validation never shares a day with training. |

### 2.4 Model choice: logistic regression is the main model
| Model | Role | Why |
|---|---|---|
| **Logistic regression** (standardised features) | **Main model** | With ~25 stations, most position information is station-level, so there are effectively only ~25 distinct "positions" to learn from. A simple, low-variance model generalises to unseen stations better than a flexible one. It's also fully transparent: each feature has a coefficient we can explain. |
| **XGBoost** (shallow gradient-boosted trees) | Robustness check | Captures non-linear effects. If it gives similar residuals, the result doesn't depend on the model choice. Falls back to scikit-learn's `HistGradientBoostingClassifier` if XGBoost isn't installed. |
| **Keras neural network** (32 → 16 → 1) | **IBM Z accelerator demonstration** | Same task and data. Runs inference through the `ibm-zdnn-plugin` on LinuxONE's on-chip AI accelerator. It doesn't need to be the best model. |

**Evidence (synthetic data, `tests/test_models_synthetic.py`):** we planted a known deprivation effect in mock data (25 stations, 10 days) and checked whether each model's residuals recovered it, across 8 random datasets:

| | Logistic regression | Gradient-boosted trees |
|---|---|---|
| Planted effect: mean adjusted ρ (higher = better recovery) | **0.61** (sd 0.14) | 0.48 (sd 0.10) |
| No effect: mean adjusted ρ (should be ≈ 0) | **0.02** (sd 0.17) | 0.02 (sd 0.24) |

Both are unbiased when there's no effect, but logistic regression recovers a real effect better and is less noisy. **This is synthetic data used to choose a method; it is not a result.** We'll report both models on the real data, with logistic regression fixed as primary **before** seeing results.

### 2.5 Training settings
| Setting | Choice | Why |
|---|---|---|
| Model size | Trees: 200 trees, depth 3, learning rate 0.05, min leaf weight 10. Network: 2 small hidden layers with dropout and early stopping | Small data; we want an honest baseline, not a memorising model. |
| Class balance | **No rebalancing or class weights** | We need realistic probabilities to average. Rebalancing distorts them. |
| Tuning | Minimal; fixed defaults | Avoids overfitting the choice of settings to a small dataset. |
| Seeds | Fixed (`seed=0`) | Reproducible results. |

### 2.6 Evaluation
| Check | Why | What "good" looks like |
|---|---|---|
| **Calibration** (curve and Brier score) | **Most important.** We average probabilities, so "20% predicted" must mean ~20% fail. | Curve near the diagonal; Brier score below the constant-rate baseline. |
| **ROC AUC** | How much position explains | Above 0.5. A value near 0.5 is itself a finding: position explains little, so residuals ≈ raw failure rates. |
| Never plain accuracy | Most trains don't fail, so accuracy is misleading | |

If calibration is poor, recalibrate inside each fold (`CalibratedClassifierCV`, sigmoid method).

### 2.7 From predictions to the result
1. Per station: `observed` = mean `failed`; `expected` = mean out-of-fold probability; `residual` = observed − expected.
2. **Raw test:** Spearman(`dep_score`, observed failure rate).
3. **Position-adjusted test:** Spearman(`dep_score`, residual).
4. Both with 95% **bootstrap intervals that resample stations** (2,000 resamples).

Reading: positive ρ means more deprived areas get worse service. With ~25 stations, intervals will be wide; results are exploratory ("suggests", not "proves").

---

## 3. Deployment versions

The same code (`src/models.py`) runs everywhere. What differs is the environment, how models are saved, and the extra IBM Z evidence. We keep **separate, versioned outputs per platform** rather than copying model files between architectures.

### 3.1 Profiles
| | `local` (normal deployment) | `ibmz` (LinuxONE deployment) |
|---|---|---|
| **Hardware** | x86_64 or ARM laptops, ordinary Linux servers | IBM LinuxONE VM, **s390x, big-endian** |
| **Environment** | Python 3.12 venv with `requirements.txt` | IBM Jupyter Lab ML container (`registry.linuxone.cloud.marist.edu/l1cc/jupyterlab-image-s390x`) |
| **Libraries** | scikit-learn, pandas, numpy, scipy; XGBoost and TensorFlow optional (code falls back or skips) | All preinstalled in the container, including TensorFlow with `ibm-zdnn-plugin` |
| **Models trained** | logreg, xgb (+ keras if TensorFlow installed) | logreg, xgb, keras |
| **Extra outputs** | none | NNPA check (`/proc/cpuinfo`), zDNN plugin version, inference benchmark used as judging evidence |
| **Purpose** | Development, testing, reproducing results anywhere | The official run shown in the submission |

Set with `ModelConfig(profile="local")` or `ModelConfig(profile="ibmz")`. The platform is also detected automatically (`platform.machine()`), so outputs from each machine never overwrite each other.

### 3.2 Versioned output layout
```
models/                                   (gitignored)
└── <version>/                            e.g. v1-2026-10-17
    ├── x86_64/                           local runs (or arm64)
    │   ├── am/
    │   │   ├── metadata.json             platform, library versions, features, metrics, tests, benchmark
    │   │   ├── oof_predictions.csv       out-of-fold probability per train, per model
    │   │   ├── station_residuals_<model>.csv
    │   │   ├── logreg.joblib
    │   │   ├── xgb.json                  (XGBoost text format) or xgb.joblib (fallback)
    │   │   └── keras.keras               (if TensorFlow available)
    │   └── pm/ …
    └── ibmz/                             LinuxONE runs, same structure
        └── am/ …
```

`metadata.json` records the machine, byte order, Python and library versions, features, primary model, row counts, metrics, both Spearman tests and the benchmark. That makes it possible to compare the `local` and `ibmz` results for the same `version` and confirm they agree.

### 3.3 Rules for moving between platforms
- **Retrain on the target platform; don't copy binary model files.** s390x is big-endian, and pickled or binary model files from a laptop may load incorrectly or not at all. The code and data move; the models are rebuilt.
- **Prefer text formats where they exist** (XGBoost JSON). Treat `.joblib` and `.keras` files as platform-specific.
- **Pin versions to the container.** `requirements.txt` mirrors the container's library versions so local and IBM Z behave the same.
- **Compare versions.** For each release, check that the `local` and `ibmz` station residuals and Spearman results agree closely. Small differences are expected (the accelerator uses a 16-bit internal format); large ones mean something is wrong.
- **Only claim acceleration if `nnpa_in_cpuinfo` is true** in the IBM Z `metadata.json`.

### 3.4 Version naming
`v<major>-<date>`, e.g. `v1-2026-10-17`. Bump the major number when features, target, threshold or model settings change. Record the git commit in the README for each version used in the video.

---

## 4. How to run

```python
from pathlib import Path
import pandas as pd
from src.models import ModelConfig, run_peak

journeys = pd.read_parquet("data/processed/journeys.parquet")
journeys = journeys[journeys.peak == "am"]                     # main test first
dep = pd.read_parquet("data/processed/station_deprivation.parquet")

cfg = ModelConfig(profile="local")                              # "ibmz" on the VM
results = run_peak(journeys, dep, "am", cfg, Path("models"), version="v1-2026-10-17")
```

`journeys` needs: `station_crs`, `service_date`, `failed` and the six feature columns. `dep` needs: `station_crs`, `dep_score`.

Smoke test (synthetic data, no real results): `python tests/test_models_synthetic.py`

---

## 5. Limitations
- **Small number of stations (~25).** Wide intervals; the model can only learn from ~25 distinct positions.
- **Short time window (~10 weekdays).** Recent performance only.
- **Position features are approximations** of network geography.
- **Operator and line effects** are not controlled in the main model (tested separately).
- **Ecological inference.** Results describe stations and areas, not individual passengers.
- **Testing status.** `src/models.py` has been run on synthetic data with scikit-learn's gradient-boosting fallback and logistic regression. **The XGBoost and Keras code paths haven't been executed yet**; run the smoke test in the IBM container (which has both) before using them.
