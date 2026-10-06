"""Join the FOS listing with PDF text, build leakage-safe model text and tag themes.

Output: data/interim/fos_decisions_enriched, one row per decision.
"""
from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd

from .config import load_config
from .io import load_table, save_table, table_exists
from .names import firm_key
from .sections import complaint_from_snippet, leakage_rate, strip_leaky_sentences
from .themes import discover, firm_name_words, tag_frame

log = logging.getLogger(__name__)


def build(listing: pd.DataFrame, texts: pd.DataFrame | None) -> tuple[pd.DataFrame, dict]:
    df = listing.copy()
    df["decision_date"] = pd.to_datetime(df["decision_date"], errors="coerce")
    # Re-derive from the raw summary so a change to the parsing rules applies to every decision,
    # not just those crawled after the change.
    df["complaint_snippet"] = df["snippet"].map(complaint_from_snippet)
    df["firm_key"] = df["firm_name"].map(firm_key)
    if texts is not None and not texts.empty:
        df = df.merge(texts, on="drn", how="left")
    else:
        for col in ["complaint_full", "outcome_from_text", "redress_max_gbp"]:
            df[col] = np.nan

    # Prefer the full complaint section; fall back to the listing summary.
    full = df["complaint_full"].where(df["complaint_full"].astype(str).str.len() > 20)
    df["complaint_text_raw"] = full.fillna(df["complaint_snippet"]).fillna("")
    df["has_full_text"] = full.notna()
    df["model_text"] = df["complaint_text_raw"].map(strip_leaky_sentences)
    df["is_upheld"] = (df["outcome"] == "Upheld").astype(int)
    df["decision_month"] = df["decision_date"].dt.to_period("M").dt.to_timestamp()

    df = tag_frame(df, "complaint_text_raw")

    # Data quality report. Each check is a number a reviewer can sanity-check.
    checked = df[df["outcome_from_text"].notna()]
    dq = {
        "rows": int(len(df)),
        "duplicate_drn": int(df["drn"].duplicated().sum()),
        "missing_date": int(df["decision_date"].isna().sum()),
        "missing_firm": int((df["firm_key"].fillna("") == "").sum()),
        "empty_model_text": int((df["model_text"].str.len() < 10).sum()),
        "full_text_share": round(float(df["has_full_text"].mean()), 4),
        "outcome_crosscheck_n": int(len(checked)),
        "outcome_crosscheck_agreement": round(float((checked["outcome_from_text"] == checked["outcome"]).mean()), 4)
        if len(checked) else None,
        "leaky_sentences_in_raw_complaint_text": round(leakage_rate(df["complaint_text_raw"]), 4),
        "leakage_rate_model_text": round(leakage_rate(df["model_text"]), 4),
        "theme_coverage": round(float((df["primary_theme"] != "other").mean()), 4),
        "date_min": str(df["decision_date"].min().date()) if df["decision_date"].notna().any() else None,
        "date_max": str(df["decision_date"].max().date()) if df["decision_date"].notna().any() else None,
        "uphold_rate": round(float(df["is_upheld"].mean()), 4) if len(df) else None,
        "unexpected_outcome_values": int((~df["outcome"].isin(["Upheld", "Not upheld"])).sum()),
        "snippet_without_complaint_heading": int((~df["snippet"].fillna("").str.contains(r"^\s*(?:DRN-\d+\s+)?(?:The\s+)?complaint\b", case=False)).sum()),
        "decisions_by_month": {str(k.date()): int(v) for k, v in df.groupby("decision_month").size().items()},
        "decisions_by_sector": {str(k): int(v) for k, v in df["sector"].value_counts().items()},
        "theme_counts": {str(k): int(v) for k, v in df["primary_theme"].value_counts().items()},
    }
    if dq["leakage_rate_model_text"] > 0:
        raise ValueError(f"Leakage guard failed: {dq['leakage_rate_model_text']:.1%} of model texts contain outcome language.")
    return df, dq


def _pct(x) -> str:
    return "n/a" if x is None else f"{x:.1%}"


def data_quality_markdown(dq: dict, fca: dict | None) -> str:
    """Readable version of data_quality.json. Every figure is computed, never typed."""
    from datetime import date

    def status(ok: bool) -> str:
        return "Pass" if ok else "Check"

    lines = ["# Data quality report", "", f"Generated {date.today().day} {date.today():%B %Y} from the data in this run.", ""]
    if fca:
        periods = fca["periods"]
        firms = fca["firms_with_opened_data_per_period"]
        lines += [
            "## FCA firm-level complaints returns", "",
            f"{len(periods)} half-years parsed ({periods[0]} to {periods[-1]}), {fca['rows']:,} rows in long format.", "",
            "| Check | Result | Status |", "|---|---|---|",
            f"| Half-years parsed | {len(periods)} of 14 | {status(len(periods) >= 14)} |",
            f"| Firms with 'opened' data per half-year | {min(firms.values())} to {max(firms.values())} | {status(min(firms.values()) >= 100)} |",
            f"| Duplicate firm-product-metric rows (firm changed reporting dates) | {fca['duplicate_rows_resolved']} resolved, latest return kept | Pass |",
            f"| Percentages outside 0-100 (source errors) | {sum(d['rows'] for d in fca['percentages_outside_0_100_dropped'])} blanked | "
            f"{status(sum(d['rows'] for d in fca['percentages_outside_0_100_dropped']) < 10)} |",
            "", "| Half-year | Firms | Firms with opened data | Complaints opened |", "|---|---|---|---|",
        ]
        lines += [f"| {p} | {fca['firms_per_period'][p]} | {firms.get(p, 0)} | {fca['complaints_opened_per_period'].get(p, 0):,} |"
                  for p in periods]
        if fca["percentages_outside_0_100_dropped"]:
            lines += ["", "Blanked percentages: " + "; ".join(
                f"{d['period']} {d['metric'].replace('_', ' ')} ({d['rows']})" for d in fca["percentages_outside_0_100_dropped"]) + "."]
        lines.append("")
    lines += [
        "## Financial Ombudsman Service decisions", "",
        f"{dq['rows']:,} published decisions dated {dq['date_min']} to {dq['date_max']}, from the listing pages "
        f"(full decision PDFs: {_pct(dq['full_text_share'])} of rows).", "",
        "| Check | Result | Status |", "|---|---|---|",
        f"| Duplicate decision references | {dq['duplicate_drn']} | {status(dq['duplicate_drn'] == 0)} |",
        f"| Missing decision date | {dq['missing_date']} | {status(dq['missing_date'] == 0)} |",
        f"| Missing firm | {dq['missing_firm']} | {status(dq['missing_firm'] == 0)} |",
        f"| Outcome not 'Upheld' or 'Not upheld' | {dq['unexpected_outcome_values']} | {status(dq['unexpected_outcome_values'] == 0)} |",
        f"| Summary without a 'The complaint' heading | {dq['snippet_without_complaint_heading']} | "
        f"{status(dq['snippet_without_complaint_heading'] <= 0.01 * dq['rows'])} |",
        f"| Model text under 10 characters | {dq['empty_model_text']} | {status(dq['empty_model_text'] <= 0.01 * dq['rows'])} |",
        f"| Raw complaint text containing outcome language | {_pct(dq['leaky_sentences_in_raw_complaint_text'])} | Information |",
        f"| Model text containing outcome language (leakage guard) | {_pct(dq['leakage_rate_model_text'])} | {status(dq['leakage_rate_model_text'] == 0)} |",
        f"| Decisions tagged to a theme | {_pct(dq['theme_coverage'])} | {status(dq['theme_coverage'] >= 0.6)} |",
        f"| Decisions against a firm also in the FCA data | {_pct(dq.get('decisions_against_firms_in_fca_data'))} | Information |",
        f"| PDF outcome agrees with listing | "
        f"{'not run (no PDFs fetched)' if not dq['outcome_crosscheck_n'] else _pct(dq['outcome_crosscheck_agreement'])} | "
        f"{'Information' if not dq['outcome_crosscheck_n'] else status(dq['outcome_crosscheck_agreement'] >= 0.95)} |",
        "", f"Overall uphold rate: {_pct(dq['uphold_rate'])}.", "",
        "| Month | Decisions |", "|---|---|",
    ]
    lines += [f"| {m[:7]} | {n:,} |" for m, n in dq["decisions_by_month"].items()]
    lines += ["", "| Sector | Decisions |", "|---|---|"] + [f"| {k} | {v:,} |" for k, v in dq["decisions_by_sector"].items()]
    lines += ["", "| Primary theme | Decisions |", "|---|---|"] + [f"| {k} | {v:,} |" for k, v in dq["theme_counts"].items()]
    return "\n".join(lines) + "\n"


def run() -> None:
    cfg = load_config()
    listing = load_table(cfg["paths"]["raw"] / "fos_listing", parse_dates=["decision_date"])
    text_base = cfg["paths"]["raw"] / "fos_decision_text"
    texts = load_table(text_base) if table_exists(text_base) else None
    df, dq = build(listing, texts)
    save_table(df, cfg["paths"]["interim"] / "fos_decisions_enriched")

    reports = cfg["paths"]["reports"]
    fca_dq_path = reports / "fca_data_quality.json"
    fca_dq = json.loads(fca_dq_path.read_text()) if fca_dq_path.exists() else None
    fca_base = cfg["paths"]["raw"] / "fca_firm_complaints"
    if fca_dq is not None and table_exists(fca_base):
        fca_keys = set(load_table(fca_base)["firm_key"])
        dq["decisions_against_firms_in_fca_data"] = round(float(df["firm_key"].isin(fca_keys).mean()), 4)
    (reports / "data_quality.json").write_text(json.dumps({"fos": dq, "fca": fca_dq}, indent=2))
    (reports / "data_quality.md").write_text(data_quality_markdown(dq, fca_dq))
    log.info("Data quality: %s", {k: v for k, v in dq.items() if not isinstance(v, dict)})

    # Firm names are stop words here, but only words that aren't also everyday complaint language.
    common = {"car", "card", "home", "money", "insurance", "finance", "credit", "loan", "loans", "mortgage", "pension",
              "travel", "pet", "motor", "life", "health", "savings", "account", "direct", "first", "one", "pay"}
    topics = discover(df.loc[df["primary_theme"] == "other", "complaint_text_raw"], random_state=cfg["model"]["random_state"],
                      extra_stop_words=firm_name_words(df["firm_name"]) - common)
    lines = ["# Theme discovery on untagged complaints", "",
             "Each row is a cluster of language the taxonomy missed. Promote useful ones into taxonomy/themes.yaml.", ""]
    lines += [f"{i}. {', '.join(terms)}" for i, terms in enumerate(topics, start=1)] or ["Not enough untagged text yet."]
    (reports / "theme_discovery.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run()
