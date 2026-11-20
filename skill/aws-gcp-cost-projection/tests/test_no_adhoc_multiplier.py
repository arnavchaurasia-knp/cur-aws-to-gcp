"""
test_no_adhoc_multiplier.py — Ratchet test forbidding unverified raw numeric unit_multiplier literals.

Enforces that every `unit_multiplier` assignment in `apply_static_mappings.py`:
1. Is 1.0 or 0.0 (identity / ignore), OR
2. Is sourced from unit_equivalence (GB_TO_GIB, GIBPS_TO_MIBPS, derive_multiplier), OR
3. Is an approved derived variable / specification constant (e.g. instance vcpu/ram, _ACU_*, MIB_PER_METRIC_*), OR
4. Is explicitly documented with a `# non-derivable:` comment explaining why it cannot be derived.

Any new unapproved raw float/int literal causes this test to fail.
"""

import os
import re

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAPPINGS_FILE = os.path.join(_SCRIPTS_DIR, "scripts", "apply_static_mappings.py")

# Approved non-derivable constants or instance-sizing variables
_APPROVED_PATTERNS = {
    "1.0",
    "0.0",
    "1",
    "0",
    "GB_TO_GIB",
    "GIBPS_TO_MIBPS",
    "GB_TO_GIB if sku_id else 1.0",
    "GB_TO_GIB if _lcu_sku_id else 1.0",
    "_ACU_VCPU",
    "_ACU_RAM_GIB",
    "MIB_PER_METRIC_MONTH_AT_1MIN",
    "600.0 if sku_id else 1.0",
    "100.0 if sku_id else 1.0",
}


def test_no_adhoc_multiplier_literals():
    assert os.path.exists(MAPPINGS_FILE), f"Missing {MAPPINGS_FILE}"

    violations = []
    with open(MAPPINGS_FILE, encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            # Match "unit_multiplier": <val>,
            m = re.search(r'["\']unit_multiplier["\']\s*:\s*([^,\n]+)', line)
            if not m:
                continue

            expr = m.group(1).strip()
            comment = ""
            if "#" in expr:
                expr, comment = expr.split("#", 1)
                expr = expr.strip()
                comment = comment.strip()
            elif "#" in line:
                comment = line.split("#", 1)[1].strip()

            if "non-derivable:" in comment.lower():
                continue

            if expr in _APPROVED_PATTERNS:
                continue

            # Allow variable names, function calls, attributes, or typecasts
            # e.g. mult, float(vcpus), ram, dcu, gpu_count, total_ecpus, sku.display_quantity
            if re.match(r'^(?:float\([a-zA-Z0-9_.]+\)|[a-zA-Z_][a-zA-Z0-9_.]*(?:\s+if\s+.*\s+else\s+.*)?)$', expr):
                continue

            # Allow float(real_local_ssd_gib) / _AVG_HOURS_PER_MONTH
            if "_AVG_HOURS_PER_MONTH" in expr:
                continue

            # If it's a raw numeric calculation or float (e.g. 1.0 / 1.074, 120.0, 1024.0) without approval
            if re.search(r'\d+\.\d+|\b\d+\b', expr):
                violations.append(f"Line {line_num}: raw literal 'unit_multiplier: {expr}' without '# non-derivable:' tag")

    assert not violations, "Found ad-hoc unit_multiplier literals:\n" + "\n".join(violations)

