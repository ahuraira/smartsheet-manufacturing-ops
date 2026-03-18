"""
fn_allocate — Material Allocation Endpoint
==========================================

Creates soft reservations in ALLOCATION_LOG for a parsed nesting session.

Trigger: POST /api/allocations/create
Payload:
    {
        "client_request_id": "uuid",
        "nest_session_id": "NEST-20260223-0001",
        "tag_id": "TAG-001",
        "planned_date": "2026-02-24",
        "shift": "Morning"
    }

Response:
    200 → ALLOCATED
    207 → PARTIAL_ALLOCATED (some shortages)
    409 → SHORTAGE (no materials available)
    400 → Invalid request
    500 → Internal error
"""

import json
import logging
import traceback
import uuid

import azure.functions as func

logger = logging.getLogger(__name__)


def main(req: func.HttpRequest) -> func.HttpResponse:
    """Handle allocation request."""

    trace_id = f"alloc-{uuid.uuid4().hex[:8]}"
    logger.info(f"[{trace_id}] fn_allocate invoked")

    # ── 1. Parse request body ───────────────────────────────────────
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON body", "trace_id": trace_id}),
            status_code=400,
            mimetype="application/json",
        )

    nest_session_id = body.get("nest_session_id")
    if not nest_session_id:
        return func.HttpResponse(
            json.dumps({
                "error": "nest_session_id is required",
                "trace_id": trace_id,
            }),
            status_code=400,
            mimetype="application/json",
        )

    client_request_id = body.get("client_request_id", str(uuid.uuid4()))
    tag_id = body.get("tag_id", "")
    planned_date = body.get("planned_date")
    shift = body.get("shift", "Morning")

    logger.info(
        f"[{trace_id}] Allocation request: session={nest_session_id}, "
        f"tag={tag_id}, date={planned_date}, shift={shift}"
    )

    # ── 2. Initialize client ────────────────────────────────────────
    try:
        from shared.smartsheet_client import get_smartsheet_client
        client = get_smartsheet_client()
    except Exception as e:
        logger.error(f"[{trace_id}] Failed to initialize Smartsheet client: {e}")
        return func.HttpResponse(
            json.dumps({
                "error": "Service initialization failed",
                "trace_id": trace_id,
            }),
            status_code=500,
            mimetype="application/json",
        )

    # ── 3. Resolve tag_id from PARSED_BOM if not provided ───────────
    if not tag_id:
        try:
            from shared.logical_names import Sheet, Column
            from shared.manifest import get_manifest

            manifest = get_manifest()
            col_session = manifest.get_column_name(
                Sheet.PARSED_BOM, Column.PARSED_BOM.NEST_SESSION_ID
            )

            # Read NESTING_LOG to find the tag_id for this session
            col_nest_session = manifest.get_column_name(
                Sheet.NESTING_LOG, Column.NESTING_LOG.NEST_SESSION_ID
            )
            col_nest_tag = manifest.get_column_name(
                Sheet.NESTING_LOG, Column.NESTING_LOG.TAG_SHEET_ID
            )

            nest_sheet = client.get_sheet(Sheet.NESTING_LOG)
            columns = nest_sheet.get("columns", [])
            col_id_to_name = {col["id"]: col["title"] for col in columns}

            for raw_row in nest_sheet.get("rows", []):
                row_data = {}
                for cell in raw_row.get("cells", []):
                    name = col_id_to_name.get(cell.get("columnId"))
                    if name:
                        row_data[name] = cell.get("value") or cell.get("displayValue")

                if row_data.get(col_nest_session) == nest_session_id:
                    tag_id = row_data.get(col_nest_tag, "")
                    break

            if not tag_id:
                logger.warning(f"[{trace_id}] Could not resolve tag_id from NESTING_LOG")

        except Exception as e:
            logger.warning(f"[{trace_id}] Tag resolution failed: {e}")

    # ── 4. Run allocation engine ────────────────────────────────────
    try:
        from shared.allocation_engine import allocate_for_session

        result = allocate_for_session(
            client=client,
            nest_session_id=nest_session_id,
            tag_id=tag_id,
            planned_date=planned_date,
            shift=shift,
            trace_id=trace_id,
            client_request_id=client_request_id,
        )

        # Determine HTTP status code
        if result.status == "ALLOCATED":
            status_code = 200
        elif result.status == "PARTIAL_ALLOCATED":
            status_code = 207  # Multi-Status
        else:
            status_code = 409  # Conflict — shortage

        response_data = {
            "trace_id": trace_id,
            "client_request_id": client_request_id,
            **result.to_dict(),
        }

        logger.info(
            f"[{trace_id}] Allocation complete: status={result.status}, "
            f"allocations={len(result.allocation_ids)}"
        )

        return func.HttpResponse(
            json.dumps(response_data, default=str),
            status_code=status_code,
            mimetype="application/json",
        )

    except Exception as e:
        logger.error(f"[{trace_id}] Allocation failed: {traceback.format_exc()}")
        try:
            from shared.audit import create_exception
            from shared.models import ReasonCode, ExceptionSeverity, ExceptionSource
            create_exception(
                client=client, trace_id=trace_id,
                reason_code=ReasonCode.SYSTEM_ERROR,
                severity=ExceptionSeverity.CRITICAL,
                source=ExceptionSource.ALLOCATION,
                message=f"fn_allocate unhandled error: {str(e)[:500]}"
            )
        except Exception:
            logger.error(f"[{trace_id}] Failed to create exception record")
        return func.HttpResponse(
            json.dumps({
                "error": f"Allocation failed: {str(e)}",
                "trace_id": trace_id,
            }),
            status_code=500,
            mimetype="application/json",
        )
