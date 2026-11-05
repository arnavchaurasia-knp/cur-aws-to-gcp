# WORKORDER — Directive Layer, Phase 4: decided policies + stale-pin safety + e2e proof

Parent design: `docs/DESIGN_report_refinement.md` (open questions §9). Depends on Phases 1–3
(unified ledger, resolver, `apply_directives.py`, AGY translator, clarification thread, the
five preference verbs) being merged — all green at commit `f6774a6`.

Phase 4 implements the **product decisions** the user made on the design's open questions, plus
the one piece of **new plumbing** they imply (an email/UI notification on a stale pin), and
closes the single remaining **verification gap** (a real bill end-to-end with the flag on).
**No new verbs, no new apply phase, no second pipeline** — everything here sits on Phase-1–3
rails. If a step wants new architecture, it's wrong; re-scope it.

Baseline: `pytest tests/` green (1270), `go test ./internal/...` green. Every step keeps them
green.

---

## 0. Decisions this encodes (read first — these are settled, do not re-litigate)

| Open question (design §9) | DECIDED |
|---|---|
| Directive sharing: per-user or job-shared? | **Per-user.** A user refines → a new report is generated → they share *that report* (the output). No live shared ledger with concurrent editors. |
| Catalog-refresh vs pinned directive | **Pin wins, but surface and ask.** Honor the pin; if a refresh makes it unavailable, email that user + show it in the UI + let AGY ask a contextual follow-up. |
| Confirm-threshold policy | **Confirm by blast radius.** Small changes auto-apply; large $/row changes force an explicit confirm. |

SP/RI netting (stays OD-vs-OD default; discount-vs-discount is a *later* maybe) and the NAT
VM-count number are **not** in this phase — those remain user policy calls, and their verbs
already exist.

---

## 1. Per-user scoping of the ledger (make the decided model explicit)

Today `<job>/directives.json` is per-job. The decision is per-**user**: one user's refinement
must not mutate what another user sees on the same baseline job.

- Partition the ledger by author identity: `<job>/directives/<user_id>.json` (or an
  `author_id` field on each directive + a resolver filter). `load_ledger` /
  `directives.py` gains a `user_id` scope; `apply_directives.py` resolves **one user's**
  ledger against the baseline to produce **that user's** refined projection.
- The baseline projection stays shared and untouched; each user's refined report is derived
  on demand from baseline + their ledger (event-sourced — already the model).
- "Share the report" = export the *generated* report artifact (HTML/PDF/CSV), which Phase-2
  rendering already produces. No new sharing surface; sharing is of the output, confirmed.
- Back-compat: an existing unscoped `directives.json` loads as the job owner's ledger (shim
  in `directives_from_legacy.py`).

Tests: two users' directives on the same job produce two independent refined projections;
neither sees the other's; baseline is unchanged. Go: the refinement handler threads the
authenticated user id into the ledger scope.

---

## 2. Stale-pin detection on catalog refresh  (`internal/jobs/catalog_refresher.go` + a check script)

When a catalog refresh invalidates a pinned directive (classic case: user pinned `C4A`, the
refreshed catalog no longer offers it in that region), **honor the pin but raise it**, do not
silently re-map.

- After `catalog_refresher.go` completes a refresh, run a **revalidation pass** over every
  job that has directives: for each pinned family/shape/region/target directive, re-check
  availability against the new catalog — reuse the Phase-2 validators
  (`compute_family_availability.json`, `resolve_sku`, allowed-targets). A directive that was
  valid and is now unpriceable/unavailable is **stale**.
- A stale directive is **not** dropped and **not** auto-remapped. It is turned into a
  **blocking clarification** in that user's `refinement_thread.json` — the *same* object
  Phase 2 already uses for an under-specified directive:
  `{reason: "catalog_refresh_invalidated", question: "C4A isn't available in <region> anymore — nearest (<x>), global, or keep current?", options:[...], blocking:true, directive_ref:<id>}`.
  The pinned row holds its last-known mapping with a `review_flag` ("pinned target no longer
  offered — awaiting your choice") until answered.
- Answering the clarification flips the directive and reruns (Phase-2 trigger, unchanged).

Tests (Go + py): a refresh that removes a pinned family marks the directive stale, writes a
blocking clarification, flags the row, and does **not** change the projected cost until the
user answers.

---

## 3. Notification trigger — email + UI  (`internal/notify/resend.go` + `refinement_handlers.go` + UI)

The **only genuinely new plumbing** in Phase 4: tell the user a pin went stale.

- **Email:** when §2 produces a stale-pin clarification, send that user one email via the
  existing `internal/notify/resend.go` sender — subject + a link back to the report's
  refinement panel. **Respect `NotificationPreferences`** (the per-user prefs added in the
  UI work): if the user disabled this class, skip the email but still raise the in-UI
  clarification. One email per refresh per user (batch multiple stale pins into a single
  message — do not spam one per directive).
- **UI:** the refinement panel (`ReportOverride.tsx` + `frontend/src/api/refinement.ts`)
  surfaces open clarifications already (Phase 2); a stale-pin clarification renders with its
  "not available in <region> — what do you want?" question and the option buttons. Add a
  badge/count so the user sees unresolved clarifications on the report without opening the
  panel.
- No new store: the clarification lives in `refinement_thread.json`; the email is a
  side-effect of raising it.

Tests: a stale pin with notifications enabled sends exactly one email (mock the sender) and
raises the UI clarification; with the pref disabled, no email but the clarification still
shows; multiple stale pins → one batched email.

---

## 4. Confirm-by-blast-radius thresholds  (extend the Phase-2 validate/confirm step)

Make the `scope_estimate` already computed in Phase 2 actually *gate* auto-apply.

- Config (`data/` or `config_loader`): thresholds, e.g.
  `{auto_apply_max_rows: 10, auto_apply_max_spend: 100.0}` (tunable, not hard-coded at call
  sites).
- In the translator/confirm path: a proposed directive whose `scope_estimate` is **under**
  both thresholds and **high-confidence** may apply without an explicit confirm; **over**
  either threshold, or low-confidence, it requires an explicit user Confirm (the UI already
  has the Confirm button — this just decides when it's mandatory).
- `set_pricing_basis` always requires confirm regardless of size (it moves the headline —
  consistent with the Phase-3 "most guarded verb" rule).

Tests: a 3-row/$40 directive auto-applies; a 400-row/$50k directive is held for confirm; any
pricing-basis directive is held for confirm.

---

## 5. End-to-end verification — close the last gap (the reason this phase also *proves* the whole thing)

The one thing never exercised: a real bill through the **orchestrator** with `DIRECTIVE_LAYER`
on, a preference verb applied, and the refined report rendered.

- Add an e2e test (py, marked slow) that: takes a pinned corpus job → runs the pipeline with
  the flag on → injects a `set_target_family` + a `set_region` directive → reruns via the
  refinement trigger → asserts the touched rows re-rated, the report (HTML + CSV) shows the
  per-row "changed by your request" provenance, and the guards (ratio clamp, allowed-targets)
  still fired.
- Add one `set_pricing_basis=cud_3yr` case asserting the headline total for untouched rows is
  unchanged and the CUD rows dropped by the configured multiplier and carry the review flag.
- Wire this into CI alongside `test_directive_parity.py` so the full round trip is guarded,
  not just unit slices.

**Done = this test is green.** It is the proof that "fully built" is true, not asserted.

---

## 6. Done criteria
- Ledger is per-user; two users refine the same job independently; sharing = export the
  generated report. Baseline untouched.
- Catalog refresh revalidates directives; a stale pin becomes a blocking clarification (pin
  held, row flagged, cost unchanged until answered) — never silently remapped.
- Stale pin emails the user (respecting NotificationPreferences, batched) and shows a UI
  clarification with a badge.
- Blast-radius thresholds gate auto-apply; pricing-basis always confirms.
- E2e test green: real bill, flag on, preference verbs, refined report with provenance,
  guards firing.
- Full `pytest tests/` + `go test ./internal/...` green.

## 7. Explicitly out of scope → later / user policy
Discount-vs-discount pricing basis, the NAT VM-count number (both user decisions, verbs exist),
the long-tail taxonomy of rare verbs (add per request), and reversibility/history **UX**
polish (timeline/diff view). Phase 4 closes the *decided* questions and proves the system
end-to-end; it does not invent new policy.
