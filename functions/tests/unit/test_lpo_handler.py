"""
Unit Tests for LPO Handler (v1.4.2)

Tests logic for extracting data from LPO staging rows, including:
- Optional fields (wastage, terms, remarks)
- Attachment processing
- Resilient column lookup
"""

import pytest
from unittest.mock import MagicMock, patch, ANY
import json

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fn_event_dispatcher.handlers.lpo_handler import handle_lpo_ingest
from fn_event_dispatcher.models import RowEvent

@pytest.fixture
def mock_manifest():
    manifest = MagicMock()
    # Map logical names to column IDs
    manifest.get_column_id.side_effect = lambda sheet, col: {
        ("01H_LPO_INGESTION", "SAP_REFERENCE"): 101,
        ("01H_LPO_INGESTION", "CUSTOMER_NAME"): 102,
        ("01H_LPO_INGESTION", "PROJECT_NAME"): 103,
        ("01H_LPO_INGESTION", "PO_QUANTITY_SQM"): 104,
        ("01H_LPO_INGESTION", "PRICE_PER_SQM"): 105,
        ("01H_LPO_INGESTION", "TERMS_OF_PAYMENT"): 106,
        ("01H_LPO_INGESTION", "WASTAGE_PCT"): 107,
        ("01H_LPO_INGESTION", "REMARKS"): 108,
    }.get((sheet, col))
    return manifest

@pytest.fixture
def mock_client():
    client = MagicMock()
    return client

@pytest.mark.unit
class TestLPOIngestHandler:

    @patch("fn_event_dispatcher.handlers.lpo_handler.get_smartsheet_client")
    @patch("shared.event_utils.get_manifest") # Patch dependency of get_cell_value_by_logical_name
    @patch("fn_event_dispatcher.handlers.lpo_handler.get_manifest") # Patch direct usage in handler
    @patch("fn_lpo_ingest.main")
    def test_lpo_full_extraction(self, mock_core_func, mock_get_manifest_handler, mock_get_manifest_utils, mock_get_client, mock_client, mock_manifest):
        """Test extraction of ALL fields including optional ones."""
        mock_get_client.return_value = mock_client
        mock_get_manifest_handler.return_value = mock_manifest
        mock_get_manifest_utils.return_value = mock_manifest
        
        # DEDUP CHECK (v1.6.5): Early dedup uses find_row - return None (no existing)
        mock_client.find_row.return_value = None
        
        # Setup row data with all fields
        mock_client.get_row.return_value = {
            101: "SAP-123",
            102: "Acme Corp",
            103: "Project X",
            104: 500,
            105: 10.5,
            106: "60 Days",
            107: 0.05,
            108: "Urgent delivery"
        }
        
        # Setup attachments
        mock_client.get_row_attachments.return_value = [
            {"name": "lpo.pdf", "url": "http://lpo.pdf"}
        ]
        
        # Mock successful core function response
        mock_response = MagicMock()
        mock_response.get_body.return_value = json.dumps({"status": "OK", "message": "Success"}).encode()
        mock_core_func.return_value = mock_response

        event = RowEvent(sheet_id=1, row_id=999, action="created")
        result = handle_lpo_ingest(event)

        assert result.status == "OK"
        
        # Verify call to core function contains extracted data
        args, _ = mock_core_func.call_args
        request_body = json.loads(args[0].get_body())
        
        assert request_body["sap_reference"] == "SAP-123"
        assert request_body["wastage_pct"] == 5.0
        assert request_body["terms_of_payment"] == "60 Days"
        assert request_body["remarks"] == "Urgent delivery"
        
        # Verify attachments
        assert len(request_body["files"]) == 1
        assert request_body["files"][0]["file_name"] == "lpo.pdf"

    @patch("fn_event_dispatcher.handlers.lpo_handler.get_smartsheet_client")
    @patch("shared.event_utils.get_manifest")
    @patch("fn_event_dispatcher.handlers.lpo_handler.get_manifest")
    @patch("fn_lpo_ingest.main")
    def test_lpo_validation_error_logging(self, mock_core_func, mock_get_manifest_handler, mock_get_manifest_utils, mock_get_client, mock_client, mock_manifest):
        """Test that validation errors return EXCEPTION_LOGGED status."""
        mock_get_client.return_value = mock_client
        mock_get_manifest_handler.return_value = mock_manifest
        mock_get_manifest_utils.return_value = mock_manifest
        
        # DEDUP CHECK (v1.6.5): Early dedup uses find_row - return None (no existing)
        mock_client.find_row.return_value = None
        
        # Missing required SAP Reference
        mock_client.get_row.return_value = {
            102: "Acme Corp" 
            # 101 (SAP) missing
        }
        
        event = RowEvent(sheet_id=1, row_id=999, action="created")
        
        # Trigger validation error by returning invalid data types or missing requireds
        # Using -10.0 to trigger 'gt=0' validation error on float field
        # "INVALID_FLOAT" would trigger ValueError in float() cast (falling to general ERROR)
        mock_client.get_row.return_value = {
            101: "SAP-123",
            104: -10.0  # PO_QUANTITY < 0
        }
        
        result = handle_lpo_ingest(event)
        
        assert result.status == "EXCEPTION_LOGGED"
        assert "Validation error" in result.message

    @patch("fn_event_dispatcher.handlers.lpo_handler.get_smartsheet_client")
    @patch("shared.event_utils.get_manifest")
    @patch("fn_event_dispatcher.handlers.lpo_handler.get_manifest")
    @patch("fn_lpo_ingest.main")
    def test_lpo_ingest_missing_row(self, mock_core_func, mock_get_manifest_handler, mock_get_manifest_utils, mock_get_client, mock_client, mock_manifest):
        """Test handling when row is not found."""
        mock_get_client.return_value = mock_client
        
        # DEDUP CHECK (v1.6.5): Early dedup uses find_row - return None (no existing)
        mock_client.find_row.return_value = None
        
        mock_client.get_row.return_value = None
        
        event = RowEvent(sheet_id=1, row_id=999, action="created")
        result = handle_lpo_ingest(event)
        
        assert result.status == "ERROR"
        assert "not found" in result.message
