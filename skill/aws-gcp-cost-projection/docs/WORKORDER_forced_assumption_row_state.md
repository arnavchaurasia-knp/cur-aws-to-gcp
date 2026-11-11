# WORK ORDER — Move "forced via assumption" from note-text parsing to a structured row_state field

**For an implementing AI with NO prior context. Read top to bottom. Do every
ORIENTATION PROBE in Phase 0 and paste real output into RESULT before writing code.
If a probe disagrees with this file, STOP and report.**

## The problem in one paragraph (plain)
When an assumption forces a row to pass through, the code records that fact by writing
a human sentence into the row's note — `[passthrough: forced via assumption <key>]` —
and then, elsewhere, **reads that sentence back** (SQL `ILIKE '%forced via
assumption%'` + a regex to pull `<key>`) to decide what to show and check. Deciding
program behavior by matching the wording of a human note is the "stringly-typed"
anti-pattern: the note is meant to be read by people and is free to be reworded, and
the moment it changes, the logic silently breaks. This project already has the correct
mechanism for machine-readable state — the **row_state registry** (`row_state.py` +
`data/row_state_registry.json`, typed fields with validated enum/text domains). This
work order moves the "forced via assumption" fact into a row_state field, leaves the
human note alone (notes stay output-only), and switches the readers to the field.

### Industry grounding (why this is the standard fix)
- Log/display text is for humans; keep the machine-readable fact in a separate
  structured field and query the field, not the sentence. Parsing a formatted message
  is fragile — even a full regex fails on slight wording changes, and messages drift
  over time. (Structured logging, e.g. Microsoft.Extensions.Logging, exists for
  exactly this reason.)
- The fix is: a closed, validated field (enum/code or a dedicated key column),
  written once at the source of truth, read everywhere else. That is precisely what
  `row_state` already provides here. A guard test
  (`tests/test_notes_are_output_only.py`) already enforces "don't read notes in
  predicates" and is currently RED on `render_report.py` — this work order makes it
  green the right way (not by raising its budget).

Sources:
- [Why is Log Parsing and Processing hard? (IBM Cloud)](https://medium.com/ibm-cloud/why-is-log-parsing-and-processing-hard-1e72bac55712)
- [Roslyn analyzer for structured log messages (Meziantou)](https://www.meziantou.net/roslyn-analyzer-to-check-the-types-of-structured-log-messages.htm)
- [System Log Parsing: A Survey (arXiv)](https://arxiv.org/pdf/2212.14277)

### Anti-hallucination rules
1. **Do NOT delete or stop writing the human note.** The note prose stays for display.
   You are ADDING a structured field and REDIRECTING the readers to it.
2. **Do NOT invent a new state mechanism.** Extend the existing `row_state` registry
   (`data/row_state_registry.json`) and go through `row_state.py` accessors. No new
   table, no ad-hoc column added outside the registry.
3. **Do NOT change any projected dollar.** This only changes HOW the "forced" fact is
   stored and read, never whether a row is forced or its price.
4. **COORDINATION:** `render_report.py` is under active concurrent editing in this
   repo. Re-read it immediately before editing; if lines moved, re-locate by the
   `forced via assumption` string, not by line number. If it changes under you, STOP
   and report rather than overwrite.
5. One phase at a time; run the named test after each. Python lives under
   `skill/aws-gcp-cost-projection/`.

---

## 0. ORIENTATION PROBES — run first, fill RESULT

| # | Probe | Expected (confirm) | RESULT |
|---|-------|--------------------|--------|
| P1 | `python -m pytest tests/ -q 2>&1 \| tail -3` | baseline pass count ____ ; note `test_notes_are_output_only` is RED | |
| P2 | `grep -n "forced via assumption" scripts/render_report.py scripts/validate_fix.py scripts/passthrough_enforcer.py scripts/apply_static_mappings.py` | WRITER: passthrough_enforcer.py:~250 writes the prose; READERS: render_report.py ~1942 & ~2687 (ILIKE + regex), validate_fix.py ~623 (ILIKE) | |
| P3 | `grep -n "^FIELDS\|def set_state\|def eq\|def ensure_schema\|ADD COLUMN" scripts/row_state.py` | row_state accessor exists: `ensure_schema`, `set_state`, `eq`, domains from the registry | |
| P4 | `python -c "import json;d=json.load(open('data/row_state_registry.json'));print(list(d['fields']))"` | existing fields incl. rate_source, passthrough_reason, strategy, … (no forced-assumption field yet) | |
| P5 | `grep -n "forced via assumption\|row_state\|set_state" scripts/passthrough_enforcer.py` | enforcer writes the note; confirm whether it already imports/uses row_state | |
| P6 | `grep -n "projection_note" tests/test_notes_are_output_only.py` | the ratchet: render_report.py budget is 1, actual reads are 3 → RED | |
| P7 | `grep -n "forced_assumption\|assumption_key\|is_assumption\|row_state" scripts/render_notes.py scripts/backfill_row_state.py` | render_notes.py is the note writer + row_state user; backfill exists for one-time migration | |

If P2 line numbers differ, use the string matches — the file is being edited live.

---

## 1. DESIGN (what to add)

A new row_state field that carries the machine-readable fact the readers need. The
readers need TWO things: (a) "is this row forced via an assumption?" and (b) WHICH
assumption key. One text field covers both:

- Field name: **`forced_assumption_key`**, type `TEXT`, domain: free text but
  **validated as a known assumption key** (see below). `NULL`/absent = not forced.
- Add it to `data/row_state_registry.json` under `fields`, mirroring the shape of an
  existing text field. Give it a `description` and, for validation, a `domain` whose
  allowed values are the assumption keys from `data/assumption-defaults.json`
  (so a typo'd key is rejected at write time by `row_state.validate_value`). If the
  registry's validation can't reference another file, declare it as a free `string`
  domain and add a separate test (Phase 4) asserting every written value is a real
  assumption key.

(___ DECISION, flag don't guess: one text key field vs. a `passthrough_reason` enum
value `forced_via_assumption` PLUS the key field. Recommend the single
`forced_assumption_key` field — it is both the flag (non-NULL) and the key. Confirm.)

---

## 2. PHASES

### Phase 1 — Declare the field (registry + schema)
- [ ] Add `forced_assumption_key` to `data/row_state_registry.json` `fields` (TEXT,
      description, domain per §1). Nothing else reads it yet.
- [ ] Confirm `row_state.ensure_schema` ADDs the column on next run (it iterates
      `FIELDS`); no code change should be needed there — verify by running a tiny
      script that calls `ensure_schema` on a temp duckdb and checks the column exists.
- [ ] Test: a unit test that `ensure_schema` creates `forced_assumption_key` and
      `row_state.validate_value("forced_assumption_key", <a real key>)` passes while a
      bogus key fails (if you used a validated domain).

### Phase 2 — Write the field at the source of truth (keep the note)
- [ ] In `scripts/passthrough_enforcer.py` (~250), at the exact point it builds the
      `[passthrough: forced via assumption {key}]` note prefix, ALSO set the row_state
      field for those rows via `row_state.set_state(con, keys, by="passthrough_enforcer",
      forced_assumption_key=key)` (match the real `set_state` signature from P3).
      **Keep writing the note prose** — do not remove it.
- [ ] Ensure `row_state.ensure_schema` has run before the write (it is called in the
      pipeline already; confirm ordering — if the enforcer runs before schema setup,
      call `ensure_schema` first).
- [ ] Test: run the enforcer on a fixture row forced by an assumption → assert the
      `forced_assumption_key` column holds the key AND the human note still contains
      the sentence.

### Phase 3 — Switch the readers to the field (remove the prose parsing)
- [ ] `scripts/render_report.py` ~1942 and ~2687: replace
      `AND m.projection_note ILIKE '%forced via assumption%'` with a predicate on the
      new column (e.g. `AND m.forced_assumption_key IS NOT NULL`, built via
      `row_state` helper if one fits), and replace the
      `re.search(r"\[passthrough:\s*forced via assumption\s+([^\]]+)\]", note)` key
      extraction with reading `forced_assumption_key` from the row. Re-locate by string
      first (file is live-edited).
- [ ] `scripts/validate_fix.py` ~623: same swap on its ILIKE predicate.
- [ ] Leave `render_report.py:264`'s regex ONLY if it is parsing a note purely for
      display formatting (not for a decision/predicate); the ratchet test targets
      predicate reads. If it feeds a decision, move it too. State which you did.
- [ ] Test: `python -m pytest tests/test_notes_are_output_only.py -q` is GREEN without
      raising the budget. Then lower the render_report.py budget in that test to its
      new true count (ratchet only goes DOWN).

### Phase 4 — Backfill + guard tests
- [ ] Extend `scripts/backfill_row_state.py` to populate `forced_assumption_key` for
      existing rows from the legacy note prose (one-time migration, same pattern as the
      other backfilled fields). This is the ONLY sanctioned place that may read the
      note for this purpose, and the ratchet test already exempts `backfill_row_state.py`.
- [ ] If the field used a free-string domain, add a test asserting every non-NULL
      `forced_assumption_key` is a real key in `assumption-defaults.json`.
- [ ] Full `python -m pytest tests/ -q` green; pass count ≥ Phase 0 baseline.

### Phase 5 — Verification
- [ ] `test_notes_are_output_only` green via the field, not a budget bump.
- [ ] Report output for a forced-assumption bill is unchanged (same rows flagged, same
      key shown) — the fact now comes from the column. Spot-check one fixture before/
      after.
- [ ] No projected dollar changed.

---

## 3. DO NOT
- Do NOT remove the human note or stop writing it.
- Do NOT raise the ratchet budget to make the test pass — that defeats the guard.
- Do NOT add the column outside the `row_state_registry.json` + `row_state.py` path.
- Do NOT change which rows are forced, or any price.
- Do NOT edit `render_report.py` blind — it is being changed by another process; re-read
  and re-locate by string, and STOP if it shifts under you.

## 4. DONE CRITERIA
- [ ] `forced_assumption_key` declared in the registry; `ensure_schema` creates it.
- [ ] `passthrough_enforcer` writes the field (key) while still writing the note.
- [ ] `render_report.py` and `validate_fix.py` read the field, not the note prose.
- [ ] `test_notes_are_output_only` green with the render_report.py budget lowered.
- [ ] Legacy rows backfilled; a test ties written values to real assumption keys.
- [ ] Full suite green; report output and dollars unchanged.

## 5. PROGRESS LOG (append-only)
- 2026-11-11 Created. A ratchet guard (`test_notes_are_output_only`) is RED because the
  "per-assumption force passthrough" feature records state as note prose
  (`[passthrough: forced via assumption <key>]`, written by passthrough_enforcer.py)
  and reads it back via `ILIKE` + regex in render_report.py (~1942, ~2687) and
  validate_fix.py (~623). Fix: carry the fact in a `row_state` field
  (`forced_assumption_key`), keep the note for humans, redirect the readers. Grounded
  in the structured-state-over-text-parsing industry pattern; the codebase already
  implements that pattern in row_state.py / row_state_registry.json.
