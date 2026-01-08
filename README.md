# Ducts Manufacturing Inventory Management System

<p align="center">
  <strong>A Smartsheet-first, migration-ready manufacturing inventory management system</strong>
</p>

<p align="center">
  <a href="docs/index.md">📚 Documentation</a> •
  <a href="docs/quick_start.md">🚀 Quick Start</a> •
  <a href="docs/reference/api_reference.md">📘 API Reference</a> •
  <a href="docs/CONTRIBUTING.md">🤝 Contributing</a>
</p>

---

## 🎯 Overview

This system implements a **state-of-the-art (SOTA)** framework for:

- **Tag-based production planning** with T-1 nesting workflow
- **Ledger-first inventory control** (allocation → pick → consumption → dispatch)
- **Exception-driven operations** with full audit trail
- **SAP integration readiness** (peer system, not master)

### Key Features

| Feature | Status |
|---------|--------|
| ✅ LPO (Local Purchase Order) management | Implemented |
| ✅ Tag Sheet Registry with file hash deduplication | Implemented |
| ✅ Sequence-based ID generation | Implemented |
| ✅ Idempotent API with retry support | Implemented |
| ✅ Exception handling with SLA tracking | Implemented |
| ✅ Full user action audit trail | Implemented |
| 🔄 Nesting execution logging | Planned |
| 🔄 Material allocation with shift-based reservations | Planned |
| 🔄 Consumption tracking with remnant support | Planned |
| 🔄 Delivery order management | Planned |

---

## 🏗️ Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Smartsheet    │────▶│  Power Automate  │────▶│   SharePoint    │
│   (UI + Data)   │     │  (Orchestration) │     │   (File Store)  │
└─────────────────┘     └────────┬─────────┘     └─────────────────┘
                                 │
                                 ▼
                        ┌──────────────────┐
                        │  Azure Functions │
                        │ (Business Logic) │
                        └────────┬─────────┘
                                 │
                                 ▼
                        ┌──────────────────┐
                        │   SAP Connector  │
                        │   (Integration)  │
                        └──────────────────┘
```

**Design Principles:**
- **Separation of concerns** - UI in Smartsheet, logic in Azure Functions
- **Ledger-first** - All movements are immutable transactions
- **Idempotency everywhere** - Safe retries with `client_request_id`
- **Exception as first-class citizens** - All errors create trackable records

For detailed architecture, see [Architecture Overview](docs/architecture_overview.md).

---

## 📁 Project Structure

```
📦 ducts_manufacturing_inventory_management
├── 📂 docs/                     # 📖 Documentation
│   ├── index.md                 # Documentation hub
│   ├── quick_start.md           # Quick start guide
│   ├── setup_guide.md           # Development setup
│   ├── architecture_overview.md # Architecture overview
│   ├── 📂 reference/            # API & data reference
│   └── 📂 howto/                # How-to guides
├── 📂 Specifications/           # 📋 Technical specifications
│   ├── architecture_specification.md
│   ├── data_strucutre_specification.md
│   ├── tag_ingestion_architecture.md
│   └── flow_architecture.md
├── 📂 functions/                # ⚡ Azure Functions
│   ├── fn_ingest_tag/           # Tag ingestion function
│   ├── shared/                  # Shared modules
│   └── tests/                   # Test suite
├── README.md                    # This file
├── implementation_plan.md       # Development roadmap
├── config_values.md             # Config table entries
└── requirements.txt             # Python dependencies
```

---

## 🚀 Quick Start

### Prerequisites

- Python 3.9+
- Azure Functions Core Tools v4+
- Smartsheet API Key

### Installation

```bash
# Clone repository
git clone <repository-url>
cd ducts_manufacturing_inventory_management

# Create virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1  # Windows
source venv/bin/activate      # macOS/Linux

# Install dependencies
pip install -r functions/requirements.txt
```

### Configure

Create `functions/local.settings.json`:

```json
{
  "IsEncrypted": false,
  "Values": {
    "SMARTSHEET_API_KEY": "your_api_key",
    "SMARTSHEET_WORKSPACE_ID": "your_workspace_id",
    "SMARTSHEET_BASE_URL": "https://api.smartsheet.eu/2.0"
  }
}
```

### Run Locally

```bash
cd functions
func start
```

### Test

```bash
cd functions
pytest
```

For detailed setup, see [Quick Start Guide](docs/quick_start.md) or [Setup Guide](docs/setup_guide.md).

---

## 📊 API Reference

### Tag Ingestion

```http
POST /api/tags/ingest
Content-Type: application/json
```

```json
{
  "client_request_id": "uuid",
  "lpo_sap_reference": "SAP-001",
  "required_area_m2": 50.0,
  "requested_delivery_date": "2026-02-01",
  "uploaded_by": "user@company.com"
}
```

**Response:**
```json
{
  "status": "UPLOADED",
  "tag_id": "TAG-0001",
  "trace_id": "trace-abc123",
  "message": "Tag uploaded successfully"
}
```

For complete API documentation, see [API Reference](docs/reference/api_reference.md).

---

## 📋 Smartsheet Schema

The system uses 19 sheets organized in 4 folders:

| Folder | Sheets |
|--------|--------|
| Root | Reference Data, Config |
| 01. Commercial | LPO Master, LPO Audit |
| 02. Tag Sheet | Tag Sheet Registry |
| 03. Production | Planning, Nesting, Allocation |
| 04. Production & Delivery | Consumption, Remnant, Delivery, Invoice, Inventory, Exceptions, Audit |

---

## 🔄 Migration Path

Designed for **zero-friction migration** to Azure SQL/Dataverse:

| Aspect | Migration Ready |
|--------|-----------------|
| ✅ Column names | Canonical across Smartsheet → SQL |
| ✅ Data types | Compatible with SQL schemas |
| ✅ Logic location | All in Azure Functions (portable) |
| ✅ Ledger pattern | Append-only (simple export) |

---

## 📖 Documentation

| Document | Description |
|----------|-------------|
| [📚 Documentation Hub](docs/index.md) | Central documentation index |
| [🚀 Quick Start](docs/quick_start.md) | 15-minute setup guide |
| [🏗️ Architecture](docs/architecture_overview.md) | System design overview |
| [📘 API Reference](docs/reference/api_reference.md) | Complete API docs |
| [📊 Data Dictionary](docs/reference/data_dictionary.md) | Data models and schemas |
| [🧪 Testing Guide](docs/howto/testing.md) | How to write tests |
| [🚀 Deployment](docs/howto/deployment.md) | Deployment procedures |

---

## 🛡️ Security

- API keys stored in environment variables / Key Vault
- Azure AD authentication for production
- RBAC for function access
- `.gitignore` excludes all sensitive files
- All user actions logged to audit trail

---

## 🧪 Testing

```bash
cd functions

# Run all tests
pytest

# Run with coverage
pytest --cov=shared --cov=fn_ingest_tag

# Run by category
pytest -m unit
pytest -m integration
pytest -m acceptance
```

See [Testing Guide](docs/howto/testing.md) for details.

---

## 🤝 Contributing

1. Read the [Contributing Guide](docs/CONTRIBUTING.md)
2. Fork the repository
3. Create your feature branch
4. Write tests for new functionality
5. Submit a Pull Request

---

## 📄 License

MIT License - see [LICENSE](LICENSE) for details.

---

## 📞 Support

- Check [Troubleshooting Guide](docs/howto/troubleshooting.md)
- Search existing GitHub issues
- Open a new issue with `trace_id` and error details

---

<p align="center">
  <strong>Built with ❤️ for manufacturing excellence</strong>
</p>
