# 📚 Ducts Manufacturing Inventory Management - Documentation Hub

> **Version:** 1.3.0 | **Last Updated:** 2026-01-13 | **Status:** Active Development

Welcome to the comprehensive documentation for the Ducts Manufacturing Inventory Management System. This documentation follows the [Diátaxis framework](https://diataxis.fr/) to organize content by user needs: **Tutorials**, **How-to Guides**, **Reference**, and **Explanation**.

---

## 🗺️ Documentation Map

### 📖 Getting Started

| Document | Description | Audience |
|----------|-------------|----------|
| [Quick Start Guide](./quick_start.md) | Get up and running in 15 minutes | New developers |
| [Setup Guide](./setup_guide.md) | Complete development environment setup | All developers |
| [Architecture Overview](./architecture_overview.md) | System design and component relationships | All team members |
| [User Journey: Tag Ingestion](./user_journey_test_guide.md) | End-to-end tag upload walkthrough | All team members |

### 🔧 How-To Guides

| Document | Description | Audience |
|----------|-------------|----------|
| [Adding a New Function](./howto/add_function.md) | Step-by-step guide for new Azure Functions | Developers |
| [Testing Guide](./howto/testing.md) | How to write and run tests | Developers |
| [Deployment Guide](./howto/deployment.md) | Deploy to Azure environments | DevOps |
| [Troubleshooting](./howto/troubleshooting.md) | Common issues and solutions | All team members |

### 📚 Reference Documentation

| Document | Description | Audience |
|----------|-------------|----------|
| [API Reference](./reference/api_reference.md) | Complete API specification | Developers, Integrators |
| [Data Dictionary](./reference/data_dictionary.md) | All data models and schemas | Developers, Analysts |
| [Configuration Reference](./reference/configuration.md) | All configuration options | Developers, Operations |
| [Error Code Reference](./reference/error_codes.md) | Exception types and handling | Developers, Support |

### 🏗️ Specifications

| Document | Description | Audience |
|----------|-------------|----------|
| [Architecture Specification](../Specifications/architecture_specification.md) | Complete system architecture | Architects, Tech Leads |
| [Data Structure Specification](../Specifications/data_strucutre_specification.md) | Data governance and schema | Developers, DBAs |
| [Tag Ingestion Architecture](../Specifications/tag_ingestion_architecture.md) | Tag ingestion flow details | Developers |
| [LPO Ingestion Architecture](../Specifications/lpo_ingestion_architecture.md) | LPO ingestion flow details | Developers |
| [Flow Architecture](../Specifications/flow_architecture.md) | Power Automate flow design | Developers, BA |

### 📋 Project Management

| Document | Description | Audience |
|----------|-------------|----------|
| [Implementation Plan](../implementation_plan.md) | Sprint-based roadmap | PM, Developers |
| [Changelog](./CHANGELOG.md) | Version history | All team members |
| [Contributing Guide](./CONTRIBUTING.md) | Contribution guidelines | All contributors |

---

## 🎯 Quick Links by Role

### 👨‍💻 Developer
1. Start with → [Quick Start Guide](./quick_start.md)
2. Understand → [Architecture Overview](./architecture_overview.md)
3. Reference → [API Reference](./reference/api_reference.md)
4. Build → [Adding a New Function](./howto/add_function.md)

### 🔧 DevOps/Operations
1. Deploy → [Deployment Guide](./howto/deployment.md)
2. Configure → [Configuration Reference](./reference/configuration.md)
3. Monitor → [Troubleshooting](./howto/troubleshooting.md)

### 📊 Business Analyst
1. Understand → [Architecture Overview](./architecture_overview.md)
2. Data → [Data Dictionary](./reference/data_dictionary.md)
3. Flows → [Flow Architecture](../Specifications/flow_architecture.md)

### 🆕 New Team Member
1. Read → This index page
2. Setup → [Setup Guide](./setup_guide.md)
3. Explore → [Architecture Overview](./architecture_overview.md)
4. Ask → Check [Troubleshooting](./howto/troubleshooting.md) for common questions

---

## 📁 Repository Structure

```
📦 ducts_manufacturing_inventory_management
├── 📂 docs/                     # 📖 Documentation (you are here)
│   ├── index.md                 # This file - documentation hub
│   ├── quick_start.md           # Quick start guide
│   ├── setup_guide.md           # Development setup
│   ├── architecture_overview.md # Architecture overview
│   ├── CHANGELOG.md             # Version history
│   ├── CONTRIBUTING.md          # Contribution guidelines
│   ├── 📂 reference/            # Reference documentation
│   │   ├── api_reference.md     # API documentation
│   │   ├── data_dictionary.md   # Data models
│   │   ├── configuration.md     # Config options
│   │   └── error_codes.md       # Error handling
│   └── 📂 howto/                # How-to guides
│       ├── add_function.md      # Adding functions
│       ├── testing.md           # Testing guide
│       ├── deployment.md        # Deployment
│       └── troubleshooting.md   # Troubleshooting
├── 📂 Specifications/           # 📋 Technical specifications
│   ├── architecture_specification.md
│   ├── data_strucutre_specification.md
│   ├── tag_ingestion_architecture.md
│   └── flow_architecture.md
├── 📂 functions/                # ⚡ Azure Functions (Core)
│   ├── 📂 fn_ingest_tag/        # Tag ingestion function
│   ├── 📂 fn_lpo_ingest/        # LPO ingestion function (v1.2.0)
│   ├── 📂 fn_lpo_update/        # LPO update function (v1.2.0)
│   ├── 📂 fn_schedule_tag/      # Production scheduling (v1.3.0)
│   ├── 📂 shared/               # Shared modules
│   └── 📂 tests/                # Test suite
├── 📂 function_adapter/         # ⚡ Azure Functions (Webhooks)
│   ├── 📂 fn_webhook_receiver/  # Smartsheet webhook receiver
│   ├── 📂 fn_webhook_admin/     # Webhook management API
│   └── 📂 fn_event_processor/   # Service Bus event processor
├── README.md                    # Project README
├── implementation_plan.md       # Development roadmap
├── config_values.md             # Config table entries
└── requirements.txt             # Python dependencies
```

---

## 🔗 External Resources

| Resource | Description |
|----------|-------------|
| [Azure Functions Documentation](https://docs.microsoft.com/azure/azure-functions/) | Official Azure Functions docs |
| [Smartsheet API Reference](https://smartsheet-platform.github.io/api-docs/) | Smartsheet API documentation |
| [Power Automate Documentation](https://docs.microsoft.com/power-automate/) | Power Automate guides |
| [Pydantic Documentation](https://docs.pydantic.dev/) | Data validation library |

---

## 📝 Documentation Standards

This documentation follows these standards:

1. **Diátaxis Framework** - Content organized by user needs
2. **Google Developer Documentation Style Guide** - Writing conventions
3. **Markdown Best Practices** - Formatting consistency
4. **Living Documentation** - Kept in sync with code

### Documentation Conventions

| Convention | Example | Usage |
|------------|---------|-------|
| `backticks` | `fn_ingest_tag` | Code, files, functions |
| **Bold** | **Important** | Key terms, emphasis |
| *Italics* | *optional* | Optional parameters, notes |
| `>` Blockquote | > Note: ... | Important notes, warnings |

---

## 🆘 Need Help?

- **Found an issue?** Create a GitHub issue with the `documentation` label
- **Have a question?** Check [Troubleshooting](./howto/troubleshooting.md) first
- **Want to contribute?** Read [Contributing Guide](./CONTRIBUTING.md)

---

<p align="center">
  <em>Last updated: 2026-01-13 | Maintained by the Development Team</em>
</p>
