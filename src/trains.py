"""Picks the train-data source named in config (`train_source: hsp | maven`) and writes
data/processed/service_level.parquet in the common schema (src.schema).

Run:  python -m src.trains
"""
import logging

from src import schema
from src.config import load_config

log = logging.getLogger(__name__)


def load_source(cfg):
    name = cfg.get("train_source", "hsp")
    if name == "hsp":
        from src import hsp
        return hsp.build_service_level(cfg)
    if name == "maven":
        from src import maven
        df = maven.build_service_level(cfg)
        log.warning("USING MOCK DATA (Maven). Results are for pipeline testing only.")
        return df
    raise ValueError(f"unknown train_source: {name!r}")


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cfg = load_config()
    df = schema.conform(load_source(cfg))
    out = cfg["paths"]["data_dir"] / "processed" / "service_level.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    print(f"service_level: {len(df)} rows -> {out}")


if __name__ == "__main__":
    main()
