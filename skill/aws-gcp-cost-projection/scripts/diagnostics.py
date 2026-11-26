#!/usr/bin/env python3
"""Internal diagnostics channel for the projection pipeline.

Scripts that degrade the projection — a mapper that raised, rows left unmapped,
a check that could not be evaluated, a clamped outlier — record it here so the
engineering team can see afterwards what happened and what it cost.

These records are INTERNAL. They stay in the job directory next to agy.log and
debug.log. They are never read by render_report.py and never reach a customer.

Nothing in this module raises and nothing exits: a diagnostics problem must
never affect a run.

Usage:
    from diagnostics import diag, ERROR, WARN
    diag(ERROR, "mapper_error", "apply_static_mappings.py",
         "compute_breakdown mapper raised KeyError; 412 rows carried at AWS cost",
         rows=412, usd=48213.55, phase=2,
         detail={"mechanic_group": "compute_breakdown"})
"""
import json
import os
import time

ERROR = "error"
WARN = "warn"
INFO = "info"

_VALID = (ERROR, WARN, INFO)


def _path(job_dir=None):
    base = job_dir or os.environ.get("JOB_DIR") or os.getcwd()
    audit = os.path.join(base, "projection-audit")
    try:
        os.makedirs(audit, exist_ok=True)
    except Exception:
        return None
    return os.path.join(audit, "diagnostics.jsonl")


def diag(level, code, source, message, rows=0, usd=0.0, phase=None,
         detail=None, job_dir=None):
    """Append one diagnostic record. Never raises."""
    try:
        if level not in _VALID:
            level = WARN
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "level": level,
            "code": str(code),
            "source": str(source),
            "phase": phase,
            "message": str(message)[:2000],
            "rows": int(rows or 0),
            "usd": round(float(usd or 0.0), 2),
            "detail": detail or {},
        }
        print("[diag] {level} {code} src={source} rows={rows} usd=${usd:,.2f} :: {message}"
              .format(**rec), flush=True)
        p = _path(job_dir)
        if p:
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str) + "\n")
    except Exception:
        pass


def read_all(job_dir=None):
    """Return every diagnostic record. Never raises; returns [] on any problem."""
    out = []
    try:
        p = _path(job_dir)
        if not p or not os.path.exists(p):
            return out
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        pass
    return out


def gate_repaired(gate, phase, violations=0, source="orchestrator", job_dir=None):
    """Emit a structured diagnostic when a gate is resolved deterministically."""
    diag(INFO, "gate_repaired_deterministically", source,
         f"gate '{gate}' repaired deterministically ({violations} violations resolved)",
         rows=violations, phase=phase, detail={"gate": str(gate), "initial_violations": violations}, job_dir=job_dir)


def gate_escalated(gate, phase, residual=0, initial=0, source="orchestrator", job_dir=None):
    """Emit a structured diagnostic when an unresolved gate escalates to an LLM retry."""
    diag(WARN, "gate_escalated_to_llm", source,
         f"gate '{gate}' residual {residual} violation(s) — escalating to LLM retry",
         rows=residual, phase=phase, detail={"gate": str(gate), "residual_violations": residual, "initial_violations": initial}, job_dir=job_dir)


def idle_wait(phase, seconds, source="orchestrator", job_dir=None):
    """Emit a structured diagnostic recording idle or elapsed execution seconds."""
    diag(INFO, "phase_idle_wait_seconds", source,
         f"phase '{phase}' elapsed {seconds}s",
         rows=0, phase=phase, detail={"phase": str(phase), "seconds": seconds}, job_dir=job_dir)

