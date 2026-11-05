#!/usr/bin/env python3
"""
tests/test_stale_pins_and_thresholds.py — Tests for blast-radius thresholds and stale-pin safety.
"""
from datetime import datetime, timezone
import json
import os
import shutil
import tempfile
import duckdb
import pytest

from scripts.directives import Directive, save_ledger, load_ledger
from scripts.refinement_thread import load_thread, answer_clarification
from scripts.directive_translator import should_auto_apply, load_directive_thresholds, translate_utterance
from scripts.check_stale_pins import check_job_stale_pins, check_directive_staleness


def test_thresholds_blast_radius():
    thresholds = {
        "auto_apply_max_rows": 10,
        "auto_apply_max_spend": 100.0,
        "high_confidence_min": 0.90,
    }

    # 1. 3-row / $40 directive -> under both thresholds, high confidence -> auto-applies
    d_small = Directive(
        id="d_small",
        author="user",
        selector={"service": "s3"},
        action={"type": "set_strategy", "value": "passthrough"},
        confidence=0.95,
        scope_estimate={"rows": 3, "aws_spend": 40.0},
    )
    assert should_auto_apply(d_small, thresholds) is True

    # 2. 400-row / $50k directive -> exceeds thresholds -> held for confirm
    d_large = Directive(
        id="d_large",
        author="user",
        selector={"service": "ec2"},
        action={"type": "set_strategy", "value": "passthrough"},
        confidence=0.95,
        scope_estimate={"rows": 400, "aws_spend": 50000.0},
    )
    assert should_auto_apply(d_large, thresholds) is False

    # 3. set_pricing_basis -> even if 1 row and $5 -> held for confirm (always requires confirm)
    d_cud = Directive(
        id="d_cud",
        author="user",
        selector={"service": "ec2"},
        action={"type": "set_pricing_basis", "value": "cud_3yr"},
        confidence=0.99,
        scope_estimate={"rows": 1, "aws_spend": 5.0},
    )
    assert should_auto_apply(d_cud, thresholds) is False

    # 4. Low confidence (< 0.90) -> held for confirm
    d_low_conf = Directive(
        id="d_low_conf",
        author="user",
        selector={"service": "s3"},
        action={"type": "set_strategy", "value": "passthrough"},
        confidence=0.75,
        scope_estimate={"rows": 2, "aws_spend": 10.0},
    )
    assert should_auto_apply(d_low_conf, thresholds) is False


def test_stale_pin_detection_and_safety():
    skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(skill_dir, "data")

    # In africa-south1, t2a is false in compute_family_availability.json
    d_stale = Directive(
        id="d_stale_arm",
        author="alice@company.com",
        selector={"service": "ec2", "region": "africa-south1"},
        action={"type": "set_target_family", "value": "T2A", "params": {"region": "africa-south1"}},
        confidence=1.0,
    )

    is_stale, info = check_directive_staleness(d_stale, data_dir)
    assert is_stale is True
    assert info is not None
    assert info["reason"] == "catalog_refresh_invalidated"
    assert "T2A isn't available in africa-south1 anymore" in info["question"]


def test_stale_pin_revalidation_job_flow():
    with tempfile.TemporaryDirectory() as tmpdir:
        job_dir = tmpdir
        audit_dir = os.path.join(job_dir, "projection-audit")
        os.makedirs(audit_dir, exist_ok=True)
        db_path = os.path.join(audit_dir, "projection.duckdb")

        con = duckdb.connect(db_path)
        con.execute("""
            CREATE TABLE aws_li_catalog (
                aws_li_key TEXT PRIMARY KEY,
                product TEXT,
                product_code TEXT,
                canonical_service TEXT,
                usage_type TEXT,
                operation TEXT,
                region TEXT
            );
            CREATE TABLE aws_li_to_gcp_li (
                aws_li_key TEXT PRIMARY KEY,
                gcp_service TEXT,
                strategy TEXT,
                gcp_sku_id TEXT,
                gcp_projected_cost DOUBLE,
                review_flags TEXT,
                projection_note TEXT
            );
            INSERT INTO aws_li_catalog VALUES
            ('k_arm', 'Amazon Elastic Compute Cloud', 'AmazonEC2', 'ec2', 'BoxUsage:c7g.large', 'RunInstances', 'africa-south1');
            INSERT INTO aws_li_to_gcp_li VALUES
            ('k_arm', 'Compute Engine', 'map', 'SKU-OLD-T2A', 123.45, NULL, 'initial note');
        """)
        con.close()

        # Directive pinned T2A in africa-south1 (where t2a is unavailable)
        d = Directive(
            id="d_t2a_africa",
            author="alice@company.com",
            selector={"service": "ec2", "region": "africa-south1"},
            action={"type": "set_target_family", "value": "T2A", "params": {"region": "africa-south1"}},
            status="confirmed",
        )
        save_ledger(job_dir, [d], user_id="alice@company.com")

        # Run stale pin revalidation pass
        reports = check_job_stale_pins(job_dir)
        assert len(reports) == 1
        assert reports[0]["directive_id"] == "d_t2a_africa"
        assert reports[0]["user_id"] == "alice@company.com"

        # Verify blocking clarification in user thread
        thread = load_thread(job_dir, user_id="alice@company.com")
        open_c = thread.get("open_clarifications", [])
        assert len(open_c) == 1
        assert open_c[0]["reason"] == "catalog_refresh_invalidated"
        assert open_c[0]["blocking"] is True
        assert open_c[0]["directive_ref"] == "d_t2a_africa"

        # Verify DuckDB: row is flagged, but cost is UNCHANGED!
        con = duckdb.connect(db_path, read_only=True)
        r = con.execute("SELECT review_flags, gcp_projected_cost FROM aws_li_to_gcp_li WHERE aws_li_key = 'k_arm'").fetchone()
        con.close()
        assert r[0] == "pinned target no longer offered — awaiting your choice"
        assert r[1] == 123.45  # Cost unchanged!

        # Now simulate user answering clarification: choose "keep current"
        ok, msg, d_ans = answer_clarification(job_dir, open_c[0]["id"], "keep current mapping", user_id="alice@company.com")
        assert ok is True
        updated_thread = load_thread(job_dir, user_id="alice@company.com")
        assert len(updated_thread.get("open_clarifications", [])) == 0
