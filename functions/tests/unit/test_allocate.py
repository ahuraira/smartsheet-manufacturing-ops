"""
Unit Tests for fn_allocate and fn_allocations_aggregate
========================================================

Tests both allocation functions:
- fn_allocate: POST /api/allocations/create
- fn_allocations_aggregate: POST /api/allocations/aggregate

Coverage:
- Happy path (200, 207, 409 for fn_allocate; 200 for fn_allocations_aggregate)
- Validation errors (400)
- Invalid JSON (400)
- Missing required fields (400)
- Client init failure (500)
- Unhandled exception with create_exception (500)
- Tag ID resolution from NESTING_LOG
- Idempotency (client_request_id)
- Backward-compat allocation_ids path for fn_allocations_aggregate
"""

import pytest
import json
from unittest.mock import MagicMock, patch
from dataclasses import dataclass, field
from typing import List

import azure.functions as func


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_request(body: dict, url: str = "/api/allocations/create") -> func.HttpRequest:
    """Build an Azure Functions HttpRequest with a JSON body."""
    return func.HttpRequest(
        method="POST",
        url=url,
        body=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )


def _build_invalid_json_request(url: str = "/api/allocations/create") -> func.HttpRequest:
    """Build an HttpRequest whose body is not valid JSON."""
    return func.HttpRequest(
        method="POST",
        url=url,
        body=b"NOT-JSON{{",
        headers={"Content-Type": "application/json"},
    )


# ---------------------------------------------------------------------------
# Mock AllocationResult (mirrors shared.allocation_engine.AllocationResult)
# ---------------------------------------------------------------------------

@dataclass
class _MockAllocationResult:
    status: str = "ALLOCATED"
    allocation_ids: List[str] = field(default_factory=list)
    lines: list = field(default_factory=list)
    shortages: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    exception_ids: list = field(default_factory=list)

    def to_dict(self):
        return {
            "status": self.status,
            "allocation_ids": self.allocation_ids,
            "lines": [],
            "shortages": self.shortages,
            "warnings": self.warnings,
            "exception_ids": self.exception_ids,
        }


# ============================================================================
# fn_allocate
# ============================================================================

@pytest.mark.unit
class TestFnAllocate:
    """Tests for fn_allocate (POST /api/allocations/create)."""

    # ── 400: invalid JSON ──────────────────────────────────────────────

    def test_invalid_json_returns_400(self):
        from fn_allocate import main

        req = _build_invalid_json_request()
        resp = main(req)

        assert resp.status_code == 400
        body = json.loads(resp.get_body())
        assert "Invalid JSON" in body["error"]
        assert "trace_id" in body

    # ── 400: missing nest_session_id ───────────────────────────────────

    def test_missing_nest_session_id_returns_400(self):
        from fn_allocate import main

        req = _build_request({"tag_id": "TAG-001"})
        resp = main(req)

        assert resp.status_code == 400
        body = json.loads(resp.get_body())
        assert "nest_session_id is required" in body["error"]
        assert "trace_id" in body

    # ── 400: empty nest_session_id ─────────────────────────────────────

    def test_empty_nest_session_id_returns_400(self):
        from fn_allocate import main

        req = _build_request({"nest_session_id": "", "tag_id": "TAG-001"})
        resp = main(req)

        assert resp.status_code == 400
        body = json.loads(resp.get_body())
        assert "nest_session_id is required" in body["error"]

    # ── 500: client init failure ───────────────────────────────────────

    @patch("shared.smartsheet_client.get_smartsheet_client", side_effect=RuntimeError("no API key"))
    def test_client_init_failure_returns_500(self, _mock_client):
        """fn_allocate does a lazy import of get_smartsheet_client inside the
        try block, so we patch it at the shared module path."""
        from fn_allocate import main

        req = _build_request({
            "nest_session_id": "NEST-20260223-0001",
            "tag_id": "TAG-001",
        })

        resp = main(req)

        assert resp.status_code == 500
        body = json.loads(resp.get_body())
        assert "Service initialization failed" in body["error"]
        assert "trace_id" in body

    # ── 200: happy path — ALLOCATED ────────────────────────────────────

    @patch("shared.allocation_engine.allocate_for_session")
    @patch("shared.smartsheet_client.get_smartsheet_client")
    def test_happy_path_allocated_returns_200(
        self, mock_get_client, mock_allocate
    ):
        from fn_allocate import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        result = _MockAllocationResult(
            status="ALLOCATED",
            allocation_ids=["ALLOC-001", "ALLOC-002"],
        )
        mock_allocate.return_value = result

        req = _build_request({
            "nest_session_id": "NEST-20260223-0001",
            "tag_id": "TAG-001",
            "planned_date": "2026-02-24",
            "shift": "Morning",
            "client_request_id": "determ-id-001",
        })

        resp = main(req)

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["status"] == "ALLOCATED"
        assert body["allocation_ids"] == ["ALLOC-001", "ALLOC-002"]
        assert body["client_request_id"] == "determ-id-001"
        assert "trace_id" in body

        mock_allocate.assert_called_once()
        call_kwargs = mock_allocate.call_args
        assert call_kwargs.kwargs["nest_session_id"] == "NEST-20260223-0001"
        assert call_kwargs.kwargs["tag_id"] == "TAG-001"
        assert call_kwargs.kwargs["planned_date"] == "2026-02-24"
        assert call_kwargs.kwargs["shift"] == "Morning"
        assert call_kwargs.kwargs["client_request_id"] == "determ-id-001"

    # ── 207: PARTIAL_ALLOCATED ─────────────────────────────────────────

    @patch("shared.allocation_engine.allocate_for_session")
    @patch("shared.smartsheet_client.get_smartsheet_client")
    def test_partial_allocated_returns_207(self, mock_get_client, mock_allocate):
        from fn_allocate import main

        mock_get_client.return_value = MagicMock()

        result = _MockAllocationResult(
            status="PARTIAL_ALLOCATED",
            allocation_ids=["ALLOC-001"],
            shortages=[{"sap_code": "MAT-999", "shortfall": 10.0}],
        )
        mock_allocate.return_value = result

        req = _build_request({
            "nest_session_id": "NEST-20260223-0002",
            "tag_id": "TAG-002",
        })

        resp = main(req)

        assert resp.status_code == 207
        body = json.loads(resp.get_body())
        assert body["status"] == "PARTIAL_ALLOCATED"
        assert len(body["shortages"]) == 1

    # ── 409: SHORTAGE ──────────────────────────────────────────────────

    @patch("shared.allocation_engine.allocate_for_session")
    @patch("shared.smartsheet_client.get_smartsheet_client")
    def test_shortage_returns_409(self, mock_get_client, mock_allocate):
        from fn_allocate import main

        mock_get_client.return_value = MagicMock()

        result = _MockAllocationResult(
            status="SHORTAGE",
            allocation_ids=[],
            shortages=[
                {"sap_code": "MAT-100", "shortfall": 50.0},
                {"sap_code": "MAT-200", "shortfall": 25.0},
            ],
        )
        mock_allocate.return_value = result

        req = _build_request({
            "nest_session_id": "NEST-20260223-0003",
            "tag_id": "TAG-003",
        })

        resp = main(req)

        assert resp.status_code == 409
        body = json.loads(resp.get_body())
        assert body["status"] == "SHORTAGE"
        assert len(body["shortages"]) == 2

    # ── client_request_id: auto-generated when missing ─────────────────

    @patch("shared.allocation_engine.allocate_for_session")
    @patch("shared.smartsheet_client.get_smartsheet_client")
    def test_client_request_id_auto_generated(
        self, mock_get_client, mock_allocate
    ):
        from fn_allocate import main

        mock_get_client.return_value = MagicMock()
        mock_allocate.return_value = _MockAllocationResult(status="ALLOCATED")

        req = _build_request({
            "nest_session_id": "NEST-20260223-0004",
            "tag_id": "TAG-004",
        })

        resp = main(req)

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        # A UUID-style string should be present
        assert "client_request_id" in body
        assert len(body["client_request_id"]) > 0

    # ── shift defaults to "Morning" ────────────────────────────────────

    @patch("shared.allocation_engine.allocate_for_session")
    @patch("shared.smartsheet_client.get_smartsheet_client")
    def test_shift_defaults_to_morning(self, mock_get_client, mock_allocate):
        from fn_allocate import main

        mock_get_client.return_value = MagicMock()
        mock_allocate.return_value = _MockAllocationResult(status="ALLOCATED")

        req = _build_request({
            "nest_session_id": "NEST-20260223-0005",
            "tag_id": "TAG-005",
        })

        resp = main(req)

        assert resp.status_code == 200
        call_kwargs = mock_allocate.call_args.kwargs
        assert call_kwargs["shift"] == "Morning"

    # ── 500: unhandled error calls create_exception ────────────────────

    @patch("shared.audit.create_exception")
    @patch("shared.allocation_engine.allocate_for_session")
    @patch("shared.smartsheet_client.get_smartsheet_client")
    def test_unhandled_error_creates_exception_and_returns_500(
        self, mock_get_client, mock_allocate, mock_create_exc
    ):
        from fn_allocate import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_allocate.side_effect = RuntimeError("Something unexpected")

        req = _build_request({
            "nest_session_id": "NEST-20260223-0006",
            "tag_id": "TAG-006",
        })

        resp = main(req)

        assert resp.status_code == 500
        body = json.loads(resp.get_body())
        assert "Allocation failed" in body["error"]
        assert "trace_id" in body

        mock_create_exc.assert_called_once()
        exc_kwargs = mock_create_exc.call_args.kwargs
        assert exc_kwargs["client"] is mock_client
        assert "fn_allocate unhandled error" in exc_kwargs["message"]

    # ── 500: create_exception itself fails silently ────────────────────

    @patch("shared.audit.create_exception", side_effect=Exception("audit broken"))
    @patch("shared.allocation_engine.allocate_for_session", side_effect=ValueError("boom"))
    @patch("shared.smartsheet_client.get_smartsheet_client")
    def test_create_exception_failure_still_returns_500(
        self, mock_get_client, mock_allocate, mock_create_exc
    ):
        from fn_allocate import main

        mock_get_client.return_value = MagicMock()

        req = _build_request({
            "nest_session_id": "NEST-20260223-0007",
            "tag_id": "TAG-007",
        })

        resp = main(req)

        assert resp.status_code == 500
        body = json.loads(resp.get_body())
        assert "Allocation failed" in body["error"]

    # ── Tag ID resolution from NESTING_LOG ─────────────────────────────

    @patch("shared.allocation_engine.allocate_for_session")
    @patch("shared.manifest.get_manifest")
    @patch("shared.smartsheet_client.get_smartsheet_client")
    def test_tag_id_resolved_from_nesting_log(
        self, mock_get_client, mock_get_manifest, mock_allocate
    ):
        from fn_allocate import main

        mock_manifest = MagicMock()
        mock_manifest.get_column_name.side_effect = lambda sheet, col: {
            ("PARSED_BOM", "NEST_SESSION_ID"): "Nest Session ID",
            ("NESTING_LOG", "NEST_SESSION_ID"): "Nest Session ID",
            ("NESTING_LOG", "TAG_SHEET_ID"): "Tag Sheet ID",
        }.get((sheet, col), col)
        mock_get_manifest.return_value = mock_manifest

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Simulate get_sheet returning NESTING_LOG data with matching session
        mock_client.get_sheet.return_value = {
            "columns": [
                {"id": 9001, "title": "Nest Session ID"},
                {"id": 9002, "title": "Tag Sheet ID"},
            ],
            "rows": [
                {
                    "id": 5001,
                    "cells": [
                        {"columnId": 9001, "value": "NEST-20260223-0010"},
                        {"columnId": 9002, "value": "TAG-RESOLVED-099"},
                    ],
                }
            ],
        }

        mock_allocate.return_value = _MockAllocationResult(
            status="ALLOCATED",
            allocation_ids=["ALLOC-099"],
        )

        # No tag_id in the request body
        req = _build_request({
            "nest_session_id": "NEST-20260223-0010",
        })

        resp = main(req)

        assert resp.status_code == 200
        # Verify the resolved tag_id was passed to allocate_for_session
        call_kwargs = mock_allocate.call_args.kwargs
        assert call_kwargs["tag_id"] == "TAG-RESOLVED-099"

    # ── Tag ID resolution: no match in NESTING_LOG ─────────────────────

    @patch("shared.allocation_engine.allocate_for_session")
    @patch("shared.manifest.get_manifest")
    @patch("shared.smartsheet_client.get_smartsheet_client")
    def test_tag_id_unresolved_passes_empty_string(
        self, mock_get_client, mock_get_manifest, mock_allocate
    ):
        from fn_allocate import main

        mock_manifest = MagicMock()
        mock_manifest.get_column_name.side_effect = lambda sheet, col: col
        mock_get_manifest.return_value = mock_manifest

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # NESTING_LOG with no matching session
        mock_client.get_sheet.return_value = {
            "columns": [
                {"id": 9001, "title": "NEST_SESSION_ID"},
                {"id": 9002, "title": "TAG_SHEET_ID"},
            ],
            "rows": [
                {
                    "id": 5001,
                    "cells": [
                        {"columnId": 9001, "value": "NEST-OTHER"},
                        {"columnId": 9002, "value": "TAG-OTHER"},
                    ],
                }
            ],
        }

        mock_allocate.return_value = _MockAllocationResult(status="ALLOCATED")

        req = _build_request({"nest_session_id": "NEST-20260223-0011"})
        resp = main(req)

        assert resp.status_code == 200
        call_kwargs = mock_allocate.call_args.kwargs
        assert call_kwargs["tag_id"] == ""

    # ── Tag ID resolution: exception is swallowed ──────────────────────

    @patch("shared.allocation_engine.allocate_for_session")
    @patch("shared.smartsheet_client.get_smartsheet_client")
    def test_tag_resolution_exception_swallowed(
        self, mock_get_client, mock_allocate
    ):
        from fn_allocate import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Force tag resolution to fail by making get_sheet raise
        mock_client.get_sheet.side_effect = RuntimeError("sheet unavailable")

        mock_allocate.return_value = _MockAllocationResult(status="ALLOCATED")

        req = _build_request({
            "nest_session_id": "NEST-20260223-0012",
        })

        resp = main(req)

        # Should still succeed — tag resolution failure is non-fatal
        assert resp.status_code == 200
        call_kwargs = mock_allocate.call_args.kwargs
        assert call_kwargs["tag_id"] == ""

    # ── Response body includes to_dict() fields ────────────────────────

    @patch("shared.allocation_engine.allocate_for_session")
    @patch("shared.smartsheet_client.get_smartsheet_client")
    def test_response_body_includes_result_fields(
        self, mock_get_client, mock_allocate
    ):
        from fn_allocate import main

        mock_get_client.return_value = MagicMock()
        result = _MockAllocationResult(
            status="ALLOCATED",
            allocation_ids=["ALLOC-A", "ALLOC-B"],
            shortages=[],
            warnings=[{"msg": "low stock"}],
            exception_ids=["EX-001"],
        )
        mock_allocate.return_value = result

        req = _build_request({
            "nest_session_id": "NEST-20260223-0013",
            "tag_id": "TAG-013",
            "client_request_id": "req-013",
        })

        resp = main(req)

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["status"] == "ALLOCATED"
        assert body["allocation_ids"] == ["ALLOC-A", "ALLOC-B"]
        assert body["warnings"] == [{"msg": "low stock"}]
        assert body["exception_ids"] == ["EX-001"]
        assert body["trace_id"].startswith("alloc-")
        assert body["client_request_id"] == "req-013"


# ============================================================================
# fn_allocations_aggregate
# ============================================================================

@pytest.mark.unit
class TestFnAllocationsAggregate:
    """Tests for fn_allocations_aggregate (POST /api/allocations/aggregate)."""

    # ── 400: invalid JSON ──────────────────────────────────────────────

    def test_invalid_json_returns_400(self):
        from fn_allocations_aggregate import main

        req = _build_invalid_json_request(url="/api/allocations/aggregate")
        resp = main(req)

        assert resp.status_code == 400
        body = json.loads(resp.get_body())
        assert body["error"]["code"] == "INVALID_PAYLOAD"
        assert "Invalid JSON" in body["error"]["message"]
        assert "trace_id" in body

    # ── 400: invalid Pydantic model ────────────────────────────────────

    @patch("fn_allocations_aggregate.get_smartsheet_client")
    def test_invalid_pydantic_model_returns_400(self, _mock_client):
        from fn_allocations_aggregate import main

        # allocation_ids must be a list, not a string
        req = _build_request(
            {"allocation_ids": "not-a-list"},
            url="/api/allocations/aggregate",
        )
        resp = main(req)

        assert resp.status_code == 400
        body = json.loads(resp.get_body())
        assert body["error"]["code"] == "INVALID_PAYLOAD"
        assert "trace_id" in body

    # ── 400: neither tag_id nor allocation_ids ─────────────────────────

    @patch("fn_allocations_aggregate.get_smartsheet_client")
    def test_no_tag_id_no_allocation_ids_returns_400(self, _mock_client):
        from fn_allocations_aggregate import main

        req = _build_request({}, url="/api/allocations/aggregate")
        resp = main(req)

        assert resp.status_code == 400
        body = json.loads(resp.get_body())
        assert body["error"]["code"] == "INVALID_PAYLOAD"
        assert "tag_id or allocation_ids" in body["error"]["message"]

    # ── 400: explicit None for both fields ─────────────────────────────

    @patch("fn_allocations_aggregate.get_smartsheet_client")
    def test_explicit_none_for_both_fields_returns_400(self, _mock_client):
        from fn_allocations_aggregate import main

        req = _build_request(
            {"tag_id": None, "allocation_ids": None},
            url="/api/allocations/aggregate",
        )
        resp = main(req)

        assert resp.status_code == 400
        body = json.loads(resp.get_body())
        assert "tag_id or allocation_ids" in body["error"]["message"]

    # ── 400: empty allocation_ids list, no tag_id ──────────────────────

    @patch("fn_allocations_aggregate.get_smartsheet_client")
    def test_empty_allocation_ids_no_tag_returns_400(self, _mock_client):
        from fn_allocations_aggregate import main

        req = _build_request(
            {"allocation_ids": []},
            url="/api/allocations/aggregate",
        )
        resp = main(req)

        assert resp.status_code == 400
        body = json.loads(resp.get_body())
        assert "tag_id or allocation_ids" in body["error"]["message"]

    # ── 200: happy path with tag_id ────────────────────────────────────

    @patch("fn_allocations_aggregate.build_consumption_card")
    @patch("fn_allocations_aggregate.build_consumption_card_lines")
    @patch("fn_allocations_aggregate.get_allocation_details_by_tag")
    @patch("fn_allocations_aggregate.get_smartsheet_client")
    def test_happy_path_tag_id_returns_200(
        self,
        mock_get_client,
        mock_get_details,
        mock_build_lines,
        mock_build_card,
    ):
        from fn_allocations_aggregate import main
        from shared.flow_models import AllocationDetail, ConsumptionCardLine

        mock_get_client.return_value = MagicMock()

        detail = AllocationDetail(
            allocation_id="ALLOC-20260311-ABC123",
            sap_code="10003456",
            nesting_description="Aluminium Tape 50mm",
            sap_qty=4.0,
            sap_uom="ROL",
            raw_qty=100.0,
            raw_uom="m",
            already_consumed=0.0,
            remaining_qty=4.0,
            stock_check_flag="Green",
            planned_date="2026-03-11",
            shift="Morning",
        )
        mock_get_details.return_value = [detail]

        card_line = ConsumptionCardLine(
            allocation_id="ALLOC-20260311-ABC123",
            sap_code="10003456",
            nesting_description="Aluminium Tape 50mm",
            sap_uom="ROL",
            raw_uom="m",
            allocated_raw_qty=100.0,
            default_actual_raw_qty=100.0,
            allocated_sap_qty=4.0,
            default_actual_sap_qty=4.0,
        )
        mock_build_lines.return_value = [card_line]

        mock_build_card.return_value = {"type": "AdaptiveCard", "body": []}

        req = _build_request(
            {"tag_id": "TAG-001", "trace_id": "test-trace-001"},
            url="/api/allocations/aggregate",
        )

        resp = main(req)

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["trace_id"] == "test-trace-001"
        assert body["tag_id"] == "TAG-001"
        assert body["total_materials"] == 1
        assert len(body["allocation_details"]) == 1
        assert body["allocation_details"][0]["allocation_id"] == "ALLOC-20260311-ABC123"
        assert body["allocation_details"][0]["sap_code"] == "10003456"
        assert body["allocation_details"][0]["remaining_qty"] == 4.0
        assert len(body["consumption_card_lines"]) == 1
        assert body["consumption_card_lines"][0]["allocated_raw_qty"] == 100.0
        assert body["consumption_card"] == {"type": "AdaptiveCard", "body": []}

        mock_get_details.assert_called_once()
        mock_build_lines.assert_called_once_with([detail])
        mock_build_card.assert_called_once_with(tag_id="TAG-001", card_lines=[card_line])

    # ── 200: happy path with multiple materials ────────────────────────

    @patch("fn_allocations_aggregate.build_consumption_card")
    @patch("fn_allocations_aggregate.build_consumption_card_lines")
    @patch("fn_allocations_aggregate.get_allocation_details_by_tag")
    @patch("fn_allocations_aggregate.get_smartsheet_client")
    def test_happy_path_multiple_materials(
        self,
        mock_get_client,
        mock_get_details,
        mock_build_lines,
        mock_build_card,
    ):
        from fn_allocations_aggregate import main
        from shared.flow_models import AllocationDetail, ConsumptionCardLine

        mock_get_client.return_value = MagicMock()

        details = [
            AllocationDetail(
                allocation_id=f"ALLOC-{i}",
                sap_code=f"MAT-{i}",
                nesting_description=f"Material {i}",
                sap_qty=float(i * 10),
                sap_uom="ROL",
                raw_qty=float(i * 50),
                raw_uom="m",
                already_consumed=0.0,
                remaining_qty=float(i * 10),
                stock_check_flag="Green",
                planned_date="2026-03-12",
                shift="Evening",
            )
            for i in range(1, 4)
        ]
        mock_get_details.return_value = details

        mock_build_lines.return_value = [
            ConsumptionCardLine(
                allocation_id=f"ALLOC-{i}",
                sap_code=f"MAT-{i}",
                nesting_description=f"Material {i}",
                sap_uom="ROL",
                raw_uom="m",
                allocated_raw_qty=float(i * 50),
                default_actual_raw_qty=float(i * 50),
                allocated_sap_qty=float(i * 10),
                default_actual_sap_qty=float(i * 10),
            )
            for i in range(1, 4)
        ]
        mock_build_card.return_value = {"type": "AdaptiveCard", "body": []}

        req = _build_request(
            {"tag_id": "TAG-MULTI"},
            url="/api/allocations/aggregate",
        )

        resp = main(req)

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["total_materials"] == 3
        assert len(body["allocation_details"]) == 3
        assert len(body["consumption_card_lines"]) == 3

    # ── trace_id: uses request value when provided ─────────────────────

    @patch("fn_allocations_aggregate.build_consumption_card")
    @patch("fn_allocations_aggregate.build_consumption_card_lines")
    @patch("fn_allocations_aggregate.get_allocation_details_by_tag")
    @patch("fn_allocations_aggregate.get_smartsheet_client")
    def test_trace_id_from_request(
        self,
        mock_get_client,
        mock_get_details,
        mock_build_lines,
        mock_build_card,
    ):
        from fn_allocations_aggregate import main

        mock_get_client.return_value = MagicMock()
        mock_get_details.return_value = []
        mock_build_lines.return_value = []
        mock_build_card.return_value = {"type": "AdaptiveCard", "body": []}

        req = _build_request(
            {"tag_id": "TAG-099", "trace_id": "my-custom-trace-xyz"},
            url="/api/allocations/aggregate",
        )

        resp = main(req)

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["trace_id"] == "my-custom-trace-xyz"

    # ── trace_id: auto-generated when not provided ─────────────────────

    @patch("fn_allocations_aggregate.build_consumption_card")
    @patch("fn_allocations_aggregate.build_consumption_card_lines")
    @patch("fn_allocations_aggregate.get_allocation_details_by_tag")
    @patch("fn_allocations_aggregate.get_smartsheet_client")
    def test_trace_id_auto_generated(
        self,
        mock_get_client,
        mock_get_details,
        mock_build_lines,
        mock_build_card,
    ):
        from fn_allocations_aggregate import main

        mock_get_client.return_value = MagicMock()
        mock_get_details.return_value = []
        mock_build_lines.return_value = []
        mock_build_card.return_value = {"type": "AdaptiveCard", "body": []}

        req = _build_request(
            {"tag_id": "TAG-100"},
            url="/api/allocations/aggregate",
        )

        resp = main(req)

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert "trace_id" in body
        assert len(body["trace_id"]) > 0

    # ── 200: backward-compat path with allocation_ids ──────────────────

    @patch("fn_allocations_aggregate.build_consumption_card")
    @patch("fn_allocations_aggregate.build_consumption_card_lines")
    @patch("fn_allocations_aggregate.aggregate_materials")
    @patch("fn_allocations_aggregate.get_smartsheet_client")
    def test_backward_compat_allocation_ids_returns_200(
        self,
        mock_get_client,
        mock_aggregate,
        mock_build_lines,
        mock_build_card,
    ):
        from fn_allocations_aggregate import main
        from shared.flow_models import AggregatedMaterial, ConsumptionCardLine

        mock_get_client.return_value = MagicMock()

        aggregated = AggregatedMaterial(
            canonical_code="10003456",
            allocated_qty=4.0,
            already_consumed=1.0,
            remaining_qty=3.0,
            uom="ROL",
        )
        mock_aggregate.return_value = [aggregated]

        card_line = ConsumptionCardLine(
            allocation_id="",
            sap_code="10003456",
            nesting_description="10003456",
            sap_uom="ROL",
            raw_uom="ROL",
            allocated_raw_qty=4.0,
            default_actual_raw_qty=3.0,
            allocated_sap_qty=4.0,
            default_actual_sap_qty=3.0,
        )
        mock_build_lines.return_value = [card_line]
        mock_build_card.return_value = {"type": "AdaptiveCard", "body": []}

        req = _build_request(
            {
                "allocation_ids": ["ALLOC-20260311-ABC123"],
                "trace_id": "compat-trace-001",
            },
            url="/api/allocations/aggregate",
        )

        resp = main(req)

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["trace_id"] == "compat-trace-001"
        assert body["total_materials"] == 1
        # tag_id should be None in compat mode (not provided in request)
        assert body["tag_id"] is None

        mock_aggregate.assert_called_once()
        call_args = mock_aggregate.call_args
        assert call_args[0][1] == ["ALLOC-20260311-ABC123"]

    # ── allocation_ids compat: allocation detail shells ─────────────────

    @patch("fn_allocations_aggregate.build_consumption_card")
    @patch("fn_allocations_aggregate.build_consumption_card_lines")
    @patch("fn_allocations_aggregate.aggregate_materials")
    @patch("fn_allocations_aggregate.get_smartsheet_client")
    def test_compat_mode_builds_allocation_detail_shells(
        self,
        mock_get_client,
        mock_aggregate,
        mock_build_lines,
        mock_build_card,
    ):
        from fn_allocations_aggregate import main
        from shared.flow_models import AggregatedMaterial

        mock_get_client.return_value = MagicMock()

        aggregated = [
            AggregatedMaterial(
                canonical_code="MAT-A",
                allocated_qty=10.0,
                already_consumed=2.0,
                remaining_qty=8.0,
                uom="KG",
            ),
            AggregatedMaterial(
                canonical_code="MAT-B",
                allocated_qty=5.0,
                already_consumed=0.0,
                remaining_qty=5.0,
                uom="ROL",
            ),
        ]
        mock_aggregate.return_value = aggregated
        mock_build_lines.return_value = []
        mock_build_card.return_value = {"type": "AdaptiveCard", "body": []}

        req = _build_request(
            {"allocation_ids": ["ALLOC-1", "ALLOC-2"]},
            url="/api/allocations/aggregate",
        )

        resp = main(req)

        assert resp.status_code == 200
        body = json.loads(resp.get_body())

        details = body["allocation_details"]
        assert len(details) == 2
        # Verify shell structure from AggregatedMaterial
        assert details[0]["sap_code"] == "MAT-A"
        assert details[0]["sap_qty"] == 10.0
        assert details[0]["already_consumed"] == 2.0
        assert details[0]["remaining_qty"] == 8.0
        assert details[0]["allocation_id"] == ""  # Shell mode — no specific ID
        assert details[1]["sap_code"] == "MAT-B"

    # ── 500: unhandled error creates exception record ──────────────────

    @patch("shared.audit.create_exception")
    @patch("fn_allocations_aggregate.get_allocation_details_by_tag", side_effect=RuntimeError("db down"))
    @patch("fn_allocations_aggregate.get_smartsheet_client")
    def test_unhandled_error_creates_exception_and_returns_500(
        self,
        mock_get_client,
        mock_get_details,
        mock_create_exc,
    ):
        from fn_allocations_aggregate import main

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        req = _build_request(
            {"tag_id": "TAG-ERR"},
            url="/api/allocations/aggregate",
        )

        resp = main(req)

        assert resp.status_code == 500
        body = json.loads(resp.get_body())
        assert body["error"]["code"] == "SERVER_ERROR"
        assert "db down" in body["error"]["message"]
        assert "trace_id" in body

        mock_create_exc.assert_called_once()
        exc_kwargs = mock_create_exc.call_args.kwargs
        assert exc_kwargs["client"] is mock_client
        assert "fn_allocations_aggregate unhandled error" in exc_kwargs["message"]

    # ── 500: create_exception itself fails silently ────────────────────

    @patch("shared.audit.create_exception", side_effect=Exception("audit unavailable"))
    @patch("fn_allocations_aggregate.get_allocation_details_by_tag", side_effect=ValueError("bad data"))
    @patch("fn_allocations_aggregate.get_smartsheet_client")
    def test_create_exception_failure_still_returns_500(
        self,
        mock_get_client,
        mock_get_details,
        mock_create_exc,
    ):
        from fn_allocations_aggregate import main

        mock_get_client.return_value = MagicMock()

        req = _build_request(
            {"tag_id": "TAG-FAIL"},
            url="/api/allocations/aggregate",
        )

        resp = main(req)

        assert resp.status_code == 500
        body = json.loads(resp.get_body())
        assert body["error"]["code"] == "SERVER_ERROR"

    # ── tag_id takes priority over allocation_ids ──────────────────────

    @patch("fn_allocations_aggregate.build_consumption_card")
    @patch("fn_allocations_aggregate.build_consumption_card_lines")
    @patch("fn_allocations_aggregate.get_allocation_details_by_tag")
    @patch("fn_allocations_aggregate.aggregate_materials")
    @patch("fn_allocations_aggregate.get_smartsheet_client")
    def test_tag_id_takes_priority_over_allocation_ids(
        self,
        mock_get_client,
        mock_aggregate,
        mock_get_details,
        mock_build_lines,
        mock_build_card,
    ):
        from fn_allocations_aggregate import main

        mock_get_client.return_value = MagicMock()
        mock_get_details.return_value = []
        mock_build_lines.return_value = []
        mock_build_card.return_value = {"type": "AdaptiveCard", "body": []}

        req = _build_request(
            {
                "tag_id": "TAG-PRIORITY",
                "allocation_ids": ["ALLOC-SHOULD-NOT-USE"],
            },
            url="/api/allocations/aggregate",
        )

        resp = main(req)

        assert resp.status_code == 200
        # tag_id path should be used
        mock_get_details.assert_called_once()
        # allocation_ids path should NOT be used
        mock_aggregate.assert_not_called()

    # ── consumption_card is included in response ───────────────────────

    @patch("fn_allocations_aggregate.build_consumption_card")
    @patch("fn_allocations_aggregate.build_consumption_card_lines")
    @patch("fn_allocations_aggregate.get_allocation_details_by_tag")
    @patch("fn_allocations_aggregate.get_smartsheet_client")
    def test_response_includes_consumption_card(
        self,
        mock_get_client,
        mock_get_details,
        mock_build_lines,
        mock_build_card,
    ):
        from fn_allocations_aggregate import main

        mock_get_client.return_value = MagicMock()
        mock_get_details.return_value = []
        mock_build_lines.return_value = []
        expected_card = {
            "type": "AdaptiveCard",
            "version": "1.4",
            "body": [{"type": "TextBlock", "text": "Consumption Form"}],
        }
        mock_build_card.return_value = expected_card

        req = _build_request(
            {"tag_id": "TAG-CARD"},
            url="/api/allocations/aggregate",
        )

        resp = main(req)

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["consumption_card"] == expected_card

    # ── zero materials returns 200 with empty lists ────────────────────

    @patch("fn_allocations_aggregate.build_consumption_card")
    @patch("fn_allocations_aggregate.build_consumption_card_lines")
    @patch("fn_allocations_aggregate.get_allocation_details_by_tag")
    @patch("fn_allocations_aggregate.get_smartsheet_client")
    def test_zero_materials_returns_200_with_empty_lists(
        self,
        mock_get_client,
        mock_get_details,
        mock_build_lines,
        mock_build_card,
    ):
        from fn_allocations_aggregate import main

        mock_get_client.return_value = MagicMock()
        mock_get_details.return_value = []
        mock_build_lines.return_value = []
        mock_build_card.return_value = {"type": "AdaptiveCard", "body": []}

        req = _build_request(
            {"tag_id": "TAG-EMPTY"},
            url="/api/allocations/aggregate",
        )

        resp = main(req)

        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["total_materials"] == 0
        assert body["allocation_details"] == []
        assert body["consumption_card_lines"] == []
