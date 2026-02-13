"""
fn_allocations_aggregate: Aggregate Materials Across Allocations
=================================================================

Aggregates material requirements across selected allocations to prepare
for consumption submission.

Endpoint: POST /api/allocations/aggregate

Request:
{
  "allocation_ids": ["A-123", "A-124"],
  "trace_id": "..."
}

Response:
{
  "trace_id": "...",
  "allocations": [
    {"allocation_id": "A-123", "tag_id": "TAG-1001"},
    {"allocation_id": "A-124", "tag_id": "TAG-1002"}
  ],
  "aggregated_materials": [
    {
      "canonical_code": "MAT-001",
      "allocated_qty": 100.0,
      "already_consumed": 10.0,
      "remaining_qty": 90.0,
      "uom": "SQM"
    }
  ]
}

SOTA Patterns:
- Return allocation metadata for confirmation
- Calculate already consumed to prevent over-consumption
- Return empty arrays if no data (not error)
"""

import logging
import json
import azure.functions as func

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import (
    get_smartsheet_client,
    generate_trace_id,
    AllocationAggregateRequest,
    AllocationAggregateResponse,
)
from shared.allocation_service import aggregate_materials

logger = logging.getLogger(__name__)


def main(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/allocations/aggregate"""
    trace_id = generate_trace_id()
    
    try:
        # 1. Parse request body
        try:
            body = req.get_json()
        except ValueError:
            return func.HttpResponse(
                json.dumps({
                    "error": {"code": "INVALID_PAYLOAD", "message": "Invalid JSON"},
                    "trace_id": trace_id
                }),
                status_code=400,
                mimetype="application/json"
            )
        
        # 2. Validate with Pydantic
        try:
            request = AllocationAggregateRequest(**body)
        except Exception as e:
            return func.HttpResponse(
                json.dumps({
                    "error": {"code": "INVALID_PAYLOAD", "message": str(e)},
                    "trace_id": trace_id
                }),
                status_code=400,
                mimetype="application/json"
            )
        
        if request.trace_id:
            trace_id = request.trace_id
        
        allocation_ids = request.allocation_ids or []
        
        if not allocation_ids:
            return func.HttpResponse(
                json.dumps({
                    "error": {"code": "INVALID_PAYLOAD", "message": "No allocation_ids provided"},
                    "trace_id": trace_id
                }),
                status_code=400,
                mimetype="application/json"
            )
        
        logger.info(f"[{trace_id}] Aggregating {len(allocation_ids)} allocations")
        
        # 3. Get Smartsheet client
        client = get_smartsheet_client()
        
        # 4. Aggregate materials
        aggregated = aggregate_materials(client, allocation_ids, trace_id)
        
        # 5. Build allocation metadata
        from shared import Sheet, Column
        from shared.manifest import get_manifest
        manifest = get_manifest()
        
        col_alloc_id = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.ALLOCATION_ID)
        col_tag_id = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.TAG_SHEET_ID)
        
        all_allocations = client.list_rows(Sheet.ALLOCATION_LOG)
        
        allocations_metadata = []
        for alloc in all_allocations:
            alloc_id = alloc.get(col_alloc_id)
            if alloc_id in allocation_ids:
                allocations_metadata.append({
                    "allocation_id": alloc_id,
                    "tag_id": alloc.get(col_tag_id, "")
                })
        
        # 6. Build response
        response = AllocationAggregateResponse(
            trace_id=trace_id,
            allocations=allocations_metadata,
            aggregated_materials=aggregated
        )
        
        logger.info(f"[{trace_id}] Returning {len(aggregated)} aggregated materials")
        
        return func.HttpResponse(
            response.model_dump_json(),
            status_code=200,
            mimetype="application/json"
        )
        
    except Exception as e:
        logger.exception(f"[{trace_id}] Error aggregating allocations: {e}")
        return func.HttpResponse(
            json.dumps({
                "error": {"code": "SERVER_ERROR", "message": str(e)},
                "trace_id": trace_id
            }),
            status_code=500,
            mimetype="application/json"
        )
