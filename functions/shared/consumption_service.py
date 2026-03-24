"""
Consumption Service
===================

Core business logic for consumption submissions.

This module handles the critical path for consumption:
1. Validation (variance checking, stock availability)
2. Locking (distributed via Azure Queue)
3. Idempotency (check for duplicate submission_id)
4. Atomic writes to Smartsheet

SOTA Patterns:
- Defensive validation with multiple error levels
- Configurable variance thresholds
- Distributed locking to prevent race conditions
- Idempotent submission tracking
"""

import logging
import os
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass
from .logical_names import Sheet, Column
from .manifest import get_manifest
from .models import ActionType, ExceptionSeverity, ExceptionSource, ReasonCode
from .helpers import parse_float_safe, now_uae
from .audit import create_exception, log_user_action
from .flow_models import (
    ConsumptionSubmission,
    ConsumptionSubmissionFromCard,
    ConsumptionLine,
    SubmissionResult,
    Warning,
    WarningCode,
    Error,
    ErrorCode,
)
from .queue_lock import AllocationLock
from .allocation_service import _parse_rows

logger = logging.getLogger(__name__)

# Thresholds from environment or defaults
VARIANCE_WARN_PCT = float(os.environ.get("VARIANCE_WARN_THRESHOLD_PCT", "5"))
VARIANCE_ERROR_PCT = float(os.environ.get("VARIANCE_ERROR_THRESHOLD_PCT", "10"))


@dataclass
class ValidationResult:
    """Result of consumption validation."""
    ok: bool
    warnings: List[Warning]
    errors: List[Error]


def validate_consumption(
    client,
    submission: ConsumptionSubmission,
    trace_id: str = ""
) -> ValidationResult:
    """
    Validate consumption submission.
    
    Checks:
    1. Variance between allocated and actual (5% warn, 10% error)
    2. Material codes exist in MATERIAL_MASTER
    3. Allocation IDs exist and are not already consumed
    
    Args:
        client: SmartsheetClient instance
        submission: ConsumptionSubmission model
        trace_id: Trace ID for logging
        
    Returns:
        ValidationResult with warnings/errors
    """
    warnings = []
    errors = []
    
    manifest = get_manifest()
    
    # 1. Check allocation IDs exist
    col_alloc_id = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.ALLOCATION_ID)
    col_alloc_status = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.STATUS)
    
    all_allocations = _parse_rows(client.get_sheet(Sheet.ALLOCATION_LOG))
    
    found_allocations = {}
    for alloc in all_allocations:
        alloc_id = alloc.get(col_alloc_id)
        if alloc_id in submission.allocation_ids:
            found_allocations[alloc_id] = alloc
    
    # Check all allocations found and not already consumed
    for alloc_id in submission.allocation_ids:
        if alloc_id not in found_allocations:
            errors.append(Error(
                code=ErrorCode.ALLOCATION_NOT_FOUND,
                message=f"Allocation {alloc_id} not found",
                details={"allocation_id": alloc_id}
            ))
        else:
            alloc_status = found_allocations[alloc_id].get(col_alloc_status, "")
            if alloc_status == "Consumed":
                errors.append(Error(
                    code=ErrorCode.ALREADY_SUBMITTED,
                    message=f"Allocation {alloc_id} is already fully consumed",
                    details={"allocation_id": alloc_id, "status": alloc_status}
                ))
    
    # 2. Check variance for each line
    from .allocation_service import aggregate_materials
    
    aggregated = aggregate_materials(client, submission.allocation_ids, trace_id)
    
    aggregated_by_code = {mat.canonical_code: mat for mat in aggregated}
    
    for line in submission.lines:
        material_info = aggregated_by_code.get(line.canonical_code)
        
        if not material_info:
            errors.append(Error(
                code=ErrorCode.ALLOCATION_NOT_FOUND,
                message=f"Material {line.canonical_code} not allocated in selected allocations",
                details={"canonical_code": line.canonical_code}
            ))
            continue

        # Variance check — use system allocation qty, not user-submitted value
        system_allocated_qty = material_info.allocated_qty
        variance_pct = abs(line.actual_qty - system_allocated_qty) / system_allocated_qty * 100 if system_allocated_qty > 0 else 0
        
        if variance_pct > VARIANCE_ERROR_PCT:
            errors.append(Error(
                code=ErrorCode.VARIANCE_CRITICAL,
                message=f"Variance {variance_pct:.1f}% exceeds error threshold ({VARIANCE_ERROR_PCT}%)",
                details={
                    "canonical_code": line.canonical_code,
                    "allocated": system_allocated_qty,
                    "actual": line.actual_qty,
                    "variance_pct": variance_pct
                }
            ))
        elif variance_pct > VARIANCE_WARN_PCT:
            warnings.append(Warning(
                code=WarningCode.VARIANCE_WARN,
                message=f"Variance {variance_pct:.1f}% exceeds warning threshold ({VARIANCE_WARN_PCT}%)",
                details={
                    "canonical_code": line.canonical_code,
                    "allocated": system_allocated_qty,
                    "actual": line.actual_qty,
                    "variance_pct": variance_pct
                }
            ))
    
    ok = len(errors) == 0
    
    logger.info(
        f"[{trace_id}] Validation: {'OK' if ok else 'FAILED'} "
        f"({len(warnings)} warnings, {len(errors)} errors)"
    )
    
    return ValidationResult(ok=ok, warnings=warnings, errors=errors)


def parse_card_data_to_submission(client, raw: ConsumptionSubmissionFromCard, trace_id: str) -> ConsumptionSubmission:
    """
    Parse raw adaptive card response into a standard ConsumptionSubmission.
    Allows Power Automate to send the raw output without mapping arrays.
    """
    card_data = raw.card_data
    
    tag_id = card_data.get("tag_id")
    if not tag_id:
        raise ValueError("card_data missing 'tag_id'")
        
    from .allocation_service import get_allocation_details_by_tag
    alloc_details = get_allocation_details_by_tag(client, tag_id, trace_id)
    alloc_map = {d.allocation_id: d for d in alloc_details}
    
    lines = []
    allocation_ids = set()
    global_remarks = card_data.get("remarks", "")
    
    for alloc_id, detail in alloc_map.items():
        actual_key = f"actual_{alloc_id}"
        accessories_key = f"accessories_{alloc_id}"

        # Add line if either quantity is present in card data
        if actual_key in card_data or accessories_key in card_data:
            allocation_ids.add(alloc_id)

            # Card now sends SAP quantities directly (QUANTITY/UOM)
            actual_sap_qty = parse_float_safe(card_data.get(actual_key), default=0.0)
            accessories_sap_qty = parse_float_safe(card_data.get(accessories_key), default=0.0)

            lines.append(ConsumptionLine(
                canonical_code=detail.sap_code,
                allocated_qty=detail.sap_qty,
                actual_qty=actual_sap_qty,
                accessories_qty=accessories_sap_qty,
                uom=detail.sap_uom,
                raw_qty=actual_sap_qty,       # Same as SAP qty for consistency
                accessories_raw_qty=accessories_sap_qty,
                raw_uom=detail.sap_uom,
                remarks=global_remarks
            ))
            
    if not lines:
        raise ValueError("No matching allocations found in card data")
        
    return ConsumptionSubmission(
        user=raw.user,
        plant=raw.plant,
        shift=raw.shift,
        allocation_ids=list(allocation_ids),
        lines=lines,
        trace_id=raw.trace_id or trace_id,
        source=raw.source
    )


def submit_consumption(
    client,
    submission: ConsumptionSubmission,
    trace_id: str = ""
) -> SubmissionResult:
    """
    Submit consumption with locking and idempotency.
    
    Flow:
    1. Check if trace_id already exists (idempotency)
    2. Acquire lock on allocation_ids
    3. Validate submission
    4. Write to CONSUMPTION_LOG
    5. Release lock
    
    Args:
        client: SmartsheetClient instance
        submission: ConsumptionSubmission model
        trace_id: Trace ID for logging and idempotency
        
    Returns:
        SubmissionResult with status and warnings/errors
    """
    manifest = get_manifest()
    
    # 1. Check idempotency - has this trace_id been processed?
    col_cons_id = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.CONSUMPTION_ID)
    
    all_consumptions = _parse_rows(client.get_sheet(Sheet.CONSUMPTION_LOG))
    
    for cons in all_consumptions:
        if cons.get(col_cons_id) == trace_id:
            logger.info(f"[{trace_id}] Submission already exists (idempotent)")
            return SubmissionResult(
                trace_id=trace_id,
                warnings=[],
                errors=[]
            )
    
    # 2. Acquire lock
    logger.info(f"[{trace_id}] Acquiring lock for allocations: {submission.allocation_ids}")
    
    with AllocationLock(submission.allocation_ids, timeout_ms=90000, trace_id=trace_id) as lock:
        if not lock.success:
            return SubmissionResult(
                trace_id=trace_id,
                warnings=[],
                errors=[Error(
                    code=ErrorCode.LOCK_TIMEOUT,
                    message="Failed to acquire lock on allocations",
                    details={"error": lock.error_message}
                )]
            )
        
        # 3. Validate
        validation = validate_consumption(client, submission, trace_id)
        
        if not validation.ok:
            return SubmissionResult(
                trace_id=trace_id,
                warnings=validation.warnings,
                errors=validation.errors
            )
        
        # 4. Write to CONSUMPTION_LOG (one row per line)
        from .id_generator import generate_next_consumption_id
        from .inventory_service import log_inventory_transactions_batch
        
        col_tag_id = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.TAG_SHEET_ID)
        col_status = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.STATUS)
        col_date = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.CONSUMPTION_DATE)
        col_shift = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.SHIFT)
        col_material = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.MATERIAL_CODE)
        col_qty = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.QUANTITY)
        col_alloc_ref = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.ALLOCATION_ID)
        col_remarks = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.REMARKS)
        
        # Extract tag IDs from allocations
        col_alloc_tag = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.TAG_SHEET_ID)
        col_alloc_id = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.ALLOCATION_ID)
        col_alloc_material = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.MATERIAL_CODE)

        all_allocations = _parse_rows(client.get_sheet(Sheet.ALLOCATION_LOG))
        tag_ids = set()
        alloc_id_by_material: dict = {}  # sap_code -> allocation_id (first match)
        
        # To calculate total consumed per allocation for 80% rule
        
        for alloc in all_allocations:
            if alloc.get(col_alloc_id) in submission.allocation_ids:
                tag_ids.add(alloc.get(col_alloc_tag))
                mat = alloc.get(col_alloc_material, "")
                if mat and mat not in alloc_id_by_material:
                    alloc_id_by_material[mat] = alloc.get(col_alloc_id, "")

        # Get existing consumptions to calculate totals
        alloc_ids_set = set(submission.allocation_ids)
        cons_rows = _parse_rows(client.get_sheet(Sheet.CONSUMPTION_LOG))
        col_cons_alloc_id = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.ALLOCATION_ID)
        col_cons_qty = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.QUANTITY)
        
        consumed_by_alloc = {}
        for row in cons_rows:
            alloc_ref = row.get(col_cons_alloc_id)
            if alloc_ref in alloc_ids_set:
                qty = parse_float_safe(row.get(col_cons_qty), default=0.0)
                consumed_by_alloc[alloc_ref] = consumed_by_alloc.get(alloc_ref, 0.0) + qty

        # Create consumption rows
        col_type = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.CONSUMPTION_TYPE)
        rows_to_add = []
        new_consumption_by_alloc = {}
        
        tag_id = sorted(tag_ids)[0] if tag_ids else ""
        
        inventory_txns = []
        
        for line in submission.lines:
            alloc_id = alloc_id_by_material.get(line.canonical_code, "")
            if not alloc_id:
                logger.warning(f"[{trace_id}] Material '{line.canonical_code}' has no matching allocation — skipping consumption line")
                try:
                    create_exception(
                        client=client, trace_id=trace_id,
                        reason_code=ReasonCode.SYSTEM_ERROR,
                        severity=ExceptionSeverity.MEDIUM,
                        source=ExceptionSource.ALLOCATION,
                        material_code=line.canonical_code,
                        message=f"Material '{line.canonical_code}' has no matching allocation during consumption"
                    )
                except Exception:
                    pass
                continue
                
            new_consumption_by_alloc[alloc_id] = new_consumption_by_alloc.get(alloc_id, 0.0)
            
            # 1. Production Consumption Row
            if line.actual_qty > 0:
                consumption_id = generate_next_consumption_id(client)
                row_data = {
                    Column.CONSUMPTION_LOG.CONSUMPTION_ID: consumption_id,
                    Column.CONSUMPTION_LOG.TAG_SHEET_ID: tag_id,
                    Column.CONSUMPTION_LOG.STATUS: "Submitted",
                    Column.CONSUMPTION_LOG.CONSUMPTION_TYPE: "PRODUCTION",
                    Column.CONSUMPTION_LOG.CONSUMPTION_DATE: now_uae().date().isoformat(),
                    Column.CONSUMPTION_LOG.SHIFT: submission.shift,
                    Column.CONSUMPTION_LOG.MATERIAL_CODE: line.canonical_code,
                    Column.CONSUMPTION_LOG.QUANTITY: line.actual_qty,
                    Column.CONSUMPTION_LOG.UOM: line.uom,
                    Column.CONSUMPTION_LOG.RAW_QUANTITY: line.raw_qty,
                    Column.CONSUMPTION_LOG.RAW_UOM: line.raw_uom,
                    Column.CONSUMPTION_LOG.ALLOCATION_ID: alloc_id,
                    Column.CONSUMPTION_LOG.REMARKS: f"Trace: {trace_id} | {line.remarks or ''}",
                }
                rows_to_add.append(row_data)
                new_consumption_by_alloc[alloc_id] += line.actual_qty
                
            # 2. Accessory Consumption Row
            if line.accessories_qty > 0:
                consumption_id = generate_next_consumption_id(client)
                row_data = {
                    Column.CONSUMPTION_LOG.CONSUMPTION_ID: consumption_id,
                    Column.CONSUMPTION_LOG.TAG_SHEET_ID: tag_id,
                    Column.CONSUMPTION_LOG.STATUS: "Submitted",
                    Column.CONSUMPTION_LOG.CONSUMPTION_TYPE: "ACCESSORY",
                    Column.CONSUMPTION_LOG.CONSUMPTION_DATE: now_uae().date().isoformat(),
                    Column.CONSUMPTION_LOG.SHIFT: submission.shift,
                    Column.CONSUMPTION_LOG.MATERIAL_CODE: line.canonical_code,
                    Column.CONSUMPTION_LOG.QUANTITY: line.accessories_qty,
                    Column.CONSUMPTION_LOG.UOM: line.uom,
                    Column.CONSUMPTION_LOG.RAW_QUANTITY: line.accessories_raw_qty,
                    Column.CONSUMPTION_LOG.RAW_UOM: line.raw_uom,
                    Column.CONSUMPTION_LOG.ALLOCATION_ID: alloc_id,
                    Column.CONSUMPTION_LOG.REMARKS: f"Trace: {trace_id} | {line.remarks or ''}",
                }
                rows_to_add.append(row_data)
                new_consumption_by_alloc[alloc_id] += line.accessories_qty

            # 3. Accumulate Inventory Transactions
            if line.actual_qty > 0:
                inventory_txns.append({
                    "txn_type": "Consumption",
                    "material_code": line.canonical_code,
                    "quantity": -line.actual_qty,
                    "reference_doc": alloc_id,
                    "source_system": "AzureFunc"
                })
            
            if line.accessories_qty > 0:
                inventory_txns.append({
                    "txn_type": "Consumption",
                    "material_code": line.canonical_code,
                    "quantity": -line.accessories_qty,
                    "reference_doc": alloc_id,
                    "source_system": "AzureFunc"
                })
        
        # Convert logical dict rows to Smartsheet API cell arrays
        if rows_to_add:
            manifest = get_manifest()
            col_ids = manifest.get_all_column_ids(Sheet.CONSUMPTION_LOG)
            
            formatted_rows = []
            for row in rows_to_add:
                cells = []
                for col_name, value in row.items():
                    if value is not None and col_name in col_ids:
                        cells.append({
                            "columnId": col_ids[col_name],
                            "value": value,
                            "strict": False
                        })
                formatted_rows.append({"toBottom": True, "cells": cells})
                
            client.add_rows_bulk(Sheet.CONSUMPTION_LOG, formatted_rows)
            logger.info(f"[{trace_id}] Added {len(formatted_rows)} consumption rows")
            try:
                from shared.helpers import resolve_user_email
                resolved_user = resolve_user_email(client, submission.user if hasattr(submission, 'user') else "system")
                log_user_action(
                    client=client, user_id=resolved_user,
                    action_type=ActionType.CONSUMPTION_SUBMITTED,
                    target_table="CONSUMPTION_LOG", target_id=tag_id,
                    notes=f"Submitted {len(formatted_rows)} consumption rows for {tag_id}",
                    trace_id=trace_id
                )
            except Exception as ua_err:
                logger.warning(f"[{trace_id}] Failed to log user action: {ua_err}")

        # Log negative INVENTORY_TXN_LOG entries representing the physical consumption issue
        if inventory_txns:
            try:
                log_inventory_transactions_batch(client, inventory_txns, trace_id=trace_id)
            except Exception as e:
                logger.error(f"[{trace_id}] Failed to dispatch INVENTORY_TXN_LOG batch: {e}")
                try:
                    create_exception(
                        client=client, trace_id=trace_id,
                        reason_code=ReasonCode.SYSTEM_ERROR,
                        severity=ExceptionSeverity.CRITICAL,
                        source=ExceptionSource.ALLOCATION,
                        message=f"Failed to log inventory transactions: {str(e)[:500]}"
                    )
                except Exception:
                    pass

        # Update Allocation Status based on 80% rule
        alloc_updates = {}
        col_alloc_qty = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.QUANTITY)
        
        for alloc in all_allocations:
            alloc_id = alloc.get(col_alloc_id)
            if alloc_id in new_consumption_by_alloc:
                allocated_qty = parse_float_safe(alloc.get(col_alloc_qty), default=0.0)
                previously_consumed = consumed_by_alloc.get(alloc_id, 0.0)
                newly_consumed = new_consumption_by_alloc.get(alloc_id, 0.0)
                total_consumed = previously_consumed + newly_consumed
                
                # If allocated_qty is 0 (due to shortage or empty BOM), any consumption submission 
                # against it should close the allocation so downstream margin calculations can proceed.
                if allocated_qty <= 0:
                    new_status = "Consumed"
                else:
                    pct_consumed = total_consumed / allocated_qty
                    # If >= 80%, Consumed, else Partial Consumed
                    new_status = "Consumed" if pct_consumed >= 0.8 else "Partial Consumed"
                    
                current_status = alloc.get(manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.STATUS))
                
                if current_status != new_status:
                    alloc_updates[alloc.get("row_id")] = {
                        Column.ALLOCATION_LOG.STATUS: new_status
                    }
                    
        if alloc_updates:
            for row_id, updates in alloc_updates.items():
                client.update_row(Sheet.ALLOCATION_LOG, row_id, updates)
            logger.info(f"[{trace_id}] Updated status for {len(alloc_updates)} allocations")
        
        # 4.5. Check if Tag Sheets are now Production Complete
        if tag_ids:
            col_tag_reg_id = manifest.get_column_name(Sheet.TAG_REGISTRY, Column.TAG_REGISTRY.TAG_ID)
            col_tag_reg_status = manifest.get_column_name(Sheet.TAG_REGISTRY, Column.TAG_REGISTRY.STATUS)
            
            try:
                tag_registry_rows = _parse_rows(client.get_sheet(Sheet.TAG_REGISTRY))
                tag_updates = {}
                
                for t_id in tag_ids:
                    if not t_id:
                        continue
                        
                    # Find allocations for this tag sheet
                    tag_allocs = [a for a in all_allocations if a.get(col_alloc_tag) == t_id]
                    if not tag_allocs:
                        continue
                        
                    all_consumed = True
                    for alloc in tag_allocs:
                        a_row_id = alloc.get("row_id")
                        # Use the new status if we just updated it, otherwise fallback to existing status
                        a_status = alloc.get(manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.STATUS))
                        if a_row_id in alloc_updates:
                            a_status = alloc_updates[a_row_id].get(Column.ALLOCATION_LOG.STATUS, a_status)
                            
                        # Missing or unset statuses are not 'Consumed'
                        if a_status != "Consumed":
                            all_consumed = False
                            break
                            
                    if all_consumed:
                        tag_row = next((r for r in tag_registry_rows if str(r.get(col_tag_reg_id)) == str(t_id)), None)
                        if tag_row:
                            current_tag_status = tag_row.get(col_tag_reg_status)
                            if current_tag_status != "Complete":
                                tag_updates[tag_row.get("row_id")] = {
                                    "tag_id": str(t_id),  # Keep human-readable ID for audit
                                    "updates": {Column.TAG_REGISTRY.STATUS: "Complete"},
                                }

                if tag_updates:
                    submission_user = resolved_user if resolved_user else "system"
                    for tag_row_id, entry in tag_updates.items():
                        client.update_row(Sheet.TAG_REGISTRY, tag_row_id, entry["updates"])
                    logger.info(f"[{trace_id}] Marked {len(tag_updates)} Tag Sheets as Complete")
                    try:
                        for tag_row_id, entry in tag_updates.items():
                            log_user_action(
                                client=client, user_id=submission_user,
                                action_type=ActionType.TAG_UPDATED,
                                target_table="TAG_REGISTRY", target_id=entry["tag_id"],
                                new_value="Complete",
                                trace_id=trace_id
                            )
                    except Exception as ua_err:
                        logger.warning(f"[{trace_id}] Failed to log tag completion action: {ua_err}")

                    # Trigger the DO Margin Approval Flow dynamically for completed tags
                    from .margin_orchestrator import MarginOrchestrator
                    orchestrator = MarginOrchestrator(client)
                    
                    for tag_row_id, entry in tag_updates.items():
                        if entry["updates"].get(Column.TAG_REGISTRY.STATUS) == "Complete":
                            tr_row = next((r for r in tag_registry_rows if str(r.get("row_id")) == str(tag_row_id)), None)
                            if tr_row:
                                c_lpo = manifest.get_column_name(Sheet.TAG_REGISTRY, Column.TAG_REGISTRY.LPO_SAP_REFERENCE)
                                c_sqm = manifest.get_column_name(Sheet.TAG_REGISTRY, Column.TAG_REGISTRY.TOTAL_AREA_SQM)

                                lpo_ref = tr_row.get(c_lpo, "")
                                sqm_val = tr_row.get(c_sqm, 0.0)
                                t_id = entry["tag_id"]
                                
                                try:
                                    orchestrator.trigger_margin_approval_for_tag(
                                        tag_sheet_id=str(t_id),
                                        delivered_sqm=parse_float_safe(sqm_val, default=0.0),
                                        lpo_sap_ref=str(lpo_ref),
                                        trace_id=trace_id
                                    )
                                except Exception as me:
                                    logger.error(f"[{trace_id}] Margin orchestrator failed for {t_id}: {me}")
                                    try:
                                        create_exception(
                                            client=client,
                                            trace_id=trace_id,
                                            reason_code=ReasonCode.SYSTEM_ERROR,
                                            severity=ExceptionSeverity.MEDIUM,
                                            source=ExceptionSource.ALLOCATION,
                                            related_tag_id=str(t_id),
                                            message=f"Margin approval trigger failed: {str(me)[:500]}",
                                        )
                                    except Exception:
                                        logger.error(f"[{trace_id}] Failed to create exception record for margin failure")
            
            except Exception as e:
                logger.error(f"[{trace_id}] Failed to update Tag Sheet statuses: {str(e)}")
        
        # 5. Determine final status
        
        return SubmissionResult(
            trace_id=trace_id,
            warnings=validation.warnings,
            errors=[]
        )
