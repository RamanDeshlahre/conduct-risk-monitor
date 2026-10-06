# Changes from the first real-data run (6 October 2026)

The pipeline was run end to end on live data for the first time: all 14 FCA half-years and the ombudsman decisions dated 1 June to 15 September 2026 (7,857, crawled at one page every 1.5 seconds). These are the bugs that run exposed and how each was fixed. Tests grew from 15 to 20, and each bug below has a regression test where one is practical.

## FCA ingestion (`crm/ingest_fca.py`)

| Bug | Effect | Fix |
|---|---|---|
| 2019H1 repeats the "Firm Name" column | The whole half-year failed to parse ("duplicate labels") | Keep the first of any repeated header |
| "Firm Group", "Joint Report", "Website", "Submission Date", "Reporting Frequency", "Semester" not recognised | They were melted in as if they were product groups | Explicit identifier and drop lists |
| Context headers carry units, e.g. "Banking and credit cards (per 1,000 accounts)" | Context rows never lined up with volumes, so complaints per 1,000 and the peer comparison were always blank | Strip the units before normalising product names |
| 2022 sheets "% >3 days & <8 " and "closed >3 days and less than 8 " | Classified as "closed within 3 days", so two metrics collided and the mart took the max of both | More sheet-name rules, tested against every real name |
| "Trading Names" sheets | Logged as unrecognised every period | Skipped explicitly |
| Consumer credit "upheld" column is a share in some years and a count in others | Counts were treated as percentages | Decide from the values; counts become `complaints_upheld` |
| Fractions detected with the sheet maximum | One bad cell (783% in 2024H2) would stop a whole sheet being rescaled | Use the 95th percentile; blank impossible values and report them |
| A firm changing reporting dates appears twice in a file | The mart silently took the max of two returns | Keep the return with the latest reporting period, and count them |
| Failed periods were logged and skipped | A run could finish with silent gaps in history | The step now fails, naming the periods |

It also writes `reports/fca_data_quality.json`.

## Ombudsman crawl (`crm/ingest_fos.py`)

| Bug | Effect | Fix |
|---|---|---|
| Link selector required `/decision/DRN-`, but live links are relative (`decision/DRN-…`) | Every page parsed as empty, so the crawl stopped on page 0 with no data | Match `DRN-` anywhere; parse the live markup by its classes, with the old text regex as a fallback |
| The listing is ordered by relevance unless told otherwise | The "stop when a page predates min_date" rule was unreliable | Always send `DateFrom` and `Sort=date`, so the crawl is bounded server-side |
| User-Agent advertised the placeholder contact address | Misleading to the site owner | Omitted until a real address is set |

## Enrichment and themes

| Bug | Effect | Fix |
|---|---|---|
| Some ombudsmen head the section "Complaint", not "The complaint" | The heading stayed in the model text, identifying the ombudsman's house style | Strip either form; also stop at a "Background" heading |
| Complaint text was parsed once at crawl time | Parsing fixes never reached decisions already crawled | Re-derive it from the raw summary in `enrich` |
| Theme discovery clusters were lists of firm names | Useless for growing the taxonomy | Firm names and titles excluded from discovery |

`enrich` now also writes a readable `reports/data_quality.md` covering both sources.

## Warehouse and dbt

- Added `fos_month_coverage` (decisions and last decision date per month), with tests.
- Generic test arguments moved under `arguments:` and `tests:` renamed `data_tests:`, clearing dbt 1.10+ deprecation warnings.
- `dbt source freshness` now runs after `dbt build`. Before, the freshness rules in `sources.yml` were never checked.

## Early warning (`crm/early_warning.py`, new `crm/coverage.py`)

| Bug | Effect | Fix |
|---|---|---|
| "Latest month" was the newest month with any decision | With FOS's publication lag that was September 2026 (one decision), so every firm looked like a collapse in volume | Only complete months are tested; partial months are reported as such |
| Too little history gave an empty result with no explanation | Looked like "no firms flagged" | `early_warning_summary.json` and the MI note say why the test was skipped |
| No minimum volume on FCA flags | A rise from 3 to 66 complaints was red | `fca_min_opened: 100`, mirroring `fos_min_count` |
| "per 1,000 accounts" for every product | Wrong unit for insurance, pensions and mortgages | Unit by product group |
| Firms with red and amber signals were counted in both | Inflated the amber count | Each firm counted once, at its worst signal |
| Watchlist sorted alphabetically within each RAG band | "Review first" listed firms starting with A, not the strongest signals | Stable sort keeps each source's severity order |

## Model (`crm/model.py`)

| Bug | Effect | Fix |
|---|---|---|
| Month split needs 12 months | Failed outright on a shorter crawl | Falls back to a strictly time-ordered split by decision date, and the model card says so |
| Titles (Mr, Mrs, Miss) were text features | A proxy for sex, a protected characteristic, was among the top drivers | Removed from the vocabulary |
| Firm names were text features | Duplicated the firm rate and filled the explanation with bank names | Removed from the vocabulary |
| Model card attributed the LR terms to whichever model won | Misleading when XGBoost wins | Labelled as coming from the logistic regression |

## MI note and dashboard

- The note's title month is the latest complete month, and incomplete months are named with how far they're published.
- Theme shifts compare as many complete months as exist (a month, if that's all there is), rather than needing six.
- New FCA section: total complaints opened, change on the previous half-year, and product split.
- Dates in British style ("6 October 2026").
- The dashboard headline explains a skipped ombudsman test, and partly published months are drawn paler on the market chart.

## Configuration

`fos.min_date` is now 2026-06-01, the window used for this run. Set it back to 2023-01-01 for the full monthly ombudsman signal and the month-based model split. New settings: `fos.complete_month_tolerance_days`, `early_warning.fca_min_opened`, `model.min_train_months` and `model.fallback_split`.
