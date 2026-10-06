# Methodology

This document explains the decisions behind the pipeline, so anyone reviewing it can challenge them.

## 1. Data

### FCA firm-level complaints returns

Firms reporting at least 500 complaints in a six-month period, or 1,000 a year, appear by name. Around 290 firms qualify, and they account for the vast majority of reported complaints. Each return gives complaints opened and closed, the share closed within three days and within eight weeks, the share the firm upheld, and "context" figures: complaints per 1,000 accounts or policies.

The workbooks are not consistent from year to year, and the parser handles each difference explicitly rather than trusting a fixed layout: the header row moves (row 4 or row 11 in 2019, row 1 from 2022), 2019 files repeat the "Firm Name" column, the group column is "Firm Group" or "Group", the "closed after 3 days" sheet has had at least five names, context headers carry their units ("Banking and credit cards (per 1,000 accounts)") and must be stripped so they line up with the volume sheets, and the consumer credit "upheld" column holds a share in some years and a count in others under similar headings, so the parser decides from the values. Percentages stored as fractions are rescaled using the 95th percentile rather than the maximum, so one bad cell can't stop a whole sheet being converted. A handful of source cells are impossible (an uphold rate of 120%, a consumer credit uphold rate of 783%); these are blanked and listed in the data quality report. When a firm changes its reporting dates it can appear twice in one file, and the return covering the later reporting period is kept.

Two quirks shape the analysis. Firms report on their own financial year, so "2025 H2" can mean April to September for one firm and July to December for another. The pipeline keeps the reporting period and treats the FCA's publication period as the time axis. Context figures for insurance can be inflated where firms can't count historic PPI policies, which the FCA flags itself, so peer comparisons sit within product groups.

### Financial Ombudsman Service decisions

Every final decision since April 2013 is published and anonymised. The listing page gives reference, date, firm, outcome, sector and the opening lines. The PDF adds the full text.

The search page lists results by relevance unless asked otherwise, so the crawler always sends `DateFrom` and `Sort=date`. That bounds the crawl on the server and makes incremental refreshes stop at the first page of decisions already seen.

Decisions are published several weeks after they are issued, and a few later ones appear early. On 6 October 2026 the newest decision on the site was dated 15 September and the next newest 18 August. A month counts as complete only once its published decisions reach within three days of the month end, and every month after the first incomplete one is treated as incomplete too. Early warning and month-on-month comparisons use complete months only, so publication lag is never read as a fall in complaints.

Published decisions are a selected sample. A complaint only reaches one after the firm's final response, the customer escalating, an investigator's view and one side rejecting it. That makes them a good signal of contested conduct and a poor estimate of total complaint volumes.

## 2. The leakage problem, and how it's handled

Every decision has the same skeleton: The complaint, What happened, What I've decided and why, Putting things right, My final decision.

A model trained on the whole text scores brilliantly and is useless, because the text states the answer. The subtle version is "What happened". It reads like background, yet it nearly always ends with what the investigator concluded, and ombudsmen agree with investigators most of the time. A model fed that section learns to spot the investigator's view.

So the model only sees "The complaint". Any sentence in it that mentions the investigator, the ombudsman, upholding, redress or a final decision is removed, and enrichment stops with an error if any model text still contains that language. Decision length is excluded too, because upheld decisions carry an extra "Putting things right" section and run longer.

## 3. Themes and Consumer Duty

Complaints are tagged with transparent rules in `taxonomy/themes.yaml`. Each theme maps to one Consumer Duty outcome with a written rationale. The mapping is analyst judgement rather than FCA guidance, and it's kept in a reviewable file for exactly that reason.

Rules were chosen over a pure topic model because a compliance reviewer needs to see why a case landed in a theme. To stop the rules going stale, NMF topic modelling runs on untagged complaints each month and writes recurring language to `reports/theme_discovery.md` for review. Firm names, titles and words like "complains" are excluded from discovery, otherwise the clusters are just lists of bank names.

## 4. Early-warning statistics

### FCA returns: robust z-score

For each firm and product group, the latest half-year's complaints opened is compared with the firm's own history using the median and the median absolute deviation, scaled by 1.4826. A mean and standard deviation would let one past spike inflate the spread and hide the next one. A firm needs four half-years of history before it's judged, and at least 100 complaints opened in the latest half-year: a move from 3 to 66 complaints gives a large z-score but isn't a conduct signal.

Volume alone punishes big firms, so the firm's complaints per 1,000 accounts is also ranked against peers in the same product group. Red means a z-score of at least 3, or at least 2 while in the top decile of peers. Amber means a z-score of at least 2, or top decile alone.

### Ombudsman decisions: Poisson test with false discovery control

Decision counts per firm, theme and month are small, so a normal approximation is wrong. Each pair's expected monthly count is its trailing 12-month average, counting empty months as zero, and the test asks how likely this month's count would be if nothing had changed. A shift in uphold rate uses a one-sided binomial test against the pair's own smoothed baseline.

Hundreds of firm-theme pairs are tested every month. At a 5% threshold, that guarantees false alarms. Benjamini-Hochberg adjustment controls the expected share of false alarms among the flags, so red means an adjusted value of 1% or less and amber 10% or less. Pairs need six months of history and at least five decisions this month.

## 5. Triage model

### Design

| Choice | Why |
|---|---|
| Time-based split: train, then 3 months validation, then 6 months test | Real use means predicting next month from the past. A random split leaks future patterns. |
| With under 12 months of decisions, split by decision date instead (oldest 60% train, next 15% validation, newest 25% test) | Keeps the split strictly time-ordered on a short crawl. Cuts fall between days, so no day straddles two sets. The model card flags the result as a first read. |
| Titles (Mr, Mrs, Miss) and firm-name words removed from the text features | Titles are a proxy for sex, a protected characteristic. Firm names duplicate the firm-rate feature and crowd the explanation. |
| Firm uphold rate encoded out-of-fold with smoothing | Each training row never sees its own label, and firms with few decisions shrink toward the average. |
| Threshold chosen on validation, never on test | Test numbers stay honest. |
| Two baselines reported every time | The base rate and a firm, sector and theme model show whether the text adds anything. |
| Logistic regression alongside XGBoost | Coefficients give a readable explanation. XGBoost is kept only if it beats it on PR-AUC. |

### Metrics

Upheld cases are the minority, so accuracy misleads and ROC-AUC flatters. PR-AUC is the headline metric. The business metric is precision in the top 10%: if the team can only review a tenth of cases, how many of those would the ombudsman have upheld, compared with the base rate? Brier score checks the probabilities are calibrated enough to read as likelihoods.

### Appropriate use

The score orders a review queue. It must never decide an individual customer's outcome, and the firm-rate feature means it partly reflects a firm's history rather than the merits of one case. Retrain whenever the ombudsman's approach shifts, for example after the October 2024 APP scam reimbursement rules.

## 6. Data quality controls

Each run writes `reports/data_quality.json` and a readable `reports/data_quality.md`. For the FCA returns they cover half-years parsed, firms per half-year, duplicates resolved and impossible percentages blanked. For ombudsman decisions they cover duplicate references, missing dates and firms, empty model text, the share of decisions with full text, theme coverage and the leakage rate. For PDF decisions the outcome is also read independently from "My final decision" and compared with the listing, which catches parsing errors.

dbt adds uniqueness, not-null, accepted-value and referential tests, plus custom tests for unique grain, percentages between 0 and 100, no future-dated decisions and upheld counts never above decisions. Source freshness, run straight after `dbt build`, warns after 45 days without new decisions and fails after 90. A failing test stops the pipeline before marts are exported, so the dashboard never shows unchecked data.

Firm names differ between sources, so a normaliser strips legal suffixes and punctuation. Anything it misses goes in `dbt/seeds/firm_name_overrides.csv`, which is version-controlled and reviewed like code.

## 7. Known limitations

Published decisions over-represent disputed cases and lag the complaint by months. FCA returns exclude smaller firms and don't align on dates. Theme rules will misclassify some complaints, and the Consumer Duty mapping is a judgement. The early-warning thresholds are sensible defaults rather than calibrated against confirmed conduct failures, because no public label of "confirmed failure" exists. Treat flags as prompts for a human to look, not as findings.
