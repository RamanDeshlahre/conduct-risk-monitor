"""Write the one-page MI note a Head of Compliance would actually read.

Every number is computed from the marts, so the note regenerates each month with the data.
The analyst's job is then to edit the "So what" section, not to retype figures.
"""
from __future__ import annotations

import json
import logging
from datetime import date

import pandas as pd

from .config import load_config
from .coverage import complete_months
from .io import load_table, table_exists

log = logging.getLogger(__name__)


def _theme_shift(market: pd.DataFrame, months: list) -> tuple[pd.DataFrame, int]:
    """Compare the latest k complete months with the k before, by theme. k is 3 (a quarter) when there
    are six or more complete months, otherwise as many as the history allows (at least 1)."""
    k = min(3, len(months) // 2)
    if k < 1:
        return pd.DataFrame(), 0
    m = market[market["decision_month"].isin(months)]
    m = m.groupby(["decision_month", "theme_label"], as_index=False)[["decisions", "upheld"]].sum()
    recent, prior = months[-k:], months[-2 * k:-k]
    r = m[m["decision_month"].isin(recent)].groupby("theme_label")[["decisions", "upheld"]].sum()
    p = m[m["decision_month"].isin(prior)].groupby("theme_label")[["decisions", "upheld"]].sum()
    out = r.join(p, lsuffix="_recent", rsuffix="_prior", how="outer").fillna(0)
    out["change"] = out["decisions_recent"] - out["decisions_prior"]
    out["uphold_recent"] = out["upheld_recent"] / out["decisions_recent"].where(out["decisions_recent"] > 0)
    out["uphold_prior"] = out["upheld_prior"] / out["decisions_prior"].where(out["decisions_prior"] > 0)
    return out.sort_values("change", ascending=False), k


def _fca_section(fca: pd.DataFrame, wl: pd.DataFrame) -> list[str]:
    if fca.empty:
        return []
    by_period = fca.groupby(["period_start", "period"], as_index=False)["opened"].sum().sort_values("period_start")
    latest, prev = by_period.iloc[-1], by_period.iloc[-2] if len(by_period) > 1 else None
    lines = ["", "## FCA complaints returns", "",
             f"Firms in the FCA's firm-level data reported {int(latest.opened):,} complaints opened in {latest.period}"]
    if prev is not None:
        change = latest.opened / prev.opened - 1
        lines[-1] += f", {abs(change):.1%} {'up' if change >= 0 else 'down'} on {prev.period} ({int(prev.opened):,})"
    lines[-1] += "."
    latest_rows = fca[fca["period"] == latest.period]
    prod = latest_rows.groupby("product_group")["opened"].sum().sort_values(ascending=False)
    lines.append("By product group: " + "; ".join(
        f"{k.replace('_', ' ').capitalize()} {int(v):,}" for k, v in prod.items()) + ".")
    if not wl.empty:
        f = wl[wl["source"] == "FCA complaints return"]
        red_keys = set(f.loc[f.rag == "red", "firm_key"])
        amber_only = set(f.loc[f.rag == "amber", "firm_key"]) - red_keys
        lines.append(f"Against their own history and their peers, {len(red_keys)} firms are red on at least one product "
                     f"group and a further {len(amber_only)} amber.")
    return lines


def build_note(cfg: dict) -> str:
    marts, reports = cfg["paths"]["marts"], cfg["paths"]["reports"]
    market = load_table(marts / "fos_market_month", parse_dates=["decision_month"])
    fca = load_table(marts / "fca_firm_period", parse_dates=["period_start"]) if table_exists(marts / "fca_firm_period") else pd.DataFrame()
    wl = load_table(marts / "watchlist") if table_exists(marts / "watchlist") else pd.DataFrame()
    metrics = json.loads((reports / "model_metrics.json").read_text()) if (reports / "model_metrics.json").exists() else None
    ew = json.loads((reports / "early_warning_summary.json").read_text()) if (reports / "early_warning_summary.json").exists() else {}

    cov = complete_months(load_table(marts / "fos_month_coverage"), cfg["fos"].get("complete_month_tolerance_days", 3))
    complete = sorted(cov.loc[cov["is_complete"], "decision_month"])
    partial = cov[~cov["is_complete"]]

    total = int(market["decisions"].sum())
    rate = market["upheld"].sum() / max(total, 1)
    first, last = market["decision_month"].min(), market["decision_month"].max()
    title_month = complete[-1] if complete else last
    cd = market.groupby("consumer_duty_label")[["decisions", "upheld"]].sum()
    cd["rate"] = cd["upheld"] / cd["decisions"]
    cd = cd.sort_values("decisions", ascending=False)
    shift, k = _theme_shift(market, complete)

    lines = [
        f"# Conduct risk MI note: {title_month:%B %Y}",
        "",
        f"Prepared {date.today().day} {date.today():%B %Y}. Covers {total:,} published ombudsman decisions dated "
        f"{first:%B %Y} to {last:%B %Y}, plus FCA complaints returns"
        + (f" to {fca['period'].max()}." if not fca.empty else "."),
    ]
    if len(partial):
        parts = [f"{r.decision_month:%B} ({int(r.decisions):,} decision{'s' if r.decisions != 1 else ''}, "
                 f"published to {r.last_decision_date.day} {r.last_decision_date:%B})" for r in partial.itertuples()]
        desc = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
        lines.append("")
        lines.append(f"The ombudsman publishes decisions several weeks after issuing them, so {desc} "
                     f"{'is' if len(partial) == 1 else 'are'} incomplete. They count in the totals but not in month-on-month comparisons.")
    lines += ["", "## Headlines", "", f"The ombudsman upheld {rate:.0%} of complaints across the period."]
    if not shift.empty:
        top = shift.drop(index="Other", errors="ignore").head(3)
        span = "quarter" if k == 3 else ("month" if k == 1 else f"{k} months")
        window = f"in {complete[-1]:%B}" if k == 1 else f"over the last {k} complete months"
        rising = [f"{t} ({int(r.decisions_recent):,} decisions, {int(r.change):+,} on the previous {span})"
                  for t, r in top.iterrows() if r.change > 0]
        if rising:
            lines.append(f"The fastest-growing themes {window} were " + "; ".join(rising) + ".")
    if not wl.empty:
        red_keys = set(wl.loc[wl["rag"] == "red", "firm_key"])
        n_red, n_amber = len(red_keys), len(set(wl.loc[wl["rag"] == "amber", "firm_key"]) - red_keys)
        lines.append(f"{n_red} {'firm is' if n_red == 1 else 'firms are'} red on the watchlist and a further "
                     f"{n_amber} amber (a firm counts once, at its worst signal).")
    if ew.get("fos_status") and ew["fos_status"] != "ok":
        need = cfg["early_warning"]["fos_min_baseline_months"]
        have = ew.get("fos_complete_months_of_history", 0)
        if ew.get("fos_month"):
            lines.append(f"The ombudsman-decision early-warning test did not run. It needs {need} complete months of history "
                         f"before the month tested, and this data has {have} before {pd.Timestamp(ew['fos_month']):%B %Y}. "
                         "The watchlist therefore rests on the FCA returns alone.")
        else:
            lines.append("The ombudsman-decision early-warning test did not run because no month of decisions is complete yet. "
                         "The watchlist therefore rests on the FCA returns alone.")
    lines += ["", "## Consumer Duty outcomes", "", "| Outcome | Decisions | Upheld |", "|---|---|---|"]
    lines += [f"| {k_} | {int(r.decisions):,} | {r.rate:.0%} |" for k_, r in cd.iterrows()]
    if "Not mapped" in cd.index:
        lines += ["", f"{cd.loc['Not mapped', 'decisions'] / total:.0%} of decisions don't match a theme rule yet. "
                  "reports/theme_discovery.md lists the recurring language in them, as candidates for new rules."]

    lines += _fca_section(fca, wl)

    if not wl.empty:
        red = wl[wl["rag"] == "red"]
        lines += ["", "## Firms to review first", ""]
        if len(red) > 10:
            lines.append(f"The 10 strongest of {len(red)} red signals. The dashboard has the full list.")
            lines.append("")
        lines += ["| Firm | Signal | Area | Why |", "|---|---|---|---|"]
        for r in red.head(10).itertuples():
            lines.append(f"| {r.firm_name} | {r.source} | {r.area} | {r.reason} |")

    if metrics:
        best = metrics["results"][metrics["best_model"]]
        lift = best["precision_at_top_10pct"] / max(best["base_rate"], 1e-9)
        lines += ["", "## Triage model", "",
                  f"Reviewing the top 10% of complaints by predicted uphold likelihood would find upheld cases at "
                  f"{best['precision_at_top_10pct']:.0%}, against a base rate of {best['base_rate']:.0%}. "
                  f"That's {lift:.1f} times better than reviewing at random."]
        if not metrics.get("split", {}).get("mode", "by month").startswith("by month"):
            lines.append(f"This is a first read from {metrics['split']['test'][2]:,} test decisions over a few weeks, "
                         "not a validated model. See the model card.")

    lines += ["", "## So what", "",
              "Edit this section. Say what the numbers mean for the firm, what you'd do next, and who owns it.",
              "", "## Caveats", "",
              "Published decisions are complaints that reached an ombudsman, not every complaint a firm receives. "
              "FCA returns cover firms reporting 500 or more complaints in six months, and firms report on their own "
              "financial year, so periods don't align exactly. Theme tags are rule-based and reviewed, not perfect."]
    return "\n".join(lines) + "\n"


def run() -> None:
    cfg = load_config()
    out = cfg["paths"]["reports"] / "mi_note.md"
    out.write_text(build_note(cfg))
    log.info("Wrote %s", out)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run()
