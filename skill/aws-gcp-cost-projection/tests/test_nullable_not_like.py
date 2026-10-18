#!/usr/bin/env python3
"""tests/test_nullable_not_like.py — Enforce COALESCE on nullable columns in NOT LIKE / != predicates.

DuckDB three-valued logic: NULL NOT LIKE 'pattern' -> NULL (not TRUE), so the row is
silently excluded. Any column that can be NULL must be wrapped in COALESCE when used
in a negative predicate.

Known nullable columns in aws_li_catalog and aws_li_to_gcp_li:
- instance_type, instance_arch, instance_vcpus, instance_ram_gb
- usage_type, operation, pricing_unit
- gcp_sku_name, gcp_sku_id, component
- gcp_service, gcp_region
- product (rarely NULL but possible in edge cases)
"""
import os
import re
import sys
import glob

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")

# Columns known to be nullable (from audit D-1 evidence and schema inspection)
NULLABLE_COLUMNS = {
    # aws_li_catalog
    "c.instance_type", "c.instance_arch", "c.instance_vcpus", "c.instance_ram_gb",
    "c.usage_type", "c.operation", "c.pricing_unit", "c.product",
    # aws_li_to_gcp_li
    "m.gcp_sku_name", "m.gcp_sku_id", "m.component", "m.gcp_service", "m.gcp_region",
    "m.projection_note", "m.rate_source", "m.rate_region_basis", "m.license_status",
    "m.strategy", "m.unit_multiplier",
    # gcp_projection
    "p.product", "p.gcp_service", "p.gcp_sku_id",
    # Generic table aliases that may be used
    "instance_type", "usage_type", "operation", "pricing_unit", "product",
    "gcp_sku_name", "gcp_sku_id", "component", "gcp_service", "gcp_region",
}

# Pattern to match bare NOT LIKE / NOT ILIKE / != against nullable columns
# We look for: column NOT LIKE 'pattern' or column NOT ILIKE 'pattern' or column != 'value'
# where column is in NULLABLE_COLUMNS and NOT wrapped in COALESCE()
# Only NEGATIVE predicates (NOT LIKE, NOT ILIKE, !=) are problematic for NULL handling.
# Positive predicates (LIKE, ILIKE) correctly return FALSE for NULL, which is the desired behavior.
PREDICATE_PATTERN = re.compile(
    r'\b(' + '|'.join(re.escape(c) for c in sorted(NULLABLE_COLUMNS, key=len, reverse=True)) + r')\b\s+(?:NOT\s+(?:I?LIKE)|!=)\s*',
    re.IGNORECASE
)

# COALESCE wrapper pattern - matches COALESCE(column, ...)
COALESCE_PATTERN = re.compile(r'COALESCE\s*\(\s*\b(\w+(?:\.\w+)?)\b\s*,', re.IGNORECASE)

# Files that are allowed to have bare predicates (migrations, one-time scripts, etc.)
ALLOWED_FILES = {
    "backfill_row_state.py",
}

def check_file(filepath):
    """Check a single file for bare NOT LIKE / != on nullable columns."""
    with open(filepath, encoding="utf-8") as f:
        lines = f.readlines()

    filename = os.path.basename(filepath)
    if filename in ALLOWED_FILES:
        return []

    violations = []
    for i, line in enumerate(lines, 1):
        # Skip comments
        stripped = line.strip()
        if stripped.startswith('--') or stripped.startswith('#'):
            continue

        # Find all column.NOT_LIKE patterns
        matches = PREDICATE_PATTERN.finditer(line)
        for match in matches:
            col = match.group(1)
            # Check if this column is wrapped in COALESCE on this line
            # We need to look at the context around the match
            start = max(0, match.start() - 20)
            end = min(len(line), match.end() + 20)
            context = line[start:end]

            # Check if COALESCE wraps this column
            coalesce_match = COALESCE_PATTERN.search(context)
            if coalesce_match and coalesce_match.group(1).lower() == col.lower():
                continue  # OK - wrapped in COALESCE

            # Also check for COALESCE before the column in the line
            if f'COALESCE({col.lower()}' in line.lower() or f'COALESCE({col}' in line:
                continue

            # Check if IS NOT NULL precedes this comparison on the same line
            # Pattern: col IS NOT NULL AND ... col != ...
            is_not_null_pos = line.lower().find(f'{col.lower()} is not null')
            if is_not_null_pos != -1:
                pred_pos = line.lower().find(match.group(0).lower())
                if pred_pos != -1 and is_not_null_pos < pred_pos:
                    continue  # OK - this column guarded by IS NOT NULL

            # Check if IS NOT NULL for this column is on the PREVIOUS line
            if i > 1:
                prev_line = lines[i - 2]
                if f'{col.lower()} is not null' in prev_line.lower():
                    continue  # OK - guarded by IS NOT NULL on previous line

            # Check if the line contains multiple IS NOT NULL checks before the !=
            # (e.g., both sides of a comparison are guarded)
            if line.lower().count('is not null') >= 2:
                is_null_positions = [m.start() for m in re.finditer(r'is not null', line.lower())]
                pred_pos = line.lower().find(match.group(0).lower())
                if all(pos < pred_pos for pos in is_null_positions):
                    continue  # OK - multiple IS NOT NULL guards

            violations.append({
                "file": filepath,
                "line": i,
                "column": col,
                "code": line.strip(),
            })

    return violations


def main():
    all_violations = []
    for script in glob.glob(os.path.join(SCRIPTS_DIR, "*.py")):
        all_violations.extend(check_file(script))

    if all_violations:
        print("FAIL: Found bare NOT LIKE / != on nullable columns:")
        for v in all_violations:
            print(f"  {v['file']}:{v['line']}: {v['column']} -> {v['code'][:120]}")
        print(f"\nTotal: {len(all_violations)} violation(s)")
        print("\nFix: Wrap nullable columns in COALESCE(column, '') or COALESCE(column, default_value)")
        sys.exit(1)

def test_no_bare_not_like():
    all_violations = []
    for script in glob.glob(os.path.join(SCRIPTS_DIR, "*.py")):
        all_violations.extend(check_file(script))
    assert not all_violations, f"Found bare NOT LIKE / != on nullable columns: {all_violations}"


if __name__ == "__main__":
    main()