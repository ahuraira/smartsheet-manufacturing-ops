"""
Integration Tests for Tag Ingestion Function

Tests the complete tag ingestion flow including:
- Request handling and validation
- LPO lookup and validation
- File hash duplicate detection
- Tag record creation
- Exception creation on failures
- User action logging

Updated for v1.1.0 with:
- Logical names (Sheet, Column)
- Manifest-based column resolution
- Base64 file content support
- Status starting at "Validate"
"""

import pytest
import json
import base64
from unittest.mock import patch, MagicMock
from datetime import datetime

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.logical_names import Sheet, Column


class TestTagIngestFlow:
    """Integration tests for complete tag ingestion flows."""
    
    @pytest.mark.integration
    def test_successful_tag_ingestion(self, mock_storage, factory, mock_http_request):
        """Test successful tag ingestion end-to-end."""
        # Arrange: Create an LPO in mock storage
        lpo = factory.create_lpo(
            sap_reference="SAP-TEST-001",
            po_quantity=500.0,
            delivered_quantity=100.0,
            status="Active"
        )
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        request_data = factory.create_tag_ingest_request(
            lpo_sap_reference="SAP-TEST-001",
            required_area_m2=50.0
        )
        
        # Act: Call the function with mocked dependencies
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    import fn_ingest_tag
                    response = fn_ingest_tag.main(mock_http_request(request_data))
        
        # Assert
        assert response.status_code == 200
        body = json.loads(response.get_body())
        assert body["status"] == "UPLOADED"
        assert "tag_id" in body
        assert body["tag_id"].startswith("TAG-")
    
    @pytest.mark.integration
    def test_idempotency_duplicate_request(self, mock_storage, factory, mock_http_request):
        """Test that duplicate client_request_id returns existing record."""
        # Arrange: Create LPO and existing tag
        lpo = factory.create_lpo(sap_reference="SAP-TEST-002")
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        client_request_id = "duplicate-request-id-123"
        existing_tag = factory.create_tag_record(
            tag_id="TAG-0001",
            client_request_id=client_request_id
        )
        mock_storage.add_row("Tag Sheet Registry", existing_tag)
        
        request_data = factory.create_tag_ingest_request(
            client_request_id=client_request_id,
            lpo_sap_reference="SAP-TEST-002"
        )
        
        # Act
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    import fn_ingest_tag
                    response = fn_ingest_tag.main(mock_http_request(request_data))
        
        # Assert
        assert response.status_code == 200
        body = json.loads(response.get_body())
        assert body["status"] == "ALREADY_PROCESSED"
    
    @pytest.mark.integration
    def test_lpo_not_found_creates_exception(self, mock_storage, factory, mock_http_request):
        """Test that missing LPO creates an exception record."""
        request_data = factory.create_tag_ingest_request(
            lpo_sap_reference="NON-EXISTENT-SAP"
        )
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    import fn_ingest_tag
                    response = fn_ingest_tag.main(mock_http_request(request_data))
        
        # Assert
        assert response.status_code == 422
        body = json.loads(response.get_body())
        assert body["status"] == "BLOCKED"
        assert "exception_id" in body
    
    @pytest.mark.integration
    def test_lpo_on_hold_creates_exception(self, mock_storage, factory, mock_http_request):
        """Test that LPO on hold creates an exception."""
        lpo = factory.create_lpo(
            sap_reference="SAP-ON-HOLD",
            status="On Hold"
        )
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        request_data = factory.create_tag_ingest_request(
            lpo_sap_reference="SAP-ON-HOLD"
        )
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    import fn_ingest_tag
                    response = fn_ingest_tag.main(mock_http_request(request_data))
        
        assert response.status_code == 422
        body = json.loads(response.get_body())
        assert body["status"] == "BLOCKED"
    
    @pytest.mark.integration
    def test_insufficient_po_balance_creates_exception(self, mock_storage, factory, mock_http_request):
        """Test that insufficient PO balance creates an exception."""
        lpo = factory.create_lpo(
            sap_reference="SAP-LOW-BALANCE",
            po_quantity=100.0,
            delivered_quantity=80.0,  # Only 20 remaining
            status="Active"
        )
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        request_data = factory.create_tag_ingest_request(
            lpo_sap_reference="SAP-LOW-BALANCE",
            required_area_m2=50.0  # Requesting more than available
        )
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    import fn_ingest_tag
                    response = fn_ingest_tag.main(mock_http_request(request_data))
        
        assert response.status_code == 422
        body = json.loads(response.get_body())
        assert body["status"] == "BLOCKED"


class TestBase64FileContent:
    """Tests for base64 file content feature (v1.1.0)."""
    
    @pytest.mark.integration
    def test_base64_file_content_hashing(self, mock_storage, factory, mock_http_request):
        """Test that base64 file content is properly hashed."""
        lpo = factory.create_lpo(sap_reference="SAP-BASE64-TEST")
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        file_content = base64.b64encode(b"PDF content here").decode()
        request_data = factory.create_tag_ingest_request(
            lpo_sap_reference="SAP-BASE64-TEST",
            file_content=file_content
        )
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    import fn_ingest_tag
                    response = fn_ingest_tag.main(mock_http_request(request_data))
        
        assert response.status_code == 200
        body = json.loads(response.get_body())
        assert body["status"] == "UPLOADED"
        assert "file_hash" in body
        assert body["file_hash"] is not None


class TestDuplicateFileDetection:
    """Tests for duplicate file detection via hash."""
    
    @pytest.mark.integration
    def test_duplicate_file_hash_creates_exception(self, mock_storage, factory, mock_http_request):
        """Test that duplicate file hash creates exception and returns 409."""
        # Arrange: LPO and existing tag with same hash
        lpo = factory.create_lpo(sap_reference="SAP-DUP-HASH")
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        # Create existing tag with known hash
        existing_tag = factory.create_tag_record(
            tag_id="TAG-EXISTING",
            file_hash="abc123duplicatehash"
        )
        mock_storage.add_row("Tag Sheet Registry", existing_tag)
        
        request_data = factory.create_tag_ingest_request(
            lpo_sap_reference="SAP-DUP-HASH",
            file_url="https://example.com/file.xlsx"
        )
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        # Mock the URL hash to return our duplicate hash
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    with patch('fn_ingest_tag.compute_file_hash_from_url', return_value="abc123duplicatehash"):
                        import fn_ingest_tag
                        response = fn_ingest_tag.main(mock_http_request(request_data))
        
        assert response.status_code == 409
        body = json.loads(response.get_body())
        assert body["status"] == "DUPLICATE"
        assert "exception_id" in body


class TestUserActionLogging:
    """Tests for user action audit logging."""
    
    @pytest.mark.integration
    def test_successful_upload_logs_user_action(self, mock_storage, factory, mock_http_request):
        """Test TAG_CREATED action is logged on success."""
        lpo = factory.create_lpo(sap_reference="SAP-LOG-TEST")
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        request_data = factory.create_tag_ingest_request(
            lpo_sap_reference="SAP-LOG-TEST",
            uploaded_by="auditor@company.com"
        )
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    import fn_ingest_tag
                    response = fn_ingest_tag.main(mock_http_request(request_data))
        
        assert response.status_code == 200
        
        # Check user action was logged
        actions = mock_storage.find_rows("98 User Action Log", "User ID", "auditor@company.com")
        assert len(actions) >= 1
        assert any(a.get("Action Type") == "TAG_CREATED" for a in actions)
    
    @pytest.mark.integration
    def test_failed_operation_logs_user_action_v110(self, mock_storage, factory, mock_http_request):
        """Test OPERATION_FAILED action is logged on validation failure (v1.1.0 fix)."""
        # No LPO exists - will fail
        request_data = factory.create_tag_ingest_request(
            lpo_sap_reference="NON-EXISTENT",
            uploaded_by="failed-user@company.com"
        )
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    import fn_ingest_tag
                    response = fn_ingest_tag.main(mock_http_request(request_data))
        
        assert response.status_code == 422
        
        # v1.1.0 fix: All BLOCKED scenarios now log user actions
        actions = mock_storage.find_rows("98 User Action Log", "User ID", "failed-user@company.com")
        assert len(actions) >= 1
        assert any(a.get("Action Type") == "OPERATION_FAILED" for a in actions)


class TestTagRecordFields:
    """Tests for complete tag record field population (v1.1.0)."""
    
    @pytest.mark.integration
    def test_tag_record_status_starts_at_validate_v110(self, mock_storage, factory, mock_http_request):
        """Test that tag status starts at 'Validate' not 'Draft' (v1.1.0 change)."""
        lpo = factory.create_lpo(sap_reference="SAP-STATUS-TEST")
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        request_data = factory.create_tag_ingest_request(
            lpo_sap_reference="SAP-STATUS-TEST"
        )
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    import fn_ingest_tag
                    response = fn_ingest_tag.main(mock_http_request(request_data))
        
        assert response.status_code == 200
        body = json.loads(response.get_body())
        
        # Find created tag
        tags = mock_storage.find_rows("Tag Sheet Registry", "Client Request ID", request_data["client_request_id"])
        assert len(tags) == 1
        assert tags[0].get("Status") == "Validate"  # v1.1.0: Was "Draft", now "Validate"
    
    @pytest.mark.integration
    def test_received_through_field_populated_v110(self, mock_storage, factory, mock_http_request):
        """Test that received_through field is saved (v1.1.0 feature)."""
        lpo = factory.create_lpo(sap_reference="SAP-RECEIVED-TEST")
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        request_data = factory.create_tag_ingest_request(
            lpo_sap_reference="SAP-RECEIVED-TEST",
            received_through="Email"
        )
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    import fn_ingest_tag
                    response = fn_ingest_tag.main(mock_http_request(request_data))
        
        assert response.status_code == 200
        
        tags = mock_storage.find_rows("Tag Sheet Registry", "Client Request ID", request_data["client_request_id"])
        assert len(tags) == 1
        assert tags[0].get("Received Through") == "Email"
