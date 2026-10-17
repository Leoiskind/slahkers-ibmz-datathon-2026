"""Position-adjustment model (spec: docs/notes/model_method.md).

Expected failure rate per station given its position only (like expected goals), then
residual = observed - expected, and two fixed tests across stations:
  raw       Spearman(dep_score, observed failure rate)
  adjusted  Spearman(dep_score, residual of the main model)
each with a 95% bootstrap interval resampling stations. Positive rho = more deprived areas
get worse service. Correlational only.

Models: logreg (MAIN), xgb (robustness; sklearn HistGradientBoosting fallback),
keras 32-16-1 (IBM Z accelerator demonstration; only if TensorFlow is installed).
Cross-validation is grouped by STATION so a station's expected rate is learned only from
other stations. No class rebalancing; fixed seeds; never plain accuracy.

Run:  python -m src.models --synthetic [--effect 0.5] [--peak morning] [--profile ibmz]
      (synthetic = fake outcomes on the real timetable: pipeline demo, never results)
"""
import argparse
import datetime as dt
import importlib.metadata
import importlib.util
import json
import logging
import platform
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from src.tableio import read_table, table_exists, write_table
from scipy import stats
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

log = logging.getLogger(__name__)

FEATURES = ["minutes_to_hub", "stops_between", "stops_before", "sched_hour", "rain_mm", "wind_kmh"]
# Never model inputs: identity (lets the model learn each station's own reliability),
# deprivation (what we test), operator / upstream delay (how the line is run), and anything
# measured after the train ran (leakage).
FORBIDDEN_FEATURES = {
    "station_crs", "station", "hub_crs", "train_uid", "identity", "unique_identity", "headcode",
    "dep_score", "dep_median", "dep_share_top20", "deprivation_pct", "imd_rank", "imd_decile",
    "operator", "operator_code", "toc",
    "upstream_delay", "origin_delay_min", "prev_stop_delay_min",
    "actual_time", "actual_measure_time", "lateness_min", "delay_min",
    "outcome", "failed", "is_cancelled", "cancelled",
}


@dataclass
class ModelConfig:
    profile: str = "local"            # "local" or "ibmz"
    seed: int = 0
    n_folds: int = 5
    n_boot: int = 2000
    features: list = field(default_factory=lambda: list(FEATURES))
    models: tuple = ("logreg", "xgb", "keras")
    primary: str = "logreg"
    target: str = "late_or_cancelled"  # recorded in metadata; see src/journeys.py for the HSP rule


def check_features(features):
    bad = set(features) & FORBIDDEN_FEATURES
    if bad:
        raise ValueError(f"forbidden model features: {sorted(bad)}")


def platform_tag():
    m = platform.machine().lower()
    return "ibmz" if m == "s390x" else {"amd64": "x86_64", "aarch64": "arm64"}.get(m, m)


def has_tensorflow():
    return importlib.util.find_spec("tensorflow") is not None


# ---------------------------------------------------------------- models

def _logreg(cfg):
    return make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000, random_state=cfg.seed))


def _xgb(cfg):
    try:
        from xgboost import XGBClassifier
        return XGBClassifier(n_estimators=200, max_depth=3, learning_rate=0.05, min_child_weight=10,
                             random_state=cfg.seed, n_jobs=2, eval_metric="logloss")
    except ImportError:
        from sklearn.ensemble import HistGradientBoostingClassifier
        log.warning("xgboost not installed: using HistGradientBoostingClassifier")
        return HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=200,
                                              min_samples_leaf=10, random_state=cfg.seed)


class KerasModel:
    """32 -> 16 -> 1 network with dropout and early stopping on the latest training date."""

    def __init__(self, cfg):
        self.cfg = cfg

    def fit(self, X, y, dates):
        import tensorflow as tf
        tf.keras.utils.set_random_seed(self.cfg.seed)
        self.scaler = StandardScaler().fit(X)
        Xs, y = self.scaler.transform(X).astype("float32"), np.asarray(y, dtype="float32")
        last = dates == dates.max()  # validation never shares a day with training
        self.model = tf.keras.Sequential([
            tf.keras.layers.Input(shape=(Xs.shape[1],)),
            tf.keras.layers.Dense(32, activation="relu"), tf.keras.layers.Dropout(0.2),
            tf.keras.layers.Dense(16, activation="relu"), tf.keras.layers.Dropout(0.2),
            tf.keras.layers.Dense(1, activation="sigmoid")])
        self.model.compile(optimizer="adam", loss="binary_crossentropy")
        stop = tf.keras.callbacks.EarlyStopping(patience=3, restore_best_weights=True)
        self.model.fit(Xs[~last], y[~last], validation_data=(Xs[last], y[last]),
                       epochs=50, batch_size=256, callbacks=[stop], verbose=0)
        return self

    def predict_proba(self, X):
        p = self.model.predict(self.scaler.transform(X).astype("float32"), batch_size=4096, verbose=0)
        return np.column_stack([1 - p[:, 0], p[:, 0]])


def _new_model(name, cfg):
    return {"logreg": _logreg, "xgb": _xgb, "keras": KerasModel}[name](cfg)


def _fit(model, X, y, dates):
    return model.fit(X, y, dates) if isinstance(model, KerasModel) else model.fit(X, y)


def oof_predict(name, X, y, groups, dates, cfg):
    """Out-of-fold probability for every train, folds grouped by station."""
    oof = np.full(len(y), np.nan)
    folds = GroupKFold(n_splits=min(cfg.n_folds, len(np.unique(groups))))
    for train, test in folds.split(X, y, groups):
        m = _fit(_new_model(name, cfg), X[train], y[train], dates[train])
        oof[test] = m.predict_proba(X[test])[:, 1]
    return oof


# ---------------------------------------------------------------- evaluation

def calibration_bins(y, p, n_bins=10):
    q = pd.qcut(p, n_bins, duplicates="drop")
    t = pd.DataFrame({"y": y, "p": p, "bin": q}).groupby("bin", observed=True)
    return [{"mean_predicted": round(float(g.p.mean()), 4), "observed": round(float(g.y.mean()), 4),
             "n": int(len(g))} for _, g in t]


def classifier_metrics(y, p):
    base = np.full(len(y), y.mean())
    return {"roc_auc": float(roc_auc_score(y, p)), "brier": float(brier_score_loss(y, p)),
            "brier_baseline": float(brier_score_loss(y, base)), "calibration": calibration_bins(y, p)}


def station_table(df, p):
    t = df.assign(expected=p).groupby("station_crs").agg(
        n=("failed", "size"), observed=("failed", "mean"), expected=("expected", "mean"))
    t["residual"] = t["observed"] - t["expected"]
    return t.reset_index()


def spearman_boot(x, y, n_boot, seed):
    """Spearman rho with a 95% bootstrap interval resampling stations."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    rho = stats.spearmanr(x, y).statistic
    rng = np.random.default_rng(seed)
    boots = []
    for _ in range(n_boot):
        i = rng.integers(0, len(x), len(x))
        if np.unique(x[i]).size > 1 and np.unique(y[i]).size > 1:
            boots.append(stats.spearmanr(x[i], y[i]).statistic)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {"rho": float(rho), "lo": float(lo), "hi": float(hi), "n_stations": int(len(x))}


# ---------------------------------------------------------------- IBM Z evidence

def ibmz_evidence(keras_model, X, n_repeats=5):
    ev = {"nnpa_in_cpuinfo": False, "zdnn_plugin_version": None, "keras_inference_benchmark": None}
    try:
        ev["nnpa_in_cpuinfo"] = "nnpa" in Path("/proc/cpuinfo").read_text()
    except OSError:
        pass
    for dist in ("ibm-zdnn-plugin", "ibm_zdnn_plugin"):
        try:
            ev["zdnn_plugin_version"] = importlib.metadata.version(dist)
            break
        except importlib.metadata.PackageNotFoundError:
            continue
    if keras_model is not None:
        keras_model.predict_proba(X[:1000])  # warm-up
        times = []
        for _ in range(n_repeats):
            t0 = time.perf_counter()
            keras_model.predict_proba(X)
            times.append(time.perf_counter() - t0)
        ev["keras_inference_benchmark"] = {"rows": int(len(X)), "best_s": min(times),
                                           "rows_per_s": int(len(X) / min(times))}
    return ev


# ---------------------------------------------------------------- run

def run_peak(journeys, dep, peak, cfg, out_dir, version):
    """Train every model for one peak, test against deprivation, write outputs.
    journeys: station_crs, service_date, failed + feature columns. dep: station_crs, dep_score."""
    df = journeys[journeys["peak"] == peak] if "peak" in journeys else journeys
    df = df[df["station_crs"].isin(dep["station_crs"])].reset_index(drop=True)
    if (df.get("source") == "synthetic").any():
        log.warning("SYNTHETIC outcomes: outputs test the pipeline only, never results")
    features = [f for f in cfg.features if f in df.columns]
    missing = sorted(set(cfg.features) - set(features))
    if missing:
        log.warning("features not in data, skipped: %s", missing)
    check_features(features)
    incomplete = df[features].isna().any(axis=1)
    if incomplete.any():
        log.warning("dropping %d trains with missing feature values", int(incomplete.sum()))
        df = df[~incomplete].reset_index(drop=True)

    models = [m for m in cfg.models if m != "keras" or has_tensorflow()]
    if "keras" in cfg.models and "keras" not in models:
        if cfg.profile == "ibmz":
            raise RuntimeError("profile ibmz needs TensorFlow (use the IBM container)")
        log.warning("TensorFlow not installed: skipping keras")

    X = df[features].to_numpy(dtype=float)
    y = df["failed"].astype(int).to_numpy()
    groups = df["station_crs"].to_numpy()
    dates = pd.to_datetime(df["service_date"]).to_numpy()
    dep_by = dep.set_index("station_crs")["dep_score"]

    out = Path(out_dir) / version / platform_tag() / peak
    out.mkdir(parents=True, exist_ok=True)
    oof = df[["station_crs", "service_date", "failed"]].copy()
    results = {"metrics": {}, "tests": {}}
    for name in models:
        log.info("[%s] %s: out-of-fold predictions (%d trains, %d stations)",
                 peak, name, len(df), len(np.unique(groups)))
        p = oof_predict(name, X, y, groups, dates, cfg)
        oof[f"p_{name}"] = p
        results["metrics"][name] = classifier_metrics(y, p)
        st = station_table(df, p)
        st["dep_score"] = st["station_crs"].map(dep_by)
        st.to_csv(out / f"station_residuals_{name}.csv", index=False)
        results["tests"][name] = {
            "raw": spearman_boot(st["dep_score"], st["observed"], cfg.n_boot, cfg.seed),
            "adjusted": spearman_boot(st["dep_score"], st["residual"], cfg.n_boot, cfg.seed)}
    oof.to_csv(out / "oof_predictions.csv", index=False)

    final = {}
    for name in models:  # final models on all data, saved for reuse on THIS platform only
        final[name] = _fit(_new_model(name, cfg), X, y, dates)
        if name == "logreg":
            joblib.dump(final[name], out / "logreg.joblib")
        elif name == "xgb" and hasattr(final[name], "save_model"):
            final[name].save_model(out / "xgb.json")
        elif name == "xgb":
            joblib.dump(final[name], out / "xgb.joblib")
        elif name == "keras":
            final[name].model.save(out / "keras.keras")
            joblib.dump(final[name].scaler, out / "keras_scaler.joblib")

    meta = {
        "version": version, "peak": peak, "profile": cfg.profile, "platform": platform_tag(),
        "machine": platform.machine(), "byteorder": sys.byteorder, "python": platform.python_version(),
        "libraries": {lib: _version(lib) for lib in
                      ("numpy", "pandas", "scikit-learn", "xgboost", "tensorflow")},
        "created": dt.datetime.now().isoformat(timespec="seconds"),
        "source": sorted(df["source"].unique().tolist()) if "source" in df else None,
        "target": cfg.target, "features": features, "primary_model": cfg.primary, "models": models,
        "n_trains": int(len(df)), "n_stations": int(df["station_crs"].nunique()),
        "failure_rate": float(y.mean()), "seed": cfg.seed, "n_folds": cfg.n_folds, "n_boot": cfg.n_boot,
        **results,
    }
    if cfg.profile == "ibmz":
        meta["ibmz"] = ibmz_evidence(final.get("keras"), X)
    (out / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    log.info("wrote %s", out)
    return meta


def _version(dist):
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return None


def summarise(meta):
    lines = [f"{meta['peak']}: {meta['n_trains']} trains, {meta['n_stations']} stations, "
             f"failure rate {meta['failure_rate']:.3f}, source {meta['source']}, target {meta.get('target')}"]
    for name, m in meta["metrics"].items():
        t = meta["tests"][name]
        lines.append(f"  {name:7s} AUC {m['roc_auc']:.3f}  Brier {m['brier']:.4f} (baseline {m['brier_baseline']:.4f})"
                     f"  raw rho {t['raw']['rho']:+.2f} [{t['raw']['lo']:+.2f}, {t['raw']['hi']:+.2f}]"
                     f"  adjusted rho {t['adjusted']['rho']:+.2f} [{t['adjusted']['lo']:+.2f}, {t['adjusted']['hi']:+.2f}]")
    if "ibmz" in meta:
        lines.append(f"  IBM Z: {meta['ibmz']}")
    return "\n".join(lines)


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    from src.config import ROOT, load_config
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true", help="fake outcomes on the real timetable")
    ap.add_argument("--effect", type=float, default=0.5, help="planted deprivation effect (synthetic)")
    ap.add_argument("--peak", default="morning", choices=["morning", "evening", "both"])
    ap.add_argument("--profile", default="local", choices=["local", "ibmz"])
    ap.add_argument("--version", default=f"v1-{dt.date.today()}")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    cfg_proj = load_config()
    proc = cfg_proj["paths"]["data_dir"] / "processed"
    dep = read_table(proc / "station_deprivation.parquet")
    weather_path = proc / "weather_hourly.parquet"
    weather = read_table(weather_path) if table_exists(weather_path) else None
    if weather is None:
        log.warning("no %s: run python -m src.weather to add rain/wind features", weather_path.name)
    if args.synthetic:
        from src.synthetic import make_outcomes
        trains = read_table(proc / "timetable_trains.parquet")
        if weather is not None:
            from src.weather import add_weather
            trains = add_weather(trains, weather)
        journeys = make_outcomes(trains, dep, effect=args.effect, seed=args.seed)
        version = f"{args.version}-synthetic"
    else:
        path = proc / "journeys.parquet"
        if not table_exists(path):
            raise SystemExit(f"{path} not found: real outcomes not built yet (use --synthetic to demo)")
        journeys, version = read_table(path), args.version
        if weather is not None and "rain_mm" not in journeys:
            from src.weather import add_weather
            journeys = add_weather(journeys, weather)

    target = "late_or_cancelled_synthetic" if args.synthetic else "late_or_cancelled_hsp_recorded"
    cfg = ModelConfig(profile=args.profile, seed=args.seed, target=target)
    for peak in (["morning", "evening"] if args.peak == "both" else [args.peak]):
        print(summarise(run_peak(journeys, dep, peak, cfg, ROOT / "models", version)))


if __name__ == "__main__":
    main()
