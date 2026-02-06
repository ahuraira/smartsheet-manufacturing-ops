---
title: Python Data Models
description: Pydantic request/response models for API endpoints
keywords: [models, pydantic, schemas, api, requests]
category: data-reference
version: 1.6.9
---

[Home](../../index.md) > [Data Dictionary](./index.md) > Data Models

# Python Data Models

> **Document Type:** Reference | **Version:** 1.6.9 | **Last Updated:** 2026-02-06

All Pydantic data models used for API requests, responses, and internal data structures.

---

## Tag Ingestion Models

### TagIngestRequest

Request payload for tag ingestion API.

```python
class TagIngestRequest(BaseModel):
    client_request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tag_id: Optional[str] = None
    lpo_id: Optional[str] = None
    customer_lpo_ref: Optional[str] = None
    lpo_sap_reference: Optional[str] = None
    required_area_m2: float
    requested_delivery_date: str  # ISO format
    
    # File handling (v1.6.3: Multi-file support)
    files: List[FileAttachment] = Field(default_factory=list)
    # Legacy support (deprecated but handled)
    file_url: Optional[str] = None
    file_content: Optional[str] = None
    original_file_name: Optional[str] = None
    
    # User info
    uploaded_by: str
    tag_name: Optional[str] = None
    
    # Reception info (v1.1.0)
    received_through: str = "API"  # Email, Whatsapp, API
    user_remarks: Optional[str] = None  # User-entered remarks
    
    # v1.6.8: Additional staging fields
    location: Optional[str] = None  # Location from staging sheet
    remarks: Optional[str] = None  # Remarks from staging sheet
    
    metadata: Optional[Dict[str, Any]] = None
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `client_request_id` | UUID string | Auto | Idempotency key |
| `tag_id` | string | No | Custom tag ID |
| `lpo_sap_reference` | string | No | SAP reference |
| `required_area_m2` | float | Yes | Required area |
| `requested_delivery_date` | ISO date | Yes | Delivery date |
| `files` | List[FileAttachment] | Yes* | Tag sheet files (*at least 1) |
| `uploaded_by` | email | Yes | Uploader email |
| `received_through` | string | No | Email/Whatsapp/API |
| `location` | string | No | Location from staging (v1.6.8) |
| `remarks` | string | No | Remarks from staging (v1.6.8) |

### TagIngestResponse

```python
class TagIngestResponse(BaseModel):
    status: str  # UPLOADED, DUPLICATE, BLOCKED
    tag_id: Optional[str] = None
    file_hash: Optional[str] = None
    trace_id: str
    message: Optional[str] = None
    exception_id: Optional[str] = None
```

---

## LPO Ingestion Models (v1.2.0)

### Brand

```python
class Brand(str, Enum):
    KIMMCO = "KIMMCO"
    WTI = "WTI"
```

### TermsOfPayment

```python
class TermsOfPayment(str, Enum):
    DAYS_30 = "30 Days Credit"
    DAYS_60 = "60 Days Credit"
    DAYS_90 = "90 Days Credit"
    IMMEDIATE = "Immediate Payment"
```

### FileType

```python
class FileType(str, Enum):
    LPO = "lpo"             # Original purchase order document
    COSTING = "costing"     # Costing/pricing sheet
    AMENDMENT = "amendment" # PO amendments/revisions
    OTHER = "other"         # Any other document type
```

### FileAttachment

```python
class FileAttachment(BaseModel):
    file_type: FileType = FileType.OTHER
    file_url: Optional[str] = None
    file_content: Optional[str] = None  # Base64 encoded
    file_name: Optional[str] = None
```

> **Note:** Either `file_url` or `file_content` is required per attach ment.

### LPOIngestRequest

```python
class LPOIngestRequest(BaseModel):
    client_request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    
    # Required fields
    sap_reference: str  # External ID (e.g., PTE-185)
    customer_name: str
    project_name: str
    brand: str  # KIMMCO or WTI
    po_quantity_sqm: float = Field(gt=0)
    price_per_sqm: float = Field(gt=0)
    
    # Optional fields
    customer_lpo_ref: Optional[str] = None
    terms_of_payment: Optional[str] = "30 Days Credit"
    wastage_pct: float = Field(default=0.0, ge=0.0, le=20.0)
    area_type: Optional[str] = None  # Internal/External (v1.6.7)
    remarks: Optional[str] = None
    
    # File attachments (multi-file support)
    files: List[FileAttachment] = Field(default_factory=list)
    
    # User info
    uploaded_by: str
```

### LPOIngestResponse

```python
class LPOIngestResponse(BaseModel):
    status: str  # OK, DUPLICATE, BLOCKED, ALREADY_PROCESSED
    lpo_id: Optional[str] = None  # Generated ID (v1.6.8)
    sap_reference: Optional[str] = None
    folder_url: Optional[str] = None  # SharePoint folder (v1.6.7)
    trace_id: str
    message: Optional[str] = None
    exception_id: Optional[str] = None
```

---

## Production Scheduling Models (v1.3.0)

### Shift

```python
class Shift(str, Enum):
    MORNING = "Morning"
    EVENING = "Evening"
```

### ScheduleStatus

```python
class ScheduleStatus(str, Enum):
    PLANNED = "Planned"
    RELEASED_FOR_NESTING = "Released for Nesting"
    NESTING_UPLOADED = "Nesting Uploaded"
    ALLOCATED = "Allocated"
    CANCELLED = "Cancelled"
    DELAYED = "Delayed"
```

### MachineStatus

```python
class MachineStatus(str, Enum):
    OPERATIONAL = "Operational"
    MAINTENANCE = "Maintenance"
```

### ScheduleTagRequest

```python
class ScheduleTagRequest(BaseModel):
    client_request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tag_id: str  # TAG-0001
    machine_id: str  # CUT-001
    planned_date: str  # ISO date
    shift: Shift
    planned_quantity_sqm: float = Field(gt=0)
    scheduled_by: str
    notes: Optional[str] = None
```

### ScheduleTagResponse

```python
class ScheduleTagResponse(BaseModel):
    status: str  # OK, BLOCKED
    schedule_id: Optional[str] = None
    tag_id: Optional[str] = None
    next_action_deadline: Optional[str] = None  # T-1 deadline
    trace_id: str
    message: Optional[str] = None
    exception_id: Optional[str] = None
```

---

## File Upload Models (v1.6.9)

### FileUploadItem

Model for generic file upload to SharePoint.

```python
class FileUploadItem(BaseModel):
    file_name: str
    file_content: str  # Base64-encoded
    subfolder: str  # Subfolder within parent folder
```

---

## Shared Models

### UserActionRecord

Audit log entry for user actions.

```python
class UserActionRecord(BaseModel):
    action_type: ActionType
    user_email: str
    timestamp: str  # ISO 8601
    entity_id: Optional[str] = None  # TAG-0001, LPO-0024, etc.
    details: Optional[str] = None
    trace_id: str
```

### LPORecord (v1.6.6)

LPO data record from LPO Service.

```python
class LPORecord(BaseModel):
    lpo_id: str
    sap_reference: str
    customer_name: str
    project_name: str
    brand: str
    po_quantity_sqm: float
    delivered_sqm: float
    committed_sqm: float
    lpo_status: str
    folder_url: Optional[str] = None  # v1.6.7
    area_type: Optional[str] = None  # v1.6.7
```

### LPOQuantities (v1.6.6)

Calculated PO balance quantities.

```python
class LPOQuantities(BaseModel):
    po_quantity_sqm: float
    delivered_sqm: float
    committed_sqm: float
    planned_sqm: float
    available_sqm: float  # Calculated: PO - delivered - committed - planned
```

---

## Related Documentation

- [Enumerations](./enums.md) - All enum types used in models
- [API Reference](../api/index.md) - Endpoints using these models
- [Shared Services](./services.md) - Business logic modules
