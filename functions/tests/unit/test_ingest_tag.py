"""
Unit Tests for Tag Ingestion (v1.6.3)

Tests the core logic of fn_ingest_tag, specifically:
- Multi-file combined hashing (idempotency)
- Attachment iteration logic
- LPO validation and balance checks
"""

import pytest
from unittest.mock import MagicMock, patch
from shared import TagIngestRequest, FileAttachment, ReasonCode, ExceptionSeverity, ActionType

# Import main function directly if possible, or mock around it
# Since fn_ingest_tag is an azure function, we test the logic inside main or helper functions
# Ideally we import the main function to integration test the flow.
from fn_ingest_tag import main, _find_lpo
import azure.functions as func
import json

@pytest.fixture
def mock_req_factory():
    def _create(body: dict):
        return func.HttpRequest(
            method="POST",
            url="/api/tags/ingest",
            body=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}
        )
    return _create

@pytest.mark.unit
class TestIngestTag:
    
    @patch("fn_ingest_tag.get_smartsheet_client")
    @patch("fn_ingest_tag.compute_combined_file_hash")
    @patch("fn_ingest_tag.generate_next_tag_id")
    def test_ingest_tag_combined_hash(self, mock_gen_id, mock_compute_hash, mock_get_client, mock_req_factory):
        """Test that combined hash is computed and checked for duplicates."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_gen_id.return_value = "TAG-NEW-001"
        
        # Scenario: 2 files
        files = [
            {"file_name": "a.pdf", "file_url": "u1"},
            {"file_name": "b.pdf", "file_url": "u2"}
        ]
        body = {
            "lpo_sap_reference": "SAP-001",
            "required_area_m2": 50.0,
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "user",
            "files": files
        }
        
        # Mock LPO lookup success
        mock_client.find_row.side_effect = [
            None, # Client Request ID check (not found)
            None, # File Hash check (not found - simulating new upload)
            {"id": 1, "cells": []} # LPO found (simplified)
        ]
        
        # Mock _find_lpo manually if needed, but find_row side effect above might handle it
        # Actually _find_lpo calls find_row. Order matters.
        # 1. Idempotency check (find_row by request_id) -> None
        # 2. Hash check (find_row by hash) -> None
        # 3. LPO lookup (find_row by sap_ref) -> Found
        
        # Mock hash computation
        mock_compute_hash.return_value = "COMBINED-HASH-123"
        
        # Mock physical column helper (fn_ingest_tag._get_physical_column_name)
        # Instead of mocking internal helper, let's mock client.add_row which receives dictionary.
        mock_client.add_row.return_value = {"id": 100}

        # Mock LPO Status/Balance checks (reading LPO row)
        # This is tricky because code reads lpo.get(col_name).
        # We need _get_physical_column_name to work or mock it.
        # Let's mock the module level function in fn_ingest_tag
        with patch("fn_ingest_tag._get_physical_column_name", side_effect=lambda s, c: c): # Return logical as physical
            # Update find_row side effect to return a dict that supports get()
            mock_lpo_row = {
                "LPO_STATUS": "Active",
                "PO_QUANTITY_SQM": "1000",
                "DELIVERED_QUANTITY_SQM": "0"
            }
            mock_client.find_row.side_effect = [None, None, mock_lpo_row]
            
            # Execute
            req = mock_req_factory(body)
            resp = main(req)
            
            assert resp.status_code == 200
            
            # Verify Hash Computation
            mock_compute_hash.assert_called_once()
            args = mock_compute_hash.call_args[0][0] # First arg is list of FileAttachment
            assert len(args) == 2
            assert isinstance(args[0], FileAttachment)
            
            # Verify File Hash Logic was used in find_row
            # 2nd call to find_row should use the computed hash
            call_args = mock_client.find_row.call_args_list[1]
            assert call_args[0][2] == "COMBINED-HASH-123"

    @patch("fn_ingest_tag.get_smartsheet_client")
    @patch("fn_ingest_tag.compute_combined_file_hash")
    @patch("fn_ingest_tag.generate_next_tag_id")
    def test_ingest_tag_attachments_iteration(self, mock_gen_id, mock_compute_hash, mock_get_client, mock_req_factory):
        """Test that all files are attached to the created row."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_gen_id.return_value = "TAG-NEW-002"
        
        files = [
            {"file_name": "url_file.pdf", "file_url": "http://test.com/1"},
            {"file_name": "content_file.txt", "file_content": "base64..."}
        ]
        body = {
            "lpo_sap_reference": "SAP-001",
            "required_area_m2": 10.0,
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "user",
            "files": files
        }
        
        with patch("fn_ingest_tag._get_physical_column_name", side_effect=lambda s, c: c):
             mock_lpo_row = {"LPO_STATUS": "Active", "PO_QUANTITY_SQM": "100", "DELIVERED_QUANTITY_SQM": "0"}
             mock_client.find_row.side_effect = [None, None, mock_lpo_row]
             mock_client.add_row.return_value = {"id": 999} # Created Row ID
             
             req = mock_req_factory(body)
             main(req)
             
             # Verify Attachments
             assert mock_client.attach_url_to_row.call_count == 1
             mock_client.attach_url_to_row.assert_called_with(
                 "TAG_REGISTRY", 999, "http://test.com/1", "url_file.pdf"
             )
             
             assert mock_client.attach_file_to_row.call_count == 1
             mock_client.attach_file_to_row.assert_called_with(
                 "TAG_REGISTRY", 999, "base64...", "content_file.txt"
             )
