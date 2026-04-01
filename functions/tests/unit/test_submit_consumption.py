"""
Unit Tests for fn_submit_consumption
=====================================

Tests the consumption submission Azure Function endpoint:
- Invalid JSON handling
- Pydantic validation errors
- Happy path with direct ConsumptionSubmission
- Happy path with card_data (adaptive card)
- Error result mapping to HTTP 400
- Warning result mapping to HTTP 200
- trace_id propagation from submission
- Exception record creation on unhandled errors
"""

import pytest
import json
from unittest.mock import patch, MagicMock
import azure.functions as func

from shared.flow_models import (
    SubmissionResult,
    Warning,
    WarningCode,
    Error,
    ErrorCode,
    ConsumptionSubmission,
    ConsumptionSubmissionFromCard,
    ConsumptionLine,
)


# --------------- helpers ---------------

def _make_request(body=None, *, raw_body: bytes = None):
    """Build an azure.functions.HttpRequest for POST /api/submission/consumption."""
    if raw_body is not None:
        return func.HttpRequest(
            method="POST",
            url="/api/submission/consumption",
            body=raw_body,
            headers={"Content-Type": "application/json"},
        )
    return func.HttpRequest(
        method="POST",
        url="/api/submission/consumption",
        body=json.dumps(body).encode() if body is not None else b"{}",
        headers={"Content-Type": "application/json"},
    )


def _valid_submission_body(trace_id=None):
    """Return a minimal valid ConsumptionSubmission dict."""
    body = {
        "user": "operator@factory.com",
        "plant": "PLANT-A",
        "shift": "Morning",
        "allocation_ids": ["ALLOC-001"],
        "lines": [
            {
                "canonical_code": "MAT-001",
                "allocated_qty": 100.0,
                "actual_qty": 95.0,
                "uom": "SQM",
            }
        ],
    }
    if trace_id:
        body["trace_id"] = trace_id
    return body


def _valid_card_body(trace_id=None):
    """Return a minimal valid ConsumptionSubmissionFromCard dict."""
    body = {
        "user": "operator@factory.com",
        "plant": "PLANT-A",
        "shift": "Morning",
        "card_data": {
            "allocation_ids": "ALLOC-001,ALLOC-002",
            "actual_qty_MAT001": "90",
        },
    }
    if trace_id:
        body["trace_id"] = trace_id
    return body


def _success_result(trace_id="test-trace"):
    return SubmissionResult(warnings=[], errors=[], trace_id=trace_id)


def _warning_result(trace_id="test-trace"):
    return SubmissionResult(
        warnings=[Warning(code=WarningCode.VARIANCE_HIGH, message="Variance above threshold")],
        errors=[],
        trace_id=trace_id,
    )


def _error_result(trace_id="test-trace"):
    return SubmissionResult(
        warnings=[],
        errors=[Error(code=ErrorCode.ALLOCATION_NOT_FOUND, message="Unknown allocation")],
        trace_id=trace_id,
    )


# --------------- tests ---------------

@pytest.mark.unit
class TestSubmitConsumption:
    """Tests for fn_submit_consumption.main"""

    # ---- invalid JSON ----

    @patch("fn_submit_consumption.get_smartsheet_client")
    def test_invalid_json_returns_400(self, mock_get_client):
        """Non-JSON body must yield 400 INVALID_PAYLOAD."""
        from fn_submit_consumption import main

        req = _make_request(raw_body=b"not json{{{")
        resp = main(req)

        assert resp.status_code == 400
        body = json.loads(resp.get_body())
        assert body["error"]["code"] == "INVALID_PAYLOAD"
        assert "Invalid JSON" in body["error"]["message"]

    # ---- Pydantic validation error (direct submission) ----

    @patch("fn_submit_consumption.get_smartsheet_client")
    def test_invalid_model_returns_400(self, mock_get_client):
        """Missing required fields must yield 400 INVALID_PAYLOAD."""
        from fn_submit_consumption import main

        mock_get_client.return_value = MagicMock()

        # Missing 'lines', 'allocation_ids', 'shift'
        req = _make_request({"user": "x@y.com", "plant": "P"})
        resp = main(req)

        assert resp.status_code == 400
        body = json.loads(resp.get_body())
        assert body["error"]["code"] == "INVALID_PAYLOAD"

    # ---- happy path (direct submission) ----

    @patch("fn_submit_consumption.submit_consumption")
    @patch("fn_submit_consumption.get_smartsheet_client")
    def test_happy_path_direct_submission(self, mock_get_client, mock_submit):
        """Valid ConsumptionSubmission body yields 200 with result JSON."""
        from fn_submit_consumption import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_submit.return_value = _success_result()

        req = _make_request(_valid_submission_body())
        resp = main(req)

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["trace_id"] == "test-trace"
        assert body["errors"] == []
        assert body["warnings"] == []

        # submit_consumption must have been called once
        mock_submit.assert_called_once()
        call_args = mock_submit.call_args
        assert call_args[0][0] is mock_client  # first positional arg = client
        assert isinstance(call_args[0][1], ConsumptionSubmission)

    # ---- happy path (card_data) ----

    @patch("fn_submit_consumption.submit_consumption")
    @patch("fn_submit_consumption.get_smartsheet_client")
    def test_happy_path_card_data(self, mock_get_client, mock_submit):
        """Body with 'card_data' key triggers card-parsing path and yields 200."""
        from fn_submit_consumption import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Build a fully valid ConsumptionSubmission to be returned by parse_card_data_to_submission
        parsed_submission = ConsumptionSubmission(
            user="operator@factory.com",
            plant="PLANT-A",
            shift="Morning",
            allocation_ids=["ALLOC-001"],
            lines=[
                ConsumptionLine(
                    canonical_code="MAT-001",
                    allocated_qty=100.0,
                    actual_qty=90.0,
                    uom="SQM",
                )
            ],
        )
        mock_submit.return_value = _success_result()

        # These are lazily imported inside the if-block, so patch at their source modules
        with patch(
            "shared.consumption_service.parse_card_data_to_submission",
            return_value=parsed_submission,
        ) as mock_parse, patch(
            "shared.flow_models.ConsumptionSubmissionFromCard",
        ) as mock_from_card_cls:
            mock_from_card_cls.return_value = MagicMock(trace_id=None)
            parsed_submission.trace_id = None

            req = _make_request(_valid_card_body())
            resp = main(req)

        assert resp.status_code == 200
        mock_parse.assert_called_once()
        mock_submit.assert_called_once()

    # ---- card_data validation error ----

    @patch("fn_submit_consumption.get_smartsheet_client")
    def test_card_data_validation_error_returns_400(self, mock_get_client):
        """If ConsumptionSubmissionFromCard validation fails, return 400."""
        from fn_submit_consumption import main

        mock_get_client.return_value = MagicMock()

        # 'card_data' present but missing required 'plant' and 'shift'
        req = _make_request({"user": "x@y.com", "card_data": {"a": 1}})
        resp = main(req)

        assert resp.status_code == 400
        body = json.loads(resp.get_body())
        assert body["error"]["code"] == "INVALID_PAYLOAD"

    # ---- result with errors -> 400 ----

    @patch("fn_submit_consumption.submit_consumption")
    @patch("fn_submit_consumption.get_smartsheet_client")
    def test_result_with_errors_returns_400(self, mock_get_client, mock_submit):
        """When SubmissionResult contains errors, HTTP status must be 400."""
        from fn_submit_consumption import main

        mock_get_client.return_value = MagicMock()
        mock_submit.return_value = _error_result()

        req = _make_request(_valid_submission_body())
        resp = main(req)

        assert resp.status_code == 400
        body = json.loads(resp.get_body())
        assert len(body["errors"]) == 1
        assert body["errors"][0]["code"] == "ALLOCATION_NOT_FOUND"

    # ---- result with warnings only -> 200 ----

    @patch("fn_submit_consumption.submit_consumption")
    @patch("fn_submit_consumption.get_smartsheet_client")
    def test_result_with_warnings_returns_200(self, mock_get_client, mock_submit):
        """Warnings without errors must still yield HTTP 200."""
        from fn_submit_consumption import main

        mock_get_client.return_value = MagicMock()
        mock_submit.return_value = _warning_result()

        req = _make_request(_valid_submission_body())
        resp = main(req)

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert len(body["warnings"]) == 1
        assert body["warnings"][0]["code"] == "VARIANCE_HIGH"
        assert body["errors"] == []

    # ---- trace_id propagation ----

    @patch("fn_submit_consumption.submit_consumption")
    @patch("fn_submit_consumption.get_smartsheet_client")
    def test_trace_id_from_submission_is_used(self, mock_get_client, mock_submit):
        """When submission provides trace_id, it must be forwarded to submit_consumption."""
        from fn_submit_consumption import main

        mock_get_client.return_value = MagicMock()
        expected_trace = "my-custom-trace-123"
        mock_submit.return_value = _success_result(trace_id=expected_trace)

        req = _make_request(_valid_submission_body(trace_id=expected_trace))
        resp = main(req)

        assert resp.status_code == 200
        # Verify the trace_id passed to submit_consumption
        call_args = mock_submit.call_args
        assert call_args[0][2] == expected_trace  # third positional arg = trace_id

    @patch("fn_submit_consumption.submit_consumption")
    @patch("fn_submit_consumption.get_smartsheet_client")
    def test_generated_trace_id_when_none_provided(self, mock_get_client, mock_submit):
        """When no trace_id in submission, a generated one is used."""
        from fn_submit_consumption import main

        mock_get_client.return_value = MagicMock()
        mock_submit.return_value = _success_result()

        req = _make_request(_valid_submission_body(trace_id=None))
        resp = main(req)

        assert resp.status_code == 200
        # submit_consumption should still have been called with some trace_id string
        call_args = mock_submit.call_args
        trace_used = call_args[0][2]
        assert isinstance(trace_used, str)
        assert len(trace_used) > 0

    # ---- unhandled error -> 500 + exception record ----

    @patch("fn_submit_consumption.submit_consumption")
    @patch("fn_submit_consumption.get_smartsheet_client")
    def test_unhandled_error_returns_500(self, mock_get_client, mock_submit):
        """Unexpected exception in submit_consumption must yield 500 SERVER_ERROR."""
        from fn_submit_consumption import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_submit.side_effect = RuntimeError("database on fire")

        with patch("shared.audit.create_exception") as mock_create_exc:
            req = _make_request(_valid_submission_body())
            resp = main(req)

        assert resp.status_code == 500
        body = json.loads(resp.get_body())
        assert body["error"]["code"] == "SERVER_ERROR"
        assert "database on fire" in body["error"]["message"]

    @patch("fn_submit_consumption.submit_consumption")
    @patch("fn_submit_consumption.get_smartsheet_client")
    def test_unhandled_error_creates_exception_record(self, mock_get_client, mock_submit):
        """On unhandled error, create_exception must be called with SYSTEM_ERROR."""
        from fn_submit_consumption import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_submit.side_effect = RuntimeError("boom")

        with patch("shared.audit.create_exception") as mock_create_exc:
            req = _make_request(_valid_submission_body())
            resp = main(req)

        assert resp.status_code == 500
        mock_create_exc.assert_called_once()
        call_kwargs = mock_create_exc.call_args[1]
        assert call_kwargs["reason_code"].value == "SYSTEM_ERROR"
        assert call_kwargs["severity"].value == "CRITICAL"
        assert "fn_submit_consumption" in call_kwargs["message"]

    @patch("fn_submit_consumption.submit_consumption")
    @patch("fn_submit_consumption.get_smartsheet_client")
    def test_exception_record_failure_does_not_crash(self, mock_get_client, mock_submit):
        """If create_exception itself raises, the function must still return 500 gracefully."""
        from fn_submit_consumption import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_submit.side_effect = RuntimeError("primary failure")

        with patch("shared.audit.create_exception", side_effect=Exception("audit down")):
            req = _make_request(_valid_submission_body())
            resp = main(req)

        assert resp.status_code == 500
        body = json.loads(resp.get_body())
        assert body["error"]["code"] == "SERVER_ERROR"

    # ---- response content-type ----

    @patch("fn_submit_consumption.submit_consumption")
    @patch("fn_submit_consumption.get_smartsheet_client")
    def test_response_mimetype_is_json(self, mock_get_client, mock_submit):
        """All responses must have application/json mimetype."""
        from fn_submit_consumption import main

        mock_get_client.return_value = MagicMock()
        mock_submit.return_value = _success_result()

        req = _make_request(_valid_submission_body())
        resp = main(req)

        assert resp.mimetype == "application/json"

    # ---- processed_submission_id propagation ----

    @patch("fn_submit_consumption.submit_consumption")
    @patch("fn_submit_consumption.get_smartsheet_client")
    def test_processed_submission_id_in_response(self, mock_get_client, mock_submit):
        """If submit_consumption returns a processed_submission_id, it appears in the response."""
        from fn_submit_consumption import main

        mock_get_client.return_value = MagicMock()
        result = SubmissionResult(
            warnings=[],
            errors=[],
            trace_id="t-1",
            processed_submission_id="SUB-999",
        )
        mock_submit.return_value = result

        req = _make_request(_valid_submission_body())
        resp = main(req)

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["processed_submission_id"] == "SUB-999"

    # ---- multiple lines in submission ----

    @patch("fn_submit_consumption.submit_consumption")
    @patch("fn_submit_consumption.get_smartsheet_client")
    def test_multiple_lines_submission(self, mock_get_client, mock_submit):
        """Submission with multiple consumption lines is parsed and forwarded correctly."""
        from fn_submit_consumption import main

        mock_get_client.return_value = MagicMock()
        mock_submit.return_value = _success_result()

        body = {
            "user": "op@factory.com",
            "plant": "PLANT-B",
            "shift": "Evening",
            "allocation_ids": ["ALLOC-001", "ALLOC-002"],
            "lines": [
                {"canonical_code": "MAT-001", "allocated_qty": 100.0, "actual_qty": 95.0, "uom": "SQM"},
                {"canonical_code": "MAT-002", "allocated_qty": 200.0, "actual_qty": 200.0, "uom": "ROL"},
            ],
        }
        req = _make_request(body)
        resp = main(req)

        assert resp.status_code == 200
        submission_arg = mock_submit.call_args[0][1]
        assert len(submission_arg.lines) == 2
        assert submission_arg.lines[0].canonical_code == "MAT-001"
        assert submission_arg.lines[1].canonical_code == "MAT-002"
        assert len(submission_arg.allocation_ids) == 2

    # ---- empty lines list fails validation ----

    @patch("fn_submit_consumption.get_smartsheet_client")
    def test_empty_lines_returns_400(self, mock_get_client):
        """Submission with empty lines list must fail Pydantic validation."""
        from fn_submit_consumption import main

        mock_get_client.return_value = MagicMock()

        body = {
            "user": "op@factory.com",
            "plant": "PLANT-A",
            "shift": "Morning",
            "allocation_ids": ["ALLOC-001"],
            "lines": [],
        }
        req = _make_request(body)
        resp = main(req)

        assert resp.status_code == 400
        result = json.loads(resp.get_body())
        assert result["error"]["code"] == "INVALID_PAYLOAD"

    # ---- empty allocation_ids fails validation ----

    @patch("fn_submit_consumption.get_smartsheet_client")
    def test_empty_allocation_ids_returns_400(self, mock_get_client):
        """Submission with empty allocation_ids list must fail Pydantic validation."""
        from fn_submit_consumption import main

        mock_get_client.return_value = MagicMock()

        body = {
            "user": "op@factory.com",
            "plant": "PLANT-A",
            "shift": "Morning",
            "allocation_ids": [],
            "lines": [
                {"canonical_code": "MAT-001", "allocated_qty": 50.0, "actual_qty": 50.0, "uom": "SQM"},
            ],
        }
        req = _make_request(body)
        resp = main(req)

        assert resp.status_code == 400
        result = json.loads(resp.get_body())
        assert result["error"]["code"] == "INVALID_PAYLOAD"
