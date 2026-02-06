"""
Schedule Handler
================

Transforms Production Planning staging row data into fn_schedule_tag payloads.

PATTERN: Follows tag_handler.py exactly
- Dedup check FIRST with early return
- Direct internal call to fn_schedule_tag
- DispatchResult with only valid fields

v1.6.6: Fixed to match tag_handler pattern
"""

import logging
from pydantic import ValidationError

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared import (
    get_smartsheet_client,
    get_manifest,
    ScheduleTagRequest,
    generate_trace_id,
    get_cell_value_by_logical_name,
    # SOTA exception handling
    create_exception,
    ReasonCode,
    ExceptionSeverity,
    ExceptionSource,
    Sheet,
    Column,
)
from ..models import RowEvent, DispatchResult

logger = logging.getLogger(__name__)


def handle_schedule_ingest(event: RowEvent) -> DispatchResult:
    """
    Handle Production Planning from staging sheet.
    
    PATTERN: Same as tag_handler.py:
    1. Early dedup check with RETURN (not just check)
    2. Fetch staging row data
    3. Build ScheduleTagRequest
    4. Direct internal call to fn_schedule_tag
    5. Return DispatchResult with valid fields only
    
    Args:
        event: RowEvent from dispatcher containing sheet_id and row_id
        
    Returns:
        DispatchResult with status, handler, message, trace_id, details
    """
    trace_id = event.trace_id or generate_trace_id()
    logger.info(f"[{trace_id}] Processing schedule staging row: {event.row_id}")
    
    # Build deterministic client_request_id (v1.6.7)
    # For CREATE: simple row_id based (dedup retries)
    # For UPDATE: include timestamp from webhook (allow reschedules, dedup retries)
    if event.action == "created":
        client_request_id = f"staging-schedule-{event.row_id}-created"
    else:
        # Use webhook timestamp for updates (dedup within same timestamp)
        timestamp = event.timestamp_utc or str(int(__import__('time').time()))
        client_request_id = f"staging-schedule-{event.row_id}-updated-{timestamp}"
    
    try:
        client = get_smartsheet_client()
        manifest = get_manifest()
        
        # =====================================================================
        # EARLY DEDUP CHECK (v1.6.5 pattern from tag_handler)
        # Check if this staging row was already processed BEFORE any other work
        # =====================================================================
        existing = client.find_row(
            Sheet.PRODUCTION_PLANNING,
            Column.PRODUCTION_PLANNING.CLIENT_REQUEST_ID,
            client_request_id
        )
        if existing:
            schedule_id = existing.get("Schedule ID") or existing.get("id")
            logger.info(f"[{trace_id}] DEDUP: Staging row {event.row_id} already processed as {schedule_id}")
            return DispatchResult(
                status="ALREADY_PROCESSED",
                handler="schedule_tag",
                message=f"This staging row was already processed as {schedule_id}",
                trace_id=trace_id,
                details={"schedule_id": schedule_id, "client_request_id": client_request_id}
            )
        
        # Fetch staging row data
        staging_sheet_id = manifest.get_sheet_id(Sheet.PRODUCTION_PLANNING_STAGING)
        row_data = client.get_row(staging_sheet_id, event.row_id)
        
        if not row_data:
            logger.error(f"[{trace_id}] Staging row not found: {event.row_id}")
            return DispatchResult(
                status="ERROR",
                handler="schedule_tag",
                message=f"Staging row {event.row_id} not found",
                trace_id=trace_id
            )
        
        # Extract fields from row using logical names (3 args: row_data, sheet, column)
        tag_id = get_cell_value_by_logical_name(
            row_data, Sheet.PRODUCTION_PLANNING_STAGING, "TAG_SHEET_ID"
        )
        planned_date = get_cell_value_by_logical_name(
            row_data, Sheet.PRODUCTION_PLANNING_STAGING, "PLANNED_DATE"
        )
        shift = get_cell_value_by_logical_name(
            row_data, Sheet.PRODUCTION_PLANNING_STAGING, "SHIFT"
        )
        machine_id = get_cell_value_by_logical_name(
            row_data, Sheet.PRODUCTION_PLANNING_STAGING, "MACHINE_ASSIGNED"
        )
        planned_qty = get_cell_value_by_logical_name(
            row_data, Sheet.PRODUCTION_PLANNING_STAGING, "PLANNED_QUANTITY"
        )
        # NOTE: REQUESTED_BY column does not exist in staging sheet (per manifest)
        # Use event.actor_id - already resolved to email by fn_event_dispatcher
        
        # Validate required field
        if not tag_id:
            logger.error(f"[{trace_id}] Missing Tag Sheet ID in staging row")
            exception_id = create_exception(
                client=client,
                trace_id=trace_id,
                reason_code=ReasonCode.SCHEDULE_INVALID_DATA,
                severity=ExceptionSeverity.MEDIUM,
                source=ExceptionSource.SCHEDULE,
                message=f"Missing Tag Sheet ID in staging row {event.row_id}",
                client_request_id=client_request_id
            )
            return DispatchResult(
                status="EXCEPTION_LOGGED",
                handler="schedule_tag",
                message="Missing required field: Tag Sheet ID",
                trace_id=trace_id,
                details={"exception_id": exception_id}
            )
        
        # Use actor_id from event (already resolved to email by fn_event_dispatcher)
        requested_by_email = event.actor_id or "System"
        
        # Build request (validation happens in Pydantic)
        request = ScheduleTagRequest(
            tag_id=str(tag_id),
            planned_date=str(planned_date) if planned_date else None,
            shift=str(shift) if shift else None,
            machine_id=str(machine_id) if machine_id else None,
            planned_qty_m2=float(planned_qty) if planned_qty else None,
            requested_by=requested_by_email,
            client_request_id=client_request_id,
        )
        
        logger.info(f"[{trace_id}] Built schedule request for tag {tag_id}")
        
        # =====================================================================
        # DIRECT INTERNAL CALL (same pattern as tag_handler)
        # =====================================================================
        from fn_schedule_tag import main as schedule_tag_main
        import azure.functions as func
        import json
        
        mock_req = func.HttpRequest(
            method="POST",
            url="/api/production/schedule",
            body=json.dumps(request.model_dump()).encode(),
            headers={"Content-Type": "application/json"}
        )
        
        response = schedule_tag_main(mock_req)
        result_data = json.loads(response.get_body())
        
        # Check if core function already logged an exception
        exception_id = result_data.get("exception_id")
        status = result_data.get("status", "OK")
        if exception_id and status not in ("OK", "ALREADY_SCHEDULED", "RELEASED_FOR_NESTING"):
            status = "EXCEPTION_LOGGED"
        
        logger.info(f"[{trace_id}] fn_schedule_tag returned: {status}")
        
        return DispatchResult(
            status=status,
            handler="schedule_tag",
            message=result_data.get("message", "Schedule processed"),
            trace_id=trace_id,
            details=result_data
        )
        
    except ValidationError as e:
        logger.error(f"[{trace_id}] Validation error: {e}")
        exception_id = create_exception(
            client=client,
            trace_id=trace_id,
            reason_code=ReasonCode.SCHEDULE_INVALID_DATA,
            severity=ExceptionSeverity.MEDIUM,
            source=ExceptionSource.SCHEDULE,
            message=f"Schedule validation error: {str(e)}",
            client_request_id=client_request_id
        )
        return DispatchResult(
            status="EXCEPTION_LOGGED",
            handler="schedule_tag",
            message=f"Validation error: {str(e)}",
            trace_id=trace_id,
            details={"exception_id": exception_id}
        )
        
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[{trace_id}] Unexpected error processing schedule staging row")
        exception_id = create_exception(
            client=client,
            trace_id=trace_id,
            reason_code=ReasonCode.SYSTEM_ERROR,
            severity=ExceptionSeverity.HIGH,
            source=ExceptionSource.SCHEDULE,
            message=f"Unexpected error: {str(e)}",
            client_request_id=client_request_id
        )
        return DispatchResult(
            status="EXCEPTION_LOGGED",
            handler="schedule_tag",
            message=f"Unexpected error: {str(e)}",
            trace_id=trace_id,
            details={"exception_id": exception_id}
        )
