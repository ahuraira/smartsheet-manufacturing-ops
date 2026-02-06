"""
LPO Service Module - Centralized LPO operations.

This module provides:
- Flexible LPO lookup by various reference fields
- LPO status and balance validation  
- Quantity extraction helpers

DRY Compliance: Consolidates LPO logic from fn_ingest_tag, fn_schedule_tag,
fn_lpo_update, and fn_lpo_ingest.

Created: v1.6.6 (2026-01-30)
"""

from dataclasses import dataclass
from typing import Optional, NamedTuple
from enum import Enum

from .logical_names import Sheet, Column
from .helpers import get_physical_column_name, parse_float_safe


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class LPOQuantities:
    """Extracted quantity values from an LPO record."""
    po_quantity: float
    delivered_quantity: float
    planned_quantity: float
    allocated_quantity: float
    
    @property
    def total_committed(self) -> float:
        """Delivered + Planned + Allocated."""
        return self.delivered_quantity + self.planned_quantity + self.allocated_quantity
    
    @property
    def available_balance(self) -> float:
        """PO Quantity minus total committed."""
        return self.po_quantity - self.total_committed


class LPOValidationStatus(Enum):
    """LPO validation result status."""
    OK = "OK"
    NOT_FOUND = "NOT_FOUND"
    ON_HOLD = "ON_HOLD"
    INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"


@dataclass
class LPOValidationResult:
    """Result of LPO validation."""
    status: LPOValidationStatus
    message: str
    lpo: Optional[dict] = None
    quantities: Optional[LPOQuantities] = None


# =============================================================================
# Lookup Functions
# =============================================================================

def find_lpo_by_sap_reference(client, sap_ref: str) -> Optional[dict]:
    """
    Find LPO by SAP Reference.
    
    Args:
        client: SmartsheetClient instance
        sap_ref: SAP Reference to search for
        
    Returns:
        LPO row dict if found, None otherwise
    """
    if not sap_ref:
        return None
    return client.find_row(
        Sheet.LPO_MASTER,
        Column.LPO_MASTER.SAP_REFERENCE,
        sap_ref
    )


def find_lpo_by_customer_ref(client, customer_ref: str) -> Optional[dict]:
    """
    Find LPO by Customer LPO Reference.
    
    Args:
        client: SmartsheetClient instance
        customer_ref: Customer LPO Reference to search for
        
    Returns:
        LPO row dict if found, None otherwise
    """
    if not customer_ref:
        return None
    return client.find_row(
        Sheet.LPO_MASTER,
        Column.LPO_MASTER.CUSTOMER_LPO_REF,
        customer_ref
    )


def find_lpo_flexible(
    client,
    sap_ref: Optional[str] = None,
    customer_ref: Optional[str] = None,
    lpo_id: Optional[str] = None
) -> Optional[dict]:
    """
    Find LPO by various reference fields (tries in priority order).
    
    Priority:
    1. SAP Reference (most specific)
    2. Customer LPO Reference
    3. LPO ID (tries as SAP first, then Customer ref)
    
    Args:
        client: SmartsheetClient instance
        sap_ref: SAP Reference to search for
        customer_ref: Customer LPO Reference to search for
        lpo_id: Generic LPO ID (tries both SAP and Customer ref)
        
    Returns:
        LPO row dict if found, None otherwise
        
    Example:
        >>> lpo = find_lpo_flexible(client, sap_ref="PTE-185")
        >>> lpo = find_lpo_flexible(client, customer_ref="CUST-001")
        >>> lpo = find_lpo_flexible(client, lpo_id="PTE-185")  # tries both
    """
    # 1. Try SAP Reference first (most specific)
    if sap_ref:
        lpo = find_lpo_by_sap_reference(client, sap_ref)
        if lpo:
            return lpo
    
    # 2. Try Customer LPO Reference
    if customer_ref:
        lpo = find_lpo_by_customer_ref(client, customer_ref)
        if lpo:
            return lpo
    
    # 3. Try LPO ID as both SAP and Customer ref
    if lpo_id:
        lpo = find_lpo_by_sap_reference(client, lpo_id)
        if lpo:
            return lpo
        lpo = find_lpo_by_customer_ref(client, lpo_id)
        if lpo:
            return lpo
    
    return None


# =============================================================================
# Data Extraction Helpers  
# =============================================================================

def get_lpo_quantities(lpo: dict) -> LPOQuantities:
    """
    Extract quantity values from an LPO record.
    
    Args:
        lpo: LPO row dict from Smartsheet
        
    Returns:
        LPOQuantities dataclass with all quantity fields
    """
    po_qty_col = get_physical_column_name("LPO_MASTER", "PO_QUANTITY_SQM")
    delivered_col = get_physical_column_name("LPO_MASTER", "DELIVERED_QUANTITY_SQM")
    planned_col = get_physical_column_name("LPO_MASTER", "PLANNED_QUANTITY")
    allocated_col = get_physical_column_name("LPO_MASTER", "ALLOCATED_QUANTITY")
    
    return LPOQuantities(
        po_quantity=parse_float_safe(lpo.get(po_qty_col), 0),
        delivered_quantity=parse_float_safe(lpo.get(delivered_col), 0),
        planned_quantity=parse_float_safe(lpo.get(planned_col), 0),
        allocated_quantity=parse_float_safe(lpo.get(allocated_col), 0),
    )


def get_lpo_status(lpo: dict) -> str:
    """
    Get the status field from an LPO record.
    
    Args:
        lpo: LPO row dict from Smartsheet
        
    Returns:
        LPO status string (normalized to lowercase), or empty string
    """
    status_col = get_physical_column_name("LPO_MASTER", "LPO_STATUS")
    status = lpo.get(status_col, "")
    return str(status).lower() if status else ""


def get_lpo_sap_reference(lpo: dict) -> Optional[str]:
    """
    Get the SAP Reference from an LPO record.
    
    Args:
        lpo: LPO row dict from Smartsheet
        
    Returns:
        SAP Reference string or None
    """
    sap_col = get_physical_column_name("LPO_MASTER", "SAP_REFERENCE")
    return lpo.get(sap_col)


# =============================================================================
# Validation Functions
# =============================================================================

def validate_lpo_status(lpo: dict) -> LPOValidationResult:
    """
    Validate that an LPO is in a valid status for operations.
    
    Args:
        lpo: LPO row dict from Smartsheet
        
    Returns:
        LPOValidationResult indicating if LPO status is OK or ON_HOLD
    """
    if not lpo:
        return LPOValidationResult(
            status=LPOValidationStatus.NOT_FOUND,
            message="LPO not found"
        )
    
    status = get_lpo_status(lpo)
    if status == "on hold":
        sap_ref = get_lpo_sap_reference(lpo) or "unknown"
        return LPOValidationResult(
            status=LPOValidationStatus.ON_HOLD,
            message=f"LPO {sap_ref} is on hold",
            lpo=lpo
        )
    
    return LPOValidationResult(
        status=LPOValidationStatus.OK,
        message="LPO status is valid",
        lpo=lpo
    )


def validate_po_balance(
    lpo: dict, 
    requested_qty: float,
    tolerance_pct: float = 0.05
) -> LPOValidationResult:
    """
    Validate that an LPO has sufficient PO balance for the requested quantity.
    
    Uses 5% tolerance by default (configurable).
    
    Args:
        lpo: LPO row dict from Smartsheet
        requested_qty: Quantity being requested
        tolerance_pct: Tolerance percentage (default 5% = 0.05)
        
    Returns:
        LPOValidationResult with quantities populated
    """
    if not lpo:
        return LPOValidationResult(
            status=LPOValidationStatus.NOT_FOUND,
            message="LPO not found"
        )
    
    quantities = get_lpo_quantities(lpo)
    
    # Check if total committed + requested exceeds PO Quantity with tolerance
    max_allowed = quantities.po_quantity * (1 + tolerance_pct)
    new_total = quantities.total_committed + requested_qty
    
    if new_total > max_allowed:
        sap_ref = get_lpo_sap_reference(lpo) or "unknown"
        return LPOValidationResult(
            status=LPOValidationStatus.INSUFFICIENT_BALANCE,
            message=f"Insufficient PO balance. PO: {quantities.po_quantity}, "
                    f"Committed: {quantities.total_committed}, Requested: {requested_qty}",
            lpo=lpo,
            quantities=quantities
        )
    
    return LPOValidationResult(
        status=LPOValidationStatus.OK,
        message="PO balance is sufficient",
        lpo=lpo,
        quantities=quantities
    )
