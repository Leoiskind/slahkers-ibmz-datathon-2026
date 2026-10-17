# ibmzDatathon
The repo for the IBM Z Datathon 2026.

## Setup

You need **Python 3.12** and Git. Use 3.12 specifically: it matches the LinuxONE VM, and the pinned numpy has no prebuilt Windows package for 3.13 or newer (pip then tries to compile it and fails). Having a newer Python installed as well is fine.

### 1. Install Python 3.12

Skip this step if `py -3.12 --version` (Windows) or `python3.12 --version` (macOS/Linux) already prints a version.

**Windows (PowerShell)**

```powershell
winget install Python.Python.3.12
```

Then close and reopen PowerShell so the new install is picked up. `py -0` should list `-3.12`.

**macOS (Homebrew)**

```bash
brew install python@3.12
```

**Ubuntu / Debian**

```bash
sudo apt update && sudo apt install -y python3.12 python3.12-venv
```

Or download the 3.12.x installer from https://www.python.org/downloads/.

### 2. Clone the repo

```bash
git clone https://github.com/Leoiskind/IBM-Z-Datathon-SLahkers.git
cd IBM-Z-Datathon-SLahkers
```

### 3. Create the virtual environment

Run this once, from the root of the repo.

**Windows (PowerShell)**

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
```

If PowerShell blocks the activate script, run this once and try again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

**Windows (Command Prompt)**

```bat
py -3.12 -m venv .venv
.venv\Scripts\activate.bat
```

**macOS / Linux**

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

Your prompt should now start with `(.venv)`. Check that `python --version` prints 3.12.x.

### 4. Install the dependencies

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 5. Check it works

```bash
python -c "import numpy, pandas, sklearn; print('OK')"
```

### 6. Set up your `.env` (API keys)

API keys and passwords live in a `.env` file in the root of the repo. It is gitignored, so it is never committed and never reaches GitHub. Copy the template:

**Windows (PowerShell)**

```powershell
Copy-Item .env.example .env
```

**macOS / Linux**

```bash
cp .env.example .env
```

Open `.env` and fill in the values you have. Leave the others blank:

| Key | What it is | Where to get it |
|---|---|---|
| `RTT_TOKEN` | Realtime Trains API bearer token | Sign up at https://api-portal.rtt.io/ |
| `HSP_URL`, `HSP_USERNAME`, `HSP_PASSWORD` | National Rail Historical Service Performance | The HSP product page on https://raildata.org.uk/ |

Rules for the file:

- Write one `KEY=value` per line, with no quotes and no spaces around `=`.
- Never paste a token into code, a notebook, a commit or a chat message.
- `src/config.py` reads `.env` automatically, so you don't need to `export` anything.

To check that your HSP settings load (it names any missing keys and never prints the values):

```bash
python -c "from src.config import hsp_credentials; hsp_credentials(); print('HSP settings OK')"
```

**On the LinuxONE VM:** `git pull` does not bring `.env` across, because it is gitignored. The teammate running the VM creates it there once, in `~/shared/slahkers-ibmz-datathon-2026/.env`. That folder is the same as `/home/jovyan/shared/slahkers-ibmz-datathon-2026/.env` inside the container.

### Everyday use

- Activate the environment every time you open a new terminal (the activate line from step 3).
- Leave it with `deactivate`.
- If you add a library, add it to `requirements.txt` with its version and tell the team.
- Never commit `.venv/`, datasets, models or secrets. They are already covered by `.gitignore`.

### Running on LinuxONE

The final solution runs on the IBM LinuxONE Community Cloud, inside IBM's s390x Jupyter Lab container, which already includes these libraries. `requirements.txt` mirrors that container's versions so code behaves the same locally and on the VM. Heavy packages such as TensorFlow and PyTorch are optional locally (see comments in `requirements.txt`).

## Results (5–16 October 2026, weekdays)

**No clear evidence of unequal service in this sample; here's a tool to keep checking, station by station.**

Main tests, fixed before seeing the data (morning peak, logistic regression, 83 Greater Manchester stations, 5,040 trains):

| Test | Spearman ρ | 95% interval (resampling stations) |
|---|---|---|
| Raw: deprivation vs failure rate | −0.12 | −0.35 to +0.12 |
| Position-adjusted: deprivation vs failure rate beyond what network position predicts | −0.14 | −0.37 to +0.08 |

Both intervals include zero. XGBoost (the robustness check) and the evening peak agree. For communication only: in the morning, 30% of trains failed at the most deprived third of stations and 32% at the least deprived third.

![Deprivation vs morning failure rate](docs/figures/deprivation_vs_failure_morning.png)

**Caveats:** results are associations, not causes. A train *fails* if it is 5+ minutes late or cancelled. Rates cover only trains recorded in National Rail's Historical Service Performance (HSP) data, about 72% of timetabled trains. Trains HSP did not record are left out rather than counted as cancelled, because a Realtime Trains check showed that most of them ran. The data covers 10 weekdays, and 9 October looks like a disruption day. Metrolink trams and buses are excluded. One station (SRN) has no deprivation score.

The per-station table is `data/processed/station_quality.csv`. Each station also gets a Granite-written brief (see below).

## How to run

```bash
python run_all.py
```

This runs geo → weather → journeys → models → evidence → briefs, with every stage cached. On the LinuxONE VM, inside the IBM container, set `granite.model_path` in `config.yaml`, then:

```bash
python run_all.py --profile ibmz
```

That adds the Keras model, the NNPA accelerator check and an inference benchmark (in `models/<version>/ibmz/*/metadata.json`), and generates the IBM Granite reports in `data/processed/briefs/` (Greater Manchester, each borough, each station). Granite only rewrites numbers computed in Python; any report containing a number that isn't in its input is flagged `needs_review`. Read every report before using it.

Tests: `python tests/test_models_synthetic.py` (the pipeline recovers a planted effect on synthetic outcomes) and `python tests/test_brief.py` (the number check).

## Data sources

| Source | Used for | Licence / attribution |
|---|---|---|
| [Open-Meteo historical weather API](https://open-meteo.com/en/docs/historical-weather-api) | Hourly rain (mm) and wind (km/h) per GM station (`src/weather.py`) | [CC BY 4.0](https://open-meteo.com/en/license): weather data by Open-Meteo.com |
| [Network Rail SCHEDULE feed](https://wiki.openraildata.com/index.php/SCHEDULE) | Planned GM timetable (`src/timetable.py`) | [Open Government Licence](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/), contains Network Rail data |
| [English Indices of Deprivation 2025](https://deprivation.communities.gov.uk/download-all) | IMD rank per LSOA (`src/geo.py`) | Open Government Licence |
| [National Rail Historical Service Performance (HSP)](https://wiki.openraildata.com/index.php?title=HSP) via the [Rail Data Marketplace](https://raildata.org.uk/) | Actual train times, lateness and cancellations, 5–16 Oct 2026 (`scripts/hsp_extract.py`, `src/journeys.py`) | Rail Data Marketplace licence terms |
| [ONS LSOA centroids](https://geoportal.statistics.gov.uk/) | LSOA → nearest station within 1.5 km (`data/processed/lsoa_station_matches.csv`) | Open Government Licence; contains OS data © Crown copyright |
| [Realtime Trains API](https://api-portal.rtt.io/) | Validation only: checked HSP lateness and cancellations for 5 Oct (`src/rtt.py`) | Realtime Trains API terms |

## Libraries

[pandas](https://pandas.pydata.org/) · [NumPy](https://numpy.org/) · [SciPy](https://scipy.org/) · [scikit-learn](https://scikit-learn.org/) · [XGBoost](https://xgboost.ai/) · [TensorFlow / Keras](https://www.tensorflow.org/) with [IBM zDNN plugin](https://pypi.org/project/ibm-zdnn-plugin/) · [matplotlib](https://matplotlib.org/) · [PyArrow](https://arrow.apache.org/docs/python/) · [PyYAML](https://pyyaml.org/) · [Requests](https://requests.readthedocs.io/) · [llama-cpp-python](https://github.com/abetlen/llama-cpp-python) · [IBM Granite 3.3 2B Instruct](https://huggingface.co/ibm-granite/granite-3.3-2b-instruct)
