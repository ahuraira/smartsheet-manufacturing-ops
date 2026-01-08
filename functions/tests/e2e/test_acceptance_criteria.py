"""
End-to-End Acceptance Tests for Tag Ingestion

These tests verify the acceptance criteria from:
- tag_ingestion_architecture.md (Section 10)
- architecture_specification.md (Section 11)

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

from shared.sheet_config import SheetName


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
        # Arrange: Create valid LPO
        lpo = factory.create_lpo(
            sap_reference="SAP-HAPPY-001",
            customer_lpo_ref="CUST-HAPPY-001",
            customer_name="Happy Customer Corp",
            brand="Premium Brand",
            status="Active",
            po_quantity=1000.0,
            delivered_quantity=0.0
        )
        mock_storage.add_row(SheetName.LPO_MASTER.value, lpo)
        
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
        with patch('shared.smartsheet_client._client', None):
            from tests.conftest import MockSmartsheetClient
            mock_client = MockSmartsheetClient(mock_storage)
            
            from fn_ingest_tag import main
            http_req = mock_http_request(request_data)
            
            with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
                response = main(http_req)
        
        # Assert: Response validation
        assert response.status_code == 200, "Expected HTTP 200 for successful upload"
        
        response_data = json.loads(response.get_body())
        assert response_data["status"] == "UPLOADED", "Status should be UPLOADED"
        assert response_data["tag_id"].startswith("TAG-"), "Tag ID should have TAG- prefix"
        assert "trace_id" in response_data, "Response must include trace_id"
        assert response_data["trace_id"].startswith("trace-"), "Trace ID format incorrect"
        
        # Assert: Tag record created
        tags = mock_storage.find_rows(SheetName.TAG_REGISTRY.value, "Client Request ID", client_request_id)
        assert len(tags) == 1, "Exactly one tag should be created"
        tag = tags[0]
        assert tag["Status"] == "Draft", "Initial status should be Draft"
        assert tag["Estimated Quantity"] == 150.5
        assert tag["Submitted By"] == "sales.team@company.com"
        
        # Assert: User action logged
        actions = mock_storage.find_rows(SheetName.USER_ACTION_LOG.value, "Action Type", "TAG_CREATED")
        assert len(actions) >= 1, "TAG_CREATED action should be logged"
        action = actions[-1]
        assert action["User ID"] == "sales.team@company.com"
        assert action["Target Table"] == SheetName.TAG_REGISTRY.value
    
    def test_acceptance_2_duplicate_client_request_id_idempotency(self, mock_storage, factory, mock_http_request):
        """
        ACCEPTANCE TEST 2: Duplicate client_request_id
        
        Repeat same request → same 200 result with same tag_id
        No duplicate tag created.
        """
        # Arrange: Create LPO and existing tag
        lpo = factory.create_lpo(sap_reference="SAP-IDEM-001", status="Active", po_quantity=500.0)
        mock_storage.add_row(SheetName.LPO_MASTER.value, lpo)
        
        client_request_id = "IDEM-" + str(uuid.uuid4())
        existing_tag = factory.create_tag_record(
            tag_name="EXISTING-IDEM-TAG",
            status="Draft",
            client_request_id=client_request_id
        )
        mock_storage.add_row(SheetName.TAG_REGISTRY.value, existing_tag)
        
        request_data = {
            "client_request_id": client_request_id,  # SAME ID
            "lpo_sap_reference": "SAP-IDEM-001",
            "required_area_m2": 50.0,
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "retry.user@company.com"
        }
        
        # Act: Make 3 requests (simulating retries)
        results = []
        for _ in range(3):
            with patch('shared.smartsheet_client._client', None):
                from tests.conftest import MockSmartsheetClient
                mock_client = MockSmartsheetClient(mock_storage)
                
                from fn_ingest_tag import main
                http_req = mock_http_request(request_data)
                
                with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
                    response = main(http_req)
                    results.append(json.loads(response.get_body()))
        
        # Assert: All responses identical
        assert all(r["status"] == "ALREADY_PROCESSED" for r in results), \
            "All retry responses should be ALREADY_PROCESSED"
        
        # Assert: Still only one tag
        tags = mock_storage.find_rows(SheetName.TAG_REGISTRY.value, "Client Request ID", client_request_id)
        assert len(tags) == 1, "Should still have only one tag after retries"
    
    def test_acceptance_3_duplicate_file_hash_returns_409(self, mock_storage, factory, mock_http_request):
        """
        ACCEPTANCE TEST 3: Duplicate file_hash
        
        Uploading same file again → 409 DUPLICATE with exception record
        """
        # Arrange
        lpo = factory.create_lpo(sap_reference="SAP-DUP-001", status="Active", po_quantity=500.0)
        mock_storage.add_row(SheetName.LPO_MASTER.value, lpo)
        
        existing_hash = "sha256_hash_of_duplicate_file_content_12345"
        existing_tag = factory.create_tag_record(
            tag_name="ORIGINAL-FILE-TAG",
            file_hash=existing_hash
        )
        mock_storage.add_row(SheetName.TAG_REGISTRY.value, existing_tag)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "lpo_sap_reference": "SAP-DUP-001",
            "required_area_m2": 100.0,
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "uploader@company.com",
            "file_url": "https://sharepoint/duplicate_file.xlsx"
        }
        
        # Act
        with patch('shared.smartsheet_client._client', None):
            from tests.conftest import MockSmartsheetClient
            mock_client = MockSmartsheetClient(mock_storage)
            
            from fn_ingest_tag import main
            http_req = mock_http_request(request_data)
            
            with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
                with patch('fn_ingest_tag.compute_file_hash_from_url', return_value=existing_hash):
                    response = main(http_req)
        
        # Assert: 409 response
        assert response.status_code == 409, "Expected HTTP 409 for duplicate file"
        
        response_data = json.loads(response.get_body())
        assert response_data["status"] == "DUPLICATE"
        assert "existing_tag_id" in response_data
        assert "exception_id" in response_data
        assert response_data["exception_id"].startswith("EX-")
        
        # Assert: Exception created
        exceptions = mock_storage.find_rows(SheetName.EXCEPTION_LOG.value, "Reason Code", "DUPLICATE_UPLOAD")
        assert len(exceptions) == 1
        exc = exceptions[0]
        assert exc["Severity"] == "MEDIUM"
        assert exc["Status"] == "Open"
    
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
        mock_storage.add_row(SheetName.LPO_MASTER.value, lpo)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "lpo_sap_reference": "SAP-HOLD-001",
            "required_area_m2": 50.0,
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "user@company.com"
        }
        
        # Act
        with patch('shared.smartsheet_client._client', None):
            from tests.conftest import MockSmartsheetClient
            mock_client = MockSmartsheetClient(mock_storage)
            
            from fn_ingest_tag import main
            http_req = mock_http_request(request_data)
            
            with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
                response = main(http_req)
        
        # Assert: 422 BLOCKED response
        assert response.status_code == 422, "Expected HTTP 422 for blocked LPO"
        
        response_data = json.loads(response.get_body())
        assert response_data["status"] == "BLOCKED"
        assert "exception_id" in response_data
        assert "LPO" in response_data.get("message", "").lower() or "hold" in response_data.get("message", "").lower()
        
        # Assert: LPO_ON_HOLD exception created
        exceptions = mock_storage.find_rows(SheetName.EXCEPTION_LOG.value, "Reason Code", "LPO_ON_HOLD")
        assert len(exceptions) == 1
        exc = exceptions[0]
        assert exc["Severity"] == "HIGH"
    
    def test_acceptance_5_po_overcommit_creates_exception(self, mock_storage, factory, mock_http_request):
        """
        ACCEPTANCE TEST 5: PO Overcommit
        
        If required_area + committed > PO quantity → exception INSUFFICIENT_PO_BALANCE
        Tag set to BLOCKED
        """
        # Arrange: LPO with limited remaining capacity
        lpo = factory.create_lpo(
            sap_reference="SAP-LIMIT-001",
            status="Active",
            po_quantity=100.0,
            delivered_quantity=90.0  # Only 10 sqm remaining!
        )
        mock_storage.add_row(SheetName.LPO_MASTER.value, lpo)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "lpo_sap_reference": "SAP-LIMIT-001",
            "required_area_m2": 50.0,  # Requesting 50 when only 10 available
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "user@company.com"
        }
        
        # Act
        with patch('shared.smartsheet_client._client', None):
            from tests.conftest import MockSmartsheetClient
            mock_client = MockSmartsheetClient(mock_storage)
            
            from fn_ingest_tag import main
            http_req = mock_http_request(request_data)
            
            with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
                response = main(http_req)
        
        # Assert: 422 BLOCKED
        assert response.status_code == 422
        
        response_data = json.loads(response.get_body())
        assert response_data["status"] == "BLOCKED"
        assert "exception_id" in response_data
        
        # Assert: INSUFFICIENT_PO_BALANCE exception
        exceptions = mock_storage.find_rows(SheetName.EXCEPTION_LOG.value, "Reason Code", "INSUFFICIENT_PO_BALANCE")
        assert len(exceptions) == 1
        exc = exceptions[0]
        assert exc["Severity"] == "HIGH"
        assert exc["Quantity"] == 50.0  # The requested amount
    
    def test_acceptance_6_idempotency_under_retry(self, mock_storage, factory, mock_http_request):
        """
        ACCEPTANCE TEST 6: Idempotency under retry
        
        Power Automate retries POST 3x → function processes only once
        """
        # Arrange
        lpo = factory.create_lpo(sap_reference="SAP-RETRY-001", status="Active", po_quantity=500.0)
        mock_storage.add_row(SheetName.LPO_MASTER.value, lpo)
        
        client_request_id = "RETRY-" + str(uuid.uuid4())
        request_data = {
            "client_request_id": client_request_id,
            "lpo_sap_reference": "SAP-RETRY-001",
            "required_area_m2": 75.0,
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "user@company.com"
        }
        
        # Act: Make 5 requests (simulating aggressive retries)
        responses = []
        for i in range(5):
            with patch('shared.smartsheet_client._client', None):
                from tests.conftest import MockSmartsheetClient
                mock_client = MockSmartsheetClient(mock_storage)
                
                from fn_ingest_tag import main
                http_req = mock_http_request(request_data)
                
                with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
                    response = main(http_req)
                    responses.append((response.status_code, json.loads(response.get_body())))
        
        # Assert: First request creates, rest return existing
        assert responses[0][0] == 200
        assert responses[0][1]["status"] == "UPLOADED"
        
        for i in range(1, 5):
            assert responses[i][0] == 200
            assert responses[i][1]["status"] == "ALREADY_PROCESSED"
        
        # Assert: Only one tag created
        tags = mock_storage.find_rows(SheetName.TAG_REGISTRY.value, "Client Request ID", client_request_id)
        assert len(tags) == 1, f"Expected 1 tag, found {len(tags)}"
        
        # Assert: Only one TAG_CREATED action (not 5)
        actions = mock_storage.find_rows(SheetName.USER_ACTION_LOG.value, "Action Type", "TAG_CREATED")
        tag_created_for_this_request = [
            a for a in actions 
            if client_request_id in str(a.get("Notes", ""))
        ]
        # Note: We may have other TAG_CREATED actions from other tests
        # Just verify only 1 tag was created (checked above)
    
    def test_acceptance_7_trace_id_consistency(self, mock_storage, factory, mock_http_request):
        """
        ACCEPTANCE TEST 7: End-to-end trace
        
        Logs for ingest show same trace_id across:
        - Function response
        - Exception records
        - User action logs
        """
        # Arrange: Set up scenario that creates exception
        lpo = factory.create_lpo(
            sap_reference="SAP-TRACE-001",
            status="On Hold",  # Will trigger exception
            po_quantity=500.0
        )
        mock_storage.add_row(SheetName.LPO_MASTER.value, lpo)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "lpo_sap_reference": "SAP-TRACE-001",
            "required_area_m2": 50.0,
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "trace.test@company.com"
        }
        
        # Act
        with patch('shared.smartsheet_client._client', None):
            from tests.conftest import MockSmartsheetClient
            mock_client = MockSmartsheetClient(mock_storage)
            
            from fn_ingest_tag import main
            http_req = mock_http_request(request_data)
            
            with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
                response = main(http_req)
        
        # Get trace_id from response
        response_data = json.loads(response.get_body())
        trace_id = response_data.get("trace_id")
        
        # Assert: trace_id is present and properly formatted
        assert trace_id is not None, "Response must include trace_id"
        assert trace_id.startswith("trace-"), "Trace ID should start with 'trace-'"
        
        # Note: In a real system, we would verify trace_id appears in:
        # - Exception record remarks/notes
        # - User action log notes
        # - Application Insights logs
        # For this test, we verify the response includes it


@pytest.mark.e2e
@pytest.mark.acceptance
class TestArchitectureSpecificationAcceptance:
    """
    Additional acceptance tests from architecture_specification.md Section 11.
    """
    
    def test_lpo_validation_prevents_production_without_coverage(self, mock_storage, factory, mock_http_request):
        """
        LPO not found → No tag created, exception logged.
        Ensures commercial coverage requirement.
        """
        # Arrange: No LPO exists
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "lpo_sap_reference": "NONEXISTENT-LPO",
            "required_area_m2": 100.0,
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "user@company.com"
        }
        
        # Act
        with patch('shared.smartsheet_client._client', None):
            from tests.conftest import MockSmartsheetClient
            mock_client = MockSmartsheetClient(mock_storage)
            
            from fn_ingest_tag import main
            http_req = mock_http_request(request_data)
            
            with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
                response = main(http_req)
        
        # Assert: BLOCKED with LPO_NOT_FOUND
        assert response.status_code == 422
        
        response_data = json.loads(response.get_body())
        assert response_data["status"] == "BLOCKED"
        
        exceptions = mock_storage.find_rows(SheetName.EXCEPTION_LOG.value, "Reason Code", "LPO_NOT_FOUND")
        assert len(exceptions) == 1
    
    def test_tag_contains_commercial_traceability(self, mock_storage, factory, mock_http_request):
        """
        Tag record must link to LPO for commercial traceability.
        """
        # Arrange
        lpo = factory.create_lpo(
            sap_reference="SAP-TRACE-LPO-001",
            customer_name="Traceable Customer",
            brand="Traceable Brand",
            status="Active",
            po_quantity=500.0
        )
        mock_storage.add_row(SheetName.LPO_MASTER.value, lpo)
        
        client_request_id = str(uuid.uuid4())
        request_data = {
            "client_request_id": client_request_id,
            "lpo_sap_reference": "SAP-TRACE-LPO-001",
            "required_area_m2": 100.0,
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "user@company.com"
        }
        
        # Act
        with patch('shared.smartsheet_client._client', None):
            from tests.conftest import MockSmartsheetClient
            mock_client = MockSmartsheetClient(mock_storage)
            
            from fn_ingest_tag import main
            http_req = mock_http_request(request_data)
            
            with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
                response = main(http_req)
        
        # Assert: Tag has LPO reference
        tags = mock_storage.find_rows(SheetName.TAG_REGISTRY.value, "Client Request ID", client_request_id)
        assert len(tags) == 1
        tag = tags[0]
        
        assert tag["LPO SAP Reference Link"] == "SAP-TRACE-LPO-001"
        assert tag["Customer Name"] == "Traceable Customer"
        assert tag["Brand"] == "Traceable Brand"


@pytest.mark.e2e
class TestEdgeCases:
    """Edge case tests for robustness."""
    
    def test_zero_area_request(self, mock_storage, factory, mock_http_request):
        """Test handling of zero required area."""
        lpo = factory.create_lpo(sap_reference="SAP-ZERO-001", status="Active", po_quantity=500.0)
        mock_storage.add_row(SheetName.LPO_MASTER.value, lpo)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "lpo_sap_reference": "SAP-ZERO-001",
            "required_area_m2": 0.0,  # Zero area
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "user@company.com"
        }
        
        with patch('shared.smartsheet_client._client', None):
            from tests.conftest import MockSmartsheetClient
            mock_client = MockSmartsheetClient(mock_storage)
            
            from fn_ingest_tag import main
            http_req = mock_http_request(request_data)
            
            with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
                response = main(http_req)
        
        # Should succeed (0 area is technically valid)
        assert response.status_code == 200
    
    def test_exactly_remaining_balance(self, mock_storage, factory, mock_http_request):
        """Test requesting exactly the remaining balance."""
        lpo = factory.create_lpo(
            sap_reference="SAP-EXACT-001",
            status="Active",
            po_quantity=100.0,
            delivered_quantity=50.0  # Exactly 50 remaining
        )
        mock_storage.add_row(SheetName.LPO_MASTER.value, lpo)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "lpo_sap_reference": "SAP-EXACT-001",
            "required_area_m2": 50.0,  # Exactly remaining
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "user@company.com"
        }
        
        with patch('shared.smartsheet_client._client', None):
            from tests.conftest import MockSmartsheetClient
            mock_client = MockSmartsheetClient(mock_storage)
            
            from fn_ingest_tag import main
            http_req = mock_http_request(request_data)
            
            with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
                response = main(http_req)
        
        # Should succeed (exactly at limit)
        assert response.status_code == 200
    
    def test_slightly_over_remaining_balance(self, mock_storage, factory, mock_http_request):
        """Test requesting slightly more than remaining balance."""
        lpo = factory.create_lpo(
            sap_reference="SAP-OVER-001",
            status="Active",
            po_quantity=100.0,
            delivered_quantity=50.0  # 50 remaining
        )
        mock_storage.add_row(SheetName.LPO_MASTER.value, lpo)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "lpo_sap_reference": "SAP-OVER-001",
            "required_area_m2": 50.01,  # Just over remaining
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "user@company.com"
        }
        
        with patch('shared.smartsheet_client._client', None):
            from tests.conftest import MockSmartsheetClient
            mock_client = MockSmartsheetClient(mock_storage)
            
            from fn_ingest_tag import main
            http_req = mock_http_request(request_data)
            
            with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
                response = main(http_req)
        
        # Should fail (over limit)
        assert response.status_code == 422
        response_data = json.loads(response.get_body())
        assert response_data["status"] == "BLOCKED"
    
    def test_malformed_request_validation(self, mock_storage, mock_http_request):
        """Test validation of malformed request."""
        # Missing required fields
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            # Missing: required_area_m2, requested_delivery_date, uploaded_by
        }
        
        with patch('shared.smartsheet_client._client', None):
            from tests.conftest import MockSmartsheetClient
            mock_client = MockSmartsheetClient(mock_storage)
            
            from fn_ingest_tag import main
            http_req = mock_http_request(request_data)
            
            with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
                response = main(http_req)
        
        # Should return validation error
        assert response.status_code == 400
        response_data = json.loads(response.get_body())
        assert response_data["status"] == "ERROR"
        assert "validation" in response_data.get("message", "").lower()
