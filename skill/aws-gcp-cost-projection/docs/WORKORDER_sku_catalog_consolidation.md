# WORKORDER — catalog-read SSOT (S2): finish migration + delete old duplicates

> **NEW AGENT:** read `ARCHITECTURE_MASTER_TRACKER.md` §0 first. This is subsystem **S2** (catalog reads). Update the
> tracker §2 (S2) and `AUDIT_built_systems_wiring.md` (B) as you progress. **Discipline (user rule):** finish building
> the standard system, THEN delete the old duplicate code — never leave two live paths for one capability.

Status: **DONE — 100% (gated & verified)** · Author: Claude (Opus 4.8)
· opened 2026-11-20 · closed 2026-11-21. Overall **11 / 11 tasks (100%)**.

## The standard (what everything must use)
`scripts/catalog_db.py` is the one catalog-read SSOT (Layer-0 repository): a single cached read-only connection to
`catalog.duckdb` + the query primitives `get_connection`, `close_connection`, `catalog_exists`, `query_skus`
(list-all), `all_tiers`, and `SkuCandidate`/`CatalogUnavailable`. **Nothing else may open `catalog.duckdb`.**
`resolve_sku` keeps its resolution logic and gets its connection from here; `sku_catalog` is a back-compat shim.

## Progress tracker
Legend: ☑ done+verified · ⏳ pending. Each code step: parity-locked, full suite green, **no dollar move** (golden
unchanged) unless an intended change is listed with an official-source cite.

| Phase | Task | Status |
|---|---|---|
| **Foundation** | F1 `catalog_db.py` repository (1 cached conn + primitives) | ☑ |
| | F2 `resolve_sku` connection delegates to `catalog_db` (shared singleton, verified) | ☑ |
| | F3 `sku_catalog` → thin shim; fail-closed tests repointed to the SSOT | ☑ |
| **Migrate dollar-path readers** | M1 `apply_rates.py:805` catalog read → `catalog_db.get_connection()` (parity-lock rate joins) | ☑ |
| | M2 `incremental_rerate.py:67` → `catalog_db.get_connection()` (parity-lock re-rate) | ☑ |
| | M3 `catalog_manager.py:434,520` health/coverage reads → `catalog_db` | ☑ |
| | M4 sweep for any other `duckdb.connect(CATALOG_DB)` (variable-named paths too) → migrate | ☑ |
| **Cleanup (delete old duplicates)** | C1 remove now-dead `resolve_sku._CATALOG_CONN` global + any unused `CATALOG_DB`/`duckdb` refs it left | ☑ |
| | C2 decide `sku_catalog` shim: keep thin (back-compat) OR delete + repoint its test to `catalog_db` | ☑ (kept thin shim) |
| | C3 ratchet test: **fail** if `duckdb.connect(…CATALOG_DB…)` appears in any script except `catalog_db.py` | ☑ (`test_single_catalog_connection.py`) |
| **Close** | C4 flip S2 🟢 in tracker + audit; retro in `TODO_backlog`; delete this file's stale design section | ☑ |

## Per-reader migration recipe (M1–M4 — the dollar-path part, do one at a time)
1. **Baseline**: capture what the reader produces today (the rate rows it injects / the coverage numbers it prints) on
   a fixture or the golden path. This is the parity reference.
2. **Swap the connection only**: replace `duckdb.connect(CATALOG_DB, read_only=True)` with
   `catalog_db.get_connection()`. Do NOT change the SQL. Import `catalog_db`.
3. **Lifecycle guard**: the connection is now a shared singleton — the reader must **not** `.close()` it (that would
   kill it for resolve_sku and every later phase). Remove any `con.close()` on the catalog connection; rely on the
   end-of-run `catalog_db.close_connection()`. (Job-DB connections are unaffected — only the catalog one is shared.)
4. **Parity-lock**: a test (fixture or golden) asserting the reader's output is byte-identical to the baseline. For
   `apply_rates` this means the injected rates / `unit_multiplier × rate` products are unchanged — dollars must not move.
5. **Verify**: parse/import → the reader's own tests → full suite → golden. Commit with the parity evidence.

## Cleanup phase (C1–C3) — "keep only the standard"
Only after M1–M4 are all green:
- **C1** `resolve_sku` still declares `_CATALOG_CONN` / `_SERVICES_CACHE` / `_VALID_SKUS_CACHE`. `_CATALOG_CONN` is now
  unused (connection lives in `catalog_db`) — delete it and any now-dead `CATALOG_DB`/`duckdb` imports it left behind.
  Keep the two caches (they're resolution-domain, built on the shared conn).
- **C2** `sku_catalog.py` is a shim. Preferred end state: **keep it as a 1-screen shim** (zero logic, pure re-export)
  for external/back-compat callers, OR delete it and repoint `tests/test_sku_catalog.py` to import `catalog_db`. Pick
  one; don't leave a shim that still has logic.
- **C3** add `tests/test_single_catalog_connection.py`: greps all `scripts/*.py` and **fails** if
  `duckdb.connect(` with a catalog path/`CATALOG_DB` appears anywhere except `catalog_db.py`. This is the ratchet that
  makes the SSOT permanent — no new module can re-introduce a second catalog connection.

## Guardrails / risks
- `apply_rates` and `incremental_rerate` feed the rate numbers → **parity is mandatory**, dollars must not move.
- The shared singleton must outlive every phase that reads the catalog; the only close is end-of-run. C3's ratchet +
  the lifecycle guard in the recipe protect this.
- Do NOT touch the ~43 `duckdb.connect` sites that read the **per-job projection DB** — different database, out of scope.

---
## Background (condensed — the decision, kept for context)
D-CAT-1 was resolved with the **repository pattern** (a dedicated `catalog_db` SSOT that `resolve_sku` builds on), over
the alternatives of making `resolve_sku` itself the base or only unifying the connection. Why: `resolve_sku` resolves
to ONE sku and lacked a list-all API; `sku_catalog` had list-all but was dormant and opened a fresh connection per
call; and ~4 paths each opened their own catalog connection. The repository gives one connection + one home for
resolve-one and list-all, with the resolution logic layered cleanly on top. Most of the ~47 `duckdb.connect` sites read
the per-job DB, not the catalog — only ~4 are catalog reads, which is why this is a small, surgical consolidation, not a
47-file sweep.
