"""
fn_lpo_update: LPO Update Azure Function
=========================================

Updates existing LPO records by SAP Reference.

Key Features
------------
- SAP Reference is the lookup key
- Only updates provided fields (partial update)
- Validates quantity reduction against committed amounts
- Audit trail with old/new values

Request Format
--------------
{
    "client_request_id": "uuid-v4",
    "sap_reference": "PTE-185",         // REQUIRED - lookup key
    "po_quantity_sqm": 1200.0,          // Optional
    "price_per_sqm": 160.0,             // Optional
    "lpo_status": "Active",             // Optional
    "hold_reason": null,                // Optional
    "remarks": "Updated notes",         // Optional
    "updated_by": "user@company.com"    // REQUIRED
}

Response Codes
--------------
200 OK
    - "OK": LPO updated successfully

404 Not Found
    - "NOT_FOUND": SAP Reference not found

422 Unprocessable Entity
    - "BLOCKED": Validation failed (e.g., quantity conflict)

400 Bad Request
    - "ERROR": Invalid request format
"""

import logging
import json
import azure.functions as func
from datetime import datetime
from typing import Optional, Dict, Any

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import (
    # Logical Names
    Sheet,
    Column,
    
    # Models
    LPOUpdateRequest,
    ExceptionSeverity,
    ExceptionSource,
    ReasonCode,
    ActionType,
    
    # Client
    get_smartsheet_client,
    
    # Manifest
    get_manifest,
    
    # Helpers
    generate_trace_id,
    format_datetime_for_smartsheet,
    parse_float_safe,
    
    # Audit (shared - DRY principle)
    create_exception,
    log_user_action,
)


logger = logging.getLogger(__name__)

# DRY (v1.6.5): Use shared helper instead of local duplicate
from shared import get_physical_column_name
_get_physical_column_name = get_physical_column_name  # Alias for backward compat


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    Main entry point for LPO update.
    
    Flow:
    1. Parse and validate request
    2. Find LPO by SAP Reference
    3. Validate update (e.g., quantity conflicts)
    4. Update LPO record
    5. Log user action with old/new values
    6. Return success response
    """
    trace_id = generate_trace_id()
    
    try:
        # 1. Parse request
        try:
            body = req.get_json()
            request = LPOUpdateRequest(**body)
        except ValueError as e:
            logger.error(f"[{trace_id}] Request validation failed: {e}")
            return func.HttpResponse(
                json.dumps({
                    "status": "ERROR",
                    "message": f"Invalid request: {str(e)}",
                    "trace_id": trace_id
                }),
                status_code=400,
                mimetype="application/json"
            )
        
        logger.info(f"[{trace_id}] Processing LPO update: SAP={request.sap_reference}")
        
        # Get client
        client = get_smartsheet_client()
        
        # 2. Find LPO by SAP Reference
        existing_lpo = client.find_row(
            Sheet.LPO_MASTER,
            Column.LPO_MASTER.SAP_REFERENCE,
            request.sap_reference
        )
        
        if not existing_lpo:
            logger.warning(f"[{trace_id}] LPO not found: {request.sap_reference}")
            exception_id = create_exception(
                client=client,
                trace_id=trace_id,
                reason_code=ReasonCode.SAP_REF_NOT_FOUND,
                severity=ExceptionSeverity.MEDIUM,
                message=f"SAP Reference not found: {request.sap_reference}"
            )
            log_user_action(
                client=client,
                user_id=request.updated_by,
                action_type=ActionType.OPERATION_FAILED,
                target_table=Sheet.LPO_MASTER,
                target_id=request.sap_reference,
                notes=f"LPO not found. Exception: {exception_id}",
                trace_id=trace_id
            )
            return func.HttpResponse(
                json.dumps({
                    "status": "NOT_FOUND",
                    "exception_id": exception_id,
                    "trace_id": trace_id,
                    "message": f"SAP Reference {request.sap_reference} not found"
                }),
                status_code=404,
                mimetype="application/json"
            )
        
        row_id = existing_lpo.get("row_id")
        
        # Get physical column names for reading existing values
        po_qty_col = _get_physical_column_name("LPO_MASTER", "PO_QUANTITY_SQM")
        delivered_qty_col = _get_physical_column_name("LPO_MASTER", "DELIVERED_QUANTITY_SQM")
        
        # 3. Validate quantity update
        if request.po_quantity_sqm is not None:
            current_delivered = parse_float_safe(existing_lpo.get(delivered_qty_col), 0)
            
            if request.po_quantity_sqm < current_delivered:
                logger.warning(f"[{trace_id}] PO quantity conflict: new={request.po_quantity_sqm}, delivered={current_delivered}")
                exception_id = create_exception(
                    client=client,
                    trace_id=trace_id,
                    reason_code=ReasonCode.PO_QUANTITY_CONFLICT,
                    severity=ExceptionSeverity.HIGH,
                    message=f"Cannot reduce PO quantity below delivered amount. Requested: {request.po_quantity_sqm}, Delivered: {current_delivered}"
                )
                log_user_action(
                    client=client,
                    user_id=request.updated_by,
                    action_type=ActionType.OPERATION_FAILED,
                    target_table=Sheet.LPO_MASTER,
                    target_id=request.sap_reference,
                    notes=f"PO quantity conflict. Exception: {exception_id}",
                    trace_id=trace_id
                )
                return func.HttpResponse(
                    json.dumps({
                        "status": "BLOCKED",
                        "exception_id": exception_id,
                        "trace_id": trace_id,
                        "message": f"Cannot reduce PO quantity below delivered amount ({current_delivered} sqm)"
                    }),
                    status_code=422,
                    mimetype="application/json"
                )
        
        # 4. Build update data (only provided fields)
        updates: Dict[str, Any] = {}
        old_values: Dict[str, Any] = {}
        new_values: Dict[str, Any] = {}
        
        if request.customer_lpo_ref is not None:
            old_values["customer_lpo_ref"] = existing_lpo.get(_get_physical_column_name("LPO_MASTER", "CUSTOMER_LPO_REF"))
            new_values["customer_lpo_ref"] = request.customer_lpo_ref
            updates[Column.LPO_MASTER.CUSTOMER_LPO_REF] = request.customer_lpo_ref
        
        if request.customer_name is not None:
            old_values["customer_name"] = existing_lpo.get(_get_physical_column_name("LPO_MASTER", "CUSTOMER_NAME"))
            new_values["customer_name"] = request.customer_name
            updates[Column.LPO_MASTER.CUSTOMER_NAME] = request.customer_name
        
        if request.project_name is not None:
            old_values["project_name"] = existing_lpo.get(_get_physical_column_name("LPO_MASTER", "PROJECT_NAME"))
            new_values["project_name"] = request.project_name
            updates[Column.LPO_MASTER.PROJECT_NAME] = request.project_name
        
        if request.po_quantity_sqm is not None:
            old_values["po_quantity_sqm"] = existing_lpo.get(po_qty_col)
            new_values["po_quantity_sqm"] = request.po_quantity_sqm
            updates[Column.LPO_MASTER.PO_QUANTITY_SQM] = request.po_quantity_sqm
            
            # Recalculate PO Value and Balance
            price = parse_float_safe(existing_lpo.get(_get_physical_column_name("LPO_MASTER", "PRICE_PER_SQM")), 0)
            if request.price_per_sqm is not None:
                price = request.price_per_sqm
            updates[Column.LPO_MASTER.PO_VALUE] = request.po_quantity_sqm * price
            delivered = parse_float_safe(existing_lpo.get(delivered_qty_col), 0)
            updates[Column.LPO_MASTER.PO_BALANCE_QUANTITY] = request.po_quantity_sqm - delivered
        
        if request.price_per_sqm is not None:
            old_values["price_per_sqm"] = existing_lpo.get(_get_physical_column_name("LPO_MASTER", "PRICE_PER_SQM"))
            new_values["price_per_sqm"] = request.price_per_sqm
            updates[Column.LPO_MASTER.PRICE_PER_SQM] = request.price_per_sqm
            
            # Recalculate PO Value
            qty = parse_float_safe(existing_lpo.get(po_qty_col), 0)
            if request.po_quantity_sqm is not None:
                qty = request.po_quantity_sqm
            updates[Column.LPO_MASTER.PO_VALUE] = qty * request.price_per_sqm
        
        if request.terms_of_payment is not None:
            old_values["terms_of_payment"] = existing_lpo.get(_get_physical_column_name("LPO_MASTER", "TERMS_OF_PAYMENT"))
            new_values["terms_of_payment"] = request.terms_of_payment
            updates[Column.LPO_MASTER.TERMS_OF_PAYMENT] = request.terms_of_payment
        
        if request.wastage_pct is not None:
            old_values["wastage_pct"] = existing_lpo.get(_get_physical_column_name("LPO_MASTER", "WASTAGE_CONSIDERED_IN_COSTING"))
            new_values["wastage_pct"] = request.wastage_pct
            updates[Column.LPO_MASTER.WASTAGE_CONSIDERED_IN_COSTING] = str(request.wastage_pct)
        
        if request.hold_reason is not None:
            old_values["hold_reason"] = existing_lpo.get(_get_physical_column_name("LPO_MASTER", "HOLD_REASON"))
            new_values["hold_reason"] = request.hold_reason
            updates[Column.LPO_MASTER.HOLD_REASON] = request.hold_reason
        
        if request.lpo_status is not None:
            old_values["lpo_status"] = existing_lpo.get(_get_physical_column_name("LPO_MASTER", "LPO_STATUS"))
            new_values["lpo_status"] = request.lpo_status
            updates[Column.LPO_MASTER.LPO_STATUS] = request.lpo_status
        
        if request.remarks is not None:
            old_values["remarks"] = existing_lpo.get(_get_physical_column_name("LPO_MASTER", "REMARKS"))
            new_values["remarks"] = request.remarks
            updates[Column.LPO_MASTER.REMARKS] = request.remarks
        
        # Always update timestamp
        updates[Column.LPO_MASTER.UPDATED_AT] = format_datetime_for_smartsheet(datetime.now())
        
        if not updates:
            return func.HttpResponse(
                json.dumps({
                    "status": "OK",
                    "sap_reference": request.sap_reference,
                    "trace_id": trace_id,
                    "message": "No changes to apply"
                }),
                status_code=200,
                mimetype="application/json"
            )
        
        # 5. Update the row
        client.update_row(Sheet.LPO_MASTER, row_id, updates)
        logger.info(f"[{trace_id}] LPO updated: {request.sap_reference}")
        
        # 6. Log user action with old/new values
        log_user_action(
            client=client,
            user_id=request.updated_by,
            action_type=ActionType.LPO_UPDATED,
            target_table=Sheet.LPO_MASTER,
            target_id=request.sap_reference,
            old_value=json.dumps(old_values) if old_values else None,
            new_value=json.dumps(new_values) if new_values else None,
            notes=f"Updated via API",
            trace_id=trace_id
        )
        
        # 7. Return success
        return func.HttpResponse(
            json.dumps({
                "status": "OK",
                "sap_reference": request.sap_reference,
                "trace_id": trace_id,
                "message": "LPO updated successfully",
                "changes": list(new_values.keys())
            }),
            status_code=200,
            mimetype="application/json"
        )
    
    except Exception as e:
        logger.exception(f"[{trace_id}] Unexpected error: {e}")
        return func.HttpResponse(
            json.dumps({
                "status": "ERROR",
                "message": f"Internal server error: {str(e)}",
                "trace_id": trace_id
            }),
            status_code=500,
            mimetype="application/json"
        )

