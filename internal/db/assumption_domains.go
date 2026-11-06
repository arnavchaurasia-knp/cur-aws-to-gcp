package db

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"

	"github.com/facets/cur-web/internal/skill"
)

// AssumptionDomain describes the legal input domain and provenance of an assumption.
type AssumptionDomain struct {
	Kind                   string   `json:"kind"`
	Values                 any      `json:"values,omitempty"`
	Value                  *float64 `json:"value,omitempty"`
	Min                    *float64 `json:"min,omitempty"`
	Max                    *float64 `json:"max,omitempty"`
	Step                   *float64 `json:"step,omitempty"`
	ValueSource            string   `json:"value_source,omitempty"`
	DerivedFrom            string   `json:"derived_from,omitempty"`
	SourceKind             string   `json:"source_kind,omitempty"`
	Source                 string   `json:"source,omitempty"`
	Refreshable            bool     `json:"refreshable"`
	LastVerified           string   `json:"last_verified,omitempty"`
	NeedsHumanVerification bool     `json:"needs_human_verification,omitempty"`
}

var (
	assumptionDomainsOnce sync.Once
	assumptionDomains     map[string]*AssumptionDomain
	assumptionDomainsErr  error
)

func assumptionDomainsPaths() []string {
	rel := filepath.Join("skill", "aws-gcp-cost-projection", "data", "assumption_domains.json")
	return []string{
		filepath.Join(skill.ResolveDir(), "data", "assumption_domains.json"),
		rel,
		filepath.Join("..", "..", rel),
	}
}

// LoadAssumptionDomains reads data/assumption_domains.json once per process.
func LoadAssumptionDomains() (map[string]*AssumptionDomain, error) {
	assumptionDomainsOnce.Do(func() {
		var raw []byte
		var tried []string
		for _, path := range assumptionDomainsPaths() {
			b, err := os.ReadFile(path)
			if err == nil {
				raw = b
				break
			}
			tried = append(tried, path)
		}
		if raw == nil {
			assumptionDomainsErr = fmt.Errorf("assumption domains not found (looked in: %s)", strings.Join(tried, ", "))
			return
		}
		var parsed struct {
			Domains map[string]*AssumptionDomain `json:"domains"`
		}
		if err := json.Unmarshal(raw, &parsed); err != nil {
			assumptionDomainsErr = fmt.Errorf("assumption domains malformed: %w", err)
			return
		}
		assumptionDomains = parsed.Domains
	})
	return assumptionDomains, assumptionDomainsErr
}

// ExpectedBounds returns the [min, max] implied by this domain for DB consistency checking.
func (d *AssumptionDomain) ExpectedBounds() (minVal, maxVal float64, err error) {
	switch d.Kind {
	case "enum":
		rawSlice, ok := d.Values.([]any)
		if !ok || len(rawSlice) == 0 {
			return 0, 0, fmt.Errorf("enum domain has no values")
		}
		minVal = 1e18
		maxVal = -1e18
		for _, v := range rawSlice {
			f, ok := v.(float64)
			if !ok {
				return 0, 0, fmt.Errorf("enum value is not float64: %v", v)
			}
			if f < minVal {
				minVal = f
			}
			if f > maxVal {
				maxVal = f
			}
		}
		return minVal, maxVal, nil
	case "flag":
		return 0.0, 1.0, nil
	case "range", "scalar_published":
		if d.Min == nil || d.Max == nil {
			return 0, 0, fmt.Errorf("range/scalar missing min/max")
		}
		return *d.Min, *d.Max, nil
	default:
		return 0, 0, fmt.Errorf("unknown domain kind: %s", d.Kind)
	}
}
