"""
Unit Tests for fn_submit_stock
================================

Tests the stock submission Azure Function endpoint:
- Happy path (valid StockSubmission -> 200)
- Invalid JSON -> 500
- Invalid Pydantic model -> 500
- trace_id propagation from submission
- Exception record creation on error
"""

import pytest
import json
from unittest.mock import patch, MagicMock
import azure.functions as func

from shared.flow_models import SubmissionResult, StockSubmission, StockLine


# --------------- helpers ---------------

def _make_request(body=None, *, raw_body: bytes = None):
    """Build an azure.functions.HttpRequest for POST /api/submission/stock."""
    if raw_body is not None:
        return func.HttpRequest(
            method="POST",
            url="/api/submission/stock",
            body=raw_body,
            headers={"Content-Type": "application/json"},
        )
    return func.HttpRequest(
        method="POST",
        url="/api/submission/stock",
        body=json.dumps(body).encode() if body is not None else b"{}",
        headers={"Content-Type": "application/json"},
    )


def _valid_stock_body(trace_id=None):
    """Return a minimal valid StockSubmission dict."""
    body = {
        "user": "counter@factory.com",
        "plant": "PLANT-A",
        "shift": "Morning",
        "snapshot_date": "2026-03-24",
        "lines": [
            {
                "canonical_code": "MAT-001",
                "counted_qty": 150.0,
                "uom": "SQM",
            }
        ],
    }
    if trace_id:
        body["trace_id"] = trace_id
    return body


# --------------- tests ---------------

@pytest.mark.unit
class TestSubmitStock:
    """Tests for fn_submit_stock.main"""

    # ---- happy path ----

    @patch("fn_submit_stock.get_smartsheet_client")
    def test_happy_path_returns_200(self, mock_get_client):
        """Valid StockSubmission yields 200 with empty warnings/errors."""
        from fn_submit_stock import main

        mock_get_client.return_value = MagicMock()

        req = _make_request(_valid_stock_body())
        resp = main(req)

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["errors"] == []
        assert body["warnings"] == []
        assert "trace_id" in body

    @patch("fn_submit_stock.get_smartsheet_client")
    def test_happy_path_calls_get_client(self, mock_get_client):
        """The function must obtain a Smartsheet client."""
        from fn_submit_stock import main

        mock_get_client.return_value = MagicMock()

        req = _make_request(_valid_stock_body())
        main(req)

        mock_get_client.assert_called_once()

    # ---- trace_id propagation ----

    @patch("fn_submit_stock.get_smartsheet_client")
    def test_trace_id_from_submission(self, mock_get_client):
        """When the submission supplies a trace_id, it must appear in the response."""
        from fn_submit_stock import main

        mock_get_client.return_value = MagicMock()

        custom_trace = "stock-trace-ABC"
        req = _make_request(_valid_stock_body(trace_id=custom_trace))
        resp = main(req)

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["trace_id"] == custom_trace

    @patch("fn_submit_stock.get_smartsheet_client")
    def test_generated_trace_id_when_none_provided(self, mock_get_client):
        """When no trace_id in submission, a generated one must still be present."""
        from fn_submit_stock import main

        mock_get_client.return_value = MagicMock()

        req = _make_request(_valid_stock_body(trace_id=None))
        resp = main(req)

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert isinstance(body["trace_id"], str)
        assert len(body["trace_id"]) > 0

    # ---- invalid JSON ----

    @patch("fn_submit_stock.get_smartsheet_client")
    def test_invalid_json_returns_500(self, mock_get_client):
        """Non-JSON body triggers the outer except and returns 500."""
        from fn_submit_stock import main

        req = _make_request(raw_body=b"{{not json")

        with patch("shared.audit.create_exception"):
            resp = main(req)

        assert resp.status_code == 500
        body = json.loads(resp.get_body())
        assert body["error"]["code"] == "SERVER_ERROR"

    # ---- invalid Pydantic model ----

    @patch("fn_submit_stock.get_smartsheet_client")
    def test_invalid_model_returns_500(self, mock_get_client):
        """Missing required fields causes Pydantic ValidationError -> outer except -> 500."""
        from fn_submit_stock import main

        # Missing 'lines', 'shift', 'snapshot_date'
        req = _make_request({"user": "x@y.com", "plant": "P"})

        with patch("shared.audit.create_exception"):
            resp = main(req)

        assert resp.status_code == 500
        body = json.loads(resp.get_body())
        assert body["error"]["code"] == "SERVER_ERROR"

    @patch("fn_submit_stock.get_smartsheet_client")
    def test_empty_lines_returns_500(self, mock_get_client):
        """Empty lines list fails Pydantic min_items and triggers 500."""
        from fn_submit_stock import main

        body = {
            "user": "counter@factory.com",
            "plant": "PLANT-A",
            "shift": "Morning",
            "snapshot_date": "2026-03-24",
            "lines": [],
        }
        req = _make_request(body)

        with patch("shared.audit.create_exception"):
            resp = main(req)

        assert resp.status_code == 500

    # ---- exception record creation on error ----

    @patch("fn_submit_stock.SubmissionResult")
    @patch("fn_submit_stock.get_smartsheet_client")
    def test_error_creates_exception_record(self, mock_get_client, mock_result_cls):
        """On unhandled error after client is obtained, create_exception is called with SYSTEM_ERROR + CRITICAL."""
        from fn_submit_stock import main

        mock_get_client.return_value = MagicMock()
        # Force an error *after* client is assigned by making SubmissionResult raise
        mock_result_cls.side_effect = RuntimeError("result construction failed")

        req = _make_request(_valid_stock_body())

        with patch("shared.audit.create_exception") as mock_create_exc:
            resp = main(req)

        assert resp.status_code == 500
        mock_create_exc.assert_called_once()
        call_kwargs = mock_create_exc.call_args[1]
        assert call_kwargs["reason_code"].value == "SYSTEM_ERROR"
        assert call_kwargs["severity"].value == "CRITICAL"
        assert "fn_submit_stock" in call_kwargs["message"]

    @patch("fn_submit_stock.get_smartsheet_client")
    def test_error_before_client_still_returns_500(self, mock_get_client):
        """Pydantic error (before client is obtained) still returns 500 even if create_exception fails."""
        from fn_submit_stock import main

        # Force a Pydantic error (missing required fields) -- client never assigned
        req = _make_request({"user": "x@y.com", "plant": "P"})
        resp = main(req)

        assert resp.status_code == 500
        body = json.loads(resp.get_body())
        assert body["error"]["code"] == "SERVER_ERROR"

    @patch("fn_submit_stock.get_smartsheet_client")
    def test_exception_record_failure_does_not_crash(self, mock_get_client):
        """If create_exception itself fails, the function still returns 500 gracefully."""
        from fn_submit_stock import main

        # Force an error by providing invalid JSON
        req = _make_request(raw_body=b"bad")

        with patch("shared.audit.create_exception", side_effect=Exception("audit broken")):
            resp = main(req)

        assert resp.status_code == 500
        body = json.loads(resp.get_body())
        assert body["error"]["code"] == "SERVER_ERROR"

    # ---- response format ----

    @patch("fn_submit_stock.get_smartsheet_client")
    def test_response_mimetype_is_json(self, mock_get_client):
        """Successful response must have application/json mimetype."""
        from fn_submit_stock import main

        mock_get_client.return_value = MagicMock()

        req = _make_request(_valid_stock_body())
        resp = main(req)

        assert resp.mimetype == "application/json"

    @patch("fn_submit_stock.get_smartsheet_client")
    def test_response_has_submission_result_shape(self, mock_get_client):
        """Response body must conform to SubmissionResult schema."""
        from fn_submit_stock import main

        mock_get_client.return_value = MagicMock()

        req = _make_request(_valid_stock_body())
        resp = main(req)

        body = json.loads(resp.get_body())
        # Validate it can be parsed back into a SubmissionResult
        result = SubmissionResult(**body)
        assert isinstance(result.trace_id, str)
        assert isinstance(result.warnings, list)
        assert isinstance(result.errors, list)

    # ---- multiple lines ----

    @patch("fn_submit_stock.get_smartsheet_client")
    def test_multiple_lines_accepted(self, mock_get_client):
        """Submission with multiple stock lines is accepted and returns 200."""
        from fn_submit_stock import main

        mock_get_client.return_value = MagicMock()

        body = {
            "user": "counter@factory.com",
            "plant": "PLANT-B",
            "shift": "Evening",
            "snapshot_date": "2026-03-24",
            "lines": [
                {"canonical_code": "MAT-001", "counted_qty": 150.0, "uom": "SQM"},
                {"canonical_code": "MAT-002", "counted_qty": 80.0, "uom": "ROL"},
                {"canonical_code": "MAT-003", "counted_qty": 0.0, "uom": "SQM"},
            ],
        }
        req = _make_request(body)
        resp = main(req)

        assert resp.status_code == 200

    # ---- negative qty rejected ----

    @patch("fn_submit_stock.get_smartsheet_client")
    def test_negative_counted_qty_returns_500(self, mock_get_client):
        """Negative counted_qty fails Pydantic ge=0 constraint, causing 500."""
        from fn_submit_stock import main

        body = {
            "user": "counter@factory.com",
            "plant": "PLANT-A",
            "shift": "Morning",
            "snapshot_date": "2026-03-24",
            "lines": [
                {"canonical_code": "MAT-001", "counted_qty": -10.0, "uom": "SQM"},
            ],
        }
        req = _make_request(body)

        with patch("shared.audit.create_exception"):
            resp = main(req)

        assert resp.status_code == 500

    # ---- source defaults to TEAMS ----

    @patch("fn_submit_stock.get_smartsheet_client")
    def test_source_defaults_to_teams(self, mock_get_client):
        """StockSubmission.source defaults to 'TEAMS' when not provided."""
        from fn_submit_stock import main

        mock_get_client.return_value = MagicMock()

        body = _valid_stock_body()
        # Do not set 'source'
        assert "source" not in body

        req = _make_request(body)
        resp = main(req)

        assert resp.status_code == 200
        # Verify by constructing the model directly
        submission = StockSubmission(**body)
        assert submission.source == "TEAMS"
