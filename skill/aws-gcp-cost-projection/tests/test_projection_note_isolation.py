"""Guardrail: projection_note (the mapping-table "why") is write-isolated.

Complements tests/test_notes_are_output_only.py (which ratchets down every place
the note is *read* for logic). These two tests add the other half of the
contract:

  * only render_notes.py may *write* the mapping-table note, and
  * the registry declares the note derived / output-only / not-logic.

Together they keep the "why" an isolated, derived view: composed from structured
decision state and never fed back into a mapping decision. See
data/row_state_registry.json (projection_note: kind="derived",
writable_by=["render_notes"], readable_as_logic=false).
"""
import glob
import os
import re

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
DATA_DIR = os.path.join(os.path.dirname(SCRIPTS_DIR), "data")

# render_notes.py is the sole legitimate writer of the mapping-table note.
_NOTE_WRITER = {"render_notes.py"}

# SQL idioms that mutate the mapping-table note in place. Scoped to SQL forms
# (SET / COALESCE / CASE) so Python kwarg defaults like projection_note="" in a
# mapper's row-builder (those flow to mapper_note via merge_mappings) and the
# separate aws_li_catalog.projection_note ingest column are not matched.
_WRITE_RE = re.compile(
    r"SET\s+projection_note"
    r"|projection_note\s*=\s*COALESCE"
    r"|projection_note\s*=\s*CASE",
    re.IGNORECASE,
)


def test_only_render_notes_writes_the_mapping_note():
    offenders = []
    for path in sorted(glob.glob(os.path.join(SCRIPTS_DIR, "*.py"))):
        name = os.path.basename(path)
        if name in _NOTE_WRITER:
            continue
        with open(path, "r", encoding="utf-8-sig") as f:
            text = f.read()
        for m in _WRITE_RE.finditer(text):
            line = text[: m.start()].count("\n") + 1
            offenders.append(f"{name}:{line}")
    assert not offenders, (
        "projection_note is written outside render_notes.py in: "
        + ", ".join(offenders)
        + ". Record the decision in a structured row_state field (or pipeline_note); "
        "render_notes composes the note from state."
    )


def test_registry_declares_note_output_only():
    import json
    with open(os.path.join(DATA_DIR, "row_state_registry.json"), "r", encoding="utf-8-sig") as f:
        reg = json.load(f)
    pn = reg["fields"]["projection_note"]
    assert pn["writable_by"] == ["render_notes"]
    assert pn["kind"] == "derived"
    assert pn.get("readable_as_logic") is False
    # pipeline_note is the structured free-text channel deterministic passes use
    # in place of writing prose straight into the note.
    pl = reg["fields"]["pipeline_note"]
    assert pl.get("readable_as_logic") is False
    assert "pipeline_note" in reg["note_order"]
