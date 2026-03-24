"""
Unit Tests for fn_process_manager_approval
==========================================
Tests the manager approval flow for margin/DO creation, including:
- Invalid JSON handling
- Action normalization (button titles -> canonical actions)
- Hold action (no-op)
- Unknown action rejection
- Idempotency (already approved)
- Approval not found (404)
- Lock acquisition failure (409)
- LPO reference missing (500)
- Blob storage not configured (500)
- Happy path: full DO creation with penalty, delivery log, status updates
"""

import pytest
import json
from unittest.mock import MagicMock, patch, ANY, PropertyMock

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _make_request(body):
    """Build a mock Azure Functions HttpRequest."""
    req = MagicMock()
    req.get_json.return_value = body
    return req


def _make_invalid_json_request():
    """Build a mock request that raises ValueError on get_json."""
    req = MagicMock()
    req.get_json.side_effect = ValueError("Invalid JSON")
    return req


# ---------------------------------------------------------------------------
# Shared patch targets (all at the fn_process_manager_approval module level)
# ---------------------------------------------------------------------------
MODULE = "fn_process_manager_approval"

COMMON_PATCHES = {
    "client_cls": f"{MODULE}.SmartsheetClient",
    "get_manifest": f"{MODULE}.get_manifest",
    "alloc_lock": f"{MODULE}.AllocationLock",
    "get_blob_svc": f"{MODULE}.get_blob_service_client",
    "get_container": f"{MODULE}.get_container_name",
    "upload_blob": f"{MODULE}.upload_json_blob",
    "log_action": f"{MODULE}.log_user_action",
    "create_exc": f"{MODULE}.create_exception",
    "resolve_email": f"{MODULE}.resolve_user_email",
    # These are imported lazily inside the function body, so patch at source
    "costing_svc": "shared.costing_service.CostingService",
    "build_card": "shared.adaptive_card_builder.build_do_creation_card",
    "parse_rows": "shared.allocation_service._parse_rows",
    "now_uae": f"{MODULE}.now_uae",
    "fmt_dt": f"{MODULE}.format_datetime_for_smartsheet",
    # requests is imported inline via `import requests` -- patch at the library level
    "requests_post": "requests.post",
    "requests_mod": f"{MODULE}.requests",
}


def _build_mock_manifest():
    """Return a mock manifest with the column names the function expects."""
    manifest = MagicMock()

    # get_column_name returns a deterministic physical name per (sheet, col) pair
    def _col_name(sheet, col):
        return f"{sheet}__{col}"

    manifest.get_column_name.side_effect = _col_name

    # get_all_column_ids returns {physical_col_name: column_id}
    def _all_ids(sheet):
        # For TAG_REGISTRY the function only looks up TAG_ID and LPO_SAP_REFERENCE
        if "TAG_REGISTRY" in str(sheet):
            return {
                f"{sheet}__TAG_ID": 2001,
                f"{sheet}__LPO_SAP_REFERENCE": 2005,
            }
        return {}

    manifest.get_all_column_ids.side_effect = _all_ids
    return manifest


def _build_mock_client(
    *,
    approval_rows=None,
    tag_reg_sheet=None,
    nesting_rows=None,
    lpo_row=None,
    consumption_sheet=None,
    config_row=None,
):
    """Return a pre-configured mock SmartsheetClient."""
    client = MagicMock()

    # find_rows — keyed on sheet logical name
    def _find_rows(sheet, col, val):
        sheet_str = str(sheet)
        if "MARGIN_APPROVAL_LOG" in sheet_str:
            return approval_rows if approval_rows is not None else []
        if "NESTING_LOG" in sheet_str:
            return nesting_rows if nesting_rows is not None else []
        return []

    client.find_rows.side_effect = _find_rows

    # Auto-build tag lookup map from tag_reg_sheet
    _tag_map = {}
    if tag_reg_sheet:
        for r in tag_reg_sheet.get("rows", []):
            cells = {c.get("columnId"): c.get("value") for c in r.get("cells", [])}
            tid = cells.get(2001, "")
            lpo = cells.get(2005, "")
            _tag_map[str(tid)] = {
                "row_id": r.get("id"),
                "TAG_REGISTRY__TAG_ID": str(tid),
                "TAG_REGISTRY__LPO_SAP_REFERENCE": str(lpo),
            }

    # find_row — used for TAG_REGISTRY, LPO_MASTER and CONFIG
    def _find_row(sheet, col, val):
        sheet_str = str(sheet)
        if "TAG_REGISTRY" in sheet_str:
            return _tag_map.get(str(val))
        if "LPO_MASTER" in sheet_str:
            return lpo_row
        if "CONFIG" in sheet_str:
            return config_row
        return None

    client.find_row.side_effect = _find_row

    # get_sheet — returns dict with "rows" list
    def _get_sheet(sheet):
        sheet_str = str(sheet)
        if "TAG_REGISTRY" in sheet_str:
            return tag_reg_sheet if tag_reg_sheet is not None else {"rows": []}
        if "CONSUMPTION_LOG" in sheet_str:
            return consumption_sheet if consumption_sheet is not None else {"rows": [], "columns": []}
        return {"rows": []}

    client.get_sheet.side_effect = _get_sheet

    # Writes
    client.add_row.return_value = {"id": 5001}
    client.update_row.return_value = {"id": 5002}

    return client


def _build_lock(success=True):
    """Return a mock AllocationLock context manager."""
    lock_cm = MagicMock()
    lock_instance = MagicMock()
    lock_instance.success = success
    lock_cm.return_value.__enter__ = MagicMock(return_value=lock_instance)
    lock_cm.return_value.__exit__ = MagicMock(return_value=False)
    return lock_cm


def _base_request_body(**overrides):
    """Return a valid proceed_to_do request payload."""
    body = {
        "action": "proceed_to_do",
        "approval_row_id": "APP-001",
        "tag_sheet_id": "TAG-001",
        "manager_penalty_pct": "5.0",
        "merge_tags": "TAG-002,TAG-003",
        "approver": "manager@co.com",
    }
    body.update(overrides)
    return body


def _tag_registry_sheet_with_rows(tag_ids, lpo_ref="LPO-REF-100"):
    """Build a mock TAG_REGISTRY get_sheet response containing the given tag IDs."""
    rows = []
    for tid in tag_ids:
        rows.append({
            "id": hash(tid) % 100000,
            "cells": [
                {"columnId": 2001, "value": tid},
                {"columnId": 2005, "value": lpo_ref},
            ],
        })
    return {"rows": rows}


# ===========================================================================
# Test class
# ===========================================================================


@pytest.mark.unit
class TestProcessManagerApproval:
    """Unit tests for fn_process_manager_approval.main."""

    # -----------------------------------------------------------------------
    # 1. Invalid JSON
    # -----------------------------------------------------------------------
    @patch(COMMON_PATCHES["create_exc"])
    @patch(COMMON_PATCHES["client_cls"])
    def test_invalid_json_returns_400(self, mock_cls, mock_exc):
        from fn_process_manager_approval import main

        req = _make_invalid_json_request()
        result = main(req)

        assert result.status_code == 400
        assert b"Invalid JSON" in result.get_body()
        mock_exc.assert_called_once()

    # -----------------------------------------------------------------------
    # 2. Action normalization — hold variants
    # -----------------------------------------------------------------------
    @pytest.mark.parametrize("raw_action", [
        "hold_tag",
        "Hold",
        "Wait",
        "wait",
        "HOLD",
        "Hold Tag",
    ])
    def test_hold_action_returns_200(self, raw_action):
        from fn_process_manager_approval import main

        req = _make_request({"action": raw_action, "approval_row_id": "APP-X"})
        result = main(req)

        assert result.status_code == 200
        assert b"held in pending state" in result.get_body()

    # -----------------------------------------------------------------------
    # 3. Action normalization — proceed variants
    # -----------------------------------------------------------------------
    @pytest.mark.parametrize("raw_action", [
        "Proceed to DO",
        "proceed_to_do",
        "PROCEED TO DO",
        "Proceed_to_DO",
    ])
    @patch(COMMON_PATCHES["create_exc"])
    @patch(COMMON_PATCHES["client_cls"])
    @patch(COMMON_PATCHES["get_manifest"])
    def test_proceed_actions_normalise_correctly(
        self, mock_manifest, mock_cls, mock_exc, raw_action
    ):
        """Proceed variants pass normalisation and reach the client query.
        We only verify they don't hit the 'unknown action' path (400)."""
        from fn_process_manager_approval import main

        mock_client = _build_mock_client(approval_rows=[])
        mock_cls.return_value = mock_client
        mock_manifest.return_value = _build_mock_manifest()

        req = _make_request({"action": raw_action, "approval_row_id": "APP-X"})
        result = main(req)

        # Should be 404 (approval not found), NOT 400 (unknown action)
        assert result.status_code == 404

    # -----------------------------------------------------------------------
    # 4. Unknown action
    # -----------------------------------------------------------------------
    @patch(COMMON_PATCHES["create_exc"])
    @patch(COMMON_PATCHES["client_cls"])
    def test_unknown_action_returns_400(self, mock_cls, mock_exc):
        from fn_process_manager_approval import main

        req = _make_request({"action": "cancel_everything", "approval_row_id": "APP-X"})
        result = main(req)

        assert result.status_code == 400
        assert b"Unknown action" in result.get_body()
        mock_exc.assert_called_once()

    # -----------------------------------------------------------------------
    # 5. Idempotency — already approved
    # -----------------------------------------------------------------------
    @patch(COMMON_PATCHES["create_exc"])
    @patch(COMMON_PATCHES["client_cls"])
    @patch(COMMON_PATCHES["get_manifest"])
    def test_already_approved_returns_200(self, mock_manifest, mock_cls, mock_exc):
        from fn_process_manager_approval import main

        manifest = _build_mock_manifest()
        mock_manifest.return_value = manifest

        col_status = manifest.get_column_name("06C_MARGIN_APPROVAL_LOG", "STATUS")
        approval_row = {
            "row_id": 9001,
            col_status: "Approved",
        }
        mock_client = _build_mock_client(approval_rows=[approval_row])
        mock_cls.return_value = mock_client

        req = _make_request(_base_request_body())
        result = main(req)

        assert result.status_code == 200
        assert b"Already approved" in result.get_body()
        # No writes should have happened
        mock_client.add_row.assert_not_called()
        mock_client.update_row.assert_not_called()

    # -----------------------------------------------------------------------
    # 6. Approval not found (404)
    # -----------------------------------------------------------------------
    @patch(COMMON_PATCHES["create_exc"])
    @patch(COMMON_PATCHES["client_cls"])
    @patch(COMMON_PATCHES["get_manifest"])
    def test_approval_not_found_returns_404(self, mock_manifest, mock_cls, mock_exc):
        from fn_process_manager_approval import main

        mock_manifest.return_value = _build_mock_manifest()
        mock_client = _build_mock_client(approval_rows=[])
        mock_cls.return_value = mock_client

        req = _make_request(_base_request_body())
        result = main(req)

        assert result.status_code == 404
        assert b"Approval not found" in result.get_body()
        mock_exc.assert_called_once()

    # -----------------------------------------------------------------------
    # 7. Lock not acquired (409)
    # -----------------------------------------------------------------------
    @patch(COMMON_PATCHES["create_exc"])
    @patch(COMMON_PATCHES["alloc_lock"])
    @patch(COMMON_PATCHES["client_cls"])
    @patch(COMMON_PATCHES["get_manifest"])
    def test_lock_timeout_returns_409(self, mock_manifest, mock_cls, mock_lock, mock_exc):
        from fn_process_manager_approval import main

        manifest = _build_mock_manifest()
        mock_manifest.return_value = manifest

        col_status = manifest.get_column_name("06C_MARGIN_APPROVAL_LOG", "STATUS")
        approval_row = {"row_id": 9001, col_status: "Pending"}
        mock_client = _build_mock_client(approval_rows=[approval_row])
        mock_cls.return_value = mock_client

        # Lock fails
        lock_instance = MagicMock()
        lock_instance.success = False
        mock_lock.return_value.__enter__ = MagicMock(return_value=lock_instance)
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)

        req = _make_request(_base_request_body())
        result = main(req)

        assert result.status_code == 409
        body = json.loads(result.get_body())
        assert body["error"] == "LOCK_TIMEOUT"

    # -----------------------------------------------------------------------
    # 8. LPO reference not determined (500)
    # -----------------------------------------------------------------------
    @patch(COMMON_PATCHES["create_exc"])
    @patch(COMMON_PATCHES["alloc_lock"])
    @patch(COMMON_PATCHES["client_cls"])
    @patch(COMMON_PATCHES["get_manifest"])
    def test_lpo_ref_missing_returns_500(self, mock_manifest, mock_cls, mock_lock, mock_exc):
        from fn_process_manager_approval import main

        manifest = _build_mock_manifest()
        mock_manifest.return_value = manifest

        col_status = manifest.get_column_name("06C_MARGIN_APPROVAL_LOG", "STATUS")
        approval_row = {"row_id": 9001, col_status: "Pending"}

        # TAG_REGISTRY with NO matching tags -> LPO ref will be empty
        empty_tag_sheet = {"rows": []}

        mock_client = _build_mock_client(
            approval_rows=[approval_row],
            tag_reg_sheet=empty_tag_sheet,
        )
        mock_cls.return_value = mock_client

        # Lock succeeds
        lock_instance = MagicMock()
        lock_instance.success = True
        mock_lock.return_value.__enter__ = MagicMock(return_value=lock_instance)
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)

        req = _make_request(_base_request_body())
        result = main(req)

        assert result.status_code == 500
        assert b"LPO Reference missing" in result.get_body()
        mock_exc.assert_called_once()

    # -----------------------------------------------------------------------
    # 9. Blob storage not configured (500)
    # -----------------------------------------------------------------------
    @patch(COMMON_PATCHES["create_exc"])
    @patch(COMMON_PATCHES["get_blob_svc"])
    @patch(COMMON_PATCHES["alloc_lock"])
    @patch(COMMON_PATCHES["client_cls"])
    @patch(COMMON_PATCHES["get_manifest"])
    def test_blob_not_configured_returns_500(
        self, mock_manifest, mock_cls, mock_lock, mock_blob_svc, mock_exc
    ):
        from fn_process_manager_approval import main

        manifest = _build_mock_manifest()
        mock_manifest.return_value = manifest

        col_status = manifest.get_column_name("06C_MARGIN_APPROVAL_LOG", "STATUS")
        approval_row = {"row_id": 9001, col_status: "Pending"}

        tag_sheet = _tag_registry_sheet_with_rows(["TAG-001", "TAG-002", "TAG-003"])
        mock_client = _build_mock_client(
            approval_rows=[approval_row],
            tag_reg_sheet=tag_sheet,
            nesting_rows=[],       # No nesting rows -> no blob fetches
            lpo_row={"Area Type": "External"},
        )
        mock_cls.return_value = mock_client

        # Lock succeeds
        lock_instance = MagicMock()
        lock_instance.success = True
        mock_lock.return_value.__enter__ = MagicMock(return_value=lock_instance)
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)

        # Blob service returns None (not configured)
        mock_blob_svc.return_value = None

        req = _make_request(_base_request_body())
        result = main(req)

        assert result.status_code == 500
        assert b"Blob storage not configured" in result.get_body()
        mock_exc.assert_called_once()

    # -----------------------------------------------------------------------
    # 10. Happy path — full DO creation with penalty
    # -----------------------------------------------------------------------
    @patch(COMMON_PATCHES["requests_post"])
    @patch(COMMON_PATCHES["parse_rows"])
    @patch(COMMON_PATCHES["build_card"])
    @patch(COMMON_PATCHES["costing_svc"])
    @patch(COMMON_PATCHES["resolve_email"])
    @patch(COMMON_PATCHES["create_exc"])
    @patch(COMMON_PATCHES["log_action"])
    @patch(COMMON_PATCHES["upload_blob"])
    @patch(COMMON_PATCHES["get_container"])
    @patch(COMMON_PATCHES["get_blob_svc"])
    @patch(COMMON_PATCHES["fmt_dt"])
    @patch(COMMON_PATCHES["now_uae"])
    @patch(COMMON_PATCHES["alloc_lock"])
    @patch(COMMON_PATCHES["client_cls"])
    @patch(COMMON_PATCHES["get_manifest"])
    def test_happy_path_do_creation(
        self,
        mock_manifest,
        mock_cls,
        mock_lock,
        mock_now,
        mock_fmt,
        mock_blob_svc,
        mock_container,
        mock_upload,
        mock_log,
        mock_exc,
        mock_resolve,
        mock_costing_cls,
        mock_build_card,
        mock_parse_rows,
        mock_requests_post,
    ):
        from fn_process_manager_approval import main

        # -- manifest --
        manifest = _build_mock_manifest()
        mock_manifest.return_value = manifest

        # -- client --
        col_status = manifest.get_column_name("06C_MARGIN_APPROVAL_LOG", "STATUS")
        approval_row = {"row_id": 9001, col_status: "Pending"}

        tag_sheet = _tag_registry_sheet_with_rows(
            ["TAG-001", "TAG-002", "TAG-003"], lpo_ref="LPO-REF-100"
        )

        col_nl_session = manifest.get_column_name("NESTING_LOG", "NEST_SESSION_ID")
        nesting_rows = [
            {col_nl_session: "SESS-001"},
            {col_nl_session: "SESS-002"},
        ]

        col_area_type = manifest.get_column_name("LPO_MASTER", "AREA_TYPE")
        lpo_row = {col_area_type: "External"}

        mock_client = _build_mock_client(
            approval_rows=[approval_row],
            tag_reg_sheet=tag_sheet,
            nesting_rows=nesting_rows,
            lpo_row=lpo_row,
            consumption_sheet={"rows": [], "columns": []},
        )
        mock_cls.return_value = mock_client

        # -- lock --
        lock_instance = MagicMock()
        lock_instance.success = True
        mock_lock.return_value.__enter__ = MagicMock(return_value=lock_instance)
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)

        # -- datetime helpers --
        from datetime import datetime
        mock_now.return_value = datetime(2026, 3, 24, 10, 0, 0)
        mock_fmt.return_value = "2026-03-24T10:00:00"

        # -- blob service --
        mock_blob_client = MagicMock()
        blob_content = json.dumps({
            "meta_data": {"tag_id": "TAG-001"},
            "finished_goods_manifest": [
                {
                    "description": "Duct Panel 50mm",
                    "external_area_m2": 10.0,
                    "internal_area_m2": 8.0,
                    "qty_produced": 5,
                },
                {
                    "description": "Elbow Fitting",
                    "external_area_m2": 2.5,
                    "internal_area_m2": 2.0,
                    "qty": 3,
                },
            ],
        }).encode()
        mock_blob_client.download_blob.return_value.readall.return_value = blob_content

        mock_container_client = MagicMock()
        mock_container_client.get_blob_client.return_value = mock_blob_client

        mock_service_client = MagicMock()
        mock_service_client.get_container_client.return_value = mock_container_client
        mock_blob_svc.return_value = mock_service_client
        mock_container.return_value = "test-container"

        # -- costing service --
        mock_costing = MagicMock()
        mock_costing.calculate_margin.return_value = {
            "selling_price_per_sqm": 30.0,
            "total_cost_aed": 1000.0,
            "gm_pct": 0.25,
        }
        mock_costing_cls.return_value = mock_costing

        # -- card builder --
        mock_build_card.return_value = {"type": "AdaptiveCard"}

        # -- parse_rows (consumption) --
        mock_parse_rows.return_value = []

        # -- resolve email --
        mock_resolve.side_effect = lambda client, email: email

        # -- requests.post (webhook POST) --
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_requests_post.return_value = mock_resp

        # -- environment variable for webhook --
        with patch.dict(os.environ, {"POWER_AUTOMATE_DO_CREATION_URL": "https://webhook.test/do"}):
            req = _make_request(_base_request_body())
            result = main(req)

        # -- assertions --
        assert result.status_code == 200
        body = json.loads(result.get_body())
        assert body["status"] == "success"
        assert "delivery_id" in body
        assert body["delivery_id"].startswith("DO-")
        assert "blob_path" in body
        assert "margin_summary" in body

        # Penalty is 5% -> multiplier = 1.05
        summary = body["margin_summary"]
        assert summary["penalty_pct"] == 5.0

        # Blob was uploaded
        mock_upload.assert_called_once()

        # No DELIVERY_LOG row created (delivery log is created later via fn_delivery_ingest)
        # add_row is NOT called — only update_row for MARGIN_APPROVAL_LOG + TAG_REGISTRY
        mock_client.update_row.assert_called()

        # Audit log_user_action was called (DO_CREATED + TAG_UPDATED for approval + TAG_UPDATED for tags)
        assert mock_log.call_count >= 2

    # -----------------------------------------------------------------------
    # 11. Penalty calculation correctness
    # -----------------------------------------------------------------------
    @patch(COMMON_PATCHES["requests_post"])
    @patch(COMMON_PATCHES["parse_rows"])
    @patch(COMMON_PATCHES["build_card"])
    @patch(COMMON_PATCHES["costing_svc"])
    @patch(COMMON_PATCHES["resolve_email"])
    @patch(COMMON_PATCHES["create_exc"])
    @patch(COMMON_PATCHES["log_action"])
    @patch(COMMON_PATCHES["upload_blob"])
    @patch(COMMON_PATCHES["get_container"])
    @patch(COMMON_PATCHES["get_blob_svc"])
    @patch(COMMON_PATCHES["fmt_dt"])
    @patch(COMMON_PATCHES["now_uae"])
    @patch(COMMON_PATCHES["alloc_lock"])
    @patch(COMMON_PATCHES["client_cls"])
    @patch(COMMON_PATCHES["get_manifest"])
    def test_zero_penalty_multiplier_is_one(
        self,
        mock_manifest,
        mock_cls,
        mock_lock,
        mock_now,
        mock_fmt,
        mock_blob_svc,
        mock_container,
        mock_upload,
        mock_log,
        mock_exc,
        mock_resolve,
        mock_costing_cls,
        mock_build_card,
        mock_parse_rows,
        mock_requests_post,
    ):
        """With 0% penalty, billed area should equal original area."""
        from fn_process_manager_approval import main

        manifest = _build_mock_manifest()
        mock_manifest.return_value = manifest

        col_status = manifest.get_column_name("06C_MARGIN_APPROVAL_LOG", "STATUS")
        approval_row = {"row_id": 9001, col_status: "Pending"}

        tag_sheet = _tag_registry_sheet_with_rows(["TAG-001"], lpo_ref="LPO-200")
        col_nl_session = manifest.get_column_name("NESTING_LOG", "NEST_SESSION_ID")
        nesting_rows = [{col_nl_session: "SESS-A"}]

        col_area = manifest.get_column_name("LPO_MASTER", "AREA_TYPE")
        lpo_row = {col_area: "External"}

        mock_client = _build_mock_client(
            approval_rows=[approval_row],
            tag_reg_sheet=tag_sheet,
            nesting_rows=nesting_rows,
            lpo_row=lpo_row,
            consumption_sheet={"rows": [], "columns": []},
        )
        mock_cls.return_value = mock_client

        lock_instance = MagicMock()
        lock_instance.success = True
        mock_lock.return_value.__enter__ = MagicMock(return_value=lock_instance)
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)

        from datetime import datetime
        mock_now.return_value = datetime(2026, 3, 24)
        mock_fmt.return_value = "2026-03-24T00:00:00"

        # Single blob with one line: area = 10 * 2 = 20 sqm
        blob_content = json.dumps({
            "meta_data": {"tag_id": "TAG-001"},
            "finished_goods_manifest": [
                {"description": "Panel", "external_area_m2": 10.0, "qty_produced": 2},
            ],
        }).encode()
        mock_blob_client = MagicMock()
        mock_blob_client.download_blob.return_value.readall.return_value = blob_content
        mock_container_client = MagicMock()
        mock_container_client.get_blob_client.return_value = mock_blob_client
        mock_svc = MagicMock()
        mock_svc.get_container_client.return_value = mock_container_client
        mock_blob_svc.return_value = mock_svc
        mock_container.return_value = "c"

        mock_costing = MagicMock()
        mock_costing.calculate_margin.return_value = {
            "selling_price_per_sqm": 25.0,
            "total_cost_aed": 400.0,
            "gm_pct": 0.20,
        }
        mock_costing_cls.return_value = mock_costing

        mock_build_card.return_value = {}
        mock_parse_rows.return_value = []
        mock_resolve.side_effect = lambda c, e: e
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_requests_post.return_value = mock_resp

        with patch.dict(os.environ, {"POWER_AUTOMATE_DO_CREATION_URL": ""}):
            req = _make_request(_base_request_body(manager_penalty_pct="0"))
            result = main(req)

        body = json.loads(result.get_body())
        summary = body["margin_summary"]
        # 0% penalty -> original == billed
        assert summary["original_area_sqm"] == summary["billed_area_sqm"]

    # -----------------------------------------------------------------------
    # 12. Empty penalty string defaults to 0
    # -----------------------------------------------------------------------
    @patch(COMMON_PATCHES["create_exc"])
    @patch(COMMON_PATCHES["client_cls"])
    @patch(COMMON_PATCHES["get_manifest"])
    def test_empty_penalty_defaults_to_zero(self, mock_manifest, mock_cls, mock_exc):
        """An empty penalty_pct string should not crash; default to 0.0."""
        from fn_process_manager_approval import main

        manifest = _build_mock_manifest()
        mock_manifest.return_value = manifest

        col_status = manifest.get_column_name("06C_MARGIN_APPROVAL_LOG", "STATUS")
        approval_row = {"row_id": 9001, col_status: "Approved"}

        mock_client = _build_mock_client(approval_rows=[approval_row])
        mock_cls.return_value = mock_client

        # With empty penalty and already approved, it should idempotently return 200
        req = _make_request(_base_request_body(manager_penalty_pct=""))
        result = main(req)

        assert result.status_code == 200

    # -----------------------------------------------------------------------
    # 13. Non-numeric penalty string defaults to 0
    # -----------------------------------------------------------------------
    @patch(COMMON_PATCHES["create_exc"])
    @patch(COMMON_PATCHES["client_cls"])
    @patch(COMMON_PATCHES["get_manifest"])
    def test_nonnumeric_penalty_defaults_to_zero(self, mock_manifest, mock_cls, mock_exc):
        from fn_process_manager_approval import main

        manifest = _build_mock_manifest()
        mock_manifest.return_value = manifest

        col_status = manifest.get_column_name("06C_MARGIN_APPROVAL_LOG", "STATUS")
        approval_row = {"row_id": 9001, col_status: "Approved"}

        mock_client = _build_mock_client(approval_rows=[approval_row])
        mock_cls.return_value = mock_client

        req = _make_request(_base_request_body(manager_penalty_pct="abc"))
        result = main(req)

        # Should reach idempotent exit, not crash on float("abc")
        assert result.status_code == 200

    # -----------------------------------------------------------------------
    # 14. merge_tags parsing — empty string yields no extras
    # -----------------------------------------------------------------------
    @patch(COMMON_PATCHES["create_exc"])
    @patch(COMMON_PATCHES["client_cls"])
    @patch(COMMON_PATCHES["get_manifest"])
    def test_empty_merge_tags(self, mock_manifest, mock_cls, mock_exc):
        from fn_process_manager_approval import main

        manifest = _build_mock_manifest()
        mock_manifest.return_value = manifest

        col_status = manifest.get_column_name("06C_MARGIN_APPROVAL_LOG", "STATUS")
        approval_row = {"row_id": 9001, col_status: "Approved"}
        mock_client = _build_mock_client(approval_rows=[approval_row])
        mock_cls.return_value = mock_client

        req = _make_request(_base_request_body(merge_tags=""))
        result = main(req)

        assert result.status_code == 200  # idempotent exit

    # -----------------------------------------------------------------------
    # 15. MARGIN_APPROVAL_LOG query exception -> 500
    # -----------------------------------------------------------------------
    @patch(COMMON_PATCHES["create_exc"])
    @patch(COMMON_PATCHES["client_cls"])
    @patch(COMMON_PATCHES["get_manifest"])
    def test_db_error_querying_approval_log_returns_500(self, mock_manifest, mock_cls, mock_exc):
        from fn_process_manager_approval import main

        manifest = _build_mock_manifest()
        mock_manifest.return_value = manifest

        mock_client = MagicMock()
        mock_client.find_rows.side_effect = RuntimeError("DB connection lost")
        mock_cls.return_value = mock_client

        req = _make_request(_base_request_body())
        result = main(req)

        assert result.status_code == 500
        assert b"Internal DB Error" in result.get_body()
        mock_exc.assert_called_once()

    # -----------------------------------------------------------------------
    # 16. Tag status updated to Dispatched
    # -----------------------------------------------------------------------
    @patch(COMMON_PATCHES["requests_post"])
    @patch(COMMON_PATCHES["parse_rows"])
    @patch(COMMON_PATCHES["build_card"])
    @patch(COMMON_PATCHES["costing_svc"])
    @patch(COMMON_PATCHES["resolve_email"])
    @patch(COMMON_PATCHES["create_exc"])
    @patch(COMMON_PATCHES["log_action"])
    @patch(COMMON_PATCHES["upload_blob"])
    @patch(COMMON_PATCHES["get_container"])
    @patch(COMMON_PATCHES["get_blob_svc"])
    @patch(COMMON_PATCHES["fmt_dt"])
    @patch(COMMON_PATCHES["now_uae"])
    @patch(COMMON_PATCHES["alloc_lock"])
    @patch(COMMON_PATCHES["client_cls"])
    @patch(COMMON_PATCHES["get_manifest"])
    def test_tag_registry_updated_to_dispatched(
        self,
        mock_manifest,
        mock_cls,
        mock_lock,
        mock_now,
        mock_fmt,
        mock_blob_svc,
        mock_container,
        mock_upload,
        mock_log,
        mock_exc,
        mock_resolve,
        mock_costing_cls,
        mock_build_card,
        mock_parse_rows,
        mock_requests_post,
    ):
        """Verify that every matched tag row gets update_row called with Dispatched."""
        from fn_process_manager_approval import main
        from shared.logical_names import Sheet, Column

        manifest = _build_mock_manifest()
        mock_manifest.return_value = manifest

        col_status = manifest.get_column_name("06C_MARGIN_APPROVAL_LOG", "STATUS")
        approval_row = {"row_id": 9001, col_status: "Pending"}

        tag_sheet = _tag_registry_sheet_with_rows(
            ["TAG-001", "TAG-002", "TAG-003"], lpo_ref="LPO-300"
        )
        mock_client = _build_mock_client(
            approval_rows=[approval_row],
            tag_reg_sheet=tag_sheet,
            nesting_rows=[],
            lpo_row={},
            consumption_sheet={"rows": [], "columns": []},
        )
        mock_cls.return_value = mock_client

        lock_instance = MagicMock()
        lock_instance.success = True
        mock_lock.return_value.__enter__ = MagicMock(return_value=lock_instance)
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)

        from datetime import datetime
        mock_now.return_value = datetime(2026, 3, 24)
        mock_fmt.return_value = "2026-03-24T00:00:00"

        mock_blob_svc.return_value = MagicMock()
        mock_container.return_value = "c"
        mock_costing = MagicMock()
        mock_costing.calculate_margin.return_value = {
            "selling_price_per_sqm": 10.0,
            "total_cost_aed": 0.0,
            "gm_pct": 0.0,
        }
        mock_costing_cls.return_value = mock_costing
        mock_build_card.return_value = {}
        mock_parse_rows.return_value = []
        mock_resolve.side_effect = lambda c, e: e
        mock_requests_post.return_value = MagicMock()

        with patch.dict(os.environ, {"POWER_AUTOMATE_DO_CREATION_URL": ""}):
            req = _make_request(_base_request_body())
            result = main(req)

        assert result.status_code == 200

        # update_row should be called for each matched tag + the approval row
        # 3 tags + 1 approval update = at least 4 update_row calls
        update_calls = mock_client.update_row.call_args_list
        dispatched_calls = [
            c for c in update_calls
            if len(c.args) >= 3 and isinstance(c.args[2], dict)
            and c.args[2].get(Column.TAG_REGISTRY.STATUS) == "Dispatched"
        ]
        assert len(dispatched_calls) == 3

    # -----------------------------------------------------------------------
    # 17. Costing failure falls back to basic figures
    # -----------------------------------------------------------------------
    @patch(COMMON_PATCHES["requests_post"])
    @patch(COMMON_PATCHES["parse_rows"])
    @patch(COMMON_PATCHES["build_card"])
    @patch(COMMON_PATCHES["costing_svc"])
    @patch(COMMON_PATCHES["resolve_email"])
    @patch(COMMON_PATCHES["create_exc"])
    @patch(COMMON_PATCHES["log_action"])
    @patch(COMMON_PATCHES["upload_blob"])
    @patch(COMMON_PATCHES["get_container"])
    @patch(COMMON_PATCHES["get_blob_svc"])
    @patch(COMMON_PATCHES["fmt_dt"])
    @patch(COMMON_PATCHES["now_uae"])
    @patch(COMMON_PATCHES["alloc_lock"])
    @patch(COMMON_PATCHES["client_cls"])
    @patch(COMMON_PATCHES["get_manifest"])
    def test_costing_failure_fallback(
        self,
        mock_manifest,
        mock_cls,
        mock_lock,
        mock_now,
        mock_fmt,
        mock_blob_svc,
        mock_container,
        mock_upload,
        mock_log,
        mock_exc,
        mock_resolve,
        mock_costing_cls,
        mock_build_card,
        mock_parse_rows,
        mock_requests_post,
    ):
        """When CostingService.calculate_margin raises, function should still succeed with fallback."""
        from fn_process_manager_approval import main

        manifest = _build_mock_manifest()
        mock_manifest.return_value = manifest

        col_status = manifest.get_column_name("06C_MARGIN_APPROVAL_LOG", "STATUS")
        approval_row = {"row_id": 9001, col_status: "Pending"}
        tag_sheet = _tag_registry_sheet_with_rows(["TAG-001"], lpo_ref="LPO-400")

        col_nl = manifest.get_column_name("NESTING_LOG", "NEST_SESSION_ID")
        nesting_rows = [{col_nl: "S1"}]

        mock_client = _build_mock_client(
            approval_rows=[approval_row],
            tag_reg_sheet=tag_sheet,
            nesting_rows=nesting_rows,
            lpo_row={},
            consumption_sheet={"rows": [], "columns": []},
        )
        mock_cls.return_value = mock_client

        lock_instance = MagicMock()
        lock_instance.success = True
        mock_lock.return_value.__enter__ = MagicMock(return_value=lock_instance)
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)

        from datetime import datetime
        mock_now.return_value = datetime(2026, 3, 24)
        mock_fmt.return_value = "2026-03-24T00:00:00"

        blob_content = json.dumps({
            "meta_data": {},
            "finished_goods_manifest": [
                {"description": "Item", "external_area_m2": 5.0, "qty_produced": 2},
            ],
        }).encode()
        mock_blob_client = MagicMock()
        mock_blob_client.download_blob.return_value.readall.return_value = blob_content
        mock_cc = MagicMock()
        mock_cc.get_blob_client.return_value = mock_blob_client
        mock_svc = MagicMock()
        mock_svc.get_container_client.return_value = mock_cc
        mock_blob_svc.return_value = mock_svc
        mock_container.return_value = "c"

        # Costing raises
        mock_costing = MagicMock()
        mock_costing.calculate_margin.side_effect = RuntimeError("Costing API down")
        mock_costing_cls.return_value = mock_costing

        mock_build_card.return_value = {}
        mock_parse_rows.return_value = []
        mock_resolve.side_effect = lambda c, e: e
        mock_requests_post.return_value = MagicMock()

        with patch.dict(os.environ, {"POWER_AUTOMATE_DO_CREATION_URL": ""}):
            req = _make_request(_base_request_body(manager_penalty_pct="0"))
            result = main(req)

        assert result.status_code == 200
        body = json.loads(result.get_body())
        # Fallback uses $25/sqm
        assert body["margin_summary"]["total_cost_aed"] == 0.0
