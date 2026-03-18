"""
Stock Service
=============

Computes available stock from SAP Inventory Snapshot + Inventory Transaction Log.

Strategy:
1. SAP_INVENTORY_SNAPSHOT provides baseline (updated 1-2x daily)
2. INVENTORY_TXN_LOG provides delta since last snapshot
3. Net available = SAP unrestricted - sum(Allocation txns) - sum(Issue txns) ...

Config toggle: STOCK_CHECK_ENABLED (default true) — when false, always returns Green.
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from .logical_names import Sheet, Column
from .manifest import get_manifest
from .helpers import parse_float_safe

logger = logging.getLogger(__name__)


# ── Config ──────────────────────────────────────────────────────────
STOCK_CHECK_ENABLED = os.environ.get("STOCK_CHECK_ENABLED", "true").lower() == "true"


# ── Data Models ─────────────────────────────────────────────────────
@dataclass
class AvailableStock:
    """Result of stock availability computation for a single material."""
    material_code: str
    sap_unrestricted: float = 0.0       # from SAP_INVENTORY_SNAPSHOT
    total_allocated: float = 0.0        # sum of Allocation txns (negative = reserved)
    total_consumed: float = 0.0         # sum of Issue/Consumption txns
    total_receipts: float = 0.0         # sum of Receipt txns since snapshot
    net_available: float = 0.0          # computed: sap + receipts - allocated - consumed
    stock_check_flag: str = "Green"     # Green / Yellow / Red
    stock_check_enabled: bool = True

    def to_dict(self):
        return {
            "material_code": self.material_code,
            "sap_unrestricted": self.sap_unrestricted,
            "total_allocated": self.total_allocated,
            "total_consumed": self.total_consumed,
            "total_receipts": self.total_receipts,
            "net_available": self.net_available,
            "stock_check_flag": self.stock_check_flag,
            "stock_check_enabled": self.stock_check_enabled,
        }


# ── Txn type classification ────────────────────────────────────────
# Positive impact on available qty
_POSITIVE_TXN_TYPES = {"Receipt", "Remnant Create", "Remnant Return"}
# Negative impact on available qty
_NEGATIVE_TXN_TYPES = {"Allocation", "Issue", "Consumption", "Pick", "DO Issue"}
# Adjustment can be either; we use the sign of the quantity


def _parse_rows(sheet_data: dict) -> list:
    """Convert raw Smartsheet sheet data into list of {physical_col_name: value} dicts."""
    columns = sheet_data.get("columns", [])
    col_id_to_name = {col["id"]: col["title"] for col in columns}
    parsed = []
    for raw_row in sheet_data.get("rows", []):
        row = {}
        for cell in raw_row.get("cells", []):
            col_id = cell.get("columnId")
            if col_id in col_id_to_name:
                row[col_id_to_name[col_id]] = cell.get("value") or cell.get("displayValue")
        row["row_id"] = raw_row.get("id")
        parsed.append(row)
    return parsed


def compute_available_qty(
    client,
    material_code: str,
    trace_id: str = "",
) -> AvailableStock:
    """
    Compute available stock for a material.

    Steps:
    1. Read latest SAP_INVENTORY_SNAPSHOT row for material → baseline unrestricted qty
    2. Read INVENTORY_TXN_LOG rows for material → compute net delta
    3. Combine: net_available = sap_unrestricted + receipts - allocations - issues

    Args:
        client: SmartsheetClient
        material_code: Canonical material code
        trace_id: For logging

    Returns:
        AvailableStock with computed quantities and stock check flag
    """
    result = AvailableStock(
        material_code=material_code,
        stock_check_enabled=STOCK_CHECK_ENABLED,
    )

    if not STOCK_CHECK_ENABLED:
        # Stock check disabled — always return Green with 0 quantities
        logger.info(f"[{trace_id}] Stock check disabled, returning Green for {material_code}")
        result.stock_check_flag = "Green"
        result.net_available = float("inf")  # unlimited
        return result

    manifest = get_manifest()

    # ── Step 1: SAP Snapshot baseline ───────────────────────────────
    try:
        col_sap_material = manifest.get_column_name(
            Sheet.SAP_INVENTORY_SNAPSHOT,
            "MATERIAL_CODE"
        )
        col_sap_unrestricted = manifest.get_column_name(
            Sheet.SAP_INVENTORY_SNAPSHOT,
            "UNRESTRICTED_QUANTITY"
        )

        sap_sheet = client.get_sheet(Sheet.SAP_INVENTORY_SNAPSHOT)
        sap_rows = _parse_rows(sap_sheet)

        # Find latest row for this material (last one wins — rows are appended chronologically)
        for row in reversed(sap_rows):
            if row.get(col_sap_material) == material_code:
                result.sap_unrestricted = parse_float_safe(row.get(col_sap_unrestricted), default=0.0)
                break

    except Exception as e:
        logger.warning(f"[{trace_id}] Failed to read SAP snapshot for {material_code}: {e}")
        # Continue — SAP snapshot may not exist yet

    # ── Step 2: Inventory Txn Log deltas ───────────────────────────
    try:
        col_txn_material = manifest.get_column_name(
            Sheet.INVENTORY_TXN_LOG,
            Column.INVENTORY_TXN_LOG.MATERIAL_CODE
        )
        col_txn_type = manifest.get_column_name(
            Sheet.INVENTORY_TXN_LOG,
            Column.INVENTORY_TXN_LOG.TXN_TYPE
        )
        col_txn_qty = manifest.get_column_name(
            Sheet.INVENTORY_TXN_LOG,
            Column.INVENTORY_TXN_LOG.QUANTITY
        )

        txn_sheet = client.get_sheet(Sheet.INVENTORY_TXN_LOG)
        txn_rows = _parse_rows(txn_sheet)

        for row in txn_rows:
            if row.get(col_txn_material) != material_code:
                continue

            txn_type = row.get(col_txn_type, "")
            qty = parse_float_safe(row.get(col_txn_qty), default=0.0)

            if txn_type in _POSITIVE_TXN_TYPES:
                result.total_receipts += qty
            elif txn_type == "Allocation":
                result.total_allocated += abs(qty)
            elif txn_type in _NEGATIVE_TXN_TYPES:
                result.total_consumed += abs(qty)
            elif txn_type == "Adjustment":
                # Adjustment: positive qty adds stock, negative removes
                if qty >= 0:
                    result.total_receipts += qty
                else:
                    result.total_consumed += abs(qty)

    except Exception as e:
        logger.warning(f"[{trace_id}] Failed to read Inventory Txn Log for {material_code}: {e}")

    # ── Step 3: Compute net available ──────────────────────────────
    result.net_available = (
        result.sap_unrestricted
        + result.total_receipts
        - result.total_allocated
        - result.total_consumed
    )

    logger.info(
        f"[{trace_id}] Stock for {material_code}: "
        f"SAP={result.sap_unrestricted}, receipts=+{result.total_receipts}, "
        f"allocated=-{result.total_allocated}, consumed=-{result.total_consumed}, "
        f"net={result.net_available}"
    )

    return result


def determine_stock_flag(available: float, needed: float) -> str:
    """
    Determine stock check flag colour.

    Args:
        available: Net available stock
        needed: Required quantity

    Returns:
        "Green" if available >= needed
        "Yellow" if available > 0 but < needed (partial)
        "Red" if available <= 0
    """
    if not STOCK_CHECK_ENABLED:
        return "Green"

    if available >= needed:
        return "Green"
    elif available > 0:
        return "Yellow"
    else:
        return "Red"
