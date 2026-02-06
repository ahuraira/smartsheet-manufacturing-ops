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
    # LPO Folder Generators
    generate_lpo_folder_path,
    generate_lpo_folder_url,
    
    # Shared attachment extraction (v1.6.3)
    extract_row_attachments_as_files,
    # File attachment models
    FileAttachment,
    FileType,
    # Percentage normalization (v1.6.7)
    normalize_percentage,
)
from ..models import RowEvent, DispatchResult

logger = logging.getLogger(__name__)


def handle_lpo_ingest(event: RowEvent) -> DispatchResult:
    """
    Handle LPO ingestion from staging sheet.
    
    Flow:
    1. Early dedup check (v1.6.5) - prevent duplicate processing on retries
    2. Fetch row data by ID
    3. Extract values by column ID (via manifest)
    4. Build LPOIngestRequest
    5. Call fn_lpo_ingest directly
    """
    trace_id = event.trace_id or generate_trace_id()
    logger.info(f"[{trace_id}] Processing LPO ingest for row {event.row_id}")
    
    # Build deterministic client_request_id FIRST (v1.6.5)
    client_request_id = f"staging-lpo-{event.row_id}"
    
    try:
        client = get_smartsheet_client()
        manifest = get_manifest()
        
        # =====================================================================
        # EARLY DEDUP CHECK (v1.6.5)
        # Check if this staging row was already processed BEFORE any other work
        # This prevents duplicate processing on webhook retries at the earliest point
        # =====================================================================
        from shared import Sheet, Column
        existing = client.find_row(
            Sheet.LPO_MASTER,
            Column.LPO_MASTER.CLIENT_REQUEST_ID,
            client_request_id
        )
        if existing:
            # Get LPO ID from physical column name
            sap_ref_col = manifest.get_column_name("LPO_MASTER", "SAP_REFERENCE") or "SAP Reference"
            existing_lpo_id = (
                existing.get(sap_ref_col) or
                existing.get("SAP Reference") or
                existing.get("id")
            )
            logger.info(f"[{trace_id}] DEDUP: Staging row {event.row_id} already processed as {existing_lpo_id}")
            return DispatchResult(
                status="ALREADY_PROCESSED",
                handler="lpo_ingest",
                message=f"This staging row was already processed as {existing_lpo_id}",
                trace_id=trace_id,
                details={"lpo_id": existing_lpo_id, "client_request_id": client_request_id}
            )
        
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
        
        # =====================================================================
        # Multi-File Attachment Extraction (SOTA)
        # Uses shared helper for DRY principle (v1.6.3)
        # =====================================================================
        files = extract_row_attachments_as_files(
            client=client,
            sheet_id=event.sheet_id,
            row_id=event.row_id,
            file_type=FileType.LPO, # Default to LPO type, user can override later
            trace_id=trace_id
        )
        
        # Build request with idempotency key
        # NOTE: client_request_id is built at the top of the function (v1.6.5)
        # for early dedup check
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
                wastage_pct=normalize_percentage(wastage_pct, 0.0),  # v1.6.7: normalize 18, 0.18, 18%
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
                message=f"Validation error in staging row {event.row_id}: {str(e)}",
                client_request_id=client_request_id  # DEDUP (v1.6.5)
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
    
    # Build deterministic client_request_id for dedup (v1.6.5)
    client_request_id = f"staging-lpo-update-{event.row_id}"
    
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
                message=f"SAP Reference missing in staging row {event.row_id}",
                client_request_id=client_request_id  # DEDUP (v1.6.5)
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
                message=f"Validation error in staging row {event.row_id}: {str(e)}",
                client_request_id=client_request_id  # DEDUP (v1.6.5)
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
