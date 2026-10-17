"""Loads config.yaml (scope) and .env (secrets) for every pipeline stage."""
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_config(path=None):
    """Return config.yaml as a dict, with data paths resolved against the repo root."""
    with open(path or ROOT / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["paths"]["data_dir"] = ROOT / cfg["paths"]["data_dir"]
    cfg["paths"]["deprivation_lookup"] = ROOT / cfg["paths"]["deprivation_lookup"]
    return cfg


def hsp_credentials():
    """Read HSP settings from the environment (load .env if python-dotenv is absent by hand)."""
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())
    missing = [k for k in ("HSP_URL", "HSP_USERNAME", "HSP_PASSWORD") if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"Missing HSP settings in .env: {', '.join(missing)}")
    return os.environ["HSP_URL"], os.environ["HSP_USERNAME"], os.environ["HSP_PASSWORD"]
