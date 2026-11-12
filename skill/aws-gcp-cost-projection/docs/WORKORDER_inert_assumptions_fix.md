# WORKORDER — Inert assumption knobs (assumption value reaches dead/mismatched mapper path)

**Status:** OPEN
**Author of analysis:** pricing audit, 2026-11-11
**Audience:** implementation model (follow steps literally; do not redesign)
**Scope:** 1 config file (`data/service_specs.json`), 1 mapper file (`scripts/apply_static_mappings.py`),
1 registry file (`data/assumption_impact.json`), 1 new test file. No schema changes. No catalog refetch.

---

## 0. Problem statement (read once, then follow the fixes)

The assumption engine itself works: change a value in `data/assumption-defaults.json`
(or a per-job override), re-run, get a new report. That part is **not** broken.

The break is **upstream of the assumption read-site**: an assumption value is consumed
*inside a specific `map_*` mapper branch*. That branch only runs if the line-item was
**classified into the exact mechanic whose mapper reads the assumption**. When classification
routes the row to a *different* mechanic (or to `misc` → LLM), the mapper that reads the knob
never sees the row, so the knob is a **silent no-op** — you can set it to anything and the
projected number does not move.

Confirmed example (the bill that triggered this): a NAT Gateway row under the **EC2 product**
projected GCP at **2% of AWS** ($2.44 vs $97.61). The `nat_vms_per_gateway` 32-VM cap logic in
`map_flat_hourly` is correct and *is* consumed by `projection_view.py`
(`c.total_usage * COALESCE(m.unit_multiplier,1) * od.rate_usd`), but the row was classified to
`misc` (not `flat_hourly`) because the EC2 NAT spec only matches on `usage_type_re` and the bill
had an empty/odd `usage_type`. So `unit_multiplier` stayed `1`, not ~31.4.

### Three failure modes found (and which assumptions hit them)

| Mode | Meaning | Affected assumptions |
|---|---|---|
| **A — orphaned handler** | mapper is in the dispatch `handlers` dict, but **no spec ever emits its mechanic**, so it receives zero rows | `dynamodb_rcu_to_reads_sec`, `dynamodb_wcu_to_writes_sec` (`map_dynamodb_capacity`, mechanic `dynamodb_capacity` is never emitted) |
| **B — read-site ≠ row mechanic** | assumption is read in mapper X, but the service's spec routes the row to mechanic Y | `memorydb_node_gib` + `dax_node_gib` (read in `map_elasticache`; MemoryDB routes to `managed_db`, DAX has no spec) |
| **C — fragile spec** | classification works on full CUR but misses PDF/flat-CSV bills (empty `usage_type`) | `nat_vms_per_gateway` (EC2-product variant only; the VPC-product variant is already robust) |

### Secondary problems (not inert, but wrong/rotten)

- `scripts/apply_static_mappings.py` contains **dead functions** `map_serverless` and
  `map_accelerators` (0 references anywhere). `data/assumption_impact.json` still points some
  assumptions at them, so the registry you would use to audit this is itself stale.
- `data/assumption_impact.json` `mapper` fields are wrong for several entries (point at the dead
  functions, or at `map_flat_hourly` for things actually read in `map_managed_db` /
  `map_quicksight` / `map_elasticache` / `map_tpu_row`).

### Assumptions that are FINE (do not touch)

`aurora_acu_vcpu`, `aurora_acu_ram_gib` (read in `map_managed_db`, rows route to `managed_db` ✓);
`glue_dpu_vcpu`, `glue_dpu_ram_gib` (read in `map_glue`, mechanic `glue` ✓);
`looker_studio_pro_seat_usd` (read in `map_quicksight`, mechanic `quicksight` ✓);
`rds_iops_included_in_storage` (read in `map_block_storage`, mechanic `block_storage` ✓);
`inferentia_chip_to_tpu`, `trainium_chip_to_tpu` (read in the **live** `map_tpu_row` — functional;
only the registry label is wrong, fixed in FIX-6);
`aws_ri_1yr_discount_pct`, `aws_ri_3yr_discount_pct`, `aws_sp_discount_pct`, `aws_spot_discount_pct`
(ingest/OD-basis path, not mapper-gated); `fx_max_staleness_days` (currency path).

---

## FIX-1 — EC2-product NAT spec is fragile (CRITICAL — this is the live $2.44 bug)

**File:** `data/service_specs.json`
**Anchor:** the `ec2` service, charge_type with `"id": "nat_and_flat_hourly"`.

The EC2 NAT spec matches ONLY on `usage_type_re`, so a NAT row whose `usage_type` does not literally
contain `NatGateway-Hours` (PDF/flat bills, where the token lives in `product`/`operation`) misses
`flat_hourly` and falls to `misc` → LLM → priced at the bare per-VM rate with `unit_multiplier=1`.

The VPC-product sibling `vpc_nat_hours` already solved this with `fallback_match` + `parsed_match`.
Mirror it.

**Current block:**
```json
      {
        "id": "nat_and_flat_hourly",
        "description": "NAT gateway-hours, Elastic IP, public IPv4, VPN connection-hours, and ALB/NLB load-balancer-hours (billed under the EC2 product) — fixed hourly fees.",
        "match": {
          "usage_type_re": "NatGateway-Hours|ElasticIP|IPAddress|PublicIPv4|VPN-Connections|VPNConnection|LoadBalancerUsage"
        },
        "mechanic": "flat_hourly",
        "fields": [
          { "name": "hours", "cur_column": "lineItem/UsageAmount", "source": "cur", "required": true },
          { "name": "region", "cur_column": "product/region", "source": "cur", "required": false }
        ],
        "source_rule": "flat_hourly (EC2 networking branch)"
      }
```

**Replace with** (adds `fallback_match` + `parsed_match`; everything else unchanged):
```json
      {
        "id": "nat_and_flat_hourly",
        "description": "NAT gateway-hours, Elastic IP, public IPv4, VPN connection-hours, and ALB/NLB load-balancer-hours (billed under the EC2 product) — fixed hourly fees.",
        "match": {
          "usage_type_re": "NatGateway-Hours|ElasticIP|IPAddress|PublicIPv4|VPN-Connections|VPNConnection|LoadBalancerUsage"
        },
        "fallback_match": {
          "operation_re": "NatGateway|NAT Gateway|LoadBalancer|VpnConnection"
        },
        "parsed_match": {
          "sub_type_in": ["gateway_hours"],
          "exclude_any_field_re": "transitgateway|transit gateway|data transfer|-bytes|databytes"
        },
        "mechanic": "flat_hourly",
        "fields": [
          { "name": "hours", "cur_column": "lineItem/UsageAmount", "source": "cur", "required": true },
          { "name": "region", "cur_column": "product/region", "source": "cur", "required": false }
        ],
        "source_rule": "flat_hourly (EC2 networking branch)",
        "notes": "FIX-1 (inert-assumptions workorder): added fallback_match/parsed_match so NAT/ELB/VPN hourly rows on PDF/flat-CSV bills (empty usage_type) still classify flat_hourly instead of falling to misc->LLM, which bypassed the nat_vms_per_gateway assumption."
      }
```

**Acceptance:** after this, a NAT-under-EC2 row with empty `usage_type` but `operation`/`product`
containing `NatGateway` classifies as `flat_hourly`, enters `map_flat_hourly`'s NAT branch, and
gets `unit_multiplier ≈ 31.4` (→ ~AWS parity) instead of `1` (→ 2%).

> ⚠️ `exclude_any_field_re` must still exclude NAT **data-processing** rows (`NatGateway-Bytes`),
> which are a different charge handled by `data_transfer`. The `-bytes|databytes` terms above do
> that. Do not drop them.

---

## FIX-2 — NAT multiplier must clamp at the 32-VM cap (LOW risk, 3-line change)

**File:** `scripts/apply_static_mappings.py`
**Anchor:** inside `def map_flat_hourly(...)`, the public-NAT `else:` branch. Find the unique line
`desc = "Networking Cloud Nat Gateway Uptime"` and the three lines immediately below it.

GCP Cloud NAT bills per VM **up to a 32-VM ceiling** — beyond 32 VMs there is **no additional
hourly charge**. The current code scales **linearly** past 32 (`nat_vms/32 * base_mult`), so the
committed default of **36** over-projects by ~12%, and large values over-project without bound.

**Current:**
```python
                nat_vms = float(_ASSUMPTIONS.get("nat_vms_per_gateway", 32.0))
                base_mult = round(0.044 / 0.0014, 8) if (sku_id and getattr(sku_id, 'rate', None) == 0.0014) else 31.42857143
                mult = base_mult if nat_vms == 32.0 else round((nat_vms / 32.0) * base_mult, 8)
```

**Replace with:**
```python
                nat_vms = float(_ASSUMPTIONS.get("nat_vms_per_gateway", 32.0))
                # GCP Cloud NAT hourly caps at 32 VMs: beyond 32 there is no extra
                # per-hour charge (https://cloud.google.com/nat/pricing). Clamp so
                # the multiplier never scales past the cap. <32 scales down linearly.
                nat_vms_billable = min(max(nat_vms, 0.0), 32.0)
                base_mult = round(0.044 / 0.0014, 8) if (sku_id and getattr(sku_id, 'rate', None) == 0.0014) else 31.42857143
                mult = round((nat_vms_billable / 32.0) * base_mult, 8)
```

**Optional cleanup (same intent, do it too):** in `data/assumption-defaults.json`, change
`values.nat_vms_per_gateway` from `36.0` to `32.0` so the default lands exactly at the cap.

**Acceptance:** `nat_vms_per_gateway` of 32, 36, or 200 all yield `mult ≈ 31.4` (AWS parity, never
above); a value of 16 yields `mult ≈ 15.7` (half). No value over-projects above parity.

---

## FIX-3 — MemoryDB routes to `managed_db`, but its assumption lives in `map_elasticache` (Mode B)

**File:** `data/service_specs.json`
**Anchor:** the `memorydb` service, charge_type `"id": "instance_hour"`.

`map_elasticache` already fully handles MemoryDB (detects `is_memdb`, reads `memorydb_node_gib`,
maps to Memorystore for Redis — see `apply_static_mappings.py` around the `is_memdb` branch). The
only problem is the row is classified `managed_db`, so it reaches `map_managed_db` instead. Flip
the mechanic.

**Current:**
```json
        { "id": "instance_hour", "mechanic": "managed_db", "match": { "unit_in": ["hrs", "hours", "hour", "hr"] } }
```
(It appears as a multi-line object; change only the `"mechanic"` value.)

**Change:** `"mechanic": "managed_db"` → `"mechanic": "elasticache"` for `memorydb.instance_hour`
**only**. Add/append to that charge_type a `notes` key:
`"notes": "FIX-3 (inert-assumptions workorder): route to elasticache so map_elasticache's is_memdb branch + memorydb_node_gib assumption apply (map_managed_db does not read that assumption)."`

Do **not** change `memorydb.storage` (stays `block_storage`).

**Acceptance:** a MemoryDB node-hours row classifies `elasticache`, reaches `map_elasticache`,
and changing `memorydb_node_gib` moves the projected cost.

---

## FIX-4 — DynamoDB capacity handler is orphaned (Mode A)

**File:** `data/service_specs.json`
**Anchor:** the `dynamodb` service. It currently has exactly two charge_types:
`dynamodb_throughput` (mechanic `misc`, `match: null`) and `dynamodb_storage`.

`map_dynamodb_capacity` is a fully-built mapper (reads `dynamodb_rcu_to_reads_sec` /
`dynamodb_wcu_to_writes_sec`, handles both provisioned RCU/WCU and on-demand request units) and it
**is** registered in the dispatch `handlers` dict under key `"dynamodb_capacity"`. But no spec
emits that mechanic, so it never runs. Add a dedicated charge_type that routes capacity/throughput
rows to it, **before** the `misc` catch-all.

**Insert this charge_type as the FIRST entry in `dynamodb.charge_types`** (before
`dynamodb_throughput`):
```json
        {
          "id": "dynamodb_capacity",
          "description": "Provisioned RCU/WCU capacity-unit-hours and on-demand read/write request units. Routed to map_dynamodb_capacity (reads dynamodb_rcu_to_reads_sec / dynamodb_wcu_to_writes_sec).",
          "match": {
            "usage_type_re": "CapacityUnit-Hrs|ReadCapacityUnit|WriteCapacityUnit|ReadRequestUnits|WriteRequestUnits|RequestUnits"
          },
          "fallback_match": {
            "operation_re": "CapacityUnit|RequestUnits|RCU|WCU"
          },
          "mechanic": "dynamodb_capacity",
          "fields": [],
          "source_rule": "dynamodb_capacity -> map_dynamodb_capacity",
          "notes": "FIX-4 (inert-assumptions workorder): the dynamodb_capacity handler was dispatched but no spec emitted its mechanic, so the dynamodb_rcu/wcu assumptions were inert. Must precede dynamodb_throughput (misc catch-all)."
        },
```
Leave `dynamodb_throughput` (mechanic `misc`) as the final catch-all for everything that is not
capacity or storage (it still catches backups, streams, etc.).

**Acceptance:** a DynamoDB `...-WriteCapacityUnit-Hrs` row classifies `dynamodb_capacity`, reaches
`map_dynamodb_capacity`, and changing `dynamodb_wcu_to_writes_sec` moves the projected cost (to
`passthrough` at `0`, or to Firestore writes scaled by the factor).

---

## FIX-5 — DAX has no spec, so `dax_node_gib` never applies (Mode B) — LOWER PRIORITY, verify

**File:** `data/service_specs.json`
**Anchor:** the `dynamodb` service (DAX bills under product code `AmazonDynamoDB`).

`map_elasticache` already handles DAX (`is_dax` branch, reads `dax_node_gib`), but no classifier
routes DAX node-hours to `elasticache`. Add a charge_type to the `dynamodb` service that catches
DAX node-usage and routes it to `elasticache`. **This must be placed ABOVE the FIX-4
`dynamodb_capacity` entry and above `dynamodb_throughput`** so DAX rows are caught first.

**Insert as the new FIRST entry in `dynamodb.charge_types`:**
```json
        {
          "id": "dax_node_hours",
          "description": "DynamoDB Accelerator (DAX) node-hours. Routed to map_elasticache (is_dax branch) which reads the dax_node_gib assumption and maps to Memorystore for Redis.",
          "match": {
            "usage_type_re": "NodeUsage|DAX"
          },
          "fallback_match": {
            "operation_re": "DAX|Accelerator"
          },
          "mechanic": "elasticache",
          "fields": [],
          "source_rule": "dax_node_hours -> map_elasticache (is_dax)",
          "notes": "FIX-5 (inert-assumptions workorder): route DAX node-hours to elasticache so dax_node_gib applies."
        },
```

**VERIFY before trusting the number (DAX node types look like `dax.r4.large`):** `map_elasticache`
derives node RAM from `instance_type` via `_elasticache_ram`. Confirm `_elasticache_ram` resolves
RAM for a `dax.*` instance type; if it returns `None`, the row falls to the `ram is None`
passthrough (acceptable — still Memorystore-labelled, no crash, but the assumption won't scale it).
If RAM is `None` for `dax.*`, open a follow-up to teach `_elasticache_ram`/`family_arch` to strip
the `dax.` prefix. Do **not** block FIX-1..4 on this.

---

## FIX-6 — Correct the stale `mapper` fields in the impact registry

**File:** `data/assumption_impact.json`
**Change:** update each listed `mapper` value to the function that actually reads the assumption.
Pure string replacements; do not touch `services`/`charge_types`/`decision_it_changes`.

| assumption key | current `mapper` (wrong) | correct `mapper` |
|---|---|---|
| `dax_node_gib` | `apply_static_mappings.map_flat_hourly` | `apply_static_mappings.map_elasticache` |
| `memorydb_node_gib` | `apply_static_mappings.map_flat_hourly` | `apply_static_mappings.map_elasticache` |
| `glue_dpu_vcpu` | `apply_static_mappings.map_flat_hourly` | `apply_static_mappings.map_glue` |
| `glue_dpu_ram_gib` | `apply_static_mappings.map_flat_hourly` | `apply_static_mappings.map_glue` |
| `looker_studio_pro_seat_usd` | `apply_static_mappings.map_flat_hourly` | `apply_static_mappings.map_quicksight` |
| `lambda_mb_per_vcpu` | `apply_static_mappings.map_serverless` | `apply_static_mappings.map_lambda` |
| `inferentia_chip_to_tpu` | `apply_static_mappings.map_accelerators` | `apply_static_mappings.map_tpu_row` |
| `trainium_chip_to_tpu` | `apply_static_mappings.map_accelerators` | `apply_static_mappings.map_tpu_row` |
| `dynamodb_rcu_to_reads_sec` | `apply_static_mappings.map_per_request` | `apply_static_mappings.map_dynamodb_capacity` |
| `dynamodb_wcu_to_writes_sec` | `apply_static_mappings.map_per_request` | `apply_static_mappings.map_dynamodb_capacity` |

Leave `nat_vms_per_gateway` (`map_flat_hourly`), `aurora_acu_*` (`map_managed_db`),
`rds_iops_included_in_storage` (`map_block_storage`), and the ingest/currency entries as-is.

---

## FIX-7 — Guard test so this class of bug fails loudly forever (the systemic fix)

**New file:** `tests/test_assumption_reachability.py`

An assumption is only live if the mechanic its mapper reads is (a) a real dispatch handler key AND
(b) actually emitted by some service spec. This test asserts exactly that, so the next inert knob
is a red test instead of a silent $0.

```python
"""Guard: every tunable assumption must be reachable.

An assumption value only affects a projection if the line-item is classified into
the mechanic whose mapper reads that assumption. This test pins, per assumption,
the mechanic its reader function is gated behind, and asserts that mechanic is both
a dispatch handler key AND emitted by at least one service spec. If a routing change
ever orphans an assumption again, this fails instead of silently projecting as if the
knob were untouched. See docs/WORKORDER_inert_assumptions_fix.md.
"""
import json
import os
import re

HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)

# assumption key -> the mechanic the row MUST carry for the reader mapper to run.
# Keep in sync with data/assumption_impact.json (FIX-6) and the handlers dict.
ASSUMPTION_READER_MECHANIC = {
    "nat_vms_per_gateway":          "flat_hourly",
    "dax_node_gib":                 "elasticache",
    "memorydb_node_gib":            "elasticache",
    "glue_dpu_vcpu":                "glue",
    "glue_dpu_ram_gib":             "glue",
    "looker_studio_pro_seat_usd":   "quicksight",
    "aurora_acu_vcpu":              "managed_db",
    "aurora_acu_ram_gib":           "managed_db",
    "lambda_mb_per_vcpu":           "per_request",   # Lambda specs to per_request, which delegates to map_lambda
    "dynamodb_rcu_to_reads_sec":    "dynamodb_capacity",
    "dynamodb_wcu_to_writes_sec":   "dynamodb_capacity",
    "rds_iops_included_in_storage": "block_storage",
    # inferentia/trainium ride the compute/apply_rates path via map_tpu_row, not a
    # single mechanic handler, so they are intentionally excluded here.
}

def _handler_keys():
    src = open(os.path.join(ROOT, "scripts", "apply_static_mappings.py"), encoding="utf-8").read()
    block = src[src.index("handlers = {"):]
    block = block[:block.index("}")]
    return set(re.findall(r'"([a-z_]+)"\s*:', block))

def _spec_emitted_mechanics():
    specs = json.load(open(os.path.join(ROOT, "data", "service_specs.json"), encoding="utf-8"))
    svcs = specs.get("services", specs)
    out = set()
    for sv in svcs.values():
        if isinstance(sv, dict):
            for ct in sv.get("charge_types") or []:
                if ct.get("mechanic"):
                    out.add(ct["mechanic"])
    return out

def test_every_assumption_reader_mechanic_is_dispatched():
    handlers = _handler_keys()
    for assumption, mech in ASSUMPTION_READER_MECHANIC.items():
        assert mech in handlers, (
            f"Assumption '{assumption}' reads inside the '{mech}' mapper, but '{mech}' "
            f"is not a key in the dispatch handlers dict — the mapper is never called."
        )

def test_every_assumption_reader_mechanic_is_emitted_by_a_spec():
    emitted = _spec_emitted_mechanics()
    for assumption, mech in ASSUMPTION_READER_MECHANIC.items():
        assert mech in emitted, (
            f"Assumption '{assumption}' reads inside the '{mech}' mapper, but no service spec "
            f"emits mechanic '{mech}', so no row ever reaches it — the knob is inert. "
            f"Add/fix a service_specs.json charge_type that routes to '{mech}'."
        )

def test_impact_registry_mappers_point_at_defined_functions():
    impact = json.load(open(os.path.join(ROOT, "data", "assumption_impact.json"), encoding="utf-8"))
    src = open(os.path.join(ROOT, "scripts", "apply_static_mappings.py"), encoding="utf-8").read()
    dead = {"map_serverless", "map_accelerators"}
    for name, info in impact.get("impacts", {}).items():
        mapper = (info.get("mapper") or "").split(".")[-1]
        if not mapper or not mapper.startswith("map_"):
            continue
        assert mapper not in dead, f"Impact registry entry '{name}' points at dead function {mapper}."
        assert re.search(rf"def {re.escape(mapper)}\\(", src), (
            f"Impact registry entry '{name}' names mapper '{mapper}' which is not defined in apply_static_mappings.py."
        )
```

> Run order note: `test_every_assumption_reader_mechanic_is_emitted_by_a_spec` will FAIL until
> FIX-3 and FIX-4 are done (that is the point — it is currently catching the two inert ones). Do
> FIX-1..6 first, then this test should pass green.

---

## FIX-8 — Delete dead mappers (OPTIONAL, do last)

**File:** `scripts/apply_static_mappings.py`
`map_serverless` and `map_accelerators` have **0 references** (verified by grep). After FIX-6 the
registry no longer names them. Deleting both `def` blocks is safe. If you are not 100% sure the
blocks are cleanly delimited, **skip this** — leaving dead code does no harm; the guard test in
FIX-7 already prevents the registry from pointing back at them. Do **not** delete `map_tpu_row`
(it is live — used by `apply_rates.py` and `family_mapper.py`) or `map_lambda`/`map_dynamodb_capacity`.

---

## Verification (run after FIX-1 .. FIX-7)

From `skill/aws-gcp-cost-projection/`:

```bash
python -c "import json; json.load(open('data/service_specs.json')); json.load(open('data/assumption_impact.json')); print('json ok')"
```

```bash
python -m pytest tests/ -v
```

```bash
python -m pytest tests/test_assumption_reachability.py -v
```

Then re-run one real bill end-to-end (the NAT bill from the report) and confirm, in the projection:
- the EC2-product NAT Gateway *uptime* row now carries `unit_multiplier ≈ 31.4` and projects near
  AWS parity (not 2%);
- its `projection_note` contains `nat_vms_per_gateway=` (proof it went through the assumption branch);
- a MemoryDB node-hours row's `gcp_service` is `Cloud Memorystore`/Memorystore and its note contains
  `memorydb_node_gib=`;
- a DynamoDB RCU/WCU row's note contains `dynamodb_rcu_to_reads_sec=` or `dynamodb_wcu_to_writes_sec=`.

## Priority / risk summary

| Fix | What | Risk | Priority |
|---|---|---|---|
| FIX-1 | EC2 NAT `fallback_match`/`parsed_match` | low (mirrors existing VPC spec) | **P0 — live bug** |
| FIX-2 | clamp NAT multiplier at 32-VM cap | low (3 lines) | P1 |
| FIX-3 | MemoryDB `managed_db`→`elasticache` | low (one value) | P1 |
| FIX-4 | add `dynamodb_capacity` charge_type | low-med (ordering before `misc`) | P1 |
| FIX-6 | correct impact-registry mappers | none (strings) | P1 |
| FIX-7 | reachability guard test | none (new file) | **P1 — prevents recurrence** |
| FIX-5 | add DAX spec → `elasticache` | med (verify `dax.*` RAM parse) | P2 |
| FIX-8 | delete dead mappers | low | P3 (optional) |

**Do NOT:** refetch the catalog, change `projection_view.py` (it already consumes
`unit_multiplier` correctly), or touch the assumptions that are listed as FINE in §0.
