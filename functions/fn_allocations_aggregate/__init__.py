"""
fn_allocations_aggregate: Get Allocation Details for a Tag Sheet
================================================================

Returns rich allocation details for a tag sheet, and builds a pre-filled
consumption card payload for use in Power Automate adaptive cards.

Endpoint: POST /api/allocations/aggregate

Primary request (Power Automate flow):
{
  "tag_id": "TAG-001",
  "trace_id": "..."
}

Backward-compat request (explicit allocation IDs):
{
  "allocation_ids": ["ALLOC-20260311-ABC123"],
  "trace_id": "..."
}

Response:
{
  "trace_id": "...",
  "tag_id": "TAG-001",
  "allocation_details": [
    {
      "allocation_id": "ALLOC-20260311-ABC123",
      "sap_code": "10003456",
      "nesting_description": "Aluminium Tape 50mm",
      "sap_qty": 4.0,
      "sap_uom": "ROL",
      "raw_qty": 100.0,
      "raw_uom": "m",
      "already_consumed": 0.0,
      "remaining_qty": 4.0,
      "stock_check_flag": "Green",
      "planned_date": "2026-03-11",
      "shift": "Morning"
    }
  ],
  "consumption_card_lines": [
    {
      "allocation_id": "ALLOC-20260311-ABC123",
      "sap_code": "10003456",
      "nesting_description": "Aluminium Tape 50mm",
      "sap_uom": "ROL",
      "raw_uom": "m",
      "allocated_raw_qty": 100.0,
      "default_actual_raw_qty": 100.0,
      "allocated_sap_qty": 4.0,
      "default_actual_sap_qty": 4.0
    }
  ],
  "total_materials": 1
}
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
from shared.allocation_service import (
    get_allocation_details_by_tag,
    build_consumption_card_lines,
    aggregate_materials,
)
from shared.card_builder import build_consumption_card

logger = logging.getLogger(__name__)


def main(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/allocations/aggregate"""
    trace_id = generate_trace_id()

    try:
        # ── 1. Parse request ────────────────────────────────────────
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

        # ── 2. Validate with Pydantic ───────────────────────────────
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

        if not request.tag_id and not request.allocation_ids:
            return func.HttpResponse(
                json.dumps({
                    "error": {
                        "code": "INVALID_PAYLOAD",
                        "message": "Either tag_id or allocation_ids must be provided"
                    },
                    "trace_id": trace_id
                }),
                status_code=400,
                mimetype="application/json"
            )

        # ── 3. Get Smartsheet client ────────────────────────────────
        client = get_smartsheet_client()

        # ── 4. Dispatch: tag_id (primary) or allocation_ids (compat) ─
        if request.tag_id:
            # Primary path: Power Automate sends tag_id from pending-items card
            tag_id = request.tag_id
            logger.info(f"[{trace_id}] Fetching allocation details for tag {tag_id}")

            details = get_allocation_details_by_tag(client, tag_id, trace_id)
            card_lines = build_consumption_card_lines(details)

        else:
            # Backward-compat path: explicit allocation_ids provided
            allocation_ids = request.allocation_ids or []
            logger.info(f"[{trace_id}] Aggregating {len(allocation_ids)} explicit allocation IDs")

            # Re-derive tag_id from the first allocation row if possible
            tag_id = None
            aggregated = aggregate_materials(client, allocation_ids, trace_id)

            # Build AllocationDetail shell from AggregatedMaterial for response parity
            from shared.flow_models import AllocationDetail, ConsumptionCardLine
            details = [
                AllocationDetail(
                    allocation_id="",
                    sap_code=m.canonical_code,
                    nesting_description=m.canonical_code,  # No description in compat mode
                    sap_qty=m.allocated_qty,
                    sap_uom=m.uom,
                    raw_qty=m.allocated_qty,
                    raw_uom=m.uom,
                    already_consumed=m.already_consumed,
                    remaining_qty=m.remaining_qty,
                    stock_check_flag="",
                    planned_date="",
                    shift="",
                )
                for m in aggregated
            ]
            card_lines = build_consumption_card_lines(details)

        # ── 5. Build adaptive card and return response ───────────────
        consumption_card = build_consumption_card(
            tag_id=request.tag_id or "",
            card_lines=card_lines,
        )

        response = AllocationAggregateResponse(
            trace_id=trace_id,
            tag_id=request.tag_id,
            allocation_details=details,
            consumption_card_lines=card_lines,
            consumption_card=consumption_card,
            total_materials=len(details),
        )

        logger.info(
            f"[{trace_id}] Returning {len(details)} allocation details "
            f"with {len(card_lines)} card lines for tag {request.tag_id}"
        )

        return func.HttpResponse(
            response.model_dump_json(),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        logger.exception(f"[{trace_id}] Error in fn_allocations_aggregate: {e}")
        try:
            from shared.audit import create_exception
            from shared.models import ReasonCode, ExceptionSeverity, ExceptionSource
            create_exception(
                client=client,
                trace_id=trace_id,
                reason_code=ReasonCode.SYSTEM_ERROR,
                severity=ExceptionSeverity.CRITICAL,
                source=ExceptionSource.ALLOCATION,
                message=f"fn_allocations_aggregate unhandled error: {str(e)}",
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
