"""Ingest FCA firm-level complaints data into one tidy table.

Output grain: one row per period x firm x product group x metric.

The FCA spreadsheets carry a few title rows above the real header, an invisible
byte-order mark before "Firm Name", thousands separators, and dashes for blanks.
The parser finds the header by searching for "Firm Name" rather than trusting a
fixed row number, and it fails loudly if a sheet doesn't look the way we expect.
"""
from __future__ import annotations

import io
import json
import logging
import re
import time
from pathlib import Path

import pandas as pd
import requests

from .config import load_config, user_agent
from .io import save_table
from .names import firm_key

log = logging.getLogger(__name__)

# Order matters. "Closed after 3 days but within 8 weeks" contains "3 days" and "closed",
# so the more specific rules are checked first. Sheet names can be truncated at 31 characters,
# and the FCA has used at least five spellings for the 3-days-to-8-weeks sheet
# ("% >3 days & <8 ", "closed >3 days and less than 8 ", "Percentage after 3 days, within"...).
SHEET_RULES: list[tuple[str, list[str]]] = [
    ("skip", ["note", "joint", "contents", "readme", "guide", "trading name"]),
    ("consumer_credit", ["consumer credit", "cc "]),
    ("context_intermediation", ["intermediation", "context b"]),
    ("context_provision", ["provision", "context a", "context"]),
    ("pct_closed_3d_to_8w", ["after 3", "8 week", "after", ">3", "3 days to", "less than 8", "<8"]),
    ("pct_closed_within_3d", ["within 3", "3 day"]),
    ("pct_upheld", ["upheld"]),
    ("closed", ["closed"]),
    ("opened", ["open", "received"]),
]
PCT_METRICS = {"pct_closed_3d_to_8w", "pct_closed_within_3d", "pct_upheld"}

# The sortable HTML page shows the same 8 tables in a fixed tab order.
HTML_TAB_ORDER = [
    "context_provision", "context_intermediation", "opened", "closed",
    "pct_closed_within_3d", "pct_closed_3d_to_8w", "pct_upheld", "consumer_credit",
]

ID_COLUMNS = {
    "firm name": "firm_name",
    "group": "group_name",
    "firm group": "group_name",
    "joint reporting": "joint_reporting",
    "joint report": "joint_reporting",
    "reporting period": "reporting_period",
}
# Descriptive columns that appear in some years and must never be melted into product groups.
DROP_COLUMNS = {"website", "submission date", "reporting frequency", "semester", "frn", "nan", ""}

# Consumer credit sheets are laid out by measure, not product. Normalise the spellings used over the years.
CONSUMER_CREDIT_MEASURES = {
    "complaints opened": "complaints_opened",
    "complaints received": "complaints_opened",
    "complaints closed": "complaints_closed",
    "percentage upheld": "pct_upheld",
    "complaints upheld": "pct_upheld",
    "complaints upheld (%)": "pct_upheld",
    "complaints upheld by firm": "pct_upheld",
}
_DROPPED_PCT: list[dict] = []  # collected for the data quality report
_UNITS_RE = re.compile(r"\s*\((?:per [^)]*|n/a)\)\s*$", re.I)


def classify_sheet(sheet_name: str) -> str | None:
    name = sheet_name.lower().strip()
    for metric, keys in SHEET_RULES:
        if any(k in name for k in keys):
            return metric
    return None


def _clean_header(value) -> str:
    return str(value).replace("\ufeff", "").strip()


def _find_header_row(raw: pd.DataFrame, max_scan: int = 30) -> int | None:
    for i in range(min(max_scan, len(raw))):
        cells = [_clean_header(v).lower() for v in raw.iloc[i].tolist()]
        if "firm name" in cells:
            return i
    return None


def _to_number(series: pd.Series) -> pd.Series:
    text = (
        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.strip()
        .replace({"-": None, "": None, "nan": None, "None": None, "n/a": None, "N/A": None})
    )
    return pd.to_numeric(text, errors="coerce")


def _looks_like_fractions(values: pd.Series) -> bool:
    """True when a percentage column is stored as 0-1 fractions. Uses the 95th percentile, not the
    maximum, because a single bad cell (e.g. 7.83 in the 2024H2 consumer credit sheet) would otherwise
    stop the whole sheet being rescaled."""
    values = values.dropna()
    return not values.empty and values.quantile(0.95) <= 1.0


def tidy_table(table: pd.DataFrame, metric: str, period: str, source: str) -> pd.DataFrame:
    """Turn one wide FCA table (firms x product groups) into long format."""
    table = table.copy()
    table.columns = [_clean_header(c) for c in table.columns]
    # 2019 workbooks repeat "Firm Name" twice and carry blank spacer columns. Keep the first of each label.
    table = table.loc[:, ~pd.Index(table.columns).duplicated()]
    table = table[[c for c in table.columns if c.lower() not in DROP_COLUMNS]]
    rename = {c: ID_COLUMNS[c.lower()] for c in table.columns if c.lower() in ID_COLUMNS}
    table = table.rename(columns=rename)
    table = table.loc[:, ~pd.Index(table.columns).duplicated()]
    if "firm_name" not in table.columns:
        raise ValueError(f"[{period}/{metric}] no 'Firm Name' column found. Columns: {list(table.columns)}")

    table = table[table["firm_name"].notna()]
    table = table[~table["firm_name"].astype(str).str.strip().str.lower().isin(["", "nan", "total", "grand total"])]

    id_cols = [c for c in dict.fromkeys(ID_COLUMNS.values()) if c in table.columns]
    value_cols = [
        c for c in table.columns
        if c not in id_cols and c.lower() not in {"grand total", "total"} and not c.lower().startswith("unnamed")
    ]
    long = table.melt(id_vars=id_cols, value_vars=value_cols, var_name="product_group", value_name="value_raw")
    long["value"] = _to_number(long["value_raw"])
    long = long.dropna(subset=["value"]).drop(columns="value_raw")

    if metric in PCT_METRICS and _looks_like_fractions(long["value"]):
        long["value"] = long["value"] * 100  # stored as fractions in most files
    if metric == "consumer_credit" and not long.empty:
        long["product_group"] = long["product_group"].map(
            lambda c: CONSUMER_CREDIT_MEASURES.get(c.strip().lower(), c))
        # The "upheld" header doesn't say whether it's a share or a count (2021H2-2023H2 use counts
        # under the same heading), so decide from the values themselves.
        up = long["product_group"] == "pct_upheld"
        if up.any():
            if _looks_like_fractions(long.loc[up, "value"]):
                long.loc[up, "value"] = long.loc[up, "value"] * 100
            elif long.loc[up, "value"].median() > 100:
                long.loc[up, "product_group"] = "complaints_upheld"
    is_pct = (metric in PCT_METRICS) | ((metric == "consumer_credit") & (long["product_group"] == "pct_upheld"))
    bad = is_pct & ~long["value"].between(0, 100)
    if bad.any():
        _DROPPED_PCT.append({"period": period, "metric": metric, "rows": int(bad.sum())})
        log.warning("[%s/%s] %d percentage values outside 0-100 set to blank: %s", period, metric, int(bad.sum()),
                    long.loc[bad, ["firm_name", "product_group", "value"]].head(3).to_dict("records"))
        long = long[~bad]

    long["metric"] = metric
    long["period"] = period
    long["source"] = source
    long["firm_name"] = long["firm_name"].astype(str).str.strip()
    long["firm_key"] = long["firm_name"].map(firm_key)
    long["product_group"] = (
        long["product_group"].astype(str).str.replace(_UNITS_RE, "", regex=True).str.strip().str.lower()
        .str.replace("&", "and", regex=False).str.replace(r"[^a-z0-9]+", "_", regex=True).str.strip("_")
    )
    for col in ["group_name", "joint_reporting", "reporting_period"]:
        if col not in long.columns:
            long[col] = None
    return long[[
        "period", "firm_name", "firm_key", "group_name", "joint_reporting",
        "reporting_period", "product_group", "metric", "value", "source",
    ]]


def parse_workbook(content: bytes, period: str) -> pd.DataFrame:
    sheets = pd.read_excel(io.BytesIO(content), sheet_name=None, header=None, dtype=object)
    frames, unknown = [], []
    for sheet_name, raw in sheets.items():
        metric = classify_sheet(sheet_name)
        if metric is None:
            unknown.append(sheet_name)
            continue
        if metric == "skip":
            continue
        header_row = _find_header_row(raw)
        if header_row is None:
            log.warning("[%s] sheet '%s' has no 'Firm Name' header; skipped", period, sheet_name)
            continue
        table = raw.iloc[header_row + 1:].copy()
        table.columns = [_clean_header(c) for c in raw.iloc[header_row].tolist()]
        frames.append(tidy_table(table, metric, period, source=f"xlsx:{sheet_name}"))
    if unknown:
        log.warning("[%s] unrecognised sheets ignored: %s", period, unknown)
    if not frames:
        raise ValueError(f"[{period}] no usable sheets. Sheet names were: {list(sheets)}")
    return pd.concat(frames, ignore_index=True)


def parse_sortable_html(html: str, period: str) -> pd.DataFrame:
    """Fallback for the latest period: the FCA's sortable HTML tables."""
    tables = pd.read_html(io.StringIO(html))
    tables = [t for t in tables if any("firm name" in _clean_header(c).lower() for c in t.columns)]
    if len(tables) != len(HTML_TAB_ORDER):
        log.warning("[%s] expected %d tables on the sortable page, found %d", period, len(HTML_TAB_ORDER), len(tables))
    frames = [
        tidy_table(t, metric, period, source="html")
        for metric, t in zip(HTML_TAB_ORDER, tables)
    ]
    return pd.concat(frames, ignore_index=True)


def period_start(period: str) -> pd.Timestamp:
    match = re.fullmatch(r"(\d{4})H([12])", period)
    if not match:
        raise ValueError(f"Bad period label '{period}'. Use e.g. 2025H2.")
    year, half = int(match.group(1)), int(match.group(2))
    return pd.Timestamp(year=year, month=1 if half == 1 else 7, day=1)


def reporting_period_end(text) -> pd.Timestamp:
    """'01 January 2019 to 30 June 2019', '2021-05-01 to 2021-10-31' or '31-12-2020 to 29-06-2021' -> end date."""
    if not isinstance(text, str) or " to " not in text:
        return pd.NaT
    end = text.split(" to ")[-1].strip()
    fmt = "%Y-%m-%d" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", end) else None
    return pd.to_datetime(end, format=fmt, dayfirst=fmt is None, errors="coerce")


def resolve_duplicates(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """A firm that changes its reporting dates can appear twice in one file (e.g. Kensington Mortgage
    Company in 2023H2). Keep the return covering the most recent reporting period, so the mart's
    one-row-per-firm-product-period grain holds without silently taking max() of two returns."""
    grain = ["period", "firm_key", "product_group", "metric"]
    df = df.assign(_end=df["reporting_period"].map(reporting_period_end))
    df = df.sort_values(grain + ["_end"], na_position="first", kind="stable")
    dupes = int(df.duplicated(grain, keep="last").sum())
    return df.drop_duplicates(grain, keep="last").drop(columns="_end").reset_index(drop=True), dupes


def download(url: str, dest: Path, session: requests.Session) -> bytes:
    if dest.exists():
        return dest.read_bytes()
    resp = session.get(url, timeout=60)
    resp.raise_for_status()
    dest.write_bytes(resp.content)
    return resp.content


def run() -> Path:
    cfg = load_config()
    raw_dir: Path = cfg["paths"]["raw"] / "fca"
    raw_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = user_agent(cfg)

    frames, failed = [], []
    for item in cfg["fca"]["periods"]:
        period, url = item["period"], item["url"]
        try:
            content = download(url, raw_dir / f"{period}.xlsx", session)
            frames.append(parse_workbook(content, period))
            log.info("[%s] parsed %s", period, url)
        except Exception as exc:  # keep going so one bad file doesn't hide others, then fail loudly
            log.error("[%s] failed: %s", period, exc)
            failed.append(period)
        time.sleep(1)

    if failed:
        raise RuntimeError(f"FCA periods failed to parse: {failed}. Fix before building marts, "
                           "or the early-warning history will have silent gaps.")
    df = pd.concat(frames, ignore_index=True)
    df["period_start"] = df["period"].map(period_start)
    df, dupes = resolve_duplicates(df)
    if dupes:
        log.warning("Resolved %d duplicate firm-product-metric rows by keeping the latest reporting period", dupes)

    # Data quality gate: a period with suspiciously few firms usually means a layout change.
    firms_per_period = df[df["metric"] == "opened"].groupby("period")["firm_key"].nunique()
    for period, n in firms_per_period.items():
        if n < 100:
            log.warning("[%s] only %d firms with 'opened' data. Inspect the workbook layout.", period, n)

    opened = df[df["metric"] == "opened"]
    dq = {
        "periods": sorted(df["period"].unique().tolist()),
        "rows": int(len(df)),
        "firms_per_period": {k: int(v) for k, v in df.groupby("period")["firm_key"].nunique().items()},
        "firms_with_opened_data_per_period": {k: int(v) for k, v in firms_per_period.items()},
        "complaints_opened_per_period": {k: int(v) for k, v in opened.groupby("period")["value"].sum().items()},
        "duplicate_rows_resolved": dupes,
        "percentages_outside_0_100_dropped": _DROPPED_PCT.copy(),
        "rows_by_metric": {k: int(v) for k, v in df["metric"].value_counts().sort_index().items()},
    }
    (cfg["paths"]["reports"] / "fca_data_quality.json").write_text(json.dumps(dq, indent=2))

    out = save_table(df, cfg["paths"]["raw"] / "fca_firm_complaints")
    log.info("Saved %d rows to %s", len(df), out)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run()
