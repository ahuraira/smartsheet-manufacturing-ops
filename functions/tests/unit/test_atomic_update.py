"""
Unit Tests for Atomic Update Module

Tests optimistic-locking helpers:
- atomic_increment: read-modify-write with collision retry
- atomic_set_if_equals: compare-and-swap
- AtomicUpdateResult.__bool__
"""

import pytest
from unittest.mock import MagicMock, patch, call

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.atomic_update import (
    atomic_increment,
    atomic_set_if_equals,
    AtomicUpdateResult,
)


# =============================================================================
# Helpers
# =============================================================================

def _make_mock_manifest(physical_col="Physical Column"):
    """Create a mock manifest whose get_column_name returns the given name."""
    manifest = MagicMock()
    manifest.get_column_name.return_value = physical_col
    return manifest


def _make_mock_manifest_missing():
    """Create a mock manifest that cannot resolve the column."""
    manifest = MagicMock()
    manifest.get_column_name.return_value = None
    return manifest


def _collision_error(msg="Error 4004: save collision"):
    """Create an exception that looks like a Smartsheet collision."""
    return Exception(msg)


# =============================================================================
# AtomicUpdateResult
# =============================================================================

class TestAtomicUpdateResult:
    """Tests for the AtomicUpdateResult dataclass."""

    @pytest.mark.unit
    def test_bool_true_on_success(self):
        """Truthy when success is True."""
        r = AtomicUpdateResult(success=True)
        assert bool(r) is True
        assert r  # implicit bool

    @pytest.mark.unit
    def test_bool_false_on_failure(self):
        """Falsy when success is False."""
        r = AtomicUpdateResult(success=False)
        assert bool(r) is False
        assert not r  # implicit bool

    @pytest.mark.unit
    def test_default_values(self):
        """Optional fields have sensible defaults."""
        r = AtomicUpdateResult(success=True)
        assert r.old_value is None
        assert r.new_value is None
        assert r.retries_used == 0
        assert r.error_message is None
        assert r.error_code is None

    @pytest.mark.unit
    def test_fields_populated(self):
        """All fields can be set explicitly."""
        r = AtomicUpdateResult(
            success=False,
            old_value=10.0,
            new_value=20.0,
            retries_used=3,
            error_message="boom",
            error_code="UPDATE_ERROR",
        )
        assert r.old_value == 10.0
        assert r.new_value == 20.0
        assert r.retries_used == 3
        assert r.error_message == "boom"
        assert r.error_code == "UPDATE_ERROR"


# =============================================================================
# atomic_increment
# =============================================================================

@patch("shared.atomic_update.time.sleep")  # avoid real delays
@patch("shared.atomic_update.get_manifest")
class TestAtomicIncrement:
    """Tests for atomic_increment."""

    # ---- Column / Row resolution errors ----

    @pytest.mark.unit
    def test_column_not_found(self, mock_get_manifest, _mock_sleep):
        """Returns COLUMN_NOT_FOUND when manifest cannot resolve the column."""
        mock_get_manifest.return_value = _make_mock_manifest_missing()
        client = MagicMock()

        result = atomic_increment(
            client, "LPO_MASTER", 123, "BAD_COLUMN", 10.0, trace_id="t1"
        )

        assert not result
        assert result.error_code == "COLUMN_NOT_FOUND"
        assert "BAD_COLUMN" in result.error_message
        client.get_row.assert_not_called()

    @pytest.mark.unit
    def test_row_not_found(self, mock_get_manifest, _mock_sleep):
        """Returns ROW_NOT_FOUND when client.get_row returns None."""
        mock_get_manifest.return_value = _make_mock_manifest("Allocated Quantity")
        client = MagicMock()
        client.get_row.return_value = None

        result = atomic_increment(
            client, "LPO_MASTER", 999, "ALLOCATED_QUANTITY", 5.0, trace_id="t2"
        )

        assert not result
        assert result.error_code == "ROW_NOT_FOUND"
        assert "999" in result.error_message

    # ---- Happy path ----

    @pytest.mark.unit
    def test_happy_path_increment(self, mock_get_manifest, _mock_sleep):
        """Reads current value, adds increment, updates row, returns success."""
        mock_get_manifest.return_value = _make_mock_manifest("Allocated Quantity")
        client = MagicMock()
        client.get_row.return_value = {"Allocated Quantity": "100"}
        client.update_row.return_value = None

        result = atomic_increment(
            client, "LPO_MASTER", 10, "ALLOCATED_QUANTITY", 25.0, trace_id="t3"
        )

        assert result
        assert result.success is True
        assert result.old_value == 100.0
        assert result.new_value == 125.0
        assert result.retries_used == 0
        client.update_row.assert_called_once()

    @pytest.mark.unit
    def test_happy_path_decrement(self, mock_get_manifest, _mock_sleep):
        """Negative increment (decrement) works correctly."""
        mock_get_manifest.return_value = _make_mock_manifest("Allocated Quantity")
        client = MagicMock()
        client.get_row.return_value = {"Allocated Quantity": "200"}

        result = atomic_increment(
            client, "LPO_MASTER", 10, "ALLOCATED_QUANTITY", -50.0, trace_id="t4"
        )

        assert result.success
        assert result.old_value == 200.0
        assert result.new_value == 150.0

    @pytest.mark.unit
    def test_current_value_none_defaults_to_zero(self, mock_get_manifest, _mock_sleep):
        """None cell value is treated as 0 via parse_float_safe."""
        mock_get_manifest.return_value = _make_mock_manifest("Planned Quantity")
        client = MagicMock()
        client.get_row.return_value = {"Planned Quantity": None}

        result = atomic_increment(
            client, "LPO_MASTER", 10, "PLANNED_QUANTITY", 75.0, trace_id="t5"
        )

        assert result.success
        assert result.old_value == 0.0
        assert result.new_value == 75.0

    @pytest.mark.unit
    def test_current_value_missing_key_defaults_to_zero(self, mock_get_manifest, _mock_sleep):
        """Row dict missing the column key defaults to 0."""
        mock_get_manifest.return_value = _make_mock_manifest("Planned Quantity")
        client = MagicMock()
        client.get_row.return_value = {"Other Column": "42"}

        result = atomic_increment(
            client, "LPO_MASTER", 10, "PLANNED_QUANTITY", 30.0, trace_id="t6"
        )

        assert result.success
        assert result.old_value == 0.0
        assert result.new_value == 30.0

    @pytest.mark.unit
    def test_increment_by_zero(self, mock_get_manifest, _mock_sleep):
        """Incrementing by 0 is a valid (no-op) operation."""
        mock_get_manifest.return_value = _make_mock_manifest("Allocated Quantity")
        client = MagicMock()
        client.get_row.return_value = {"Allocated Quantity": "50"}

        result = atomic_increment(
            client, "LPO_MASTER", 10, "ALLOCATED_QUANTITY", 0.0, trace_id="t7"
        )

        assert result.success
        assert result.old_value == 50.0
        assert result.new_value == 50.0

    # ---- Collision and retry ----

    @pytest.mark.unit
    def test_collision_retries_and_succeeds(self, mock_get_manifest, mock_sleep):
        """On 4004 collision, retries with fresh read and succeeds."""
        mock_get_manifest.return_value = _make_mock_manifest("Allocated Quantity")
        client = MagicMock()
        # First read returns 100; after collision, re-read returns 110 (updated by other)
        client.get_row.side_effect = [
            {"Allocated Quantity": "100"},
            {"Allocated Quantity": "110"},
        ]
        # First update collides, second succeeds
        client.update_row.side_effect = [
            _collision_error(),
            None,
        ]

        result = atomic_increment(
            client, "LPO_MASTER", 10, "ALLOCATED_QUANTITY", 25.0,
            trace_id="t8", max_retries=3,
        )

        assert result.success
        assert result.old_value == 110.0  # fresh read after collision
        assert result.new_value == 135.0
        assert result.retries_used == 1
        assert mock_sleep.call_count == 1  # backoff sleep happened once

    @pytest.mark.unit
    def test_collision_max_retries_exceeded(self, mock_get_manifest, mock_sleep):
        """When all retries fail with collision, returns COLLISION_MAX_RETRIES."""
        mock_get_manifest.return_value = _make_mock_manifest("Allocated Quantity")
        client = MagicMock()
        client.get_row.return_value = {"Allocated Quantity": "100"}
        client.update_row.side_effect = _collision_error()

        result = atomic_increment(
            client, "LPO_MASTER", 10, "ALLOCATED_QUANTITY", 10.0,
            trace_id="t9", max_retries=3,
        )

        assert not result
        assert result.error_code == "COLLISION_MAX_RETRIES"
        # Should have tried 3 times (indices 0, 1, 2)
        assert client.update_row.call_count == 3
        # Sleep happens on retries (not on final attempt)
        assert mock_sleep.call_count == 2  # between attempt 0->1 and 1->2

    @pytest.mark.unit
    def test_non_collision_error_no_retry(self, mock_get_manifest, mock_sleep):
        """Non-collision error returns UPDATE_ERROR immediately without retrying."""
        mock_get_manifest.return_value = _make_mock_manifest("Allocated Quantity")
        client = MagicMock()
        client.get_row.return_value = {"Allocated Quantity": "100"}
        client.update_row.side_effect = Exception("Connection timeout")

        result = atomic_increment(
            client, "LPO_MASTER", 10, "ALLOCATED_QUANTITY", 10.0,
            trace_id="t10", max_retries=5,
        )

        assert not result
        assert result.error_code == "UPDATE_ERROR"
        assert "Connection timeout" in result.error_message
        # No retry: only one update attempt
        client.update_row.assert_called_once()
        mock_sleep.assert_not_called()

    @pytest.mark.unit
    def test_retries_used_tracks_attempts(self, mock_get_manifest, mock_sleep):
        """retries_used reflects the attempt index on success."""
        mock_get_manifest.return_value = _make_mock_manifest("Allocated Quantity")
        client = MagicMock()
        client.get_row.return_value = {"Allocated Quantity": "50"}
        # Fail twice, succeed on third
        client.update_row.side_effect = [
            _collision_error(),
            _collision_error(),
            None,
        ]

        result = atomic_increment(
            client, "LPO_MASTER", 10, "ALLOCATED_QUANTITY", 10.0,
            trace_id="t11", max_retries=5,
        )

        assert result.success
        assert result.retries_used == 2  # 0-indexed: third attempt is index 2

    @pytest.mark.unit
    def test_collision_detected_by_conflict_keyword(self, mock_get_manifest, mock_sleep):
        """'conflict' in error message is also recognized as a collision."""
        mock_get_manifest.return_value = _make_mock_manifest("Allocated Quantity")
        client = MagicMock()
        client.get_row.side_effect = [
            {"Allocated Quantity": "50"},
            {"Allocated Quantity": "55"},
        ]
        client.update_row.side_effect = [
            Exception("resource conflict detected"),
            None,
        ]

        result = atomic_increment(
            client, "LPO_MASTER", 10, "ALLOCATED_QUANTITY", 10.0,
            trace_id="t12", max_retries=3,
        )

        assert result.success
        assert result.retries_used == 1

    @pytest.mark.unit
    def test_collision_detected_by_collision_keyword(self, mock_get_manifest, mock_sleep):
        """'collision' in error message is also recognized."""
        mock_get_manifest.return_value = _make_mock_manifest("Allocated Quantity")
        client = MagicMock()
        client.get_row.side_effect = [
            {"Allocated Quantity": "50"},
            {"Allocated Quantity": "55"},
        ]
        client.update_row.side_effect = [
            Exception("save collision on row"),
            None,
        ]

        result = atomic_increment(
            client, "LPO_MASTER", 10, "ALLOCATED_QUANTITY", 10.0,
            trace_id="t13", max_retries=3,
        )

        assert result.success

    @pytest.mark.unit
    def test_max_retries_one_no_retry_on_collision(self, mock_get_manifest, mock_sleep):
        """With max_retries=1, a single collision gives COLLISION_MAX_RETRIES."""
        mock_get_manifest.return_value = _make_mock_manifest("Allocated Quantity")
        client = MagicMock()
        client.get_row.return_value = {"Allocated Quantity": "50"}
        client.update_row.side_effect = _collision_error()

        result = atomic_increment(
            client, "LPO_MASTER", 10, "ALLOCATED_QUANTITY", 10.0,
            trace_id="t14", max_retries=1,
        )

        assert not result
        assert result.error_code == "COLLISION_MAX_RETRIES"
        client.update_row.assert_called_once()
        mock_sleep.assert_not_called()

    # ---- Sheet/Column enum handling ----

    @pytest.mark.unit
    def test_sheet_enum_value_extracted(self, mock_get_manifest, _mock_sleep):
        """Sheet enum .value is extracted for manifest lookup."""
        manifest = _make_mock_manifest("Allocated Quantity")
        mock_get_manifest.return_value = manifest
        client = MagicMock()
        client.get_row.return_value = {"Allocated Quantity": "0"}

        # Use an object with a .value attribute (simulating a Sheet enum)
        sheet_enum = MagicMock()
        sheet_enum.value = "LPO_MASTER"

        col_enum = MagicMock()
        col_enum.value = "ALLOCATED_QUANTITY"

        result = atomic_increment(client, sheet_enum, 10, col_enum, 5.0)

        assert result.success
        manifest.get_column_name.assert_called_with("LPO_MASTER", "ALLOCATED_QUANTITY")

    @pytest.mark.unit
    def test_string_refs_used_directly(self, mock_get_manifest, _mock_sleep):
        """Plain strings are used directly without .value extraction."""
        manifest = _make_mock_manifest("Allocated Quantity")
        mock_get_manifest.return_value = manifest
        client = MagicMock()
        client.get_row.return_value = {"Allocated Quantity": "0"}

        result = atomic_increment(client, "LPO_MASTER", 10, "ALLOCATED_QUANTITY", 5.0)

        assert result.success
        manifest.get_column_name.assert_called_with("LPO_MASTER", "ALLOCATED_QUANTITY")

    # ---- Float parsing edge cases ----

    @pytest.mark.unit
    def test_current_value_na_string(self, mock_get_manifest, _mock_sleep):
        """'N/A' in cell is treated as 0.0."""
        mock_get_manifest.return_value = _make_mock_manifest("Allocated Quantity")
        client = MagicMock()
        client.get_row.return_value = {"Allocated Quantity": "N/A"}

        result = atomic_increment(
            client, "LPO_MASTER", 10, "ALLOCATED_QUANTITY", 20.0
        )

        assert result.success
        assert result.old_value == 0.0
        assert result.new_value == 20.0

    @pytest.mark.unit
    def test_current_value_float_type(self, mock_get_manifest, _mock_sleep):
        """Numeric float cell value is parsed correctly."""
        mock_get_manifest.return_value = _make_mock_manifest("Allocated Quantity")
        client = MagicMock()
        client.get_row.return_value = {"Allocated Quantity": 150.5}

        result = atomic_increment(
            client, "LPO_MASTER", 10, "ALLOCATED_QUANTITY", 10.0
        )

        assert result.success
        assert result.old_value == 150.5
        assert result.new_value == 160.5


# =============================================================================
# atomic_set_if_equals
# =============================================================================

@patch("shared.atomic_update.get_manifest")
class TestAtomicSetIfEquals:
    """Tests for atomic_set_if_equals (compare-and-swap)."""

    # ---- Column / Row resolution errors ----

    @pytest.mark.unit
    def test_column_not_found(self, mock_get_manifest):
        """Returns COLUMN_NOT_FOUND when manifest cannot resolve the column."""
        mock_get_manifest.return_value = _make_mock_manifest_missing()
        client = MagicMock()

        result = atomic_set_if_equals(
            client, "LPO_MASTER", 123, "BAD_COLUMN",
            expected_value=10.0, new_value=20.0, trace_id="cas1",
        )

        assert not result
        assert result.error_code == "COLUMN_NOT_FOUND"
        client.get_row.assert_not_called()

    @pytest.mark.unit
    def test_row_not_found(self, mock_get_manifest):
        """Returns ROW_NOT_FOUND when client.get_row returns None."""
        mock_get_manifest.return_value = _make_mock_manifest("Allocated Quantity")
        client = MagicMock()
        client.get_row.return_value = None

        result = atomic_set_if_equals(
            client, "LPO_MASTER", 999, "ALLOCATED_QUANTITY",
            expected_value=0.0, new_value=50.0, trace_id="cas2",
        )

        assert not result
        assert result.error_code == "ROW_NOT_FOUND"

    # ---- Happy path ----

    @pytest.mark.unit
    def test_value_matches_updates_successfully(self, mock_get_manifest):
        """When current value equals expected, update goes through."""
        mock_get_manifest.return_value = _make_mock_manifest("Allocated Quantity")
        client = MagicMock()
        client.get_row.return_value = {"Allocated Quantity": "100"}

        result = atomic_set_if_equals(
            client, "LPO_MASTER", 10, "ALLOCATED_QUANTITY",
            expected_value=100.0, new_value=200.0, trace_id="cas3",
        )

        assert result
        assert result.success is True
        assert result.old_value == 100.0
        assert result.new_value == 200.0
        client.update_row.assert_called_once()

    @pytest.mark.unit
    def test_value_matches_zero(self, mock_get_manifest):
        """Zero-to-nonzero swap works."""
        mock_get_manifest.return_value = _make_mock_manifest("Planned Quantity")
        client = MagicMock()
        client.get_row.return_value = {"Planned Quantity": "0"}

        result = atomic_set_if_equals(
            client, "LPO_MASTER", 10, "PLANNED_QUANTITY",
            expected_value=0.0, new_value=500.0, trace_id="cas4",
        )

        assert result.success
        assert result.old_value == 0.0
        assert result.new_value == 500.0

    # ---- Value mismatch ----

    @pytest.mark.unit
    def test_value_changed_returns_failure(self, mock_get_manifest):
        """When current != expected, returns VALUE_CHANGED without updating."""
        mock_get_manifest.return_value = _make_mock_manifest("Allocated Quantity")
        client = MagicMock()
        client.get_row.return_value = {"Allocated Quantity": "150"}

        result = atomic_set_if_equals(
            client, "LPO_MASTER", 10, "ALLOCATED_QUANTITY",
            expected_value=100.0, new_value=200.0, trace_id="cas5",
        )

        assert not result
        assert result.error_code == "VALUE_CHANGED"
        assert result.old_value == 150.0
        assert "150" in result.error_message
        client.update_row.assert_not_called()

    @pytest.mark.unit
    def test_value_changed_includes_expected_in_message(self, mock_get_manifest):
        """Error message mentions both current and expected values."""
        mock_get_manifest.return_value = _make_mock_manifest("Allocated Quantity")
        client = MagicMock()
        client.get_row.return_value = {"Allocated Quantity": "75"}

        result = atomic_set_if_equals(
            client, "LPO_MASTER", 10, "ALLOCATED_QUANTITY",
            expected_value=50.0, new_value=100.0, trace_id="cas6",
        )

        assert result.error_code == "VALUE_CHANGED"
        assert "75" in result.error_message
        assert "50" in result.error_message

    @pytest.mark.unit
    def test_value_none_treated_as_zero(self, mock_get_manifest):
        """None cell value is parsed as 0, so expecting 0 succeeds."""
        mock_get_manifest.return_value = _make_mock_manifest("Allocated Quantity")
        client = MagicMock()
        client.get_row.return_value = {"Allocated Quantity": None}

        result = atomic_set_if_equals(
            client, "LPO_MASTER", 10, "ALLOCATED_QUANTITY",
            expected_value=0.0, new_value=10.0, trace_id="cas7",
        )

        assert result.success

    @pytest.mark.unit
    def test_value_none_mismatch_nonzero(self, mock_get_manifest):
        """None cell (parsed as 0) != nonzero expected => VALUE_CHANGED."""
        mock_get_manifest.return_value = _make_mock_manifest("Allocated Quantity")
        client = MagicMock()
        client.get_row.return_value = {"Allocated Quantity": None}

        result = atomic_set_if_equals(
            client, "LPO_MASTER", 10, "ALLOCATED_QUANTITY",
            expected_value=50.0, new_value=100.0, trace_id="cas8",
        )

        assert not result
        assert result.error_code == "VALUE_CHANGED"
        assert result.old_value == 0.0

    # ---- Update error ----

    @pytest.mark.unit
    def test_update_error(self, mock_get_manifest):
        """Exception during update_row returns UPDATE_ERROR."""
        mock_get_manifest.return_value = _make_mock_manifest("Allocated Quantity")
        client = MagicMock()
        client.get_row.return_value = {"Allocated Quantity": "100"}
        client.update_row.side_effect = Exception("Network failure")

        result = atomic_set_if_equals(
            client, "LPO_MASTER", 10, "ALLOCATED_QUANTITY",
            expected_value=100.0, new_value=200.0, trace_id="cas9",
        )

        assert not result
        assert result.error_code == "UPDATE_ERROR"
        assert "Network failure" in result.error_message

    # ---- Sheet/Column enum handling ----

    @pytest.mark.unit
    def test_sheet_enum_value_extracted(self, mock_get_manifest):
        """Sheet/Column enum .value is extracted for manifest lookup."""
        manifest = _make_mock_manifest("Allocated Quantity")
        mock_get_manifest.return_value = manifest
        client = MagicMock()
        client.get_row.return_value = {"Allocated Quantity": "0"}

        sheet_enum = MagicMock()
        sheet_enum.value = "LPO_MASTER"
        col_enum = MagicMock()
        col_enum.value = "ALLOCATED_QUANTITY"

        result = atomic_set_if_equals(
            client, sheet_enum, 10, col_enum,
            expected_value=0.0, new_value=10.0,
        )

        assert result.success
        manifest.get_column_name.assert_called_with("LPO_MASTER", "ALLOCATED_QUANTITY")

    @pytest.mark.unit
    def test_string_refs_used_directly(self, mock_get_manifest):
        """Plain strings pass through without .value extraction."""
        manifest = _make_mock_manifest("Allocated Quantity")
        mock_get_manifest.return_value = manifest
        client = MagicMock()
        client.get_row.return_value = {"Allocated Quantity": "0"}

        result = atomic_set_if_equals(
            client, "LPO_MASTER", 10, "ALLOCATED_QUANTITY",
            expected_value=0.0, new_value=10.0,
        )

        assert result.success
        manifest.get_column_name.assert_called_with("LPO_MASTER", "ALLOCATED_QUANTITY")

    # ---- Float edge cases ----

    @pytest.mark.unit
    def test_missing_key_treated_as_zero(self, mock_get_manifest):
        """Column key absent from row dict is treated as 0."""
        mock_get_manifest.return_value = _make_mock_manifest("Allocated Quantity")
        client = MagicMock()
        client.get_row.return_value = {"Other Column": "42"}

        result = atomic_set_if_equals(
            client, "LPO_MASTER", 10, "ALLOCATED_QUANTITY",
            expected_value=0.0, new_value=10.0, trace_id="cas10",
        )

        assert result.success
        assert result.old_value == 0.0

    @pytest.mark.unit
    def test_na_string_treated_as_zero(self, mock_get_manifest):
        """'N/A' string in cell is parsed as 0."""
        mock_get_manifest.return_value = _make_mock_manifest("Allocated Quantity")
        client = MagicMock()
        client.get_row.return_value = {"Allocated Quantity": "N/A"}

        result = atomic_set_if_equals(
            client, "LPO_MASTER", 10, "ALLOCATED_QUANTITY",
            expected_value=0.0, new_value=10.0, trace_id="cas11",
        )

        assert result.success

    @pytest.mark.unit
    def test_fractional_comparison(self, mock_get_manifest):
        """Fractional float comparison works correctly."""
        mock_get_manifest.return_value = _make_mock_manifest("Price")
        client = MagicMock()
        client.get_row.return_value = {"Price": "99.99"}

        result = atomic_set_if_equals(
            client, "LPO_MASTER", 10, "PRICE",
            expected_value=99.99, new_value=109.99, trace_id="cas12",
        )

        assert result.success
        assert result.old_value == 99.99
        assert result.new_value == 109.99

    @pytest.mark.unit
    def test_same_value_swap(self, mock_get_manifest):
        """Swapping a value with itself (expected == new) still succeeds."""
        mock_get_manifest.return_value = _make_mock_manifest("Allocated Quantity")
        client = MagicMock()
        client.get_row.return_value = {"Allocated Quantity": "50"}

        result = atomic_set_if_equals(
            client, "LPO_MASTER", 10, "ALLOCATED_QUANTITY",
            expected_value=50.0, new_value=50.0, trace_id="cas13",
        )

        assert result.success
        assert result.old_value == 50.0
        assert result.new_value == 50.0
