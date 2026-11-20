#!/usr/bin/env python3
"""
scripts/gen_unit_dimensions.py — Generates data/unit_dimensions.json.
Follows WORKORDER_unit_conversion_engine.md Phase P1 (P1.1, P1.2, P1.3).

This file is GENERATED — do not hand-edit data/unit_dimensions.json directly.
Regenerate by running:
    python scripts/gen_unit_dimensions.py

Every scale is verified and cited from official standards:
- IEC 80000-13 (binary prefixes: KiB, MiB, GiB, TiB)
- SI Brochure 9th edition (decimal prefixes: kB, MB, GB, TB)
- GCP Cloud Billing Catalog API (pricingExpression.usageUnit)
- AWS CUR pricing/unit and pricing documentation (730.0 hours/month convention)
"""

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SKILL_DIR = os.environ.get("SKILL_DIR") or os.path.dirname(_HERE)
_DATA_DIR = os.path.join(_SKILL_DIR, "data")
_OUTPUT_PATH = os.path.join(_DATA_DIR, "unit_dimensions.json")

# Standard constants
HOURS_PER_DAY = 24.0
HOURS_PER_MONTH = 730.0  # AWS & GCP standard: 365 * 24 / 12 = 730.0 hours
HOURS_PER_YEAR = 8760.0  # 365 * 24

GIB_BYTES = 1024.0 ** 3  # 1,073,741,824 bytes (IEC 80000-13)
GB_BYTES = 1000.0 ** 3   # 1,000,000,000 bytes (SI)
GB_TO_GIB_SCALE = GB_BYTES / GIB_BYTES  # 1 / 1.073741824 = 0.9313225746154785


def build_units_table() -> dict:
    units = {}

    def add(names, dims, scale, description=""):
        if isinstance(names, str):
            names = [names]
        for name in names:
            norm = name.strip()
            units[norm] = {
                "dims": dims,
                "scale": float(scale),
            }
            # Also register hyphen/dot variants if applicable
            if "." in norm:
                units[norm.replace(".", "-")] = {"dims": dims, "scale": float(scale)}
                units[norm.replace(".", " ")] = {"dims": dims, "scale": float(scale)}
            if "-" in norm:
                units[norm.replace("-", ".")] = {"dims": dims, "scale": float(scale)}
                units[norm.replace("-", " ")] = {"dims": dims, "scale": float(scale)}

    # ── 1. Time (base: hour 'h' = 1.0) ──────────────────────────────────────────
    time_dim = {"time": 1}
    add(["s", "sec", "second", "seconds"], time_dim, 1.0 / 3600.0, "SI second: 1/3600 hour")
    add(["ms", "millisecond", "milliseconds"], time_dim, 1.0 / 3600000.0, "SI millisecond: 1/3.6e6 hour")
    add(["min", "minute", "minutes"], time_dim, 1.0 / 60.0, "Minute: 1/60 hour")
    add(["h", "hr", "hrs", "hour", "hours"], time_dim, 1.0, "Base time unit: 1 hour")
    add(["d", "day", "days"], time_dim, HOURS_PER_DAY, "Day: 24 hours")
    add(["mo", "month", "months"], time_dim, HOURS_PER_MONTH, "Month: 730 hours (AWS/GCP billing standard)")
    add(["yr", "year", "years"], time_dim, HOURS_PER_YEAR, "Year: 8760 hours (365 days)")

    # ── 2. Data Capacity (base: binary gibibyte 'GiB' = 1.0) ────────────────────
    data_dim = {"data": 1}
    add(["by", "byte", "bytes"], data_dim, 1.0 / GIB_BYTES, "Byte: 1/(1024^3) GiB")
    add(["kiby", "kib"], data_dim, (1024.0) / GIB_BYTES, "Kibibyte: 1/(1024^2) GiB")
    add(["kby", "kb"], data_dim, (1000.0) / GIB_BYTES, "Kilobyte: 1000/(1024^3) GiB")
    add(["miby", "mib"], data_dim, (1024.0 ** 2) / GIB_BYTES, "Mebibyte: 1/1024 GiB")
    add(["mby", "mb"], data_dim, (1000.0 ** 2) / GIB_BYTES, "Megabyte: 10^6/(1024^3) GiB")
    add(["giby", "gib", "gibibyte", "gibibytes"], data_dim, 1.0, "Base data unit: 1 GiB = 2^30 bytes (IEC 80000-13)")
    add(["gby", "gb", "gigabyte", "gigabytes"], data_dim, GB_TO_GIB_SCALE, "Decimal gigabyte: 10^9/(1024^3) = 1/1.073741824 GiB")
    add(["tiby", "tib", "tebibyte"], data_dim, 1024.0, "Tebibyte: 1024 GiB")
    add(["tby", "tb", "terabyte"], data_dim, (1000.0 ** 4) / GIB_BYTES, "Decimal terabyte: 10^12/(1024^3) GiB")
    add(["piby", "pib", "pebibyte"], data_dim, 1024.0 ** 2, "Pebibyte: 1024^2 GiB")
    add(["pby", "pb", "petabyte"], data_dim, (1000.0 ** 5) / GIB_BYTES, "Decimal petabyte: 10^15/(1024^3) GiB")

    # ── 3. Data * Time (base: 'GiB.h' = 1.0 GiB-hour) ───────────────────────────
    storage_dim = {"data": 1, "time": 1}
    add(["storage-month", "storagemonth"], storage_dim, HOURS_PER_MONTH, "Storage-month base unit: 1 GiB-month")
    add(["by.h", "byte-hours", "byte-hrs"], storage_dim, 1.0 / GIB_BYTES, "Byte-hours")
    add(["miby.h", "mib-hours", "mib-hrs"], storage_dim, 1.0 / 1024.0, "MiB-hours")
    add(["miby.s", "mib-s"], storage_dim, (1.0 / 1024.0) / 3600.0, "MiB-seconds")
    add(["miby.mo", "mib-mo", "mib-month"], storage_dim, (1.0 / 1024.0) * HOURS_PER_MONTH, "MiB-month")
    add(["mb-mo", "mb-month"], storage_dim, ((1000.0 ** 2) / GIB_BYTES) * HOURS_PER_MONTH, "MB-month")
    add(["giby.h", "gib.h", "gib-hours", "gib-hrs"], storage_dim, 1.0, "Base storage unit: 1 GiB-hour")
    add(["gby.h", "gb.h", "gb-hours", "gb-hrs"], storage_dim, GB_TO_GIB_SCALE, "1 GB-hour = 1/1.073741824 GiB-hour")
    add(["giby.s", "gib-s", "gib-sec"], storage_dim, 1.0 / 3600.0, "1 GiB-second: 1/3600 GiB-hour")
    add(["giby.d", "gib-d", "gib-day"], storage_dim, HOURS_PER_DAY, "1 GiB-day: 24 GiB-hours")
    add(["giby.mo", "gib-mo", "gib-month", "gibibyte-mo", "gibibyte-month", "gibibyte month"], storage_dim, HOURS_PER_MONTH, "1 GiB-month: 730 GiB-hours")
    add(["gby.mo", "gby-mo", "gb-mo", "gb-month"], storage_dim, GB_TO_GIB_SCALE * HOURS_PER_MONTH, "1 GB-month: 730/1.073741824 GiB-hours")
    add(["tiby.h", "tib-hours", "tib-hrs"], storage_dim, 1024.0, "1 TiB-hour: 1024 GiB-hours")
    add(["tiby.mo", "tib-mo", "tib-month"], storage_dim, 1024.0 * HOURS_PER_MONTH, "1 TiB-month")
    add(["tb-mo", "tb-month"], storage_dim, ((1000.0 ** 4) / GIB_BYTES) * HOURS_PER_MONTH, "1 TB-month")
    add(["lambda-gb-second", "lambdagbsecond"], storage_dim, GB_TO_GIB_SCALE / 3600.0, "AWS Lambda GB-second: 1 GB * 1 second")

    # ── 4. Throughput (base: 'gibps-mo' = 1.0 GiB/s provisioned for a month) ─────
    # EBS gp3/io2 and Hyperdisk provisioned throughput (P1.2)
    throughput_dim = {"throughput": 1, "time": 1}
    add(["gibps-mo", "gib/s-mo", "gibps-month", "gib/s-month"], throughput_dim, 1.0, "1 GiB/s provisioned for 1 month")
    add(["mibps-mo", "mib/s-mo", "mibps-month", "mib/s-month", "mb/s-mo"], throughput_dim, 1.0 / 1024.0, "1 MiB/s provisioned for 1 month: 1/1024 GiB/s-month")
    add(["gbps-mo", "gb/s-mo", "gbps-month", "gb/s-month"], throughput_dim, 1000.0 / (1024.0 * 1024.0), "1 GB/s provisioned for 1 month (decimal)")

    # ── 5. CPU / Compute (P1.2) ──────────────────────────────────────────────────
    cpu_dim = {"cpu": 1}
    add(["vcpu"], cpu_dim, 1.0, "1 vCPU")
    add(["mcpu"], cpu_dim, 1.0 / 1000.0, "1 mCPU (millicore): 1/1000 vCPU")

    cpu_time_dim = {"cpu": 1, "time": 1}
    add(["vcpu-hours", "vcpu-hrs", "vcpu-hr", "vcpuhours"], cpu_time_dim, 1.0, "1 vCPU-hour")
    add(["mcpu-hours", "mcpu-hrs", "mcpuhours"], cpu_time_dim, 1.0 / 1000.0, "1 mCPU-hour: 1/1000 vCPU-hour")
    add(["vcpu-months", "vcpu-mo", "vcpumonths"], cpu_time_dim, HOURS_PER_MONTH, "1 vCPU-month: 730 vCPU-hours")
    add(["dpu-hour", "dpu-hours", "dpuhours"], cpu_time_dim, 4.0, "1 AWS Glue DPU-hour: 4 vCPU-hours")

    # Aurora Serverless v2 ACU: 1 ACU = 0.25 vCPU + 2 GiB RAM
    add(["acu-hrs", "acu-hours"], {"cpu": 1, "data": 1, "time": 1}, 1.0, "1 Aurora ACU-hour")

    # ── 6. IOPS (P1.2) ───────────────────────────────────────────────────────────
    iops_dim = {"iops": 1}
    add(["iops"], iops_dim, 1.0, "IOPS")

    iops_time_dim = {"iops": 1, "time": 1}
    add(["iops-mo", "iops-month"], iops_time_dim, HOURS_PER_MONTH, "IOPS-month: 730 IOPS-hours")

    # ── 7. Object (P1.2) ─────────────────────────────────────────────────────────
    object_dim = {"object": 1}
    add(["object", "objects"], object_dim, 1.0, "S3 / storage objects count")

    # ── 8. Count / Operations ────────────────────────────────────────────────────
    count_dim = {"count": 1}
    add(["count", "counts", "request", "requests", "req", "reqs",
         "operation", "operations", "op", "ops",
         "call", "calls",
         "unit", "units", "queries", "query", "invocations", "events"], count_dim, 1.0, "Single count")
    add(["1k-requests", "1k-reqs", "1k-ops"], count_dim, 1000.0, "1,000 requests")
    add(["10k-requests", "10k-reqs", "10k-ops"], count_dim, 10000.0, "10,000 requests")
    add(["100k-requests", "100k-reqs", "100k-ops"], count_dim, 100000.0, "100,000 requests")
    add(["1m-requests", "million-requests", "million-reqs", "1m-ops"], count_dim, 1000000.0, "1,000,000 requests")

    # ── 9. User / Seats ──────────────────────────────────────────────────────────
    user_dim = {"user": 1}
    add(["user", "users", "seat", "seats"], user_dim, 1.0, "Single user seat")

    user_time_dim = {"user": 1, "time": 1}
    add(["user-month", "user-mo", "seat-month", "seat-mo"], user_time_dim, HOURS_PER_MONTH, "1 user-month: 730 user-hours")

    return units


def main():
    units = build_units_table()
    data = {
        "_meta": {
            "notice": "GENERATED — do not edit. Regenerate by running: python scripts/gen_unit_dimensions.py",
            "version": 2,
            "description": "Canonical physical unit dimensions and scale factors relative to base units: time in hours (h), data in GiB (gib), throughput in GiB/s-month (gibps-mo), cpu in vCPU (vcpu), iops in IOPS (iops), object in objects (object), count in units (count), seat in users (user).",
            "base_units": {
                "time": "h",
                "data": "gib",
                "throughput": "gibps-mo",
                "cpu": "vcpu",
                "iops": "iops",
                "object": "object",
                "count": "count",
                "user": "user",
            },
        },
        "units": units,
    }

    with open(_OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)

    print(f"Generated {len(units)} units in {_OUTPUT_PATH}")


if __name__ == "__main__":
    main()

