import json
import os
import subprocess
import sys
import tempfile
import pytest
from diagnostics import diag, ERROR, WARN


def test_summary_totals():
    with tempfile.TemporaryDirectory() as td:
        diag(ERROR, "mapper_error", "test1.py", "err 1", rows=5, usd=100.0, job_dir=td)
        diag(ERROR, "check_not_evaluated", "orchestrator", "err 2", rows=0, usd=50.0, job_dir=td)
        diag(WARN, "storage_misroute_rerouted", "test2.py", "warn 1", rows=2, usd=25.0, job_dir=td)

        script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "run_diag_summary.py")
        res = subprocess.run([sys.executable, script, td], capture_output=True, text=True)
        assert res.returncode == 0

        summary_path = os.path.join(td, "run_diagnostics.json")
        assert os.path.exists(summary_path)
        with open(summary_path, "r", encoding="utf-8") as f:
            summary = json.load(f)

        assert summary["error_count"] == 2
        assert summary["warn_count"] == 1
        assert summary["error_usd"] == 150.0
        assert summary["checks_not_evaluated"] == 1
        assert "mapper_error" in summary["by_code"]


def test_summary_always_exits_zero():
    with tempfile.TemporaryDirectory() as td:
        script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "run_diag_summary.py")
        # Empty job dir with no diagnostics.jsonl
        res1 = subprocess.run([sys.executable, script, td], capture_output=True, text=True)
        assert res1.returncode == 0

        # Corrupt diagnostics.jsonl
        audit_dir = os.path.join(td, "projection-audit")
        os.makedirs(audit_dir, exist_ok=True)
        with open(os.path.join(audit_dir, "diagnostics.jsonl"), "w") as f:
            f.write("{this is not valid json\n\n\x00\xff")

        res2 = subprocess.run([sys.executable, script, td], capture_output=True, text=True)
        assert res2.returncode == 0
