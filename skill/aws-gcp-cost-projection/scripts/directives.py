#!/usr/bin/env python3
"""
directives.py — Schema definition and ledger I/O for the Directive Layer.

Single source of truth for conversational / refinement directives.
Unifies review fixes, outlier fixes, commitment ignores, assumption overrides,
and architecture disclosures into an event-sourced, append-only ledger.
"""
from __future__ import annotations

import copy
import dataclasses
from datetime import datetime, timezone
import json
import os
import shutil
import tempfile
from typing import Any, Dict, List, Optional, Union

PHASE1_ACTION_VERBS = {
    "set_target_service",
    "set_strategy",
    "set_is_workload",
    "set_assumption",
    "annotate",
    "set_field",
}
PHASE3_ACTION_VERBS = {
    "set_target_family",
    "set_target_shape",
    "set_region",
    "set_pricing_basis",
}
ALLOWED_ACTION_VERBS = PHASE1_ACTION_VERBS | PHASE3_ACTION_VERBS

ALLOWED_STRATEGIES = {"map", "passthrough", "ignore", "break_down"}
ALLOWED_STATUSES = {"proposed", "confirmed", "applied", "superseded"}

SCHEMA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "directive_schema.json"
)


@dataclasses.dataclass
class Directive:
    id: str
    author: str
    selector: Dict[str, Any]
    action: Dict[str, Any]
    created_at: str = dataclasses.field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source_utterance: str = ""
    scope_estimate: Optional[Dict[str, Any]] = None
    status: str = "applied"
    confidence: float = 1.0
    needs_clarification: Optional[Dict[str, Any]] = None
    applied_provenance: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "author": self.author,
            "source_utterance": self.source_utterance,
            "selector": copy.deepcopy(self.selector),
            "action": copy.deepcopy(self.action),
            "scope_estimate": copy.deepcopy(self.scope_estimate) if self.scope_estimate is not None else None,
            "status": self.status,
            "confidence": self.confidence,
            "needs_clarification": copy.deepcopy(self.needs_clarification) if self.needs_clarification is not None else None,
            "applied_provenance": copy.deepcopy(self.applied_provenance) if self.applied_provenance is not None else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Directive:
        return cls(
            id=str(data.get("id", "")),
            author=str(data.get("author", "user")),
            selector=dict(data.get("selector") or {}),
            action=dict(data.get("action") or {}),
            created_at=str(data.get("created_at") or datetime.now(timezone.utc).isoformat()),
            source_utterance=str(data.get("source_utterance") or ""),
            scope_estimate=data.get("scope_estimate"),
            status=str(data.get("status") or "applied"),
            confidence=float(data.get("confidence", 1.0)),
            needs_clarification=data.get("needs_clarification"),
            applied_provenance=data.get("applied_provenance"),
        )


def validate_directive(d: Union[Directive, Dict[str, Any]], raise_exc: bool = False) -> tuple[bool, Optional[str]]:
    """Validate a directive against the Phase 1 schema rules."""
    raw = d.to_dict() if hasattr(d, "to_dict") else d

    if not isinstance(raw, dict):
        msg = f"Directive must be a dict or Directive instance, got {type(raw).__name__}"
        if raise_exc:
            raise ValueError(msg)
        return False, msg

    for req in ("id", "author", "selector", "action"):
        if req not in raw or raw[req] is None or (isinstance(raw[req], str) and not raw[req].strip()):
            msg = f"Missing or empty required field: '{req}'"
            if raise_exc:
                raise ValueError(msg)
            return False, msg

    if not isinstance(raw["selector"], dict):
        msg = f"'selector' must be a dict, got {type(raw['selector']).__name__}"
        if raise_exc:
            raise ValueError(msg)
        return False, msg

    action = raw["action"]
    if not isinstance(action, dict) or "type" not in action:
        msg = "'action' must be a dict with a 'type' field"
        if raise_exc:
            raise ValueError(msg)
        return False, msg

    act_type = action.get("type")
    if act_type not in ALLOWED_ACTION_VERBS:
        msg = f"Unknown or unsupported action.type: '{act_type}'. Allowed: {sorted(ALLOWED_ACTION_VERBS)}"
        if raise_exc:
            raise ValueError(msg)
        return False, msg

    # Validate specific verbs
    if act_type == "set_strategy":
        val = action.get("value")
        if val not in ALLOWED_STRATEGIES:
            msg = f"set_strategy value '{val}' invalid. Must be one of {sorted(ALLOWED_STRATEGIES)}"
            if raise_exc:
                raise ValueError(msg)
            return False, msg

    elif act_type == "set_is_workload":
        val = action.get("value")
        if not isinstance(val, bool):
            msg = f"set_is_workload value must be a boolean, got {type(val).__name__}"
            if raise_exc:
                raise ValueError(msg)
            return False, msg

    elif act_type == "set_target_service":
        val = action.get("value")
        if not val or not isinstance(val, str):
            msg = f"set_target_service value must be a non-empty string, got {val!r}"
            if raise_exc:
                raise ValueError(msg)
            return False, msg

    elif act_type == "set_assumption":
        # Can have key/value in action or in params
        has_key = "key" in action or (isinstance(action.get("params"), dict) and "key" in action["params"])
        if not has_key and not action.get("value"):
            msg = "set_assumption requires a key and value (or params with key/value)"
            if raise_exc:
                raise ValueError(msg)
            return False, msg

    elif act_type == "set_target_family":
        val = action.get("value") or action.get("target_family")
        if not val or not isinstance(val, str):
            msg = f"set_target_family value must be a non-empty string, got {val!r}"
            if raise_exc:
                raise ValueError(msg)
            return False, msg

    elif act_type == "set_target_shape":
        val = action.get("value") if action.get("value") is not None else action.get("shape")
        if not val or not isinstance(val, (str, dict)):
            msg = f"set_target_shape value must be a string or dict, got {val!r}"
            if raise_exc:
                raise ValueError(msg)
            return False, msg

    elif act_type == "set_region":
        val = action.get("value") or action.get("region")
        if not val or not isinstance(val, str):
            msg = f"set_region value must be a non-empty string, got {val!r}"
            if raise_exc:
                raise ValueError(msg)
            return False, msg

    elif act_type == "set_pricing_basis":
        val = action.get("value") or action.get("pricing_basis")
        if not val or not isinstance(val, str):
            msg = f"set_pricing_basis value must be a non-empty string, got {val!r}"
            if raise_exc:
                raise ValueError(msg)
            return False, msg

    status = raw.get("status", "applied")
    if status not in ALLOWED_STATUSES:
        msg = f"Invalid status '{status}'. Must be one of {sorted(ALLOWED_STATUSES)}"
        if raise_exc:
            raise ValueError(msg)
        return False, msg

    return True, None


def load_ledger(job_dir: str) -> List[Directive]:
    """
    Read <job_dir>/directives.json (ordered, append-only).
    Returns [] when absent or corrupted — never raises (mirrors load_assumptions).
    """
    if not job_dir:
        return []
    path = os.path.join(job_dir, "directives.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        res: List[Directive] = []
        for item in data:
            if isinstance(item, dict):
                valid, _ = validate_directive(item)
                if valid:
                    res.append(Directive.from_dict(item))
        return res
    except Exception:
        return []


def save_ledger(job_dir: str, directives: List[Union[Directive, Dict[str, Any]]]) -> None:
    """
    Atomically write directives to <job_dir>/directives.json.
    Writes <job_dir>/directives.baseline.json on first write if it does not already exist.
    """
    if not job_dir:
        return
    os.makedirs(job_dir, exist_ok=True)
    target_path = os.path.join(job_dir, "directives.json")
    baseline_path = os.path.join(job_dir, "directives.baseline.json")

    # Serialize
    payload: List[Dict[str, Any]] = []
    for d in directives:
        if isinstance(d, Directive):
            payload.append(d.to_dict())
        elif isinstance(d, dict):
            payload.append(d)

    # First write baseline capture
    if not os.path.exists(baseline_path):
        temp_base = None
        try:
            with tempfile.NamedTemporaryFile("w", dir=job_dir, delete=False, encoding="utf-8") as tf:
                temp_base = tf.name
                json.dump(payload, tf, indent=2)
            os.replace(temp_base, baseline_path)
        except Exception:
            if temp_base and os.path.exists(temp_base):
                os.remove(temp_base)
            raise

    # Atomic write of target_path (write to temp file in same directory, then rename)
    temp_target = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=job_dir, delete=False, encoding="utf-8") as tf:
            temp_target = tf.name
            json.dump(payload, tf, indent=2)
        os.replace(temp_target, target_path)
    except Exception:
        if temp_target and os.path.exists(temp_target):
            os.remove(temp_target)
        raise

