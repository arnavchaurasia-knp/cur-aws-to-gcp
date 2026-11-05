# WORKORDER — Analytics Platform (corpus-level, fully managed)

**Status:** design / not yet implemented (supersedes the quick Economics UI already on `AdminAnalytics.tsx`, which becomes Phase 1 of this plan).
**Owner decision captured:** build *all four lenses*, full data lifecycle (CRUD), industry-level — not a UI stat row.

---

## 0. Scope boundary (why this is not System Health)

| Surface | Question it answers | Time horizon |
|---|---|---|
| **System Health** | *Is the machine healthy right now?* queue depth, running/failed jobs, catalog vintage, token spend, store integrity | **now** |
| **Analytics** (this doc) | *What have we learned from the whole corpus of bills we've projected?* savings, spend mix, projection quality, methodology | **historical, cumulative** |

Rule: Analytics never duplicates a Health metric. Failure-rate/processing-time stay as a thin "Pipeline" footnote only (volume context), everything else is corpus analysis.

---

## 1. The core architectural decision: materialize a rollup

The headline $ (AWS spend, GCP OD) live in the **control-plane `jobs` table** — cheap. Everything deeper (by-service, by-charge-type, confidence, coverage, findings, CUD) lives **inside each job's `projection-audit/projection.duckdb`** (`aws_li_catalog`, `aws_li_to_gcp_li`, `gcp_projection`, `run_results`, `review_findings`). Sweeping every job DB per page load does not scale (cf. `global_sku_audit.py` opening 75 DBs).

**Decision: write a per-job analytics rollup once, at job finalize, into the control-plane SQLite DB.** Analytics reads only the rollup — O(rows) not O(bills × duckdb spawns). This is the standard warehouse pattern (fact table + dimension breakdowns).

### 1.1 Schema (control-plane SQLite, new migration)

```sql
-- One row per finalized job: the "fact" row.
CREATE TABLE IF NOT EXISTS job_analytics (
  job_id            TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
  prospect          TEXT,
  owner             TEXT,
  created_at        TEXT,          -- copied for time-bucketing without a join
  currency          TEXT DEFAULT 'USD',

  -- economics (lens A)
  aws_spend         REAL,          -- Σ aws_amortized_cost (net, all rows)
  aws_workload      REAL,          -- Σ where is_workload=TRUE
  gcp_od            REAL,          -- run_results.gcp_od
  gcp_1yr_cud       REAL,          -- run_results.gcp_1yr_cud
  gcp_3yr_cud       REAL,          -- run_results.gcp_3yr_cud
  savings_od        REAL,          -- aws_spend - gcp_od
  savings_pct_od    REAL,

  -- quality (lens C)
  total_rows        INTEGER,
  mapped_rows       INTEGER,       -- strategy in (map, break_down)
  passthrough_rows  INTEGER,
  ignored_rows      INTEGER,
  llm_rows          INTEGER,       -- rows priced via LLM vs deterministic
  deterministic_rows INTEGER,
  unmapped_usd      REAL,          -- $ with NULL/Manual-Review target
  coverage_pct      REAL,          -- (aws_spend - unmapped_usd)/aws_spend
  confidence_band   TEXT,          -- run_results.confidence (high/med/low)

  -- methodology (lens D)
  sp_ri_exposed_usd REAL,          -- Σ aws_amortized where amortized<<public (SP/RI netting artifact)
  findings_high     INTEGER,
  findings_med      INTEGER,
  findings_low      INTEGER,
  matrix_decided_pct REAL,         -- share of $ with a pricing-matrix decision source

  catalog_vintage   TEXT,          -- catalog age at projection time
  assumptions_version TEXT,
  engine_version    TEXT,
  rolled_up_at      TEXT           -- when this row was computed
);

-- Composition breakdowns (lens B): many rows per job, long format.
CREATE TABLE IF NOT EXISTS job_analytics_breakdown (
  job_id       TEXT REFERENCES jobs(id) ON DELETE CASCADE,
  dimension    TEXT,   -- 'service' | 'charge_type' | 'decision_type' | 'region'
  key          TEXT,   -- e.g. 'AmazonEC2', 'compute', 'SKU', 'us-east-1'
  aws_usd      REAL,
  gcp_usd      REAL,
  savings_usd  REAL,
  rows         INTEGER,
  PRIMARY KEY (job_id, dimension, key)
);
```

Both guarded by a `schema_migrations` row (`create_job_analytics_2026_11`), same idempotent pattern as the existing migrations in `internal/db/db.go`.

### 1.2 CRUD lifecycle — "fully handled"

| Event | Hook (existing code) | Action |
|---|---|---|
| **Create / finalize** | watcher finalize (where `QueryAWSSpend` → `UpdateJobDoneWithGCPOD` runs) | compute rollup, `UPSERT job_analytics` + replace breakdown rows |
| **Update / recalculate** | `recalculate` + `regenerate-report` admin handlers | re-run the same rollup upsert (idempotent on `job_id`) |
| **Delete** | `SoftDeleteJob` / hard delete | `ON DELETE CASCADE` clears both tables; soft-delete also removes rollup so it leaves the corpus aggregates |
| **Line-item override** | `overrideLineItem` / delete override | mark rollup stale → recompute on next read, or recompute inline |
| **Backfill** | one-off admin command, mirrors `internal/jobs/backfill.go` | walk all `done` jobs missing a `job_analytics` row, compute from their duckdb |

Rollup computation is **pure read** against the job's duckdb (reuse `runDuckDB`), so it can always be re-derived — the rollup is a cache, the duckdb is source of truth. A `POST /api/admin/analytics/rebuild` re-derives the whole corpus on demand.

---

## 2. Metric catalog — the parameters, by lens

Each metric lists its **source** so implementation is mechanical.

### Lens A — Deal economics (GTM)
| Metric | Source |
|---|---|
| Portfolio AWS analyzed, projected GCP (OD / 1yr / 3yr CUD) | `job_analytics` Σ |
| Net savings $ and % — **OD headline, CUD shown alongside** | derived |
| Win-rate: % of bills above a savings threshold (configurable) | `savings_pct_od` histogram |
| Savings distribution (`<0 / 0–20 / 20–40 / 40–60 / 60%+`) | bucket of `savings_pct_od` |
| Top-prospect leaderboard by savings $ | `job_analytics` sort |
| AWS vs GCP spend over time; savings trend | `created_at` bucketed |
| Slice by bill-size band / region / vertical | breakdown + (vertical needs new prospect metadata — see §5) |

### Lens B — Spend composition (pricing engineering)
| Metric | Source |
|---|---|
| AWS spend & savings **by service** (top N + "Other") | `job_analytics_breakdown dimension='service'` |
| By **charge-type** (compute/storage/network/db/ops/other) | `dimension='charge_type'` (map `mechanic_group`→category) |
| **Decision-type mix** (SKU / FORMULA / PASSTHROUGH / IGNORE share of $) | `dimension='decision_type'` from `strategy` |
| Region mix | `dimension='region'` |
| Services that cost *more* on GCP (negative savings) | breakdown where `savings_usd<0` |

### Lens C — Projection quality (product / accuracy)
| Metric | Source |
|---|---|
| Deterministic vs LLM vs passthrough vs ignored (% of $) | `llm_rows` / `deterministic_rows` / `passthrough_rows` / `ignored_rows` |
| Confidence distribution weighted by $ | `confidence_band` |
| Mapping coverage % (real target vs NULL/Manual-Review) | `coverage_pct`, `unmapped_usd` |
| Outlier / finding rate by severity | `findings_high/med/low` |
| Long-tail services leaking to `misc` | breakdown service rows with group=`misc` |
| Catalog vintage at projection time | `catalog_vintage` |

### Lens D — Methodology & trust
| Metric | Source |
|---|---|
| Pricing-matrix coverage ("decision source: matrix v1" %) | `matrix_decided_pct` |
| **SP/RI netting exposure** — $ of apparent savings that is amortization artifact | `sp_ri_exposed_usd` |
| Assumption sensitivity (which assumption moves the number most) | `assumptions` + re-price deltas (Phase 4, heaviest) |
| Self-heal loop throughput: findings raised/accepted/rejected over time | existing `review_findings` table |

---

## 3. API surface

Read endpoints (all admin-guarded, served from the rollup):
- `GET /api/admin/analytics` — portfolio summary + trends (lens A). *(extends today's endpoint)*
- `GET /api/admin/analytics/composition?dimension=service|charge_type|decision_type|region&window=30d|90d|all`
- `GET /api/admin/analytics/quality`
- `GET /api/admin/analytics/methodology`
- Shared query params: `window`, `from`/`to`, `min_bill`, `region`, `vertical` (segmentation).

Write/management endpoints (the CRUD surface):
- `POST /api/admin/analytics/rebuild` — re-derive whole corpus (admin, audited).
- `POST /api/admin/analytics/jobs/{id}/refresh` — re-roll one job.
- rollup upsert/delete are **internal** (called by lifecycle hooks, not HTTP).

---

## 4. Frontend IA

One Analytics route, four sub-sections (tabs or anchored sections), reusing the house palette + the `BigStat`/`ChartCard` components already built:
1. **Economics** (default) — the Phase-1 page already shipped.
2. **Composition** — service/charge-type/decision-type/region treemaps + bars, OD/CUD toggle.
3. **Quality** — coverage gauge, deterministic-vs-LLM split, confidence-by-$, finding-rate.
4. **Methodology** — matrix coverage, SP/RI exposure callout, assumption sensitivity, review-loop throughput.

Global controls (one row): time window, min-bill filter, OD⇄CUD basis toggle, region/vertical segment.

---

## 5. Open gaps that need a data source, not just code
- **Vertical / company-size segmentation**: today we store only a prospect *name*. Real vertical slicing needs a `prospect_metadata` field (industry, size band) captured at upload or inferred. Flag for decision — ship without it, add later.
- **Assumption sensitivity** (lens D) requires re-pricing under perturbed assumptions — expensive; defer to Phase 4 or approximate from stored per-assumption elasticity.
- **SP/RI exposure** needs `public_on_demand_cost` alongside `amortized` in `aws_li_catalog`; verify the column exists (the global audit references amortized only).

---

## 6. Phasing
1. **Phase 1 — Economics (done).** jobs-table totals, trends, leaderboard, distribution. *Already on `AdminAnalytics.tsx`; fold into the tabbed shell.*
2. **Phase 2 — Rollup + Composition.** Schema + lifecycle hooks + backfill + lens B. The keystone; everything else reads the rollup.
3. **Phase 3 — Quality.** lens C off the same rollup.
4. **Phase 4 — Methodology.** lens D; assumption sensitivity last.

---

## 7. Decisions baked (per "obviously all, fully handled")
- Materialized rollup (not on-the-fly sweep). ✅
- OD-vs-OD headline, CUD columns shown alongside, SP/RI exposure surfaced separately. ✅
- All four lenses live in one tabbed Analytics surface (distinct from System Health). ✅
- Full CRUD lifecycle via finalize/recalculate/delete/override hooks + rebuild/backfill. ✅

Still needs a user call: vertical/size segmentation data source (§5), and whether win-rate threshold is fixed (e.g. 20%) or admin-configurable.
