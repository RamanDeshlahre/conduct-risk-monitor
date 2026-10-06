"""Flag firms whose complaint picture has moved outside its normal range.

Two independent signals, so one data source's quirks don't drive the watchlist alone.

FCA half-yearly data (firm x product group):
    Robust z-score of the latest period against the firm's own history, using the
    median and MAD so one past spike doesn't hide the next. Peer position compares
    the firm's complaints per 1,000 accounts with others in the same product group.

FOS decisions (firm x theme x month):
    Poisson test: given the firm-theme's trailing 12-month average, how surprising is
    this month's count? Hundreds of firm-theme pairs are tested at once, so p-values
    are corrected with Benjamini-Hochberg to control false alarms. Uphold-rate shifts
    use a binomial test against the firm-theme's own baseline.

RAG: red means review now, amber means watch, green means within normal range.
"""
from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd
from scipy import stats

from .config import load_config
from .coverage import complete_months
from .io import load_table, save_table, table_exists

log = logging.getLogger(__name__)
MAD_SCALE = 1.4826
# The FCA's denominator for "complaints per 1,000" differs by product group.
CONTEXT_UNITS = {
    "banking_and_credit_cards": "1,000 accounts",
    "decumulation_and_pensions": "1,000 policies in force",
    "home_finance": "1,000 balances outstanding",
    "insurance_and_pure_protection": "1,000 policies in force",
    "investments": "1,000 client accounts",
}  # makes MAD comparable to a standard deviation under normality


def bh_adjust(p: pd.Series) -> pd.Series:
    """Benjamini-Hochberg false discovery rate adjustment."""
    p = p.astype(float)
    mask = p.notna()
    vals = p[mask].values
    n = len(vals)
    if n == 0:
        return p
    order = np.argsort(vals)
    ranked = vals[order] * n / np.arange(1, n + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(n)
    out[order] = np.clip(ranked, 0, 1)
    result = p.copy()
    result[mask] = out
    return result


def robust_z(history: np.ndarray, latest: float) -> float:
    med = np.median(history)
    mad = np.median(np.abs(history - med)) * MAD_SCALE
    if mad == 0:
        spread = np.std(history) or max(abs(med) * 0.1, 1.0)
        return float((latest - med) / spread)
    return float((latest - med) / mad)


def fca_flags(fca: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """fca: mart fca_firm_period, one row per firm x product_group x period."""
    ew = cfg["early_warning"]
    fca = fca.sort_values("period_start")
    latest_period = fca["period_start"].max()
    rows = []
    for (key, product), g in fca.groupby(["firm_key", "product_group"]):
        g = g.dropna(subset=["opened"])
        if g.empty or g["period_start"].iloc[-1] != latest_period:
            continue
        hist, last = g.iloc[:-1], g.iloc[-1]
        z = robust_z(hist["opened"].values, last["opened"]) if len(hist) >= ew["fca_min_history"] else np.nan
        rows.append({
            "firm_key": key, "firm_name": last["firm_name"], "product_group": product,
            "period": last["period"], "opened": last["opened"],
            "opened_median_history": float(hist["opened"].median()) if len(hist) else np.nan,
            "history_periods": len(hist), "volume_z": z,
            "per_1000_accounts": last.get("context_provision", np.nan),
            "pct_upheld_by_firm": last.get("pct_upheld", np.nan),
        })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["peer_percentile"] = out.groupby("product_group")["per_1000_accounts"].rank(pct=True)

    min_opened = ew.get("fca_min_opened", 0)

    def rag(r) -> str:
        if r["opened"] < min_opened:  # a jump from 3 to 66 complaints is a big z-score but not a conduct signal
            return "green"
        z, peer = r["volume_z"], r["peer_percentile"]
        high_peer = pd.notna(peer) and peer >= ew["fca_peer_percentile"]
        if pd.notna(z) and (z >= ew["fca_z_red"] or (high_peer and z >= ew["fca_z_amber"])):
            return "red"
        if (pd.notna(z) and z >= ew["fca_z_amber"]) or high_peer:
            return "amber"
        return "green"

    out["rag"] = out.apply(rag, axis=1)
    out["reason"] = out.apply(lambda r: _fca_reason(r, min_opened), axis=1)
    return out.sort_values(["rag", "volume_z"], key=_rag_sort_key, ascending=[True, False])


def _fca_reason(r, min_opened: int = 0) -> str:
    if r["opened"] < min_opened:
        return f"Too few complaints to judge ({int(r['opened']):,} opened, threshold {min_opened:,})"
    parts = []
    if pd.notna(r["volume_z"]) and r["volume_z"] >= 2:
        typical = r["opened_median_history"]
        change = f" ({r['opened'] / typical - 1:+,.0%})" if typical > 0 else ""
        parts.append(f"{int(r['opened']):,} complaints opened against a typical {int(typical):,}{change}")
    if pd.notna(r["peer_percentile"]) and r["peer_percentile"] >= 0.9:
        unit = CONTEXT_UNITS.get(r["product_group"], "1,000 accounts")
        parts.append(f"{r['per_1000_accounts']:.2f} per {unit}, top decile of peers")
    return "; ".join(parts) or "Within normal range"


def _rag_sort_key(col: pd.Series) -> pd.Series:
    if col.name == "rag":
        return col.map({"red": 0, "amber": 1, "green": 2})
    return col


def fos_flags(fos: pd.DataFrame, cfg: dict, latest: pd.Timestamp | None = None) -> pd.DataFrame:
    """fos: mart fos_firm_theme_month with decisions and upheld counts.

    latest: the month to test, normally the latest *complete* month (see crm.coverage). Months after it
    are partly published, so they are dropped rather than read as a fall in volumes.
    """
    ew = cfg["early_warning"]
    fos = fos.copy()
    fos["decision_month"] = pd.to_datetime(fos["decision_month"])
    if latest is None:
        latest = fos["decision_month"].max()
    fos = fos[fos["decision_month"] <= latest]
    window_start = latest - pd.DateOffset(months=ew["fos_baseline_months"])
    rows = []
    for (key, theme), g in fos.groupby(["firm_key", "primary_theme"]):
        g = g.set_index("decision_month").sort_index()
        current = g.loc[g.index == latest]
        x = int(current["decisions"].sum()) if len(current) else 0
        up = int(current["upheld"].sum()) if len(current) else 0
        base = g[(g.index >= window_start) & (g.index < latest)]
        months_observed = (latest.to_period("M") - g.index.min().to_period("M")).n
        if months_observed < ew["fos_min_baseline_months"] or (x == 0 and base.empty):
            continue
        # Months with no decisions count as zero. A pair first seen 8 months ago divides by 8, not 12.
        lam = float(base["decisions"].sum()) / min(ew["fos_baseline_months"], months_observed)
        p_volume = float(stats.poisson.sf(x - 1, max(lam, 0.1))) if x >= ew["fos_min_count"] else np.nan
        base_rate = (base["upheld"].sum() + 1) / (base["decisions"].sum() + 2)  # Laplace smoothing
        p_uphold = float(stats.binomtest(up, x, base_rate, alternative="greater").pvalue) if x >= ew["fos_min_count"] else np.nan
        rows.append({
            "firm_key": key, "firm_name": g["firm_name"].iloc[-1], "primary_theme": theme,
            "theme_label": g["theme_label"].iloc[-1], "consumer_duty_label": g["consumer_duty_label"].iloc[-1],
            "month": latest, "decisions": x, "expected": round(lam, 2), "upheld": up,
            "uphold_rate": up / x if x else np.nan, "baseline_uphold_rate": round(float(base_rate), 4),
            "p_volume": p_volume, "p_uphold": p_uphold,
        })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["q_volume"] = bh_adjust(out["p_volume"])
    out["q_uphold"] = bh_adjust(out["p_uphold"])
    q = out[["q_volume", "q_uphold"]].min(axis=1)
    out["rag"] = np.where(q <= ew["fos_q_red"], "red", np.where(q <= ew["fos_q_amber"], "amber", "green"))
    out["reason"] = out.apply(_fos_reason, axis=1)
    return out.sort_values(["rag", "decisions"], key=_rag_sort_key, ascending=[True, False])


def _fos_reason(r) -> str:
    parts = []
    if pd.notna(r["q_volume"]) and r["q_volume"] <= 0.10:
        parts.append(f"{r['decisions']} decisions against about {r['expected']:.1f} expected")
    if pd.notna(r["q_uphold"]) and r["q_uphold"] <= 0.10:
        parts.append(f"{r['uphold_rate']:.0%} upheld against a usual {r['baseline_uphold_rate']:.0%}")
    return "; ".join(parts) or "Within normal range"


def watchlist(fca_ew: pd.DataFrame, fos_ew: pd.DataFrame) -> pd.DataFrame:
    """One row per firm: the worst signal from either source, with the reason in plain English."""
    frames = []
    if not fca_ew.empty:
        frames.append(fca_ew.assign(source="FCA complaints return", area=fca_ew["product_group"].str.replace("_", " ").str.capitalize())
                      [["firm_key", "firm_name", "source", "area", "rag", "reason"]])
    if not fos_ew.empty:
        frames.append(fos_ew.assign(source="Ombudsman decisions", area=fos_ew["theme_label"])
                      [["firm_key", "firm_name", "source", "area", "rag", "reason"]])
    if not frames:
        return pd.DataFrame(columns=["firm_key", "firm_name", "source", "area", "rag", "reason"])
    allsig = pd.concat(frames, ignore_index=True)
    allsig = allsig[allsig["rag"] != "green"]
    allsig["rag_order"] = allsig["rag"].map({"red": 0, "amber": 1})
    # Stable sort keeps each source's own severity order (largest z-score, most decisions) within a RAG band,
    # so "review first" lists the strongest signals first rather than alphabetically.
    return allsig.sort_values("rag_order", kind="stable").drop(columns="rag_order").reset_index(drop=True)


def run() -> None:
    cfg = load_config()
    marts = cfg["paths"]["marts"]
    fca = load_table(marts / "fca_firm_period", parse_dates=["period_start"])
    fos = load_table(marts / "fos_firm_theme_month", parse_dates=["decision_month"])
    ew = cfg["early_warning"]

    fos_month, fos_status, months_history = None, "no ombudsman data", 0
    if table_exists(marts / "fos_month_coverage") and not fos.empty:
        cov = complete_months(load_table(marts / "fos_month_coverage"), cfg["fos"].get("complete_month_tolerance_days", 3))
        complete = cov.loc[cov["is_complete"], "decision_month"]
        if len(complete):
            fos_month = complete.max()
            months_history = int(len(cov[(cov["decision_month"] < fos_month) & cov["is_complete"]]))
    if fos_month is None and not fos.empty:
        fos_status = "no complete month of ombudsman decisions yet"
    elif fos_month is not None and months_history < ew["fos_min_baseline_months"]:
        fos_status = (f"skipped (needs {ew['fos_min_baseline_months']} complete months of history before "
                      f"{fos_month:%B %Y}; has {months_history})")
    elif fos_month is not None:
        fos_status = "ok"

    fca_ew = fca_flags(fca, cfg)
    fos_ew = fos_flags(fos, cfg, latest=fos_month) if fos_status == "ok" else pd.DataFrame()
    wl = watchlist(fca_ew, fos_ew)
    save_table(fca_ew, marts / "ew_fca")
    save_table(fos_ew, marts / "ew_fos")
    save_table(wl, marts / "watchlist")
    summary = {
        "fca_period": str(fca_ew["period"].iloc[0]) if len(fca_ew) else None,
        "fca_firm_products_tested": int(len(fca_ew)),
        "fca_firm_products_with_enough_history": int(fca_ew["volume_z"].notna().sum()) if len(fca_ew) else 0,
        "fos_month": str(fos_month.date()) if fos_month is not None else None,
        "fos_status": fos_status,
        "fos_complete_months_of_history": months_history,
        "fos_firm_themes_tested": int(len(fos_ew)),
        "red_signals": int((wl["rag"] == "red").sum()), "amber_signals": int((wl["rag"] == "amber").sum()),
        "red_firms": int(wl.loc[wl["rag"] == "red", "firm_key"].nunique()),
        "amber_only_firms": int(wl["firm_key"].nunique() - wl.loc[wl["rag"] == "red", "firm_key"].nunique()),
        "firms_flagged": int(wl["firm_key"].nunique()),
    }
    (cfg["paths"]["reports"] / "early_warning_summary.json").write_text(json.dumps(summary, indent=2))
    log.info("Early warning: %s", summary)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run()
