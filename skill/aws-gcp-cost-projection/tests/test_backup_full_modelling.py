"""Full-modelling test suite for AWS Backup (§4.3 of WORKORDER_per_service_full_modelling.md).

DoD coverage:
- EBS/AMI backup storage -> Compute Engine Storage PD Snapshot (snapshot-class rate, NOT live Hyperdisk capacity)
- Managed DB backup storage (RDS, Aurora, DocumentDB, Neptune) -> Cloud SQL: Backups
- File/Object/NoSQL backup storage (EFS, FSx, DynamoDB, S3) -> Cloud Storage Nearline Storage
- Cold/Archive backup storage -> Cloud Storage Archive Storage
- Restore operations -> Cloud Storage Data Retrieval
- Copy operations (cross-region/account) -> Compute Engine Inter-Region Egress
- Non-storage operations (Audit, Search, Malware) -> passthrough with explicit reason
- Core invariant: An AWS Backup row must NEVER be priced as live Hyperdisk/PD capacity!

Sources verified 2026-11-13:
  aws.amazon.com/backup/pricing
  cloud.google.com/compute/disks-image-pricing#persistentdisk_snapshots
  cloud.google.com/storage/pricing
  cloud.google.com/sql/pricing
"""

import pytest
import scripts.apply_static_mappings as asm
import scripts.spec_dispatch as sd


# ─── helpers ────────────────────────────────────────────────────────────────

def _backup_row(usage_type, operation="", unit="GB-Mo", product_code="AWSBackup"):
    return {
        "aws_li_key":                 "k_backup_test",
        "product":                    "AWS Backup",
        "product_code":               product_code,
        "canonical":                  "backup",
        "usage_type":                 usage_type,
        "operation":                  operation,
        "pricing_unit":               unit,
        "unit":                       unit,
        "line_item_type":             "Usage",
        "pricing_model":              "OnDemand",
        "aws_amortized_cost":         50.0,
        "total_usage":                100.0,
        "gcp_region":                 "us-central1",
        "aws_region":                 "us-east-1",
        "_bill_has_discounted_usage": True,
        "instance_type":              "",
        "deployment_option":          "",
        "database_engine":            "",
        "volume_type":                "",
        "instance_ram_gb":            None,
        "mechanic_group":             "backup",
        "mechanic_rule":              None,
        "charge_type_id":             None,
    }


def _dispatch(usage_type, operation="", unit="GB-Mo"):
    row = _backup_row(usage_type, operation, unit)
    return sd.dispatch(row)


def _map(charge_type_id, usage_type, operation="", unit="GB-Mo"):
    """Dispatch a row and pass it through map_backup."""
    base = _backup_row(usage_type, operation, unit)
    base["mechanic_rule"] = f"spec:backup.{charge_type_id}"
    mapped, llm = asm.map_backup([base])
    return mapped, llm


# ─── Dispatch Tests ──────────────────────────────────────────────────────────

class TestBackupDispatch:
    """Verify charge-type and mechanic routing for all AWS Backup billing variants."""

    def test_generic_warm_storage_dispatches(self):
        r = _dispatch("APS3-BackupUsage")
        assert r["charge_type"] == "backup_storage_warm"
        assert r["mechanic"] == "backup"

    def test_ebs_warm_storage_dispatches(self):
        r = _dispatch("APS3-WarmStorage-ByteHrs-EBS")
        assert r["charge_type"] == "backup_ebs"
        assert r["mechanic"] == "backup"

    def test_cold_storage_dispatches(self):
        r = _dispatch("APS3-ColdStorage-ByteHrs")
        assert r["charge_type"] == "backup_cold"
        assert r["mechanic"] == "backup"

    def test_cold_storage_ebs_dispatches(self):
        r = _dispatch("APS3-ColdStorage-ByteHrs-EBS")
        assert r["charge_type"] == "backup_cold"
        assert r["mechanic"] == "backup"

    def test_rds_warm_storage_dispatches(self):
        r = _dispatch("APS3-WarmStorage-ByteHrs-RDS")
        assert r["charge_type"] == "backup_db"
        assert r["mechanic"] == "backup"

    def test_aurora_warm_storage_dispatches(self):
        r = _dispatch("APS3-WarmStorage-ByteHrs-Aurora")
        assert r["charge_type"] == "backup_db"
        assert r["mechanic"] == "backup"

    def test_documentdb_warm_storage_dispatches(self):
        r = _dispatch("APS3-WarmStorage-ByteHrs-DocumentDB")
        assert r["charge_type"] == "backup_db"
        assert r["mechanic"] == "backup"

    def test_efs_warm_storage_dispatches(self):
        r = _dispatch("APS3-WarmStorage-ByteHrs-EFS")
        assert r["charge_type"] == "backup_efs"
        assert r["mechanic"] == "backup"

    def test_fsx_warm_storage_dispatches(self):
        r = _dispatch("APS3-WarmStorage-ByteHrs-FSx")
        assert r["charge_type"] == "backup_fsx"
        assert r["mechanic"] == "backup"

    def test_dynamodb_warm_storage_dispatches(self):
        r = _dispatch("APS3-WarmStorage-ByteHrs-DynamoDB")
        assert r["charge_type"] == "backup_dynamodb"
        assert r["mechanic"] == "backup"

    def test_s3_warm_storage_dispatches(self):
        r = _dispatch("APS3-WarmStorage-ByteHrs-S3")
        assert r["charge_type"] == "backup_s3"
        assert r["mechanic"] == "backup"

    def test_restore_dispatches(self):
        r = _dispatch("APS3-Restore-Bytes", unit="GB")
        assert r["charge_type"] == "backup_restore"
        assert r["mechanic"] == "backup"

    def test_restore_ebs_dispatches(self):
        r = _dispatch("APS3-Restore-Bytes-EBS", unit="GB")
        assert r["charge_type"] == "backup_restore"
        assert r["mechanic"] == "backup"

    def test_cross_region_dispatches(self):
        r = _dispatch("APS3-CrossRegion-Bytes", unit="GB")
        assert r["charge_type"] == "backup_copy"
        assert r["mechanic"] == "backup"

    def test_audit_evaluations_dispatches(self):
        r = _dispatch("BackupAudit-Evaluations", unit="Count")
        assert r["charge_type"] == "backup_audit"
        assert r["mechanic"] == "backup"

    def test_search_items_dispatches(self):
        r = _dispatch("BackupSearch-ItemCount", unit="Count")
        assert r["charge_type"] == "backup_search"
        assert r["mechanic"] == "backup"

    def test_malware_scan_dispatches(self):
        r = _dispatch("Backup-Malware-ScanBytes", unit="GB")
        assert r["charge_type"] == "backup_malware"
        assert r["mechanic"] == "backup"


# ─── Mapping Contract & Target Tests ─────────────────────────────────────────

class TestBackupMappingContract:
    """Verify that map_backup maps each source type to the correct snapshot/backup tier."""

    def test_ebs_backup_maps_to_pd_snapshot(self):
        mapped, llm = _map("backup_ebs", "APS3-WarmStorage-ByteHrs-EBS")
        assert len(mapped) == 1
        m = mapped[0]
        assert m["gcp_service"] == "Compute Engine"
        assert m["gcp_sku_name"] == "Storage PD Snapshot"
        assert m["strategy"] == "map"
        assert m["gcp_sku_id"] is not None

    def test_cold_storage_maps_to_gcs_archive(self):
        mapped, llm = _map("backup_cold", "APS3-ColdStorage-ByteHrs-EBS")
        assert len(mapped) == 1
        m = mapped[0]
        assert m["gcp_service"] == "Cloud Storage"
        assert m["gcp_sku_name"] == "Archive Storage"
        assert m["strategy"] == "map"
        assert m["gcp_sku_id"] is not None

    def test_rds_aurora_backup_maps_to_cloud_sql_backups(self):
        mapped, llm = _map("backup_db", "APS3-WarmStorage-ByteHrs-Aurora")
        assert len(mapped) == 1
        m = mapped[0]
        assert m["gcp_service"] == "Cloud SQL"
        assert m["gcp_sku_name"] == "Cloud SQL: Backups"
        assert m["strategy"] == "map"
        assert m["gcp_sku_id"] is not None

    def test_neptune_backup_maps_to_gcs_nearline_not_cloud_sql(self):
        # Neptune is a graph DB, not relational (WORKORDER_followup_fixes A2)
        mapped, llm = _map("backup_db", "APS3-WarmStorage-ByteHrs-Neptune")
        assert len(mapped) == 1
        m = mapped[0]
        assert m["gcp_service"] == "Cloud Storage"
        assert m["gcp_sku_name"] == "Nearline Storage"
        assert "graph DB" in m["projection_note"]

    def test_efs_backup_maps_to_gcs_nearline(self):
        mapped, llm = _map("backup_efs", "APS3-WarmStorage-ByteHrs-EFS")
        assert len(mapped) == 1
        m = mapped[0]
        assert m["gcp_service"] == "Cloud Storage"
        assert m["gcp_sku_name"] == "Nearline Storage"
        assert m["strategy"] == "map"
        assert m["gcp_sku_id"] is not None

    def test_dynamodb_backup_maps_to_gcs_nearline(self):
        mapped, llm = _map("backup_dynamodb", "APS3-WarmStorage-ByteHrs-DynamoDB")
        assert len(mapped) == 1
        m = mapped[0]
        assert m["gcp_service"] == "Cloud Storage"
        assert m["gcp_sku_name"] == "Nearline Storage"
        assert m["strategy"] == "map"

    def test_s3_backup_maps_to_gcs_nearline(self):
        mapped, llm = _map("backup_s3", "APS3-WarmStorage-ByteHrs-S3")
        assert len(mapped) == 1
        m = mapped[0]
        assert m["gcp_service"] == "Cloud Storage"
        assert m["gcp_sku_name"] == "Nearline Storage"
        assert m["strategy"] == "map"

    def test_restore_maps_to_data_retrieval(self):
        mapped, llm = _map("backup_restore", "APS3-Restore-Bytes", unit="GB")
        assert len(mapped) == 1
        m = mapped[0]
        assert m["gcp_service"] == "Cloud Storage"
        assert m["gcp_sku_name"] == "Data Retrieval"
        assert m["strategy"] == "map"

    def test_copy_maps_to_inter_region_egress(self):
        mapped, llm = _map("backup_copy", "APS3-CrossRegion-Bytes", unit="GB")
        assert len(mapped) == 1
        m = mapped[0]
        assert m["gcp_service"] == "Compute Engine"
        assert m["gcp_sku_name"] == "Inter-region"
        assert m["strategy"] == "map"

    def test_audit_evaluations_is_passthrough(self):
        mapped, llm = _map("backup_audit", "BackupAudit-Evaluations", unit="Count")
        assert len(mapped) == 1
        m = mapped[0]
        assert m["strategy"] == "passthrough"
        assert "Audit Manager" in m["projection_note"]

    def test_search_is_passthrough(self):
        mapped, llm = _map("backup_search", "BackupSearch-ItemCount", unit="Count")
        assert len(mapped) == 1
        m = mapped[0]
        assert m["strategy"] == "passthrough"
        assert "Search" in m["projection_note"]

    def test_malware_is_passthrough(self):
        mapped, llm = _map("backup_malware", "Backup-Malware-ScanBytes", unit="GB")
        assert len(mapped) == 1
        m = mapped[0]
        assert m["strategy"] == "passthrough"
        assert "Malware Protection" in m["projection_note"]

    def test_unknown_source_warm_storage_defaults_to_snapshot(self):
        mapped, llm = _map("backup_storage_warm", "APS3-BackupUsage")
        assert len(mapped) == 1
        m = mapped[0]
        assert m["gcp_service"] == "Compute Engine"
        assert m["gcp_sku_name"] == "Storage PD Snapshot"
        assert m["strategy"] == "map"


# ─── CORE INVARIANT TEST ─────────────────────────────────────────────────────

class TestNeverLiveHyperdiskCapacity:
    """The #1 rule: an AWS Backup row must NEVER be priced as live Hyperdisk/PD capacity."""

    @pytest.mark.parametrize("usage_type,charge_type_id", [
        ("APS3-BackupUsage", "backup_storage_warm"),
        ("APS3-BackupStorage", "backup_storage_warm"),
        ("APS3-WarmStorage-ByteHrs", "backup_storage_warm"),
        ("APS3-WarmStorage-ByteHrs-EBS", "backup_ebs"),
        ("APS3-WarmStorage-ByteHrs-EC2", "backup_storage_warm"),
        ("APS3-ColdStorage-ByteHrs", "backup_cold"),
        ("APS3-ColdStorage-ByteHrs-EBS", "backup_cold"),
        ("APS3-WarmStorage-ByteHrs-RDS", "backup_db"),
        ("APS3-WarmStorage-ByteHrs-Aurora", "backup_db"),
        ("APS3-WarmStorage-ByteHrs-EFS", "backup_efs"),
        ("APS3-WarmStorage-ByteHrs-DynamoDB", "backup_dynamodb"),
        ("APS3-WarmStorage-ByteHrs-S3", "backup_s3"),
    ])
    def test_backup_rows_never_map_to_live_disk_capacity(self, usage_type, charge_type_id):
        mapped, llm = _map(charge_type_id, usage_type)
        assert len(mapped) >= 1
        for m in mapped:
            sku_name = m.get("gcp_sku_name") or ""
            # Must NOT be live disk capacity (Hyperdisk Balanced, Standard PD Capacity, SSD PD Capacity)
            assert "Hyperdisk Balanced" not in sku_name or "Snapshot" in sku_name, (
                f"AWS Backup row {usage_type} mis-mapped to live Hyperdisk capacity: {m}"
            )
            assert "Balanced PD Capacity" not in sku_name, (
                f"AWS Backup row {usage_type} mis-mapped to live PD capacity: {m}"
            )
            assert "Standard PD Capacity" not in sku_name, (
                f"AWS Backup row {usage_type} mis-mapped to live Standard PD capacity: {m}"
            )
            assert "SSD PD Capacity" not in sku_name, (
                f"AWS Backup row {usage_type} mis-mapped to live SSD PD capacity: {m}"
            )
