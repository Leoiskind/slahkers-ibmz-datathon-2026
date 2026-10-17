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

## Data sources

| Source | Used for | Licence / attribution |
|---|---|---|
| [Open-Meteo historical weather API](https://open-meteo.com/en/docs/historical-weather-api) | Hourly rain (mm) and wind (km/h) per GM station (`src/weather.py`) | [CC BY 4.0](https://open-meteo.com/en/license): weather data by Open-Meteo.com |
| [Network Rail SCHEDULE feed](https://wiki.openraildata.com/index.php/SCHEDULE) | Planned GM timetable (`src/timetable.py`) | [Open Government Licence](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/), contains Network Rail data |
| [English Indices of Deprivation 2025](https://deprivation.communities.gov.uk/download-all) | IMD rank per LSOA (`src/geo.py`) | Open Government Licence |
