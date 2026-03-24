"""
fn_delivery_ingest: Delivery Log Ingestion Azure Function
=========================================================

Endpoint: POST /api/deliveries/ingest   (create)
Endpoint: PUT  /api/deliveries/update   (update SAP invoice, status, vehicle)

Transfers delivery records from the ingestion staging sheet (07h) to the
main Delivery Log (07). On create, attaches POD files and uploads them to
the LPO's SharePoint folder under a "Deliveries" subfolder.

Key Features
------------
- Idempotency via client_request_id (deterministic: staging-delivery-{row_id})
- Duplicate SAP DO Number detection
- POD file attachment to delivery row + SharePoint upload to LPO folder
- SAP Invoice Number, Status, Vehicle ID updatable post-creation
- Full audit trail via log_user_action / create_exception
"""

import logging
import json
import azure.functions as func

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import (
    # Logical names
    Sheet,
    Column,

    # Models
    ExceptionSeverity,
    ExceptionSource,
    ReasonCode,
    ActionType,

    # Client
    get_smartsheet_client,

    # Manifest
    get_manifest,

    # Helpers
    generate_trace_id,
    format_datetime_for_smartsheet,
    parse_float_safe,
    parse_int_safe,
    scope_filename,
    now_uae,

    # ID generation
    generate_next_delivery_id,

    # User resolution
    resolve_user_email,

    # File upload to SharePoint
    trigger_upload_files_flow,
    FileUploadItem,

    # Audit
    create_exception,
    log_user_action,
)
from shared.models import DeliveryIngestRequest, DeliveryUpdateRequest, DeliveryStatus

logger = logging.getLogger(__name__)


def _get_physical_column_name(sheet_logical: str, column_logical: str) -> str:
    """Resolve logical column name to physical name via manifest."""
    manifest = get_manifest()
    return manifest.get_column_name(sheet_logical, column_logical)


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    Main entry point for delivery ingestion/update.
    Routes based on HTTP method: POST = create, PUT = update.
    """
    trace_id = generate_trace_id()

    if req.method == "PUT":
        return _handle_update(req, trace_id)
    return _handle_ingest(req, trace_id)


def _handle_ingest(req: func.HttpRequest, trace_id: str) -> func.HttpResponse:
    """Create a new delivery record in the main Delivery Log."""
    try:
        body = req.get_json()
        request = DeliveryIngestRequest(**body)
        if body.get("trace_id"):
            trace_id = body["trace_id"]

        logger.info(f"[{trace_id}] Delivery ingest: DO={request.sap_do_number}")

        client = get_smartsheet_client()
        manifest = get_manifest()

        # 1. Idempotency check
        existing = client.find_row(
            Sheet.DELIVERY_LOG,
            Column.DELIVERY_LOG.SAP_DO_NUMBER,
            request.sap_do_number,
        )
        if existing:
            existing_id = existing.get(
                _get_physical_column_name("DELIVERY_LOG", "DELIVERY_ID")
            )
            logger.info(
                f"[{trace_id}] DEDUP: DO {request.sap_do_number} already exists as {existing_id}"
            )
            return func.HttpResponse(
                json.dumps({
                    "status": "ALREADY_PROCESSED",
                    "delivery_id": existing_id,
                    "sap_do_number": request.sap_do_number,
                    "trace_id": trace_id,
                    "message": f"Delivery {request.sap_do_number} already exists",
                }),
                status_code=200,
                mimetype="application/json",
            )

        # 2. Generate delivery ID
        delivery_id = generate_next_delivery_id(client)
        created_by = resolve_user_email(client, request.uploaded_by)

        # 3. Build row data
        delivery_data = {
            Column.DELIVERY_LOG.DELIVERY_ID: delivery_id,
            Column.DELIVERY_LOG.SAP_DO_NUMBER: request.sap_do_number,
            Column.DELIVERY_LOG.TAG_SHEET_ID: request.tag_sheet_id,
            Column.DELIVERY_LOG.SAP_INVOICE_NUMBER: request.sap_invoice_number,
            Column.DELIVERY_LOG.STATUS: request.status,
            Column.DELIVERY_LOG.LINES: request.lines,
            Column.DELIVERY_LOG.QUANTITY: request.quantity,
            Column.DELIVERY_LOG.VALUE: request.value,
            Column.DELIVERY_LOG.VEHICLE_ID: request.vehicle_id,
            Column.DELIVERY_LOG.CREATED_AT: format_datetime_for_smartsheet(now_uae()),
            Column.DELIVERY_LOG.REMARKS: request.remarks,
        }

        result = client.add_row(Sheet.DELIVERY_LOG, delivery_data)
        row_id = None
        if isinstance(result, dict):
            row_id = result.get("id") or result.get("row_id")
        logger.info(f"[{trace_id}] Delivery created: {delivery_id}, row_id={row_id}")

        # 4. Attach files (POD documents) to the delivery row
        all_files = request.get_all_files()
        attached_count = 0
        if row_id and all_files:
            for f in all_files:
                try:
                    file_name = f.file_name or "POD_document"
                    if f.file_url:
                        client.attach_url_to_row(
                            Sheet.DELIVERY_LOG, row_id, f.file_url, file_name
                        )
                        attached_count += 1
                    elif f.file_content:
                        client.attach_file_to_row(
                            Sheet.DELIVERY_LOG, row_id, f.file_content, file_name
                        )
                        attached_count += 1
                except Exception as attach_err:
                    logger.error(
                        f"[{trace_id}] Failed to attach file {file_name}: {attach_err}"
                    )
            logger.info(
                f"[{trace_id}] Attached {attached_count}/{len(all_files)} files"
            )

        # 5. Upload POD files to LPO SharePoint folder (fire-and-forget)
        _upload_pod_to_sharepoint(
            client, request.tag_sheet_id, all_files, delivery_id, trace_id
        )

        # 6. Audit trail
        log_user_action(
            client=client,
            user_id=created_by,
            action_type=ActionType.DO_CREATED,
            target_table=Sheet.DELIVERY_LOG,
            target_id=delivery_id,
            notes=f"Delivery {delivery_id} created for DO {request.sap_do_number}",
            trace_id=trace_id,
        )

        return func.HttpResponse(
            json.dumps({
                "status": "OK",
                "delivery_id": delivery_id,
                "sap_do_number": request.sap_do_number,
                "trace_id": trace_id,
                "message": "Delivery created successfully",
            }),
            status_code=200,
            mimetype="application/json",
        )

    except Exception as e:
        logger.exception(f"[{trace_id}] Unexpected error in delivery ingest: {e}")
        try:
            client = get_smartsheet_client()
            create_exception(
                client=client,
                trace_id=trace_id,
                reason_code=ReasonCode.SYSTEM_ERROR,
                severity=ExceptionSeverity.HIGH,
                source=ExceptionSource.INGEST,
                message=f"fn_delivery_ingest error: {e}",
            )
        except Exception:
            logger.error(f"[{trace_id}] Failed to create exception record")

        return func.HttpResponse(
            json.dumps({
                "status": "ERROR",
                "message": f"Internal server error: {e}",
                "trace_id": trace_id,
            }),
            status_code=500,
            mimetype="application/json",
        )


def _handle_update(req: func.HttpRequest, trace_id: str) -> func.HttpResponse:
    """Update an existing delivery record (SAP invoice, status, vehicle ID)."""
    try:
        body = req.get_json()
        request = DeliveryUpdateRequest(**body)
        if body.get("trace_id"):
            trace_id = body["trace_id"]

        logger.info(
            f"[{trace_id}] Delivery update: DO={request.sap_do_number}"
        )

        client = get_smartsheet_client()

        # 1. Find existing delivery row
        existing = client.find_row(
            Sheet.DELIVERY_LOG,
            Column.DELIVERY_LOG.SAP_DO_NUMBER,
            request.sap_do_number,
        )
        if not existing:
            create_exception(
                client=client,
                trace_id=trace_id,
                reason_code=ReasonCode.SAP_REF_NOT_FOUND,
                severity=ExceptionSeverity.MEDIUM,
                source=ExceptionSource.INGEST,
                message=f"Delivery DO {request.sap_do_number} not found for update",
            )
            return func.HttpResponse(
                json.dumps({
                    "status": "NOT_FOUND",
                    "message": f"Delivery with DO {request.sap_do_number} not found",
                    "trace_id": trace_id,
                }),
                status_code=404,
                mimetype="application/json",
            )

        row_id = existing.get("row_id") or existing.get("id")
        delivery_id = existing.get(
            _get_physical_column_name("DELIVERY_LOG", "DELIVERY_ID")
        )

        # 2. Build update payload (only provided fields)
        update_data = {}
        if request.sap_invoice_number is not None:
            update_data[Column.DELIVERY_LOG.SAP_INVOICE_NUMBER] = request.sap_invoice_number
        if request.status is not None:
            update_data[Column.DELIVERY_LOG.STATUS] = request.status
        if request.vehicle_id is not None:
            update_data[Column.DELIVERY_LOG.VEHICLE_ID] = request.vehicle_id
        if request.remarks is not None:
            update_data[Column.DELIVERY_LOG.REMARKS] = request.remarks

        if not update_data and not request.files:
            return func.HttpResponse(
                json.dumps({
                    "status": "NO_CHANGE",
                    "delivery_id": delivery_id,
                    "trace_id": trace_id,
                    "message": "No fields to update",
                }),
                status_code=200,
                mimetype="application/json",
            )

        # 3. Apply update
        if update_data:
            client.update_row(Sheet.DELIVERY_LOG, row_id, update_data)
            logger.info(
                f"[{trace_id}] Updated delivery {delivery_id}: {list(update_data.keys())}"
            )

        # 4. Attach new files if provided
        all_files = request.files
        attached_count = 0
        if row_id and all_files:
            for f in all_files:
                try:
                    file_name = f.file_name or "POD_document"
                    if f.file_url:
                        client.attach_url_to_row(
                            Sheet.DELIVERY_LOG, row_id, f.file_url, file_name
                        )
                        attached_count += 1
                    elif f.file_content:
                        client.attach_file_to_row(
                            Sheet.DELIVERY_LOG, row_id, f.file_content, file_name
                        )
                        attached_count += 1
                except Exception as attach_err:
                    logger.error(
                        f"[{trace_id}] Failed to attach file: {attach_err}"
                    )

            # Upload to SharePoint too
            tag_sheet_id = existing.get(
                _get_physical_column_name("DELIVERY_LOG", "TAG_SHEET_ID"), ""
            )
            _upload_pod_to_sharepoint(
                client, tag_sheet_id, all_files, delivery_id, trace_id
            )

        # 5. Audit trail
        updated_by_email = resolve_user_email(client, request.updated_by)
        changed_fields = list(update_data.keys())
        if attached_count:
            changed_fields.append(f"files({attached_count})")
        log_user_action(
            client=client,
            user_id=updated_by_email,
            action_type=ActionType.LPO_UPDATED,
            target_table=Sheet.DELIVERY_LOG,
            target_id=delivery_id or request.sap_do_number,
            notes=f"Delivery updated: {', '.join(changed_fields)}",
            trace_id=trace_id,
        )

        return func.HttpResponse(
            json.dumps({
                "status": "OK",
                "delivery_id": delivery_id,
                "sap_do_number": request.sap_do_number,
                "trace_id": trace_id,
                "message": "Delivery updated successfully",
                "updated_fields": changed_fields,
            }),
            status_code=200,
            mimetype="application/json",
        )

    except Exception as e:
        logger.exception(f"[{trace_id}] Unexpected error in delivery update: {e}")
        try:
            client = get_smartsheet_client()
            create_exception(
                client=client,
                trace_id=trace_id,
                reason_code=ReasonCode.SYSTEM_ERROR,
                severity=ExceptionSeverity.HIGH,
                source=ExceptionSource.INGEST,
                message=f"fn_delivery_ingest update error: {e}",
            )
        except Exception:
            logger.error(f"[{trace_id}] Failed to create exception record")

        return func.HttpResponse(
            json.dumps({
                "status": "ERROR",
                "message": f"Internal server error: {e}",
                "trace_id": trace_id,
            }),
            status_code=500,
            mimetype="application/json",
        )


def _upload_pod_to_sharepoint(
    client, tag_sheet_id: str, files, delivery_id: str, trace_id: str
):
    """
    Upload POD files to the LPO's SharePoint folder under Deliveries/.
    Filenames are prefixed with delivery_id and tag_id for traceability.
    Uses the first tag's LPO reference to locate the LPO folder.
    Fire-and-forget — failure does not block delivery creation.
    """
    if not files:
        return

    try:
        # Resolve LPO folder URL from the first tag's LPO reference
        first_tag = tag_sheet_id.split(",")[0].strip() if tag_sheet_id else None
        if not first_tag:
            logger.warning(f"[{trace_id}] No tag ID to resolve LPO folder for POD upload")
            return

        # Look up tag to get LPO SAP reference
        tag_row = client.find_row(
            Sheet.TAG_REGISTRY,
            Column.TAG_REGISTRY.TAG_ID,
            first_tag,
        )
        if not tag_row:
            logger.warning(f"[{trace_id}] Tag {first_tag} not found — skipping POD upload")
            return

        lpo_sap_ref_col = _get_physical_column_name("TAG_REGISTRY", "LPO_SAP_REFERENCE")
        lpo_sap_ref = tag_row.get(lpo_sap_ref_col)
        if not lpo_sap_ref:
            logger.warning(f"[{trace_id}] No LPO SAP ref on tag {first_tag}")
            return

        # Get LPO folder URL from LPO master
        lpo_row = client.find_row(
            Sheet.LPO_MASTER,
            Column.LPO_MASTER.SAP_REFERENCE,
            lpo_sap_ref,
        )
        if not lpo_row:
            logger.warning(f"[{trace_id}] LPO {lpo_sap_ref} not found — skipping POD upload")
            return

        folder_url_col = _get_physical_column_name("LPO_MASTER", "FOLDER_URL")
        lpo_folder_url = lpo_row.get(folder_url_col)
        if not lpo_folder_url:
            logger.warning(f"[{trace_id}] No folder URL on LPO {lpo_sap_ref}")
            return

        # Build upload items — store under Deliveries/ with scoped filenames
        upload_items = []
        for f in files:
            if f.file_content:
                raw_name = f.file_name or "POD_document"
                file_name = scope_filename(raw_name, delivery_id)
                upload_items.append(FileUploadItem(
                    file_name=file_name,
                    file_content=f.file_content,
                    subfolder="Deliveries",
                ))

        if upload_items:
            upload_result = trigger_upload_files_flow(
                lpo_folder_url=lpo_folder_url,
                files=upload_items,
                correlation_id=trace_id,
            )
            if upload_result.success:
                logger.info(
                    f"[{trace_id}] POD upload triggered: {len(upload_items)} files to {lpo_sap_ref_str}/Deliveries/"
                )
            else:
                logger.warning(
                    f"[{trace_id}] POD upload failed: {upload_result.error_message}"
                )
    except Exception as e:
        logger.warning(f"[{trace_id}] POD SharePoint upload failed (non-blocking): {e}")
