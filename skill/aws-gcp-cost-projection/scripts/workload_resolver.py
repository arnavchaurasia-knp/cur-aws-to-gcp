#!/usr/bin/env python3
"""Single source of truth for workload resolution across all pipeline phases.

Loads data/workload_classification.json and provides:
- is_workload: Python-path check on whether a row is workload
- classify_charge: Maps line_item_type/product/cost/etc. to (charge_category, lit, is_workload)
- sql_classify_charge: Single-source SQL expression counterpart of classify_charge
- sql_nonworkload: SQL boolean expression for non-workload patterns
- is_row_workload: Helper that checks row['is_workload'] or resolves via pattern matching
"""
from __future__ import annotations

import os
import re
import sys
from typing import Any

_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from config_loader import load_data_config as _cfg

_WL_CFG = _cfg("workload_classification") or {}
_WL_TYPES = set(_WL_CFG.get("workload_line_item_types", ["Usage", "DiscountedUsage", "SavingsPlanCoveredUsage"]))
_NONWL_PATTERNS = list(_WL_CFG.get("nonworkload_patterns", []))
_NONWL_WORD_PATTERNS = list(_WL_CFG.get("nonworkload_word_patterns", []))
_NONWL_WORD_RES = [re.compile(r'\b' + wp + r'\b', re.IGNORECASE) for wp in _NONWL_WORD_PATTERNS]


def is_workload(line_item_type: str | None, *text_fields: str | None) -> bool:
    """Python-path verdict for workload consumption."""
    blob = " ".join((t or "") for t in text_fields).lower()
    if any(p in blob for p in _NONWL_PATTERNS):
        return False
    if any(rx.search(blob) for rx in _NONWL_WORD_RES):
        return False
    if line_item_type and line_item_type not in _WL_TYPES:
        return False  # Authoritative enum says non-consumption
    return True


def is_row_workload(row: dict[str, Any]) -> bool:
    """Determine whether an ingested or synthetic row represents a workload item.

    If row has an explicit 'is_workload' bool, int, or string, respects it directly.
    Otherwise, resolves using line_item_type, product, usage_type, operation, category, and cost.
    """
    if "is_workload" in row and row["is_workload"] is not None:
        val = row["is_workload"]
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, float)):
            return bool(val)
        if isinstance(val, str):
            s = val.strip().lower()
            if s in ("true", "t", "1"):
                return True
            if s in ("false", "f", "0"):
                return False

    cost = float(row.get("aws_amortized_cost") or row.get("cost") or 0.0)
    lit = row.get("line_item_type")
    prod = row.get("product") or ""
    ut = row.get("usage_type") or ""
    op = row.get("operation") or ""
    cat = row.get("category") or ""

    _, _, is_wl = classify_charge(
        line_item_type_raw=lit,
        product=prod,
        usage_type=ut,
        operation=op,
        cost=cost,
        category=cat,
    )
    return is_wl


def classify_charge(
    *,
    line_item_type_raw: str | None = None,
    product: str = "",
    usage_type: str = "",
    operation: str = "",
    cost: float = 0.0,
    category: str | None = None,
) -> tuple[str, str, bool]:
    """Return (charge_category, line_item_type, is_workload).

    charge_category follows FOCUS: 'Usage' | 'Purchase' | 'Credit' | 'Tax'.
    One definition, used by every ingest branch and cross-cutting tool.
    When a raw line_item_type is given (authoritative from CUR), trust it.
    """
    cost_val = float(cost or 0.0)
    cat_str = str(category or "")
    prod_str = str(product or "")
    ut_str = str(usage_type or "")
    op_str = str(operation or "")
    blob = f"{prod_str} {ut_str} {op_str} {cat_str}".lower()

    if line_item_type_raw and str(line_item_type_raw).strip():
        lit = str(line_item_type_raw).strip()
    else:
        if cost_val < 0:
            if "covered by" in blob and "savings plan" in blob:
                lit = "SavingsPlanCoveredUsage"
            else:
                lit = "Credit"
        elif "savings plan" in blob and any(w in blob for w in ("recurring", "hourly commitment", "commitment", "upfront")):
            lit = "SavingsPlanRecurringFee"
        elif "savings plans" in cat_str.lower() or "savings plan" in cat_str.lower():
            lit = "SavingsPlanRecurringFee"
        elif "reserved instance fee" in blob or "rifee" in blob:
            lit = "RIFee"
        elif "reserved instance applied" in blob or "reserved instances" in blob or "reserved instance" in blob or "discountedusage" in blob:
            lit = "DiscountedUsage"
        elif prod_str.strip().lower() == "tax" or ut_str.strip().lower() == "tax" or op_str.strip().lower() == "tax":
            lit = "Tax"
        else:
            lit = "Usage"

    if lit in ("SavingsPlanRecurringFee", "RIFee", "Fee", "Purchase"):
        charge_category = "Purchase"
    elif cost_val < 0 or lit in ("Credit", "Refund"):
        charge_category = "Credit"
    elif lit == "SavingsPlanCoveredUsage" and cost_val < 0:
        charge_category = "Credit"
    elif lit == "Tax" or "tax" in blob:
        charge_category = "Tax"
    else:
        charge_category = "Usage"

    is_wl = (
        charge_category == "Usage"
        and lit in _WL_TYPES
        and is_workload(lit, prod_str, ut_str, op_str, cat_str)
    )
    return (charge_category, lit, is_wl)


def sql_classify_charge(
    cost_expr: str,
    product_expr: str,
    usage_type_expr: str,
    op_expr: str = "''",
    category_expr: str = "''",
    raw_lit_expr: str | None = None,
) -> tuple[str, str]:
    """Build single-source-of-truth SQL CASE expressions for (line_item_type, is_workload).

    Generates the exact SQL counterpart of classify_charge() to guarantee no divergence
    between Python and SQL branches.
    """
    if raw_lit_expr and raw_lit_expr != "NULL":
        lit_case = f"COALESCE(NULLIF({raw_lit_expr}, ''), 'Usage')"
    else:
        lit_case = f"""(
            CASE
                WHEN {cost_expr} < 0 AND ({op_expr} ILIKE '%covered by%savings plan%' OR {usage_type_expr} ILIKE '%covered by%savings plan%' OR {product_expr} ILIKE '%covered by%savings plan%' OR {category_expr} ILIKE '%covered by%savings plan%')
                    THEN 'SavingsPlanCoveredUsage'
                WHEN {cost_expr} < 0
                    THEN 'Credit'
                WHEN ({category_expr} ILIKE '%savings plan%' OR {product_expr} ILIKE '%savings plan%' OR {usage_type_expr} ILIKE '%savings plan%' OR {op_expr} ILIKE '%savings plan%')
                     AND ({category_expr} ILIKE '%recurring%' OR {category_expr} ILIKE '%hourly commitment%' OR {category_expr} ILIKE '%upfront%'
                          OR {product_expr} ILIKE '%recurring%' OR {product_expr} ILIKE '%hourly commitment%' OR {product_expr} ILIKE '%upfront%'
                          OR {usage_type_expr} ILIKE '%recurring%' OR {usage_type_expr} ILIKE '%hourly commitment%' OR {usage_type_expr} ILIKE '%upfront%'
                          OR {op_expr} ILIKE '%recurring%' OR {op_expr} ILIKE '%hourly commitment%' OR {op_expr} ILIKE '%upfront%')
                    THEN 'SavingsPlanRecurringFee'
                WHEN {category_expr} ILIKE '%savings plans%' OR {category_expr} ILIKE '%savings plan%'
                     OR {product_expr} ILIKE '%savings plans%' OR {product_expr} ILIKE '%savings plan%'
                    THEN 'SavingsPlanRecurringFee'
                WHEN {usage_type_expr} ILIKE '%rifee%' OR {op_expr} ILIKE '%rifee%'
                     OR {usage_type_expr} ILIKE '%reserved instance fee%' OR {op_expr} ILIKE '%reserved instance fee%'
                    THEN 'RIFee'
                WHEN {usage_type_expr} ILIKE '%reserved instance applied%' OR {usage_type_expr} ILIKE '%reserved instances%' OR {usage_type_expr} ILIKE '%reserved instance%' OR {usage_type_expr} ILIKE '%discountedusage%'
                     OR {op_expr} ILIKE '%reserved instance applied%' OR {op_expr} ILIKE '%reserved instances%' OR {op_expr} ILIKE '%reserved instance%' OR {op_expr} ILIKE '%discountedusage%'
                     OR {product_expr} ILIKE '%reserved instance applied%' OR {product_expr} ILIKE '%reserved instances%' OR {product_expr} ILIKE '%reserved instance%' OR {product_expr} ILIKE '%discountedusage%'
                    THEN 'DiscountedUsage'
                WHEN {product_expr} = 'Tax' OR {usage_type_expr} = 'Tax' OR {op_expr} = 'Tax'
                    THEN 'Tax'
                ELSE 'Usage'
            END
        )"""

    non_wl = sql_nonworkload(product_expr, usage_type_expr, op_expr, category_expr)
    is_wl_case = f"""(
        CASE
            WHEN {cost_expr} < 0 THEN FALSE
            WHEN {lit_case} NOT IN ('Usage', 'DiscountedUsage', 'SavingsPlanCoveredUsage') THEN FALSE
            WHEN {non_wl} THEN FALSE
            ELSE TRUE
        END
    )"""
    return lit_case, is_wl_case


def sql_nonworkload(*col_exprs: str) -> str:
    """Build a SQL boolean that is TRUE when the row is NON-workload, from the
    pattern list — so the spreadsheet/flat-CSV SQL CASEs stay in sync with the JSON.
    Substring patterns use ILIKE; short word patterns use word-boundary regexp so
    e.g. 'tax' does not match 'syntax'."""
    ors = []
    for col in col_exprs:
        for p in _NONWL_PATTERNS:
            ors.append(f"{col} ILIKE '%{p}%'")
        for wp in _NONWL_WORD_PATTERNS:
            ors.append(f"regexp_matches({col}, '\\b{wp}\\b', 'i')")
    return "(" + " OR ".join(ors) + ")" if ors else "(FALSE)"
