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
from datetime import datetime, timezone
from typing import List, Dict, Optional, Literal

from .logical_names import Sheet, Column
from .manifest import get_manifest

logger = logging.getLogger(__name__)

# Valid Transaction Types matching the logic defined in Architecture Specs
TxnType = Literal["Allocation", "Consumption", "Receipt", "Adjustment"]


def generate_txn_id() -> str:
    """Generate a unique transaction ID for inventory ledgers."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d")
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

    manifest = get_manifest()
    
    col_txn_id = manifest.get_column_name(Sheet.INVENTORY_TXN_LOG, Column.INVENTORY_TXN_LOG.TXN_ID)
    col_txn_date = manifest.get_column_name(Sheet.INVENTORY_TXN_LOG, Column.INVENTORY_TXN_LOG.TXN_DATE)
    col_txn_type = manifest.get_column_name(Sheet.INVENTORY_TXN_LOG, Column.INVENTORY_TXN_LOG.TXN_TYPE)
    col_txn_material = manifest.get_column_name(Sheet.INVENTORY_TXN_LOG, Column.INVENTORY_TXN_LOG.MATERIAL_CODE)
    col_txn_qty = manifest.get_column_name(Sheet.INVENTORY_TXN_LOG, Column.INVENTORY_TXN_LOG.QUANTITY)
    col_txn_ref = manifest.get_column_name(Sheet.INVENTORY_TXN_LOG, Column.INVENTORY_TXN_LOG.REFERENCE_DOC)
    col_txn_source = manifest.get_column_name(Sheet.INVENTORY_TXN_LOG, Column.INVENTORY_TXN_LOG.SOURCE_SYSTEM)
    col_txn_trace = manifest.get_column_name(Sheet.INVENTORY_TXN_LOG, Column.INVENTORY_TXN_LOG.TRACE_ID)

    rows_to_add = []
    generated_ids = []
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for txn in transactions:
        txn_id = generate_txn_id()
        generated_ids.append(txn_id)
        
        row_data = {
            col_txn_id: txn_id,
            col_txn_date: now_str,
            col_txn_type: txn.get("txn_type"),
            col_txn_material: txn.get("material_code"),
            col_txn_qty: float(txn.get("quantity", 0.0)),
            col_txn_ref: txn.get("reference_doc", ""),
            col_txn_source: txn.get("source_system", "AzureFunc"),
            col_txn_trace: trace_id,
        }
        rows_to_add.append(row_data)

    try:
        # We process this as a bulk write to Smartsheet INVENTORY_TXN_LOG sheet.
        # We do NOT pass strict:False here because we don't anticipate tricky Picklist issues
        # on the transaction log unless user adds one. Wait, let's keep it safe.
        # Actually add_rows_bulk in smartsheet_client.py now does strict traversal internally, 
        # but since we are just giving dictionaries, we need to map to physical IDs first or 
        # use the client properly. The client.add_rows doesn't have a bulk dictionary wrapper yet
        # that gracefully resolves dict to raw cells unless we loop add_row (slow) or use the 
        # native format (columnId, value) we fixed in consumption_service. 
        #
        # Let's map it cleanly to the standard cells format:
        sheet_id = manifest.get_sheet_id(Sheet.INVENTORY_TXN_LOG)
        
        # Build column name to ID map
        columns_meta = manifest.get_sheet_columns(Sheet.INVENTORY_TXN_LOG)
        col_name_to_id = {}
        if isinstance(columns_meta, list):
            for col in columns_meta:
                if isinstance(col, dict) and "title" in col and "id" in col:
                    col_name_to_id[col["title"]] = col["id"]
        
        smartsheet_payload = []
        for row in rows_to_add:
            cells = []
            for col_name, val in row.items():
                col_id = col_name_to_id.get(col_name)
                if col_id:
                    cells.append({
                        "columnId": col_id,
                        "value": val,
                        "strict": False
                    })
            if cells:
                smartsheet_payload.append({"toBottom": True, "cells": cells})
        
        if smartsheet_payload:
            client.add_rows_bulk(Sheet.INVENTORY_TXN_LOG, smartsheet_payload)
            logger.info(f"[{trace_id}] Logged {len(transactions)} inventory transactions in batch")
            
    except Exception as e:
        logger.error(f"[{trace_id}] Failed to log inventory batch: {str(e)}")
        # In this failure, we shouldn't necessarily crash the whole app if 
        # it was a consumption/allocation, but architecturally, transaction logs are critical.
        raise e

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
