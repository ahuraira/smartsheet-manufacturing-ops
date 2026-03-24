import logging
import uuid
from typing import Dict, Any, List, Optional

from .logical_names import Sheet, Column
from .manifest import get_manifest
from .smartsheet_client import SmartsheetClient
from .costing_service import CostingService
from .adaptive_card_builder import build_margin_approval_card
from .helpers import parse_float_safe, now_uae, format_datetime_for_smartsheet
from .audit import create_exception, log_user_action
from .models import ActionType, ReasonCode, ExceptionSeverity, ExceptionSource

logger = logging.getLogger(__name__)

class MarginOrchestrator:
    def __init__(self, client: SmartsheetClient):
        self.client = client
        self.manifest = get_manifest()
        self.costing_service = CostingService(client)

    def trigger_margin_approval_for_tag(self, tag_sheet_id: str, delivered_sqm: float, lpo_sap_ref: str, trace_id: str) -> Optional[Dict[str, Any]]:
        """
        Calculates margins, logs a Pending row in MARGIN_APPROVAL_LOG,
        generates the Adaptive Card payload, and dispatches to Power Automate.
        """
        if not tag_sheet_id or not lpo_sap_ref:
            logger.warning(f"[{trace_id}] Cannot trigger margin approval: missing tag_sheet_id={tag_sheet_id} or lpo_sap_ref={lpo_sap_ref}")
            return None

        logger.info(f"[{trace_id}] Orchestrating margin approval for Tag: {tag_sheet_id}")
        
        # 1. Calculate Metrics
        metrics = self.costing_service.calculate_margin(tag_sheet_id, delivered_sqm, lpo_sap_ref)
        
        # 2. Get Pending Tags for the same LPO to present in ChoiceSet
        # Target: Status = "Production Complete" (or "Complete") AND matching LPO
        pending_tags = []
        try:
            col_tag_id = self.manifest.get_column_name(Sheet.TAG_REGISTRY, Column.TAG_REGISTRY.TAG_ID)
            col_status = self.manifest.get_column_name(Sheet.TAG_REGISTRY, Column.TAG_REGISTRY.STATUS)
            col_lpo_ref = self.manifest.get_column_name(Sheet.TAG_REGISTRY, Column.TAG_REGISTRY.LPO_SAP_REFERENCE)
            col_sqm = self.manifest.get_column_name(Sheet.TAG_REGISTRY, Column.TAG_REGISTRY.TOTAL_AREA_SQM)
            
            # Note: client.find_rows might only support one filter, so fetching all for LPO and filtering local
            lpo_tags = self.client.find_rows(Sheet.TAG_REGISTRY, Column.TAG_REGISTRY.LPO_SAP_REFERENCE, lpo_sap_ref)
            
            for tr in lpo_tags:
                status = tr.get(col_status, "")
                if status in ("Complete", "Production Complete"):
                    sqm_val = tr.get(col_sqm, 0.0)
                    t_id = tr.get(col_tag_id)
                    if t_id:
                        pending_tags.append({
                            "id": str(t_id),
                            "delivered_sqm": parse_float_safe(sqm_val, default=0.0)
                        })
        except Exception as e:
            logger.warning(f"[{trace_id}] Failed to load pending tags for merging: {e}")
            try:
                create_exception(
                    client=self.client, trace_id=trace_id,
                    reason_code=ReasonCode.SYSTEM_ERROR,
                    severity=ExceptionSeverity.LOW,
                    source=ExceptionSource.ALLOCATION,
                    related_tag_id=tag_sheet_id,
                    message=f"Failed to load pending tags for merging: {str(e)[:500]}"
                )
            except Exception:
                pass

        # 3. Create MARGIN_APPROVAL_LOG row (Pending)
        from .id_generator import generate_next_approval_id
        approval_id = generate_next_approval_id(self.client)
        client_request_id = str(uuid.uuid4())
        
        # Use add_row() with logical column names — it handles resolution via manifest
        row_data = {
            Column.MARGIN_APPROVAL_LOG.APPROVAL_ID: approval_id,
            Column.MARGIN_APPROVAL_LOG.TAG_SHEET_ID: tag_sheet_id,
            Column.MARGIN_APPROVAL_LOG.LPO_ID: lpo_sap_ref,
            Column.MARGIN_APPROVAL_LOG.TOTAL_COST: metrics["total_cost_aed"],
            Column.MARGIN_APPROVAL_LOG.ACCESSORY_COST: metrics["accessory_material_cost_aed"],
            Column.MARGIN_APPROVAL_LOG.EQ_ACCESSORY_SQM: metrics["eq_accessory_sqm"],
            Column.MARGIN_APPROVAL_LOG.BASELINE_MARGIN_PCT: metrics["target_margin_pct"],
            Column.MARGIN_APPROVAL_LOG.FINAL_MARGIN_PCT: metrics["gm_pct"],
            Column.MARGIN_APPROVAL_LOG.STATUS: "Pending",
            Column.MARGIN_APPROVAL_LOG.CLIENT_REQUEST_ID: client_request_id,
            Column.MARGIN_APPROVAL_LOG.CREATED_DATE: format_datetime_for_smartsheet(now_uae()),
        }

        try:
            self.client.add_row(Sheet.MARGIN_APPROVAL_LOG, row_data)
            logger.info(f"[{trace_id}] Logged MARGIN_APPROVAL_LOG Pending row: {approval_id}")
            try:
                log_user_action(
                    client=self.client, user_id="system",
                    action_type=ActionType.DO_CREATED,
                    target_table="MARGIN_APPROVAL_LOG", target_id=approval_id,
                    new_value="Pending",
                    trace_id=trace_id
                )
            except Exception:
                pass
        except Exception as e:
            logger.error(f"[{trace_id}] Failed to write to MARGIN_APPROVAL_LOG: {e}")
            try:
                create_exception(
                    client=self.client, trace_id=trace_id,
                    reason_code=ReasonCode.SYSTEM_ERROR,
                    severity=ExceptionSeverity.CRITICAL,
                    source=ExceptionSource.ALLOCATION,
                    related_tag_id=tag_sheet_id,
                    message=f"Failed to write MARGIN_APPROVAL_LOG: {str(e)[:500]}"
                )
            except Exception:
                pass
            raise # Fail fast if we cannot audit log
            
        # 4. Generate Adaptive Card
        card_json = build_margin_approval_card(
            tag_sheet_id=tag_sheet_id,
            costing_metrics=metrics,
            pending_tags_for_lpo=pending_tags,
            approval_row_id=approval_id
        )
        
        # 5. Dispatch to Power Automate
        # Get the PA webhook URL for Margin cards from config or env
        pa_webhook_url = self._get_config_value("POWER_AUTOMATE_MANAGER_APPROVAL_URL", "")
        if not pa_webhook_url:
            import os
            pa_webhook_url = os.environ.get("POWER_AUTOMATE_MANAGER_APPROVAL_URL", "")
            
        if pa_webhook_url:
            import requests
            try:
                # Wrap the card inside a payload that PA can easily parse
                payload = {
                    "card_json": card_json,
                    "approval_id": approval_id,
                    "tag_sheet_id": tag_sheet_id,
                    "lpo": lpo_sap_ref,
                    "trace_id": trace_id
                }
                
                resp = requests.post(pa_webhook_url, json=payload, timeout=10)
                resp.raise_for_status()
                logger.info(f"[{trace_id}] Successfully posted Adaptive Card to Power Automate for {approval_id}")
            except Exception as e:
                logger.error(f"[{trace_id}] Failed to dispatch card to Power Automate URL: {e}")
                try:
                    create_exception(
                        client=self.client, trace_id=trace_id,
                        reason_code=ReasonCode.SYSTEM_ERROR,
                        severity=ExceptionSeverity.MEDIUM,
                        source=ExceptionSource.ALLOCATION,
                        related_tag_id=tag_sheet_id,
                        message=f"Failed to dispatch margin card to Power Automate: {str(e)[:500]}"
                    )
                except Exception:
                    pass
        else:
            logger.warning(f"[{trace_id}] PA_MARGIN_CARD_WEBHOOK_URL not configured. Card {approval_id} generated but not dispatched.")
        
        return card_json
        
    def _get_config_value(self, config_key: str, default: str) -> str:
        """Fetch string config from Smartsheet"""
        mfst = self.manifest
        col_key = mfst.get_column_name(Sheet.CONFIG, Column.CONFIG.CONFIG_KEY)
        col_val = mfst.get_column_name(Sheet.CONFIG, Column.CONFIG.CONFIG_VALUE)
        
        try:
            rows = self.client.find_rows(Sheet.CONFIG, Column.CONFIG.CONFIG_KEY, config_key)
            if rows:
                val = str(rows[0].get(col_val, default))
                return val
        except Exception:
            pass
        return default
