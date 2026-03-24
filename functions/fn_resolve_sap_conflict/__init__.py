"""
fn_resolve_sap_conflict: Handle PM response to SAP material conflict card
=========================================================================
Endpoint: POST /api/sap-conflicts/resolve

Receives PM's SAP code selections and creates LPO-scoped overrides
in MAPPING_OVERRIDE (05b).
"""
import json
import logging
import uuid

import azure.functions as func

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.smartsheet_client import SmartsheetClient
from shared.manifest import get_manifest
from shared.logical_names import Sheet, Column
from shared.audit import log_user_action, create_exception
from shared.models import ActionType, ReasonCode, ExceptionSeverity, ExceptionSource
from shared.helpers import format_datetime_for_smartsheet, now_uae

logger = logging.getLogger(__name__)


def main(req: func.HttpRequest) -> func.HttpResponse:
    trace_id = str(uuid.uuid4())
    client = None

    try:
        try:
            req_body = req.get_json()
        except ValueError:
            return func.HttpResponse(
                json.dumps({"error": "Invalid JSON", "trace_id": trace_id}),
                status_code=400, mimetype="application/json"
            )

        # Accept both formats:
        # 1. Full Teams card response: {responder: {email: ...}, data: {action, conflict_*, ...}}
        # 2. Flat payload: {action, sap_reference, approver, conflict_*, ...}
        if "data" in req_body and isinstance(req_body["data"], dict):
            # Full Teams response — extract data and responder
            card_data = req_body["data"]
            responder = req_body.get("responder", {})
            approver = responder.get("email") or responder.get("userPrincipalName") or "system"
        else:
            # Flat payload (already extracted)
            card_data = req_body
            approver = req_body.get("approver", "system")

        action = card_data.get("action", "")
        # Clean sap_reference: strip trailing .0 from float coercion (Power Automate sends "12345.0")
        raw_ref = str(card_data.get("sap_reference", ""))
        sap_reference = raw_ref.rstrip("0").rstrip(".") if "." in raw_ref else raw_ref
        original_trace_id = card_data.get("trace_id", trace_id)

        logger.info(f"[{trace_id}] SAP conflict resolution: action={action}, sap_ref={sap_reference}")

        client = SmartsheetClient()
        manifest = get_manifest()

        # Handle skip
        if action == "skip_sap_overrides":
            try:
                log_user_action(
                    client=client, user_id=approver,
                    action_type=ActionType.TAG_UPDATED,
                    target_table="SAP_MATERIAL_CATALOG",
                    target_id=sap_reference,
                    notes=f"PM skipped SAP conflict resolution for {sap_reference}",
                    trace_id=trace_id,
                )
            except Exception as e:
                logger.warning(f"[{trace_id}] Failed to log skip action: {e}")

            return func.HttpResponse(
                json.dumps({"status": "SKIPPED", "message": "Using default SAP codes", "trace_id": trace_id}),
                status_code=200, mimetype="application/json"
            )

        # Handle approve
        if action != "approve_sap_overrides":
            return func.HttpResponse(
                json.dumps({"error": f"Unknown action: {action}", "trace_id": trace_id}),
                status_code=400, mimetype="application/json"
            )

        # Extract conflict selections (keys like "conflict_CANONICAL-CODE")
        selections = {}
        for key, value in card_data.items():
            if key.startswith("conflict_") and value:
                canonical_code = key[len("conflict_"):]  # Strip prefix
                selections[canonical_code] = str(value)

        if not selections:
            return func.HttpResponse(
                json.dumps({"error": "No conflict selections provided", "trace_id": trace_id}),
                status_code=400, mimetype="application/json"
            )

        logger.info(f"[{trace_id}] Processing {len(selections)} SAP override selections for {sap_reference}")

        created_count = 0
        skipped_count = 0

        for canonical_code, selected_sap_code in selections.items():
            # Deterministic override ID for idempotency
            override_id = f"OVR-{sap_reference}-{canonical_code}"

            # Idempotency check
            existing = client.find_row(
                Sheet.MAPPING_OVERRIDE,
                Column.MAPPING_OVERRIDE.OVERRIDE_ID,
                override_id
            )
            if existing:
                logger.info(f"[{trace_id}] Override {override_id} already exists, skipping")
                skipped_count += 1
                continue

            # Look up nesting description from Material Master
            nesting_description = ""
            try:
                sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fn_map_lookup"))
                from mapping_service import MappingService
                mapping_svc = MappingService(client)
                # Search material master cache for this canonical code
                mapping_svc._ensure_cache_fresh()
                for entry in mapping_svc._material_master_cache.values():
                    if entry.canonical_code == canonical_code:
                        nesting_description = entry.nesting_description or ""
                        break
            except Exception as e:
                logger.warning(f"[{trace_id}] Could not look up nesting_description for {canonical_code}: {e}")

            # Create override row
            override_row = {
                Column.MAPPING_OVERRIDE.OVERRIDE_ID: override_id,
                Column.MAPPING_OVERRIDE.SCOPE_TYPE: "LPO",
                Column.MAPPING_OVERRIDE.SCOPE_VALUE: sap_reference,
                Column.MAPPING_OVERRIDE.NESTING_DESCRIPTION: nesting_description,
                Column.MAPPING_OVERRIDE.CANONICAL_CODE: canonical_code,
                Column.MAPPING_OVERRIDE.SAP_CODE: selected_sap_code,
                Column.MAPPING_OVERRIDE.ACTIVE: "Yes",
                Column.MAPPING_OVERRIDE.EFFECTIVE_FROM: now_uae().date().isoformat(),
                Column.MAPPING_OVERRIDE.CREATED_BY: approver,
                Column.MAPPING_OVERRIDE.CREATED_AT: format_datetime_for_smartsheet(now_uae()),
            }

            try:
                client.add_row(Sheet.MAPPING_OVERRIDE, override_row)
                created_count += 1
                logger.info(f"[{trace_id}] Created override {override_id}: {canonical_code} -> {selected_sap_code}")

                log_user_action(
                    client=client, user_id=approver,
                    action_type=ActionType.OVERRIDE_CREATED,
                    target_table="MAPPING_OVERRIDE",
                    target_id=override_id,
                    notes=f"LPO {sap_reference}: {canonical_code} -> {selected_sap_code}",
                    trace_id=trace_id,
                )
            except Exception as e:
                logger.error(f"[{trace_id}] Failed to create override {override_id}: {e}")
                try:
                    create_exception(
                        client=client, trace_id=trace_id,
                        reason_code=ReasonCode.SYSTEM_ERROR,
                        severity=ExceptionSeverity.HIGH,
                        source=ExceptionSource.INGEST,
                        message=f"Failed to create SAP override {override_id}: {str(e)[:500]}",
                    )
                except Exception:
                    logger.error(f"[{trace_id}] Failed to create exception record")

        return func.HttpResponse(
            json.dumps({
                "status": "OK",
                "overrides_created": created_count,
                "overrides_skipped": skipped_count,
                "sap_reference": sap_reference,
                "trace_id": trace_id,
            }),
            status_code=200, mimetype="application/json"
        )

    except Exception as e:
        logger.exception(f"[{trace_id}] Error in fn_resolve_sap_conflict: {e}")
        try:
            create_exception(
                client=client or SmartsheetClient(),
                trace_id=trace_id,
                reason_code=ReasonCode.SYSTEM_ERROR,
                severity=ExceptionSeverity.CRITICAL,
                source=ExceptionSource.INGEST,
                message=f"fn_resolve_sap_conflict unhandled error: {str(e)[:500]}",
            )
        except Exception:
            logger.error(f"[{trace_id}] Failed to create exception record")
        return func.HttpResponse(
            json.dumps({"error": str(e), "trace_id": trace_id}),
            status_code=500, mimetype="application/json"
        )
