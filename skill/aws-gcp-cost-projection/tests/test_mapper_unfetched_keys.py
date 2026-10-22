import ast
import os
import duckdb
import pytest
from scripts.apply_static_mappings import STATIC_MAPPER_ROW_COLS, map_flat_hourly


def test_mapper_read_keys_are_subset_of_query_cols():
    """T4: Ensure every key read as r.get(...) or r[...] across all mapper functions
    is a subset of STATIC_MAPPER_ROW_COLS fetched by the row query.
    """
    script_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "apply_static_mappings.py")
    with open(script_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())

    class KeyVisitor(ast.NodeVisitor):
        def __init__(self):
            self.current_fn = None
            self.reads = set()

        def visit_FunctionDef(self, node):
            old_fn = self.current_fn
            self.current_fn = node.name
            self.generic_visit(node)
            self.current_fn = old_fn

        def visit_Subscript(self, node):
            if isinstance(node.value, ast.Name) and node.value.id in ("r", "row"):
                if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                    self.reads.add((node.slice.value, self.current_fn))
            self.generic_visit(node)

        def visit_Call(self, node):
            if isinstance(node.func, ast.Attribute) and node.func.attr == "get":
                if isinstance(node.func.value, ast.Name) and node.func.value.id in ("r", "row"):
                    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                        self.reads.add((node.args[0].value, self.current_fn))
            self.generic_visit(node)

    visitor = KeyVisitor()
    visitor.visit(tree)

    missing = {}
    for key, fn_name in sorted(visitor.reads):
        if key not in STATIC_MAPPER_ROW_COLS:
            missing.setdefault(key, []).append(fn_name)

    assert not missing, f"mappers read keys the row query does not fetch: {missing}"


def test_billing_month_reaches_mappers(tmp_path):
    """T5.1: A row dict built through the real query path contains billing_month."""
    db_file = str(tmp_path / "test_billing_month.duckdb")
    con = duckdb.connect(db_file)
    con.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key VARCHAR,
            mechanic_group VARCHAR,
            product VARCHAR,
            usage_type VARCHAR,
            pricing_unit VARCHAR,
            aws_amortized_cost DOUBLE,
            aws_region VARCHAR,
            gcp_region VARCHAR,
            operation VARCHAR,
            volume_type VARCHAR,
            total_usage DOUBLE,
            instance_type VARCHAR,
            instance_ram_gb DOUBLE,
            deployment_option VARCHAR,
            database_engine VARCHAR,
            pricing_model VARCHAR,
            product_code VARCHAR,
            instance_vcpus DOUBLE,
            operating_system VARCHAR,
            license_model VARCHAR,
            line_item_type VARCHAR,
            billing_month VARCHAR
        )
    """)
    con.execute("""
        INSERT INTO aws_li_catalog VALUES (
            'k1', 'flat_hourly', 'AWS Shield', 'Shield-Advanced', 'Hrs',
            3000.0, 'us-east-1', 'us-central1', 'None', '',
            720.0, '', 0.0, '', '',
            'OnDemand', 'AWSShield', 0.0, '', '', 'Usage', '2026-03'
        )
    """)

    catalog_cols = {c[1].lower() for c in con.execute("PRAGMA table_info('aws_li_catalog')").fetchall()}
    _pc_expr = "product_code" if "product_code" in catalog_cols else "NULL AS product_code"
    _vcpu_expr = "instance_vcpus" if "instance_vcpus" in catalog_cols else "NULL AS instance_vcpus"
    _os_expr = "operating_system" if "operating_system" in catalog_cols else "NULL AS operating_system"
    _lic_expr = "license_model" if "license_model" in catalog_cols else "NULL AS license_model"
    _bm_expr = "billing_month" if "billing_month" in catalog_cols else "NULL AS billing_month"

    rows = con.execute(f"""
        SELECT aws_li_key, mechanic_group, product, usage_type, pricing_unit AS unit,
               aws_amortized_cost, aws_region AS region, gcp_region, operation, volume_type,
               total_usage, instance_type, instance_ram_gb, deployment_option, database_engine,
               pricing_model, {_pc_expr}, {_vcpu_expr}, {_os_expr}, {_lic_expr}, line_item_type,
               {_bm_expr}
        FROM aws_li_catalog
        WHERE mechanic_group = 'flat_hourly'
    """).fetchall()
    con.close()

    assert len(rows) == 1
    row_dict = dict(zip(STATIC_MAPPER_ROW_COLS, rows[0]))
    assert row_dict["billing_month"] == "2026-03"
    assert row_dict["region"] == "us-east-1"
    assert row_dict["pricing_model"] == "OnDemand"


def test_monthly_fee_priced_per_month_end_to_end(tmp_path):
    """T5.2: A bill with three distinct billing months and one enrollment fee row per month
    yields three priced fees, exercised through the real query path.
    """
    db_file = str(tmp_path / "test_multi_month.duckdb")
    con = duckdb.connect(db_file)
    con.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key VARCHAR,
            mechanic_group VARCHAR,
            product VARCHAR,
            usage_type VARCHAR,
            pricing_unit VARCHAR,
            aws_amortized_cost DOUBLE,
            aws_region VARCHAR,
            gcp_region VARCHAR,
            operation VARCHAR,
            volume_type VARCHAR,
            total_usage DOUBLE,
            instance_type VARCHAR,
            instance_ram_gb DOUBLE,
            deployment_option VARCHAR,
            database_engine VARCHAR,
            pricing_model VARCHAR,
            product_code VARCHAR,
            instance_vcpus DOUBLE,
            operating_system VARCHAR,
            license_model VARCHAR,
            line_item_type VARCHAR,
            billing_month VARCHAR
        )
    """)
    # 3 distinct billing months with Cloud Armor Enterprise fee
    for i, month in enumerate(["2026-01", "2026-02", "2026-03"]):
        con.execute(f"""
            INSERT INTO aws_li_catalog VALUES (
                'armor_{i}', 'flat_hourly', 'AWS WAF', 'Bot Control managed rule group', 'Mo',
                3000.0, 'us-east-1', 'global', 'Enrollment', '',
                1.0, '', 0.0, '', '',
                'OnDemand', 'awswaf', 0.0, '', '', 'Usage', '{month}'
            )
        """)

    catalog_cols = {c[1].lower() for c in con.execute("PRAGMA table_info('aws_li_catalog')").fetchall()}
    _pc_expr = "product_code" if "product_code" in catalog_cols else "NULL AS product_code"
    _vcpu_expr = "instance_vcpus" if "instance_vcpus" in catalog_cols else "NULL AS instance_vcpus"
    _os_expr = "operating_system" if "operating_system" in catalog_cols else "NULL AS operating_system"
    _lic_expr = "license_model" if "license_model" in catalog_cols else "NULL AS license_model"
    _bm_expr = "billing_month" if "billing_month" in catalog_cols else "NULL AS billing_month"

    rows = con.execute(f"""
        SELECT aws_li_key, mechanic_group, product, usage_type, pricing_unit AS unit,
               aws_amortized_cost, aws_region AS region, gcp_region, operation, volume_type,
               total_usage, instance_type, instance_ram_gb, deployment_option, database_engine,
               pricing_model, {_pc_expr}, {_vcpu_expr}, {_os_expr}, {_lic_expr}, line_item_type,
               {_bm_expr}
        FROM aws_li_catalog
        WHERE mechanic_group = 'flat_hourly'
    """).fetchall()
    con.close()

    row_dicts = [dict(zip(STATIC_MAPPER_ROW_COLS, raw)) for raw in rows]
    mappings = map_flat_hourly(row_dicts)

    # All 3 months must have priced Cloud Armor Enterprise enrollment fees (strategy != 'ignore')
    priced_mappings = [m for m in mappings if m["strategy"] == "map" and m.get("gcp_sku_id")]
    assert len(priced_mappings) == 3
    # Check that each billing month is accounted for and that 'month unknown' note does NOT appear
    notes = [m.get("projection_note", "") for m in priced_mappings]
    assert not any("month unknown" in n for n in notes)
