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
from shared.helpers import now_uae, format_datetime_for_smartsheet, parse_float_safe, resolve_user_email, normalize_ref_value
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

    raw_action = req_body.get("action", "")
    approval_row_id = req_body.get("approval_row_id")

    # Normalize action — Power Automate may forward button title ("Proceed to DO")
    # or submit data value ("proceed_to_do") or any casing variant
    normalized = raw_action.lower().strip().replace("_", " ")
    if "proceed" in normalized or "do" in normalized:
        action = "proceed_to_do"
    elif "hold" in normalized or "wait" in normalized:
        action = "hold_tag"
    else:
        action = raw_action  # Unknown — will be rejected below

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

        # 2. Gather Tag Information — use find_row (handles float/string normalization)
        lpo_ref = ""
        tag_smartsheet_ids_to_update = []
        col_tr_lpo = manifest.get_column_name(Sheet.TAG_REGISTRY, Column.TAG_REGISTRY.LPO_SAP_REFERENCE)

        for t_id in all_tags:
            tag_row = client.find_row(Sheet.TAG_REGISTRY, Column.TAG_REGISTRY.TAG_ID, t_id)
            if tag_row:
                tag_smartsheet_ids_to_update.append(tag_row.get("row_id"))
                if not lpo_ref and col_tr_lpo:
                    lpo_ref = normalize_ref_value(tag_row.get(col_tr_lpo, ""))

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

        # 3. Gather Session IDs from NESTING_LOG
        nesting_rows = client.find_rows(Sheet.NESTING_LOG, Column.NESTING_LOG.TAG_SHEET_ID, all_tags[0]) if all_tags else []
        session_ids = []
        col_nl_session = manifest.get_column_name(Sheet.NESTING_LOG, Column.NESTING_LOG.NEST_SESSION_ID)
        for r in nesting_rows:
            sess_val = r.get(col_nl_session, "")
            if sess_val:
                session_ids.append(str(sess_val))

        # 3b. Resolve LPO area_type (Internal/External) for correct billing area
        area_type = "External"  # Default
        try:
            lpo_row = client.find_row(Sheet.LPO_MASTER, Column.LPO_MASTER.SAP_REFERENCE, lpo_ref)
            if lpo_row:
                col_area_type = manifest.get_column_name(Sheet.LPO_MASTER, Column.LPO_MASTER.AREA_TYPE)
                if col_area_type:
                    area_type = lpo_row.get(col_area_type) or "External"
        except Exception as e:
            logger.warning(f"[{trace_id}] Could not resolve area_type for LPO {lpo_ref}: {e}")

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

                # Read delivery lines from finished_goods_manifest (parsed from nesting)
                # Each line has internal_area_m2 and external_area_m2; pick based on LPO area_type
                fg_lines = nest_json.get("finished_goods_manifest", [])
                source_tag = nest_json.get("meta_data", {}).get("tag_id") or sess_id
                area_field = "external_area_m2" if area_type == "External" else "internal_area_m2"

                for line in fg_lines:
                    original_area = parse_float_safe(line.get(area_field, 0.0), default=0.0)
                    qty = parse_float_safe(line.get("qty_produced", line.get("qty", 1)), default=1.0)
                    line_area = original_area * qty
                    inflated_area = line_area * multiplier

                    master_do_lines.append({
                        "description": line.get("description", ""),
                        "qty": qty,
                        "original_area_m2": round(line_area, 4),
                        "billed_area_m2": round(inflated_area, 4),
                        "penalty_multiplier": multiplier,
                        "area_type": area_type,
                        "source_tag": source_tag,
                    })
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

        # 5. Compute original (un-penalised) area for cost calculation
        total_original_area = sum(
            line.get("original_area_m2", 0.0) for line in master_do_lines
        )

        # 6. Recalculate margin
        # Revenue uses inflated area (what the customer is billed)
        # Cost uses original area (actual production cost doesn't change with penalty)
        from shared.costing_service import CostingService
        from shared.adaptive_card_builder import build_do_creation_card

        costing = CostingService(client)
        try:
            margin_metrics = costing.calculate_margin(
                tag_sheet_id, total_original_area, lpo_ref
            )
            # Override revenue with inflated area (penalty-adjusted billing)
            selling_price = margin_metrics.get("selling_price_per_sqm", 0.0)
            inflated_revenue = total_inflated_area * selling_price
            inflated_profit = inflated_revenue - margin_metrics["total_cost_aed"]
            inflated_gm_pct = (inflated_profit / inflated_revenue) if inflated_revenue > 0 else 0.0

            margin_metrics["billed_area_sqm"] = round(total_inflated_area, 2)
            margin_metrics["original_area_sqm"] = round(total_original_area, 2)
            margin_metrics["total_revenue_aed"] = round(inflated_revenue, 2)
            margin_metrics["gross_profit_aed"] = round(inflated_profit, 2)
            margin_metrics["gm_pct"] = round(inflated_gm_pct, 4)
        except Exception as e:
            logger.warning(f"[{trace_id}] Margin recalculation failed, using basic figures: {e}")
            margin_metrics = {
                "total_cost_aed": 0.0,
                "total_revenue_aed": total_inflated_area * 25.0,  # fallback
                "gm_pct": 0.0,
            }

        margin_summary = {
            "original_area_sqm": round(total_original_area, 2),
            "billed_area_sqm": round(total_inflated_area, 2),
            "penalty_pct": penalty_pct,
            "adjusted_gm_pct": margin_metrics.get("gm_pct", 0.0),
            "adjusted_revenue_aed": margin_metrics.get("total_revenue_aed", 0.0),
            "total_cost_aed": margin_metrics.get("total_cost_aed", 0.0),
        }

        # 5b. Build and Save DO Payload (after margin calc so we can include it)
        delivery_id = f"DO-{uuid.uuid4().hex[:8].upper()}"
        do_payload = {
            "delivery_id": delivery_id,
            "lpo_reference": lpo_ref,
            "tags_included": all_tags,
            "area_type": area_type,
            "manager_penalty_pct": penalty_pct,
            "original_area_sqm": round(total_original_area, 2),
            "total_billed_area": round(total_inflated_area, 2),
            "margin_summary": margin_summary,
            "delivery_lines": master_do_lines,
            "generated_at": format_datetime_for_smartsheet(now_uae()),
            "trace_id": trace_id,
        }

        do_blob_path = f"{lpo_ref}/Deliveries/do_{delivery_id}.json"
        upload_json_blob(do_payload, do_blob_path, trace_id)

        # 7. Save SINGLE line to DELIVERY_LOG using logical column names
        approver = resolve_user_email(client, req_body.get("approver", "system"))
        delivery_row = {
            Column.DELIVERY_LOG.DELIVERY_ID: delivery_id,
            Column.DELIVERY_LOG.TAG_SHEET_ID: ", ".join(all_tags),
            Column.DELIVERY_LOG.SAP_DO_NUMBER: "PENDING_SAP",
            Column.DELIVERY_LOG.QUANTITY: total_inflated_area,
            Column.DELIVERY_LOG.LINES: len(master_do_lines),
        }

        try:
            client.add_row(Sheet.DELIVERY_LOG, delivery_row)
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
                    message=f"fn_process_manager_approval: Failed to log to DELIVERY_LOG: {e}",
                )
            except Exception:
                logger.error(f"[{trace_id}] Failed to create exception record")

        # 8. Update MARGIN_APPROVAL_LOG to "Approved"
        approval_updates = {
            Column.MARGIN_APPROVAL_LOG.STATUS: "Approved",
            Column.MARGIN_APPROVAL_LOG.PM_ADJUSTED_PCT: penalty_pct,
            Column.MARGIN_APPROVAL_LOG.DECISION_DATE: format_datetime_for_smartsheet(now_uae()),
        }

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
                    message=f"fn_process_manager_approval: Failed to update MARGIN_APPROVAL_LOG: {e}",
                )
            except Exception:
                logger.error(f"[{trace_id}] Failed to create exception record")

        # 9. Update TAG_REGISTRY Status to Dispatched
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
                            message=f"fn_process_manager_approval: Failed to dispatch tag {tag_row_id}: {e}",
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

        # 10. Gather consumption data for all tags (split production vs accessories)
        from shared.allocation_service import _parse_rows

        production_lines = []
        accessory_lines = []
        try:
            cons_rows = _parse_rows(client.get_sheet(Sheet.CONSUMPTION_LOG))
            col_cons_tag = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.TAG_SHEET_ID)
            col_cons_mat = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.MATERIAL_CODE)
            col_cons_qty = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.QUANTITY)
            col_cons_uom = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.UOM)
            col_cons_type = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.CONSUMPTION_TYPE)
            col_cons_date = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.CONSUMPTION_DATE)
            col_cons_id = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.CONSUMPTION_ID)

            for row in cons_rows:
                if row.get(col_cons_tag) in all_tags:
                    line = {
                        "consumption_id": row.get(col_cons_id, ""),
                        "material_code": row.get(col_cons_mat, ""),
                        "quantity": parse_float_safe(row.get(col_cons_qty), default=0.0),
                        "uom": row.get(col_cons_uom, ""),
                        "type": row.get(col_cons_type, ""),
                        "date": row.get(col_cons_date, ""),
                    }
                    if str(line["type"]).lower() in ("accessory", "accessories"):
                        accessory_lines.append(line)
                    else:
                        production_lines.append(line)

            logger.info(f"[{trace_id}] Gathered {len(production_lines)} production + {len(accessory_lines)} accessory consumption lines")
        except Exception as e:
            logger.warning(f"[{trace_id}] Failed to fetch consumption data for DO card: {e}")

        # 11. Send DO Creation Card to Supervisor / Teams Channel
        try:
            do_card = build_do_creation_card(
                delivery_id=delivery_id,
                lpo_reference=lpo_ref,
                tags=all_tags,
                total_billed_area=total_inflated_area,
                penalty_pct=penalty_pct,
                margin_summary=margin_summary,
                approval_row_id=str(approval_row_id),
                production_lines=production_lines,
                accessory_lines=accessory_lines,
            )

            import os
            import requests
            do_webhook_url = os.environ.get("POWER_AUTOMATE_DO_CREATION_URL", "")
            if not do_webhook_url:
                # Try reading from config
                try:
                    config_row = client.find_row(Sheet.CONFIG, Column.CONFIG.CONFIG_KEY, "POWER_AUTOMATE_DO_CREATION_URL")
                    if config_row:
                        col_val = manifest.get_column_name(Sheet.CONFIG, Column.CONFIG.CONFIG_VALUE)
                        do_webhook_url = str(config_row.get(col_val, ""))
                except Exception:
                    pass

            if do_webhook_url:
                payload = {
                    "card_json": do_card,
                    "delivery_id": delivery_id,
                    "lpo_reference": lpo_ref,
                    "tags": all_tags,
                    "trace_id": trace_id,
                }
                resp = requests.post(do_webhook_url, json=payload, timeout=10)
                resp.raise_for_status()
                logger.info(f"[{trace_id}] DO creation card sent to supervisor for {delivery_id}")
            else:
                logger.warning(f"[{trace_id}] POWER_AUTOMATE_DO_CREATION_URL not configured — DO card not sent")
        except Exception as e:
            logger.error(f"[{trace_id}] Failed to send DO creation card: {e}")
            try:
                create_exception(
                    client=client,
                    trace_id=trace_id,
                    reason_code=ReasonCode.SYSTEM_ERROR,
                    severity=ExceptionSeverity.MEDIUM,
                    source=ExceptionSource.INGEST,
                    message=f"fn_process_manager_approval: Failed to send DO creation card: {e}",
                )
            except Exception:
                logger.error(f"[{trace_id}] Failed to create exception record")

    return func.HttpResponse(json.dumps({
        "status": "success",
        "delivery_id": delivery_id,
        "blob_path": do_blob_path,
        "margin_summary": margin_summary,
    }), mimetype="application/json")
