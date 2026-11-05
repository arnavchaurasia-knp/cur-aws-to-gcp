#!/usr/bin/env python3
"""
tests/test_directive_parity.py — Parity gate for the Directive Layer.

Asserts that running the unified Directive Layer (apply_directives.py via
directives_from_legacy.py) produces row-for-row identical output compared
to the legacy apply mechanisms across the pinned corpus.
"""
import os
import shutil
import tempfile
import duckdb
import pytest

from scripts.apply_architecture_notes import apply as apply_arch_legacy, load_disclosures
from scripts.apply_directives import apply_directives_to_db

CORPUS_DIR = os.path.join(os.path.dirname(__file__), "corpus")
CORPUS_JOBS = ["enterprise_high_sp", "standard_workload", "modern_serverless"]


@pytest.mark.parametrize("job_name", CORPUS_JOBS)
def test_corpus_job_directive_parity(job_name):
    job_dir = os.path.join(CORPUS_DIR, job_name)
    orig_db = os.path.join(job_dir, "projection-audit", "projection.duckdb")
    if not os.path.exists(orig_db):
        pytest.skip(f"Corpus DB {orig_db} not found")

    with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
        db_legacy = os.path.join(tmp1, "projection.duckdb")
        db_directive = os.path.join(tmp2, "projection.duckdb")
        shutil.copyfile(orig_db, db_legacy)
        shutil.copyfile(orig_db, db_directive)

        # 1. Run legacy path
        con_leg = duckdb.connect(db_legacy)
        apply_arch_legacy(con_leg, load_disclosures())
        con_leg.close()

        # 2. Run directive path
        apply_directives_to_db(db_directive, job_dir=job_dir)

        # 3. Assert row-for-row parity on aws_li_to_gcp_li
        con_leg = duckdb.connect(db_legacy, read_only=True)
        con_dir = duckdb.connect(db_directive, read_only=True)

        cols = "aws_li_key, gcp_service, strategy, gcp_sku_id, projection_note"
        rows_leg = con_leg.execute(f"SELECT {cols} FROM aws_li_to_gcp_li ORDER BY aws_li_key").fetchall()
        rows_dir = con_dir.execute(f"SELECT {cols} FROM aws_li_to_gcp_li ORDER BY aws_li_key").fetchall()

        con_leg.close()
        con_dir.close()

        assert len(rows_leg) == len(rows_dir)
        for r_leg, r_dir in zip(rows_leg, rows_dir):
            assert r_leg[0] == r_dir[0], f"Key mismatch: {r_leg[0]} vs {r_dir[0]}"
            assert r_leg[1] == r_dir[1], f"gcp_service mismatch on {r_leg[0]}: {r_leg[1]} vs {r_dir[1]}"
            assert r_leg[2] == r_dir[2], f"strategy mismatch on {r_leg[0]}: {r_leg[2]} vs {r_dir[2]}"
            assert r_leg[3] == r_dir[3], f"gcp_sku_id mismatch on {r_leg[0]}: {r_leg[3]} vs {r_dir[3]}"
            assert r_leg[4] == r_dir[4], f"projection_note mismatch on {r_leg[0]}: {r_leg[4]} vs {r_dir[4]}"


def test_job_overrides_directive_parity(tmp_path):
    """Parity test: job_overrides.json produces identical outcomes via apply_directives as apply_overrides."""
    job_dir_leg = tmp_path / "legacy"
    job_dir_dir = tmp_path / "directive"
    job_dir_leg.mkdir()
    job_dir_dir.mkdir()

    for jd in (job_dir_leg, job_dir_dir):
        (jd / "projection-audit").mkdir()

    overrides = [
        {"row_key": "k_supp", "suppressed": True, "note": "Drop this"},
        {"row_key": "k_tgt", "suppressed": False, "gcp_target": "Cloud SQL", "override_rate": 42.5},
        {"row_key": "k_rate", "suppressed": False, "override_rate": 99.0},
    ]
    import json
    for jd in (job_dir_leg, job_dir_dir):
        with open(jd / "job_overrides.json", "w", encoding="utf-8") as f:
            json.dump(overrides, f)

    def init_db(db_path):
        con = duckdb.connect(str(db_path))
        con.execute("""
            CREATE TABLE aws_li_catalog (
                aws_li_key VARCHAR PRIMARY KEY,
                product VARCHAR,
                product_code VARCHAR,
                canonical_service VARCHAR,
                usage_type VARCHAR,
                operation VARCHAR,
                region VARCHAR,
                account_id VARCHAR,
                tags VARCHAR,
                aws_amortized_cost DOUBLE
            );
            CREATE TABLE aws_li_to_gcp_li (
                aws_li_key VARCHAR,
                gcp_service VARCHAR,
                strategy VARCHAR,
                is_workload BOOLEAN,
                projection_note VARCHAR
            );
            CREATE TABLE gcp_projection (
                aws_li_key VARCHAR,
                is_workload BOOLEAN,
                gcp_projected_cost DOUBLE,
                projection_note VARCHAR
            );
        """)
        for k, prod, svc, strat, cost in [
            ("k_supp", "AmazonEC2", "Compute Engine", "map", 100.0),
            ("k_tgt", "AmazonRDS", "Compute Engine", "map", 50.0),
            ("k_rate", "AmazonS3", "Cloud Storage", "map", 20.0),
        ]:
            con.execute("INSERT INTO aws_li_catalog VALUES (?, ?, 'code', 'svc', 'ut', 'op', 'us-east-1', '123', '{}', ?)", [k, prod, cost])
            con.execute("INSERT INTO aws_li_to_gcp_li VALUES (?, ?, ?, TRUE, '')", [k, svc, strat])
            con.execute("INSERT INTO gcp_projection VALUES (?, TRUE, ?, '')", [k, cost])
        con.close()

    db_leg = job_dir_leg / "projection-audit" / "projection.duckdb"
    db_dir = job_dir_dir / "projection-audit" / "projection.duckdb"
    init_db(db_leg)
    init_db(db_dir)

    # 1. Run legacy apply_overrides logic on db_leg
    con_l = duckdb.connect(str(db_leg))
    t_l = {r[0] for r in con_l.execute("SHOW TABLES").fetchall()}
    for ov in overrides:
        key = ov.get("row_key")
        if not key:
            continue
        suppressed = ov.get("suppressed", False)
        gcp_target = ov.get("gcp_target", "")
        rate = ov.get("override_rate", -1)
        if suppressed:
            if "gcp_projection" in t_l:
                con_l.execute("UPDATE gcp_projection SET is_workload = false, gcp_projected_cost = 0.0, projection_note = 'Suppressed via admin override' WHERE aws_li_key = ?", [key])
            if "aws_li_to_gcp_li" in t_l:
                con_l.execute("UPDATE aws_li_to_gcp_li SET strategy = 'ignore', projection_note = 'Suppressed via admin override' WHERE aws_li_key = ?", [key])
        else:
            if gcp_target and "aws_li_to_gcp_li" in t_l:
                con_l.execute("UPDATE aws_li_to_gcp_li SET gcp_service = ? WHERE aws_li_key = ?", [gcp_target, key])
            if rate is not None and rate >= 0 and "gcp_projection" in t_l:
                con_l.execute("UPDATE gcp_projection SET gcp_projected_cost = ? WHERE aws_li_key = ?", [float(rate), key])
    con_l.close()

    # 2. Run apply_directives_to_db on db_dir
    apply_directives_to_db(str(db_dir), job_dir=str(job_dir_dir))

    # 3. Assert parity
    con_leg = duckdb.connect(str(db_leg), read_only=True)
    con_dir = duckdb.connect(str(db_dir), read_only=True)

    rows_leg_m = con_leg.execute("SELECT aws_li_key, gcp_service, strategy FROM aws_li_to_gcp_li ORDER BY aws_li_key").fetchall()
    rows_dir_m = con_dir.execute("SELECT aws_li_key, gcp_service, strategy FROM aws_li_to_gcp_li ORDER BY aws_li_key").fetchall()

    rows_leg_p = con_leg.execute("SELECT aws_li_key, is_workload, gcp_projected_cost FROM gcp_projection ORDER BY aws_li_key").fetchall()
    rows_dir_p = con_dir.execute("SELECT aws_li_key, is_workload, gcp_projected_cost FROM gcp_projection ORDER BY aws_li_key").fetchall()

    con_leg.close()
    con_dir.close()

    assert rows_leg_m == rows_dir_m
    assert rows_leg_p == rows_dir_p


