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

