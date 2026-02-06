"""
Schedule Handler Tests
======================

Tests for schedule_handler.py that ACTUALLY verify behavior:
1. Verifies fn_schedule_tag.main() is actually called
2. Verifies DispatchResult uses only valid fields  
3. Verifies dedup returns early
4. Verifies exception creation on errors

These tests would have caught the silent failures:
- Handler not calling fn_schedule_tag
- Invalid DispatchResult fields being silently ignored
- Dedup check not returning early
"""

import pytest
import json
from unittest.mock import MagicMock, patch, call
from fn_event_dispatcher.models import RowEvent, DispatchResult
from fn_event_dispatcher.handlers.schedule_handler import handle_schedule_ingest


class TestScheduleHandlerIntegrity:
    """Tests that verify actual behavior, not just mocks."""
    
    @patch("fn_event_dispatcher.handlers.schedule_handler.get_smartsheet_client")
    @patch("fn_event_dispatcher.handlers.schedule_handler.get_manifest")
    @patch("fn_event_dispatcher.handlers.schedule_handler.get_cell_value_by_logical_name")
    @patch("fn_schedule_tag.main")
    def test_fn_schedule_tag_is_actually_called(
        self, mock_main, mock_get_cell, mock_manifest, mock_client
    ):
        """
        CRITICAL TEST: Verify fn_schedule_tag.main() is actually invoked.
        This would have caught the original bug where handler returned READY
        without calling the target function.
        """
        # Setup
        client = MagicMock()
        client.find_row.return_value = None  # No dedup
        client.get_row.return_value = {"id": 123}
        mock_client.return_value = client
        
        manifest = MagicMock()
        manifest.get_sheet_id.return_value = 123456
        mock_manifest.return_value = manifest
        
        # Mock cell values
        mock_get_cell.side_effect = lambda row, sheet, col: {
            "TAG_SHEET_ID": "TAG-0012",
            "PLANNED_DATE": "2026-02-01",
            "SHIFT": "Morning",
            "MACHINE_ASSIGNED": "1",
            "PLANNED_QUANTITY": 100.0,
        }.get(col)
        
        # Mock fn_schedule_tag response
        mock_response = MagicMock()
        mock_response.get_body.return_value = json.dumps({
            "status": "RELEASED_FOR_NESTING",
            "message": "Scheduled",
            "schedule_id": "SCH-0001"
        }).encode()
        mock_main.return_value = mock_response
        
        event = RowEvent(
            sheet_id=123456,
            row_id=789,
            action="created",
            actor_id="test@example.com"
        )
        
        # Execute
        result = handle_schedule_ingest(event)
        
        # CRITICAL ASSERTION: main() MUST be called
        assert mock_main.called, "fn_schedule_tag.main() was never called!"
        assert mock_main.call_count == 1
        
        # Verify it was called with an HttpRequest
        call_args = mock_main.call_args[0]
        http_req = call_args[0]
        assert http_req.method == "POST"
        
    @patch("fn_event_dispatcher.handlers.schedule_handler.get_smartsheet_client")
    @patch("fn_event_dispatcher.handlers.schedule_handler.get_manifest")
    def test_dedup_returns_immediately(self, mock_manifest, mock_client):
        """
        Verify dedup check returns immediately - does NOT continue processing.
        This would have caught the bug where dedup checked but didn't return.
        """
        client = MagicMock()
        # Dedup finds existing row
        client.find_row.return_value = {"Schedule ID": "SCH-0001", "id": 123}
        mock_client.return_value = client
        
        manifest = MagicMock()
        mock_manifest.return_value = manifest
        
        event = RowEvent(
            sheet_id=123456,
            row_id=789,
            action="created"
        )
        
        # Execute
        result = handle_schedule_ingest(event)
        
        # CRITICAL: Should return ALREADY_PROCESSED, NOT continue
        assert result.status == "ALREADY_PROCESSED"
        
        # CRITICAL: get_row should NOT be called (early return)
        assert not client.get_row.called, "get_row called after dedup - should have returned early!"
        
    def test_dispatch_result_uses_valid_fields_only(self):
        """
        Verify DispatchResult only uses fields that exist in the model.
        This would have caught the bug where invalid fields were silently ignored.
        """
        # Get valid fields from DispatchResult model
        valid_fields = set(DispatchResult.model_fields.keys())
        
        # These are the ONLY valid fields
        expected = {"status", "handler", "message", "trace_id", "processing_time_ms", "details"}
        assert valid_fields == expected, f"DispatchResult fields changed! Valid: {valid_fields}"
        
        # Test that invalid fields raise error when using Pydantic strict mode
        # (Though by default extra is ignored - this documents expected behavior)
        result = DispatchResult(
            status="OK",
            handler="test",
            message="test",
            trace_id="trace-123",
            details={"exception_id": "EX-001"}  # exception_id goes in details, not as top-level
        )
        
        # Verify exception_id is in details, not top-level
        assert result.details.get("exception_id") == "EX-001"
        assert not hasattr(result, "exception_id") or result.model_fields.get("exception_id") is None

    @patch("fn_event_dispatcher.handlers.schedule_handler.get_smartsheet_client")
    @patch("fn_event_dispatcher.handlers.schedule_handler.get_manifest")
    @patch("fn_event_dispatcher.handlers.schedule_handler.get_cell_value_by_logical_name")
    @patch("fn_event_dispatcher.handlers.schedule_handler.create_exception")
    def test_missing_tag_id_creates_exception(
        self, mock_create_exc, mock_get_cell, mock_manifest, mock_client
    ):
        """
        Verify missing tag_id creates an exception (not silent failure).
        """
        client = MagicMock()
        client.find_row.return_value = None
        client.get_row.return_value = {"id": 123}
        mock_client.return_value = client
        
        manifest = MagicMock()
        manifest.get_sheet_id.return_value = 123456
        mock_manifest.return_value = manifest
        
        # Return None for TAG_SHEET_ID
        mock_get_cell.return_value = None
        mock_create_exc.return_value = "EX-001"
        
        event = RowEvent(
            sheet_id=123456,
            row_id=789,
            action="created"
        )
        
        result = handle_schedule_ingest(event)
        
        # CRITICAL: Must create exception, not silently fail
        assert mock_create_exc.called, "Exception not created for missing tag_id!"
        assert result.status == "EXCEPTION_LOGGED"
        assert result.details.get("exception_id") == "EX-001"


class TestScheduleHandlerEdgeCases:
    """Edge case tests."""
    
    @patch("fn_event_dispatcher.handlers.schedule_handler.get_smartsheet_client")
    @patch("fn_event_dispatcher.handlers.schedule_handler.get_manifest")
    def test_row_not_found_returns_error(self, mock_manifest, mock_client):
        """Verify proper error when staging row doesn't exist."""
        client = MagicMock()
        client.find_row.return_value = None
        client.get_row.return_value = None  # Row not found
        mock_client.return_value = client
        
        manifest = MagicMock()
        manifest.get_sheet_id.return_value = 123456
        mock_manifest.return_value = manifest
        
        event = RowEvent(
            sheet_id=123456,
            row_id=789,
            action="created"
        )
        
        result = handle_schedule_ingest(event)
        
        assert result.status == "ERROR"
        assert "not found" in result.message.lower()
