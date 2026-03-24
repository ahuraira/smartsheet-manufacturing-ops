import logging
from typing import Dict, Any, List, Optional
from decimal import Decimal

from .logical_names import Sheet, Column
from .manifest import get_manifest
from .smartsheet_client import SmartsheetClient
from .helpers import parse_float_safe
from .audit import create_exception
from .models import ReasonCode, ExceptionSeverity, ExceptionSource

logger = logging.getLogger(__name__)

class CostingService:
    """
    Handles deterministic cost and margin calculations.
    Ensures SOTA separation of concerns by keeping all business logic out of Smartsheet.
    """
    
    # Fixed Setup Costs (AED per SQM Delivered) per specification
    FIXED_COSTS = {
        "executive_labor": 1.00,
        "floor_staff": 6.00,
        "delivery": 1.00,
        "machine_depreciation": 0.27,
        "utility_charges": 0.10,
        "warehouse_rent": 0.50,
        "scrap_collection": 0.20,
        "finance_charges": 0.45,
        "additional_warranty": 0.00,
        "factory_acceptance_test": 0.00,
        "factory_witness_test": 0.00,
        "installation_charges": 0.00,
        "testing_and_commissioning": 0.00,
        "dlp": 0.00,
        "amc": 0.00
    }
    
    TOTAL_FIXED_COST_PER_SQM = sum(FIXED_COSTS.values())
    CREDIT_RISK_PCT = 0.01

    def __init__(self, client: SmartsheetClient):
        self.client = client
        self.manifest = get_manifest()
        self._price_cache = {}
        
    def _get_config_value(self, config_key: str, default: float) -> float:
        """Fetch numeric config, cache it locally for the request instance."""
        if config_key in self._price_cache:
            return self._price_cache[config_key]
            
        mfst = self.manifest
        col_key = mfst.get_column_name(Sheet.CONFIG, Column.CONFIG.CONFIG_KEY)
        col_val = mfst.get_column_name(Sheet.CONFIG, Column.CONFIG.CONFIG_VALUE)
        
        try:
            rows = self.client.find_rows(Sheet.CONFIG, Column.CONFIG.CONFIG_KEY, config_key)
            if rows:
                val = parse_float_safe(rows[0].get(col_val, default), default=default)
                self._price_cache[config_key] = val
                return val
        except Exception as e:
            logger.warning(f"Error reading config key {config_key}: {e}")
            try:
                create_exception(
                    client=self.client, trace_id="costing-config",
                    reason_code=ReasonCode.SYSTEM_ERROR,
                    severity=ExceptionSeverity.LOW,
                    source=ExceptionSource.ALLOCATION,
                    message=f"Failed to read config key {config_key}: {str(e)[:500]}"
                )
            except Exception:
                pass

        self._price_cache[config_key] = default
        return default

    def get_material_unit_cost(self, material_code: str) -> float:
        """
        Retrieves the standard cost of a material from SAP_INVENTORY_SNAPSHOT
        (Unrestricted Value / Unrestricted Quantity).
        Falls back to CONFIG table overrides if snapshot data is missing or invalid.
        """
        cache_key = f"COST_{material_code}"
        if cache_key in self._price_cache:
            return self._price_cache[cache_key]
            
        mfst = self.manifest
        col_val = mfst.get_column_name(Sheet.SAP_INVENTORY_SNAPSHOT, Column.SAP_INVENTORY_SNAPSHOT.UNRESTRICTED_VALUE)
        col_qty = mfst.get_column_name(Sheet.SAP_INVENTORY_SNAPSHOT, Column.SAP_INVENTORY_SNAPSHOT.UNRESTRICTED_QUANTITY)

        try:
            rows = self.client.find_rows(Sheet.SAP_INVENTORY_SNAPSHOT, Column.SAP_INVENTORY_SNAPSHOT.MATERIAL_CODE, material_code)
            if rows:
                val_str = rows[0].get(col_val, 0.0)
                qty_str = rows[0].get(col_qty, 0.0)
                
                try:
                    val = parse_float_safe(val_str, default=0.0)
                    qty = parse_float_safe(qty_str, default=0.0)
                    
                    if qty > 0:
                        unit_price = val / qty
                        self._price_cache[cache_key] = unit_price
                        return unit_price
                except ValueError:
                    pass
        except Exception as e:
            logger.warning(f"Failed to fetch SAP price for {material_code}: {e}")
            try:
                create_exception(
                    client=self.client, trace_id="costing-material",
                    reason_code=ReasonCode.SYSTEM_ERROR,
                    severity=ExceptionSeverity.LOW,
                    source=ExceptionSource.ALLOCATION,
                    message=f"Failed to fetch SAP price for {material_code}: {str(e)[:500]}"
                )
            except Exception:
                pass

        # Default fallback — WARNING: 0.0 cost will inflate margin calculations
        logger.warning(f"[COSTING] No unit cost found for {material_code} — defaulting to 0.0, margin may be inflated")
        return self._get_config_value(cache_key, 0.0)

    def calculate_material_costs_split(self, tag_sheet_id: str) -> Dict[str, float]:
        """
        Calculate material costs split by consumption type (PRODUCTION vs ACCESSORY).

        Returns:
            {"production_cost": float, "accessory_cost": float, "total_cost": float}
        """
        rows = self.client.find_rows(
            sheet_ref=Sheet.CONSUMPTION_LOG,
            column_ref=Column.CONSUMPTION_LOG.TAG_SHEET_ID,
            value=tag_sheet_id
        )

        mfst = self.manifest
        col_qty = mfst.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.QUANTITY)
        col_material = mfst.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.MATERIAL_CODE)
        col_type = mfst.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.CONSUMPTION_TYPE)

        production_cost = 0.0
        accessory_cost = 0.0

        for row in rows:
            qty = parse_float_safe(row.get(col_qty, 0.0), default=0.0)
            material_code = row.get(col_material, "")
            cons_type = str(row.get(col_type, "")).lower() if col_type else ""

            if material_code and qty > 0:
                unit_cost = self.get_material_unit_cost(str(material_code))
                line_cost = qty * unit_cost
                if cons_type in ("accessory", "accessories"):
                    accessory_cost += line_cost
                else:
                    production_cost += line_cost

        return {
            "production_cost": production_cost,
            "accessory_cost": accessory_cost,
            "total_cost": production_cost + accessory_cost,
        }

    def calculate_material_costs(self, tag_sheet_id: str) -> float:
        """Total material cost (production + accessory). Legacy wrapper."""
        return self.calculate_material_costs_split(tag_sheet_id)["total_cost"]

    def calculate_margin(self, tag_sheet_id: str, delivered_sqm: float, lpo_sap_ref: Optional[str]) -> Dict[str, Any]:
        """
        Core Margin Calculation.

        Cost model:
        - Variable costs = production material cost + accessory material cost (per-tag from consumption)
        - Fixed costs = factory overhead per sqm (not attributable per-order)
        - Eq. accessory SQM = accessory_cost / (price_per_sqm × (1 - target_margin))
          so billing covers both cost AND target margin on accessories
        - Billable area = production area (delivered_sqm) + eq_accessory_sqm
        - Revenue = billable_area × price_per_sqm
        - Total cost = material cost + fixed cost + credit risk
        - GM% = (revenue - total cost) / revenue
        """
        # 1. Material costs split by type
        cost_split = self.calculate_material_costs_split(tag_sheet_id)
        production_material_cost = cost_split["production_cost"]
        accessory_material_cost = cost_split["accessory_cost"]
        total_material_cost = cost_split["total_cost"]

        # Fetch LPO specifics
        selling_price_per_sqm = 0.0
        target_margin_pct = self._get_config_value("DEFAULT_MARGIN_PCT", 0.12)

        if lpo_sap_ref:
            try:
                lpo_rows = self.client.find_rows(
                    sheet_ref=Sheet.LPO_MASTER,
                    column_ref=Column.LPO_MASTER.SAP_REFERENCE,
                    value=lpo_sap_ref
                )
                if not lpo_rows:
                    lpo_rows = self.client.find_rows(
                        sheet_ref=Sheet.LPO_MASTER,
                        column_ref=Column.LPO_MASTER.CUSTOMER_LPO_REF,
                        value=lpo_sap_ref
                    )

                if lpo_rows:
                    mfst = self.manifest
                    col_price = mfst.get_column_name(Sheet.LPO_MASTER, Column.LPO_MASTER.PRICE_PER_SQM)
                    col_planned_gm = mfst.get_column_name(Sheet.LPO_MASTER, Column.LPO_MASTER.PLANNED_GM_PCT)
                    col_margin = mfst.get_column_name(Sheet.LPO_MASTER, Column.LPO_MASTER.MARGIN_PCT)

                    price_val = lpo_rows[0].get(col_price, 0.0)
                    selling_price_per_sqm = parse_float_safe(price_val, default=0.0)

                    planned_val = parse_float_safe(
                        lpo_rows[0].get(col_planned_gm) if col_planned_gm else None,
                        default=None
                    )
                    if planned_val is None and col_margin:
                        planned_val = parse_float_safe(lpo_rows[0].get(col_margin), default=None)

                    if planned_val is not None:
                        target_margin_pct = planned_val / 100.0 if planned_val > 1.0 else planned_val
            except Exception as e:
                logger.warning(f"Failed to extract LPO details for {lpo_sap_ref}: {e}")
                try:
                    create_exception(
                        client=self.client, trace_id="costing-lpo",
                        reason_code=ReasonCode.SYSTEM_ERROR,
                        severity=ExceptionSeverity.MEDIUM,
                        source=ExceptionSource.ALLOCATION,
                        message=f"Failed to extract LPO details for {lpo_sap_ref}: {str(e)[:500]}"
                    )
                except Exception:
                    pass

        if selling_price_per_sqm <= 0.0:
            logger.warning(f"Could not determine Selling Price for LPO {lpo_sap_ref}. Using Config default.")
            selling_price_per_sqm = self._get_config_value("DEFAULT_SELLING_PRICE", 50.0)

        # 2. Equivalent accessory SQM — includes target margin on accessories
        #    eq_acc_sqm = accessory_cost / (price × (1 - target_margin))
        #    so billing eq_acc_sqm × price covers cost + margin
        margin_denominator = selling_price_per_sqm * (1.0 - target_margin_pct)
        if margin_denominator > 0:
            eq_accessory_sqm = accessory_material_cost / margin_denominator
        else:
            eq_accessory_sqm = 0.0

        # 3. Billable area = production area + equivalent accessory area
        billable_area = delivered_sqm + eq_accessory_sqm

        # 4. Fixed cost (factory overhead — applied on production area only)
        #    Read from config if available, otherwise use hardcoded default
        fixed_cost_per_sqm = self._get_config_value("FIXED_COST_PER_SQM", self.TOTAL_FIXED_COST_PER_SQM)
        fixed_cost = fixed_cost_per_sqm * delivered_sqm

        # 5. Credit risk (1% of all costs)
        pre_risk_cost = total_material_cost + fixed_cost
        credit_risk = pre_risk_cost * self.CREDIT_RISK_PCT

        # 6. Total cost
        total_cost = pre_risk_cost + credit_risk

        # 7. Revenue = billable_area × price_per_sqm
        total_revenue = billable_area * selling_price_per_sqm

        # 8. Gross Profit & GM%
        gross_profit = total_revenue - total_cost
        gm_pct = (gross_profit / total_revenue) if total_revenue > 0 else 0.0

        # 9. Corporate Tax (9%)
        corp_tax = gross_profit * 0.09 if gross_profit > 0 else 0.0

        # 10. Required area variation to hit target margin
        if target_margin_pct < 1.0:
            target_revenue = total_cost / (1.0 - target_margin_pct)
        else:
            target_revenue = total_cost
        required_billing_area = target_revenue / selling_price_per_sqm if selling_price_per_sqm > 0 else billable_area

        if billable_area > 0:
            area_variation_pct = (required_billing_area / billable_area) - 1.0
        else:
            area_variation_pct = 0.0

        return {
            "delivered_sqm": round(delivered_sqm, 2),
            "eq_accessory_sqm": round(eq_accessory_sqm, 2),
            "billable_area_sqm": round(billable_area, 2),
            "production_material_cost_aed": round(production_material_cost, 2),
            "accessory_material_cost_aed": round(accessory_material_cost, 2),
            "material_cost_aed": round(total_material_cost, 2),
            "fixed_cost_aed": round(fixed_cost, 2),
            "credit_risk_aed": round(credit_risk, 2),
            "total_cost_aed": round(total_cost, 2),
            "selling_price_per_sqm": round(selling_price_per_sqm, 2),
            "total_revenue_aed": round(total_revenue, 2),
            "gross_profit_aed": round(gross_profit, 2),
            "gm_pct": round(gm_pct, 4),
            "corp_tax_aed": round(corp_tax, 2),
            "target_margin_pct": round(target_margin_pct, 4),
            "required_billing_area": round(required_billing_area, 2),
            "area_variation_pct": round(area_variation_pct, 4),
            "suggested_manager_penalty_pct": round(area_variation_pct * 100, 2),
        }
