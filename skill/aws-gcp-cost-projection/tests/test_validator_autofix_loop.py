import os
import sys
import duckdb
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from validate_fix import _repair_illegal_passthrough, _has_usable_rate_clause, _mappable_pattern_clause
from apply_static_mappings import intentional_passthrough_exclude_clause


@pytest.fixture
def test_db():
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key VARCHAR PRIMARY KEY,
            product VARCHAR,
            operation VARCHAR,
            usage_type VARCHAR,
            is_workload BOOLEAN,
            aws_amortized_cost DOUBLE
        );
        CREATE TABLE aws_li_to_gcp_li (
            aws_li_key VARCHAR,
            component VARCHAR,
            gcp_service VARCHAR,
            gcp_sku_id VARCHAR,
            gcp_sku_name VARCHAR,
            strategy VARCHAR,
            passthrough_reason VARCHAR,
            projection_note VARCHAR
        );
        CREATE VIEW gcp_projection AS
        SELECT
            m.aws_li_key,
            m.component,
            c.product,
            c.is_workload,
            m.strategy,
            m.gcp_service,
            m.gcp_sku_id,
            m.gcp_sku_name,
            c.aws_amortized_cost,
            CASE
                WHEN m.strategy = 'passthrough' THEN c.aws_amortized_cost
                WHEN m.gcp_sku_id IS NULL THEN NULL
                WHEN m.gcp_sku_id = 'ZERO_RATE' THEN 0.0
                ELSE c.aws_amortized_cost * 0.9
            END AS gcp_projected_cost
        FROM aws_li_to_gcp_li m
        JOIN aws_li_catalog c USING (aws_li_key);
    """)
    return con


def test_no_rate_row_is_not_promoted(test_db):
    """1. test_no_rate_row_is_not_promoted:
    A passthrough row on a mappable service whose projection cost is NULL stays
    passthrough and gains a reason (no_rate_in_region).
    """
    con = test_db
    con.execute("""
        INSERT INTO aws_li_catalog VALUES ('k1', 'Amazon Elastic Compute Cloud', 'RunInstances', 'BoxUsage:c7g.xlarge', true, 100.0);
        INSERT INTO aws_li_to_gcp_li VALUES ('k1', 'core', 'Compute Engine', NULL, 'C4A Arm core', 'passthrough', NULL, 'note');
    """)

    restored = _repair_illegal_passthrough(con)
    assert restored == 0

    row = con.execute("SELECT strategy, passthrough_reason FROM aws_li_to_gcp_li WHERE aws_li_key = 'k1'").fetchone()
    assert row[0] == "passthrough"
    assert row[1] == "no_rate_in_region"


def test_rated_row_is_promoted(test_db):
    """2. test_rated_row_is_promoted:
    The same row with a non-zero projected cost is promoted to map.
    """
    con = test_db
    con.execute("""
        INSERT INTO aws_li_catalog VALUES ('k2', 'Amazon Elastic Compute Cloud', 'RunInstances', 'BoxUsage:c6i.xlarge', true, 100.0);
        INSERT INTO aws_li_to_gcp_li VALUES ('k2', 'core', 'Compute Engine', 'VALID_SKU_123', 'C3 Instance Core', 'passthrough', NULL, 'note');
    """)

    restored = _repair_illegal_passthrough(con)
    assert restored == 1

    row = con.execute("SELECT strategy FROM aws_li_to_gcp_li WHERE aws_li_key = 'k2'").fetchone()
    assert row[0] == "map"


def test_restore_and_downgrade_cannot_disagree(test_db):
    """3. test_restore_and_downgrade_cannot_disagree:
    Over a mixed fixture, the set promoted by A and the set demoted by B are disjoint.
    """
    con = test_db
    # k_rated: has valid rate -> should be promoted by A, and NOT demoted by B
    con.execute("INSERT INTO aws_li_catalog VALUES ('k_rated', 'Amazon Relational Database Service', 'CreateDB', 'InstanceUsage', true, 50.0);")
    con.execute("INSERT INTO aws_li_to_gcp_li VALUES ('k_rated', 'core', 'Cloud SQL', 'SKU_RDS', 'Cloud SQL Core', 'passthrough', NULL, 'note');")

    # k_unrated: no rate -> should NOT be promoted by A; if it were map, B would demote it
    con.execute("INSERT INTO aws_li_catalog VALUES ('k_unrated', 'Amazon Relational Database Service', 'CreateDB', 'InstanceUsage', true, 50.0);")
    con.execute("INSERT INTO aws_li_to_gcp_li VALUES ('k_unrated', 'core', 'Cloud SQL', NULL, 'Cloud SQL Core', 'passthrough', NULL, 'note');")

    # k_prod_parity (C2): production shape where strategy='passthrough', gcp_sku_id is NULL,
    # and gcp_projected_cost equals aws_amortized_cost (non-zero parity amount). Must NOT be promoted.
    con.execute("INSERT INTO aws_li_catalog VALUES ('k_prod_parity', 'Amazon Elastic Compute Cloud', 'RunInstances', 'BoxUsage:c7g.xlarge', true, 98.11);")
    con.execute("INSERT INTO aws_li_to_gcp_li VALUES ('k_prod_parity', 'core', 'Compute Engine', NULL, 'C4A Arm core', 'passthrough', NULL, 'note');")

    # Run A
    _repair_illegal_passthrough(con)
    promoted_by_a = {r[0] for r in con.execute("SELECT aws_li_key FROM aws_li_to_gcp_li WHERE strategy = 'map'").fetchall()}

    # Check which rows B would demote using shared predicate
    demoted_by_b = {r[0] for r in con.execute(f"""
        SELECT m.aws_li_key
        FROM aws_li_to_gcp_li m
        JOIN aws_li_catalog c USING (aws_li_key)
        JOIN gcp_projection p ON p.aws_li_key = m.aws_li_key AND p.component IS NOT DISTINCT FROM m.component
        WHERE m.strategy IN ('map', 'break_down')
          AND NOT {_has_usable_rate_clause('p', 'gcp_projected_cost')}
          AND c.aws_amortized_cost > 5
    """).fetchall()}

    assert promoted_by_a.isdisjoint(demoted_by_b), f"Autofixes A and B collided on: {promoted_by_a & demoted_by_b}"
    assert "k_rated" in promoted_by_a
    assert "k_unrated" not in promoted_by_a
    assert "k_prod_parity" not in promoted_by_a


def test_reasoned_passthrough_not_counted_as_violation(test_db):
    """4. test_reasoned_passthrough_not_counted_as_violation:
    A passthrough row with no_rate_in_region or family_not_available_in_region
    does not appear in passthrough_on_mappable_service.
    """
    con = test_db
    con.execute("""
        INSERT INTO aws_li_catalog VALUES ('k3', 'Amazon Elastic Compute Cloud', 'RunInstances', 'BoxUsage:m7g.2xlarge', true, 200.0);
        INSERT INTO aws_li_to_gcp_li VALUES ('k3', 'core', 'Compute Engine', NULL, NULL, 'passthrough', 'no_rate_in_region', 'note');
    """)

    like, params = _mappable_pattern_clause("LOWER(c.product)")
    note_exclude = intentional_passthrough_exclude_clause("m.projection_note")
    bad_pt = con.execute(f"""
        SELECT c.product, COALESCE(c.operation,'') op, ROUND(c.aws_amortized_cost,2)
        FROM aws_li_to_gcp_li m JOIN aws_li_catalog c USING (aws_li_key)
        WHERE m.strategy='passthrough' AND c.aws_amortized_cost > 1 AND ({like})
          AND COALESCE(m.gcp_service,'') NOT LIKE '%Accelerator%'
          AND COALESCE(m.component,'') <> 'accelerator'
          AND {note_exclude}
    """, params).fetchall()

    assert len(bad_pt) == 0


def test_unexplained_passthrough_still_violates(test_db):
    """5. test_unexplained_passthrough_still_violates:
    A passthrough row with no reason still counts, so the gate keeps its teeth.
    """
    con = test_db
    con.execute("""
        INSERT INTO aws_li_catalog VALUES ('k4', 'Amazon Elastic Compute Cloud', 'RunInstances', 'BoxUsage:c6i.large', true, 150.0);
        INSERT INTO aws_li_to_gcp_li VALUES ('k4', 'core', 'Compute Engine', NULL, NULL, 'passthrough', NULL, 'unexplained plain note');
    """)

    like, params = _mappable_pattern_clause("LOWER(c.product)")
    note_exclude = intentional_passthrough_exclude_clause("m.projection_note")
    bad_pt = con.execute(f"""
        SELECT c.product, COALESCE(c.operation,'') op, ROUND(c.aws_amortized_cost,2)
        FROM aws_li_to_gcp_li m JOIN aws_li_catalog c USING (aws_li_key)
        WHERE m.strategy='passthrough' AND c.aws_amortized_cost > 1 AND ({like})
          AND COALESCE(m.gcp_service,'') NOT LIKE '%Accelerator%'
          AND COALESCE(m.component,'') <> 'accelerator'
          AND {note_exclude}
    """, params).fetchall()

    assert len(bad_pt) == 1
    assert bad_pt[0][0] == "Amazon Elastic Compute Cloud"
    assert bad_pt[0][2] == 150.0
