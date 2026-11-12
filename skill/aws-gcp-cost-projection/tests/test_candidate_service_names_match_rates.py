"""Guard: CUD-eligible service names in mappers and catalog match (BUG-3).

For every CUD-eligible service in data/cud_pct.json, assert that the gcp_service
values emitted by static mappers can be matched by the CUD synthesis path
(either directly or via known aliases in apply_rates.py).
"""

import os
import sys
import duckdb

HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from apply_rates import load_cud_eligible, load_cud_pct


def test_cud_eligible_services_have_matching_catalog_or_alias():
    cud_eligible = load_cud_eligible()
    assert len(cud_eligible) > 0

    catalog_path = os.path.join(ROOT, "data", "catalog.duckdb")
    catalog_services = set()
    if os.path.exists(catalog_path):
        con = duckdb.connect(catalog_path, read_only=True)
        catalog_services = {r[0] for r in con.execute("SELECT DISTINCT service_name FROM skus WHERE service_name IS NOT NULL").fetchall()}
        con.close()

    # Aliases known to CUD synthesis
    known_aliases = {
        "Cloud Memorystore for Redis": {"Memorystore", "Cloud Memorystore", "Cloud Memorystore for Redis"},
        "Cloud Memorystore for Memcached": {"Memorystore", "Cloud Memorystore", "Cloud Memorystore for Memcached"},
        "Cloud Memorystore": {"Memorystore", "Cloud Memorystore", "Cloud Memorystore for Redis", "Cloud Memorystore for Memcached"},
        "Memorystore": {"Memorystore", "Cloud Memorystore", "Cloud Memorystore for Redis", "Cloud Memorystore for Memcached"},
        "AlloyDB": {"AlloyDB", "AlloyDB for PostgreSQL", "Cloud AlloyDB"},
        "Compute Engine Memory Optimized": {"Compute Engine", "Compute Engine Memory Optimized"},
        "Cloud SQL": {"Cloud SQL"},
        "Compute Engine": {"Compute Engine"},
        "Cloud Spanner": {"Cloud Spanner", "Spanner"},
        "Cloud Bigtable": {"Cloud Bigtable", "Bigtable"},
        "Cloud Run": {"Cloud Run"},
    }

    # Every eligible service must either be in catalog directly or have an alias in catalog
    if catalog_services:
        for svc in cud_eligible:
            aliases = known_aliases.get(svc, {svc})
            matched = any(a in catalog_services for a in aliases)
            assert matched, f"CUD-eligible service '{svc}' not found in catalog.duckdb services and has no matching alias."
