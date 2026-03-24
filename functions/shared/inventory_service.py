"""
Inventory Service
=================

Centralized service for managing all interactions with the INVENTORY_TXN_LOG sheet.
Following SOTA architecture specifying that all logic strictly uses manifest column 
names and deterministic operations.

Handlers for transactions:
- Allocation (soft reservations)
- Consumption (actual production usage and accessories)
- Receipt (replenishment)
- Adjustment (cycle counts/reconciliation)
"""

import logging
import uuid
from typing import List, Dict, Optional, Literal

from .logical_names import Sheet, Column
from .manifest import get_manifest
from .helpers import now_uae, format_datetime_for_smartsheet

logger = logging.getLogger(__name__)

# Valid Transaction Types matching the logic defined in Architecture Specs
TxnType = Literal["Allocation", "Consumption", "Receipt", "Adjustment"]


def generate_txn_id() -> str:
    """Generate a unique transaction ID for inventory ledgers."""
    ts = now_uae().strftime("%Y%m%d")
    short_uuid = str(uuid.uuid4().hex)[:6].upper()
    return f"TXN-{ts}-{short_uuid}"


def log_inventory_transactions_batch(
    client,
    transactions: List[Dict],
    trace_id: str = ""
) -> List[str]:
    """
    Log a batch of inventory transactions efficiently.

    Args:
        client: SmartsheetClient instance
        transactions: List of dicts with keys:
            - txn_type: TxnType
            - material_code: str (SAP_CODE)
            - quantity: float (positive for receipt/adjustment, negative for consume/allocate)
            - reference_doc: str (Allocation ID, Consumption ID, DO ID, etc.)
            - source_system: str
        trace_id: Trace ID for logging

    Returns:
        List of generated transaction IDs
    """
    if not transactions:
        return []

    from .helpers import parse_float_safe

    generated_ids = []
    now_str = format_datetime_for_smartsheet(now_uae())

    # Build rows using LOGICAL column names — client.add_row() resolves via manifest
    rows_to_add = []
    for txn in transactions:
        txn_id = generate_txn_id()
        generated_ids.append(txn_id)

        row_data = {
            Column.INVENTORY_TXN_LOG.TXN_ID: txn_id,
            Column.INVENTORY_TXN_LOG.TXN_DATE: now_str,
            Column.INVENTORY_TXN_LOG.TXN_TYPE: txn.get("txn_type"),
            Column.INVENTORY_TXN_LOG.MATERIAL_CODE: txn.get("material_code"),
            Column.INVENTORY_TXN_LOG.QUANTITY: parse_float_safe(txn.get("quantity", 0.0), default=0.0),
            Column.INVENTORY_TXN_LOG.REFERENCE_DOC: txn.get("reference_doc", ""),
            Column.INVENTORY_TXN_LOG.SOURCE_SYSTEM: txn.get("source_system", "AzureFunc"),
            Column.INVENTORY_TXN_LOG.TRACE_ID: trace_id,
        }
        rows_to_add.append(row_data)

    try:
        for row_data in rows_to_add:
            client.add_row(Sheet.INVENTORY_TXN_LOG, row_data)

        logger.info(f"[{trace_id}] Logged {len(transactions)} inventory transactions in batch")

    except Exception as e:
        logger.error(f"[{trace_id}] Failed to log inventory batch: {e}")
        raise

    return generated_ids


def log_inventory_transaction(
    client,
    txn_type: TxnType,
    material_code: str,
    quantity: float,
    reference_doc: str = "",
    source_system: str = "AzureFunc",
    trace_id: str = ""
) -> str:
    """
    Log a single inventory transaction. Wrapper around the batch function.
    """
    txn = {
        "txn_type": txn_type,
        "material_code": material_code,
        "quantity": quantity,
        "reference_doc": reference_doc,
        "source_system": source_system
    }
    
    ids = log_inventory_transactions_batch(client, [txn], trace_id=trace_id)
    return ids[0] if ids else ""
