"""
SAP Conflict Resolution Service
================================
Detects canonical codes with multiple SAP codes in the SAP Material Catalog (05c)
and dispatches an adaptive card to the Production Manager for resolution.

Triggered fire-and-forget from fn_lpo_ingest after LPO row creation.
"""
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional

import requests

from .smartsheet_client import SmartsheetClient
from .manifest import get_manifest
from .logical_names import Sheet, Column
from .audit import log_user_action, create_exception
from .models import ActionType, ReasonCode, ExceptionSeverity, ExceptionSource
from .adaptive_card_builder import build_sap_conflict_card

logger = logging.getLogger(__name__)


class SAPConflictService:
    """Orchestrates SAP conflict detection and PM notification."""

    def __init__(self, client: SmartsheetClient):
        self.client = client
        self.manifest = get_manifest()

    def check_and_notify_conflicts(
        self,
        sap_reference: str,
        customer_name: str,
        project_name: str,
        brand: str,
        trace_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Check SAP catalog for canonical codes with multiple SAP codes.
        If conflicts found, build and dispatch an adaptive card to PM.

        Returns card JSON if conflicts found and card dispatched, None otherwise.
        """
        # 1. Detect conflicts (deferred import to avoid circular dependency)
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from fn_map_lookup.mapping_service import MappingService
        service = MappingService(self.client)
        conflicts = service.get_sap_conflicts()

        if not conflicts:
            logger.info(f"[{trace_id}] No SAP conflicts detected for {sap_reference}")
            return None

        logger.info(f"[{trace_id}] Found {len(conflicts)} SAP conflicts for {sap_reference}")

        # 2. Fetch LPO details for the card
        from .helpers import parse_float_safe
        lpo_details = {}
        try:
            lpo_row = self.client.find_row(Sheet.LPO_MASTER, Column.LPO_MASTER.SAP_REFERENCE, sap_reference)
            if lpo_row:
                col = lambda c: self.manifest.get_column_name(Sheet.LPO_MASTER, c)
                lpo_details = {
                    "customer_lpo_ref": lpo_row.get(col(Column.LPO_MASTER.CUSTOMER_LPO_REF), ""),
                    "po_quantity_sqm": parse_float_safe(lpo_row.get(col(Column.LPO_MASTER.PO_QUANTITY_SQM)), default=0.0),
                    "po_value": parse_float_safe(lpo_row.get(col(Column.LPO_MASTER.PO_VALUE)), default=0.0),
                    "price_per_sqm": parse_float_safe(lpo_row.get(col(Column.LPO_MASTER.PRICE_PER_SQM)), default=0.0),
                    "wastage_pct": parse_float_safe(lpo_row.get(col(Column.LPO_MASTER.WASTAGE_CONSIDERED_IN_COSTING)), default=0.0),
                    "planned_gm_pct": parse_float_safe(lpo_row.get(col(Column.LPO_MASTER.PLANNED_GM_PCT)), default=None),
                }
        except Exception as e:
            logger.warning(f"[{trace_id}] Could not fetch LPO details for card: {e}")

        # 3. Enrich with SAP descriptions from Material Master (05a)
        enriched_conflicts = {}
        for canonical_code, entries in conflicts.items():
            sap_description = service.get_material_description(canonical_code)
            default_sap_code = service.get_default_sap_code(canonical_code)
            enriched_conflicts[canonical_code] = {
                "entries": entries,
                "sap_description": sap_description or canonical_code,
                "default_sap_code": default_sap_code,
            }

        # 4. Build adaptive card
        card_json = build_sap_conflict_card(
            sap_reference=sap_reference,
            customer_name=customer_name,
            project_name=project_name,
            brand=brand,
            conflicts=enriched_conflicts,
            lpo_details=lpo_details,
            trace_id=trace_id,
        )

        # 4. Dispatch to Power Automate
        webhook_url = os.environ.get("POWER_AUTOMATE_SAP_CONFLICT_URL", "")
        if not webhook_url:
            try:
                config_row = self.client.find_row(
                    Sheet.CONFIG, Column.CONFIG.CONFIG_KEY,
                    "POWER_AUTOMATE_SAP_CONFLICT_URL"
                )
                if config_row:
                    col_val = self.manifest.get_column_name(Sheet.CONFIG, Column.CONFIG.CONFIG_VALUE)
                    webhook_url = str(config_row.get(col_val, ""))
            except Exception:
                pass

        if webhook_url:
            try:
                payload = {
                    "card_json": card_json,
                    "sap_reference": sap_reference,
                    "conflict_count": len(conflicts),
                    "trace_id": trace_id,
                }
                resp = requests.post(webhook_url, json=payload, timeout=10)
                resp.raise_for_status()
                logger.info(f"[{trace_id}] SAP conflict card dispatched for {sap_reference}")
            except Exception as e:
                logger.warning(f"[{trace_id}] Failed to dispatch SAP conflict card: {e}")
                try:
                    create_exception(
                        client=self.client, trace_id=trace_id,
                        reason_code=ReasonCode.SAP_CODE_CONFLICT,
                        severity=ExceptionSeverity.MEDIUM,
                        source=ExceptionSource.INGEST,
                        message=f"Failed to dispatch SAP conflict card for {sap_reference}: {str(e)[:500]}",
                    )
                except Exception:
                    pass
        else:
            logger.warning(f"[{trace_id}] POWER_AUTOMATE_SAP_CONFLICT_URL not configured — SAP conflict card not sent")

        # 5. Log user action
        try:
            log_user_action(
                client=self.client, user_id="system",
                action_type=ActionType.SAP_CONFLICT_DETECTED,
                target_table="SAP_MATERIAL_CATALOG",
                target_id=sap_reference,
                notes=f"Detected {len(conflicts)} canonical codes with multiple SAP codes",
                trace_id=trace_id,
            )
        except Exception as e:
            logger.warning(f"[{trace_id}] Failed to log SAP conflict user action: {e}")

        return card_json
