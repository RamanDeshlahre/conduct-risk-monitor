"""Parser tests: firm names, FCA workbooks and HTML, FOS listings and decision sections."""
import io
from pathlib import Path

import pandas as pd
from openpyxl import Workbook

from crm.config import load_config
from crm.coverage import complete_months
from crm.ingest_fca import (classify_sheet, parse_sortable_html, parse_workbook, period_start,
                            reporting_period_end, resolve_duplicates)
from crm.ingest_fos import parse_listing, search_url
from crm.names import firm_key
from crm.sections import (complaint_from_snippet, leakage_rate, outcome_from_final_decision, redress_amounts,
                          split_sections, strip_leaky_sentences)

FIX = Path(__file__).parent / "fixtures"


def test_firm_key_reconciles_fca_and_fos_spellings():
    assert firm_key("NATIONAL WESTMINSTER BANK PUBLIC LIMITED COMPANY") == firm_key("National Westminster Bank Public Limited Company")
    assert firm_key("Barclays Bank UK PLC") == "barclays_bank_uk"
    assert firm_key("Bank of Ireland (UK) Plc") == "bank_of_ireland_uk"
    assert firm_key("Scottish Widows Limited trading as Halifax Financial Services") == "scottish_widows"
    assert firm_key("Royal London Mutual Insurance Society, Limited (THE)") == "royal_london_mutual_insurance_society"
    assert firm_key(None) == ""


def test_sheet_classification_handles_overlapping_names():
    assert classify_sheet("Closed after 3 days but within") == "pct_closed_3d_to_8w"
    assert classify_sheet("Closed within 3 days") == "pct_closed_within_3d"
    assert classify_sheet("Closed") == "closed"
    assert classify_sheet("Opened") == "opened"
    assert classify_sheet("Upheld") == "pct_upheld"
    assert classify_sheet("Context A (Provision)") == "context_provision"
    assert classify_sheet("Context B (Intermediation)") == "context_intermediation"
    assert classify_sheet("Main return Joint reporters") == "skip"
    assert classify_sheet("Notes") == "skip"
    # Real sheet names from 2019-2025 files
    assert classify_sheet("% >3 days & <8 ") == "pct_closed_3d_to_8w"
    assert classify_sheet("closed >3 days and less than 8 ") == "pct_closed_3d_to_8w"
    assert classify_sheet("Percentage after 3 days, within") == "pct_closed_3d_to_8w"
    assert classify_sheet("(1.5) Closed 3 Days") == "pct_closed_within_3d"
    assert classify_sheet("Trading Names") == "skip"


def _workbook_bytes() -> bytes:
    wb = Workbook()
    header = ["\ufeffFirm Name", "Group", "Joint Reporting", "Reporting period",
              "Banking and credit cards", "Home finance", "Grand Total"]
    sheets = {
        "Opened": [["Barclays Bank UK PLC", "BARCLAYS GROUP", "no", "2025-07-01 to 2025-12-31", "80,470", "5,356", "85,996"],
                   ["HSBC UK Bank Plc", "HSBC GROUP", "no", "2025-07-01 to 2025-12-31", "75,064", "5,047", "81,503"]],
        "Upheld": [["Barclays Bank UK PLC", "BARCLAYS GROUP", "no", "2025-07-01 to 2025-12-31", 0.41, "-", None]],
        "Notes": [["This sheet should be skipped", None, None, None, None, None, None]],
    }
    first = True
    for name, rows in sheets.items():
        ws = wb.active if first else wb.create_sheet()
        first = False
        ws.title = name
        ws.append(["Firm-level complaints data 2025 H2"])  # title rows above the real header
        ws.append([])
        ws.append(header)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_fca_workbook_parses_to_tidy_long_format():
    df = parse_workbook(_workbook_bytes(), "2025H2")
    opened = df[df.metric == "opened"].set_index(["firm_key", "product_group"])["value"]
    assert opened[("barclays_bank_uk", "banking_and_credit_cards")] == 80470
    assert opened[("hsbc_uk_bank", "home_finance")] == 5047
    assert "grand_total" not in set(df.product_group)
    upheld = df[df.metric == "pct_upheld"]
    assert len(upheld) == 1 and abs(upheld.value.iloc[0] - 41.0) < 1e-9  # fraction converted to a percentage
    assert set(df.metric) == {"opened", "pct_upheld"}
    assert period_start("2025H2") == pd.Timestamp("2025-07-01")


def test_fca_sortable_html_maps_tables_in_tab_order():
    def table(val):
        return (f"<table><tr><th>\ufeffFirm Name</th><th>Group</th><th>Joint Reporting</th><th>Reporting period</th>"
                f"<th>Investments</th></tr><tr><td>AJ Bell Securities Limited</td><td>AJ BELL GROUP</td><td>no</td>"
                f"<td>2025-04-01 to 2025-09-30</td><td>{val}</td></tr></table>")
    html = "<html><body>" + "".join(table(v) for v in ["2.09", "0.5", "1,140", "1,100", "60", "35", "40", "-"]) + "</body></html>"
    df = parse_sortable_html(html, "2025H2")
    got = df.set_index("metric")["value"]
    assert got["opened"] == 1140 and got["context_provision"] == 2.09 and got["pct_upheld"] == 40
    assert "consumer_credit" not in got.index  # the dash became a blank and was dropped


def test_fos_listing_parser_reads_every_field():
    rows = parse_listing((FIX / "fos_listing_page.html").read_text(), "https://www.financial-ombudsman.org.uk")
    assert [r["drn"] for r in rows] == ["DRN-1000001", "DRN-1000002", "DRN-1000003"]
    first, second, third = rows
    assert first["firm_name"] == "Example Bank UK PLC" and first["outcome"] == "Upheld"
    assert first["sector"] == "Banking and Payments" and first["pages"] == 5
    assert first["decision_date"] == pd.Timestamp("2026-08-18")
    assert second["outcome"] == "Not upheld" and second["sector"] == "Insurance"
    assert first["pdf_url"] == "https://www.financial-ombudsman.org.uk/decision/DRN-1000001.pdf"  # relative link
    assert third["pdf_url"] == "https://www.financial-ombudsman.org.uk/decision/DRN-1000003.pdf"
    assert third["firm_name"] == "Illustrative Motor Finance Ltd" and third["pages"] == 4  # text fallback
    assert complaint_from_snippet(first["snippet"]).startswith("Mr A says Example Bank UK PLC refused")
    assert "What happened" not in complaint_from_snippet(first["snippet"])


def test_decision_sections_outcome_and_redress():
    sec = split_sections((FIX / "fos_decision.txt").read_text())
    assert sec["complaint"].startswith("Mr A says")
    assert "investigator" in sec["what_happened"]
    assert "DRN-" not in sec["complaint"] and "-1-" not in sec["what_happened"]
    assert outcome_from_final_decision(sec["final_decision"]) == "Upheld"
    assert outcome_from_final_decision("My final decision is that I don't uphold this complaint.") == "Not upheld"
    assert redress_amounts(sec["putting_right"]) == [12450.50, 100.0]


def test_leakage_stripping_removes_outcome_language():
    text = "Mr A says the bank refused a refund. Our investigator upheld it. He wants his money back."
    cleaned = strip_leaky_sentences(text)
    assert "investigator" not in cleaned and "refused a refund" in cleaned
    assert leakage_rate([cleaned]) == 0.0
    assert leakage_rate([text]) == 1.0


def test_fca_2019_layout_with_duplicate_firm_column_and_units():
    """2019 files repeat 'Firm Name', use 'Firm Group'/'Joint Report', carry a Website column, and put
    units in the context headers. All of these broke the original parser."""
    wb = Workbook()
    ws = wb.active
    ws.title = "(1.1) Context (Provision)"
    ws.append([None, None, "Complaints data - Context (provision)"])
    ws.append([None, None, "Firm Name", "Firm Name", "Firm Group", "Website", "Joint Report", "Reporting Period",
               "Banking and credit cards (per 1,000 accounts)", "Home finance (per 1,000 balances outstanding)"])
    ws.append([None, None, "Accord Mortgages Limited", "Accord Mortgages Limited", "ACCORD", "https://x", "No",
               "01 January 2019 to 30 June 2019", 1.35, 5.16])
    ws2 = wb.create_sheet("(1.3) Opened")
    ws2.append([None, None, "Firm Name", "Firm Name", "Firm Group", "Website", "Joint Report", "Reporting Period",
                "Banking and credit cards", "Home finance", "Grand Total"])
    ws2.append([None, None, "Accord Mortgages Limited", "Accord Mortgages Limited", "ACCORD", "https://x", "No",
                "01 January 2019 to 30 June 2019", 9, 672, 681])
    buf = io.BytesIO()
    wb.save(buf)
    df = parse_workbook(buf.getvalue(), "2019H1")
    assert set(df.product_group) == {"banking_and_credit_cards", "home_finance"}  # units stripped, so metrics align
    assert df.group_name.unique().tolist() == ["ACCORD"]
    ctx = df[df.metric == "context_provision"].set_index("product_group")["value"]
    assert ctx["home_finance"] == 5.16


def test_fca_duplicate_returns_keep_the_latest_reporting_period():
    assert reporting_period_end("01 January 2019 to 30 June 2019") == pd.Timestamp("2019-06-30")
    assert reporting_period_end("2023-04-01 to 2023-09-30") == pd.Timestamp("2023-09-30")
    assert reporting_period_end("01-07-2020 to 31-12-2020") == pd.Timestamp("2020-12-31")
    df = pd.DataFrame({"period": ["2023H2"] * 2, "firm_key": ["k"] * 2, "product_group": ["home_finance"] * 2,
                       "metric": ["opened"] * 2, "value": [1101.0, 1056.0],
                       "reporting_period": ["2023-07-01 to 2023-12-31", "2023-04-01 to 2023-09-30"]})
    out, n = resolve_duplicates(df)
    assert n == 1 and out["value"].tolist() == [1101.0]


def test_fos_search_url_filters_and_sorts_by_date():
    url = search_url(load_config(), 20)
    assert "DateFrom=" in url and "Sort=date" in url and url.endswith("Start=20")


def test_partial_months_are_not_treated_as_complete():
    cov = pd.DataFrame({
        "decision_month": pd.to_datetime(["2026-06-01", "2026-07-01", "2026-08-01", "2026-09-01"]),
        "decisions": [3452, 2864, 1540, 1],
        "first_decision_date": pd.to_datetime(["2026-06-01", "2026-07-01", "2026-08-01", "2026-09-15"]),
        "last_decision_date": pd.to_datetime(["2026-06-30", "2026-07-31", "2026-08-18", "2026-09-15"]),
    })
    assert complete_months(cov)["is_complete"].tolist() == [True, True, False, False]
