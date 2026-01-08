"""
Shared Data Models for Azure Functions
======================================

This module defines all Pydantic models used for request/response validation
and data transfer across the Ducts Manufacturing Inventory Management System.

Design Principles
-----------------
- **Pydantic v2** for validation and serialization
- **Type hints** for all fields
- **Enums** for constrained values (status, severity, etc.)
- **Optional fields** with sensible defaults
- **Field validation** via Pydantic validators

Model Categories
----------------
Enumerations
    TagStatus, LPOStatus, ExceptionSeverity, ReasonCode, ActionType

Request/Response Models
    TagIngestRequest, TagIngestResponse

Entity Models
    ExceptionRecord, UserActionRecord, LPORecord, TagRecord

Usage Examples
--------------
Creating a request:
    >>> request = TagIngestRequest(
    ...     lpo_sap_reference="SAP-001",
    ...     required_area_m2=50.0,
    ...     requested_delivery_date="2026-02-01",
    ...     uploaded_by="user@company.com"
    ... )

Validating data:
    >>> try:
    ...     request = TagIngestRequest(**invalid_data)
    ... except ValidationError as e:
    ...     print(e.errors())

Serializing to JSON:
    >>> request.model_dump_json()

See Also
--------
- docs/reference/data_dictionary.md : Complete data dictionary
- docs/reference/api_reference.md : API schemas
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from enum import Enum
import uuid


class TagStatus(str, Enum):
    """Tag sheet status values - must match Smartsheet picklist."""
    DRAFT = "Draft"
    VALIDATE = "Validate"
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
    BLOCKED = "BLOCKED"  # When validation fails


class LPOStatus(str, Enum):
    """LPO status values - must match Smartsheet picklist."""
    DRAFT = "Draft"
    PENDING_APPROVAL = "Pending Approval"
    ACTIVE = "Active"
    ON_HOLD = "On Hold"
    CLOSED = "Closed"


class ExceptionSeverity(str, Enum):
    """Exception severity levels."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ExceptionSource(str, Enum):
    """Source of exception."""
    PARSER = "Parser"
    ALLOCATION = "Allocation"
    RECONCILE = "Reconcile"
    MANUAL = "Manual"
    SAP_SYNC = "SAP Sync"
    INGEST = "Ingest"


class ReasonCode(str, Enum):
    """Exception reason codes."""
    DUPLICATE_UPLOAD = "DUPLICATE_UPLOAD"
    MULTI_TAG_NEST = "MULTI_TAG_NEST"
    SHORTAGE = "SHORTAGE"
    OVERCONSUMPTION = "OVERCONSUMPTION"
    PHYSICAL_VARIANCE = "PHYSICAL_VARIANCE"
    SAP_CREATE_FAILED = "SAP_CREATE_FAILED"
    PICK_NEGATIVE = "PICK_NEGATIVE"
    LPO_NOT_FOUND = "LPO_NOT_FOUND"
    LPO_ON_HOLD = "LPO_ON_HOLD"
    INSUFFICIENT_PO_BALANCE = "INSUFFICIENT_PO_BALANCE"
    PARSE_FAILED = "PARSE_FAILED"


class ActionType(str, Enum):
    """User action types for audit log."""
    TAG_UPLOAD = "TAG_UPLOAD"
    TAG_CREATED = "TAG_CREATED"
    TAG_UPDATED = "TAG_UPDATED"
    TAG_RELEASED = "TAG_RELEASED"
    ALLOCATION_CREATED = "ALLOCATION_CREATED"
    CONSUMPTION_SUBMITTED = "CONSUMPTION_SUBMITTED"
    DO_CREATED = "DO_CREATED"
    EXCEPTION_CREATED = "EXCEPTION_CREATED"
    EXCEPTION_RESOLVED = "EXCEPTION_RESOLVED"
    OPERATION_FAILED = "OPERATION_FAILED"


# ============== Request/Response Models ==============

class TagIngestRequest(BaseModel):
    """Request payload for tag ingestion API."""
    client_request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tag_id: Optional[str] = None  # Optional, will be generated if not provided
    lpo_id: Optional[str] = None
    customer_lpo_ref: Optional[str] = None  # Fallback if lpo_id not provided
    lpo_sap_reference: Optional[str] = None  # Another fallback
    required_area_m2: float
    requested_delivery_date: str  # ISO format date
    file_url: Optional[str] = None
    original_file_name: Optional[str] = None
    uploaded_by: str
    tag_name: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class TagIngestResponse(BaseModel):
    """Response payload for tag ingestion API."""
    status: str  # UPLOADED, DUPLICATE, BLOCKED
    tag_id: Optional[str] = None
    file_hash: Optional[str] = None
    trace_id: str
    message: Optional[str] = None
    exception_id: Optional[str] = None


class ExceptionRecord(BaseModel):
    """Exception log record."""
    exception_id: str = Field(default_factory=lambda: f"EX-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}")
    created_at: datetime = Field(default_factory=datetime.now)
    source: ExceptionSource
    related_tag_id: Optional[str] = None
    related_txn_id: Optional[str] = None
    material_code: Optional[str] = None
    quantity: Optional[float] = None
    reason_code: ReasonCode
    severity: ExceptionSeverity
    assigned_to: Optional[str] = None
    status: str = "Open"
    sla_due: Optional[datetime] = None
    attachment_links: Optional[str] = None
    resolution_action: Optional[str] = None
    approvals: Optional[str] = None
    trace_id: Optional[str] = None


class UserActionRecord(BaseModel):
    """User action history record."""
    action_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=datetime.now)
    user_id: str
    action_type: ActionType
    target_table: str
    target_id: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    notes: Optional[str] = None
    trace_id: Optional[str] = None


class LPORecord(BaseModel):
    """LPO master record (read from Smartsheet)."""
    lpo_id: str
    customer_lpo_ref: str
    sap_reference: Optional[str] = None
    customer_name: Optional[str] = None
    project_name: Optional[str] = None
    lpo_status: LPOStatus
    brand: Optional[str] = None
    po_quantity_sqm: float
    delivered_quantity_sqm: float = 0
    total_allocated_cost: float = 0
    current_status: Optional[str] = None
    row_id: Optional[int] = None  # Smartsheet row ID


class TagRecord(BaseModel):
    """Tag sheet record."""
    tag_id: str
    tag_name: Optional[str] = None
    lpo_sap_reference: Optional[str] = None
    required_delivery_date: Optional[str] = None
    estimated_quantity: Optional[float] = None
    status: TagStatus = TagStatus.DRAFT
    file_hash: Optional[str] = None
    client_request_id: Optional[str] = None
    submitted_by: Optional[str] = None
    row_id: Optional[int] = None  # Smartsheet row ID
