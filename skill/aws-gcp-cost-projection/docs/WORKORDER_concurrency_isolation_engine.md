# WORKORDER — Concurrency, Isolation & the Engine Abstraction

**Status:** DESIGN — awaiting sign-off before any code moves
**Author:** architecture pass, 2026-11-12
**Scope:** `internal/jobs` (Go orchestration), `scripts/orchestrate.go` embedded driver, `spawner.go`
**Goal:** take the pipeline from a single-box, single-identity `agy`-CLI test harness to a backend that can run **many tenants' jobs concurrently with true isolation and without one tenant degrading another** — while keeping a cheap local-dev path on the same codebase.

---

## 0. TL;DR of the decision being asked for

1. Adopt an **`Engine` interface** with two implementations selected by config — `CLIEngine` (today's `agy`, for local/dev/test) and `APIEngine` (Gemini/Vertex API, for production). **One codebase, config switch. No commented-out "prod blocks", no fork.**
2. Put a **durable queue + bounded worker pool + per-tenant fair scheduler** in front of job execution, replacing today's unbounded `go h.runJob(...)`.
3. Give each tenant its **own quota lane** (separate credential/project or a central per-tenant token bucket) so the model-quota contention we measured goes away.

Everything below is the justification and the implementation plan.

---

## 1. Why today's design cannot scale (measured, not theoretical)

Observed on 2026-11-12 from live job logs (`cur-web-data/jobs/*`):

- Two jobs (`c5d8746e`, `c3943a22`) started the **Mapping** phase in the same second (10:34:19 / 10:34:25). Each ran ~27 min, then each did a ~28–35 min gate retry → **~1 hour of Phase 2 per job, overlapping.**
- Auth in `agy-internal.log`: `email=arnavchaurasia5@gmail.com, authMethod=consumer` — the **free consumer Gemini tier**, one identity shared by every job.

Three structural walls:

| Dimension | Today | Why it breaks at scale |
|---|---|---|
| **Identity / quota** | One Google identity (consumer OAuth, or one `ANTIGRAVITY_API_KEY`) shared by all jobs — see [`spawner.go`](../../../internal/jobs/spawner.go) `skillEnv()` / lines 20–23 | Every job draws on one RPM/TPM bucket. N concurrent jobs = each throttles the others. No per-tenant isolation is even expressible. |
| **Compute** | One **detached OS process per job**, and that process is Antigravity (Electron in Node mode) — `startDetached()` | Electron is hundreds of MB resident. You cannot run thousands of them on a host; the kernel, not a scheduler, is your admission control. |
| **State** | `agy` defaults to a **shared scratch sandbox** (`~/.gemini/antigravity-cli/scratch`); the `--add-dir jobDir` hack exists *only* to stop artifacts "leaking between repos" (`startDetached` comment) | Isolation depends on every prompt obeying an absolute-path instruction. One slip writes into shared scratch. Fragile by construction. |

Additional scaling hazards in the current control plane:

- **Unbounded fan-out:** jobs launch as `go h.runJob(...)` ([`handler.go:314`](../../../internal/jobs/handler.go), `:445`, `:636`). Nothing caps total in-flight work; the only limiter is a **per-user** count that **admins bypass** ([`handler.go:189`, `:201`](../../../internal/jobs/handler.go)).
- **No fairness:** a single tenant submitting 50 bills can starve everyone else.
- **Liveness tied to a PID on one host:** the watcher polls a local PID. There is no way to move a job to another worker or survive a host restart.

**Conclusion:** the `agy` CLI is an excellent *development and skill-iteration* tool and a poor *production execution substrate*. The fix is not to delete it — it is to put it behind an interface and build a second, API-based implementation for production.

---

## 2. Target architecture

```
                 ┌──────────────┐
  HTTP upload →  │  API server  │  enqueue(job, tenant)   (stateless, N replicas)
                 └──────┬───────┘
                        │ durable enqueue
                 ┌──────▼───────────────────────────┐
                 │  Job queue (Postgres SKIP LOCKED  │  at-least-once, lease + heartbeat
                 │  / Cloud Tasks / Pub-Sub)         │
                 └──────┬───────────────────────────┘
                        │ lease (fair scheduler: global cap + per-tenant cap + weighted RR)
                 ┌──────▼───────┐   ┌──────────────┐
                 │  Worker pool │ … │  Worker pool │   (horizontal: k8s pods / Cloud Run)
                 └──────┬───────┘   └──────┬───────┘
                        │ per phase
                 ┌──────▼───────┐
                 │  Engine      │  CLIEngine (dev) | APIEngine (prod)
                 └──────┬───────┘
                        │ model calls
                 ┌──────▼─────────────────────────────┐
                 │ Per-tenant quota lane               │  token bucket / project-per-tenant
                 │ 429 → backoff+jitter, respect Retry-After
                 └─────────────────────────────────────┘

  Data isolation: per-job ephemeral workdir (object storage) + per-job DuckDB;
  shared catalog is read-only. Blast radius = one job.
```

### 2.1 The `Engine` interface (the core seam)

The pipeline already has the *shape* of this seam — `validateEngine`, `engineChoice`, `default_engine`/`allowed_engines` in the DB ([`handler.go:166`](../../../internal/jobs/handler.go)), `EngineVersion()`. Today "engine" is only a **model label** and everything funnels into `agy`. Make it a real backend boundary:

```go
// internal/jobs/engine/engine.go
type PhaseRequest struct {
    JobDir   string
    Phase    Phase       // num, name, prompt, gate
    Tenant   TenantID
    Deadline time.Time
}
type PhaseResult struct {
    DurationSeconds int
    TokensIn, TokensOut int
    Err error
}
type Engine interface {
    // RunPhase executes exactly one phase's LLM portion. Deterministic
    // pre/post scripts and gates stay in the orchestrator — the Engine only
    // owns the model call. This keeps the gate/retry logic identical across
    // engines.
    RunPhase(ctx context.Context, req PhaseRequest) (PhaseResult, error)
    Version() string
    // Admit blocks until this tenant has quota to make a model call, or ctx
    // is cancelled. For CLIEngine this is a local semaphore; for APIEngine it
    // is the per-tenant token bucket.
    Admit(ctx context.Context, tenant TenantID) (release func(), err error)
}
```

Two implementations:

- **`CLIEngine`** — wraps today's `agy` spawn (`startDetached` / `run_agy` in the embedded driver). `Admit` = a small local semaphore (default 1–2). **This is the default for local dev + CI + skill iteration.** Zero API cost, offline-friendly, exercises the exact prompts.
- **`APIEngine`** — calls Gemini/Vertex `streamGenerateContent` directly over HTTP from Go (no Electron, no per-job process, no shared scratch). `Admit` = per-tenant token bucket backed by the tenant's provisioned throughput. **This is production.**

**Selection:** one env/config value, reusing the existing settings surface:

```
CUR_WEB_ENGINE = cli | api        # default cli locally, api in prod deploy
```

Wire it where the Spawner is constructed in `main.go`; `allowed_engines`/`default_engine` in the admin DB continue to gate *which model*, orthogonally.

> **Explicitly rejected:** `if production { ... } else { ... }` scattered through the orchestrator, build-tag forks of the driver, or commenting prod code in/out. All three guarantee drift and untested paths. The interface + one construction-site switch is the industry-standard answer and keeps both paths compiled and testable at all times. (Build tags are acceptable *only* to keep heavy CLI-only deps out of the prod image — still behind the same interface.)

### 2.2 Isolation — the four axes

1. **Identity/quota isolation (the one that bit us).** Each tenant gets its own lane:
   - *Preferred:* a credential/GCP project per tenant (clean quota accounting, clean cost attribution).
   - *Or:* a central **per-tenant token bucket** over provisioned throughput, keyed by `TenantID`, enforced in `Engine.Admit`. Never a single global identity again.
2. **Compute isolation.** Production worker = one job at a time in a **container/pod** with CPU/memory limits (cgroups). A job OOMing or pegging CPU cannot touch its neighbors. Locally, the semaphore + separate process is enough.
3. **Data isolation.** Per-job working dir on object storage (GCS prefix per job) hydrated into the worker's ephemeral local FS; per-job `projection.duckdb`; the GCP **catalog is mounted read-only and shared** (DuckDB read-only allows many concurrent readers — already the pattern in [`duckdb.go:53`](../../../internal/jobs/duckdb.go)). Nothing a job writes is visible to another job.
4. **Blast radius / fault isolation.** A soft-script crash, a gate failure, or a worker kill affects exactly one job. At-least-once queue semantics + idempotent phases (checkpoint resume already exists via `phase_checkpoint.json`) make a dropped worker a retried job, not a lost one.

### 2.3 Concurrency & fairness

- **Durable queue** replaces `go h.runJob`. Options, cheapest first: Postgres `SELECT … FOR UPDATE SKIP LOCKED` (you already run a DB), then Cloud Tasks / Pub-Sub if you outgrow it.
- **Bounded worker pool**, horizontally scalable. Global concurrency = pool size, not goroutine count.
- **Admission policy** at lease time:
  - global cap (protects the model backend + infra),
  - **per-tenant cap** (so one tenant can't hold every worker),
  - **weighted fair scheduling** (round-robin across tenants with pending work) so latency is fair under load.
- **Backpressure:** on `429 / RESOURCE_EXHAUSTED`, exponential backoff **with jitter**, honor `Retry-After`. Today the CLI hides this inside `agy`; `APIEngine` must own it explicitly and record it (see §2.4).

### 2.4 Observability (reuse what's already good)

The pipeline already has a **well-built structured diagnostics channel** — `scripts/diagnostics.py` → `projection-audit/diagnostics.jsonl` (levels error/warn/info; code, source, rows, usd, phase, detail). **Do not reinvent it — extend it.** Add, per phase: `queued_at`, `leased_at`, `model_wait_seconds` (time spent in `Admit`/backoff vs. actually generating), `tokens_in/out`, `tenant_id`. Emit these to the same channel and roll them up centrally (the analytics rollup path already exists, `analytics_rollup.go`). This makes "is this slow because of the model or because of queue contention?" answerable per phase per tenant — which is the exact question that started this work.

> **Timing-accuracy prerequisite:** the per-phase duration bug (recorded duration excludes the retry pass; see the separate finding on `write_phase_checkpoint` in `orchestrate.go:1908` using a frozen `phase_entry["duration_seconds"]`) must be fixed first, or all phase-timing telemetry undercounts any retried phase by ~½.

---

## 3. Local vs production — concrete matrix

| Concern | Local / dev / CI | Production |
|---|---|---|
| Engine | `CLIEngine` (`agy`) | `APIEngine` (Vertex/Gemini HTTP) |
| Selection | `CUR_WEB_ENGINE=cli` (default) | `CUR_WEB_ENGINE=api` |
| Identity | developer's `agy` login / one key | per-tenant credential or token bucket |
| Concurrency | in-process semaphore (1–2) | queue + worker pool + fair scheduler |
| Isolation | separate process + jobDir | pod + cgroup + object-storage workdir |
| Queue | synchronous or tiny in-mem | durable (Postgres SKIP LOCKED / Cloud Tasks) |
| Cost | $0 (dev quota) | metered per tenant |

**Same source, same orchestrator, same gates, same prompts, same diagnostics.** Only the `Engine` impl and the admission front-end differ, and both differences are chosen by config at startup.

---

## 4. Incremental migration plan (low-risk, each step shippable)

1. **Extract `Engine` interface; wrap today's `agy` path as `CLIEngine`.** Pure refactor, zero behavior change. Add a contract test that runs a fixture bill through `CLIEngine` and asserts report output unchanged.
2. **Fix the per-phase timing bug** (`write_phase_checkpoint` recomputes elapsed at write time at all call sites) so telemetry is trustworthy before we optimize against it.
3. **Add `Engine.Admit` + a token-bucket admission gate** in the orchestrator loop. Even for `CLIEngine` this stops two jobs from colliding on one account *today* — immediate win, no API work.
4. **Introduce a durable queue + bounded worker pool**; replace `go h.runJob` with enqueue. Keep `CLIEngine` as the executor so this is testable locally.
5. **Build `APIEngine`** against Vertex AI: streaming, explicit 429 backoff+jitter, per-tenant token bucket, emit the new timing/token diagnostics. Flip a staging tenant to `CUR_WEB_ENGINE=api`.
6. **Per-tenant fair scheduling + per-tenant quota lanes**; load-test with multi-tenant submission; verify no cross-tenant starvation via the rolled-up `model_wait_seconds`.

**Acceptance criteria:** (a) two tenants submitting simultaneously show independent phase latencies (no ~2× inflation like the measured 10:34 collision); (b) one tenant flooding N jobs never pushes another tenant's p95 queue wait beyond a set bound; (c) a killed worker results in the job being re-leased and completed, not lost; (d) `CLIEngine` remains the zero-config local default and all existing tests pass against it.

---

## 5. Out of scope here (tracked elsewhere)
- The Phase-2 **mapping_coverage retry waste** (LLM retry changes nothing; `ensure_full_coverage` backstop fixes it in seconds) — see the retry-cause finding; it is an orchestration-logic fix independent of this architecture, but it compounds the cost under concurrency so should land early.
- Per-SKU pricing accuracy work (existing workorders).
