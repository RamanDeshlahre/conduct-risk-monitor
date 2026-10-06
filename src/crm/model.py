"""Predict whether an ombudsman will uphold a complaint, from what's known when it arrives.

Use case: a second-line triage queue. Scores help a compliance team decide which open
complaints to review first. They support a human decision and never replace one.

Design choices that matter more than the algorithm:
  * Time-based split. Train on older decisions, tune on the next 3 months, test on the
    newest 6. A random split would let the model peek at the future.
  * Leakage-safe text. Only "The complaint" section, with outcome language stripped.
  * No decision length. Upheld decisions run longer because they include "Putting
    things right", so page count would leak the answer.
  * Out-of-fold firm rates. A firm's historical uphold rate is encoded without each
    training row seeing its own label.
  * Honest baselines. Every metric is shown next to "always guess the base rate" and
    "firm and sector only", so the text model has to earn its place.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.dummy import DummyClassifier
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (average_precision_score, brier_score_loss, confusion_matrix,
                             f1_score, precision_recall_curve, precision_score, recall_score,
                             roc_auc_score)
from sklearn.model_selection import KFold
from sklearn.preprocessing import OneHotEncoder

from .config import load_config
from .io import load_table, save_table
from .themes import firm_name_words

log = logging.getLogger(__name__)
SMOOTHING_M = 20
TITLE_WORDS = {"mr", "mrs", "miss", "ms", "mx", "dr", "sir", "lady", "lord", "he", "she", "his", "her", "him", "hers"}
# Firm-name words that are also ordinary complaint vocabulary, so they stay in the text.
KEEP_WORDS = {"car", "card", "cards", "home", "money", "insurance", "finance", "credit", "loan", "loans", "mortgage",
              "mortgages", "pension", "pensions", "travel", "pet", "motor", "life", "health", "savings", "account",
              "direct", "first", "pay", "payment", "payments", "investment", "investments", "property", "claims",
              "general", "assurance", "protection", "legal", "vehicle", "vehicles", "lending", "debt", "loans"}


def time_split(df: pd.DataFrame, test_months: int, val_months: int):
    months = df["decision_month"]
    last = months.max()
    test_start = last - pd.DateOffset(months=test_months - 1)
    val_start = test_start - pd.DateOffset(months=val_months)
    train = df[months < val_start]
    val = df[(months >= val_start) & (months < test_start)]
    test = df[months >= test_start]
    return train, val, test


def date_split(df: pd.DataFrame, fractions=(0.60, 0.15, 0.25)):
    """Split by decision date when there are too few months for the month-based split.

    Cut points fall between calendar days, so no day's decisions straddle two sets and every
    validation and test decision is dated after every training decision.
    """
    dates = df["decision_date"].dt.normalize()
    cum = dates.value_counts().sort_index().cumsum() / len(df)
    train_end = cum.index[cum.searchsorted(fractions[0])]
    val_end = cum.index[cum.searchsorted(fractions[0] + fractions[1])]
    train = df[dates <= train_end]
    val = df[(dates > train_end) & (dates <= val_end)]
    test = df[dates > val_end]
    return train, val, test


def choose_split(df: pd.DataFrame, mcfg: dict):
    """Month-based split when there's enough history, otherwise a date-based one. Returns (train, val, test, mode)."""
    n_months = df["decision_month"].nunique()
    needed = mcfg["test_months"] + mcfg["validation_months"] + mcfg.get("min_train_months", 3)
    if n_months >= needed:
        return (*time_split(df, mcfg["test_months"], mcfg["validation_months"]),
                f"by month: newest {mcfg['test_months']} months tested, {mcfg['validation_months']} before that for validation")
    fr = tuple(mcfg.get("fallback_split", (0.60, 0.15, 0.25)))
    log.warning("Only %d months of decisions (the month-based split needs %d). Splitting by decision date %s.",
                n_months, needed, fr)
    return (*date_split(df, fr),
            f"by decision date: oldest {fr[0]:.0%} train, next {fr[1]:.0%} validation, newest {fr[2]:.0%} test, "
            f"because the decisions span only {n_months} calendar months and the month-based split needs {needed}")


def smoothed_rate(y: pd.Series, groups: pd.Series, prior: float, m: int = SMOOTHING_M) -> pd.Series:
    stats = pd.DataFrame({"y": y.values, "g": groups.values}).groupby("g")["y"].agg(["sum", "count"])
    return (stats["sum"] + m * prior) / (stats["count"] + m)


@dataclass
class Features:
    min_df: int
    max_features: int
    random_state: int
    tfidf: TfidfVectorizer = field(init=False)
    onehot: OneHotEncoder = field(init=False)
    firm_rates: pd.Series = field(init=False)
    prior: float = field(init=False)

    def fit_transform(self, df: pd.DataFrame) -> sparse.csr_matrix:
        # Titles (Mr, Mrs, Miss) are a proxy for sex, a protected characteristic, and firm names duplicate the
        # firm-rate feature while crowding out the language that explains a case. Neither belongs in the text.
        stop = sorted(set(ENGLISH_STOP_WORDS) | TITLE_WORDS | (firm_name_words(df["firm_name"]) - KEEP_WORDS)) \
            if "firm_name" in df else sorted(set(ENGLISH_STOP_WORDS) | TITLE_WORDS)
        self.tfidf = TfidfVectorizer(stop_words=stop, ngram_range=(1, 2), min_df=self.min_df,
                                     max_features=self.max_features, sublinear_tf=True,
                                     token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z]+\b")
        self.onehot = OneHotEncoder(handle_unknown="ignore")
        self.prior = float(df["is_upheld"].mean())
        self.firm_rates = smoothed_rate(df["is_upheld"], df["firm_key"], self.prior)

        # Out-of-fold firm rate for training rows.
        oof = np.full(len(df), self.prior)
        for tr, va in KFold(5, shuffle=True, random_state=self.random_state).split(df):
            rates = smoothed_rate(df["is_upheld"].iloc[tr], df["firm_key"].iloc[tr], self.prior)
            oof[va] = df["firm_key"].iloc[va].map(rates).fillna(self.prior).values

        text = self.tfidf.fit_transform(df["model_text"].fillna(""))
        cats = self.onehot.fit_transform(df[["sector", "primary_theme"]].fillna("unknown"))
        return sparse.hstack([text, cats, sparse.csr_matrix(oof.reshape(-1, 1))]).tocsr()

    def transform(self, df: pd.DataFrame) -> sparse.csr_matrix:
        firm = df["firm_key"].map(self.firm_rates).fillna(self.prior).values.reshape(-1, 1)
        text = self.tfidf.transform(df["model_text"].fillna(""))
        cats = self.onehot.transform(df[["sector", "primary_theme"]].fillna("unknown"))
        return sparse.hstack([text, cats, sparse.csr_matrix(firm)]).tocsr()

    def structured_only(self, X: sparse.csr_matrix) -> sparse.csr_matrix:
        return X[:, len(self.tfidf.vocabulary_):]


def evaluate(y: np.ndarray, p: np.ndarray, threshold: float) -> dict:
    pred = (p >= threshold).astype(int)
    k = max(1, int(round(0.10 * len(p))))
    top = np.argsort(-p)[:k]
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    return {
        "n": int(len(y)),
        "base_rate": round(float(y.mean()), 4),
        "roc_auc": round(float(roc_auc_score(y, p)), 4) if len(set(y)) > 1 else None,
        "pr_auc": round(float(average_precision_score(y, p)), 4),
        "brier": round(float(brier_score_loss(y, p)), 4),
        "threshold": round(float(threshold), 4),
        "precision": round(float(precision_score(y, pred, zero_division=0)), 4),
        "recall": round(float(recall_score(y, pred, zero_division=0)), 4),
        "f1": round(float(f1_score(y, pred, zero_division=0)), 4),
        "precision_at_top_10pct": round(float(y[top].mean()), 4),
        "recall_at_top_10pct": round(float(y[top].sum() / max(y.sum(), 1)), 4),
        "confusion": {"tp": int(tp), "fp": int(fp), "fn": int(fn), "tn": int(tn)},
    }


def best_f1_threshold(y: np.ndarray, p: np.ndarray) -> float:
    prec, rec, thr = precision_recall_curve(y, p)
    f1 = 2 * prec * rec / np.clip(prec + rec, 1e-9, None)
    return float(thr[np.nanargmax(f1[:-1])]) if len(thr) else 0.5


def train_and_evaluate(df: pd.DataFrame, cfg: dict) -> dict:
    mcfg = cfg["model"]
    df = df[(df["model_text"].str.len() >= 15) & df["decision_month"].notna()].copy()
    train, val, test, split_mode = choose_split(df, mcfg)
    if min(len(train), len(val), len(test)) < 200:
        raise ValueError(f"Not enough data for a fair split: train={len(train)} val={len(val)} test={len(test)}")
    log.info("Split sizes: train=%d val=%d test=%d", len(train), len(val), len(test))

    feats = Features(mcfg["min_df"], mcfg["max_features"], mcfg["random_state"])
    Xtr, Xva, Xte = feats.fit_transform(train), feats.transform(val), feats.transform(test)
    ytr, yva, yte = train["is_upheld"].values, val["is_upheld"].values, test["is_upheld"].values

    results, scores = {}, {}

    dummy = DummyClassifier(strategy="prior").fit(Xtr, ytr)
    p = dummy.predict_proba(Xte)[:, 1]
    results["baseline_base_rate"] = evaluate(yte, p, 0.5)

    struct = LogisticRegression(max_iter=2000, class_weight="balanced")
    struct.fit(feats.structured_only(Xtr), ytr)
    thr = best_f1_threshold(yva, struct.predict_proba(feats.structured_only(Xva))[:, 1])
    p = struct.predict_proba(feats.structured_only(Xte))[:, 1]
    results["baseline_firm_sector_theme"] = evaluate(yte, p, thr)
    scores["baseline_firm_sector_theme"] = p

    best_c, best_ap = None, -1.0
    for c in [0.1, 0.3, 1.0, 3.0]:
        lr = LogisticRegression(C=c, max_iter=3000, class_weight="balanced", solver="liblinear")
        lr.fit(Xtr, ytr)
        ap = average_precision_score(yva, lr.predict_proba(Xva)[:, 1])
        if ap > best_ap:
            best_c, best_ap = c, ap
    lr = LogisticRegression(C=best_c, max_iter=3000, class_weight="balanced", solver="liblinear").fit(Xtr, ytr)
    thr = best_f1_threshold(yva, lr.predict_proba(Xva)[:, 1])
    p = lr.predict_proba(Xte)[:, 1]
    results["text_logistic_regression"] = {**evaluate(yte, p, thr), "C": best_c}
    scores["text_logistic_regression"] = p

    try:
        from xgboost import XGBClassifier

        xgb = XGBClassifier(n_estimators=600, max_depth=6, learning_rate=0.08, subsample=0.8,
                            colsample_bytree=0.5, eval_metric="aucpr", early_stopping_rounds=40,
                            scale_pos_weight=(1 - ytr.mean()) / max(ytr.mean(), 1e-6),
                            random_state=mcfg["random_state"], n_jobs=-1)
        xgb.fit(Xtr, ytr, eval_set=[(Xva, yva)], verbose=False)
        thr = best_f1_threshold(yva, xgb.predict_proba(Xva)[:, 1])
        p = xgb.predict_proba(Xte)[:, 1]
        results["text_xgboost"] = evaluate(yte, p, thr)
        scores["text_xgboost"] = p
    except ImportError:
        log.info("xgboost not installed; skipping that model.")

    best = max((k for k in scores), key=lambda k: results[k]["pr_auc"])
    vocab = np.array(feats.tfidf.get_feature_names_out())
    coefs = lr.coef_[0][: len(vocab)]
    order = np.argsort(coefs)
    explain = {
        "terms_raising_uphold_likelihood": vocab[order[::-1][:25]].tolist(),
        "terms_lowering_uphold_likelihood": vocab[order[:25]].tolist(),
    }

    prec, rec, thr_curve = precision_recall_curve(yte, scores[best])
    step = max(1, len(thr_curve) // 200)
    curve = pd.DataFrame({"precision": prec[:-1][::step], "recall": rec[:-1][::step], "threshold": thr_curve[::step]})

    scored = test[["drn", "decision_date", "decision_month", "firm_name", "firm_key", "sector",
                   "primary_theme", "theme_label", "consumer_duty_label", "is_upheld"]].copy()
    scored["score"] = scores[best]
    scored["triage_rank"] = scored["score"].rank(ascending=False, method="first").astype(int)

    return {
        "results": results, "best_model": best, "explain": explain,
        "split": {"mode": split_mode,
                  "train": [str(train.decision_date.min().date()), str(train.decision_date.max().date()), len(train)],
                  "validation": [str(val.decision_date.min().date()), str(val.decision_date.max().date()), len(val)],
                  "test": [str(test.decision_date.min().date()), str(test.decision_date.max().date()), len(test)]},
        "curve": curve, "scored": scored,
    }


def _short_history_warning(split: dict) -> str:
    if split["mode"].startswith("by month"):
        return ""
    return ("\n**Short history.** These results come from a few weeks of decisions, so they are a first read, not a "
            "validated model: the test window is short, seasonal and policy effects are invisible, and the firm "
            "rates rest on few decisions per firm. Re-run on 12 months or more before relying on it.\n")


def model_card(out: dict) -> str:
    r, best = out["results"], out["best_model"]
    b = r[best]
    base = r["baseline_base_rate"]
    struct = r["baseline_firm_sector_theme"]
    rows = "\n".join(
        f"| {name} | {m['roc_auc']} | {m['pr_auc']} | {m['precision']} | {m['recall']} | {m['f1']} | {m['precision_at_top_10pct']} |"
        for name, m in r.items()
    )
    lift = b["precision_at_top_10pct"] / max(base["base_rate"], 1e-9)
    s = out["split"]
    return f"""# Model card: complaint uphold likelihood

## Purpose
Rank incoming complaints by how likely an ombudsman is to uphold them, so a second-line team
can review the riskiest cases first. It supports human judgement and is not an automated decision.

## Data
Financial Ombudsman Service published final decisions.
Decisions dated: train {s['train'][0]} to {s['train'][1]} ({s['train'][2]:,} decisions), validation {s['validation'][0]} to {s['validation'][1]} ({s['validation'][2]:,}),
test {s['test'][0]} to {s['test'][1]} ({s['test'][2]:,}).

The split is by time ({s['mode']}), so every test decision post-dates everything the model learned from.
{_short_history_warning(s)}

## Features
Text of "The complaint" section only, with any sentence mentioning the investigator, the ombudsman or an
outcome removed. Sector, primary theme and the firm's smoothed historical uphold rate, computed out-of-fold.
Excluded on purpose: "What happened" (it reports the investigator's view), decision length (upheld decisions are longer),
titles such as Mr, Mrs and Miss (a proxy for sex, a protected characteristic) and firm-name words (the firm rate
already carries firm history, and names would otherwise dominate the explanation below).

## Results on the test set
| Model | ROC-AUC | PR-AUC | Precision | Recall | F1 | Precision in top 10% |
|---|---|---|---|---|---|---|
{rows}

Best model: **{best}**. The base uphold rate in the test period is {base['base_rate']:.1%}.
Reviewing the top 10% of scored cases finds upheld complaints at {b['precision_at_top_10pct']:.1%},
which is {lift:.1f} times the base rate. The firm, sector and theme baseline reaches {struct['pr_auc']} PR-AUC,
so the text adds {b['pr_auc'] - struct['pr_auc']:+.3f}.

## What drives the score
From the text logistic regression's coefficients (the most readable of the models, even when another scores higher).

Terms that raise it: {', '.join(out['explain']['terms_raising_uphold_likelihood'][:15])}.

Terms that lower it: {', '.join(out['explain']['terms_lowering_uphold_likelihood'][:15])}.

## Limitations
Published decisions are cases that reached a final ombudsman decision, so they over-represent disputed
complaints and don't reflect every complaint a firm handles. Outcomes reflect the ombudsman's approach at
the time, which shifts with regulation (for example the 2024 APP scam reimbursement rules), so the model needs
retraining as policy moves. Firm rates penalise firms with past problems, so scores should never be used to
judge an individual customer, only to order a review queue.
"""


def run() -> None:
    cfg = load_config()
    df = load_table(cfg["paths"]["interim"] / "fos_decisions_enriched", parse_dates=["decision_date", "decision_month"])
    out = train_and_evaluate(df, cfg)
    reports, marts = cfg["paths"]["reports"], cfg["paths"]["marts"]
    (reports / "model_metrics.json").write_text(json.dumps(
        {k: out[k] for k in ["results", "best_model", "explain", "split"]}, indent=2))
    (reports / "model_card.md").write_text(model_card(out))
    save_table(out["scored"], marts / "model_scores")
    save_table(out["curve"], marts / "model_pr_curve")
    log.info("Best model %s: %s", out["best_model"], out["results"][out["best_model"]])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run()
