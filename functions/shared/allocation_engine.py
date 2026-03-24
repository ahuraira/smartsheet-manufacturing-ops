"""
Allocation Engine
=================

Core allocation logic. Creates soft reservations in ALLOCATION_LOG
after nesting is parsed.

Called by:
- fn_allocate (HTTP endpoint for manual allocation)
- fn_parse_nesting (internal call after BOM processing)

Flow:
1. Read PARSED_BOM for the nesting session → group by SAP_CODE
2. For each material: check available stock → determine flag
3. Write ALLOCATION_LOG rows (one per material × shift)
4. Write INVENTORY_TXN_LOG rows (txn_type=Allocation, negative qty)
5. If any shortage detected → create exception
"""

import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Dict, List, Optional

from .logical_names import Sheet, Column
from .manifest import get_manifest
from .helpers import parse_float_safe, now_uae, format_datetime_for_smartsheet
from .audit import create_exception, log_user_action
from .models import ActionType, ReasonCode, ExceptionSeverity, ExceptionSource
from .stock_service import compute_available_qty, determine_stock_flag
from .inventory_service import log_inventory_transaction

logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────────
# How long soft reservation is held (hours from allocation time)
RESERVATION_HOURS = int(os.environ.get("ALLOCATION_RESERVE_HOURS", "24"))


# ── Data Models ─────────────────────────────────────────────────────
@dataclass
class MaterialNeed:
    """Aggregated material need from PARSED_BOM."""
    sap_code: str
    total_qty: float = 0.0
    uom: str = ""                 # SAP UOM (e.g. ROL)
    nesting_description: str = "" # First human-readable description for this SAP code
    raw_quantity: float = 0.0     # Sum of raw (nesting) quantities
    raw_uom: str = ""             # Nesting UOM (e.g. m)


@dataclass
class AllocationLine:
    """Single allocation row created."""
    allocation_id: str
    material_code: str
    quantity: float
    stock_check_flag: str  # Green / Yellow / Red
    net_available: float


@dataclass
class AllocationResult:
    """Result of allocation attempt."""
    status: str  # ALLOCATED, PARTIAL_ALLOCATED, SHORTAGE
    allocation_ids: List[str] = field(default_factory=list)
    lines: List[AllocationLine] = field(default_factory=list)
    shortages: List[dict] = field(default_factory=list)
    warnings: List[dict] = field(default_factory=list)
    exception_ids: List[str] = field(default_factory=list)

    def to_dict(self):
        return {
            "status": self.status,
            "allocation_ids": self.allocation_ids,
            "lines": [
                {
                    "allocation_id": ln.allocation_id,
                    "material_code": ln.material_code,
                    "quantity": ln.quantity,
                    "stock_check_flag": ln.stock_check_flag,
                    "net_available": ln.net_available,
                }
                for ln in self.lines
            ],
            "shortages": self.shortages,
            "warnings": self.warnings,
            "exception_ids": self.exception_ids,
        }


# ── Row parsing helper ──────────────────────────────────────────────
def _parse_rows(sheet_data: dict) -> list:
    """Convert raw Smartsheet sheet data into list of {physical_col_name: value} dicts."""
    columns = sheet_data.get("columns", [])
    col_id_to_name = {col["id"]: col["title"] for col in columns}
    parsed = []
    for raw_row in sheet_data.get("rows", []):
        row = {}
        for cell in raw_row.get("cells", []):
            col_id = cell.get("columnId")
            if col_id in col_id_to_name:
                row[col_id_to_name[col_id]] = cell.get("value") or cell.get("displayValue")
        row["row_id"] = raw_row.get("id")
        parsed.append(row)
    return parsed


def _generate_allocation_id() -> str:
    """Generate a unique allocation ID."""
    ts = now_uae().strftime("%Y%m%d")
    short_uuid = uuid.uuid4().hex[:6].upper()
    return f"ALLOC-{ts}-{short_uuid}"


# ── Main allocation function ────────────────────────────────────────
def allocate_for_session(
    client,
    nest_session_id: str,
    tag_id: str,
    planned_date: Optional[str] = None,
    shift: str = "Morning",
    trace_id: str = "",
    client_request_id: str = "",
) -> AllocationResult:
    """
    Create material allocations for a nesting session.

    Steps:
    1. Read PARSED_BOM rows filtered by nest_session_id
    2. Aggregate quantities by SAP_CODE (using CANONICAL_QUANTITY in SAP UOM)
    3. For each material: check stock → write allocation + txn rows
    4. Handle shortages with exceptions

    Args:
        client: SmartsheetClient instance
        nest_session_id: The nesting session to allocate for
        tag_id: Tag Sheet ID for the allocation
        planned_date: Production date (ISO string), defaults to tomorrow
        shift: Shift assignment (Morning/Evening)
        trace_id: Trace ID for logging
        client_request_id: For idempotency

    Returns:
        AllocationResult with status and created allocation IDs
    """
    manifest = get_manifest()
    now = now_uae()
    result = AllocationResult(status="ALLOCATED")

    if not planned_date:
        planned_date = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    logger.info(
        f"[{trace_id}] Starting allocation for session={nest_session_id}, "
        f"tag={tag_id}, date={planned_date}, shift={shift}"
    )

    # ── Step 1: Read PARSED_BOM ─────────────────────────────────────
    col_bom_session      = manifest.get_column_name(Sheet.PARSED_BOM, Column.PARSED_BOM.NEST_SESSION_ID)
    col_bom_sap_code     = manifest.get_column_name(Sheet.PARSED_BOM, Column.PARSED_BOM.SAP_CODE)
    col_bom_qty          = manifest.get_column_name(Sheet.PARSED_BOM, Column.PARSED_BOM.CANONICAL_QUANTITY)
    col_bom_uom          = manifest.get_column_name(Sheet.PARSED_BOM, Column.PARSED_BOM.CANONICAL_UOM)
    col_bom_material_type = manifest.get_column_name(Sheet.PARSED_BOM, Column.PARSED_BOM.MATERIAL_TYPE)
    col_bom_description  = manifest.get_column_name(Sheet.PARSED_BOM, Column.PARSED_BOM.NESTING_DESCRIPTION)
    col_bom_raw_qty      = manifest.get_column_name(Sheet.PARSED_BOM, Column.PARSED_BOM.QUANTITY)
    col_bom_raw_uom      = manifest.get_column_name(Sheet.PARSED_BOM, Column.PARSED_BOM.UOM)

    bom_sheet = client.get_sheet(Sheet.PARSED_BOM)
    bom_rows = _parse_rows(bom_sheet)

    # Filter to this session only
    session_rows = [r for r in bom_rows if r.get(col_bom_session) == nest_session_id]

    if not session_rows:
        logger.warning(f"[{trace_id}] No PARSED_BOM rows found for session {nest_session_id}")
        result.status = "SHORTAGE"
        result.warnings.append({
            "code": "NO_BOM_LINES",
            "message": f"No PARSED_BOM rows for session {nest_session_id}"
        })
        return result

    # ── Step 2: Aggregate by SAP code ────────────────────────────────
    materials: Dict[str, MaterialNeed] = {}
    for row in session_rows:
        sap_code = row.get(col_bom_sap_code)
        if not sap_code:
            continue  # Skip unmapped lines

        qty = parse_float_safe(row.get(col_bom_qty), default=0.0)
        uom = row.get(col_bom_uom, "")
        raw_qty = parse_float_safe(row.get(col_bom_raw_qty), default=0.0)
        raw_uom = row.get(col_bom_raw_uom, "")
        description = row.get(col_bom_description, "")

        if sap_code not in materials:
            materials[sap_code] = MaterialNeed(
                sap_code=sap_code,
                uom=uom,
                nesting_description=description,  # First occurrence wins
                raw_uom=raw_uom,
            )
        materials[sap_code].total_qty += qty
        materials[sap_code].raw_quantity += raw_qty

    logger.info(
        f"[{trace_id}] Aggregated {len(materials)} materials from "
        f"{len(session_rows)} BOM lines"
    )

    # ── Step 3: Check stock + create allocations ────────────────────
    reserve_until = (now + timedelta(hours=RESERVATION_HOURS)).isoformat()
    has_shortage = False
    has_partial = False

    # Resolve physical column names for writes
    col_alloc_id     = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.ALLOCATION_ID)
    col_alloc_tag    = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.TAG_SHEET_ID)
    col_alloc_mat    = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.MATERIAL_CODE)
    col_alloc_qty    = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.QUANTITY)
    col_alloc_uom    = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.UOM)
    col_alloc_date   = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.PLANNED_DATE)
    col_alloc_shift  = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.SHIFT)
    col_alloc_status = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.STATUS)
    col_alloc_flag   = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.STOCK_CHECK_FLAG)
    col_alloc_at     = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.ALLOCATED_AT)
    col_alloc_until  = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.RESERVED_UNTIL)
    col_alloc_rmk    = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.REMARKS)
    col_alloc_sess   = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.NEST_SESSION_ID)
    col_alloc_desc   = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.NESTING_DESCRIPTION)
    col_alloc_rqty   = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.RAW_QUANTITY)
    col_alloc_ruom   = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.RAW_UOM)

    for sap_code, need in materials.items():
        # Check stock
        stock = compute_available_qty(client, sap_code, trace_id=trace_id)
        flag = determine_stock_flag(stock.net_available, need.total_qty)

        alloc_id = _generate_allocation_id()
        alloc_qty = need.total_qty

        # For partial availability, only allocate what's available
        if flag == "Yellow":
            alloc_qty = min(need.total_qty, max(0, stock.net_available))
            has_partial = True
        elif flag == "Red":
            alloc_qty = 0.0
            has_shortage = True

        # ── Write ALLOCATION_LOG row ────────────────────────────────
        alloc_row_data = {
            col_alloc_id:     alloc_id,
            col_alloc_tag:    tag_id,
            col_alloc_mat:    sap_code,
            col_alloc_qty:    alloc_qty,
            col_alloc_uom:    need.uom,
            col_alloc_date:   planned_date,
            col_alloc_shift:  shift,
            col_alloc_status: "Submitted",
            col_alloc_flag:   flag,
            col_alloc_at:     format_datetime_for_smartsheet(now),
            col_alloc_until:  reserve_until,
            col_alloc_rmk:    f"Session: {nest_session_id}",
            col_alloc_sess:   nest_session_id,
            col_alloc_desc:   need.nesting_description,
            col_alloc_rqty:   need.raw_quantity,
            col_alloc_ruom:   need.raw_uom,
        }

        try:
            client.add_row(Sheet.ALLOCATION_LOG, alloc_row_data)
            result.allocation_ids.append(alloc_id)
            result.lines.append(AllocationLine(
                allocation_id=alloc_id,
                material_code=sap_code,
                quantity=alloc_qty,
                stock_check_flag=flag,
                net_available=stock.net_available,
            ))
            logger.info(
                f"[{trace_id}] Created allocation {alloc_id}: "
                f"{sap_code} qty={alloc_qty} flag={flag}"
            )
            try:
                log_user_action(
                    client=client, user_id="system",
                    action_type=ActionType.ALLOCATION_CREATED,
                    target_table="ALLOCATION_LOG", target_id=alloc_id,
                    notes=f"Allocated {sap_code} qty={alloc_qty}",
                    trace_id=trace_id
                )
            except Exception:
                pass
        except Exception as e:
            logger.error(f"[{trace_id}] Failed to write allocation row for {sap_code}: {e}")
            try:
                create_exception(
                    client=client, trace_id=trace_id,
                    reason_code=ReasonCode.SYSTEM_ERROR,
                    severity=ExceptionSeverity.CRITICAL,
                    source=ExceptionSource.ALLOCATION,
                    material_code=sap_code,
                    message=f"Failed to create allocation row for {sap_code}: {str(e)[:500]}"
                )
            except Exception:
                pass
            result.warnings.append({
                "code": "ALLOC_WRITE_FAILED",
                "message": f"Failed to create allocation for {sap_code}: {e}"
            })
            continue

        # ── Write INVENTORY_TXN_LOG row (negative = reservation) ───
        if alloc_qty > 0:
            try:
                from .inventory_service import log_inventory_transactions_batch
                log_inventory_transactions_batch(
                    client=client,
                    transactions=[{
                        "txn_type": "Allocation",
                        "material_code": sap_code,
                        "quantity": -alloc_qty,  # Negative = reservation
                        "reference_doc": alloc_id,
                        "source_system": "AzureFunc"
                    }],
                    trace_id=trace_id
                )
                logger.debug(f"[{trace_id}] Created inventory txn batch for {sap_code}")
            except Exception as e:
                logger.error(f"[{trace_id}] Failed to write inventory txn for {sap_code}: {e}")
                try:
                    create_exception(
                        client=client, trace_id=trace_id,
                        reason_code=ReasonCode.SYSTEM_ERROR,
                        severity=ExceptionSeverity.CRITICAL,
                        source=ExceptionSource.ALLOCATION,
                        material_code=sap_code,
                        message=f"Failed to write inventory txn for {sap_code}: {str(e)[:500]}"
                    )
                except Exception:
                    pass
                result.warnings.append({
                    "code": "TXN_WRITE_FAILED",
                    "message": f"Inventory txn failed for {sap_code}: {e}"
                })

        # ── Shortage → create exception ─────────────────────────────
        if flag == "Red":
            result.shortages.append({
                "material_code": sap_code,
                "needed": need.total_qty,
                "available": stock.net_available,
                "deficit": need.total_qty - max(0, stock.net_available),
            })

    # ── Step 4: Create exceptions for shortages ─────────────────────
    if result.shortages:
        try:
            from .audit import create_exception
            from .models import ReasonCode, ExceptionSeverity, ExceptionSource

            shortage_summary = "; ".join(
                f"{s['material_code']}: need {s['needed']}, avail {s['available']}"
                for s in result.shortages
            )

            exc_id = create_exception(
                client=client,
                trace_id=trace_id,
                source=ExceptionSource.ALLOCATION,
                reason_code=ReasonCode.SHORTAGE,
                severity=ExceptionSeverity.HIGH,
                related_tag_id=tag_id,
                message=f"Material shortage during allocation for session {nest_session_id}: {shortage_summary}"
            )
            result.exception_ids.append(exc_id)
            logger.warning(f"[{trace_id}] Created shortage exception {exc_id}")

        except Exception as e:
            logger.error(f"[{trace_id}] Failed to create shortage exception: {e}")
            result.warnings.append({
                "code": "EXCEPTION_FAILED",
                "message": f"Failed to create shortage exception: {e}"
            })

    # ── Step 5: Determine final status ──────────────────────────────
    if has_shortage and not has_partial and not result.allocation_ids:
        result.status = "SHORTAGE"
    elif has_shortage or has_partial:
        result.status = "PARTIAL_ALLOCATED"
    else:
        result.status = "ALLOCATED"

    logger.info(
        f"[{trace_id}] Allocation complete: status={result.status}, "
        f"allocations={len(result.allocation_ids)}, shortages={len(result.shortages)}"
    )

    return result
