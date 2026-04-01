"""
Unit Tests for fn_stock_snapshot
=================================
Tests:
- Returns 400 when plant parameter missing
- Happy path: returns stock lines for plant
- Empty inventory: returns empty lines list (not error)
- Handles INVENTORY_SNAPSHOT sheet not in manifest gracefully (returns empty)
- Limits results to 100 materials max
- Uses parse_float_safe for numeric values
- Response includes trace_id, plant, snapshot_time, lines
- Creates exception record on unhandled error
"""

import pytest
import json
from datetime import datetime
from unittest.mock import MagicMock, patch
import azure.functions as func

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


# ---------------------------------------------------------------------------
# Helpers to build realistic mock data
# ---------------------------------------------------------------------------

COLUMN_DEFS = [
    {"id": 1, "title": "Material Code"},
    {"id": 2, "title": "System Closing"},
    {"id": 3, "title": "UOM"},
    {"id": 4, "title": "Last Count Date"},
]


def _make_row(row_id, material_code, system_qty, uom, last_count):
    """Build a single raw Smartsheet row dict."""
    return {
        "id": row_id,
        "cells": [
            {"columnId": 1, "value": material_code},
            {"columnId": 2, "value": system_qty},
            {"columnId": 3, "value": uom},
            {"columnId": 4, "value": last_count},
        ],
    }


def _build_sheet_data(rows):
    """Wrap rows in the standard Smartsheet sheet envelope."""
    return {"columns": COLUMN_DEFS, "rows": rows}


def _parsed_rows_from_raw(raw_rows):
    """Simulate _parse_rows: convert raw sheet data into list of dicts."""
    col_map = {c["id"]: c["title"] for c in COLUMN_DEFS}
    result = []
    for raw in raw_rows:
        row = {"row_id": raw["id"]}
        for cell in raw.get("cells", []):
            name = col_map.get(cell["columnId"])
            if name:
                row[name] = cell.get("value")
        result.append(row)
    return result


def _make_manifest_mock(has_inventory_snapshot=True):
    """Return a mock manifest.

    When has_inventory_snapshot is False, get_sheet_id raises to simulate
    the sheet not existing in the manifest.
    """
    col_mapping = {
        "MATERIAL_CODE": "Material Code",
        "SYSTEM_CLOSING": "System Closing",
        "UOM": "UOM",
        "LAST_COUNT_DATE": "Last Count Date",
    }
    manifest = MagicMock()
    manifest.get_column_name = MagicMock(side_effect=lambda _sheet, col: col_mapping.get(col, col))

    if has_inventory_snapshot:
        manifest.get_sheet_id = MagicMock(return_value=9999)
    else:
        manifest.get_sheet_id = MagicMock(side_effect=KeyError("INVENTORY_SNAPSHOT not found"))

    return manifest


def _make_request(plant=None):
    """Build an azure.functions.HttpRequest for GET /api/stock/snapshot."""
    params = {}
    if plant is not None:
        params["plant"] = plant
    return func.HttpRequest(
        method="GET",
        url="/api/stock/snapshot",
        params=params,
        body=b"",
    )


FIXED_NOW = datetime(2026, 3, 24, 10, 0, 0)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestFnStockSnapshot:
    """Tests for the fn_stock_snapshot Azure Function."""

    PATCH_CLIENT = "fn_stock_snapshot.get_smartsheet_client"
    # get_manifest is imported inside the function body (from shared.manifest import get_manifest),
    # so we must patch the source module rather than the fn_stock_snapshot namespace.
    PATCH_MANIFEST = "shared.manifest.get_manifest"
    PATCH_PARSE_ROWS = "fn_stock_snapshot._parse_rows"
    PATCH_NOW_UAE = "fn_stock_snapshot.now_uae"
    # create_exception is also imported inside the except block from shared.audit.
    PATCH_CREATE_EXCEPTION = "shared.audit.create_exception"

    # ---- Validation -------------------------------------------------------

    def test_missing_plant_returns_400(self):
        """A request without the plant query param returns 400."""
        with patch(self.PATCH_CLIENT, return_value=MagicMock()), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW):

            from fn_stock_snapshot import main
            resp = main(_make_request(plant=None))

        assert resp.status_code == 400
        body = json.loads(resp.get_body())
        assert body["error"]["code"] == "INVALID_PAYLOAD"
        assert "plant" in body["error"]["message"].lower()
        assert "trace_id" in body

    def test_empty_plant_returns_400(self):
        """An empty-string plant param is treated as missing."""
        # Smartsheet's request param returns "" for ?plant= which is falsy
        req = func.HttpRequest(
            method="GET",
            url="/api/stock/snapshot",
            params={"plant": ""},
            body=b"",
        )
        with patch(self.PATCH_CLIENT, return_value=MagicMock()), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW):

            from fn_stock_snapshot import main
            resp = main(req)

        assert resp.status_code == 400

    # ---- Happy path -------------------------------------------------------

    def test_happy_path_returns_stock_lines(self):
        """Valid plant yields 200 with inventory lines."""
        raw_rows = [
            _make_row(100, "MAT-001", 1000.5, "SQM", "2026-03-23"),
            _make_row(101, "MAT-002", 500.0, "ROL", "2026-03-22"),
        ]
        parsed = _parsed_rows_from_raw(raw_rows)

        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(raw_rows)

        manifest = _make_manifest_mock(has_inventory_snapshot=True)

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=manifest), \
             patch(self.PATCH_PARSE_ROWS, return_value=parsed), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW):

            from fn_stock_snapshot import main
            resp = main(_make_request(plant="PLANT-A"))

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert len(body["lines"]) == 2
        codes = {line["canonical_code"] for line in body["lines"]}
        assert codes == {"MAT-001", "MAT-002"}

    # ---- Response shape ---------------------------------------------------

    def test_response_includes_required_fields(self):
        """Response contains trace_id, plant, snapshot_time, and lines."""
        parsed = _parsed_rows_from_raw([
            _make_row(100, "MAT-001", 10.0, "SQM", "2026-03-20"),
        ])
        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data([])

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=_make_manifest_mock()), \
             patch(self.PATCH_PARSE_ROWS, return_value=parsed), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW):

            from fn_stock_snapshot import main
            resp = main(_make_request(plant="PLANT-A"))

        body = json.loads(resp.get_body())
        assert "trace_id" in body
        assert body["plant"] == "PLANT-A"
        assert "snapshot_time" in body
        assert "lines" in body

    def test_line_fields(self):
        """Each line carries canonical_code, system_physical_closing, uom, last_count."""
        parsed = _parsed_rows_from_raw([
            _make_row(100, "MAT-X", 123.45, "ROL", "2026-03-01"),
        ])
        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data([])

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=_make_manifest_mock()), \
             patch(self.PATCH_PARSE_ROWS, return_value=parsed), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW):

            from fn_stock_snapshot import main
            resp = main(_make_request(plant="PLANT-A"))

        body = json.loads(resp.get_body())
        line = body["lines"][0]
        assert line["canonical_code"] == "MAT-X"
        assert line["system_physical_closing"] == 123.45
        assert line["uom"] == "ROL"
        assert line["last_count"] == "2026-03-01"

    # ---- Empty inventory --------------------------------------------------

    def test_empty_inventory_returns_empty_list(self):
        """An empty INVENTORY_SNAPSHOT sheet returns an empty lines list (not error)."""
        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data([])

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=_make_manifest_mock()), \
             patch(self.PATCH_PARSE_ROWS, return_value=[]), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW):

            from fn_stock_snapshot import main
            resp = main(_make_request(plant="PLANT-B"))

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["lines"] == []
        assert body["plant"] == "PLANT-B"

    # ---- Sheet not in manifest --------------------------------------------

    def test_missing_sheet_in_manifest_returns_empty(self):
        """If INVENTORY_SNAPSHOT is not in the manifest, return 200 with empty lines."""
        client = MagicMock()

        manifest = _make_manifest_mock(has_inventory_snapshot=False)

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=manifest), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW):

            from fn_stock_snapshot import main
            resp = main(_make_request(plant="PLANT-A"))

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["lines"] == []
        assert body["plant"] == "PLANT-A"

    # ---- Limit to 100 materials -------------------------------------------

    def test_limits_to_100_materials(self):
        """No more than 100 lines are returned even if the sheet has more."""
        raw_rows = [
            _make_row(1000 + i, f"MAT-{i:03d}", float(i), "SQM", "2026-03-20")
            for i in range(120)
        ]
        parsed = _parsed_rows_from_raw(raw_rows)

        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(raw_rows)

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=_make_manifest_mock()), \
             patch(self.PATCH_PARSE_ROWS, return_value=parsed), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW):

            from fn_stock_snapshot import main
            resp = main(_make_request(plant="PLANT-A"))

        body = json.loads(resp.get_body())
        assert len(body["lines"]) == 100

    # ---- parse_float_safe -------------------------------------------------

    def test_none_quantity_defaults_to_zero(self):
        """A None system_qty cell is returned as 0.0 via parse_float_safe."""
        parsed = _parsed_rows_from_raw([
            _make_row(100, "MAT-001", None, "SQM", "2026-03-20"),
        ])
        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data([])

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=_make_manifest_mock()), \
             patch(self.PATCH_PARSE_ROWS, return_value=parsed), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW):

            from fn_stock_snapshot import main
            resp = main(_make_request(plant="PLANT-A"))

        body = json.loads(resp.get_body())
        assert body["lines"][0]["system_physical_closing"] == 0.0

    def test_string_quantity_handled(self):
        """A string quantity like '250' is correctly parsed."""
        parsed = [{"row_id": 100, "Material Code": "MAT-001", "System Closing": "250", "UOM": "SQM", "Last Count Date": "2026-03-20"}]
        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data([])

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=_make_manifest_mock()), \
             patch(self.PATCH_PARSE_ROWS, return_value=parsed), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW):

            from fn_stock_snapshot import main
            resp = main(_make_request(plant="PLANT-A"))

        body = json.loads(resp.get_body())
        assert body["lines"][0]["system_physical_closing"] == 250.0

    def test_na_quantity_defaults_to_zero(self):
        """An 'N/A' quantity cell defaults to 0.0 via parse_float_safe."""
        parsed = [{"row_id": 100, "Material Code": "MAT-001", "System Closing": "N/A", "UOM": "SQM", "Last Count Date": None}]
        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data([])

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=_make_manifest_mock()), \
             patch(self.PATCH_PARSE_ROWS, return_value=parsed), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW):

            from fn_stock_snapshot import main
            resp = main(_make_request(plant="PLANT-A"))

        body = json.loads(resp.get_body())
        assert body["lines"][0]["system_physical_closing"] == 0.0

    # ---- Default values ---------------------------------------------------

    def test_missing_uom_defaults_to_sqm(self):
        """When UOM is missing from a row, the function defaults to 'SQM'."""
        parsed = [{"row_id": 100, "Material Code": "MAT-001", "System Closing": 10.0, "Last Count Date": None}]
        # Note: "UOM" key is absent
        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data([])

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=_make_manifest_mock()), \
             patch(self.PATCH_PARSE_ROWS, return_value=parsed), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW):

            from fn_stock_snapshot import main
            resp = main(_make_request(plant="PLANT-A"))

        body = json.loads(resp.get_body())
        assert body["lines"][0]["uom"] == "SQM"

    def test_missing_material_code_defaults_to_unknown(self):
        """When Material Code is missing, the function defaults to 'UNKNOWN'."""
        parsed = [{"row_id": 100, "System Closing": 5.0, "UOM": "ROL", "Last Count Date": None}]
        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data([])

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=_make_manifest_mock()), \
             patch(self.PATCH_PARSE_ROWS, return_value=parsed), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW):

            from fn_stock_snapshot import main
            resp = main(_make_request(plant="PLANT-A"))

        body = json.loads(resp.get_body())
        assert body["lines"][0]["canonical_code"] == "UNKNOWN"

    def test_last_count_none(self):
        """A None last_count date is passed through as null."""
        parsed = _parsed_rows_from_raw([
            _make_row(100, "MAT-001", 10.0, "SQM", None),
        ])
        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data([])

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=_make_manifest_mock()), \
             patch(self.PATCH_PARSE_ROWS, return_value=parsed), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW):

            from fn_stock_snapshot import main
            resp = main(_make_request(plant="PLANT-A"))

        body = json.loads(resp.get_body())
        assert body["lines"][0]["last_count"] is None

    # ---- Error handling ---------------------------------------------------

    def test_unhandled_error_returns_500(self):
        """An unexpected error in business logic yields a 500 response."""
        client = MagicMock()
        client.get_sheet.side_effect = RuntimeError("API failure")

        manifest = _make_manifest_mock()

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=manifest), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW), \
             patch("shared.audit.create_exception", MagicMock()):

            from fn_stock_snapshot import main
            resp = main(_make_request(plant="PLANT-A"))

        assert resp.status_code == 500
        body = json.loads(resp.get_body())
        assert body["error"]["code"] == "SERVER_ERROR"
        assert "trace_id" in body

    def test_error_response_contains_trace_id(self):
        """Even a 500 response includes a trace_id for correlation."""
        client = MagicMock()
        client.get_sheet.side_effect = ValueError("corrupt data")

        manifest = _make_manifest_mock()

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=manifest), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW), \
             patch("shared.audit.create_exception", MagicMock()):

            from fn_stock_snapshot import main
            resp = main(_make_request(plant="PLANT-A"))

        assert resp.status_code == 500
        body = json.loads(resp.get_body())
        assert isinstance(body.get("trace_id"), str)
        assert len(body["trace_id"]) > 0

    def test_unhandled_error_attempts_exception_record(self):
        """On unhandled error the function tries to create an exception record."""
        client = MagicMock()
        client.get_sheet.side_effect = RuntimeError("boom")

        manifest = _make_manifest_mock()
        mock_create_exception = MagicMock()

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=manifest), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW), \
             patch(self.PATCH_CREATE_EXCEPTION, mock_create_exception), \
             patch("shared.audit.create_exception", mock_create_exception):

            from fn_stock_snapshot import main
            resp = main(_make_request(plant="PLANT-A"))

        assert resp.status_code == 500
