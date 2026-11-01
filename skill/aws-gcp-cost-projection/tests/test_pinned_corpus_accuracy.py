#!/usr/bin/env python3
"""tests/test_pinned_corpus_accuracy.py — CI assertions for the pinned benchmark corpus."""

import json
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from accuracy_report import compute_scorecard, compute_portfolio_scorecard, check_thresholds

CORPUS_DIR = os.path.join(os.path.dirname(__file__), "corpus")


@pytest.fixture(scope="module")
def corpus_jobs():
    """Ensure pinned corpus exists and return individual scorecards."""
    jobs = ["enterprise_high_sp", "standard_workload", "modern_serverless"]
    scorecards = {}
    for j in jobs:
        j_dir = os.path.join(CORPUS_DIR, j)
        if not os.path.exists(j_dir):
            import build_pinned_corpus
            build_pinned_corpus.main()
            break
    for j in jobs:
        j_dir = os.path.join(CORPUS_DIR, j)
        sc = compute_scorecard(j_dir)
        assert sc["status"] == "ok", f"Job {j} failed to load: {sc.get('error')}"
        scorecards[j] = sc
    return scorecards


def test_enterprise_high_sp_accuracy(corpus_jobs):
    sc = corpus_jobs["enterprise_high_sp"]
    # 100% deterministic coverage (no LLM, no unmapped)
    assert sc["coverage_mix"]["deterministic"]["pct_of_spend"] >= 95.0
    # Spec gap spend must be $0
    assert sc["determinism_holes"]["spec_gap"]["total_usd"] == 0.0
    # 100% public_od basis
    assert sc["od_basis_mix"]["public_od"]["pct_of_spend"] >= 95.0
    # Threshold check passes
    passed, checks = check_thresholds(sc, min_deterministic_cov=90.0, max_spec_gap_pct=1.0)
    assert passed is True


def test_standard_workload_accuracy(corpus_jobs):
    sc = corpus_jobs["standard_workload"]
    assert sc["coverage_mix"]["deterministic"]["pct_of_spend"] >= 95.0
    assert sc["determinism_holes"]["spec_gap"]["total_usd"] == 0.0
    passed, checks = check_thresholds(sc, min_deterministic_cov=90.0, max_spec_gap_pct=1.0)
    assert passed is True


def test_modern_serverless_accuracy(corpus_jobs):
    sc = corpus_jobs["modern_serverless"]
    assert sc["coverage_mix"]["deterministic"]["pct_of_spend"] >= 95.0
    assert sc["determinism_holes"]["spec_gap"]["total_usd"] == 0.0
    passed, checks = check_thresholds(sc, min_deterministic_cov=90.0, max_spec_gap_pct=1.0)
    assert passed is True


def test_portfolio_rollup_and_thresholds(corpus_jobs):
    sc_list = list(corpus_jobs.values())
    portfolio = compute_portfolio_scorecard(sc_list)
    assert portfolio["status"] == "ok"
    assert portfolio["headline"]["workload_rows"] == 17
    assert portfolio["headline"]["aws_od_cost"] > 4000.0

    # Pinned accuracy KPIs:
    # Deterministic coverage must be >= 95%
    det_cov = portfolio["coverage_mix"]["deterministic"]["pct_of_spend"]
    assert det_cov >= 95.0, f"Deterministic coverage {det_cov}% fell below 95%"

    # Spec gap spend must be < 1%
    sg_spend = portfolio["determinism_holes"]["spec_gap"]["total_usd"]
    assert sg_spend == 0.0

    # No un-whitelisted ratio > 10x
    r10_rows = portfolio["ratio_distribution"]["over_10x"]["rows"]
    assert r10_rows == 0, f"Found {r10_rows} ratio > 10x rows in pinned corpus"

    # Evaluates cleanly through check_thresholds
    passed, checks = check_thresholds(portfolio, min_deterministic_cov=95.0, max_spec_gap_pct=1.0, max_ratio_10x_pct=0.0)
    assert passed is True

