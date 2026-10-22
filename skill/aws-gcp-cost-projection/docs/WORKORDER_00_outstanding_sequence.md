# WORK ORDER — The four outstanding pieces, in dependency order

**Read this first. It does not re-specify anything** — each piece has its own
file with the full detail. This exists because the four interact, two of them
are already half-landed, and doing them out of order will make one of them look
like it failed.

Verified state at the time of writing (suite: **1064 passing**):

| # | piece | file | status |
|---|---|---|---|
| 1 | Commitment-fee rows grossed up | `WORKORDER_commitment_basis_tiers.md` **§0 only** | §1–7 **done**; §0 correction **not started** |
| 2 | `billing_month` never fetched | `WORKORDER_mapper_unfetched_keys.md` | **not started** |
| 3 | Arm family not available in region | `WORKORDER_arm_region_availability.md` | **not started** |
| 4 | Validator autofixes undo each other | `WORKORDER_validator_autofix_loop.md` | **not started** |

Checks used, so you can confirm the same state before you begin:

```bash
cd skill/aws-gcp-cost-projection
sed -n '8764,8784p' scripts/apply_static_mappings.py | grep -c billing_month   # 0 = piece 2 open
grep -c family_not_available_in_region data/passthrough_reasons.json           # 0 = piece 3 open
git diff --stat -- scripts/validate_fix.py                                     # empty = piece 4 open
```

---

## Do them in this order, and why

### 1 — Commitment-fee correction (`WORKORDER_commitment_basis_tiers.md` §0)

**First, because it is live and wrong right now.** Three Savings-Plan
commitment-fee rows are being divided by a discount factor, turning a billed
$4,836 into an estimated **$10,075** — $5,239 of AWS cost that does not exist,
inflating the AWS side and overstating the saving.

Only §0 is outstanding. **§1–§7 are implemented and verified — do not redo
them.** The status banner at the top of that file lists what must not be
disturbed, including the measured results to preserve: GCP totals moved exactly
$0.00, `committed_1yr` 1.45x → 0.90x, `spot` 10.80x → 3.24x.

Independent of the other three. Can be done in parallel with 2.

### 2 — `billing_month` (`WORKORDER_mapper_unfetched_keys.md`)

**Second, because it silently disables work that already shipped.** The
per-month fee dedup is correct in the mapper and has never run in production,
because `billing_month` is not among the 20 columns fetched into the row dicts.
Its tests pass by hand-building dicts.

That file also contains the item that matters most beyond this bug: **T4, the
test that derives every key mappers read and asserts it is a subset of `cols`.**
Do not skip it. Both this and piece 1's root cause were instances of the same
class — code reading a field that never arrives — and T4 is the only thing that
catches the next one.

Note `line_item_type` was already added to the SELECT and `cols` by the
commitment work order, correctly aligned (21 columns, 21 expressions, verified).
Append `billing_month` the same way — **to the end of both lists**, never
inserted mid-list.

Independent of 1, 3 and 4.

### 3 — Arm region reason code (`WORKORDER_arm_region_availability.md`)

**Third, and it must come before 4.** Piece 4 fixes the validator so it stops
counting *reasoned* passthrough as a violation. These Arm rows — 97 line items,
$9,900, 7.5% of workload — are the largest block of legitimately-unpriceable
rows, and they currently carry `passthrough_reason = 'unresolved_sku'`, which
reads as a defect.

Until they carry `family_not_available_in_region`, piece 4 has nothing correct
to exclude them by, and its results cannot be judged.

Changes **no dollar figure**. Reason codes, one name field, and logging only.

### 4 — Validator autofix loop (`WORKORDER_validator_autofix_loop.md`)

**Last, because it depends on 3.** Two autofixes cancel each other exactly on
every bill (`restored == downgraded`, 34/34, 30/30, 28/28…), and the validator
fails **10 of 11** bills.

Judge it only after 3 has landed. Before that, the Arm rows still look like
unexplained passthrough and the failure count will not fall to where it should.

---

## When all four are done

- Suite passes at **1064 + the sum of each file's new tests**. Record your own
  baseline before starting; it has moved several times.
- **GCP totals unchanged** by pieces 1, 3 and 4. Piece 1 *reduces* the AWS side
  by removing a fabricated $5,239; piece 2 raises monthly fees on multi-month
  bills, which is its purpose.
- `validate_fix.py` fails far fewer than 10 of 11 bills — and still fails any
  bill with genuinely unexplained passthrough. **A validator that passes
  everything is a worse outcome than the bug.**
- A test exists that would have caught `billing_month`.

## Do NOT

- Do **not** reorder 3 and 4.
- Do **not** redo `WORKORDER_commitment_basis_tiers.md` §1–§7.
- Do **not** skip T4 in piece 2 because the four known keys are fixed. The gap
  is the issue; the keys are symptoms.
- Do **not** treat "the validator now passes" as success on its own (see above).
