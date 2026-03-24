"""
Unit Tests for Allocation Engine
=================================

Tests the core allocation algorithm in shared/allocation_engine.py:
- _parse_rows() — raw Smartsheet data → list of dicts
- _generate_allocation_id() — format "ALLOC-YYYYMMDD-XXXXXX"
- AllocationResult.to_dict() — serialization
- allocate_for_session() — full allocation flow including:
  - No BOM rows (SHORTAGE + NO_BOM_LINES)
  - All Green (ALLOCATED)
  - Mixed Yellow/Red (PARTIAL_ALLOCATED)
  - All Red (SHORTAGE)
  - Aggregation of multiple BOM lines per SAP code
  - Skipping BOM lines without SAP code
  - ALLOCATION_LOG and INVENTORY_TXN_LOG writes
  - Exception creation for shortages
  - Graceful error handling on write failures
"""

import pytest
import re
from datetime import datetime, timedelta
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.allocation_engine import (
    _parse_rows,
    _generate_allocation_id,
    AllocationResult,
    AllocationLine,
    allocate_for_session,
)
from shared.logical_names import Sheet


# ── Helpers for building mock Smartsheet sheet data ────────────────────

def _build_sheet_data(columns, rows):
    """
    Build mock Smartsheet sheet data dict.

    Args:
        columns: list of (col_id, col_title) tuples
        rows: list of dicts mapping col_title -> value, each dict must include 'row_id'
    """
    col_defs = [{"id": cid, "title": ctitle} for cid, ctitle in columns]
    title_to_id = {ctitle: cid for cid, ctitle in columns}

    raw_rows = []
    for row in rows:
        row_id = row.get("row_id", 1)
        cells = []
        for col_title, value in row.items():
            if col_title == "row_id":
                continue
            col_id = title_to_id.get(col_title)
            if col_id is not None:
                cells.append({"columnId": col_id, "value": value})
        raw_rows.append({"id": row_id, "cells": cells})

    return {"columns": col_defs, "rows": raw_rows}


# ── Fake AvailableStock ──────────────────────────────────────────────

@dataclass
class FakeAvailableStock:
    material_code: str = ""
    net_available: float = 0.0


# ── Fixed datetime for deterministic tests ────────────────────────────

FIXED_NOW = datetime(2026, 3, 24, 10, 0, 0)
FIXED_FORMATTED = "2026-03-24T10:00:00"
ALLOC_ID_COUNTER = [0]


def _make_alloc_id():
    ALLOC_ID_COUNTER[0] += 1
    return f"ALLOC-20260324-AAA{ALLOC_ID_COUNTER[0]:03d}"


# ── Mock manifest that returns column titles as-is ────────────────────

class _StubManifest:
    """Manifest stub that returns column_logical as the physical name."""

    def get_column_name(self, sheet_logical, column_logical):
        return column_logical


# ── BOM column definitions (matching the column_logical values) ───────

BOM_COLUMNS = [
    (101, "NEST_SESSION_ID"),
    (102, "SAP_CODE"),
    (103, "CANONICAL_QUANTITY"),
    (104, "CANONICAL_UOM"),
    (105, "MATERIAL_TYPE"),
    (106, "NESTING_DESCRIPTION"),
    (107, "QUANTITY"),
    (108, "UOM"),
]


# =====================================================================
# Tests: _parse_rows
# =====================================================================


class TestParseRows:

    @pytest.mark.unit
    def test_basic_parsing(self):
        """Rows are converted to dicts keyed by column title."""
        sheet = _build_sheet_data(
            columns=[(1, "Name"), (2, "Qty")],
            rows=[
                {"row_id": 100, "Name": "Widget", "Qty": 5},
                {"row_id": 101, "Name": "Gadget", "Qty": 10},
            ],
        )
        result = _parse_rows(sheet)

        assert len(result) == 2
        assert result[0]["Name"] == "Widget"
        assert result[0]["Qty"] == 5
        assert result[0]["row_id"] == 100
        assert result[1]["Name"] == "Gadget"

    @pytest.mark.unit
    def test_empty_sheet(self):
        """Empty sheet returns empty list."""
        sheet = _build_sheet_data(columns=[(1, "A")], rows=[])
        assert _parse_rows(sheet) == []

    @pytest.mark.unit
    def test_missing_columns_key(self):
        """Missing 'columns' key does not crash."""
        result = _parse_rows({"rows": [{"id": 1, "cells": []}]})
        # Should return a row with only row_id, since no column mapping
        assert len(result) == 1
        assert result[0]["row_id"] == 1

    @pytest.mark.unit
    def test_cell_falls_back_to_display_value(self):
        """When value is None/falsy, displayValue is used."""
        sheet = {
            "columns": [{"id": 1, "title": "Status"}],
            "rows": [{
                "id": 10,
                "cells": [{"columnId": 1, "value": None, "displayValue": "Active"}],
            }],
        }
        result = _parse_rows(sheet)
        assert result[0]["Status"] == "Active"

    @pytest.mark.unit
    def test_unknown_column_id_ignored(self):
        """Cells referencing unknown column IDs are silently skipped."""
        sheet = {
            "columns": [{"id": 1, "title": "Known"}],
            "rows": [{
                "id": 10,
                "cells": [
                    {"columnId": 1, "value": "yes"},
                    {"columnId": 999, "value": "ghost"},
                ],
            }],
        }
        result = _parse_rows(sheet)
        assert "Known" in result[0]
        assert len([k for k in result[0] if k != "row_id"]) == 1


# =====================================================================
# Tests: _generate_allocation_id
# =====================================================================

class TestGenerateAllocationId:

    @pytest.mark.unit
    def test_format(self, mock_client):
        """ID follows ALLOC-NNNN sequential pattern."""
        alloc_id = _generate_allocation_id(mock_client)
        assert re.match(r"^ALLOC-\d{4}$", alloc_id)

    @pytest.mark.unit
    def test_sequential(self, mock_client):
        """Each call produces a sequentially increasing ID."""
        id1 = _generate_allocation_id(mock_client)
        id2 = _generate_allocation_id(mock_client)
        num1 = int(id1.split("-")[1])
        num2 = int(id2.split("-")[1])
        assert num2 == num1 + 1


# =====================================================================
# Tests: AllocationResult.to_dict
# =====================================================================

class TestAllocationResultToDict:

    @pytest.mark.unit
    def test_empty_result(self):
        """An empty result serializes correctly."""
        r = AllocationResult(status="ALLOCATED")
        d = r.to_dict()
        assert d == {
            "status": "ALLOCATED",
            "allocation_ids": [],
            "lines": [],
            "shortages": [],
            "warnings": [],
            "exception_ids": [],
        }

    @pytest.mark.unit
    def test_populated_result(self):
        """Result with lines serializes all fields."""
        line = AllocationLine(
            allocation_id="ALLOC-001",
            material_code="MAT-A",
            quantity=100.0,
            stock_check_flag="Green",
            net_available=500.0,
        )
        r = AllocationResult(
            status="PARTIAL_ALLOCATED",
            allocation_ids=["ALLOC-001"],
            lines=[line],
            shortages=[{"material_code": "MAT-B", "needed": 200, "available": 0, "deficit": 200}],
            warnings=[{"code": "TXN_WRITE_FAILED", "message": "oops"}],
            exception_ids=["EX-001"],
        )
        d = r.to_dict()
        assert d["status"] == "PARTIAL_ALLOCATED"
        assert d["allocation_ids"] == ["ALLOC-001"]
        assert len(d["lines"]) == 1
        assert d["lines"][0]["allocation_id"] == "ALLOC-001"
        assert d["lines"][0]["quantity"] == 100.0
        assert d["shortages"][0]["deficit"] == 200
        assert d["exception_ids"] == ["EX-001"]


# =====================================================================
# Tests: allocate_for_session
# =====================================================================

# Common patch target prefix
_MOD = "shared.allocation_engine"


def _make_patches(
    bom_rows=None,
    stock_map=None,
    flag_map=None,
    add_row_side_effect=None,
):
    """
    Return a dict of patch kwargs for allocate_for_session tests.

    Args:
        bom_rows: list of row dicts for the PARSED_BOM sheet (uses BOM_COLUMNS)
        stock_map: dict mapping sap_code -> FakeAvailableStock
        flag_map: dict mapping sap_code -> flag string
        add_row_side_effect: optional side_effect for client.add_row
    """
    stock_map = stock_map or {}
    flag_map = flag_map or {}

    manifest = _StubManifest()
    bom_sheet = _build_sheet_data(BOM_COLUMNS, bom_rows or [])

    client = MagicMock()
    client.get_sheet.return_value = bom_sheet
    if add_row_side_effect:
        client.add_row.side_effect = add_row_side_effect
    else:
        client.add_row.return_value = {"id": 123}

    def _compute(c, material_code, trace_id=""):
        return stock_map.get(material_code, FakeAvailableStock(material_code=material_code))

    def _flag(available, needed):
        # Look up by a simple key; caller must precompute
        # Fall back to simple logic
        for code, f in flag_map.items():
            if code == "__default__":
                continue
            # We resolve by matching the available value
        return flag_map.get("__default__", "Green")

    return client, manifest, _compute, _flag


class TestAllocateForSessionNoBom:

    @pytest.mark.unit
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    @patch(f"{_MOD}.now_uae", return_value=FIXED_NOW)
    @patch(f"{_MOD}.format_datetime_for_smartsheet", return_value=FIXED_FORMATTED)
    def test_no_bom_rows_returns_shortage(self, _fmt, _now, _manifest):
        """When no PARSED_BOM rows exist for the session, return SHORTAGE with warning."""
        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(BOM_COLUMNS, [])

        result = allocate_for_session(
            client=client,
            nest_session_id="SESS-999",
            tag_id="TAG-001",
            trace_id="t-001",
        )

        assert result.status == "SHORTAGE"
        assert len(result.warnings) == 1
        assert result.warnings[0]["code"] == "NO_BOM_LINES"
        assert "SESS-999" in result.warnings[0]["message"]
        assert result.allocation_ids == []
        assert result.lines == []

    @pytest.mark.unit
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    @patch(f"{_MOD}.now_uae", return_value=FIXED_NOW)
    @patch(f"{_MOD}.format_datetime_for_smartsheet", return_value=FIXED_FORMATTED)
    def test_no_matching_session_rows(self, _fmt, _now, _manifest):
        """Rows exist but none match the requested session."""
        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(BOM_COLUMNS, [
            {"row_id": 1, "NEST_SESSION_ID": "OTHER-SESS", "SAP_CODE": "MAT-A",
             "CANONICAL_QUANTITY": 10, "CANONICAL_UOM": "ROL", "QUANTITY": 10, "UOM": "m",
             "MATERIAL_TYPE": "Sheet", "NESTING_DESCRIPTION": "Desc"},
        ])

        result = allocate_for_session(
            client=client,
            nest_session_id="SESS-001",
            tag_id="TAG-001",
            trace_id="t-002",
        )
        assert result.status == "SHORTAGE"
        assert result.warnings[0]["code"] == "NO_BOM_LINES"


class TestAllocateForSessionAllGreen:

    @pytest.mark.unit
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    @patch(f"{_MOD}.now_uae", return_value=FIXED_NOW)
    @patch(f"{_MOD}.format_datetime_for_smartsheet", return_value=FIXED_FORMATTED)
    @patch(f"{_MOD}.compute_available_qty")
    @patch(f"{_MOD}.determine_stock_flag", return_value="Green")
    @patch(f"{_MOD}.log_user_action")
    @patch(f"{_MOD}.create_exception")
    def test_all_green_status_allocated(
        self, mock_exc, mock_log, mock_flag, mock_stock, _fmt, _now, _manifest
    ):
        """All materials available -> ALLOCATED."""
        mock_stock.return_value = FakeAvailableStock(material_code="MAT-A", net_available=500.0)

        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(BOM_COLUMNS, [
            {"row_id": 1, "NEST_SESSION_ID": "SESS-001", "SAP_CODE": "MAT-A",
             "CANONICAL_QUANTITY": 100, "CANONICAL_UOM": "ROL",
             "QUANTITY": 200, "UOM": "m",
             "MATERIAL_TYPE": "Sheet", "NESTING_DESCRIPTION": "Aluminum 0.5mm"},
        ])
        client.add_row.return_value = {"id": 123}

        # Patch inventory txn batch at its source module (lazy-imported inside the function)
        with patch("shared.inventory_service.log_inventory_transactions_batch") as mock_txn_batch:
            mock_txn_batch.return_value = ["TXN-001"]

            result = allocate_for_session(
                client=client,
                nest_session_id="SESS-001",
                tag_id="TAG-001",
                planned_date="2026-03-25",
                shift="Morning",
                trace_id="t-green",
            )

        assert result.status == "ALLOCATED"
        assert len(result.allocation_ids) == 1
        assert len(result.lines) == 1
        assert result.lines[0].material_code == "MAT-A"
        assert result.lines[0].quantity == 100.0
        assert result.lines[0].stock_check_flag == "Green"
        assert result.lines[0].net_available == 500.0
        assert result.shortages == []
        assert result.exception_ids == []

        # Verify ALLOCATION_LOG write
        client.add_row.assert_called_once()
        alloc_row_call = client.add_row.call_args
        assert alloc_row_call[0][0] == Sheet.ALLOCATION_LOG
        row_data = alloc_row_call[0][1]
        assert row_data["MATERIAL_CODE"] == "MAT-A"
        assert row_data["QUANTITY"] == 100.0
        assert row_data["STOCK_CHECK_FLAG"] == "Green"
        assert row_data["TAG_SHEET_ID"] == "TAG-001"
        assert row_data["PLANNED_DATE"] == "2026-03-25"
        assert row_data["SHIFT"] == "Morning"
        assert row_data["STATUS"] == "Submitted"
        assert row_data["NESTING_DESCRIPTION"] == "Aluminum 0.5mm"
        assert row_data["RAW_QUANTITY"] == 200.0
        assert row_data["RAW_UOM"] == "m"

    @pytest.mark.unit
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    @patch(f"{_MOD}.now_uae", return_value=FIXED_NOW)
    @patch(f"{_MOD}.format_datetime_for_smartsheet", return_value=FIXED_FORMATTED)
    @patch(f"{_MOD}.compute_available_qty")
    @patch(f"{_MOD}.determine_stock_flag", return_value="Green")
    @patch(f"{_MOD}.log_user_action")
    @patch(f"{_MOD}.create_exception")
    def test_inventory_txn_written_for_green(
        self, mock_exc, mock_log, mock_flag, mock_stock, _fmt, _now, _manifest
    ):
        """Inventory transaction logged with negative qty for reservations."""
        mock_stock.return_value = FakeAvailableStock(net_available=500.0)

        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(BOM_COLUMNS, [
            {"row_id": 1, "NEST_SESSION_ID": "SESS-001", "SAP_CODE": "MAT-A",
             "CANONICAL_QUANTITY": 50, "CANONICAL_UOM": "ROL",
             "QUANTITY": 50, "UOM": "m",
             "MATERIAL_TYPE": "Sheet", "NESTING_DESCRIPTION": "Desc"},
        ])
        client.add_row.return_value = {"id": 123}

        with patch("shared.inventory_service.log_inventory_transactions_batch") as mock_txn_batch:
            mock_txn_batch.return_value = ["TXN-001"]

            allocate_for_session(
                client=client,
                nest_session_id="SESS-001",
                tag_id="TAG-001",
                trace_id="t-txn",
            )

            mock_txn_batch.assert_called_once()
            txns = mock_txn_batch.call_args[1].get("transactions") or mock_txn_batch.call_args[0][1]
            assert len(txns) == 1
            assert txns[0]["txn_type"] == "Allocation"
            assert txns[0]["quantity"] == -50.0  # Negative = reservation
            assert txns[0]["material_code"] == "MAT-A"
            assert txns[0]["source_system"] == "AzureFunc"


class TestAllocateForSessionPartial:

    @pytest.mark.unit
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    @patch(f"{_MOD}.now_uae", return_value=FIXED_NOW)
    @patch(f"{_MOD}.format_datetime_for_smartsheet", return_value=FIXED_FORMATTED)
    @patch(f"{_MOD}.compute_available_qty")
    @patch(f"{_MOD}.determine_stock_flag", return_value="Yellow")
    @patch(f"{_MOD}.log_user_action")
    @patch(f"{_MOD}.create_exception")
    def test_yellow_caps_quantity_at_available(
        self, mock_exc, mock_log, mock_flag, mock_stock, _fmt, _now, _manifest
    ):
        """Yellow flag caps allocated qty to available stock."""
        mock_stock.return_value = FakeAvailableStock(net_available=30.0)

        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(BOM_COLUMNS, [
            {"row_id": 1, "NEST_SESSION_ID": "SESS-001", "SAP_CODE": "MAT-A",
             "CANONICAL_QUANTITY": 100, "CANONICAL_UOM": "ROL",
             "QUANTITY": 100, "UOM": "m",
             "MATERIAL_TYPE": "Sheet", "NESTING_DESCRIPTION": "Desc"},
        ])
        client.add_row.return_value = {"id": 123}

        with patch("shared.inventory_service.log_inventory_transactions_batch") as mock_txn_batch:
            mock_txn_batch.return_value = ["TXN-001"]

            result = allocate_for_session(
                client=client,
                nest_session_id="SESS-001",
                tag_id="TAG-001",
                trace_id="t-yellow",
            )

        assert result.status == "PARTIAL_ALLOCATED"
        assert result.lines[0].quantity == 30.0  # Capped at available
        assert result.lines[0].stock_check_flag == "Yellow"

    @pytest.mark.unit
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    @patch(f"{_MOD}.now_uae", return_value=FIXED_NOW)
    @patch(f"{_MOD}.format_datetime_for_smartsheet", return_value=FIXED_FORMATTED)
    @patch(f"{_MOD}.compute_available_qty")
    @patch(f"{_MOD}.determine_stock_flag", return_value="Yellow")
    @patch(f"{_MOD}.log_user_action")
    @patch(f"{_MOD}.create_exception")
    def test_yellow_negative_available_clamps_to_zero(
        self, mock_exc, mock_log, mock_flag, mock_stock, _fmt, _now, _manifest
    ):
        """When available < 0 but flag is Yellow, qty is clamped to 0."""
        mock_stock.return_value = FakeAvailableStock(net_available=-5.0)

        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(BOM_COLUMNS, [
            {"row_id": 1, "NEST_SESSION_ID": "SESS-001", "SAP_CODE": "MAT-A",
             "CANONICAL_QUANTITY": 100, "CANONICAL_UOM": "ROL",
             "QUANTITY": 100, "UOM": "m",
             "MATERIAL_TYPE": "Sheet", "NESTING_DESCRIPTION": "Desc"},
        ])
        client.add_row.return_value = {"id": 123}

        with patch("shared.inventory_service.log_inventory_transactions_batch"):
            result = allocate_for_session(
                client=client, nest_session_id="SESS-001", tag_id="TAG-001",
                trace_id="t-neg",
            )

        # min(100, max(0, -5)) = 0
        assert result.lines[0].quantity == 0.0


class TestAllocateForSessionShortage:

    @pytest.mark.unit
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    @patch(f"{_MOD}.now_uae", return_value=FIXED_NOW)
    @patch(f"{_MOD}.format_datetime_for_smartsheet", return_value=FIXED_FORMATTED)
    @patch(f"{_MOD}.compute_available_qty")
    @patch(f"{_MOD}.determine_stock_flag", return_value="Red")
    @patch(f"{_MOD}.log_user_action")
    @patch(f"{_MOD}.create_exception", return_value="EX-001")
    def test_red_sets_qty_zero_and_creates_shortage(
        self, mock_exc, mock_log, mock_flag, mock_stock, _fmt, _now, _manifest
    ):
        """Red flag sets allocated qty to 0 and creates shortage record.

        When add_row succeeds, allocation_ids is non-empty, so status is
        PARTIAL_ALLOCATED (not SHORTAGE). The shortage record is still created.
        """
        mock_stock.return_value = FakeAvailableStock(net_available=-10.0)

        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(BOM_COLUMNS, [
            {"row_id": 1, "NEST_SESSION_ID": "SESS-001", "SAP_CODE": "MAT-A",
             "CANONICAL_QUANTITY": 100, "CANONICAL_UOM": "ROL",
             "QUANTITY": 100, "UOM": "m",
             "MATERIAL_TYPE": "Sheet", "NESTING_DESCRIPTION": "Desc"},
        ])
        client.add_row.return_value = {"id": 123}

        # Step 4 does `from .audit import create_exception` (local re-import),
        # so also patch shared.audit.create_exception to control the return value
        with patch("shared.audit.create_exception", return_value="EX-001"):
            result = allocate_for_session(
                client=client,
                nest_session_id="SESS-001",
                tag_id="TAG-001",
                trace_id="t-red",
            )

        # add_row succeeds -> allocation_ids non-empty -> PARTIAL_ALLOCATED
        assert result.status == "PARTIAL_ALLOCATED"
        assert result.lines[0].quantity == 0.0
        assert result.lines[0].stock_check_flag == "Red"
        assert len(result.shortages) == 1
        assert result.shortages[0]["material_code"] == "MAT-A"
        assert result.shortages[0]["needed"] == 100.0
        assert result.shortages[0]["available"] == -10.0
        # deficit = need.total_qty - max(0, net_available) = 100 - max(0, -10) = 100
        assert result.shortages[0]["deficit"] == 100.0
        assert result.exception_ids == ["EX-001"]

    @pytest.mark.unit
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    @patch(f"{_MOD}.now_uae", return_value=FIXED_NOW)
    @patch(f"{_MOD}.format_datetime_for_smartsheet", return_value=FIXED_FORMATTED)
    @patch(f"{_MOD}.compute_available_qty")
    @patch(f"{_MOD}.determine_stock_flag", return_value="Red")
    @patch(f"{_MOD}.log_user_action")
    @patch(f"{_MOD}.create_exception", return_value="EX-002")
    def test_red_with_write_failure_gives_shortage(
        self, mock_exc, mock_log, mock_flag, mock_stock, _fmt, _now, _manifest
    ):
        """When Red AND add_row fails, allocation_ids is empty -> SHORTAGE."""
        mock_stock.return_value = FakeAvailableStock(net_available=-10.0)

        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(BOM_COLUMNS, [
            {"row_id": 1, "NEST_SESSION_ID": "SESS-001", "SAP_CODE": "MAT-A",
             "CANONICAL_QUANTITY": 100, "CANONICAL_UOM": "ROL",
             "QUANTITY": 100, "UOM": "m",
             "MATERIAL_TYPE": "Sheet", "NESTING_DESCRIPTION": "Desc"},
        ])
        client.add_row.side_effect = RuntimeError("Write failed")

        result = allocate_for_session(
            client=client,
            nest_session_id="SESS-001",
            tag_id="TAG-001",
            trace_id="t-red-fail",
        )

        # add_row fails -> no allocation_ids -> SHORTAGE
        assert result.status == "SHORTAGE"
        assert result.allocation_ids == []

    @pytest.mark.unit
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    @patch(f"{_MOD}.now_uae", return_value=FIXED_NOW)
    @patch(f"{_MOD}.format_datetime_for_smartsheet", return_value=FIXED_FORMATTED)
    @patch(f"{_MOD}.compute_available_qty")
    @patch(f"{_MOD}.determine_stock_flag", return_value="Red")
    @patch(f"{_MOD}.log_user_action")
    @patch(f"{_MOD}.create_exception", return_value="EX-001")
    def test_red_does_not_write_inventory_txn(
        self, mock_exc, mock_log, mock_flag, mock_stock, _fmt, _now, _manifest
    ):
        """No inventory txn is created when allocated qty is 0 (Red)."""
        mock_stock.return_value = FakeAvailableStock(net_available=0.0)

        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(BOM_COLUMNS, [
            {"row_id": 1, "NEST_SESSION_ID": "SESS-001", "SAP_CODE": "MAT-A",
             "CANONICAL_QUANTITY": 100, "CANONICAL_UOM": "ROL",
             "QUANTITY": 100, "UOM": "m",
             "MATERIAL_TYPE": "Sheet", "NESTING_DESCRIPTION": "Desc"},
        ])
        client.add_row.return_value = {"id": 123}

        with patch("shared.inventory_service.log_inventory_transactions_batch") as mock_txn:
            allocate_for_session(
                client=client, nest_session_id="SESS-001", tag_id="TAG-001",
                trace_id="t-no-txn",
            )
            # alloc_qty=0 for Red, so the `if alloc_qty > 0:` guard skips the txn write
            mock_txn.assert_not_called()


class TestAllocateForSessionMixed:

    @pytest.mark.unit
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    @patch(f"{_MOD}.now_uae", return_value=FIXED_NOW)
    @patch(f"{_MOD}.format_datetime_for_smartsheet", return_value=FIXED_FORMATTED)
    @patch(f"{_MOD}.compute_available_qty")
    @patch(f"{_MOD}.determine_stock_flag")
    @patch(f"{_MOD}.log_user_action")
    @patch(f"{_MOD}.create_exception", return_value="EX-002")
    def test_mixed_green_yellow_red(
        self, mock_exc, mock_log, mock_flag, mock_stock, _fmt, _now, _manifest
    ):
        """Green + Yellow + Red -> PARTIAL_ALLOCATED with shortage record."""
        stock_lookup = {
            "MAT-GREEN": FakeAvailableStock(net_available=500.0),
            "MAT-YELLOW": FakeAvailableStock(net_available=30.0),
            "MAT-RED": FakeAvailableStock(net_available=-5.0),
        }
        mock_stock.side_effect = lambda c, code, trace_id="": stock_lookup[code]

        flag_lookup = {
            "MAT-GREEN": "Green",
            "MAT-YELLOW": "Yellow",
            "MAT-RED": "Red",
        }
        # determine_stock_flag is called with (available, needed) — map by available
        def _flag_side_effect(available, needed):
            for code, stock in stock_lookup.items():
                if stock.net_available == available:
                    return flag_lookup[code]
            return "Green"
        mock_flag.side_effect = _flag_side_effect

        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(BOM_COLUMNS, [
            {"row_id": 1, "NEST_SESSION_ID": "SESS-001", "SAP_CODE": "MAT-GREEN",
             "CANONICAL_QUANTITY": 100, "CANONICAL_UOM": "ROL",
             "QUANTITY": 100, "UOM": "m",
             "MATERIAL_TYPE": "Sheet", "NESTING_DESCRIPTION": "Green Mat"},
            {"row_id": 2, "NEST_SESSION_ID": "SESS-001", "SAP_CODE": "MAT-YELLOW",
             "CANONICAL_QUANTITY": 50, "CANONICAL_UOM": "ROL",
             "QUANTITY": 50, "UOM": "m",
             "MATERIAL_TYPE": "Sheet", "NESTING_DESCRIPTION": "Yellow Mat"},
            {"row_id": 3, "NEST_SESSION_ID": "SESS-001", "SAP_CODE": "MAT-RED",
             "CANONICAL_QUANTITY": 80, "CANONICAL_UOM": "ROL",
             "QUANTITY": 80, "UOM": "m",
             "MATERIAL_TYPE": "Sheet", "NESTING_DESCRIPTION": "Red Mat"},
        ])
        client.add_row.return_value = {"id": 123}

        with patch("shared.inventory_service.log_inventory_transactions_batch") as mock_txn_batch, \
             patch("shared.audit.create_exception", return_value="EX-MIX"):
            mock_txn_batch.return_value = ["TXN-001"]

            result = allocate_for_session(
                client=client,
                nest_session_id="SESS-001",
                tag_id="TAG-001",
                trace_id="t-mixed",
            )

        assert result.status == "PARTIAL_ALLOCATED"
        assert len(result.allocation_ids) == 3
        assert len(result.lines) == 3
        assert len(result.shortages) == 1
        assert result.shortages[0]["material_code"] == "MAT-RED"

        # Verify quantities
        line_by_mat = {ln.material_code: ln for ln in result.lines}
        assert line_by_mat["MAT-GREEN"].quantity == 100.0
        assert line_by_mat["MAT-YELLOW"].quantity == 30.0  # Capped
        assert line_by_mat["MAT-RED"].quantity == 0.0


class TestAllocateForSessionAggregation:

    @pytest.mark.unit
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    @patch(f"{_MOD}.now_uae", return_value=FIXED_NOW)
    @patch(f"{_MOD}.format_datetime_for_smartsheet", return_value=FIXED_FORMATTED)
    @patch(f"{_MOD}.compute_available_qty")
    @patch(f"{_MOD}.determine_stock_flag", return_value="Green")
    @patch(f"{_MOD}.log_user_action")
    @patch(f"{_MOD}.create_exception")
    def test_aggregates_same_sap_code(
        self, mock_exc, mock_log, mock_flag, mock_stock, _fmt, _now, _manifest
    ):
        """Multiple BOM lines with the same SAP code are aggregated."""
        mock_stock.return_value = FakeAvailableStock(net_available=1000.0)

        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(BOM_COLUMNS, [
            {"row_id": 1, "NEST_SESSION_ID": "SESS-001", "SAP_CODE": "MAT-A",
             "CANONICAL_QUANTITY": 40, "CANONICAL_UOM": "ROL",
             "QUANTITY": 80, "UOM": "m",
             "MATERIAL_TYPE": "Sheet", "NESTING_DESCRIPTION": "Mat A - Line 1"},
            {"row_id": 2, "NEST_SESSION_ID": "SESS-001", "SAP_CODE": "MAT-A",
             "CANONICAL_QUANTITY": 60, "CANONICAL_UOM": "ROL",
             "QUANTITY": 120, "UOM": "m",
             "MATERIAL_TYPE": "Sheet", "NESTING_DESCRIPTION": "Mat A - Line 2"},
        ])
        client.add_row.return_value = {"id": 123}

        with patch("shared.inventory_service.log_inventory_transactions_batch") as mock_txn_batch:
            mock_txn_batch.return_value = ["TXN-001"]

            result = allocate_for_session(
                client=client, nest_session_id="SESS-001", tag_id="TAG-001",
                trace_id="t-agg",
            )

        # Should produce a single allocation line with summed qty
        assert len(result.allocation_ids) == 1
        assert result.lines[0].quantity == 100.0  # 40 + 60
        assert result.lines[0].material_code == "MAT-A"

        # Verify raw_quantity aggregated as well
        row_data = client.add_row.call_args[0][1]
        assert row_data["RAW_QUANTITY"] == 200.0  # 80 + 120

    @pytest.mark.unit
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    @patch(f"{_MOD}.now_uae", return_value=FIXED_NOW)
    @patch(f"{_MOD}.format_datetime_for_smartsheet", return_value=FIXED_FORMATTED)
    @patch(f"{_MOD}.compute_available_qty")
    @patch(f"{_MOD}.determine_stock_flag", return_value="Green")
    @patch(f"{_MOD}.log_user_action")
    @patch(f"{_MOD}.create_exception")
    def test_skips_bom_lines_without_sap_code(
        self, mock_exc, mock_log, mock_flag, mock_stock, _fmt, _now, _manifest
    ):
        """BOM lines with no SAP code are silently skipped."""
        mock_stock.return_value = FakeAvailableStock(net_available=500.0)

        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(BOM_COLUMNS, [
            {"row_id": 1, "NEST_SESSION_ID": "SESS-001", "SAP_CODE": None,
             "CANONICAL_QUANTITY": 10, "CANONICAL_UOM": "ROL",
             "QUANTITY": 10, "UOM": "m",
             "MATERIAL_TYPE": "Sheet", "NESTING_DESCRIPTION": "Unmapped"},
            {"row_id": 2, "NEST_SESSION_ID": "SESS-001", "SAP_CODE": "MAT-B",
             "CANONICAL_QUANTITY": 25, "CANONICAL_UOM": "ROL",
             "QUANTITY": 25, "UOM": "m",
             "MATERIAL_TYPE": "Sheet", "NESTING_DESCRIPTION": "Mapped"},
        ])
        client.add_row.return_value = {"id": 123}

        with patch("shared.inventory_service.log_inventory_transactions_batch") as mock_txn_batch:
            mock_txn_batch.return_value = ["TXN-001"]

            result = allocate_for_session(
                client=client, nest_session_id="SESS-001", tag_id="TAG-001",
                trace_id="t-skip",
            )

        assert len(result.allocation_ids) == 1
        assert result.lines[0].material_code == "MAT-B"


class TestAllocateForSessionPlannedDate:

    @pytest.mark.unit
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    @patch(f"{_MOD}.now_uae", return_value=FIXED_NOW)
    @patch(f"{_MOD}.format_datetime_for_smartsheet", return_value=FIXED_FORMATTED)
    @patch(f"{_MOD}.compute_available_qty")
    @patch(f"{_MOD}.determine_stock_flag", return_value="Green")
    @patch(f"{_MOD}.log_user_action")
    @patch(f"{_MOD}.create_exception")
    def test_defaults_planned_date_to_tomorrow(
        self, mock_exc, mock_log, mock_flag, mock_stock, _fmt, _now, _manifest
    ):
        """When planned_date is not provided, defaults to tomorrow."""
        mock_stock.return_value = FakeAvailableStock(net_available=500.0)

        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(BOM_COLUMNS, [
            {"row_id": 1, "NEST_SESSION_ID": "SESS-001", "SAP_CODE": "MAT-A",
             "CANONICAL_QUANTITY": 10, "CANONICAL_UOM": "ROL",
             "QUANTITY": 10, "UOM": "m",
             "MATERIAL_TYPE": "Sheet", "NESTING_DESCRIPTION": "Desc"},
        ])
        client.add_row.return_value = {"id": 123}

        with patch("shared.inventory_service.log_inventory_transactions_batch") as mock_txn_batch:
            mock_txn_batch.return_value = ["TXN-001"]

            allocate_for_session(
                client=client, nest_session_id="SESS-001", tag_id="TAG-001",
                trace_id="t-date",
            )

        row_data = client.add_row.call_args[0][1]
        expected_date = (FIXED_NOW + timedelta(days=1)).strftime("%Y-%m-%d")
        assert row_data["PLANNED_DATE"] == expected_date

    @pytest.mark.unit
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    @patch(f"{_MOD}.now_uae", return_value=FIXED_NOW)
    @patch(f"{_MOD}.format_datetime_for_smartsheet", return_value=FIXED_FORMATTED)
    @patch(f"{_MOD}.compute_available_qty")
    @patch(f"{_MOD}.determine_stock_flag", return_value="Green")
    @patch(f"{_MOD}.log_user_action")
    @patch(f"{_MOD}.create_exception")
    def test_explicit_planned_date_used(
        self, mock_exc, mock_log, mock_flag, mock_stock, _fmt, _now, _manifest
    ):
        """When planned_date is provided, it is used directly."""
        mock_stock.return_value = FakeAvailableStock(net_available=500.0)

        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(BOM_COLUMNS, [
            {"row_id": 1, "NEST_SESSION_ID": "SESS-001", "SAP_CODE": "MAT-A",
             "CANONICAL_QUANTITY": 10, "CANONICAL_UOM": "ROL",
             "QUANTITY": 10, "UOM": "m",
             "MATERIAL_TYPE": "Sheet", "NESTING_DESCRIPTION": "Desc"},
        ])
        client.add_row.return_value = {"id": 123}

        with patch("shared.inventory_service.log_inventory_transactions_batch") as mock_txn_batch:
            mock_txn_batch.return_value = ["TXN-001"]

            allocate_for_session(
                client=client, nest_session_id="SESS-001", tag_id="TAG-001",
                planned_date="2026-04-15",
                trace_id="t-explicit-date",
            )

        row_data = client.add_row.call_args[0][1]
        assert row_data["PLANNED_DATE"] == "2026-04-15"


class TestAllocateForSessionWriteFailures:

    @pytest.mark.unit
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    @patch(f"{_MOD}.now_uae", return_value=FIXED_NOW)
    @patch(f"{_MOD}.format_datetime_for_smartsheet", return_value=FIXED_FORMATTED)
    @patch(f"{_MOD}.compute_available_qty")
    @patch(f"{_MOD}.determine_stock_flag", return_value="Green")
    @patch(f"{_MOD}.log_user_action")
    @patch(f"{_MOD}.create_exception", return_value="EX-FAIL")
    def test_add_row_failure_produces_alloc_write_failed_warning(
        self, mock_exc, mock_log, mock_flag, mock_stock, _fmt, _now, _manifest
    ):
        """When client.add_row raises, ALLOC_WRITE_FAILED warning is added."""
        mock_stock.return_value = FakeAvailableStock(net_available=500.0)

        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(BOM_COLUMNS, [
            {"row_id": 1, "NEST_SESSION_ID": "SESS-001", "SAP_CODE": "MAT-A",
             "CANONICAL_QUANTITY": 50, "CANONICAL_UOM": "ROL",
             "QUANTITY": 50, "UOM": "m",
             "MATERIAL_TYPE": "Sheet", "NESTING_DESCRIPTION": "Desc"},
        ])
        client.add_row.side_effect = RuntimeError("Smartsheet API error")

        result = allocate_for_session(
            client=client, nest_session_id="SESS-001", tag_id="TAG-001",
            trace_id="t-fail",
        )

        assert any(w["code"] == "ALLOC_WRITE_FAILED" for w in result.warnings)
        assert result.allocation_ids == []  # No successful writes

    @pytest.mark.unit
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    @patch(f"{_MOD}.now_uae", return_value=FIXED_NOW)
    @patch(f"{_MOD}.format_datetime_for_smartsheet", return_value=FIXED_FORMATTED)
    @patch(f"{_MOD}.compute_available_qty")
    @patch(f"{_MOD}.determine_stock_flag", return_value="Green")
    @patch(f"{_MOD}.log_user_action")
    @patch(f"{_MOD}.create_exception")
    def test_inventory_txn_failure_produces_warning(
        self, mock_exc, mock_log, mock_flag, mock_stock, _fmt, _now, _manifest
    ):
        """When inventory txn write fails, TXN_WRITE_FAILED warning is added."""
        mock_stock.return_value = FakeAvailableStock(net_available=500.0)

        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(BOM_COLUMNS, [
            {"row_id": 1, "NEST_SESSION_ID": "SESS-001", "SAP_CODE": "MAT-A",
             "CANONICAL_QUANTITY": 50, "CANONICAL_UOM": "ROL",
             "QUANTITY": 50, "UOM": "m",
             "MATERIAL_TYPE": "Sheet", "NESTING_DESCRIPTION": "Desc"},
        ])
        client.add_row.return_value = {"id": 123}

        with patch("shared.inventory_service.log_inventory_transactions_batch",
                    side_effect=RuntimeError("Txn write error")):
            result = allocate_for_session(
                client=client, nest_session_id="SESS-001", tag_id="TAG-001",
                trace_id="t-txn-fail",
            )

        # Allocation row was written successfully
        assert len(result.allocation_ids) == 1
        # But txn write failure added a warning
        assert any(w["code"] == "TXN_WRITE_FAILED" for w in result.warnings)

    @pytest.mark.unit
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    @patch(f"{_MOD}.now_uae", return_value=FIXED_NOW)
    @patch(f"{_MOD}.format_datetime_for_smartsheet", return_value=FIXED_FORMATTED)
    @patch(f"{_MOD}.compute_available_qty")
    @patch(f"{_MOD}.determine_stock_flag", return_value="Red")
    @patch(f"{_MOD}.log_user_action")
    @patch(f"{_MOD}.create_exception")
    def test_shortage_exception_failure_adds_warning(
        self, mock_exc, mock_log, mock_flag, mock_stock, _fmt, _now, _manifest
    ):
        """When create_exception for shortages fails, EXCEPTION_FAILED warning is added.

        Step 4's shortage exception uses a local re-import from .audit, so we
        must also patch shared.audit.create_exception to simulate the failure.
        """
        mock_stock.return_value = FakeAvailableStock(net_available=-5.0)

        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(BOM_COLUMNS, [
            {"row_id": 1, "NEST_SESSION_ID": "SESS-001", "SAP_CODE": "MAT-A",
             "CANONICAL_QUANTITY": 100, "CANONICAL_UOM": "ROL",
             "QUANTITY": 100, "UOM": "m",
             "MATERIAL_TYPE": "Sheet", "NESTING_DESCRIPTION": "Desc"},
        ])
        client.add_row.return_value = {"id": 123}

        # The shortage exception in Step 4 does `from .audit import create_exception`
        # which bypasses the module-level patch, so we also patch shared.audit
        with patch("shared.audit.create_exception",
                    side_effect=RuntimeError("Exception write failed")):
            result = allocate_for_session(
                client=client, nest_session_id="SESS-001", tag_id="TAG-001",
                trace_id="t-exc-fail",
            )

        assert any(w["code"] == "EXCEPTION_FAILED" for w in result.warnings)


class TestAllocateForSessionEdgeCases:

    @pytest.mark.unit
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    @patch(f"{_MOD}.now_uae", return_value=FIXED_NOW)
    @patch(f"{_MOD}.format_datetime_for_smartsheet", return_value=FIXED_FORMATTED)
    @patch(f"{_MOD}.compute_available_qty")
    @patch(f"{_MOD}.determine_stock_flag", return_value="Green")
    @patch(f"{_MOD}.log_user_action")
    @patch(f"{_MOD}.create_exception")
    def test_all_bom_lines_without_sap_code_still_works(
        self, mock_exc, mock_log, mock_flag, mock_stock, _fmt, _now, _manifest
    ):
        """Session with rows but all missing SAP codes -> no allocations but no crash."""
        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(BOM_COLUMNS, [
            {"row_id": 1, "NEST_SESSION_ID": "SESS-001", "SAP_CODE": None,
             "CANONICAL_QUANTITY": 10, "CANONICAL_UOM": "ROL",
             "QUANTITY": 10, "UOM": "m",
             "MATERIAL_TYPE": "Sheet", "NESTING_DESCRIPTION": "Unmapped 1"},
            {"row_id": 2, "NEST_SESSION_ID": "SESS-001", "SAP_CODE": "",
             "CANONICAL_QUANTITY": 20, "CANONICAL_UOM": "ROL",
             "QUANTITY": 20, "UOM": "m",
             "MATERIAL_TYPE": "Sheet", "NESTING_DESCRIPTION": "Unmapped 2"},
        ])

        with patch("shared.inventory_service.log_inventory_transactions_batch"):
            result = allocate_for_session(
                client=client, nest_session_id="SESS-001", tag_id="TAG-001",
                trace_id="t-no-sap",
            )

        # No materials aggregated, so status stays ALLOCATED (no shortage/partial flags set)
        assert result.status == "ALLOCATED"
        assert result.allocation_ids == []
        assert result.lines == []

    @pytest.mark.unit
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    @patch(f"{_MOD}.now_uae", return_value=FIXED_NOW)
    @patch(f"{_MOD}.format_datetime_for_smartsheet", return_value=FIXED_FORMATTED)
    @patch(f"{_MOD}.compute_available_qty")
    @patch(f"{_MOD}.determine_stock_flag", return_value="Green")
    @patch(f"{_MOD}.log_user_action")
    @patch(f"{_MOD}.create_exception")
    def test_first_description_wins_for_aggregated_materials(
        self, mock_exc, mock_log, mock_flag, mock_stock, _fmt, _now, _manifest
    ):
        """When aggregating, the nesting_description from the first BOM line is used."""
        mock_stock.return_value = FakeAvailableStock(net_available=500.0)

        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(BOM_COLUMNS, [
            {"row_id": 1, "NEST_SESSION_ID": "SESS-001", "SAP_CODE": "MAT-A",
             "CANONICAL_QUANTITY": 30, "CANONICAL_UOM": "ROL",
             "QUANTITY": 30, "UOM": "m",
             "MATERIAL_TYPE": "Sheet", "NESTING_DESCRIPTION": "First Description"},
            {"row_id": 2, "NEST_SESSION_ID": "SESS-001", "SAP_CODE": "MAT-A",
             "CANONICAL_QUANTITY": 70, "CANONICAL_UOM": "ROL",
             "QUANTITY": 70, "UOM": "m",
             "MATERIAL_TYPE": "Sheet", "NESTING_DESCRIPTION": "Second Description"},
        ])
        client.add_row.return_value = {"id": 123}

        with patch("shared.inventory_service.log_inventory_transactions_batch") as mock_txn_batch:
            mock_txn_batch.return_value = ["TXN-001"]

            allocate_for_session(
                client=client, nest_session_id="SESS-001", tag_id="TAG-001",
                trace_id="t-desc",
            )

        row_data = client.add_row.call_args[0][1]
        assert row_data["NESTING_DESCRIPTION"] == "First Description"

    @pytest.mark.unit
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    @patch(f"{_MOD}.now_uae", return_value=FIXED_NOW)
    @patch(f"{_MOD}.format_datetime_for_smartsheet", return_value=FIXED_FORMATTED)
    @patch(f"{_MOD}.compute_available_qty")
    @patch(f"{_MOD}.determine_stock_flag", return_value="Green")
    @patch(f"{_MOD}.log_user_action")
    @patch(f"{_MOD}.create_exception")
    def test_session_remarks_in_allocation_row(
        self, mock_exc, mock_log, mock_flag, mock_stock, _fmt, _now, _manifest
    ):
        """Allocation row remarks contain the session ID."""
        mock_stock.return_value = FakeAvailableStock(net_available=500.0)

        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(BOM_COLUMNS, [
            {"row_id": 1, "NEST_SESSION_ID": "SESS-XYZ", "SAP_CODE": "MAT-A",
             "CANONICAL_QUANTITY": 10, "CANONICAL_UOM": "ROL",
             "QUANTITY": 10, "UOM": "m",
             "MATERIAL_TYPE": "Sheet", "NESTING_DESCRIPTION": "Desc"},
        ])
        client.add_row.return_value = {"id": 123}

        with patch("shared.inventory_service.log_inventory_transactions_batch") as mock_txn_batch:
            mock_txn_batch.return_value = ["TXN-001"]

            allocate_for_session(
                client=client, nest_session_id="SESS-XYZ", tag_id="TAG-001",
                trace_id="t-remarks",
            )

        row_data = client.add_row.call_args[0][1]
        assert "SESS-XYZ" in row_data["REMARKS"]
        assert row_data["NEST_SESSION_ID"] == "SESS-XYZ"

    @pytest.mark.unit
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    @patch(f"{_MOD}.now_uae", return_value=FIXED_NOW)
    @patch(f"{_MOD}.format_datetime_for_smartsheet", return_value=FIXED_FORMATTED)
    @patch(f"{_MOD}.compute_available_qty")
    @patch(f"{_MOD}.determine_stock_flag", return_value="Green")
    @patch(f"{_MOD}.log_user_action")
    @patch(f"{_MOD}.create_exception")
    def test_user_action_logged_on_success(
        self, mock_exc, mock_log, mock_flag, mock_stock, _fmt, _now, _manifest
    ):
        """log_user_action is called after successful allocation write."""
        mock_stock.return_value = FakeAvailableStock(net_available=500.0)

        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(BOM_COLUMNS, [
            {"row_id": 1, "NEST_SESSION_ID": "SESS-001", "SAP_CODE": "MAT-A",
             "CANONICAL_QUANTITY": 10, "CANONICAL_UOM": "ROL",
             "QUANTITY": 10, "UOM": "m",
             "MATERIAL_TYPE": "Sheet", "NESTING_DESCRIPTION": "Desc"},
        ])
        client.add_row.return_value = {"id": 123}

        with patch("shared.inventory_service.log_inventory_transactions_batch") as mock_txn_batch:
            mock_txn_batch.return_value = ["TXN-001"]

            allocate_for_session(
                client=client, nest_session_id="SESS-001", tag_id="TAG-001",
                trace_id="t-audit",
            )

        mock_log.assert_called_once()
        log_kwargs = mock_log.call_args
        # Verify the action type is ALLOCATION_CREATED
        assert log_kwargs[1]["action_type"] == "ALLOCATION_CREATED" or \
               log_kwargs.kwargs.get("action_type") == "ALLOCATION_CREATED"
