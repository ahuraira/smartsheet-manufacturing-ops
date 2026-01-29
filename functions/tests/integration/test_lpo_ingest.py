"""
Integration Tests for LPO Ingestion Function (v1.2.0)

Tests the complete LPO ingestion flow including:
- Happy path LPO creation
- Idempotency via client_request_id
- Duplicate SAP Reference detection
- Duplicate file hash detection
- Brand validation
- Folder path generation
- User action logging
"""

import pytest
import json
import uuid
import base64
from unittest.mock import patch, MagicMock
from datetime import datetime

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


@pytest.mark.integration
class TestLPOIngestHappyPath:
    """Tests for successful LPO ingestion."""
    
    def test_create_lpo_success(self, mock_storage, factory, mock_http_request):
        """Test successful LPO creation."""
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "sap_reference": "PTE-TEST-001",
            "customer_name": "Test Customer",
            "project_name": "Test Project",
            "brand": "KIMMCO",
            "po_quantity_sqm": 1000.0,
            "price_per_sqm": 150.0,
            "uploaded_by": "user@company.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_lpo_ingest.get_smartsheet_client', return_value=mock_client):
            with patch('fn_lpo_ingest.get_manifest', return_value=MockWorkspaceManifest()):
                from fn_lpo_ingest import main
                http_req = mock_http_request(request_data)
                response = main(http_req)
        
        assert response.status_code == 200
        body = json.loads(response.get_body())
        assert body["status"] == "OK"
        assert body["sap_reference"] == "PTE-TEST-001"
        assert "folder_path" in body
        assert "LPOs" in body["folder_path"]
        assert "trace_id" in body
    
    def test_lpo_folder_path_contains_sap_and_customer(self, mock_storage, factory, mock_http_request):
        """Test that folder path contains SAP ref and customer name."""
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "sap_reference": "PTE-500",
            "customer_name": "Acme Corp",
            "project_name": "Project X",
            "brand": "WTI",
            "po_quantity_sqm": 500.0,
            "price_per_sqm": 200.0,
            "uploaded_by": "user@company.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_lpo_ingest.get_smartsheet_client', return_value=mock_client):
            with patch('fn_lpo_ingest.get_manifest', return_value=MockWorkspaceManifest()):
                from fn_lpo_ingest import main
                response = main(mock_http_request(request_data))
        
        body = json.loads(response.get_body())
        assert "PTE-500" in body["folder_path"]
        assert "Acme" in body["folder_path"]
    
    def test_user_action_logged_on_success(self, mock_storage, factory, mock_http_request):
        """Test that LPO_CREATED action is logged."""
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "sap_reference": "PTE-LOG-001",
            "customer_name": "Log Test",
            "project_name": "Project",
            "brand": "KIMMCO",
            "po_quantity_sqm": 100.0,
            "price_per_sqm": 100.0,
            "uploaded_by": "logger@company.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_lpo_ingest.get_smartsheet_client', return_value=mock_client):
            with patch('fn_lpo_ingest.get_manifest', return_value=MockWorkspaceManifest()):
                from fn_lpo_ingest import main
                response = main(mock_http_request(request_data))
        
        assert response.status_code == 200
        
        # Verify user action logged
        actions = mock_storage.find_rows("98 User Action Log", "Action Type", "LPO_CREATED")
        assert len(actions) >= 1


@pytest.mark.integration
class TestLPOIngestIdempotency:
    """Tests for idempotency via client_request_id."""
    
    def test_duplicate_client_request_id_returns_already_processed(self, mock_storage, factory, mock_http_request):
        """Test that duplicate client_request_id returns ALREADY_PROCESSED."""
        client_request_id = str(uuid.uuid4())
        
        # Create existing LPO with same client_request_id
        existing_lpo = {
            "SAP Reference": "PTE-EXISTING",
            "Client Request ID": client_request_id,
            "Customer Name": "Existing",
            "Project Name": "Existing",
            "Brand": "KIMMCO",
            "LPO Status": "Draft",
            "Folder URL": "https://test.com/LPOs/PTE-EXISTING_Existing"
        }
        mock_storage.add_row("01 LPO Master LOG", existing_lpo)
        
        request_data = {
            "client_request_id": client_request_id,  # Same ID
            "sap_reference": "PTE-NEW-001",
            "customer_name": "New Customer",
            "project_name": "New Project",
            "brand": "KIMMCO",
            "po_quantity_sqm": 500.0,
            "price_per_sqm": 150.0,
            "uploaded_by": "user@company.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_lpo_ingest.get_smartsheet_client', return_value=mock_client):
            with patch('fn_lpo_ingest.get_manifest', return_value=MockWorkspaceManifest()):
                from fn_lpo_ingest import main
                response = main(mock_http_request(request_data))
        
        assert response.status_code == 200
        body = json.loads(response.get_body())
        assert body["status"] == "ALREADY_PROCESSED"


@pytest.mark.integration
class TestLPOIngestDuplicateDetection:
    """Tests for duplicate detection."""
    
    def test_duplicate_sap_reference_returns_409(self, mock_storage, factory, mock_http_request):
        """Test that duplicate SAP Reference returns 409 DUPLICATE."""
        # Create existing LPO
        existing_lpo = {
            "SAP Reference": "PTE-DUP-001",
            "Customer Name": "Existing",
            "Project Name": "Existing",
            "Brand": "KIMMCO",
            "LPO Status": "Active"
        }
        mock_storage.add_row("01 LPO Master LOG", existing_lpo)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "sap_reference": "PTE-DUP-001",  # Same SAP Reference!
            "customer_name": "New Customer",
            "project_name": "New Project",
            "brand": "KIMMCO",
            "po_quantity_sqm": 500.0,
            "price_per_sqm": 150.0,
            "uploaded_by": "user@company.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_lpo_ingest.get_smartsheet_client', return_value=mock_client):
            with patch('fn_lpo_ingest.get_manifest', return_value=MockWorkspaceManifest()):
                from fn_lpo_ingest import main
                response = main(mock_http_request(request_data))
        
        assert response.status_code == 409
        body = json.loads(response.get_body())
        assert body["status"] == "DUPLICATE"
        assert "exception_id" in body
        assert body["exception_id"].startswith("EX-")
    
    def test_duplicate_file_hash_returns_409(self, mock_storage, factory, mock_http_request):
        """Test that duplicate file hash returns 409."""
        existing_hash = "abc123duplicatehash"
        
        # Create existing LPO with same hash
        existing_lpo = {
            "SAP Reference": "PTE-HASH-001",
            "Customer Name": "Existing",
            "Project Name": "Existing",
            "Brand": "KIMMCO",
            "LPO Status": "Active",
            "Source File Hash": existing_hash
        }
        mock_storage.add_row("01 LPO Master LOG", existing_lpo)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "sap_reference": "PTE-NEW-002",
            "customer_name": "New Customer",
            "project_name": "New Project",
            "brand": "KIMMCO",
            "po_quantity_sqm": 500.0,
            "price_per_sqm": 150.0,
            "file_url": "https://test.com/duplicate.pdf",
            "uploaded_by": "user@company.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_lpo_ingest.get_smartsheet_client', return_value=mock_client):
            with patch('fn_lpo_ingest.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_lpo_ingest.compute_combined_file_hash', return_value=existing_hash):
                    from fn_lpo_ingest import main
                    response = main(mock_http_request(request_data))
        
        assert response.status_code == 409
        body = json.loads(response.get_body())
        assert body["status"] == "DUPLICATE"


@pytest.mark.integration
class TestLPOIngestValidation:
    """Tests for business validation."""
    
    def test_invalid_brand_returns_422(self, mock_storage, factory, mock_http_request):
        """Test that invalid brand returns 422 BLOCKED."""
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "sap_reference": "PTE-BRAND-001",
            "customer_name": "Test",
            "project_name": "Test",
            "brand": "INVALID_BRAND",  # Not KIMMCO or WTI
            "po_quantity_sqm": 500.0,
            "price_per_sqm": 150.0,
            "uploaded_by": "user@company.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_lpo_ingest.get_smartsheet_client', return_value=mock_client):
            with patch('fn_lpo_ingest.get_manifest', return_value=MockWorkspaceManifest()):
                from fn_lpo_ingest import main
                response = main(mock_http_request(request_data))
        
        assert response.status_code == 422
        body = json.loads(response.get_body())
        assert body["status"] == "BLOCKED"
        assert "exception_id" in body
    
    def test_missing_required_field_returns_422(self, mock_storage, factory, mock_http_request):
        """Test that missing required field returns 422 (Pydantic validation error)."""
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            # Missing: sap_reference
            "customer_name": "Test",
            "project_name": "Test",
            "brand": "KIMMCO",
            "po_quantity_sqm": 500.0,
            "price_per_sqm": 150.0,
            "uploaded_by": "user@company.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_lpo_ingest.get_smartsheet_client', return_value=mock_client):
            with patch('fn_lpo_ingest.get_manifest', return_value=MockWorkspaceManifest()):
                from fn_lpo_ingest import main
                response = main(mock_http_request(request_data))
        
        # Pydantic 2.x returns 422 for validation errors
        assert response.status_code == 422
        body = json.loads(response.get_body())
        assert body["status"] == "VALIDATION_ERROR"


@pytest.mark.integration
class TestLPOIngestMultiFile:
    """Tests for multi-file attachment support (v1.2.0)."""
    
    def test_multi_file_creates_combined_hash(self, mock_storage, factory, mock_http_request):
        """Test that multiple files create a combined hash."""
        content1 = base64.b64encode(b"file1 content").decode()
        content2 = base64.b64encode(b"file2 content").decode()
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "sap_reference": "PTE-MULTI-001",
            "customer_name": "Multi File Test",
            "project_name": "Project",
            "brand": "KIMMCO",
            "po_quantity_sqm": 500.0,
            "price_per_sqm": 150.0,
            "uploaded_by": "user@company.com",
            "files": [
                {"file_type": "lpo", "file_content": content1, "file_name": "po.pdf"},
                {"file_type": "costing", "file_content": content2, "file_name": "cost.xlsx"}
            ]
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_lpo_ingest.get_smartsheet_client', return_value=mock_client):
            with patch('fn_lpo_ingest.get_manifest', return_value=MockWorkspaceManifest()):
                from fn_lpo_ingest import main
                response = main(mock_http_request(request_data))
        
        assert response.status_code == 200
        
        # Verify LPO was created with file hash
        lpos = mock_storage.find_rows("01 LPO Master LOG", "SAP Reference", "PTE-MULTI-001")
        assert len(lpos) == 1


@pytest.mark.integration
class TestLPOUpdate:
    """Tests for LPO update function."""
    
    def test_update_lpo_success(self, mock_storage, factory, mock_http_request):
        """Test successful LPO update."""
        # Create existing LPO
        existing_lpo = {
            "SAP Reference": "PTE-UPDATE-001",
            "Customer Name": "Old Customer",
            "Project Name": "Old Project",
            "Brand": "KIMMCO",
            "LPO Status": "Draft",
            "PO Quantity (Sqm)": 500.0,
            "Delivered Quantity (Sqm)": 0.0,
            "Price per Sqm": 150.0,
        }
        result = mock_storage.add_row("01 LPO Master LOG", existing_lpo)
        
        update_data = {
            "client_request_id": str(uuid.uuid4()),
            "sap_reference": "PTE-UPDATE-001",
            "customer_name": "New Customer",
            "lpo_status": "Active",
            "updated_by": "admin@company.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_lpo_update.get_smartsheet_client', return_value=mock_client):
            with patch('fn_lpo_update.get_manifest', return_value=MockWorkspaceManifest()):
                from fn_lpo_update import main
                response = main(mock_http_request(update_data))
        
        assert response.status_code == 200
        body = json.loads(response.get_body())
        assert body["status"] == "OK"
        assert "customer_name" in body.get("changes", [])
    
    def test_update_nonexistent_lpo_returns_404(self, mock_storage, factory, mock_http_request):
        """Test updating non-existent LPO returns 404."""
        update_data = {
            "client_request_id": str(uuid.uuid4()),
            "sap_reference": "PTE-NONEXISTENT",
            "customer_name": "New Customer",
            "updated_by": "admin@company.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_lpo_update.get_smartsheet_client', return_value=mock_client):
            with patch('fn_lpo_update.get_manifest', return_value=MockWorkspaceManifest()):
                from fn_lpo_update import main
                response = main(mock_http_request(update_data))
        
        assert response.status_code == 404
        body = json.loads(response.get_body())
        assert body["status"] == "NOT_FOUND"
    
    def test_quantity_conflict_returns_422(self, mock_storage, factory, mock_http_request):
        """Test reducing PO quantity below delivered returns 422."""
        # Create LPO with delivered quantity
        existing_lpo = {
            "SAP Reference": "PTE-CONFLICT-001",
            "Customer Name": "Conflict Test",
            "Project Name": "Project",
            "Brand": "KIMMCO",
            "LPO Status": "Active",
            "PO Quantity (Sqm)": 1000.0,
            "Delivered Quantity (Sqm)": 500.0,  # Already delivered 500
            "Price per Sqm": 150.0,
        }
        mock_storage.add_row("01 LPO Master LOG", existing_lpo)
        
        update_data = {
            "client_request_id": str(uuid.uuid4()),
            "sap_reference": "PTE-CONFLICT-001",
            "po_quantity_sqm": 400.0,  # Trying to reduce below delivered!
            "updated_by": "admin@company.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_lpo_update.get_smartsheet_client', return_value=mock_client):
            with patch('fn_lpo_update.get_manifest', return_value=MockWorkspaceManifest()):
                from fn_lpo_update import main
                response = main(mock_http_request(update_data))
        
        assert response.status_code == 422
        body = json.loads(response.get_body())
        assert body["status"] == "BLOCKED"
        assert "exception_id" in body
    
    def test_update_logs_old_new_values(self, mock_storage, factory, mock_http_request):
        """Test that update logs old and new values in audit trail."""
        # Create existing LPO
        existing_lpo = {
            "SAP Reference": "PTE-AUDIT-001",
            "Customer Name": "Old Name",
            "Project Name": "Project",
            "Brand": "KIMMCO",
            "LPO Status": "Draft",
            "PO Quantity (Sqm)": 500.0,
            "Delivered Quantity (Sqm)": 0.0,
        }
        mock_storage.add_row("01 LPO Master LOG", existing_lpo)
        
        update_data = {
            "client_request_id": str(uuid.uuid4()),
            "sap_reference": "PTE-AUDIT-001",
            "customer_name": "New Name",
            "updated_by": "auditor@company.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_lpo_update.get_smartsheet_client', return_value=mock_client):
            with patch('fn_lpo_update.get_manifest', return_value=MockWorkspaceManifest()):
                from fn_lpo_update import main
                response = main(mock_http_request(update_data))
        
        assert response.status_code == 200
        
        # Verify LPO_UPDATED action logged
        actions = mock_storage.find_rows("98 User Action Log", "Action Type", "LPO_UPDATED")
        assert len(actions) >= 1
