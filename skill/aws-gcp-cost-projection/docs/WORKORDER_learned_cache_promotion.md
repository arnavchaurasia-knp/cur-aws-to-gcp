# WORK ORDER — Stop jobs writing the shared learned-codes cache; propose instead

**For an implementing AI with no prior context. Read fully before editing.**
Paths starting `skill/` or `internal/` are from the repo root.

**Scope: where a cache is written. No classification or pricing changes.**
If a dollar figure moves, you have done something wrong.

Background: `docs/DESIGN_state_durability_on_render.md` Tier B, audit finding
D-8 in `docs/AUDIT_pipeline_defects.md`.

---

## 1. The problem, in five steps

1. `scripts/resolve_unknown_product_codes.py` resolves AWS service names that
   carry no product code (PDF/CSV bills). When it learns one, it caches it so
   the model is not asked again. Its own docstring:

   > "Self-building: ... grows across bills so the model is asked less over time."

2. It runs **per job**, as a Phase-1 post-script
   (`internal/jobs/orchestrate.go:130`).

3. It writes to a file in the **shared skill directory**
   (`resolve_unknown_product_codes.py:56, 93`):

   ```python
   _CACHE_PATH = os.path.join(_DATA, "learned-product-codes.json")
   with open(_CACHE_PATH, "w", encoding="utf-8") as fh:
       json.dump(doc, fh, indent=2, ensure_ascii=False)
   ```

   Read-modify-write. No lock, no atomic replace.

4. **Two jobs at once lose data.** Both read 100 entries; A learns 3 and writes
   103; B learns 2 and writes 102 — A's three are gone. And `open(..., "w")`
   truncates immediately, so a job reading mid-write gets empty JSON, treats
   the cache as cold, and re-asks the model for everything.

5. **On Render it never persists anyway.** That directory is the Docker image
   layer, rebuilt on every deploy. The cache reverts to the committed copy on
   every push to `main`. The "grows across bills" promise is false in
   production.

The orchestrator states per-job isolation three times in its own prompts
(`orchestrate.go:88, 173, 299`): *"this job's files live ONLY under
<<JOB_DIR>>, isolated from every other job running on this machine at the same
time."* This file breaks that.

### The decision this work order implements

Do **not** fix it with a lock. A lock makes the race safe but leaves the data
ephemeral, and leaves the next script free to make the same mistake.

Invert the direction: **a job proposes, it does not mutate.** The committed
file stays the single source of truth, learning becomes reviewable, and the
shared write disappears entirely.

---

## 2. Tasks

### T1 — Jobs write proposals into their own directory

In `scripts/resolve_unknown_product_codes.py`:

- **Keep** reading `data/learned-product-codes.json`. It is the baked-in
  knowledge and must still be consulted.
- **Replace** `_save_cache()` with a function that writes newly-learned
  mappings to **`<job_dir>/projection-audit/learned_proposals.json`**, not to
  the shared path.

Format — self-describing, because a human or a promotion step will read it:

```json
{
  "_meta": {
    "job_id": "<dir name>",
    "created_at": "2026-10-21T10:00:00Z",
    "source": "resolve_unknown_product_codes.py tier-3 agy pass",
    "note": "Proposals only. Promote with scripts/promote_learned_codes.py."
  },
  "proposals": [
    { "product_name": "Amazon Detective", "service_code": "AmazonDetective",
      "canonical": "detective", "confidence": 0.9,
      "evidence_rows": 2, "evidence_usd": 146.31 }
  ]
}
```

Carry `evidence_rows` and `evidence_usd` through — a reviewer needs to know
whether a proposal is worth $3 or $3,000.

**Keep the existing validation**: an entry is only proposed if its service code
exists in `data/aws-product-codes.json`'s closed set. A proposal that fails
validation is dropped and reported, not written.

### T2 — A promotion step

New `scripts/promote_learned_codes.py`. A maintenance tool, not part of the
per-bill pipeline.

```bash
python scripts/promote_learned_codes.py <jobs-root> [--apply]
```

- Scans every `*/projection-audit/learned_proposals.json` under the root.
- Merges proposals, de-duplicating by `product_name`.
- **Conflicts** — the same product name proposed with two different codes —
  are reported and **never auto-applied**. That disagreement is a signal worth
  a human.
- Re-validates every code against `data/aws-product-codes.json`.
- Default is a **dry run** printing what would change. `--apply` writes
  `data/learned-product-codes.json`.
- Writing uses a temp file in the same directory plus `os.replace()` — atomic,
  so even this tool cannot leave a truncated file.

Print a diff-style summary: entries added, entries already known, conflicts,
total evidence dollars behind each addition.

### T3 — Make the mistake un-repeatable

Add `tests/test_no_shared_data_writes.py`:

- Scan every `scripts/*.py`.
- Fail if any opens a path built from the skill `data/` directory in a write
  mode (`"w"`, `"a"`, `"w+"`, or `json.dump` to such a path).
- Allow a small explicit list of maintenance tools that legitimately do this:
  `promote_learned_codes.py`, `refresh_aws_product_codes.py`,
  `refresh_ec2_specs.py`, `build_catalog_index.py`, `prefetch_skus.py`,
  `catalog_manager.py`. Pin them by name, the same ratchet pattern as
  `tests/test_notes_are_output_only.py`.

**This test is the real deliverable.** The file fix is this week; the test is
what stops the next per-job script writing to shared state.

### T4 — Check for others

Before finishing, run the T3 scan and report anything it catches besides the
allow-list. Fix any that are per-job scripts; report any you are unsure about
rather than guessing.

---

## 3. Verification

```bash
cd skill/aws-gcp-cost-projection && python -m pytest tests/ -q
python -m py_compile scripts/resolve_unknown_product_codes.py scripts/promote_learned_codes.py
```

Then:

1. **No shared write remains.** `git diff` must show
   `learned-product-codes.json` is never opened for writing by
   `resolve_unknown_product_codes.py`.
2. **Proposals are produced.** Run the script against a job DB containing an
   unresolvable product name; confirm `learned_proposals.json` appears in the
   job dir and the shared file is byte-identical to before
   (`git status` clean for `data/`).
3. **Promotion is a dry run by default.** Run `promote_learned_codes.py`
   without `--apply`; confirm nothing changes on disk.
4. **Conflicts are refused.** Hand-craft two proposal files mapping the same
   product name to different codes; confirm the tool reports the conflict and
   applies neither.
5. **Concurrency is gone.** Two jobs can no longer collide, because neither
   writes the shared file — state this in your summary as reasoning, there is
   nothing to run.
6. **No number moved.** Dollar totals on an existing job DB unchanged.

---

## 4. Do NOT

- Do **not** solve this with a file lock or an advisory `.lock` file. The
  problem is shared mutable state in an ephemeral directory, not just the race.
- Do **not** stop *reading* `learned-product-codes.json` — the baked-in
  knowledge is still needed.
- Do **not** auto-apply promotions, and do **not** auto-resolve conflicts.
- Do **not** write proposals anywhere outside the job directory.
- Do **not** change `classify_mechanics`, any mapper, or any rate.
- Do **not** weaken the existing closed-set validation on service codes.

---

## 5. Done

- [ ] `resolve_unknown_product_codes.py` reads the shared cache, writes only
      `<job_dir>/projection-audit/learned_proposals.json`.
- [ ] Proposals carry evidence rows and dollars.
- [ ] `promote_learned_codes.py` exists, dry-run by default, atomic on
      `--apply`, refuses conflicts.
- [ ] `test_no_shared_data_writes.py` passes and would fail if the write were
      restored.
- [ ] T4 scan run; anything else it caught is reported.
- [ ] Dollar totals unchanged; full suite passes.
