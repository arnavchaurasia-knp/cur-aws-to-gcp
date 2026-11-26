import duckdb
import pytest
from scripts.schema_contract import (
    CANONICAL_PROJECTION_COLUMNS,
    CANONICAL_CATALOG_COLUMNS,
    CANONICAL_MAPPING_COLUMNS,
    SchemaContractViolationError,
    projection_columns,
    mapping_columns,
    catalog_columns,
    require_columns,
    clear_schema_cache,
)


def test_canonical_columns_defined():
    assert "gcp_projected_cost" in CANONICAL_PROJECTION_COLUMNS
    assert "gcp_cost_1yr_cud" in CANONICAL_PROJECTION_COLUMNS
    assert "gcp_cost_3yr_cud" in CANONICAL_PROJECTION_COLUMNS
    assert "is_workload" in CANONICAL_PROJECTION_COLUMNS
    assert "aws_li_key" in CANONICAL_PROJECTION_COLUMNS

    assert "aws_li_key" in CANONICAL_CATALOG_COLUMNS
    assert "product" in CANONICAL_CATALOG_COLUMNS

    assert "aws_li_key" in CANONICAL_MAPPING_COLUMNS
    assert "strategy" in CANONICAL_MAPPING_COLUMNS


def test_projection_columns_and_require_columns_success():
    con = duckdb.connect(":memory:")
    # Create synthetic gcp_projection
    con.execute("""
        CREATE VIEW gcp_projection AS
        SELECT 'k1' AS aws_li_key, 'EC2' AS product, true AS is_workload,
               10.5 AS gcp_projected_cost, 9.0 AS gcp_cost_1yr_cud, 7.5 AS gcp_cost_3yr_cud
    """)

    cols = projection_columns(con)
    assert "gcp_projected_cost" in cols
    assert "aws_li_key" in cols
    assert "product" in cols

    # Calling require_columns with needed columns succeeds
    res = require_columns(con, "gcp_projection", ["gcp_projected_cost", "is_workload"])
    assert "gcp_projected_cost" in res
    assert "is_workload" in res


def test_require_columns_missing_column_raises_contract_violation():
    con = duckdb.connect(":memory:")
    # Synthetic gcp_projection missing 'gcp_cost_3yr_cud'
    con.execute("""
        CREATE VIEW gcp_projection AS
        SELECT 'k1' AS aws_li_key, 'EC2' AS product, true AS is_workload,
               10.5 AS gcp_projected_cost
    """)

    with pytest.raises(SchemaContractViolationError) as exc_info:
        require_columns(con, "gcp_projection", ["gcp_projected_cost", "gcp_cost_3yr_cud"])

    err_msg = str(exc_info.value)
    assert "Schema contract violation" in err_msg
    assert "'gcp_projection'" in err_msg
    assert "gcp_cost_3yr_cud" in err_msg
    # Ensure it's not a raw BinderException
    assert "Binder Error" not in err_msg


def test_require_columns_missing_table_raises_contract_violation():
    con = duckdb.connect(":memory:")
    with pytest.raises(SchemaContractViolationError) as exc_info:
        require_columns(con, "nonexistent_table", ["col1"])

    err_msg = str(exc_info.value)
    assert "Schema contract violation" in err_msg
    assert "'nonexistent_table'" in err_msg
    assert "col1" in err_msg


def test_cache_and_clear_schema_cache():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE test_tab (a INT, b TEXT)")
    cols1 = mapping_columns(con)
    assert cols1 == set()

    con.execute("CREATE TABLE aws_li_to_gcp_li (aws_li_key VARCHAR, strategy VARCHAR)")
    # Clear cache and re-introspect
    clear_schema_cache(con)
    cols2 = mapping_columns(con)
    assert "aws_li_key" in cols2
    assert "strategy" in cols2

    # Add a column, introspect again without clear -> returns cached
    con.execute("ALTER TABLE aws_li_to_gcp_li ADD COLUMN component VARCHAR")
    cols_cached = mapping_columns(con)
    assert "component" not in cols_cached

    # Now clear cache -> returns updated
    clear_schema_cache(con)
    cols_fresh = mapping_columns(con)
    assert "component" in cols_fresh
