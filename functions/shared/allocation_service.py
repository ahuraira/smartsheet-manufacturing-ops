"""
Allocation Service
==================

Business logic for allocation queries and material aggregation.

DRY functions used by:
- fn_pending_items
- fn_allocations_aggregate
- fn_submit_consumption (for validation)
"""

import logging
from typing import List, Dict, Optional
from datetime import date, datetime, timedelta

from .logical_names import Sheet, Column
from .manifest import get_manifest
from .flow_models import (
    AllocationSummary,
    AggregatedMaterial,
    AllocationDetail,
    ConsumptionCardLine,
)

logger = logging.getLogger(__name__)


def _parse_rows(sheet_data: dict) -> List[Dict]:
    """Convert raw Smartsheet sheet data into list of {physical_col_name: value} dicts."""
    columns = sheet_data.get("columns", [])
    col_id_to_name = {col["id"]: col["title"] for col in columns}

    parsed = []
    for raw_row in sheet_data.get("rows", []):
        row = {
            col_id_to_name[cell["columnId"]]: (cell.get("value") or cell.get("displayValue"))
            for cell in raw_row.get("cells", [])
            if cell.get("columnId") in col_id_to_name
        }
        row["row_id"] = raw_row.get("id")
        parsed.append(row)

    return parsed


def get_pending_allocations(
    client,
    shift: Optional[str] = None,
    max_results: int = 50,
    trace_id: str = ""
) -> List[AllocationSummary]:
    """
    Get pending allocations.

    Filters:
    - status in (Submitted, Approved)
    - planned_date today or yesterday
    - optional shift filter

    Args:
        client: SmartsheetClient instance
        shift: Optional shift filter (Morning/Evening)
        max_results: Maximum results to return
        trace_id: Trace ID for logging

    Returns:
        List of AllocationSummary objects
    """
    manifest = get_manifest()

    col_status       = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.STATUS)
    col_alloc_id     = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.ALLOCATION_ID)
    col_tag_id       = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.TAG_SHEET_ID)
    col_planned_date = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.PLANNED_DATE)
    col_qty          = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.QUANTITY)
    col_shift        = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.SHIFT)

    sheet_data = client.get_sheet(Sheet.ALLOCATION_LOG)
    rows = _parse_rows(sheet_data)

    today     = date.today()
    yesterday = today - timedelta(days=1)
    pending   = []

    for row in rows:
        if row.get(col_status) not in ("Submitted", "Approved"):
            continue

        if shift and row.get(col_shift) != shift:
            continue

        planned_date_str = row.get(col_planned_date, "")
        if planned_date_str:
            try:
                planned_date = datetime.fromisoformat(str(planned_date_str)).date()
                if planned_date not in (today, yesterday):
                    continue
            except (ValueError, TypeError):
                continue

        allocation_id = row.get(col_alloc_id, "")
        tag_id        = row.get(col_tag_id, "")
        alloc_qty     = float(row.get(col_qty) or 0)

        pending.append(AllocationSummary(
            allocation_id=allocation_id,
            tag_id=tag_id,
            brief=f"{tag_id} - Allocation {allocation_id}",
            alloc_date=planned_date_str or str(today),
            alloc_qty=alloc_qty,
        ))

        if len(pending) >= max_results:
            break

    logger.info(f"[{trace_id}] Found {len(pending)} pending allocations")
    return pending


def get_allocation_details_by_tag(
    client,
    tag_id: str,
    trace_id: str = ""
) -> List[AllocationDetail]:
    """
    Get rich allocation details for a tag sheet.

    Reads ALLOCATION_LOG filtered by TAG_SHEET_ID, including the new enrichment
    columns (NESTING_DESCRIPTION, UOM, RAW_QUANTITY, RAW_UOM).

    Cross-references CONSUMPTION_LOG (filtered by ALLOCATION_ID) to compute
    already_consumed and remaining_qty per allocation row.

    Args:
        client: SmartsheetClient instance
        tag_id: Tag Sheet ID to fetch allocations for
        trace_id: Trace ID for logging

    Returns:
        List of AllocationDetail objects, one per ALLOCATION_LOG row for this tag
    """
    manifest = get_manifest()

    # ── ALLOCATION_LOG columns ──────────────────────────────────────
    col_alloc_id   = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.ALLOCATION_ID)
    col_tag        = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.TAG_SHEET_ID)
    col_material   = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.MATERIAL_CODE)
    col_qty        = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.QUANTITY)
    col_uom        = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.UOM)
    col_desc       = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.NESTING_DESCRIPTION)
    col_raw_qty    = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.RAW_QUANTITY)
    col_raw_uom    = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.RAW_UOM)
    col_date       = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.PLANNED_DATE)
    col_shift      = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.SHIFT)
    col_flag       = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.STOCK_CHECK_FLAG)

    # ── CONSUMPTION_LOG columns ─────────────────────────────────────
    col_cons_alloc_id = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.ALLOCATION_ID)
    col_cons_qty      = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.QUANTITY)

    # ── 1. Fetch allocation rows for this tag ───────────────────────
    alloc_rows = _parse_rows(client.get_sheet(Sheet.ALLOCATION_LOG))
    tag_alloc_rows = [r for r in alloc_rows if r.get(col_tag) == tag_id]

    if not tag_alloc_rows:
        logger.info(f"[{trace_id}] No allocation rows found for tag {tag_id}")
        return []

    # ── 2. Build set of allocation IDs for this tag ─────────────────
    alloc_ids = {r.get(col_alloc_id) for r in tag_alloc_rows if r.get(col_alloc_id)}

    # ── 3. Fetch consumed qty per allocation from CONSUMPTION_LOG ───
    cons_rows = _parse_rows(client.get_sheet(Sheet.CONSUMPTION_LOG))
    consumed_by_alloc: Dict[str, float] = {}
    for row in cons_rows:
        alloc_ref = row.get(col_cons_alloc_id)
        if alloc_ref in alloc_ids:
            qty = float(row.get(col_cons_qty) or 0)
            consumed_by_alloc[alloc_ref] = consumed_by_alloc.get(alloc_ref, 0.0) + qty

    # ── 4. Build AllocationDetail list ─────────────────────────────
    details = []
    for row in tag_alloc_rows:
        alloc_id       = row.get(col_alloc_id, "")
        sap_code       = row.get(col_material, "")
        description    = row.get(col_desc, sap_code)  # Fall back to SAP code if no description
        sap_qty        = float(row.get(col_qty) or 0)
        sap_uom        = row.get(col_uom, "")
        raw_qty        = float(row.get(col_raw_qty) or 0)
        raw_uom        = row.get(col_raw_uom, sap_uom)  # Fall back to SAP UOM if no raw UOM
        already_consumed = consumed_by_alloc.get(alloc_id, 0.0)
        remaining      = max(0.0, sap_qty - already_consumed)

        details.append(AllocationDetail(
            allocation_id=alloc_id,
            sap_code=sap_code,
            nesting_description=description,
            sap_qty=sap_qty,
            sap_uom=sap_uom,
            raw_qty=raw_qty,
            raw_uom=raw_uom,
            already_consumed=already_consumed,
            remaining_qty=remaining,
            stock_check_flag=row.get(col_flag, ""),
            planned_date=str(row.get(col_date, "")),
            shift=row.get(col_shift, ""),
        ))

    logger.info(f"[{trace_id}] Built {len(details)} allocation details for tag {tag_id}")
    return details


def build_consumption_card_lines(
    details: List[AllocationDetail],
) -> List[ConsumptionCardLine]:
    """
    Build pre-filled consumption card lines from allocation details.

    The default_actual_raw_qty is set to the raw remaining quantity so the
    operator only needs to change it if actual differs from plan.

    Args:
        details: List of AllocationDetail objects

    Returns:
        List of ConsumptionCardLine objects ready for the adaptive card
    """
    lines = []
    for d in details:
        # Compute raw remaining proportionally if raw_qty is available
        if d.sap_qty > 0 and d.raw_qty > 0:
            proportion_remaining = d.remaining_qty / d.sap_qty
            default_raw = round(d.raw_qty * proportion_remaining, 4)
        else:
            default_raw = d.raw_qty

        lines.append(ConsumptionCardLine(
            allocation_id=d.allocation_id,
            sap_code=d.sap_code,
            nesting_description=d.nesting_description,
            sap_uom=d.sap_uom,
            raw_uom=d.raw_uom,
            allocated_raw_qty=d.raw_qty,
            default_actual_raw_qty=default_raw,
            allocated_sap_qty=d.sap_qty,
            default_actual_sap_qty=d.remaining_qty,
        ))
    return lines


def aggregate_materials(
    client,
    allocation_ids: List[str],
    trace_id: str = ""
) -> List[AggregatedMaterial]:
    """
    Aggregate materials across multiple allocations (backward compat).

    For each material:
    - Sum allocated quantities
    - Sum already consumed quantities
    - Calculate remaining

    Used by fn_submit_consumption validation.

    Args:
        client: SmartsheetClient instance
        allocation_ids: List of allocation IDs to aggregate
        trace_id: Trace ID for logging

    Returns:
        List of AggregatedMaterial objects
    """
    manifest = get_manifest()

    # ALLOCATION_LOG columns
    col_alloc_id = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.ALLOCATION_ID)
    col_material  = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.MATERIAL_CODE)
    col_qty       = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.QUANTITY)
    col_uom       = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.UOM)
    col_tag_id    = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.TAG_SHEET_ID)

    # CONSUMPTION_LOG columns
    col_cons_alloc_id = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.ALLOCATION_ID)
    col_cons_qty      = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.QUANTITY)
    col_cons_mat      = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.MATERIAL_CODE)
    col_cons_tag      = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.TAG_SHEET_ID)

    # 1. Fetch and parse ALLOCATION_LOG
    alloc_rows = _parse_rows(client.get_sheet(Sheet.ALLOCATION_LOG))

    selected = [r for r in alloc_rows if r.get(col_alloc_id) in allocation_ids]
    tag_ids  = {r.get(col_tag_id) for r in selected if r.get(col_tag_id)}

    # 2. Aggregate allocated qty and uom by material
    allocated_by_material: Dict[str, float] = {}
    uom_by_material: Dict[str, str] = {}
    for row in selected:
        material = row.get(col_material, "")
        qty      = float(row.get(col_qty) or 0)
        uom      = row.get(col_uom, "")
        if material:
            allocated_by_material[material] = allocated_by_material.get(material, 0.0) + qty
            if material not in uom_by_material:
                uom_by_material[material] = uom

    # 3. Fetch and parse CONSUMPTION_LOG — prefer ALLOCATION_ID match, fall back to tag
    cons_rows = _parse_rows(client.get_sheet(Sheet.CONSUMPTION_LOG))
    alloc_id_set = set(allocation_ids)

    consumed_by_material: Dict[str, float] = {}
    for row in cons_rows:
        if row.get(col_cons_alloc_id) in alloc_id_set or row.get(col_cons_tag) in tag_ids:
            material = row.get(col_cons_mat, "")
            qty = float(row.get(col_cons_qty) or 0)
            if material:
                consumed_by_material[material] = consumed_by_material.get(material, 0.0) + qty

    # 4. Build result
    aggregated = [
        AggregatedMaterial(
            canonical_code=material,
            allocated_qty=alloc_qty,
            already_consumed=consumed_by_material.get(material, 0.0),
            remaining_qty=alloc_qty - consumed_by_material.get(material, 0.0),
            uom=uom_by_material.get(material, ""),
        )
        for material, alloc_qty in allocated_by_material.items()
    ]

    logger.info(f"[{trace_id}] Aggregated {len(aggregated)} materials from {len(allocation_ids)} allocations")
    return aggregated
