# Ducts Manufacturing Inventory Management System

A **Smartsheet-first, migration-ready** manufacturing inventory management system for ducts production planning, allocation, consumption tracking, and delivery operations.

## 🎯 Overview

This system implements a SOTA (State-of-the-Art) framework for:
- **Tag-based production planning** with T-1 nesting workflow
- **Ledger-first inventory control** (allocation → pick → consumption → dispatch)
- **Exception-driven operations** with full audit trail
- **SAP integration readiness** (peer system, not master)

## 📋 Features

- ✅ LPO (Local Purchase Order) management
- ✅ Tag Sheet Registry with file hash deduplication
- ✅ Nesting execution logging
- ✅ Material allocation with shift-based reservations
- ✅ Consumption tracking with remnant support
- ✅ Delivery order management (SAP + Virtual DO)
- ✅ Inventory snapshots (System, SAP, Physical)
- ✅ Exception handling with SLA tracking
- ✅ Full user action audit trail

## 🏗️ Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Smartsheet    │────▶│  Power Automate  │────▶│   SharePoint    │
│   (UI + Data)   │     │  (Orchestration) │     │   (File Store)  │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                               │
                               ▼
                        ┌──────────────────┐
                        │  Azure Functions │
                        │  (Business Logic)│
                        └──────────────────┘
                               │
                               ▼
                        ┌──────────────────┐
                        │   SAP Connector  │
                        │   (Integration)  │
                        └──────────────────┘
```

## 📁 Project Structure

```
├── Specifications/
│   ├── architecture_specification.md    # Full system architecture
│   └── data_structure_specification.md  # Data model & governance
├── fetch_smartsheet_metadata.py         # Pull workspace metadata
├── create_workspace.py                  # Create new workspace
├── config_values.md                     # Config table entries
├── implementation_plan.md               # Sprint-based plan
├── requirements.txt                     # Python dependencies
└── .env.example                         # Environment template
```

## 🚀 Getting Started

### Prerequisites

- Python 3.8+
- Smartsheet account (EU region)
- API access token

### Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/ducts-manufacturing-inventory.git
cd ducts-manufacturing-inventory
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Set up environment variables:
```bash
cp .env.example .env
# Edit .env with your credentials
```

4. (Optional) Create a new workspace:
```bash
python create_workspace.py
```

## ⚙️ Configuration

Copy `.env.example` to `.env` and set:

| Variable | Description |
|----------|-------------|
| `SMARTSHEET_API_KEY` | Your Smartsheet API token |
| `SMARTSHEET_WORKSPACE_ID` | Target workspace ID |
| `SMARTSHEET_BASE_URL` | API base URL (default: EU) |

## 📊 Smartsheet Schema

The system uses 19 sheets organized in 4 folders:

| Folder | Sheets |
|--------|--------|
| Root | Reference Data, Config |
| 01. Commercial | LPO Master, LPO Audit |
| 02. Tag Sheet | Tag Sheet Registry |
| 03. Production | Planning, Nesting, Allocation |
| 04. Production & Delivery | Consumption, Remnant, Delivery, Invoice, Inventory, Exceptions, Audit |

## 🔄 Migration Path

This system is designed for **zero-friction migration** to Azure SQL/Dataverse:

1. **Same column names** - Canonical naming across all sheets
2. **Same data types** - Compatible with SQL schemas
3. **Logic in Power Automate** - Portable orchestration
4. **Append-only ledgers** - Simple data export

## 📖 Documentation

- [Architecture Specification](Specifications/architecture_specification.md)
- [Data Structure Specification](Specifications/data_strucutre_specification.md)
- [Implementation Plan](implementation_plan.md)
- [Config Values](config_values.md)

## 🛡️ Security

- API keys stored in environment variables
- `.gitignore` excludes sensitive files
- All metadata files excluded from version control

## 📄 License

MIT License - see [LICENSE](LICENSE) for details.

## 🤝 Contributing

1. Fork the repository
2. Create your feature branch
3. Commit your changes
4. Push to the branch
5. Open a Pull Request
