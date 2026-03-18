import json
import uuid
import logging

import azure.functions as func

from shared.smartsheet_client import SmartsheetClient
from shared.manifest import get_manifest
from shared.logical_names import Sheet, Column
from shared.blob_storage import get_blob_service_client, get_container_name, upload_json_blob
from shared.audit import log_user_action, create_exception
from shared.models import ActionType, ExceptionSeverity, ExceptionSource, ReasonCode
from shared.queue_lock import AllocationLock

logger = logging.getLogger(__name__)

def main(req: func.HttpRequest) -> func.HttpResponse:
    trace_id = str(uuid.uuid4())
    logger.info(f"[{trace_id}] Received Manager Margin Approval webhook.")

    client = None

    try:
        req_body = req.get_json()
    except ValueError:
        try:
            create_exception(
                client=SmartsheetClient(),
                trace_id=trace_id,
                reason_code=ReasonCode.SYSTEM_ERROR,
                severity=ExceptionSeverity.MEDIUM,
                source=ExceptionSource.INGEST,
                message="fn_process_manager_approval: Invalid JSON payload",
            )
        except Exception:
            logger.error(f"[{trace_id}] Failed to create exception record")
        return func.HttpResponse("Invalid JSON", status_code=400)

    action = req_body.get("action", "")
    approval_row_id = req_body.get("approval_row_id")
    # If the user clicks 'Wait', we just log it and maybe leave it pending
    if action == "hold_tag":
        logger.info(f"[{trace_id}] Manager selected Hold for approval {approval_row_id}. Doing nothing.")
        return func.HttpResponse("Status held in pending state.", status_code=200)

    if action != "proceed_to_do":
        logger.warning(f"[{trace_id}] Unknown action {action}")
        try:
            create_exception(
                client=SmartsheetClient(),
                trace_id=trace_id,
                reason_code=ReasonCode.SYSTEM_ERROR,
                severity=ExceptionSeverity.MEDIUM,
                source=ExceptionSource.INGEST,
                message=f"fn_process_manager_approval: Unknown action '{action}'",
            )
        except Exception:
            logger.error(f"[{trace_id}] Failed to create exception record")
        return func.HttpResponse("Unknown action.", status_code=400)

    tag_sheet_id = str(req_body.get("tag_sheet_id", ""))

    # Adaptive Card returns inputs as strings
    try:
        penalty_input = req_body.get("manager_penalty_pct", "0.0")
        if penalty_input == "":
            penalty_input = "0.0"
        penalty_pct = float(penalty_input)
    except ValueError:
        penalty_pct = 0.0

    merge_tags_str = str(req_body.get("merge_tags", ""))
    merge_tags = []
    if merge_tags_str.strip():
        # ChoiceSet multi-select gives comma-separated values
        merge_tags = [t.strip() for t in merge_tags_str.split(',') if t.strip()]

    all_tags = [tag_sheet_id] + merge_tags
    logger.info(f"[{trace_id}] Proceeding with DO generation for tags: {all_tags} with penalty {penalty_pct}%")

    # 1. Smartsheet Validation & Idempotency
    client = SmartsheetClient()
    manifest = get_manifest()

    col_approval_status = manifest.get_column_name(Sheet.MARGIN_APPROVAL_LOG, Column.MARGIN_APPROVAL_LOG.STATUS)
    col_approval_id = manifest.get_column_name(Sheet.MARGIN_APPROVAL_LOG, Column.MARGIN_APPROVAL_LOG.APPROVAL_ID)

    try:
        # Check if already processed
        app_rows = client.find_rows(Sheet.MARGIN_APPROVAL_LOG, Column.MARGIN_APPROVAL_LOG.APPROVAL_ID, str(approval_row_id))
        if app_rows:
            app_row = app_rows[0]
            if app_row.get(col_approval_status) == "Approved":
                logger.info(f"[{trace_id}] Margin Approval {approval_row_id} already processed. Idempotent exit.")
                return func.HttpResponse("Already approved.", status_code=200)
            app_smartsheet_row_id = app_row.get("row_id")
        else:
            logger.warning(f"[{trace_id}] Approval ID {approval_row_id} not found in DB.")
            try:
                create_exception(
                    client=client,
                    trace_id=trace_id,
                    reason_code=ReasonCode.TAG_NOT_FOUND,
                    severity=ExceptionSeverity.HIGH,
                    source=ExceptionSource.INGEST,
                    message=f"fn_process_manager_approval: Approval ID {approval_row_id} not found in MARGIN_APPROVAL_LOG",
                )
            except Exception:
                logger.error(f"[{trace_id}] Failed to create exception record")
            return func.HttpResponse("Approval not found", status_code=404)
    except Exception as e:
        logger.error(f"[{trace_id}] Failed querying MARGIN_APPROVAL_LOG: {e}")
        try:
            create_exception(
                client=client,
                trace_id=trace_id,
                reason_code=ReasonCode.SYSTEM_ERROR,
                severity=ExceptionSeverity.CRITICAL,
                source=ExceptionSource.INGEST,
                message=f"fn_process_manager_approval: Failed querying MARGIN_APPROVAL_LOG: {str(e)}",
            )
        except Exception:
            logger.error(f"[{trace_id}] Failed to create exception record")
        return func.HttpResponse("Internal DB Error", status_code=500)

    # Acquire lock for the read-modify-write section
    with AllocationLock([f"approval-{approval_row_id}"], timeout_ms=60000, trace_id=trace_id) as lock:
        if not lock.success:
            return func.HttpResponse(
                json.dumps({"error": "LOCK_TIMEOUT", "message": "Another approval is being processed"}),
                status_code=409, mimetype="application/json"
            )

        # 2. Gather Tag Information
        lpo_ref = ""
        # We fetch TAG_REGISTRY directly
        tag_reg_rows = client.get_sheet(Sheet.TAG_REGISTRY).get("rows", [])
        col_tr_id = manifest.get_column_name(Sheet.TAG_REGISTRY, Column.TAG_REGISTRY.TAG_ID)
        col_tr_lpo = manifest.get_column_name(Sheet.TAG_REGISTRY, Column.TAG_REGISTRY.LPO_SAP_REFERENCE)

        tag_smartsheet_ids_to_update = []

        for r in tag_reg_rows:
            row_cells = r.get("cells", [])
            c_dict = {str(c.get("columnId")): str(c.get("value", "")) for c in row_cells}

            # We need the logical names to columnId mapping
            cid_tag_id = manifest.get_all_column_ids(Sheet.TAG_REGISTRY).get(col_tr_id)
            cid_lpo_id = manifest.get_all_column_ids(Sheet.TAG_REGISTRY).get(col_tr_lpo)

            t_id = c_dict.get(str(cid_tag_id), "")
            if t_id in all_tags:
                tag_smartsheet_ids_to_update.append(r.get("id"))
                if not lpo_ref:
                    lpo_ref = c_dict.get(str(cid_lpo_id), "")

        if not lpo_ref:
            logger.error(f"[{trace_id}] LPO Reference could not be determined for DO creation.")
            try:
                create_exception(
                    client=client,
                    trace_id=trace_id,
                    reason_code=ReasonCode.LPO_NOT_FOUND,
                    severity=ExceptionSeverity.HIGH,
                    source=ExceptionSource.INGEST,
                    message=f"fn_process_manager_approval: LPO Reference missing for tags {all_tags}",
                )
            except Exception:
                logger.error(f"[{trace_id}] Failed to create exception record")
            return func.HttpResponse("LPO Reference missing.", status_code=500)

        # 3. Gather Session IDs from CUT_SESSION
        cut_session_rows = client.get_sheet(Sheet.CUT_SESSION).get("rows", [])
        col_cs_tag = manifest.get_column_name(Sheet.CUT_SESSION, Column.CUT_SESSION.TAG_SHEET_ID)
        col_cs_session = manifest.get_column_name(Sheet.CUT_SESSION, Column.CUT_SESSION.SESSION_ID)

        cid_cs_tag = manifest.get_all_column_ids(Sheet.CUT_SESSION).get(col_cs_tag)
        cid_cs_session = manifest.get_all_column_ids(Sheet.CUT_SESSION).get(col_cs_session)

        session_ids = []
        for r in cut_session_rows:
            cells = r.get("cells", [])
            cs_tag_val = next((str(c.get("value", "")) for c in cells if str(c.get("columnId")) == str(cid_cs_tag)), "")
            cs_sess_val = next((str(c.get("value", "")) for c in cells if str(c.get("columnId")) == str(cid_cs_session)), "")
            if cs_tag_val in all_tags and cs_sess_val:
                session_ids.append(cs_sess_val)

        # 4. Fetch JSON Blobs and Apply Margin Penalty
        service_client = get_blob_service_client()
        if not service_client:
            try:
                create_exception(
                    client=client,
                    trace_id=trace_id,
                    reason_code=ReasonCode.SYSTEM_ERROR,
                    severity=ExceptionSeverity.CRITICAL,
                    source=ExceptionSource.INGEST,
                    message="fn_process_manager_approval: Blob storage not configured",
                )
            except Exception:
                logger.error(f"[{trace_id}] Failed to create exception record")
            return func.HttpResponse("Blob storage not configured", status_code=500)

        container_client = service_client.get_container_client(get_container_name())

        multiplier = 1.0 + (penalty_pct / 100.0)
        master_do_lines = []
        total_inflated_area = 0.0

        for sess_id in session_ids:
            blob_path = f"{lpo_ref}/{sess_id}.json"
            try:
                blob_client = container_client.get_blob_client(blob_path)
                content = blob_client.download_blob().readall()
                nest_json = json.loads(content)

                # The structure of nest_json typically has "bom_result" -> "lines"
                bom_lines = nest_json.get("bom_result", {}).get("lines", [])
                for line in bom_lines:
                    # Assuming duck area is 'area_m2' or calculate from dims
                    original_area = float(line.get("area_m2", 0.0))
                    inflated_area = original_area * multiplier
                    line["billed_area_m2"] = round(inflated_area, 4)
                    line["original_area_m2"] = original_area
                    line["penalty_multiplier"] = multiplier
                    line["source_tag"] = nest_json.get("tag_id", sess_id)
                    master_do_lines.append(line)
                    total_inflated_area += inflated_area

            except Exception as e:
                logger.error(f"[{trace_id}] Failed to download or parse blob {blob_path}: {e}")
                try:
                    create_exception(
                        client=client,
                        trace_id=trace_id,
                        reason_code=ReasonCode.SYSTEM_ERROR,
                        severity=ExceptionSeverity.MEDIUM,
                        source=ExceptionSource.INGEST,
                        message=f"fn_process_manager_approval: Failed to download/parse blob {blob_path}: {str(e)}",
                    )
                except Exception:
                    logger.error(f"[{trace_id}] Failed to create exception record")

        # 5. Build and Save DO Payload
        delivery_id = f"DO-{uuid.uuid4().hex[:8].upper()}"
        do_payload = {
            "delivery_id": delivery_id,
            "lpo_reference": lpo_ref,
            "tags_included": all_tags,
            "manager_penalty_pct": penalty_pct,
            "total_billed_area": total_inflated_area,
            "delivery_lines": master_do_lines,
            "generated_at": trace_id
        }

        do_blob_path = f"{lpo_ref}/Deliveries/do_{delivery_id}.json"
        upload_json_blob(do_payload, do_blob_path, trace_id)

        # 6. Save SINGLE line to DELIVERY_LOG
        col_dl_id = manifest.get_column_name(Sheet.DELIVERY_LOG, "DELIVERY_ID")  # TODO: add Column.DELIVERY_LOG to logical_names.py
        col_dl_tag = manifest.get_column_name(Sheet.DELIVERY_LOG, "TAG_SHEET_ID")  # TODO: add Column.DELIVERY_LOG to logical_names.py
        col_dl_sap = manifest.get_column_name(Sheet.DELIVERY_LOG, "SAP_DO_NUMBER")  # TODO: add Column.DELIVERY_LOG to logical_names.py
        col_dl_qty = manifest.get_column_name(Sheet.DELIVERY_LOG, "QUANTITY")  # TODO: add to logical_names.py
        col_dl_lines = manifest.get_column_name(Sheet.DELIVERY_LOG, "LINES")  # TODO: add to logical_names.py

        dl_cids = manifest.get_all_column_ids(Sheet.DELIVERY_LOG)
        dl_row = []
        if col_dl_id in dl_cids: dl_row.append({"columnId": dl_cids[col_dl_id], "value": delivery_id})
        if col_dl_tag in dl_cids: dl_row.append({"columnId": dl_cids[col_dl_tag], "value": ", ".join(all_tags)})
        if col_dl_sap in dl_cids: dl_row.append({"columnId": dl_cids[col_dl_sap], "value": "PENDING_SAP"})
        if col_dl_qty in dl_cids: dl_row.append({"columnId": dl_cids[col_dl_qty], "value": total_inflated_area})

        approver = req_body.get("approver", "system")

        try:
            client.add_rows_bulk(Sheet.DELIVERY_LOG, [{"toBottom": True, "cells": dl_row}])
            log_user_action(
                client=client,
                user_id=approver,
                action_type=ActionType.DO_CREATED,
                target_table="DELIVERY_LOG",
                target_id=delivery_id,
                trace_id=trace_id,
            )
        except Exception as e:
            logger.error(f"[{trace_id}] Failed to log to DELIVERY_LOG: {e}")
            try:
                create_exception(
                    client=client,
                    trace_id=trace_id,
                    reason_code=ReasonCode.SYSTEM_ERROR,
                    severity=ExceptionSeverity.HIGH,
                    source=ExceptionSource.INGEST,
                    message=f"fn_process_manager_approval: Failed to log to DELIVERY_LOG: {str(e)}",
                )
            except Exception:
                logger.error(f"[{trace_id}] Failed to create exception record")

        # 7. Update MARGIN_APPROVAL_LOG to "Approved"
        col_app_pct = manifest.get_column_name(Sheet.MARGIN_APPROVAL_LOG, "MANAGER_ADJUSTED_PCT")  # TODO: add to logical_names.py

        approval_updates = {
            Column.MARGIN_APPROVAL_LOG.STATUS: "Approved",
        }

        col_ids = manifest.get_all_column_ids(Sheet.MARGIN_APPROVAL_LOG)
        if col_app_pct and col_app_pct in col_ids:
            approval_updates["MANAGER_ADJUSTED_PCT"] = penalty_pct  # TODO: add to logical_names.py

        try:
            client.update_row(Sheet.MARGIN_APPROVAL_LOG, app_smartsheet_row_id, approval_updates)
            log_user_action(
                client=client,
                user_id=approver,
                action_type=ActionType.TAG_UPDATED,
                target_table="MARGIN_APPROVAL_LOG",
                target_id=str(approval_row_id),
                new_value="Approved",
                trace_id=trace_id,
            )
        except Exception as e:
            logger.error(f"[{trace_id}] Failed to update margin log: {e}")
            try:
                create_exception(
                    client=client,
                    trace_id=trace_id,
                    reason_code=ReasonCode.SYSTEM_ERROR,
                    severity=ExceptionSeverity.HIGH,
                    source=ExceptionSource.INGEST,
                    message=f"fn_process_manager_approval: Failed to update MARGIN_APPROVAL_LOG: {str(e)}",
                )
            except Exception:
                logger.error(f"[{trace_id}] Failed to create exception record")

        # 8. Update TAG_REGISTRY Status to Dispatched
        if tag_smartsheet_ids_to_update:
            for tag_row_id in tag_smartsheet_ids_to_update:
                try:
                    client.update_row(Sheet.TAG_REGISTRY, tag_row_id, {
                        Column.TAG_REGISTRY.STATUS: "Dispatched",
                    })
                except Exception as e:
                    logger.error(f"[{trace_id}] Failed to dispatch tag row {tag_row_id}: {e}")
                    try:
                        create_exception(
                            client=client,
                            trace_id=trace_id,
                            reason_code=ReasonCode.SYSTEM_ERROR,
                            severity=ExceptionSeverity.MEDIUM,
                            source=ExceptionSource.INGEST,
                            message=f"fn_process_manager_approval: Failed to dispatch tag row {tag_row_id}: {str(e)}",
                        )
                    except Exception:
                        logger.error(f"[{trace_id}] Failed to create exception record")

            log_user_action(
                client=client,
                user_id=approver,
                action_type=ActionType.TAG_UPDATED,
                target_table="TAG_REGISTRY",
                target_id=", ".join(str(t) for t in all_tags),
                new_value="Dispatched",
                trace_id=trace_id,
            )

    return func.HttpResponse(json.dumps({
        "status": "success",
        "delivery_id": delivery_id,
        "blob_path": do_blob_path
    }), mimetype="application/json")
