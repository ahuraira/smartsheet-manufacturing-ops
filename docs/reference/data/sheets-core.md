---
title: Core & Master Sheets
description: System configuration and master data sheet schemas
keywords: [sheets, config, lpo master, smartsheet]
category: data-reference
version: 1.6.9
---

[Home](../../index.md) > [Data Dictionary](./index.md) > Core Sheets

# Core & Master Sheets

> **Document Type:** Reference | **Version:** 1.6.9 | **Last Updated:** 2026-02-06

Schemas for system configuration and master data sheets.

---

## Config Sheet (00a)

System configuration and sequence counters.

| Logical Name | Physical Column | Type | Description |
|--------------|-----------------|------|-------------|
| `KEY` | Key | Text | Config key (e.g., SEQ_TAG, SEQ_LPO) (v1.6.8) |
| `VALUE` | Value | Text/Number | Config value |
| `DESCRIPTION` | Description | Text | Human-readable description |
| `LAST_UPDATED` | Last Updated | Date | Last modification timestamp |

### Config Keys

| Key | Purpose | Example Value |
|-----|---------|---------------|
| `SEQ_TAG` | Tag ID sequence counter | 1234 |
| `SEQ_LPO` | LPO ID sequence counter (v1.6.8) | 24 |
| `DEFAULT_ADMIN_EMAIL` | Admin email for notifications | admin@company.com |

---

## LPO Master LOG (01)

Master record of all LPOs (Local Purchase Orders).

| Logical Name | Physical Column | Type | Description |
|--------------|-----------------|------|-------------|
| `LPO_ID` | LPO ID | Text | Internal LPO identifier (e.g., LPO-0024) (v1.6.8) |
| `SAP_REFERENCE` | SAP Reference | Text (Unique) | External SAP reference (e.g., PTE-185) |
| `CUSTOMER_NAME` | Customer Name | Text | Customer name |
| `PROJECT_NAME` | Project Name | Text | Project name |
| `BRAND` | Brand | Dropdown | KIMMCO or WTI |
| `PO_QUANTITY_SQM` | PO Quantity (Sqm) | Number | Total PO quantity in sqm |
| `PRICE_PER_SQM` | Price Per Sqm | Number | Unit price per sqm |
| `CUSTOMER_LPO_REF` | Customer LPO Ref | Text | Customer's LPO reference |
| `TERMS_OF_PAYMENT` | Terms of Payment | Dropdown | Payment terms (default: "30 Days Credit") |
| `WASTAGE_PCT` | Wastage % | Number | Wastage percentage (0-20%) |
| `AREA_TYPE` | Area Type | Dropdown | Internal/External (v1.6.7) |
| `REMARKS` | Remarks | Text | User remarks |
| `LPO_STATUS` | LPO Status | Dropdown | DRAFT, ACTIVE, ON_HOLD, CLOSED |
| `FOLDER_URL` | Folder URL | Text (URL) | SharePoint folder link (v1.6.7) |
| `DELIVERED_SQM` | Delivered (Sqm) | Formula | Sum of delivered quantities |
| `COMMITTED_SQM` | Committed (Sqm) | Formula | Sum of allocated quantities |
| `ALLOCATED_QUANTITY` | Allocated Quantity | Formula | Allocated sqm (v1.6.7) |
| `PLANNED_QUANTITY` | Planned Quantity | Formula | Planned sqm (v1.6.7) |
| `UPLOADED_BY` | Uploaded By | Contact List | User who created LPO |
| `CREATED_AT` | Created At | Date | Creation timestamp |
| `UPDATED_AT` | Updated At | Date | Last update timestamp |

### Business Rules

- **SAP Reference Uniqueness**: Enforced via Smartsheet unique column constraint
- **Auto-ID Generation (v1.6.8)**: `LPO_ID` generated using `SEQ_LPO` config key
- **SharePoint Integration (v1.6.7)**: `FOLDER_URL` auto-populated on creation
- **PO Balance**: `Available = PO_QUANTITY - DELIVERED - COMMITTED - PLANNED`

---

## Related Documentation

- [LPO Ingestion API](../api/lpo-ingestion.md) - Create LPO records
- [LPO Service](./services.md#lpo-service) - Centralized LPO operations (v1.6.6)
- [LPOIngestRequest Model](./models.md#lpoingestrequest) - API request schema
