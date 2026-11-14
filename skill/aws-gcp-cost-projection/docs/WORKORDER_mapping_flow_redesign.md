# WORKORDER — Mapping Write-Path Redesign

**Status:** COMPLETE (steps 1–4 + latent precedence gap, ERROR escalation, and skill provenance landed 2026-11-12; steps 5–6 design tracked)
**Author:** investigation pass, 2026-11-12
**Scope:** `scripts/classify_mechanics.py`, `apply_static_mappings.py`, `apply_commitment_ignores.py`, `merge_mappings.py`, `ensure_full_coverage.py`, the Phase-2 block of the embedded orchestrator (`internal/jobs/orchestrate.go`), and how the Go spawner provisions scripts (`spawner.go`).
**Goal:** replace the current multi-source, erase-and-rebuild mapping flow with a **single-source-of-truth, forward-only row lifecycle** that is correct, reproducible, loudly honest on failure, and safely parallelizable.

---

## 0. Decision being asked for

Adopt a mapping write-path where:
1. **The `aws_li_to_gcp_li` table is the one source of truth.** No producer writes JSON that a later step rebuilds the table from. Every producer **UPSERTs its own rows keyed by `aws_li_key`**; **nothing ever `DELETE`s the whole table.**
2. **Every row moves forward through explicit states** (`classified → mapped → verified`) and **no step can move a row backward.** Coverage = "no row left below `mapped`."
3. **The LLM is a real fallback stage**, not a post-gate retry hack — it maps whatever the deterministic layer left at `classified`, and its output is never clobbered.
4. **Scripts are pinned per job** (run from the job's own copy, not the live shared skill dir) so an edit can never corrupt a running job.
5. **A crashed deterministic mapper fails loud**, not silently into passthrough.

---

## 1. What is actually broken (evidence, 2026-11-12)

Three *compounding* failures turned a handful of leftover rows into ~30–60 wasted minutes per job **and** silent pricing corruption. All three are confirmed from live logs in `cur-web-data/jobs/*`.

### F1 — The deterministic mapper crashed; the whole deterministic layer produced nothing
`apply_static_mappings.py` exited 1 with **`SyntaxError: invalid syntax` (line ~9068)** on jobs `c5d8746e` and `c3943a22`. Because it is a **soft (`?`) script**, the pipeline continued — with **zero** deterministic mappings written. `merge_mappings.py` then logged `empty_group_mappings` for *every* deterministic group (`block_storage`, `flat_hourly`, `per_request`, `compute_arm`, `negative_cost`, …). The file **compiles fine now** — it was **half-saved mid-edit** when those jobs ran.

Root enabler: **scripts run from the live shared skill dir.** The orchestrator invokes every deterministic script as `subprocess.run([PYTHON, os.path.join(SKILL_DIR, script_path)])` ([orchestrate.go:1605](../../../internal/jobs/orchestrate.go)), where `SKILL_DIR` is the shared installed skill (`spawner.go` `skillEnv()` → `skill.ResolveDir()`). There is **no isolation between code-under-edit and in-flight jobs** — a job's correctness depends on the exact byte-state of the scripts dir at the instant each phase happens to run. (The Go spawner *does* `cp -r scripts jobDir`, but only the **LLM/agy** uses that copy; the orchestrator's own pre/post scripts ignore it.)

### F2 — The merge step erases the retry's work
When coverage fails, the retry prompt ([orchestrate.go:1379](../../../internal/jobs/orchestrate.go)) is *correct*: it hands the LLM the exact unmapped rows (any group) and tells it to **`INSERT` directly** into `aws_li_to_gcp_li`. But the orchestrator then unconditionally re-runs the post-LLM scripts ([orchestrate.go:1853](../../../internal/jobs/orchestrate.go)), which includes `merge_mappings.py`, which does:

```python
con.execute("DELETE FROM aws_li_to_gcp_li")          # merge_mappings.py:453
con.executemany("INSERT ...", records)               # records rebuilt from JSON files ONLY
```

The retry wrote **no JSON** (it was told not to), so the rebuild **deletes the retry's direct INSERTs**. Result, every time: `76 unmapped → 30-min retry inserts 76 → merge deletes them → 76 remaining`. The LLM succeeded; the next script threw the work away. **Two sources of truth (JSON files vs. table) that disagree.**

### F3 — The gate + backstop mask it as success, with silent pricing loss
`ensure_full_coverage.py` then passthrough-fills the leftovers and the gate flips to PASS. So the job "succeeds" — but those rows are carried at **AWS cost with no real GCP pricing** (`rows_unmapped_after_retry`: 99 rows / ~$8.9k across 3 jobs). A whole deterministic layer vanished and the output still looked green.

**Net:** the "useless retry" and "76 unmapped" are **not the LLM being dumb.** They are a crashed mapper (F1) whose gap the retry *could* fix but is structurally prevented from fixing (F2), hidden by a backstop (F3).

---

## 2. Design principles

1. **Single source of truth = the table.** Delete the JSON-files-vs-table split. Producers write rows, not files-to-be-rebuilt-from.
2. **Forward-only lifecycle.** A row's `state` only advances. No global `DELETE`. No step undoes another.
3. **Idempotent UPSERT keyed by `aws_li_key` (+ `component`).** Re-running any producer converges to the same result; a retry's rows survive subsequent steps.
4. **Precedence, not last-writer-wins.** Each row records `decided_by` + priority (deterministic-fact > static-mapper > llm > passthrough-backstop). A lower-priority writer can never overwrite a higher-priority decision. (Generalizes today's `DETERMINISTIC_GROUPS` immunity into the universal rule — see [[project_generic_mechanic_overclaim]].)
5. **Fail loud, not into passthrough.** A deterministic mapper that *crashes* is a hard error; passthrough is only ever a *deliberate* decision for a row with genuinely no GCP equivalent.
6. **Isolation = reproducibility.** A running job executes a pinned snapshot of the scripts; editing the repo never mutates an in-flight run.

---

## 3. Target architecture

### 3.1 Row lifecycle (state machine)

```
 ingested ─▶ classified(group) ─▶ mapped(by: static│llm│passthrough, decided_by, priority) ─▶ verified
                     │                                                                               ▲
                     └──────────────── LLM fallback stage maps anything still 'classified' ──────────┘
```

- `classify_mechanics.py` advances `ingested → classified` and stamps `mechanic_group`.
- Deterministic mappers advance **their** rows `classified → mapped` and **UPSERT** them.
- **LLM mapping is a first-class stage**, not a retry: after the deterministic mappers, *every* row still at `classified` is handed to the LLM (this is where today's deterministic-group leftovers would be caught **in the normal flow**, not via a post-gate retry). The LLM UPSERTs its rows.
- `ensure_full_coverage.py` becomes the **explicit terminal state** `passthrough` for anything still unmapped — recorded as such, lowest priority, and surfaced in the report as "no GCP estimate," never silently.
- The coverage gate checks **state ≥ mapped for all rows**; repairs **advance** state; by construction they cannot fight.

### 3.2 `merge_mappings.py` → UPSERT, never rebuild

Replace `DELETE FROM aws_li_to_gcp_li` + rebuild with an **UPSERT keyed by (`aws_li_key`, `component`)** that respects precedence:
- New/higher-priority rows insert or replace; equal/lower-priority rows for an already-decided key are ignored.
- Running it twice is a no-op. A direct INSERT from the LLM fallback **survives** a subsequent merge. F2 disappears outright.

> **Implementation note (2026-11-12): F2 fix landed — but close a latent gap in the second guard.**
> `merge_mappings.py:447` now builds a `_incoming_mappings` temp table from the JSON files and only deletes/re-inserts **keys present in the incoming set**. The coverage retry writes **no JSON**, so its keys are never in the incoming set → its rows are never touched. This is what actually kills F2, and it works today.
> There is a *second*, independent precedence guard in the same block: it spares an existing row from deletion when `COALESCE(decided_by,'') LIKE '%retry%' OR LIKE '%direct%' OR = 'human'`. **This guard is currently inert.** The retry prompt (`orchestrate.go:_build_mapping_coverage_retry_prompt`, the `INSERT` column list ~line 1426) does **not** include `decided_by`, so every retry row lands with `decided_by = NULL` and matches none of those patterns.
> - **Why it's harmless today:** the NOT-EXISTS-in-incoming mechanism already protects retry rows (their keys have no JSON).
> - **Why it's still a gap:** the moment a retry re-maps a key that *also* has a JSON mapping file (e.g. a static mapper wrote JSON for a component the retry then re-decided), that key **is** in the incoming set, the `decided_by` guard is the only thing that would protect it, and it won't fire — merge silently clobbers the retry's decision. This is the exact F2 failure class, just one layer down.
> - **Fix:** stamp the retry's direct INSERTs with a protected author, e.g. add `decided_by='llm_retry_direct'` to the retry prompt's INSERT column list so the `%retry%`/`%direct%` guard becomes real rather than dead insurance. Mirror this for any other direct-INSERT fallback path (`over_and_under_projection` targeted retry). Add a regression test: a merge run over a JSON file that re-asserts a key already written by a `decided_by LIKE '%retry%'` row must leave the retry row intact.

### 3.3 Producers write disjoint row sets

Each producer owns a disjoint set of rows (by `mechanic_group`), so concurrent UPSERTs never collide on the same key. This is what makes §5 (parallelism) safe.

### 3.4 Script isolation (fixes F1 class permanently)

Run the orchestrator's deterministic scripts from the **job's pinned copy** (the `cp -r scripts jobDir` the spawner already does — just point `SKILL_DIR` at the job copy, or add a `JOB_SKILL_DIR`), OR pin an immutable, versioned skill snapshot per job. Consequences:
- A mid-edit save can never break a running job.
- Two jobs are byte-identical in the code they ran → reproducible.
- Record the skill version/hash on the job for provenance.

---

## 4. Fail-loud contract (fixes F3)

- A deterministic mapper that **raises/exits non-zero is NOT soft** for the mapping stage: it must fail the phase (or trip a dedicated `deterministic_mapper_crashed` hard gate), because a silent zero-coverage run produces a confidently wrong projection.
- Distinguish, in diagnostics, **"row is passthrough by design"** (no GCP equivalent) from **"row is passthrough because an upstream mapper crashed"** — today both look identical. Add `passthrough_reason ∈ {no_equivalent, upstream_failure, backstop}` and gate on `upstream_failure`.
- Keep `empty_group_mappings` but **escalate to ERROR** when a group that *should* have a deterministic handler produces zero rows (that is the crash fingerprint, not a benign info).

---

## 5. Parallelism — where it actually pays (honest)

**Can the mapping scripts run in parallel? Yes — and the redesign makes it safe — but set expectations:**

- The deterministic mappers (`map_block_storage`, `map_object_storage`, `map_per_request`, `map_managed_db`, …) each operate on a **disjoint `mechanic_group`**, so they are embarrassingly parallel once every producer UPSERTs its own disjoint rows (§3.3). A worker pool over groups is clean and correct under the new model.
- **But the deterministic layer runs in *seconds*** (classify_mechanics classified 123 rows in <1s in the logs). The job's wall-clock is dominated by the **LLM phase (~27 min)** and the **wasted retry (~30 min)**. Parallelizing a 5-second stage saves ~5 seconds — the wrong lever.
- **Where parallelism actually saves time, in priority order:**
  1. **Delete the wasted retry** (F2 fix) — ~30 min/job, immediately.
  2. **Run jobs concurrently with real isolation** — see `WORKORDER_concurrency_isolation_engine.md`.
  3. **Parallelize the LLM's own group mapping** (`compute_breakdown` / `managed_db` / `misc` sub-agents) — this is the only *mapping* step big enough to matter.
  4. Parallelize deterministic mappers — do it for **cleanliness and to compose with the state model**, not for speed.

Rule of thumb for this codebase: **parallelize the slow, independent part (the LLM), not the cheap scripts.**

---

## 6. Migration plan (incremental, each step shippable)

1. **Hotfix now (not the redesign):** run `ensure_full_coverage` **before** the LLM retry for `mapping_coverage`, OR make the retry write JSON so the merge includes it, OR make `merge_mappings` UPSERT. Any one stops the ~30-min waste today. (Recommend the UPSERT — it is step 3 anyway.)
2. **Make `apply_static_mappings` a hard dependency / fail-loud** (F3): a crash fails the phase instead of yielding silent zero coverage. Add `passthrough_reason`.
3. **Convert `merge_mappings` to idempotent UPSERT keyed by (`aws_li_key`,`component`) with precedence** (§3.2). Kills F2.
4. **Pin scripts per job** (§3.4) — point the orchestrator at the job-local scripts copy; record skill version. Kills F1 class.
5. **Introduce the `state` column + lifecycle**; make the LLM a normal fallback stage over `classified` rows (§3.1); coverage gate checks state. Retire the post-gate retry hack.
6. **Parallelize deterministic mappers over disjoint groups** (§5) — optional, for cleanliness.

### Status of each step (2026-11-12)
- **Step 1/3 (merge UPSERT) — DONE.** (`merge_mappings.py:447`, incoming-set delete+insert). Kills F2. **Closed:** the `decided_by` precedence guard is now active: the targeted coverage retry prompt instructs the LLM to stamp `decided_by='llm_retry_direct'` (and outlier retry stamps `decided_by='llm_outlier_retry_direct'`), so direct retry rows are fully immune even if their keys exist in an incoming JSON file. Verified by `test_merge_mappings_upsert_protects_retry_row_even_when_in_incoming_json`.
- **Step 2 (fail-loud) — DONE.** `apply_commitment_ignores.py` and `apply_static_mappings.py` are hard dependencies (no `?` soft prefix in `orchestrate.go` phaseSpecs), failing loudly on crash. `empty_group_mappings` in `merge_mappings.py` now escalates to `ERROR` if any group in `DETERMINISTIC_GROUPS` has catalog rows but zero mapping rows.
- **Step 4 (script pinning & provenance) — DONE.** `spawner.go` and `orchestrate.go` copy `phases/` + `scripts/` into the job dir; `orchestrate.go` `_resolve_script()` prefers `JOB_SCRIPTS_DIR` over `SKILL_DIR` at every call site. Kills the F1 class. Skill git SHA is recorded into `skill_version.txt` in the job directory.
- **Step 5 (state column + lifecycle, retire post-gate retry) — DESIGN TRACKED.** The targeted coverage retry exists with full non-clobbering immunity.
- **Step 6 (parallelize mappers) — DESIGN TRACKED** (low priority per §5).
- **Verification debt:** all 1366 Python tests + Go build/engine tests pass, but none of the above has been exercised in a live end-to-end job run. Confirm acceptance criteria (a)–(e) against a real bill before sign-off.

**Acceptance criteria:** (a) a deterministic-mapper crash fails the job loudly, never a green run with silent passthrough; (b) an LLM fallback's mapped rows survive every subsequent step (no `N → N` retries); (c) re-running the whole Phase-2 script set is a no-op (idempotent); (d) two runs of the same bill with the same skill version produce identical mappings; (e) editing a script cannot change the result of an already-running job.

---

## 7. Relationship to existing work
- Generalizes the precedence idea from [[project_generic_mechanic_overclaim]] (DETERMINISTIC_GROUPS immunity) into the universal §2.4 rule.
- Complements, and shares the isolation theme with, `WORKORDER_concurrency_isolation_engine.md` (that one isolates *jobs*; this one isolates *the mapping write-path and the code it runs*).
- The spec-dispatch cutover ([[project_service_specs_registry]]) is the classification half; this is the **write-path** half — both aim at one deterministic source of truth.
