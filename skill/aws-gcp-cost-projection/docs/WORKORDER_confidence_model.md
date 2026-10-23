# WORK ORDER — Confidence is no longer shown; decide whether the machinery stays

**For an implementing AI with no prior context. Read fully before editing.**
Paths starting `skill/` are relative to `skill/aws-gcp-cost-projection/`.

**Scope: the `mapping_confidence` subsystem.** No pricing, mapping or
classification changes. **No dollar figure may move.**

---

## 0. STATUS — the display is already removed

Two changes are **done**, and the suite passes at 1,107:

- `render_report.py` no longer prints
  `"**N** AWS line items · **X%** average mapping confidence"` — it now prints
  the line-item count alone.
- `export_report.py` no longer emits the `"Confidence"` column in the
  customer-facing XLSX/CSV download (`handler.go` streams that file).

The value is still computed and still written to the internal log and the
`run_results` row. **Nothing customer-facing shows it any more.**

What remains is a decision about the machinery behind it.

---

## 1. Why it was removed

The figure was `AVG(mapping_confidence)` over non-passthrough rows. Its inputs:

```
136 hardcoded literals in apply_static_mappings.py, 21 distinct values:

  0.90 x18   0.85 x17   0.50 x10   0.65 x9    0.40 x8    0.55 x8
  0.70 x7    0.80 x7    0.95 x7    0.45 x7    0.75 x7    0.60 x6
  0.30 x4    1.0  x4    0.82 x2    0.88 x1    0.92 x1    0.93 x1
  0.72 x1    0.3  x1    0.0  x1
```

`0.3` and `0.30` both appear — the same number typed two ways, which is how you
can tell these were written by hand at 136 separate sites rather than derived
from anything. `0.82`, `0.88`, `0.92`, `0.93` appear once each; nothing
distinguishes them.

Across the corpus the displayed figure ranged **67.4% to 92.1%** — a spread
precise enough to invite comparison between bills, on a scale nobody
calibrated. It never made a dollar figure wrong; it made a credibility claim
the data could not support.

---

## 2. What is now orphaned

With no consumer, this is what the subsystem costs:

| piece | size | status now |
|---|---|---|
| `confidence_engine.py` | 304 lines | imported by nothing, **was already dead before this change** |
| `calibrate_confidence.py` | 142 lines | a pipeline step (`orchestrate.go:162`) whose only job is to lower a number nobody reads |
| literals in `apply_static_mappings.py` | 136 sites | written, never read |
| clamp sites in `apply_rates.py`, `validate_fix.py` | 6 | lower a number nobody reads |
| `data/llm_guardrails.json` | 1 rule | instructs the model to "set mapping_confidence honestly" |
| `data/row_state_registry.json` | 1 declaration | — |

**The only remaining read is `calibrate_confidence.py` reading the value in
order to clamp it** — a closed loop with no consumer outside itself.

---

## 3. The decision

**Does confidence have a future use?**

- **If no** — remove it (§4). That is ~446 lines of dedicated code, 136
  literals, 6 clamps, an LLM instruction and a pipeline step, all deleted. The
  pipeline gets materially simpler and nothing observable changes.
- **If yes** — say what will consume it *before* keeping it. The credible use
  is routing: send low-confidence rows to review, or flag them in the report.
  Nothing does that today, and a value nobody reads will drift silently
  whatever its design.

**Do not keep it "just in case."** It has been written-but-unread long enough
to accumulate 21 distinct values and a 304-line unused redesign; that is what
keeping it "just in case" already produced.

---

## 4. If removing — the order that keeps the build green

### T1 — Delete `confidence_engine.py`

304 lines, imported by nothing, and it describes a three-part model
(classification / mapping / data confidence) the pipeline never implemented.
Leaving it is actively harmful: the next person to read it will believe the
system works that way.

**Keep its analysis** — move the docstring's three-way distinction into
`docs/` as a design note. The reasoning is good even though the code never ran.

### T2 — Remove `calibrate_confidence.py` from the pipeline

Drop the `?scripts/calibrate_confidence.py $DB` entry from `orchestrate.go`
(~line 162) and delete the script. Its ceilings only ever lowered a number with
no consumer.

**Check its notes first.** It appends explanatory text to `projection_note` for
some services ("architecture review", high-ambiguity caps). If any of that
wording is worth keeping, move it to the mapper that owns the row **before**
deleting — a note explaining an OpenSearch assumption is worth more than the
number it accompanied.

### T3 — Remove the writes

The 136 literals, the 6 clamp sites, the `llm_guardrails.json` rule and the
`row_state_registry.json` declaration. Drop the column from
`aws_li_to_gcp_li` last, once nothing writes it.

Leave the `run_results` column alone unless you also migrate the table — a
stale column in a history table is harmless, and dropping it loses past rows.

### T4 — Tests

1. `test_no_confidence_in_report` — the rendered summary contains no
   confidence percentage.
2. `test_no_confidence_in_export` — the XLSX/CSV header has no `Confidence`
   column.
3. `test_corpus_dollars_unchanged` — every bill's AWS and GCP totals are
   identical before and after. **This is the regression that matters**: this
   work order must not move a single figure.

---

## 5. Do NOT

- Do **not** delete `calibrate_confidence.py`'s explanatory notes along with
  its numbers (T2).
- Do **not** drop the `run_results` column without a migration (T3).
- Do **not** keep `confidence_engine.py` as "documentation". Move the text to
  `docs/` and delete the code (T1).
- Do **not** re-add a confidence figure anywhere customer-facing without first
  answering §3.

---

## 6. Done

- [ ] §3 answered in writing: removed, or kept with a named consumer.
- [ ] If removed: `confidence_engine.py` deleted with its analysis preserved in
      `docs/`; `calibrate_confidence.py` out of the pipeline with its notes
      rehomed; writes and the LLM guardrail removed.
- [ ] Report and export carry no confidence figure.
- [ ] **Every bill's totals unchanged.**
