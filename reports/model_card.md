# Model card: complaint uphold likelihood

## Purpose
Rank incoming complaints by how likely an ombudsman is to uphold them, so a second-line team
can review the riskiest cases first. It supports human judgement and is not an automated decision.

## Data
Financial Ombudsman Service published final decisions.
Decisions dated: train 2026-06-01 to 2026-07-15 (4,757 decisions), validation 2026-07-16 to 2026-07-28 (1,162),
test 2026-07-29 to 2026-09-15 (1,930).

The split is by time (by decision date: oldest 60% train, next 15% validation, newest 25% test, because the decisions span only 4 calendar months and the month-based split needs 12), so every test decision post-dates everything the model learned from.

**Short history.** These results come from a few weeks of decisions, so they are a first read, not a validated model: the test window is short, seasonal and policy effects are invisible, and the firm rates rest on few decisions per firm. Re-run on 12 months or more before relying on it.


## Features
Text of "The complaint" section only, with any sentence mentioning the investigator, the ombudsman or an
outcome removed. Sector, primary theme and the firm's smoothed historical uphold rate, computed out-of-fold.
Excluded on purpose: "What happened" (it reports the investigator's view), decision length (upheld decisions are longer),
titles such as Mr, Mrs and Miss (a proxy for sex, a protected characteristic) and firm-name words (the firm rate
already carries firm history, and names would otherwise dominate the explanation below).

## Results on the test set
| Model | ROC-AUC | PR-AUC | Precision | Recall | F1 | Precision in top 10% |
|---|---|---|---|---|---|---|
| baseline_base_rate | 0.5 | 0.2466 | 0.0 | 0.0 | 0.0 | 0.2435 |
| baseline_firm_sector_theme | 0.6309 | 0.3387 | 0.3224 | 0.5378 | 0.4031 | 0.399 |
| text_logistic_regression | 0.6584 | 0.3685 | 0.3292 | 0.6723 | 0.442 | 0.4301 |
| text_xgboost | 0.6409 | 0.3584 | 0.3066 | 0.7626 | 0.4373 | 0.4767 |

Best model: **text_logistic_regression**. The base uphold rate in the test period is 24.7%.
Reviewing the top 10% of scored cases finds upheld complaints at 43.0%,
which is 1.7 times the base rate. The firm, sector and theme baseline reaches 0.3387 PR-AUC,
so the text adds +0.030.

## What drives the score
From the text logistic regression's coefficients (the most readable of the models, even when another scores higher).

Terms that raise it: property, car, general insurance, isa, complains charges, investment, lost falling, victim scam, complains service, bond, inconvenience, handled, scam director, complains provided, general.

Terms that lower it: unfairly, says, says lost, decision, paid, complains insurance, life, says didn, unhappy, provided irresponsibly, money says, unhappy didn, amended, private medical, disclose.

## Limitations
Published decisions are cases that reached a final ombudsman decision, so they over-represent disputed
complaints and don't reflect every complaint a firm handles. Outcomes reflect the ombudsman's approach at
the time, which shifts with regulation (for example the 2024 APP scam reimbursement rules), so the model needs
retraining as policy moves. Firm rates penalise firms with past problems, so scores should never be used to
judge an individual customer, only to order a review queue.
