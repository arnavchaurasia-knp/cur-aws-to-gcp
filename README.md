# 🚀 CUR AWS to GCP

Convert AWS Cost and Usage Reports (CUR) into GCP cost projections with an intelligent mapping engine.

![Python](https://img.shields.io/badge/Python-63.9%25-3776ab?style=flat-square)
![Go](https://img.shields.io/badge/Go-24.2%25-00ADD8?style=flat-square)
![TypeScript](https://img.shields.io/badge/TypeScript-7.8%25-3178c6?style=flat-square)
![Shell](https://img.shields.io/badge/Shell-3.8%25-4EAA25?style=flat-square)

---

## 📋 Overview

This project provides a comprehensive solution for **analyzing AWS billing data and projecting costs on Google Cloud Platform (GCP)**. It combines a powerful backend service written in Go with an intelligent Python-based skill engine for cost mapping and analysis.

### Key Features

✨ **AWS CUR Analysis** - Upload and parse AWS Cost and Usage Reports  
🎯 **GCP Cost Projection** - Intelligent mapping of AWS services to GCP equivalents  
📊 **Cost Comparison** - Visual comparison and cost projection reports  
🔄 **Multi-Phase Pipeline** - Sophisticated 6-phase processing pipeline  
🎨 **Interactive UI** - React + TypeScript frontend for easy interaction  
⚡ **Scalable Architecture** - Go backend with Python skill engine for extensibility  

---

## 🏗️ Project Structure

```
cur-aws-to-gcp/
├── backend/                    # Go service (24.2%)
│   ├── internal/
│   │   ├── jobs/              # Job spawner and scheduling
│   │   └── handlers/          # API handlers
│   └── main.go
│
├── skill/                      # Python skill engine (63.9%)
│   └── aws-gcp-cost-projection/
│       ├── SKILL.md           # Skill documentation
│       ├── phases/            # 6-phase pipeline
│       │   ├── 01-ingest.md   # PDF ingestion
│       │   ├── 02-map.md      # Service mapping
│       │   ├── 03-review.md   # Review phase
│       │   ├── 04-rate-fill.md # Rate filling
│       │   ├── 05-outlier.md  # Outlier detection
│       │   └── 06-report.md   # Report generation
│       ├── scripts/           # Utilities
│       ├── reference/         # Schemas & recipes
│       └── data/              # GCP Cloud Billing Catalog
│
├── frontend/                   # React + TypeScript (7.8%)
│   ├── src/
│   ├── vite.config.ts
│   └── package.json
│
└── README.md
```

---

## 🔄 Processing Pipeline

The core of this project is a **6-phase intelligent pipeline** that transforms AWS billing data into accurate GCP cost projections:

```
┌─────────────┐     ┌────────┐     ┌────────┐     ┌──────────┐     ┌──────────┐     ┌────────┐
│   INGEST    │ --> │  MAP   │ --> │ REVIEW │ --> │RATE-FILL │ --> │ OUTLIER  │ --> │ REPORT │
│  (PDF/CSV)  │     │Services│     │  Data  │     │ GCP Rates│     │Detection │     │  Gen   │
└─────────────┘     └────────┘     └────────┘     └──────────┘     └──────────┘     └────────┘
```

**Phase Details:**
- **Phase 1 - Ingest**: Parse AWS CUR PDFs/CSVs and extract billing data
- **Phase 2 - Map**: Intelligently map AWS services to GCP equivalents
- **Phase 3 - Review**: Validate and review mapped data for accuracy
- **Phase 4 - Rate Fill**: Apply current GCP pricing catalog
- **Phase 5 - Outlier**: Detect and flag anomalies in cost projections
- **Phase 6 - Report**: Generate comprehensive cost comparison reports

---

## 💻 Tech Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **Backend** | Go | REST API, job orchestration, GCP Cloud Billing integration |
| **Skill Engine** | Python | Cost mapping logic, service translation, rate calculations |
| **Frontend** | React + TypeScript + Vite | Interactive UI for bill uploads and report viewing |
| **Data** | JSON (GCP Catalog) | Pre-bundled GCP pricing and service catalog |

---

## 🚀 Getting Started

### Prerequisites

- Go 1.20+
- Python 3.9+
- Node.js 18+
- GCP Cloud Billing Catalog access

### Backend Setup

```bash
# Navigate to backend directory
cd backend

# Install dependencies
go mod download

# Run the service
go run main.go
```

### Skill Engine Setup

```bash
# Navigate to skill directory
cd skill/aws-gcp-cost-projection

# Review the skill documentation
cat SKILL.md

# Run preflight checks
bash preflight.sh
```

### Frontend Setup

```bash
# Navigate to frontend directory
cd frontend

# Install dependencies
npm install

# Start development server
npm run dev

# Build for production
npm run build
```

---

## 🔧 Core Components

### Backend (Go)

- **REST API** - Handles bill uploads and report requests
- **Job Spawner** (`internal/jobs/spawner.go`) - Orchestrates skill execution
- **Request Handlers** - Manages incoming requests and responses
- **GCP Integration** - Connects with GCP Cloud Billing API

### Skill Engine (Python)

Located in `skill/aws-gcp-cost-projection/`, the skill is the **source of truth** for mapping logic:
- **Phase Scripts** - Each phase is documented in Markdown
- **Data Catalog** - Pre-bundled GCP services and SKUs
- **Utilities** - `find-sku.sh`, `refresh-catalog.sh`, allow-list management

### Frontend (React + TypeScript)

- **Bill Upload** - Drag-and-drop AWS CUR upload interface
- **Results Dashboard** - View projections and comparisons
- **Report Generation** - Export detailed cost analysis

---

## 📝 Deploying Changes

### Skill Updates

```bash
# From repo root, push skill changes to GCP VM
gcloud compute scp --recurse --zone=asia-south1-a \
  skill/aws-gcp-cost-projection/phases \
  skill/aws-gcp-cost-projection/reference \
  cur-web:~/.claude/skills/aws-gcp-cost-projection/
```

### Catalog Updates

```bash
# Update GCP pricing catalog
cd skill/aws-gcp-cost-projection
bash scripts/refresh-catalog.sh
```

---

## 📚 Documentation

- **[SKILL.md](skill/aws-gcp-cost-projection/SKILL.md)** - Comprehensive skill documentation
- **[Phase Docs](skill/aws-gcp-cost-projection/phases/)** - Detailed phase specifications
- **[PDF Ingestion Recipe](skill/aws-gcp-cost-projection/reference/pdf-ingestion.md)** - Bill parsing guide
- **[Frontend README](frontend/README.md)** - React/TypeScript setup details

---

## 🔄 Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                        Frontend (React)                      │
│                  (Upload Bill, View Results)                 │
└────────────────────┬────────────────────────────────────────┘
                     │ HTTP/REST
┌────────────────────▼────────────────────────────────────────┐
│                    Backend (Go)                              │
│              (API, Job Orchestration)                        │
└────────────────────┬────────────────────────────────────────┘
                     │ Spawns
┌────────────────────▼────────────────────────────────────────┐
│               Skill Engine (Python)                          │
│          (6-Phase Mapping Pipeline)                          │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ Phase 1: Ingest → Phase 2: Map → Phase 3: Review  │   │
│  │ Phase 4: Rate-Fill → Phase 5: Outlier → Phase 6   │   │
│  └─────────────────────────────────────────────────────┘   │
└────────────────────┬──────────────────────────────��─────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│                  GCP Resources                               │
│        (Cloud Billing API, Pricing Catalog)                 │
└─────────────────────────────────────────────────────────────┘
```

---

## 🤝 Contributing

Contributions are welcome! When making changes:

1. **Backend Changes** - Update `internal/` and test with the skill engine
2. **Skill Changes** - Modify `skill/aws-gcp-cost-projection/` and redeploy using `gcloud compute scp`
3. **Frontend Changes** - Update React components and rebuild
4. **Catalog Changes** - Use `refresh-catalog.sh` to regenerate pricing data

---

## 📄 License

This project is open source and available under the [MIT License](LICENSE).

---

## 📧 Support & Questions

For issues, questions, or suggestions, please open an issue on GitHub or contact the maintainers.

---

**Built with ❤️ to bridge AWS and GCP cost analysis**
