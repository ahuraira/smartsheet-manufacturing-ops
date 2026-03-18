"""
Queue-Based Distributed Lock
=============================

Provides distributed locking using Azure Queue Storage message leases.
This ensures only one process can modify a given allocation at a time.

Architecture:
- Each allocation gets a lock message in the 'allocation-locks' queue
- Message visibility timeout acts as the lock duration
- Lock is released by deleting the message
- If process crashes, lock auto-releases after timeout

Usage:
    from shared.queue_lock import acquire_allocation_lock, release_allocation_lock
    
    lock_handle = acquire_allocation_lock(
        allocation_ids=["A-123", "A-124"],
        timeout_ms=30000,
        trace_id=trace_id
    )
    
    if lock_handle.success:
        try:
            # Do work
            ...
        finally:
            release_allocation_lock(lock_handle)
    else:
        # Handle lock acquisition failure
        logger.error(f"Failed to acquire lock: {lock_handle.error_message}")
"""

import logging
import os
import time
from typing import List, Optional
from dataclasses import dataclass
from azure.storage.queue import QueueClient, QueueServiceClient
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError

logger = logging.getLogger(__name__)

# Queue configuration
LOCK_QUEUE_NAME = "allocation-locks"
DEFAULT_TIMEOUT_MS = 60000  # 60 seconds — allows for slow Smartsheet API calls
MAX_TIMEOUT_MS = 300000  # 5 minutes (Azure Queue max visibility timeout: 7 days, but we use shorter)


@dataclass
class LockHandle:
    """Handle for an acquired lock."""
    success: bool
    allocation_ids: List[str] = None
    message_ids: List[str] = None  # Queue message IDs
    pop_receipts: List[str] = None  # For message deletion
    queue_client: Optional[QueueClient] = None
    error_message: Optional[str] = None
    error_code: Optional[str] = None
    acquired_at: float = 0.0  # time.monotonic() when lock was acquired
    timeout_ms: int = DEFAULT_TIMEOUT_MS

    def __bool__(self):
        return self.success

    def is_likely_held(self) -> bool:
        """Check if the lock is likely still held (elapsed time < timeout).

        WARNING: This is a best-effort check. The actual lock is managed by
        Azure Queue visibility timeout, and clock drift or delays can cause
        the lock to expire before this method returns False.
        """
        if not self.success or self.acquired_at == 0.0:
            return False
        elapsed_ms = (time.monotonic() - self.acquired_at) * 1000
        return elapsed_ms < self.timeout_ms


def _get_queue_client() -> QueueClient:
    """Get Azure Queue client for allocation locks."""
    connection_string = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    
    if not connection_string:
        raise ValueError("AZURE_STORAGE_CONNECTION_STRING environment variable not set")
    
    service_client = QueueServiceClient.from_connection_string(connection_string)
    queue_client = service_client.get_queue_client(LOCK_QUEUE_NAME)
    
    # Ensure queue exists
    try:
        queue_client.create_queue()
        logger.info(f"Created queue: {LOCK_QUEUE_NAME}")
    except ResourceExistsError:
        pass  # Queue already exists
    
    return queue_client


def acquire_allocation_lock(
    allocation_ids: List[str],
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    trace_id: str = ""
) -> LockHandle:
    """
    Acquire distributed lock on allocation IDs.
    
    Uses Azure Queue Storage message visibility as lock mechanism:
    1. Send message with allocation_id as content
    2. Message becomes invisible (locked) for timeout duration
    3. Only holder can delete message (release lock)
    4. If process crashes, lock auto-releases after timeout
    
    Args:
        allocation_ids: List of allocation IDs to lock
        timeout_ms: Lock timeout in milliseconds (max 300000)
        trace_id: Trace ID for logging
        
    Returns:
        LockHandle with success status and lock details
    """
    if not allocation_ids:
        return LockHandle(
            success=False,
            error_code="NO_ALLOCATIONS",
            error_message="No allocation IDs provided"
        )
    
    if timeout_ms > MAX_TIMEOUT_MS:
        logger.warning(
            f"[{trace_id}] Requested timeout {timeout_ms}ms exceeds max {MAX_TIMEOUT_MS}ms, capping"
        )
        timeout_ms = MAX_TIMEOUT_MS
    
    try:
        queue_client = _get_queue_client()
        timeout_seconds = timeout_ms / 1000.0
        
        message_ids = []
        pop_receipts = []
        
        # Send lock message for each allocation
        for allocation_id in allocation_ids:
            # Message content is the allocation_id (for debugging/monitoring)
            message_content = f"{{\"allocation_id\":\"{allocation_id}\",\"trace_id\":\"{trace_id}\"}}"
            
            # Send message with visibility timeout (this "locks" it)
            response = queue_client.send_message(
                message_content,
                visibility_timeout=int(timeout_seconds)
            )
            
            message_ids.append(response.id)
            pop_receipts.append(response.pop_receipt)
            
            logger.info(
                f"[{trace_id}] Acquired lock for allocation {allocation_id} "
                f"(timeout={timeout_ms}ms, msg_id={response.id})"
            )
        
        return LockHandle(
            success=True,
            allocation_ids=allocation_ids,
            message_ids=message_ids,
            pop_receipts=pop_receipts,
            queue_client=queue_client,
            acquired_at=time.monotonic(),
            timeout_ms=timeout_ms
        )
        
    except Exception as e:
        logger.error(f"[{trace_id}] Failed to acquire lock: {e}")
        return LockHandle(
            success=False,
            allocation_ids=allocation_ids,
            error_code="LOCK_FAILED",
            error_message=str(e)
        )


def release_allocation_lock(lock_handle: LockHandle, trace_id: str = "") -> bool:
    """
    Release lock by deleting queue messages.
    
    Args:
        lock_handle: LockHandle from acquire_allocation_lock
        trace_id: Trace ID for logging
        
    Returns:
        True if all locks released successfully
    """
    if not lock_handle.success or not lock_handle.message_ids:
        logger.warning(f"[{trace_id}] Invalid lock handle, nothing to release")
        return False

    # Warn if lock may have already expired
    if not lock_handle.is_likely_held():
        elapsed_ms = (time.monotonic() - lock_handle.acquired_at) * 1000 if lock_handle.acquired_at else 0
        logger.warning(
            f"[{trace_id}] Lock may have expired before release — "
            f"elapsed {elapsed_ms:.0f}ms exceeds timeout {lock_handle.timeout_ms}ms. "
            f"Another process may have acquired these allocations."
        )

    success_count = 0
    
    for i, (msg_id, pop_receipt) in enumerate(zip(lock_handle.message_ids, lock_handle.pop_receipts)):
        try:
            lock_handle.queue_client.delete_message(msg_id, pop_receipt)
            success_count += 1
            logger.info(
                f"[{trace_id}] Released lock for allocation {lock_handle.allocation_ids[i]} "
                f"(msg_id={msg_id})"
            )
        except ResourceNotFoundError:
            # Message already deleted or expired (lock auto-released)
            logger.warning(
                f"[{trace_id}] Lock message {msg_id} not found (may have expired)"
            )
            success_count += 1  # Still counts as "released"
        except Exception as e:
            logger.error(
                f"[{trace_id}] Failed to release lock {msg_id}: {e}"
            )
    
    return success_count == len(lock_handle.message_ids)


class AllocationLock:
    """
    Context manager for allocation locks.

    WARNING: This lock relies on Azure Queue visibility timeout. The lock is
    NOT automatically renewed. If your critical section may exceed timeout_ms,
    increase the timeout. If the process crashes, the lock auto-releases after
    the timeout — this is crash-safe but means long-running work can lose
    exclusivity.

    Usage:
        with AllocationLock(["A-123", "A-124"], trace_id=trace_id) as lock:
            if not lock.success:
                raise LockError("Failed to acquire lock")
            # Do work
    """
    
    def __init__(self, allocation_ids: List[str], timeout_ms: int = DEFAULT_TIMEOUT_MS, trace_id: str = ""):
        self.allocation_ids = allocation_ids
        self.timeout_ms = timeout_ms
        self.trace_id = trace_id
        self.lock_handle: Optional[LockHandle] = None
    
    def __enter__(self) -> LockHandle:
        self.lock_handle = acquire_allocation_lock(
            self.allocation_ids,
            self.timeout_ms,
            self.trace_id
        )
        return self.lock_handle
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.lock_handle and self.lock_handle.success:
            release_allocation_lock(self.lock_handle, self.trace_id)
        return False  # Don't suppress exceptions
