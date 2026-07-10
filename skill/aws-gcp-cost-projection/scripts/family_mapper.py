#!/usr/bin/env python3
import json
import os
import sys
import re
import duckdb

# Add scripts directory to path to import resolve_sku
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from apply_static_mappings import resolve_sku

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else "projection-audit/projection.duckdb"
MANIFEST_PATH = "projection-audit/phase2_manifest.json"
MAPPINGS_DIR = "projection-audit/mappings"

_ITYPE_RE = re.compile(r'^(?:db\.)?([a-z]+)(\d+)([a-z]*)\.(.*)$')

# Load semantic family map config
MAP_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "family_map.json")
with open(MAP_CONFIG_PATH) as f:
    _cfg = json.load(f)

_AWS_WORKLOAD = _cfg["aws_workload"]   # prefix → workload type
_ARCH_SUFFIX  = _cfg["arch_suffix"]    # suffix char → architecture
_GCP_FAMILIES = _cfg["gcp_families"]   # workload → arch → {family, arm_sku, min_aws_gen, ...}


def parse_instance(itype):
    if not itype:
        return None
    m = _ITYPE_RE.match(itype.lower())
    if not m:
        return None
    return {
        "prefix": m.group(1),
        "gen": int(m.group(2)),
        "suffixes": m.group(3),
        "size": m.group(4)
    }


def get_gcp_family(parsed):
    """Return (gcp_family, arm_sku) or (None, False).

    Semantic lookup: AWS prefix → workload type, suffix → architecture,
    then find the matching GCP family from config. gen-based fallback is
    also data-driven via min_aws_gen / prev_gen_family in family_map.json.
    """
    prefix   = parsed["prefix"]
    suffixes = parsed["suffixes"]
    gen      = parsed["gen"]

    workload = _AWS_WORKLOAD.get(prefix, "general")

    # Detect architecture from suffix characters
    arch = "intel"
    for char, detected in _ARCH_SUFFIX.items():
        if char in suffixes:
            arch = detected
            break

    target = _GCP_FAMILIES.get(workload, {}).get(arch)
    if not target:
        return None, False

    family = target["family"]
    if gen < target.get("min_aws_gen", 1):
        family = target.get("prev_gen_family", family)

    return family, target.get("arm_sku", False)

def map_gce_row(r, parsed):
    gcp_family, arm_sku = get_gcp_family(parsed)
    if not gcp_family:
        return None

    gcp_region = r.get("gcp_region")
    aws_key = r["aws_li_key"]
    vcpus = r.get("instance_vcpus") or 2
    ram = r.get("instance_ram_gb") or 8.0

    # burstable workload: E2/T2A have no burst-credit model — projection assumes
    # steady-state CPU usage, which may overstate cost for mostly-idle workloads.
    is_burstable = _AWS_WORKLOAD.get(parsed["prefix"]) == "burstable"
    burst_note = (" [Note: E2 has no burst-credit model; projection assumes steady-state CPU usage]"
                  if is_burstable else "")

    # arm_sku comes from family_map.json — ARM GCP families use "Arm" in catalog SKU names.
    arm_infix = "Arm " if arm_sku else ""

    # 1. Core Component
    core_desc = f"{gcp_family} {arm_infix}Instance Core"
    core_sku = resolve_sku("Compute Engine", core_desc, gcp_region)
    core_entry = {
        "aws_li_key": aws_key,
        "gcp_service": "Compute Engine",
        "gcp_sku_name": core_desc,
        "component": "core",
        "strategy": "map",
        "unit_multiplier": float(vcpus),
        "gcp_region": gcp_region,
        "projection_note": f"Deterministic mapping: GCE {gcp_family} Core{burst_note}",
        "mapping_confidence": 0.85 if is_burstable else 1.0,
        "is_workload": True,
        "break_down": True
    }
    if core_sku:
        core_entry["gcp_sku_id"] = core_sku
        core_entry["gcp_sku_unit"] = core_sku.unit

    # 2. RAM Component
    ram_desc = f"{gcp_family} {arm_infix}Instance Ram"
    ram_sku = resolve_sku("Compute Engine", ram_desc, gcp_region)
    ram_entry = {
        "aws_li_key": aws_key,
        "gcp_service": "Compute Engine",
        "gcp_sku_name": ram_desc,
        "component": "ram",
        "strategy": "map",
        "unit_multiplier": float(ram),
        "gcp_region": gcp_region,
        "projection_note": f"Deterministic mapping: GCE {gcp_family} RAM{burst_note}",
        "mapping_confidence": 0.85 if is_burstable else 1.0,
        "is_workload": True,
        "break_down": True
    }
    if ram_sku:
        ram_entry["gcp_sku_id"] = ram_sku
        ram_entry["gcp_sku_unit"] = ram_sku.unit
        
    mappings = [core_entry, ram_entry]
    
    # 3. Local SSD Suffix Handling or Storage Optimized
    if "d" in parsed["suffixes"] or parsed["prefix"] in ("i", "im", "is", "d", "h"):
        ssd_desc = "Local SSD Capacity"
        ssd_sku = resolve_sku("Compute Engine", ssd_desc, gcp_region)
        ssd_entry = {
            "aws_li_key": aws_key,
            "gcp_service": "Compute Engine",
            "gcp_sku_name": ssd_desc,
            "component": "storage",
            "strategy": "map",
            "unit_multiplier": 375.0, # default 1 SSD increment (375 GB)
            "gcp_region": gcp_region,
            "projection_note": "Local SSD suffix / storage-optimized VM attachment",
            "mapping_confidence": 0.95,
            "is_workload": True,
            "break_down": True
        }
        if ssd_sku:
            ssd_entry["gcp_sku_id"] = ssd_sku
            ssd_entry["gcp_sku_unit"] = ssd_sku.unit
        mappings.append(ssd_entry)
        
    return mappings

def _resolve_gpu_count(entry, vcpus):
    """Compute GPU count from a gpu_profiles config entry and the instance vCPU count."""
    if "fixed_count" in entry:
        return float(entry["fixed_count"])
    if "gpu_per_vcpu" in entry:
        return max(float(entry.get("min_count", 1)), vcpus * entry["gpu_per_vcpu"])
    if "vcpu_breakpoints" in entry:
        for bp in sorted(entry["vcpu_breakpoints"], key=lambda x: -x["min_vcpu"]):
            if vcpus >= bp["min_vcpu"]:
                return float(bp["count"])
        return float(entry.get("default_count", 1))
    return 1.0


def _gpu_profile(prefix, gen, vcpus):
    """Return (gcp_family, gpu_desc, gpu_count) from gpu_profiles config, or None."""
    for entry in _cfg.get("gpu_profiles", {}).get(prefix, []):
        min_gen = entry.get("min_gen", 1)
        max_gen = entry.get("max_gen", 9999)
        if min_gen <= gen <= max_gen:
            return entry["family"], entry["gpu_desc"], _resolve_gpu_count(entry, vcpus)
    return None


def map_gpu_row(r, parsed):
    prefix = parsed["prefix"]
    gen = parsed["gen"]
    gcp_region = r.get("gcp_region")
    aws_key = r["aws_li_key"]
    vcpus = r.get("instance_vcpus") or 8
    ram = r.get("instance_ram_gb") or 32.0

    profile = _gpu_profile(prefix, gen, vcpus)
    if not profile:
        return None
    gcp_family, gpu_desc, gpu_count = profile
        
    # Core
    core_desc = f"{gcp_family} Instance Core"
    core_sku = resolve_sku("Compute Engine", core_desc, gcp_region)
    core_entry = {
        "aws_li_key": aws_key,
        "gcp_service": "Compute Engine",
        "gcp_sku_name": core_desc,
        "component": "core",
        "strategy": "map",
        "unit_multiplier": float(vcpus),
        "gcp_region": gcp_region,
        "projection_note": f"GPU workload mapping: GCE {gcp_family} Core",
        "mapping_confidence": 0.95,
        "is_workload": True,
        "break_down": True
    }
    if core_sku:
        core_entry["gcp_sku_id"] = core_sku
        core_entry["gcp_sku_unit"] = core_sku.unit

    # RAM
    ram_desc = f"{gcp_family} Instance Ram"
    ram_sku = resolve_sku("Compute Engine", ram_desc, gcp_region)
    ram_entry = {
        "aws_li_key": aws_key,
        "gcp_service": "Compute Engine",
        "gcp_sku_name": ram_desc,
        "component": "ram",
        "strategy": "map",
        "unit_multiplier": float(ram),
        "gcp_region": gcp_region,
        "projection_note": f"GPU workload mapping: GCE {gcp_family} RAM",
        "mapping_confidence": 0.95,
        "is_workload": True,
        "break_down": True
    }
    if ram_sku:
        ram_entry["gcp_sku_id"] = ram_sku
        ram_entry["gcp_sku_unit"] = ram_sku.unit

    # GPU
    gpu_sku = resolve_sku("Compute Engine", gpu_desc, gcp_region)
    gpu_entry = {
        "aws_li_key": aws_key,
        "gcp_service": "Compute Engine",
        "gcp_sku_name": gpu_desc,
        "component": "accelerator",
        "strategy": "map",
        "unit_multiplier": gpu_count,
        "gcp_region": gcp_region,
        "projection_note": f"Nvidia Accelerator attachment ({gpu_desc})",
        "mapping_confidence": 0.95,
        "is_workload": True,
        "break_down": True
    }
    if gpu_sku:
        gpu_entry["gcp_sku_id"] = gpu_sku
        gpu_entry["gcp_sku_unit"] = gpu_sku.unit
        
    return [core_entry, ram_entry, gpu_entry]

def map_db_row(r, parsed):
    gcp_family, _ = get_gcp_family(parsed)  # arm_sku unused — Cloud SQL has no ARM variants
    if not gcp_family:
        return None
        
    gcp_region = r.get("gcp_region")
    aws_key = r["aws_li_key"]
    vcpus = r.get("instance_vcpus") or 2
    ram = r.get("instance_ram_gb") or 8.0
    is_ha = r.get("deployment_option") == "Multi-AZ" or "multi-az" in (r.get("operation") or "").lower()
    
    suffix = " (Regional)" if is_ha else ""
    
    # SKU names must match Cloud SQL catalog descriptions via word overlap.
    # "SQLGen2InstancesCPU" resolves to null — use catalog-matching patterns instead.
    tier = "Regional" if is_ha else "Zonal"
    core_desc = f"Cloud SQL {tier} vCPU"
    ram_desc = f"Cloud SQL {tier} RAM"

    # 1. Core Component
    core_sku = resolve_sku("Cloud SQL", core_desc, gcp_region)
    core_entry = {
        "aws_li_key": aws_key,
        "gcp_service": "Cloud SQL",
        "gcp_sku_name": core_desc,
        "component": "core",
        "strategy": "map",
        "unit_multiplier": float(vcpus),
        "gcp_region": gcp_region,
        "projection_note": f"Deterministic DB mapping: Cloud SQL {tier} vCPU ({gcp_family} family equivalent)",
        "mapping_confidence": 0.90,
        "is_workload": True,
        "break_down": True
    }
    if core_sku:
        core_entry["gcp_sku_id"] = core_sku
        core_entry["gcp_sku_unit"] = core_sku.unit

    # 2. RAM Component
    ram_sku = resolve_sku("Cloud SQL", ram_desc, gcp_region)
    ram_entry = {
        "aws_li_key": aws_key,
        "gcp_service": "Cloud SQL",
        "gcp_sku_name": ram_desc,
        "component": "ram",
        "strategy": "map",
        "unit_multiplier": float(ram),
        "gcp_region": gcp_region,
        "projection_note": f"Deterministic DB mapping: Cloud SQL {tier} RAM ({gcp_family} family equivalent)",
        "mapping_confidence": 0.90,
        "is_workload": True,
        "break_down": True
    }
    if ram_sku:
        ram_entry["gcp_sku_id"] = ram_sku
        ram_entry["gcp_sku_unit"] = ram_sku.unit
        
    return [core_entry, ram_entry]

def main():
    if not os.path.exists(MANIFEST_PATH):
        print("phase2_manifest.json not found; skipping family_mapper")
        sys.exit(0)
        
    with open(MANIFEST_PATH) as f:
        manifest = json.load(f)
        
    compute_mapped = []
    db_mapped = []
    
    compute_skipped_keys = set()
    db_skipped_keys = set()
    
    unknowns = []
    
    # Map GCE Compute Instances
    for r in manifest.get("compute_breakdown", {}).get("rows", []):
        itype = r.get("instance_type")
        parsed = parse_instance(itype)
        if not parsed:
            continue
            
        is_gpu = _AWS_WORKLOAD.get(parsed["prefix"]) == "accelerated"
        if is_gpu:
            res = map_gpu_row(r, parsed)
        else:
            res = map_gce_row(r, parsed)
            
        if res:
            compute_mapped.extend(res)
            compute_skipped_keys.add(r["aws_li_key"])
        else:
            unknowns.append(f"compute: {itype}")

    # Map RDS Database Instances
    for r in manifest.get("managed_db", {}).get("rows", []):
        itype = r.get("instance_type")
        parsed = parse_instance(itype)
        if not parsed:
            continue
            
        res = map_db_row(r, parsed)
        if res:
            db_mapped.extend(res)
            db_skipped_keys.add(r["aws_li_key"])
        else:
            unknowns.append(f"database: {itype}")
            
    # Write mapping files if any mapped
    os.makedirs(MAPPINGS_DIR, exist_ok=True)
    if compute_mapped:
        with open(os.path.join(MAPPINGS_DIR, "compute_breakdown_fm_mappings.json"), "w") as f:
            json.dump(compute_mapped, f, indent=2)
        print(f"  family_mapper: mapped {len(compute_skipped_keys)} compute row(s) deterministically")

    if db_mapped:
        with open(os.path.join(MAPPINGS_DIR, "managed_db_fm_mappings.json"), "w") as f:
            json.dump(db_mapped, f, indent=2)
        print(f"  family_mapper: mapped {len(db_skipped_keys)} database row(s) deterministically")

    # Prune phase2_manifest.json to avoid double mapping by LLM
    pruned = False
    if compute_skipped_keys:
        manifest["compute_breakdown"]["rows"] = [
            r for r in manifest["compute_breakdown"]["rows"]
            if r["aws_li_key"] not in compute_skipped_keys
        ]
        manifest["compute_breakdown"]["row_count"] = len(manifest["compute_breakdown"]["rows"])
        pruned = True
        
    if db_skipped_keys:
        manifest["managed_db"]["rows"] = [
            r for r in manifest["managed_db"]["rows"]
            if r["aws_li_key"] not in db_skipped_keys
        ]
        manifest["managed_db"]["row_count"] = len(manifest["managed_db"]["rows"])
        pruned = True
        
    if pruned:
        with open(MANIFEST_PATH, "w") as f:
            json.dump(manifest, f, indent=2)
        print("  family_mapper: pruned manifest.json")
        
    # Log unknown families for coverage reviews
    if unknowns:
        log_dir = os.path.dirname(DB_PATH)
        with open(os.path.join(log_dir, "unknown_families.log"), "w") as f:
            f.write("\n".join(unknowns) + "\n")
        print(f"  family_mapper: logged {len(unknowns)} unknown instance type(s)")

if __name__ == "__main__":
    main()
