"""
Unit Tests for fn_lpo_update
==============================

Tests the POST endpoint for updating existing LPO records by SAP Reference.

Covers:
- Request validation (400 for bad payload)
- SAP Reference not found (404 + exception + audit log)
- PO quantity conflict — below delivered amount (422 + exception + audit log)
- Happy path — partial update with changes list (200)
- PO Value recalculation when po_quantity_sqm changes
- PO Value recalculation when price_per_sqm changes
- PO Balance recalculation when po_quantity_sqm changes
- Timestamp always updated
- Audit log with old/new values
- "No changes" response when no optional fields supplied
- Each updatable field independently
"""

import pytest
import json
from datetime import datetime
from unittest.mock import patch, MagicMock
import azure.functions as func

from shared import Column, Sheet

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXED_DATETIME_STR = "2026-03-24 14:30:00"
FIXED_NOW = datetime(2026, 3, 24, 14, 30, 0)

MODULE = "fn_lpo_update"
# now_uae is imported locally inside main() via `from shared.helpers import now_uae`,
# so it must be patched at its source rather than on the fn_lpo_update module.
NOW_UAE_PATCH = "shared.helpers.now_uae"


def _make_request(body: dict) -> func.HttpRequest:
    """Build a mock Azure Functions HTTP POST request."""
    return func.HttpRequest(
        method="POST",
        url="/api/lpo/update",
        body=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )


def _make_invalid_json_request() -> func.HttpRequest:
    """Build a request whose body is not valid JSON."""
    return func.HttpRequest(
        method="POST",
        url="/api/lpo/update",
        body=b"{{BAD-JSON",
        headers={"Content-Type": "application/json"},
    )


def _base_body(**overrides) -> dict:
    """Minimal valid LPOUpdateRequest body."""
    base = {
        "sap_reference": "PTE-185",
        "updated_by": "user@company.com",
    }
    base.update(overrides)
    return base


def _existing_lpo_row(
    row_id: int = 5001,
    po_qty: float = 1000.0,
    delivered_qty: float = 200.0,
    price_per_sqm: float = 150.0,
    customer_name: str = "Old Customer",
    project_name: str = "Old Project",
    customer_lpo_ref: str = "CUST-OLD",
    lpo_status: str = "Active",
    terms_of_payment: str = "30 Days Credit",
    wastage_pct: str = "5",
    hold_reason: str = "",
    remarks: str = "old remark",
) -> dict:
    """
    Simulate the dict returned by client.find_row for an existing LPO.
    Keys are the logical column names passed through the mock
    _get_physical_column_name (which returns the logical name as-is).
    """
    return {
        "row_id": row_id,
        "PO_QUANTITY_SQM": str(po_qty),
        "DELIVERED_QUANTITY_SQM": str(delivered_qty),
        "PRICE_PER_SQM": str(price_per_sqm),
        "CUSTOMER_NAME": customer_name,
        "PROJECT_NAME": project_name,
        "CUSTOMER_LPO_REF": customer_lpo_ref,
        "LPO_STATUS": lpo_status,
        "TERMS_OF_PAYMENT": terms_of_payment,
        "WASTAGE_CONSIDERED_IN_COSTING": wastage_pct,
        "HOLD_REASON": hold_reason,
        "REMARKS": remarks,
    }


# Common patches applied to every test in the class.
def _common_patches():
    """Return a dict of patch targets and their default values."""
    return {
        f"{MODULE}.generate_trace_id": "TRACE-LPO",
        f"{MODULE}.format_datetime_for_smartsheet": FIXED_DATETIME_STR,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestLpoUpdate:
    """Tests for fn_lpo_update.main."""

    # ------------------------------------------------------------------ #
    # 1. Request validation  (400)
    # ------------------------------------------------------------------ #

    @patch(f"{MODULE}.generate_trace_id", return_value="TRACE-400")
    def test_invalid_json_returns_400(self, mock_trace):
        """Non-JSON body triggers the inner ValueError handler -> 400."""
        from fn_lpo_update import main

        resp = main(_make_invalid_json_request())

        assert resp.status_code == 400
        data = json.loads(resp.get_body())
        assert data["status"] == "ERROR"
        assert data["trace_id"] == "TRACE-400"

    @patch(f"{MODULE}.generate_trace_id", return_value="TRACE-400B")
    def test_missing_required_fields_returns_400(self, mock_trace):
        """Missing 'sap_reference' or 'updated_by' -> 400."""
        from fn_lpo_update import main

        # Missing both required fields
        resp = main(_make_request({"remarks": "hello"}))

        assert resp.status_code == 400
        data = json.loads(resp.get_body())
        assert data["status"] == "ERROR"

    @patch(f"{MODULE}.generate_trace_id", return_value="TRACE-400C")
    def test_negative_po_quantity_returns_400(self, mock_trace):
        """po_quantity_sqm must be > 0 per Pydantic Field(gt=0) -> 400."""
        from fn_lpo_update import main

        body = _base_body(po_quantity_sqm=-5.0)
        resp = main(_make_request(body))

        assert resp.status_code == 400
        data = json.loads(resp.get_body())
        assert data["status"] == "ERROR"

    # ------------------------------------------------------------------ #
    # 2. SAP Reference not found  (404)
    # ------------------------------------------------------------------ #

    @patch(f"{MODULE}.log_user_action")
    @patch(f"{MODULE}.create_exception", return_value="EX-NOT-FOUND")
    @patch(f"{MODULE}.get_smartsheet_client")
    @patch(f"{MODULE}.generate_trace_id", return_value="TRACE-404")
    def test_sap_ref_not_found_returns_404(
        self, mock_trace, mock_get_client, mock_create_exc, mock_log_action
    ):
        """When find_row returns None, 404 is returned with an exception."""
        from fn_lpo_update import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.find_row.return_value = None

        resp = main(_make_request(_base_body()))

        assert resp.status_code == 404
        data = json.loads(resp.get_body())
        assert data["status"] == "NOT_FOUND"
        assert data["exception_id"] == "EX-NOT-FOUND"
        assert "PTE-185" in data["message"]

    @patch(f"{MODULE}.log_user_action")
    @patch(f"{MODULE}.create_exception", return_value="EX-NF2")
    @patch(f"{MODULE}.get_smartsheet_client")
    @patch(f"{MODULE}.generate_trace_id", return_value="TRACE-404B")
    def test_not_found_creates_exception_record(
        self, mock_trace, mock_get_client, mock_create_exc, mock_log_action
    ):
        """A NOT_FOUND triggers create_exception with SAP_REF_NOT_FOUND reason."""
        from fn_lpo_update import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.find_row.return_value = None

        main(_make_request(_base_body()))

        mock_create_exc.assert_called_once()
        kwargs = mock_create_exc.call_args[1]
        assert kwargs["reason_code"].value == "SAP_REF_NOT_FOUND"
        assert kwargs["severity"].value == "MEDIUM"

    @patch(f"{MODULE}.log_user_action")
    @patch(f"{MODULE}.create_exception", return_value="EX-NF3")
    @patch(f"{MODULE}.get_smartsheet_client")
    @patch(f"{MODULE}.generate_trace_id", return_value="TRACE-404C")
    def test_not_found_logs_user_action(
        self, mock_trace, mock_get_client, mock_create_exc, mock_log_action
    ):
        """A NOT_FOUND also logs a user action with OPERATION_FAILED."""
        from fn_lpo_update import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.find_row.return_value = None

        main(_make_request(_base_body()))

        mock_log_action.assert_called_once()
        kwargs = mock_log_action.call_args[1]
        assert kwargs["action_type"].value == "OPERATION_FAILED"
        assert kwargs["user_id"] == "user@company.com"
        assert kwargs["target_id"] == "PTE-185"

    # ------------------------------------------------------------------ #
    # 3. PO quantity conflict  (422)
    # ------------------------------------------------------------------ #

    @patch(f"{MODULE}.log_user_action")
    @patch(f"{MODULE}.create_exception", return_value="EX-QTY")
    @patch(f"{MODULE}._get_physical_column_name", side_effect=lambda s, c: c)
    @patch(f"{MODULE}.get_smartsheet_client")
    @patch(f"{MODULE}.generate_trace_id", return_value="TRACE-422")
    def test_quantity_below_delivered_returns_422(
        self, mock_trace, mock_get_client, mock_phys, mock_create_exc, mock_log_action
    ):
        """Reducing PO quantity below delivered amount -> 422 BLOCKED."""
        from fn_lpo_update import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        # Existing LPO has 200 delivered
        mock_client.find_row.return_value = _existing_lpo_row(delivered_qty=200.0)

        body = _base_body(po_quantity_sqm=100.0)  # < 200 delivered
        resp = main(_make_request(body))

        assert resp.status_code == 422
        data = json.loads(resp.get_body())
        assert data["status"] == "BLOCKED"
        assert data["exception_id"] == "EX-QTY"
        assert "200" in data["message"]  # delivered amount mentioned

    @patch(f"{MODULE}.log_user_action")
    @patch(f"{MODULE}.create_exception", return_value="EX-QTY2")
    @patch(f"{MODULE}._get_physical_column_name", side_effect=lambda s, c: c)
    @patch(f"{MODULE}.get_smartsheet_client")
    @patch(f"{MODULE}.generate_trace_id", return_value="TRACE-422B")
    def test_quantity_conflict_creates_exception(
        self, mock_trace, mock_get_client, mock_phys, mock_create_exc, mock_log_action
    ):
        """Quantity conflict creates an exception with PO_QUANTITY_CONFLICT reason."""
        from fn_lpo_update import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.find_row.return_value = _existing_lpo_row(delivered_qty=500.0)

        body = _base_body(po_quantity_sqm=100.0)
        main(_make_request(body))

        mock_create_exc.assert_called_once()
        kwargs = mock_create_exc.call_args[1]
        assert kwargs["reason_code"].value == "PO_QUANTITY_CONFLICT"
        assert kwargs["severity"].value == "HIGH"

    @patch(f"{MODULE}.log_user_action")
    @patch(f"{MODULE}.create_exception", return_value="EX-QTY3")
    @patch(f"{MODULE}._get_physical_column_name", side_effect=lambda s, c: c)
    @patch(f"{MODULE}.get_smartsheet_client")
    @patch(f"{MODULE}.generate_trace_id", return_value="TRACE-422C")
    def test_quantity_equal_to_delivered_is_allowed(
        self, mock_trace, mock_get_client, mock_phys, mock_create_exc, mock_log_action
    ):
        """Setting PO quantity exactly equal to delivered is NOT a conflict."""
        from fn_lpo_update import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.find_row.return_value = _existing_lpo_row(delivered_qty=200.0)

        # Patch now_uae and format helper for the update path
        with patch(NOW_UAE_PATCH, return_value=FIXED_NOW), \
             patch(f"{MODULE}.format_datetime_for_smartsheet", return_value=FIXED_DATETIME_STR):
            body = _base_body(po_quantity_sqm=200.0)  # == delivered
            resp = main(_make_request(body))

        assert resp.status_code == 200
        mock_create_exc.assert_not_called()

    # ------------------------------------------------------------------ #
    # 4. Happy path — partial update  (200)
    # ------------------------------------------------------------------ #

    @patch(f"{MODULE}.log_user_action")
    @patch(f"{MODULE}.create_exception")
    @patch(f"{MODULE}._get_physical_column_name", side_effect=lambda s, c: c)
    @patch(NOW_UAE_PATCH, return_value=FIXED_NOW)
    @patch(f"{MODULE}.format_datetime_for_smartsheet", return_value=FIXED_DATETIME_STR)
    @patch(f"{MODULE}.get_smartsheet_client")
    @patch(f"{MODULE}.generate_trace_id", return_value="TRACE-200")
    def test_happy_path_partial_update(
        self, mock_trace, mock_get_client, mock_fmt, mock_now,
        mock_phys, mock_create_exc, mock_log_action
    ):
        """Updating customer_name and remarks returns 200 with changes list."""
        from fn_lpo_update import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.find_row.return_value = _existing_lpo_row()

        body = _base_body(customer_name="New Customer", remarks="new remark")
        resp = main(_make_request(body))

        assert resp.status_code == 200
        data = json.loads(resp.get_body())
        assert data["status"] == "OK"
        assert data["sap_reference"] == "PTE-185"
        assert "customer_name" in data["changes"]
        assert "remarks" in data["changes"]

    @patch(f"{MODULE}.log_user_action")
    @patch(f"{MODULE}.create_exception")
    @patch(f"{MODULE}._get_physical_column_name", side_effect=lambda s, c: c)
    @patch(NOW_UAE_PATCH, return_value=FIXED_NOW)
    @patch(f"{MODULE}.format_datetime_for_smartsheet", return_value=FIXED_DATETIME_STR)
    @patch(f"{MODULE}.get_smartsheet_client")
    @patch(f"{MODULE}.generate_trace_id", return_value="TRACE-200B")
    def test_update_calls_update_row(
        self, mock_trace, mock_get_client, mock_fmt, mock_now,
        mock_phys, mock_create_exc, mock_log_action
    ):
        """update_row is called with the correct sheet, row_id, and column updates."""
        from fn_lpo_update import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.find_row.return_value = _existing_lpo_row(row_id=7777)

        body = _base_body(project_name="Alpha Tower")
        main(_make_request(body))

        mock_client.update_row.assert_called_once()
        args, kwargs = mock_client.update_row.call_args
        assert args[0] == Sheet.LPO_MASTER
        assert args[1] == 7777
        updates = args[2]
        assert updates[Column.LPO_MASTER.PROJECT_NAME] == "Alpha Tower"
        # Timestamp always present
        assert Column.LPO_MASTER.UPDATED_AT in updates

    # ------------------------------------------------------------------ #
    # 5. PO Value recalculation  (po_quantity_sqm)
    # ------------------------------------------------------------------ #

    @patch(f"{MODULE}.log_user_action")
    @patch(f"{MODULE}.create_exception")
    @patch(f"{MODULE}._get_physical_column_name", side_effect=lambda s, c: c)
    @patch(NOW_UAE_PATCH, return_value=FIXED_NOW)
    @patch(f"{MODULE}.format_datetime_for_smartsheet", return_value=FIXED_DATETIME_STR)
    @patch(f"{MODULE}.get_smartsheet_client")
    @patch(f"{MODULE}.generate_trace_id", return_value="TRACE-PV1")
    def test_po_value_recalc_on_quantity_change(
        self, mock_trace, mock_get_client, mock_fmt, mock_now,
        mock_phys, mock_create_exc, mock_log_action
    ):
        """Changing po_quantity_sqm recalculates PO_VALUE = qty * existing price."""
        from fn_lpo_update import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.find_row.return_value = _existing_lpo_row(
            po_qty=1000, price_per_sqm=150.0, delivered_qty=100
        )

        body = _base_body(po_quantity_sqm=500.0)  # new qty
        main(_make_request(body))

        updates = mock_client.update_row.call_args[0][2]
        assert updates[Column.LPO_MASTER.PO_VALUE] == 500.0 * 150.0

    # ------------------------------------------------------------------ #
    # 6. PO Value recalculation  (price_per_sqm only)
    # ------------------------------------------------------------------ #

    @patch(f"{MODULE}.log_user_action")
    @patch(f"{MODULE}.create_exception")
    @patch(f"{MODULE}._get_physical_column_name", side_effect=lambda s, c: c)
    @patch(NOW_UAE_PATCH, return_value=FIXED_NOW)
    @patch(f"{MODULE}.format_datetime_for_smartsheet", return_value=FIXED_DATETIME_STR)
    @patch(f"{MODULE}.get_smartsheet_client")
    @patch(f"{MODULE}.generate_trace_id", return_value="TRACE-PV2")
    def test_po_value_recalc_on_price_change(
        self, mock_trace, mock_get_client, mock_fmt, mock_now,
        mock_phys, mock_create_exc, mock_log_action
    ):
        """Changing price_per_sqm recalculates PO_VALUE = existing qty * new price."""
        from fn_lpo_update import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.find_row.return_value = _existing_lpo_row(
            po_qty=1000, price_per_sqm=100.0
        )

        body = _base_body(price_per_sqm=200.0)  # new price
        main(_make_request(body))

        updates = mock_client.update_row.call_args[0][2]
        assert updates[Column.LPO_MASTER.PO_VALUE] == 1000.0 * 200.0

    # ------------------------------------------------------------------ #
    # 6b. PO Value when both qty and price change simultaneously
    # ------------------------------------------------------------------ #

    @patch(f"{MODULE}.log_user_action")
    @patch(f"{MODULE}.create_exception")
    @patch(f"{MODULE}._get_physical_column_name", side_effect=lambda s, c: c)
    @patch(NOW_UAE_PATCH, return_value=FIXED_NOW)
    @patch(f"{MODULE}.format_datetime_for_smartsheet", return_value=FIXED_DATETIME_STR)
    @patch(f"{MODULE}.get_smartsheet_client")
    @patch(f"{MODULE}.generate_trace_id", return_value="TRACE-PV3")
    def test_po_value_recalc_when_both_qty_and_price_change(
        self, mock_trace, mock_get_client, mock_fmt, mock_now,
        mock_phys, mock_create_exc, mock_log_action
    ):
        """When both po_quantity_sqm and price_per_sqm change, PO_VALUE uses new values."""
        from fn_lpo_update import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.find_row.return_value = _existing_lpo_row(
            po_qty=1000, price_per_sqm=100.0, delivered_qty=50
        )

        body = _base_body(po_quantity_sqm=800.0, price_per_sqm=120.0)
        main(_make_request(body))

        updates = mock_client.update_row.call_args[0][2]
        # The price_per_sqm block runs after po_quantity_sqm block and overwrites PO_VALUE
        assert updates[Column.LPO_MASTER.PO_VALUE] == 800.0 * 120.0

    # ------------------------------------------------------------------ #
    # 7. PO Balance recalculation
    # ------------------------------------------------------------------ #

    @patch(f"{MODULE}.log_user_action")
    @patch(f"{MODULE}.create_exception")
    @patch(f"{MODULE}._get_physical_column_name", side_effect=lambda s, c: c)
    @patch(NOW_UAE_PATCH, return_value=FIXED_NOW)
    @patch(f"{MODULE}.format_datetime_for_smartsheet", return_value=FIXED_DATETIME_STR)
    @patch(f"{MODULE}.get_smartsheet_client")
    @patch(f"{MODULE}.generate_trace_id", return_value="TRACE-BAL")
    def test_po_balance_recalculated_on_quantity_change(
        self, mock_trace, mock_get_client, mock_fmt, mock_now,
        mock_phys, mock_create_exc, mock_log_action
    ):
        """PO_BALANCE_QUANTITY = new_qty - delivered when po_quantity_sqm changes."""
        from fn_lpo_update import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.find_row.return_value = _existing_lpo_row(
            po_qty=1000, delivered_qty=300
        )

        body = _base_body(po_quantity_sqm=900.0)
        main(_make_request(body))

        updates = mock_client.update_row.call_args[0][2]
        assert updates[Column.LPO_MASTER.PO_BALANCE_QUANTITY] == 900.0 - 300.0

    # ------------------------------------------------------------------ #
    # 8. Timestamp always updated
    # ------------------------------------------------------------------ #

    @patch(f"{MODULE}.log_user_action")
    @patch(f"{MODULE}.create_exception")
    @patch(f"{MODULE}._get_physical_column_name", side_effect=lambda s, c: c)
    @patch(NOW_UAE_PATCH, return_value=FIXED_NOW)
    @patch(f"{MODULE}.format_datetime_for_smartsheet", return_value=FIXED_DATETIME_STR)
    @patch(f"{MODULE}.get_smartsheet_client")
    @patch(f"{MODULE}.generate_trace_id", return_value="TRACE-TS")
    def test_updated_at_always_set(
        self, mock_trace, mock_get_client, mock_fmt, mock_now,
        mock_phys, mock_create_exc, mock_log_action
    ):
        """UPDATED_AT is set even when only one field changes."""
        from fn_lpo_update import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.find_row.return_value = _existing_lpo_row()

        body = _base_body(remarks="just a note")
        main(_make_request(body))

        updates = mock_client.update_row.call_args[0][2]
        assert updates[Column.LPO_MASTER.UPDATED_AT] == FIXED_DATETIME_STR

    # ------------------------------------------------------------------ #
    # 9. Audit log with old/new values
    # ------------------------------------------------------------------ #

    @patch(f"{MODULE}.log_user_action")
    @patch(f"{MODULE}.create_exception")
    @patch(f"{MODULE}._get_physical_column_name", side_effect=lambda s, c: c)
    @patch(NOW_UAE_PATCH, return_value=FIXED_NOW)
    @patch(f"{MODULE}.format_datetime_for_smartsheet", return_value=FIXED_DATETIME_STR)
    @patch(f"{MODULE}.get_smartsheet_client")
    @patch(f"{MODULE}.generate_trace_id", return_value="TRACE-AUDIT")
    def test_audit_log_records_old_and_new_values(
        self, mock_trace, mock_get_client, mock_fmt, mock_now,
        mock_phys, mock_create_exc, mock_log_action
    ):
        """log_user_action is called with JSON-encoded old/new value dicts."""
        from fn_lpo_update import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.find_row.return_value = _existing_lpo_row(
            customer_name="Old Customer"
        )

        body = _base_body(customer_name="New Customer")
        main(_make_request(body))

        mock_log_action.assert_called_once()
        kwargs = mock_log_action.call_args[1]
        assert kwargs["action_type"].value == "LPO_UPDATED"
        assert kwargs["user_id"] == "user@company.com"
        assert kwargs["target_id"] == "PTE-185"
        assert kwargs["target_table"] == Sheet.LPO_MASTER

        old_vals = json.loads(kwargs["old_value"])
        new_vals = json.loads(kwargs["new_value"])
        assert old_vals["customer_name"] == "Old Customer"
        assert new_vals["customer_name"] == "New Customer"

    # ------------------------------------------------------------------ #
    # 10. No changes — only timestamp
    # ------------------------------------------------------------------ #

    @patch(f"{MODULE}._get_physical_column_name", side_effect=lambda s, c: c)
    @patch(NOW_UAE_PATCH, return_value=FIXED_NOW)
    @patch(f"{MODULE}.format_datetime_for_smartsheet", return_value=FIXED_DATETIME_STR)
    @patch(f"{MODULE}.get_smartsheet_client")
    @patch(f"{MODULE}.generate_trace_id", return_value="TRACE-NOOP")
    def test_no_optional_fields_still_updates_timestamp(
        self, mock_trace, mock_get_client, mock_fmt, mock_now, mock_phys
    ):
        """
        When no optional update fields are provided, the timestamp is still added
        to updates dict, so the function proceeds to call update_row (not the
        'No changes' branch, because updates dict has the UPDATED_AT key).
        """
        from fn_lpo_update import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.find_row.return_value = _existing_lpo_row()

        # Only required fields, no optional update fields
        body = _base_body()
        with patch(f"{MODULE}.log_user_action"):
            resp = main(_make_request(body))

        assert resp.status_code == 200
        data = json.loads(resp.get_body())
        # The function still performs update_row (timestamp at minimum)
        # update_row is called because updates dict has UPDATED_AT
        mock_client.update_row.assert_called_once()

    # ------------------------------------------------------------------ #
    # 11. Individual field updates
    # ------------------------------------------------------------------ #

    @patch(f"{MODULE}.log_user_action")
    @patch(f"{MODULE}.create_exception")
    @patch(f"{MODULE}._get_physical_column_name", side_effect=lambda s, c: c)
    @patch(NOW_UAE_PATCH, return_value=FIXED_NOW)
    @patch(f"{MODULE}.format_datetime_for_smartsheet", return_value=FIXED_DATETIME_STR)
    @patch(f"{MODULE}.get_smartsheet_client")
    @patch(f"{MODULE}.generate_trace_id", return_value="TRACE-FIELD")
    def test_update_customer_lpo_ref(
        self, mock_trace, mock_get_client, mock_fmt, mock_now,
        mock_phys, mock_create_exc, mock_log_action
    ):
        """customer_lpo_ref is written and tracked in changes."""
        from fn_lpo_update import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.find_row.return_value = _existing_lpo_row()

        body = _base_body(customer_lpo_ref="CUST-NEW-001")
        resp = main(_make_request(body))

        assert resp.status_code == 200
        updates = mock_client.update_row.call_args[0][2]
        assert updates[Column.LPO_MASTER.CUSTOMER_LPO_REF] == "CUST-NEW-001"
        data = json.loads(resp.get_body())
        assert "customer_lpo_ref" in data["changes"]

    @patch(f"{MODULE}.log_user_action")
    @patch(f"{MODULE}.create_exception")
    @patch(f"{MODULE}._get_physical_column_name", side_effect=lambda s, c: c)
    @patch(NOW_UAE_PATCH, return_value=FIXED_NOW)
    @patch(f"{MODULE}.format_datetime_for_smartsheet", return_value=FIXED_DATETIME_STR)
    @patch(f"{MODULE}.get_smartsheet_client")
    @patch(f"{MODULE}.generate_trace_id", return_value="TRACE-FIELD2")
    def test_update_lpo_status(
        self, mock_trace, mock_get_client, mock_fmt, mock_now,
        mock_phys, mock_create_exc, mock_log_action
    ):
        """lpo_status field is written to the correct column."""
        from fn_lpo_update import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.find_row.return_value = _existing_lpo_row()

        body = _base_body(lpo_status="On Hold")
        resp = main(_make_request(body))

        assert resp.status_code == 200
        updates = mock_client.update_row.call_args[0][2]
        assert updates[Column.LPO_MASTER.LPO_STATUS] == "On Hold"
        data = json.loads(resp.get_body())
        assert "lpo_status" in data["changes"]

    @patch(f"{MODULE}.log_user_action")
    @patch(f"{MODULE}.create_exception")
    @patch(f"{MODULE}._get_physical_column_name", side_effect=lambda s, c: c)
    @patch(NOW_UAE_PATCH, return_value=FIXED_NOW)
    @patch(f"{MODULE}.format_datetime_for_smartsheet", return_value=FIXED_DATETIME_STR)
    @patch(f"{MODULE}.get_smartsheet_client")
    @patch(f"{MODULE}.generate_trace_id", return_value="TRACE-FIELD3")
    def test_update_terms_of_payment(
        self, mock_trace, mock_get_client, mock_fmt, mock_now,
        mock_phys, mock_create_exc, mock_log_action
    ):
        """terms_of_payment field is written to the correct column."""
        from fn_lpo_update import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.find_row.return_value = _existing_lpo_row()

        body = _base_body(terms_of_payment="60 Days Credit")
        resp = main(_make_request(body))

        assert resp.status_code == 200
        updates = mock_client.update_row.call_args[0][2]
        assert updates[Column.LPO_MASTER.TERMS_OF_PAYMENT] == "60 Days Credit"

    @patch(f"{MODULE}.log_user_action")
    @patch(f"{MODULE}.create_exception")
    @patch(f"{MODULE}._get_physical_column_name", side_effect=lambda s, c: c)
    @patch(NOW_UAE_PATCH, return_value=FIXED_NOW)
    @patch(f"{MODULE}.format_datetime_for_smartsheet", return_value=FIXED_DATETIME_STR)
    @patch(f"{MODULE}.get_smartsheet_client")
    @patch(f"{MODULE}.generate_trace_id", return_value="TRACE-FIELD4")
    def test_update_wastage_pct(
        self, mock_trace, mock_get_client, mock_fmt, mock_now,
        mock_phys, mock_create_exc, mock_log_action
    ):
        """wastage_pct is written as a string."""
        from fn_lpo_update import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.find_row.return_value = _existing_lpo_row()

        body = _base_body(wastage_pct=10.0)
        resp = main(_make_request(body))

        assert resp.status_code == 200
        updates = mock_client.update_row.call_args[0][2]
        assert updates[Column.LPO_MASTER.WASTAGE_CONSIDERED_IN_COSTING] == "10.0"

    @patch(f"{MODULE}.log_user_action")
    @patch(f"{MODULE}.create_exception")
    @patch(f"{MODULE}._get_physical_column_name", side_effect=lambda s, c: c)
    @patch(NOW_UAE_PATCH, return_value=FIXED_NOW)
    @patch(f"{MODULE}.format_datetime_for_smartsheet", return_value=FIXED_DATETIME_STR)
    @patch(f"{MODULE}.get_smartsheet_client")
    @patch(f"{MODULE}.generate_trace_id", return_value="TRACE-FIELD5")
    def test_update_hold_reason(
        self, mock_trace, mock_get_client, mock_fmt, mock_now,
        mock_phys, mock_create_exc, mock_log_action
    ):
        """hold_reason field is written to the correct column."""
        from fn_lpo_update import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.find_row.return_value = _existing_lpo_row()

        body = _base_body(hold_reason="Awaiting approval")
        resp = main(_make_request(body))

        assert resp.status_code == 200
        updates = mock_client.update_row.call_args[0][2]
        assert updates[Column.LPO_MASTER.HOLD_REASON] == "Awaiting approval"

    # ------------------------------------------------------------------ #
    # 12. Unhandled exception -> 500
    # ------------------------------------------------------------------ #

    @patch(f"{MODULE}._get_physical_column_name", side_effect=lambda s, c: c)
    @patch(f"{MODULE}.get_smartsheet_client")
    @patch(f"{MODULE}.generate_trace_id", return_value="TRACE-500")
    def test_unexpected_error_returns_500(
        self, mock_trace, mock_get_client, mock_phys
    ):
        """An unexpected exception during processing returns 500."""
        from fn_lpo_update import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.find_row.side_effect = RuntimeError("Connection reset")

        body = _base_body()
        resp = main(_make_request(body))

        assert resp.status_code == 500
        data = json.loads(resp.get_body())
        assert data["status"] == "ERROR"
        assert "Connection reset" in data["message"]

    # ------------------------------------------------------------------ #
    # 13. Response format
    # ------------------------------------------------------------------ #

    @patch(f"{MODULE}.log_user_action")
    @patch(f"{MODULE}.create_exception")
    @patch(f"{MODULE}._get_physical_column_name", side_effect=lambda s, c: c)
    @patch(NOW_UAE_PATCH, return_value=FIXED_NOW)
    @patch(f"{MODULE}.format_datetime_for_smartsheet", return_value=FIXED_DATETIME_STR)
    @patch(f"{MODULE}.get_smartsheet_client")
    @patch(f"{MODULE}.generate_trace_id", return_value="TRACE-MIME")
    def test_success_response_mimetype(
        self, mock_trace, mock_get_client, mock_fmt, mock_now,
        mock_phys, mock_create_exc, mock_log_action
    ):
        """Successful response has application/json mimetype."""
        from fn_lpo_update import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.find_row.return_value = _existing_lpo_row()

        body = _base_body(customer_name="Test")
        resp = main(_make_request(body))

        assert resp.mimetype == "application/json"

    @patch(f"{MODULE}.log_user_action")
    @patch(f"{MODULE}.create_exception", return_value="EX-MIME")
    @patch(f"{MODULE}.get_smartsheet_client")
    @patch(f"{MODULE}.generate_trace_id", return_value="TRACE-MIME2")
    def test_not_found_response_mimetype(
        self, mock_trace, mock_get_client, mock_create_exc, mock_log_action
    ):
        """404 response has application/json mimetype."""
        from fn_lpo_update import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.find_row.return_value = None

        resp = main(_make_request(_base_body()))
        assert resp.mimetype == "application/json"

    # ------------------------------------------------------------------ #
    # 14. Multiple fields updated simultaneously
    # ------------------------------------------------------------------ #

    @patch(f"{MODULE}.log_user_action")
    @patch(f"{MODULE}.create_exception")
    @patch(f"{MODULE}._get_physical_column_name", side_effect=lambda s, c: c)
    @patch(NOW_UAE_PATCH, return_value=FIXED_NOW)
    @patch(f"{MODULE}.format_datetime_for_smartsheet", return_value=FIXED_DATETIME_STR)
    @patch(f"{MODULE}.get_smartsheet_client")
    @patch(f"{MODULE}.generate_trace_id", return_value="TRACE-MULTI")
    def test_multiple_fields_updated_at_once(
        self, mock_trace, mock_get_client, mock_fmt, mock_now,
        mock_phys, mock_create_exc, mock_log_action
    ):
        """Updating several fields at once produces the correct changes list."""
        from fn_lpo_update import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.find_row.return_value = _existing_lpo_row()

        body = _base_body(
            customer_name="Multi Corp",
            project_name="Tower B",
            lpo_status="On Hold",
            hold_reason="Credit issue",
            remarks="Updated in batch",
        )
        resp = main(_make_request(body))

        assert resp.status_code == 200
        data = json.loads(resp.get_body())
        expected_changes = {
            "customer_name", "project_name", "lpo_status", "hold_reason", "remarks"
        }
        assert set(data["changes"]) == expected_changes

    # ------------------------------------------------------------------ #
    # 15. sap_reference coercion
    # ------------------------------------------------------------------ #

    @patch(f"{MODULE}.log_user_action")
    @patch(f"{MODULE}.create_exception", return_value="EX-COERCE")
    @patch(f"{MODULE}.get_smartsheet_client")
    @patch(f"{MODULE}.generate_trace_id", return_value="TRACE-COERCE")
    def test_sap_reference_coerced_to_string(
        self, mock_trace, mock_get_client, mock_create_exc, mock_log_action
    ):
        """Numeric sap_reference is coerced to string by the Pydantic model."""
        from fn_lpo_update import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.find_row.return_value = None  # Not found is fine for this test

        # Send numeric sap_reference
        body = {"sap_reference": 12345, "updated_by": "user@company.com"}
        main(_make_request(body))

        # find_row should be called with the string version
        call_args = mock_client.find_row.call_args
        assert call_args[0][2] == "12345"
