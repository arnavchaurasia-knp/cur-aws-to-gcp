# WORK ORDER — Wire build_guidance into Phase 2 (make the refined misc guidance reach the LLM)

**For an implementing AI / engineer with NO prior context. Read fully. Do the steps
in order. Stop and report; a reviewer checks.**

## Why
`scripts/llm_guidance.py::build_guidance()` is the spec-driven replacement for
`classify_mechanics._misc_reason()` (whose tailored branches are dead code — see
`docs/WORKORDER_misc_llm_guardrails.md` ADDENDUM). But `build_guidance` is NOT called
anywhere in the pipeline yet, so the LLM still receives the broken `_misc_reason`
output. This work order routes Phase-2 misc rows through `build_guidance`.

## Prerequisite
Complete the ADDENDUM in `docs/WORKORDER_misc_llm_guardrails.md` first (ADD-1 dynamodb
throughput, ADD-2 text tests, ADD-3 `_is_accelerator` usage_type fix) so the guidance
is complete before it goes live. If not yet done, this wiring still works — misc rows
just get slightly less coverage until the addendum lands.

## How the misc guidance reaches the LLM today (read before editing)
In `scripts/classify_mechanics.py::main()`:
- In the classify loop (~line 1467), for a `misc` row it calls
  `reason = _misc_reason(row)` and stores `misc_reasons[aws_li_key] = reason` (a JSON
  string). `row` here has the full context (canonical, instance_type/vcpus/ram,
  product_code, _bill_has_discounted_usage).
- The data-gap report reads `json.loads(reason).get("data_gap")` (~line 1470).
- Later (~line 1594) when building `phase2_manifest.json`, each misc row gets
  `row["misc_annotation"] = json.loads(misc_reasons[aws_li_key])`.
- The Phase-2 `agy` prompt (in `cmd/.../orchestrate.go`) has the LLM read
  `manifest[group]["rows"]`; each misc row carries `misc_annotation`.

So the single wiring point is the `_misc_reason(row)` call in the classify loop.

## STEP 1 — Call build_guidance instead of _misc_reason (classify loop)
In `classify_mechanics.py::main()`, where `reason = _misc_reason(row)` is computed for
misc rows, replace it so the annotation comes from `build_guidance`:
```python
# Lazy import INSIDE main() — avoids a circular import: llm_guidance and
# spec_dispatch both import classify_mechanics at module load, so importing them at
# the top of this file would deadlock. By the time main() runs, this module is fully
# initialized, so these imports resolve cleanly.
try:
    from llm_guidance import build_guidance
    from spec_dispatch import dispatch as _spec_dispatch
    _USE_SPEC_GUIDANCE = True
except Exception as _exc:
    print(f"classify_mechanics: build_guidance unavailable ({_exc!r}); "
          f"falling back to _misc_reason", file=sys.stderr)
    _USE_SPEC_GUIDANCE = False
```
Then in the loop, for a misc row:
```python
if group == "misc":
    if _USE_SPEC_GUIDANCE:
        reason = json.dumps(build_guidance(row, _spec_dispatch(row)), default=str)
    else:
        reason = _misc_reason(row)
    misc_reasons[row["aws_li_key"]] = reason
    try:
        if json.loads(reason).get("data_gap"):
            data_gaps.append(...)   # unchanged
    except (ValueError, TypeError):
        pass
```
Notes:
- Keep the output a JSON STRING (the manifest step does `json.loads` on it) — hence
  `json.dumps(...)`.
- `build_guidance` returns `data_gap`, so the existing data-gap report keeps working.
- The import-guarded fallback means a broken llm_guidance import degrades gracefully to
  the old behavior instead of breaking Phase 2.
- Do NOT change the manifest-building block (~line 1594) — it already consumes
  `misc_reasons` generically.

## STEP 2 — Tell the LLM to use the annotation (orchestrate.go Phase-2 prompt)
In the Go Phase-2 mapping prompt (search `cmd/`/`internal/jobs/orchestrate.go` for the
`misc` instructions / "phase2_manifest"), add one explicit instruction near the misc
mapping guidance:
> "For every `misc` row, follow `misc_annotation.guidance` and the recommended
> `strategy`/`confidence_max` in it, and you MUST obey every rule in
> `misc_annotation.guardrails` (never fabricate a SKU, never alter aws_* values,
> passthrough = one row, cite the mechanism). Do not exceed `confidence_max`."
Rebuild/restart the Go server if the project requires it (per Makefile). This is a
prompt-string change only; no Go logic changes.

## STEP 3 — Integration test (this path was NEVER tested — that's why _misc_reason rotted)
Add `tests/test_misc_guidance_wiring.py` that runs `classify_mechanics.main()` end to end
on a small synthetic DB and asserts the manifest annotations:
1. Build a temp DuckDB with an `aws_li_catalog` table containing a few rows that land in
   misc: a Cognito row, a DynamoDB RCU row, an unknown-third-party row, and (optional) a
   data-gap row. Minimal columns: the ones `main()` SELECTs (aws_li_key, product,
   usage_type, operation, pricing_unit, line_item_type, pricing_model,
   aws_amortized_cost, instance_type, instance_vcpus, instance_ram_gb, instance_count;
   product_code/billing_format optional).
2. Run `classify_mechanics.main()` against that DB path (set `sys.argv`), or refactor the
   classify+manifest body into a callable and invoke it. Read the written
   `phase2_manifest.json`.
3. Assert, for the misc rows:
   - every misc row has `misc_annotation` with a non-empty `guardrails` list containing
     all `llm_guardrails.json` universal entries;
   - the Cognito row's annotation guidance contains "MAU";
   - the DynamoDB RCU row's annotation contains "Firestore" (needs ADD-1);
   - the unknown row falls to the generic fallback (strategy passthrough/map, confidence_max 0.6).
This test fails loudly if guidance ever goes dead again.

## STEP 4 — Retire `_misc_reason` (only after Steps 1–3 are green)
Once the wiring is verified: delete `_misc_reason` and the dead hint-loop machinery it
used (`_MISC_SERVICE_HINTS`, `_load_misc_hints`, `_PASSTHROUGH_SERVICES`,
`_SIZING_SENSITIVE`, and the `_misc_reason`-only branches). Confirm no references remain:
`grep -rn "_misc_reason\|_MISC_SERVICE_HINTS" scripts/`. Keep `_info_completeness` and
`_is_accelerator` (still used by `build_guidance`). Remove the now-unused fallback branch
from Step 1 only if you are confident the import never fails in the deployed env;
otherwise leave the guarded fallback but point it at a minimal passthrough default.

## Acceptance
- `tests/test_misc_guidance_wiring.py` passes; `tests/test_spec_dispatch.py`,
  `tests/test_classify_mechanics.py`, `tests/test_llm_guidance.py` still pass.
- A real (or synthetic) Phase-2 run produces a `phase2_manifest.json` whose misc rows
  carry `misc_annotation.guardrails` and the correct tailored `guidance`.
- No circular-import error at `python -c "import classify_mechanics"` or at
  `classify_mechanics.py <db>` runtime.
- Report: the wiring diff, whether orchestrate.go was updated, test counts, and whether
  `_misc_reason` was deleted or left as guarded fallback (with reason).
