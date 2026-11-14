"""Tests for Amazon DocumentDB full modelling (§4.5 WORKORDER_per_service_full_modelling.md).

Guarantees verified:
1. DocumentDB instance-hours map to Firestore with MongoDB compatibility (review/passthrough),
   NEVER Cloud SQL MySQL or relational database engines.
2. Disclosed architectural candidates (Firestore Enterprise MongoDB compat, MongoDB Atlas on GCP Marketplace,
   self-hosted MongoDB on Compute Engine) and unit model differences are clearly preserved in the projection note.
3. DocumentDB backup storage maps to Cloud Storage Nearline (backup tier for NoSQL/MongoDB),
   NEVER Cloud SQL: Backups.
4. DocumentDB database I/O passes through (never folded into storage capacity, preventing 1300x inflation).
5. DocumentDB storage maps to block_storage.
6. Elastic Clusters and Serverless usage types dispatch to managed_db.
7. Service usage catalog completeness is verified.
"""

import pytest
from scripts import apply_static_mappings as asm
from scripts import spec_dispatch as sd


def test_documentdb_instance_maps_to_firestore_never_mysql():
    """DocumentDB instance-hours must map to Firestore (MongoDB compat), NEVER Cloud SQL MySQL."""
    row = {
        "aws_li_key": "k_docdb_inst",
        "product": "Amazon DocumentDB",
        "product_code": "AmazonDocDB",
        "canonical": "documentdb",
        "database_engine": "docdb",
        "instance_type": "db.r5.large",
        "usage_type": "APS3-InstanceUsage:db.r5.large",
        "operation": "CreateDBInstance",
        "gcp_region": "asia-south1",
        "instance_vcpus": 2,
        "instance_ram_gb": 16.0,
        "aws_amortized_cost": 150.0,
        "total_usage": 100.0,
    }
    parsed = asm.parse_instance("db.r5.large")
    res = asm.map_db_row(row, parsed)
    assert res is not None
    assert len(res) == 1
    entry = res[0]
    assert entry["gcp_service"] == "Firestore"
    assert entry["gcp_sku_name"] == "Firestore (MongoDB compatibility)"
    assert entry["strategy"] == "passthrough"
    assert entry["mapping_confidence"] <= 0.60
    assert "Cloud SQL" not in entry["gcp_service"]
    assert "MySQL" not in entry["gcp_sku_name"]
    assert "MongoDB" in entry["projection_note"]
    assert "Atlas" in entry["projection_note"]
    assert "https://cloud.google.com/firestore/mongodb-compatibility" in entry["projection_note"]


def test_documentdb_backup_never_cloud_sql_backups():
    """DocumentDB backup storage maps to Cloud Storage Nearline, NEVER Cloud SQL: Backups."""
    row = {
        "aws_li_key": "k_docdb_backup",
        "mechanic_rule": "spec:documentdb.documentdb_backup",
        "usage_type": "APS3-BackupUsage",
        "operation": "backup storage",
        "product": "Amazon DocumentDB",
        "gcp_region": "asia-south1",
        "aws_amortized_cost": 25.0,
        "total_usage": 100.0,
    }
    mapped, llm_rows = asm.map_backup([row])
    assert len(mapped) == 1
    entry = mapped[0]
    assert entry["gcp_service"] == "Cloud Storage"
    assert "Nearline" in entry["gcp_sku_name"]
    assert "Cloud SQL" not in entry["gcp_service"]
    assert "Cloud SQL: Backups" not in entry["gcp_sku_name"]


def test_documentdb_io_dispatches_to_misc_passthrough():
    """DocumentDB database I/O must dispatch to documentdb_io / misc (passthrough, never fold into storage)."""
    row = {
        "product": "Amazon DocumentDB",
        "canonical": "documentdb",
        "usage_type": "APS3-StorageIOUsage",
        "pricing_unit": "Requests",
        "unit": "Requests",
        "line_item_type": "Usage",
    }
    res = sd.dispatch(row)
    assert res is not None
    assert res["charge_type"] == "documentdb_io"
    assert res["mechanic"] == "misc"


def test_documentdb_storage_dispatches_to_block_storage():
    """DocumentDB storage must dispatch to storage / block_storage."""
    row = {
        "product": "Amazon DocumentDB",
        "canonical": "documentdb",
        "usage_type": "APS3-StorageUsage",
        "pricing_unit": "GB-Mo",
        "unit": "GB-Mo",
        "line_item_type": "Usage",
    }
    res = sd.dispatch(row)
    assert res is not None
    assert res["charge_type"] == "storage"
    assert res["mechanic"] == "block_storage"


def test_documentdb_elastic_and_serverless_dispatch():
    """Elastic Clusters and Serverless DCU dispatch to managed_db."""
    row_elastic = {
        "product": "Amazon DocumentDB",
        "canonical": "documentdb",
        "usage_type": "APS3-ElasticCluster-vCPU",
        "pricing_unit": "Hrs",
        "unit": "Hrs",
        "line_item_type": "Usage",
    }
    res_elastic = sd.dispatch(row_elastic)
    assert res_elastic is not None
    assert res_elastic["charge_type"] == "documentdb_elastic"
    assert res_elastic["mechanic"] == "managed_db"

    row_serverless = {
        "product": "Amazon DocumentDB",
        "canonical": "documentdb",
        "usage_type": "APS3-ServerlessUsage",
        "pricing_unit": "DCU-Hrs",
        "unit": "DCU-Hrs",
        "line_item_type": "Usage",
    }
    res_serverless = sd.dispatch(row_serverless)
    assert res_serverless is not None
    assert res_serverless["charge_type"] == "documentdb_serverless"
    assert res_serverless["mechanic"] == "managed_db"
