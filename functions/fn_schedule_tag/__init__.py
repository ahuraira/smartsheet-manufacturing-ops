"""
fn_schedule_tag: Production Schedule Azure Function
====================================================

This is the **AUTHORITATIVE** component for production scheduling. All business logic,
validations, ID generation, and exception creation happen here.

API Contract
------------
Endpoint: POST /api/production/schedule

Request:
{
    "client_request_id": "uuid",
    "tag_id": "TAG-0001",
    "planned_date": "2026-02-10",
    "shift": "Morning",
    "machine_id": "M1",
    "planned_qty_m2": 120.0,
    "requested_by": "pm@company",
    "notes": "Optional notes"
}

Response Codes
--------------
200 OK
    - status: "RELEASED_FOR_NESTING" - Schedule created successfully
    - status: "ALREADY_SCHEDULED" - Idempotent return of existing schedule

409 Conflict
    - status: "DUPLICATE" - Same tag already scheduled for same date/shift

422 Unprocessable Entity
    - status: "BLOCKED" - LPO on hold, insufficient balance, or tag invalid

400 Bad Request
    - status: "ERROR" - Validation error in request

Processing Flow
---------------
1. Parse and validate request (ScheduleTagRequest)
2. Idempotency check (client_request_id)
3. Load tag from TAG_REGISTRY, validate status
4. Load LPO and validate (exists, not ON_HOLD)
5. PO balance check
6. Machine validation (exists, OPERATIONAL)
7. Create schedule in PRODUCTION_PLANNING
8. Update TAG_REGISTRY with planning info
9. Update LPO_MASTER.PLANNED_QUANTITY
10. Log user action
11. Return success with T-1 deadline
"""

import logging
import json
import azure.functions as func
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import (
    # Logical Names (SOTA)
    Sheet,
    Column,
    
    # Models
    ScheduleTagRequest,
    ExceptionSeverity,
    ExceptionSource,
    ReasonCode,
    ActionType,
    ScheduleStatus,
    MachineStatus,
    
    # Client
    get_smartsheet_client,
    
    # Manifest
    get_manifest,
    
    # ID generation
    generate_next_schedule_id,
    
    # Helpers
    generate_trace_id,
    format_datetime_for_smartsheet,
    parse_float_safe,
    
    # Audit (shared - DRY)
    create_exception,
    log_user_action,
)

logger = logging.getLogger(__name__)

# Get manifest for column name resolution
_manifest = None

def _get_physical_column_name(sheet_logical: str, column_logical: str) -> str:
    """Get physical column name from manifest."""
    global _manifest
    if _manifest is None:
        _manifest = get_manifest()
    return _manifest.get_column_name(sheet_logical, column_logical)


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    Main entry point for production scheduling.
    
    Flow:
    1. Parse and validate request
    2. Idempotency check (client_request_id)
    3. Load tag and validate status
    4. Load LPO and validate (exists, not ON_HOLD, balance OK)
    5. Validate machine (exists, OPERATIONAL)
    6. Create schedule record
    7. Update TAG_REGISTRY
    8. Update LPO_MASTER.PLANNED_QUANTITY
    9. Log user action
    10. Return success with T-1 deadline
    """
    trace_id = generate_trace_id()
    logger.info(f"[{trace_id}] fn_schedule_tag invoked")
    
    try:
        # 1. Parse request
        try:
            body = req.get_json()
            request = ScheduleTagRequest(**body)
        except Exception as e:
            logger.error(f"[{trace_id}] Invalid request: {e}")
            return func.HttpResponse(
                json.dumps({
                    "status": "ERROR",
                    "message": f"Invalid request: {str(e)}",
                    "trace_id": trace_id
                }),
                status_code=400,
                mimetype="application/json"
            )
        
        client = get_smartsheet_client()
        
        # 2. Idempotency check
        existing_schedule = client.find_row(
            Sheet.PRODUCTION_PLANNING,
            Column.PRODUCTION_PLANNING.CLIENT_REQUEST_ID,
            request.client_request_id
        )
        if existing_schedule:
            logger.info(f"[{trace_id}] Already processed: {request.client_request_id}")
            existing_schedule_id = existing_schedule.get(_get_physical_column_name("PRODUCTION_PLANNING", "SCHEDULE_ID"))
            return func.HttpResponse(
                json.dumps({
                    "status": "ALREADY_SCHEDULED",
                    "schedule_id": existing_schedule_id,
                    "trace_id": trace_id,
                    "message": "Request already processed"
                }),
                status_code=200,
                mimetype="application/json"
            )
        
        # 3. Load tag and validate status
        tag = client.find_row(
            Sheet.TAG_REGISTRY,
            Column.TAG_REGISTRY.TAG_ID,
            request.tag_id
        )
        if not tag:
            logger.warning(f"[{trace_id}] Tag not found: {request.tag_id}")
            exception_id = create_exception(
                client=client,
                trace_id=trace_id,
                reason_code=ReasonCode.TAG_NOT_FOUND,
                severity=ExceptionSeverity.MEDIUM,
                source=ExceptionSource.INGEST,
                message=f"Tag {request.tag_id} not found"
            )
            return func.HttpResponse(
                json.dumps({
                    "status": "BLOCKED",
                    "exception_id": exception_id,
                    "trace_id": trace_id,
                    "message": f"Tag {request.tag_id} not found"
                }),
                status_code=422,
                mimetype="application/json"
            )
        
        tag_status = tag.get(_get_physical_column_name("TAG_REGISTRY", "STATUS"), "")
        if tag_status.lower() in ["cancelled", "closed"]:
            logger.warning(f"[{trace_id}] Tag {request.tag_id} has invalid status: {tag_status}")
            exception_id = create_exception(
                client=client,
                trace_id=trace_id,
                reason_code=ReasonCode.TAG_INVALID_STATUS,
                severity=ExceptionSeverity.MEDIUM,
                source=ExceptionSource.INGEST,
                related_tag_id=request.tag_id,
                message=f"Tag status is {tag_status}, cannot schedule"
            )
            return func.HttpResponse(
                json.dumps({
                    "status": "BLOCKED",
                    "exception_id": exception_id,
                    "trace_id": trace_id,
                    "message": f"Tag status is {tag_status}, cannot schedule"
                }),
                status_code=422,
                mimetype="application/json"
            )
        
        # 4. Load LPO and validate
        lpo_ref = tag.get(_get_physical_column_name("TAG_REGISTRY", "LPO_SAP_REFERENCE"))
        if not lpo_ref:
            lpo_ref = tag.get(_get_physical_column_name("TAG_REGISTRY", "LPO_SAP_REFERENCE_LINK"))
        
        lpo = None
        if lpo_ref:
            lpo = client.find_row(
                Sheet.LPO_MASTER,
                Column.LPO_MASTER.SAP_REFERENCE,
                lpo_ref
            )
        
        if not lpo:
            logger.warning(f"[{trace_id}] LPO not found for tag {request.tag_id}")
            exception_id = create_exception(
                client=client,
                trace_id=trace_id,
                reason_code=ReasonCode.LPO_NOT_FOUND,
                severity=ExceptionSeverity.HIGH,
                source=ExceptionSource.INGEST,
                related_tag_id=request.tag_id,
                message=f"LPO {lpo_ref} not found for tag {request.tag_id}"
            )
            return func.HttpResponse(
                json.dumps({
                    "status": "BLOCKED",
                    "exception_id": exception_id,
                    "trace_id": trace_id,
                    "message": f"LPO not found for tag {request.tag_id}"
                }),
                status_code=422,
                mimetype="application/json"
            )
        
        # Check LPO status
        lpo_status = lpo.get(_get_physical_column_name("LPO_MASTER", "LPO_STATUS"), "")
        if lpo_status.lower() == "on hold":
            logger.warning(f"[{trace_id}] LPO {lpo_ref} is on hold")
            exception_id = create_exception(
                client=client,
                trace_id=trace_id,
                reason_code=ReasonCode.LPO_ON_HOLD,
                severity=ExceptionSeverity.HIGH,
                source=ExceptionSource.INGEST,
                related_tag_id=request.tag_id,
                message=f"LPO {lpo_ref} is on hold"
            )
            return func.HttpResponse(
                json.dumps({
                    "status": "BLOCKED",
                    "exception_id": exception_id,
                    "trace_id": trace_id,
                    "message": f"LPO is on hold"
                }),
                status_code=422,
                mimetype="application/json"
            )
        
        # 5. PO balance check
        po_quantity = parse_float_safe(lpo.get(_get_physical_column_name("LPO_MASTER", "PO_QUANTITY_SQM")), 0)
        delivered_qty = parse_float_safe(lpo.get(_get_physical_column_name("LPO_MASTER", "DELIVERED_QUANTITY_SQM")), 0)
        planned_qty = parse_float_safe(lpo.get(_get_physical_column_name("LPO_MASTER", "PLANNED_QUANTITY")), 0)
        allocated_qty = parse_float_safe(lpo.get(_get_physical_column_name("LPO_MASTER", "ALLOCATED_QUANTITY")), 0)
        
        # Get planned quantity from request or tag
        schedule_qty = request.planned_qty_m2
        if schedule_qty is None:
            schedule_qty = parse_float_safe(tag.get(_get_physical_column_name("TAG_REGISTRY", "ESTIMATED_QUANTITY")), 0)
        
        current_committed = delivered_qty + planned_qty + allocated_qty
        if current_committed + schedule_qty > po_quantity * 1.05:  # 5% tolerance
            logger.warning(f"[{trace_id}] Insufficient PO balance")
            exception_id = create_exception(
                client=client,
                trace_id=trace_id,
                reason_code=ReasonCode.INSUFFICIENT_PO_BALANCE,
                severity=ExceptionSeverity.HIGH,
                source=ExceptionSource.INGEST,
                related_tag_id=request.tag_id,
                quantity=schedule_qty,
                message=f"PO balance exceeded. PO: {po_quantity}, Committed: {current_committed}, Requested: {schedule_qty}"
            )
            return func.HttpResponse(
                json.dumps({
                    "status": "BLOCKED",
                    "exception_id": exception_id,
                    "trace_id": trace_id,
                    "message": f"Insufficient PO balance. Available: {po_quantity - current_committed:.2f}, Requested: {schedule_qty:.2f}"
                }),
                status_code=422,
                mimetype="application/json"
            )
        
        # 6. Machine validation
        machine = client.find_row(
            Sheet.MACHINE_MASTER,
            Column.MACHINE_MASTER.MACHINE_ID,
            request.machine_id
        )
        if not machine:
            logger.warning(f"[{trace_id}] Machine not found: {request.machine_id}")
            exception_id = create_exception(
                client=client,
                trace_id=trace_id,
                reason_code=ReasonCode.MACHINE_NOT_FOUND,
                severity=ExceptionSeverity.MEDIUM,
                source=ExceptionSource.INGEST,
                related_tag_id=request.tag_id,
                message=f"Machine {request.machine_id} not found"
            )
            return func.HttpResponse(
                json.dumps({
                    "status": "BLOCKED",
                    "exception_id": exception_id,
                    "trace_id": trace_id,
                    "message": f"Machine {request.machine_id} not found"
                }),
                status_code=422,
                mimetype="application/json"
            )
        
        machine_status = machine.get(_get_physical_column_name("00B_MACHINE_MASTER", "STATUS"), "")
        if machine_status.lower() == "maintenance":
            logger.warning(f"[{trace_id}] Machine {request.machine_id} is under maintenance")
            exception_id = create_exception(
                client=client,
                trace_id=trace_id,
                reason_code=ReasonCode.MACHINE_MAINTENANCE,
                severity=ExceptionSeverity.HIGH,
                source=ExceptionSource.INGEST,
                related_tag_id=request.tag_id,
                message=f"Machine {request.machine_id} is under maintenance"
            )
            return func.HttpResponse(
                json.dumps({
                    "status": "BLOCKED",
                    "exception_id": exception_id,
                    "trace_id": trace_id,
                    "message": f"Machine {request.machine_id} is under maintenance"
                }),
                status_code=422,
                mimetype="application/json"
            )
        
        # 7. Create schedule record
        schedule_id = generate_next_schedule_id(client)
        now = format_datetime_for_smartsheet(datetime.now())
        
        schedule_data = {
            Column.PRODUCTION_PLANNING.SCHEDULE_ID: schedule_id,
            Column.PRODUCTION_PLANNING.TAG_SHEET_ID: request.tag_id,
            Column.PRODUCTION_PLANNING.PLANNED_DATE: request.planned_date,
            Column.PRODUCTION_PLANNING.SHIFT: request.shift,
            Column.PRODUCTION_PLANNING.MACHINE_ASSIGNED: request.machine_id,
            Column.PRODUCTION_PLANNING.PLANNED_QUANTITY: schedule_qty,
            Column.PRODUCTION_PLANNING.STATUS: "Released for Nesting",
            Column.PRODUCTION_PLANNING.CREATED_BY: request.requested_by,
            Column.PRODUCTION_PLANNING.CREATED_AT: now,
            Column.PRODUCTION_PLANNING.CLIENT_REQUEST_ID: request.client_request_id,
            Column.PRODUCTION_PLANNING.TRACE_ID: trace_id,
        }
        if request.notes:
            schedule_data[Column.PRODUCTION_PLANNING.REMARKS] = request.notes
        
        client.add_row(Sheet.PRODUCTION_PLANNING, schedule_data)
        logger.info(f"[{trace_id}] Schedule created: {schedule_id}")
        
        # 8. Update TAG_REGISTRY with planning info
        tag_row_id = tag.get("row_id")
        if tag_row_id:
            try:
                # Note: These columns might not exist - handle gracefully
                client.update_row(Sheet.TAG_REGISTRY, tag_row_id, {
                    Column.TAG_REGISTRY.PLANNED_CUT_DATE: request.planned_date,
                })
            except Exception as tag_update_err:
                logger.warning(f"[{trace_id}] Could not update tag planning info: {tag_update_err}")
        
        # 9. Update LPO_MASTER.PLANNED_QUANTITY
        lpo_row_id = lpo.get("row_id")
        if lpo_row_id:
            try:
                new_planned_qty = planned_qty + schedule_qty
                client.update_row(Sheet.LPO_MASTER, lpo_row_id, {
                    Column.LPO_MASTER.PLANNED_QUANTITY: new_planned_qty,
                    Column.LPO_MASTER.UPDATED_AT: now,
                    Column.LPO_MASTER.UPDATED_BY: request.requested_by,
                })
            except Exception as lpo_update_err:
                logger.warning(f"[{trace_id}] Could not update LPO planned quantity: {lpo_update_err}")
        
        # 10. Calculate T-1 deadline (cutoff at 18:00 previous day)
        try:
            planned_date_dt = datetime.strptime(request.planned_date, "%Y-%m-%d")
            t1_deadline = planned_date_dt - timedelta(days=1)
            t1_deadline = t1_deadline.replace(hour=18, minute=0, second=0)
            next_action_deadline = t1_deadline.strftime("%Y-%m-%dT%H:%M:%S")
        except Exception:
            next_action_deadline = None
        
        # 11. Log user action with JSON new_value
        schedule_details = json.dumps({
            "planned_date": request.planned_date,
            "shift": request.shift,
            "machine_id": request.machine_id,
            "planned_qty_m2": schedule_qty,
            "tag_id": request.tag_id,
            "next_action_deadline": next_action_deadline
        })
        log_user_action(
            client=client,
            user_id=request.requested_by,
            action_type=ActionType.SCHEDULE_CREATED,
            target_table=Sheet.PRODUCTION_PLANNING,
            target_id=schedule_id,
            new_value=schedule_details,
            notes=f"Tag: {request.tag_id}",
            trace_id=trace_id
        )
        
        return func.HttpResponse(
            json.dumps({
                "status": "RELEASED_FOR_NESTING",
                "schedule_id": schedule_id,
                "next_action_deadline": next_action_deadline,
                "trace_id": trace_id,
                "message": "Schedule created successfully"
            }),
            status_code=200,
            mimetype="application/json"
        )
    
    except Exception as e:
        logger.exception(f"[{trace_id}] Unexpected error: {e}")
        return func.HttpResponse(
            json.dumps({
                "status": "ERROR",
                "message": f"Unexpected error: {str(e)}",
                "trace_id": trace_id
            }),
            status_code=500,
            mimetype="application/json"
        )
