"""
Integration Tests for Delivery Log Ingestion Function

Tests the complete delivery ingestion/update flow including:
- Happy path delivery creation
- Idempotency via SAP DO Number dedup
- Update: SAP Invoice Number, Status, Vehicle ID
- No-change update returns NO_CHANGE
- Non-existent delivery update returns 404
- File attachment handling
- Audit trail (DO_CREATED action logged)
"""

import pytest
import json
import uuid
from unittest.mock import patch

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_delivery_request(**overrides):
    """Build a minimal valid delivery ingest request."""
    defaults = {
        "client_request_id": str(uuid.uuid4()),
        "sap_do_number": f"DO-{uuid.uuid4().hex[:6].upper()}",
        "tag_sheet_id": "TAG-001",
        "status": "Pending SAP",
        "lines": 5,
        "quantity": 250.0,
        "value": 37500.0,
        "uploaded_by": "test@company.com",
    }
    defaults.update(overrides)
    return defaults


def _call_delivery_ingest(mock_storage, request_data, method="POST"):
    """Invoke fn_delivery_ingest with patched dependencies."""
    from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest, MockHttpRequest

    mock_client = MockSmartsheetClient(mock_storage)
    manifest = MockWorkspaceManifest()

    # Build a mock HttpRequest with method attribute
    class _Req(MockHttpRequest):
        def __init__(self, body, http_method):
            super().__init__(body)
            self.method = http_method
            self.headers = {"Content-Type": "application/json"}

    http_req = _Req(request_data, method)

    with patch("fn_delivery_ingest.get_smartsheet_client", return_value=mock_client):
        with patch("fn_delivery_ingest.get_manifest", return_value=manifest):
            from fn_delivery_ingest import main
            return main(http_req)


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestDeliveryIngestHappyPath:

    def test_create_delivery_success(self, mock_storage):
        """POST creates a delivery row in main Delivery Log."""
        request_data = _make_delivery_request(
            sap_do_number="DO-TEST-001",
            tag_sheet_id="TAG-010",
            lines=3,
            quantity=120.0,
            value=18000.0,
        )

        response = _call_delivery_ingest(mock_storage, request_data)

        assert response.status_code == 200
        body = json.loads(response.get_body())
        assert body["status"] == "OK"
        assert body["sap_do_number"] == "DO-TEST-001"
        assert body["delivery_id"].startswith("DO-")
        assert "trace_id" in body

    def test_delivery_row_written_to_sheet(self, mock_storage):
        """Delivery row exists in 07 Delivery Log after create."""
        request_data = _make_delivery_request(
            sap_do_number="DO-WRITE-001",
            tag_sheet_id="TAG-020",
            status="SAP Created",
            vehicle_id="TRK-7",
        )

        _call_delivery_ingest(mock_storage, request_data)

        rows = mock_storage.find_rows("07 Delivery Log", "SAP DO Number", "DO-WRITE-001")
        assert len(rows) == 1
        row = rows[0]
        assert row["Tag Sheet ID"] == "TAG-020"
        assert row["Status"] == "SAP Created"
        assert row["Vehicle ID"] == "TRK-7"

    def test_audit_trail_logged(self, mock_storage):
        """DO_CREATED action is logged to User Action Log."""
        request_data = _make_delivery_request(sap_do_number="DO-AUDIT-001")

        _call_delivery_ingest(mock_storage, request_data)

        actions = mock_storage.find_rows(
            "98 User Action Log", "Action Type", "DO_CREATED"
        )
        assert len(actions) >= 1

    def test_created_at_set(self, mock_storage):
        """Created At timestamp is written to the delivery row."""
        request_data = _make_delivery_request(sap_do_number="DO-TIME-001")

        _call_delivery_ingest(mock_storage, request_data)

        rows = mock_storage.find_rows("07 Delivery Log", "SAP DO Number", "DO-TIME-001")
        assert len(rows) == 1
        assert rows[0].get("Created At") is not None

    def test_all_fields_transferred(self, mock_storage):
        """Every field from the request is written to the row."""
        request_data = _make_delivery_request(
            sap_do_number="DO-FULL-001",
            tag_sheet_id="TAG-100, TAG-101",
            sap_invoice_number="INV-555",
            status="Invoiced",
            lines=8,
            quantity=400.5,
            value=60075.0,
            vehicle_id="VH-99",
            remarks="Express delivery",
        )

        _call_delivery_ingest(mock_storage, request_data)

        rows = mock_storage.find_rows("07 Delivery Log", "SAP DO Number", "DO-FULL-001")
        assert len(rows) == 1
        row = rows[0]
        assert row["Tag Sheet ID"] == "TAG-100, TAG-101"
        assert row["SAP Invoice Number"] == "INV-555"
        assert row["Status"] == "Invoiced"
        assert row["Lines"] == 8  # int stays as int
        assert row["Quantity"] == 400.5  # Decimal float stays as float
        assert row["Value"] == "60075"  # Whole-number float → string
        assert row["Vehicle ID"] == "VH-99"
        assert row["Remarks"] == "Express delivery"


# ---------------------------------------------------------------------------
# Idempotency tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestDeliveryIngestIdempotency:

    def test_duplicate_do_number_returns_already_processed(self, mock_storage):
        """Second POST with same SAP DO Number returns ALREADY_PROCESSED."""
        # Create first delivery
        existing = {
            "Delivery ID": "DO-0001",
            "SAP DO Number": "DO-DUP-001",
            "Tag Sheet ID": "TAG-001",
            "Status": "Pending SAP",
        }
        mock_storage.add_row("07 Delivery Log", existing)

        # Try to create again
        request_data = _make_delivery_request(sap_do_number="DO-DUP-001")

        response = _call_delivery_ingest(mock_storage, request_data)

        assert response.status_code == 200
        body = json.loads(response.get_body())
        assert body["status"] == "ALREADY_PROCESSED"
        assert body["sap_do_number"] == "DO-DUP-001"

    def test_no_duplicate_rows_created(self, mock_storage):
        """Dedup does not create a second row."""
        existing = {
            "Delivery ID": "DO-0002",
            "SAP DO Number": "DO-NODEDUP-001",
            "Tag Sheet ID": "TAG-002",
        }
        mock_storage.add_row("07 Delivery Log", existing)

        request_data = _make_delivery_request(sap_do_number="DO-NODEDUP-001")
        _call_delivery_ingest(mock_storage, request_data)

        rows = mock_storage.find_rows(
            "07 Delivery Log", "SAP DO Number", "DO-NODEDUP-001"
        )
        assert len(rows) == 1  # Still only one row


# ---------------------------------------------------------------------------
# Update tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestDeliveryUpdate:

    def _seed_delivery(self, mock_storage, do_number="DO-UPD-001"):
        """Insert a delivery row and return its row_id."""
        row = {
            "Delivery ID": "DO-0010",
            "SAP DO Number": do_number,
            "Tag Sheet ID": "TAG-050",
            "Status": "Pending SAP",
            "Lines": 5,
            "Quantity": 250.0,
        }
        result = mock_storage.add_row("07 Delivery Log", row)
        return result["id"]

    def test_update_sap_invoice(self, mock_storage):
        """PUT updates SAP Invoice Number on existing delivery."""
        self._seed_delivery(mock_storage, "DO-INV-001")

        update_data = {
            "sap_do_number": "DO-INV-001",
            "sap_invoice_number": "INV-99999",
            "updated_by": "user@company.com",
        }
        response = _call_delivery_ingest(mock_storage, update_data, method="PUT")

        assert response.status_code == 200
        body = json.loads(response.get_body())
        assert body["status"] == "OK"
        assert "SAP_INVOICE_NUMBER" in body.get("updated_fields", [])

        rows = mock_storage.find_rows(
            "07 Delivery Log", "SAP DO Number", "DO-INV-001"
        )
        assert rows[0]["SAP Invoice Number"] == "INV-99999"

    def test_update_status(self, mock_storage):
        """PUT updates Status."""
        self._seed_delivery(mock_storage, "DO-STAT-001")

        update_data = {
            "sap_do_number": "DO-STAT-001",
            "status": "Invoiced",
            "updated_by": "admin@company.com",
        }
        response = _call_delivery_ingest(mock_storage, update_data, method="PUT")

        assert response.status_code == 200
        rows = mock_storage.find_rows(
            "07 Delivery Log", "SAP DO Number", "DO-STAT-001"
        )
        assert rows[0]["Status"] == "Invoiced"

    def test_update_vehicle_id(self, mock_storage):
        """PUT updates Vehicle ID."""
        self._seed_delivery(mock_storage, "DO-VH-001")

        update_data = {
            "sap_do_number": "DO-VH-001",
            "vehicle_id": "TRUCK-42",
            "updated_by": "logistics@company.com",
        }
        response = _call_delivery_ingest(mock_storage, update_data, method="PUT")

        assert response.status_code == 200
        rows = mock_storage.find_rows(
            "07 Delivery Log", "SAP DO Number", "DO-VH-001"
        )
        assert rows[0]["Vehicle ID"] == "TRUCK-42"

    def test_update_multiple_fields(self, mock_storage):
        """PUT updates all three updatable fields at once."""
        self._seed_delivery(mock_storage, "DO-MULTI-001")

        update_data = {
            "sap_do_number": "DO-MULTI-001",
            "sap_invoice_number": "INV-MULTI",
            "status": "Closed",
            "vehicle_id": "VAN-1",
            "remarks": "Final delivery",
            "updated_by": "pm@company.com",
        }
        response = _call_delivery_ingest(mock_storage, update_data, method="PUT")

        assert response.status_code == 200
        body = json.loads(response.get_body())
        assert body["status"] == "OK"

        rows = mock_storage.find_rows(
            "07 Delivery Log", "SAP DO Number", "DO-MULTI-001"
        )
        row = rows[0]
        assert row["SAP Invoice Number"] == "INV-MULTI"
        assert row["Status"] == "Closed"
        assert row["Vehicle ID"] == "VAN-1"
        assert row["Remarks"] == "Final delivery"

    def test_update_nonexistent_returns_404(self, mock_storage):
        """PUT for non-existent DO Number returns 404."""
        update_data = {
            "sap_do_number": "DO-GHOST-001",
            "status": "Closed",
            "updated_by": "admin@company.com",
        }
        response = _call_delivery_ingest(mock_storage, update_data, method="PUT")

        assert response.status_code == 404
        body = json.loads(response.get_body())
        assert body["status"] == "NOT_FOUND"

    def test_update_no_changes_returns_no_change(self, mock_storage):
        """PUT with no updatable fields returns NO_CHANGE."""
        self._seed_delivery(mock_storage, "DO-NOOP-001")

        update_data = {
            "sap_do_number": "DO-NOOP-001",
            # No updatable fields provided
            "updated_by": "user@company.com",
        }
        response = _call_delivery_ingest(mock_storage, update_data, method="PUT")

        assert response.status_code == 200
        body = json.loads(response.get_body())
        assert body["status"] == "NO_CHANGE"

    def test_update_logs_audit_trail(self, mock_storage):
        """PUT logs user action on successful update."""
        self._seed_delivery(mock_storage, "DO-AUDITUPD-001")

        update_data = {
            "sap_do_number": "DO-AUDITUPD-001",
            "status": "POD Uploaded",
            "updated_by": "auditor@company.com",
        }
        _call_delivery_ingest(mock_storage, update_data, method="PUT")

        actions = mock_storage.find_rows(
            "98 User Action Log", "Action Type", "LPO_UPDATED"
        )
        assert len(actions) >= 1


# ---------------------------------------------------------------------------
# Model validation tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestDeliveryModels:

    def test_float_coercion_on_do_number(self):
        """SAP DO Number as float '12345.0' is coerced to '12345'."""
        from shared.models import DeliveryIngestRequest

        req = DeliveryIngestRequest(
            sap_do_number=12345.0,
            tag_sheet_id="TAG-001",
            uploaded_by="test@co.com",
        )
        assert req.sap_do_number == "12345"

    def test_float_coercion_on_invoice_number(self):
        """SAP Invoice Number as float is coerced to string without .0."""
        from shared.models import DeliveryUpdateRequest

        req = DeliveryUpdateRequest(
            sap_do_number="DO-001",
            sap_invoice_number=99999.0,
            updated_by="test@co.com",
        )
        assert req.sap_invoice_number == "99999"

    def test_float_coercion_on_vehicle_id(self):
        """Vehicle ID as float is coerced to string without .0."""
        from shared.models import DeliveryUpdateRequest

        req = DeliveryUpdateRequest(
            sap_do_number="DO-001",
            vehicle_id=42.0,
            updated_by="test@co.com",
        )
        assert req.vehicle_id == "42"

    def test_delivery_status_enum(self):
        """DeliveryStatus enum has all expected values."""
        from shared.models import DeliveryStatus

        assert DeliveryStatus.PENDING_SAP == "Pending SAP"
        assert DeliveryStatus.SAP_CREATED == "SAP Created"
        assert DeliveryStatus.POD_UPLOADED == "POD Uploaded"
        assert DeliveryStatus.INVOICED == "Invoiced"
        assert DeliveryStatus.CLOSED == "Closed"
