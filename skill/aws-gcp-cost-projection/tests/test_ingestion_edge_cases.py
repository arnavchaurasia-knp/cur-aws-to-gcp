import pytest
from scripts.ingest import parse_money, check_faithfulness


def test_ing4_parse_money_handles_accounting_parens():
    assert parse_money("(150.00)") == -150.00
    assert parse_money("($1,234.56)") == -1234.56
    assert parse_money("  (45.50)  ") == -45.50


def test_ing4_parse_money_handles_eu_decimal_comma():
    assert parse_money("1.234,56") == 1234.56
    assert parse_money("1234,56") == 1234.56
    assert parse_money("0,75") == 0.75
    assert parse_money("1,234.56") == 1234.56  # US style preserved


def test_ing4_parse_money_handles_currency_symbols():
    assert parse_money("$150.00") == 150.00
    assert parse_money("€250.75") == 250.75
    assert parse_money("£99.99") == 99.99
    assert parse_money("₹1,500.00") == 1500.00


def test_ing2_check_faithfulness_fails_on_lost_spend():
    # If catalog total is $1000 but reference raw total is $1500 (e.g. 500 lost)
    err = check_faithfulness(cat_rows=10, cat_total=1000.0, reference_total=1500.0)
    assert err is not None
    assert "Cost reconciliation failed" in err
    assert "$1,000.00" in err and "$1,500.00" in err


def test_ing2_check_faithfulness_passes_on_reconciled_total():
    # Catalog total equals reference total within tolerance
    err = check_faithfulness(cat_rows=10, cat_total=1500.00, reference_total=1500.00)
    assert err is None

