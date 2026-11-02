#!/usr/bin/env python3
"""
currency_utils.py — Multi-currency detection, FX rate resolution, and formatting.

H-6: Detect bill currency (EUR, GBP, etc.) and stated FX rate if present.
GCP list prices are USD-only; convert GCP costs into the bill's currency using
the resolved FX rate so both sides compare in the customer's native currency.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

try:
    from diagnostics import diag, WARN
except (ImportError, ModuleNotFoundError):
    def diag(*a, **k): pass
    WARN = "warn"

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
_FX_FILE = os.path.join(_DATA_DIR, "fx_rates.json")
_FX_MAX_AGE_DAYS = 7  # FX rates drift much faster than catalogs; flag as stale after 7 days


@dataclass
class FxResult:
    """Explicit, typed FX rate resolution result (Directive S2-2)."""
    currency: str
    symbol: str
    rate: float
    source: str  # "base", "bill_stated", "live", "fallback_table"
    as_of: Optional[str] = None
    is_stale: bool = False
    age_days: Optional[int] = None
    label: str = ""
    aws_cost_currency: str = "USD"
    aws_fx_rate: float = 1.0

    def to_dict(self) -> Dict:
        return {
            "currency": self.currency,
            "symbol": self.symbol,
            "rate": self.rate,
            "fx_rate": self.rate,
            "source": self.source,
            "as_of": self.as_of,
            "updated": self.as_of,
            "is_stale": self.is_stale,
            "age_days": self.age_days,
            "label": self.label,
            "aws_cost_currency": self.aws_cost_currency,
            "aws_fx_rate": self.aws_fx_rate,
        }


def get_max_staleness_days() -> int:
    """Load fx_max_staleness_days from assumption-defaults.json, falling back to 7."""
    try:
        defaults_path = os.path.join(_DATA_DIR, "assumption-defaults.json")
        if os.path.exists(defaults_path):
            with open(defaults_path, "r", encoding="utf-8") as f:
                val = json.load(f).get("values", {}).get("fx_max_staleness_days")
                if val is not None:
                    return int(val)
    except Exception:
        pass
    return _FX_MAX_AGE_DAYS


def get_fx_file_age_days(updated_str: Optional[str]) -> Optional[int]:
    """Calculate age in days of an updated date string (YYYY-MM-DD)."""
    if not updated_str:
        return None
    try:
        dt = datetime.strptime(updated_str.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        age = int((datetime.now(timezone.utc) - dt).total_seconds() / 86400)
        return max(0, age)
    except Exception:
        return None


# Currency symbols map
_DEFAULT_SYMBOLS = {
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "CAD": "CA$",
    "AUD": "A$",
    "JPY": "¥",
    "INR": "₹",
    "CHF": "CHF ",
    "SEK": "kr ",
    "NZD": "NZ$",
    "SGD": "S$",
    "BRL": "R$",
    "CNY": "¥",
    "HKD": "HK$",
    "KRW": "₩",
    "MXN": "Mex$",
    "NOK": "kr ",
    "ZAR": "R ",
}

_SYMBOL_TO_CURRENCY = {
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "₹": "INR",
    "₩": "KRW",
}


def load_fallback_fx() -> Dict:
    """Load fallback FX table from data/fx_rates.json."""
    if os.path.exists(_FX_FILE):
        try:
            with open(_FX_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "base": "USD",
        "updated": "2026-10-23",
        "rates": {"USD": 1.0, "EUR": 0.8915, "GBP": 0.7467, "JPY": 156.92, "CNY": 6.6882, "CAD": 1.4014, "AUD": 1.4253, "INR": 94.9013},
        "symbols": _DEFAULT_SYMBOLS,
    }


def fetch_live_fx_rates(timeout_sec: float = 5.0) -> Optional[Dict[str, float]]:
    """Attempt a live fetch for USD exchange rates. Fails gracefully if offline."""
    url = "https://open.er-api.com/v6/latest/USD"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "cur-web-fx/1.0"})
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("result") == "success" and "rates" in data:
                return data["rates"]
    except Exception:
        pass
    return None


def resolve_fx(
    bill_ccy: str,
    as_of_date: Optional[str] = None,
    stated_rate: Optional[float] = None,
    max_staleness_days: Optional[int] = None,
) -> FxResult:
    """
    Explicit, typed FX resolution (Directive S2-2).
    Returns FxResult{currency, symbol, rate, source, as_of, is_stale, age_days, label, ...}.
    Raises ValueError on unsupported bill currency (hard fail-closed).
    """
    if max_staleness_days is None:
        max_staleness_days = get_max_staleness_days()

    fallback_data = load_fallback_fx()
    rates_map = fallback_data.get("rates", {})
    symbols_map = fallback_data.get("symbols", _DEFAULT_SYMBOLS)

    norm_ccy = (bill_ccy or "").strip().upper()
    if norm_ccy in ("RMB", "CNY"):
        norm_ccy = "CNY"
    elif norm_ccy in ("US DOLLAR", "US$", "USD"):
        norm_ccy = "USD"

    symbol = symbols_map.get(norm_ccy, f"{norm_ccy} ")

    # 1. Base USD
    if norm_ccy == "USD":
        today_str = as_of_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return FxResult(
            currency="USD",
            symbol="$",
            rate=1.0,
            source="base",
            as_of=today_str,
            is_stale=False,
            age_days=0,
            label="USD",
            aws_cost_currency="USD",
            aws_fx_rate=1.0,
        )

    # 2. Bill-stated rate
    if stated_rate is not None and stated_rate > 0:
        rate = float(stated_rate)
        label = f"1 USD = {rate:.4f} {norm_ccy} (stated on bill)"
        return FxResult(
            currency=norm_ccy,
            symbol=symbol,
            rate=rate,
            source="bill_stated",
            as_of=as_of_date,
            is_stale=False,
            age_days=0,
            label=label,
            aws_cost_currency=norm_ccy,
            aws_fx_rate=1.0,
        )

    # 3. Live rate lookup
    live_rates = None
    try:
        live_rates = fetch_live_fx_rates()
    except Exception:
        live_rates = None

    if live_rates and norm_ccy in live_rates:
        rate = float(live_rates[norm_ccy])
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        label = f"Converted at 1 USD = {rate:.4f} {norm_ccy} (live rate, {today_str})."
        return FxResult(
            currency=norm_ccy,
            symbol=symbol,
            rate=rate,
            source="live",
            as_of=today_str,
            is_stale=False,
            age_days=0,
            label=label,
            aws_cost_currency=norm_ccy,
            aws_fx_rate=1.0,
        )

    # 4. Fallback table
    if norm_ccy in rates_map:
        rate = float(rates_map[norm_ccy])
        updated_date = fallback_data.get("updated")
        age_days = get_fx_file_age_days(updated_date)
        is_stale = (age_days is not None and age_days > max_staleness_days)
        age_str = f", {age_days} days old" if age_days is not None else ""
        label = f"Converted at 1 USD = {rate:.4f} {norm_ccy} (committed rate{age_str} — live lookup unavailable)."
        if is_stale:
            label += " [fx-stale]"

        try:
            diag(WARN, "fx_rate_fallback", "currency_utils.py",
                 f"Live FX lookup failed; using committed rate for {norm_ccy} "
                 f"({rate}) dated {updated_date} ({age_days} days old). All {norm_ccy} "
                 f"figures scale with this rate.")
            if is_stale:
                diag(WARN, "fx_stale", "currency_utils.py",
                     f"Committed FX rate for {norm_ccy} is {age_days} days old, "
                     f"exceeding max staleness policy of {max_staleness_days} days.")
        except Exception:
            pass

        return FxResult(
            currency=norm_ccy,
            symbol=symbol,
            rate=rate,
            source="fallback_table",
            as_of=updated_date,
            is_stale=is_stale,
            age_days=age_days,
            label=label,
            aws_cost_currency=norm_ccy,
            aws_fx_rate=1.0,
        )

    # 5. Unsupported currency — hard-fail closed!
    raise ValueError(f"unsupported bill currency {norm_ccy} — add to data/fx_rates.json or supply a rate")


def detect_currency_and_rate(
    raw_text: str = "",
    column_values: Optional[list] = None,
    stated_rate: Optional[float] = None,
    aws_cost_currency: Optional[str] = None,
) -> Dict:
    """
    Detect bill currency and determine USD->currency exchange rate.
    Returns a dict compatible with existing callers.
    """
    fallback_data = load_fallback_fx()
    rates_map = fallback_data.get("rates", {})
    symbols_map = fallback_data.get("symbols", _DEFAULT_SYMBOLS)

    detected_curr = "USD"
    detected_symbol = "$"
    found_rate = stated_rate

    # 1. Check column values if provided (authoritative signal, e.g. CUR line_item_currency_code)
    col_has_usd = False
    if column_values:
        non_usd_col_codes = []
        for val in column_values:
            if not val:
                continue
            clean_val = str(val).strip().upper()
            if clean_val == "RMB":
                clean_val = "CNY"
            if clean_val in ("USD", "US DOLLAR", "US$"):
                col_has_usd = True
            else:
                non_usd_col_codes.append(clean_val)
        if non_usd_col_codes:
            detected_curr = non_usd_col_codes[0]
            detected_symbol = symbols_map.get(detected_curr, f"{detected_curr} ")
        elif col_has_usd:
            detected_curr = "USD"
            detected_symbol = "$"

    # 2. Check text for currency codes and symbols if not found in columns (or columns said USD)
    if detected_curr == "USD" and raw_text:
        is_cny = bool(re.search(r'\b(CNY|RMB|China|Chinese|Beijing|Ningxia|Yuan)\b', raw_text, re.IGNORECASE))
        has_usd_marker = bool(re.search(r'\$|\bUSD\b', raw_text, re.IGNORECASE))

        # Check symbols: require corroboration so a single stray symbol doesn't flip a USD bill (RV-D3c)
        for sym, code in [("€", "EUR"), ("£", "GBP"), ("¥", "CNY" if is_cny else "JPY"), ("₹", "INR"), ("₩", "KRW")]:
            sym_count = raw_text.count(sym)
            if sym_count >= 2:
                detected_curr = code
                detected_symbol = sym
                break
            elif sym_count == 1:
                # Single symbol: accept only if attached to an amount AND not contradicted by USD/$ mentions
                has_digit = bool(re.search(rf"{re.escape(sym)}\s*\d+|\d+\s*{re.escape(sym)}", raw_text))
                if has_digit and not has_usd_marker:
                    detected_curr = code
                    detected_symbol = sym
                    break

        # Check currency codes with word boundaries
        if detected_curr == "USD":
            for code in ["EUR", "GBP", "JPY", "CNY", "INR", "CAD", "AUD", "CHF", "SEK", "SGD"]:
                matches = re.findall(rf"\b{code}\b", raw_text, re.IGNORECASE)
                if len(matches) >= 2 or (len(matches) == 1 and re.search(rf"\b{code}\s*[\d,]+|[\d,]+\s*{code}|(?:exchange|conversion)\s*rate", raw_text, re.IGNORECASE)):
                    detected_curr = code.upper()
                    detected_symbol = symbols_map.get(detected_curr, f"{detected_curr} ")
                    break

    # 3. Detect stated exchange rate from text if not explicitly given
    if found_rate is None and raw_text:
        m_usd_to_curr = re.search(rf"1\s*USD\s*=\s*([0-9.]+)\s*{detected_curr}", raw_text, re.IGNORECASE)
        m_curr_to_usd = re.search(rf"1\s*{detected_curr}\s*=\s*([0-9.]+)\s*USD", raw_text, re.IGNORECASE)

        if m_usd_to_curr:
            try:
                found_rate = float(m_usd_to_curr.group(1).rstrip('.'))
            except ValueError:
                pass
        elif m_curr_to_usd:
            try:
                val = float(m_curr_to_usd.group(1).rstrip('.'))
                if val > 0:
                    found_rate = round(1.0 / val, 4)
            except ValueError:
                pass
        else:
            m_gen = re.search(r"(?:exchange|conversion)\s*rate(?:\s*\([^)]*\))?[:\s]+([0-9.]+)", raw_text, re.IGNORECASE)
            if m_gen:
                try:
                    val = float(m_gen.group(1).rstrip('.'))
                    if re.search(rf"{detected_curr}\s*(?:to|/)\s*USD", raw_text, re.IGNORECASE):
                        if val > 0:
                            found_rate = round(1.0 / val, 4)
                    elif detected_curr in ("EUR", "GBP") and val > 1.0:
                        found_rate = round(1.0 / val, 4)
                    else:
                        found_rate = val
                except ValueError:
                    pass

    # 4. Resolve FX via typed resolver
    fx_res = resolve_fx(detected_curr, stated_rate=found_rate)
    res_dict = fx_res.to_dict()

    # Determine what currency the ingested AWS cost numbers are in (RV-D3 Item 1)
    if aws_cost_currency is None:
        if col_has_usd and fx_res.currency != "USD":
            aws_cost_currency = "USD"
        else:
            aws_cost_currency = fx_res.currency

    if aws_cost_currency == "USD" and fx_res.currency != "USD":
        aws_fx_rate = fx_res.rate
    else:
        aws_fx_rate = 1.0

    res_dict["aws_cost_currency"] = aws_cost_currency
    res_dict["aws_fx_rate"] = aws_fx_rate
    return res_dict


def save_currency_info(job_dir: str, currency_info: Dict, conn=None):
    """Save currency info to currency.json and DuckDB bill_currency table."""
    audit_dir = os.path.join(job_dir, "projection-audit")
    os.makedirs(audit_dir, exist_ok=True)
    json_path = os.path.join(audit_dir, "currency.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(currency_info, f, indent=2)

    if conn is not None:
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bill_currency (
                    currency TEXT,
                    symbol TEXT,
                    fx_rate DOUBLE,
                    source TEXT,
                    label TEXT,
                    aws_cost_currency TEXT,
                    aws_fx_rate DOUBLE,
                    updated TEXT,
                    age_days INTEGER,
                    is_stale BOOLEAN
                );
            """)
            cols = {r[1].lower() for r in conn.execute("PRAGMA table_info('bill_currency')").fetchall()}
            if "updated" not in cols:
                conn.execute("ALTER TABLE bill_currency ADD COLUMN updated TEXT")
            if "age_days" not in cols:
                conn.execute("ALTER TABLE bill_currency ADD COLUMN age_days INTEGER")
            if "is_stale" not in cols:
                conn.execute("ALTER TABLE bill_currency ADD COLUMN is_stale BOOLEAN")

            conn.execute("DELETE FROM bill_currency;")
            conn.execute("""
                INSERT INTO bill_currency VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, [
                currency_info.get("currency", "USD"),
                currency_info.get("symbol", "$"),
                currency_info.get("fx_rate", 1.0),
                currency_info.get("source", "default"),
                currency_info.get("label", "USD"),
                currency_info.get("aws_cost_currency", currency_info.get("currency", "USD")),
                currency_info.get("aws_fx_rate", 1.0),
                currency_info.get("updated") or currency_info.get("as_of"),
                currency_info.get("age_days"),
                currency_info.get("is_stale", False),
            ])
        except Exception:
            pass


def load_currency_info(job_dir: str, conn=None) -> Dict:
    """Load currency info from currency.json or DuckDB, defaulting to USD."""
    audit_dir = os.path.join(job_dir, "projection-audit")
    json_path = os.path.join(audit_dir, "currency.json")
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "aws_fx_rate" not in data:
                    data["aws_fx_rate"] = data.get("fx_rate", 1.0) if data.get("aws_cost_currency") == "USD" and data.get("currency") != "USD" else 1.0
                return data
        except Exception:
            pass

    if conn is not None:
        try:
            cols = {r[1].lower() for r in conn.execute("PRAGMA table_info('bill_currency')").fetchall()}
            has_vintage = "updated" in cols and "age_days" in cols
            has_aws_fx = "aws_fx_rate" in cols
            has_stale = "is_stale" in cols
            
            if has_vintage and has_aws_fx and has_stale:
                row = conn.execute("SELECT currency, symbol, fx_rate, source, label, aws_cost_currency, aws_fx_rate, updated, age_days, is_stale FROM bill_currency LIMIT 1").fetchone()
                if row:
                    return {
                        "currency": row[0],
                        "symbol": row[1],
                        "fx_rate": float(row[2]),
                        "source": row[3],
                        "label": row[4],
                        "aws_cost_currency": row[5],
                        "aws_fx_rate": float(row[6]),
                        "updated": row[7],
                        "as_of": row[7],
                        "age_days": row[8],
                        "is_stale": bool(row[9]),
                    }
            elif has_vintage and has_aws_fx:
                row = conn.execute("SELECT currency, symbol, fx_rate, source, label, aws_cost_currency, aws_fx_rate, updated, age_days FROM bill_currency LIMIT 1").fetchone()
                if row:
                    return {
                        "currency": row[0],
                        "symbol": row[1],
                        "fx_rate": float(row[2]),
                        "source": row[3],
                        "label": row[4],
                        "aws_cost_currency": row[5],
                        "aws_fx_rate": float(row[6]),
                        "updated": row[7],
                        "as_of": row[7],
                        "age_days": row[8],
                        "is_stale": False,
                    }
            elif has_aws_fx:
                row = conn.execute("SELECT currency, symbol, fx_rate, source, label, aws_cost_currency, aws_fx_rate FROM bill_currency LIMIT 1").fetchone()
                if row:
                    return {
                        "currency": row[0],
                        "symbol": row[1],
                        "fx_rate": float(row[2]),
                        "source": row[3],
                        "label": row[4],
                        "aws_cost_currency": row[5],
                        "aws_fx_rate": float(row[6]),
                    }
            else:
                row = conn.execute("SELECT currency, symbol, fx_rate, source, label FROM bill_currency LIMIT 1").fetchone()
                if row:
                    return {
                        "currency": row[0],
                        "symbol": row[1],
                        "fx_rate": float(row[2]),
                        "source": row[3],
                        "label": row[4],
                        "aws_cost_currency": row[0],
                        "aws_fx_rate": 1.0,
                    }
        except Exception:
            pass

    return {
        "currency": "USD",
        "symbol": "$",
        "fx_rate": 1.0,
        "source": "default",
        "label": "USD",
        "aws_cost_currency": "USD",
        "aws_fx_rate": 1.0,
        "is_stale": False,
    }
