"""
LPO Handler
===========

Transforms LPO staging row data into fn_lpo_ingest/update payloads.

RESILIENCE:
- All column access by ID (not name)
- Manifest provides ID mapping
- Renames don't affect this code
- Validation errors create exceptions (SOTA)
- Extracts ALL fields and attachments from staging
"""

import logging
from typing import Dict, Any, Optional, List
from pydantic import ValidationError

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared import (
    get_smartsheet_client,
    get_manifest,
    LPOIngestRequest,
    LPOUpdateRequest,
    generate_trace_id,
    get_cell_value_by_logical_name,  # Shared helper (DRY)
    # SOTA exception handling
    create_exception,
    ReasonCode,
    ExceptionSeverity,
    ExceptionSource,
    # File attachment models
    FileAttachment,
    FileType,
)
from ..models import RowEvent, DispatchResult

logger = logging.getLogger(__name__)


def handle_lpo_ingest(event: RowEvent) -> DispatchResult:
    """
    Handle LPO ingestion from staging sheet.
    
    Flow:
    1. Fetch row data by ID
    2. Extract values by column ID (via manifest)
    3. Build LPOIngestRequest
    4. Call fn_lpo_ingest directly
    """
    trace_id = event.trace_id or generate_trace_id()
    logger.info(f"[{trace_id}] Processing LPO ingest for row {event.row_id}")
    
    try:
        client = get_smartsheet_client()
        manifest = get_manifest()
        
        # Fetch row by immutable ID
        row_data = client.get_row(event.sheet_id, event.row_id)
        
        if not row_data:
            return DispatchResult(
                status="ERROR",
                handler="lpo_ingest",
                message=f"Row {event.row_id} not found",
                trace_id=trace_id
            )
        
        # Extract ALL values by column ID (resilient to renames)
        sheet_logical = "01H_LPO_INGESTION"
        
        # Required fields
        sap_reference = get_cell_value_by_logical_name(row_data, sheet_logical, "SAP_REFERENCE")
        customer_name = get_cell_value_by_logical_name(row_data, sheet_logical, "CUSTOMER_NAME")
        project_name = get_cell_value_by_logical_name(row_data, sheet_logical, "PROJECT_NAME")
        brand = get_cell_value_by_logical_name(row_data, sheet_logical, "BRAND")
        po_quantity = get_cell_value_by_logical_name(row_data, sheet_logical, "PO_QUANTITY_SQM")
        price_per_sqm = get_cell_value_by_logical_name(row_data, sheet_logical, "PRICE_PER_SQM")
        
        # Optional fields - extract all available
        customer_lpo_ref = get_cell_value_by_logical_name(row_data, sheet_logical, "CUSTOMER_LPO_REF")
        terms_of_payment = get_cell_value_by_logical_name(row_data, sheet_logical, "TERMS_OF_PAYMENT")
        wastage_pct = get_cell_value_by_logical_name(row_data, sheet_logical, "WASTAGE_PCT")
        hold_reason = get_cell_value_by_logical_name(row_data, sheet_logical, "HOLD_REASON")
        remarks = get_cell_value_by_logical_name(row_data, sheet_logical, "REMARKS")
        
        # Fetch row attachments from Smartsheet
        files: List[FileAttachment] = []
        try:
            attachments = client.get_row_attachments(event.sheet_id, event.row_id)
            for att in attachments:
                # Convert Smartsheet attachment to FileAttachment
                file_url = att.get("url") or att.get("attachmentUrl")
                file_name = att.get("name") or att.get("fileName")
                if file_url:
                    files.append(FileAttachment(
                        file_type=FileType.LPO,
                        file_url=file_url,
                        file_name=file_name
                    ))
            logger.info(f"[{trace_id}] Fetched {len(files)} attachments from staging row")
        except Exception as att_err:
            logger.warning(f"[{trace_id}] Failed to fetch attachments: {att_err}")
        
        # Build request with idempotency key
        client_request_id = f"staging-{event.row_id}-{event.timestamp_utc or 'unknown'}"
        
        try:
            request = LPOIngestRequest(
                client_request_id=client_request_id,
                sap_reference=sap_reference or "",
                customer_name=customer_name or "",
                project_name=project_name or "",
                brand=brand or "KIMMCO",
                po_quantity_sqm=float(po_quantity or 0),
                price_per_sqm=float(price_per_sqm or 0),
                # Optional fields
                customer_lpo_ref=customer_lpo_ref,
                terms_of_payment=terms_of_payment or "30 Days Credit",
                wastage_pct=float(wastage_pct or 0),
                hold_reason=hold_reason,
                remarks=remarks,
                # Attachments
                files=files,
                uploaded_by=event.actor_id or "system",
            )
        except ValidationError as e:
            # SOTA: Create exception for validation errors
            logger.warning(f"[{trace_id}] Validation error: {e}")
            exception_id = create_exception(
                client=client,
                trace_id=trace_id,
                reason_code=ReasonCode.LPO_INVALID_DATA,
                severity=ExceptionSeverity.MEDIUM,
                source=ExceptionSource.INGEST,
                message=f"Validation error in staging row {event.row_id}: {str(e)}"
            )
            return DispatchResult(
                status="EXCEPTION_LOGGED",
                handler="lpo_ingest",
                message=f"Validation error: {str(e)}",
                trace_id=trace_id,
                details={"exception_id": exception_id, "row_id": event.row_id}
            )
        
        # Direct internal call (no HTTP overhead)
        from fn_lpo_ingest import main as lpo_ingest_main
        
        # Create mock HTTP request for the function
        import azure.functions as func
        import json
        
        mock_req = func.HttpRequest(
            method="POST",
            url="/api/lpos/ingest",
            body=json.dumps(request.model_dump()).encode(),
            headers={"Content-Type": "application/json"}
        )
        
        response = lpo_ingest_main(mock_req)
        result_data = json.loads(response.get_body())
        
        # SOTA: Check if core function already logged an exception
        exception_id = result_data.get("exception_id")
        status = result_data.get("status", "OK")
        
        # If exception was logged, use EXCEPTION_LOGGED status
        if exception_id and status not in ("OK", "ALREADY_PROCESSED"):
            status = "EXCEPTION_LOGGED"
        
        return DispatchResult(
            status=status,
            handler="lpo_ingest",
            message=result_data.get("message", "LPO processed"),
            trace_id=trace_id,
            details=result_data
        )
        
    except Exception as e:
        logger.exception(f"[{trace_id}] Error in LPO ingest handler: {e}")
        return DispatchResult(
            status="ERROR",
            handler="lpo_ingest",
            message=str(e),
            trace_id=trace_id
        )


def handle_lpo_update(event: RowEvent) -> DispatchResult:
    """
    Handle LPO update from staging sheet.
    Similar to ingest but uses LPOUpdateRequest.
    """
    trace_id = event.trace_id or generate_trace_id()
    logger.info(f"[{trace_id}] Processing LPO update for row {event.row_id}")
    
    try:
        client = get_smartsheet_client()
        manifest = get_manifest()
        
        row_data = client.get_row(event.sheet_id, event.row_id)
        
        if not row_data:
            return DispatchResult(
                status="ERROR",
                handler="lpo_update",
                message=f"Row {event.row_id} not found",
                trace_id=trace_id
            )
        
        sheet_logical = "01H_LPO_INGESTION"
        sap_reference = get_cell_value_by_logical_name(row_data, sheet_logical, "SAP_REFERENCE")
        
        if not sap_reference:
            exception_id = create_exception(
                client=client,
                trace_id=trace_id,
                reason_code=ReasonCode.LPO_INVALID_DATA,
                severity=ExceptionSeverity.MEDIUM,
                source=ExceptionSource.INGEST,
                message=f"SAP Reference missing in staging row {event.row_id}"
            )
            return DispatchResult(
                status="EXCEPTION_LOGGED",
                handler="lpo_update",
                message="SAP Reference is required for update",
                trace_id=trace_id,
                details={"exception_id": exception_id}
            )
        
        # Build update request
        try:
            request = LPOUpdateRequest(
                sap_reference=sap_reference,
                po_quantity_sqm=get_cell_value_by_logical_name(row_data, sheet_logical, "PO_QUANTITY_SQM"),
                updated_by=event.actor_id or "system",
            )
        except ValidationError as e:
            logger.warning(f"[{trace_id}] Validation error: {e}")
            exception_id = create_exception(
                client=client,
                trace_id=trace_id,
                reason_code=ReasonCode.LPO_INVALID_DATA,
                severity=ExceptionSeverity.MEDIUM,
                source=ExceptionSource.INGEST,
                message=f"Validation error in staging row {event.row_id}: {str(e)}"
            )
            return DispatchResult(
                status="EXCEPTION_LOGGED",
                handler="lpo_update",
                message=f"Validation error: {str(e)}",
                trace_id=trace_id,
                details={"exception_id": exception_id}
            )
        
        # Direct internal call
        from fn_lpo_update import main as lpo_update_main
        import azure.functions as func
        import json
        
        mock_req = func.HttpRequest(
            method="PUT",
            url="/api/lpos/update",
            body=json.dumps(request.model_dump(exclude_none=True)).encode(),
            headers={"Content-Type": "application/json"}
        )
        
        response = lpo_update_main(mock_req)
        result_data = json.loads(response.get_body())
        
        # SOTA: Check if core function already logged an exception
        exception_id = result_data.get("exception_id")
        status = result_data.get("status", "OK")
        if exception_id and status not in ("OK", "ALREADY_PROCESSED"):
            status = "EXCEPTION_LOGGED"
        
        return DispatchResult(
            status=status,
            handler="lpo_update",
            message=result_data.get("message", "LPO updated"),
            trace_id=trace_id,
            details=result_data
        )
        
    except Exception as e:
        logger.exception(f"[{trace_id}] Error in LPO update handler: {e}")
        return DispatchResult(
            status="ERROR",
            handler="lpo_update",
            message=str(e),
            trace_id=trace_id
        )
