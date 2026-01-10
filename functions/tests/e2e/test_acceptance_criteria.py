"""
End-to-End Acceptance Tests for Tag Ingestion

These tests verify the acceptance criteria from:
- tag_ingestion_architecture.md (Section 10)
- architecture_specification.md (Section 11)

Updated for v1.1.0 with manifest-based architecture.

Each test corresponds to a specific acceptance criterion that MUST pass
for the system to meet SOTA quality standards.
"""

import pytest
import json
import uuid
import hashlib
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


@pytest.mark.e2e
@pytest.mark.acceptance
class TestAcceptanceCriteria:
    """
    Acceptance tests from tag_ingestion_architecture.md Section 10.
    
    1. Happy path — upload unique file → UPLOADED
    2. Duplicate client_request_id → same 200 result (idempotency)
    3. Duplicate file_hash → 409 DUPLICATE with exception
    4. LPO on hold → 422 BLOCKED with exception
    5. PO overcommit → exception INSUFFICIENT_PO_BALANCE, BLOCKED
    6. Idempotency under retry → processes only once
    7. End-to-end trace — same trace_id across all components
    """
    
    def test_acceptance_1_happy_path_unique_file(self, mock_storage, factory, mock_http_request):
        """
        ACCEPTANCE TEST 1: Happy Path
        
        Upload unique file → function returns 'UPLOADED'
        - tag_sheet inserted with correct status
        - user_action_history inserted
        - Response includes tag_id and trace_id
        """
        # Arrange: Create valid LPO (use physical sheet name)
        lpo = factory.create_lpo(
            sap_reference="SAP-HAPPY-001",
            customer_lpo_ref="CUST-HAPPY-001",
            customer_name="Happy Customer Corp",
            brand="Premium Brand",
            status="Active",
            po_quantity=1000.0,
            delivered_quantity=0.0
        )
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        # Create unique request
        client_request_id = str(uuid.uuid4())
        request_data = {
            "client_request_id": client_request_id,
            "lpo_sap_reference": "SAP-HAPPY-001",
            "required_area_m2": 150.5,
            "requested_delivery_date": "2026-02-15",
            "uploaded_by": "sales.team@company.com",
            "tag_name": "TAG-HAPPY-REV1"
        }
        
        # Act
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    from fn_ingest_tag import main
                    http_req = mock_http_request(request_data)
                    response = main(http_req)
        
        # Assert: Response validation
        assert response.status_code == 200, "Expected HTTP 200 for successful upload"
        
        response_data = json.loads(response.get_body())
        assert response_data["status"] == "UPLOADED", "Status should be UPLOADED"
        assert response_data["tag_id"].startswith("TAG-"), "Tag ID should have TAG- prefix"
        assert "trace_id" in response_data, "Response must include trace_id"
        assert response_data["trace_id"].startswith("trace-"), "Trace ID format incorrect"
        
        # Assert: Tag record created (use physical sheet name)
        tags = mock_storage.find_rows("Tag Sheet Registry", "Client Request ID", client_request_id)
        assert len(tags) == 1, "Exactly one tag should be created"
        tag = tags[0]
        assert tag["Status"] == "Validate", "v1.1.0: Initial status should be Validate"
        assert tag["Estimated Quantity"] == 150.5
        assert tag["Submitted By"] == "sales.team@company.com"
        
        # Assert: User action logged
        actions = mock_storage.find_rows("98 User Action Log", "Action Type", "TAG_CREATED")
        assert len(actions) >= 1, "TAG_CREATED action should be logged"
    
    def test_acceptance_2_duplicate_client_request_id_idempotency(self, mock_storage, factory, mock_http_request):
        """
        ACCEPTANCE TEST 2: Duplicate client_request_id
        
        Repeat same request → same 200 result with same tag_id
        No duplicate tag created.
        """
        # Arrange: Create LPO and existing tag
        lpo = factory.create_lpo(sap_reference="SAP-IDEM-001", status="Active", po_quantity=500.0)
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        client_request_id = "IDEM-" + str(uuid.uuid4())
        existing_tag = factory.create_tag_record(
            tag_name="EXISTING-IDEM-TAG",
            status="Validate",
            client_request_id=client_request_id
        )
        mock_storage.add_row("Tag Sheet Registry", existing_tag)
        
        request_data = {
            "client_request_id": client_request_id,  # SAME ID
            "lpo_sap_reference": "SAP-IDEM-001",
            "required_area_m2": 50.0,
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "retry.user@company.com"
        }
        
        # Act
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    from fn_ingest_tag import main
                    http_req = mock_http_request(request_data)
                    response = main(http_req)
                    result = json.loads(response.get_body())
        
        # Assert: Response is ALREADY_PROCESSED
        assert result["status"] == "ALREADY_PROCESSED", "Should return ALREADY_PROCESSED"
        
        # Assert: Still only one tag
        tags = mock_storage.find_rows("Tag Sheet Registry", "Client Request ID", client_request_id)
        assert len(tags) == 1, "Should still have only one tag"
    
    def test_acceptance_3_duplicate_file_hash_returns_409(self, mock_storage, factory, mock_http_request):
        """
        ACCEPTANCE TEST 3: Duplicate file_hash
        
        Uploading same file again → 409 DUPLICATE with exception record
        """
        # Arrange
        lpo = factory.create_lpo(sap_reference="SAP-DUP-001", status="Active", po_quantity=500.0)
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        existing_hash = "sha256_hash_of_duplicate_file_content_12345"
        existing_tag = factory.create_tag_record(
            tag_name="ORIGINAL-FILE-TAG",
            file_hash=existing_hash
        )
        mock_storage.add_row("Tag Sheet Registry", existing_tag)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "lpo_sap_reference": "SAP-DUP-001",
            "required_area_m2": 100.0,
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "uploader@company.com",
            "file_url": "https://sharepoint/duplicate_file.xlsx"
        }
        
        # Act
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    with patch('fn_ingest_tag.compute_file_hash_from_url', return_value=existing_hash):
                        from fn_ingest_tag import main
                        http_req = mock_http_request(request_data)
                        response = main(http_req)
        
        # Assert: 409 response
        assert response.status_code == 409, "Expected HTTP 409 for duplicate file"
        
        response_data = json.loads(response.get_body())
        assert response_data["status"] == "DUPLICATE"
        assert "exception_id" in response_data
        assert response_data["exception_id"].startswith("EX-")
        
        # Assert: Exception created
        exceptions = mock_storage.find_rows("99 Exception Log", "Reason Code", "DUPLICATE_UPLOAD")
        assert len(exceptions) == 1
    
    def test_acceptance_4_lpo_on_hold_returns_blocked(self, mock_storage, factory, mock_http_request):
        """
        ACCEPTANCE TEST 4: LPO on hold
        
        Ingest returns 422 BLOCKED with exception row
        """
        # Arrange: LPO with On Hold status
        lpo = factory.create_lpo(
            sap_reference="SAP-HOLD-001",
            status="On Hold",  # BLOCKED!
            po_quantity=500.0
        )
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "lpo_sap_reference": "SAP-HOLD-001",
            "required_area_m2": 50.0,
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "user@company.com"
        }
        
        # Act
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    from fn_ingest_tag import main
                    http_req = mock_http_request(request_data)
                    response = main(http_req)
        
        # Assert: 422 BLOCKED response
        assert response.status_code == 422, "Expected HTTP 422 for blocked LPO"
        
        response_data = json.loads(response.get_body())
        assert response_data["status"] == "BLOCKED"
        assert "exception_id" in response_data
    
    def test_acceptance_5_po_overcommit_creates_exception(self, mock_storage, factory, mock_http_request):
        """
        ACCEPTANCE TEST 5: PO Overcommit
        
        If required_area + committed > PO quantity → exception INSUFFICIENT_PO_BALANCE
        """
        # Arrange: LPO with limited remaining capacity
        lpo = factory.create_lpo(
            sap_reference="SAP-LIMIT-001",
            status="Active",
            po_quantity=100.0,
            delivered_quantity=90.0  # Only 10 sqm remaining!
        )
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "lpo_sap_reference": "SAP-LIMIT-001",
            "required_area_m2": 50.0,  # Requesting 50 when only 10 available
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "user@company.com"
        }
        
        # Act
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    from fn_ingest_tag import main
                    http_req = mock_http_request(request_data)
                    response = main(http_req)
        
        # Assert: 422 BLOCKED
        assert response.status_code == 422
        
        response_data = json.loads(response.get_body())
        assert response_data["status"] == "BLOCKED"
        assert "exception_id" in response_data
    
    def test_acceptance_6_idempotency_under_retry(self, mock_storage, factory, mock_http_request):
        """
        ACCEPTANCE TEST 6: Idempotency under retry
        
        Power Automate retries POST 3x → function processes only once
        """
        # Arrange
        lpo = factory.create_lpo(sap_reference="SAP-RETRY-001", status="Active", po_quantity=500.0)
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        client_request_id = "RETRY-" + str(uuid.uuid4())
        request_data = {
            "client_request_id": client_request_id,
            "lpo_sap_reference": "SAP-RETRY-001",
            "required_area_m2": 75.0,
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "user@company.com"
        }
        
        # Act: First request creates
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    from fn_ingest_tag import main
                    http_req = mock_http_request(request_data)
                    response1 = main(http_req)
        
        result1 = json.loads(response1.get_body())
        assert result1["status"] == "UPLOADED", "First request should create tag"
        
        # Act: Retry should return ALREADY_PROCESSED
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    http_req = mock_http_request(request_data)
                    response2 = main(http_req)
        
        result2 = json.loads(response2.get_body())
        assert result2["status"] == "ALREADY_PROCESSED", "Retry should return ALREADY_PROCESSED"
        
        # Assert: Only one tag created
        tags = mock_storage.find_rows("Tag Sheet Registry", "Client Request ID", client_request_id)
        assert len(tags) == 1, f"Expected 1 tag, found {len(tags)}"
    
    def test_acceptance_7_trace_id_consistency(self, mock_storage, factory, mock_http_request):
        """
        ACCEPTANCE TEST 7: End-to-end trace
        
        Response includes trace_id for correlation.
        """
        # Arrange
        lpo = factory.create_lpo(sap_reference="SAP-TRACE-001", status="Active", po_quantity=500.0)
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "lpo_sap_reference": "SAP-TRACE-001",
            "required_area_m2": 50.0,
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "trace.test@company.com"
        }
        
        # Act
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    from fn_ingest_tag import main
                    http_req = mock_http_request(request_data)
                    response = main(http_req)
        
        # Get trace_id from response
        response_data = json.loads(response.get_body())
        trace_id = response_data.get("trace_id")
        
        # Assert: trace_id is present and properly formatted
        assert trace_id is not None, "Response must include trace_id"
        assert trace_id.startswith("trace-"), "Trace ID should start with 'trace-'"


@pytest.mark.e2e
@pytest.mark.acceptance
class TestArchitectureSpecificationAcceptance:
    """Additional acceptance tests from architecture_specification.md Section 11."""
    
    def test_lpo_validation_prevents_production_without_coverage(self, mock_storage, factory, mock_http_request):
        """LPO not found → No tag created, exception logged."""
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "lpo_sap_reference": "NONEXISTENT-LPO",
            "required_area_m2": 100.0,
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "user@company.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    from fn_ingest_tag import main
                    http_req = mock_http_request(request_data)
                    response = main(http_req)
        
        assert response.status_code == 422
        response_data = json.loads(response.get_body())
        assert response_data["status"] == "BLOCKED"
    
    def test_tag_contains_commercial_traceability(self, mock_storage, factory, mock_http_request):
        """Tag record must link to LPO for commercial traceability."""
        lpo = factory.create_lpo(
            sap_reference="SAP-TRACE-LPO-001",
            customer_name="Traceable Customer",
            brand="Traceable Brand",
            status="Active",
            po_quantity=500.0
        )
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        client_request_id = str(uuid.uuid4())
        request_data = {
            "client_request_id": client_request_id,
            "lpo_sap_reference": "SAP-TRACE-LPO-001",
            "required_area_m2": 100.0,
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "user@company.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    from fn_ingest_tag import main
                    http_req = mock_http_request(request_data)
                    response = main(http_req)
        
        # Assert: Tag has LPO reference
        tags = mock_storage.find_rows("Tag Sheet Registry", "Client Request ID", client_request_id)
        assert len(tags) == 1
        tag = tags[0]
        
        # v1.1.0: Check LPO reference is stored
        assert tag.get("LPO SAP Reference Link") is not None or tag.get("Customer Name") is not None


@pytest.mark.e2e
class TestEdgeCases:
    """Edge case tests for robustness."""
    
    def test_zero_area_request(self, mock_storage, factory, mock_http_request):
        """Test handling of zero required area."""
        lpo = factory.create_lpo(sap_reference="SAP-ZERO-001", status="Active", po_quantity=500.0)
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "lpo_sap_reference": "SAP-ZERO-001",
            "required_area_m2": 0.0,
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "user@company.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    from fn_ingest_tag import main
                    http_req = mock_http_request(request_data)
                    response = main(http_req)
        
        assert response.status_code == 200
    
    def test_exactly_remaining_balance(self, mock_storage, factory, mock_http_request):
        """Test requesting exactly the remaining balance."""
        lpo = factory.create_lpo(
            sap_reference="SAP-EXACT-001",
            status="Active",
            po_quantity=100.0,
            delivered_quantity=50.0
        )
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "lpo_sap_reference": "SAP-EXACT-001",
            "required_area_m2": 50.0,
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "user@company.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    from fn_ingest_tag import main
                    http_req = mock_http_request(request_data)
                    response = main(http_req)
        
        assert response.status_code == 200
    
    def test_slightly_over_remaining_balance(self, mock_storage, factory, mock_http_request):
        """Test requesting slightly more than remaining balance."""
        lpo = factory.create_lpo(
            sap_reference="SAP-OVER-001",
            status="Active",
            po_quantity=100.0,
            delivered_quantity=50.0
        )
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "lpo_sap_reference": "SAP-OVER-001",
            "required_area_m2": 50.01,
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "user@company.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    from fn_ingest_tag import main
                    http_req = mock_http_request(request_data)
                    response = main(http_req)
        
        assert response.status_code == 422
        response_data = json.loads(response.get_body())
        assert response_data["status"] == "BLOCKED"
    
    def test_malformed_request_validation(self, mock_storage, mock_http_request):
        """Test validation of malformed request."""
        request_data = {
            "client_request_id": str(uuid.uuid4()),
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    from fn_ingest_tag import main
                    http_req = mock_http_request(request_data)
                    response = main(http_req)
        
        assert response.status_code == 400
        response_data = json.loads(response.get_body())
        assert response_data["status"] == "ERROR"
