# WORK ORDER — KMS key-months and Route 53 hosted zones are priced at AWS cost

**For an implementing AI with no prior context. Read fully before editing.**
Paths are relative to `skill/aws-gcp-cost-projection/`. Verified against the
catalog refreshed 2026-10-23 (108 services, 90,335 SKUs) at commit `25f44de`.

**Scope: two charge types.** `data/service_map.json`,
`scripts/apply_static_mappings.py`. No frontend, no Go, no orchestrator.

---

## 0. Read this section before touching anything

This work order exists because a previous review rejected a batch of "PASS →
SKU" upgrades as *"parity passthrough already yields identical dollars; zero
accuracy delta"* (see the implementation-status table in `AGENTS.md`). That
conclusion was checked against the live catalog. **It is right about three of
the five things it covers and wrong about two**, because the GCP rates it
compared against were the wrong tier.

**Do NOT implement these — they are already correct. Verified in code:**

| thing | why it is already fine |
|---|---|
| **Public IPv4** | `apply_static_mappings.py:397-399` already maps `In-use public IPv4\|ElasticIP\|EIP\|…` to the SKU `External IP Charge on a Standard VM`, which is **$0.00/h**. It was never passthrough. |
| **EKS cluster-hours** | `FLAT_HOURLY_MAP` already maps `AmazonEKS\|EKS.*Hours\|…` to `Zonal Kubernetes Clusters`, **$0.10/h** — exact parity with AWS. |
| **KMS *operations*** | AWS $0.03/10k = $0.000003/op; GCP `Cryptographic operations with a software symmetric key` = **$0.000003/count**. Genuine parity. Passthrough is correct. |
| **CloudWatch alarms + dashboards** | Both already resolve to $0 in `map_cloudwatch` (`apply_static_mappings.py:5740-5760`), using a product+usage_type+operation blob so PDF bills work too. |
| **Fargate / Autopilot rates** | Fixed in commit `25f44de` — `Kubernetes Engine` was missing from `prefetch_core_gcp_services`. |

Re-implementing any of the above is the most expensive mistake available here:
it costs time and it risks replacing correct pricing with something worse.

**Only the two items below are real.**

---

## A-1 — KMS key-months are projected at 16.7x (HIGH)

### The defect

`data/service_map.json` has one KMS rule:

```
match "key management"  →  mode "review"  →  gcp_service "Cloud KMS"
```

`mode: "review"` is defined in that file's own `_meta.mode_semantics` as:

> FORCES gcp_service=target + strategy=passthrough (carry AWS cost, no invented
> figure) + manual-review flag.

So every KMS row carries its **AWS** cost onto the GCP side. There is no KMS
entry anywhere in `apply_static_mappings.py` — grep confirms it.

For key-months that is badly wrong:

| | AWS | GCP |
|---|---|---|
| customer-managed key | **$1.00** / key / month | **$0.06** / key version / month |

GCP SKU: `Active software symmetric key versions`, service `Cloud KMS`
(`EE2F-D110-890C`), `$0.06/mo`. **16.7x over-projection** on every KMS
key-month row, on every bill — roughly 46 rows per bill by the earlier count.

### Why the earlier review called this parity

`AGENTS.md` records *"Cloud KMS key-versions $1/mo vs $1/mo — exact parity"*.
That `$1/mo` is GCP's **HSM** price (`Active HSM symmetric key versions`,
`$1.00/mo`). The comparison used the HSM tier against AWS's ordinary CMK. An
ordinary AWS KMS customer-managed key is a *software* key, and the correct
counterpart is the $0.06 SKU.

### Fix

Map KMS key-month rows to `Active software symmetric key versions` at a 1.0
multiplier and flip the rule's `mode` from `review` to `keep` (meaning "the
pipeline prices this deterministically; the classifier only stamps metadata").

**Be careful about which rows you capture.** Not every KMS row is a software
key:

- The catalog genuinely charges **$1.00/mo** for `Active HSM symmetric key
  versions` and **$2.50/mo** for HSM asymmetric/ECDSA variants. If an AWS row
  is an HSM-backed key, $1 is the *right* answer and your change must not
  rewrite it to $0.06.
- `Active external symmetric key versions` is **$3.00/mo** — external key
  stores are more expensive on GCP, not cheaper.
- **AWS CloudHSM is a different AWS service** and must not be swept into this
  rule. Check what, if anything, already routes CloudHSM before you widen any
  pattern.
- Leave **operations** rows on passthrough, or map them 1:1. They are at
  parity and changing them gains nothing while adding a failure mode.

So: match the key-month unit specifically (monthly key/key-version charges),
not "any row whose product mentions KMS". Follow the unit-guard style already
used in `map_cloudwatch`, which separates dashboard, alarm and API-call rows
out of one service rather than treating the service as uniform.

### Verification

1. A test asserting a KMS key-month row resolves to the $0.06 SKU and that the
   projected cost is ~1/16.7 of the AWS cost.
2. A test asserting an **HSM** key row does **not** get the $0.06 rate.
3. A test asserting KMS operation rows are unchanged by this work.
4. Re-run a bill that contains KMS rows and confirm the per-row note explains
   the software-vs-HSM choice, so a reviewer can see which tier was assumed.

---

## A-2 — Route 53 hosted zones are projected at 2.5x, and the comment says otherwise (MEDIUM)

### The defect

`PER_REQUEST_MAP` (`apply_static_mappings.py:502`) maps queries correctly:

```python
(r"Route.?53(?!.*Recovery Controller)", "Cloud DNS", "DNS Query", 1.0),
```

Queries really are at parity — AWS $0.40/M, GCP `DNS Query (port 53)`
$0.00000040/count = $0.40/M. **Leave that line alone.**

The comment immediately above it (lines 493-495) says:

> Hosted-zone fees are caught by flat_hourly (unit=Hrs) so only per-query rows
> reach this mapper.

**That is not true.** `FLAT_HOURLY_MAP` contains no Route 53 entry at all —
grep it. And AWS bills hosted zones **per month**, not per hour, so a zone row
would never match an hourly-unit mechanic even if an entry existed. The comment
describes a safety net that does not exist, which is why nobody noticed the
zones were unpriced.

Hosted zones therefore fall through to the `service_map` rule
`match "route 53" → mode "review" → Cloud DNS`, i.e. passthrough at AWS cost:

| | AWS | GCP |
|---|---|---|
| hosted zone | **$0.50** / zone / month | **$0.20** / `ManagedZone` / month |

2.5x over-projection. Small per row, but it is every zone on every bill.

### Fix

Add a hosted-zone mapping to `ManagedZone` at a 1.0 multiplier, guarded on the
monthly zone unit so it cannot swallow the query rows that line 502 already
handles correctly.

**Then correct the comment at lines 493-495.** Whatever you implement, the
comment must describe what the code actually does. A wrong comment about a
guard is worse than no comment, because the next reader stops looking.

Keep the existing `(?!.*Recovery Controller)` exclusion in any new pattern.
Route 53 Application Recovery Controller is a different, hourly-billed product
that only shares the brand name, and `service_map.json` already routes it to
Manual Review.

### Verification

1. A test that a hosted-zone row resolves to `ManagedZone` and projects at 0.4x
   the AWS cost.
2. A test that a Route 53 **query** row still resolves to `DNS Query` — this is
   the regression that a careless pattern would cause.
3. A test that a Recovery Controller row reaches neither mapping.

---

## A-3 — `data/pricing_matrix.json` (BLOCKED here, read before starting)

The original request was a matrix "covering all SKUs globally across the 75 job
DBs". **Those 75 databases are not in this repository or on this machine** —
they were on the author's other workstation. Any matrix produced here is built
from the catalog and from documented decisions, and **cannot** be validated
against real bills.

Build it only if you can reach the job corpus. If you can, the matrix must
encode the **corrected** rates from this work order, not the ones in the
`AGENTS.md` table — that table states KMS as "$1/mo vs $1/mo exact parity",
which is the error this work order exists to fix. Shipping the matrix with that
number would freeze the mistake into an artifact that other code then asserts
against.

If you cannot reach the corpus, say so and stop. Do not generate a matrix of
unverified numbers and present it as an audit.

---

## Verification for the whole work order

1. `python3 -m pytest tests/ -v` — all tests pass (1,117 at the time of
   writing).
2. Syntax-compile every touched Python file.
3. Re-run a bill containing KMS and Route 53 rows and confirm: key-months drop
   to ~6% of the AWS figure, hosted zones to 40%, and **queries and operations
   are unchanged**.
4. Confirm `git diff` touches nothing listed in section 0.

---

## Done

- [ ] KMS key-months resolve to `Active software symmetric key versions`
      ($0.06/mo); HSM and external key rows are *not* given that rate.
- [ ] KMS operations unchanged.
- [ ] KMS `service_map` rule moved from `review` to `keep`.
- [ ] Route 53 hosted zones resolve to `ManagedZone` ($0.20/mo).
- [ ] Route 53 queries still resolve to `DNS Query`; Recovery Controller still
      excluded.
- [ ] The false comment at `apply_static_mappings.py:493-495` now describes
      what the code does.
- [ ] Nothing in section 0 was modified.
