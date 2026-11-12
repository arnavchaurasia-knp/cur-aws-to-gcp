# WORKORDER — Live UI updates (no manual refresh)

**Goal:** Views that show server-changing state (job status, run history, summary,
job lists) must update **live**, without the user pressing refresh. Today a job's
terminal transition (failed / done / cancelled) often only appears after a manual
page reload, even though the notification bell already updated. Reuse the existing
notifications polling hook as the live signal, and make customer-facing pages
resilient on their own.

Implement the tasks below **exactly**. Do not refactor unrelated code. After each
task, run the verification in the last section.

---

## Background: the hook and its ONE hard constraint

File: `frontend/src/lib/notificationsContext.tsx` (DO NOT MODIFY in this workorder).

It already exports:

- `useNotifications()` → `{ feed, unreadCount, loading, lastViewedTimestamp, refresh, markAllAsRead }`.
  The provider re-polls `feed` **every 12s + on tab focus/visibility + on route change
  + on a custom event**. When `feed` changes, a job's state very likely changed too.
- `triggerNotificationRefresh()` → fire-and-forget; forces an immediate `feed` refresh
  from anywhere (call it right after a mutation so the bell + any feed-driven view update now).

**HARD CONSTRAINT — read before you code:** the feed is fetched from
`/api/admin/notifications` (see `getAdminNotifications` in `frontend/src/api/admin.ts`)
and the provider only polls when `isAdmin` is true (`NotificationProvider` is mounted with
`isAdmin={isAdmin}` in `frontend/src/App.tsx`, and `refresh()` returns early when `!isAdmin`).

Therefore:
- For **admin** users, `feed` changes on the 12s cadence → use it as the live trigger (Pattern A).
- For **non-admin / customer** users, `feed` NEVER changes → Pattern A alone does nothing.
  Customer-facing pages MUST ALSO have a resilient local poll (Pattern C).

A page used by both (e.g. the job detail page) needs **both** Pattern A and Pattern C.

---

## The three patterns

### Pattern A — refetch when the notification feed changes (admins get instant updates)

```tsx
import { useNotifications } from '../lib/notificationsContext'
// ...inside the component:
const { feed: notifFeed } = useNotifications()
useEffect(() => {
  if (!id) return
  let cancelled = false
  refetchJob()                 // the same fetch your initial load / poll uses
  return () => { cancelled = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
}, [notifFeed, id])
```

### Pattern B — trigger a feed refresh after a state-changing mutation

```tsx
import { triggerNotificationRefresh } from '../lib/notificationsContext'
// ...after a successful mutation (delete/cancel/retry/refine/regenerate/recalculate):
triggerNotificationRefresh()
```

### Pattern C — resilient local poll (works for everyone, incl. non-admins)

Rules for any `setTimeout`/`setInterval` poll that reads job state:
1. **Reschedule in a `finally`, not only on the running branch** — a thrown
   `getJob()` (transient network blip) must NOT kill the loop permanently.
2. Stop rescheduling ONLY on a definite "gone" signal (HTTP 404 / job-not-found),
   not on any error.
3. After a terminal status, do ONE final fetch of dependent data (runs/summary) and
   then stop — do not keep hammering.
4. Add a `visibilitychange` + `focus` listener that triggers one refetch (so returning
   to the tab updates immediately, same UX the bell gives admins).
5. Always guard async setState with a `cancelled` flag cleaned up in the effect return.

---

## TASK 1 — Job detail page: live terminal-state updates (HIGHEST PRIORITY)

File: `frontend/src/pages/JobStatus.tsx`

The page already renders every terminal state (`job.status === 'done'` ~L621,
`=== 'failed'` ~L873, `=== 'cancelled'` ~L899) — the ONLY problem is that `job` is not
re-fetched after the local poll stops or dies. The local poll (search `const poll = async`
/ `setTimeout(poll, 5000)`) reschedules **only** inside the `if (j.status === 'running' ||
j.status === 'pending')` branch, and its `catch` does NOT reschedule.

Do all of the following:

1. **Extract a single `refetchJob` callback** (use `useCallback`) that does what one poll
   tick does today: `getJob(id)` → `setJob(j)` → `setLoadError(null)` → update the list row
   (`setJobs(prev => prev.map(o => o.id === j.id ? { ...o, status: j.status } : o))`), and
   when `j.status === 'done'` fetch `getRuns(id)` (+ `setCurrency`) and `getSummary(id)`.
   Guard every setState behind a `cancelled` ref/flag. Reuse this from both the poll and
   Pattern A so there is one source of truth.

2. **Pattern A:** consume `useNotifications()` and add the `useEffect` on `[notifFeed, id]`
   that calls `refetchJob()`. (Admins now see failed/done/cancelled within ≤12s, no refresh.)

3. **Pattern C — harden the existing poll:**
   - Move the `setTimeout(poll, 5000)` reschedule so it runs whenever the status is still
     non-terminal, INCLUDING after a caught error (wrap in `try/finally`), EXCEPT when the
     fetch reported 404 / job-not-found (then stop and show the existing `loadError`).
   - Keep the existing "on first `done`, fetch runs+summary" behavior (dedupe with the
     `refetchJob` done-branch so summary isn't fetched twice; a `prevStatus` guard is fine).
   - Add a `visibilitychange` + `focus` effect that calls `refetchJob()` once (covers
     non-admins returning to the tab).

4. **Pattern B:** after each of these succeeds, call `triggerNotificationRefresh()`:
   - `handleDelete` (after `await deleteJob(id)`, ~L388)
   - cancel handler (after `await cancelJob(id)`, ~L403)
   - regenerate handler (after `await regenerateReport(id)`, ~L419)
   - recalculate handler (after `await recalculateJob(id)`, ~L438)
   - retry handler (after `await retryJob(id)`, ~L479)
   - refine handler (after `await refineJob(...)`, ~L496)
   This makes the bell + any feed-driven view reflect the action immediately.

**Acceptance:** With the app open on a running job's page (as an admin AND as a
non-admin test user), when the job flips to `failed`, `done`, or `cancelled`, the page
switches to that state's view within ≤12s with NO manual refresh. Killing the network
for one tick does not freeze the page permanently (it recovers on the next tick). A 404
(deleted job) still shows the "Failed to load report" state and stops polling.

---

## TASK 2 — Admin jobs list: instant row updates

File: `frontend/src/pages/AdminJobs.tsx`

It already polls every 25s (`const ticker = setInterval(... refreshActive() ..., 25_000)`).
That is slow for "did my job finish?". Add Pattern A so the list refreshes the moment the
bell feed changes (admins only, which is correct — this page is admin-only):

- Consume `useNotifications()`, add `useEffect(() => { refreshActive() }, [notifFeed])`
  (keep the 25s interval as a backstop). Keep the existing `cancelled`/cleanup conventions.

**Acceptance:** finishing/failing a job updates its row in the admin list within ≤12s
without a manual refresh.

---

## TASK 3 — Customer bills/jobs list (VERIFY, then fix if needed)

File: `frontend/src/pages/Bills.tsx`

Confirm whether this page lists jobs/bills with a **status** column that can change while
the page is open (look at the `filteredBills.map(...)` render and whether a status/badge is
shown). 
- If it shows live-changing status and has no poll → add **Pattern C** (a resilient local
  interval, ~15s, + focus refetch). Do NOT rely on Pattern A here — customers are non-admin,
  so the feed never updates for them.
- If it only shows static/historical data → no change; note "N/A, static" in your report.

**Acceptance:** a customer watching their bills list sees a job go from running → done/failed
without refreshing, OR you have documented that the page shows no live status.

---

## TASK 4 — Sweep for other manual-refresh-only views (report only, fix the obvious)

Grep and eyeball these known polling/stateful spots. For each, decide: already-live /
needs Pattern A / needs Pattern C / N-A. Fix the clearly-broken ones with the matching
pattern; list the rest in your report with a one-line verdict.

- `frontend/src/pages/health/HealthPage.tsx` (`refreshIntervalRef` interval)
- `frontend/src/pages/health/CatalogTab.tsx` (two intervals — refresh/coverage progress)
- `frontend/src/components/JobLogsViewer.tsx` (`setInterval(fetchLogs, 5000)` — ensure it
  stops when the job is terminal and the last lines are in, and reschedules through a
  transient error)
- `frontend/src/pages/ReleaseDashboard.tsx` (uses job APIs — verify it live-updates)
- `frontend/src/pages/ReportOverride.tsx` (uses job APIs — verify)

Do NOT touch: `AdminAssumptions.tsx` / `AdminIntegrations.tsx` — they already call
`triggerNotificationRefresh()` correctly. Do NOT touch `notificationsContext.tsx`.

---

## Global gotchas (apply to every task)

- Every async effect sets state only after checking a `cancelled` flag; clean it up in the
  effect's return.
- Dependency arrays: Pattern A effects depend on `[notifFeed, id]` (and whatever `id` key
  the page uses). Do not add `feed` object identity churn into unrelated effects.
- Never introduce an unconditional infinite poll: terminal + 404 must stop it.
- Do not duplicate network calls per tick (e.g. don't fetch summary on every feed change
  once it's already loaded — guard with a `prevStatus`/"already fetched" check).
- Keep existing optimistic-update code (e.g. the refine handler's immediate
  `setJob(prev => ... status:'running')`) — Pattern B/A are additive.
- TypeScript must pass (`npx tsc --noEmit` in `frontend/`) and the prod build must succeed
  (`npm run build`).

---

## OUT OF SCOPE (do not implement; flag for the human)

- Making the notification feed work for **non-admin** users (a customer-scoped
  `/api/notifications` + polling for non-admins). That is a backend + provider change and a
  product decision. Until it exists, customer-facing pages rely on Pattern C only.

---

## Verification (run after each task; all must pass)

1. `cd frontend && npx tsc --noEmit` → no errors.
2. `cd frontend && npm run build` → succeeds.
3. Manual: open a running job as an admin; let it finish/fail → page updates within ≤12s,
   no refresh. Repeat as a non-admin test user → page still updates (Pattern C / focus).
4. Manual: cancel / regenerate / recalculate a job → the action's new state shows on the
   page and the admin list without a manual refresh.
5. Simulate a one-tick network failure (devtools offline for ~6s) on the job page → the
   page recovers and keeps updating once the network returns (loop did not die).
