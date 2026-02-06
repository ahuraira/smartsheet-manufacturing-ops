---
title: Material Mapping Sheets
description: Material master, overrides, and BOM sheet schemas
keywords: [sheets, material, mapping, bom, sap codes]
category: data-reference
version: 1.6.9
---

[Home](../../index.md) > [Data Dictionary](./index.md) > Material Mapping

# Material Mapping Sheets

> **Document Type:** Reference | **Version:** 1.6.0+ | **Last Updated:** 2026-02-06

Schemas for canonical material mapping system introduced in v1.6.0.

---

## Overview

The Material Mapping system provides deterministic resolution of nesting descriptions to canonical material codes and SAP codes, with support for customer/LPO-specific overrides.

**Precedence Order:** LPO > PROJECT > CUSTOMER > MATERIAL_MASTER

---

## Material Master (05a)

Canonical material definitions - the default mappingfor all materials.

| Logical Name | Physical Column | Type | Description |
|--------------|-----------------|------|-------------|
| `NESTING_DESCRIPTION` | Nesting Description | Text (Unique) | Raw material name from nesting software |
| `CANONICAL_CODE` | Canonical Code | Text | Internal canonical material code |
| `SAP_CODE` | SAP Code | Text | SAP material code |
| `UOM` | UOM | Dropdown | Unit of measure (Sqm, Lm, Kg, Pcs) |
| `CONVERSION_FACTOR` | Conversion Factor | Number | UOM conversion factor (v1.6.1) |
| `CATEGORY` | Category | Dropdown | Panel, Profile, Consumable, Accessory |
| `IS_ACTIVE` | Is Active | Checkbox | Active flag |
| `CREATED_AT` | Created At | Date | Creation timestamp |

---

## Mapping Override (05b)

Customer or project-specific material overrides.

| Logical Name | Physical Column | Type | Description |
|--------------|-----------------|------|-------------|
| `OVERRIDE_TYPE` | Override Type | Dropdown | CUSTOMER, PROJECT |
| `OVERRIDE_VALUE` | Override Value | Text | Customer name or project name |
| `NESTING_DESCRIPTION` | Nesting Description | Text | Raw material name |
| `CANONICAL_CODE` | Canonical Code | Text | Override canonical code |
| `SAP_CODE` | SAP Code | Text | Override SAP code |
| `PRIORITY` | Priority | Number | Precedence (lower = higher priority) |
| `IS_ACTIVE` | Is Active | Checkbox | Active flag |

---

## LPO Material Brand Map (05c)

LPO-specific material overrides (highest precedence).

| Logical Name | Physical Column | Type | Description |
|--------------|-----------------|------|-------------|
| `LPO_ID` | LPO ID | Text | Linked LPO identifier |
| `NESTING_DESCRIPTION` | Nesting Description | Text | Raw material name |
| `CANONICAL_CODE` | Canonical Code | Text | LPO-specific canonical code |
| `SAP_CODE` | SAP Code | Text | LPO-specific SAP code |
| `BRAND` | Brand | Dropdown | KIMMCO, WTI |
| `IS_ACTIVE` | Is Active | Checkbox | Active flag |

---

## Mapping History (05d)

Audit trail of all material mapping lookups.

| Logical Name | Physical Column | Type | Description |
|--------------|-----------------|------|-------------|
| `MAPPING_ID` | Mapping ID | Text (Auto) | Auto-generated mapping history ID |
| `TIMESTAMP` | Timestamp | Date/Time | Lookup timestamp |
| `NESTING_DESCRIPTION` | Nesting Description | Text | Input material description |
| `CANONICAL_CODE` | Canonical Code | Text | Resolved canonical code (or null) |
| `SAP_CODE` | SAP Code | Text | Resolved SAP code (or null) |
| `DECISION` | Decision | Dropdown | FOUND, OVERRIDE, EXCEPTION |
| `OVERRIDE_SOURCE` | Override Source | Text | LPO_MATERIAL_BRAND_MAP, MAPPING_OVERRIDE, MATERIAL_MASTER |
| `LPO_ID` | LPO ID | Text | Context LPO ID |
| `PROJECT_NAME` | Project Name | Text | Context project name |
| `CUSTOMER_NAME` | Customer Name | Text | Context customer name |
| `CLIENT_REQUEST_ID` | Client Request ID | Text | Idempotency key (v1.6.5) |

### Features

- **Idempotency (v1.6.1)**: Same `client_request_id` returns cached `mapping_id`
- **Audit Trail**: Every lookup logged, no deduplication
- **Context Capture**: Stores LPO/Project/Customer context for decision traceability

---

## Mapping Exception (05e)

Unresolved material mapping cases requiring manual intervention.

| Logical Name | Physical Column | Type | Description |
|--------------|-----------------|------|-------------|
| `EXCEPTION_ID` | Exception ID | Text (Auto) | Auto-generated exception ID |
| `TIMESTAMP` | Timestamp | Date/Time | Exception creation time |
| `NESTING_DESCRIPTION` | Nesting Description | Text | Unmapped material description |
| `LPO_ID` | LPO ID | Text | Context LPO ID |
| `PROJECT_NAME` | Project Name | Text | Context project name |
| `CUSTOMER_NAME` | Customer Name | Text | Context customer name |
| `QUANTITY` | Quantity | Number | Quantity from nesting file |
| `UOM` | UOM | Text | Unit of measure |
| `STATUS` | Status | Dropdown | PENDING, RESOLVED, IGNORED |
| `RESOLVED_BY` | Resolved By | Contact List | User who resolved |
| `RESOLUTION_NOTE` | Resolution Note | Text | Resolution details |
| `CREATED_AT` | Created At | Date | Creation timestamp |

---

## Parsed BOM (06a)

Bill of materials extracted from nesting files.

| Logical Name | Physical Column | Type | Description |
|--------------|-----------------|------|-------------|
| `BOM_ID` | BOM ID | Text (Auto) | Auto-generated BOM line ID |
| `TAG_ID` | Tag ID | Text | Linked tag identifier |
| `NESTING_ID` | Nesting ID | Text | Linked nesting execution ID |
| `LINE_NUMBER` | Line Number | Number | BOM line number |
| `NESTING_DESCRIPTION` | Nesting Description | Text | Raw material name from nesting |
| `CANONICAL_CODE` | Canonical Code | Text | Mapped canonical code |
| `SAP_CODE` | SAP Code | Text | Mapped SAP code |
| `QUANTITY` | Quantity | Number | Quantity required |
| `UOM` | UOM | Dropdown | Unit of measure |
| `MAPPING_DECISION` | Mapping Decision | Dropdown | FOUND, OVERRIDE, EXCEPTION |
| `MAPPING_HISTORY_ID` | Mapping History ID | Text | Link to mapping history record (v1.6.1) |
| `CREATED_AT` | Created At | Date | Creation timestamp |

### Features

- **Auto-Generation (v1.6.0)**: Created during nesting parse
- **Idempotent Mapping (v1.6.1)**: Uses cached mapping_history_id for consistent results
- **Exception Linkage**: Failed mappings create corresponding Mapping Exception records

---

## Related Documentation

- [Material Mapping API](../api/material-mapping.md) - Lookup material codes
- [Nesting Parser API](../api/nesting-parser.md) - BOM generation flow
- [Unit Service](./services.md#unit-service) - UOM conversion logic (v1.6.1)
