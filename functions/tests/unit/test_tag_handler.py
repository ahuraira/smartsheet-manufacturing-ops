
import pytest
from unittest.mock import MagicMock, patch
from shared import TagIngestRequest, FileAttachment
from fn_event_dispatcher.models import RowEvent, DispatchResult
from fn_event_dispatcher.handlers.tag_handler import handle_tag_ingest

@patch("fn_event_dispatcher.handlers.tag_handler.get_smartsheet_client")
@patch("fn_event_dispatcher.handlers.tag_handler.get_cell_value_by_logical_name")
@patch("fn_event_dispatcher.handlers.tag_handler.extract_row_attachments_as_files")
@patch("fn_ingest_tag.main")
def test_handle_tag_ingest_v1_6_3_features(mock_main, mock_extract_files, mock_get_cell, mock_get_client):
    """
    Test v1.6.2 and v1.6.3 features:
    1. Correct logical column names (v1.6.2)
    2. Multi-file attachment extraction (v1.6.3)
    """
    # Setup mocks
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    
    # Mock row lookup
    mock_row = {"id": 12345, "cells": []}
    mock_client.get_row.return_value = mock_row
    
    # Mock Column Values (v1.6.2 Mapping)
    def side_effect(row, sheet, col_name):
        mapping = {
            "LPO_SAP_REFERENCE_LINK": "SAP-001",
            "ESTIMATED_QUANTITY": 100.0,
            "REQUIRED_DELIVERY_DATE": "2026-02-01",
            "TAG_SHEET_NAME_REV": "TAG-ABC Rev 1"
        }
        return mapping.get(col_name)
    mock_get_cell.side_effect = side_effect
    
    # Mock File Extraction (v1.6.3 Multi-file)
    mock_files = [
        FileAttachment(file_name="f1.pdf", file_url="http://u1", file_hash="h1"),
        FileAttachment(file_name="f2.pdf", file_url="http://u2", file_hash="h2")
    ]
    mock_extract_files.return_value = mock_files
    
    # Mock main function response
    mock_response = MagicMock()
    mock_response.get_body.return_value = b'{"status": "UPLOADED", "message": "Success"}'
    mock_main.return_value = mock_response

    # Test Event
    event = RowEvent(
        source="smartsheet",
        source_id="sheet_id_123",
        event_type="created",
        action="created",
        row_id=12345,
        sheet_id=987654321
    )
    
    # Execute
    result = handle_tag_ingest(event)
    
    assert result.status == "UPLOADED"
    
    # Verify Column Mapping (v1.6.2)
    mock_get_cell.assert_any_call(mock_row, "02H_TAG_SHEET_STAGING", "LPO_SAP_REFERENCE_LINK")
    mock_get_cell.assert_any_call(mock_row, "02H_TAG_SHEET_STAGING", "ESTIMATED_QUANTITY")
    mock_get_cell.assert_any_call(mock_row, "02H_TAG_SHEET_STAGING", "REQUIRED_DELIVERY_DATE")
    
    # Verify Multi-file Extraction (v1.6.3)
    mock_extract_files.assert_called_once()
    assert mock_extract_files.call_args[1]["row_id"] == 12345
    
    # Verify Payload Construction
    # We need to capture the HttpRequest body sent to fn_ingest_tag
    call_args = mock_main.call_args[0]
    http_req = call_args[0]
    body_json = http_req.get_json()
    
    # Assert tag_name extracted
    assert body_json["tag_name"] == "TAG-ABC Rev 1"
    
    # Assert files list passed correctly
    assert len(body_json["files"]) == 2
    assert body_json["files"][0]["file_name"] == "f1.pdf"
    assert body_json["files"][1]["file_name"] == "f2.pdf"
