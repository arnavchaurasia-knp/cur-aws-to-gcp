"""
Tests for WORKORDER_pricer_correctness fixes (P-1 through P-13).
"""

import re
import pytest
from unittest.mock import patch
from conftest import row

from apply_static_mappings import (
    SKUMeta,
    GB_TO_GIB,
    map_block_storage,
    map_efs,
    map_per_request,
    map_fsx,
    map_dynamodb_storage,
    map_flat_hourly,
    map_managed_db,
    map_shield,
    map_msk,
    map_opensearch,
    map_commitment_discount,
    map_redshift,
    map_compute_breakdown,
    resolve_sku,
    family_arch,
    _emit,
)
import apply_rates
import merge_mappings
import duckdb
import family_mapper as fm

MOCK_SKU = SKUMeta("MOCK-SKU-1234", unit="h", resource_group="Mock")


def _with_sku(mapper, rows):
    """Call mapper with resolve_sku patched to return MOCK_SKU."""
    with patch("apply_static_mappings.resolve_sku", return_value=MOCK_SKU):
        res = mapper(rows)
    if isinstance(res, tuple):
        return res[0]
    return res


# ---------------------------------------------------------------------------
# P-1: Unified GB -> GiB conversion
# ---------------------------------------------------------------------------

def test_p1_ebs_gp3_capacity_uses_gb_to_gib():
    rows = [row(product="Amazon Elastic Block Store", usage_type="APS3-VolumeUsage.gp3", unit="GB-Mo")]
    out = _with_sku(map_block_storage, rows)
    assert len(out) == 1
    assert out[0]["component"] == "storage"
    assert pytest.approx(out[0]["unit_multiplier"], rel=1e-5) == GB_TO_GIB
    assert pytest.approx(1000.0 * out[0]["unit_multiplier"], rel=1e-3) == 931.098


def test_p1_efs_standard_uses_gb_to_gib():
    rows = [row(product="Amazon Elastic File System", usage_type="APS3-StorageUsage", unit="GB-Mo")]
    out = _with_sku(map_efs, rows)
    assert len(out) == 1
    assert out[0]["component"] == "storage"
    assert pytest.approx(out[0]["unit_multiplier"], rel=1e-5) == GB_TO_GIB


def test_p1_fargate_memory_uses_gb_to_gib():
    rows = [row(product="Amazon Elastic Container Service", usage_type="APS3-Fargate-GB-Hours", unit="GB-Hours")]
    out = _with_sku(map_per_request, rows)
    assert len(out) == 1
    assert out[0]["component"] == "memory"
    assert pytest.approx(out[0]["unit_multiplier"], rel=1e-5) == GB_TO_GIB


def test_p1_fsx_uses_gb_to_gib():
    rows = [row(product="Amazon FSx for Lustre", usage_type="APS3-StorageUsage", unit="GB-Mo")]
    out = _with_sku(map_fsx, rows)
    assert len(out) == 1
    assert out[0]["component"] == "storage"
    assert pytest.approx(out[0]["unit_multiplier"], rel=1e-5) == GB_TO_GIB


def test_p1_dynamodb_storage_uses_gb_to_gib():
    rows = [row(product="Amazon DynamoDB", usage_type="APS3-TimedStorage-ByteHrs", unit="GB-Mo")]
    out = _with_sku(map_dynamodb_storage, rows)
    assert len(out) == 1
    assert out[0]["component"] == "storage"
    assert pytest.approx(out[0]["unit_multiplier"], rel=1e-5) == GB_TO_GIB


# ---------------------------------------------------------------------------
# P-2: flat_hourly emits strategy="map" if sku_id else "passthrough"
# and unrecognized hourly charge emits passthrough without fabricated SKU
# ---------------------------------------------------------------------------

def test_p2_unrecognized_hourly_emits_passthrough():
    rows = [row(product="AmazonVPC", usage_type="APS3-SomeNovelFeature-Hours", unit="Hrs")]
    out = map_flat_hourly(rows)
    assert len(out) == 1
    assert out[0]["strategy"] == "passthrough"
    assert out[0]["gcp_sku_id"] is None
    assert "unrecognized hourly charge" in out[0]["projection_note"]
    assert out[0]["mapping_confidence"] <= 0.30


def test_p2_matched_hourly_unresolved_sku_emits_passthrough():
    rows = [row(product="Amazon Elastic Container Service for Kubernetes CreateOperation", usage_type="", unit="Hrs")]
    with patch("apply_static_mappings.resolve_sku", return_value=None):
        out = map_flat_hourly(rows)
    assert len(out) == 1
    assert out[0]["strategy"] == "passthrough"
    assert out[0]["gcp_sku_id"] is None


def test_p2_matched_hourly_resolved_sku_emits_map():
    rows = [row(product="Amazon Elastic Container Service for Kubernetes CreateOperation", usage_type="", unit="Hrs")]
    out = _with_sku(map_flat_hourly, rows)
    assert len(out) == 1
    assert out[0]["strategy"] == "map"
    assert out[0]["gcp_sku_id"] is not None


# ---------------------------------------------------------------------------
# P-3: Aurora Serverless v2 ACU sizing — 1 ACU = 2 GiB RAM + ~0.25 vCPU
# (AWS does not expose a custom vCPU for Serverless v2; empirical 16 ACU ≈
#  32 GiB/4 vCPU ≈ db.r5.xlarge → 0.25 vCPU/ACU)
# ---------------------------------------------------------------------------

def test_p3_aurora_serverless_v2_ram_multiplier_is_2():
    rows = [row(
        product="Amazon Aurora PostgreSQL",
        usage_type="APS3-ServerlessUsage",
        unit="ACU-Hr",
        pricing_unit="ACU-Hr",
        total_usage=1000.0,
    )]
    out = _with_sku(map_managed_db, rows)
    assert len(out) == 2
    components = {e["component"]: e for e in out}
    assert "core" in components
    assert "ram" in components
    assert components["ram"]["unit_multiplier"] == 16.0
    assert components["core"]["unit_multiplier"] == 2.0
    assert "1 ACU = 2 vCPU + 16 GiB" in components["ram"]["projection_note"]


# ---------------------------------------------------------------------------
# P-4: DynamoDB PITR detection reads usage_type blob & sets Cloud Firestore
# ---------------------------------------------------------------------------

def test_p4_dynamodb_pitr_detection_and_service_label():
    rows = [row(
        product="Amazon DynamoDB",
        usage_type="APS3-TimedPITRStorage-ByteHrs",
        operation="",
        unit="GB-Mo",
    )]
    out = _with_sku(map_dynamodb_storage, rows)
    assert len(out) == 1
    assert out[0]["component"] == "pitr-storage"
    assert out[0]["gcp_service"] == "Cloud Firestore"
    assert "Point-in-time Recovery Storage" in out[0]["gcp_sku_name"]


# ---------------------------------------------------------------------------
# P-5: Shield prices subscription at most once, splits DTO, and ignores standard
# ---------------------------------------------------------------------------

def test_p5_shield_subscription_dedup_dto_and_standard():
    rows = [
        row(aws_li_key="k_sub1", product="AWS Shield", usage_type="Shield-Advanced-Subscription-Monthly", aws_amortized_cost=3000.0),
        row(aws_li_key="k_sub2", product="AWS Shield", usage_type="Shield-Advanced-Subscription-Monthly", aws_amortized_cost=3000.0),
        row(aws_li_key="k_dto", product="AWS Shield", usage_type="Shield-Advanced-DataTransfer-Out-Bytes", aws_amortized_cost=150.0),
        row(aws_li_key="k_std", product="AWS Shield", usage_type="Shield-Standard", aws_amortized_cost=0.0),
    ]
    out = _with_sku(map_shield, rows)
    assert len(out) == 4

    sub_entries = [e for e in out if e["component"] == "subscription" and e["strategy"] == "map"]
    assert len(sub_entries) == 1
    assert sub_entries[0]["gcp_service"] == "Cloud Armor Enterprise"

    sub_dedup = [e for e in out if e["component"] == "subscription" and e["strategy"] == "ignore"]
    assert len(sub_dedup) == 1
    assert "$0 dedup" in sub_dedup[0]["projection_note"]

    dto_entries = [e for e in out if e["component"] == "data_transfer"]
    assert len(dto_entries) == 1
    assert dto_entries[0]["strategy"] == "passthrough"

    std_entries = [e for e in out if e["aws_li_key"] == "k_std"]
    assert len(std_entries) == 1
    assert std_entries[0]["strategy"] == "ignore"


# ---------------------------------------------------------------------------
# P-6: MSK ARM brokers (r6g, m7g, Graviton detection)
# ---------------------------------------------------------------------------

def test_p6_msk_arm_graviton_detection():
    rows = [
        row(aws_li_key="k_r6g", product="Amazon Managed Streaming for Apache Kafka", usage_type="APS3-Kafka.r6g.large"),
        row(aws_li_key="k_m7g", product="Amazon Managed Streaming for Apache Kafka", usage_type="APS3-Kafka.m7g.large"),
        row(aws_li_key="k_m5", product="Amazon Managed Streaming for Apache Kafka", usage_type="APS3-Kafka.m5.large"),
    ]
    out = _with_sku(map_msk, rows)

    # r6g is ARM
    r6g_entries = [e for e in out if e["aws_li_key"] == "k_r6g"]
    assert len(r6g_entries) >= 1
    assert any("Arm" in (e.get("gcp_sku_name") or "") or "arm" in e.get("projection_note", "").lower() for e in r6g_entries)

    # m7g is ARM
    m7g_entries = [e for e in out if e["aws_li_key"] == "k_m7g"]
    assert len(m7g_entries) >= 1
    assert any("Arm" in (e.get("gcp_sku_name") or "") or "arm" in e.get("projection_note", "").lower() for e in m7g_entries)

    # m5 is x86 sustained (N4D/N2D)
    m5_entries = [e for e in out if e["aws_li_key"] == "k_m5"]
    assert len(m5_entries) >= 1
    for e in m5_entries:
        assert "arm" not in (e.get("gcp_sku_name") or "").lower()


# ---------------------------------------------------------------------------
# P-7: OpenSearch compute-optimized .search nodes shop against compute pool
# ---------------------------------------------------------------------------

def test_p7_opensearch_compute_optimized_scoped_to_compute():
    rows = [row(
        aws_li_key="k_c6i",
        product="Amazon OpenSearch Service",
        usage_type="APS3-ESNode:c6i.4xlarge.search",
        instance_type="c6i.4xlarge.search",
    )]
    res = map_opensearch(rows)
    mapped, llm_rows = res if isinstance(res, tuple) else (res, [])
    assert len(mapped) == 2  # core and ram
    # c6i should map to C4D compute family, not N4D general
    core_entry = next(e for e in mapped if e["component"] == "core")
    assert "C4D" in core_entry["projection_note"] or "Compute" in core_entry.get("gcp_sku_name", "")


# ---------------------------------------------------------------------------
# P-8: EFS bare 'ia' substring removed so California / standard won't match HDD
# ---------------------------------------------------------------------------

def test_p8_efs_standard_containing_ia_does_not_match_hdd():
    rows = [row(
        product="Amazon Elastic File System",
        usage_type="APS3-StorageUsage-California-Standard",
        operation="VariableIO-Standard",
        unit="GB-Mo",
    )]
    out = _with_sku(map_efs, rows)
    assert len(out) == 1
    assert "Basic SSD" in out[0]["gcp_sku_name"]


# ---------------------------------------------------------------------------
# P-9: commitment_discount defensive guard
# ---------------------------------------------------------------------------

def test_p9_commitment_discount_guard():
    genuine = [
        row(aws_li_key="k_ri", product="Amazon EC2", line_item_type="RIFee", aws_amortized_cost=100.0),
        row(aws_li_key="k_sp", product="Amazon EC2", line_item_type="SavingsPlanRecurringFee", aws_amortized_cost=50.0),
        row(aws_li_key="k_neg", product="Amazon EC2", line_item_type="Usage", aws_amortized_cost=-20.0),
    ]
    non_comm = [
        row(aws_li_key="k_real_usage", product="Amazon EC2", line_item_type="Usage", usage_type="BoxUsage:m5.large", aws_amortized_cost=30.0),
    ]
    out_gen, llm_gen = map_commitment_discount(genuine)
    assert len(out_gen) == 3
    assert len(llm_gen) == 0
    for e in out_gen:
        assert e["strategy"] == "ignore"

    out_nc, llm_nc = map_commitment_discount(non_comm)
    assert len(out_nc) == 0
    assert len(llm_nc) == 1
    assert llm_nc[0]["aws_li_key"] == "k_real_usage"


# ---------------------------------------------------------------------------
# P-10: Redshift RMS entry initializes gcp_sku_id key to None
# ---------------------------------------------------------------------------

def test_p10_redshift_rms_entry_has_gcp_sku_id_key():
    rows = [row(
        product="Amazon Redshift",
        usage_type="APS3-RMS:ra3.4xlarge",
        operation="",
        unit="GB-Mo",
    )]
    with patch("apply_static_mappings.resolve_sku", return_value=None):
        out = map_redshift(rows)
    assert len(out) >= 1
    rms_entry = out[0]
    assert "gcp_sku_id" in rms_entry
    assert rms_entry["gcp_sku_id"] is None


# ---------------------------------------------------------------------------
# P-11: Local SSD true capacity scaling
# ---------------------------------------------------------------------------

def test_p11_family_mapper_local_ssd_true_capacity():
    # i3en.24xlarge has 60,000 GB NVMe storage
    r_i3en = {
        "aws_li_key": "k_i3en",
        "instance_type": "i3en.24xlarge",
        "instance_vcpus": 96,
        "instance_ram_gb": 768.0,
        "gcp_region": "us-central1",
    }
    parsed = fm.parse_instance("i3en.24xlarge")
    res = fm.map_gce_row(r_i3en, parsed)
    storage_entries = [e for e in res if e["component"] == "storage"]
    assert len(storage_entries) == 1
    # 60,000 / 730 = 82.19178...
    expected_mult = 60000.0 / 730.0
    assert pytest.approx(storage_entries[0]["unit_multiplier"], rel=1e-3) == expected_mult
    assert "60000" in storage_entries[0]["projection_note"]


def test_p11_family_mapper_gpu_local_ssd():
    # g4dn.xlarge has 125 GB NVMe storage
    r_g4dn = {
        "aws_li_key": "k_g4dn",
        "instance_type": "g4dn.xlarge",
        "instance_vcpus": 4,
        "instance_ram_gb": 16.0,
        "gcp_region": "us-central1",
    }
    parsed = fm.parse_instance("g4dn.xlarge")
    res = fm.map_gpu_row(r_g4dn, parsed)
    storage_entries = [e for e in res if e["component"] == "storage"]
    assert len(storage_entries) == 1
    expected_mult = 125.0 / 730.0
    assert pytest.approx(storage_entries[0]["unit_multiplier"], rel=1e-3) == expected_mult


# ---------------------------------------------------------------------------
# P-13: gp3 throughput per-volume guard
# ---------------------------------------------------------------------------

def test_p13_gp3_throughput_aggregate_not_escalated_to_extreme():
    # An account-wide aggregate throughput of 3000 GiBps-month (> 2400 ceiling)
    rows = [row(
        product="Amazon Elastic Block Store",
        usage_type="APS3-VolumeP-Throughput.gp3",
        unit="GiBps-mo",
        total_usage=3000.0,
    )]
    out = _with_sku(map_block_storage, rows)
    assert len(out) == 1
    assert out[0]["component"] == "throughput"
    assert "Hyperdisk Balanced Throughput" in out[0]["gcp_sku_name"]
    assert out[0]["strategy"] == "map"


# ---------------------------------------------------------------------------
# P-14: Tiered rate blending on SUM & egress ladder
# ---------------------------------------------------------------------------

def test_p14_tiered_rates_blend_and_egress_ladder():
    total_gb = 53248.0
    expected_cost = 1024.0 * 0.12 + (10240.0 - 1024.0) * 0.11 + (total_gb - 10240.0) * 0.08
    expected_rate = expected_cost / total_gb

    actual_rate = apply_rates.blended_rate(apply_rates._INTERNET_EGRESS_TIERS, total_gb)
    assert pytest.approx(actual_rate, rel=1e-5) == expected_rate

    r1_cost = 51200.0 * actual_rate
    r2_cost = 2048.0 * actual_rate
    assert pytest.approx(r1_cost + r2_cost, rel=1e-5) == expected_cost


# ---------------------------------------------------------------------------
# P-15: NAT Gateway public vs private split & per-VM modeling
# ---------------------------------------------------------------------------

def test_p15_nat_gateway_public_vs_private():
    # Public NAT row — Directive 4: 32-VM cap ($0.044/hr), no fleet-VM dependency
    pub_rows = [row(
        product="Amazon Virtual Private Cloud",
        usage_type="APS3-NatGateway-Hours",
        operation="",
        unit="Hrs",
        aws_amortized_cost=100.0,
        total_usage=730.0,
    )]
    pub_out = _with_sku(map_flat_hourly, pub_rows)
    assert len(pub_out) == 1
    assert "Cloud Nat" in pub_out[0]["gcp_sku_name"]
    assert pub_out[0]["is_assumption"] is True
    assert "32-VM cap" in pub_out[0]["assumption"]
    assert abs(pub_out[0]["unit_multiplier"] * 0.0014 - 0.044) < 1e-4

    # Private NAT row
    priv_rows = [row(
        product="Amazon Virtual Private Cloud",
        usage_type="APS3-NatGateway-Hours",
        operation="PrivateNatGateway",
        unit="Hrs",
        aws_amortized_cost=100.0,
    )]
    priv_out = _with_sku(map_flat_hourly, priv_rows)
    assert len(priv_out) == 1
    assert "Private Nat" in priv_out[0]["gcp_sku_name"]
    assert priv_out[0]["unit_multiplier"] == 1.0


def test_directive_4_nat_fleet_vm_independence():
    # A NAT row with 500 fleet VMs behind 1 gateway -> still uses 32-VM cap ($0.044/hr), NOT 500x
    pub_rows = [row(
        product="Amazon Virtual Private Cloud",
        usage_type="APS3-NatGateway-Hours",
        operation="",
        unit="Hrs",
        aws_amortized_cost=32.85, # 730 * $0.045
        total_usage=730.0,
        _vms_per_gateway=500, # Fleet VM test hint
    )]
    pub_out = _with_sku(map_flat_hourly, pub_rows)
    assert len(pub_out) == 1
    unit_rate = pub_out[0]["unit_multiplier"] * 0.0014
    assert abs(unit_rate - 0.044) < 1e-4
    projected_cost = pub_rows[0]["total_usage"] * unit_rate
    # Projected cost is 730 * 0.044 = $32.12, within 5% of AWS $32.85
    assert abs(projected_cost - pub_rows[0]["aws_amortized_cost"]) / pub_rows[0]["aws_amortized_cost"] < 0.05



# ---------------------------------------------------------------------------
# AR-1: Strict SKU resolver by default
# ---------------------------------------------------------------------------

def test_ar1_strict_sku_resolver_default():
    from apply_static_mappings import resolve_sku, lookup_sku_in_catalog
    res_lookup = lookup_sku_in_catalog("Compute Engine", "Some Nonexistent Sku in Region", "asia-south2", allow_continent_fallback=False)
    assert res_lookup[0] is None
    res_strict = resolve_sku("Compute Engine", "Some Nonexistent Sku in Region", "asia-south2", allow_continent_fallback=False)
    assert res_strict.sku_id is None
    assert not res_strict



# ---------------------------------------------------------------------------
# AR-2: Frozen cache / read-only resolver (no live network/subprocess calls)
# ---------------------------------------------------------------------------

def test_ar2_resolver_read_only_no_network_calls():
    with patch("subprocess.run") as mock_run, patch("subprocess.check_output") as mock_co:
        res = resolve_sku("Compute Engine", "Definitely Not Real SKU 9999", "us-central1")
        assert res.sku_id is None
        mock_run.assert_not_called()
        mock_co.assert_not_called()


# ---------------------------------------------------------------------------
# AR-3: Consolidated compute breakdown in apply_static_mappings
# ---------------------------------------------------------------------------

def test_ar3_consolidated_compute():
    r_vm = row(
        aws_li_key="k_ec2_m5",
        product="Amazon Elastic Compute Cloud",
        instance_type="m5.xlarge",
        instance_vcpus=4,
        instance_ram_gb=16.0,
        gcp_region="us-central1",
    )
    mapped, llm_rows = map_compute_breakdown([r_vm])
    assert len(mapped) == 2
    assert len(llm_rows) == 0
    core = next(e for e in mapped if e["component"] == "core")
    ram = next(e for e in mapped if e["component"] == "ram")
    assert core["gcp_service"] == "Compute Engine"
    assert ram["gcp_service"] == "Compute Engine"

    arch, workload = family_arch("m6g.xlarge")
    assert arch == "arm"
    assert workload == "general"

    arch, workload = family_arch("c7g.2xlarge")
    assert arch == "arm"
    assert workload == "compute"

    e = _emit("k_test", "map", "Compute Engine", gcp_sku_id=None)
    assert e["strategy"] == "passthrough"
    assert e["gcp_sku_id"] is None


# ---------------------------------------------------------------------------
# AR-4: Dominant region rate fallback chain
# ---------------------------------------------------------------------------

def test_ar4_dominant_region_fallback():
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE TABLE aws_li_catalog (gcp_region TEXT, aws_amortized_cost DOUBLE)")
    conn.execute("INSERT INTO aws_li_catalog VALUES ('asia-south1', 100.0), ('asia-south1', 50.0), ('us-central1', 10.0)")
    dom = apply_rates.compute_dominant_region(conn)
    assert dom == "asia-south1"


# ---------------------------------------------------------------------------
# AR-5: Assumptions in schema and merge_mappings
# ---------------------------------------------------------------------------

def test_ar5_assumption_schema_columns():
    assert "is_assumption" in merge_mappings.COLUMNS
    assert "assumption_note" in merge_mappings.COLUMNS
    conn = duckdb.connect(":memory:")
    merge_mappings.ensure_table(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info('aws_li_to_gcp_li')").fetchall()}
    assert "is_assumption" in cols
    assert "assumption_note" in cols

