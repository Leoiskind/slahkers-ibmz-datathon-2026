"""Run the whole pipeline in order. Every stage is cached / safe to rerun.

  timetable (only if missing) -> geo -> weather -> journeys -> models -> evidence -> brief

Run:  python run_all.py                    laptop (models: local profile; briefs: dry run unless
                                           granite.model_path is set)
      python run_all.py --profile ibmz     LinuxONE VM inside the IBM container (Keras + NNPA evidence)
"""
import argparse
import subprocess
import sys

from src.config import load_config
from src.tableio import table_exists


def stage(*args):
    print(f"\n=== python -m {' '.join(args)}", flush=True)
    subprocess.run([sys.executable, "-m", *args], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="local", choices=["local", "ibmz"])
    args = ap.parse_args()
    cfg = load_config()
    proc = cfg["paths"]["data_dir"] / "processed"

    if not table_exists(proc / "timetable_trains.parquet"):
        stage("src.timetable")
    stage("src.geo")
    stage("src.weather")
    stage("src.journeys")
    stage("src.models", "--peak", "both", "--profile", args.profile)
    stage("src.evidence")
    if (cfg.get("granite") or {}).get("model_path"):
        stage("src.brief", "--level", "area")
        stage("src.brief", "--level", "borough")
        stage("src.brief", "--level", "station")
    else:
        stage("src.brief", "--dry-run", "--peak", "both")
        print("granite.model_path not set: wrote prompts only (set it on the VM to generate reports)")
    print("\nDone. Read every Granite report before using it (human review).")


if __name__ == "__main__":
    main()
