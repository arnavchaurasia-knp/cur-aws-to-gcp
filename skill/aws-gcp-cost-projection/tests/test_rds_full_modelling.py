import os
import sys
import pytest

HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from apply_static_mappings import map_db_row, parse_instance, map_block_storage

def _make_row(engine=None, itype="db.m5.large", deployment="Single-AZ", region="us-central1", product="Amazon Relational Database Service", operation="CreateDBInstance:0002"):
    return {
        "aws_li_key": "k_test",
        "product": product,
        "product_code": "AmazonRDS",
        "canonical": "rds",
        "usage_type": f"InstanceUsage:{itype}",
        "operation": operation,
        "instance_type": itype,
        "database_engine": engine,
        "deployment_option": deployment,
        "instance_vcpus": 2,
        "instance_ram_gb": 8.0,
        "gcp_region": region,
        "aws_amortized_cost": 100.0,
        "total_usage": 100.0,
        "unit": "Hrs",
        "pricing_unit": "Hrs",
    }


def test_rds_sqlserver_maps_to_cloud_sql_sqlserver():
    row = _make_row(engine="sqlserver-ex", itype="db.m5.large", deployment="Single-AZ")
    parsed = parse_instance("db.m5.large")
    res = map_db_row(row, parsed)
    assert res is not None
    assert len(res) == 2
    for e in res:
        assert e["gcp_service"] == "Cloud SQL"
        assert "Cloud SQL for SQL Server: Zonal" in e["gcp_sku_name"]
        assert e["strategy"] == "map"
        assert e["mapping_confidence"] == 0.90


def test_rds_sqlserver_multi_az_maps_to_regional():
    row = _make_row(engine="sqlserver-ee", itype="db.m5.large", deployment="Multi-AZ")
    parsed = parse_instance("db.m5.large")
    res = map_db_row(row, parsed)
    assert res is not None
    assert len(res) == 2
    for e in res:
        assert e["gcp_service"] == "Cloud SQL"
        assert "Cloud SQL for SQL Server: Regional" in e["gcp_sku_name"]
        assert e["strategy"] == "map"


def test_rds_oracle_maps_to_oracle_gcp_passthrough():
    row = _make_row(engine="oracle-ee", itype="db.m5.large", region="us-central1")
    parsed = parse_instance("db.m5.large")
    res = map_db_row(row, parsed)
    assert res is not None
    assert len(res) == 1
    e = res[0]
    assert e["gcp_service"] == "Oracle Database@Google Cloud"
    assert e["strategy"] == "passthrough"
    assert "Oracle Database@Google Cloud" in e["projection_note"]
    assert "https://cloud.google.com/oracle/database/docs/overview" in e["projection_note"]


def test_rds_oracle_unavailable_region_passthrough():
    row = _make_row(engine="oracle-se2", itype="db.m5.large", region="mars-north1")
    parsed = parse_instance("db.m5.large")
    res = map_db_row(row, parsed)
    assert res is not None
    assert len(res) == 1
    e = res[0]
    assert e["gcp_service"] == "Oracle Database@Google Cloud"
    assert e["strategy"] == "passthrough"
    assert "unavailable" in e["gcp_sku_name"].lower() or "not offered" in e["projection_note"].lower()


def test_rds_db2_maps_to_manual_review_passthrough():
    row = _make_row(engine="db2-standard", itype="db.m5.large")
    parsed = parse_instance("db.m5.large")
    res = map_db_row(row, parsed)
    assert res is not None
    assert len(res) == 1
    e = res[0]
    assert e["gcp_service"] == "Manual Review"
    assert e["strategy"] == "passthrough"
    assert "no native GCP managed database equivalent" in e["projection_note"]


def test_rds_mariadb_maps_to_mysql_with_disclosed_substitution():
    row = _make_row(engine="mariadb", itype="db.m5.large")
    parsed = parse_instance("db.m5.large")
    res = map_db_row(row, parsed)
    assert res is not None
    assert len(res) == 2
    for e in res:
        assert e["gcp_service"] == "Cloud SQL"
        assert "Cloud SQL for MySQL" in e["gcp_sku_name"]
        assert "engine substitution: Cloud SQL has no native MariaDB" in e["projection_note"]


def test_aurora_postgresql_defaults_to_cloud_sql_pg_with_alloydb_alternative():
    row = _make_row(engine="aurora-postgresql", itype="db.r5.large", region="us-central1", product="Amazon Aurora PostgreSQL")
    parsed = parse_instance("db.r5.large")
    res = map_db_row(row, parsed)
    assert res is not None
    assert len(res) == 2
    for e in res:
        assert e["gcp_service"] == "Cloud SQL"
        assert "Cloud SQL for PostgreSQL" in e["gcp_sku_name"]
        assert e["strategy"] in ("map", "passthrough")
        assert "AlloyDB for PostgreSQL" in e["projection_note"]


def test_rds_standard_postgresql_maps_to_cloud_sql_pg_with_alloydb_note():
    row = _make_row(engine="postgres", itype="db.m5.large", region="us-central1")
    parsed = parse_instance("db.m5.large")
    res = map_db_row(row, parsed)
    assert res is not None
    assert len(res) == 2
    for e in res:
        assert e["gcp_service"] == "Cloud SQL"
        assert "Cloud SQL for PostgreSQL" in e["gcp_sku_name"]
        assert e["strategy"] == "map"
        assert "AlloyDB for PostgreSQL" in e["projection_note"]


def test_rds_unknown_engine_routes_to_manual_review_not_mysql():
    row = _make_row(engine="", itype="db.m5.large", product="Amazon Relational Database Service", operation="CreateDBInstance")
    parsed = parse_instance("db.m5.large")
    res = map_db_row(row, parsed)
    assert res is not None
    assert len(res) == 1
    e = res[0]
    assert e["gcp_service"] == "Manual Review"
    assert e["strategy"] == "passthrough"
    assert e["mapping_confidence"] == 0.40
    assert "engine could not be determined" in e["projection_note"]


def test_rds_multiaz_db_cluster_carries_topology_note():
    row = _make_row(engine="mysql", itype="db.m5.large", deployment="Multi-AZ DB cluster")
    parsed = parse_instance("db.m5.large")
    res = map_db_row(row, parsed)
    assert res is not None
    for e in res:
        assert "Regional" in e["gcp_sku_name"]
        assert "Multi-AZ DB cluster (2 readable standbys)" in e["projection_note"]


def test_rds_graviton_instance_carries_c4a_arm_note():
    row = _make_row(engine="mysql", itype="db.m6g.xlarge")
    parsed = parse_instance("db.m6g.xlarge")
    res = map_db_row(row, parsed)
    assert res is not None
    for e in res:
        assert "Enterprise Plus candidate offers C4A Arm" in e["projection_note"]


def test_rds_io1_io2_provisioned_iops_not_ignored():
    row = {
        "aws_li_key": "k_io2",
        "product": "Amazon Relational Database Service",
        "usage_type": "APS3-ProvisionedIOPS",
        "operation": "CreateDBInstance:io2",
        "pricing_unit": "IOPS-Mo",
        "gcp_region": "us-central1",
        "aws_amortized_cost": 200.0,
        "total_usage": 1000.0,
        "is_workload": True,
    }
    out = map_block_storage([row])
    assert len(out) == 1
    assert out[0]["strategy"] == "passthrough"
    assert "io1/io2 Provisioned IOPS" in out[0]["projection_note"]


def test_aurora_serverless_v2_acu_ratio_reconcile():
    # WORKORDER_followup_fixes A3: 0.25 vCPU + 2.0 GiB per ACU (project decision D2)
    from apply_static_mappings import map_managed_db
    row = {
        "aws_li_key": "k_acu",
        "product": "Amazon Aurora PostgreSQL",
        "usage_type": "ServerlessV2Usage",
        "operation": "AuroraServerlessV2",
        "unit": "ACU-Hr",
        "gcp_region": "us-central1",
        "aws_amortized_cost": 500.0,
        "total_usage": 1000.0,
    }
    mapped, _ = map_managed_db([row])
    assert len(mapped) == 2
    core = next(e for e in mapped if e["component"] == "core")
    ram = next(e for e in mapped if e["component"] == "ram")
    assert core["unit_multiplier"] == pytest.approx(0.25)
    assert ram["unit_multiplier"] == pytest.approx(2.0)
    assert "0.25" in core["projection_note"]
    assert "2 GiB" in core["projection_note"] or "2" in core["projection_note"]
    assert "0.25" in core["assumption"] and "2 GiB" in core["assumption"]
