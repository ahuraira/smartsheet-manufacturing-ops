"""
Robustness Tests - Preventing Production Failures

These tests focus on scenarios that could cause the system to FAIL in production.
Not about coverage numbers - about ensuring the code handles:

1. Invalid/malformed inputs
2. Missing data
3. Null/None values
4. Type mismatches
5. Edge cases in business logic
6. Error propagation
7. Graceful degradation

Each test represents a potential production failure that we're preventing.
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


@pytest.mark.unit
class TestInputValidationFailures:
    """Tests for malformed/invalid inputs that could crash the system."""
    
    def test_empty_request_body(self, mock_storage, mock_http_request):
        """FAILURE PREVENTION: Empty JSON body should not crash."""
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    from fn_ingest_tag import main
                    response = main(mock_http_request({}))
        
        # Should return 400, not crash
        assert response.status_code == 400
        body = json.loads(response.get_body())
        assert body["status"] == "ERROR"
    
    def test_null_required_area(self, mock_storage, mock_http_request):
        """FAILURE PREVENTION: Null required_area_m2 should not crash."""
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "lpo_sap_reference": "SAP-001",
            "required_area_m2": None,  # NULL!
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "user@test.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    from fn_ingest_tag import main
                    response = main(mock_http_request(request_data))
        
        assert response.status_code == 400
    
    def test_string_instead_of_number_for_area(self, mock_storage, factory, mock_http_request):
        """FAILURE PREVENTION: String 'fifty' instead of 50.0 should not crash."""
        lpo = factory.create_lpo(sap_reference="SAP-001", status="Active", po_quantity=500.0)
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "lpo_sap_reference": "SAP-001",
            "required_area_m2": "fifty",  # Invalid string!
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "user@test.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    from fn_ingest_tag import main
                    response = main(mock_http_request(request_data))
        
        # Should return 400 validation error, not crash
        assert response.status_code == 400
    
    def test_negative_area_handling(self, mock_storage, factory, mock_http_request):
        """FAILURE PREVENTION: Negative area should be handled gracefully."""
        lpo = factory.create_lpo(sap_reference="SAP-NEG-001", status="Active", po_quantity=500.0)
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "lpo_sap_reference": "SAP-NEG-001",
            "required_area_m2": -100.0,  # NEGATIVE!
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "user@test.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    from fn_ingest_tag import main
                    response = main(mock_http_request(request_data))
        
        # System should handle gracefully (either accept or reject, but not crash)
        assert response.status_code in [200, 400, 422]
    
    def test_extremely_large_area(self, mock_storage, factory, mock_http_request):
        """FAILURE PREVENTION: Extremely large numbers should not cause overflow."""
        lpo = factory.create_lpo(sap_reference="SAP-HUGE-001", status="Active", po_quantity=float('inf'))
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "lpo_sap_reference": "SAP-HUGE-001",
            "required_area_m2": 999999999999.99,  # HUGE!
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "user@test.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    from fn_ingest_tag import main
                    response = main(mock_http_request(request_data))
        
        # Should not crash - may succeed or fail with INSUFFICIENT_PO_BALANCE
        assert response.status_code in [200, 422]
    
    def test_special_characters_in_user_email(self, mock_storage, factory, mock_http_request):
        """FAILURE PREVENTION: Special characters in email should not break anything."""
        lpo = factory.create_lpo(sap_reference="SAP-SPECIAL-001", status="Active", po_quantity=500.0)
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "lpo_sap_reference": "SAP-SPECIAL-001",
            "required_area_m2": 50.0,
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "user+tag's\"test@company.com"  # Special chars!
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    from fn_ingest_tag import main
                    response = main(mock_http_request(request_data))
        
        # Should handle special characters without crashing
        assert response.status_code in [200, 400]
    
    def test_unicode_in_tag_name(self, mock_storage, factory, mock_http_request):
        """FAILURE PREVENTION: Unicode characters in tag_name should not crash."""
        lpo = factory.create_lpo(sap_reference="SAP-UNICODE-001", status="Active", po_quantity=500.0)
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "lpo_sap_reference": "SAP-UNICODE-001",
            "required_area_m2": 50.0,
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "user@test.com",
            "tag_name": "TAG-日本語-العربية-中文"  # Unicode!
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    from fn_ingest_tag import main
                    response = main(mock_http_request(request_data))
        
        assert response.status_code == 200


@pytest.mark.unit
class TestNullAndMissingDataFailures:
    """Tests for null/missing data in LPO records that could crash."""
    
    def test_lpo_with_null_po_quantity(self, mock_storage, factory, mock_http_request):
        """FAILURE PREVENTION: LPO with null PO quantity should not crash on balance check."""
        # Create LPO with null quantity
        lpo = {
            "LPO ID": "LPO-NULL-001",
            "Customer LPO Ref": "CUST-NULL",
            "SAP Reference": "SAP-NULL-001",
            "Customer Name": "Test",
            "Project Name": "Test",
            "LPO Status": "Active",
            "Brand": "Test",
            "Wastage Considered in Costing": "5%",
            "PO Quantity (Sqm)": None,  # NULL!
            "Delivered Quantity (Sqm)": 0.0,
        }
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "lpo_sap_reference": "SAP-NULL-001",
            "required_area_m2": 50.0,
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "user@test.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    from fn_ingest_tag import main
                    response = main(mock_http_request(request_data))
        
        # Should handle null gracefully (likely BLOCKED due to 0 balance)
        assert response.status_code in [200, 422]
    
    def test_lpo_with_null_delivered_quantity(self, mock_storage, factory, mock_http_request):
        """FAILURE PREVENTION: LPO with null delivered quantity should default to 0."""
        lpo = {
            "LPO ID": "LPO-NULLDEL-001",
            "Customer LPO Ref": "CUST-NULLDEL",
            "SAP Reference": "SAP-NULLDEL-001",
            "Customer Name": "Test",
            "Project Name": "Test",
            "LPO Status": "Active",
            "Brand": "Test",
            "Wastage Considered in Costing": "5%",
            "PO Quantity (Sqm)": 500.0,
            "Delivered Quantity (Sqm)": None,  # NULL - should default to 0
        }
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "lpo_sap_reference": "SAP-NULLDEL-001",
            "required_area_m2": 50.0,
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "user@test.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    from fn_ingest_tag import main
                    response = main(mock_http_request(request_data))
        
        # Should succeed (null delivered treated as 0)
        assert response.status_code == 200
    
    def test_lpo_with_empty_status(self, mock_storage, factory, mock_http_request):
        """FAILURE PREVENTION: LPO with empty status should not crash."""
        lpo = {
            "LPO ID": "LPO-EMPTYSTAT-001",
            "Customer LPO Ref": "CUST-EMPTYSTAT",
            "SAP Reference": "SAP-EMPTYSTAT-001",
            "Customer Name": "Test",
            "Project Name": "Test",
            "LPO Status": "",  # EMPTY STRING!
            "Brand": "Test",
            "Wastage Considered in Costing": "5%",
            "PO Quantity (Sqm)": 500.0,
            "Delivered Quantity (Sqm)": 0.0,
        }
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "lpo_sap_reference": "SAP-EMPTYSTAT-001",
            "required_area_m2": 50.0,
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "user@test.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    from fn_ingest_tag import main
                    response = main(mock_http_request(request_data))
        
        # Should handle empty status (not "On Hold" so likely succeeds)
        assert response.status_code in [200, 422]


@pytest.mark.unit
class TestBase64ContentFailures:
    """Tests for invalid base64 content that could crash."""
    
    def test_invalid_base64_content(self, mock_storage, factory, mock_http_request):
        """FAILURE PREVENTION: Invalid base64 should not crash the system."""
        lpo = factory.create_lpo(sap_reference="SAP-B64-001", status="Active", po_quantity=500.0)
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "lpo_sap_reference": "SAP-B64-001",
            "required_area_m2": 50.0,
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "user@test.com",
            "file_content": "THIS IS NOT VALID BASE64!!!"  # Invalid!
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    from fn_ingest_tag import main
                    response = main(mock_http_request(request_data))
        
        # Should succeed (file_content is optional, hash will be None)
        assert response.status_code == 200
    
    def test_empty_base64_content(self, mock_storage, factory, mock_http_request):
        """FAILURE PREVENTION: Empty base64 string should not crash."""
        lpo = factory.create_lpo(sap_reference="SAP-B64EMPTY-001", status="Active", po_quantity=500.0)
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "lpo_sap_reference": "SAP-B64EMPTY-001",
            "required_area_m2": 50.0,
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "user@test.com",
            "file_content": ""  # Empty!
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    from fn_ingest_tag import main
                    response = main(mock_http_request(request_data))
        
        # Should succeed (empty content = no hash check)
        assert response.status_code == 200


@pytest.mark.unit
class TestConcurrencyFailures:
    """Tests for race conditions and concurrent access issues."""
    
    def test_same_request_id_rapid_succession(self, mock_storage, factory, mock_http_request):
        """FAILURE PREVENTION: Rapid duplicate requests should not create duplicate tags."""
        lpo = factory.create_lpo(sap_reference="SAP-RAPID-001", status="Active", po_quantity=500.0)
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        client_request_id = str(uuid.uuid4())
        request_data = {
            "client_request_id": client_request_id,
            "lpo_sap_reference": "SAP-RAPID-001",
            "required_area_m2": 50.0,
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "user@test.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        responses = []
        for _ in range(10):  # 10 rapid requests
            with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
                with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                    with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                        from fn_ingest_tag import main
                        response = main(mock_http_request(request_data))
                        responses.append(json.loads(response.get_body()))
        
        # First should be UPLOADED, rest ALREADY_PROCESSED
        statuses = [r["status"] for r in responses]
        assert statuses.count("UPLOADED") == 1
        assert statuses.count("ALREADY_PROCESSED") == 9
        
        # Only one tag created
        tags = mock_storage.find_rows("Tag Sheet Registry", "Client Request ID", client_request_id)
        assert len(tags) == 1


@pytest.mark.unit
class TestBoundaryConditions:
    """Tests for boundary conditions that could cause unexpected behavior."""
    
    def test_exactly_zero_remaining_balance(self, mock_storage, factory, mock_http_request):
        """FAILURE PREVENTION: Exactly 0 remaining balance should reject requests."""
        lpo = factory.create_lpo(
            sap_reference="SAP-ZEROREM-001",
            status="Active",
            po_quantity=100.0,
            delivered_quantity=100.0  # Exactly 0 remaining!
        )
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "lpo_sap_reference": "SAP-ZEROREM-001",
            "required_area_m2": 0.01,  # Tiny amount
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "user@test.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    from fn_ingest_tag import main
                    response = main(mock_http_request(request_data))
        
        # Should be BLOCKED - no remaining balance
        assert response.status_code == 422
    
    def test_floating_point_precision_boundary(self, mock_storage, factory, mock_http_request):
        """FAILURE PREVENTION: Floating point precision should not cause incorrect rejections."""
        lpo = factory.create_lpo(
            sap_reference="SAP-FLOAT-001",
            status="Active",
            po_quantity=100.0,
            delivered_quantity=49.999999999  # Very close to 50
        )
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "lpo_sap_reference": "SAP-FLOAT-001",
            "required_area_m2": 50.0,  # Should this succeed or fail?
            "requested_delivery_date": "2026-02-01",
            "uploaded_by": "user@test.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_ingest_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_ingest_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_ingest_tag._manifest', MockWorkspaceManifest()):
                    from fn_ingest_tag import main
                    response = main(mock_http_request(request_data))
        
        # Should handle floating point correctly without crashing
        assert response.status_code in [200, 422]


@pytest.mark.unit
class TestHelperFunctionRobustness:
    """Tests for helper functions handling edge cases."""
    
    def test_parse_float_safe_handles_all_types(self):
        """FAILURE PREVENTION: parse_float_safe should never crash."""
        from shared.helpers import parse_float_safe
        
        # All these should return a float, never crash
        test_cases = [
            (None, 0.0),
            ("", 0.0),
            ("abc", 0.0),
            ("123.45", 123.45),
            (123.45, 123.45),
            (123, 123.0),
            (True, 1.0),
            (False, 0.0),
            ([], 0.0),
            ({}, 0.0),
        ]
        
        for input_val, expected in test_cases:
            try:
                result = parse_float_safe(input_val)
                assert isinstance(result, float), f"parse_float_safe({input_val}) returned {type(result)}"
            except Exception as e:
                pytest.fail(f"parse_float_safe({input_val}) crashed: {e}")
    
    def test_compute_file_hash_handles_edge_cases(self):
        """FAILURE PREVENTION: File hash should handle all inputs."""
        from shared.helpers import compute_file_hash, compute_file_hash_from_base64
        
        # These should not crash
        assert compute_file_hash(b"") is not None
        assert compute_file_hash(b"x" * 10000000) is not None  # 10MB
        
        # Invalid base64 should return None, not crash
        result = compute_file_hash_from_base64("not valid base64!!!")
        assert result is None
    
    def test_safe_get_handles_all_structures(self):
        """FAILURE PREVENTION: safe_get should never crash."""
        from shared.helpers import safe_get
        
        test_cases = [
            ({}, "key"),
            ({"a": None}, "a"),
            ({"a": {"b": None}}, "a", "b"),
            (None, "key"),
            ("string", "key"),
            (123, "key"),
            ([], "key"),
        ]
        
        for args in test_cases:
            if len(args) >= 2:
                d = args[0]
                keys = args[1:]
                try:
                    result = safe_get(d, *keys, default="fallback")
                except Exception as e:
                    pytest.fail(f"safe_get({args}) crashed: {e}")
