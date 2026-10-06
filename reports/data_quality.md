# Data quality report

Generated 6 October 2026 from the data in this run.

## FCA firm-level complaints returns

14 half-years parsed (2019H1 to 2025H2), 40,722 rows in long format.

| Check | Result | Status |
|---|---|---|
| Half-years parsed | 14 of 14 | Pass |
| Firms with 'opened' data per half-year | 212 to 234 | Pass |
| Duplicate firm-product-metric rows (firm changed reporting dates) | 14 resolved, latest return kept | Pass |
| Percentages outside 0-100 (source errors) | 3 blanked | Pass |

| Half-year | Firms | Firms with opened data | Complaints opened |
|---|---|---|---|
| 2019H1 | 293 | 222 | 4,191,329 |
| 2019H2 | 292 | 222 | 5,916,736 |
| 2020H1 | 280 | 228 | 2,859,339 |
| 2020H2 | 283 | 229 | 2,090,861 |
| 2021H1 | 289 | 234 | 1,925,580 |
| 2021H2 | 269 | 214 | 1,740,653 |
| 2022H1 | 274 | 213 | 1,778,323 |
| 2022H2 | 267 | 212 | 1,671,155 |
| 2023H1 | 289 | 232 | 1,781,451 |
| 2023H2 | 291 | 233 | 1,772,741 |
| 2024H1 | 289 | 229 | 1,774,139 |
| 2024H2 | 290 | 220 | 1,692,066 |
| 2025H1 | 288 | 218 | 1,740,222 |
| 2025H2 | 293 | 219 | 1,652,438 |

Blanked percentages: 2022H1 pct closed within 3d (1); 2022H2 pct upheld (1); 2024H2 consumer credit (1).

## Financial Ombudsman Service decisions

7,857 published decisions dated 2026-06-01 to 2026-09-15, from the listing pages (full decision PDFs: 0.0% of rows).

| Check | Result | Status |
|---|---|---|
| Duplicate decision references | 0 | Pass |
| Missing decision date | 0 | Pass |
| Missing firm | 0 | Pass |
| Outcome not 'Upheld' or 'Not upheld' | 0 | Pass |
| Summary without a 'The complaint' heading | 16 | Pass |
| Model text under 10 characters | 7 | Pass |
| Raw complaint text containing outcome language | 0.8% | Information |
| Model text containing outcome language (leakage guard) | 0.0% | Pass |
| Decisions tagged to a theme | 57.8% | Check |
| Decisions against a firm also in the FCA data | 79.5% | Information |
| PDF outcome agrees with listing | not run (no PDFs fetched) | Information |

Overall uphold rate: 23.6%.

| Month | Decisions |
|---|---|
| 2026-06 | 3,452 |
| 2026-07 | 2,864 |
| 2026-08 | 1,540 |
| 2026-09 | 1 |

| Sector | Decisions |
|---|---|
| Banking and Payments | 3,443 |
| Consumer Credit | 1,912 |
| Insurance | 1,831 |
| Investments | 260 |
| Mortgages | 230 |
| Pensions and Annuities | 167 |
| Funeral Planning Services | 9 |
| Claims Management | 5 |

| Primary theme | Decisions |
|---|---|
| other | 3,319 |
| app_scam | 1,108 |
| motor_finance | 591 |
| affordability | 537 |
| claim_declined | 503 |
| card_disputes | 447 |
| financial_difficulty | 280 |
| account_restrictions | 246 |
| premiums_charges | 243 |
| service_delay | 233 |
| advice_suitability | 161 |
| communication | 105 |
| credit_reporting | 84 |
