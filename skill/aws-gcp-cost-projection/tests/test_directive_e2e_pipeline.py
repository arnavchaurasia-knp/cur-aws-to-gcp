#!/usr/bin/env python3
"""
tests/test_directive_e2e_pipeline.py — End-to-end verification of Directive Layer.

Exercises a real corpus bill through the Directive Layer pipeline with DIRECTIVE_LAYER=1,
injects preference verbs (set_target_family, set_region, set_pricing_basis),
asserts row re-rating, provenance stamps in HTML & CSV reports, and invariant guards.
"""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import duckdb
import pytest

from scripts.directives import Directive, save_ledger, load_ledger
from scripts.apply_directives import apply_directives_to_db, get_cud_mult

CORPUS_DIR = os.path.join(os.path.dirname(__file__), "corpus")
CORPUS_JOB = "standard_workload"


@pytest.mark.slow
def test_directive_e2e_preference_verbs_and_report_provenance():
    src_job = os.path.join(CORPUS_DIR, CORPUS_JOB)
    if not os.path.exists(src_job):
        pytest.skip(f"Corpus job not found at {src_job}")

    with tempfile.TemporaryDirectory() as tmpdir:
        # Copy corpus job to temp environment
        shutil.copytree(src_job, tmpdir, dirs_exist_ok=True)
        db_path = os.path.join(tmpdir, "projection-audit", "projection.duckdb")

        # Snapshot baseline costs
        con = duckdb.connect(db_path, read_only=True)
        orig_costs = dict(con.execute("SELECT aws_li_key, gcp_projected_cost FROM gcp_projection").fetchall())
        con.close()

        # Ingest user directives:
        # 1. set_target_family = "N2D" for ec2 Graviton/ARM rows
        # 2. set_region = "europe-west1" for S3 storage
        d_fam = Directive(
            id="dir_pref_fam_n2d",
            author="alice@company.com",
            source_utterance="Use N2D family for ec2 arm boxes",
            selector={"aws_li_keys": ["ec2_t4g_od"]},
            action={"type": "set_target_family", "value": "N2D"},
            status="confirmed",
            confidence=1.0,
        )
        d_reg = Directive(
            id="dir_pref_reg_eu",
            author="alice@company.com",
            source_utterance="Price S3 storage in europe-west1",
            selector={"aws_li_keys": ["s3_std"]},
            action={"type": "set_region", "value": "europe-west1", "params": {"region_policy": "exact"}},
            status="confirmed",
            confidence=1.0,
        )

        save_ledger(tmpdir, [d_fam, d_reg], user_id="alice@company.com")

        # Run pipeline with DIRECTIVE_LAYER=1
        env = dict(os.environ)
        env["DIRECTIVE_LAYER"] = "1"
        env["USER_ID"] = "alice@company.com"
        env["JOB_DIR"] = tmpdir
        scripts_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
        env["PYTHONPATH"] = scripts_dir
        env["SKILL_DIR"] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        # 1. Apply directives
        updated = apply_directives_to_db(db_path, job_dir=tmpdir, user_id="alice@company.com")
        assert updated >= 2

        # Verify DB state
        con = duckdb.connect(db_path, read_only=True)
        rows_raw = con.execute("SELECT aws_li_key, gcp_region, applied_directives FROM aws_li_to_gcp_li").fetchall()
        rows_map = {r[0]: (r[1], r[2]) for r in rows_raw}
        con.close()

        # s3_std region was updated to europe-west1
        assert rows_map["s3_std"][0] == "europe-west1"
        # Provenance stamped
        assert "dir_pref_reg_eu" in json.loads(rows_map["s3_std"][1] or "[]")
        assert "dir_pref_fam_n2d" in json.loads(rows_map["ec2_t4g_od"][1] or "[]")

        # 2. Render report.html
        render_script = os.path.join(scripts_dir, "render_report.py")
        subprocess.run([sys.executable, render_script], cwd=tmpdir, env=env, check=True)
        report_html_path = os.path.join(tmpdir, "projection-audit", "report.html")
        assert os.path.exists(report_html_path), "report.html must be generated"

        html_content = open(report_html_path, "r", encoding="utf-8").read()
        # HTML report shows per-row "Changed by your request" provenance
        assert "Changed by your request" in html_content
        assert "dir_pref_fam_n2d" in html_content
        assert "dir_pref_reg_eu" in html_content

        # 3. Export CSV report
        csv_path = os.path.join(tmpdir, "report_export.csv")
        export_script = os.path.join(scripts_dir, "export_report.py")
        subprocess.run([
            sys.executable, export_script,
            "--format", "csv",
            "--level", "line-item",
            "--out", csv_path,
            "--db", db_path,
        ], cwd=tmpdir, env=env, check=True)
        assert os.path.exists(csv_path), "report_export.csv must be generated"

        csv_content = open(csv_path, "r", encoding="utf-8").read()
        # CSV report shows per-row "Changed by your request" provenance
        assert "Changed by your request" in csv_content
        assert "dir_pref_fam_n2d" in csv_content
        assert "dir_pref_reg_eu" in csv_content


@pytest.mark.slow
def test_directive_e2e_pricing_basis_cud_3yr():
    src_job = os.path.join(CORPUS_DIR, CORPUS_JOB)
    if not os.path.exists(src_job):
        pytest.skip(f"Corpus job not found at {src_job}")

    with tempfile.TemporaryDirectory() as tmpdir:
        shutil.copytree(src_job, tmpdir, dirs_exist_ok=True)
        db_path = os.path.join(tmpdir, "projection-audit", "projection.duckdb")

        # Read initial costs
        con = duckdb.connect(db_path, read_only=True)
        init_rows = con.execute("SELECT aws_li_key, gcp_projected_cost FROM gcp_projection").fetchall()
        con.close()
        init_costs = {r[0]: float(r[1] or 0.0) for r in init_rows}

        # Apply set_pricing_basis = "cud_3yr" on ec2_m5_od
        d_cud = Directive(
            id="dir_cud_m5",
            author="alice@company.com",
            source_utterance="Use 3-year CUD for M5 instances",
            selector={"aws_li_keys": ["ec2_m5_od"]},
            action={"type": "set_pricing_basis", "value": "cud_3yr"},
            status="confirmed",
            confidence=1.0,
        )
        save_ledger(tmpdir, [d_cud], user_id="alice@company.com")

        # Apply directives
        apply_directives_to_db(db_path, job_dir=tmpdir, user_id="alice@company.com")

        # Verify post-application costs and flags
        con = duckdb.connect(db_path, read_only=True)
        post_rows = con.execute("SELECT aws_li_key, gcp_projected_cost FROM gcp_projection").fetchall()
        post_costs = {r[0]: float(r[1] or 0.0) for r in post_rows}
        post_map = dict(con.execute("SELECT aws_li_key, review_flags FROM aws_li_to_gcp_li").fetchall())
        con.close()

        # Untouched rows have identical costs
        for key, init_c in init_costs.items():
            if key != "ec2_m5_od":
                assert abs(post_costs[key] - init_c) < 1e-4, f"Untouched row {key} cost changed: {init_c} -> {post_costs[key]}"

        # CUD row dropped by configured multiplier
        mult = get_cud_mult("Compute Engine", "cud_3yr")
        expected_cud_cost = round(init_costs["ec2_m5_od"] * mult, 4)
        actual_cud_cost = round(post_costs["ec2_m5_od"], 4)
        assert abs(actual_cud_cost - expected_cud_cost) < 0.05, f"CUD cost mismatch: {actual_cud_cost} vs expected {expected_cud_cost}"

        # Carries review flag
        assert "cud_3yr" in (post_map.get("ec2_m5_od") or "")
