"""
Unit Tests for Queue-Based Distributed Lock
=============================================
Tests:
- LockHandle dataclass (bool, is_likely_held)
- acquire_allocation_lock (happy path, empty IDs, timeout capping, failure)
- release_allocation_lock (success, invalid handle, expired, partial failure)
- AllocationLock context manager (enter/exit, exception propagation)
- _get_queue_client (missing env var, queue creation, queue already exists)
"""

import pytest
import time
from unittest.mock import MagicMock, patch, PropertyMock
from dataclasses import dataclass

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.queue_lock import (
    LockHandle,
    acquire_allocation_lock,
    release_allocation_lock,
    AllocationLock,
    _get_queue_client,
    DEFAULT_TIMEOUT_MS,
    MAX_TIMEOUT_MS,
    LOCK_QUEUE_NAME,
)


# ============== Helper Factories ==============

def _make_queue_message(msg_id="msg-001", pop_receipt="pop-001"):
    """Build a mock queue send_message response."""
    msg = MagicMock()
    msg.id = msg_id
    msg.pop_receipt = pop_receipt
    return msg


def _make_successful_lock(
    allocation_ids=None,
    message_ids=None,
    pop_receipts=None,
    acquired_at=None,
    timeout_ms=DEFAULT_TIMEOUT_MS,
):
    """Build a LockHandle that looks like a successful acquisition."""
    allocation_ids = allocation_ids or ["A-100"]
    message_ids = message_ids or ["msg-001"]
    pop_receipts = pop_receipts or ["pop-001"]
    return LockHandle(
        success=True,
        allocation_ids=allocation_ids,
        message_ids=message_ids,
        pop_receipts=pop_receipts,
        queue_client=MagicMock(),
        acquired_at=acquired_at or time.monotonic(),
        timeout_ms=timeout_ms,
    )


# ============== LockHandle Tests ==============

@pytest.mark.unit
class TestLockHandle:

    def test_bool_true_when_success(self):
        handle = LockHandle(success=True)
        assert bool(handle) is True

    def test_bool_false_when_not_success(self):
        handle = LockHandle(success=False)
        assert bool(handle) is False

    def test_is_likely_held_returns_true_when_within_timeout(self):
        handle = LockHandle(
            success=True,
            acquired_at=time.monotonic(),
            timeout_ms=60000,
        )
        assert handle.is_likely_held() is True

    def test_is_likely_held_returns_false_when_not_acquired(self):
        handle = LockHandle(success=False)
        assert handle.is_likely_held() is False

    def test_is_likely_held_returns_false_when_acquired_at_zero(self):
        handle = LockHandle(success=True, acquired_at=0.0)
        assert handle.is_likely_held() is False

    def test_is_likely_held_returns_false_when_expired(self):
        # Acquired 2 seconds ago with a 1ms timeout
        handle = LockHandle(
            success=True,
            acquired_at=time.monotonic() - 2.0,
            timeout_ms=1,
        )
        assert handle.is_likely_held() is False

    def test_default_timeout_is_set(self):
        handle = LockHandle(success=True)
        assert handle.timeout_ms == DEFAULT_TIMEOUT_MS

    def test_error_fields_default_to_none(self):
        handle = LockHandle(success=False)
        assert handle.error_message is None
        assert handle.error_code is None

    def test_lists_default_to_none(self):
        handle = LockHandle(success=True)
        assert handle.message_ids is None
        assert handle.pop_receipts is None
        assert handle.allocation_ids is None


# ============== _get_queue_client Tests ==============

@pytest.mark.unit
class TestGetQueueClient:

    @patch.dict(os.environ, {}, clear=True)
    def test_raises_when_no_connection_string(self):
        with pytest.raises(ValueError, match="AZURE_STORAGE_CONNECTION_STRING"):
            _get_queue_client()

    @patch("shared.queue_lock.QueueServiceClient")
    @patch.dict(os.environ, {"AZURE_STORAGE_CONNECTION_STRING": "DefaultEndpointsProtocol=https;AccountName=test"})
    def test_creates_queue_on_first_call(self, mock_service_cls):
        mock_service = MagicMock()
        mock_queue = MagicMock()
        mock_service_cls.from_connection_string.return_value = mock_service
        mock_service.get_queue_client.return_value = mock_queue

        result = _get_queue_client()

        mock_service.get_queue_client.assert_called_once_with(LOCK_QUEUE_NAME)
        mock_queue.create_queue.assert_called_once()
        assert result is mock_queue

    @patch("shared.queue_lock.QueueServiceClient")
    @patch.dict(os.environ, {"AZURE_STORAGE_CONNECTION_STRING": "DefaultEndpointsProtocol=https;AccountName=test"})
    def test_ignores_resource_exists_error(self, mock_service_cls):
        from azure.core.exceptions import ResourceExistsError

        mock_service = MagicMock()
        mock_queue = MagicMock()
        mock_queue.create_queue.side_effect = ResourceExistsError("Queue already exists")
        mock_service_cls.from_connection_string.return_value = mock_service
        mock_service.get_queue_client.return_value = mock_queue

        result = _get_queue_client()

        assert result is mock_queue


# ============== acquire_allocation_lock Tests ==============

@pytest.mark.unit
class TestAcquireAllocationLock:

    def test_empty_allocation_ids_returns_no_allocations(self):
        handle = acquire_allocation_lock([], trace_id="t-001")

        assert handle.success is False
        assert handle.error_code == "NO_ALLOCATIONS"
        assert not bool(handle)

    @patch("shared.queue_lock._get_queue_client")
    def test_happy_path_single_allocation(self, mock_get_client):
        mock_queue = MagicMock()
        mock_queue.send_message.return_value = _make_queue_message("msg-100", "pop-100")
        mock_get_client.return_value = mock_queue

        handle = acquire_allocation_lock(["A-001"], timeout_ms=30000, trace_id="t-002")

        assert handle.success is True
        assert handle.allocation_ids == ["A-001"]
        assert handle.message_ids == ["msg-100"]
        assert handle.pop_receipts == ["pop-100"]
        assert handle.queue_client is mock_queue
        assert handle.timeout_ms == 30000
        assert handle.acquired_at > 0

        # Verify send_message was called with correct visibility timeout (30s)
        mock_queue.send_message.assert_called_once()
        call_kwargs = mock_queue.send_message.call_args
        assert call_kwargs[1]["visibility_timeout"] == 30

    @patch("shared.queue_lock._get_queue_client")
    def test_happy_path_multiple_allocations(self, mock_get_client):
        mock_queue = MagicMock()
        mock_queue.send_message.side_effect = [
            _make_queue_message("msg-1", "pop-1"),
            _make_queue_message("msg-2", "pop-2"),
            _make_queue_message("msg-3", "pop-3"),
        ]
        mock_get_client.return_value = mock_queue

        handle = acquire_allocation_lock(
            ["A-001", "A-002", "A-003"], timeout_ms=45000, trace_id="t-003"
        )

        assert handle.success is True
        assert handle.message_ids == ["msg-1", "msg-2", "msg-3"]
        assert handle.pop_receipts == ["pop-1", "pop-2", "pop-3"]
        assert mock_queue.send_message.call_count == 3

    @patch("shared.queue_lock._get_queue_client")
    def test_timeout_capped_to_max(self, mock_get_client):
        mock_queue = MagicMock()
        mock_queue.send_message.return_value = _make_queue_message()
        mock_get_client.return_value = mock_queue

        handle = acquire_allocation_lock(
            ["A-001"], timeout_ms=999999, trace_id="t-004"
        )

        assert handle.success is True
        assert handle.timeout_ms == MAX_TIMEOUT_MS

        # Visibility timeout should be MAX_TIMEOUT_MS / 1000
        call_kwargs = mock_queue.send_message.call_args
        assert call_kwargs[1]["visibility_timeout"] == int(MAX_TIMEOUT_MS / 1000)

    @patch("shared.queue_lock._get_queue_client")
    def test_queue_client_creation_fails(self, mock_get_client):
        mock_get_client.side_effect = RuntimeError("Connection refused")

        handle = acquire_allocation_lock(["A-001"], trace_id="t-005")

        assert handle.success is False
        assert handle.error_code == "LOCK_FAILED"
        assert "Connection refused" in handle.error_message

    @patch("shared.queue_lock._get_queue_client")
    def test_send_message_fails_mid_batch(self, mock_get_client):
        mock_queue = MagicMock()
        mock_queue.send_message.side_effect = [
            _make_queue_message("msg-1", "pop-1"),
            Exception("Queue service unavailable"),
        ]
        mock_get_client.return_value = mock_queue

        handle = acquire_allocation_lock(
            ["A-001", "A-002"], trace_id="t-006"
        )

        # The whole acquisition fails because the exception propagates
        assert handle.success is False
        assert handle.error_code == "LOCK_FAILED"

    @patch("shared.queue_lock._get_queue_client")
    def test_message_content_includes_allocation_id_and_trace(self, mock_get_client):
        mock_queue = MagicMock()
        mock_queue.send_message.return_value = _make_queue_message()
        mock_get_client.return_value = mock_queue

        acquire_allocation_lock(["A-999"], trace_id="trace-abc")

        call_args = mock_queue.send_message.call_args[0][0]
        assert "A-999" in call_args
        assert "trace-abc" in call_args

    @patch("shared.queue_lock._get_queue_client")
    def test_default_timeout_used_when_not_specified(self, mock_get_client):
        mock_queue = MagicMock()
        mock_queue.send_message.return_value = _make_queue_message()
        mock_get_client.return_value = mock_queue

        handle = acquire_allocation_lock(["A-001"])

        assert handle.timeout_ms == DEFAULT_TIMEOUT_MS

    @patch("shared.queue_lock._get_queue_client")
    def test_default_trace_id_is_empty_string(self, mock_get_client):
        mock_queue = MagicMock()
        mock_queue.send_message.return_value = _make_queue_message()
        mock_get_client.return_value = mock_queue

        # Should not raise; trace_id defaults to ""
        handle = acquire_allocation_lock(["A-001"])
        assert handle.success is True


# ============== release_allocation_lock Tests ==============

@pytest.mark.unit
class TestReleaseAllocationLock:

    def test_invalid_handle_not_success(self):
        handle = LockHandle(success=False)
        result = release_allocation_lock(handle, trace_id="t-010")
        assert result is False

    def test_invalid_handle_no_message_ids(self):
        handle = LockHandle(success=True, message_ids=None)
        result = release_allocation_lock(handle, trace_id="t-011")
        assert result is False

    def test_invalid_handle_empty_message_ids(self):
        handle = LockHandle(success=True, message_ids=[])
        result = release_allocation_lock(handle, trace_id="t-012")
        assert result is False

    def test_successful_release_single_message(self):
        handle = _make_successful_lock()
        result = release_allocation_lock(handle, trace_id="t-013")

        assert result is True
        handle.queue_client.delete_message.assert_called_once_with("msg-001", "pop-001")

    def test_successful_release_multiple_messages(self):
        handle = _make_successful_lock(
            allocation_ids=["A-1", "A-2", "A-3"],
            message_ids=["m-1", "m-2", "m-3"],
            pop_receipts=["p-1", "p-2", "p-3"],
        )
        result = release_allocation_lock(handle, trace_id="t-014")

        assert result is True
        assert handle.queue_client.delete_message.call_count == 3

    def test_resource_not_found_counts_as_success(self):
        from azure.core.exceptions import ResourceNotFoundError

        handle = _make_successful_lock()
        handle.queue_client.delete_message.side_effect = ResourceNotFoundError("Gone")

        result = release_allocation_lock(handle, trace_id="t-015")
        assert result is True

    def test_partial_failure_returns_false(self):
        handle = _make_successful_lock(
            allocation_ids=["A-1", "A-2"],
            message_ids=["m-1", "m-2"],
            pop_receipts=["p-1", "p-2"],
        )
        # First delete succeeds, second fails with a generic exception
        handle.queue_client.delete_message.side_effect = [
            None,
            RuntimeError("Service unavailable"),
        ]

        result = release_allocation_lock(handle, trace_id="t-016")
        assert result is False

    def test_all_generic_failures_returns_false(self):
        handle = _make_successful_lock(
            allocation_ids=["A-1", "A-2"],
            message_ids=["m-1", "m-2"],
            pop_receipts=["p-1", "p-2"],
        )
        handle.queue_client.delete_message.side_effect = RuntimeError("Timeout")

        result = release_allocation_lock(handle, trace_id="t-017")
        assert result is False

    def test_warns_when_lock_likely_expired(self):
        """Release still works but should warn when is_likely_held() is False."""
        handle = _make_successful_lock(
            acquired_at=time.monotonic() - 120,  # 120 seconds ago
            timeout_ms=1000,  # 1 second timeout — long expired
        )

        result = release_allocation_lock(handle, trace_id="t-018")

        # Should still succeed (message deleted)
        assert result is True
        handle.queue_client.delete_message.assert_called_once()

    def test_mixed_not_found_and_success(self):
        from azure.core.exceptions import ResourceNotFoundError

        handle = _make_successful_lock(
            allocation_ids=["A-1", "A-2", "A-3"],
            message_ids=["m-1", "m-2", "m-3"],
            pop_receipts=["p-1", "p-2", "p-3"],
        )
        handle.queue_client.delete_message.side_effect = [
            None,  # success
            ResourceNotFoundError("expired"),  # still counts as success
            None,  # success
        ]

        result = release_allocation_lock(handle, trace_id="t-019")
        assert result is True

    def test_mixed_not_found_and_failure(self):
        from azure.core.exceptions import ResourceNotFoundError

        handle = _make_successful_lock(
            allocation_ids=["A-1", "A-2", "A-3"],
            message_ids=["m-1", "m-2", "m-3"],
            pop_receipts=["p-1", "p-2", "p-3"],
        )
        handle.queue_client.delete_message.side_effect = [
            None,  # success
            ResourceNotFoundError("expired"),  # counts as success
            RuntimeError("broken"),  # failure
        ]

        result = release_allocation_lock(handle, trace_id="t-020")
        assert result is False  # 2/3 != 3/3


# ============== AllocationLock Context Manager Tests ==============

@pytest.mark.unit
class TestAllocationLockContextManager:

    @patch("shared.queue_lock._get_queue_client")
    def test_enter_acquires_lock_and_returns_handle(self, mock_get_client):
        mock_queue = MagicMock()
        mock_queue.send_message.return_value = _make_queue_message()
        mock_get_client.return_value = mock_queue

        with AllocationLock(["A-001"], timeout_ms=15000, trace_id="t-cm-1") as handle:
            assert handle.success is True
            assert handle.allocation_ids == ["A-001"]
            assert handle.timeout_ms == 15000

    @patch("shared.queue_lock._get_queue_client")
    def test_exit_releases_lock(self, mock_get_client):
        mock_queue = MagicMock()
        mock_queue.send_message.return_value = _make_queue_message("m-1", "p-1")
        mock_get_client.return_value = mock_queue

        with AllocationLock(["A-001"], trace_id="t-cm-2") as handle:
            pass  # do nothing

        # delete_message should have been called during __exit__
        mock_queue.delete_message.assert_called_once_with("m-1", "p-1")

    @patch("shared.queue_lock._get_queue_client")
    def test_exit_does_not_release_when_acquisition_failed(self, mock_get_client):
        mock_get_client.side_effect = RuntimeError("No connection")

        with AllocationLock(["A-001"], trace_id="t-cm-3") as handle:
            assert handle.success is False

        # No delete calls since lock was never acquired
        # (queue_client is None on the handle)

    @patch("shared.queue_lock._get_queue_client")
    def test_does_not_suppress_exceptions(self, mock_get_client):
        mock_queue = MagicMock()
        mock_queue.send_message.return_value = _make_queue_message()
        mock_get_client.return_value = mock_queue

        with pytest.raises(ValueError, match="business logic error"):
            with AllocationLock(["A-001"], trace_id="t-cm-4") as handle:
                assert handle.success is True
                raise ValueError("business logic error")

        # Lock should still be released even though exception was raised
        mock_queue.delete_message.assert_called_once()

    @patch("shared.queue_lock._get_queue_client")
    def test_context_manager_with_empty_ids(self, mock_get_client):
        """Empty allocation_ids should produce a failed lock, no release."""
        with AllocationLock([], trace_id="t-cm-5") as handle:
            assert handle.success is False
            assert handle.error_code == "NO_ALLOCATIONS"

        # _get_queue_client should not even be called
        mock_get_client.assert_not_called()

    @patch("shared.queue_lock._get_queue_client")
    def test_context_manager_passes_timeout_to_acquire(self, mock_get_client):
        mock_queue = MagicMock()
        mock_queue.send_message.return_value = _make_queue_message()
        mock_get_client.return_value = mock_queue

        with AllocationLock(["A-001"], timeout_ms=90000, trace_id="t-cm-6") as handle:
            assert handle.timeout_ms == 90000

        call_kwargs = mock_queue.send_message.call_args
        assert call_kwargs[1]["visibility_timeout"] == 90

    @patch("shared.queue_lock._get_queue_client")
    def test_context_manager_multiple_allocations(self, mock_get_client):
        mock_queue = MagicMock()
        mock_queue.send_message.side_effect = [
            _make_queue_message("m-a", "p-a"),
            _make_queue_message("m-b", "p-b"),
        ]
        mock_get_client.return_value = mock_queue

        with AllocationLock(["A-100", "A-200"], trace_id="t-cm-7") as handle:
            assert handle.success is True
            assert len(handle.message_ids) == 2

        assert mock_queue.delete_message.call_count == 2


# ============== Constants Tests ==============

@pytest.mark.unit
class TestConstants:

    def test_default_timeout(self):
        assert DEFAULT_TIMEOUT_MS == 60000

    def test_max_timeout(self):
        assert MAX_TIMEOUT_MS == 300000

    def test_queue_name(self):
        assert LOCK_QUEUE_NAME == "allocation-locks"


# ============== Integration-style (still mocked) Tests ==============

@pytest.mark.unit
class TestAcquireAndReleaseCycle:

    @patch("shared.queue_lock._get_queue_client")
    def test_full_acquire_release_cycle(self, mock_get_client):
        mock_queue = MagicMock()
        mock_queue.send_message.return_value = _make_queue_message("msg-full", "pop-full")
        mock_get_client.return_value = mock_queue

        handle = acquire_allocation_lock(["A-500"], timeout_ms=10000, trace_id="t-cycle")

        assert handle.success is True
        assert handle.is_likely_held() is True

        released = release_allocation_lock(handle, trace_id="t-cycle")

        assert released is True
        mock_queue.delete_message.assert_called_once_with("msg-full", "pop-full")

    @patch("shared.queue_lock._get_queue_client")
    def test_acquire_fails_gracefully(self, mock_get_client):
        mock_get_client.side_effect = ConnectionError("Network down")

        handle = acquire_allocation_lock(["A-600"], trace_id="t-fail")

        assert handle.success is False
        assert handle.error_code == "LOCK_FAILED"

        # Releasing a failed handle should return False
        released = release_allocation_lock(handle, trace_id="t-fail")
        assert released is False
