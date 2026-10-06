"""Move tables between local files and the warehouse (DuckDB locally, BigQuery in the cloud).

load:   data/raw + data/interim -> warehouse raw schema (dbt sources)
export: warehouse marts schema  -> data/marts (what the dashboard reads)
"""
from __future__ import annotations

import argparse
import logging

import pandas as pd

from .config import ROOT, load_config
from .io import load_table, save_table

log = logging.getLogger(__name__)

RAW_TABLES = {
    "fca_firm_complaints": ("raw", ["period_start"]),
    "fos_decisions_enriched": ("interim", ["decision_date", "decision_month"]),
}
MART_TABLES = ["fca_firm_period", "fos_firm_theme_month", "fos_market_month", "fos_month_coverage", "firm_dim"]
FOS_COLUMNS = [
    "drn", "decision_date", "decision_month", "firm_name", "firm_key", "outcome", "is_upheld", "sector",
    "primary_theme", "theme_label", "consumer_duty_outcome", "consumer_duty_label", "themes",
    "has_full_text", "redress_max_gbp",
]


def _frames(cfg: dict) -> dict[str, pd.DataFrame]:
    out = {}
    for name, (folder, dates) in RAW_TABLES.items():
        df = load_table(cfg["paths"][folder] / name, parse_dates=dates)
        if name == "fos_decisions_enriched":
            df = df[[c for c in FOS_COLUMNS if c in df.columns]]  # the warehouse never needs free text
        out[name] = df
    return out


def load() -> None:
    cfg = load_config()
    wh = cfg["warehouse"]
    frames = _frames(cfg)
    if wh["target"] == "duckdb":
        import duckdb

        con = duckdb.connect(str(ROOT / wh["duckdb_path"]))
        con.execute("CREATE SCHEMA IF NOT EXISTS raw")
        for name, df in frames.items():
            con.register("tmp_df", df)
            con.execute(f"CREATE OR REPLACE TABLE raw.{name} AS SELECT * FROM tmp_df")
            con.unregister("tmp_df")
            log.info("Loaded raw.%s (%d rows) into DuckDB", name, len(df))
        con.close()
    elif wh["target"] == "bigquery":
        from google.cloud import bigquery

        client = bigquery.Client(project=wh["bigquery_project"], location=wh["bigquery_location"])
        dataset = f"{wh['bigquery_project']}.{wh['bigquery_dataset_raw']}"
        client.create_dataset(bigquery.Dataset(dataset), exists_ok=True)
        for name, df in frames.items():
            job = client.load_table_from_dataframe(
                df, f"{dataset}.{name}",
                job_config=bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE"))
            job.result()
            log.info("Loaded %s.%s (%d rows) into BigQuery", dataset, name, len(df))
    else:
        raise ValueError(f"Unknown warehouse target {wh['target']}")


def export() -> None:
    cfg = load_config()
    wh = cfg["warehouse"]
    if wh["target"] == "duckdb":
        import duckdb

        con = duckdb.connect(str(ROOT / wh["duckdb_path"]), read_only=True)
        fetch = lambda t: con.execute(f"SELECT * FROM marts.{t}").df()  # noqa: E731
    else:
        from google.cloud import bigquery

        client = bigquery.Client(project=wh["bigquery_project"], location=wh["bigquery_location"])
        ds = f"{wh['bigquery_project']}.{wh['bigquery_dataset_marts']}"
        fetch = lambda t: client.query(f"SELECT * FROM `{ds}.{t}`").to_dataframe()  # noqa: E731
    for table in MART_TABLES:
        df = fetch(table)
        save_table(df, cfg["paths"]["marts"] / table)
        log.info("Exported marts.%s (%d rows)", table, len(df))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["load", "export"])
    load() if parser.parse_args().action == "load" else export()
