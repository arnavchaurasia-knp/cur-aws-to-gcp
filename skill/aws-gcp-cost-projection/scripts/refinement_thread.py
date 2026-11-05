#!/usr/bin/env python3
"""
refinement_thread.py — State management for conversational refinement threads.

Persists conversation messages, pending clarifications, and proposed/confirmed
directives in <job>/refinement_thread.json, allowing interactive follow-ups and resuming.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import sys
import uuid
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from directives import Directive, load_ledger, save_ledger


def thread_path(job_dir: str) -> str:
    return os.path.join(job_dir, "refinement_thread.json")


def load_thread(job_dir: str) -> Dict[str, Any]:
    """Load or initialize refinement_thread.json for a job."""
    path = thread_path(job_dir)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception as exc:
            print(f"refinement_thread: error reading {path}: {exc}", file=sys.stderr)

    return {
        "job_id": os.path.basename(os.path.abspath(job_dir)),
        "messages": [],
        "open_clarifications": [],
        "proposed_directives": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def save_thread(job_dir: str, thread: Dict[str, Any]) -> None:
    """Save refinement thread state atomically."""
    thread["updated_at"] = datetime.now(timezone.utc).isoformat()
    path = thread_path(job_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = f"{path}.tmp.{uuid.uuid4().hex[:6]}"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(thread, f, indent=2)
        f.write("\n")
    os.replace(temp_path, path)


def add_user_message(job_dir: str, content: str) -> Dict[str, Any]:
    """Append a user message to the thread."""
    thread = load_thread(job_dir)
    thread["messages"].append({
        "id": f"msg_{uuid.uuid4().hex[:8]}",
        "role": "user",
        "content": content,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    save_thread(job_dir, thread)
    return thread


def add_agy_response(
    job_dir: str,
    content: str,
    proposed_directives: Optional[List[Dict[str, Any]]] = None,
    clarifications: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """Append an AGY assistant response, proposed directives, and any open clarifications."""
    thread = load_thread(job_dir)
    thread["messages"].append({
        "id": f"msg_{uuid.uuid4().hex[:8]}",
        "role": "agy",
        "content": content,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    if proposed_directives:
        existing_ids = {d.get("id") for d in thread.get("proposed_directives", [])}
        for pd in proposed_directives:
            if pd.get("id") not in existing_ids:
                thread.setdefault("proposed_directives", []).append(pd)

    if clarifications:
        existing_clarif_ids = {c.get("id") for c in thread.get("open_clarifications", [])}
        for cl in clarifications:
            if cl.get("id") not in existing_clarif_ids:
                thread.setdefault("open_clarifications", []).append(cl)

    save_thread(job_dir, thread)
    return thread


def confirm_directive(job_dir: str, directive_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Confirm a proposed directive, flipping status to 'confirmed' and adding to directives.json.
    """
    thread = load_thread(job_dir)
    props = thread.get("proposed_directives", [])
    target = next((d for d in props if d.get("id") == directive_id), None)
    if not target:
        return False, f"Directive '{directive_id}' not found in proposed directives", None

    # Check if there are blocking open clarifications on this directive
    blocking = [c for c in thread.get("open_clarifications", []) if c.get("directive_ref") == directive_id and c.get("blocking", True)]
    if blocking:
        return False, f"Cannot confirm directive '{directive_id}': has {len(blocking)} blocking clarification(s)", None

    target["status"] = "confirmed"

    # Save to directives.json ledger
    ledger = load_ledger(job_dir)
    existing_idx = next((i for i, d in enumerate(ledger) if d.id == directive_id), None)
    d_obj = Directive.from_dict(target)
    if existing_idx is not None:
        ledger[existing_idx] = d_obj
    else:
        ledger.append(d_obj)
    save_ledger(job_dir, ledger)

    save_thread(job_dir, thread)
    return True, "Directive confirmed", target


def answer_clarification(
    job_dir: str,
    clarification_id: str,
    answer: str
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Resolve an open clarification with the user's answer, filling the directive parameter
    and flipping the directive proposed -> confirmed.
    """
    thread = load_thread(job_dir)
    open_c = thread.get("open_clarifications", [])
    clarif = next((c for c in open_c if c.get("id") == clarification_id), None)
    if not clarif:
        return False, f"Clarification '{clarification_id}' not found", None

    directive_ref = clarif.get("directive_ref")
    props = thread.get("proposed_directives", [])
    directive_dict = next((d for d in props if d.get("id") == directive_ref), None)

    reason = clarif.get("reason", "")
    answer_clean = answer.strip()

    if directive_dict is not None:
        act = directive_dict.get("action", {})
        if reason == "invalid_target_service" or reason == "unpriced_target":
            act["value"] = answer_clean
        elif reason == "missing_region":
            params = act.setdefault("params", {})
            params["region"] = answer_clean
        elif reason == "under_specified":
            if answer_clean.lower() in ("ignore", "suppress", "drop"):
                act["type"] = "set_strategy"
                act["value"] = "ignore"
            elif answer_clean.lower() == "passthrough":
                act["type"] = "set_strategy"
                act["value"] = "passthrough"
            elif "map to" in answer_clean.lower():
                act["type"] = "set_target_service"
                act["value"] = answer_clean.split("map to", 1)[1].strip()

        # Directive is now confirmed
        directive_dict["needs_clarification"] = None
        directive_dict["status"] = "confirmed"

        # Sync to ledger
        ledger = load_ledger(job_dir)
        d_obj = Directive.from_dict(directive_dict)
        existing_idx = next((i for i, d in enumerate(ledger) if d.id == directive_ref), None)
        if existing_idx is not None:
            ledger[existing_idx] = d_obj
        else:
            ledger.append(d_obj)
        save_ledger(job_dir, ledger)

    # Remove from open clarifications
    thread["open_clarifications"] = [c for c in open_c if c.get("id") != clarification_id]

    # Record message in thread
    thread["messages"].append({
        "id": f"msg_{uuid.uuid4().hex[:8]}",
        "role": "user",
        "content": f"[Answer to clarification {clarification_id}]: {answer_clean}",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    save_thread(job_dir, thread)
    return True, "Clarification answered and directive confirmed", directive_dict


def revoke_directive(job_dir: str, directive_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Mark a directive as 'superseded' in directives.json and the thread, allowing rerun to restore baseline.
    """
    ledger = load_ledger(job_dir)
    target_d = next((d for d in ledger if d.id == directive_id), None)
    if not target_d:
        return False, f"Directive '{directive_id}' not found in ledger", None

    target_d.status = "superseded"
    save_ledger(job_dir, ledger)

    thread = load_thread(job_dir)
    for pd in thread.get("proposed_directives", []):
        if pd.get("id") == directive_id:
            pd["status"] = "superseded"

    thread["messages"].append({
        "id": f"msg_{uuid.uuid4().hex[:8]}",
        "role": "user",
        "content": f"Revoked directive {directive_id}",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    save_thread(job_dir, thread)
    return True, f"Directive '{directive_id}' marked as superseded", target_d.to_dict()


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <job_dir> <confirm|clarify|revoke|get> [args...]", file=sys.stderr)
        return 1

    job_dir = sys.argv[1]
    cmd = sys.argv[2].lower()

    if cmd == "confirm" and len(sys.argv) >= 4:
        ok, msg, d = confirm_directive(job_dir, sys.argv[3])
        print(json.dumps({"ok": ok, "message": msg, "directive": d}))
    elif cmd == "clarify" and len(sys.argv) >= 5:
        ok, msg, d = answer_clarification(job_dir, sys.argv[3], sys.argv[4])
        print(json.dumps({"ok": ok, "message": msg, "directive": d}))
    elif cmd == "revoke" and len(sys.argv) >= 4:
        ok, msg, d = revoke_directive(job_dir, sys.argv[3])
        print(json.dumps({"ok": ok, "message": msg, "directive": d}))
    elif cmd == "get":
        print(json.dumps(load_thread(job_dir)))
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
