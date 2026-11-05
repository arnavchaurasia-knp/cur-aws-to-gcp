#!/usr/bin/env python3
"""
tests/test_directive_translator.py — Unit tests for directive_translator.py and refinement_thread.py.
"""
import json
import os
import shutil
import tempfile
import duckdb
import pytest

from scripts.directives import Directive, load_ledger
from scripts.directive_translator import (
    translate_utterance,
    detect_phase3_intent,
    compute_scope_estimate,
    validate_proposed_directive,
)
from scripts.refinement_thread import (
    load_thread,
    save_thread,
    add_user_message,
    add_agy_response,
    answer_clarification,
    confirm_directive,
    revoke_directive,
)

CORPUS_DIR = os.path.join(os.path.dirname(__file__), "corpus")


@pytest.fixture
def mock_job_env(tmp_path):
    job_dir = tmp_path / "job_refine"
    audit_dir = job_dir / "projection-audit"
    audit_dir.mkdir(parents=True)
    db_path = audit_dir / "projection.duckdb"

    con = duckdb.connect(str(db_path))
    con.execute("""
        CREATE TABLE aws_li_catalog (
            aws_li_key VARCHAR PRIMARY KEY,
            product VARCHAR,
            product_code VARCHAR,
            canonical_service VARCHAR,
            usage_type VARCHAR,
            operation VARCHAR,
            aws_region VARCHAR,
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
            gcp_projected_cost DOUBLE
        );
    """)

    con.execute("""
        INSERT INTO aws_li_catalog VALUES
        ('k_ec2_1', 'Amazon Elastic Compute Cloud', 'AmazonEC2', 'ec2', 'BoxUsage:m5.large', 'RunInstances', 'us-east-1', '111', '{}', 100.0),
        ('k_ec2_2', 'Amazon Elastic Compute Cloud', 'AmazonEC2', 'ec2', 'BoxUsage:m5.xlarge', 'RunInstances', 'us-east-1', '111', '{}', 200.0),
        ('k_s3_1',  'Amazon Simple Storage Service', 'AmazonS3', 's3', 'TimedStorage-ByteHrs', 'StandardStorage', 'us-east-1', '111', '{}', 50.0);
    """)
    con.execute("""
        INSERT INTO aws_li_to_gcp_li VALUES
        ('k_ec2_1', 'Compute Engine', 'map', TRUE, ''),
        ('k_ec2_2', 'Compute Engine', 'map', TRUE, ''),
        ('k_s3_1',  'Cloud Storage', 'map', TRUE, '');
    """)
    con.execute("""
        INSERT INTO gcp_projection VALUES
        ('k_ec2_1', TRUE, 85.0),
        ('k_ec2_2', TRUE, 170.0),
        ('k_s3_1',  TRUE, 40.0);
    """)
    con.close()
    return str(job_dir)


def test_clean_utterance_proposed_directive(mock_job_env):
    """A clean utterance generates a valid proposed directive with scope estimate."""
    res = translate_utterance("suppress ec2", mock_job_env)
    assert len(res["proposed_directives"]) == 1
    d = res["proposed_directives"][0]
    assert d["status"] == "proposed"
    assert d["author"] == "agy"
    assert d["action"]["type"] == "set_strategy"
    assert d["action"]["value"] == "ignore"
    assert d["scope_estimate"]["rows"] == 2
    assert d["scope_estimate"]["aws_spend"] == 300.0


def test_phase3_preference_verbs(mock_job_env):
    """Verify Phase-3 preference verbs round-trip from NL to proposed directives."""
    # 1. set_target_family
    res_fam = translate_utterance("use N2D for these ARM boxes", mock_job_env)
    assert len(res_fam["proposed_directives"]) == 1
    d_fam = res_fam["proposed_directives"][0]
    assert d_fam["action"]["type"] == "set_target_family"
    assert d_fam["action"]["value"] == "N2D"
    assert d_fam["selector"]["arch"] == "arm64"

    # 2. set_target_shape
    res_shape = translate_utterance("custom 4 vcpu 16 gb", mock_job_env)
    assert len(res_shape["proposed_directives"]) == 1
    d_shape = res_shape["proposed_directives"][0]
    assert d_shape["action"]["type"] == "set_target_shape"
    assert d_shape["action"]["value"] == {"vcpu": 4, "ram_gb": 16.0}

    # 3. set_region
    res_reg = translate_utterance("price these in europe-west1", mock_job_env)
    assert len(res_reg["proposed_directives"]) == 1
    d_reg = res_reg["proposed_directives"][0]
    assert d_reg["action"]["type"] == "set_region"
    assert d_reg["action"]["value"] == "europe-west1"

    # 4. set_pricing_basis
    res_cud = translate_utterance("use 3-yr CUD", mock_job_env)
    assert len(res_cud["proposed_directives"]) == 1
    d_cud = res_cud["proposed_directives"][0]
    assert d_cud["action"]["type"] == "set_pricing_basis"
    assert d_cud["action"]["value"] == "cud_3yr"


def test_phase3_validation_clarifications():
    """Verify validation triggers appropriate clarifications for invalid Phase 3 directives."""
    # 1. Custom shape with odd vCPU
    d_odd = Directive(
        id="d_odd",
        author="agy",
        selector={"service": "ec2"},
        action={"type": "set_target_shape", "value": {"vcpu": 3, "ram_gb": 8.0}},
        status="proposed"
    )
    ok_odd, clarif_odd = validate_proposed_directive(d_odd)
    assert not ok_odd
    assert clarif_odd["reason"] == "custom_shape_out_of_constraint"

    # 2. Invalid pricing basis
    d_pb = Directive(
        id="d_pb",
        author="agy",
        selector={"service": "ec2"},
        action={"type": "set_pricing_basis", "value": "unlimited_free"},
        status="proposed"
    )
    ok_pb, clarif_pb = validate_proposed_directive(d_pb)
    assert not ok_pb
    assert clarif_pb["reason"] == "invalid_pricing_basis"


def test_mock_llm_out_of_schema_rejected(mock_job_env):
    """An out-of-schema reply from the model is rejected with a clarification, nothing applied."""
    def bad_llm(prompt):
        return "I think we should just set price to zero without valid schema"

    res = translate_utterance("make it cheaper", mock_job_env, llm_func=bad_llm)
    assert len(res["proposed_directives"]) == 0
    assert len(res["clarifications"]) == 1
    assert res["clarifications"][0]["blocking"] is True


def test_invalid_target_service_clarification(mock_job_env):
    """An invalid/unsupported GCP target service yields a clarification and remains proposed."""
    d = Directive(
        id="d_test",
        author="agy",
        source_utterance="map to NonExistentService",
        selector={"service": "ec2"},
        action={"type": "set_target_service", "value": "NonExistentService"},
        status="proposed"
    )
    ok, clarif = validate_proposed_directive(d)
    assert not ok
    assert clarif["reason"] == "invalid_target_service"
    assert "options" in clarif
    assert len(clarif["options"]) > 0


def test_refinement_thread_lifecycle(mock_job_env):
    """Test follow-up question, answering clarification, confirmation, and revocation."""
    # 1. Under-specified utterance
    res = translate_utterance("something weird with no action", mock_job_env)
    assert len(res["clarifications"]) == 1
    clarif = res["clarifications"][0]
    assert clarif["reason"] == "under_specified"

    # Add to thread
    add_user_message(mock_job_env, "something weird with no action")
    add_agy_response(mock_job_env, "Could you clarify?", clarifications=[clarif])

    t = load_thread(mock_job_env)
    assert len(t["open_clarifications"]) == 1

    # 2. Add an invalid target directive to thread
    bad_dir = Directive(
        id="d_inv",
        author="agy",
        source_utterance="map s3 to FancyCloud",
        selector={"service": "s3"},
        action={"type": "set_target_service", "value": "FancyCloud"},
        status="proposed"
    )
    _, c_inv = validate_proposed_directive(bad_dir)
    c_inv["id"] = "clarif_inv_1"
    c_inv["directive_ref"] = "d_inv"

    add_agy_response(mock_job_env, "Invalid target", proposed_directives=[bad_dir.to_dict()], clarifications=[c_inv])

    # 3. Answer clarification with valid service
    ok, msg, updated_d = answer_clarification(mock_job_env, "clarif_inv_1", "Cloud Storage")
    assert ok
    assert updated_d["status"] == "confirmed"
    assert updated_d["action"]["value"] == "Cloud Storage"

    # Assert written to directives.json ledger
    ledger = load_ledger(mock_job_env)
    assert any(d.id == "d_inv" and d.status == "confirmed" for d in ledger)

    # 4. Revoke directive
    rev_ok, rev_msg, rev_d = revoke_directive(mock_job_env, "d_inv")
    assert rev_ok
    assert rev_d["status"] == "superseded"

    # Ledger updated
    ledger2 = load_ledger(mock_job_env)
    d_after = next(d for d in ledger2 if d.id == "d_inv")
    assert d_after.status == "superseded"


def test_scope_estimate_corpus_job():
    """Verify scope_estimate against known corpus job standard_workload."""
    corpus_job = os.path.join(CORPUS_DIR, "standard_workload")
    if not os.path.exists(corpus_job):
        pytest.skip("standard_workload corpus job not found")

    d = Directive("d_ec2", "user", {"service": "ec2"}, {"type": "set_strategy", "value": "ignore"})
    est = compute_scope_estimate(corpus_job, d)
    assert est["rows"] == 2
    assert est["aws_spend"] == 93.31
