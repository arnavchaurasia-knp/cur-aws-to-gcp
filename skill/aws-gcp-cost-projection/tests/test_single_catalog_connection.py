"""Ratchet test: enforces that catalog.duckdb is opened ONLY by catalog_db.py (and build_catalog_index.py for offline compilation).

Subsystem S2 (WORKORDER_sku_catalog_consolidation.md):
No script may call duckdb.connect() with CATALOG_DB or 'catalog.duckdb' directly.
All catalog reads must route through catalog_db.get_connection() (or sku_catalog re-exports).
"""
import ast
import os
import re

_SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")

# Only catalog_db (SSOT reader) and build_catalog_index (offline index compiler) may open catalog.duckdb
_ALLOWED_CONNECT_FILES = {
    "catalog_db.py",
    "build_catalog_index.py",
}


def test_no_ad_hoc_catalog_connect():
    """Fail if any script outside the allowed set calls duckdb.connect on the catalog."""
    violations = []

    for fname in os.listdir(_SCRIPTS_DIR):
        if not fname.endswith(".py") or fname in _ALLOWED_CONNECT_FILES:
            continue

        fpath = os.path.join(_SCRIPTS_DIR, fname)
        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        # Check for duckdb.connect with CATALOG_DB or catalog.duckdb or cat_file
        # Look for AST call sites to duckdb.connect
        try:
            tree = ast.parse(content, filename=fpath)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                # match duckdb.connect(...)
                is_duckdb_connect = False
                if isinstance(func, ast.Attribute) and func.attr == "connect":
                    if isinstance(func.value, ast.Name) and func.value.id == "duckdb":
                        is_duckdb_connect = True

                if is_duckdb_connect and node.args:
                    first_arg = node.args[0]
                    arg_str = ast.unparse(first_arg).lower()
                    if any(term in arg_str for term in ("catalog", "cat_file", "catalog_db")):
                        violations.append(f"{fname}:{node.lineno}: duckdb.connect({ast.unparse(first_arg)})")

    assert not violations, (
        "Found direct duckdb.connect() to catalog outside catalog_db.py:\n"
        + "\n".join(f"  - {v}" for v in violations)
        + "\nAll catalog reads must use catalog_db.get_connection()."
    )


def test_catalog_db_singleton_connection():
    """Verify that catalog_db.get_connection() returns a functioning singleton."""
    import catalog_db

    if not catalog_db.catalog_exists():
        return

    c1 = catalog_db.get_connection()
    c2 = catalog_db.get_connection()
    assert c1 is not None
    assert c1 is c2, "get_connection() must return the same cached singleton"

    # Liveness verification
    row = c1.execute("SELECT 1").fetchone()
    assert row == (1,)
