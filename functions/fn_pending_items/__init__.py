"""
fn_pending_items: Pending Allocations Query
============================================

Returns list of pending allocations for Power Automate to display in Teams cards.

Endpoint: GET /api/pending-items?shift=Morning&max=50

Response:
{
  "trace_id": "...",
  "timestamp": "2026-02-06T10:00:00Z",
  "pending_tags": [
    {
      "allocation_id": "A-123",
      "tag_id": "TAG-1001",
      "brief": "TAG-1001 - Allocation A-123",
      "alloc_date": "2026-02-06",
      "alloc_qty": 50.0
    }
  ],
  "allow_stock_submission": true
}
"""

import logging
import json
import azure.functions as func
from datetime import datetime, date, timedelta
from typing import List

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import (
    Sheet,
    Column,
    get_smartsheet_client,
    generate_trace_id,
    AllocationSummary,
    PendingItemsResponse,
    TagChoice,
    now_uae,
)
from shared.helpers import parse_float_safe
from shared.manifest import get_manifest

logger = logging.getLogger(__name__)


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    GET /api/pending-items

    Query params:
    - shift (optional): Filter by shift (Morning/Evening)
    - max (optional): Max results, default 50
    """
    trace_id = generate_trace_id()

    try:
        # 1. Parse query params (plant check removed - no plant column in ALLOCATION_LOG)
        shift = req.params.get('shift')
        max_results = min(int(req.params.get('max', '50')), 100)

        logger.info(f"[{trace_id}] Fetching pending items shift={shift!r} max={max_results}")

        # 2. Get client + manifest
        client = get_smartsheet_client()
        manifest = get_manifest()

        # 3. Resolve physical column names from manifest
        col_status       = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.STATUS)
        col_alloc_id     = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.ALLOCATION_ID)
        col_tag_id       = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.TAG_SHEET_ID)
        col_planned_date = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.PLANNED_DATE)
        col_qty          = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.QUANTITY)
        col_shift        = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.SHIFT)

        # 4. Fetch sheet (get_sheet returns columns + rows in one call)
        sheet_data = client.get_sheet(Sheet.ALLOCATION_LOG)
        columns    = sheet_data.get("columns", [])
        col_id_to_name = {col["id"]: col["title"] for col in columns}

        # 5. Filter rows
        today     = date.today()
        yesterday = today - timedelta(days=1)

        pending_tags: List[AllocationSummary] = []

        for raw_row in sheet_data.get("rows", []):
            # Convert raw Smartsheet row -> {physical_col_name: value}
            row = {
                col_id_to_name[cell["columnId"]]: (cell.get("value") or cell.get("displayValue"))
                for cell in raw_row.get("cells", [])
                if cell.get("columnId") in col_id_to_name
            }

            # --- Filter by status ---
            if row.get(col_status) not in ("Submitted", "Approved"):
                continue

            # --- Filter by shift (optional) ---
            if shift and row.get(col_shift) != shift:
                continue

            # --- Filter by date: today or yesterday ---
            planned_date_str = row.get(col_planned_date, "")
            if planned_date_str:
                try:
                    planned_date = datetime.fromisoformat(str(planned_date_str)).date()
                    if planned_date not in (today, yesterday):
                        continue
                except (ValueError, TypeError):
                    continue  # Skip rows with unparseable dates

            allocation_id = row.get(col_alloc_id, "")
            tag_id        = row.get(col_tag_id, "")
            alloc_qty     = parse_float_safe(row.get(col_qty), default=0.0)
            brief         = f"{tag_id} - Allocation {allocation_id}"

            pending_tags.append(AllocationSummary(
                allocation_id=allocation_id,
                tag_id=tag_id,
                brief=brief,
                alloc_date=planned_date_str or str(today),
                alloc_qty=alloc_qty,
            ))

            if len(pending_tags) >= max_results:
                break

        logger.info(f"[{trace_id}] Found {len(pending_tags)} pending allocations")

        # 6. Build deduplicated tag_choices for adaptive card Input.ChoiceSet
        seen_tags: set = set()
        tag_choices = []
        for tag in pending_tags:
            if tag.tag_id and tag.tag_id not in seen_tags:
                seen_tags.add(tag.tag_id)
                tag_choices.append(TagChoice(title=tag.tag_id, value=tag.tag_id))

        # 7. Build response
        response = PendingItemsResponse(
            trace_id=trace_id,
            timestamp=now_uae().isoformat(),
            pending_tags=pending_tags,
            allow_stock_submission=True,
            tag_choices=tag_choices,
        )

        return func.HttpResponse(
            response.model_dump_json(),
            status_code=200,
            mimetype="application/json",
        )

    except Exception as e:
        logger.exception(f"[{trace_id}] Error fetching pending items: {e}")
        try:
            from shared.audit import create_exception
            from shared.models import ReasonCode, ExceptionSeverity, ExceptionSource
            create_exception(
                client=client,
                trace_id=trace_id,
                reason_code=ReasonCode.SYSTEM_ERROR,
                severity=ExceptionSeverity.CRITICAL,
                source=ExceptionSource.ALLOCATION,
                message=f"fn_pending_items unhandled error: {str(e)}",
            )
        except Exception:
            logger.error(f"[{trace_id}] Failed to create exception record")
        return func.HttpResponse(
            json.dumps({"error": {"code": "SERVER_ERROR", "message": str(e)}, "trace_id": trace_id}),
            status_code=500,
            mimetype="application/json",
        )
