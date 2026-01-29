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
    # Manifest (for column name resolution)
    get_manifest,
    # ID generation (sequence-based)
    generate_next_tag_id,
    # Helpers
    generate_trace_id,
    compute_file_hash_from_url,
    compute_file_hash_from_base64,
    compute_combined_file_hash,  # Multi-file support (DRY with fn_lpo_ingest)
    format_datetime_for_smartsheet,
    parse_float_safe,
    # Audit (shared - DRY principle)
    create_exception,
    log_user_action,
)

# Get manifest for column name resolution
_manifest = None

def _get_physical_column_name(sheet_logical: str, column_logical: str) -> str:
    """Get physical column name from manifest."""
    global _manifest
    if _manifest is None:
        _manifest = get_manifest()
    physical_name = _manifest.get_column_name(sheet_logical, column_logical)
    return physical_name or column_logical  # Fallback to logical if not found

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
                    "tag_id": existing.get(Column.TAG_REGISTRY.TAG_NAME) or existing.get(Column.TAG_REGISTRY.TAG_ID),
                    "trace_id": trace_id,
                    "message": "This request was already processed"
                }),
                status_code=200,
                mimetype="application/json"
            )
        
        # 3. File hash check (multi-file support - v1.6.3)
        # Uses get_all_files() for backward compatibility (DRY with LPOIngestRequest)
        all_files = request.get_all_files()
        file_hash = compute_combined_file_hash(all_files) if all_files else None
        
        if file_hash:
            existing_by_hash = client.find_row(
                Sheet.TAG_REGISTRY,
                Column.TAG_REGISTRY.FILE_HASH,
                file_hash
            )
            if existing_by_hash:
                logger.warning(f"[{trace_id}] Duplicate file hash detected")
                exception_id = create_exception(
                    client=client,
                    trace_id=trace_id,
                    reason_code=ReasonCode.DUPLICATE_UPLOAD,
                    severity=ExceptionSeverity.MEDIUM,
                    related_tag_id=existing_by_hash.get(Column.TAG_REGISTRY.TAG_NAME),
                    message=f"Duplicate file upload. Existing tag: {existing_by_hash.get(Column.TAG_REGISTRY.TAG_NAME)}"
                )
                log_user_action(
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
            exception_id = create_exception(
                client=client,
                trace_id=trace_id,
                reason_code=ReasonCode.LPO_NOT_FOUND,
                severity=ExceptionSeverity.HIGH,
                message=f"LPO not found: {request.lpo_sap_reference or request.customer_lpo_ref or request.lpo_id}"
            )
            log_user_action(
                client=client,
                user_id=request.uploaded_by,
                action_type=ActionType.OPERATION_FAILED,
                target_table=Sheet.TAG_REGISTRY,
                target_id="N/A",
                notes=f"LPO not found. Exception: {exception_id}",
                trace_id=trace_id
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
        
        # Check LPO status - use physical column name
        lpo_status_col = _get_physical_column_name("LPO_MASTER", "LPO_STATUS")
        customer_lpo_ref_col = _get_physical_column_name("LPO_MASTER", "CUSTOMER_LPO_REF")
        lpo_status = lpo.get(lpo_status_col)
        
        if lpo_status == LPOStatus.ON_HOLD.value:
            logger.warning(f"[{trace_id}] LPO is on hold")
            exception_id = create_exception(
                client=client,
                trace_id=trace_id,
                reason_code=ReasonCode.LPO_ON_HOLD,
                severity=ExceptionSeverity.HIGH,
                message=f"LPO {lpo.get(customer_lpo_ref_col)} is currently on hold"
            )
            log_user_action(
                client=client,
                user_id=request.uploaded_by,
                action_type=ActionType.OPERATION_FAILED,
                target_table=Sheet.TAG_REGISTRY,
                target_id="N/A",
                notes=f"LPO on hold. Exception: {exception_id}",
                trace_id=trace_id
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
        
        # Check PO balance - use physical column names from manifest
        po_qty_col = _get_physical_column_name("LPO_MASTER", "PO_QUANTITY_SQM")
        delivered_qty_col = _get_physical_column_name("LPO_MASTER", "DELIVERED_QUANTITY_SQM")
        allocated_qty_col = _get_physical_column_name("LPO_MASTER", "ALLOCATED_QUANTITY")
        
        po_quantity = parse_float_safe(lpo.get(po_qty_col))
        delivered_qty = parse_float_safe(lpo.get(delivered_qty_col))
        allocated_qty = parse_float_safe(lpo.get(allocated_qty_col))
        
        logger.info(f"[{trace_id}] PO balance check: po_qty={po_quantity}, delivered={delivered_qty}, allocated={allocated_qty}")
        
        # FIX: Include allocated quantity in balance check (architecture spec §2 Step 2)
        # Formula: delivered + allocated + planned <= PO Quantity
        committed = delivered_qty + allocated_qty
        remaining = po_quantity - committed
        
        if request.required_area_m2 > remaining:
            logger.warning(f"[{trace_id}] Insufficient PO balance: required={request.required_area_m2}, remaining={remaining}")
            exception_id = create_exception(
                client=client,
                trace_id=trace_id,
                reason_code=ReasonCode.INSUFFICIENT_PO_BALANCE,
                severity=ExceptionSeverity.HIGH,
                quantity=request.required_area_m2,
                message=f"Required: {request.required_area_m2} m², Available: {remaining} m²"
            )
            log_user_action(
                client=client,
                user_id=request.uploaded_by,
                action_type=ActionType.OPERATION_FAILED,
                target_table=Sheet.TAG_REGISTRY,
                target_id="N/A",
                notes=f"Insufficient PO balance. Exception: {exception_id}",
                trace_id=trace_id
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
        
        # 6. Create tag record - get physical column names for LPO data
        sap_ref_col = _get_physical_column_name("LPO_MASTER", "SAP_REFERENCE")
        customer_name_col = _get_physical_column_name("LPO_MASTER", "CUSTOMER_NAME")
        brand_col = _get_physical_column_name("LPO_MASTER", "BRAND")
        project_col = _get_physical_column_name("LPO_MASTER", "PROJECT_NAME")
        wastage_col = _get_physical_column_name("LPO_MASTER", "WASTAGE_CONSIDERED_IN_COSTING")
        lpo_status_from_lpo = lpo.get(lpo_status_col)
        
        tag_name = request.tag_name or request.original_file_name or tag_id
        
        # Build complete tag data with all required fields
        tag_data = {
            # Core identification
            Column.TAG_REGISTRY.TAG_ID: tag_id,  # Save tag_id in proper column!
            Column.TAG_REGISTRY.TAG_NAME: tag_name,
            Column.TAG_REGISTRY.CLIENT_REQUEST_ID: request.client_request_id,
            
            # Reception info
            Column.TAG_REGISTRY.DATE_TAG_SHEET_RECEIVED: format_datetime_for_smartsheet(datetime.now()),
            Column.TAG_REGISTRY.RECEIVED_THROUGH: request.received_through,
            Column.TAG_REGISTRY.SUBMITTED_BY: request.uploaded_by,
            
            # LPO link and copied data
            Column.TAG_REGISTRY.LPO_SAP_REFERENCE: lpo.get(sap_ref_col) or lpo.get(customer_lpo_ref_col),
            Column.TAG_REGISTRY.LPO_STATUS: lpo_status_from_lpo,
            Column.TAG_REGISTRY.CUSTOMER_NAME: lpo.get(customer_name_col),
            Column.TAG_REGISTRY.PROJECT: lpo.get(project_col),
            Column.TAG_REGISTRY.BRAND: lpo.get(brand_col),
            Column.TAG_REGISTRY.LPO_ALLOWABLE_WASTAGE: lpo.get(wastage_col),
            
            # Quantity and dates
            Column.TAG_REGISTRY.REQUIRED_DELIVERY_DATE: request.requested_delivery_date,
            Column.TAG_REGISTRY.ESTIMATED_QUANTITY: request.required_area_m2,
            
            # Status and workflow
            Column.TAG_REGISTRY.STATUS: "Validate",  # Starts at Validate, not Draft
            Column.TAG_REGISTRY.PRODUCTION_GATE: "Green",  # Default to Green
            
            # File tracking
            Column.TAG_REGISTRY.FILE_HASH: file_hash,
            
            # Remarks - user input separate from system notes
            Column.TAG_REGISTRY.REMARKS: request.user_remarks or f"Trace: {trace_id}",
        }
        
        created_row = client.add_row(Sheet.TAG_REGISTRY, tag_data)
        row_id = created_row.get("id")
        
        logger.info(f"[{trace_id}] Tag created successfully: {tag_id}, row_id: {row_id}")
        
        # 6b. Attach files to the row (multi-file support - v1.6.3)
        # Reuses pattern from fn_lpo_ingest (DRY principle)
        attached_count = 0
        if row_id and all_files:
            for f in all_files:
                try:
                    file_name = f.file_name or f"attachment_{tag_id}_{attached_count}"
                    if f.file_url:
                        client.attach_url_to_row(Sheet.TAG_REGISTRY, row_id, f.file_url, file_name)
                        attached_count += 1
                    elif f.file_content:
                        client.attach_file_to_row(Sheet.TAG_REGISTRY, row_id, f.file_content, file_name)
                        attached_count += 1
                except Exception as attach_err:
                    logger.warning(f"[{trace_id}] Failed to attach file {file_name}: {attach_err}")
            logger.info(f"[{trace_id}] Attached {attached_count}/{len(all_files)} files to tag")
        
        # 7. Log user action
        log_user_action(
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

