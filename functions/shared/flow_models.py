"""
Flow Helper Models
==================

Models for consumption submission, stock submission, and allocation endpoints.
These support the Power Automate Teams adaptive card workflows.

Created: v1.7.0
"""

from datetime import datetime
from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field
from enum import Enum
from .models import ExceptionSeverity


# =====================
# Enums
# =====================

class SubmissionStatus(str, Enum):
    """Status of a consumption or stock submission."""
    PENDING = "PENDING"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


class WarningCode(str, Enum):
    """Warning codes for submissions."""
    VARIANCE_WARN = "VARIANCE_WARN"          # 5-10% variance
    VARIANCE_HIGH = "VARIANCE_HIGH"          # >10% variance
    REMNANT_USED = "REMNANT_USED"            # Consumed from remnant
    TOLERANCE_USED = "TOLERANCE_USED"        # Used tolerance allocation


class ErrorCode(str, Enum):
    """Error codes for submissions."""
    INVALID_PAYLOAD = "INVALID_PAYLOAD"
    ALLOCATION_NOT_FOUND = "ALLOCATION_NOT_FOUND"
    ALREADY_SUBMITTED = "ALREADY_SUBMITTED"
    INSUFFICIENT_STOCK = "INSUFFICIENT_STOCK"
    VARIANCE_CRITICAL = "VARIANCE_CRITICAL"
    AUTH_ERROR = "AUTH_ERROR"
    SERVER_ERROR = "SERVER_ERROR"
    LOCK_TIMEOUT = "LOCK_TIMEOUT"


# =====================
# Sub-Models
# =====================

class ConsumptionLine(BaseModel):
    """Single line in a consumption submission."""
    canonical_code: str = Field(..., min_length=1, max_length=50)
    allocated_qty: float = Field(..., ge=0)
    actual_qty: float = Field(..., ge=0)
    accessories_qty: float = Field(default=0.0, ge=0)
    uom: str = Field(..., min_length=1, max_length=10)
    remarks: Optional[str] = Field(None, max_length=500)


class StockLine(BaseModel):
    """Single line in a stock count submission."""
    canonical_code: str = Field(..., min_length=1, max_length=50)
    counted_qty: float = Field(..., ge=0)
    uom: str = Field(..., min_length=1, max_length=10)
    remarks: Optional[str] = Field(None, max_length=500)


class Warning(BaseModel):
    """Warning in submission response."""
    code: WarningCode
    message: str
    details: Optional[Dict[str, Any]] = None


class Error(BaseModel):
    """Error in submission response."""
    code: ErrorCode
    message: str
    details: Optional[Dict[str, Any]] = None


class AggregatedMaterial(BaseModel):
    """Aggregated material requirement across allocations."""
    canonical_code: str
    allocated_qty: float
    already_consumed: float
    remaining_qty: float
    uom: str


class AllocationSummary(BaseModel):
    """Summary of a pending allocation."""
    allocation_id: str
    tag_id: str
    brief: str  # e.g., "TAG-1001 - 5 ducts - LPO-55"
    alloc_date: str  # ISO date
    alloc_qty: float


class StockSnapshotLine(BaseModel):
    """Single line in stock snapshot."""
    canonical_code: str
    system_physical_closing: float
    uom: str
    last_count: Optional[str] = None  # ISO date


class TagChoice(BaseModel):
    """A single choice for a Power Automate adaptive card Input.ChoiceSet."""
    title: str  # Display label shown to the operator
    value: str  # Value submitted back to the flow


# =====================
# Request Models
# =====================

class ConsumptionSubmission(BaseModel):
    """
    Consumption submission request.
    
    Sent from Power Automate when user submits consumption from Teams card.
    trace_id serves as the idempotency key.
    """
    user: str = Field(..., description="User email or ID")
    plant: str = Field(..., min_length=1, max_length=50)
    shift: str = Field(..., min_length=1, max_length=50)
    allocation_ids: List[str] = Field(..., min_items=1)
    lines: List[ConsumptionLine] = Field(..., min_items=1)
    trace_id: Optional[str] = None
    source: str = Field(default="TEAMS", max_length=50)


class ConsumptionSubmissionFromCard(BaseModel):
    """
    Consumption submission directly from the adaptive card response.
    Eliminates the need for Power Automate to loop and map values.
    trace_id serves as the idempotency key.
    """
    user: str = Field(..., description="User email or ID")
    plant: str = Field(..., min_length=1, max_length=50)
    shift: str = Field(..., min_length=1, max_length=50)
    card_data: Dict[str, Any] = Field(..., description="Raw Adaptive Card Data")
    trace_id: Optional[str] = None
    source: str = Field(default="TEAMS", max_length=50)


class StockSubmission(BaseModel):
    """
    Stock count submission request.
    
    Sent from Power Automate when user submits stock count from Teams card.
    trace_id serves as the idempotency key.
    """
    user: str = Field(..., description="User email or ID")
    plant: str = Field(..., min_length=1, max_length=50)
    shift: str = Field(..., min_length=1, max_length=50)
    snapshot_date: str = Field(..., description="ISO date")
    lines: List[StockLine] = Field(..., min_items=1)
    trace_id: Optional[str] = None
    source: str = Field(default="TEAMS", max_length=50)


class SubmissionConfirmRequest(BaseModel):
    """Request to confirm/reject a pending submission."""
    processed_submission_id: str = Field(..., description="Submission ID to confirm")
    approver: str = Field(..., description="Approver email or ID")
    decision: Literal["APPROVE", "REJECT"]
    notes: Optional[str] = Field(None, max_length=1000)
    trace_id: Optional[str] = None


class AllocationAggregateRequest(BaseModel):
    """Request to aggregate materials for a tag sheet."""
    tag_id: Optional[str] = Field(None, description="Tag Sheet ID — primary input from Power Automate")
    allocation_ids: Optional[List[str]] = Field(None, description="Explicit allocation IDs (backward compat)")
    trace_id: Optional[str] = None


class ExceptionCreateRequest(BaseModel):
    """Request to create an exception programmatically."""
    type: str = Field(..., min_length=1, max_length=50)
    reference: str = Field(..., description="submission_id or allocation_id")
    severity: ExceptionSeverity
    note: Optional[str] = Field(None, max_length=1000)
    created_by: str = Field(..., description="User email or ID")
    trace_id: Optional[str] = None


# =====================
# Response Models
# =====================

class SubmissionResult(BaseModel):
    """Result of submission (consumption or stock)."""
    trace_id: str
    warnings: List[Warning] = Field(default_factory=list)
    errors: List[Error] = Field(default_factory=list)


class SubmissionStatusResponse(BaseModel):
    """Response for submission status query."""
    submission_id: str
    status: SubmissionStatus
    warnings: List[Warning] = Field(default_factory=list)
    errors: List[Error] = Field(default_factory=list)
    created_at: Optional[str] = None  # ISO datetime


class PendingItemsResponse(BaseModel):
    """Response for pending items query."""
    trace_id: str
    timestamp: str  # ISO datetime
    pending_tags: List[AllocationSummary]
    allow_stock_submission: bool = True
    tag_choices: List[TagChoice] = Field(
        default_factory=list,
        description="Deduplicated tag IDs as title/value pairs for an adaptive card Input.ChoiceSet"
    )


class AllocationDetail(BaseModel):
    """Rich detail for a single allocation row — used to build the consumption card."""
    allocation_id: str
    sap_code: str                  # MATERIAL_CODE from ALLOCATION_LOG (SAP code)
    nesting_description: str       # Human-readable name for the operator
    sap_qty: float                 # SAP quantity (system units, e.g. 4 ROL)
    sap_uom: str                   # SAP UOM (e.g. ROL)
    raw_qty: float                 # Raw physical quantity (e.g. 100)
    raw_uom: str                   # Raw UOM (e.g. m)
    already_consumed: float        # From CONSUMPTION_LOG for this allocation
    remaining_qty: float           # sap_qty − already_consumed
    stock_check_flag: str          # Green / Yellow / Red
    planned_date: str
    shift: str


class ConsumptionCardLine(BaseModel):
    """Pre-filled line for a Power Automate adaptive card consumption form."""
    allocation_id: str
    sap_code: str                  # Read-only label
    nesting_description: str       # Read-only label shown to operator
    sap_uom: str                   # Read-only label
    raw_uom: str                   # Read-only label
    allocated_raw_qty: float       # Read-only — what was originally allocated (raw units)
    default_actual_raw_qty: float  # Editable default = remaining raw qty (raw units)
    allocated_sap_qty: float       # Read-only — what was originally allocated (SAP units)
    default_actual_sap_qty: float  # Editable default = remaining SAP qty


class AllocationAggregateResponse(BaseModel):
    """Response for allocation aggregation."""
    trace_id: str
    tag_id: Optional[str] = None
    allocation_details: List[AllocationDetail]
    consumption_card_lines: List[ConsumptionCardLine]
    consumption_card: Dict[str, Any]  # Ready-to-post adaptive card JSON for Teams
    total_materials: int


class StockSnapshotResponse(BaseModel):
    """Response for stock snapshot query."""
    trace_id: str
    plant: str
    snapshot_time: str  # ISO datetime
    lines: List[StockSnapshotLine]


class AllocationDetailResponse(BaseModel):
    """Response for single allocation detail."""
    allocation_id: str
    tag_id: str
    allocated_date: str
    lines: List[AggregatedMaterial]  # Per-material allocated/consumed
    trace_id: str


class ExceptionCreateResponse(BaseModel):
    """Response for exception creation."""
    exception_id: str
    trace_id: str


# Import existing enums for compatibility
# from .models import ExceptionSeverity  # Moved to top
