"""
Allocation Service
==================

Business logic for allocation queries and material aggregation.

This module provides DRY functions for:
- Querying pending allocations
- Aggregating materials across multiple allocations
- Calculating consumed vs allocated quantities

Used by:
- fn_pending_items
- fn_allocations_aggregate
- fn_submit_consumption (for validation)
"""

import logging
from typing import List, Dict, Optional
from datetime import date, timedelta

from .logical_names import Sheet, Column
from .manifest import get_manifest
from .flow_models import AllocationSummary, AggregatedMaterial

logger = logging.getLogger(__name__)


def get_pending_allocations(
    client,
    plant: str,
    shift: Optional[str] = None,
    max_results: int = 50,
    trace_id: str = ""
) -> List[AllocationSummary]:
    """
    Get list of pending allocations for a plant/shift.
    
    Args:
        client: SmartsheetClient instance
        plant: Plant identifier
        shift: Optional shift filter (Morning/Evening)
        max_results: Maximum results to return
        trace_id: Trace ID for logging
        
    Returns:
        List of AllocationSummary objects
    """
    manifest = get_manifest()
    
    # Get column names
    col_status = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.STATUS)
    col_tag_id = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.TAG_SHEET_ID)
    col_alloc_id = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.ALLOCATION_ID)
    col_planned_date = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.PLANNED_DATE)
    col_qty = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.QUANTITY)
    col_shift = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.SHIFT)
    
    # Get all allocations
    all_allocations = client.list_rows(Sheet.ALLOCATION_LOG)
    
    # Filter
    today = date.today()
    yesterday = today - timedelta(days=1)
    
    pending = []
    
    for alloc in all_allocations[:200]:  # Scan limit
        status = alloc.get(col_status, "")
        planned_date_str = alloc.get(col_planned_date, "")
        alloc_shift = alloc.get(col_shift, "")
        
        # Filter by status
        if status not in ["Submitted", "Approved"]:
            continue
        
        # Filter by shift
        if shift and alloc_shift != shift:
            continue
        
        # Filter by date (today or yesterday)
        if planned_date_str:
            try:
                from datetime import datetime
                planned_date = datetime.fromisoformat(planned_date_str).date()
                if planned_date not in [today, yesterday]:
                    continue
            except:
                continue
        
        allocation_id = alloc.get(col_alloc_id, "")
        tag_id = alloc.get(col_tag_id, "")
        alloc_qty = float(alloc.get(col_qty, 0))
        
        brief = f"{tag_id} - Allocation {allocation_id}"
        
        pending.append(AllocationSummary(
            allocation_id=allocation_id,
            tag_id=tag_id,
            brief=brief,
            alloc_date=planned_date_str or str(today),
            alloc_qty=alloc_qty
        ))
        
        if len(pending) >= max_results:
            break
    
    logger.info(f"[{trace_id}] Found {len(pending)} pending allocations")
    return pending


def aggregate_materials(
    client,
    allocation_ids: List[str],
    trace_id: str = ""
) -> List[AggregatedMaterial]:
    """
    Aggregate materials across multiple allocations.
    
    For each material:
    - Sum allocated quantities
    - Sum already consumed quantities
    - Calculate remaining
    
    Args:
        client: SmartsheetClient instance
        allocation_ids: List of allocation IDs to aggregate
        trace_id: Trace ID for logging
        
    Returns:
        List of AggregatedMaterial objects
    """
    manifest = get_manifest()
    
    # Get ALLOCATION_LOG columns
    col_alloc_id = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.ALLOCATION_ID)
    col_material = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.MATERIAL_CODE)
    col_qty = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.QUANTITY)
    
    # Get CONSUMPTION_LOG columns
    col_cons_material = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.MATERIAL_CODE)
    col_cons_qty = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.QUANTITY)
    col_cons_tag = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.TAG_SHEET_ID)
    
    # 1. Get allocations
    all_allocations = client.list_rows(Sheet.ALLOCATION_LOG)
    
    # Filter to selected allocation IDs
    selected_allocations = []
    tag_ids = set()
    
    for alloc in all_allocations:
        if alloc.get(col_alloc_id) in allocation_ids:
            selected_allocations.append(alloc)
            tag_ids.add(alloc.get(manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.TAG_SHEET_ID)))
    
    # 2. Aggregate allocated quantities by material
    allocated_by_material: Dict[str, float] = {}
    
    for alloc in selected_allocations:
        material = alloc.get(col_material, "")
        qty = float(alloc.get(col_qty, 0))
        
        if material:
            allocated_by_material[material] = allocated_by_material.get(material, 0) + qty
    
    # 3. Get consumption for these tag IDs
    all_consumptions = client.list_rows(Sheet.CONSUMPTION_LOG)
    
    consumed_by_material: Dict[str, float] = {}
    
    for cons in all_consumptions:
        tag_id = cons.get(col_cons_tag, "")
        if tag_id in tag_ids:
            material = cons.get(col_cons_material, "")
            qty = float(cons.get(col_cons_qty, 0))
            
            if material:
                consumed_by_material[material] = consumed_by_material.get(material, 0) + qty
    
    # 4. Build aggregated result
    aggregated = []
    
    for material, allocated_qty in allocated_by_material.items():
        already_consumed = consumed_by_material.get(material, 0)
        remaining_qty = allocated_qty - already_consumed
        
        aggregated.append(AggregatedMaterial(
            canonical_code=material,
            allocated_qty=allocated_qty,
            already_consumed=already_consumed,
            remaining_qty=remaining_qty,
            uom="SQM"  # TODO: Get from MATERIAL_MASTER
        ))
    
    logger.info(f"[{trace_id}] Aggregated {len(aggregated)} materials from {len(allocation_ids)} allocations")
    return aggregated
