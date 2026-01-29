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
from pydantic import BaseModel, Field, model_validator
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
    LPO_MISMATCH = "LPO_MISMATCH"
    INSUFFICIENT_PO_BALANCE = "INSUFFICIENT_PO_BALANCE"
    PARSE_FAILED = "PARSE_FAILED"
    # LPO-specific reason codes
    DUPLICATE_SAP_REF = "DUPLICATE_SAP_REF"
    SAP_REF_NOT_FOUND = "SAP_REF_NOT_FOUND"
    LPO_INVALID_DATA = "LPO_INVALID_DATA"
    PO_QUANTITY_CONFLICT = "PO_QUANTITY_CONFLICT"
    DUPLICATE_LPO_FILE = "DUPLICATE_LPO_FILE"
    # Scheduling-specific reason codes
    MACHINE_NOT_FOUND = "MACHINE_NOT_FOUND"
    MACHINE_MAINTENANCE = "MACHINE_MAINTENANCE"
    CAPACITY_WARNING = "CAPACITY_WARNING"
    DUPLICATE_SCHEDULE = "DUPLICATE_SCHEDULE"
    T1_NESTING_DELAY = "T1_NESTING_DELAY"
    PLANNED_MISMATCH = "PLANNED_MISMATCH"
    TAG_NOT_FOUND = "TAG_NOT_FOUND"
    TAG_INVALID_STATUS = "TAG_INVALID_STATUS"


class ActionType(str, Enum):
    """User action types for audit log."""
    TAG_UPLOAD = "TAG_UPLOAD"
    TAG_CREATED = "TAG_CREATED"
    TAG_UPDATED = "TAG_UPDATED"
    TAG_RELEASED = "TAG_RELEASED"
    LPO_CREATED = "LPO_CREATED"
    LPO_UPDATED = "LPO_UPDATED"
    ALLOCATION_CREATED = "ALLOCATION_CREATED"
    CONSUMPTION_SUBMITTED = "CONSUMPTION_SUBMITTED"
    DO_CREATED = "DO_CREATED"
    EXCEPTION_CREATED = "EXCEPTION_CREATED"
    EXCEPTION_RESOLVED = "EXCEPTION_RESOLVED"
    OPERATION_FAILED = "OPERATION_FAILED"
    # Scheduling actions
    SCHEDULE_CREATED = "SCHEDULE_CREATED"
    SCHEDULE_UPDATED = "SCHEDULE_UPDATED"
    SCHEDULE_CANCELLED = "SCHEDULE_CANCELLED"


class Shift(str, Enum):
    """Production shift values - must match Smartsheet picklist."""
    MORNING = "Morning"
    EVENING = "Evening"


class ScheduleStatus(str, Enum):
    """Production schedule status values - must match Smartsheet picklist."""
    PLANNED = "Planned"
    RELEASED_FOR_NESTING = "Released for Nesting"
    NESTING_UPLOADED = "Nesting Uploaded"
    ALLOCATED = "Allocated"
    IN_PRODUCTION = "In Production"
    COMPLETED = "Completed"
    CANCELLED = "Cancelled"
    DELAYED = "Delayed"


class MachineStatus(str, Enum):
    """Machine status values - must match Smartsheet picklist."""
    OPERATIONAL = "Operational"
    MAINTENANCE = "Maintenance"


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
    
    # Multi-file support (SOTA - v1.6.3)
    files: List["FileAttachment"] = Field(default_factory=list)
    
    # Legacy single-file fields (backward compatibility)
    file_url: Optional[str] = None
    file_content: Optional[str] = None  # Base64 encoded file content
    original_file_name: Optional[str] = None
    
    # User info
    uploaded_by: str
    tag_name: Optional[str] = None
    
    # Reception info
    received_through: str = "API"  # Email, Whatsapp, API
    user_remarks: Optional[str] = None  # User-entered remarks
    
    metadata: Optional[Dict[str, Any]] = None
    
    def get_all_files(self) -> List["FileAttachment"]:
        """Get all files including legacy single-file fields (DRY - matches LPOIngestRequest)."""
        all_files = list(self.files)
        
        # Convert legacy fields to FileAttachment if present
        if self.file_url or self.file_content:
            legacy_file = FileAttachment(
                file_type=FileType.OTHER,  # Tags don't have typed files like LPO
                file_url=self.file_url,
                file_content=self.file_content,
                file_name=self.original_file_name
            )
            all_files.insert(0, legacy_file)
        
        return all_files


class TagIngestResponse(BaseModel):
    """Response payload for tag ingestion API."""
    status: str  # UPLOADED, DUPLICATE, BLOCKED
    tag_id: Optional[str] = None
    file_hash: Optional[str] = None
    trace_id: str
    message: Optional[str] = None
    exception_id: Optional[str] = None


class ScheduleTagRequest(BaseModel):
    """Request payload for production schedule API."""
    client_request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tag_id: str
    planned_date: str  # YYYY-MM-DD format
    shift: str  # Morning or Evening
    machine_id: str
    planned_qty_m2: Optional[float] = None  # Defaults to tag expected_consumption
    requested_by: str
    notes: Optional[str] = None


class ScheduleTagResponse(BaseModel):
    """Response payload for production schedule API."""
    status: str  # RELEASED_FOR_NESTING, BLOCKED, CONFLICT
    schedule_id: Optional[str] = None
    next_action_deadline: Optional[str] = None  # T-1 cutoff timestamp
    trace_id: str
    exception_id: Optional[str] = None
    message: Optional[str] = None


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


# ============== LPO Request/Response Models ==============

class Brand(str, Enum):
    """Valid brand values."""
    KIMMCO = "KIMMCO"
    WTI = "WTI"



class TermsOfPayment(str, Enum):
    """Valid payment terms."""
    DAYS_30 = "30 Days Credit"
    DAYS_60 = "60 Days Credit"
    DAYS_90 = "90 Days Credit"
    IMMEDIATE = "Immediate Payment"


class FileType(str, Enum):
    """Known file types for LPO attachments."""
    LPO = "lpo"             # Original purchase order document
    COSTING = "costing"     # Costing/pricing sheet
    AMENDMENT = "amendment" # PO amendments/revisions
    OTHER = "other"         # Any other document type


class FileAttachment(BaseModel):
    """Single file attachment for LPO."""
    file_type: FileType = FileType.OTHER
    file_url: Optional[str] = None
    file_content: Optional[str] = None  # Base64 encoded
    file_name: Optional[str] = None     # Original filename
    
    @model_validator(mode='after')
    def validate_file_source(self):
        """Ensure at least one file source is provided."""
        if not self.file_url and not self.file_content:
            raise ValueError("Either file_url or file_content is required")
        return self


class LPOIngestRequest(BaseModel):
    """Request payload for LPO ingestion API (create)."""
    client_request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    
    # Required fields
    sap_reference: str  # REQUIRED - external ID (e.g., PTE-185)
    customer_name: str
    project_name: str
    brand: str  # KIMMCO or WTI
    po_quantity_sqm: float = Field(gt=0)  # Must be positive
    price_per_sqm: float = Field(gt=0)  # Must be positive
    
    # Optional fields
    customer_lpo_ref: Optional[str] = None
    terms_of_payment: str = "30 Days Credit"
    wastage_pct: float = Field(default=0.0, ge=0, le=20)  # 0-20%
    hold_reason: Optional[str] = None
    remarks: Optional[str] = None
    
    # File attachments (multi-file support)
    files: List[FileAttachment] = Field(default_factory=list)
    
    # Legacy single-file fields (backward compatibility)
    file_url: Optional[str] = None
    file_content: Optional[str] = None
    original_file_name: Optional[str] = None
    
    # User info
    uploaded_by: str
    
    # SharePoint config (optional, can use env vars)
    sharepoint_base_url: Optional[str] = None
    
    def get_all_files(self) -> List[FileAttachment]:
        """Get all files including legacy single-file fields."""
        all_files = list(self.files)
        
        # Convert legacy fields to FileAttachment if present
        if self.file_url or self.file_content:
            legacy_file = FileAttachment(
                file_type=FileType.LPO,  # Assume legacy = LPO type
                file_url=self.file_url,
                file_content=self.file_content,
                file_name=self.original_file_name
            )
            all_files.insert(0, legacy_file)
        
        return all_files



class LPOUpdateRequest(BaseModel):
    """Request payload for LPO update API."""
    client_request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    
    # Key for lookup
    sap_reference: str  # REQUIRED - identifies which LPO to update
    
    # Optional fields - only update what's provided
    customer_lpo_ref: Optional[str] = None
    customer_name: Optional[str] = None
    project_name: Optional[str] = None
    po_quantity_sqm: Optional[float] = Field(default=None, gt=0)
    price_per_sqm: Optional[float] = Field(default=None, gt=0)
    terms_of_payment: Optional[str] = None
    wastage_pct: Optional[float] = Field(default=None, ge=0, le=20)
    hold_reason: Optional[str] = None
    lpo_status: Optional[str] = None  # Draft, Active, On Hold, etc.
    remarks: Optional[str] = None
    
    # User info
    updated_by: str


class LPOIngestResponse(BaseModel):
    """Response payload for LPO ingestion API."""
    status: str  # OK, DUPLICATE, BLOCKED, ALREADY_PROCESSED
    sap_reference: Optional[str] = None
    folder_path: Optional[str] = None
    trace_id: str
    message: Optional[str] = None
    exception_id: Optional[str] = None


class LPOUpdateResponse(BaseModel):
    """Response payload for LPO update API."""
    status: str  # OK, NOT_FOUND, BLOCKED
    sap_reference: Optional[str] = None
    trace_id: str
    message: Optional[str] = None
    exception_id: Optional[str] = None
