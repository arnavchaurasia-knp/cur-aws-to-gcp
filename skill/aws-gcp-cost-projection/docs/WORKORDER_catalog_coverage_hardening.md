# WORK ORDER — Catalog coverage hardening (Phase 4 pre-step)

**For an implementing AI / engineer with NO prior context. Read fully. This covers
`scripts/ensure_catalog_coverage.py` (runs just before `apply_rates.py` in Phase 4,
"Rate-Card Fill"). Do the items in order; stop and report per item. Every fix here is a
standard safe-cache pattern — NO hardcoded SKU/rate values, NO per-service special-casing.**

---

## STATUS — 2026-10-15: B1/B2/B3/B4/B6 BUILT in `scripts/catalog_manager.py`

A single consolidated tool now owns catalog source + artifact health:
`scripts/catalog_manager.py` with 3 modes — `check` (read-only: deep source integrity +
artifact sanity + age), `refresh` (locked single-writer: complete-only atomic fetch →
integrity gate → rebuild `catalog.duckdb` → update META), `coverage --db` (in-job,
advisory/log-only, resolves needed SKUs against `catalog.duckdb` via `resolve_sku`).

- **B1 (atomic write)** → `_atomic_write_gz` (temp + `os.replace`). DONE.
- **B2 (complete-fetch-only + count floor)** → `_fetch_service_skus` returns `(skus, complete)`;
  refresh discards partial/empty/collapsed before promoting. DONE.
- **B3 (coverage via the real resolver against catalog.duckdb)** → `cmd_coverage` uses
  `resolve_intent_to_sku` + `get_catalog_connection`. DONE.
- **B4 (in-job = log-only)** → `coverage` mode never writes, always exits 0. DONE (wire
  `catalog_manager.py coverage --db $JOBDIR` into Phase 4 in place of
  `ensure_catalog_coverage.py`'s Step-2 refresh, which should be removed).
- **B6 (source integrity gating the rebuild)** → `integrity_check_file` + `_scan_source`;
  `cmd_refresh` REFUSES to rebuild if any source is unhealthy. DONE.
- **Consolidation**: `catalog_health_check.py` DELETED; callers repointed —
  `internal/jobs/catalog_refresher.go` → `catalog_manager.py refresh` (Go builds clean);
  `weekly_catalog_refresh.py` → thin wrapper around `catalog_manager.py refresh`.
  Free fix: the Go background refresher previously fetched `.json.gz` but never rebuilt the
  duckdb (no-op for pricing) — now it rebuilds.

STILL OPEN here:
- **B5** (versioned immutable artifact + atomic pointer-swap for fleet/live scale) — DESIGN,
  not built. `catalog_manager refresh` is the single-writer; B5 is the next step up.
- The three **repair** passes in `ensure_catalog_coverage.py` (Half A) — upstream work,
  untouched.
- Verify `refresh` (needs GCP creds) and `coverage` (needs a job DB) on a real run — only
  `check` has been executed.

---

## Scope note — this work order is "HALF B" only

`ensure_catalog_coverage.py` does two unrelated kinds of work:

- **HALF A (NOT in this work order):** three *repairs* that patch mistakes made in earlier
  phases — `_repair_null_sku_names`, `_repair_illegal_passthrough`,
  `_downgrade_enterprise_plus`. These are symptoms of upstream bugs (the LLM in Phase 2/5
  overwriting deterministic rows, escaping to passthrough on a missing rate, and over-picking
  premium SKU tiers). They must be fixed at the SOURCE phase, not here. Tracked separately —
  do not touch them in this work order.
- **HALF B (THIS work order):** the catalog-coverage machinery itself — the part the file is
  actually named for (`_check_and_refresh`, `_fetch_service_skus`, `_save_catalog`,
  `_has_coverage`, `_load_catalog`). It has its own real bugs, independent of anything
  upstream.

---

## Key finding the implementer MUST know first: source vs compiled catalog

The catalog exists in TWO forms, and they are a SOURCE → COMPILE → ARTIFACT chain:

```
GCP Billing API  →  data/skus/*.json.gz   →  build_catalog_index.py  →  data/catalog.duckdb
                     (raw source dump)        (compile step)             (what the pricer reads)
```

1. **`data/skus/{service_id}.json.gz`** (110 files, one per GCP service) — the RAW source dump
   from the GCP Cloud Billing API: full SKU objects incl. `description`, `serviceRegions`, and
   `pricingInfo` (tieredRates → unitPrice/nanos). This is the master copy / build INPUT. Only
   `ensure_catalog_coverage.py` reads/writes these; the pricer never reads them.
2. **`data/catalog.duckdb`** — COMPILED from the `.json.gz` files by `build_catalog_index.py`
   (which already uses temp-dir + atomic `os.replace`). Two tables: `skus` (searchable index)
   and `tiered_rates` (the actual prices). **This is the ONLY thing the pricer reads** —
   `resolve_sku.py` opens it read-only for SKU lookup, and `apply_rates.py` loads all rates
   from it (`tiered_rates` + `blended_rate`). Read path is already lock-free / high-concurrency
   safe.

Two consequences that DRIVE the fixes below (verified 2026-10-15):
- **The coverage check reads the WRONG artifact.** `_has_coverage` / `_load_catalog` inspect
  `.json.gz`, but pricing resolves against `catalog.duckdb`. If the two drift (e.g. json.gz
  refreshed but duckdb not rebuilt), the check reports coverage the pricer does not actually
  have, or vice-versa. The check MUST target `catalog.duckdb` — the thing the pricer uses.
- **The in-job refresh is effectively a no-op for pricing.** When `_save_catalog` writes a
  `.json.gz`, nothing rebuilds `catalog.duckdb` in the same run, so the pricer sees NO change
  until `build_catalog_index.py` runs later. Refreshing `.json.gz` alone cannot change a price.
  A real refresh must be source-update → rebuild-duckdb, and (per B5) belongs out-of-band.

Before coding, re-VERIFY: `catalog.duckdb` tables = `skus` + `tiered_rates`; `apply_rates.py`
and `resolve_sku.py` both read `catalog.duckdb` only; `build_catalog_index.py` builds duckdb
from `data/skus/*.json.gz`.

---

## B1 — Atomic catalog write (temp file + os.replace)
**Problem:** `_save_catalog` does `gzip.open(path, "wt")` directly on the shared file. A crash
or a concurrent writer mid-write leaves a truncated/corrupt `.gz` that then fails to load for
every future job.
**Fix (standard atomic-write pattern):** write to a sibling temp file, flush+fsync, then
`os.replace(tmp, final)` (atomic on POSIX and Windows). Never write the final path in place.
```python
import tempfile, os, gzip, json
def _save_catalog(service_id, skus):
    final = os.path.join(DATA_DIR, "skus", f"{service_id}.json.gz")
    d = os.path.dirname(final)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with gzip.open(fd, "wt") as f:   # fd is owned by gzip now
            json.dump(skus, f)
        os.replace(tmp, final)           # atomic promote
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise
```
**Test:** simulate a write that raises mid-way (monkeypatch json.dump to throw) → the original
`final` file is unchanged and no `.tmp` is left behind.

---

## B2 — Complete-fetch-only save (never promote a partial pull)
**Problem:** `_fetch_service_skus` returns whatever it accumulated BEFORE an error (it `break`s
on exception). `_check_and_refresh` then saves it anyway via `if fresh_skus:`. A network blip
on page 2 of 3 permanently truncates the bundled catalog for all future jobs.
**Fix (staging → promote-on-success):**
1. `_fetch_service_skus` returns `(skus, complete: bool)`. Set `complete=False` on ANY page
   exception; `True` only when pagination ends cleanly (no `nextPageToken` left, no errors).
2. In `_check_and_refresh`, only call `_save_catalog` when `complete is True`.
3. Add a sanity floor: refuse to replace an existing catalog with a dramatically smaller one
   (e.g. `len(fresh) < 0.5 * len(existing)` → skip + warn). Guards against a "successful but
   suspiciously empty" API response.
**Test:** (a) a fetch that errors on a later page → old file untouched, warning logged;
(b) a fetch returning far fewer SKUs than the bundled file → skipped with a warning.

---

## B3 — Coverage check must target catalog.duckdb via the SAME resolver the pricer uses
**Problem (two bugs in one):**
1. `_has_coverage` matches on **≥1 shared word** between the needed SKU name and a description.
   "Standard Storage" matches any SKU containing "Storage" → can report "covered" when the real
   SKU is absent, or a false gap.
2. It inspects **`.json.gz`**, but the pricer resolves against **`catalog.duckdb`**. The check
   must speak for what the pricer actually sees, which is the compiled duckdb — NOT the source
   dump.
**Fix (one matcher, single source of truth = catalog.duckdb):**
- Replace the word-overlap-over-json.gz check with a call to the real resolver against the
  compiled DB: ask `resolve_sku.py` "would this (gcp_service, sku_name, region) resolve against
  `catalog.duckdb`?" → resolves ⇒ covered; None ⇒ real gap. Reuse `get_catalog_connection()`
  (read-only) so the check and the pricer share the exact same data and matching logic.
- VERIFY the exact public function + signature in `resolve_sku.py` before wiring (it exposes
  `resolve_intent_to_sku(...)`, `get_catalog_connection()`, `get_valid_catalog_skus()`,
  `infer_expected_unit(...)`, `_score_sku_match(...)`). Use the real one; do not invent a name.
- Drop `_load_catalog`/`_has_coverage`'s dependence on `.json.gz` for the CHECK entirely.
- If a full resolver call is too heavy for a pure coverage pass, at minimum query the duckdb
  `skus` table with normalized tokens, strip a generic stopword list (`storage, standard,
  network, cost, usage, tier, ...`), and require ALL distinctive (non-stopword) tokens — never ≥1.
**Test:** a needed SKU that exists in `catalog.duckdb` → covered; a plausible-but-absent name
that shares only a generic word ("...Storage") → reported as a gap, not "covered".

---

## B4 — In-job refresh is ineffective AND unsafe → make the check log-only in the job
**Problem:** `_save_catalog` writes a GLOBAL `.json.gz` shared by all jobs, with no lock
(parallel jobs corrupt it) — AND, critically, writing `.json.gz` changes nothing the pricer
sees, because the pricer reads `catalog.duckdb` and nothing rebuilds it in the same run. So the
in-job refresh is both unsafe and a no-op for pricing. It also mutates repo-vendored data
mid-run (reproducibility smell).
**Fix (simplest correct thing): the in-job coverage pass becomes LOG-ONLY.**
- The Phase-4 pre-step should CHECK coverage (against `catalog.duckdb`, per B3) and, on a gap,
  **record it loudly** (into the existing rate-gaps / data-gaps output) for an out-of-band
  refresher to act on — it must NOT fetch or write a catalog in the job path.
- Remove the in-job `_fetch_service_skus` / `_save_catalog` write path from the job (keep the
  functions only if the out-of-band refresher in B5 reuses them). apply_rates' global-fallback
  rate already handles a missing exact-region rate safely for the current run.
- If an interim in-job refresh is truly required before B5 exists: it must (a) write `.json.gz`
  atomically + complete-only (B1/B2), AND (b) immediately run `build_catalog_index.py` to
  rebuild `catalog.duckdb` (itself atomic), AND (c) hold a lock around the whole source-update
  +rebuild. This is strictly worse than log-only; prefer log-only.
**Test:** a job with a catalog gap logs the gap and does NOT write `data/skus/*.json.gz` or
`catalog.duckdb`; the run still completes (global-fallback prices the gapped rows).

---

## B6 — Validate the SOURCE (.json.gz) completeness/integrity, and GATE the rebuild on it
**Why:** source → compile → artifact means you must validate BOTH ends. If the `.json.gz`
source dump is partial, stale, or corrupt, `catalog.duckdb` compiled from it inherits that
silently and the pricer prices off bad data. A coverage check on the artifact alone (B3) won't
catch "the source itself is broken".
**What already exists (strengthen, don't duplicate):** `catalog_health_check.py` already checks
that every service in `data/services.json` has a NON-EMPTY `.json.gz` + reports age. That is
only presence + non-empty — not real integrity.
**Fix — deepen `catalog_health_check.py` into a real source-integrity check:**
Per `.json.gz` file, verify: (a) valid gzip + valid JSON (not truncated); (b) non-empty SKU
list; (c) each SKU has the fields the compiler needs (`skuId`, `description`, `serviceRegions`,
and `pricingInfo` with a usable `tieredRates`/`unitPrice`); (d) per-file freshness
(`effectiveTime` not absurdly old); (e) a SKU-count sanity floor vs the last known-good count
(detect a collapsed/truncated dump — same spirit as B2's floor). Across files: every service in
`services.json` is present.
**GATE the compile:** `build_catalog_index.py` (and `weekly_catalog_refresh.py`) must run this
integrity check and REFUSE to rebuild `catalog.duckdb` from a source that fails — keep the last
good duckdb instead, and report loudly. Never compile a bad source into the artifact the pricer
reads.
**Test:** a `.json.gz` that is truncated / empty-SKU-list / missing `pricingInfo` / collapsed
count → integrity check fails that service and the rebuild is refused (old duckdb retained);
a healthy source set → passes and rebuild proceeds.

---

## B5 — TARGET ARCHITECTURE for live + high scale (separate project, not a patch)
**Why:** fetching from the GCP Billing API inside the projection job cannot support
production / millions of jobs — API rate limits, per-job latency, and write contention. GCP
prices are a slowly-changing dimension (daily/weekly), so "live" should mean "kept fresh by a
dedicated refresher", NOT "each job fetches live".
**What already exists:** `weekly_catalog_refresh.py` IS a basic out-of-band refresher (Windows
Task Scheduler → `catalog_health_check.py --refresh` → rebuild `catalog.duckdb`). So this
architecture is partly BUILT — it needs hardening (B1/B2 atomic+complete-only on the source
fetch, B6 integrity gate before rebuild), not building from scratch. Make it the SOLE writer.
**Design (out-of-band refresh + immutable versioned artifact + atomic pointer swap):**
- **Write path — one scheduled refresher, rare, out-of-band:** extend `weekly_catalog_refresh.py`
  to pull the full catalog, run B6 integrity, build a VERSIONED immutable artifact (e.g.
  `catalog-YYYY-MM-DD.duckdb`) in a shared store, then flip a `current` pointer (blue-green).
  Readers see the old full catalog or the new full catalog — never a partial one, never a lock.
  Reuse B1/B2 logic (atomic promote, complete-only) here.
- **Read path — every job, read-only:** jobs load the catalog pointed to by `current`
  (the `catalog.duckdb` read-only connection already does this per process). No per-job API
  call, no lock. Back with a shared read cache (Redis) / read-replica only if ever needed.
- **In-job coverage check becomes log-only:** it reports gaps for the refresher to pick up;
  it NEVER writes the catalog.
- This folds into the planned refresh layer — see `project_refresh_and_reviewer_layer`.
**Status:** DESIGN-ONLY here. Do not build B5 as part of the B1–B4 patch pass; it is the
scalable end-state once the product goes live. B1–B4 make today's in-job path safe in the
meantime.

---

## Report back (per item)
Files changed, the test added, and anything that needed a decision you could not make. For B3,
state the exact `resolve_sku` function + signature you wired to. For the two-catalog finding,
confirm whether `catalog.duckdb` is built from the `.json.gz` files and where.
