#!/usr/bin/env python3
import duckdb
import os
import sys
import glob
import json
import re
import hashlib

JOB_DIR = os.getcwd()
DB_PATH = os.path.join(JOB_DIR, "projection-audit", "projection.duckdb")
DATA_DIR = os.path.join(os.environ.get("SKILL_DIR", ""), "data")

REGION_MAP = {
    "us-east-1": "us-east4",
    "us-east-2": "us-east4",
    "us-west-1": "us-west2",
    "us-west-2": "us-west1",
    "ca-central-1": "northamerica-northeast1",
    "ca-west-1": "northamerica-northeast2",
    "sa-east-1": "southamerica-east1",
    "eu-west-1": "europe-west1",
    "eu-west-2": "europe-west2",
    "eu-west-3": "europe-west9",
    "eu-central-1": "europe-west3",
    "eu-central-2": "europe-west4",
    "eu-north-1": "europe-north1",
    "eu-south-1": "europe-west8",
    "eu-south-2": "europe-southwest1",
    "ap-east-1": "asia-east2",
    "ap-southeast-1": "asia-southeast1",
    "ap-southeast-2": "australia-southeast1",
    "ap-southeast-3": "asia-southeast2",
    "ap-southeast-4": "australia-southeast2",
    "ap-south-1": "asia-south1",
    "ap-south-2": "asia-south2",
    "ap-northeast-1": "asia-northeast1",
    "ap-northeast-2": "asia-northeast3",
    "ap-northeast-3": "asia-northeast2",
    "me-central-1": "me-central1",
    "me-south-1": "me-west1",
    "af-south-1": "africa-south1",
    "il-central-1": "me-central2",
    "mx-central-1": "northamerica-south1",
    "us-gov-east-1": "us-east4",
    "us-gov-west-1": "us-west1"
}

DESCRIPTIVE_REGION_MAP = {
    "asia pacific (singapore)": "asia-southeast1",
    "asia pacific (tokyo)": "asia-northeast1",
    "asia pacific (mumbai)": "asia-south1",
    "us east (n. virginia)": "us-east4",
    "us east (ohio)": "us-east4",
    "us west (n. california)": "us-west2",
    "us west (oregon)": "us-west1",
    "europe (ireland)": "europe-west1",
    "europe (london)": "europe-west2",
    "europe (paris)": "europe-west9",
    "europe (frankfurt)": "europe-west3",
    "europe (stockholm)": "europe-north1",
    "global": "global"
}


def _fail(msg):
    """Write a clean structural-failure reason and stop (watcher surfaces it)."""
    with open(os.path.join(JOB_DIR, "failure.txt"), "w") as f:
        f.write(msg)
    print("INGEST FAILURE: " + msg)
    sys.exit(0)


def _load_excel(conn, path):
    """Excel → aws_raw via the DuckDB excel extension (read_xlsx), then spatial
    st_read as a fallback."""
    for setup, query in (
        ("INSTALL excel; LOAD excel;", f"SELECT * FROM read_xlsx('{path}', all_varchar=true)"),
        ("INSTALL spatial; LOAD spatial;", f"SELECT * FROM st_read('{path}')"),
    ):
        try:
            conn.execute(setup)
            conn.execute(f"CREATE TABLE aws_raw AS {query}")
            return
        except Exception:
            conn.execute("DROP TABLE IF EXISTS aws_raw")
    _fail("Could not read the Excel file. Re-export the bill as CSV or Parquet and upload that.")


# AWS region display-name fragments — used to tell a REGION sub-header apart from
# a SERVICE sub-header in the flat text of an AWS estimated-bill PDF.
_PDF_REGION_RE = re.compile(
    r'^(asia pacific|us east|us west|eu |europe|canada|south america|middle east|'
    r'africa|global|israel|sa[- ]east|ap[- ]|us[- ])', re.I)
# A line-item row: "<description> <qty> <unit> USD <amount>". The unit keeps it
# distinct from service/region subtotal lines ("<name> USD <amount>").
_PDF_ITEM_RE = re.compile(
    r'^(.*?)\s+([\d,]+(?:\.\d+)?)\s+([A-Za-z][A-Za-z0-9\-/]*)\s+USD\s+([\d,]+(?:\.\d+)?)\s*$')
# A subtotal/header row: "<name> USD <amount>" with no usage qty/unit.
_PDF_HDR_RE = re.compile(r'^(.+?)\s+USD\s+[\d,]+(?:\.\d+)?\s*$')


def _load_pdf(conn, path):
    """AWS estimated-bill PDF → aws_raw in the simplified-bill schema
    (Service, Region, Custom Usage Type, Description, Usage Quantity, Cost).

    Parses text lines (pdfplumber table detection fails on these border-less
    PDFs), tracking the current Service / Region sub-headers and attaching them
    to each line item. NOTE: amounts are GROSS (pre Savings-Plan/RI discount) and
    a PDF reconciles less precisely than a CSV/Parquet CUR — good for a
    directional projection, but CUR is preferred for the exact figure."""
    try:
        import pdfplumber
    except Exception:
        _fail("This is a PDF but the PDF text-extraction library isn't installed. "
              "Export the AWS Cost & Usage Report as CSV or Parquet and upload that instead.")
        return
    import csv as _csv

    items = []            # (service, region, description, qty, cost)
    cur_service, cur_region = "", ""
    try:
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                for raw in (page.extract_text() or "").split("\n"):
                    ln = raw.strip()
                    if not ln:
                        continue
                    m = _PDF_ITEM_RE.match(ln)
                    if m:
                        desc, qty, unit, amt = m.groups()
                        # Skip Savings-Plan/RI "covered by" lines (parenthesized
                        # amount) — they double-count usage already charged.
                        if "covered by" in desc.lower() or desc.rstrip().endswith("("):
                            continue
                        items.append((cur_service, cur_region, "",
                                      f"{desc.strip()} ({qty} {unit})",
                                      qty.replace(",", ""), amt.replace(",", "")))
                        continue
                    h = _PDF_HDR_RE.match(ln)
                    if h:
                        name = h.group(1).strip()
                        if _PDF_REGION_RE.match(name):
                            cur_region = name
                        elif len(name) > 3 and "total" not in name.lower():
                            cur_service = name
    except Exception as e:
        _fail(f"Could not parse the PDF ({e}). Export the CUR as CSV/Parquet instead.")
        return

    if len(items) < 5:
        _fail("Couldn't extract line items from this PDF (it may be summary-only). "
              "Export the AWS Cost & Usage Report as CSV or Parquet for an accurate projection.")
        return

    tmp_csv = os.path.join(JOB_DIR, "input_from_pdf.csv")
    with open(tmp_csv, "w", newline="", encoding="utf-8") as fh:
        w = _csv.writer(fh)
        w.writerow(["Service", "Region", "Custom Usage Type", "Description", "Usage Quantity", "Cost ($)"])
        w.writerows(items)
    print(f"PDF: extracted {len(items)} line items → {tmp_csv}")
    conn.execute(f"CREATE TABLE aws_raw AS SELECT * FROM read_csv_auto('{tmp_csv}', ALL_VARCHAR=TRUE, header=TRUE)")


def _read_into_aws_raw(conn, path):
    """Load one data file into aws_raw, dispatching by extension. DuckDB's
    read_csv_auto transparently handles .gz / .zst and delimiter detection."""
    p = path.lower()
    if p.endswith(".parquet"):
        conn.execute(f"CREATE TABLE aws_raw AS SELECT * FROM read_parquet('{path}')")
    elif p.endswith((".json", ".jsonl", ".ndjson")):
        conn.execute(f"CREATE TABLE aws_raw AS SELECT * FROM read_json_auto('{path}')")
    elif p.endswith((".xlsx", ".xlsm", ".xls")):
        _load_excel(conn, path)
    elif p.endswith(".pdf"):
        _load_pdf(conn, path)
    else:  # .csv .tsv .txt and .gz/.zst variants of them
        conn.execute(f"CREATE TABLE aws_raw AS SELECT * FROM read_csv_auto('{path}', ALL_VARCHAR=TRUE)")


def load_raw(conn, input_file):
    """Format-aware entry point. Handles CSV/TSV (+gzip/zstd), Parquet, JSON,
    Excel, PDF, and ZIP archives containing any of those."""
    if input_file.lower().endswith(".zip"):
        import zipfile, tempfile
        dest = tempfile.mkdtemp(prefix="cur_zip_")
        try:
            with zipfile.ZipFile(input_file) as z:
                z.extractall(dest)
        except Exception as e:
            _fail(f"Could not open the ZIP archive ({e}).")
            return
        DATA_EXT = (".parquet", ".csv", ".tsv", ".txt", ".json", ".jsonl",
                    ".ndjson", ".gz", ".zst", ".xlsx", ".xls")
        cands = []
        for root, _dirs, files in os.walk(dest):
            for fn in files:
                if fn.startswith(".") or fn.startswith("__MACOSX"):
                    continue
                if fn.lower().endswith(DATA_EXT):
                    fp = os.path.join(root, fn)
                    cands.append((os.path.getsize(fp), fp))
        if not cands:
            _fail("The ZIP archive contains no recognizable data file (CSV / Parquet / JSON / Excel).")
            return
        # Largest data file is the bill; manifests/metadata are small.
        cands.sort(reverse=True)
        _read_into_aws_raw(conn, cands[0][1])
    else:
        _read_into_aws_raw(conn, input_file)


def main():
    if not os.path.exists(os.path.dirname(DB_PATH)):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    conn = duckdb.connect(DB_PATH)
    
    # 1. Inspect input
    inputs = glob.glob(os.path.join(JOB_DIR, "input.*"))
    if not inputs:
        with open(os.path.join(JOB_DIR, "failure.txt"), "w") as f:
            f.write("No input file found.")
        sys.exit(0)
    input_file = inputs[0]

    # 2. Load aws_raw — format-aware loader (CSV/TSV, gzip/zstd, Parquet, JSON,
    #    Excel, PDF, and ZIP archives of any of those).
    conn.execute("DROP TABLE IF EXISTS aws_raw")
    load_raw(conn, input_file)
        
    cols = [c[1] for c in conn.execute("PRAGMA table_info(aws_raw)").fetchall()]
    
    is_raw_cur = "lineItem/LineItemType" in cols or "LineItemType" in cols
    
    # Normalize col names to make queries easier
    col_map = {}
    for c in cols:
        col_map[c.lower().replace("/", "_").replace(" ", "_")] = c
        
    # Helper to find exact col name
    def c(names):
        for n in names:
            if n.lower() in col_map:
                return f'"{col_map[n.lower()]}"'
        return "NULL"
        
    # 3. Create schema tables
    # (Schema is defined in reference/schemas.md but we just create aws_li_catalog here)
    conn.execute("DROP TABLE IF EXISTS aws_li_catalog")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS aws_li_catalog (
            aws_li_key VARCHAR PRIMARY KEY,
            product VARCHAR,
            usage_type VARCHAR,
            operation VARCHAR,
            aws_region VARCHAR,
            gcp_region VARCHAR,
            pricing_model VARCHAR,
            line_item_type VARCHAR,
            is_workload BOOLEAN,
            total_usage DOUBLE,
            aws_amortized_cost DOUBLE,
            projection_note VARCHAR,
            instance_type VARCHAR,
            instance_vcpus INTEGER,
            instance_ram_gb DOUBLE,
            instance_arch VARCHAR,
            instance_count DOUBLE,
            billing_days INTEGER,
            aws_effective_unit_rate DOUBLE,
            license_model VARCHAR,
            operating_system VARCHAR,
            database_engine VARCHAR,
            deployment_option VARCHAR,
            volume_type VARCHAR,
            pricing_unit VARCHAR,
            workload_class VARCHAR
        )
    """)

    # Reconcile sum helper
    if is_raw_cur:
        cost_col = c(['lineitem/unblendedcost', 'unblendedcost'])
        if cost_col == "NULL": cost_col = c(['lineitem/blendedcost'])
    else:
        cost_col = c(['cost_($)', 'cost'])

    # Build SQL to group and classify
    if is_raw_cur:
        sql = f"""
            INSERT INTO aws_li_catalog (
                aws_li_key, product, usage_type, operation, aws_region, gcp_region, 
                pricing_model, line_item_type, is_workload, total_usage, aws_amortized_cost,
                license_model, operating_system, database_engine, deployment_option, volume_type, pricing_unit
            )
            SELECT 
                md5(COALESCE({c(['lineitem/productcode', 'productcode'])}, '') || COALESCE({c(['lineitem/usagetype', 'usagetype'])}, '') || COALESCE({c(['lineitem/operation', 'operation'])}, '') || COALESCE({c(['product/region', 'region'])}, '') || COALESCE({c(['pricing/term', 'term'])}, '') || COALESCE({c(['lineitem/lineitemtype', 'lineitemtype'])}, '') || CAST(
                    CASE 
                        WHEN {c(['lineitem/lineitemtype', 'lineitemtype'])} IN ('Tax','RIFee','SavingsPlanUpfrontFee','SavingsPlanRecurringFee','SavingsPlanNegation','SavingsPlanCoveredUsage','Refund','Credit','EdpDiscount','PrivateRateDiscount','BundledDiscount') THEN FALSE
                        WHEN {c(['lineitem/lineitemtype', 'lineitemtype'])} IN ('Usage','DiscountedUsage') THEN TRUE
                        ELSE FALSE
                    END AS BOOLEAN)
                ) as key,
                COALESCE({c(['lineitem/productcode', 'productcode'])}, '') as product,
                COALESCE({c(['lineitem/usagetype', 'usagetype'])}, '') as usage_type,
                COALESCE({c(['lineitem/operation', 'operation'])}, '') as operation,
                COALESCE({c(['product/region', 'region'])}, '') as aws_region,
                NULL as gcp_region, -- Will map later
                COALESCE({c(['pricing/term', 'term'])}, 'OnDemand') as pricing_model,
                COALESCE({c(['lineitem/lineitemtype', 'lineitemtype'])}, '') as line_item_type,
                CASE 
                    WHEN {c(['lineitem/lineitemtype', 'lineitemtype'])} IN ('Tax','RIFee','SavingsPlanUpfrontFee','SavingsPlanRecurringFee','SavingsPlanNegation','SavingsPlanCoveredUsage','Refund','Credit','EdpDiscount','PrivateRateDiscount','BundledDiscount') THEN FALSE
                    WHEN {c(['lineitem/lineitemtype', 'lineitemtype'])} IN ('Usage','DiscountedUsage') THEN TRUE
                    ELSE FALSE
                END as is_workload,
                SUM(CAST(COALESCE({c(['lineitem/usageamount', 'usageamount'])}, '0') AS DOUBLE)) as total_usage,
                SUM(CAST(COALESCE({cost_col}, '0') AS DOUBLE)) as aws_amortized_cost,
                COALESCE({c(['product/licensemodel', 'licensemodel'])}, '') as license_model,
                COALESCE({c(['product/operatingsystem', 'operatingsystem'])}, '') as operating_system,
                COALESCE({c(['product/databaseengine', 'databaseengine'])}, '') as database_engine,
                COALESCE({c(['product/deploymentoption', 'deploymentoption'])}, '') as deployment_option,
                COALESCE({c(['product/volumetype', 'volumetype'])}, '') as volume_type,
                COALESCE({c(['pricing/unit', 'unit'])}, '') as pricing_unit
            FROM aws_raw
            WHERE {c(['lineitem/lineitemtype', 'lineitemtype'])} != 'Tax'
            GROUP BY product, usage_type, operation, aws_region, pricing_model, line_item_type, is_workload,
                     license_model, operating_system, database_engine, deployment_option, volume_type, pricing_unit
        """
    else:
        sql = f"""
            INSERT INTO aws_li_catalog (aws_li_key, product, usage_type, operation, aws_region, gcp_region, pricing_model, line_item_type, is_workload, total_usage, aws_amortized_cost, projection_note)
            SELECT 
                md5(COALESCE({c(['service'])}, '') || COALESCE({c(['custom_usage_type'])}, '') || COALESCE({c(['description'])}, '') || COALESCE({c(['region'])}, '') || 
                    CASE WHEN {c(['description'])} ILIKE '%reserved instance applied%' THEN 'Committed' ELSE 'OnDemand' END || 
                    CASE WHEN {c(['description'])} ILIKE '%reserved instance applied%' THEN 'DiscountedUsage' ELSE 'Usage' END || 
                    CAST(
                        CASE 
                            WHEN {c(['description'])} ILIKE '%covered by Compute Savings Plans%' OR {c(['description'])} ILIKE '%covered by EC2 Instance Savings Plans%' OR {c(['description'])} ILIKE '%covered by Reserved Instances%' OR {c(['description'])} ILIKE '%committed%upfront%' OR {c(['description'])} ILIKE '%No upfront fee%' OR {c(['description'])} ILIKE '%Recurring monthly fee%' OR {c(['description'])} ILIKE '%EDP Discount%' OR {c(['description'])} ILIKE '%Private Pricing Discount%' OR {c(['description'])} ILIKE '%Refund%' OR {c(['description'])} ILIKE '%Credit%' THEN FALSE
                            ELSE TRUE
                        END AS BOOLEAN
                    )
                ) as key,
                COALESCE({c(['service'])}, '') as product,
                COALESCE({c(['custom_usage_type'])}, '') as usage_type,
                COALESCE({c(['description'])}, '') as operation,
                COALESCE({c(['region'])}, '') as aws_region,
                NULL as gcp_region, -- Will map later
                CASE WHEN {c(['description'])} ILIKE '%reserved instance applied%' THEN 'Committed' ELSE 'OnDemand' END as pricing_model,
                CASE WHEN {c(['description'])} ILIKE '%reserved instance applied%' THEN 'DiscountedUsage' ELSE 'Usage' END as line_item_type,
                CASE 
                    WHEN {c(['description'])} ILIKE '%covered by Compute Savings Plans%' OR {c(['description'])} ILIKE '%covered by EC2 Instance Savings Plans%' OR {c(['description'])} ILIKE '%covered by Reserved Instances%' OR {c(['description'])} ILIKE '%committed%upfront%' OR {c(['description'])} ILIKE '%No upfront fee%' OR {c(['description'])} ILIKE '%Recurring monthly fee%' OR {c(['description'])} ILIKE '%EDP Discount%' OR {c(['description'])} ILIKE '%Private Pricing Discount%' OR {c(['description'])} ILIKE '%Refund%' OR {c(['description'])} ILIKE '%Credit%' THEN FALSE
                    ELSE TRUE
                END as is_workload,
                SUM(CAST(REPLACE(COALESCE({c(['usage_quantity'])}, '0'), ',', '') AS DOUBLE)) as total_usage,
                SUM(CAST(REPLACE(COALESCE({cost_col}, '0'), ',', '') AS DOUBLE)) as aws_amortized_cost,
                CASE WHEN {c(['description'])} ILIKE '%reserved instance applied%' THEN 'AWS rate is RI-amortized; compare GCP CUD, not OD' ELSE NULL END as projection_note
            FROM aws_raw
            WHERE {c(['service'])} != 'Tax' AND {c(['service'])} NOT ILIKE '%Tax%' AND {c(['description'])} NOT ILIKE '%Tax%' AND COALESCE({c(['service'])}, '') != ''
            GROUP BY product, usage_type, operation, aws_region, pricing_model, line_item_type, is_workload, projection_note
        """
        
    conn.execute(sql)
    
    # 5. Map Regions
    catalog_rows = conn.execute("SELECT aws_li_key, aws_region FROM aws_li_catalog").fetchall()
    for row in catalog_rows:
        key = row[0]
        aws_r = row[1]
        # Unknown regions fall back to 'global' so the gcp_projection VIEW's
        # COALESCE(regional_rate, global_rate) always finds a rate.
        aws_r_clean = aws_r.strip().lower() if aws_r else ""
        gcp_r = REGION_MAP.get(aws_r_clean) or DESCRIPTIVE_REGION_MAP.get(aws_r_clean, "global")
        conn.execute("UPDATE aws_li_catalog SET gcp_region = ? WHERE aws_li_key = ?", (gcp_r, key))
            
    # 6. Reconcile - let orchestrate.go verification gate handle failure, but check
    # In raw CUR, sometimes totals have slight float differences, we'll ignore for script unless it's huge
    
    # 7. Enrich instances
    ec2_path = os.path.join(DATA_DIR, "ec2-instance-types.json")
    rds_path = os.path.join(DATA_DIR, "rds-instance-types.json")
    
    ec2_table = {}
    if os.path.exists(ec2_path):
        with open(ec2_path) as f: ec2_table = json.load(f)
        
    rds_table = {}
    if os.path.exists(rds_path):
        with open(rds_path) as f: rds_table = json.load(f)
        
    def infer_billing_days(rows):
        candidates = [r[1] for r in rows if r[1] and 670 <= r[1] <= 745]
        if candidates:
            return round(max(candidates) / 24)
        return 30
        
    all_cat = conn.execute("""
        SELECT aws_li_key, total_usage, product, operation, aws_amortized_cost, usage_type,
               license_model, operating_system, database_engine, deployment_option, volume_type, pricing_unit
        FROM aws_li_catalog
    """).fetchall()
    b_days = infer_billing_days(all_cat)
    
    for r in all_cat:
        key, total_usage, product, operation, aws_cost, usage_type, lic, os_name, db_eng, deploy_opt, vol_t, p_unit = r
        itype = None
        op = operation or ""
        ut = usage_type or ""
        
        # 1. Infer OS
        if not os_name:
            if "windows" in op.lower() or "windows" in ut.lower():
                os_name = "Windows"
            elif "rhel" in op.lower() or "rhel" in ut.lower():
                os_name = "RHEL"
            elif "suse" in op.lower() or "suse" in ut.lower():
                os_name = "SUSE"
            else:
                os_name = "Linux"
                
        # 2. Infer DB Engine
        if not db_eng:
            if "postgres" in op.lower() or "postgres" in ut.lower():
                db_eng = "PostgreSQL"
            elif "mysql" in op.lower() or "mysql" in ut.lower():
                db_eng = "MySQL"
            elif "oracle" in op.lower() or "oracle" in ut.lower():
                db_eng = "Oracle"
            elif "sql server" in op.lower() or "sql server" in ut.lower() or "sqlserver" in op.lower() or "sqlserver" in ut.lower():
                db_eng = "SQL Server"
            elif "mariadb" in op.lower() or "mariadb" in ut.lower():
                db_eng = "MariaDB"
                
        # 3. Infer Deployment Option (Multi-AZ)
        if not deploy_opt:
            if "multi-az" in op.lower() or "multiaz" in op.lower() or "multi-az" in ut.lower() or "multiaz" in ut.lower():
                deploy_opt = "Multi-AZ"
            else:
                deploy_opt = "Single-AZ"
                
        # 4. Infer License Model
        if not lic:
            if "byol" in op.lower() or "byol" in ut.lower() or "bring your own license" in op.lower() or "bring your own license" in ut.lower() or "customer-provided" in op.lower():
                lic = "Bring Your Own License"
            else:
                lic = "License Included"
                
        # 5. Infer Volume Type
        if not vol_t:
            for vt in ["gp2", "gp3", "io1", "io2", "st1", "sc1", "standard"]:
                if vt in op.lower() or vt in ut.lower():
                    vol_t = vt
                    break
                    
        # 6. Infer Pricing Unit
        if not p_unit:
            if "hour" in op.lower() or "hour" in ut.lower() or "boxusage" in ut.lower() or "instancehour" in ut.lower():
                p_unit = "Hrs"
            elif "gb" in op.lower() or "gb" in ut.lower() or "byte" in op.lower() or "byte" in ut.lower() or "storage" in op.lower():
                p_unit = "GB-Mo"
                
        # Update inferred info back to database
        conn.execute("""
            UPDATE aws_li_catalog
            SET license_model = ?, operating_system = ?, database_engine = ?,
                deployment_option = ?, volume_type = ?, pricing_unit = ?
            WHERE aws_li_key = ?
        """, (lic, os_name, db_eng, deploy_opt, vol_t, p_unit, key))
        LEGACY_SPECS = {
            "c3.2xlarge": { "vcpus": 8, "ram_gb": 15.0, "arch": "x86_64" },
            "c4.xlarge": { "vcpus": 4, "ram_gb": 7.5, "arch": "x86_64" },
            "c4.2xlarge": { "vcpus": 8, "ram_gb": 15.0, "arch": "x86_64" },
            "c4.8xlarge": { "vcpus": 36, "ram_gb": 60.0, "arch": "x86_64" },
            "a1.2xlarge": { "vcpus": 8, "ram_gb": 16.0, "arch": "arm64" },
            "c7g.2xlarge.search": { "vcpus": 8, "ram_gb": 16.0, "arch": "arm64" },
            "kafka.t3.small": { "vcpus": 2, "ram_gb": 2.0, "arch": "x86_64" },
            "db.t4g.xlarge": { "vcpus": 4, "ram_gb": 16.0, "arch": "arm64" }
        }
        
        if "RDS" in product or "Aurora" in product or "Relational Database" in product:
            m = re.search(r'(db\.[a-z0-9]+\.[a-z0-9]+)', op, re.IGNORECASE)
            if m: itype = m.group(1).lower()
        elif "ElastiCache" in product:
            m = re.search(r'(cache\.[a-z0-9]+\.[a-z0-9]+)', op, re.IGNORECASE)
            if m:
                itype = m.group(1).lower()
            else:
                m2 = re.search(r'([A-Z][0-9][A-Za-z0-9]*)\s+(Micro|Small|Medium|Large|XLarge|[0-9]+XLarge)\s+Cache',
                               op, re.IGNORECASE)
                if m2:
                    fam  = m2.group(1).lower()
                    size = m2.group(2).lower()
                    itype = f"cache.{fam}.{size}"
        elif "OpenSearch" in product or "Elasticsearch" in product:
            m = re.search(r'([a-z0-9]+\.[a-z0-9]+\.search)', op, re.IGNORECASE)
            if m: itype = m.group(1).lower()
        elif "Managed Streaming for Apache Kafka" in product or "MSK" in product:
            m = re.search(r'(kafka\.[a-z0-9]+\.[a-z0-9]+)', op, re.IGNORECASE)
            if m: itype = m.group(1).lower()
        elif "Elastic Compute Cloud" in product or "EC2" in product:
            m = re.search(r'(\S+)\s+Instance\s+Hour', op)
            if m:
                itype = m.group(1)
            else:
                m = re.search(r'(?:BoxUsage|SpotUsage):(\S+)', op)
                if m: itype = m.group(1)
                
        if not itype: continue

        # Managed services (OpenSearch/MSK) decorate a standard EC2 type with a
        # service suffix/prefix — "c7g.2xlarge.search", "kafka.t3.small". Strip
        # the decoration to the BASE EC2 type so the full 357-entry ec2 table
        # resolves specs for ANY instance, not just a hardcoded few.
        base_itype = itype
        if base_itype.endswith(".search"):
            base_itype = base_itype[: -len(".search")]
        if base_itype.startswith("kafka."):
            base_itype = base_itype[len("kafka.") :]

        table = rds_table if base_itype.startswith(("db.", "cache.")) else ec2_table
        spec = table.get(base_itype) or ec2_table.get(base_itype)
        if not spec:
            # Fall back to legacy static spec lookup (decorated or base name)
            spec = LEGACY_SPECS.get(itype) or LEGACY_SPECS.get(base_itype)
            
        if not spec:
            conn.execute("UPDATE aws_li_catalog SET instance_type = ? WHERE aws_li_key = ?", (itype, key))
            continue
            
        # Classify workload
        it = itype.lower()
        ram = spec.get("ram_gb", 0)
        w_class = "General-Purpose"
        
        if ram > 1536 or it.startswith(("u-", "hpc-")):
            w_class = "Outlier"
        elif it.startswith(("g", "p")) and not it.startswith(("gd", "pd", "gp", "gl")):
            if len(it) > 1 and it[1].isdigit():
                w_class = "GPU"
        elif it.startswith("t"):
            w_class = "Burstable"
        elif spec.get("arch") == "arm64" or "graviton" in it or (len(it) > 2 and it[2] == 'g'):
            w_class = "ARM"
        elif it.startswith(("r", "x", "z")):
            w_class = "Memory-Optimized"
        elif it.startswith("c"):
            w_class = "Compute-Optimized"
            
        instance_count = round(total_usage / (b_days * 24), 4) if b_days else None
        rate = (aws_cost / total_usage) if (total_usage and total_usage > 0) else None
        
        conn.execute("""
            UPDATE aws_li_catalog 
            SET instance_type = ?, instance_vcpus = ?, instance_ram_gb = ?, instance_arch = ?,
                billing_days = ?, instance_count = ?, aws_effective_unit_rate = ?, workload_class = ?
            WHERE aws_li_key = ?
        """, (itype, spec.get("vcpus"), spec.get("ram_gb"), spec.get("arch"), b_days, instance_count, rate, w_class, key))

if __name__ == "__main__":
    main()
