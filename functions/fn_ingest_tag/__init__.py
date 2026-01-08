"""
fn_ingest_tag: Tag Sheet Ingestion Azure Function
=================================================

This is the **AUTHORITATIVE** component for tag ingestion. All business logic,
validations, ID generation, and exception creation happen here.

Power Automate only orchestrates: it calls this function and reflects the result.

Endpoint
--------
POST /api/tags/ingest

Request Format
--------------
{
    "client_request_id": "uuid",        // Idempotency key (optional, auto-generated)
    "lpo_sap_reference": "SAP-001",     // SAP reference (required for lookup)
    "customer_lpo_ref": "CUST-001",     // Customer reference (alternative lookup)
    "lpo_id": "LPO-001",                // Internal ID (alternative lookup)
    "required_area_m2": 50.0,           // Required area in sqm (required)
    "requested_delivery_date": "2026-02-01",  // ISO date (required)
    "uploaded_by": "user@company.com",  // User email (required)
    "file_url": "https://...",          // Optional file URL for hashing
    "tag_name": "TAG-001 Rev A",        // Optional display name
    "metadata": {...}                   // Optional additional data
}

Response Codes
--------------
200 OK
    - "UPLOADED": Tag created successfully
    - "ALREADY_PROCESSED": Idempotent return of existing record

409 Conflict
    - "DUPLICATE": Same file hash already exists

422 Unprocessable Entity
    - "BLOCKED": LPO not found, on hold, or insufficient balance

400 Bad Request
    - "ERROR": Validation error in request

500 Internal Server Error
    - "ERROR": Unexpected server error

Processing Flow
---------------
1. Parse and validate request
2. Idempotency check (client_request_id)
3. File hash check (duplicate detection)
4. LPO validation (exists, not on hold, sufficient balance)
5. Generate Tag ID (sequence-based)
6. Create tag record
7. Log user action
8. Return success response

On validation failure, an exception record is created and the function
returns an error response with the exception_id.

Example
-------
>>> import requests
>>> response = requests.post(
...     "http://localhost:7071/api/tags/ingest",
...     json={
...         "lpo_sap_reference": "SAP-001",
...         "required_area_m2": 50.0,
...         "requested_delivery_date": "2026-02-01",
...         "uploaded_by": "user@company.com"
...     }
... )
>>> print(response.json())
{"status": "UPLOADED", "tag_id": "TAG-0001", "trace_id": "trace-..."}

See Also
--------
- docs/reference/api_reference.md : Complete API documentation
- Specifications/tag_ingestion_architecture.md : Detailed architecture
"""

import logging
import json
import azure.functions as func
from datetime import datetime
from typing import Optional

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import (
    # Config
    # SheetName, ColumnName (legacy) - removed
    
    # Logical Names (SOTA)
    Sheet,
    Column,

    # Models
    TagIngestRequest,
    TagStatus,
    LPOStatus,
    ExceptionSeverity,
    ExceptionSource,
    ReasonCode,
    ActionType,
    # Client
    get_smartsheet_client,
    # ID generation (sequence-based)
    generate_next_tag_id,
    generate_next_exception_id,
    # Helpers
    generate_trace_id,
    compute_file_hash_from_url,
    calculate_sla_due,
    format_datetime_for_smartsheet,
    parse_float_safe,
)

logger = logging.getLogger(__name__)


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    Main entry point for tag ingestion.
    
    Flow:
    1. Parse and validate request
    2. Idempotency check (client_request_id)
    3. File hash check (duplicate detection)
    4. LPO validation (exists, not on hold, sufficient balance)
    5. Generate Tag ID (using sequence counter)
    6. Create tag record
    7. Log user action
    8. Return success response
    
    On any validation failure:
    - Create exception record
    - Log user action (failed attempt)
    - Return error response with exception_id
    """
    trace_id = generate_trace_id()
    logger.info(f"[{trace_id}] Tag ingest request received")
    
    try:
        # 1. Parse request
        request_data = req.get_json()
        request = TagIngestRequest(**request_data)
        logger.info(f"[{trace_id}] Processing request: client_request_id={request.client_request_id}")
        
        # Get Smartsheet client
        client = get_smartsheet_client()
        
        # 2. Idempotency check
        # 2. Idempotency check
        existing = client.find_row(
            Sheet.TAG_REGISTRY,
            Column.TAG_REGISTRY.CLIENT_REQUEST_ID,
            request.client_request_id
        )
        if existing:
            logger.info(f"[{trace_id}] Duplicate client_request_id found, returning existing record")
            return func.HttpResponse(
                json.dumps({
                    "status": "ALREADY_PROCESSED",
                    "status": "ALREADY_PROCESSED",
                    "tag_id": existing.get(Column.TAG_REGISTRY.TAG_NAME) or existing.get(Column.TAG_REGISTRY.TAG_ID),
                    "trace_id": trace_id,
                    "message": "This request was already processed"
                }),
                status_code=200,
                mimetype="application/json"
            )
        
        # 3. File hash check (if file URL provided)
        file_hash = None
        if request.file_url:
            file_hash = compute_file_hash_from_url(request.file_url)
            if file_hash:
                existing_by_hash = client.find_row(
                    Sheet.TAG_REGISTRY,
                    Column.TAG_REGISTRY.FILE_HASH,
                    file_hash
                )
                if existing_by_hash:
                    logger.warning(f"[{trace_id}] Duplicate file hash detected")
                    exception_id = _create_exception(
                        client=client,
                        trace_id=trace_id,
                        reason_code=ReasonCode.DUPLICATE_UPLOAD,
                        severity=ExceptionSeverity.MEDIUM,
                        related_tag_id=existing_by_hash.get(Column.TAG_REGISTRY.TAG_NAME),
                        message=f"Duplicate file upload. Existing tag: {existing_by_hash.get(Column.TAG_REGISTRY.TAG_NAME)}"
                    )
                    _log_user_action(
                        client=client,
                        user_id=request.uploaded_by,
                        action_type=ActionType.OPERATION_FAILED,
                        target_table=Sheet.TAG_REGISTRY,
                        target_id="N/A",
                        notes=f"Duplicate file upload rejected. Exception: {exception_id}",
                        trace_id=trace_id
                    )
                    return func.HttpResponse(
                        json.dumps({
                            "status": "DUPLICATE",
                            "status": "DUPLICATE",
                            "existing_tag_id": existing_by_hash.get(Column.TAG_REGISTRY.TAG_NAME) or existing_by_hash.get(Column.TAG_REGISTRY.TAG_ID),
                            "exception_id": exception_id,
                            "trace_id": trace_id,
                            "message": "This file has already been uploaded"
                        }),
                        status_code=409,
                        mimetype="application/json"
                    )
        
        # 4. LPO validation
        lpo = _find_lpo(client, request)
        if lpo is None:
            logger.warning(f"[{trace_id}] LPO not found")
            exception_id = _create_exception(
                client=client,
                trace_id=trace_id,
                reason_code=ReasonCode.LPO_NOT_FOUND,
                severity=ExceptionSeverity.HIGH,
                message=f"LPO not found: {request.lpo_sap_reference or request.customer_lpo_ref or request.lpo_id}"
            )
            return func.HttpResponse(
                json.dumps({
                    "status": "BLOCKED",
                    "exception_id": exception_id,
                    "trace_id": trace_id,
                    "message": "Referenced LPO not found"
                }),
                status_code=422,
                mimetype="application/json"
            )
        
        # Check LPO status
        lpo_status = lpo.get(Column.LPO_MASTER.LPO_STATUS)
        if lpo_status == LPOStatus.ON_HOLD.value:
            logger.warning(f"[{trace_id}] LPO is on hold")
            exception_id = _create_exception(
                trace_id=trace_id,
                reason_code=ReasonCode.LPO_ON_HOLD,
                severity=ExceptionSeverity.HIGH,
                message=f"LPO {lpo.get(Column.LPO_MASTER.CUSTOMER_LPO_REF)} is currently on hold"
            )
            return func.HttpResponse(
                json.dumps({
                    "status": "BLOCKED",
                    "exception_id": exception_id,
                    "trace_id": trace_id,
                    "message": "LPO is currently on hold"
                }),
                status_code=422,
                mimetype="application/json"
            )
        
        # Check PO balance
        po_quantity = parse_float_safe(lpo.get(Column.LPO_MASTER.PO_QUANTITY_SQM))
        delivered_qty = parse_float_safe(lpo.get(Column.LPO_MASTER.DELIVERED_QUANTITY_SQM))
        # TODO: Sum active allocations for accurate committed calculation
        committed = delivered_qty
        remaining = po_quantity - committed
        
        if request.required_area_m2 > remaining:
            logger.warning(f"[{trace_id}] Insufficient PO balance: required={request.required_area_m2}, remaining={remaining}")
            exception_id = _create_exception(
                client=client,
                trace_id=trace_id,
                reason_code=ReasonCode.INSUFFICIENT_PO_BALANCE,
                severity=ExceptionSeverity.HIGH,
                quantity=request.required_area_m2,
                message=f"Required: {request.required_area_m2} m², Available: {remaining} m²"
            )
            return func.HttpResponse(
                json.dumps({
                    "status": "BLOCKED",
                    "exception_id": exception_id,
                    "trace_id": trace_id,
                    "message": f"Insufficient PO balance. Required: {request.required_area_m2}, Available: {remaining}"
                }),
                status_code=422,
                mimetype="application/json"
            )
        
        # 5. Generate Tag ID (using sequence counter - human-friendly!)
        tag_id = request.tag_id or generate_next_tag_id(client)
        logger.info(f"[{trace_id}] Generated tag_id: {tag_id}")
        
        # 6. Create tag record
        tag_name = request.tag_name or request.original_file_name or tag_id
        tag_data = {
            Column.TAG_REGISTRY.TAG_NAME: tag_name,
            Column.TAG_REGISTRY.LPO_SAP_REFERENCE: lpo.get(Column.LPO_MASTER.SAP_REFERENCE) or lpo.get(Column.LPO_MASTER.CUSTOMER_LPO_REF),
            Column.TAG_REGISTRY.REQUIRED_DELIVERY_DATE: request.requested_delivery_date,
            Column.TAG_REGISTRY.ESTIMATED_QUANTITY: request.required_area_m2,
            Column.TAG_REGISTRY.STATUS: TagStatus.DRAFT.value,
            Column.TAG_REGISTRY.CUSTOMER_NAME: lpo.get(Column.LPO_MASTER.CUSTOMER_NAME),
            Column.TAG_REGISTRY.BRAND: lpo.get(Column.LPO_MASTER.BRAND),
            Column.TAG_REGISTRY.FILE_HASH: file_hash,
            Column.TAG_REGISTRY.CLIENT_REQUEST_ID: request.client_request_id,
            Column.TAG_REGISTRY.SUBMITTED_BY: request.uploaded_by,
            Column.TAG_REGISTRY.REMARKS: f"ID: {tag_id} | Trace: {trace_id}"
        }
        
        created_row = client.add_row(Sheet.TAG_REGISTRY, tag_data)
        
        logger.info(f"[{trace_id}] Tag created successfully: {tag_id}")
        
        # 7. Log user action
        _log_user_action(
            client=client,
            user_id=request.uploaded_by,
            action_type=ActionType.TAG_CREATED,
            target_table=Sheet.TAG_REGISTRY,
            target_id=tag_id,
            new_value=json.dumps({"tag_name": tag_name, "required_area_m2": request.required_area_m2}),
            notes=f"Tag uploaded via API. Trace: {trace_id}",
            trace_id=trace_id
        )
        
        # 8. Return success
        return func.HttpResponse(
            json.dumps({
                "status": "UPLOADED",
                "tag_id": tag_id,
                "tag_name": tag_name,
                "row_id": created_row.get("id"),
                "file_hash": file_hash,
                "trace_id": trace_id,
                "message": "Tag uploaded successfully"
            }),
            status_code=200,
            mimetype="application/json"
        )
    
    except ValueError as e:
        logger.error(f"[{trace_id}] Validation error: {e}")
        return func.HttpResponse(
            json.dumps({
                "status": "ERROR",
                "trace_id": trace_id,
                "message": f"Validation error: {str(e)}"
            }),
            status_code=400,
            mimetype="application/json"
        )
    
    except Exception as e:
        logger.exception(f"[{trace_id}] Unexpected error: {e}")
        return func.HttpResponse(
            json.dumps({
                "status": "ERROR",
                "trace_id": trace_id,
                "message": f"Internal error: {str(e)}"
            }),
            status_code=500,
            mimetype="application/json"
        )


def _find_lpo(client, request: TagIngestRequest) -> Optional[dict]:
    """Find LPO by various reference fields."""
    if request.lpo_sap_reference:
        lpo = client.find_row(
            Sheet.LPO_MASTER,
            Column.LPO_MASTER.SAP_REFERENCE,
            request.lpo_sap_reference
        )
        if lpo:
            return lpo
    
    if request.customer_lpo_ref:
        lpo = client.find_row(
            Sheet.LPO_MASTER,
            Column.LPO_MASTER.CUSTOMER_LPO_REF,
            request.customer_lpo_ref
        )
        if lpo:
            return lpo
    
    if request.lpo_id:
        # Try as SAP reference first, then customer ref
        lpo = client.find_row(
            Sheet.LPO_MASTER,
            Column.LPO_MASTER.SAP_REFERENCE,
            request.lpo_id
        )
        if lpo:
            return lpo
        lpo = client.find_row(
            Sheet.LPO_MASTER,
            Column.LPO_MASTER.CUSTOMER_LPO_REF,
            request.lpo_id
        )
        if lpo:
            return lpo
    
    return None


def _create_exception(
    client,
    trace_id: str,
    reason_code: ReasonCode,
    severity: ExceptionSeverity,
    related_tag_id: Optional[str] = None,
    related_txn_id: Optional[str] = None,
    material_code: Optional[str] = None,
    quantity: Optional[float] = None,
    message: Optional[str] = None,
) -> str:
    """Create an exception record and return the exception_id."""
    # Use sequence-based ID
    exception_id = generate_next_exception_id(client)
    sla_due = calculate_sla_due(severity)
    
    exception_data = {
        Column.EXCEPTION_LOG.EXCEPTION_ID: exception_id,
        Column.EXCEPTION_LOG.CREATED_AT: format_datetime_for_smartsheet(datetime.now()),
        Column.EXCEPTION_LOG.SOURCE: ExceptionSource.INGEST.value,
        Column.EXCEPTION_LOG.RELATED_TAG_ID: related_tag_id,
        Column.EXCEPTION_LOG.RELATED_TXN_ID: related_txn_id,
        Column.EXCEPTION_LOG.MATERIAL_CODE: material_code,
        Column.EXCEPTION_LOG.QUANTITY: quantity,
        Column.EXCEPTION_LOG.REASON_CODE: reason_code.value,
        Column.EXCEPTION_LOG.SEVERITY: severity.value,
        Column.EXCEPTION_LOG.STATUS: "Open",
        Column.EXCEPTION_LOG.SLA_DUE: format_datetime_for_smartsheet(sla_due),
        Column.EXCEPTION_LOG.RESOLUTION_ACTION: message,
    }
    
    record_created = False
    try:
        client.add_row(Sheet.EXCEPTION_LOG, exception_data)
        logger.info(f"[{trace_id}] Exception created: {exception_id}")
        record_created = True
    except Exception as e:
        logger.error(f"[{trace_id}] Failed to create exception record: {e}")
    
    # Return ID with suffix indicating if record was saved
    # This helps callers know whether the exception was actually logged
    if not record_created:
        exception_id = f"{exception_id}-UNSAVED"
    
    return exception_id


def _log_user_action(
    client,
    user_id: str,
    action_type: ActionType,
    target_table: str,
    target_id: str,
    old_value: Optional[str] = None,
    new_value: Optional[str] = None,
    notes: Optional[str] = None,
    trace_id: Optional[str] = None,
):
    """Log a user action to the audit trail."""
    import uuid
    
    action_data = {
        Column.USER_ACTION_LOG.ACTION_ID: str(uuid.uuid4()),
        Column.USER_ACTION_LOG.TIMESTAMP: format_datetime_for_smartsheet(datetime.now()),
        Column.USER_ACTION_LOG.USER_ID: user_id,
        Column.USER_ACTION_LOG.ACTION_TYPE: action_type.value,
        Column.USER_ACTION_LOG.TARGET_TABLE: target_table,
        Column.USER_ACTION_LOG.TARGET_ID: target_id,
        Column.USER_ACTION_LOG.OLD_VALUE: old_value,
        Column.USER_ACTION_LOG.NEW_VALUE: new_value,
        Column.USER_ACTION_LOG.NOTES: notes or f"Trace: {trace_id}",
    }
    
    
    try:
        client.add_row(Sheet.USER_ACTION_LOG, action_data)
        logger.info(f"[{trace_id}] User action logged: {action_type.value}")
    except Exception as e:
        # Log the failure but don't raise - audit logging shouldn't block main operations
        # However, logging this at ERROR level ensures it's captured for monitoring
        logger.error(f"[{trace_id}] Failed to log user action: {e}. Action: {action_type.value}, Target: {target_id}")
