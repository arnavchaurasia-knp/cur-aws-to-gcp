import os
import sys
import tempfile
import duckdb
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from validate_fix import check_capacity_reconciliation
import diagnostics


@pytest.fixture
def cap_db():
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key VARCHAR PRIMARY KEY,
            product VARCHAR,
            instance_type VARCHAR,
            instance_vcpus DOUBLE,
            instance_ram_gb DOUBLE,
            instance_count DOUBLE,
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
            unit_multiplier DOUBLE
        );
    """)
    return con


def test_unverified_row_not_counted_as_gcp_capacity(cap_db):
    """1. test_unverified_row_not_counted_as_gcp_capacity:
    A passthrough row with no mapped core contributes nothing to the GCP total.
    This is the regression that matters most.
    """
    con = cap_db
    # k_pt is an instance row (e.g. Arm c7g.xlarge) that is passthrough with no mapped core/ram component.
    con.execute("""
        INSERT INTO aws_li_catalog VALUES (
            'k_pt', 'Amazon Elastic Compute Cloud', 'c7g.xlarge', 4.0, 8.0, 1.0, true, 100.0
        );
        INSERT INTO aws_li_to_gcp_li VALUES (
            'k_pt', NULL, 'Compute Engine', NULL, NULL, 'passthrough', NULL
        );
    """)

    violations, summary = check_capacity_reconciliation(con)
    # GCP total for verified rows must be 0, not 4.0!
    assert summary["verified_gcp_vcpu"] == 0.0
    assert summary["verified_aws_vcpu"] == 0.0
    assert summary["unverified_rows"] == 1
    assert summary["unverified_aws_vcpu"] == 4.0
    assert len(violations) == 0


def test_gate_fails_on_real_shortfall(cap_db):
    """2. test_gate_fails_on_real_shortfall:
    A mapped row whose GCP core is smaller than its AWS vCPU count produces a violation.
    Proves the gate can fail on a genuine deficit.
    """
    con = cap_db
    # AWS has 8 vCPUs, but GCP only mapped 4 vCPUs
    con.execute("""
        INSERT INTO aws_li_catalog VALUES (
            'k_short', 'Amazon Elastic Compute Cloud', 'c6i.2xlarge', 8.0, 16.0, 1.0, true, 200.0
        );
        INSERT INTO aws_li_to_gcp_li VALUES (
            'k_short', 'core', 'Compute Engine', 'SKU_1', 'C3 Core', 'map', 4.0
        );
        INSERT INTO aws_li_to_gcp_li VALUES (
            'k_short', 'ram', 'Compute Engine', 'SKU_2', 'C3 Ram', 'map', 16.0
        );
    """)

    violations, summary = check_capacity_reconciliation(con)
    assert len(violations) == 1
    assert violations[0]["metric"] == "vCPU"
    assert violations[0]["aws_capacity"] == 8.0
    assert violations[0]["gcp_capacity"] == 4.0
    assert violations[0]["deficit"] == 4.0
    assert summary["status"] == "FAIL"


def test_gate_passes_when_verified_rows_are_adequate(cap_db):
    """3. test_gate_passes_when_verified_rows_are_adequate:
    Verified rows meeting or exceeding AWS pass, regardless of how many unverified rows exist.
    """
    con = cap_db
    # Mapped row with adequate capacity: AWS 4 vCPU, GCP 4 vCPU
    con.execute("""
        INSERT INTO aws_li_catalog VALUES (
            'k_good', 'Amazon Elastic Compute Cloud', 'c6i.xlarge', 4.0, 8.0, 1.0, true, 100.0
        );
        INSERT INTO aws_li_to_gcp_li VALUES (
            'k_good', 'core', 'Compute Engine', 'SKU_1', 'C3 Core', 'map', 4.0
        );
        INSERT INTO aws_li_to_gcp_li VALUES (
            'k_good', 'ram', 'Compute Engine', 'SKU_2', 'C3 Ram', 'map', 8.0
        );
        -- 10 unverified passthrough rows with 40 vCPUs
        INSERT INTO aws_li_catalog VALUES (
            'k_pt1', 'Amazon Elastic Compute Cloud', 'c7g.xlarge', 4.0, 8.0, 10.0, true, 1000.0
        );
        INSERT INTO aws_li_to_gcp_li VALUES (
            'k_pt1', NULL, 'Compute Engine', NULL, NULL, 'passthrough', NULL
        );
    """)

    violations, summary = check_capacity_reconciliation(con)
    assert len(violations) == 0
    assert summary["status"] == "OK"
    assert summary["verified_aws_vcpu"] == 4.0
    assert summary["verified_gcp_vcpu"] == 4.0
    assert summary["unverified_rows"] == 1
    assert summary["unverified_aws_vcpu"] == 40.0


def test_unverified_count_reported(cap_db, capsys, monkeypatch):
    """4. test_unverified_count_reported:
    The row count and vCPU weight of unverified rows reach the log and a diagnostic.
    """
    con = cap_db
    con.execute("""
        INSERT INTO aws_li_catalog VALUES (
            'k_v', 'Amazon Elastic Compute Cloud', 'c6i.xlarge', 4.0, 8.0, 1.0, true, 100.0
        );
        INSERT INTO aws_li_to_gcp_li VALUES (
            'k_v', 'core', 'Compute Engine', 'SKU_1', 'C3 Core', 'map', 4.0
        );
        INSERT INTO aws_li_to_gcp_li VALUES (
            'k_v', 'ram', 'Compute Engine', 'SKU_2', 'C3 Ram', 'map', 8.0
        );
        INSERT INTO aws_li_catalog VALUES (
            'k_unv', 'Amazon Elastic Compute Cloud', 'c7g.2xlarge', 8.0, 16.0, 2.0, true, 400.0
        );
        INSERT INTO aws_li_to_gcp_li VALUES (
            'k_unv', NULL, 'Compute Engine', NULL, NULL, 'passthrough', NULL
        );
    """)

    import validate_fix
    recorded_diags = []
    monkeypatch.setattr(validate_fix, "diag", lambda level, code, source, message, rows=0, usd=0.0, phase=None, detail=None, job_dir=None: recorded_diags.append({
        "level": level, "code": code, "source": source, "message": message, "rows": rows
    }))

    with tempfile.TemporaryDirectory() as tmpdir:
        violations, summary = check_capacity_reconciliation(con, jobdir=tmpdir)

    out = capsys.readouterr().out
    assert "Capacity check (verified rows only): AWS 4.0 vCPU vs GCP 4.0 vCPU — OK" in out
    assert "unverified:     1 row(s), 16 AWS vCPU carried at assumed parity" in out

    assert len(recorded_diags) == 1
    d = recorded_diags[0]
    assert d["code"] == "capacity_check_unverified"
    assert d["rows"] == 1
    assert "16 AWS vCPU" in d["message"]


def test_unresolved_instance_is_unverified_not_na(cap_db):
    """5. test_unresolved_instance_is_unverified_not_na:
    An instance row with NULL instance_vcpus counts as unverified (T3).
    """
    con = cap_db
    # Row 1: Instance row whose specs failed to resolve (instance_type IS NOT NULL, instance_vcpus IS NULL)
    # Row 2: Non-instance row (storage/egress) with instance_type IS NULL -> not_applicable
    con.execute("""
        INSERT INTO aws_li_catalog VALUES (
            'k_unresolved', 'Amazon Elastic Compute Cloud', 'custom.exotic.instance', NULL, NULL, 1.0, true, 150.0
        );
        INSERT INTO aws_li_to_gcp_li VALUES (
            'k_unresolved', NULL, 'Compute Engine', NULL, NULL, 'passthrough', NULL
        );
        INSERT INTO aws_li_catalog VALUES (
            'k_s3', 'Amazon Simple Storage Service', NULL, NULL, NULL, 1.0, true, 50.0
        );
        INSERT INTO aws_li_to_gcp_li VALUES (
            'k_s3', NULL, 'Cloud Storage', 'SKU_GCS', 'Standard Storage', 'map', 1.0
        );
    """)

    violations, summary = check_capacity_reconciliation(con)
    assert summary["unverified_rows"] == 1
    assert summary["na_rows"] == 1
    assert summary["verified_aws_vcpu"] == 0.0
    assert summary["verified_gcp_vcpu"] == 0.0
