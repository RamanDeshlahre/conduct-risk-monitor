"""Which ombudsman decision months are complete enough to judge.

FOS publishes decisions several weeks after they are issued, and a handful of later decisions can appear
before the rest of their month. On 6 October 2026 the newest published decision was dated 15 September
and the one before that 18 August, so treating "the latest month" as September (one decision) or August
(published to the 18th) would read publication lag as a collapse in complaint volumes.

A month counts as complete when its decisions reach within `tolerance_days` of the month end.
"""
from __future__ import annotations

import pandas as pd


def complete_months(coverage: pd.DataFrame, tolerance_days: int = 3) -> pd.DataFrame:
    """coverage: mart fos_month_coverage. Returns it with an `is_complete` flag, oldest first."""
    cov = coverage.copy()
    cov["decision_month"] = pd.to_datetime(cov["decision_month"])
    cov["first_decision_date"] = pd.to_datetime(cov["first_decision_date"])
    cov["last_decision_date"] = pd.to_datetime(cov["last_decision_date"])
    cov = cov.sort_values("decision_month").reset_index(drop=True)
    tol = pd.Timedelta(days=tolerance_days)
    month_end = cov["decision_month"] + pd.offsets.MonthEnd(0)
    reaches_end = cov["last_decision_date"] >= month_end - tol
    reaches_start = cov["first_decision_date"] <= cov["decision_month"] + tol  # crawl may start mid-month
    cov["is_complete"] = reaches_end & reaches_start
    # Once a month stops short of its end, every later month is publication-lag stragglers.
    gaps = cov.index[~reaches_end]
    if len(gaps):
        cov.loc[cov.index >= gaps[0], "is_complete"] = False
    return cov


def latest_complete_month(coverage: pd.DataFrame, tolerance_days: int = 3) -> pd.Timestamp | None:
    cov = complete_months(coverage, tolerance_days)
    done = cov.loc[cov["is_complete"], "decision_month"]
    return done.max() if len(done) else None
