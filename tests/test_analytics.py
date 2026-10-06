"""Analytics tests on synthetic data. Synthetic data is used here only to prove the maths works;
the real pipeline runs on downloaded FCA and FOS data."""
import numpy as np
import pandas as pd

from crm.config import load_config
from crm.early_warning import bh_adjust, fca_flags, fos_flags, robust_z, watchlist
from crm.enrich import build
from crm.model import choose_split, time_split, train_and_evaluate
from crm.themes import tag


def test_theme_rules():
    assert tag("Mr A lost money to an investment scam and wants it reimbursed")[0] == "app_scam"
    assert tag("Miss C complains about a car supplied under a hire purchase agreement")[0] == "motor_finance"
    assert "affordability" in tag("She says the loans were unaffordable and should not have been lent")
    assert tag("Mrs B is unhappy the insurer declined her claim for an escape of water")[0] == "claim_declined"
    assert tag("He says the bank closed his account without warning")[0] == "account_restrictions"
    assert tag("Something entirely different") == []


def test_bh_adjust_matches_hand_calculation():
    q = bh_adjust(pd.Series([0.01, 0.04, 0.03, 0.20]))
    # sorted p: .01 .03 .04 .20 -> p*n/rank: .04 .06 .0533 .20 -> cumulative min from the top: .04 .0533 .0533 .20
    assert np.allclose(q.values, [0.04, 0.05333333, 0.05333333, 0.20])
    assert bh_adjust(pd.Series([np.nan, 0.5])).isna().iloc[0]


def test_robust_z_ignores_one_past_spike():
    assert robust_z(np.array([100, 102, 98, 101, 400]), 101) < 1
    assert robust_z(np.array([100, 102, 98, 101, 99]), 160) > 3


def _fca_mart():
    rows = []
    periods = [f"{y}H{h}" for y in range(2021, 2026) for h in (1, 2)]
    rng = np.random.default_rng(0)
    for i, firm in enumerate(["steady_bank", "spiking_bank", "small_lender"]):
        for j, p in enumerate(periods):
            opened = 1000 + rng.normal(0, 20)
            if firm == "spiking_bank" and j == len(periods) - 1:
                opened = 1600
            rows.append({"period": p, "period_start": pd.Timestamp(int(p[:4]), 1 if p[-1] == "1" else 7, 1),
                         "firm_key": firm, "firm_name": firm.replace("_", " ").title(), "product_group": "banking",
                         "opened": opened, "pct_upheld": 40.0, "context_provision": 1.0 + i})
    return pd.DataFrame(rows)


def test_fca_flags_catch_a_planted_spike():
    out = fca_flags(_fca_mart(), load_config()).set_index("firm_key")
    assert out.loc["spiking_bank", "rag"] == "red"
    assert out.loc["steady_bank", "rag"] == "green"


def _fos_mart():
    rows = []
    months = pd.date_range("2024-01-01", "2026-06-01", freq="MS")
    rng = np.random.default_rng(1)
    for firm in [f"firm_{i}" for i in range(30)]:
        for m in months:
            n = int(rng.poisson(4))
            if firm == "firm_0" and m == months[-1]:
                n = 25
            if n:
                up = int(rng.binomial(n, 0.35))
                rows.append({"firm_key": firm, "firm_name": firm, "primary_theme": "app_scam",
                             "theme_label": "Scams and fraud reimbursement", "consumer_duty_label": "Consumer support",
                             "decision_month": m, "decisions": n, "upheld": up})
    return pd.DataFrame(rows)


def test_fos_flags_catch_a_planted_spike_without_flooding_false_alarms():
    out = fos_flags(_fos_mart(), load_config()).set_index("firm_key")
    assert out.loc["firm_0", "rag"] == "red"
    assert (out.drop(index="firm_0")["rag"] == "red").sum() <= 1  # BH keeps false alarms down
    wl = watchlist(pd.DataFrame(), out.reset_index())
    assert wl.iloc[0]["firm_key"] == "firm_0"


def _synthetic_decisions(n=6000, seed=7):
    """Text carries real signal: 'ignored warnings' cases are upheld more often than 'clear terms' cases."""
    rng = np.random.default_rng(seed)
    months = pd.date_range("2023-01-01", "2026-06-01", freq="MS")
    phrases_up = ["the bank ignored clear warning signs", "the firm failed to explain the charges",
                  "the lender did not check affordability"]
    phrases_down = ["the policy terms clearly excluded this", "he authorised the payment and was warned",
                    "the information provided was clear"]
    rows = []
    for i in range(n):
        up = rng.random() < 0.35
        pool = phrases_up if (up and rng.random() < 0.7) or (not up and rng.random() < 0.3) else phrases_down
        text = f"Mr X complains that {pool[rng.integers(len(pool))]} and he lost money"
        rows.append({"drn": f"DRN-{i}", "decision_date": months[rng.integers(len(months))],
                     "firm_name": f"Firm {rng.integers(40)}", "outcome": "Upheld" if up else "Not upheld",
                     "sector": rng.choice(["Banking and Payments", "Insurance", "Consumer Credit"]),
                     "snippet": f"DRN-{i} The complaint {text}. What happened Our investigator upheld it",
                     "pages": 4})
    df = pd.DataFrame(rows)
    df["firm_key"] = df["firm_name"].str.lower().str.replace(" ", "_")
    from crm.sections import complaint_from_snippet
    df["complaint_snippet"] = df["snippet"].map(complaint_from_snippet)
    return df


def test_enrich_keeps_investigator_view_out_of_model_text():
    enriched, dq = build(_synthetic_decisions(300), None)
    assert dq["leakage_rate_model_text"] == 0
    assert not enriched["model_text"].str.contains("investigator", case=False).any()
    assert dq["duplicate_drn"] == 0


def test_time_split_never_trains_on_the_future():
    enriched, _ = build(_synthetic_decisions(2000), None)
    train, val, test = time_split(enriched, 6, 3)
    assert train.decision_month.max() < val.decision_month.min() <= val.decision_month.max() < test.decision_month.min()


def test_model_beats_baselines_on_learnable_signal():
    enriched, _ = build(_synthetic_decisions(), None)
    cfg = load_config()
    out = train_and_evaluate(enriched, {**cfg, "model": {**cfg["model"], "min_df": 2}})
    r = out["results"]
    assert r["text_logistic_regression"]["pr_auc"] > r["baseline_firm_sector_theme"]["pr_auc"]
    assert r["text_logistic_regression"]["roc_auc"] > 0.6
    assert len(out["scored"]) == r["text_logistic_regression"]["n"]


def test_short_history_falls_back_to_a_date_split_without_overlap():
    enriched, _ = build(_synthetic_decisions(2000), None)
    short = enriched[enriched["decision_month"] >= "2026-04-01"].copy()
    short["decision_date"] = short["decision_month"] + pd.to_timedelta(np.arange(len(short)) % 28, unit="D")
    train, val, test, mode = choose_split(short, load_config()["model"])
    assert mode.startswith("by decision date")
    assert train.decision_date.max() < val.decision_date.min() <= val.decision_date.max() < test.decision_date.min()
    assert len(train) + len(val) + len(test) == len(short)
