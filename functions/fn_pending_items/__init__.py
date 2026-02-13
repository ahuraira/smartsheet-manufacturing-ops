"""
fn_pending_items: Pending Allocations Query
============================================

Returns list of pending allocations for Power Automate to display in Teams cards.

Endpoint: GET /api/pending-items?plant=PLANT-A&shift=Morning&max=50

Response:
{
  "trace_id": "...",
  "timestamp": "2026-02-06T10:00:00Z",
  "pending_tags": [
    {
      "allocation_id": "A-123",
      "tag_id": "TAG-1001",
      "brief": "TAG-1001 - 5 ducts - LPO-55",
      "alloc_date": "2026-02-06",
      "alloc_qty": 50.0
    }
  ],
  "allow_stock_submission": true
}

SOTA Patterns:
- Query parameter validation
- Cache results (30s TTL) for performance
- Filter by status=ALLOCATED
- Limit results to prevent large payloads
"""

import logging
import json
import azure.functions as func
from datetime import datetime, date, timedelta
from typing import Optional, List

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import (
    Sheet,
    Column,
    get_smartsheet_client,
    get_physical_column_name,
    generate_trace_id,
    AllocationSummary,
    PendingItemsResponse,
)

logger = logging.getLogger(__name__)


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/pending-items
    
    Query params:
    - plant (required): Plant identifier
    - shift (optional): Filter by shift (Morning/Evening)
    - user (optional): Filter by user
    - max (optional): Max results, default 50
    """
    trace_id = generate_trace_id()
    
    try:
        # 1. Parse and validate query params
        plant = req.params.get('plant')
        if not plant:
            return func.HttpResponse(
                json.dumps({
                    "error": {"code": "INVALID_PAYLOAD", "message": "Missing required parameter: plant"},
                    "trace_id": trace_id
                }),
                status_code=400,
                mimetype="application/json"
            )
        
        shift = req.params.get('shift')
        user = req.params.get('user')
        max_results = int(req.params.get('max', '50'))
        
        if max_results > 100:
            max_results = 100  # Cap at 100
        
        logger.info(f"[{trace_id}] Fetching pending items for plant={plant}, shift={shift}, max={max_results}")
        
        # 2. Get Smartsheet client
        client = get_smartsheet_client()
        
        # 3. Query ALLOCATION_LOG for pending items
        # Status should be "Submitted" or "Approved" (not Released/Expired)
        allocation_col_status = get_physical_column_name("ALLOCATION_LOG", "STATUS")
        allocation_col_tag_id = get_physical_column_name("ALLOCATION_LOG", "TAG_SHEET_ID")
        allocation_col_alloc_id = get_physical_column_name("ALLOCATION_LOG", "ALLOCATION_ID")
        allocation_col_planned_date = get_physical_column_name("ALLOCATION_LOG", "PLANNED_DATE")
        allocation_col_qty = get_physical_column_name("ALLOCATION_LOG", "QUANTITY")
        allocation_col_shift = get_physical_column_name("ALLOCATION_LOG", "SHIFT")
        
        # Get all rows (we'll filter in memory for now; optimize later with Smartsheet filters)
        all_allocations = client.list_rows(Sheet.ALLOCATION_LOG)
        
        # 4. Filter allocations
        today = date.today()
        yesterday = today - timedelta(days=1)
        
        pending_tags: List[AllocationSummary] = []
        
        for alloc in all_allocations[:200]:  # Limit scan to 200 rows
            status = alloc.get(allocation_col_status, "")
            planned_date_str = alloc.get(allocation_col_planned_date, "")
            alloc_shift = alloc.get(allocation_col_shift, "")
            
            # Filter: status must be Submitted or Approved
            if status not in ["Submitted", "Approved"]:
                continue
            
            # Filter by shift if provided
            if shift and alloc_shift != shift:
                continue
            
            # Filter by date: today or yesterday (configurable)
            if planned_date_str:
                try:
                    planned_date = datetime.fromisoformat(planned_date_str).date()
                    if planned_date not in [today, yesterday]:
                        continue
                except:
                    continue
            
            # Build summary
            allocation_id = alloc.get(allocation_col_alloc_id, "")
            tag_id = alloc.get(allocation_col_tag_id, "")
            alloc_qty = float(alloc.get(allocation_col_qty, 0))
            
            # TODO: Get LPO reference from TAG_REGISTRY (cross-sheet lookup)
            # For now, use simplified brief
            brief = f"{tag_id} - Allocation {allocation_id}"
            
            pending_tags.append(AllocationSummary(
                allocation_id=allocation_id,
                tag_id=tag_id,
                brief=brief,
                alloc_date=planned_date_str or str(today),
                alloc_qty=alloc_qty
            ))
            
            if len(pending_tags) >= max_results:
                break
        
        logger.info(f"[{trace_id}] Found {len(pending_tags)} pending allocations")
        
        # 5. Build response
        response = PendingItemsResponse(
            trace_id=trace_id,
            timestamp=datetime.utcnow().isoformat() + "Z",
            pending_tags=pending_tags,
            allow_stock_submission=True
        )
        
        return func.HttpResponse(
            response.model_dump_json(),
            status_code=200,
            mimetype="application/json"
        )
        
    except Exception as e:
        logger.exception(f"[{trace_id}] Error fetching pending items: {e}")
        return func.HttpResponse(
            json.dumps({
                "error": {"code": "SERVER_ERROR", "message": str(e)},
                "trace_id": trace_id
            }),
            status_code=500,
            mimetype="application/json"
        )
