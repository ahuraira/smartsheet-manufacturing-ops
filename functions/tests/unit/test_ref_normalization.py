"""
Unit Tests for Reference Value Normalization

Validates that numeric-looking reference values (SAP Reference, DO Number,
Invoice Number, etc.) match correctly regardless of whether Smartsheet
returns them as floats or strings.

Root cause: Smartsheet TEXT_NUMBER columns may return numeric values as
floats (12345 → 12345.0). Without normalization, find_row("12345")
fails to match a cell containing 12345.0.
"""

import pytest

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.helpers import normalize_ref_value
from shared.smartsheet_client import SmartsheetClient


# ---------------------------------------------------------------------------
# normalize_ref_value() helper tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestNormalizeRefValue:

    def test_float_integer(self):
        assert normalize_ref_value(12345.0) == "12345"

    def test_float_with_trailing_zeros(self):
        assert normalize_ref_value(99999.00) == "99999"

    def test_string_integer(self):
        assert normalize_ref_value("12345") == "12345"

    def test_string_float_integer(self):
        assert normalize_ref_value("12345.0") == "12345"

    def test_string_with_prefix(self):
        assert normalize_ref_value("PTE-185") == "PTE-185"

    def test_string_do_number(self):
        assert normalize_ref_value("DO-0001") == "DO-0001"

    def test_none_returns_empty(self):
        assert normalize_ref_value(None) == ""

    def test_float_with_decimal(self):
        """Non-integer floats should preserve the decimal part."""
        assert normalize_ref_value(12345.5) == "12345.5"

    def test_float_100(self):
        """10.0, 100.0, etc. should not lose significant zeros."""
        assert normalize_ref_value(10.0) == "10"
        assert normalize_ref_value(100.0) == "100"
        assert normalize_ref_value(1000.0) == "1000"

    def test_int_passthrough(self):
        assert normalize_ref_value(42) == "42"

    def test_whitespace_stripped(self):
        assert normalize_ref_value("  12345  ") == "12345"

    def test_bool_passthrough(self):
        assert normalize_ref_value(True) == "True"


# ---------------------------------------------------------------------------
# SmartsheetClient._normalize_for_comparison() tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestClientNormalize:

    def test_float_to_string(self):
        assert SmartsheetClient._normalize_for_comparison(12345.0) == "12345"

    def test_string_stays_same(self):
        assert SmartsheetClient._normalize_for_comparison("PTE-185") == "PTE-185"

    def test_none_to_empty(self):
        assert SmartsheetClient._normalize_for_comparison(None) == ""

    def test_matches_normalize_ref_value(self):
        """Client normalize should produce same results as helpers version."""
        test_cases = [12345.0, "12345", "PTE-185", None, 10.0, 100.0, 42, "DO-0001"]
        for val in test_cases:
            assert (
                SmartsheetClient._normalize_for_comparison(val)
                == normalize_ref_value(val)
            ), f"Mismatch for {val!r}"


# ---------------------------------------------------------------------------
# MockSmartsheetStorage find_rows normalization tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestMockStorageFindRows:
    """Tests that the mock storage matches real client normalization."""

    def test_find_row_float_cell_with_string_search(self, mock_storage):
        """Cell value 12345.0 found when searching for string '12345'."""
        mock_storage.add_row("01 LPO Master LOG", {"SAP Reference": 12345.0})
        rows = mock_storage.find_rows("01 LPO Master LOG", "SAP Reference", "12345")
        assert len(rows) == 1

    def test_find_row_string_cell_with_float_search(self, mock_storage):
        """Cell value '12345' found when searching with float 12345.0."""
        mock_storage.add_row("01 LPO Master LOG", {"SAP Reference": "12345"})
        rows = mock_storage.find_rows("01 LPO Master LOG", "SAP Reference", 12345.0)
        assert len(rows) == 1

    def test_find_row_exact_string_match(self, mock_storage):
        """Exact string match still works."""
        mock_storage.add_row("01 LPO Master LOG", {"SAP Reference": "PTE-185"})
        rows = mock_storage.find_rows("01 LPO Master LOG", "SAP Reference", "PTE-185")
        assert len(rows) == 1

    def test_find_row_no_false_positive(self, mock_storage):
        """Different values should not match."""
        mock_storage.add_row("01 LPO Master LOG", {"SAP Reference": "12345"})
        rows = mock_storage.find_rows("01 LPO Master LOG", "SAP Reference", "54321")
        assert len(rows) == 0


# ---------------------------------------------------------------------------
# MockSmartsheetClient find_row normalization tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestMockClientFindRow:
    """Tests that MockSmartsheetClient.find_row handles float/string."""

    def test_lpo_lookup_float_sap_ref(self, mock_storage):
        """LPO with SAP ref 12345.0 found when searching '12345'."""
        from tests.conftest import MockSmartsheetClient

        mock_storage.add_row("01 LPO Master LOG", {
            "SAP Reference": 12345.0,
            "Customer Name": "Test",
        })

        client = MockSmartsheetClient(mock_storage)
        result = client.find_row("LPO_MASTER", "SAP_REFERENCE", "12345")
        assert result is not None
        assert result["Customer Name"] == "Test"

    def test_lpo_lookup_string_sap_ref_with_float_search(self, mock_storage):
        """LPO with SAP ref '12345' found when searching 12345.0."""
        from tests.conftest import MockSmartsheetClient

        mock_storage.add_row("01 LPO Master LOG", {
            "SAP Reference": "12345",
            "Customer Name": "Test",
        })

        client = MockSmartsheetClient(mock_storage)
        result = client.find_row("LPO_MASTER", "SAP_REFERENCE", 12345.0)
        assert result is not None

    def test_tag_lookup_float_lpo_ref(self, mock_storage):
        """Tag with LPO SAP ref 99999.0 found when searching '99999'."""
        from tests.conftest import MockSmartsheetClient

        mock_storage.add_row("Tag Sheet Registry", {
            "Tag ID": "TAG-001",
            "LPO SAP Reference Link": 99999.0,
        })

        client = MockSmartsheetClient(mock_storage)
        result = client.find_row("TAG_REGISTRY", "LPO_SAP_REFERENCE", "99999")
        assert result is not None
        assert result["Tag ID"] == "TAG-001"

    def test_delivery_lookup_float_do_number(self, mock_storage):
        """Delivery with DO number 77777.0 found when searching '77777'."""
        from tests.conftest import MockSmartsheetClient

        mock_storage.add_row("07 Delivery Log", {
            "SAP DO Number": 77777.0,
            "Delivery ID": "DO-0001",
        })

        client = MockSmartsheetClient(mock_storage)
        result = client.find_row("DELIVERY_LOG", "SAP_DO_NUMBER", "77777")
        assert result is not None
        assert result["Delivery ID"] == "DO-0001"


# ---------------------------------------------------------------------------
# Integration: LPO ingest with numeric SAP reference
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestNumericSapRefIntegration:
    """The exact scenario the user reported: numeric SAP ref in LPO + tag."""

    def test_tag_finds_lpo_with_numeric_sap_ref(self, mock_storage):
        """
        User enters 12345 as SAP ref in LPO and tag sheet.
        LPO stores it as 12345.0 (float), tag searches for "12345" (string).
        The lookup must succeed.
        """
        from tests.conftest import MockSmartsheetClient

        # LPO stored with float (as Smartsheet might do)
        mock_storage.add_row("01 LPO Master LOG", {
            "SAP Reference": 12345.0,
            "Customer Name": "Acme",
            "LPO Status": "Active",
            "PO Quantity (Sqm)": 1000.0,
        })

        client = MockSmartsheetClient(mock_storage)

        # Tag lookup with string (as Pydantic model produces)
        lpo = client.find_row("LPO_MASTER", "SAP_REFERENCE", "12345")
        assert lpo is not None, "LPO lookup failed: 12345.0 should match '12345'"
        assert lpo["Customer Name"] == "Acme"

    def test_dedup_with_numeric_client_request_id(self, mock_storage):
        """
        client_request_id stored as float also matches string search.
        """
        from tests.conftest import MockSmartsheetClient

        mock_storage.add_row("01 LPO Master LOG", {
            "SAP Reference": "PTE-185",
            "Client Request ID": 98765.0,
        })

        client = MockSmartsheetClient(mock_storage)
        result = client.find_row("LPO_MASTER", "CLIENT_REQUEST_ID", "98765")
        assert result is not None
