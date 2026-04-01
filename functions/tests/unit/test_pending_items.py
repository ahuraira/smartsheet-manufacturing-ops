"""
Unit Tests for fn_pending_items
================================
Tests:
- Happy path: returns pending allocations filtered by status (Submitted/Approved)
- Shift filter: only returns matching shift rows
- Max results: respects max parameter (default 50, max 100)
- Date filter: only returns today/yesterday planned dates
- Empty results: returns empty list, not error
- Builds deduplicated tag_choices for adaptive card
- Response shape includes trace_id, timestamp, pending_tags, allow_stock_submission, tag_choices
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
# Helpers to build realistic mock sheet data
# ---------------------------------------------------------------------------

COLUMN_DEFS = [
    {"id": 1, "title": "Status"},
    {"id": 2, "title": "Allocation ID"},
    {"id": 3, "title": "Tag Sheet ID"},
    {"id": 4, "title": "Planned Date"},
    {"id": 5, "title": "Quantity"},
    {"id": 6, "title": "Shift"},
]


def _make_row(row_id, status, alloc_id, tag_id, planned_date, qty, shift):
    """Build a single raw Smartsheet row dict."""
    return {
        "id": row_id,
        "cells": [
            {"columnId": 1, "value": status},
            {"columnId": 2, "value": alloc_id},
            {"columnId": 3, "value": tag_id},
            {"columnId": 4, "value": planned_date},
            {"columnId": 5, "value": qty},
            {"columnId": 6, "value": shift},
        ],
    }


def _build_sheet_data(rows):
    """Wrap rows in the standard Smartsheet sheet envelope."""
    return {"columns": COLUMN_DEFS, "rows": rows}


def _make_manifest_mock():
    """Return a mock manifest whose get_column_name maps logical names to the
    physical column titles used in COLUMN_DEFS above."""
    mapping = {
        "STATUS": "Status",
        "ALLOCATION_ID": "Allocation ID",
        "TAG_SHEET_ID": "Tag Sheet ID",
        "PLANNED_DATE": "Planned Date",
        "QUANTITY": "Quantity",
        "SHIFT": "Shift",
    }
    manifest = MagicMock()
    manifest.get_column_name = MagicMock(side_effect=lambda _sheet, col: mapping.get(col, col))
    return manifest


def _make_request(shift=None, max_results=None):
    """Build an azure.functions.HttpRequest for GET /api/pending-items."""
    params = {}
    if shift is not None:
        params["shift"] = shift
    if max_results is not None:
        params["max"] = str(max_results)
    return func.HttpRequest(
        method="GET",
        url="/api/pending-items",
        params=params,
        body=b"",
    )


# Fixed "now" anchored to 2026-03-24
FIXED_NOW = datetime(2026, 3, 24, 10, 0, 0)
TODAY_ISO = "2026-03-24"
YESTERDAY_ISO = "2026-03-23"
OLD_DATE_ISO = "2026-03-20"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestFnPendingItems:
    """Tests for the fn_pending_items Azure Function."""

    # -- Patch targets are relative to the fn_pending_items module ----------
    PATCH_CLIENT = "fn_pending_items.get_smartsheet_client"
    PATCH_MANIFEST = "fn_pending_items.get_manifest"
    PATCH_NOW_UAE = "fn_pending_items.now_uae"
    PATCH_CREATE_EXCEPTION = "fn_pending_items.create_exception"

    # ---- Happy path -------------------------------------------------------

    def test_happy_path_returns_pending_allocations(self):
        """Submitted and Approved rows for today are returned."""
        rows = [
            _make_row(100, "Submitted", "ALLOC-001", "TAG-001", TODAY_ISO, 50.0, "Morning"),
            _make_row(101, "Approved", "ALLOC-002", "TAG-002", TODAY_ISO, 30.0, "Evening"),
        ]
        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(rows)

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=_make_manifest_mock()), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW):

            from fn_pending_items import main
            resp = main(_make_request())

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert len(body["pending_tags"]) == 2
        ids = {t["allocation_id"] for t in body["pending_tags"]}
        assert ids == {"ALLOC-001", "ALLOC-002"}

    # ---- Response shape ---------------------------------------------------

    def test_response_includes_required_fields(self):
        """Response contains trace_id, timestamp, pending_tags,
        allow_stock_submission, and tag_choices."""
        rows = [
            _make_row(100, "Submitted", "ALLOC-001", "TAG-001", TODAY_ISO, 10.0, "Morning"),
        ]
        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(rows)

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=_make_manifest_mock()), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW):

            from fn_pending_items import main
            resp = main(_make_request())

        body = json.loads(resp.get_body())
        assert "trace_id" in body
        assert "timestamp" in body
        assert "pending_tags" in body
        assert body["allow_stock_submission"] is True
        assert "tag_choices" in body

    # ---- Status filter ----------------------------------------------------

    def test_filters_out_non_pending_statuses(self):
        """Rows with status other than Submitted/Approved are excluded."""
        rows = [
            _make_row(100, "Submitted", "ALLOC-001", "TAG-001", TODAY_ISO, 10.0, "Morning"),
            _make_row(101, "Completed", "ALLOC-002", "TAG-002", TODAY_ISO, 20.0, "Morning"),
            _make_row(102, "Cancelled", "ALLOC-003", "TAG-003", TODAY_ISO, 30.0, "Morning"),
            _make_row(103, "Approved", "ALLOC-004", "TAG-004", TODAY_ISO, 40.0, "Morning"),
        ]
        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(rows)

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=_make_manifest_mock()), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW):

            from fn_pending_items import main
            resp = main(_make_request())

        body = json.loads(resp.get_body())
        ids = {t["allocation_id"] for t in body["pending_tags"]}
        assert ids == {"ALLOC-001", "ALLOC-004"}

    # ---- Shift filter -----------------------------------------------------

    def test_shift_filter_morning(self):
        """Only Morning-shift rows are returned when shift=Morning."""
        rows = [
            _make_row(100, "Submitted", "ALLOC-001", "TAG-001", TODAY_ISO, 10.0, "Morning"),
            _make_row(101, "Submitted", "ALLOC-002", "TAG-002", TODAY_ISO, 20.0, "Evening"),
        ]
        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(rows)

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=_make_manifest_mock()), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW):

            from fn_pending_items import main
            resp = main(_make_request(shift="Morning"))

        body = json.loads(resp.get_body())
        assert len(body["pending_tags"]) == 1
        assert body["pending_tags"][0]["allocation_id"] == "ALLOC-001"

    def test_shift_filter_evening(self):
        """Only Evening-shift rows are returned when shift=Evening."""
        rows = [
            _make_row(100, "Submitted", "ALLOC-001", "TAG-001", TODAY_ISO, 10.0, "Morning"),
            _make_row(101, "Submitted", "ALLOC-002", "TAG-002", TODAY_ISO, 20.0, "Evening"),
        ]
        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(rows)

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=_make_manifest_mock()), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW):

            from fn_pending_items import main
            resp = main(_make_request(shift="Evening"))

        body = json.loads(resp.get_body())
        assert len(body["pending_tags"]) == 1
        assert body["pending_tags"][0]["allocation_id"] == "ALLOC-002"

    def test_no_shift_filter_returns_all_shifts(self):
        """Omitting shift param returns rows from every shift."""
        rows = [
            _make_row(100, "Submitted", "ALLOC-001", "TAG-001", TODAY_ISO, 10.0, "Morning"),
            _make_row(101, "Submitted", "ALLOC-002", "TAG-002", TODAY_ISO, 20.0, "Evening"),
        ]
        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(rows)

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=_make_manifest_mock()), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW):

            from fn_pending_items import main
            resp = main(_make_request())

        body = json.loads(resp.get_body())
        assert len(body["pending_tags"]) == 2

    # ---- Max results ------------------------------------------------------

    def test_max_results_default_50(self):
        """Without a max param the function returns at most 50 rows."""
        rows = [
            _make_row(1000 + i, "Submitted", f"ALLOC-{i:03d}", f"TAG-{i:03d}", TODAY_ISO, 1.0, "Morning")
            for i in range(60)
        ]
        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(rows)

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=_make_manifest_mock()), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW):

            from fn_pending_items import main
            resp = main(_make_request())

        body = json.loads(resp.get_body())
        assert len(body["pending_tags"]) == 50

    def test_max_results_custom(self):
        """The max query parameter limits the result count."""
        rows = [
            _make_row(1000 + i, "Submitted", f"ALLOC-{i:03d}", f"TAG-{i:03d}", TODAY_ISO, 1.0, "Morning")
            for i in range(20)
        ]
        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(rows)

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=_make_manifest_mock()), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW):

            from fn_pending_items import main
            resp = main(_make_request(max_results=5))

        body = json.loads(resp.get_body())
        assert len(body["pending_tags"]) == 5

    def test_max_results_capped_at_100(self):
        """Requesting max > 100 still returns at most 100 rows."""
        rows = [
            _make_row(1000 + i, "Submitted", f"ALLOC-{i:03d}", f"TAG-{i:03d}", TODAY_ISO, 1.0, "Morning")
            for i in range(120)
        ]
        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(rows)

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=_make_manifest_mock()), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW):

            from fn_pending_items import main
            resp = main(_make_request(max_results=999))

        body = json.loads(resp.get_body())
        assert len(body["pending_tags"]) == 100

    # ---- Date filter ------------------------------------------------------

    def test_returns_today_and_yesterday_rows(self):
        """Rows planned for today or yesterday are included."""
        rows = [
            _make_row(100, "Submitted", "ALLOC-TODAY", "TAG-001", TODAY_ISO, 10.0, "Morning"),
            _make_row(101, "Submitted", "ALLOC-YEST", "TAG-002", YESTERDAY_ISO, 20.0, "Morning"),
        ]
        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(rows)

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=_make_manifest_mock()), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW):

            from fn_pending_items import main
            resp = main(_make_request())

        body = json.loads(resp.get_body())
        ids = {t["allocation_id"] for t in body["pending_tags"]}
        assert ids == {"ALLOC-TODAY", "ALLOC-YEST"}

    def test_excludes_old_dates(self):
        """Rows with a planned date older than yesterday are excluded."""
        rows = [
            _make_row(100, "Submitted", "ALLOC-OLD", "TAG-001", OLD_DATE_ISO, 10.0, "Morning"),
            _make_row(101, "Submitted", "ALLOC-TODAY", "TAG-002", TODAY_ISO, 20.0, "Morning"),
        ]
        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(rows)

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=_make_manifest_mock()), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW):

            from fn_pending_items import main
            resp = main(_make_request())

        body = json.loads(resp.get_body())
        assert len(body["pending_tags"]) == 1
        assert body["pending_tags"][0]["allocation_id"] == "ALLOC-TODAY"

    def test_skips_rows_with_unparseable_date(self):
        """Rows whose Planned Date cannot be parsed are silently skipped."""
        rows = [
            _make_row(100, "Submitted", "ALLOC-BAD", "TAG-001", "not-a-date", 10.0, "Morning"),
            _make_row(101, "Submitted", "ALLOC-OK", "TAG-002", TODAY_ISO, 20.0, "Morning"),
        ]
        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(rows)

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=_make_manifest_mock()), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW):

            from fn_pending_items import main
            resp = main(_make_request())

        body = json.loads(resp.get_body())
        assert len(body["pending_tags"]) == 1
        assert body["pending_tags"][0]["allocation_id"] == "ALLOC-OK"

    # ---- Empty results ----------------------------------------------------

    def test_empty_sheet_returns_empty_list(self):
        """An empty ALLOCATION_LOG sheet returns an empty pending_tags list (not an error)."""
        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data([])

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=_make_manifest_mock()), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW):

            from fn_pending_items import main
            resp = main(_make_request())

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["pending_tags"] == []
        assert body["tag_choices"] == []

    def test_no_matching_rows_returns_empty_list(self):
        """All rows are filtered out (wrong status) -> empty list."""
        rows = [
            _make_row(100, "Completed", "ALLOC-001", "TAG-001", TODAY_ISO, 10.0, "Morning"),
        ]
        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(rows)

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=_make_manifest_mock()), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW):

            from fn_pending_items import main
            resp = main(_make_request())

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["pending_tags"] == []

    # ---- Tag choices deduplication ----------------------------------------

    def test_tag_choices_deduplicated(self):
        """Duplicate tag IDs produce only one entry in tag_choices."""
        rows = [
            _make_row(100, "Submitted", "ALLOC-001", "TAG-001", TODAY_ISO, 10.0, "Morning"),
            _make_row(101, "Submitted", "ALLOC-002", "TAG-001", TODAY_ISO, 20.0, "Morning"),
            _make_row(102, "Submitted", "ALLOC-003", "TAG-002", TODAY_ISO, 30.0, "Morning"),
        ]
        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(rows)

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=_make_manifest_mock()), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW):

            from fn_pending_items import main
            resp = main(_make_request())

        body = json.loads(resp.get_body())
        assert len(body["tag_choices"]) == 2
        choice_values = {c["value"] for c in body["tag_choices"]}
        assert choice_values == {"TAG-001", "TAG-002"}

    def test_tag_choices_have_title_and_value(self):
        """Each tag_choice entry has a title and value matching the tag ID."""
        rows = [
            _make_row(100, "Submitted", "ALLOC-001", "TAG-X", TODAY_ISO, 5.0, "Morning"),
        ]
        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(rows)

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=_make_manifest_mock()), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW):

            from fn_pending_items import main
            resp = main(_make_request())

        body = json.loads(resp.get_body())
        assert len(body["tag_choices"]) == 1
        assert body["tag_choices"][0]["title"] == "TAG-X"
        assert body["tag_choices"][0]["value"] == "TAG-X"

    # ---- Allocation summary fields ----------------------------------------

    def test_allocation_summary_fields(self):
        """Each pending_tags item carries the expected data."""
        rows = [
            _make_row(100, "Submitted", "ALLOC-001", "TAG-001", TODAY_ISO, 75.5, "Morning"),
        ]
        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(rows)

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=_make_manifest_mock()), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW):

            from fn_pending_items import main
            resp = main(_make_request())

        body = json.loads(resp.get_body())
        tag = body["pending_tags"][0]
        assert tag["allocation_id"] == "ALLOC-001"
        assert tag["tag_id"] == "TAG-001"
        assert tag["alloc_date"] == TODAY_ISO
        assert tag["alloc_qty"] == 75.5
        assert "TAG-001" in tag["brief"]
        assert "ALLOC-001" in tag["brief"]

    # ---- Quantity via parse_float_safe ------------------------------------

    def test_quantity_none_defaults_to_zero(self):
        """A None quantity cell is returned as 0.0 via parse_float_safe."""
        rows = [
            _make_row(100, "Submitted", "ALLOC-001", "TAG-001", TODAY_ISO, None, "Morning"),
        ]
        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(rows)

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=_make_manifest_mock()), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW):

            from fn_pending_items import main
            resp = main(_make_request())

        body = json.loads(resp.get_body())
        assert body["pending_tags"][0]["alloc_qty"] == 0.0

    # ---- Error handling ---------------------------------------------------

    def test_unhandled_error_returns_500_and_creates_exception(self):
        """An unexpected error in business logic yields a 500 response and
        attempts to create an exception record."""
        client = MagicMock()
        client.get_sheet.side_effect = RuntimeError("Smartsheet unavailable")

        mock_create_exception = MagicMock()

        # create_exception is imported inside the except block from shared.audit,
        # so we must patch it there rather than on the fn_pending_items module.
        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=_make_manifest_mock()), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW), \
             patch("shared.audit.create_exception", mock_create_exception):

            from fn_pending_items import main
            resp = main(_make_request())

        assert resp.status_code == 500
        body = json.loads(resp.get_body())
        assert body["error"]["code"] == "SERVER_ERROR"
        assert "trace_id" in body

    def test_error_response_contains_trace_id(self):
        """Even a 500 response includes a trace_id for correlation."""
        client = MagicMock()
        client.get_sheet.side_effect = ValueError("bad data")

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=_make_manifest_mock()), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW), \
             patch("shared.audit.create_exception", MagicMock()):

            from fn_pending_items import main
            resp = main(_make_request())

        assert resp.status_code == 500
        body = json.loads(resp.get_body())
        assert isinstance(body.get("trace_id"), str)
        assert len(body["trace_id"]) > 0

    # ---- Combined filters -------------------------------------------------

    def test_shift_and_date_combined_filter(self):
        """Shift + date filters work together correctly."""
        rows = [
            _make_row(100, "Submitted", "A1", "TAG-001", TODAY_ISO, 10.0, "Morning"),
            _make_row(101, "Submitted", "A2", "TAG-002", TODAY_ISO, 20.0, "Evening"),
            _make_row(102, "Submitted", "A3", "TAG-003", OLD_DATE_ISO, 30.0, "Morning"),
            _make_row(103, "Approved", "A4", "TAG-004", YESTERDAY_ISO, 40.0, "Morning"),
        ]
        client = MagicMock()
        client.get_sheet.return_value = _build_sheet_data(rows)

        with patch(self.PATCH_CLIENT, return_value=client), \
             patch(self.PATCH_MANIFEST, return_value=_make_manifest_mock()), \
             patch(self.PATCH_NOW_UAE, return_value=FIXED_NOW):

            from fn_pending_items import main
            resp = main(_make_request(shift="Morning"))

        body = json.loads(resp.get_body())
        ids = {t["allocation_id"] for t in body["pending_tags"]}
        # A1 (Morning + today), A4 (Morning + yesterday) pass;
        # A2 (Evening) and A3 (old date) are excluded.
        assert ids == {"A1", "A4"}
