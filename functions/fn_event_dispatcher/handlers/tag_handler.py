"""
Tag Handler
============

Transforms Tag staging row data into fn_ingest_tag payloads.

RESILIENCE:
- All column access by ID (not name)
- Manifest provides ID mapping
- Validation errors create exceptions (SOTA)
- Multi-file attachment support (v1.6.3)
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
    TagIngestRequest,
    generate_trace_id,
    get_cell_value_by_logical_name,  # Shared helper (DRY)
    # SOTA exception handling
    create_exception,
    ReasonCode,
    ExceptionSeverity,
    ExceptionSource,
    # Multi-file support (DRY - reuse from LPO)
    FileAttachment,
    FileType,
    # Shared attachment extraction (v1.6.3)
    extract_row_attachments_as_files,
)
from ..models import RowEvent, DispatchResult

logger = logging.getLogger(__name__)


def handle_tag_ingest(event: RowEvent) -> DispatchResult:
    """
    Handle Tag ingestion from staging sheet.
    
    Flow:
    1. Fetch row data by ID
    2. Extract values by column ID (via manifest)
    3. Fetch row attachments (multi-file support)
    4. Build TagIngestRequest
    5. Call fn_ingest_tag directly
    """
    trace_id = event.trace_id or generate_trace_id()
    logger.info(f"[{trace_id}] Processing Tag ingest for row {event.row_id}")
    
    try:
        client = get_smartsheet_client()
        
        # Fetch row by immutable ID
        row_data = client.get_row(event.sheet_id, event.row_id)
        
        if not row_data:
            return DispatchResult(
                status="ERROR",
                handler="tag_ingest",
                message=f"Row {event.row_id} not found",
                trace_id=trace_id
            )
        
        sheet_logical = "02H_TAG_SHEET_STAGING"
        
        # Extract values by column ID
        lpo_sap_ref = get_cell_value_by_logical_name(row_data, sheet_logical, "LPO_SAP_REFERENCE_LINK")
        required_area = get_cell_value_by_logical_name(row_data, sheet_logical, "ESTIMATED_QUANTITY")
        delivery_date = get_cell_value_by_logical_name(row_data, sheet_logical, "REQUIRED_DELIVERY_DATE")
        tag_name = get_cell_value_by_logical_name(row_data, sheet_logical, "TAG_SHEET_NAME_REV")
        
        # =====================================================================
        # Multi-File Attachment Extraction (SOTA - v1.6.3)
        # Uses shared helper for DRY principle
        # =====================================================================
        files = extract_row_attachments_as_files(
            client=client,
            sheet_id=event.sheet_id,
            row_id=event.row_id,
            file_type=FileType.OTHER,
            trace_id=trace_id
        )
        
        # Build request
        # CRITICAL: Use deterministic client_request_id (no timestamp!)
        # Same staging row must always map to same idempotency key
        # This prevents duplicate creation on webhook retries (fixes v1.6.4)
        client_request_id = f"staging-tag-{event.row_id}"
        
        try:
            request = TagIngestRequest(
                client_request_id=client_request_id,
                lpo_sap_reference=lpo_sap_ref or "",
                required_area_m2=float(required_area or 0),
                requested_delivery_date=delivery_date,
                uploaded_by=event.actor_id or "system",
                tag_name=tag_name,  # Now populated from staging
                files=files,  # Multi-file support
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
                handler="tag_ingest",
                message=f"Validation error: {str(e)}",
                trace_id=trace_id,
                details={"exception_id": exception_id}
            )
        
        # Direct internal call
        from fn_ingest_tag import main as tag_ingest_main
        import azure.functions as func
        import json
        
        mock_req = func.HttpRequest(
            method="POST",
            url="/api/tags/ingest",
            body=json.dumps(request.model_dump()).encode(),
            headers={"Content-Type": "application/json"}
        )
        
        response = tag_ingest_main(mock_req)
        result_data = json.loads(response.get_body())
        
        # SOTA: Check if core function already logged an exception
        exception_id = result_data.get("exception_id")
        status = result_data.get("status", "OK")
        if exception_id and status not in ("OK", "ALREADY_PROCESSED"):
            status = "EXCEPTION_LOGGED"
        
        return DispatchResult(
            status=status,
            handler="tag_ingest",
            message=result_data.get("message", "Tag processed"),
            trace_id=trace_id,
            details=result_data
        )
        
    except Exception as e:
        logger.exception(f"[{trace_id}] Error in Tag ingest handler: {e}")
        return DispatchResult(
            status="ERROR",
            handler="tag_ingest",
            message=str(e),
            trace_id=trace_id
        )

