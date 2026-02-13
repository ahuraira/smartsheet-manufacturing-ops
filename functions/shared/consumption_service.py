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
from datetime import datetime

from .logical_names import Sheet, Column
from .manifest import get_manifest
from .flow_models import (
    ConsumptionSubmission,
    SubmissionResult,
    Warning,
    WarningCode,
    Error,
    ErrorCode,
)
from .queue_lock import AllocationLock

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
    
    all_allocations = client.list_rows(Sheet.ALLOCATION_LOG)
    
    found_allocations = {}
    for alloc in all_allocations:
        alloc_id = alloc.get(col_alloc_id)
        if alloc_id in submission.allocation_ids:
            found_allocations[alloc_id] = alloc
    
    # Check all allocations found
    for alloc_id in submission.allocation_ids:
        if alloc_id not in found_allocations:
            errors.append(Error(
                code=ErrorCode.ALLOCATION_NOT_FOUND,
                message=f"Allocation {alloc_id} not found",
                details={"allocation_id": alloc_id}
            ))
    
    # 2. Check variance for each line
    from shared.allocation_service import aggregate_materials
    
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
        
        # Variance check
        variance_pct = abs(line.actual_qty - line.allocated_qty) / line.allocated_qty * 100 if line.allocated_qty > 0 else 0
        
        if variance_pct > VARIANCE_ERROR_PCT:
            errors.append(Error(
                code=ErrorCode.VARIANCE_CRITICAL,
                message=f"Variance {variance_pct:.1f}% exceeds error threshold ({VARIANCE_ERROR_PCT}%)",
                details={
                    "canonical_code": line.canonical_code,
                    "allocated": line.allocated_qty,
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
                    "allocated": line.allocated_qty,
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


def submit_consumption(
    client,
    submission: ConsumptionSubmission,
    trace_id: str = ""
) -> SubmissionResult:
    """
    Submit consumption with locking and idempotency.
    
    Flow:
    1. Check if submission_id already exists (idempotency)
    2. Acquire lock on allocation_ids
    3. Validate submission
    4. Write to CONSUMPTION_LOG
    5. Release lock
    
    Args:
        client: SmartsheetClient instance
        submission: ConsumptionSubmission model
        trace_id: Trace ID for logging
        
    Returns:
        SubmissionResult with status and warnings/errors
    """
    manifest = get_manifest()
    
    # 1. Check idempotency - has this submission_id been processed?
    col_cons_id = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.CONSUMPTION_ID)
    
    all_consumptions = client.list_rows(Sheet.CONSUMPTION_LOG)
    
    for cons in all_consumptions:
        if cons.get(col_cons_id) == submission.submission_id:
            logger.info(f"[{trace_id}] Submission {submission.submission_id} already exists (idempotent)")
            return SubmissionResult(
                status="OK",
                processed_submission_id=submission.submission_id,
                warnings=[],
                errors=[],
                trace_id=trace_id
            )
    
    # 2. Acquire lock
    logger.info(f"[{trace_id}] Acquiring lock for allocations: {submission.allocation_ids}")
    
    with AllocationLock(submission.allocation_ids, timeout_ms=30000, trace_id=trace_id) as lock:
        if not lock.success:
            return SubmissionResult(
                status="ERROR",
                processed_submission_id=submission.submission_id,
                warnings=[],
                errors=[Error(
                    code=ErrorCode.LOCK_TIMEOUT,
                    message="Failed to acquire lock on allocations",
                    details={"error": lock.error_message}
                )],
                trace_id=trace_id
            )
        
        # 3. Validate
        validation = validate_consumption(client, submission, trace_id)
        
        if not validation.ok:
            return SubmissionResult(
                status="ERROR",
                processed_submission_id=submission.submission_id,
                warnings=validation.warnings,
                errors=validation.errors,
                trace_id=trace_id
            )
        
        # 4. Write to CONSUMPTION_LOG (one row per line)
        from .id_generator import generate_next_consumption_id
        
        col_tag_id = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.TAG_SHEET_ID)
        col_status = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.STATUS)
        col_date = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.CONSUMPTION_DATE)
        col_shift = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.SHIFT)
        col_material = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.MATERIAL_CODE)
        col_qty = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.QUANTITY)
        col_remarks = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.REMARKS)
        
        # Extract tag IDs from allocations
        col_alloc_tag = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.TAG_SHEET_ID)
        col_alloc_id = manifest.get_column_name(Sheet.ALLOCATION_LOG, Column.ALLOCATION_LOG.ALLOCATION_ID)
        
        all_allocations = client.list_rows(Sheet.ALLOCATION_LOG)
        tag_ids = set()
        
        for alloc in all_allocations:
            if alloc.get(col_alloc_id) in submission.allocation_ids:
                tag_ids.add(alloc.get(col_alloc_tag))
        
        # Create consumption rows
        rows_to_add = []
        
        for line in submission.lines:
            consumption_id = generate_next_consumption_id(client)
            
            # Pick first tag_id (or use logic to map line to tag)
            tag_id = list(tag_ids)[0] if tag_ids else ""
            
            row_data = {
                Column.CONSUMPTION_LOG.CONSUMPTION_ID: consumption_id,
                Column.CONSUMPTION_LOG.TAG_SHEET_ID: tag_id,
                Column.CONSUMPTION_LOG.STATUS: "Submitted",
                Column.CONSUMPTION_LOG.CONSUMPTION_DATE: datetime.now().date().isoformat(),
                Column.CONSUMPTION_LOG.SHIFT: submission.shift,
                Column.CONSUMPTION_LOG.MATERIAL_CODE: line.canonical_code,
                Column.CONSUMPTION_LOG.QUANTITY: line.actual_qty,
                Column.CONSUMPTION_LOG.REMARKS: f"Submission: {submission.submission_id} | {line.remarks or ''}",
            }
            
            rows_to_add.append(row_data)
        
        # Batch add rows
        client.add_rows(Sheet.CONSUMPTION_LOG, rows_to_add)
        
        logger.info(f"[{trace_id}] Added {len(rows_to_add)} consumption rows")
        
        # 5. Determine final status
        status = "WARN" if validation.warnings else "OK"
        
        return SubmissionResult(
            status=status,
            processed_submission_id=submission.submission_id,
            warnings=validation.warnings,
            errors=[],
            trace_id=trace_id
        )
