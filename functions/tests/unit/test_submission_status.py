"""
Unit Tests for fn_submission_status
====================================
Tests the GET /api/submission/status/{submission_id} endpoint.

Covers:
- Missing submission_id in route params (400)
- Submission not found (404)
- Status mapping: Submitted -> PENDING_APPROVAL, Approved -> APPROVED,
  Adjustment Requested -> REJECTED, unknown -> PENDING
- Response shape (submission_id, status, warnings, errors, created_at)
- Exception record on unhandled error
"""

import pytest
import json
from unittest.mock import MagicMock, patch

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import azure.functions as func
from fn_submission_status import main


# --------------- Patch target constants ---------------
# get_manifest and create_exception are imported locally inside main(),
# so we patch at the source module.
_PATCH_GET_CLIENT = "fn_submission_status.get_smartsheet_client"
_PATCH_PARSE_ROWS = "fn_submission_status._parse_rows"
_PATCH_GET_MANIFEST = "shared.manifest.get_manifest"
_PATCH_CREATE_EXCEPTION = "shared.audit.create_exception"


# --------------- helpers ---------------

def _make_get_request(submission_id=None):
    """Build an azure.functions.HttpRequest for GET /api/submission/status/{id}."""
    route_params = {}
    if submission_id is not None:
        route_params["submission_id"] = submission_id

    return func.HttpRequest(
        method="GET",
        url=f"/api/submission/status/{submission_id or ''}",
        route_params=route_params,
        body=b"",
    )


def _consumption_row(
    consumption_id="SUBM-123",
    status="Submitted",
    consumption_date="2026-02-06T10:00:00Z",
    col_id="Consumption ID",
    col_status="Status",
    col_date="Consumption Date",
):
    """Return a dict that mimics _parse_rows output for a CONSUMPTION_LOG row."""
    return {
        "row_id": 9001,
        col_id: consumption_id,
        col_status: status,
        col_date: consumption_date,
    }


# --------------- test class ---------------

@pytest.mark.unit
class TestSubmissionStatus:
    """Tests for fn_submission_status.main()."""

    # ---- 1. Missing submission_id ----

    @patch(_PATCH_GET_CLIENT)
    def test_missing_submission_id_returns_400(self, _mock_get_client):
        req = _make_get_request(submission_id=None)
        resp = main(req)

        assert resp.status_code == 400
        body = json.loads(resp.get_body())
        assert body["error"]["code"] == "INVALID_PAYLOAD"
        assert "submission_id" in body["error"]["message"].lower()

    # ---- 2. Submission not found ----

    @patch(_PATCH_PARSE_ROWS, return_value=[])
    @patch(_PATCH_GET_MANIFEST)
    @patch(_PATCH_GET_CLIENT)
    def test_submission_not_found_returns_404(
        self, mock_get_client, mock_manifest_fn, _mock_parse,
    ):
        mock_get_client.return_value = MagicMock()

        manifest = MagicMock()
        manifest.get_column_name.side_effect = lambda s, c: c
        mock_manifest_fn.return_value = manifest

        req = _make_get_request(submission_id="SUBM-MISSING")
        resp = main(req)

        assert resp.status_code == 404
        body = json.loads(resp.get_body())
        assert body["error"]["code"] == "NOT_FOUND"
        assert "SUBM-MISSING" in body["error"]["message"]

    # ---- 3. Status mapping: Submitted -> PENDING_APPROVAL ----

    @patch(_PATCH_PARSE_ROWS)
    @patch(_PATCH_GET_MANIFEST)
    @patch(_PATCH_GET_CLIENT)
    def test_submitted_maps_to_pending_approval(
        self, mock_get_client, mock_manifest_fn, mock_parse,
    ):
        mock_get_client.return_value = MagicMock()

        manifest = MagicMock()
        manifest.get_column_name.side_effect = lambda s, c: c
        mock_manifest_fn.return_value = manifest

        mock_parse.return_value = [
            _consumption_row(
                consumption_id="SUBM-200",
                status="Submitted",
                col_id="CONSUMPTION_ID",
                col_status="STATUS",
                col_date="CONSUMPTION_DATE",
            ),
        ]

        req = _make_get_request(submission_id="SUBM-200")
        resp = main(req)

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["status"] == "PENDING_APPROVAL"
        assert body["submission_id"] == "SUBM-200"

    # ---- 4. Status mapping: Approved -> APPROVED ----

    @patch(_PATCH_PARSE_ROWS)
    @patch(_PATCH_GET_MANIFEST)
    @patch(_PATCH_GET_CLIENT)
    def test_approved_maps_to_approved(
        self, mock_get_client, mock_manifest_fn, mock_parse,
    ):
        mock_get_client.return_value = MagicMock()

        manifest = MagicMock()
        manifest.get_column_name.side_effect = lambda s, c: c
        mock_manifest_fn.return_value = manifest

        mock_parse.return_value = [
            _consumption_row(
                consumption_id="SUBM-201",
                status="Approved",
                col_id="CONSUMPTION_ID",
                col_status="STATUS",
                col_date="CONSUMPTION_DATE",
            ),
        ]

        req = _make_get_request(submission_id="SUBM-201")
        resp = main(req)

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["status"] == "APPROVED"

    # ---- 5. Status mapping: Adjustment Requested -> REJECTED ----

    @patch(_PATCH_PARSE_ROWS)
    @patch(_PATCH_GET_MANIFEST)
    @patch(_PATCH_GET_CLIENT)
    def test_adjustment_requested_maps_to_rejected(
        self, mock_get_client, mock_manifest_fn, mock_parse,
    ):
        mock_get_client.return_value = MagicMock()

        manifest = MagicMock()
        manifest.get_column_name.side_effect = lambda s, c: c
        mock_manifest_fn.return_value = manifest

        mock_parse.return_value = [
            _consumption_row(
                consumption_id="SUBM-202",
                status="Adjustment Requested",
                col_id="CONSUMPTION_ID",
                col_status="STATUS",
                col_date="CONSUMPTION_DATE",
            ),
        ]

        req = _make_get_request(submission_id="SUBM-202")
        resp = main(req)

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["status"] == "REJECTED"

    # ---- 6. Status mapping: unknown -> PENDING ----

    @patch(_PATCH_PARSE_ROWS)
    @patch(_PATCH_GET_MANIFEST)
    @patch(_PATCH_GET_CLIENT)
    def test_unknown_status_maps_to_pending(
        self, mock_get_client, mock_manifest_fn, mock_parse,
    ):
        mock_get_client.return_value = MagicMock()

        manifest = MagicMock()
        manifest.get_column_name.side_effect = lambda s, c: c
        mock_manifest_fn.return_value = manifest

        mock_parse.return_value = [
            _consumption_row(
                consumption_id="SUBM-203",
                status="SomeUnknownStatus",
                col_id="CONSUMPTION_ID",
                col_status="STATUS",
                col_date="CONSUMPTION_DATE",
            ),
        ]

        req = _make_get_request(submission_id="SUBM-203")
        resp = main(req)

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["status"] == "PENDING"

    # ---- 7. Response shape includes all expected fields ----

    @patch(_PATCH_PARSE_ROWS)
    @patch(_PATCH_GET_MANIFEST)
    @patch(_PATCH_GET_CLIENT)
    def test_response_includes_all_fields(
        self, mock_get_client, mock_manifest_fn, mock_parse,
    ):
        mock_get_client.return_value = MagicMock()

        manifest = MagicMock()
        manifest.get_column_name.side_effect = lambda s, c: c
        mock_manifest_fn.return_value = manifest

        mock_parse.return_value = [
            _consumption_row(
                consumption_id="SUBM-300",
                status="Submitted",
                consumption_date="2026-02-06T10:00:00Z",
                col_id="CONSUMPTION_ID",
                col_status="STATUS",
                col_date="CONSUMPTION_DATE",
            ),
        ]

        req = _make_get_request(submission_id="SUBM-300")
        resp = main(req)

        assert resp.status_code == 200
        body = json.loads(resp.get_body())

        # Required fields per SubmissionStatusResponse model
        assert "submission_id" in body
        assert "status" in body
        assert "warnings" in body
        assert "errors" in body
        assert "created_at" in body

        assert body["submission_id"] == "SUBM-300"
        assert body["created_at"] == "2026-02-06T10:00:00Z"
        assert isinstance(body["warnings"], list)
        assert isinstance(body["errors"], list)

    # ---- 8. Empty warnings and errors lists ----

    @patch(_PATCH_PARSE_ROWS)
    @patch(_PATCH_GET_MANIFEST)
    @patch(_PATCH_GET_CLIENT)
    def test_warnings_and_errors_are_empty_lists(
        self, mock_get_client, mock_manifest_fn, mock_parse,
    ):
        mock_get_client.return_value = MagicMock()

        manifest = MagicMock()
        manifest.get_column_name.side_effect = lambda s, c: c
        mock_manifest_fn.return_value = manifest

        mock_parse.return_value = [
            _consumption_row(
                col_id="CONSUMPTION_ID", col_status="STATUS", col_date="CONSUMPTION_DATE",
            ),
        ]

        req = _make_get_request(submission_id="SUBM-123")
        resp = main(req)

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["warnings"] == []
        assert body["errors"] == []

    # ---- 9. Null created_at when date column is empty ----

    @patch(_PATCH_PARSE_ROWS)
    @patch(_PATCH_GET_MANIFEST)
    @patch(_PATCH_GET_CLIENT)
    def test_null_created_at_when_date_missing(
        self, mock_get_client, mock_manifest_fn, mock_parse,
    ):
        mock_get_client.return_value = MagicMock()

        manifest = MagicMock()
        manifest.get_column_name.side_effect = lambda s, c: c
        mock_manifest_fn.return_value = manifest

        row = _consumption_row(
            consumption_date=None,
            col_id="CONSUMPTION_ID",
            col_status="STATUS",
            col_date="CONSUMPTION_DATE",
        )
        row["CONSUMPTION_DATE"] = None
        mock_parse.return_value = [row]

        req = _make_get_request(submission_id="SUBM-123")
        resp = main(req)

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["created_at"] is None

    # ---- 10. Unhandled exception returns 500 and creates exception record ----

    @patch(_PATCH_CREATE_EXCEPTION)
    @patch(_PATCH_GET_MANIFEST)
    @patch(_PATCH_GET_CLIENT)
    def test_unhandled_error_returns_500_and_creates_exception(
        self, mock_get_client, mock_manifest_fn, mock_create_exc,
    ):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        manifest = MagicMock()
        manifest.get_column_name.side_effect = RuntimeError("manifest broken")
        mock_manifest_fn.return_value = manifest

        req = _make_get_request(submission_id="SUBM-ERR")
        resp = main(req)

        assert resp.status_code == 500
        body = json.loads(resp.get_body())
        assert body["error"]["code"] == "SERVER_ERROR"
        assert "trace_id" in body

        mock_create_exc.assert_called_once()
        kwargs = mock_create_exc.call_args.kwargs
        assert kwargs["reason_code"].value == "SYSTEM_ERROR"
        assert kwargs["severity"].value == "CRITICAL"

    # ---- 11. First matching row is used when multiple exist ----

    @patch(_PATCH_PARSE_ROWS)
    @patch(_PATCH_GET_MANIFEST)
    @patch(_PATCH_GET_CLIENT)
    def test_first_matching_row_used(
        self, mock_get_client, mock_manifest_fn, mock_parse,
    ):
        mock_get_client.return_value = MagicMock()

        manifest = MagicMock()
        manifest.get_column_name.side_effect = lambda s, c: c
        mock_manifest_fn.return_value = manifest

        # Two rows with the same submission ID but different statuses
        mock_parse.return_value = [
            _consumption_row(
                consumption_id="SUBM-DUP",
                status="Approved",
                col_id="CONSUMPTION_ID",
                col_status="STATUS",
                col_date="CONSUMPTION_DATE",
            ),
            _consumption_row(
                consumption_id="SUBM-DUP",
                status="Submitted",
                col_id="CONSUMPTION_ID",
                col_status="STATUS",
                col_date="CONSUMPTION_DATE",
            ),
        ]

        req = _make_get_request(submission_id="SUBM-DUP")
        resp = main(req)

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        # The first row's status should be used
        assert body["status"] == "APPROVED"

    # ---- 12. Empty string status maps to PENDING ----

    @patch(_PATCH_PARSE_ROWS)
    @patch(_PATCH_GET_MANIFEST)
    @patch(_PATCH_GET_CLIENT)
    def test_empty_status_maps_to_pending(
        self, mock_get_client, mock_manifest_fn, mock_parse,
    ):
        mock_get_client.return_value = MagicMock()

        manifest = MagicMock()
        manifest.get_column_name.side_effect = lambda s, c: c
        mock_manifest_fn.return_value = manifest

        mock_parse.return_value = [
            _consumption_row(
                consumption_id="SUBM-EMPTY",
                status="",
                col_id="CONSUMPTION_ID",
                col_status="STATUS",
                col_date="CONSUMPTION_DATE",
            ),
        ]

        req = _make_get_request(submission_id="SUBM-EMPTY")
        resp = main(req)

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["status"] == "PENDING"

    # ---- 13. Non-matching rows are skipped ----

    @patch(_PATCH_PARSE_ROWS)
    @patch(_PATCH_GET_MANIFEST)
    @patch(_PATCH_GET_CLIENT)
    def test_non_matching_rows_skipped(
        self, mock_get_client, mock_manifest_fn, mock_parse,
    ):
        mock_get_client.return_value = MagicMock()

        manifest = MagicMock()
        manifest.get_column_name.side_effect = lambda s, c: c
        mock_manifest_fn.return_value = manifest

        mock_parse.return_value = [
            _consumption_row(
                consumption_id="SUBM-OTHER",
                status="Approved",
                col_id="CONSUMPTION_ID",
                col_status="STATUS",
                col_date="CONSUMPTION_DATE",
            ),
        ]

        req = _make_get_request(submission_id="SUBM-WANTED")
        resp = main(req)

        assert resp.status_code == 404
        body = json.loads(resp.get_body())
        assert body["error"]["code"] == "NOT_FOUND"
