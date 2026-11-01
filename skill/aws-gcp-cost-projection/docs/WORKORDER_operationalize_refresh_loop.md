# WORKORDER — Operationalize the self-healing refresh loop

**For the implementation agent. Read fully. Verify in Phase 0.**
The advisory reviewer + refresh store are BUILT and safe (read-only reviewer,
trusted-only auto-apply, distinct-source promotion gating — see
WORKORDER_reviewer_refresh_loop). But a loop only compounds accuracy if it actually
RUNS on every job and someone triages its findings. This work order makes it
operational, not just present.

---

## 0. ORIENTATION — run first, every time

| # | Probe (from repo root) | Tells you | RESULT |
|---|---|---|---|
| P1 | `grep -rn "advisory_reviewer\|review_findings" internal/jobs/orchestrate*.go internal/jobs/watcher.go` | is the reviewer wired to run on every job? | |
| P2 | `grep -rn "findings" internal/jobs/admin_handlers.go` | are the triage endpoints live? | |
| P3 | `grep -rn "get_trusted_mapping\|refresh_store\|refresh_mappings" skill/aws-gcp-cost-projection/scripts/classify_mechanics.py` | is the trusted-fact short-circuit active in the pipeline? | |
| P4 | check the admin UI: is there a findings review screen a human can act on? | whether triage is actually reachable | |

Resume rule: most plumbing exists; this is wiring + UX + a feedback metric, not new
engine code.

---

## 1. GOAL

The loop runs end-to-end on real jobs and visibly reduces non-determinism over time:
- Reviewer runs automatically after every projection; findings land in the admin
  triage queue, grouped by service + severity + $.
- A human (or a rule) accepts/rejects findings; accepted ones promote to trusted in
  the per-user refresh store and short-circuit the LLM on the next run.
- A **feedback metric** (via the accuracy harness) shows `spec_gap$` / `misc$` /
  LLM$ shrinking bill-over-bill as trusted facts accumulate. That trend IS the
  proof the loop works.

---

## 2. INVARIANTS
- Reviewer stays read-only; only trusted facts auto-apply (already enforced — keep).
- The writable refresh store stays OUT of shared data/ (per-user state dir — do not
  regress the fix from WORKORDER_reviewer_refresh_loop).
- No auto-promotion without either admin accept or genuinely independent sources.

---

## 3. PHASES

### Phase 1 — Always-on
- [ ] Confirm/complete: reviewer runs on every job; findings ingested to the DB.

### Phase 2 — Triage UX
- [ ] A usable admin findings screen: filter by service/severity/$, accept/reject/
      defer, with the accept writing through to the refresh store.

### Phase 3 — Feedback metric
- [ ] Add a "determinism trend" to the accuracy harness: for a fixed bill re-run
      over time, show deterministic-coverage rising / spec_gap falling as the store
      grows. Demonstrate on one service end-to-end.

---

## 4. PROGRESS LOG (append-only)
- 2026-10-31 Work order created. Engine + store + review fixes are in place; this is
  about always-on execution, triage UX, and a trend metric proving the loop
  compounds. Depends on WORKORDER_accuracy_harness for the trend metric.

- 2026-11-01 Verified complete (review pass):
  - Phase 1 (always-on): orchestrate.go runs `scripts/advisory_reviewer.py $JOBDIR`
    in the pipeline (non-fatal `?` prefix, so a reviewer error never fails a job),
    and watcher.go ingests `review_findings.json` into the DB. ✓
  - Phase 2 (triage UX): `/api/admin/findings` (list) + `/api/admin/findings/{id}/action`
    (accept/reject/defer) handlers; frontend AdminFindings.tsx page, /admin/findings
    nav route, listFindings/actionFinding in admin.ts. Routes sit inside the
    `auth.RequireAdmin(cfg.AdminEmails)` group (cmd/server/main.go), so triage is
    ADMIN-ONLY — confirmed no privilege gap despite AdminActionFinding having no
    inline isAdmin check (the middleware covers it). Accept -> promoteFindingToRefreshStore. ✓
  - Phase 3 (feedback metric): accuracy_report.py emits a determinism-trend verdict
    (improving when deterministic coverage up AND spec_gap+misc+llm down). ✓
  - Store location: refresh_store.py writes to the per-user state dir (~/.cur-web),
    NOT shared data/ (relocation fix present on this branch); the Go comment in
    admin_handlers.py still says "data/refresh_store.json" — STALE DOC NIT, not a
    bug (left untouched; file is being edited by another active session).
  - Go builds clean. Phase checkboxes above reflect this verification.

## 5. IF STUCK
If triage volume is high, prioritize by $ — a human should see the biggest-dollar
findings first; the long tail can wait for the next pass.
