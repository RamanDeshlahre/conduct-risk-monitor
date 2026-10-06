"""Ingest Financial Ombudsman Service published decisions.

Two stages:
  1. Listing crawl (cheap): each search page lists 10 decisions with reference, date,
     firm, outcome, sector and the opening lines of the complaint. 10 per request.
  2. PDF sample (richer): full decision text for a sample, which gives the complete
     complaint wording, an independent outcome check and any redress amounts.

The crawler is polite and resumable: fixed delay, identifying User-Agent, a
checkpoint file, and an incremental mode that stops at decisions it has already seen.
Read the site's terms and robots.txt before running it, and keep the delay.
"""
from __future__ import annotations

import argparse
import io
import logging
import re
import time
from pathlib import Path
from urllib.parse import urlencode, urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

from .config import load_config, user_agent
from .io import load_table, save_table, table_exists
from .names import firm_key
from .sections import complaint_from_snippet, outcome_from_final_decision, redress_amounts, split_sections

log = logging.getLogger(__name__)

LISTING_RE = re.compile(
    r"Decision Reference\s+(?P<drn>DRN-\d+)\s+"
    r"(?P<date>\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})\s+"
    r"(?P<firm>.+?)\s+"
    r"(?P<outcome>Not upheld|Upheld)\s+"
    r"(?P<sector>.+?)\s+"
    r"(?P=drn)\s+"
    r"(?P<snippet>.*?)\s*\(\s*(?P<pages>\d+)\s+pages?\s*\)",
    re.S,
)
DRN_RE = re.compile(r"DRN-\d+")
LISTING_COLUMNS = ["drn", "decision_date", "firm_name", "outcome", "sector", "snippet", "pages", "pdf_url"]


def search_url(cfg: dict, start: int) -> str:
    # The unfiltered listing is ordered by relevance, not date (the first result on 6 October 2026
    # was dated 15 September, the next ten 18 August), so the old "stop when a page predates
    # min_date" rule could stop early or never. Filtering by DateFrom server-side and sorting by
    # date makes the crawl bounded and newest-first.
    params = {"DateFrom": str(cfg["fos"]["min_date"]), "Sort": "date"}
    params.update(cfg["fos"].get("extra_params") or {})
    if start:
        params["Start"] = start
    base = cfg["fos"]["base_url"] + cfg["fos"]["search_path"]
    return f"{base}?{urlencode(params)}" if params else base


def _text(node) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip() if node is not None else ""


def _parse_structured(a) -> dict | None:
    """Read the live markup (October 2026): h3 reference, em date, firm and outcome either side of a
    separator span in .search-result__info-main, sector tag, and a description ending '(N pages)'."""
    info = a.select_one(".search-result__info-main")
    date_el, tag_el, desc_el = a.find("em"), a.select_one(".search-result__tag"), a.select_one(".search-result__desc")
    ref = DRN_RE.search(_text(a.find("h3")) or a.get("href", ""))
    if not (info and date_el and tag_el and desc_el and ref):
        return None
    parts = [p.strip() for p in info.find_all(string=True, recursive=False) if p.strip()]
    if len(parts) < 2 or parts[-1] not in ("Upheld", "Not upheld"):
        return None
    desc = _text(desc_el)
    pages = re.search(r"\(\s*(\d+)\s+pages?\s*\)\s*$", desc)
    return {
        "drn": ref.group(0), "date": _text(date_el), "firm": " ".join(parts[:-1]), "outcome": parts[-1],
        "sector": _text(tag_el), "snippet": desc[: pages.start()].strip() if pages else desc,
        "pages": int(pages.group(1)) if pages else None,
    }


def parse_listing(html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    rows, seen = [], set()
    # Live links are relative ("decision/DRN-123.pdf"), so don't require a leading slash.
    for a in soup.select('a[href*="DRN-"]'):
        rec = _parse_structured(a)
        if rec is None:  # fall back to reading the flattened text
            container = a.find_parent("li") or a
            m = LISTING_RE.search(_text(container))
            rec = m.groupdict() if m else None
            if rec:
                rec["pages"] = int(rec["pages"])
        if not rec or rec["drn"] in seen:
            continue
        seen.add(rec["drn"])
        rows.append({
            "drn": rec["drn"],
            "decision_date": pd.to_datetime(rec["date"], format="%d %b %Y", errors="coerce"),
            "firm_name": rec["firm"].strip(),
            "outcome": rec["outcome"],
            "sector": rec["sector"].strip(),
            "snippet": rec["snippet"].strip(),
            "pages": rec["pages"],
            # urljoin against the site root: a relative href must not resolve under the search path.
            "pdf_url": urljoin(base_url.rstrip("/") + "/", a["href"]),
        })
    return rows


def _fetch(session: requests.Session, url: str, retries: int = 4) -> requests.Response:
    for attempt in range(retries):
        try:
            resp = session.get(url, timeout=60)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.HTTPError(f"HTTP {resp.status_code}")
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            wait = 10 * (2 ** attempt)
            log.warning("Fetch failed (%s). Retrying in %ss: %s", exc, wait, url)
            time.sleep(wait)
    raise RuntimeError(f"Gave up after {retries} attempts: {url}")


def crawl_listing(incremental: bool = False) -> Path:
    cfg = load_config()
    fos = cfg["fos"]
    out_base = cfg["paths"]["raw"] / "fos_listing"
    existing = load_table(out_base, parse_dates=["decision_date"]) if table_exists(out_base) else pd.DataFrame(columns=LISTING_COLUMNS)
    known = set(existing["drn"])
    min_date = pd.Timestamp(fos["min_date"])

    session = requests.Session()
    session.headers["User-Agent"] = user_agent(cfg)

    new_rows: list[dict] = []
    last_dates: list[pd.Timestamp] = []
    page = 0
    while page < fos["max_pages"]:
        url = search_url(cfg, page * fos["page_size"])
        rows = parse_listing(_fetch(session, url).text, fos["base_url"])
        if not rows:
            log.info("Empty page at %d; crawl complete.", page)
            break

        fresh = [r for r in rows if r["drn"] not in known]
        new_rows.extend(fresh)
        known.update(r["drn"] for r in fresh)
        dates = [r["decision_date"] for r in rows if pd.notna(r["decision_date"])]
        last_dates.extend(dates)

        if incremental and not fresh:
            log.info("Incremental mode: page %d holds only known decisions. Stopping.", page)
            break
        if dates and max(dates) < min_date:
            log.info("Every decision on page %d predates %s. Stopping.", page, min_date.date())
            break

        page += 1
        if page % fos["checkpoint_every"] == 0:
            _save_listing(existing, new_rows, out_base)
            log.info("Checkpoint: %d pages, %d new decisions, oldest date %s",
                     page, len(new_rows), min(last_dates).date() if last_dates else "n/a")
        time.sleep(fos["delay_seconds"])

    # Sorting sanity check: the early stop assumes newest-first ordering.
    if len(last_dates) > 50:
        s = pd.Series(last_dates)
        out_of_order = (s.diff().dt.days > 31).mean()
        if out_of_order > 0.05:
            log.warning("Listing does not look date-sorted (%.0f%% jumps). Set fos.extra_params to the "
                        "site's 'sort by date' parameters so the early stop is reliable.", 100 * out_of_order)

    return _save_listing(existing, new_rows, out_base)


def _save_listing(existing: pd.DataFrame, new_rows: list[dict], out_base: Path) -> Path:
    df = pd.concat([existing, pd.DataFrame(new_rows, columns=LISTING_COLUMNS)], ignore_index=True)
    df = df.drop_duplicates("drn", keep="last")
    df["firm_key"] = df["firm_name"].map(firm_key)
    df["complaint_snippet"] = df["snippet"].map(complaint_from_snippet)
    return save_table(df, out_base)


def extract_pdf_text(content: bytes) -> str:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(content)) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def fetch_pdf_sample() -> Path:
    cfg = load_config()
    fos = cfg["fos"]
    listing = load_table(cfg["paths"]["raw"] / "fos_listing", parse_dates=["decision_date"])
    out_base = cfg["paths"]["raw"] / "fos_decision_text"
    done = load_table(out_base) if table_exists(out_base) else pd.DataFrame(columns=["drn"])

    pool = listing[(listing["decision_date"] >= pd.Timestamp(fos["pdf_sample_min_date"])) & ~listing["drn"].isin(done["drn"])]
    target = max(0, fos["pdf_sample_size"] - len(done))
    # Stratify by sector and month so the sample mirrors the population, not just the busiest weeks.
    pool = pool.assign(month=pool["decision_date"].dt.to_period("M"))
    frac = min(1.0, target / max(len(pool), 1))
    sample = (
        pool.groupby(["sector", "month"], observed=True)
        .sample(frac=frac, random_state=cfg["model"]["random_state"])
        .head(target)
    )
    log.info("Downloading %d decision PDFs", len(sample))

    session = requests.Session()
    session.headers["User-Agent"] = user_agent(cfg)
    rows = []
    for i, rec in enumerate(sample.itertuples(index=False), start=1):
        try:
            text = extract_pdf_text(_fetch(session, rec.pdf_url).content)
            sec = split_sections(text)
            amounts = redress_amounts(sec["putting_right"])
            rows.append({
                "drn": rec.drn,
                "complaint_full": sec["complaint"],
                "what_happened": sec["what_happened"],
                "outcome_from_text": outcome_from_final_decision(sec["final_decision"]),
                "redress_max_gbp": max(amounts) if amounts else None,
                "n_chars": len(text),
            })
        except Exception as exc:
            log.warning("PDF %s failed: %s", rec.drn, exc)
        if i % 200 == 0:
            save_table(pd.concat([done, pd.DataFrame(rows)], ignore_index=True), out_base)
            log.info("Checkpoint: %d PDFs", i)
        time.sleep(fos["delay_seconds"])

    return save_table(pd.concat([done, pd.DataFrame(rows)], ignore_index=True).drop_duplicates("drn"), out_base)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Crawl FOS decisions")
    parser.add_argument("stage", choices=["listing", "pdfs"])
    parser.add_argument("--incremental", action="store_true", help="stop at the first page of already-seen decisions")
    args = parser.parse_args()
    crawl_listing(args.incremental) if args.stage == "listing" else fetch_pdf_sample()
