import json
import os
import subprocess
import sys
import duckdb
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import ensure_full_coverage as efc

SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts", "ensure_full_coverage.py")


def _create_minimal_tables(con):
    con.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key TEXT,
            gcp_region TEXT,
            is_workload BOOLEAN,
            aws_amortized_cost DOUBLE,
            product TEXT,
            mechanic_group TEXT
        )
    """)
    con.execute("""
        CREATE TABLE aws_li_to_gcp_li (
            aws_li_key TEXT,
            gcp_service TEXT,
            gcp_sku_id TEXT,
            gcp_sku_name TEXT,
            gcp_sku_unit TEXT,
            strategy TEXT,
            unit_multiplier DOUBLE,
            gcp_region TEXT,
            projection_note TEXT,
            mapping_confidence DOUBLE,
            is_workload BOOLEAN,
            break_down BOOLEAN,
            passthrough_reason TEXT,
            decided_by TEXT
        )
    """)


# ---------------------------------------------------------------------------
# FIX-1: Never fatal (robustness)
# ---------------------------------------------------------------------------

def test_fix1_missing_column_exits_zero(tmp_path):
    """Pointing at a DB with a broken schema (e.g. missing column) exits 0 and logs non-fatal error.
    FIX-5: Asserts that stdout does NOT falsely claim success ('backstopped'), DOES notify of INSERT failure,
    and data_gaps.json marks rows as INSERT_FAILED."""
    db_path = tmp_path / "broken_schema.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key TEXT,
            gcp_region TEXT,
            is_workload BOOLEAN,
            aws_amortized_cost DOUBLE,
            product TEXT,
            mechanic_group TEXT
        )
    """)
    # Missing gcp_sku_unit column
    con.execute("""
        CREATE TABLE aws_li_to_gcp_li (
            aws_li_key TEXT,
            gcp_service TEXT,
            strategy TEXT
        )
    """)
    con.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('k1', 'us-central1', true, 100.0, 'Amazon Elastic Compute Cloud', 'misc')
    """)
    con.close()

    res = subprocess.run(
        [sys.executable, SCRIPT_PATH, str(db_path)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert "non-fatal error" in res.stderr
    assert "skipping" in res.stderr
    # FIX-5: Must NOT claim false backstopped success
    assert "backstopped" not in res.stdout
    assert "INSERT FAILED" in res.stdout
    assert "remain UNMAPPED" in res.stdout

    # Verify data_gaps.json was written with resolution: INSERT_FAILED
    gaps_path = tmp_path / "data_gaps.json"
    assert gaps_path.exists()
    with open(gaps_path, "r", encoding="utf-8") as f:
        gaps_data = json.load(f)
    assert gaps_data["count"] == 1
    assert gaps_data["total_cost"] == 100.0
    assert gaps_data["rows"][0]["resolution"] == "INSERT_FAILED"


def test_fix1_no_args_exits_zero():
    """Calling script with no arguments exits 0 cleanly."""
    res = subprocess.run(
        [sys.executable, SCRIPT_PATH],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0


# ---------------------------------------------------------------------------
# FIX-2: Ignore vs Passthrough logic
# ---------------------------------------------------------------------------

def test_fix2_is_ignore_helper():
    """Unit test _is_ignore logic matrix."""
    # is_workload=False -> always ignore regardless of group or cost
    assert efc._is_ignore(False, "misc", 58000.0) is True
    assert efc._is_ignore(None, "misc", 10.0) is True

    # Known ignore groups -> ignore even if is_workload=True
    assert efc._is_ignore(True, "commitment_discount", 150.0) is True
    assert efc._is_ignore(True, "negative_cost", -20.0) is True

    # Negative cost mis-grouped in misc -> ignore
    assert efc._is_ignore(True, "misc", -200.0) is True

    # Real positive workload -> passthrough
    assert efc._is_ignore(True, "misc", 10.0) is False
    assert efc._is_ignore(True, "compute_breakdown", 500.0) is False


def test_fix2_ignore_vs_passthrough_db_execution(tmp_path):
    """End-to-end DB test of the 4 key FIX-2 scenarios."""
    db_path = tmp_path / "projection.duckdb"
    con = duckdb.connect(str(db_path))
    _create_minimal_tables(con)
    con.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('nw1', 'us-central1', false, 58000.0, 'Enterprise Support', 'non_workload'),
            ('m1',  'us-central1', true,  10.0,    'Some Misc Service', 'misc'),
            ('cd1', 'us-central1', true,  250.0,   'RI Fee Mis-stamped', 'commitment_discount'),
            ('m2',  'us-central1', true,  -200.0,  'Credit Mis-grouped', 'misc')
    """)
    con.close()

    res = subprocess.run(
        [sys.executable, SCRIPT_PATH, str(db_path)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0

    con = duckdb.connect(str(db_path))
    rows = con.execute("""
        SELECT aws_li_key, gcp_service, strategy, unit_multiplier, mapping_confidence
        FROM aws_li_to_gcp_li
        ORDER BY aws_li_key
    """).fetchall()
    con.close()

    mapping = {r[0]: (r[1], r[2], r[3], r[4]) for r in rows}

    # 1. non_workload, is_workload=false, $58K -> ignore
    assert mapping["nw1"][0] == "Non-workload / Commitment (ignored)"
    assert mapping["nw1"][1] == "ignore"

    # 2. misc, is_workload=true, $10 -> passthrough
    assert mapping["m1"][0] == "Manual Review — Unmapped"
    assert mapping["m1"][1] == "passthrough"

    # 3. commitment_discount, is_workload=true -> ignore
    assert mapping["cd1"][0] == "Non-workload / Commitment (ignored)"
    assert mapping["cd1"][1] == "ignore"

    # 4. misc, cost=-$200 -> ignore
    assert mapping["m2"][0] == "Non-workload / Commitment (ignored)"
    assert mapping["m2"][1] == "ignore"


# ---------------------------------------------------------------------------
# FIX-3, FIX-6: Surface backstopped rows in data_gaps.json (observability)
# ---------------------------------------------------------------------------

def test_fix3_data_gaps_created_and_idempotent(tmp_path):
    """Backstopped rows are surfaced in data_gaps.json next to DB; re-running is idempotent.
    FIX-6a: Exclude 'ignore' rows from count and total_cost so banner isn't inflated."""
    audit_dir = tmp_path / "projection-audit"
    audit_dir.mkdir()
    db_path = audit_dir / "projection.duckdb"
    con = duckdb.connect(str(db_path))
    _create_minimal_tables(con)
    con.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('k_pass', 'us-east-1', true, 120.50, 'Custom Service A', 'misc'),
            ('k_ign',  'us-east-1', false, 300.00, 'Custom Service B', 'non_workload')
    """)
    con.close()

    res = subprocess.run(
        [sys.executable, SCRIPT_PATH, str(db_path)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0

    gaps_path = audit_dir / "data_gaps.json"
    assert gaps_path.exists()

    with open(gaps_path, "r", encoding="utf-8") as f:
        gaps_data = json.load(f)

    # FIX-6a: count and total_cost exclude 'ignore' rows ($300 non_workload excluded)
    assert gaps_data["count"] == 1
    assert gaps_data["total_cost"] == 120.50
    rows = gaps_data["rows"]
    # All rows recorded in the rows list
    assert len(rows) == 2

    row_pass = next(r for r in rows if r["aws_li_key"] == "k_pass")
    assert row_pass["product"] == "Custom Service A"
    assert row_pass["cost"] == 120.50
    assert row_pass["resolution"] == "passthrough"
    assert row_pass["source"] == "ensure_full_coverage.py"

    row_ign = next(r for r in rows if r["aws_li_key"] == "k_ign")
    assert row_ign["product"] == "Custom Service B"
    assert row_ign["cost"] == 300.00
    assert row_ign["resolution"] == "ignore"
    assert row_ign["source"] == "ensure_full_coverage.py"

    # Re-running on the same DB has no new gaps (already mapped) -> data_gaps.json unchanged
    res2 = subprocess.run(
        [sys.executable, SCRIPT_PATH, str(db_path)],
        capture_output=True,
        text=True,
    )
    assert res2.returncode == 0
    assert "no gaps found, nothing to do" in res2.stdout

    with open(gaps_path, "r", encoding="utf-8") as f:
        gaps_data2 = json.load(f)
    assert gaps_data2["count"] == 1
    assert gaps_data2["total_cost"] == 120.50
    assert len(gaps_data2["rows"]) == 2


def test_fix3_data_gaps_additive_merge_with_existing(tmp_path):
    """If data_gaps.json already exists (e.g. from classify_mechanics), backstopped rows are added cleanly."""
    db_path = tmp_path / "projection.duckdb"
    con = duckdb.connect(str(db_path))
    _create_minimal_tables(con)
    con.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('k_new', 'us-west-1', true, 75.0, 'New Service', 'misc')
    """)
    con.close()

    gaps_path = tmp_path / "data_gaps.json"
    existing_content = {
        "count": 1,
        "total_cost": 50.0,
        "rows": [
            {
                "canonical": "vpc",
                "product": "Amazon Virtual Private Cloud",
                "cost": 50.0,
            }
        ],
    }
    with open(gaps_path, "w", encoding="utf-8") as f:
        json.dump(existing_content, f, indent=2)

    res = subprocess.run(
        [sys.executable, SCRIPT_PATH, str(db_path)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0

    with open(gaps_path, "r", encoding="utf-8") as f:
        merged = json.load(f)

    assert merged["count"] == 2
    assert merged["total_cost"] == 125.0
    keys = [r.get("aws_li_key") for r in merged["rows"]]
    assert "k_new" in keys


def test_fix6b_data_gaps_deduplication_against_classify_mechanics(tmp_path):
    """FIX-6b: classify_mechanics entry (no aws_li_key) matching (product, cost) must not be double-counted."""
    db_path = tmp_path / "projection.duckdb"
    con = duckdb.connect(str(db_path))
    _create_minimal_tables(con)
    con.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('k_dup', 'us-west-1', true, 150.0, 'Amazon SageMaker', 'misc')
    """)
    con.close()

    # Pre-populate data_gaps.json as classify_mechanics writes it
    gaps_path = tmp_path / "data_gaps.json"
    seed_content = {
        "count": 1,
        "total_cost": 150.0,
        "rows": [
            {
                "canonical": "sagemaker",
                "product": "Amazon SageMaker",
                "cost": 150.0,
            }
        ],
    }
    with open(gaps_path, "w", encoding="utf-8") as f:
        json.dump(seed_content, f, indent=2)

    res = subprocess.run(
        [sys.executable, SCRIPT_PATH, str(db_path)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0

    with open(gaps_path, "r", encoding="utf-8") as f:
        merged = json.load(f)

    # Must NOT double-count to count=2, total_cost=300.0
    assert merged["count"] == 1
    assert merged["total_cost"] == 150.0
    assert len(merged["rows"]) == 1


def test_fix6b_keyed_same_product_cost_not_false_deduped(tmp_path):
    """FIX-6b exactness: two genuinely DIFFERENT rows that share (product, cost) must
    both be recorded when the existing entry carries an aws_li_key. The (product, cost)
    fallback applies only to KEYLESS legacy rows, so it must not collapse distinct keyed rows."""
    db_path = tmp_path / "projection.duckdb"
    con = duckdb.connect(str(db_path))
    _create_minimal_tables(con)
    # New unmapped row shares product+cost with a DIFFERENT already-recorded keyed row.
    con.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('k_second', 'us-west-1', true, 150.0, 'Amazon SageMaker', 'misc')
    """)
    con.close()

    gaps_path = tmp_path / "data_gaps.json"
    seed_content = {
        "count": 1,
        "total_cost": 150.0,
        "rows": [
            # Already recorded, WITH a key, same (product, cost) but a different line item
            {"aws_li_key": "k_first", "product": "Amazon SageMaker", "cost": 150.0,
             "resolution": "passthrough", "source": "ensure_full_coverage.py"},
        ],
    }
    with open(gaps_path, "w", encoding="utf-8") as f:
        json.dump(seed_content, f, indent=2)

    res = subprocess.run(
        [sys.executable, SCRIPT_PATH, str(db_path)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0

    with open(gaps_path, "r", encoding="utf-8") as f:
        merged = json.load(f)

    # Both distinct rows must be present — NOT collapsed by the (product, cost) fallback.
    assert merged["count"] == 2
    assert merged["total_cost"] == 300.0
    keys = {r.get("aws_li_key") for r in merged["rows"]}
    assert keys == {"k_first", "k_second"}


def test_fix5_stale_insert_failed_refreshed_on_success(tmp_path):
    """A row previously recorded as INSERT_FAILED must be refreshed to its real
    resolution once a later successful run actually maps it — so the data-gap banner
    stops over-reporting rows that are now mapped."""
    audit_dir = tmp_path / "projection-audit"
    audit_dir.mkdir()
    db_path = audit_dir / "projection.duckdb"
    con = duckdb.connect(str(db_path))
    _create_minimal_tables(con)
    con.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('k_retry', 'us-east-1', true, 200.0, 'Retry Service', 'misc')
    """)
    con.close()

    # Simulate a prior failed run: data_gaps.json already marks this key INSERT_FAILED.
    gaps_path = audit_dir / "data_gaps.json"
    seed = {
        "count": 1,
        "total_cost": 200.0,
        "rows": [{
            "aws_li_key": "k_retry", "product": "Retry Service", "cost": 200.0,
            "mechanic_group": "misc", "reason": "prior failure",
            "resolution": "INSERT_FAILED", "source": "ensure_full_coverage.py",
        }],
    }
    with open(gaps_path, "w", encoding="utf-8") as f:
        json.dump(seed, f, indent=2)

    # This run's INSERT succeeds (full schema), so the stale entry must be refreshed.
    res = subprocess.run(
        [sys.executable, SCRIPT_PATH, str(db_path)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert "backstopped" in res.stdout  # genuine success this run

    with open(gaps_path, "r", encoding="utf-8") as f:
        merged = json.load(f)

    assert len(merged["rows"]) == 1          # not duplicated
    row = merged["rows"][0]
    assert row["aws_li_key"] == "k_retry"
    assert row["resolution"] == "passthrough"  # refreshed from INSERT_FAILED
    assert merged["count"] == 1
    assert merged["total_cost"] == 200.0


# ---------------------------------------------------------------------------
# FIX-4: Minor cleanups (zero-cost reporting & 12-tuple parity)
# ---------------------------------------------------------------------------

def test_fix4_zero_cost_reporting_in_summary(tmp_path):
    """Mix of $0 and non-$0 unmapped rows reports zero-cost count separately in summary."""
    db_path = tmp_path / "projection.duckdb"
    con = duckdb.connect(str(db_path))
    _create_minimal_tables(con)
    con.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('z1', 'us-central1', true, 0.0,   'Zero Cost Service 1', 'misc'),
            ('z2', 'us-central1', true, 0.0,   'Zero Cost Service 2', 'misc'),
            ('p1', 'us-central1', true, 85.0,  'Paid Service 1',      'misc'),
            ('i1', 'us-central1', false, 0.0,  'Zero Cost NonWorkload', 'non_workload')
    """)
    con.close()

    res = subprocess.run(
        [sys.executable, SCRIPT_PATH, str(db_path)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0

    # Summary line must report total rows, dollar total, breakdown, and zero-cost count
    # e.g.: "ensure_full_coverage: backstopped 4 row(s) totaling $85.00 (1 as ignore/commitment, 3 as passthrough/Manual Review; 3 zero-cost) — loud, not silent:"
    assert "backstopped 4 row(s) totaling $85.00" in res.stdout
    assert "1 as ignore/commitment" in res.stdout
    assert "3 as passthrough/Manual Review" in res.stdout
    assert "3 zero-cost" in res.stdout


def test_fix4_tuple_parity_and_columns_smoke(tmp_path):
    """Verify 12-tuple value insertion matches 12-column table schema."""
    db_path = tmp_path / "smoke.duckdb"
    con = duckdb.connect(str(db_path))
    _create_minimal_tables(con)
    con.execute("""
        INSERT INTO aws_li_catalog VALUES
            ('smoke_1', 'us-east-1', true, 12.34, 'Smoke Test Service', 'misc')
    """)
    con.close()

    res = subprocess.run(
        [sys.executable, SCRIPT_PATH, str(db_path)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0

    con = duckdb.connect(str(db_path))
    inserted = con.execute("SELECT * FROM aws_li_to_gcp_li WHERE aws_li_key = 'smoke_1'").fetchone()
    con.close()

    assert inserted is not None
    assert len(inserted) == 14
    # Verify values
    aws_li_key, gcp_svc, sku_id, sku_nm, sku_u, strat, mult, reg, note, conf, wl, bd, pt_reason, decided_by = inserted
    assert aws_li_key == "smoke_1"
    assert gcp_svc == "Manual Review — Unmapped"
    assert sku_id is None
    assert sku_nm is None
    assert sku_u is None
    assert strat == "passthrough"
    assert mult == 1.0
    assert reg == "us-east-1"
    assert efc.NOTE in note
    assert conf == 0.10
    assert wl is True
    assert bd is False
    assert pt_reason == "llm_dropped"
    assert decided_by == "ensure_full_coverage.py"
