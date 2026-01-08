"""
Unit Tests for Data Models

Tests all Pydantic models for:
- Validation rules
- Default value generation
- Serialization/deserialization
- Type coercion
"""

import pytest
import uuid
from datetime import datetime
from pydantic import ValidationError

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.models import (
    TagStatus,
    LPOStatus,
    ExceptionSeverity,
    ExceptionSource,
    ReasonCode,
    ActionType,
    TagIngestRequest,
    TagIngestResponse,
    ExceptionRecord,
    UserActionRecord,
    LPORecord,
    TagRecord,
)


class TestEnums:
    """Test enum definitions and values."""
    
    @pytest.mark.unit
    def test_tag_status_values(self):
        """Verify TagStatus enum has all expected values."""
        expected = [
            "Draft", "Validate", "Sent to Nesting", "Nesting Complete",
            "Planned Queued", "WIP", "Complete", "Partial Dispatch",
            "Dispatched", "Closed", "Revision Pending", "Hold", "Cancelled", "BLOCKED"
        ]
        actual = [s.value for s in TagStatus]
        assert set(expected) == set(actual), f"Missing or extra TagStatus values"
    
    @pytest.mark.unit
    def test_lpo_status_values(self):
        """Verify LPOStatus enum has all expected values."""
        expected = ["Draft", "Pending Approval", "Active", "On Hold", "Closed"]
        actual = [s.value for s in LPOStatus]
        assert set(expected) == set(actual)
    
    @pytest.mark.unit
    def test_exception_severity_ordering(self):
        """Verify severity levels are available."""
        assert ExceptionSeverity.LOW.value == "LOW"
        assert ExceptionSeverity.MEDIUM.value == "MEDIUM"
        assert ExceptionSeverity.HIGH.value == "HIGH"
        assert ExceptionSeverity.CRITICAL.value == "CRITICAL"
    
    @pytest.mark.unit
    def test_reason_codes_complete(self):
        """Verify all business reason codes are defined."""
        expected_codes = [
            "DUPLICATE_UPLOAD", "MULTI_TAG_NEST", "SHORTAGE",
            "OVERCONSUMPTION", "PHYSICAL_VARIANCE", "SAP_CREATE_FAILED",
            "PICK_NEGATIVE", "LPO_NOT_FOUND", "LPO_ON_HOLD",
            "INSUFFICIENT_PO_BALANCE", "PARSE_FAILED"
        ]
        actual_codes = [r.value for r in ReasonCode]
        for code in expected_codes:
            assert code in actual_codes, f"Missing ReasonCode: {code}"


class TestTagIngestRequest:
    """Test TagIngestRequest model validation."""
    
    @pytest.mark.unit
    def test_valid_request_minimal(self):
        """Test creation with minimal required fields."""
        request = TagIngestRequest(
            required_area_m2=100.5,
            requested_delivery_date="2026-02-01",
            uploaded_by="user@test.com"
        )
        assert request.required_area_m2 == 100.5
        assert request.uploaded_by == "user@test.com"
        assert request.client_request_id is not None  # Auto-generated
    
    @pytest.mark.unit
    def test_valid_request_full(self):
        """Test creation with all fields."""
        request = TagIngestRequest(
            client_request_id="test-uuid-123",
            tag_id="TAG-20260107-0001",
            lpo_id="LPO-123",
            customer_lpo_ref="CUST-REF-001",
            lpo_sap_reference="SAP-123456",
            required_area_m2=250.75,
            requested_delivery_date="2026-03-15",
            file_url="https://sharepoint/file.xlsx",
            original_file_name="tag_sheet_v1.xlsx",
            uploaded_by="sales@company.com",
            tag_name="TAG-SPECIAL-001",
            metadata={"priority": "high", "notes": "urgent"}
        )
        assert request.client_request_id == "test-uuid-123"
        assert request.lpo_sap_reference == "SAP-123456"
        assert request.metadata["priority"] == "high"
    
    @pytest.mark.unit
    def test_auto_generated_client_request_id(self):
        """Test that client_request_id is auto-generated if not provided."""
        request1 = TagIngestRequest(
            required_area_m2=50.0,
            requested_delivery_date="2026-02-01",
            uploaded_by="user@test.com"
        )
        request2 = TagIngestRequest(
            required_area_m2=50.0,
            requested_delivery_date="2026-02-01",
            uploaded_by="user@test.com"
        )
        # Each should have unique ID
        assert request1.client_request_id != request2.client_request_id
    
    @pytest.mark.unit
    def test_missing_required_field(self):
        """Test validation fails without required fields."""
        with pytest.raises(ValidationError) as exc_info:
            TagIngestRequest(
                required_area_m2=100.0,
                # Missing: requested_delivery_date, uploaded_by
            )
        errors = exc_info.value.errors()
        assert len(errors) >= 1
    
    @pytest.mark.unit
    def test_invalid_required_area_type(self):
        """Test validation for required_area_m2 type."""
        # String that can be coerced to float should work
        request = TagIngestRequest(
            required_area_m2="50.5",  # type: ignore
            requested_delivery_date="2026-02-01",
            uploaded_by="user@test.com"
        )
        assert request.required_area_m2 == 50.5
    
    @pytest.mark.unit
    def test_negative_area_validation(self):
        """Test handling of negative area values."""
        # Pydantic allows negative floats by default
        request = TagIngestRequest(
            required_area_m2=-100.0,
            requested_delivery_date="2026-02-01",
            uploaded_by="user@test.com"
        )
        assert request.required_area_m2 == -100.0


class TestTagIngestResponse:
    """Test TagIngestResponse model."""
    
    @pytest.mark.unit
    def test_success_response(self):
        """Test successful response creation."""
        response = TagIngestResponse(
            status="UPLOADED",
            tag_id="TAG-20260107-0001",
            file_hash="abc123def456",
            trace_id="trace-xyz789",
            message="Tag uploaded successfully"
        )
        assert response.status == "UPLOADED"
        assert response.tag_id == "TAG-20260107-0001"
    
    @pytest.mark.unit
    def test_duplicate_response(self):
        """Test duplicate response creation."""
        response = TagIngestResponse(
            status="DUPLICATE",
            trace_id="trace-xyz789",
            exception_id="EX-20260107-001"
        )
        assert response.status == "DUPLICATE"
        assert response.tag_id is None
    
    @pytest.mark.unit
    def test_blocked_response(self):
        """Test blocked response creation."""
        response = TagIngestResponse(
            status="BLOCKED",
            trace_id="trace-xyz789",
            exception_id="EX-20260107-002",
            message="LPO is on hold"
        )
        assert response.status == "BLOCKED"
        assert response.exception_id == "EX-20260107-002"


class TestExceptionRecord:
    """Test ExceptionRecord model."""
    
    @pytest.mark.unit
    def test_auto_generated_exception_id(self):
        """Test that exception_id is auto-generated."""
        record = ExceptionRecord(
            source=ExceptionSource.INGEST,
            reason_code=ReasonCode.DUPLICATE_UPLOAD,
            severity=ExceptionSeverity.MEDIUM
        )
        assert record.exception_id.startswith("EX-")
        assert len(record.exception_id) > 10
    
    @pytest.mark.unit
    def test_created_at_default(self):
        """Test that created_at defaults to now."""
        before = datetime.now()
        record = ExceptionRecord(
            source=ExceptionSource.ALLOCATION,
            reason_code=ReasonCode.SHORTAGE,
            severity=ExceptionSeverity.HIGH
        )
        after = datetime.now()
        assert before <= record.created_at <= after
    
    @pytest.mark.unit
    def test_default_status(self):
        """Test default status is Open."""
        record = ExceptionRecord(
            source=ExceptionSource.INGEST,
            reason_code=ReasonCode.DUPLICATE_UPLOAD,
            severity=ExceptionSeverity.LOW
        )
        assert record.status == "Open"
    
    @pytest.mark.unit
    def test_full_exception_record(self):
        """Test creation with all fields."""
        record = ExceptionRecord(
            exception_id="EX-TEST-001",
            created_at=datetime(2026, 1, 7, 10, 30, 0),
            source=ExceptionSource.RECONCILE,
            related_tag_id="TAG-001",
            related_txn_id="TXN-001",
            material_code="MAT-001",
            quantity=50.5,
            reason_code=ReasonCode.PHYSICAL_VARIANCE,
            severity=ExceptionSeverity.CRITICAL,
            assigned_to="manager@company.com",
            status="In Progress",
            sla_due=datetime(2026, 1, 7, 14, 30, 0),
            attachment_links="https://sharepoint/doc1",
            resolution_action="Investigating...",
            approvals="pending",
            trace_id="trace-123"
        )
        assert record.material_code == "MAT-001"
        assert record.status == "In Progress"


class TestUserActionRecord:
    """Test UserActionRecord model."""
    
    @pytest.mark.unit
    def test_auto_generated_action_id(self):
        """Test that action_id is auto-generated."""
        record = UserActionRecord(
            user_id="user@test.com",
            action_type=ActionType.TAG_CREATED,
            target_table="Tag Sheet Registry",
            target_id="TAG-001"
        )
        # Should be a valid UUID
        uuid.UUID(record.action_id)
    
    @pytest.mark.unit
    def test_timestamp_default(self):
        """Test that timestamp defaults to now."""
        record = UserActionRecord(
            user_id="user@test.com",
            action_type=ActionType.TAG_UPLOAD,
            target_table="Tag Sheet Registry",
            target_id="TAG-001"
        )
        assert record.timestamp is not None
        assert isinstance(record.timestamp, datetime)


class TestLPORecord:
    """Test LPORecord model."""
    
    @pytest.mark.unit
    def test_valid_lpo_record(self):
        """Test valid LPO record creation."""
        record = LPORecord(
            lpo_id="LPO-001",
            customer_lpo_ref="CUST-001",
            sap_reference="SAP-123",
            customer_name="Test Customer",
            lpo_status=LPOStatus.ACTIVE,
            po_quantity_sqm=1000.0,
            delivered_quantity_sqm=250.0
        )
        assert record.lpo_id == "LPO-001"
        assert record.po_quantity_sqm == 1000.0
    
    @pytest.mark.unit
    def test_default_values(self):
        """Test default values are applied."""
        record = LPORecord(
            lpo_id="LPO-002",
            customer_lpo_ref="CUST-002",
            lpo_status=LPOStatus.DRAFT,
            po_quantity_sqm=500.0
        )
        assert record.delivered_quantity_sqm == 0
        assert record.total_allocated_cost == 0


class TestTagRecord:
    """Test TagRecord model."""
    
    @pytest.mark.unit
    def test_valid_tag_record(self):
        """Test valid tag record creation."""
        record = TagRecord(
            tag_id="TAG-20260107-0001",
            tag_name="Test Tag",
            status=TagStatus.DRAFT
        )
        assert record.tag_id == "TAG-20260107-0001"
        assert record.status == TagStatus.DRAFT
    
    @pytest.mark.unit
    def test_default_status(self):
        """Test default status is DRAFT."""
        record = TagRecord(tag_id="TAG-002")
        assert record.status == TagStatus.DRAFT
