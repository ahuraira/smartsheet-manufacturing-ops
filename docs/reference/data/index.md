# 📊 Data Dictionary

> **Document Type:** Reference | **Version:** 1.6.9 | **Last Updated:** 2026-02-06

Complete reference for all data models, sheets, and column definitions in the Ducts Manufacturing Inventory Management System.

---

## Overview

### Data Architecture Principles

| Principle | Implementation |
|-----------|----------------|
| **Ledger-First** | All transactions are immutable, append-only |
| **Canonical Names** | Same column names across Smartsheet → SQL |
| **Separation** | Planned ≠ Allocated ≠ Consumed |
| **Snapshot + Txn** | Transactions explain *how*, snapshots explain *what* |

### Naming Conventions

| Type | Convention | Example |
|------|------------|---------|
| Sheet Names | `NN Description` | `01 LPO Master LOG` |
| Column Names | Title Case | `Customer Name` |
| ID Fields | `ENTITY_ID` pattern | `Tag ID`, `LPO ID` |
| Status Fields | `Status` or `ENTITY Status` | `LPO Status` |
| Dates | ISO 8601 | `2026-01-08` |
| Timestamps | ISO 8601 + Time | `2026-01-08T10:30:00` |

### ID Patterns

| Entity | Pattern | Example | Generator |
|--------|---------|---------|-----------|
| Tag | `TAG-YYYYMMDD-NNNN` | `TAG-20260105-0001` | `generate_tag_id()` |
| LPO | `LPO-NNNN` | `LPO-0024` | `generate_next_lpo_id()` (v1.6.8) |
| Exception | `EX-NNNN` | `EX-0123` | Auto-increment |
| BOM | `BOM-NNNN` | `BOM-1001` | Auto-increment |

### Logical Names (Code Constants)

The system uses **ID-First Architecture**. While "Sheet Name" refers to user-facing names in Smartsheet, application code uses **Logical Names** (e.g., `Sheet.TAG_REGISTRY`), mapped to physical IDs via `workspace_manifest.json`.

- **Physical Name**: `02 Tag Sheet Registry` (Mutable, User-facing)
- **Logical Name**: `TAG_REGISTRY` (Immutable, Code-facing)

---

## Documentation Structure

### Enumerations
[View all system enumerations →](./enums.md)

Status types, action types, exception sources, config keys, and other enumerated values.

### Data Models
[View all Pydantic models →](./models.md)

Request/response schemas for API endpoints and internal data structures.

### Sheet Schemas

| Category | File | Sheets Covered |
|----------|------|----------------|
| Core & Master | [sheets-core.md](./sheets-core.md) | Config (00a), LPO Master (01), LPO Audit (01a) |
| Production | [sheets-production.md](./sheets-production.md) | Tags (02), Planning (03), Nesting (04) |
| Material Mapping | [sheets-mapping.md](./sheets-mapping.md) | Material Master (05a-05e), Parsed BOM (06a) |
| Governance | [sheets-governance.md](./sheets-governance.md) | User Actions (98), Exceptions (99) |

### Shared Services
[View service modules →](./services.md)

Centralized business logic: `lpo_service`, `unit_service`, `atomic_update`, helper functions.

---

## Related Documentation

- [API Reference](../api/index.md) - API endpoints using these data models
- [Configuration](../configuration.md) - Environment variables and settings
- [Error Codes](../error_codes.md) - Exception types and handling
