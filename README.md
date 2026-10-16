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

### Everyday use

- Activate the environment every time you open a new terminal (the activate line from step 3).
- Leave it with `deactivate`.
- If you add a library, add it to `requirements.txt` with its version and tell the team.
- Never commit `.venv/`, datasets, models or secrets. They are already covered by `.gitignore`.

### Running on LinuxONE

The final solution runs on the IBM LinuxONE Community Cloud, inside IBM's s390x Jupyter Lab container, which already includes these libraries. `requirements.txt` mirrors that container's versions so code behaves the same locally and on the VM. Heavy packages such as TensorFlow and PyTorch are optional locally (see comments in `requirements.txt`).
