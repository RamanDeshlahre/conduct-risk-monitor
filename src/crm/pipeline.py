"""Run pipeline steps from one entry point: python -m crm.pipeline <step> [<step> ...]"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys

from .config import ROOT

STEPS = {
    "fca": lambda: __import__("crm.ingest_fca", fromlist=["run"]).run(),
    "fos": lambda: __import__("crm.ingest_fos", fromlist=["crawl_listing"]).crawl_listing(incremental=False),
    "fos-update": lambda: __import__("crm.ingest_fos", fromlist=["crawl_listing"]).crawl_listing(incremental=True),
    "pdfs": lambda: __import__("crm.ingest_fos", fromlist=["fetch_pdf_sample"]).fetch_pdf_sample(),
    "enrich": lambda: __import__("crm.enrich", fromlist=["run"]).run(),
    "load": lambda: __import__("crm.warehouse", fromlist=["load"]).load(),
    "dbt": lambda: _dbt(),
    "export": lambda: __import__("crm.warehouse", fromlist=["export"]).export(),
    "detect": lambda: __import__("crm.early_warning", fromlist=["run"]).run(),
    "model": lambda: __import__("crm.model", fromlist=["run"]).run(),
    "note": lambda: __import__("crm.mi_note", fromlist=["run"]).run(),
}


def _dbt() -> None:
    dirs = ["--project-dir", str(ROOT / "dbt"), "--profiles-dir", str(ROOT / "dbt")]
    if subprocess.run(["dbt", "build", *dirs], cwd=ROOT / "dbt").returncode != 0:
        sys.exit("dbt build failed. Fix the failing test before exporting marts.")
    # `dbt build` doesn't check source freshness, so the freshness rules in sources.yml need their own run.
    if subprocess.run(["dbt", "source", "freshness", *dirs], cwd=ROOT / "dbt").returncode != 0:
        sys.exit("dbt source freshness failed: the newest ombudsman decision is over 90 days old. Re-run the crawl.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("steps", nargs="+", choices=list(STEPS))
    for step in parser.parse_args().steps:
        logging.info("=== %s ===", step)
        STEPS[step]()
