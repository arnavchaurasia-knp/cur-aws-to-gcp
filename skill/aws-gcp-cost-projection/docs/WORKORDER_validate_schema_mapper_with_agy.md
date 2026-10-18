# WORK ORDER — Validate the schema-mapper against a real agy model call

**For an implementing AI with no prior context.** Paths are relative to
`skill/aws-gcp-cost-projection/` unless they start with a drive letter.

**This is a measurement task, not a coding task.** You are finding out whether a
cheap model produces usable column mappings, and whether the existing verifier
catches its mistakes. Do not change the pipeline. Do not "improve" the mapper
until you have the baseline numbers.

---

## 0. Background in four sentences

`scripts/schema_mapper.py` maps a bill's columns onto our canonical fields. It
profiles every column, resolves what a deterministic alias table can, sends only
the leftovers to agy in one call, then **verifies every proposal against the
column's real values** before accepting it. Everything except the agy call has
been tested; the call itself has never run. That is what you are testing.

Design and prior test results: `docs/DESIGN_field_registry_and_schema_mapping.md`.

---

## 1. Check agy runs at all

`.env` already sets `AGY_PATH`. Confirm the binary answers before anything else:

```bash
cd /c/Users/arnav/Downloads/cur-web-AGY
"$(grep '^AGY_PATH=' .env | cut -d= -f2-)" --dangerously-skip-permissions \
  --model gemini-3.8-flash -p "Reply with exactly: PONG"
```

- Prints `PONG` (possibly with surrounding noise) → continue.
- Asks you to log in → **stop and tell the user**; they must authenticate in a
  terminal. Do not try to automate a login.
- Hangs past ~3 minutes → stop, report the exact command and behaviour.

---

## 2. See the prompt before spending a call

```bash
cd skill/aws-gcp-cost-projection
python scripts/schema_mapper.py "C:\Users\arnav\Downloads\Facets\aws_cur_style_report_enriched.csv" --dry-run
```

Expected: 16 of 50 columns resolved by the alias table (all `ACCEPT`), 34 in the
residue, then the prompt. **If those counts differ, stop and report** — the
baseline has moved and the rest of this work order is measuring the wrong thing.

---

## 3. Run it for real, on four bill shapes

One run each. These are four genuinely different formats, which is the point.

```bash
cd skill/aws-gcp-cost-projection
B="C:\Users\arnav\Downloads\Facets"
python scripts/schema_mapper.py "$B\aws_cur_style_report_enriched.csv"  --out /tmp/map_cur.json
python scripts/schema_mapper.py "$B\test-bill (1).csv"                  --out /tmp/map_simple.json
python scripts/schema_mapper.py "$B\New bills\May_filtered.csv"         --out /tmp/map_vendor12.json
python scripts/schema_mapper.py "$B\New bills\June-dev_filtered.csv"    --out /tmp/map_vendor10.json
```

Capture the full stdout of each. If a run fails, record the failure and keep
going — a format that breaks it is a result, not a blocker.

---

## 4. What to measure

For each bill, from the run output:

| metric | how |
|---|---|
| model responded | yes / no / timeout |
| response parseable | did the script print proposals, or `RESPONSE UNUSABLE`? |
| proposals returned | count |
| `ACCEPT` / `WARN` | count |
| `REJECT` | count, **and the reason for each** |
| invented field names | any `is not a declared field` rejects |
| `null` answers | count — most residue columns genuinely are not our fields, so a high null count is CORRECT, not a failure |

Then the two numbers that actually matter:

**False accepts** — a proposal the verifier accepted that is *wrong*. Check each
`ACCEPT` by eye against the column's samples. A model mapping
`lineItem/BlendedCost → cost` is right; `product/instanceFamily → instance_type`
is wrong and the verifier would not catch it, because both are
high-cardinality text.

**False rejects** — a proposal the verifier rejected that was *right*. These are
worse than false accepts for adoption: they mean the verifier blocks good data.
For each `REJECT`, decide whether the model or the verifier was wrong.

---

## 5. Also note

- **Latency** per call, roughly. The design claims this is affordable because it
  is one call per bill, cached by header signature. If a call takes 3 minutes,
  that claim needs revisiting.
- **Did the model obey the output contract?** It was told "JSON array only". Did
  it wrap it in prose or a fence? The parser handles both, but if the model
  ignores instructions here it will ignore them elsewhere.
- **Did it map any column the alias table already handled?** It should not have
  been shown those at all.

---

## 6. Report back

A short table per bill with the §4 metrics, then:

1. Every false accept, with the column, the proposed field, and why it is wrong.
2. Every false reject, with your judgement on whether the model or the verifier
   erred.
3. One sentence: **is a cheap model good enough for this job?** Your answer
   should follow from the numbers, not from a general impression.
4. Anything you noticed but did not change.

---

## 7. Do NOT

- Do **not** modify `scripts/schema_mapper.py`, the contracts, or the alias
  table to make the numbers better. Measure first; the fix is a separate task.
- Do **not** wire the mapper into the pipeline. It is a standalone prototype.
- Do **not** write files into `C:\Users\arnav\Downloads\Facets` — that is the
  user's own folder. Use `--out`.
- Do **not** attempt to log in to agy, or store any credential.
- Do **not** re-run a bill repeatedly to get a better-looking result. If you
  want to test variance, run the same bill 3× **and report all three**.
