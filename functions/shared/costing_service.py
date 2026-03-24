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

    def calculate_material_costs(self, tag_sheet_id: str) -> float:
        """
        Sums up the actual material costs consumed by this Tag Sheet.
        Retrieves from CONSUMPTION_LOG.
        """
        rows = self.client.find_rows(
            sheet_ref=Sheet.CONSUMPTION_LOG,
            column_ref=Column.CONSUMPTION_LOG.TAG_SHEET_ID,
            value=tag_sheet_id
        )
        
        total_cost = 0.0
        mfst = self.manifest
        
        col_qty = mfst.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.QUANTITY)
        col_material = mfst.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.MATERIAL_CODE)
        
        for row in rows:
            qty_val = row.get(col_qty, 0.0)
            material_code = row.get(col_material, "")
            
            qty = parse_float_safe(qty_val, default=0.0)
                
            if material_code and qty > 0:
                unit_cost = self.get_material_unit_cost(str(material_code))
                total_cost += (qty * unit_cost)
                
        return total_cost

    def calculate_margin(self, tag_sheet_id: str, delivered_sqm: float, lpo_sap_ref: Optional[str]) -> Dict[str, Any]:
        """
        Core Margin Calculation Logic conforming strictly to SOTA equations.
        Returns all computed values required for the Adaptive Card and DB storage.
        """
        # 1. Total Material Cost
        material_cost = self.calculate_material_costs(tag_sheet_id)
        
        # 2. Total Fixed Cost
        fixed_cost = self.TOTAL_FIXED_COST_PER_SQM * delivered_sqm
        
        # 3. Pre-Risk Cost
        pre_risk_cost = material_cost + fixed_cost
        
        # 4. Credit Risk (1%)
        credit_risk = pre_risk_cost * self.CREDIT_RISK_PCT
        
        # 5. Total Cost
        total_cost = pre_risk_cost + credit_risk
        
        # Fetch LPO specifics
        selling_price_per_sqm = 0.0
        target_margin_pct = self._get_config_value("DEFAULT_MARGIN_PCT", 0.12)
        
        if lpo_sap_ref:
            try:
                lpo_rows = self.client.find_rows(
                    sheet_ref=Sheet.LPO_MASTER,
                    column_ref=Column.LPO_MASTER.SAP_REFERENCE,  # Ensure correct fallback or index
                    value=lpo_sap_ref
                )
                if not lpo_rows:
                    # Try by Customer LPO ref just in case
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

                    # Use PLANNED_GM_PCT (user-entered target) as primary,
                    # fall back to MARGIN_PCT, then config default
                    planned_val = parse_float_safe(
                        lpo_rows[0].get(col_planned_gm) if col_planned_gm else None,
                        default=None
                    )
                    if planned_val is None and col_margin:
                        planned_val = parse_float_safe(lpo_rows[0].get(col_margin), default=None)

                    if planned_val is not None:
                        # Normalize: 12 → 0.12, 0.12 stays 0.12
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
            logger.warning(f"Could not determine Selling Price for LPO {lpo_sap_ref}. Using Config default for testing.")
            selling_price_per_sqm = self._get_config_value("DEFAULT_SELLING_PRICE", 50.0)
            
        # 6. Total Revenue
        total_revenue = delivered_sqm * selling_price_per_sqm
        
        # 7. Gross Profit
        gross_profit = total_revenue - total_cost
        
        # 8. Gross Margin (GM) %
        gm_pct = (gross_profit / total_revenue) if total_revenue > 0 else 0.0
        
        # 9. Corporate Tax (9%)
        corp_tax = gross_profit * 0.09 if gross_profit > 0 else 0.0
        
        # 10. Required Area Variation %
        if target_margin_pct < 1.0:
            target_revenue = total_cost / (1.0 - target_margin_pct)
        else:
            target_revenue = total_cost
            
        required_billing_area = target_revenue / selling_price_per_sqm if selling_price_per_sqm > 0 else delivered_sqm
        
        if delivered_sqm > 0:
            area_variation_pct = (required_billing_area / delivered_sqm) - 1.0
        else:
            area_variation_pct = 0.0

        return {
            "delivered_sqm": delivered_sqm,
            "material_cost_aed": round(material_cost, 2),
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
            "suggested_manager_penalty_pct": round(area_variation_pct * 100, 2)
        }
