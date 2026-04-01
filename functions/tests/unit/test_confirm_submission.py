"""
Unit Tests for fn_confirm_submission
=====================================
Tests the approve/reject flow for consumption submissions.

Covers:
- Invalid JSON / Pydantic validation
- Submission not found (404)
- Lock timeout (409)
- Idempotency (already at target status)
- APPROVE path (updates rows to "Approved")
- REJECT path (updates rows to "Adjustment Requested")
- Approver notes appended to remarks
- Audit logging via log_user_action
- Exception record on unhandled error
- Email resolution via resolve_user_email
"""

import pytest
import json
from unittest.mock import MagicMock, patch

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import azure.functions as func
from fn_confirm_submission import main


# --------------- Patch target constants ---------------
# get_manifest is imported locally inside main() via `from shared.manifest import get_manifest`,
# so we patch it at the source module.
_PATCH_GET_CLIENT = "fn_confirm_submission.get_smartsheet_client"
_PATCH_LOCK = "fn_confirm_submission.AllocationLock"
_PATCH_PARSE_ROWS = "fn_confirm_submission._parse_rows"
_PATCH_LOG_ACTION = "fn_confirm_submission.log_user_action"
_PATCH_CREATE_EXCEPTION = "fn_confirm_submission.create_exception"
_PATCH_RESOLVE_EMAIL = "fn_confirm_submission.resolve_user_email"
_PATCH_GET_MANIFEST = "shared.manifest.get_manifest"


# --------------- helpers ---------------

def _make_request(body=None, raw_body=None):
    """Build an azure.functions.HttpRequest for POST /api/submission/confirm."""
    if raw_body is not None:
        return func.HttpRequest(
            method="POST",
            url="/api/submission/confirm",
            body=raw_body,
            headers={"Content-Type": "application/json"},
        )
    return func.HttpRequest(
        method="POST",
        url="/api/submission/confirm",
        body=json.dumps(body).encode() if body is not None else b"{}",
        headers={"Content-Type": "application/json"},
    )


def _make_valid_body(
    submission_id="SUBM-123",
    approver="supervisor@company.com",
    decision="APPROVE",
    notes="Looks good",
    trace_id=None,
):
    body = {
        "processed_submission_id": submission_id,
        "approver": approver,
        "decision": decision,
        "notes": notes,
    }
    if trace_id:
        body["trace_id"] = trace_id
    return body


def _parsed_row(
    row_id=5001,
    consumption_id="SUBM-123",
    status="Submitted",
    remarks="initial remarks",
    col_cons_id="Consumption ID",
    col_status="Status",
    col_remarks="Remarks",
):
    """Return a dict that mimics _parse_rows output (physical column names).

    The production _parse_rows stores the Smartsheet row id under the key 'row_id',
    but fn_confirm_submission accesses it as row["id"]. We provide both keys so
    the test data satisfies the function code as-is.
    """
    return {
        "id": row_id,
        "row_id": row_id,
        col_cons_id: consumption_id,
        col_status: status,
        col_remarks: remarks,
    }


# --------------- mock context manager for AllocationLock ---------------

class _FakeLock:
    """Mimics the AllocationLock context-manager protocol."""
    def __init__(self, success=True):
        self.success = success

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


# --------------- fixtures ---------------

@pytest.fixture
def manifest():
    m = MagicMock()
    m.get_column_name.side_effect = lambda sheet, col: {
        "CONSUMPTION_ID": "Consumption ID",
        "STATUS": "Status",
        "REMARKS": "Remarks",
    }.get(col, col)
    return m


# --------------- test class ---------------

@pytest.mark.unit
class TestConfirmSubmission:
    """Tests for fn_confirm_submission.main()."""

    # ---- 1. Invalid JSON ----

    @patch(_PATCH_GET_CLIENT)
    def test_invalid_json_returns_400(self, mock_get_client):
        req = _make_request(raw_body=b"NOT JSON{{{")
        resp = main(req)

        assert resp.status_code == 400
        body = json.loads(resp.get_body())
        assert body["error"]["code"] == "INVALID_PAYLOAD"
        assert "Invalid JSON" in body["error"]["message"]

    # ---- 2. Pydantic validation failure ----

    @patch(_PATCH_GET_CLIENT)
    def test_missing_required_fields_returns_400(self, mock_get_client):
        """Omitting required fields should fail Pydantic validation."""
        req = _make_request(body={"approver": "a@b.com"})
        resp = main(req)

        assert resp.status_code == 400
        body = json.loads(resp.get_body())
        assert body["error"]["code"] == "INVALID_PAYLOAD"

    @patch(_PATCH_GET_CLIENT)
    def test_invalid_decision_returns_400(self, mock_get_client):
        """Decision must be APPROVE or REJECT."""
        req = _make_request(body=_make_valid_body(decision="MAYBE"))
        resp = main(req)

        assert resp.status_code == 400
        body = json.loads(resp.get_body())
        assert body["error"]["code"] == "INVALID_PAYLOAD"

    # ---- 3. Submission not found ----

    @patch(_PATCH_CREATE_EXCEPTION)
    @patch(_PATCH_RESOLVE_EMAIL, side_effect=lambda c, uid: uid)
    @patch(_PATCH_PARSE_ROWS, return_value=[])
    @patch(_PATCH_GET_MANIFEST)
    @patch(_PATCH_LOCK)
    @patch(_PATCH_GET_CLIENT)
    def test_submission_not_found_returns_404(
        self, mock_get_client, mock_lock_cls, mock_manifest_fn,
        mock_parse, mock_resolve, mock_create_exc,
    ):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_lock_cls.return_value = _FakeLock(success=True)

        manifest = MagicMock()
        manifest.get_column_name.side_effect = lambda s, c: c
        mock_manifest_fn.return_value = manifest

        req = _make_request(body=_make_valid_body())
        resp = main(req)

        assert resp.status_code == 404
        body = json.loads(resp.get_body())
        assert body["error"]["code"] == "NOT_FOUND"
        assert "SUBM-123" in body["error"]["message"]

        # Should create an exception record for missing submission
        mock_create_exc.assert_called_once()

    # ---- 4. Lock timeout ----

    @patch(_PATCH_RESOLVE_EMAIL, side_effect=lambda c, uid: uid)
    @patch(_PATCH_GET_MANIFEST)
    @patch(_PATCH_LOCK)
    @patch(_PATCH_GET_CLIENT)
    def test_lock_timeout_returns_409(
        self, mock_get_client, mock_lock_cls, mock_manifest_fn, mock_resolve,
    ):
        mock_get_client.return_value = MagicMock()
        mock_lock_cls.return_value = _FakeLock(success=False)

        manifest = MagicMock()
        manifest.get_column_name.side_effect = lambda s, c: c
        mock_manifest_fn.return_value = manifest

        req = _make_request(body=_make_valid_body())
        resp = main(req)

        assert resp.status_code == 409
        body = json.loads(resp.get_body())
        assert body["error"]["code"] == "LOCK_TIMEOUT"

    # ---- 5. Idempotency -- already approved ----

    @patch(_PATCH_RESOLVE_EMAIL, side_effect=lambda c, uid: uid)
    @patch(_PATCH_PARSE_ROWS)
    @patch(_PATCH_GET_MANIFEST)
    @patch(_PATCH_LOCK)
    @patch(_PATCH_GET_CLIENT)
    def test_already_approved_returns_already_processed(
        self, mock_get_client, mock_lock_cls, mock_manifest_fn,
        mock_parse, mock_resolve,
    ):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_lock_cls.return_value = _FakeLock(success=True)

        manifest = MagicMock()
        manifest.get_column_name.side_effect = lambda s, c: c
        mock_manifest_fn.return_value = manifest

        # Row already at "Approved" status
        mock_parse.return_value = [
            _parsed_row(
                status="Approved",
                col_cons_id="CONSUMPTION_ID",
                col_status="STATUS",
                col_remarks="REMARKS",
            ),
        ]

        req = _make_request(body=_make_valid_body(decision="APPROVE"))
        resp = main(req)

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["status"] == "ALREADY_PROCESSED"
        assert "Approved" in body["message"]

        # No update_row calls when already processed
        mock_client.update_row.assert_not_called()

    # ---- 5b. Idempotency -- already rejected ----

    @patch(_PATCH_RESOLVE_EMAIL, side_effect=lambda c, uid: uid)
    @patch(_PATCH_PARSE_ROWS)
    @patch(_PATCH_GET_MANIFEST)
    @patch(_PATCH_LOCK)
    @patch(_PATCH_GET_CLIENT)
    def test_already_rejected_returns_already_processed(
        self, mock_get_client, mock_lock_cls, mock_manifest_fn,
        mock_parse, mock_resolve,
    ):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_lock_cls.return_value = _FakeLock(success=True)

        manifest = MagicMock()
        manifest.get_column_name.side_effect = lambda s, c: c
        mock_manifest_fn.return_value = manifest

        mock_parse.return_value = [
            _parsed_row(
                status="Adjustment Requested",
                col_cons_id="CONSUMPTION_ID",
                col_status="STATUS",
                col_remarks="REMARKS",
            ),
        ]

        req = _make_request(body=_make_valid_body(decision="REJECT"))
        resp = main(req)

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["status"] == "ALREADY_PROCESSED"
        assert "Adjustment Requested" in body["message"]

    # ---- 6. APPROVE happy path ----

    @patch(_PATCH_LOG_ACTION)
    @patch(_PATCH_RESOLVE_EMAIL, side_effect=lambda c, uid: uid)
    @patch(_PATCH_PARSE_ROWS)
    @patch(_PATCH_GET_MANIFEST)
    @patch(_PATCH_LOCK)
    @patch(_PATCH_GET_CLIENT)
    def test_approve_updates_status_to_approved(
        self, mock_get_client, mock_lock_cls, mock_manifest_fn,
        mock_parse, mock_resolve, mock_log_action,
    ):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_lock_cls.return_value = _FakeLock(success=True)

        manifest = MagicMock()
        manifest.get_column_name.side_effect = lambda s, c: c
        mock_manifest_fn.return_value = manifest

        row = _parsed_row(
            row_id=5001,
            consumption_id="SUBM-123",
            status="Submitted",
            remarks="batch run",
            col_cons_id="CONSUMPTION_ID",
            col_status="STATUS",
            col_remarks="REMARKS",
        )
        mock_parse.return_value = [row]

        req = _make_request(body=_make_valid_body(
            decision="APPROVE", notes="All verified",
        ))
        resp = main(req)

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["status"] == "OK"
        assert body["new_status"] == "Approved"
        assert body["submission_id"] == "SUBM-123"

        # Verify update_row was called with the correct status
        mock_client.update_row.assert_called_once()
        call_args = mock_client.update_row.call_args
        assert call_args[0][0] == "CONSUMPTION_LOG"  # sheet
        assert call_args[0][1] == 5001  # row_id

        # The updates dict should contain status = "Approved"
        updates = call_args[0][2]
        assert updates["STATUS"] == "Approved"

        # Remarks should include approver notes
        assert "supervisor@company.com" in updates["REMARKS"]
        assert "All verified" in updates["REMARKS"]

    # ---- 7. REJECT happy path ----

    @patch(_PATCH_LOG_ACTION)
    @patch(_PATCH_RESOLVE_EMAIL, side_effect=lambda c, uid: uid)
    @patch(_PATCH_PARSE_ROWS)
    @patch(_PATCH_GET_MANIFEST)
    @patch(_PATCH_LOCK)
    @patch(_PATCH_GET_CLIENT)
    def test_reject_updates_status_to_adjustment_requested(
        self, mock_get_client, mock_lock_cls, mock_manifest_fn,
        mock_parse, mock_resolve, mock_log_action,
    ):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_lock_cls.return_value = _FakeLock(success=True)

        manifest = MagicMock()
        manifest.get_column_name.side_effect = lambda s, c: c
        mock_manifest_fn.return_value = manifest

        row = _parsed_row(
            row_id=5002,
            consumption_id="SUBM-456",
            status="Submitted",
            remarks="",
            col_cons_id="CONSUMPTION_ID",
            col_status="STATUS",
            col_remarks="REMARKS",
        )
        mock_parse.return_value = [row]

        req = _make_request(body=_make_valid_body(
            submission_id="SUBM-456",
            decision="REJECT",
            notes="Quantities incorrect",
        ))
        resp = main(req)

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["status"] == "OK"
        assert body["new_status"] == "Adjustment Requested"

        call_args = mock_client.update_row.call_args
        updates = call_args[0][2]
        assert updates["STATUS"] == "Adjustment Requested"

    # ---- 8. Notes appended to existing remarks ----

    @patch(_PATCH_LOG_ACTION)
    @patch(_PATCH_RESOLVE_EMAIL, side_effect=lambda c, uid: uid)
    @patch(_PATCH_PARSE_ROWS)
    @patch(_PATCH_GET_MANIFEST)
    @patch(_PATCH_LOCK)
    @patch(_PATCH_GET_CLIENT)
    def test_notes_appended_to_existing_remarks(
        self, mock_get_client, mock_lock_cls, mock_manifest_fn,
        mock_parse, mock_resolve, mock_log_action,
    ):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_lock_cls.return_value = _FakeLock(success=True)

        manifest = MagicMock()
        manifest.get_column_name.side_effect = lambda s, c: c
        mock_manifest_fn.return_value = manifest

        row = _parsed_row(
            remarks="existing notes here",
            col_cons_id="CONSUMPTION_ID",
            col_status="STATUS",
            col_remarks="REMARKS",
        )
        mock_parse.return_value = [row]

        req = _make_request(body=_make_valid_body(notes="supervisor comment"))
        resp = main(req)

        assert resp.status_code == 200
        updates = mock_client.update_row.call_args[0][2]
        remarks_value = updates["REMARKS"]
        # Original remarks preserved and new notes appended
        assert "existing notes here" in remarks_value
        assert "supervisor comment" in remarks_value
        assert "supervisor@company.com" in remarks_value

    # ---- 9. No notes -- REMARKS column not updated ----

    @patch(_PATCH_LOG_ACTION)
    @patch(_PATCH_RESOLVE_EMAIL, side_effect=lambda c, uid: uid)
    @patch(_PATCH_PARSE_ROWS)
    @patch(_PATCH_GET_MANIFEST)
    @patch(_PATCH_LOCK)
    @patch(_PATCH_GET_CLIENT)
    def test_no_notes_skips_remarks_update(
        self, mock_get_client, mock_lock_cls, mock_manifest_fn,
        mock_parse, mock_resolve, mock_log_action,
    ):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_lock_cls.return_value = _FakeLock(success=True)

        manifest = MagicMock()
        manifest.get_column_name.side_effect = lambda s, c: c
        mock_manifest_fn.return_value = manifest

        row = _parsed_row(
            col_cons_id="CONSUMPTION_ID",
            col_status="STATUS",
            col_remarks="REMARKS",
        )
        mock_parse.return_value = [row]

        req = _make_request(body=_make_valid_body(notes=None))
        resp = main(req)

        assert resp.status_code == 200
        updates = mock_client.update_row.call_args[0][2]
        # Only STATUS should be updated, REMARKS should not be in updates
        assert "STATUS" in updates
        assert "REMARKS" not in updates

    # ---- 10. log_user_action called for each updated row ----

    @patch(_PATCH_LOG_ACTION)
    @patch(_PATCH_RESOLVE_EMAIL, side_effect=lambda c, uid: uid)
    @patch(_PATCH_PARSE_ROWS)
    @patch(_PATCH_GET_MANIFEST)
    @patch(_PATCH_LOCK)
    @patch(_PATCH_GET_CLIENT)
    def test_log_user_action_called_per_row(
        self, mock_get_client, mock_lock_cls, mock_manifest_fn,
        mock_parse, mock_resolve, mock_log_action,
    ):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_lock_cls.return_value = _FakeLock(success=True)

        manifest = MagicMock()
        manifest.get_column_name.side_effect = lambda s, c: c
        mock_manifest_fn.return_value = manifest

        rows = [
            _parsed_row(
                row_id=5001, consumption_id="SUBM-123", status="Submitted",
                col_cons_id="CONSUMPTION_ID", col_status="STATUS", col_remarks="REMARKS",
            ),
            _parsed_row(
                row_id=5002, consumption_id="SUBM-123", status="Submitted",
                col_cons_id="CONSUMPTION_ID", col_status="STATUS", col_remarks="REMARKS",
            ),
        ]
        mock_parse.return_value = rows

        req = _make_request(body=_make_valid_body(decision="APPROVE", notes="ok"))
        resp = main(req)

        assert resp.status_code == 200
        # Two rows => two update_row calls and two log_user_action calls
        assert mock_client.update_row.call_count == 2
        assert mock_log_action.call_count == 2

        # Verify log_user_action receives the approver email as user_id
        for c in mock_log_action.call_args_list:
            assert c.kwargs.get("user_id") == "supervisor@company.com"
            assert c.kwargs.get("action_type").value == "LPO_UPDATED"
            assert c.kwargs.get("target_table") == "CONSUMPTION_LOG"
            assert c.kwargs.get("new_value") == "Approved"

    # ---- 11. resolve_user_email called for approver ----

    @patch(_PATCH_LOG_ACTION)
    @patch(_PATCH_RESOLVE_EMAIL)
    @patch(_PATCH_PARSE_ROWS)
    @patch(_PATCH_GET_MANIFEST)
    @patch(_PATCH_LOCK)
    @patch(_PATCH_GET_CLIENT)
    def test_resolve_user_email_called(
        self, mock_get_client, mock_lock_cls, mock_manifest_fn,
        mock_parse, mock_resolve, mock_log_action,
    ):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_lock_cls.return_value = _FakeLock(success=True)
        mock_resolve.return_value = "resolved@company.com"

        manifest = MagicMock()
        manifest.get_column_name.side_effect = lambda s, c: c
        mock_manifest_fn.return_value = manifest

        mock_parse.return_value = [
            _parsed_row(
                col_cons_id="CONSUMPTION_ID", col_status="STATUS", col_remarks="REMARKS",
            ),
        ]

        req = _make_request(body=_make_valid_body(approver="12345"))
        resp = main(req)

        assert resp.status_code == 200
        mock_resolve.assert_called_once_with(mock_client, "12345")

        # The resolved email should be used in update_row remarks
        updates = mock_client.update_row.call_args[0][2]
        assert "resolved@company.com" in updates.get("REMARKS", "")

    # ---- 12. Unhandled exception creates exception record ----

    @patch(_PATCH_CREATE_EXCEPTION)
    @patch(_PATCH_RESOLVE_EMAIL, side_effect=lambda c, uid: uid)
    @patch(_PATCH_GET_MANIFEST)
    @patch(_PATCH_LOCK)
    @patch(_PATCH_GET_CLIENT)
    def test_unhandled_error_returns_500_and_creates_exception(
        self, mock_get_client, mock_lock_cls, mock_manifest_fn,
        mock_resolve, mock_create_exc,
    ):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_lock_cls.return_value = _FakeLock(success=True)

        manifest = MagicMock()
        manifest.get_column_name.side_effect = RuntimeError("boom")
        mock_manifest_fn.return_value = manifest

        req = _make_request(body=_make_valid_body())
        resp = main(req)

        assert resp.status_code == 500
        body = json.loads(resp.get_body())
        assert body["error"]["code"] == "SERVER_ERROR"
        assert "trace_id" in body

        mock_create_exc.assert_called_once()
        kwargs = mock_create_exc.call_args.kwargs
        assert kwargs["reason_code"].value == "SYSTEM_ERROR"
        assert kwargs["severity"].value == "CRITICAL"

    # ---- 13. trace_id from request body is used ----

    @patch(_PATCH_LOG_ACTION)
    @patch(_PATCH_RESOLVE_EMAIL, side_effect=lambda c, uid: uid)
    @patch(_PATCH_PARSE_ROWS)
    @patch(_PATCH_GET_MANIFEST)
    @patch(_PATCH_LOCK)
    @patch(_PATCH_GET_CLIENT)
    def test_trace_id_from_request_is_used(
        self, mock_get_client, mock_lock_cls, mock_manifest_fn,
        mock_parse, mock_resolve, mock_log_action,
    ):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_lock_cls.return_value = _FakeLock(success=True)

        manifest = MagicMock()
        manifest.get_column_name.side_effect = lambda s, c: c
        mock_manifest_fn.return_value = manifest

        mock_parse.return_value = [
            _parsed_row(
                col_cons_id="CONSUMPTION_ID", col_status="STATUS", col_remarks="REMARKS",
            ),
        ]

        req = _make_request(body=_make_valid_body(trace_id="MY-TRACE-42"))
        resp = main(req)

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["trace_id"] == "MY-TRACE-42"

    # ---- 14. Submission matched via remarks field ----

    @patch(_PATCH_LOG_ACTION)
    @patch(_PATCH_RESOLVE_EMAIL, side_effect=lambda c, uid: uid)
    @patch(_PATCH_PARSE_ROWS)
    @patch(_PATCH_GET_MANIFEST)
    @patch(_PATCH_LOCK)
    @patch(_PATCH_GET_CLIENT)
    def test_submission_found_via_remarks_field(
        self, mock_get_client, mock_lock_cls, mock_manifest_fn,
        mock_parse, mock_resolve, mock_log_action,
    ):
        """Rows can also be matched when the submission_id appears in REMARKS."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_lock_cls.return_value = _FakeLock(success=True)

        manifest = MagicMock()
        manifest.get_column_name.side_effect = lambda s, c: c
        mock_manifest_fn.return_value = manifest

        # Row does NOT match on CONSUMPTION_ID but does match on REMARKS
        row = {
            "id": 6001,
            "row_id": 6001,
            "CONSUMPTION_ID": "CONS-OTHER",
            "STATUS": "Submitted",
            "REMARKS": "Batch containing SUBM-123 processed on 2026-01-15",
        }
        mock_parse.return_value = [row]

        req = _make_request(body=_make_valid_body(submission_id="SUBM-123"))
        resp = main(req)

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["status"] == "OK"

    # ---- 15. Multiple rows updated together ----

    @patch(_PATCH_LOG_ACTION)
    @patch(_PATCH_RESOLVE_EMAIL, side_effect=lambda c, uid: uid)
    @patch(_PATCH_PARSE_ROWS)
    @patch(_PATCH_GET_MANIFEST)
    @patch(_PATCH_LOCK)
    @patch(_PATCH_GET_CLIENT)
    def test_multiple_rows_all_updated(
        self, mock_get_client, mock_lock_cls, mock_manifest_fn,
        mock_parse, mock_resolve, mock_log_action,
    ):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_lock_cls.return_value = _FakeLock(success=True)

        manifest = MagicMock()
        manifest.get_column_name.side_effect = lambda s, c: c
        mock_manifest_fn.return_value = manifest

        rows = [
            _parsed_row(
                row_id=7001, consumption_id="SUBM-100", status="Submitted",
                col_cons_id="CONSUMPTION_ID", col_status="STATUS", col_remarks="REMARKS",
            ),
            _parsed_row(
                row_id=7002, consumption_id="SUBM-100", status="Submitted",
                col_cons_id="CONSUMPTION_ID", col_status="STATUS", col_remarks="REMARKS",
            ),
            _parsed_row(
                row_id=7003, consumption_id="SUBM-100", status="Submitted",
                col_cons_id="CONSUMPTION_ID", col_status="STATUS", col_remarks="REMARKS",
            ),
        ]
        mock_parse.return_value = rows

        req = _make_request(body=_make_valid_body(
            submission_id="SUBM-100", decision="REJECT", notes="Fix quantities",
        ))
        resp = main(req)

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["new_status"] == "Adjustment Requested"

        # All three rows should be updated
        assert mock_client.update_row.call_count == 3
        updated_row_ids = [c[0][1] for c in mock_client.update_row.call_args_list]
        assert updated_row_ids == [7001, 7002, 7003]
