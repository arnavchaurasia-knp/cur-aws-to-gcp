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
    # dynamodb_rcu_to_reads_sec and dynamodb_wcu_to_writes_sec are consumed via LLM guidance
    # for dynamodb_throughput (WORKORDER_spec_partition_completeness §3 item 2).
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
        assert re.search(rf"def {re.escape(mapper)}\(", src), (
            f"Impact registry entry '{name}' names mapper '{mapper}' which is not defined in apply_static_mappings.py."
        )

