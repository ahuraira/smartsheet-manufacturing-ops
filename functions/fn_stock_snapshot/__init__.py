"""
fn_stock_snapshot: Get Current Stock Snapshot
==============================================

Returns current inventory snapshot for a plant.

Endpoint: GET /api/stock/snapshot?plant=PLANT-A

Response:
{
  "trace_id": "...",
  "plant": "PLANT-A",
  "snapshot_time": "2026-02-06T10:00:00Z",
  "lines": [
    {
      "canonical_code": "MAT-001",
      "system_physical_closing": 1000.5,
      "uom": "SQM",
      "last_count": "2026-02-05"
    }
  ]
}

SOTA Patterns:
- Read from INVENTORY_SNAPSHOT sheet
- Return empty list if no data (not error)
- Include last count date for variance checking
"""

import logging
import json
import azure.functions as func
from datetime import datetime

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import (
    Sheet,
    Column,
    get_smartsheet_client,
    generate_trace_id,
    StockSnapshotLine,
    StockSnapshotResponse,
)
from shared.helpers import parse_float_safe
from shared.allocation_service import _parse_rows

logger = logging.getLogger(__name__)


def main(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/stock/snapshot?plant=PLANT-A"""
    trace_id = generate_trace_id()
    
    try:
        # 1. Parse query params
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
        
        logger.info(f"[{trace_id}] Fetching stock snapshot for plant={plant}")
        
        # 2. Get Smartsheet client
        client = get_smartsheet_client()
        
        # 3. Query INVENTORY_SNAPSHOT sheet
        # Note: Based on manifest, we need to find the column names
        # Assuming structure: CANONICAL_CODE, SYSTEM_QTY, UOM, LAST_COUNT_DATE, PLANT
        
        from shared.manifest import get_manifest
        manifest = get_manifest()
        
        # Check if INVENTORY_SNAPSHOT exists in manifest
        try:
            sheet_id = manifest.get_sheet_id(Sheet.INVENTORY_SNAPSHOT)
        except Exception as e:
            logger.warning(f"[{trace_id}] INVENTORY_SNAPSHOT sheet not found in manifest: {e}")
            # Return empty snapshot
            response = StockSnapshotResponse(
                trace_id=trace_id,
                plant=plant,
                snapshot_time=datetime.utcnow().isoformat() + "Z",
                lines=[]
            )
            return func.HttpResponse(
                response.model_dump_json(),
                status_code=200,
                mimetype="application/json"
            )
        
        # Get all rows (filter by plant if column exists)
        all_inventory = _parse_rows(client.get_sheet(Sheet.INVENTORY_SNAPSHOT))
        
        # Build snapshot lines using manifest-based column lookups
        col_code = manifest.get_column_name(Sheet.INVENTORY_SNAPSHOT, Column.INVENTORY_SNAPSHOT.MATERIAL_CODE)
        col_sys_qty = manifest.get_column_name(Sheet.INVENTORY_SNAPSHOT, Column.INVENTORY_SNAPSHOT.SYSTEM_CLOSING)
        col_uom = manifest.get_column_name(Sheet.INVENTORY_SNAPSHOT, Column.INVENTORY_SNAPSHOT.UOM)
        col_last_count = manifest.get_column_name(Sheet.INVENTORY_SNAPSHOT, Column.INVENTORY_SNAPSHOT.LAST_COUNT_DATE)

        lines = []

        for row in all_inventory[:100]:  # Limit to 100 materials
            canonical_code = row.get(col_code, "UNKNOWN")
            try:
                system_qty = parse_float_safe(row.get(col_sys_qty), default=0.0)
            except (ValueError, TypeError):
                system_qty = 0.0
            uom = row.get(col_uom, "SQM")
            last_count = row.get(col_last_count, None)
            
            lines.append(StockSnapshotLine(
                canonical_code=canonical_code,
                system_physical_closing=system_qty,
                uom=uom,
                last_count=last_count
            ))
        
        logger.info(f"[{trace_id}] Returning {len(lines)} inventory lines")
        
        # 4. Build response
        response = StockSnapshotResponse(
            trace_id=trace_id,
            plant=plant,
            snapshot_time=datetime.utcnow().isoformat() + "Z",
            lines=lines
        )
        
        return func.HttpResponse(
            response.model_dump_json(),
            status_code=200,
            mimetype="application/json"
        )
        
    except Exception as e:
        logger.exception(f"[{trace_id}] Error fetching stock snapshot: {e}")
        try:
            from shared.audit import create_exception
            from shared.models import ReasonCode, ExceptionSeverity, ExceptionSource
            create_exception(
                client=client,
                trace_id=trace_id,
                reason_code=ReasonCode.SYSTEM_ERROR,
                severity=ExceptionSeverity.CRITICAL,
                source=ExceptionSource.ALLOCATION,
                message=f"fn_stock_snapshot unhandled error: {str(e)}",
            )
        except Exception:
            logger.error(f"[{trace_id}] Failed to create exception record")
        return func.HttpResponse(
            json.dumps({
                "error": {"code": "SERVER_ERROR", "message": str(e)},
                "trace_id": trace_id
            }),
            status_code=500,
            mimetype="application/json"
        )
