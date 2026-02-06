# Enumerations

> **Document Type:** Reference | **Version:** 1.6.9 | **Last Updated:** 2026-02-06

All enumerated types used in the Ducts Manufacturing Inventory Management System.

---

## Tag & LPO Status

### TagStatus

Status values for tag sheet records.

```python
class TagStatus(str, Enum):
    DRAFT = "Draft"
    VALIDATE = "Validate"  # Default for new tags (v1.1.0+)
    SENT_TO_NESTING = "Sent to Nesting"
    NESTING_COMPLETE = "Nesting Complete"
    PLANNED_QUEUED = "Planned Queued"
    WIP = "WIP"
    COMPLETE = "Complete"
    PARTIAL_DISPATCH = "Partial Dispatch"
    DISPATCHED = "Dispatched"
    CLOSED = "Closed"
    REVISION_PENDING = "Revision Pending"
    HOLD = "Hold"
    CANCELLED = "Cancelled"
    BLOCKED = "BLOCKED"
```

> **Note (v1.1.0):** Smartsheet forms default to `Draft`. When processed by `fn_ingest_tag`, the status is set to `Validate` indicating the tag has passed validation and is ready for nesting.

### LPOStatus

Status values for LPO records.

```python
class LPOStatus(str, Enum):
    DRAFT = "Draft"
    PENDING_APPROVAL = "Pending Approval"
    ACTIVE = "Active"
    ON_HOLD = "On Hold"
    CLOSED = "Closed"
```

---

## Exception Management

### ExceptionSeverity

Severity levels for exceptions (determines SLA).

```python
class ExceptionSeverity(str, Enum):
    LOW = "LOW"        # SLA: 72 hours
    MEDIUM = "MEDIUM"  # SLA: 48 hours
    HIGH = "HIGH"      # SLA: 24 hours
    CRITICAL = "CRITICAL"  # SLA: 4 hours
```

### ReasonCode

Exception reason codes.

```python
class ReasonCode(str, Enum):
    # Tag & File Processing
    DUPLICATE_UPLOAD = "DUPLICATE_UPLOAD"
    MULTI_TAG_NEST = "MULTI_TAG_NEST"
    PARSE_FAILED = "PARSE_FAILED"
    
    # LPO-specific (v1.2.0)
    DUPLICATE_SAP_REF = "DUPLICATE_SAP_REF"
    SAP_REF_NOT_FOUND = "SAP_REF_NOT_FOUND"
    LPO_INVALID_DATA = "LPO_INVALID_DATA"
    PO_QUANTITY_CONFLICT = "PO_QUANTITY_CONFLICT"
    DUPLICATE_LPO_FILE = "DUPLICATE_LPO_FILE"
    LPO_NOT_FOUND = "LPO_NOT_FOUND"
    LPO_ON_HOLD = "LPO_ON_HOLD"
    INSUFFICIENT_PO_BALANCE = "INSUFFICIENT_PO_BALANCE"
    
    # Scheduling-specific (v1.3.0)
    MACHINE_NOT_FOUND = "MACHINE_NOT_FOUND"
    MACHINE_MAINTENANCE = "MACHINE_MAINTENANCE"
    TAG_NOT_FOUND = "TAG_NOT_FOUND  "
    TAG_INVALID_STATUS = "TAG_INVALID_STATUS"
    DUPLICATE_SCHEDULE = "DUPLICATE_SCHEDULE"
    CAPACITY_WARNING = "CAPACITY_WARNING"
    T1_NESTING_DELAY = "T1_NESTING_DELAY"
    
    # Inventory & SAP
    SHORTAGE = "SHORTAGE"
    OVERCONSUMPTION = "OVERCONSUMPTION"
    PHYSICAL_VARIANCE = "PHYSICAL_VARIANCE"
    SAP_CREATE_FAILED = "SAP_CREATE_FAILED"
    PICK_NEGATIVE = "PICK_NEGATIVE"
```

### ExceptionSource

Source of exception creation.

```python
class ExceptionSource(str, Enum):
    PARSER = "Parser"
    ALLOCATION = "Allocation"
    RECONCILE = "Reconcile"
    MANUAL = "Manual"
    SAP_SYNC = "SAP Sync"
    INGEST = "Ingest"
    SCHEDULE = "Schedule"  # v1.6.6
```

---

## User Action Types

### ActionType

User action types for audit log.

```python
class ActionType(str, Enum):
    TAG_UPLOAD = "TAG_UPLOAD"
    TAG_CREATED = "TAG_CREATED"
    TAG_UPDATED = "TAG_UPDATED"
    TAG_RELEASED = "TAG_RELEASED"
    LPO_CREATED = "LPO_CREATED"  # v1.2.0
    LPO_UPDATED = "LPO_UPDATED"  # v1.2.0
    SCHEDULE_CREATED = "SCHEDULE_CREATED"  # v1.3.0
    SCHEDULE_UPDATED = "SCHEDULE_UPDATED"  # v1.3.0
    SCHEDULE_CANCELLED = "SCHEDULE_CANCELLED"  # v1.3.0
    ALLOCATION_CREATED = "ALLOCATION_CREATED"
    CONSUMPTION_SUBMITTED = "CONSUMPTION_SUBMITTED"
    DO_CREATED = "DO_CREATED"
    EXCEPTION_CREATED = "EXCEPTION_CREATED"
    EXCEPTION_RESOLVED = "EXCEPTION_RESOLVED"
    OPERATION_FAILED = "OPERATION_FAILED"
```

---

## System Configuration

### ConfigKey (v1.6.8)

Configuration keys for system settings stored in Config sheet.

```python
class ConfigKey(str, Enum):
    SEQ_TAG = "SEQ_TAG"        # Tag ID sequence counter
    SEQ_LPO = "SEQ_LPO"        # LPO ID sequence counter (v1.6.8)
    DEFAULT_ADMIN_EMAIL = "DEFAULT_ADMIN_EMAIL"
```

---

## Related Documentation

- [Data Models](./models.md) - Pydantic models using these enums
- [Exception Log Schema](./sheets-governance.md#exception-log) - Exception sheet structure
- [Config Sheet Schema](./sheets-core.md#config-sheet) - Config key storage
