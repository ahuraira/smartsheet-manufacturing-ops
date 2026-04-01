"""
Unit Tests for fn_create_exception_api
=======================================

Tests the POST /api/exception endpoint which creates exception records
programmatically via the ExceptionCreateRequest model.

Covers:
- Happy path: valid request -> 201 with exception_id and trace_id
- Trace ID passthrough: request-supplied trace_id is used in response
- Auto-generated trace ID when none provided
- Invalid JSON body -> 500
- Invalid/missing Pydantic fields -> 500
- create_exception called with correct keyword arguments
- Smartsheet client failure -> 500
"""

import pytest
import json
from unittest.mock import patch, MagicMock
import azure.functions as func

from shared.models import ExceptionSeverity
from shared.flow_models import ExceptionCreateRequest, ExceptionCreateResponse


def _make_request(body: dict) -> func.HttpRequest:
    """Build a mock Azure Functions HTTP POST request."""
    return func.HttpRequest(
        method="POST",
        url="/api/exception",
        body=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )


def _make_invalid_json_request() -> func.HttpRequest:
    """Build a request whose body is not valid JSON."""
    return func.HttpRequest(
        method="POST",
        url="/api/exception",
        body=b"NOT-JSON{{",
        headers={"Content-Type": "application/json"},
    )


def _valid_body(**overrides) -> dict:
    """Return a minimal valid ExceptionCreateRequest body."""
    base = {
        "type": "VARIANCE",
        "reference": "ALLOC-0001",
        "severity": "HIGH",
        "note": "Variance exceeded threshold",
        "created_by": "user@company.com",
    }
    base.update(overrides)
    return base


@pytest.mark.unit
class TestCreateExceptionApi:
    """Tests for fn_create_exception_api.main."""

    # ------------------------------------------------------------------ #
    # Happy path
    # ------------------------------------------------------------------ #

    @patch("fn_create_exception_api.create_exception")
    @patch("fn_create_exception_api.get_smartsheet_client")
    @patch("fn_create_exception_api.generate_trace_id", return_value="TRACE-001")
    def test_happy_path_returns_201(self, mock_trace, mock_get_client, mock_create_exc):
        """Valid request returns 201 with exception_id and trace_id."""
        from fn_create_exception_api import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_create_exc.return_value = "EX-0001"

        req = _make_request(_valid_body())
        resp = main(req)

        assert resp.status_code == 201
        data = json.loads(resp.get_body())
        assert data["exception_id"] == "EX-0001"
        assert data["trace_id"] == "TRACE-001"

    @patch("fn_create_exception_api.create_exception")
    @patch("fn_create_exception_api.get_smartsheet_client")
    @patch("fn_create_exception_api.generate_trace_id", return_value="TRACE-001")
    def test_create_exception_called_with_correct_params(
        self, mock_trace, mock_get_client, mock_create_exc
    ):
        """create_exception is invoked with the values from the request model."""
        from fn_create_exception_api import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_create_exc.return_value = "EX-0002"

        body = _valid_body(
            type="SHORTAGE",
            reference="SUB-999",
            severity="CRITICAL",
            note="Stock below minimum",
        )
        req = _make_request(body)
        main(req)

        mock_create_exc.assert_called_once_with(
            client=mock_client,
            exception_type="SHORTAGE",
            reference_id="SUB-999",
            severity="CRITICAL",
            message="Stock below minimum",
            trace_id="TRACE-001",
        )

    @patch("fn_create_exception_api.create_exception")
    @patch("fn_create_exception_api.get_smartsheet_client")
    @patch("fn_create_exception_api.generate_trace_id", return_value="TRACE-001")
    def test_note_defaults_to_empty_string(self, mock_trace, mock_get_client, mock_create_exc):
        """When note is None, empty string is passed as message."""
        from fn_create_exception_api import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_create_exc.return_value = "EX-0003"

        body = _valid_body()
        del body["note"]  # omit note entirely
        req = _make_request(body)
        main(req)

        _, kwargs = mock_create_exc.call_args
        assert kwargs["message"] == ""

    # ------------------------------------------------------------------ #
    # Trace ID handling
    # ------------------------------------------------------------------ #

    @patch("fn_create_exception_api.create_exception")
    @patch("fn_create_exception_api.get_smartsheet_client")
    @patch("fn_create_exception_api.generate_trace_id", return_value="AUTO-TRACE")
    def test_uses_request_trace_id_when_provided(
        self, mock_trace, mock_get_client, mock_create_exc
    ):
        """If the request body includes trace_id, it overrides the generated one."""
        from fn_create_exception_api import main

        mock_get_client.return_value = MagicMock()
        mock_create_exc.return_value = "EX-0004"

        body = _valid_body(trace_id="MY-TRACE-123")
        req = _make_request(body)
        resp = main(req)

        data = json.loads(resp.get_body())
        assert data["trace_id"] == "MY-TRACE-123"
        # create_exception should also receive the overridden trace_id
        _, kwargs = mock_create_exc.call_args
        assert kwargs["trace_id"] == "MY-TRACE-123"

    @patch("fn_create_exception_api.create_exception")
    @patch("fn_create_exception_api.get_smartsheet_client")
    @patch("fn_create_exception_api.generate_trace_id", return_value="AUTO-TRACE")
    def test_uses_generated_trace_id_when_not_provided(
        self, mock_trace, mock_get_client, mock_create_exc
    ):
        """Without a request-level trace_id the auto-generated one is used."""
        from fn_create_exception_api import main

        mock_get_client.return_value = MagicMock()
        mock_create_exc.return_value = "EX-0005"

        body = _valid_body()  # no trace_id key
        req = _make_request(body)
        resp = main(req)

        data = json.loads(resp.get_body())
        assert data["trace_id"] == "AUTO-TRACE"

    # ------------------------------------------------------------------ #
    # Severity enum values
    # ------------------------------------------------------------------ #

    @patch("fn_create_exception_api.create_exception")
    @patch("fn_create_exception_api.get_smartsheet_client")
    @patch("fn_create_exception_api.generate_trace_id", return_value="T")
    def test_all_severity_levels_accepted(self, mock_trace, mock_get_client, mock_create_exc):
        """Every ExceptionSeverity value is accepted and passed through."""
        from fn_create_exception_api import main

        mock_get_client.return_value = MagicMock()
        mock_create_exc.return_value = "EX-SEV"

        for sev in ExceptionSeverity:
            body = _valid_body(severity=sev.value)
            resp = main(_make_request(body))
            assert resp.status_code == 201, f"Severity {sev.value} should be accepted"

    # ------------------------------------------------------------------ #
    # Error handling
    # ------------------------------------------------------------------ #

    @patch("fn_create_exception_api.generate_trace_id", return_value="TRACE-ERR")
    def test_invalid_json_returns_500(self, mock_trace):
        """Non-JSON body triggers the outer except and returns 500."""
        from fn_create_exception_api import main

        req = _make_invalid_json_request()
        resp = main(req)

        assert resp.status_code == 500
        data = json.loads(resp.get_body())
        assert data["error"]["code"] == "SERVER_ERROR"
        assert data["trace_id"] == "TRACE-ERR"

    @patch("fn_create_exception_api.generate_trace_id", return_value="TRACE-VAL")
    def test_missing_required_field_returns_500(self, mock_trace):
        """Missing a required Pydantic field falls through to 500."""
        from fn_create_exception_api import main

        # Missing 'type' and 'reference' and 'created_by'
        req = _make_request({"severity": "LOW"})
        resp = main(req)

        assert resp.status_code == 500
        data = json.loads(resp.get_body())
        assert data["error"]["code"] == "SERVER_ERROR"

    @patch("fn_create_exception_api.generate_trace_id", return_value="TRACE-VAL2")
    def test_invalid_severity_returns_500(self, mock_trace):
        """An unrecognised severity string fails Pydantic validation -> 500."""
        from fn_create_exception_api import main

        body = _valid_body(severity="NOT_A_LEVEL")
        req = _make_request(body)
        resp = main(req)

        assert resp.status_code == 500
        data = json.loads(resp.get_body())
        assert data["error"]["code"] == "SERVER_ERROR"

    @patch("fn_create_exception_api.create_exception")
    @patch("fn_create_exception_api.get_smartsheet_client")
    @patch("fn_create_exception_api.generate_trace_id", return_value="TRACE-FAIL")
    def test_create_exception_raises_returns_500(
        self, mock_trace, mock_get_client, mock_create_exc
    ):
        """If create_exception raises, the function returns 500."""
        from fn_create_exception_api import main

        mock_get_client.return_value = MagicMock()
        mock_create_exc.side_effect = RuntimeError("Smartsheet API failure")

        req = _make_request(_valid_body())
        resp = main(req)

        assert resp.status_code == 500
        data = json.loads(resp.get_body())
        assert "Smartsheet API failure" in data["error"]["message"]

    @patch("fn_create_exception_api.get_smartsheet_client")
    @patch("fn_create_exception_api.generate_trace_id", return_value="TRACE-CLI")
    def test_client_init_failure_returns_500(self, mock_trace, mock_get_client):
        """If get_smartsheet_client raises, the function returns 500."""
        from fn_create_exception_api import main

        mock_get_client.side_effect = RuntimeError("Missing API key")

        req = _make_request(_valid_body())
        resp = main(req)

        assert resp.status_code == 500
        data = json.loads(resp.get_body())
        assert "Missing API key" in data["error"]["message"]

    # ------------------------------------------------------------------ #
    # Response format
    # ------------------------------------------------------------------ #

    @patch("fn_create_exception_api.create_exception")
    @patch("fn_create_exception_api.get_smartsheet_client")
    @patch("fn_create_exception_api.generate_trace_id", return_value="TRACE-FMT")
    def test_success_response_mimetype_is_json(self, mock_trace, mock_get_client, mock_create_exc):
        """Successful response has application/json mimetype."""
        from fn_create_exception_api import main

        mock_get_client.return_value = MagicMock()
        mock_create_exc.return_value = "EX-FMT"

        resp = main(_make_request(_valid_body()))
        assert resp.mimetype == "application/json"

    @patch("fn_create_exception_api.generate_trace_id", return_value="TRACE-FMT2")
    def test_error_response_mimetype_is_json(self, mock_trace):
        """Error response has application/json mimetype."""
        from fn_create_exception_api import main

        resp = main(_make_invalid_json_request())
        assert resp.mimetype == "application/json"

    # ------------------------------------------------------------------ #
    # Empty body
    # ------------------------------------------------------------------ #

    @patch("fn_create_exception_api.generate_trace_id", return_value="TRACE-EMPTY")
    def test_empty_body_returns_500(self, mock_trace):
        """An empty JSON object fails validation and returns 500."""
        from fn_create_exception_api import main

        req = _make_request({})
        resp = main(req)

        assert resp.status_code == 500
        data = json.loads(resp.get_body())
        assert data["error"]["code"] == "SERVER_ERROR"
