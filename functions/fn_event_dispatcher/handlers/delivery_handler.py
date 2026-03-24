"""
Delivery Handler
================

Transforms Delivery Log Ingestion staging row data into fn_delivery_ingest
payloads (create and update).

RESILIENCE:
- All column access by logical name (via manifest)
- Renames don't affect this code
- Early dedup check on create
- Updates target SAP Invoice Number, Status, and Vehicle ID
"""

import logging
from typing import Dict, Any
from pydantic import ValidationError

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared import (
    get_smartsheet_client,
    get_manifest,
    generate_trace_id,
    get_cell_value_by_logical_name,
    parse_float_safe,
    parse_int_safe,
    # SOTA exception handling
    create_exception,
    ReasonCode,
    ExceptionSeverity,
    ExceptionSource,
    # Shared attachment extraction
    extract_row_attachments_as_files,
    FileAttachment,
    FileType,
    # Logical names
    Sheet,
    Column,
)
from shared.models import DeliveryIngestRequest, DeliveryUpdateRequest
from ..models import RowEvent, DispatchResult

logger = logging.getLogger(__name__)

# Staging sheet logical key
STAGING_SHEET = "07H_DELIVERY_LOG_INGESTION"


def handle_delivery_ingest(event: RowEvent) -> DispatchResult:
    """
    Handle delivery creation from staging sheet.

    Flow:
    1. Early dedup check — prevent duplicate processing on retries
    2. Fetch row data by ID
    3. Extract values by column ID (via manifest)
    4. Extract file attachments (POD documents)
    5. Build DeliveryIngestRequest
    6. Call fn_delivery_ingest directly
    """
    trace_id = event.trace_id or generate_trace_id()
    logger.info(f"[{trace_id}] Processing delivery ingest for row {event.row_id}")

    # Deterministic dedup key
    client_request_id = f"staging-delivery-{event.row_id}"

    try:
        client = get_smartsheet_client()

        # === EARLY DEDUP CHECK ===
        existing = client.find_row(
            Sheet.DELIVERY_LOG,
            Column.DELIVERY_LOG.SAP_DO_NUMBER,
            _get_staging_do_number(client, event),
        )
        if existing:
            manifest = get_manifest()
            delivery_id_col = manifest.get_column_name("DELIVERY_LOG", "DELIVERY_ID")
            existing_id = existing.get(delivery_id_col) if delivery_id_col else None
            logger.info(
                f"[{trace_id}] DEDUP: Staging row {event.row_id} already processed as {existing_id}"
            )
            return DispatchResult(
                status="ALREADY_PROCESSED",
                handler="delivery_ingest",
                message=f"This staging row was already processed as {existing_id}",
                trace_id=trace_id,
                details={"delivery_id": existing_id, "client_request_id": client_request_id},
            )

        # Fetch row from staging sheet
        row_data = client.get_row(event.sheet_id, event.row_id)
        if not row_data:
            return DispatchResult(
                status="ERROR",
                handler="delivery_ingest",
                message=f"Row {event.row_id} not found",
                trace_id=trace_id,
            )

        # Extract all values by logical name
        sap_do_number = get_cell_value_by_logical_name(row_data, STAGING_SHEET, "SAP_DO_NUMBER")
        tag_sheet_id = get_cell_value_by_logical_name(row_data, STAGING_SHEET, "TAG_SHEET_ID")
        sap_invoice_number = get_cell_value_by_logical_name(row_data, STAGING_SHEET, "SAP_INVOICE_NUMBER")
        status = get_cell_value_by_logical_name(row_data, STAGING_SHEET, "STATUS")
        lines = get_cell_value_by_logical_name(row_data, STAGING_SHEET, "LINES")
        quantity = get_cell_value_by_logical_name(row_data, STAGING_SHEET, "QUANTITY")
        value = get_cell_value_by_logical_name(row_data, STAGING_SHEET, "VALUE")
        vehicle_id = get_cell_value_by_logical_name(row_data, STAGING_SHEET, "VEHICLE_ID")
        remarks = get_cell_value_by_logical_name(row_data, STAGING_SHEET, "REMARKS")

        # Extract file attachments (POD documents)
        files = extract_row_attachments_as_files(
            client=client,
            sheet_id=event.sheet_id,
            row_id=event.row_id,
            file_type=FileType.OTHER,
            trace_id=trace_id,
        )

        # Build request
        try:
            request = DeliveryIngestRequest(
                client_request_id=client_request_id,
                sap_do_number=sap_do_number or "",
                tag_sheet_id=tag_sheet_id or "",
                sap_invoice_number=sap_invoice_number,
                status=status or "Pending SAP",
                lines=parse_int_safe(lines, default=None),
                quantity=parse_float_safe(quantity, default=None),
                value=parse_float_safe(value, default=None),
                vehicle_id=vehicle_id,
                remarks=remarks,
                files=files,
                uploaded_by=event.actor_id or "system",
            )
        except ValidationError as e:
            logger.warning(f"[{trace_id}] Validation error: {e}")
            exception_id = create_exception(
                client=client,
                trace_id=trace_id,
                reason_code=ReasonCode.LPO_INVALID_DATA,
                severity=ExceptionSeverity.MEDIUM,
                source=ExceptionSource.INGEST,
                message=f"Delivery validation error in staging row {event.row_id}: {e}",
                client_request_id=client_request_id,
            )
            return DispatchResult(
                status="EXCEPTION_LOGGED",
                handler="delivery_ingest",
                message=f"Validation error: {e}",
                trace_id=trace_id,
                details={"exception_id": exception_id, "row_id": event.row_id},
            )

        # Direct internal call (no HTTP overhead)
        from fn_delivery_ingest import main as delivery_ingest_main
        import azure.functions as func
        import json

        mock_req = func.HttpRequest(
            method="POST",
            url="/api/deliveries/ingest",
            body=json.dumps(request.model_dump()).encode(),
            headers={"Content-Type": "application/json"},
        )

        response = delivery_ingest_main(mock_req)
        result_data = json.loads(response.get_body())

        status_val = result_data.get("status", "OK")
        return DispatchResult(
            status=status_val,
            handler="delivery_ingest",
            message=result_data.get("message", "Delivery processed"),
            trace_id=trace_id,
            details=result_data,
        )

    except Exception as e:
        logger.exception(f"[{trace_id}] Error in delivery ingest handler: {e}")
        return DispatchResult(
            status="ERROR",
            handler="delivery_ingest",
            message=str(e),
            trace_id=trace_id,
        )


def handle_delivery_update(event: RowEvent) -> DispatchResult:
    """
    Handle delivery update from staging sheet.

    Updates SAP Invoice Number, Status, and Vehicle ID on an existing
    delivery record in the main Delivery Log.
    """
    trace_id = event.trace_id or generate_trace_id()
    logger.info(f"[{trace_id}] Processing delivery update for row {event.row_id}")

    client_request_id = f"staging-delivery-update-{event.row_id}"

    try:
        client = get_smartsheet_client()

        row_data = client.get_row(event.sheet_id, event.row_id)
        if not row_data:
            return DispatchResult(
                status="ERROR",
                handler="delivery_update",
                message=f"Row {event.row_id} not found",
                trace_id=trace_id,
            )

        sap_do_number = get_cell_value_by_logical_name(row_data, STAGING_SHEET, "SAP_DO_NUMBER")
        if not sap_do_number:
            exception_id = create_exception(
                client=client,
                trace_id=trace_id,
                reason_code=ReasonCode.SAP_REF_NOT_FOUND,
                severity=ExceptionSeverity.MEDIUM,
                source=ExceptionSource.INGEST,
                message=f"SAP DO Number missing in staging row {event.row_id}",
                client_request_id=client_request_id,
            )
            return DispatchResult(
                status="EXCEPTION_LOGGED",
                handler="delivery_update",
                message="SAP DO Number is required for update",
                trace_id=trace_id,
                details={"exception_id": exception_id},
            )

        # Extract updatable fields
        sap_invoice_number = get_cell_value_by_logical_name(row_data, STAGING_SHEET, "SAP_INVOICE_NUMBER")
        status = get_cell_value_by_logical_name(row_data, STAGING_SHEET, "STATUS")
        vehicle_id = get_cell_value_by_logical_name(row_data, STAGING_SHEET, "VEHICLE_ID")
        remarks = get_cell_value_by_logical_name(row_data, STAGING_SHEET, "REMARKS")

        # Extract any new file attachments
        files = extract_row_attachments_as_files(
            client=client,
            sheet_id=event.sheet_id,
            row_id=event.row_id,
            file_type=FileType.OTHER,
            trace_id=trace_id,
        )

        try:
            request = DeliveryUpdateRequest(
                client_request_id=client_request_id,
                sap_do_number=sap_do_number,
                sap_invoice_number=sap_invoice_number,
                status=status,
                vehicle_id=vehicle_id,
                remarks=remarks,
                files=files,
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
                message=f"Delivery update validation error in staging row {event.row_id}: {e}",
                client_request_id=client_request_id,
            )
            return DispatchResult(
                status="EXCEPTION_LOGGED",
                handler="delivery_update",
                message=f"Validation error: {e}",
                trace_id=trace_id,
                details={"exception_id": exception_id},
            )

        # Direct internal call
        from fn_delivery_ingest import main as delivery_ingest_main
        import azure.functions as func
        import json

        mock_req = func.HttpRequest(
            method="PUT",
            url="/api/deliveries/ingest",
            body=json.dumps(request.model_dump(exclude_none=True)).encode(),
            headers={"Content-Type": "application/json"},
        )

        response = delivery_ingest_main(mock_req)
        result_data = json.loads(response.get_body())

        status_val = result_data.get("status", "OK")
        return DispatchResult(
            status=status_val,
            handler="delivery_update",
            message=result_data.get("message", "Delivery updated"),
            trace_id=trace_id,
            details=result_data,
        )

    except Exception as e:
        logger.exception(f"[{trace_id}] Error in delivery update handler: {e}")
        return DispatchResult(
            status="ERROR",
            handler="delivery_update",
            message=str(e),
            trace_id=trace_id,
        )


def _get_staging_do_number(client, event: RowEvent) -> str:
    """
    Quick-fetch the SAP DO Number from the staging row for early dedup check.
    Returns empty string if not found (dedup check will pass through).
    """
    try:
        row_data = client.get_row(event.sheet_id, event.row_id)
        if row_data:
            return get_cell_value_by_logical_name(row_data, STAGING_SHEET, "SAP_DO_NUMBER") or ""
    except Exception:
        pass
    return ""
