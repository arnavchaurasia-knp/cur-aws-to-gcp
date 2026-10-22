# DESIGN — Where state lives: fixing D-12 (and D-8) given the Render deployment

Status: **proposal, for decision.** Single-issue design.

Audit findings addressed: **D-12** (rate vintage neither gated nor disclosed)
and **D-8** (concurrent jobs race on a shared cache). The hosting details show
these are the same issue, and that my original diagnosis of D-12 was wrong.

---

## 1. What I got wrong

I reported D-12 as *"the weekly catalog refresher has not run for 82 days —
an operational question"*. It is not operational. It is structural, and the
refresher could never have worked.

`Dockerfile:66`:

```dockerfile
RUN python /app/skill/aws-gcp-cost-projection/scripts/build_catalog_index.py
```

The catalog is **compiled at image-build time** from SKU datasets committed to
git, and baked into `/app/skill/aws-gcp-cost-projection/data/`. So:

> **Catalog freshness = git freshness.** `CATALOG_META.json` says
> `fetched_at: 2026-07-27` because that is when a human last ran
> `refresh-catalog.sh` locally and committed the output.

`internal/jobs/catalog_refresher.go` runs weekly and writes into that same
directory — which on Render is **the image layer**. Writable at runtime,
discarded on the next deploy. With continuous deployment on every push to
`main`, any refresh it performs is erased, often within hours. The refresher
is not broken; it is writing to a disk that does not survive.

---

## 2. The three storage classes, and what the pipeline assumes about each

| path | what Render does with it | what the code assumes |
|---|---|---|
| `/app/skill/.../data/` | image layer — **reset on every deploy** | durable, mutable |
| `/data` (`DATA_DIR`) | container disk, **no `disk:` block in `render.yaml`** — wiped on restart *and* on the free tier's 15-minute spin-down | durable |
| Turso + R2 | genuinely durable | optional (`sync: false`) |

Both of the first two are wrong, and the third is opt-in.

### What is actually lost

| file | role | on redeploy |
|---|---|---|
| `data/catalog.duckdb` | 85,952 GCP SKUs, all pricing | reverts to the image's build-time copy |
| `data/CATALOG_META.json` | the vintage stamp | reverts |
| `data/learned-product-codes.json` | **self-building** cache; its own docstring says *"grows across bills so the model is asked less over time"* | **reverts to the committed copy** |
| `data/resolved_skus.json` | resolved-SKU cache | reverts |
| `/data/jobs/*` | job history, artifacts | **gone**, unless R2 is configured |

The learned-codes cache is the sharpest case: it is designed to accumulate, it
is written per job (`orchestrate.go:130`), and on this deployment it is
**truncated back to the committed version on every push to main**. The model
gets re-asked forever, and the race in D-8 is a race over a file that was never
going to persist anyway.

And `internal/storage/storage.go:43` falls back silently:

```
"cloud storage not configured; using local filesystem only"
```

On a free-tier container with no disk, "local filesystem only" means "until the
next restart".

---

## 3. The fix: stop fighting the immutable image; make each state class live where it belongs

Three classes of state, three different homes. The mistake is that all three
currently share one.

### Tier A — Reference data (catalog, SKU rates): keep it immutable, gate it at build

The image model is *right* for pricing data: every container then prices
identically and reproducibly, and a projection can name the exact catalog that
produced it. Runtime refresh would break that — two containers could price the
same bill differently.

So keep baking it, and make freshness a **release property**:

1. **CI gate.** The build fails if `CATALOG_META.fetched_at` is older than
   `CATALOG_MAX_AGE_DAYS` (start at 14). A stale catalog becomes something that
   blocks a deploy, not something nobody notices for 82 days.
2. **Stamp the vintage onto every job** at rate-fill time, so the projection
   records which catalog produced it.
3. **Disclose it.** One line in the report: *"Priced against the GCP catalog as
   of 2026-07-27."* That is the honest statement and it is cheap.
4. **Expose it on `/health`** — add `catalog_fetched_at` and `catalog_age_days`
   to the existing health endpoint, so staleness is visible to monitoring
   instead of requiring someone to read a JSON file inside a container.

**Delete `catalog_refresher.go`'s runtime write**, or repoint it at a durable
store. Today it burns CPU on a 512 MB box to produce a result that is thrown
away.

### Tier B — Learned knowledge (product codes, resolved SKUs): propose, don't mutate

These genuinely want to accumulate. But writing them live from a job is what
creates the D-8 race, and on this host it does not persist regardless.

Invert it:

- A job that learns something writes a **proposal** into its own job directory
  (`learned_proposals.json`) — no shared-state write, so the race disappears.
- A separate promotion step collects proposals and merges them into the
  committed `data/learned-product-codes.json`, which is then baked into the
  next image.
- Promotion can be a periodic task or a human review. Either way the learning
  becomes **reviewable** — which is what I argued for in
  `DESIGN_field_registry_and_schema_mapping.md` for model-authored facts, and
  the same argument applies here.

This fixes D-8 by removing the shared write entirely rather than making it
atomic, and it makes the cache durable by routing it through the one thing that
does persist: the repository.

### Tier C — Job state (database, artifacts): require the durable backends

`DATABASE_URL` and the S3/R2 variables are `sync: false`, and both fall back
silently to the ephemeral filesystem. On the free tier that means job history
can disappear between a user submitting a bill and coming back to it after the
container sleeps.

- At boot, if `APP_ENV`/`RENDER` indicates production and either backend is
  unconfigured, **log an explicit warning naming what will be lost** — or
  refuse to start, if you would rather it be loud.
- Keep the local fallback for development, where it is correct.

---

## 4. Why this ordering

The Tier A changes are small and immediately useful: a CI gate, a stamp, a
report line, a health field. They make the staleness visible and bounded
without changing how pricing works.

Tier B is a modest refactor that deletes a class of bug rather than patching
it.

Tier C is a configuration decision more than a code change, but it needs an
explicit answer: **is job history expected to survive a redeploy today?** If
yes, the backends must be mandatory; if no, the product should say so.

---

## 5. Open questions I cannot answer from the code

1. **Is R2/Turso actually configured on the live service?** `render.yaml` has
   them as `sync: false`, so the dashboard is the source of truth. If unset,
   every job and artifact is lost on each deploy — which, with CD on `main`,
   may be daily.
2. **Concurrency under 512 MB.** Each job runs Python plus DuckDB (capped at
   280 MB) plus an agy subprocess. Two concurrent jobs cannot both hold 280 MB
   on a 512 MB box. Is there a cap on simultaneous jobs? If not, the OOM
   behaviour under two uploads is worth testing deliberately rather than
   discovering.
3. **Cold-start interaction.** The free tier sleeps after 15 minutes. If a job
   is running when the container is idle-scaled, what happens to it? The
   watcher tracks a PID that will not survive.

---

## 6. What this does not fix

- The catalog is still only as fresh as the last commit. The CI gate bounds the
  staleness; it does not automate the refresh. Automating it means a scheduled
  job that runs `refresh-catalog.sh` and opens a PR — worth doing, separate
  work.
- Nothing here improves pricing accuracy. It makes the vintage of the prices
  knowable and bounded, which is a precondition for trusting an accuracy
  measurement, not a substitute for one.
