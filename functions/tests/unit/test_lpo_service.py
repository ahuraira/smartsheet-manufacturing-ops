"""
Unit Tests for LPO Service Module

Tests all LPO lookup, data extraction, and validation functions:
- find_lpo_by_sap_reference
- find_lpo_by_customer_ref
- find_lpo_flexible
- get_lpo_quantities (+ LPOQuantities properties)
- get_lpo_status
- get_lpo_sap_reference
- validate_lpo_status
- validate_po_balance
"""

import pytest
from unittest.mock import MagicMock, patch

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.lpo_service import (
    find_lpo_by_sap_reference,
    find_lpo_by_customer_ref,
    find_lpo_flexible,
    get_lpo_quantities,
    get_lpo_status,
    get_lpo_sap_reference,
    validate_lpo_status,
    validate_po_balance,
    LPOQuantities,
    LPOValidationStatus,
    LPOValidationResult,
)
from shared.logical_names import Sheet, Column


# =============================================================================
# Helper: Mock get_physical_column_name to return the logical name as-is.
# This makes building test dicts straightforward: use logical column names
# as dict keys (e.g., "PO_QUANTITY_SQM") instead of physical names.
# =============================================================================

def _identity_column(sheet, col):
    """Return the logical column name unchanged."""
    return col


# =============================================================================
# Lookup Functions
# =============================================================================

class TestFindLpoBySapReference:
    """Tests for find_lpo_by_sap_reference."""

    @pytest.mark.unit
    def test_returns_row_when_found(self):
        """Row dict is returned when SAP reference matches."""
        client = MagicMock()
        expected_row = {"row_id": 100, "SAP Reference": "PTE-185"}
        client.find_row.return_value = expected_row

        result = find_lpo_by_sap_reference(client, "PTE-185")

        assert result == expected_row
        client.find_row.assert_called_once_with(
            Sheet.LPO_MASTER,
            Column.LPO_MASTER.SAP_REFERENCE,
            "PTE-185",
        )

    @pytest.mark.unit
    def test_returns_none_when_not_found(self):
        """None is returned when no row matches."""
        client = MagicMock()
        client.find_row.return_value = None

        result = find_lpo_by_sap_reference(client, "NONEXISTENT")

        assert result is None

    @pytest.mark.unit
    def test_returns_none_for_empty_string(self):
        """None is returned and find_row is NOT called when sap_ref is empty."""
        client = MagicMock()

        result = find_lpo_by_sap_reference(client, "")

        assert result is None
        client.find_row.assert_not_called()

    @pytest.mark.unit
    def test_returns_none_for_none(self):
        """None is returned and find_row is NOT called when sap_ref is None."""
        client = MagicMock()

        result = find_lpo_by_sap_reference(client, None)

        assert result is None
        client.find_row.assert_not_called()

    @pytest.mark.unit
    def test_returns_none_for_falsy_zero(self):
        """Falsy values like 0 also skip the lookup."""
        client = MagicMock()

        result = find_lpo_by_sap_reference(client, 0)

        assert result is None
        client.find_row.assert_not_called()


class TestFindLpoByCustomerRef:
    """Tests for find_lpo_by_customer_ref."""

    @pytest.mark.unit
    def test_returns_row_when_found(self):
        """Row dict is returned when customer ref matches."""
        client = MagicMock()
        expected_row = {"row_id": 200, "Customer LPO Ref": "CUST-001"}
        client.find_row.return_value = expected_row

        result = find_lpo_by_customer_ref(client, "CUST-001")

        assert result == expected_row
        client.find_row.assert_called_once_with(
            Sheet.LPO_MASTER,
            Column.LPO_MASTER.CUSTOMER_LPO_REF,
            "CUST-001",
        )

    @pytest.mark.unit
    def test_returns_none_when_not_found(self):
        """None is returned when no row matches."""
        client = MagicMock()
        client.find_row.return_value = None

        result = find_lpo_by_customer_ref(client, "CUST-MISSING")

        assert result is None

    @pytest.mark.unit
    def test_returns_none_for_empty_string(self):
        """Empty customer ref skips lookup entirely."""
        client = MagicMock()

        result = find_lpo_by_customer_ref(client, "")

        assert result is None
        client.find_row.assert_not_called()

    @pytest.mark.unit
    def test_returns_none_for_none(self):
        """None customer ref skips lookup entirely."""
        client = MagicMock()

        result = find_lpo_by_customer_ref(client, None)

        assert result is None
        client.find_row.assert_not_called()


class TestFindLpoFlexible:
    """Tests for find_lpo_flexible (priority-based lookup)."""

    @pytest.mark.unit
    def test_sap_ref_found_returns_immediately(self):
        """When SAP ref matches, customer_ref lookup is never attempted."""
        client = MagicMock()
        sap_row = {"row_id": 1, "source": "sap"}
        client.find_row.return_value = sap_row

        result = find_lpo_flexible(client, sap_ref="PTE-100", customer_ref="CUST-001")

        assert result == sap_row
        # Only one call: SAP reference
        client.find_row.assert_called_once_with(
            Sheet.LPO_MASTER,
            Column.LPO_MASTER.SAP_REFERENCE,
            "PTE-100",
        )

    @pytest.mark.unit
    def test_sap_not_found_falls_through_to_customer(self):
        """When SAP ref misses, customer ref is tried next."""
        client = MagicMock()
        cust_row = {"row_id": 2, "source": "customer"}

        def side_effect(sheet, col, val):
            if col == Column.LPO_MASTER.SAP_REFERENCE:
                return None
            if col == Column.LPO_MASTER.CUSTOMER_LPO_REF:
                return cust_row
            return None

        client.find_row.side_effect = side_effect

        result = find_lpo_flexible(client, sap_ref="BAD", customer_ref="CUST-002")

        assert result == cust_row

    @pytest.mark.unit
    def test_sap_and_customer_not_found_tries_lpo_id_as_sap(self):
        """lpo_id is tried as SAP ref when both sap_ref and customer_ref miss."""
        client = MagicMock()
        lpo_row = {"row_id": 3, "source": "lpo_id_sap"}

        call_count = 0

        def side_effect(sheet, col, val):
            nonlocal call_count
            call_count += 1
            # First two calls miss (sap_ref, customer_ref), third matches (lpo_id as sap)
            if call_count == 3:
                return lpo_row
            return None

        client.find_row.side_effect = side_effect

        result = find_lpo_flexible(
            client, sap_ref="MISS", customer_ref="MISS", lpo_id="LPO-999"
        )

        assert result == lpo_row

    @pytest.mark.unit
    def test_lpo_id_tried_as_customer_ref_when_sap_misses(self):
        """lpo_id is tried as customer ref when SAP lookup of lpo_id also misses."""
        client = MagicMock()
        cust_row = {"row_id": 4, "source": "lpo_id_customer"}

        call_count = 0

        def side_effect(sheet, col, val):
            nonlocal call_count
            call_count += 1
            # All miss except the very last call (lpo_id as customer ref)
            if call_count == 4:
                return cust_row
            return None

        client.find_row.side_effect = side_effect

        result = find_lpo_flexible(
            client, sap_ref="MISS", customer_ref="MISS", lpo_id="LPO-999"
        )

        assert result == cust_row

    @pytest.mark.unit
    def test_all_none_returns_none(self):
        """All parameters None => returns None without any lookup."""
        client = MagicMock()

        result = find_lpo_flexible(client)

        assert result is None
        client.find_row.assert_not_called()

    @pytest.mark.unit
    def test_only_lpo_id_tries_both_lookups(self):
        """When only lpo_id is given, both SAP and customer ref lookups are attempted."""
        client = MagicMock()
        client.find_row.return_value = None

        result = find_lpo_flexible(client, lpo_id="LPO-555")

        assert result is None
        assert client.find_row.call_count == 2
        # First try SAP reference
        client.find_row.assert_any_call(
            Sheet.LPO_MASTER, Column.LPO_MASTER.SAP_REFERENCE, "LPO-555"
        )
        # Then try customer ref
        client.find_row.assert_any_call(
            Sheet.LPO_MASTER, Column.LPO_MASTER.CUSTOMER_LPO_REF, "LPO-555"
        )

    @pytest.mark.unit
    def test_only_customer_ref_provided(self):
        """When only customer_ref is given, sap_ref and lpo_id are skipped."""
        client = MagicMock()
        row = {"row_id": 10}
        client.find_row.return_value = row

        result = find_lpo_flexible(client, customer_ref="CUST-ONLY")

        assert result == row
        client.find_row.assert_called_once_with(
            Sheet.LPO_MASTER, Column.LPO_MASTER.CUSTOMER_LPO_REF, "CUST-ONLY"
        )

    @pytest.mark.unit
    def test_sap_ref_not_found_but_no_customer_ref_tries_nothing_else(self):
        """sap_ref miss without customer_ref or lpo_id returns None."""
        client = MagicMock()
        client.find_row.return_value = None

        result = find_lpo_flexible(client, sap_ref="MISS")

        assert result is None
        assert client.find_row.call_count == 1


# =============================================================================
# Data Extraction Helpers
# =============================================================================

@patch("shared.lpo_service.get_physical_column_name", side_effect=_identity_column)
class TestGetLpoQuantities:
    """Tests for get_lpo_quantities and LPOQuantities dataclass."""

    @pytest.mark.unit
    def test_extracts_all_quantities(self, _mock_col):
        """All four quantity fields are extracted from the LPO dict."""
        lpo = {
            "PO_QUANTITY_SQM": "1000",
            "DELIVERED_QUANTITY_SQM": "200",
            "PLANNED_QUANTITY": "300",
            "ALLOCATED_QUANTITY": "100",
        }

        q = get_lpo_quantities(lpo)

        assert q.po_quantity == 1000.0
        assert q.delivered_quantity == 200.0
        assert q.planned_quantity == 300.0
        assert q.allocated_quantity == 100.0

    @pytest.mark.unit
    def test_total_committed(self, _mock_col):
        """total_committed = delivered + planned + allocated."""
        lpo = {
            "PO_QUANTITY_SQM": "1000",
            "DELIVERED_QUANTITY_SQM": "100",
            "PLANNED_QUANTITY": "200",
            "ALLOCATED_QUANTITY": "50",
        }

        q = get_lpo_quantities(lpo)

        assert q.total_committed == 350.0  # 100 + 200 + 50

    @pytest.mark.unit
    def test_available_balance(self, _mock_col):
        """available_balance = po_quantity - total_committed."""
        lpo = {
            "PO_QUANTITY_SQM": "1000",
            "DELIVERED_QUANTITY_SQM": "100",
            "PLANNED_QUANTITY": "200",
            "ALLOCATED_QUANTITY": "50",
        }

        q = get_lpo_quantities(lpo)

        assert q.available_balance == 650.0  # 1000 - 350

    @pytest.mark.unit
    def test_handles_missing_values(self, _mock_col):
        """Missing keys default to 0 via parse_float_safe."""
        lpo = {"PO_QUANTITY_SQM": "500"}

        q = get_lpo_quantities(lpo)

        assert q.po_quantity == 500.0
        assert q.delivered_quantity == 0.0
        assert q.planned_quantity == 0.0
        assert q.allocated_quantity == 0.0
        assert q.total_committed == 0.0
        assert q.available_balance == 500.0

    @pytest.mark.unit
    def test_handles_none_values(self, _mock_col):
        """Explicit None values default to 0."""
        lpo = {
            "PO_QUANTITY_SQM": None,
            "DELIVERED_QUANTITY_SQM": None,
            "PLANNED_QUANTITY": None,
            "ALLOCATED_QUANTITY": None,
        }

        q = get_lpo_quantities(lpo)

        assert q.po_quantity == 0.0
        assert q.total_committed == 0.0
        assert q.available_balance == 0.0

    @pytest.mark.unit
    def test_handles_na_strings(self, _mock_col):
        """'N/A' values from Smartsheet are treated as 0."""
        lpo = {
            "PO_QUANTITY_SQM": "N/A",
            "DELIVERED_QUANTITY_SQM": "N/A",
            "PLANNED_QUANTITY": "N/A",
            "ALLOCATED_QUANTITY": "N/A",
        }

        q = get_lpo_quantities(lpo)

        assert q.po_quantity == 0.0

    @pytest.mark.unit
    def test_handles_float_strings(self, _mock_col):
        """Float-looking strings are parsed correctly."""
        lpo = {
            "PO_QUANTITY_SQM": "1000.50",
            "DELIVERED_QUANTITY_SQM": "200.25",
            "PLANNED_QUANTITY": "0",
            "ALLOCATED_QUANTITY": "0",
        }

        q = get_lpo_quantities(lpo)

        assert q.po_quantity == 1000.50
        assert q.delivered_quantity == 200.25

    @pytest.mark.unit
    def test_handles_numeric_floats(self, _mock_col):
        """Raw float values (not strings) are handled."""
        lpo = {
            "PO_QUANTITY_SQM": 750.0,
            "DELIVERED_QUANTITY_SQM": 100.5,
            "PLANNED_QUANTITY": 0.0,
            "ALLOCATED_QUANTITY": 50.0,
        }

        q = get_lpo_quantities(lpo)

        assert q.po_quantity == 750.0
        assert q.delivered_quantity == 100.5

    @pytest.mark.unit
    def test_negative_available_balance(self, _mock_col):
        """Over-committed LPO yields a negative available_balance."""
        lpo = {
            "PO_QUANTITY_SQM": "100",
            "DELIVERED_QUANTITY_SQM": "80",
            "PLANNED_QUANTITY": "50",
            "ALLOCATED_QUANTITY": "20",
        }

        q = get_lpo_quantities(lpo)

        assert q.total_committed == 150.0
        assert q.available_balance == -50.0

    @pytest.mark.unit
    def test_empty_dict(self, _mock_col):
        """Completely empty dict defaults all to 0."""
        q = get_lpo_quantities({})

        assert q.po_quantity == 0.0
        assert q.total_committed == 0.0
        assert q.available_balance == 0.0


@patch("shared.lpo_service.get_physical_column_name", side_effect=_identity_column)
class TestGetLpoStatus:
    """Tests for get_lpo_status."""

    @pytest.mark.unit
    def test_returns_lowercase(self, _mock_col):
        """Status is normalized to lowercase."""
        lpo = {"LPO_STATUS": "Active"}
        assert get_lpo_status(lpo) == "active"

    @pytest.mark.unit
    def test_returns_lowercase_on_hold(self, _mock_col):
        """'On Hold' becomes 'on hold'."""
        lpo = {"LPO_STATUS": "On Hold"}
        assert get_lpo_status(lpo) == "on hold"

    @pytest.mark.unit
    def test_returns_empty_for_missing(self, _mock_col):
        """Missing status key returns empty string."""
        assert get_lpo_status({}) == ""

    @pytest.mark.unit
    def test_returns_empty_for_none(self, _mock_col):
        """None status returns empty string."""
        lpo = {"LPO_STATUS": None}
        assert get_lpo_status(lpo) == ""

    @pytest.mark.unit
    def test_returns_empty_for_empty_string(self, _mock_col):
        """Empty string status stays empty."""
        lpo = {"LPO_STATUS": ""}
        assert get_lpo_status(lpo) == ""

    @pytest.mark.unit
    def test_mixed_case(self, _mock_col):
        """Mixed case is normalized."""
        lpo = {"LPO_STATUS": "COMPLETED"}
        assert get_lpo_status(lpo) == "completed"


@patch("shared.lpo_service.get_physical_column_name", side_effect=_identity_column)
class TestGetLpoSapReference:
    """Tests for get_lpo_sap_reference."""

    @pytest.mark.unit
    def test_returns_sap_reference(self, _mock_col):
        """SAP Reference value is returned."""
        lpo = {"SAP_REFERENCE": "PTE-185"}
        assert get_lpo_sap_reference(lpo) == "PTE-185"

    @pytest.mark.unit
    def test_returns_none_for_missing(self, _mock_col):
        """None is returned when key is absent."""
        assert get_lpo_sap_reference({}) is None

    @pytest.mark.unit
    def test_returns_none_value(self, _mock_col):
        """Explicit None value is returned as-is."""
        lpo = {"SAP_REFERENCE": None}
        assert get_lpo_sap_reference(lpo) is None


# =============================================================================
# Validation Functions
# =============================================================================

@patch("shared.lpo_service.get_physical_column_name", side_effect=_identity_column)
class TestValidateLpoStatus:
    """Tests for validate_lpo_status."""

    @pytest.mark.unit
    def test_none_lpo_returns_not_found(self, _mock_col):
        """None input yields NOT_FOUND status."""
        result = validate_lpo_status(None)

        assert result.status == LPOValidationStatus.NOT_FOUND
        assert "not found" in result.message.lower()
        assert result.lpo is None

    @pytest.mark.unit
    def test_empty_dict_returns_not_found(self, _mock_col):
        """Empty dict (falsy) yields NOT_FOUND."""
        result = validate_lpo_status({})

        assert result.status == LPOValidationStatus.NOT_FOUND

    @pytest.mark.unit
    def test_on_hold_status(self, _mock_col):
        """LPO with 'On Hold' status returns ON_HOLD."""
        lpo = {"LPO_STATUS": "On Hold", "SAP_REFERENCE": "PTE-300"}

        result = validate_lpo_status(lpo)

        assert result.status == LPOValidationStatus.ON_HOLD
        assert "on hold" in result.message.lower()
        assert "PTE-300" in result.message
        assert result.lpo is lpo

    @pytest.mark.unit
    def test_on_hold_lowercase(self, _mock_col):
        """LPO with lowercase 'on hold' is also caught."""
        lpo = {"LPO_STATUS": "on hold", "SAP_REFERENCE": "PTE-301"}

        result = validate_lpo_status(lpo)

        assert result.status == LPOValidationStatus.ON_HOLD

    @pytest.mark.unit
    def test_active_status_returns_ok(self, _mock_col):
        """Active LPO returns OK."""
        lpo = {"LPO_STATUS": "Active", "SAP_REFERENCE": "PTE-100"}

        result = validate_lpo_status(lpo)

        assert result.status == LPOValidationStatus.OK
        assert result.lpo is lpo

    @pytest.mark.unit
    def test_empty_status_returns_ok(self, _mock_col):
        """Missing/empty status is treated as OK (not on hold)."""
        lpo = {"LPO_STATUS": "", "SAP_REFERENCE": "PTE-101"}

        result = validate_lpo_status(lpo)

        assert result.status == LPOValidationStatus.OK

    @pytest.mark.unit
    def test_none_status_returns_ok(self, _mock_col):
        """None status field is treated as OK."""
        lpo = {"LPO_STATUS": None, "SAP_REFERENCE": "PTE-102"}

        result = validate_lpo_status(lpo)

        assert result.status == LPOValidationStatus.OK

    @pytest.mark.unit
    def test_on_hold_missing_sap_ref_uses_unknown(self, _mock_col):
        """On-hold LPO without SAP ref uses 'unknown' in message."""
        lpo = {"LPO_STATUS": "On Hold"}

        result = validate_lpo_status(lpo)

        assert result.status == LPOValidationStatus.ON_HOLD
        assert "unknown" in result.message.lower()

    @pytest.mark.unit
    def test_completed_status_returns_ok(self, _mock_col):
        """Any non-hold status (e.g. Completed) returns OK."""
        lpo = {"LPO_STATUS": "Completed"}

        result = validate_lpo_status(lpo)

        assert result.status == LPOValidationStatus.OK


@patch("shared.lpo_service.get_physical_column_name", side_effect=_identity_column)
class TestValidatePoBalance:
    """Tests for validate_po_balance."""

    @pytest.mark.unit
    def test_none_lpo_returns_not_found(self, _mock_col):
        """None input yields NOT_FOUND."""
        result = validate_po_balance(None, 100.0)

        assert result.status == LPOValidationStatus.NOT_FOUND
        assert result.quantities is None

    @pytest.mark.unit
    def test_empty_dict_returns_not_found(self, _mock_col):
        """Empty dict (falsy) yields NOT_FOUND."""
        result = validate_po_balance({}, 100.0)

        assert result.status == LPOValidationStatus.NOT_FOUND

    @pytest.mark.unit
    def test_sufficient_balance_returns_ok(self, _mock_col):
        """Request within available balance passes."""
        lpo = {
            "PO_QUANTITY_SQM": "1000",
            "DELIVERED_QUANTITY_SQM": "100",
            "PLANNED_QUANTITY": "200",
            "ALLOCATED_QUANTITY": "100",
            "SAP_REFERENCE": "PTE-200",
        }

        result = validate_po_balance(lpo, 300.0)

        assert result.status == LPOValidationStatus.OK
        assert result.quantities is not None
        assert result.quantities.po_quantity == 1000.0
        assert result.lpo is lpo

    @pytest.mark.unit
    def test_insufficient_balance_returns_error(self, _mock_col):
        """Request exceeding PO balance (with tolerance) fails."""
        lpo = {
            "PO_QUANTITY_SQM": "1000",
            "DELIVERED_QUANTITY_SQM": "500",
            "PLANNED_QUANTITY": "300",
            "ALLOCATED_QUANTITY": "200",
            "SAP_REFERENCE": "PTE-201",
        }
        # committed = 1000, requesting 100 => new_total = 1100
        # max_allowed = 1000 * 1.05 = 1050
        # 1100 > 1050 => INSUFFICIENT

        result = validate_po_balance(lpo, 100.0)

        assert result.status == LPOValidationStatus.INSUFFICIENT_BALANCE
        assert result.quantities is not None
        assert "Insufficient" in result.message or "balance" in result.message.lower()

    @pytest.mark.unit
    def test_default_tolerance_allows_5_percent_over(self, _mock_col):
        """5% tolerance: total committed + requested <= PO * 1.05 passes."""
        lpo = {
            "PO_QUANTITY_SQM": "1000",
            "DELIVERED_QUANTITY_SQM": "0",
            "PLANNED_QUANTITY": "0",
            "ALLOCATED_QUANTITY": "0",
        }
        # max_allowed = 1000 * 1.05 = 1050
        # requesting 1050 => new_total = 1050, 1050 <= 1050 => OK

        result = validate_po_balance(lpo, 1050.0)

        assert result.status == LPOValidationStatus.OK

    @pytest.mark.unit
    def test_default_tolerance_rejects_above_5_percent(self, _mock_col):
        """Slightly above 5% tolerance fails."""
        lpo = {
            "PO_QUANTITY_SQM": "1000",
            "DELIVERED_QUANTITY_SQM": "0",
            "PLANNED_QUANTITY": "0",
            "ALLOCATED_QUANTITY": "0",
        }
        # max_allowed = 1050, requesting 1050.01 => FAIL

        result = validate_po_balance(lpo, 1050.01)

        assert result.status == LPOValidationStatus.INSUFFICIENT_BALANCE

    @pytest.mark.unit
    def test_custom_tolerance_10_percent(self, _mock_col):
        """Custom 10% tolerance allows higher overage."""
        lpo = {
            "PO_QUANTITY_SQM": "1000",
            "DELIVERED_QUANTITY_SQM": "0",
            "PLANNED_QUANTITY": "0",
            "ALLOCATED_QUANTITY": "0",
        }
        # max_allowed = 1000 * 1.10 = 1100
        # requesting 1100 => new_total = 1100, 1100 <= 1100 => OK

        result = validate_po_balance(lpo, 1100.0, tolerance_pct=0.10)

        assert result.status == LPOValidationStatus.OK

    @pytest.mark.unit
    def test_custom_tolerance_10_percent_rejects_above(self, _mock_col):
        """Custom 10% tolerance rejects above threshold."""
        lpo = {
            "PO_QUANTITY_SQM": "1000",
            "DELIVERED_QUANTITY_SQM": "0",
            "PLANNED_QUANTITY": "0",
            "ALLOCATED_QUANTITY": "0",
        }
        # max_allowed = 1100, requesting 1100.01 => FAIL

        result = validate_po_balance(lpo, 1100.01, tolerance_pct=0.10)

        assert result.status == LPOValidationStatus.INSUFFICIENT_BALANCE

    @pytest.mark.unit
    def test_zero_tolerance(self, _mock_col):
        """0% tolerance means exact PO quantity is the limit."""
        lpo = {
            "PO_QUANTITY_SQM": "500",
            "DELIVERED_QUANTITY_SQM": "0",
            "PLANNED_QUANTITY": "0",
            "ALLOCATED_QUANTITY": "0",
        }

        ok = validate_po_balance(lpo, 500.0, tolerance_pct=0.0)
        assert ok.status == LPOValidationStatus.OK

        fail = validate_po_balance(lpo, 500.01, tolerance_pct=0.0)
        assert fail.status == LPOValidationStatus.INSUFFICIENT_BALANCE

    @pytest.mark.unit
    def test_quantities_populated_on_ok(self, _mock_col):
        """On OK, quantities are attached to the result."""
        lpo = {
            "PO_QUANTITY_SQM": "500",
            "DELIVERED_QUANTITY_SQM": "50",
            "PLANNED_QUANTITY": "100",
            "ALLOCATED_QUANTITY": "25",
        }

        result = validate_po_balance(lpo, 100.0)

        assert result.status == LPOValidationStatus.OK
        assert result.quantities.po_quantity == 500.0
        assert result.quantities.total_committed == 175.0
        assert result.quantities.available_balance == 325.0

    @pytest.mark.unit
    def test_quantities_populated_on_failure(self, _mock_col):
        """On INSUFFICIENT_BALANCE, quantities are also attached."""
        lpo = {
            "PO_QUANTITY_SQM": "100",
            "DELIVERED_QUANTITY_SQM": "90",
            "PLANNED_QUANTITY": "10",
            "ALLOCATED_QUANTITY": "5",
        }

        result = validate_po_balance(lpo, 50.0)

        assert result.status == LPOValidationStatus.INSUFFICIENT_BALANCE
        assert result.quantities is not None
        assert result.quantities.total_committed == 105.0

    @pytest.mark.unit
    def test_message_contains_quantities_on_failure(self, _mock_col):
        """Failure message includes PO, committed, and requested values."""
        lpo = {
            "PO_QUANTITY_SQM": "1000",
            "DELIVERED_QUANTITY_SQM": "900",
            "PLANNED_QUANTITY": "100",
            "ALLOCATED_QUANTITY": "50",
            "SAP_REFERENCE": "PTE-999",
        }

        result = validate_po_balance(lpo, 200.0)

        assert result.status == LPOValidationStatus.INSUFFICIENT_BALANCE
        assert "1000" in result.message
        assert "1050" in result.message  # total committed
        assert "200" in result.message   # requested

    @pytest.mark.unit
    def test_zero_requested_is_always_ok(self, _mock_col):
        """Requesting 0 qty should always pass."""
        lpo = {
            "PO_QUANTITY_SQM": "100",
            "DELIVERED_QUANTITY_SQM": "100",
            "PLANNED_QUANTITY": "0",
            "ALLOCATED_QUANTITY": "0",
        }

        result = validate_po_balance(lpo, 0.0)

        # committed=100, requested=0, new_total=100, max=105 => OK
        assert result.status == LPOValidationStatus.OK

    @pytest.mark.unit
    def test_partial_committed_with_tolerance(self, _mock_col):
        """Partially committed LPO with tolerance boundary."""
        lpo = {
            "PO_QUANTITY_SQM": "1000",
            "DELIVERED_QUANTITY_SQM": "400",
            "PLANNED_QUANTITY": "300",
            "ALLOCATED_QUANTITY": "200",
        }
        # committed = 900, max_allowed = 1050
        # requesting 150 => new_total = 1050, 1050 <= 1050 => OK

        result = validate_po_balance(lpo, 150.0)

        assert result.status == LPOValidationStatus.OK


# =============================================================================
# LPOQuantities Dataclass Unit Tests
# =============================================================================

class TestLPOQuantitiesDataclass:
    """Standalone tests for the LPOQuantities dataclass properties."""

    @pytest.mark.unit
    def test_total_committed_sum(self):
        """total_committed is the sum of delivered, planned, and allocated."""
        q = LPOQuantities(
            po_quantity=1000.0,
            delivered_quantity=100.0,
            planned_quantity=200.0,
            allocated_quantity=300.0,
        )
        assert q.total_committed == 600.0

    @pytest.mark.unit
    def test_available_balance_calculation(self):
        """available_balance = po_quantity - total_committed."""
        q = LPOQuantities(
            po_quantity=1000.0,
            delivered_quantity=100.0,
            planned_quantity=200.0,
            allocated_quantity=300.0,
        )
        assert q.available_balance == 400.0

    @pytest.mark.unit
    def test_all_zeros(self):
        """All-zero quantities work correctly."""
        q = LPOQuantities(0.0, 0.0, 0.0, 0.0)

        assert q.total_committed == 0.0
        assert q.available_balance == 0.0

    @pytest.mark.unit
    def test_over_committed(self):
        """Over-committed gives negative available_balance."""
        q = LPOQuantities(
            po_quantity=100.0,
            delivered_quantity=60.0,
            planned_quantity=30.0,
            allocated_quantity=20.0,
        )
        assert q.total_committed == 110.0
        assert q.available_balance == -10.0


# =============================================================================
# LPOValidationResult Dataclass
# =============================================================================

class TestLPOValidationResult:
    """Tests for LPOValidationResult defaults."""

    @pytest.mark.unit
    def test_default_fields(self):
        """Optional fields default to None."""
        r = LPOValidationResult(
            status=LPOValidationStatus.OK, message="good"
        )
        assert r.lpo is None
        assert r.quantities is None

    @pytest.mark.unit
    def test_status_enum_values(self):
        """All enum values have expected string representations."""
        assert LPOValidationStatus.OK.value == "OK"
        assert LPOValidationStatus.NOT_FOUND.value == "NOT_FOUND"
        assert LPOValidationStatus.ON_HOLD.value == "ON_HOLD"
        assert LPOValidationStatus.INSUFFICIENT_BALANCE.value == "INSUFFICIENT_BALANCE"
