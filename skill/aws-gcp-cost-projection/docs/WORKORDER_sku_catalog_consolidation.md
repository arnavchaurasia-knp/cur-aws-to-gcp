# WORKORDER — sku_catalog consolidation (S2: one catalog-read SSOT)

> **NEW AGENT:** read `ARCHITECTURE_MASTER_TRACKER.md` §0 first. This is subsystem **S2** (catalog reads). Update the
> tracker §2 (S2) and `AUDIT_built_systems_wiring.md` (B, sku_catalog row) as you progress.

Status: **OPEN — design done, 1 decision blocking (D-CAT-1).** Author: Claude (Opus 4.8) · 2026-11-20.
Related: `scripts/sku_catalog.py`, `scripts/resolve_sku.py`, `scripts/apply_rates.py`, `scripts/incremental_rerate.py`,
`scripts/catalog_manager.py`, `AUDIT_built_systems_wiring.md`.

## Corrected finding (the audit's "migrate all catalog reads to sku_catalog" was too broad)

`duckdb.connect` appears in ~47 scripts, but **most read the per-job projection DB, not the GCP SKU catalog**
(`catalog.duckdb`). Conflating the two would be wrong. The catalog itself is read by ~4 code paths, and there are
effectively **two catalog-access layers**:

| Layer | Role | Connection | Status |
|---|---|---|---|
| `resolve_sku` | **resolve to THE sku** (+ `get_valid_catalog_services/skus`, `_select`, scoring, unit guard, `prefer_single_region`) | **cached** read-only (`get_catalog_connection`) | 🟢 load-bearing, wired everywhere |
| `sku_catalog` | **list ALL matching candidates** (`query_skus`, find-sku.sh shape) + `all_tiers` + `catalog_exists` | **fresh per call** (opens+closes each time) | 🔴 0 importers (only its test) |
| `apply_rates` / `incremental_rerate` / `catalog_manager` | ad-hoc catalog reads for rate joins / health / coverage | own `duckdb.connect(CATALOG_DB)` | inline |

Key facts:
- `resolve_sku` has **no list-all-candidates API** — so `sku_catalog.query_skus` is a *real missing capability*, not pure
  redundancy. But it is dormant and opens a fresh connection per call (a perf wart vs resolve_sku's cached conn).
- There is **no single catalog connection** — resolve_sku (cached), sku_catalog (per-call), apply_rates,
  incremental_rerate, catalog_manager each open their own. That duplication is the real S2 debt.

## Decision required (D-CAT-1) — what is the catalog-read SSOT?

- **Option A — resolve_sku is the base; absorb + delete sku_catalog.** Move `query_skus`/`all_tiers` into the
  resolve_sku catalog layer (or a small shared `catalog_db` module it owns), reusing the cached connection; delete the
  standalone `sku_catalog`. One module owns connection + resolve-one + list-all + tiers. **Recommended** — resolve_sku is
  already the de-facto SSOT with the better (cached) connection design.
- **Option B — sku_catalog is the primitive; resolve_sku builds on it.** Make `sku_catalog` own the single cached
  connection + raw queries; resolve_sku calls it for reads and keeps only resolution logic. Cleaner layering in theory,
  but refactors the hot resolve path (perf/behavior risk) and inverts the current dependency.
- **Option C — keep both, just unify the connection.** Lowest-risk: one cached `catalog_db` connection module both use;
  leave the two APIs where they are. Removes the connection duplication without moving the resolve/list APIs.

Recommendation: **A** (one module, resolve_sku-based, cached conn, list-all absorbed), or **C** as a safe first step
toward A.

## Plan (after D-CAT-1)

- [ ] S2.1 Introduce/choose the single cached read-only catalog connection (per the decision).
- [ ] S2.2 Route `apply_rates`, `incremental_rerate`, `catalog_manager` catalog reads through it (verify rate joins are
  byte-identical — these feed dollars, so parity is mandatory).
- [ ] S2.3 Provide the one list-all API (`query_skus`) + `all_tiers` on the SSOT; give it a real consumer (e.g. the
  candidate-pool / review tools that currently hand-roll candidate SQL).
- [ ] S2.4 Parity-lock: a test asserting the SSOT's connection/queries return the same rows as the old inline SQL on
  fixtures (so no silent drift; mirrors `test_spec_match_parity`).
- [ ] S2.5 Delete the redundant layer (A) or document the kept split (C); flip S2 green in the tracker + audit row.

## Why not just migrate the 47 duckdb.connect sites

Most are per-job-DB reads (a different database and concern). Catalog reads are ~4 paths. The real win is **one cached
catalog connection + one place for resolve-one and list-all**, not forcing every DB read through one module. Changing
`resolve_sku`'s cached connection or `apply_rates`' rate joins is dollar-path-adjacent, so each step is parity-locked
and ships only with the suite green and no dollar move.
