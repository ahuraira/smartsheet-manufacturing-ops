# Nesting Parser Integration Specification

> **Document Type:** Specification | **Version:** 2.0.0 | **Last Updated:** 2026-01-22

---

## 📋 Quick Links

| Related Documents |
|-------------------|
| [Nesting Parser Specification](./nesting_parser_speccification.md) - Core parsing logic |
| [Architecture Specification](./architecture_specification.md) - Overall system |
| [LPO Ingestion Architecture](./lpo_ingestion_architecture.md) - LPO flow |
| [Tag Ingestion Architecture](./tag_ingestion_architecture.md) - Tag flow |
| [Flow Architecture](./flow_architecture.md) - Power Automate design |

---

## 1. Executive Summary

This specification defines the **SOTA integration layer** for `fn_parse_nesting` that enables:

1. **Power Automate trigger** from SharePoint file uploads in LPO `/CutSessions/` folders
2. **Tag ID validation** — verifies Tag exists in Tag Registry before processing
3. **LPO ownership validation** — confirms Tag belongs to the correct LPO (extracted from folder path)
4. **Early failure with exceptions** — creates `exception_log` entries for validation failures
5. **Nesting Execution Log** — adds row to Smartsheet with parsed metrics
6. **File attachment** — attaches nesting JSON to both log row and Tag Registry row

**Core principle:** Power Automate is **orchestration only**. All business logic, validation, ID generation, exception creation, and Smartsheet writes happen in the Azure Function.

---

## 2. High-Level Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     SharePoint: File Upload Trigger                          │
│   User uploads nesting.xlsx to /LPOs/{SAP_REF}_{Customer}/CutSessions/       │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                   Power Automate: TRG_Nesting_Upload                         │
│ 1. Extract file metadata (path, URL, name, created_by)                       │
│ 2. Extract SAP Reference from folder path                                    │
│ 3. Generate client_request_id (UUID)                                         │
│ 4. Call POST /api/nesting/parse with canonical payload                       │
│ 5. Handle response → update Smartsheet / notify on exception                 │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                   Azure Function: fn_parse_nesting (Authoritative)           │
│                                                                              │
│ PHASE 1: IDEMPOTENCY & DEDUPLICATION                                         │
│   • Check client_request_id → return prior response if exists                │
│   • Compute file_hash (SHA256) → check for duplicate                         │
│                                                                              │
│ PHASE 2: PARSE FILE                                                          │
│   • Load Excel workbook, extract Tag ID from PROJECT_REFERENCE               │
│   • Parse all sheets using anchor-based strategy                             │
│                                                                              │
│ PHASE 3: VALIDATION (Fail-Fast)                                              │
│   • Validate: Tag ID exists in TAG_REGISTRY                                  │
│   • Validate: Tag belongs to provided SAP LPO Reference                      │
│   • If validation fails → create exception_log → return 422                  │
│                                                                              │
│ PHASE 4: LOGGING & ATTACHMENTS                                               │
│   • Add row to NESTING_LOG (04 Nesting Execution Log)                        │
│   • Attach file URL to nesting log row                                       │
│   • Attach file URL to Tag Registry row                                      │
│   • Write user_action_history                                                │
│   • Update Tag Registry status to NESTING_COMPLETE                           │
│                                                                              │
│ PHASE 5: RETURN SUCCESS                                                      │
│   • Return parsed data with nesting_row_id, attachments, trace_id            │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. API Contract

### Endpoint: `POST /api/nesting/parse`

#### Request Payload

```json
{
  "client_request_id": "550e8400-e29b-41d4-a716-446655440000",
  "file_content_base64": "UEsDBBQAAAAIAL...",
  "filename": "TAG-001_CutExpert_20260122.xlsx",
  "file_url": "https://tenant.sharepoint.com/sites/Ducts/Shared Documents/LPOs/PTE-185_Acme_Corp/CutSessions/nesting.xlsx",
  "file_path": "/sites/Ducts/Shared Documents/LPOs/PTE-185_Acme_Corp/CutSessions/nesting.xlsx",
  "sap_lpo_reference": "PTE-185",
  "uploaded_by": "supervisor@company.com"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `client_request_id` | UUID | **Yes** | Idempotency key (caller-generated) |
| `file_content_base64` | string | **Yes** | Base64-encoded Excel file |
| `filename` | string | **Yes** | Original filename |
| `file_url` | string | **Yes** | SharePoint URL for attachment |
| `file_path` | string | No | SharePoint relative path (fallback for LPO extraction) |
| `sap_lpo_reference` | string | **Yes** | SAP Reference extracted from folder path |
| `uploaded_by` | string | **Yes** | User who uploaded the file |

#### Response: Success (200 OK)

```json
{
  "status": "SUCCESS",
  "request_id": "trace-20260122-001",
  "tag_id": "TAG-001",
  "nest_session_id": "NEST-20260122-001",
  "nesting_row_id": 1234567890123456,
  "tag_row_id": 9876543210987654,
  "file_hash": "a3f2b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3",
  "attachments": [
    {"target": "NESTING_LOG", "row_id": 1234567890123456, "name": "nesting.xlsx"},
    {"target": "TAG_REGISTRY", "row_id": 9876543210987654, "name": "nesting.xlsx"}
  ],
  "data": {
    "meta_data": { "project_ref_id": "TAG-001", "validation_status": "OK" },
    "raw_material_panel": { "material_spec_name": "GI 0.9mm", "thickness_mm": 0.9 },
    "...": "full NestingExecutionRecord"
  },
  "expected_consumption_m2": 45.0,
  "wastage_percentage": 8.5,
  "trace_id": "trace-20260122-001"
}
```

#### Response: Duplicate (409 Conflict)

```json
{
  "status": "DUPLICATE",
  "request_id": "...",
  "existing_nest_session_id": "NEST-20260121-005",
  "file_hash": "...",
  "message": "File with identical hash already processed for this LPO",
  "trace_id": "..."
}
```

#### Response: Validation Error (422 Unprocessable Entity)

```json
{
  "status": "VALIDATION_ERROR",
  "request_id": "...",
  "error_code": "TAG_NOT_FOUND",
  "error_message": "Tag ID 'TAG-999' does not exist in Tag Registry",
  "exception_id": "EX-20260122-0015",
  "trace_id": "..."
}
```

#### Response: Parse Error (422 Unprocessable Entity)

```json
{
  "status": "PARSE_ERROR",
  "request_id": "...",
  "error_code": "CRITICAL_FIELD_MISSING",
  "error_message": "Could not extract Tag ID from PROJECT_REFERENCE",
  "exception_id": "EX-20260122-0016",
  "parse_warnings": ["Flanges sheet not available", "..."],
  "trace_id": "..."
}
```

---

## 4. Validation Rules (Fail-Fast)

The function performs validations **in order** and fails at the first error:

### 4.1 Idempotency Check

```python
# Check if client_request_id was already processed
if client_request_id in processed_requests:
    return previous_response  # HTTP 200 with same result
```

### 4.2 File Hash Deduplication

```python
file_hash = sha256(file_content)
existing = find_nesting_by_hash(sap_lpo_reference, file_hash)
if existing:
    return 409 DUPLICATE with existing_nest_session_id
```

### 4.3 Tag ID Validation

```python
# After parsing, Tag ID is extracted from PROJECT_REFERENCE
tag_id = parsed_record.meta_data.project_ref_id

# Validate Tag exists
tag_row = smartsheet.find_rows(TAG_REGISTRY, "TAG_ID", tag_id)
if not tag_row:
    exception = create_exception(
        source="fn_parse_nesting",
        reason_code="TAG_NOT_FOUND",
        severity="HIGH",
        related_tag_id=tag_id,
        message=f"Tag ID '{tag_id}' not found in Tag Registry"
    )
    return 422 VALIDATION_ERROR with exception_id
```

### 4.4 LPO Ownership Validation

```python
# Validate Tag belongs to the provided LPO
tag_lpo_ref = tag_row["LPO SAP Reference Link"]
if tag_lpo_ref != sap_lpo_reference:
    exception = create_exception(
        source="fn_parse_nesting",
        reason_code="LPO_MISMATCH",
        severity="HIGH",
        related_tag_id=tag_id,
        message=f"Tag '{tag_id}' belongs to LPO '{tag_lpo_ref}', "
                f"but file was uploaded to LPO '{sap_lpo_reference}'"
    )
    return 422 VALIDATION_ERROR with exception_id
```

---

## 5. Exception Creation (Azure Function is Authoritative)

> [!IMPORTANT]
> **All exceptions are created BY the Azure Function at the moment of failure.**
> Power Automate does NOT create exceptions — it only routes notifications.

### 5.1 Exception Creation Flow

```python
# fn_parse_nesting/__init__.py - Exception creation pattern

def main(req: func.HttpRequest) -> func.HttpResponse:
    try:
        # ... validation logic ...
        
        if not tag_exists:
            # IMMEDIATELY create exception in ledger
            exception = create_exception(
                source=ExceptionSource.PARSER,
                reason_code=ReasonCode.TAG_NOT_FOUND,
                severity=ExceptionSeverity.HIGH,
                related_tag_id=tag_id,
                sap_lpo_reference=sap_lpo_reference,
                message=f"Tag ID '{tag_id}' not found in Tag Registry",
                file_url=file_url,
                uploaded_by=uploaded_by,
                trace_id=trace_id
            )
            
            # Log user action for audit
            log_user_action(
                action_type=ActionType.EXCEPTION_CREATED,
                user_id=uploaded_by,
                target_id=exception.exception_id,
                trace_id=trace_id
            )
            
            # Return error with exception_id
            return func.HttpResponse(
                json.dumps({
                    "status": "VALIDATION_ERROR",
                    "error_code": "TAG_NOT_FOUND",
                    "exception_id": exception.exception_id,
                    "trace_id": trace_id
                }),
                status_code=422
            )
```

### 5.2 Exception Catalog (Nesting Integration)

| Reason Code | Severity | HTTP | Trigger | Assigned To |
|-------------|----------|------|---------|-------------|
| `DUPLICATE_NESTING_FILE` | LOW | 409 | Same file hash for LPO | Supervisor |
| `TAG_NOT_FOUND` | HIGH | 422 | Tag ID from file not in registry | Production Manager |
| `LPO_MISMATCH` | HIGH | 422 | Tag belongs to different LPO | Production Manager |
| `PARSE_FAILED_CRITICAL` | CRITICAL | 422 | Cannot extract Tag ID | IT Support |
| `MULTI_TAG_NEST` | HIGH | 422 | Multiple Tag IDs detected | Supervisor |

### 5.3 Exception Record Structure

All exceptions include:
- `exception_id` - Generated ID (e.g., `EX-20260122-0015`)
- `trace_id` - Correlation ID for debugging
- `file_url` - Attachment link to source file
- `sla_due` - Calculated based on severity
- `assigned_to` - From config (see Section 5A)
- `status` - Always starts as `Open`

---

## 6. Smartsheet Integration

### 6.1 Nesting Execution Log Write

Column mapping for `NESTING_LOG` (04 Nesting Execution Log):

| Column | Source |
|--------|--------|
| `NEST_SESSION_ID` | Generated: `NEST-{YYYYMMDD}-{SEQ}` |
| `TAG_SHEET_ID` | `parsed_record.meta_data.project_ref_id` |
| `TIMESTAMP` | Current UTC datetime |
| `BRAND` | From LPO lookup or parsed data |
| `SHEETS_CONSUMED_VIRTUAL` | `raw_material_panel.inventory_impact.utilized_sheets_count` |
| `EXPECTED_CONSUMPTION_M2` | Computed total consumption |
| `WASTAGE_PERCENTAGE` | `efficiency_metrics.nesting_waste_pct` |
| `PLANNED_DATE` | From Tag Registry lookup |
| `REMNANT_ID_GENERATED` | JSON array of remnant IDs if generated |
| `FILLER_IDS_GENERATED` | JSON array of filler IDs if generated |
| `FILE_HASH` | SHA256 of uploaded file |
| `CLIENT_REQUEST_ID` | From request |

### 6.2 Tag Registry Update

On successful parse:
1. Update `TAG_REGISTRY` row status to `Nesting Complete`
2. Update `SHEETS_USED` column with parsed value
3. Update `WASTAGE_NESTED` column with parsed value

### 6.3 File Attachments

```python
# Attach to Nesting Log row
smartsheet.attach_url_to_row(
    sheet_ref="NESTING_LOG",
    row_id=nesting_row_id,
    url=file_url,
    name=filename,
    description="CutExpert nesting export"
)

# Attach to Tag Registry row
smartsheet.attach_url_to_row(
    sheet_ref="TAG_REGISTRY",
    row_id=tag_row_id,
    url=file_url,
    name=filename,
    description="Nesting file for this tag"
)
```

---

## 7. Power Automate Flow Specification

### Flow: `TRG_Nesting_Upload`

**Trigger:** SharePoint → When a file is created in folder

**Filter:** Path matches `/LPOs/*/CutSessions/*` and file extension is `.xls` or `.xlsx`

**Steps:**

1. **Get File Content** — Read file as base64
2. **Extract SAP Reference** — Parse folder path
   ```
   Path: /sites/Ducts/Shared Documents/LPOs/PTE-185_Acme_Corp/CutSessions/file.xlsx
   Extract: PTE-185 (everything before first underscore in folder name)
   ```
3. **Generate client_request_id** — `guid()`
4. **HTTP POST to Azure Function**
   ```json
   {
     "client_request_id": "@{guid()}",
     "file_content_base64": "@{base64(body('Get_file_content'))}",
     "filename": "@{triggerOutputs()?['body/Name']}",
     "file_url": "@{triggerOutputs()?['body/{Link}']}",
     "file_path": "@{triggerOutputs()?['body/{Path}']}",
     "sap_lpo_reference": "@{variables('SapReference')}",
     "uploaded_by": "@{triggerOutputs()?['body/Author/Email']}"
   }
   ```
5. **Condition: Response Status**
   - **200 OK** → Log success, optionally notify supervisor
   - **409 Duplicate** → Update Smartsheet with message, no action needed
   - **422 Error** → Call `UTL_Send_Notification` with exception details
     (Exception already created by Function — just send notification)

---

## 5A. Configuration Management

> [!IMPORTANT]
> **All customizable parameters must be in config files, not hardcoded.**

### 5A.1 Configuration Sources

| Source | Location | Purpose |
|--------|----------|--------|
| `nesting_config.json` | `functions/fn_parse_nesting/` | Nesting-specific settings |
| `00a Config` sheet | Smartsheet | Runtime-adjustable parameters |
| Environment variables | Azure Function App Settings | Secrets and deployment-specific values |

### 5A.2 nesting_config.json

```json
{
  "version": "1.0.0",
  "validation": {
    "require_tag_validation": true,
    "require_lpo_validation": true,
    "allow_multi_tag_nesting": false,
    "tag_id_regex": "^TAG-.*"
  },
  "deduplication": {
    "check_file_hash": true,
    "check_client_request_id": true
  },
  "exception_assignment": {
    "TAG_NOT_FOUND": "production.manager@company.com",
    "LPO_MISMATCH": "production.manager@company.com",
    "PARSE_FAILED_CRITICAL": "it.support@company.com",
    "MULTI_TAG_NEST": "supervisor@company.com",
    "DUPLICATE_NESTING_FILE": "supervisor@company.com"
  },
  "sla_hours": {
    "CRITICAL": 4,
    "HIGH": 24,
    "MEDIUM": 48,
    "LOW": 72
  },
  "smartsheet": {
    "update_tag_status_on_success": true,
    "attach_file_to_tag": true,
    "attach_file_to_nesting_log": true
  },
  "id_generation": {
    "nest_session_id_prefix": "NEST",
    "date_format": "%Y%m%d"
  }
}
```

### 5A.3 Smartsheet Config Sheet (Runtime Override)

The `00a Config` sheet allows runtime parameter changes without redeployment:

| config_key | config_value | effective_from | changed_by |
|------------|--------------|----------------|------------|
| `nesting.allow_multi_tag` | `false` | 2026-01-22 | admin@company.com |
| `nesting.sla_hours.HIGH` | `24` | 2026-01-01 | admin@company.com |
| `nesting.require_tag_validation` | `true` | 2026-01-01 | admin@company.com |

### 5A.4 Environment Variables

| Variable | Purpose | Example |
|----------|---------|--------|
| `SMARTSHEET_ACCESS_TOKEN` | API authentication | (in Key Vault) |
| `STORAGE_BACKEND` | Repository selection | `smartsheet` or `azure_sql` |
| `SHAREPOINT_BASE_URL` | File attachment base URL | `https://tenant.sharepoint.com/sites/Ducts` |
| `LOG_LEVEL` | Logging verbosity | `INFO` |

### 5A.5 Config Loading Pattern

```python
# fn_parse_nesting/config.py
import json
import os
from pathlib import Path
from functools import lru_cache

@lru_cache(maxsize=1)
def load_nesting_config() -> dict:
    """Load nesting config with caching."""
    config_path = Path(__file__).parent / "nesting_config.json"
    with open(config_path) as f:
        config = json.load(f)
    
    # Override with Smartsheet config if available
    try:
        from shared import get_smartsheet_client
        client = get_smartsheet_client()
        runtime_config = client.get_config_values(prefix="nesting.")
        config = deep_merge(config, runtime_config)
    except Exception:
        pass  # Use file config if Smartsheet unavailable
    
    return config

def get_exception_assignee(reason_code: str) -> str:
    """Get assignee for exception based on config."""
    config = load_nesting_config()
    return config["exception_assignment"].get(
        reason_code, 
        config["exception_assignment"].get("default", "admin@company.com")
    )
```

## 8. Security & Access Control

- Function protected by Azure AD or Function Key (API key in headers)
- Power Automate uses service principal with minimal permissions
- Smartsheet API token stored in Azure Key Vault
- All requests include `uploaded_by` for audit trail
- Rate limiting enforced via SmartsheetClient (290 req/min)

---

## 9. Observability & Monitoring

### Logging

All operations log with structured fields:
```python
logger.info(f"Nesting parsed", extra={
    "trace_id": trace_id,
    "client_request_id": client_request_id,
    "tag_id": tag_id,
    "sap_lpo_reference": sap_lpo_reference,
    "file_hash": file_hash,
    "processing_time_ms": elapsed
})
```

### Metrics & Alerts

| Metric | Alert Threshold |
|--------|-----------------|
| Parse failures (CRITICAL) | > 3 per hour |
| TAG_NOT_FOUND exceptions | > 5 per day |
| LPO_MISMATCH exceptions | > 2 per day |
| Average parse time | > 5 seconds |

---

## 10. Acceptance Tests

| # | Scenario | Expected Result |
|---|----------|-----------------|
| 1 | **Happy path** | Parse succeeds, nesting log row created, attachments added, 200 returned |
| 2 | **Duplicate file** | Same file uploaded twice → 409 with existing session ID |
| 3 | **Tag not found** | File references TAG-999 not in registry → 422 + exception created |
| 4 | **LPO mismatch** | Tag belongs to PTE-100 but uploaded to PTE-200 folder → 422 + exception |
| 5 | **Idempotency** | Same client_request_id sent twice → identical 200 response both times |
| 6 | **Missing Tag ID in file** | PROJECT_REFERENCE empty → 422 PARSE_FAILED_CRITICAL |
| 7 | **Backward compatibility** | Request without sap_lpo_reference → skips validation, parses only |

---

## 11. Implementation Checklist

- [ ] Create `validation.py` module with `validate_tag_exists()` and `validate_tag_lpo_ownership()`
- [ ] Create `nesting_logger.py` module with `log_nesting_execution()` and attachment logic
- [ ] Modify `fn_parse_nesting/__init__.py` to add validation and logging phases
- [ ] Add exception creation capability (import from shared audit module)
- [ ] Add file hash computation and deduplication check
- [ ] Add client_request_id idempotency check
- [ ] Update response model to include `nesting_row_id`, `attachments`, `exception_id`
- [ ] Create unit tests for validation module
- [ ] Create integration tests for full flow with mock Smartsheet
- [ ] Document Power Automate flow setup steps

---

## 12. Dependencies

### New Request Fields

| Field | Provided By |
|-------|-------------|
| `client_request_id` | Power Automate (guid()) |
| `file_url` | SharePoint trigger metadata |
| `sap_lpo_reference` | Power Automate (extracted from path) |
| `uploaded_by` | SharePoint trigger metadata (Author/Email) |

### Smartsheet Sheets Used

| Sheet | Purpose |
|-------|---------|
| `TAG_REGISTRY` | Validate tag exists, get LPO reference, attach file |
| `NESTING_LOG` | Write parsed nesting session, attach file |
| `EXCEPTION_LOG` | Create exceptions on validation failure |
| `USER_ACTION_LOG` | Audit all operations |

---

## 13. Migration Readiness: Repository Pattern

> **Critical for high-stakes project:** This section ensures **one-click migration** from Smartsheet to Azure SQL/Dataverse.

### 13.1 The Problem

Currently, functions call `SmartsheetClient` directly:

```python
# Current (tightly coupled)
from shared import get_smartsheet_client
client = get_smartsheet_client()
tag_rows = client.find_rows("TAG_REGISTRY", "TAG_ID", tag_id)
client.add_row("NESTING_LOG", row_data)
```

This **leaks storage implementation** into business logic, making migration require changes across all functions.

### 13.2 The Solution: Repository Pattern

Introduce an **abstraction layer** that decouples business logic from storage:

```
┌─────────────────────────────────────────────────────────────────┐
│                     Business Logic (Functions)                   │
│   fn_parse_nesting, fn_ingest_tag, fn_schedule_tag, etc.        │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼ (uses interface)
┌─────────────────────────────────────────────────────────────────┐
│                     Repository Interface                         │
│   TagRepository, NestingRepository, LPORepository, etc.         │
│   - find_by_id(), create(), update(), find_by_lpo()             │
└─────────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│ Smartsheet Impl │  │  Azure SQL Impl │  │ In-Memory Impl  │
│ (Current)       │  │  (Future)       │  │ (Testing)       │
└─────────────────┘  └─────────────────┘  └─────────────────┘
```

### 13.3 Repository Interface Design

```python
# shared/repositories/base.py
from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any
from pydantic import BaseModel

class TagEntity(BaseModel):
    """Domain entity - storage-agnostic."""
    row_id: Optional[int] = None  # Smartsheet row_id or SQL primary key
    tag_id: str
    tag_name: str
    lpo_sap_reference: str
    status: str
    sheets_used: Optional[int] = None
    wastage_nested: Optional[float] = None
    # ... all fields from TAG_REGISTRY

class NestingEntity(BaseModel):
    """Domain entity for nesting execution."""
    row_id: Optional[int] = None
    nest_session_id: str
    tag_id: str
    timestamp: datetime
    sheets_consumed_virtual: int
    expected_consumption_m2: float
    wastage_percentage: float
    file_hash: str
    client_request_id: str

class TagRepository(ABC):
    """Abstract repository for Tag operations."""
    
    @abstractmethod
    def find_by_id(self, tag_id: str) -> Optional[TagEntity]:
        """Find tag by TAG_ID."""
        pass
    
    @abstractmethod
    def find_by_lpo(self, lpo_sap_reference: str) -> List[TagEntity]:
        """Find all tags for an LPO."""
        pass
    
    @abstractmethod
    def update(self, tag: TagEntity) -> TagEntity:
        """Update tag record."""
        pass
    
    @abstractmethod
    def attach_file(self, tag_id: str, file_url: str, name: str) -> Dict[str, Any]:
        """Attach file to tag record."""
        pass

class NestingRepository(ABC):
    """Abstract repository for Nesting operations."""
    
    @abstractmethod
    def create(self, nesting: NestingEntity) -> NestingEntity:
        """Create nesting execution record."""
        pass
    
    @abstractmethod
    def find_by_hash(self, lpo_ref: str, file_hash: str) -> Optional[NestingEntity]:
        """Find nesting by file hash (for deduplication)."""
        pass
    
    @abstractmethod
    def attach_file(self, nest_session_id: str, file_url: str, name: str) -> Dict[str, Any]:
        """Attach file to nesting record."""
        pass
```

### 13.4 Smartsheet Implementation

```python
# shared/repositories/smartsheet_impl.py
from .base import TagRepository, TagEntity, NestingRepository, NestingEntity
from ..smartsheet_client import SmartsheetClient
from ..logical_names import Sheet, Column

class SmartsheetTagRepository(TagRepository):
    """Smartsheet implementation of TagRepository."""
    
    def __init__(self, client: SmartsheetClient):
        self._client = client
    
    def find_by_id(self, tag_id: str) -> Optional[TagEntity]:
        rows = self._client.find_rows(Sheet.TAG_REGISTRY, Column.TAG_ID, tag_id)
        if not rows:
            return None
        return self._to_entity(rows[0])
    
    def _to_entity(self, row: dict) -> TagEntity:
        """Map Smartsheet row to domain entity."""
        return TagEntity(
            row_id=row.get("id"),
            tag_id=row.get("Tag ID"),
            tag_name=row.get("Tag Sheet Name/ Rev"),
            lpo_sap_reference=row.get("LPO SAP Reference Link"),
            status=row.get("Status"),
            sheets_used=row.get("Sheets Used"),
            wastage_nested=row.get("Wastage Nested"),
        )
```

### 13.5 Azure SQL Implementation (Future)

```python
# shared/repositories/sql_impl.py
from sqlalchemy import select
from .base import TagRepository, TagEntity

class SQLTagRepository(TagRepository):
    """Azure SQL implementation of TagRepository."""
    
    def __init__(self, session):
        self._session = session
    
    def find_by_id(self, tag_id: str) -> Optional[TagEntity]:
        stmt = select(TagTable).where(TagTable.tag_id == tag_id)
        row = self._session.execute(stmt).first()
        if not row:
            return None
        return TagEntity.model_validate(row)
```

### 13.6 Factory for Environment-Based Selection

```python
# shared/repositories/__init__.py
import os
from .base import TagRepository, NestingRepository
from .smartsheet_impl import SmartsheetTagRepository, SmartsheetNestingRepository

def get_tag_repository() -> TagRepository:
    """Factory: returns appropriate repository based on environment."""
    storage_backend = os.environ.get("STORAGE_BACKEND", "smartsheet")
    
    if storage_backend == "smartsheet":
        from ..smartsheet_client import get_smartsheet_client
        return SmartsheetTagRepository(get_smartsheet_client())
    elif storage_backend == "azure_sql":
        from .sql_impl import SQLTagRepository, get_db_session
        return SQLTagRepository(get_db_session())
    else:
        raise ValueError(f"Unknown storage backend: {storage_backend}")
```

### 13.7 Usage in Functions (Migration-Ready)

```python
# fn_parse_nesting/validation.py
from shared.repositories import get_tag_repository, get_nesting_repository

def validate_tag_exists(tag_id: str) -> ValidationResult:
    repo = get_tag_repository()
    tag = repo.find_by_id(tag_id)
    
    if not tag:
        return ValidationResult(
            is_valid=False,
            error_code="TAG_NOT_FOUND",
            error_message=f"Tag ID '{tag_id}' not found"
        )
    
    return ValidationResult(is_valid=True, tag_entity=tag)
```

### 13.8 Migration Checklist

| Step | Description | Effort |
|------|-------------|--------|
| 1 | Create `shared/repositories/base.py` with abstract interfaces | 2 hours |
| 2 | Create `shared/repositories/smartsheet_impl.py` | 4 hours |
| 3 | Refactor functions to use repository interfaces | 8 hours |
| 4 | Create `shared/repositories/sql_impl.py` | 4 hours |
| 5 | Add `STORAGE_BACKEND` env var toggle | 1 hour |
| 6 | Test both backends | 4 hours |
| **Total** | | **~23 hours** |

### 13.9 Benefits

1. **One-click migration**: Change `STORAGE_BACKEND=azure_sql` and deploy
2. **Testing**: Use in-memory repository for unit tests (no Smartsheet mocking needed)
3. **Gradual migration**: Run Smartsheet and SQL in parallel during transition
4. **No business logic changes**: Functions remain unchanged during migration

---

## 14. Implementation Phases

### Phase 1: Immediate (Current Sprint)
- Implement nesting parser integration **without** repository layer
- Use SmartsheetClient directly (as currently designed)
- Document all direct Smartsheet calls for future refactoring

### Phase 2: Repository Abstraction (Next Sprint)
- Create repository interfaces and Smartsheet implementations
- Refactor existing functions to use repositories
- Create in-memory repository for testing

### Phase 3: SQL Migration (Future)
- Create Azure SQL schema matching Smartsheet structure
- Implement SQL repositories
- Toggle `STORAGE_BACKEND` and validate

---

**End of Specification**
