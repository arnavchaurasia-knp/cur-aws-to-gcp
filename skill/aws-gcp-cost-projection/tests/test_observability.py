#!/usr/bin/env python3
"""Tests for WO-5: Observability for gate repairs, LLM retries, and idle waits."""
import json
import os
import subprocess
import sys
import pytest

_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import diagnostics as diag
from run_diag_summary import main as summary_main


def test_diagnostics_event_emitters(tmp_path):
    """Verify structured event emitters record proper diagnostic format."""
    job_dir = str(tmp_path)
    audit_dir = os.path.join(job_dir, "projection-audit")
    os.makedirs(audit_dir, exist_ok=True)

    diag.gate_repaired("no_illegal_passthroughs", 3, violations=4, job_dir=job_dir)
    diag.gate_escalated("mapping_coverage", 2, residual=2, initial=5, job_dir=job_dir)
    diag.idle_wait(3, 45, job_dir=job_dir)

    records = diag.read_all(job_dir)
    assert len(records) == 3

    r1 = records[0]
    assert r1["code"] == "gate_repaired_deterministically"
    assert r1["level"] == "info"
    assert r1["phase"] == 3
    assert r1["rows"] == 4
    assert r1["detail"]["gate"] == "no_illegal_passthroughs"

    r2 = records[1]
    assert r2["code"] == "gate_escalated_to_llm"
    assert r2["level"] == "warn"
    assert r2["phase"] == 2
    assert r2["rows"] == 2
    assert r2["detail"]["residual_violations"] == 2

    r3 = records[2]
    assert r3["code"] == "phase_idle_wait_seconds"
    assert r3["level"] == "info"
    assert r3["detail"]["seconds"] == 45


def test_run_diag_summary_surfaces_contract_events(tmp_path, monkeypatch):
    """run_diag_summary aggregates contract observability events into run_diagnostics.json."""
    job_dir = str(tmp_path)
    audit_dir = os.path.join(job_dir, "projection-audit")
    os.makedirs(audit_dir, exist_ok=True)

    diag.gate_repaired("no_null_projected_cost", 4, violations=2, job_dir=job_dir)
    diag.gate_escalated("over_and_under_projection", 5, residual=1, initial=1, job_dir=job_dir)

    # Run summary
    monkeypatch.setattr(sys, "argv", ["run_diag_summary.py", job_dir])
    with pytest.raises(SystemExit) as exc:
        summary_main()
    assert exc.value.code == 0

    diag_json_path = os.path.join(job_dir, "run_diagnostics.json")
    assert os.path.exists(diag_json_path)

    with open(diag_json_path, "r", encoding="utf-8") as f:
        summary = json.load(f)

    assert summary["gate_repaired_count"] == 1
    assert summary["gate_retry_count"] == 1
    assert "gate_repaired_deterministically" in summary["by_code"]
    assert "gate_escalated_to_llm" in summary["by_code"]
    assert summary["by_code"]["gate_repaired_deterministically"]["count"] == 1
