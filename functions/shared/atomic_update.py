"""
Atomic Update Helper
====================

Provides safe read-modify-write operations for Smartsheet rows.

Smartsheet API does not support true atomic operations or optimistic locking.
However, it returns errorCode 4004 on save collisions. This module implements:

1. Read current value
2. Compute new value
3. Attempt update
4. On 4004 error: retry with fresh read (exponential backoff)

Usage:
    from shared.atomic_update import atomic_increment
    
    result = atomic_increment(
        client=client,
        sheet_ref=Sheet.LPO_MASTER,
        row_id=lpo_row_id,
        column_ref=Column.LPO_MASTER.ALLOCATED_QUANTITY,
        increment_by=consumed_area,
        trace_id=trace_id
    )
    
    if result.success:
        print(f"Updated: {result.old_value} -> {result.new_value}")
    else:
        print(f"Failed: {result.error_message}")
"""

import logging
import time
import random
from typing import Optional, Union
from dataclasses import dataclass

from .logical_names import Sheet, Column
from .manifest import get_manifest

logger = logging.getLogger(__name__)


# Retry configuration
MAX_RETRIES = 5
BASE_DELAY_MS = 100  # Starting delay
MAX_DELAY_MS = 3000  # Maximum delay
JITTER_MS = 50


@dataclass
class AtomicUpdateResult:
    """Result of an atomic update operation."""
    
    success: bool
    old_value: Optional[float] = None
    new_value: Optional[float] = None
    retries_used: int = 0
    error_message: Optional[str] = None
    error_code: Optional[str] = None
    
    def __bool__(self):
        return self.success


def atomic_increment(
    client,
    sheet_ref: Union[str, Sheet],
    row_id: int,
    column_ref: Union[str, Column],
    increment_by: float,
    trace_id: str = "",
    max_retries: int = MAX_RETRIES
) -> AtomicUpdateResult:
    """
    Atomically increment a numeric column value with retry on collision.
    
    Implements optimistic locking pattern:
    1. Read current value
    2. Compute new value
    3. Attempt update
    4. On collision (4004): retry with fresh read
    
    Args:
        client: SmartsheetClient instance
        sheet_ref: Sheet logical name or Sheet enum
        row_id: Row ID to update
        column_ref: Column logical name or Column enum
        increment_by: Amount to add (can be negative for decrement)
        trace_id: Trace ID for logging
        max_retries: Maximum retry attempts
        
    Returns:
        AtomicUpdateResult with success status and old/new values
    """
    sheet_name = sheet_ref.value if hasattr(sheet_ref, 'value') else sheet_ref
    column_name = column_ref.value if hasattr(column_ref, 'value') else column_ref
    
    # Get physical column name via manifest
    manifest = get_manifest()
    physical_col = manifest.get_column_name(sheet_name, column_name)
    
    if not physical_col:
        logger.error(f"[{trace_id}] Column {column_name} not found in manifest for {sheet_name}")
        return AtomicUpdateResult(
            success=False,
            error_code="COLUMN_NOT_FOUND",
            error_message=f"Column {column_name} not found in manifest"
        )
    
    last_error = None
    
    for attempt in range(max_retries):
        try:
            # 1. Fresh read of current value
            current_row = client.get_row(sheet_name, row_id)
            
            if not current_row:
                logger.error(f"[{trace_id}] Row {row_id} not found in {sheet_name}")
                return AtomicUpdateResult(
                    success=False,
                    error_code="ROW_NOT_FOUND",
                    error_message=f"Row {row_id} not found"
                )
            
            current_value = float(current_row.get(physical_col) or 0)
            new_value = current_value + increment_by
            
            # 2. Attempt update
            client.update_row(
                sheet_name,
                row_id,
                {column_ref: new_value}
            )
            
            # 3. Success!
            logger.info(
                f"[{trace_id}] Atomic increment {sheet_name}.{column_name}: "
                f"{current_value} -> {new_value} (attempt {attempt + 1})"
            )
            
            return AtomicUpdateResult(
                success=True,
                old_value=current_value,
                new_value=new_value,
                retries_used=attempt
            )
            
        except Exception as e:
            last_error = e
            error_str = str(e).lower()
            
            # Check if this is a collision error (4004)
            is_collision = "4004" in error_str or "collision" in error_str or "conflict" in error_str
            
            if is_collision and attempt < max_retries - 1:
                # Exponential backoff with jitter
                delay_ms = min(
                    BASE_DELAY_MS * (2 ** attempt) + random.randint(0, JITTER_MS),
                    MAX_DELAY_MS
                )
                logger.warning(
                    f"[{trace_id}] Collision on {sheet_name}.{column_name}, "
                    f"retry {attempt + 1}/{max_retries} in {delay_ms}ms"
                )
                time.sleep(delay_ms / 1000.0)
                continue
            
            # Non-collision error or max retries exceeded
            logger.error(
                f"[{trace_id}] Atomic increment failed for {sheet_name}.{column_name}: {e}"
            )
            
            return AtomicUpdateResult(
                success=False,
                retries_used=attempt,
                error_code="COLLISION_MAX_RETRIES" if is_collision else "UPDATE_ERROR",
                error_message=str(e)
            )
    
    # Should not reach here, but safety fallback
    return AtomicUpdateResult(
        success=False,
        retries_used=max_retries,
        error_code="MAX_RETRIES_EXCEEDED",
        error_message=f"Max retries ({max_retries}) exceeded: {last_error}"
    )


def atomic_set_if_equals(
    client,
    sheet_ref: Union[str, Sheet],
    row_id: int,
    column_ref: Union[str, Column],
    expected_value: float,
    new_value: float,
    trace_id: str = ""
) -> AtomicUpdateResult:
    """
    Compare-and-swap: Set value only if current equals expected.
    
    Useful for status transitions or exclusive locks.
    
    Args:
        client: SmartsheetClient instance
        sheet_ref: Sheet logical name or Sheet enum
        row_id: Row ID to update
        column_ref: Column logical name or Column enum
        expected_value: Expected current value
        new_value: New value to set
        trace_id: Trace ID for logging
        
    Returns:
        AtomicUpdateResult with success status
    """
    sheet_name = sheet_ref.value if hasattr(sheet_ref, 'value') else sheet_ref
    column_name = column_ref.value if hasattr(column_ref, 'value') else column_ref
    
    manifest = get_manifest()
    physical_col = manifest.get_column_name(sheet_name, column_name)
    
    if not physical_col:
        return AtomicUpdateResult(
            success=False,
            error_code="COLUMN_NOT_FOUND",
            error_message=f"Column {column_name} not found"
        )
    
    try:
        current_row = client.get_row(sheet_name, row_id)
        
        if not current_row:
            return AtomicUpdateResult(
                success=False,
                error_code="ROW_NOT_FOUND",
                error_message=f"Row {row_id} not found"
            )
        
        current_value = float(current_row.get(physical_col) or 0)
        
        if current_value != expected_value:
            logger.warning(
                f"[{trace_id}] CAS failed: {column_name} is {current_value}, "
                f"expected {expected_value}"
            )
            return AtomicUpdateResult(
                success=False,
                old_value=current_value,
                error_code="VALUE_CHANGED",
                error_message=f"Current value {current_value} != expected {expected_value}"
            )
        
        client.update_row(sheet_name, row_id, {column_ref: new_value})
        
        logger.info(
            f"[{trace_id}] CAS success: {column_name} {current_value} -> {new_value}"
        )
        
        return AtomicUpdateResult(
            success=True,
            old_value=current_value,
            new_value=new_value
        )
        
    except Exception as e:
        logger.error(f"[{trace_id}] CAS failed: {e}")
        return AtomicUpdateResult(
            success=False,
            error_code="UPDATE_ERROR",
            error_message=str(e)
        )
