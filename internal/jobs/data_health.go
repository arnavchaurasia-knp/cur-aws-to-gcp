package jobs

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/facets/cur-web/internal/skill"
)

// storeInfo is the human description of a data file shown in System Health →
// Data Files. Wording comes from each file's own _meta block — keep them in
// sync when a file's purpose changes.
type storeInfo struct {
	Title       string
	Description string
}

// storeManifest describes every monitored data file. To monitor a new file:
// add a check in CheckDataStores, an entry here, and (if admins may edit it)
// an entry in editableDatastores (datastore_handlers.go).
var storeManifest = map[string]storeInfo{
	"catalog_artifact": {"Pricing catalog database",
		"The compiled GCP price list every projection reads (catalog.duckdb). Rebuilt by 'Refresh catalog' — never edit it by hand."},
	"catalog_source": {"Pricing catalog downloads",
		"Raw per-service price files downloaded from Google (skus/*.json.gz). The catalog database is built from these."},
	"service_map": {"AWS → GCP service map",
		"Decides which GCP service each AWS service maps to, and whether it is priced, passed through at AWS cost, or ignored. Rules are checked top to bottom and the FIRST match wins, so keep specific rules above general ones."},
	"service_specs": {"Service charge-type specs",
		"For each AWS service, the kinds of charges it has (instance hours, storage, requests…) and how each is recognised and priced. Charge types are tested in order; first match wins."},
	"pricing_matrix": {"Per-SKU pricing decisions",
		"The audited decision table: for each service and charge type, whether it is priced by a GCP SKU, a formula, passed through, or ignored — with the reason."},
	"fx_rates": {"Currency exchange rates",
		"Converts non-USD bills to USD. Flagged as stale after 60 days."},
	"review_config": {"Validation thresholds",
		"Numbers the pipeline uses to flag outliers, limit passthrough spend and decide when the pricing catalog is stale. Changing them needs no code change."},
	"refresh_store": {"Learned mappings cache",
		"Mappings and rates confirmed on earlier bills, reused to skip AI calls. Safe to empty — it refills as bills are processed."},
	"assumption_defaults": {"Default sizing assumptions",
		"Fallback values (for example Aurora ACU size) used when a bill does not say. Day-to-day overrides belong in Pricing Assumptions."},
}

// StoreHealth represents the integrity status of a data dependency file.
type StoreHealth struct {
	Key         string `json:"key"`
	Title       string `json:"title,omitempty"`
	Description string `json:"description,omitempty"`
	File        string `json:"file"`
	Status     string `json:"status"` // ok | warn | fail | unknown
	Detail     string `json:"detail"` // human reason
	SizeBytes  int64  `json:"size_bytes"`
	AgeDays    int    `json:"age_days"` // -1 if n/a
	Count      int    `json:"count"`    // rows/keys where meaningful, else 0
	Editable   bool   `json:"editable"`
	IsDBBacked bool   `json:"is_db_backed,omitempty"`
	ManageURL  string `json:"manage_url,omitempty"`
}

// checkJSONStore inspects a JSON file on disk, validates parseability, computes age and top-level item count.
func checkJSONStore(path, key, relFile string, warnIfMissing bool, minCountFloor int) StoreHealth {
	res := StoreHealth{
		Key:      key,
		File:     relFile,
		AgeDays:  -1,
		Editable: true,
	}

	info, err := os.Stat(path)
	if err != nil {
		if os.IsNotExist(err) {
			if warnIfMissing {
				res.Status = "warn"
				res.Detail = "file missing (optional or created on demand)"
			} else {
				res.Status = "fail"
				res.Detail = "file missing"
			}
			return res
		}
		res.Status = "fail"
		res.Detail = err.Error()
		return res
	}

	res.SizeBytes = info.Size()
	res.AgeDays = int(time.Since(info.ModTime()).Hours() / 24)

	if res.SizeBytes == 0 {
		if warnIfMissing {
			res.Status = "warn"
			res.Detail = "file is empty (0 bytes)"
		} else {
			res.Status = "fail"
			res.Detail = "file is empty (0 bytes)"
		}
		return res
	}

	data, err := os.ReadFile(path)
	if err != nil {
		res.Status = "fail"
		res.Detail = fmt.Sprintf("unreadable: %v", err)
		return res
	}

	var raw any
	if err := json.Unmarshal(data, &raw); err != nil {
		res.Status = "fail"
		res.Detail = fmt.Sprintf("corrupt JSON: %v", err)
		return res
	}

	count := 0
	switch v := raw.(type) {
	case map[string]any:
		count = len(v)
	case []any:
		count = len(v)
	}
	res.Count = count

	if count == 0 && minCountFloor > 0 {
		res.Status = "fail"
		res.Detail = "empty JSON container (0 items)"
		return res
	}

	if minCountFloor > 0 && count < minCountFloor {
		res.Status = "warn"
		res.Detail = fmt.Sprintf("low item count (%d < %d)", count, minCountFloor)
		return res
	}

	res.Status = "ok"
	res.Detail = fmt.Sprintf("valid (%d items)", count)
	return res
}

func checkCatalogArtifact(dataDir string) StoreHealth {
	path := filepath.Join(dataDir, "catalog.duckdb")
	res := StoreHealth{
		Key:     "catalog_artifact",
		File:    "catalog.duckdb",
		AgeDays: -1,
	}

	info, err := os.Stat(path)
	if err != nil {
		res.Status = "fail"
		res.Detail = "catalog.duckdb missing"
		return res
	}

	res.SizeBytes = info.Size()
	res.AgeDays = int(time.Since(info.ModTime()).Hours() / 24)
	if res.SizeBytes == 0 {
		res.Status = "fail"
		res.Detail = "catalog.duckdb is 0 bytes"
		return res
	}

	// Read count from CATALOG_META.json if present
	metaPath := filepath.Join(dataDir, "CATALOG_META.json")
	if mData, mErr := os.ReadFile(metaPath); mErr == nil {
		var meta struct {
			SKUCount int `json:"sku_count"`
		}
		if json.Unmarshal(mData, &meta) == nil && meta.SKUCount > 0 {
			res.Count = meta.SKUCount
		}
	}

	res.Status = "ok"
	if res.Count > 0 {
		res.Detail = fmt.Sprintf("valid duckdb catalog (%d SKUs)", res.Count)
	} else {
		res.Detail = "valid duckdb catalog"
	}
	return res
}

func checkCatalogSource(dataDir string) StoreHealth {
	skusDir := filepath.Join(dataDir, "skus")
	res := StoreHealth{
		Key:     "catalog_source",
		File:    "skus/*.json.gz",
		AgeDays: -1,
	}

	entries, err := os.ReadDir(skusDir)
	if err != nil {
		res.Status = "fail"
		res.Detail = "data/skus directory missing"
		return res
	}

	var count int
	var totalSize int64
	var hasZeroByte bool
	var oldestMod time.Time

	for _, entry := range entries {
		name := strings.ToLower(entry.Name())
		if strings.HasSuffix(name, ".json.gz") {
			count++
			if info, err := entry.Info(); err == nil {
				totalSize += info.Size()
				if info.Size() == 0 {
					hasZeroByte = true
				}
				if oldestMod.IsZero() || info.ModTime().Before(oldestMod) {
					oldestMod = info.ModTime()
				}
			}
		}
	}

	res.Count = count
	res.SizeBytes = totalSize
	if !oldestMod.IsZero() {
		res.AgeDays = int(time.Since(oldestMod).Hours() / 24)
	}

	if count == 0 {
		res.Status = "fail"
		res.Detail = "no SKU source files found in data/skus"
		return res
	}

	if hasZeroByte {
		res.Status = "warn"
		res.Detail = fmt.Sprintf("%d archives found but some are 0 bytes", count)
		return res
	}

	res.Status = "ok"
	res.Detail = fmt.Sprintf("%d service archives present and non-empty", count)
	return res
}

func checkFXRates(path string) StoreHealth {
	res := checkJSONStore(path, "fx_rates", "fx_rates.json", false, 1)
	if res.Status == "fail" {
		return res
	}

	// Check if date inside fx_rates is older than 60 days
	data, err := os.ReadFile(path)
	if err == nil {
		var fx struct {
			Updated string             `json:"updated"`
			Rates   map[string]float64 `json:"rates"`
		}
		if json.Unmarshal(data, &fx) == nil {
			if len(fx.Rates) > 0 {
				res.Count = len(fx.Rates)
			}
			if fx.Updated != "" {
				if t, err := time.Parse("2006-01-02", fx.Updated); err == nil {
					age := int(time.Since(t).Hours() / 24)
					res.AgeDays = age
					if age > 60 {
						res.Status = "warn"
						res.Detail = fmt.Sprintf("FX rates stale (%dd old, threshold 60d)", age)
						return res
					}
				}
			}
		}
	}

	if res.AgeDays > 60 {
		res.Status = "warn"
		res.Detail = fmt.Sprintf("FX rates stale (%dd old, threshold 60d)", res.AgeDays)
		return res
	}

	return res
}

// CheckDataStores validates all declared dependencies and returns the individual checks plus a roll-up status.
func CheckDataStores(skillDir string) ([]StoreHealth, string) {
	if skillDir == "" {
		skillDir = skill.ResolveDir()
	}
	dataDir := filepath.Join(skillDir, "data")

	stores := []StoreHealth{
		checkCatalogArtifact(dataDir),
		checkCatalogSource(dataDir),
		checkJSONStore(filepath.Join(dataDir, "service_map.json"), "service_map", "service_map.json", false, 1),
		checkJSONStore(filepath.Join(dataDir, "service_specs.json"), "service_specs", "service_specs.json", false, 5),
		checkJSONStore(filepath.Join(dataDir, "pricing_matrix.json"), "pricing_matrix", "pricing_matrix.json", false, 1),
		checkFXRates(filepath.Join(dataDir, "fx_rates.json")),
		checkJSONStore(filepath.Join(dataDir, "review-config.json"), "review_config", "review-config.json", false, 1),
		checkJSONStore(filepath.Join(dataDir, "refresh_store.json"), "refresh_store", "refresh_store.json", true, 0),
		checkJSONStore(filepath.Join(dataDir, "assumption-defaults.json"), "assumption_defaults", "assumption-defaults.json", false, 1),
	}
	for i := range stores {
		if m, ok := storeManifest[stores[i].Key]; ok {
			stores[i].Title = m.Title
			stores[i].Description = m.Description
		}
		_, stores[i].Editable = editableDatastores[stores[i].Key]
	}

	dataStatus := "ok"
	for _, s := range stores {
		if s.Status == "fail" {
			dataStatus = "fail"
			break
		}
		if s.Status == "warn" && dataStatus != "fail" {
			dataStatus = "warn"
		}
	}

	return stores, dataStatus
}
