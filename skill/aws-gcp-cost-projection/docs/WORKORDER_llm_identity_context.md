# WORKORDER — Hand the LLM the row's service identity (kill the "Cloud Storage" misroute at its source)

Status: READY for implementation model
Owner decision needed: none (mechanical + guidance change; no pricing policy)
Relates to: `[[project_layer1_product_code]]`, `[[project_service_specs_registry]]`, `[[project_generic_mechanic_overclaim]]`

---

## 1. The defect, stated precisely

Non-object-storage rows (CloudTrail event storage, EMR, VPC-endpoint data, etc.) get
mapped by the LLM to **Cloud Storage** and over-project (CloudTrail: 790,600 events →
a fake $38,247 "Standard Storage" line). Downstream patches
(`fix_storage_misroute.py`, `service_classifier.py` forced overrides, the
`and not _ilike(product, "Lambda"/"VPC"/"SageMaker")` blacklists in
`classify_mechanics.py`) all exist to mop this up **after** it happens.

Root cause is **upstream, at the LLM context boundary**, not in the pricing logic.

### What the LLM actually receives
`classify_mechanics.py` resolves authoritative identity for every row:

```python
# classify_mechanics.py:1624
row["canonical"] = canonical_service(row.get("product"), row.get("product_code"))
```

…and its own routing rules key on it (`r.get("canonical") == "s3"`, `== "cloudwatch"`, …).

But the manifest that is serialized for the LLM (`phase2_manifest.json`) is built from a
fixed column list that **omits `product_code` and `canonical`**:

```python
# classify_mechanics.py:1738-1763  (manifest_col_specs)
aws_li_key, mechanic_group, product, usage_type, operation, unit,
line_item_type, pricing_model, aws_amortized_cost, instance_type, ...
# ^ no product_code, no canonical
```

The manifest `SELECT` re-queries `aws_li_catalog` fresh (line 1773), so the in-memory
`canonical` computed at 1624 is discarded, and `product_code` is never selected.

### The one path identity *does* leak through (and why it's not enough)
For `misc` rows only, `build_guidance(row, spec_dispatch(row))` is attached as an
annotation, and it *does* use `canonical` to emit a `service_hint`
(`llm_guidance.py:61, 128-130, 188-217`). **But** that hint only appears when:

- `canonical_service()` successfully resolved an identity, **and**
- that identity has a curated `_MISC_CONTEXT` entry or a `service_specs.json` spec or a
  dispatch result.

When any of those miss, `build_guidance` falls to the generic branch
(`llm_guidance.py:226`) with `"service_hint": None`. At that point the LLM is left with
only the raw text columns — `product`, `usage_type`, `unit="GB-Mo"`, `operation="…storage…"`
— and reasonably guesses **Cloud Storage**. So identity reaches the LLM through a
*curated-coverage-gated* side channel, not as a first-class field on the row. The gaps in
that curated coverage are exactly where the misroutes occur.

**Net:** even a perfect LLM cannot avoid the misroute on an uncurated row, because the
authoritative identity the system already computed is not in front of it.

---

## 2. The fix (principled; adds information, removes no safety)

### Change A — put identity on every row handed to the LLM
In `classify_mechanics.py`, add `product_code` and `canonical` to the manifest payload.

1. Add to `manifest_col_specs` (line 1738):
   ```python
   ("product_code", "product_code"),
   ```
   (`product_code` is a real `aws_li_catalog` column — already used at lines 1221, 1624.
   The dynamic `NULL AS product_code` fallback at 1764-1768 covers older schemas.)

2. `canonical` is **computed, not a DB column**, so the fresh manifest `SELECT` (line 1773)
   can't fetch it. After building each `row = dict(zip(full_cols, raw))` in the manifest
   loop (line 1782-1786), stamp it the same way the classify loop does:
   ```python
   row["canonical"] = canonical_service(row.get("product"), row.get("product_code"))
   ```
   Keep it unconditional (all groups, not just `misc`) so it survives the
   `_drop_null_cols` optimization only where non-null — acceptable, since a row with no
   resolvable identity legitimately has `canonical = None`.

### Change B — never let `build_guidance` drop identity on the floor
In `llm_guidance.py`, the generic fallback branch (around line 226) sets
`"service_hint": None`. Change it to surface the canonical identity even when there's no
curated context:
```python
"service_hint": canonical or None,
"identity_known": bool(canonical),
```
This guarantees that "we know this is CloudTrail, we just lack a curated mapping" is
*stated*, instead of looking identical to "we have no idea what this is."

### Change C — tell the LLM to key on identity first
In the Phase-2 LLM guidance/prompt (the mapping instructions; see
`data/llm_guardrails.json` and the Phase-2 prompt text in `orchestrate.go:166+`), add a
rule near the top of target selection:

> Determine the GCP target from `product_code` / `canonical` FIRST — that is the
> authoritative AWS service identity. Use `product`, `usage_type`, and `operation` only to
> choose the *charge-type within that service*, never to pick the service itself. A row's
> unit being `GB-Mo` or the word "storage" appearing in free text does **not** make it
> object storage — e.g. `AWSCloudTrail` is a logging/audit service regardless of its unit.
> If `product_code`/`canonical` is null, say so and fall back to passthrough + manual
> review rather than guessing an object-storage target.

---

## 3. How this composes with the larger plan

- This is the **LLM-side** half of `[[project_layer1_product_code]]`. The deterministic
  (`classify_mechanics`) side already consumes `canonical`; this makes the LLM side consume
  it too. Same field, both decision paths.
- Once identity is on every row and the LLM keys on it, the downstream patches become
  **removable** (verify, then delete in a later pass, not this one):
  - the `and not _ilike(product, "Lambda"/"Virtual Private Cloud"/"SageMaker")` exclusions
    in the `block_storage`/`object_storage` rules,
  - `fix_storage_misroute.py` (storage-shaped backstop),
  - `service_classifier.py`'s force-the-target overrides (become redundant with correct
    first-pass routing).
  Do NOT delete them in this work order — land the context fix, confirm misroutes stop on
  the audit set, then retire the scaffolding separately.

---

## 4. Verification

1. `python3 -m pytest tests/ -v` — all existing tests pass.
2. Inspect a generated `phase2_manifest.json`: every row has `product_code` and
   `canonical`; a known CloudTrail/EMR/VPC row shows its real canonical, not null.
3. Re-run a bill with the CloudTrail-storage case (the $38K job). Confirm:
   - the CloudTrail rows are NOT mapped to Cloud Storage,
   - `fix_storage_misroute.py` reports **0 rerouted rows** (the misroute no longer reaches
     it — the point is to make it a no-op, not to keep feeding it),
   - no new over-projection introduced on object-storage rows that are genuinely S3.
4. Global audit (the 75-DB sweep from AGENTS.md): the
   `TARGETS:['Cloud Logging','Cloud Monitoring','Cloud Monitoring + Logging']`-style
   cross-run target drift on identity-resolvable services should collapse.

---

## 5. Scope guard

- No pricing/policy change. No new hardcoded service list. The change is: *add the
  identity field that already exists to the payload, and instruct the consumer to use it.*
- If `canonical_service()` returns null for a product, that is a **Layer-1 coverage gap**
  (fix in `aws_normalizer.py` / `aws-product-codes.json`), NOT a reason to re-add a
  text-blacklist here.
