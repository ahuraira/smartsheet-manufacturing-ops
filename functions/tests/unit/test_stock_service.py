"""
Unit Tests for Stock Service
=============================

Tests the stock availability computation in shared/stock_service.py:
- compute_available_qty() — net stock calculation from SAP snapshot + txn log
- determine_stock_flag() — Green/Yellow/Red classification
- _parse_rows() — raw Smartsheet data -> list of dicts
- AvailableStock.to_dict() — serialization

Key scenarios:
- Stock check disabled (STOCK_CHECK_ENABLED=False) -> Green with inf
- No SAP snapshot row -> sap_unrestricted stays 0
- Happy path: SAP baseline + positive/negative txns
- Adjustment txn: positive adds to receipts, negative adds to consumed
- Multiple txn types accumulated correctly
- net_available = sap_unrestricted + receipts - allocated - consumed
- Graceful handling if SAP_INVENTORY_SNAPSHOT or INVENTORY_TXN_LOG read fails
"""

import pytest
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.stock_service import (
    _parse_rows,
    compute_available_qty,
    determine_stock_flag,
    AvailableStock,
    _POSITIVE_TXN_TYPES,
    _NEGATIVE_TXN_TYPES,
)
from shared.logical_names import Sheet, Column


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


# ── Column definitions for SAP_INVENTORY_SNAPSHOT and INVENTORY_TXN_LOG ──

SAP_SNAPSHOT_COLUMNS = [
    (201, "MATERIAL_CODE"),
    (202, "UNRESTRICTED_QUANTITY"),
]

TXN_LOG_COLUMNS = [
    (301, "MATERIAL_CODE"),
    (302, "TXN_TYPE"),
    (303, "QUANTITY"),
]


# ── Stub manifest that returns column_logical as the physical name ───

class _StubManifest:
    def get_column_name(self, sheet_logical, column_logical):
        return column_logical


# Patch prefix
_MOD = "shared.stock_service"


# =====================================================================
# Tests: _parse_rows
# =====================================================================

class TestParseRows:

    @pytest.mark.unit
    def test_basic_row_parsing(self):
        """Rows are parsed into list of dicts keyed by column title."""
        sheet = _build_sheet_data(
            columns=[(1, "Material"), (2, "Qty")],
            rows=[
                {"row_id": 10, "Material": "MAT-A", "Qty": 100},
                {"row_id": 11, "Material": "MAT-B", "Qty": 200},
            ],
        )
        result = _parse_rows(sheet)
        assert len(result) == 2
        assert result[0]["Material"] == "MAT-A"
        assert result[0]["Qty"] == 100
        assert result[0]["row_id"] == 10

    @pytest.mark.unit
    def test_empty_sheet(self):
        """Empty rows list returns empty result."""
        sheet = _build_sheet_data(columns=[(1, "A")], rows=[])
        assert _parse_rows(sheet) == []

    @pytest.mark.unit
    def test_display_value_fallback(self):
        """When cell value is None/falsy, displayValue is used."""
        sheet = {
            "columns": [{"id": 1, "title": "Status"}],
            "rows": [{
                "id": 5,
                "cells": [{"columnId": 1, "value": None, "displayValue": "Active"}],
            }],
        }
        result = _parse_rows(sheet)
        assert result[0]["Status"] == "Active"

    @pytest.mark.unit
    def test_missing_columns_key(self):
        """Missing 'columns' key does not crash."""
        result = _parse_rows({"rows": [{"id": 1, "cells": []}]})
        assert len(result) == 1
        assert result[0]["row_id"] == 1


# =====================================================================
# Tests: AvailableStock.to_dict
# =====================================================================

class TestAvailableStockToDict:

    @pytest.mark.unit
    def test_serialization(self):
        """to_dict includes all fields."""
        stock = AvailableStock(
            material_code="MAT-A",
            sap_unrestricted=100.0,
            total_allocated=20.0,
            total_consumed=10.0,
            total_receipts=50.0,
            net_available=120.0,
            stock_check_flag="Green",
            stock_check_enabled=True,
        )
        d = stock.to_dict()
        assert d["material_code"] == "MAT-A"
        assert d["sap_unrestricted"] == 100.0
        assert d["total_allocated"] == 20.0
        assert d["total_consumed"] == 10.0
        assert d["total_receipts"] == 50.0
        assert d["net_available"] == 120.0
        assert d["stock_check_flag"] == "Green"
        assert d["stock_check_enabled"] is True

    @pytest.mark.unit
    def test_default_values(self):
        """Default values are zeros and Green."""
        stock = AvailableStock(material_code="MAT-X")
        d = stock.to_dict()
        assert d["sap_unrestricted"] == 0.0
        assert d["total_allocated"] == 0.0
        assert d["total_consumed"] == 0.0
        assert d["total_receipts"] == 0.0
        assert d["net_available"] == 0.0
        assert d["stock_check_flag"] == "Green"
        assert d["stock_check_enabled"] is True


# =====================================================================
# Tests: determine_stock_flag
# =====================================================================

class TestDetermineStockFlag:

    @pytest.mark.unit
    @patch(f"{_MOD}.STOCK_CHECK_ENABLED", True)
    def test_green_when_available_equals_needed(self):
        """Exactly enough stock -> Green."""
        assert determine_stock_flag(100.0, 100.0) == "Green"

    @pytest.mark.unit
    @patch(f"{_MOD}.STOCK_CHECK_ENABLED", True)
    def test_green_when_available_exceeds_needed(self):
        """More than enough stock -> Green."""
        assert determine_stock_flag(200.0, 100.0) == "Green"

    @pytest.mark.unit
    @patch(f"{_MOD}.STOCK_CHECK_ENABLED", True)
    def test_yellow_when_partial_available(self):
        """Some stock but not enough -> Yellow."""
        assert determine_stock_flag(50.0, 100.0) == "Yellow"

    @pytest.mark.unit
    @patch(f"{_MOD}.STOCK_CHECK_ENABLED", True)
    def test_yellow_with_very_small_available(self):
        """Even tiny amount of stock -> Yellow (not Red)."""
        assert determine_stock_flag(0.001, 100.0) == "Yellow"

    @pytest.mark.unit
    @patch(f"{_MOD}.STOCK_CHECK_ENABLED", True)
    def test_red_when_zero_available(self):
        """No stock -> Red."""
        assert determine_stock_flag(0.0, 100.0) == "Red"

    @pytest.mark.unit
    @patch(f"{_MOD}.STOCK_CHECK_ENABLED", True)
    def test_red_when_negative_available(self):
        """Negative stock -> Red."""
        assert determine_stock_flag(-10.0, 100.0) == "Red"

    @pytest.mark.unit
    @patch(f"{_MOD}.STOCK_CHECK_ENABLED", False)
    def test_always_green_when_disabled(self):
        """With stock check disabled, always returns Green."""
        assert determine_stock_flag(0.0, 100.0) == "Green"
        assert determine_stock_flag(-999.0, 100.0) == "Green"
        assert determine_stock_flag(50.0, 100.0) == "Green"

    @pytest.mark.unit
    @patch(f"{_MOD}.STOCK_CHECK_ENABLED", True)
    def test_green_with_zero_needed(self):
        """No quantity needed with any available -> Green."""
        assert determine_stock_flag(0.0, 0.0) == "Green"
        assert determine_stock_flag(100.0, 0.0) == "Green"


# =====================================================================
# Tests: compute_available_qty — Stock Check Disabled
# =====================================================================

class TestComputeAvailableQtyDisabled:

    @pytest.mark.unit
    @patch(f"{_MOD}.STOCK_CHECK_ENABLED", False)
    def test_returns_green_with_infinite_available(self):
        """When stock check disabled, always Green with inf net_available."""
        client = MagicMock()
        result = compute_available_qty(client, "MAT-A", trace_id="t-disabled")

        assert result.stock_check_flag == "Green"
        assert result.net_available == float("inf")
        assert result.stock_check_enabled is False
        assert result.material_code == "MAT-A"
        # Should NOT have called get_sheet at all
        client.get_sheet.assert_not_called()

    @pytest.mark.unit
    @patch(f"{_MOD}.STOCK_CHECK_ENABLED", False)
    def test_quantities_default_to_zero_when_disabled(self):
        """Detailed quantities are all zero when disabled."""
        client = MagicMock()
        result = compute_available_qty(client, "MAT-B", trace_id="t-dis2")

        assert result.sap_unrestricted == 0.0
        assert result.total_allocated == 0.0
        assert result.total_consumed == 0.0
        assert result.total_receipts == 0.0


# =====================================================================
# Tests: compute_available_qty — No SAP Snapshot
# =====================================================================

class TestComputeAvailableQtyNoSnapshot:

    @pytest.mark.unit
    @patch(f"{_MOD}.STOCK_CHECK_ENABLED", True)
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    def test_no_sap_row_sap_unrestricted_stays_zero(self, _manifest):
        """When no SAP snapshot row for the material, sap_unrestricted is 0."""
        client = MagicMock()
        # SAP snapshot sheet with rows for OTHER materials only
        sap_sheet = _build_sheet_data(SAP_SNAPSHOT_COLUMNS, [
            {"row_id": 1, "MATERIAL_CODE": "OTHER-MAT", "UNRESTRICTED_QUANTITY": 999},
        ])
        txn_sheet = _build_sheet_data(TXN_LOG_COLUMNS, [])

        client.get_sheet.side_effect = lambda s: {
            Sheet.SAP_INVENTORY_SNAPSHOT: sap_sheet,
            Sheet.INVENTORY_TXN_LOG: txn_sheet,
        }[s]

        result = compute_available_qty(client, "MAT-A", trace_id="t-no-snap")

        assert result.sap_unrestricted == 0.0
        assert result.net_available == 0.0

    @pytest.mark.unit
    @patch(f"{_MOD}.STOCK_CHECK_ENABLED", True)
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    def test_empty_sap_snapshot_sheet(self, _manifest):
        """Empty SAP snapshot sheet keeps sap_unrestricted at 0."""
        client = MagicMock()
        sap_sheet = _build_sheet_data(SAP_SNAPSHOT_COLUMNS, [])
        txn_sheet = _build_sheet_data(TXN_LOG_COLUMNS, [])

        client.get_sheet.side_effect = lambda s: {
            Sheet.SAP_INVENTORY_SNAPSHOT: sap_sheet,
            Sheet.INVENTORY_TXN_LOG: txn_sheet,
        }[s]

        result = compute_available_qty(client, "MAT-A", trace_id="t-empty-snap")
        assert result.sap_unrestricted == 0.0


# =====================================================================
# Tests: compute_available_qty — Happy Path
# =====================================================================

class TestComputeAvailableQtyHappyPath:

    @pytest.mark.unit
    @patch(f"{_MOD}.STOCK_CHECK_ENABLED", True)
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    def test_sap_baseline_only(self, _manifest):
        """SAP baseline with no transactions -> net = sap_unrestricted."""
        client = MagicMock()
        sap_sheet = _build_sheet_data(SAP_SNAPSHOT_COLUMNS, [
            {"row_id": 1, "MATERIAL_CODE": "MAT-A", "UNRESTRICTED_QUANTITY": 500},
        ])
        txn_sheet = _build_sheet_data(TXN_LOG_COLUMNS, [])

        client.get_sheet.side_effect = lambda s: {
            Sheet.SAP_INVENTORY_SNAPSHOT: sap_sheet,
            Sheet.INVENTORY_TXN_LOG: txn_sheet,
        }[s]

        result = compute_available_qty(client, "MAT-A", trace_id="t-base")

        assert result.sap_unrestricted == 500.0
        assert result.total_receipts == 0.0
        assert result.total_allocated == 0.0
        assert result.total_consumed == 0.0
        assert result.net_available == 500.0

    @pytest.mark.unit
    @patch(f"{_MOD}.STOCK_CHECK_ENABLED", True)
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    def test_full_computation(self, _manifest):
        """
        Full calculation: net = sap + receipts - allocated - consumed.

        SAP=500, Receipt=100, Allocation=-80 (abs=80), Issue=-30 (abs=30)
        net = 500 + 100 - 80 - 30 = 490
        """
        client = MagicMock()
        sap_sheet = _build_sheet_data(SAP_SNAPSHOT_COLUMNS, [
            {"row_id": 1, "MATERIAL_CODE": "MAT-A", "UNRESTRICTED_QUANTITY": 500},
        ])
        txn_sheet = _build_sheet_data(TXN_LOG_COLUMNS, [
            {"row_id": 10, "MATERIAL_CODE": "MAT-A", "TXN_TYPE": "Receipt", "QUANTITY": 100},
            {"row_id": 11, "MATERIAL_CODE": "MAT-A", "TXN_TYPE": "Allocation", "QUANTITY": -80},
            {"row_id": 12, "MATERIAL_CODE": "MAT-A", "TXN_TYPE": "Issue", "QUANTITY": -30},
        ])

        client.get_sheet.side_effect = lambda s: {
            Sheet.SAP_INVENTORY_SNAPSHOT: sap_sheet,
            Sheet.INVENTORY_TXN_LOG: txn_sheet,
        }[s]

        result = compute_available_qty(client, "MAT-A", trace_id="t-full")

        assert result.sap_unrestricted == 500.0
        assert result.total_receipts == 100.0
        assert result.total_allocated == 80.0
        assert result.total_consumed == 30.0
        assert result.net_available == 490.0

    @pytest.mark.unit
    @patch(f"{_MOD}.STOCK_CHECK_ENABLED", True)
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    def test_latest_sap_row_wins(self, _manifest):
        """When multiple SAP snapshot rows exist, the last (most recent) one wins."""
        client = MagicMock()
        sap_sheet = _build_sheet_data(SAP_SNAPSHOT_COLUMNS, [
            {"row_id": 1, "MATERIAL_CODE": "MAT-A", "UNRESTRICTED_QUANTITY": 100},
            {"row_id": 2, "MATERIAL_CODE": "MAT-A", "UNRESTRICTED_QUANTITY": 250},
            {"row_id": 3, "MATERIAL_CODE": "MAT-A", "UNRESTRICTED_QUANTITY": 300},
        ])
        txn_sheet = _build_sheet_data(TXN_LOG_COLUMNS, [])

        client.get_sheet.side_effect = lambda s: {
            Sheet.SAP_INVENTORY_SNAPSHOT: sap_sheet,
            Sheet.INVENTORY_TXN_LOG: txn_sheet,
        }[s]

        result = compute_available_qty(client, "MAT-A", trace_id="t-latest")

        # Last row's value should be used (reversed iteration takes last row first)
        assert result.sap_unrestricted == 300.0

    @pytest.mark.unit
    @patch(f"{_MOD}.STOCK_CHECK_ENABLED", True)
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    def test_filters_by_material_code(self, _manifest):
        """Only transactions matching the requested material are counted."""
        client = MagicMock()
        sap_sheet = _build_sheet_data(SAP_SNAPSHOT_COLUMNS, [
            {"row_id": 1, "MATERIAL_CODE": "MAT-A", "UNRESTRICTED_QUANTITY": 200},
            {"row_id": 2, "MATERIAL_CODE": "MAT-B", "UNRESTRICTED_QUANTITY": 999},
        ])
        txn_sheet = _build_sheet_data(TXN_LOG_COLUMNS, [
            {"row_id": 10, "MATERIAL_CODE": "MAT-A", "TXN_TYPE": "Allocation", "QUANTITY": -50},
            {"row_id": 11, "MATERIAL_CODE": "MAT-B", "TXN_TYPE": "Allocation", "QUANTITY": -888},
        ])

        client.get_sheet.side_effect = lambda s: {
            Sheet.SAP_INVENTORY_SNAPSHOT: sap_sheet,
            Sheet.INVENTORY_TXN_LOG: txn_sheet,
        }[s]

        result = compute_available_qty(client, "MAT-A", trace_id="t-filter")

        assert result.sap_unrestricted == 200.0
        assert result.total_allocated == 50.0
        # net = 200 + 0 - 50 - 0 = 150
        assert result.net_available == 150.0


# =====================================================================
# Tests: compute_available_qty — Transaction Types
# =====================================================================

class TestComputeAvailableQtyTxnTypes:

    @pytest.mark.unit
    @patch(f"{_MOD}.STOCK_CHECK_ENABLED", True)
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    def test_positive_txn_types_add_to_receipts(self, _manifest):
        """Receipt, Remnant Create, Remnant Return all add to total_receipts."""
        client = MagicMock()
        sap_sheet = _build_sheet_data(SAP_SNAPSHOT_COLUMNS, [])
        txn_sheet = _build_sheet_data(TXN_LOG_COLUMNS, [
            {"row_id": 10, "MATERIAL_CODE": "MAT-A", "TXN_TYPE": "Receipt", "QUANTITY": 100},
            {"row_id": 11, "MATERIAL_CODE": "MAT-A", "TXN_TYPE": "Remnant Create", "QUANTITY": 25},
            {"row_id": 12, "MATERIAL_CODE": "MAT-A", "TXN_TYPE": "Remnant Return", "QUANTITY": 15},
        ])

        client.get_sheet.side_effect = lambda s: {
            Sheet.SAP_INVENTORY_SNAPSHOT: sap_sheet,
            Sheet.INVENTORY_TXN_LOG: txn_sheet,
        }[s]

        result = compute_available_qty(client, "MAT-A", trace_id="t-pos")

        assert result.total_receipts == 140.0  # 100 + 25 + 15
        assert result.total_allocated == 0.0
        assert result.total_consumed == 0.0
        assert result.net_available == 140.0

    @pytest.mark.unit
    @patch(f"{_MOD}.STOCK_CHECK_ENABLED", True)
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    def test_allocation_txn_adds_to_allocated(self, _manifest):
        """Allocation txn quantities (negative) are abs-valued into total_allocated."""
        client = MagicMock()
        sap_sheet = _build_sheet_data(SAP_SNAPSHOT_COLUMNS, [])
        txn_sheet = _build_sheet_data(TXN_LOG_COLUMNS, [
            {"row_id": 10, "MATERIAL_CODE": "MAT-A", "TXN_TYPE": "Allocation", "QUANTITY": -80},
            {"row_id": 11, "MATERIAL_CODE": "MAT-A", "TXN_TYPE": "Allocation", "QUANTITY": -20},
        ])

        client.get_sheet.side_effect = lambda s: {
            Sheet.SAP_INVENTORY_SNAPSHOT: sap_sheet,
            Sheet.INVENTORY_TXN_LOG: txn_sheet,
        }[s]

        result = compute_available_qty(client, "MAT-A", trace_id="t-alloc")

        assert result.total_allocated == 100.0  # abs(-80) + abs(-20)
        assert result.net_available == -100.0  # 0 + 0 - 100 - 0

    @pytest.mark.unit
    @patch(f"{_MOD}.STOCK_CHECK_ENABLED", True)
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    def test_negative_txn_types_add_to_consumed(self, _manifest):
        """Issue, Consumption, Pick, DO Issue all add to total_consumed."""
        client = MagicMock()
        sap_sheet = _build_sheet_data(SAP_SNAPSHOT_COLUMNS, [])
        txn_sheet = _build_sheet_data(TXN_LOG_COLUMNS, [
            {"row_id": 10, "MATERIAL_CODE": "MAT-A", "TXN_TYPE": "Issue", "QUANTITY": -40},
            {"row_id": 11, "MATERIAL_CODE": "MAT-A", "TXN_TYPE": "Consumption", "QUANTITY": -30},
            {"row_id": 12, "MATERIAL_CODE": "MAT-A", "TXN_TYPE": "Pick", "QUANTITY": -10},
            {"row_id": 13, "MATERIAL_CODE": "MAT-A", "TXN_TYPE": "DO Issue", "QUANTITY": -5},
        ])

        client.get_sheet.side_effect = lambda s: {
            Sheet.SAP_INVENTORY_SNAPSHOT: sap_sheet,
            Sheet.INVENTORY_TXN_LOG: txn_sheet,
        }[s]

        result = compute_available_qty(client, "MAT-A", trace_id="t-neg")

        assert result.total_consumed == 85.0  # 40 + 30 + 10 + 5
        assert result.total_allocated == 0.0

    @pytest.mark.unit
    @patch(f"{_MOD}.STOCK_CHECK_ENABLED", True)
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    def test_positive_adjustment_adds_to_receipts(self, _manifest):
        """Positive Adjustment qty adds to receipts."""
        client = MagicMock()
        sap_sheet = _build_sheet_data(SAP_SNAPSHOT_COLUMNS, [])
        txn_sheet = _build_sheet_data(TXN_LOG_COLUMNS, [
            {"row_id": 10, "MATERIAL_CODE": "MAT-A", "TXN_TYPE": "Adjustment", "QUANTITY": 50},
        ])

        client.get_sheet.side_effect = lambda s: {
            Sheet.SAP_INVENTORY_SNAPSHOT: sap_sheet,
            Sheet.INVENTORY_TXN_LOG: txn_sheet,
        }[s]

        result = compute_available_qty(client, "MAT-A", trace_id="t-adj-pos")

        assert result.total_receipts == 50.0
        assert result.total_consumed == 0.0
        assert result.net_available == 50.0

    @pytest.mark.unit
    @patch(f"{_MOD}.STOCK_CHECK_ENABLED", True)
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    def test_negative_adjustment_adds_to_consumed(self, _manifest):
        """Negative Adjustment qty adds to consumed."""
        client = MagicMock()
        sap_sheet = _build_sheet_data(SAP_SNAPSHOT_COLUMNS, [])
        txn_sheet = _build_sheet_data(TXN_LOG_COLUMNS, [
            {"row_id": 10, "MATERIAL_CODE": "MAT-A", "TXN_TYPE": "Adjustment", "QUANTITY": -20},
        ])

        client.get_sheet.side_effect = lambda s: {
            Sheet.SAP_INVENTORY_SNAPSHOT: sap_sheet,
            Sheet.INVENTORY_TXN_LOG: txn_sheet,
        }[s]

        result = compute_available_qty(client, "MAT-A", trace_id="t-adj-neg")

        assert result.total_receipts == 0.0
        assert result.total_consumed == 20.0
        assert result.net_available == -20.0

    @pytest.mark.unit
    @patch(f"{_MOD}.STOCK_CHECK_ENABLED", True)
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    def test_zero_adjustment_adds_to_receipts(self, _manifest):
        """Zero-value Adjustment (qty >= 0) adds to receipts (adds 0)."""
        client = MagicMock()
        sap_sheet = _build_sheet_data(SAP_SNAPSHOT_COLUMNS, [
            {"row_id": 1, "MATERIAL_CODE": "MAT-A", "UNRESTRICTED_QUANTITY": 100},
        ])
        txn_sheet = _build_sheet_data(TXN_LOG_COLUMNS, [
            {"row_id": 10, "MATERIAL_CODE": "MAT-A", "TXN_TYPE": "Adjustment", "QUANTITY": 0},
        ])

        client.get_sheet.side_effect = lambda s: {
            Sheet.SAP_INVENTORY_SNAPSHOT: sap_sheet,
            Sheet.INVENTORY_TXN_LOG: txn_sheet,
        }[s]

        result = compute_available_qty(client, "MAT-A", trace_id="t-adj-zero")

        assert result.total_receipts == 0.0
        assert result.net_available == 100.0

    @pytest.mark.unit
    @patch(f"{_MOD}.STOCK_CHECK_ENABLED", True)
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    def test_unknown_txn_type_ignored(self, _manifest):
        """Unknown transaction types are silently ignored."""
        client = MagicMock()
        sap_sheet = _build_sheet_data(SAP_SNAPSHOT_COLUMNS, [
            {"row_id": 1, "MATERIAL_CODE": "MAT-A", "UNRESTRICTED_QUANTITY": 100},
        ])
        txn_sheet = _build_sheet_data(TXN_LOG_COLUMNS, [
            {"row_id": 10, "MATERIAL_CODE": "MAT-A", "TXN_TYPE": "UnknownType", "QUANTITY": 999},
        ])

        client.get_sheet.side_effect = lambda s: {
            Sheet.SAP_INVENTORY_SNAPSHOT: sap_sheet,
            Sheet.INVENTORY_TXN_LOG: txn_sheet,
        }[s]

        result = compute_available_qty(client, "MAT-A", trace_id="t-unknown")

        assert result.total_receipts == 0.0
        assert result.total_allocated == 0.0
        assert result.total_consumed == 0.0
        assert result.net_available == 100.0


# =====================================================================
# Tests: compute_available_qty — Mixed Transactions
# =====================================================================

class TestComputeAvailableQtyMixed:

    @pytest.mark.unit
    @patch(f"{_MOD}.STOCK_CHECK_ENABLED", True)
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    def test_comprehensive_scenario(self, _manifest):
        """
        Comprehensive test with multiple txn types:

        SAP baseline: 1000
        Receipt:    +200
        Remnant Create: +50
        Allocation: -300 (abs)
        Issue:      -100 (abs)
        Consumption: -75 (abs)
        Adjustment(+): +25
        Adjustment(-): -10 (abs)

        net = 1000 + (200+50+25) - 300 - (100+75+10) = 1000 + 275 - 300 - 185 = 790
        """
        client = MagicMock()
        sap_sheet = _build_sheet_data(SAP_SNAPSHOT_COLUMNS, [
            {"row_id": 1, "MATERIAL_CODE": "MAT-A", "UNRESTRICTED_QUANTITY": 1000},
        ])
        txn_sheet = _build_sheet_data(TXN_LOG_COLUMNS, [
            {"row_id": 10, "MATERIAL_CODE": "MAT-A", "TXN_TYPE": "Receipt", "QUANTITY": 200},
            {"row_id": 11, "MATERIAL_CODE": "MAT-A", "TXN_TYPE": "Remnant Create", "QUANTITY": 50},
            {"row_id": 12, "MATERIAL_CODE": "MAT-A", "TXN_TYPE": "Allocation", "QUANTITY": -300},
            {"row_id": 13, "MATERIAL_CODE": "MAT-A", "TXN_TYPE": "Issue", "QUANTITY": -100},
            {"row_id": 14, "MATERIAL_CODE": "MAT-A", "TXN_TYPE": "Consumption", "QUANTITY": -75},
            {"row_id": 15, "MATERIAL_CODE": "MAT-A", "TXN_TYPE": "Adjustment", "QUANTITY": 25},
            {"row_id": 16, "MATERIAL_CODE": "MAT-A", "TXN_TYPE": "Adjustment", "QUANTITY": -10},
        ])

        client.get_sheet.side_effect = lambda s: {
            Sheet.SAP_INVENTORY_SNAPSHOT: sap_sheet,
            Sheet.INVENTORY_TXN_LOG: txn_sheet,
        }[s]

        result = compute_available_qty(client, "MAT-A", trace_id="t-mixed")

        assert result.sap_unrestricted == 1000.0
        assert result.total_receipts == 275.0   # 200 + 50 + 25
        assert result.total_allocated == 300.0   # abs(-300)
        assert result.total_consumed == 185.0    # abs(-100) + abs(-75) + abs(-10)
        assert result.net_available == 790.0     # 1000 + 275 - 300 - 185


# =====================================================================
# Tests: compute_available_qty — Error Handling
# =====================================================================

class TestComputeAvailableQtyErrors:

    @pytest.mark.unit
    @patch(f"{_MOD}.STOCK_CHECK_ENABLED", True)
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    def test_sap_snapshot_failure_continues(self, _manifest):
        """If SAP snapshot read fails, sap_unrestricted stays 0 and processing continues."""
        client = MagicMock()

        call_count = [0]
        def _get_sheet(sheet):
            call_count[0] += 1
            if sheet == Sheet.SAP_INVENTORY_SNAPSHOT:
                raise RuntimeError("SAP snapshot unavailable")
            return _build_sheet_data(TXN_LOG_COLUMNS, [
                {"row_id": 10, "MATERIAL_CODE": "MAT-A", "TXN_TYPE": "Receipt", "QUANTITY": 50},
            ])

        client.get_sheet.side_effect = _get_sheet

        result = compute_available_qty(client, "MAT-A", trace_id="t-sap-err")

        assert result.sap_unrestricted == 0.0
        assert result.total_receipts == 50.0
        assert result.net_available == 50.0

    @pytest.mark.unit
    @patch(f"{_MOD}.STOCK_CHECK_ENABLED", True)
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    def test_txn_log_failure_continues(self, _manifest):
        """If txn log read fails, only SAP baseline is used."""
        client = MagicMock()

        def _get_sheet(sheet):
            if sheet == Sheet.SAP_INVENTORY_SNAPSHOT:
                return _build_sheet_data(SAP_SNAPSHOT_COLUMNS, [
                    {"row_id": 1, "MATERIAL_CODE": "MAT-A", "UNRESTRICTED_QUANTITY": 200},
                ])
            raise RuntimeError("Txn log unavailable")

        client.get_sheet.side_effect = _get_sheet

        result = compute_available_qty(client, "MAT-A", trace_id="t-txn-err")

        assert result.sap_unrestricted == 200.0
        assert result.total_receipts == 0.0
        assert result.total_allocated == 0.0
        assert result.total_consumed == 0.0
        assert result.net_available == 200.0

    @pytest.mark.unit
    @patch(f"{_MOD}.STOCK_CHECK_ENABLED", True)
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    def test_both_sheets_fail_returns_zero(self, _manifest):
        """If both SAP snapshot and txn log fail, all values are 0."""
        client = MagicMock()
        client.get_sheet.side_effect = RuntimeError("Both sheets down")

        result = compute_available_qty(client, "MAT-A", trace_id="t-both-err")

        assert result.sap_unrestricted == 0.0
        assert result.total_receipts == 0.0
        assert result.total_allocated == 0.0
        assert result.total_consumed == 0.0
        assert result.net_available == 0.0
        assert result.material_code == "MAT-A"

    @pytest.mark.unit
    @patch(f"{_MOD}.STOCK_CHECK_ENABLED", True)
    @patch(f"{_MOD}.get_manifest", return_value=_StubManifest())
    def test_non_numeric_quantity_uses_default(self, _manifest):
        """Non-numeric quantity values are treated as 0 via parse_float_safe."""
        client = MagicMock()
        sap_sheet = _build_sheet_data(SAP_SNAPSHOT_COLUMNS, [
            {"row_id": 1, "MATERIAL_CODE": "MAT-A", "UNRESTRICTED_QUANTITY": "not-a-number"},
        ])
        txn_sheet = _build_sheet_data(TXN_LOG_COLUMNS, [
            {"row_id": 10, "MATERIAL_CODE": "MAT-A", "TXN_TYPE": "Receipt", "QUANTITY": "N/A"},
        ])

        client.get_sheet.side_effect = lambda s: {
            Sheet.SAP_INVENTORY_SNAPSHOT: sap_sheet,
            Sheet.INVENTORY_TXN_LOG: txn_sheet,
        }[s]

        result = compute_available_qty(client, "MAT-A", trace_id="t-nan")

        assert result.sap_unrestricted == 0.0
        assert result.total_receipts == 0.0
        assert result.net_available == 0.0


# =====================================================================
# Tests: Txn type set coverage
# =====================================================================

class TestTxnTypeClassification:

    @pytest.mark.unit
    def test_positive_types_are_documented(self):
        """Verify the documented positive txn types."""
        assert "Receipt" in _POSITIVE_TXN_TYPES
        assert "Remnant Create" in _POSITIVE_TXN_TYPES
        assert "Remnant Return" in _POSITIVE_TXN_TYPES

    @pytest.mark.unit
    def test_negative_types_are_documented(self):
        """Verify the documented negative txn types."""
        assert "Allocation" in _NEGATIVE_TXN_TYPES
        assert "Issue" in _NEGATIVE_TXN_TYPES
        assert "Consumption" in _NEGATIVE_TXN_TYPES
        assert "Pick" in _NEGATIVE_TXN_TYPES
        assert "DO Issue" in _NEGATIVE_TXN_TYPES

    @pytest.mark.unit
    def test_no_overlap_between_positive_and_negative(self):
        """Positive and negative sets must not overlap."""
        assert _POSITIVE_TXN_TYPES.isdisjoint(_NEGATIVE_TXN_TYPES)
