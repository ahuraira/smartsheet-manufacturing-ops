"""
fn_lpo_ingest: LPO Ingestion Azure Function
============================================

This is the **AUTHORITATIVE** component for LPO creation. All business logic,
validations, duplicate detection, and exception creation happen here.

Architecture
------------
Power Automate (orchestration) → fn_lpo_ingest (authority) → Smartsheet (store)

The function is stateless - all state lives in Smartsheet:
- LPO Master LOG: Primary LPO records  
- Exception Log: Validation failures
- User Action Log: Audit trail

Key Features
------------
- SAP Reference is REQUIRED and serves as external-facing ID
- Idempotency via client_request_id
- Duplicate SAP Reference detection → 409 DUPLICATE
- Folder path generation for SharePoint
- Full audit trail

Request Format
--------------
{
    "client_request_id": "uuid-v4",
    "sap_reference": "PTE-185",           // REQUIRED
    "customer_name": "Acme Corp",          // REQUIRED
    "project_name": "Project X",           // REQUIRED
    "brand": "KIMMCO",                     // REQUIRED (KIMMCO or WTI)
    "po_quantity_sqm": 1000.0,             // REQUIRED
    "price_per_sqm": 150.0,                // REQUIRED
    "customer_lpo_ref": "CUST-001",        // Optional
    "terms_of_payment": "30 Days Credit",  // Optional
    "wastage_pct": 3.0,                    // Optional
    "file_url": "https://...",             // Optional
    "remarks": "Notes",                    // Optional
    "uploaded_by": "user@company.com"      // REQUIRED
}

Response Codes
--------------
200 OK
    - "OK": LPO created successfully
    - "ALREADY_PROCESSED": Idempotent return of existing record

409 Conflict
    - "DUPLICATE": SAP Reference already exists

422 Unprocessable Entity
    - "BLOCKED": Validation failed

400 Bad Request
    - "ERROR": Invalid request format

500 Internal Server Error
    - "ERROR": Unexpected server error
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
    # Logical Names (SOTA)
    Sheet,
    Column,
    
    # Models
    LPOIngestRequest,
    ExceptionSeverity,
    ExceptionSource,
    ReasonCode,
    ActionType,
    LPOStatus,
    
    # Client
    get_smartsheet_client,
    
    # Manifest
    get_manifest,
    
    # Helpers
    generate_trace_id,
    compute_combined_file_hash,
    calculate_sla_due,
    format_datetime_for_smartsheet,
    generate_lpo_folder_path,
    generate_lpo_folder_url,
    
    # Audit (shared - DRY)
    create_exception,
    log_user_action,
    
    # Power Automate (v1.3.1+)
    trigger_create_lpo_folders,
)


logger = logging.getLogger(__name__)


def _get_physical_column_name(sheet_logical: str, column_logical: str) -> Optional[str]:
    """Get physical column name from manifest."""
    manifest = get_manifest()
    return manifest.get_column_name(sheet_logical, column_logical)


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    Main entry point for LPO ingestion.
    
    Flow:
    1. Parse and validate request
    2. Idempotency check (client_request_id)
    3. Duplicate SAP Reference check
    4. File hash check (if file_url provided)
    5. Validate business rules
    6. Generate folder path
    7. Create LPO record
    8. Log user action
    9. Return success response
    """
    trace_id = generate_trace_id()
    
    try:
        # 1. Parse request
        try:
            body = req.get_json()
            request = LPOIngestRequest(**body)
        except ValueError as e:
            logger.error(f"[{trace_id}] Request validation failed: {e}")
            
            # SOTA: Create exception for validation errors
            client = get_smartsheet_client()
            exception_id = create_exception(
                client=client,
                trace_id=trace_id,
                reason_code=ReasonCode.LPO_INVALID_DATA,
                severity=ExceptionSeverity.MEDIUM,
                source=ExceptionSource.INGEST,
                message=f"Validation error: {str(e)}"
            )
            
            return func.HttpResponse(
                json.dumps({
                    "status": "VALIDATION_ERROR",
                    "exception_id": exception_id,
                    "message": f"Invalid request: {str(e)}",
                    "trace_id": trace_id
                }),
                status_code=422,
                mimetype="application/json"
            )
        
        logger.info(f"[{trace_id}] Processing LPO ingest: SAP={request.sap_reference}")
        
        # Get client
        client = get_smartsheet_client()
        
        # 2. Idempotency check
        existing = client.find_row(
            Sheet.LPO_MASTER,
            Column.LPO_MASTER.CLIENT_REQUEST_ID,
            request.client_request_id
        )
        if existing:
            logger.info(f"[{trace_id}] Idempotent return for client_request_id")
            sap_ref_col = _get_physical_column_name("LPO_MASTER", "SAP_REFERENCE")
            folder_url_col = _get_physical_column_name("LPO_MASTER", "FOLDER_URL")
            return func.HttpResponse(
                json.dumps({
                    "status": "ALREADY_PROCESSED",
                    "sap_reference": existing.get(sap_ref_col),
                    "folder_path": existing.get(folder_url_col),
                    "trace_id": trace_id,
                    "message": "This request was already processed"
                }),
                status_code=200,
                mimetype="application/json"
            )
        
        # 3. Duplicate SAP Reference check
        existing_sap = client.find_row(
            Sheet.LPO_MASTER,
            Column.LPO_MASTER.SAP_REFERENCE,
            request.sap_reference
        )
        if existing_sap:
            logger.warning(f"[{trace_id}] Duplicate SAP Reference: {request.sap_reference}")
            exception_id = create_exception(
                client=client,
                trace_id=trace_id,
                reason_code=ReasonCode.DUPLICATE_SAP_REF,
                severity=ExceptionSeverity.MEDIUM,
                message=f"SAP Reference {request.sap_reference} already exists"
            )
            log_user_action(
                client=client,
                user_id=request.uploaded_by,
                action_type=ActionType.OPERATION_FAILED,
                target_table=Sheet.LPO_MASTER,
                target_id="N/A",
                notes=f"Duplicate SAP Reference. Exception: {exception_id}",
                trace_id=trace_id
            )
            return func.HttpResponse(
                json.dumps({
                    "status": "DUPLICATE",
                    "existing_sap_reference": request.sap_reference,
                    "exception_id": exception_id,
                    "trace_id": trace_id,
                    "message": "SAP Reference already exists. Use update endpoint to modify."
                }),
                status_code=409,
                mimetype="application/json"
            )
        
        # 4. File hash check (multi-file support)
        all_files = request.get_all_files()
        file_hash = compute_combined_file_hash(all_files) if all_files else None
        
        # Build file metadata for storage
        files_metadata = []
        for f in all_files:
            files_metadata.append({
                "type": f.file_type.value,
                "name": f.file_name or "unnamed"
            })
        
        if file_hash:
            existing_by_hash = client.find_row(
                Sheet.LPO_MASTER,
                Column.LPO_MASTER.SOURCE_FILE_HASH,
                file_hash
            )
            if existing_by_hash:
                logger.warning(f"[{trace_id}] Duplicate LPO file(s) detected")
                exception_id = create_exception(
                    client=client,
                    trace_id=trace_id,
                    reason_code=ReasonCode.DUPLICATE_LPO_FILE,
                    severity=ExceptionSeverity.MEDIUM,
                    message=f"Duplicate LPO file(s). Existing SAP: {existing_by_hash.get(_get_physical_column_name('LPO_MASTER', 'SAP_REFERENCE'))}"
                )
                log_user_action(
                    client=client,
                    user_id=request.uploaded_by,
                    action_type=ActionType.OPERATION_FAILED,
                    target_table=Sheet.LPO_MASTER,
                    target_id="N/A",
                    notes=f"Duplicate LPO file(s). Exception: {exception_id}",
                    trace_id=trace_id
                )
                return func.HttpResponse(
                    json.dumps({
                        "status": "DUPLICATE",
                        "exception_id": exception_id,
                        "trace_id": trace_id,
                        "message": "These LPO file(s) have already been uploaded"
                    }),
                    status_code=409,
                    mimetype="application/json"
                )
        
        # 5. Business validation
        if request.brand not in ["KIMMCO", "WTI"]:
            logger.warning(f"[{trace_id}] Invalid brand: {request.brand}")
            exception_id = create_exception(
                client=client,
                trace_id=trace_id,
                reason_code=ReasonCode.LPO_INVALID_DATA,
                severity=ExceptionSeverity.MEDIUM,
                message=f"Invalid brand: {request.brand}. Must be KIMMCO or WTI."
            )
            log_user_action(
                client=client,
                user_id=request.uploaded_by,
                action_type=ActionType.OPERATION_FAILED,
                target_table=Sheet.LPO_MASTER,
                target_id="N/A",
                notes=f"Invalid brand. Exception: {exception_id}",
                trace_id=trace_id
            )
            return func.HttpResponse(
                json.dumps({
                    "status": "BLOCKED",
                    "exception_id": exception_id,
                    "trace_id": trace_id,
                    "message": f"Invalid brand: {request.brand}. Must be KIMMCO or WTI."
                }),
                status_code=422,
                mimetype="application/json"
            )
        
        # 6. Generate folder paths
        # Relative path for Power Automate (folder creation)
        folder_path = generate_lpo_folder_path(
            sap_reference=request.sap_reference,
            customer_name=request.customer_name
        )
        # Full encoded URL for Smartsheet (clickable link)
        folder_url = generate_lpo_folder_url(
            sap_reference=request.sap_reference,
            customer_name=request.customer_name
        )
        
        # 7. Create LPO record
        now = format_datetime_for_smartsheet(datetime.now())
        po_value = request.po_quantity_sqm * request.price_per_sqm
        
        lpo_data = {
            Column.LPO_MASTER.SAP_REFERENCE: request.sap_reference,
            Column.LPO_MASTER.CUSTOMER_LPO_REF: request.customer_lpo_ref,
            Column.LPO_MASTER.CUSTOMER_NAME: request.customer_name,
            Column.LPO_MASTER.PROJECT_NAME: request.project_name,
            Column.LPO_MASTER.BRAND: request.brand,
            Column.LPO_MASTER.LPO_STATUS: LPOStatus.DRAFT.value,
            Column.LPO_MASTER.WASTAGE_CONSIDERED_IN_COSTING: str(request.wastage_pct),
            Column.LPO_MASTER.PRICE_PER_SQM: request.price_per_sqm,
            Column.LPO_MASTER.PO_QUANTITY_SQM: request.po_quantity_sqm,
            Column.LPO_MASTER.PO_VALUE: po_value,
            Column.LPO_MASTER.TERMS_OF_PAYMENT: request.terms_of_payment,
            Column.LPO_MASTER.HOLD_REASON: request.hold_reason,
            Column.LPO_MASTER.REMARKS: request.remarks,
            Column.LPO_MASTER.FOLDER_URL: folder_url,
            Column.LPO_MASTER.SOURCE_FILE_HASH: file_hash,
            Column.LPO_MASTER.CLIENT_REQUEST_ID: request.client_request_id,
            Column.LPO_MASTER.CREATED_BY: request.uploaded_by,
            # Initialize tracking fields
            Column.LPO_MASTER.DELIVERED_QUANTITY_SQM: 0,
            Column.LPO_MASTER.DELIVERED_VALUE: 0,
            Column.LPO_MASTER.PO_BALANCE_QUANTITY: request.po_quantity_sqm,
        }
        
        result = client.add_row(Sheet.LPO_MASTER, lpo_data)
        # Get row_id from result (can be 'id' or 'row_id')
        row_id = None
        if isinstance(result, dict):
            row_id = result.get("id") or result.get("row_id")
        logger.info(f"[{trace_id}] LPO created: {request.sap_reference}, row_id: {row_id}")
        
        # Attach files to the row
        attached_count = 0
        if row_id and all_files:
            for f in all_files:
                try:
                    file_name = f.file_name or f"{f.file_type.value}_file"
                    if f.file_url:
                        client.attach_url_to_row(Sheet.LPO_MASTER, row_id, f.file_url, file_name)
                        attached_count += 1
                    elif f.file_content:
                        client.attach_file_to_row(Sheet.LPO_MASTER, row_id, f.file_content, file_name)
                        attached_count += 1
                except Exception as attach_err:
                    logger.error(f"[{trace_id}] Failed to attach file {file_name}: {attach_err}")
            logger.info(f"[{trace_id}] Attached {attached_count}/{len(all_files)} files")
        
        # 8. Log user action
        log_user_action(
            client=client,
            user_id=request.uploaded_by,
            action_type=ActionType.LPO_CREATED,
            target_table=Sheet.LPO_MASTER,
            target_id=request.sap_reference,
            notes=f"LPO created via API. Folder: {folder_path}",
            trace_id=trace_id
        )
        
        # 9. Trigger Power Automate flow for folder creation (fire-and-forget)
        # This is non-blocking - LPO creation succeeds even if flow fails
        flow_result = trigger_create_lpo_folders(
            sap_reference=request.sap_reference,
            customer_name=request.customer_name,
            folder_path=folder_path,
            correlation_id=trace_id
        )
        
        if flow_result.success:
            logger.info(f"[{trace_id}] Folder creation flow triggered successfully")
        else:
            # Don't fail the LPO creation - folder creation is eventual consistency
            logger.warning(
                f"[{trace_id}] Folder creation flow trigger failed: {flow_result.error_message}. "
                "Folders can be created manually or on retry."
            )
        
        # 10. Return success
        return func.HttpResponse(
            json.dumps({
                "status": "OK",
                "sap_reference": request.sap_reference,
                "folder_path": folder_path,
                "folder_creation_triggered": flow_result.success,
                "trace_id": trace_id,
                "message": "LPO created successfully"
            }),
            status_code=200,
            mimetype="application/json"
        )
    
    except Exception as e:
        logger.exception(f"[{trace_id}] Unexpected error: {e}")
        
        # SOTA: Create exception for unexpected errors
        try:
            client = get_smartsheet_client()
            exception_id = create_exception(
                client=client,
                trace_id=trace_id,
                reason_code=ReasonCode.LPO_INVALID_DATA,
                severity=ExceptionSeverity.HIGH,
                source=ExceptionSource.INGEST,
                message=f"Unexpected error: {str(e)}"
            )
        except Exception:
            exception_id = None  # Fallback if exception logging also fails
        
        return func.HttpResponse(
            json.dumps({
                "status": "ERROR",
                "exception_id": exception_id,
                "message": f"Internal server error: {str(e)}",
                "trace_id": trace_id
            }),
            status_code=500,
            mimetype="application/json"
        )

