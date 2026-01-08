"""
Integration Tests for Tag Ingestion Function

Tests the fn_ingest_tag function with mock Smartsheet client to verify:
- Request processing flow
- Component interactions
- Data writes across multiple sheets
- Error handling and exception creation
"""

import pytest
import json
import uuid
from datetime import datetime
from unittest.mock import patch, MagicMock


import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.sheet_config import SheetName

class TestTagIngestFlow:
    """Test the complete tag ingestion flow."""
    
    @pytest.mark.integration
    def test_successful_tag_ingestion(self, mock_storage, factory, mock_http_request):
        """Test successful tag ingestion with all writes."""
        # Arrange: Create an active LPO
        lpo = factory.create_lpo(
            sap_reference="SAP-TEST-001",
            status="Active",
            po_quantity=500.0,
            delivered_quantity=0.0
        )
        mock_storage.add_row(SheetName.LPO_MASTER.value, lpo)
        
        # Create request
        request_data = factory.create_tag_ingest_request(
            lpo_sap_reference="SAP-TEST-001",
            required_area_m2=50.0,
            uploaded_by="test@company.com"
        )
        
        # Act: Import and call function with mocked client
        with patch('shared.smartsheet_client._client', None):
            with patch('shared.smartsheet_client.SmartsheetClient') as MockClient:
                from tests.conftest import MockSmartsheetClient
                mock_client = MockSmartsheetClient(mock_storage)
                MockClient.return_value = mock_client
                
                # Import function after patching
                from fn_ingest_tag import main
                
                http_req = mock_http_request(request_data)
                
                with patch.object(mock_client, 'find_row_by_column', wraps=mock_client.find_row_by_column):
                    with patch.object(mock_client, 'add_row', wraps=mock_client.add_row):
                        # Mock the get_smartsheet_client to return our mock
                        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
                            response = main(http_req)
        
        # Assert: Check response
        assert response.status_code == 200
        response_data = json.loads(response.get_body())
        assert response_data["status"] == "UPLOADED"
        assert response_data["tag_id"].startswith("TAG-")
        assert "trace_id" in response_data
    
    @pytest.mark.integration
    def test_idempotency_duplicate_request(self, mock_storage, factory, mock_http_request):
        """Test that duplicate client_request_id returns same result."""
        # Arrange: Create LPO and existing tag with client_request_id
        lpo = factory.create_lpo(sap_reference="SAP-001", status="Active", po_quantity=500.0)
        mock_storage.add_row(SheetName.LPO_MASTER.value, lpo)
        
        client_request_id = str(uuid.uuid4())
        existing_tag = factory.create_tag_record(
            tag_name="EXISTING-TAG",
            status="Draft",
            client_request_id=client_request_id
        )
        mock_storage.add_row(SheetName.TAG_REGISTRY.value, existing_tag)
        
        # Create request with same client_request_id
        request_data = factory.create_tag_ingest_request(
            client_request_id=client_request_id,
            lpo_sap_reference="SAP-001",
            required_area_m2=50.0
        )
        
        # Act
        with patch('shared.smartsheet_client._client', None):
            from tests.conftest import MockSmartsheetClient
            mock_client = MockSmartsheetClient(mock_storage)
            
            from fn_ingest_tag import main
            
            http_req = mock_http_request(request_data)
            with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
                response = main(http_req)
        
        # Assert: Should return ALREADY_PROCESSED
        assert response.status_code == 200
        response_data = json.loads(response.get_body())
        assert response_data["status"] == "ALREADY_PROCESSED"
    
    @pytest.mark.integration
    def test_lpo_not_found_creates_exception(self, mock_storage, factory, mock_http_request):
        """Test that LPO not found creates an exception record."""
        # Arrange: No LPO in storage
        request_data = factory.create_tag_ingest_request(
            lpo_sap_reference="NONEXISTENT-SAP",
            required_area_m2=50.0
        )
        
        # Act
        with patch('shared.smartsheet_client._client', None):
            from tests.conftest import MockSmartsheetClient
            mock_client = MockSmartsheetClient(mock_storage)
            
            from fn_ingest_tag import main
            
            http_req = mock_http_request(request_data)
            with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
                response = main(http_req)
        
        # Assert: Should return BLOCKED with exception
        assert response.status_code == 422
        response_data = json.loads(response.get_body())
        assert response_data["status"] == "BLOCKED"
        assert "exception_id" in response_data
        
        # Verify exception was created
        exceptions = mock_storage.find_rows(SheetName.EXCEPTION_LOG.value, "Reason Code", "LPO_NOT_FOUND")
        assert len(exceptions) == 1
    
    @pytest.mark.integration
    def test_lpo_on_hold_creates_exception(self, mock_storage, factory, mock_http_request):
        """Test that LPO on hold creates an exception record."""
        # Arrange: Create LPO on hold
        lpo = factory.create_lpo(
            sap_reference="SAP-HOLD-001",
            status="On Hold",
            po_quantity=500.0
        )
        mock_storage.add_row(SheetName.LPO_MASTER.value, lpo)
        
        request_data = factory.create_tag_ingest_request(
            lpo_sap_reference="SAP-HOLD-001",
            required_area_m2=50.0
        )
        
        # Act
        with patch('shared.smartsheet_client._client', None):
            from tests.conftest import MockSmartsheetClient
            mock_client = MockSmartsheetClient(mock_storage)
            
            from fn_ingest_tag import main
            
            http_req = mock_http_request(request_data)
            with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
                response = main(http_req)
        
        # Assert: Should return BLOCKED
        assert response.status_code == 422
        response_data = json.loads(response.get_body())
        assert response_data["status"] == "BLOCKED"
        
        # Verify exception was created
        exceptions = mock_storage.find_rows(SheetName.EXCEPTION_LOG.value, "Reason Code", "LPO_ON_HOLD")
        assert len(exceptions) == 1
    
    @pytest.mark.integration
    def test_insufficient_po_balance_creates_exception(self, mock_storage, factory, mock_http_request):
        """Test that insufficient PO balance creates an exception."""
        # Arrange: Create LPO with limited capacity
        lpo = factory.create_lpo(
            sap_reference="SAP-LIMITED-001",
            status="Active",
            po_quantity=100.0,
            delivered_quantity=80.0  # Only 20 sqm remaining
        )
        mock_storage.add_row(SheetName.LPO_MASTER.value, lpo)
        
        request_data = factory.create_tag_ingest_request(
            lpo_sap_reference="SAP-LIMITED-001",
            required_area_m2=50.0  # Request more than available
        )
        
        # Act
        with patch('shared.smartsheet_client._client', None):
            from tests.conftest import MockSmartsheetClient
            mock_client = MockSmartsheetClient(mock_storage)
            
            from fn_ingest_tag import main
            
            http_req = mock_http_request(request_data)
            with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
                response = main(http_req)
        
        # Assert: Should return BLOCKED
        assert response.status_code == 422
        response_data = json.loads(response.get_body())
        assert response_data["status"] == "BLOCKED"
        
        # Verify exception was created
        exceptions = mock_storage.find_rows(SheetName.EXCEPTION_LOG.value, "Reason Code", "INSUFFICIENT_PO_BALANCE")
        assert len(exceptions) == 1


class TestDuplicateFileDetection:
    """Test duplicate file detection via file hash."""
    
    @pytest.mark.integration
    def test_duplicate_file_hash_creates_exception(self, mock_storage, factory, mock_http_request):
        """Test that duplicate file hash creates exception."""
        # Arrange: Create LPO and existing tag with file hash
        lpo = factory.create_lpo(sap_reference="SAP-001", status="Active", po_quantity=500.0)
        mock_storage.add_row(SheetName.LPO_MASTER.value, lpo)
        
        existing_file_hash = "abc123def456789"
        existing_tag = factory.create_tag_record(
            tag_name="EXISTING-TAG",
            status="Draft",
            file_hash=existing_file_hash
        )
        mock_storage.add_row(SheetName.TAG_REGISTRY.value, existing_tag)
        
        # Create request with file URL
        request_data = factory.create_tag_ingest_request(
            lpo_sap_reference="SAP-001",
            required_area_m2=50.0,
            file_url="https://sharepoint/file.xlsx"
        )
        
        # Act: Mock the hash computation to return same hash
        with patch('shared.smartsheet_client._client', None):
            from tests.conftest import MockSmartsheetClient
            mock_client = MockSmartsheetClient(mock_storage)
            
            from fn_ingest_tag import main
            
            http_req = mock_http_request(request_data)
            with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
                with patch('fn_ingest_tag.compute_file_hash_from_url', return_value=existing_file_hash):
                    response = main(http_req)
        
        # Assert: Should return DUPLICATE
        assert response.status_code == 409
        response_data = json.loads(response.get_body())
        assert response_data["status"] == "DUPLICATE"
        assert "existing_tag_id" in response_data
        
        # Verify exception was created
        exceptions = mock_storage.find_rows(SheetName.EXCEPTION_LOG.value, "Reason Code", "DUPLICATE_UPLOAD")
        assert len(exceptions) == 1


class TestUserActionLogging:
    """Test user action history logging."""
    
    @pytest.mark.integration
    def test_successful_upload_logs_user_action(self, mock_storage, factory, mock_http_request):
        """Test that successful upload logs user action."""
        # Arrange
        lpo = factory.create_lpo(sap_reference="SAP-001", status="Active", po_quantity=500.0)
        mock_storage.add_row(SheetName.LPO_MASTER.value, lpo)
        
        request_data = factory.create_tag_ingest_request(
            lpo_sap_reference="SAP-001",
            required_area_m2=50.0,
            uploaded_by="specific.user@company.com"
        )
        
        # Act
        with patch('shared.smartsheet_client._client', None):
            from tests.conftest import MockSmartsheetClient
            mock_client = MockSmartsheetClient(mock_storage)
            
            from fn_ingest_tag import main
            
            http_req = mock_http_request(request_data)
            with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
                response = main(http_req)
        
        # Assert: Verify user action was logged
        actions = mock_storage.find_rows(SheetName.USER_ACTION_LOG.value, "Action Type", "TAG_CREATED")
        assert len(actions) == 1
        assert actions[0]["User ID"] == "specific.user@company.com"
    
    @pytest.mark.integration
    def test_failed_upload_logs_operation_failed(self, mock_storage, factory, mock_http_request):
        """Test that failed upload logs OPERATION_FAILED action."""
        # Arrange: Create LPO and duplicate file hash
        lpo = factory.create_lpo(sap_reference="SAP-001", status="Active", po_quantity=500.0)
        mock_storage.add_row(SheetName.LPO_MASTER.value, lpo)
        
        existing_tag = factory.create_tag_record(
            tag_name="EXISTING",
            file_hash="duplicate_hash"
        )
        mock_storage.add_row(SheetName.TAG_REGISTRY.value, existing_tag)
        
        request_data = factory.create_tag_ingest_request(
            lpo_sap_reference="SAP-001",
            required_area_m2=50.0,
            file_url="https://sharepoint/file.xlsx"
        )
        
        # Act
        with patch('shared.smartsheet_client._client', None):
            from tests.conftest import MockSmartsheetClient
            mock_client = MockSmartsheetClient(mock_storage)
            
            from fn_ingest_tag import main
            
            http_req = mock_http_request(request_data)
            with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
                with patch('fn_ingest_tag.compute_file_hash_from_url', return_value="duplicate_hash"):
                    response = main(http_req)
        
        # Assert: Verify OPERATION_FAILED was logged
        actions = mock_storage.find_rows(SheetName.USER_ACTION_LOG.value, "Action Type", "OPERATION_FAILED")
        assert len(actions) == 1


class TestTagDataWrite:
    """Test tag record data integrity."""
    
    @pytest.mark.integration
    def test_tag_record_contains_all_fields(self, mock_storage, factory, mock_http_request):
        """Test that created tag record contains all expected fields."""
        # Arrange
        lpo = factory.create_lpo(
            sap_reference="SAP-FULL-001",
            customer_name="Test Customer Inc",
            brand="Premium Brand",
            status="Active",
            po_quantity=500.0
        )
        mock_storage.add_row(SheetName.LPO_MASTER.value, lpo)
        
        request_data = factory.create_tag_ingest_request(
            lpo_sap_reference="SAP-FULL-001",
            required_area_m2=75.5,
            requested_delivery_date="2026-03-15",
            tag_name="Custom Tag Name",
            uploaded_by="sales@company.com"
        )
        client_request_id = request_data["client_request_id"]
        
        # Act
        with patch('shared.smartsheet_client._client', None):
            from tests.conftest import MockSmartsheetClient
            mock_client = MockSmartsheetClient(mock_storage)
            
            from fn_ingest_tag import main
            
            http_req = mock_http_request(request_data)
            with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
                response = main(http_req)
        
        # Assert response success
        assert response.status_code == 200
        
        # Verify tag record
        tags = mock_storage.find_rows(SheetName.TAG_REGISTRY.value, "Client Request ID", client_request_id)
        assert len(tags) == 1
        tag = tags[0]
        
        assert tag["Tag Sheet Name/ Rev"] == "Custom Tag Name"
        assert tag["Status"] == "Draft"
        assert tag["Estimated Quantity"] == 75.5
        assert tag["Submitted By"] == "sales@company.com"
        assert tag["LPO SAP Reference Link"] == "SAP-FULL-001"
