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

# Load family map config
MAP_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "family_map.json")
with open(MAP_CONFIG_PATH) as f:
    FAMILY_MAP = json.load(f).get("mappings", {})

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
    prefix = parsed["prefix"]
    suffixes = parsed["suffixes"]
    gen = parsed["gen"]
    
    # Storage-optimized maps to high-memory Intel/AMD profiles with SSDs
    if prefix in ("i", "im", "is", "d", "h"):
        prefix = "r"
        
    cfg = FAMILY_MAP.get(prefix)
    if not cfg:
        return None
        
    if "g" in suffixes:
        return cfg.get("g")
    if "a" in suffixes:
        return cfg.get("a")
        
    # Gen-based refinement
    default_family = cfg.get("default")
    if default_family == "C3" and gen <= 5:
        return "N2"
    if default_family == "N4" and gen <= 5:
        return "N2"
        
    return default_family

def map_gce_row(r, parsed):
    gcp_family = get_gcp_family(parsed)
    if not gcp_family:
        return None
        
    gcp_region = r.get("gcp_region")
    aws_key = r["aws_li_key"]
    vcpus = r.get("instance_vcpus") or 2
    ram = r.get("instance_ram_gb") or 8.0
    
    # 1. Core Component
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
        "projection_note": f"Deterministic mapping: GCE {gcp_family} Core",
        "mapping_confidence": 1.0,
        "is_workload": True,
        "break_down": True
    }
    if core_sku:
        core_entry["gcp_sku_id"] = core_sku

    # 2. RAM Component
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
        "projection_note": f"Deterministic mapping: GCE {gcp_family} RAM",
        "mapping_confidence": 1.0,
        "is_workload": True,
        "break_down": True
    }
    if ram_sku:
        ram_entry["gcp_sku_id"] = ram_sku
        
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
        mappings.append(ssd_entry)
        
    return mappings

def map_gpu_row(r, parsed):
    prefix = parsed["prefix"]
    gen = parsed["gen"]
    gcp_region = r.get("gcp_region")
    aws_key = r["aws_li_key"]
    vcpus = r.get("instance_vcpus") or 8
    ram = r.get("instance_ram_gb") or 32.0
    
    if prefix == "g":
        gcp_family = "G2"
        gpu_desc = "Nvidia L4 GPU running in"
        gpu_count = 1.0
    elif prefix == "p" and gen == 4:
        gcp_family = "A2"
        gpu_desc = "Nvidia Tesla A100 GPU running in"
        gpu_count = 8.0
    elif prefix == "p" and gen == 5:
        gcp_family = "A3"
        gpu_desc = "Nvidia H100 80GB GPU"
        gpu_count = 8.0
    else:
        return None
        
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
        
    return [core_entry, ram_entry, gpu_entry]

def map_db_row(r, parsed):
    gcp_family = get_gcp_family(parsed)
    if not gcp_family:
        return None
        
    gcp_region = r.get("gcp_region")
    aws_key = r["aws_li_key"]
    vcpus = r.get("instance_vcpus") or 2
    ram = r.get("instance_ram_gb") or 8.0
    is_ha = r.get("deployment_option") == "Multi-AZ" or "multi-az" in (r.get("operation") or "").lower()
    
    suffix = " (Regional)" if is_ha else ""
    
    # 1. Core Component
    core_desc = f"SQLGen2InstancesCPU{suffix}"
    core_sku = resolve_sku("Cloud SQL", core_desc, gcp_region)
    core_entry = {
        "aws_li_key": aws_key,
        "gcp_service": "Cloud SQL",
        "gcp_sku_name": core_desc,
        "component": "core",
        "strategy": "map",
        "unit_multiplier": float(vcpus),
        "gcp_region": gcp_region,
        "projection_note": f"Deterministic DB mapping: Cloud SQL CPU ({gcp_family} family equivalent{suffix})",
        "mapping_confidence": 1.0,
        "is_workload": True,
        "break_down": True
    }
    if core_sku:
        core_entry["gcp_sku_id"] = core_sku

    # 2. RAM Component
    ram_desc = f"SQLGen2InstancesRAM{suffix}"
    ram_sku = resolve_sku("Cloud SQL", ram_desc, gcp_region)
    ram_entry = {
        "aws_li_key": aws_key,
        "gcp_service": "Cloud SQL",
        "gcp_sku_name": ram_desc,
        "component": "ram",
        "strategy": "map",
        "unit_multiplier": float(ram),
        "gcp_region": gcp_region,
        "projection_note": f"Deterministic DB mapping: Cloud SQL RAM ({gcp_family} family equivalent{suffix})",
        "mapping_confidence": 1.0,
        "is_workload": True,
        "break_down": True
    }
    if ram_sku:
        ram_entry["gcp_sku_id"] = ram_sku
        
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
            
        is_gpu = parsed["prefix"] in ("g", "p")
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
        with open(os.path.join(MAPPINGS_DIR, "compute_breakdown_mappings.json"), "w") as f:
            json.dump(compute_mapped, f, indent=2)
        print(f"  family_mapper: mapped {len(compute_skipped_keys)} compute row(s) deterministically")
        
    if db_mapped:
        with open(os.path.join(MAPPINGS_DIR, "managed_db_mappings.json"), "w") as f:
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
