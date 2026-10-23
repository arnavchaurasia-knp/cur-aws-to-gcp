"""Reconciliation gate: it must be able to run at all.

Before these fixes, extract_bill_total() opened only input.txt/input.csv and
matched only prose patterns requiring a literal "USD" token. The corpus is
6 csv, 4 pdf and 5 xlsx, and a spreadsheet states its total as a ROW rather
than a sentence — so the gate skipped on 12 of 12 bills and nobody noticed.
"""
import os
import sys

_SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_SKILL, "scripts"))

from validate_fix import (  # noqa: E402
    extract_bill_total,
    _total_from_tabular,
    check_faithfulness,
)


def test_total_from_csv_total_row(tmp_path):
    """A spreadsheet states its total as a row, with no USD token anywhere."""
    (tmp_path / "input.csv").write_text(
        "Service,Region,Custom Usage Type,Description,Usage Quantity,Cost ($)\n"
        "EC2,Mumbai,BoxUsage,m5.large hour,744,62.05\n"
        'Total ($),,,,"56,28,331.98","19,358.78"\n',
        encoding="utf-8",
    )
    assert extract_bill_total(str(tmp_path)) == 19358.78


def test_total_row_uses_cost_column_not_largest_number(tmp_path):
    """The neighbouring cell is a usage quantity, not a second currency.

    Taking max() across the row picks 5,628,331.98 and produces a false
    reconciliation violation on every bill of this shape.
    """
    p = tmp_path / "b.csv"
    p.write_text(
        "Service,Usage Quantity,Cost ($)\n"
        "EC2,744,62.05\n"
        'Total ($),"56,28,331.98","19,358.78"\n',
        encoding="utf-8",
    )
    assert _total_from_tabular(str(p)) == 19358.78


def test_ambiguous_total_returns_none(tmp_path):
    """Two total rows that materially disagree: skip rather than guess.

    A wrong total fails every bill, which is worse than not checking.
    """
    p = tmp_path / "b.csv"
    p.write_text(
        "Service,Cost\nEC2,10.00\nTotal,100.00\nTotal,999.00\n",
        encoding="utf-8",
    )
    assert _total_from_tabular(str(p)) is None


def test_prose_total_still_works(tmp_path):
    """PDF-style invoices state the total in prose; that path must not regress."""
    (tmp_path / "input.txt").write_text(
        "Charges by service\n...\nTotal pre-tax USD 1,234.56\n", encoding="utf-8"
    )
    assert extract_bill_total(str(tmp_path)) == 1234.56


def test_any_input_extension_is_opened(tmp_path):
    """input.tsv is read; the old code only ever opened .txt and .csv."""
    (tmp_path / "input.tsv").write_text(
        "Service,Cost\nEC2,5.00\nTotal,42.00\n", encoding="utf-8"
    )
    assert extract_bill_total(str(tmp_path)) == 42.00


def test_no_total_row_is_not_invented(tmp_path):
    """A pure line-item export states no total; skipping is correct.

    job6/7/8/10 are this shape. The gate must not manufacture a figure.
    """
    (tmp_path / "input.csv").write_text(
        "Category,Meter,Cost\nAWS Lambda,Invoke,0.85\n", encoding="utf-8"
    )
    assert extract_bill_total(str(tmp_path)) is None


def test_all_zero_bill_message_differs_from_unparsed():
    """A free-tier-only bill must not be told its layout was unrecognised."""
    zero = check_faithfulness(0, 0.0, 0.0, rows_before_materiality=5)
    unparsed = check_faithfulness(0, 0.0, 0.0, rows_before_materiality=0)
    assert "every line item" in zero
    assert "layout was not recognized" in unparsed
    assert zero != unparsed
