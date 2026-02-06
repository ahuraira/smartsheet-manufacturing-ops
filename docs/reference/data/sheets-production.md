---
title: Production Sheets
description: Tag registry, production planning, and nesting log schemas
keywords: [sheets, tags, planning, nesting, production]
category: data-reference
version: 1.6.9
---

[Home](../../index.md) > [Data Dictionary](./index.md) > Production Sheets

# Production Sheets

> **Document Type:** Reference | **Version:** 1.6.9 | **Last Updated:** 2026-02-06

Schemas for tag registry, production planning, and nesting execution sheets.

---

## Tag Sheet Registry (02)

Master record of all tag sheets.

| Logical Name | Physical Column | Type | Description |
|--------------|-----------------|------|-------------|
| `TAG_ID` | Tag ID | Text (Primary) | Tag identifier (e.g., TAG-20260105-0001) |
| `TAG_NAME` | Tag Name | Text | Display name for the tag |
| `LPO_ID` | LPO ID | Text | Linked LPO identifier (v1.6.8) |
| `CUSTOMER_LPO_REF` | Customer LPO Ref | Text | Customer's LPO reference |
| `LPO_SAP_REFERENCE` | LPO SAP Reference | Text | SAP reference for the LPO |
| `REQUIRED_AREA_M2` | Required Area (m²) | Number | Required area in square meters |
| `REQUESTED_DELIVERY` | Requested Delivery | Date | Delivery date requested |
| `FILES` | Files | Attachment | Tag sheet file(s) |
| `TAG_STATUS` | Tag Status | Dropdown | See TagStatus enum |
| `UPLOADED_BY` | Uploaded By | Contact List | User who uploaded |
| `RECEIVED_THROUGH` | Received Through | Dropdown | Email, Whatsapp, API (v1.1.0) |
| `USER_REMARKS` | User Remarks | Text | User-entered remarks (v1.1.0) |
| `LOCATION` | Location | Text | Location from staging (v1.6.8) |
| `REMARKS` | Remarks | Text | Remarks from staging (v1.6.8) |
| `FILE_HASH` | File Hash | Text | SHA-256 hash for deduplication |
| `CLIENT_REQUEST_ID` | Client Request ID | Text (Unique) | Idempotency key |
| `CREATED_AT` | Created At | Date | Creation timestamp |

---

## Production Planning (03)

Shift-level production schedules.

| Logical Name | Physical Column | Type | Description |
|--------------|-----------------|------|-------------|
| `SCHEDULE_ID` | Schedule ID | Text (Auto) | Auto-generated schedule ID |
| `TAG_ID` | Tag ID | Text | Linked tag identifier |
| `MACHINE_ID` | Machine ID | Dropdown | CUT-001, CUT-002, etc. |
| `PLANNED_DATE` | Planned Date | Date | Production date |
| `SHIFT` | Shift | Dropdown | Morning, Evening |
| `PLANNED_QUANTITY_SQM` | Planned Quantity (Sqm) | Number | Planned quantity |
| `SCHEDULE_STATUS` | Schedule Status | Dropdown | See ScheduleStatus enum |
| `NEXT_ACTION_DEADLINE` | Next Action Deadline | Date/Time | T-1 nesting deadline (18:00 day before) |
| `SCHEDULED_BY` | Scheduled By | Contact List | User who created schedule |
| `NOTES` | Notes | Text | Additional notes |
| `CREATED_AT` | Created At | Date | Creation timestamp |

### Business Rules

- **T-1 Deadline**: Nesting file must be uploaded by 18:00 the day before production
- **Machine Validation**: Machine must be in OPERATIONAL status
- **PO Balance Check**: Validates sufficient quantity available

---

## Nesting Execution Log (04)

Records of nesting file parsing sessions.

| Logical Name | Physical Column | Type | Description |
|--------------|-----------------|------|-------------|
| `NESTING_ID` | Nesting ID | Text (Auto) | Auto-generated nesting exec ID |
| `TAG_ID` | Tag ID | Text | Linked tag identifier |
| `FILE_NAME` | File Name | Text | Original nesting file name |
| `FILE_HASH` | File Hash | Text | SHA-256 hash for deduplication |
| `NESTING_FILE` | Nesting File | Attachment | Uploaded nesting file |
| `BLOB_URL` | Blob URL | Text (URL) | Azure Blob Storage URL (v1.6.7) |
| `PARSE_STATUS` | Parse Status | Dropdown | SUCCESS, PARTIAL, ERROR |
| `EXECUTION_TIME_MS` | Execution Time (ms) | Number | Parsing time in milliseconds |
| `WARNINGS_COUNT` | Warnings Count | Number | Number of parsing warnings |
| `WARNINGS` | Warnings | Text (Long) | JSON array of warning messages |
| `MATERIAL_SPEC` | Material Spec | Text | Extracted material specification |
| `THICKNESS_MM` | Thickness (mm) | Number | Extracted thickness |
| `UTILIZED_SHEETS` | Utilized Sheets | Number | Number of sheets used |
| `REMNANT_AREA_M2` | Remnant Area (m²) | Number | Reusable remnant area |
| `NESTING_WASTE_M2` | Waste (m²) | Number | Waste area |
| `CREATED_AT` | Created At | Date | Execution timestamp |

### Features

- **Idempotency (v2.0.0)**: Duplicate file hash or client_request_id blocked
- **Blob Storage (v1.6.7)**: Files uploaded to Azure Blob for Power Automate
- **BOM Generation (v1.6.0)**: Auto-creates Parsed BOM records
- **Enrichment (v1.6.7)**: Backtracking for brand, area_type, folder URL

---

## Related Documentation

- [Tag Ingestion API](../api/tag-ingestion.md) - Create tag records
- [Nesting Parser API](../api/nesting-parser.md) - Parse nesting files (v2.0.0)
- [Scheduling API](../api/scheduling.md) - Create production schedules
- [TagIngestRequest Model](./models.md#tagingestrequest) - API request schema
