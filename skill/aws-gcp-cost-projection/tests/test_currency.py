#!/usr/bin/env python3
"""
test_currency.py — Industry-grade multi-currency test suite (Stage 2 S2-2).
Covers:
  1. USD identity
  2. EUR, GBP, INR, JPY (large magnitude, catches 1.0 default bug)
  3. Bill-stated rate override
  4. Fallback table and staleness policy
  5. Unknown currency hard-fail closed
  6. Mixed-currency detection and rejection
  7. Dual-side round-trip comparison in bill currency
"""

import duckdb
import pytest
import currency_utils
from currency_utils import (
    resolve_fx,
    detect_currency_and_rate,
    load_fallback_fx,
    FxResult,
)


def test_resolve_fx_usd_identity():
    res = resolve_fx("USD")
    assert isinstance(res, FxResult)
    assert res.currency == "USD"
    assert res.symbol == "$"
    assert res.rate == 1.0
    assert res.source == "base"
    assert not res.is_stale
    assert res.aws_cost_currency == "USD"
    assert res.aws_fx_rate == 1.0


def test_resolve_fx_bill_stated():
    res = resolve_fx("EUR", stated_rate=0.9234)
    assert res.currency == "EUR"
    assert res.symbol == "€"
    assert res.rate == 0.9234
    assert res.source == "bill_stated"
    assert not res.is_stale
    assert "stated on bill" in res.label


def test_resolve_fx_live_rates(monkeypatch):
    mock_rates = {
        "EUR": 0.8950,
        "GBP": 0.7520,
        "INR": 95.1200,
        "JPY": 155.80,
    }
    monkeypatch.setattr(currency_utils, "fetch_live_fx_rates", lambda timeout_sec=5.0: mock_rates)

    # EUR
    r_eur = resolve_fx("EUR")
    assert r_eur.rate == 0.8950
    assert r_eur.source == "live"
    assert not r_eur.is_stale

    # GBP
    r_gbp = resolve_fx("GBP")
    assert r_gbp.rate == 0.7520
    assert r_gbp.source == "live"

    # INR
    r_inr = resolve_fx("INR")
    assert r_inr.rate == 95.1200
    assert r_inr.source == "live"

    # JPY (large magnitude)
    r_jpy = resolve_fx("JPY")
    assert r_jpy.rate == 155.80
    assert r_jpy.source == "live"
    assert r_jpy.symbol == "¥"


def test_resolve_fx_fallback_and_staleness(monkeypatch):
    monkeypatch.setattr(currency_utils, "fetch_live_fx_rates", lambda timeout_sec=5.0: None)
    fallback = load_fallback_fx()
    jpy_rate = fallback["rates"]["JPY"]

    # When age is within max_staleness_days
    r_fresh = resolve_fx("JPY", max_staleness_days=10000)
    assert r_fresh.rate == jpy_rate
    assert r_fresh.source == "fallback_table"
    assert not r_fresh.is_stale

    # When age exceeds max_staleness_days
    r_stale = resolve_fx("JPY", max_staleness_days=0)
    assert r_stale.rate == jpy_rate
    assert r_stale.source == "fallback_table"
    assert r_stale.is_stale
    assert "[fx-stale]" in r_stale.label


def test_resolve_fx_unknown_currency_hard_fails(monkeypatch):
    monkeypatch.setattr(currency_utils, "fetch_live_fx_rates", lambda timeout_sec=5.0: None)

    # Unknown currency must raise ValueError (never default to 1.0)
    with pytest.raises(ValueError, match="unsupported bill currency XYZ"):
        resolve_fx("XYZ")

    with pytest.raises(ValueError, match="unsupported bill currency FOOBAR"):
        detect_currency_and_rate(column_values=["FOOBAR"])


def test_mixed_currency_detected_and_rejected(tmp_path, monkeypatch):
    from ingest import _detect_foreign_currency
    monkeypatch.chdir(tmp_path)

    conn = duckdb.connect(":memory:")
    conn.execute("""
        CREATE TABLE aws_raw (
            "line_item_currency_code" VARCHAR,
            "description" VARCHAR
        );
        INSERT INTO aws_raw VALUES
        ('USD', 'EC2 instance running Linux'),
        ('EUR', 'CloudWatch Logs');
    """)

    # Attempting to detect foreign currency on a mixed USD+EUR bill must fail direct
    with pytest.raises(SystemExit):
        _detect_foreign_currency(["line_item_currency_code", "description"], conn)

    conn.close()


def test_jpy_round_trip_headline_magnitude(monkeypatch):
    """Verify high-magnitude currencies (JPY ~155) don't experience 1.0-default inflation."""
    monkeypatch.setattr(currency_utils, "fetch_live_fx_rates", lambda timeout_sec=5.0: {"JPY": 150.0})

    info = detect_currency_and_rate(column_values=["JPY"])
    assert info["currency"] == "JPY"
    assert info["fx_rate"] == 150.0
    assert info["symbol"] == "¥"

    # Suppose AWS bill is 150,000 JPY (= $1,000 USD)
    # GCP projected is $900 USD
    aws_jpy = 150_000.0
    gcp_usd = 900.0
    gcp_jpy = gcp_usd * info["fx_rate"]  # 135,000 JPY

    assert gcp_jpy == 135_000.0
    # Expected savings: 15,000 JPY (10% savings)
    savings_jpy = aws_jpy - gcp_jpy
    assert savings_jpy == 15_000.0
    ratio = gcp_jpy / aws_jpy
    assert pytest.approx(ratio, 0.001) == 0.90

