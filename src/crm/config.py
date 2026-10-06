"""Load config.yaml once and expose resolved paths."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def load_config(path: str | None = None) -> dict:
    cfg_path = Path(path) if path else ROOT / "config.yaml"
    with open(cfg_path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    for key, rel in cfg["paths"].items():
        full = ROOT / rel
        full.mkdir(parents=True, exist_ok=True)
        cfg["paths"][key] = full
    return cfg


def user_agent(cfg: dict) -> str:
    email = os.environ.get("CRM_CONTACT_EMAIL") or cfg["project"]["contact_email"]
    if not email or email.endswith("@example.com"):
        # Don't advertise a placeholder address as a contact point.
        return "ConductRiskMonitor/1.0 (portfolio research project)"
    return f"ConductRiskMonitor/1.0 (portfolio research project; contact: {email})"
