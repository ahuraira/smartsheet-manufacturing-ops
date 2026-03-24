"""
Unit Tests for fn_resolve_sap_conflict
=======================================
Tests the SAP conflict resolution endpoint, including:
- Invalid JSON handling
- Skip action (no overrides created)
- Unknown action rejection
- Approve with no selections (400)
- Idempotency (existing override skipped)
- Happy path: override row creation in MAPPING_OVERRIDE
- Teams card response format ("data" wrapper)
- SAP reference float cleanup (trailing .0)
- Deterministic override ID generation
- Audit logging per override
- Exception creation on override failure
- Unhandled exception handling
"""

import pytest
import json
from datetime import datetime, date
from unittest.mock import MagicMock, patch, call, ANY

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
# Shared patch targets
# ---------------------------------------------------------------------------
MODULE = "fn_resolve_sap_conflict"

PATCH = {
    "client_cls": f"{MODULE}.SmartsheetClient",
    "get_manifest": f"{MODULE}.get_manifest",
    "log_action": f"{MODULE}.log_user_action",
    "create_exc": f"{MODULE}.create_exception",
    "resolve_email": f"{MODULE}.resolve_user_email",
    "now_uae": f"{MODULE}.now_uae",
    "fmt_dt": f"{MODULE}.format_datetime_for_smartsheet",
}


def _fixed_now():
    return datetime(2026, 3, 24, 14, 30, 0)


def _flat_payload(**overrides):
    """Return a valid flat approve payload."""
    body = {
        "action": "approve_sap_overrides",
        "sap_reference": "PTE-185",
        "approver": "pm@co.com",
        "conflict_MAT-001": "10003456",
        "conflict_MAT-002": "10007890",
    }
    body.update(overrides)
    return body


def _teams_card_payload(**data_overrides):
    """Return a valid Teams card response payload."""
    data = {
        "action": "approve_sap_overrides",
        "sap_reference": "PTE-185",
        "conflict_MAT-001": "10003456",
        "conflict_MAT-002": "10007890",
        "trace_id": "trace-abc123",
    }
    data.update(data_overrides)
    return {
        "responseTime": "2026-03-24T14:30:00Z",
        "responder": {
            "objectId": "user-obj-id",
            "tenantId": "tenant-id",
            "email": "pm@co.com",
            "userPrincipalName": "pm@co.com",
            "displayName": "PM User",
        },
        "submitActionId": "Approve Overrides",
        "data": data,
    }


# ===========================================================================
# Test class
# ===========================================================================


@pytest.mark.unit
class TestResolveSAPConflict:
    """Unit tests for fn_resolve_sap_conflict.main."""

    # -----------------------------------------------------------------------
    # 1. Invalid JSON
    # -----------------------------------------------------------------------
    def test_invalid_json_returns_400(self):
        from fn_resolve_sap_conflict import main

        req = _make_invalid_json_request()
        result = main(req)

        assert result.status_code == 400
        body = json.loads(result.get_body())
        assert "Invalid JSON" in body["error"]
        assert "trace_id" in body

    # -----------------------------------------------------------------------
    # 2. Skip action returns 200 SKIPPED
    # -----------------------------------------------------------------------
    @patch(PATCH["resolve_email"], side_effect=lambda c, e: e)
    @patch(PATCH["log_action"])
    @patch(PATCH["get_manifest"])
    @patch(PATCH["client_cls"])
    def test_skip_action_returns_200(self, mock_cls, mock_manifest, mock_log, mock_resolve):
        from fn_resolve_sap_conflict import main

        mock_cls.return_value = MagicMock()
        mock_manifest.return_value = MagicMock()

        req = _make_request({
            "action": "skip_sap_overrides",
            "sap_reference": "PTE-100",
            "approver": "pm@test.com",
        })

        result = main(req)
        body = json.loads(result.get_body())

        assert result.status_code == 200
        assert body["status"] == "SKIPPED"
        assert body["message"] == "Using default SAP codes"

    # -----------------------------------------------------------------------
    # 3. Skip action logs user action
    # -----------------------------------------------------------------------
    @patch(PATCH["resolve_email"], side_effect=lambda c, e: e)
    @patch(PATCH["log_action"])
    @patch(PATCH["get_manifest"])
    @patch(PATCH["client_cls"])
    def test_skip_action_logs_audit(self, mock_cls, mock_manifest, mock_log, mock_resolve):
        from fn_resolve_sap_conflict import main
        from shared.models import ActionType

        mock_cls.return_value = MagicMock()
        mock_manifest.return_value = MagicMock()

        req = _make_request({
            "action": "skip_sap_overrides",
            "sap_reference": "PTE-200",
            "approver": "pm@test.com",
        })

        main(req)

        mock_log.assert_called_once()
        call_kwargs = mock_log.call_args
        assert call_kwargs.kwargs.get("action_type") == ActionType.TAG_UPDATED or \
               (call_kwargs.args and call_kwargs.args[0] if call_kwargs.args else False)

    # -----------------------------------------------------------------------
    # 4. Unknown action returns 400
    # -----------------------------------------------------------------------
    @patch(PATCH["resolve_email"], side_effect=lambda c, e: e)
    @patch(PATCH["get_manifest"])
    @patch(PATCH["client_cls"])
    def test_unknown_action_returns_400(self, mock_cls, mock_manifest, mock_resolve):
        from fn_resolve_sap_conflict import main

        mock_cls.return_value = MagicMock()
        mock_manifest.return_value = MagicMock()

        req = _make_request({
            "action": "delete_all_overrides",
            "sap_reference": "PTE-100",
        })

        result = main(req)
        body = json.loads(result.get_body())

        assert result.status_code == 400
        assert "Unknown action" in body["error"]

    # -----------------------------------------------------------------------
    # 5. Approve with no selections returns 400
    # -----------------------------------------------------------------------
    @patch(PATCH["resolve_email"], side_effect=lambda c, e: e)
    @patch(PATCH["get_manifest"])
    @patch(PATCH["client_cls"])
    def test_no_selections_returns_400(self, mock_cls, mock_manifest, mock_resolve):
        from fn_resolve_sap_conflict import main

        mock_cls.return_value = MagicMock()
        mock_manifest.return_value = MagicMock()

        req = _make_request({
            "action": "approve_sap_overrides",
            "sap_reference": "PTE-100",
            "approver": "pm@test.com",
            # No conflict_ keys at all
        })

        result = main(req)
        body = json.loads(result.get_body())

        assert result.status_code == 400
        assert "No conflict selections" in body["error"]

    # -----------------------------------------------------------------------
    # 6. Approve with empty conflict value is ignored
    # -----------------------------------------------------------------------
    @patch(PATCH["resolve_email"], side_effect=lambda c, e: e)
    @patch(PATCH["get_manifest"])
    @patch(PATCH["client_cls"])
    def test_empty_conflict_values_ignored(self, mock_cls, mock_manifest, mock_resolve):
        from fn_resolve_sap_conflict import main

        mock_cls.return_value = MagicMock()
        mock_manifest.return_value = MagicMock()

        req = _make_request({
            "action": "approve_sap_overrides",
            "sap_reference": "PTE-100",
            "approver": "pm@test.com",
            "conflict_MAT-001": "",   # Empty -> filtered out
            "conflict_MAT-002": None,  # None -> filtered out
        })

        result = main(req)
        body = json.loads(result.get_body())

        assert result.status_code == 400
        assert "No conflict selections" in body["error"]

    # -----------------------------------------------------------------------
    # 7. Idempotency — existing override is skipped
    # -----------------------------------------------------------------------
    @patch(PATCH["resolve_email"], side_effect=lambda c, e: e)
    @patch(PATCH["fmt_dt"], return_value="2026-03-24T14:30:00")
    @patch(PATCH["now_uae"], side_effect=_fixed_now)
    @patch(PATCH["log_action"])
    @patch(PATCH["get_manifest"])
    @patch(PATCH["client_cls"])
    def test_existing_override_is_skipped(
        self, mock_cls, mock_manifest, mock_log, mock_now, mock_fmt, mock_resolve
    ):
        from fn_resolve_sap_conflict import main

        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_manifest.return_value = MagicMock()

        # Override already exists for this ID
        mock_client.find_row.return_value = {
            "row_id": 500,
            "OVERRIDE_ID": "OVR-PTE-185-MAT-001",
        }

        req = _make_request(_flat_payload(
            conflict_MAT_002=None,  # Remove second conflict so only MAT-001 remains
            **{"conflict_MAT-002": None},  # Ensure the key is explicitly None
        ))
        # Rebuild cleanly with a single conflict
        req = _make_request({
            "action": "approve_sap_overrides",
            "sap_reference": "PTE-185",
            "approver": "pm@co.com",
            "conflict_MAT-001": "10003456",
        })

        result = main(req)
        body = json.loads(result.get_body())

        assert result.status_code == 200
        assert body["overrides_created"] == 0
        assert body["overrides_skipped"] == 1
        mock_client.add_row.assert_not_called()

    # -----------------------------------------------------------------------
    # 8. Happy path — creates override rows
    # -----------------------------------------------------------------------
    @patch(PATCH["resolve_email"], side_effect=lambda c, e: e)
    @patch(PATCH["fmt_dt"], return_value="2026-03-24T14:30:00")
    @patch(PATCH["now_uae"], side_effect=_fixed_now)
    @patch(PATCH["log_action"])
    @patch(PATCH["get_manifest"])
    @patch(PATCH["client_cls"])
    def test_happy_path_creates_overrides(
        self, mock_cls, mock_manifest, mock_log, mock_now, mock_fmt, mock_resolve
    ):
        from fn_resolve_sap_conflict import main
        from shared.logical_names import Sheet, Column

        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_manifest.return_value = MagicMock()

        # No existing overrides
        mock_client.find_row.return_value = None
        mock_client.add_row.return_value = {"id": 999}

        req = _make_request(_flat_payload())
        result = main(req)
        body = json.loads(result.get_body())

        assert result.status_code == 200
        assert body["status"] == "OK"
        assert body["overrides_created"] == 2
        assert body["overrides_skipped"] == 0
        assert body["sap_reference"] == "PTE-185"

        # add_row called twice
        assert mock_client.add_row.call_count == 2

        # Verify the override IDs used in idempotency checks
        find_calls = mock_client.find_row.call_args_list
        override_ids_checked = [
            c.args[2] for c in find_calls
            if len(c.args) >= 3 and str(c.args[2]).startswith("OVR-")
        ]
        assert "OVR-PTE-185-MAT-001" in override_ids_checked
        assert "OVR-PTE-185-MAT-002" in override_ids_checked

    # -----------------------------------------------------------------------
    # 9. Override row contains correct column data
    # -----------------------------------------------------------------------
    @patch(PATCH["resolve_email"], side_effect=lambda c, e: e)
    @patch(PATCH["fmt_dt"], return_value="2026-03-24T14:30:00")
    @patch(PATCH["now_uae"], return_value=datetime(2026, 3, 24, 14, 30, 0))
    @patch(PATCH["log_action"])
    @patch(PATCH["get_manifest"])
    @patch(PATCH["client_cls"])
    def test_override_row_data_structure(
        self, mock_cls, mock_manifest, mock_log, mock_now, mock_fmt, mock_resolve
    ):
        from fn_resolve_sap_conflict import main
        from shared.logical_names import Column

        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_manifest.return_value = MagicMock()
        mock_client.find_row.return_value = None
        mock_client.add_row.return_value = {"id": 1}

        req = _make_request({
            "action": "approve_sap_overrides",
            "sap_reference": "PTE-300",
            "approver": "pm@co.com",
            "conflict_CAN_PANEL_50": "SAP-50MM",
        })

        main(req)

        # Inspect the row data passed to add_row
        add_call = mock_client.add_row.call_args
        row_data = add_call.args[1] if len(add_call.args) >= 2 else add_call.kwargs.get("row_data", {})

        assert row_data[Column.MAPPING_OVERRIDE.OVERRIDE_ID] == "OVR-PTE-300-CAN_PANEL_50"
        assert row_data[Column.MAPPING_OVERRIDE.SCOPE_TYPE] == "LPO"
        assert row_data[Column.MAPPING_OVERRIDE.SCOPE_VALUE] == "PTE-300"
        assert row_data[Column.MAPPING_OVERRIDE.CANONICAL_CODE] == "CAN_PANEL_50"
        assert row_data[Column.MAPPING_OVERRIDE.SAP_CODE] == "SAP-50MM"
        assert row_data[Column.MAPPING_OVERRIDE.ACTIVE] == "Yes"
        assert row_data[Column.MAPPING_OVERRIDE.CREATED_BY] == "pm@co.com"
        assert row_data[Column.MAPPING_OVERRIDE.CREATED_AT] == "2026-03-24T14:30:00"
        assert row_data[Column.MAPPING_OVERRIDE.EFFECTIVE_FROM] == "2026-03-24"

    # -----------------------------------------------------------------------
    # 10. Teams card response format ("data" wrapper)
    # -----------------------------------------------------------------------
    @patch(PATCH["resolve_email"], side_effect=lambda c, e: e)
    @patch(PATCH["fmt_dt"], return_value="2026-03-24T14:30:00")
    @patch(PATCH["now_uae"], side_effect=_fixed_now)
    @patch(PATCH["log_action"])
    @patch(PATCH["get_manifest"])
    @patch(PATCH["client_cls"])
    def test_teams_card_response_format(
        self, mock_cls, mock_manifest, mock_log, mock_now, mock_fmt, mock_resolve
    ):
        from fn_resolve_sap_conflict import main

        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_manifest.return_value = MagicMock()
        mock_client.find_row.return_value = None
        mock_client.add_row.return_value = {"id": 999}

        req = _make_request(_teams_card_payload())
        result = main(req)
        body = json.loads(result.get_body())

        assert result.status_code == 200
        assert body["status"] == "OK"
        assert body["overrides_created"] == 2

    # -----------------------------------------------------------------------
    # 11. Teams card extracts approver email from responder
    # -----------------------------------------------------------------------
    @patch(PATCH["resolve_email"], side_effect=lambda c, e: e)
    @patch(PATCH["fmt_dt"], return_value="2026-03-24T14:30:00")
    @patch(PATCH["now_uae"], side_effect=_fixed_now)
    @patch(PATCH["log_action"])
    @patch(PATCH["get_manifest"])
    @patch(PATCH["client_cls"])
    def test_teams_card_uses_responder_email(
        self, mock_cls, mock_manifest, mock_log, mock_now, mock_fmt, mock_resolve
    ):
        from fn_resolve_sap_conflict import main
        from shared.logical_names import Column

        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_manifest.return_value = MagicMock()
        mock_client.find_row.return_value = None
        mock_client.add_row.return_value = {"id": 1}

        payload = _teams_card_payload()
        payload["responder"]["email"] = "specific.pm@company.ae"

        req = _make_request(payload)
        main(req)

        # The CREATED_BY in the override row should use the responder email
        add_call = mock_client.add_row.call_args
        row_data = add_call.args[1] if len(add_call.args) >= 2 else {}
        assert row_data[Column.MAPPING_OVERRIDE.CREATED_BY] == "specific.pm@company.ae"

    # -----------------------------------------------------------------------
    # 12. Teams card falls back to userPrincipalName when email missing
    # -----------------------------------------------------------------------
    @patch(PATCH["resolve_email"], side_effect=lambda c, e: e)
    @patch(PATCH["fmt_dt"], return_value="2026-03-24T14:30:00")
    @patch(PATCH["now_uae"], side_effect=_fixed_now)
    @patch(PATCH["log_action"])
    @patch(PATCH["get_manifest"])
    @patch(PATCH["client_cls"])
    def test_teams_card_fallback_to_upn(
        self, mock_cls, mock_manifest, mock_log, mock_now, mock_fmt, mock_resolve
    ):
        from fn_resolve_sap_conflict import main

        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_manifest.return_value = MagicMock()
        mock_client.find_row.return_value = None
        mock_client.add_row.return_value = {"id": 1}

        payload = _teams_card_payload()
        payload["responder"]["email"] = ""  # Empty email
        payload["responder"]["userPrincipalName"] = "upn.user@company.ae"
        payload["data"]["conflict_MAT-002"] = None  # Only one conflict
        # Rebuild with single conflict to simplify
        payload["data"] = {
            "action": "approve_sap_overrides",
            "sap_reference": "PTE-185",
            "conflict_MAT-001": "10003456",
        }

        req = _make_request(payload)
        main(req)

        # resolve_user_email should have been called with the UPN
        mock_resolve.assert_called_with(mock_client, "upn.user@company.ae")

    # -----------------------------------------------------------------------
    # 13. SAP reference float cleanup — trailing .0 stripped
    # -----------------------------------------------------------------------
    @patch(PATCH["resolve_email"], side_effect=lambda c, e: e)
    @patch(PATCH["fmt_dt"], return_value="2026-03-24T14:30:00")
    @patch(PATCH["now_uae"], side_effect=_fixed_now)
    @patch(PATCH["log_action"])
    @patch(PATCH["get_manifest"])
    @patch(PATCH["client_cls"])
    def test_sap_ref_float_cleanup(
        self, mock_cls, mock_manifest, mock_log, mock_now, mock_fmt, mock_resolve
    ):
        from fn_resolve_sap_conflict import main
        from shared.logical_names import Sheet, Column

        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_manifest.return_value = MagicMock()
        mock_client.find_row.return_value = None
        mock_client.add_row.return_value = {"id": 1}

        req = _make_request({
            "action": "approve_sap_overrides",
            "sap_reference": "12345.0",
            "approver": "pm@co.com",
            "conflict_MAT-001": "SAP-A",
        })

        result = main(req)
        body = json.loads(result.get_body())

        # Response should have cleaned reference
        assert body["sap_reference"] == "12345"

        # Idempotency check should use cleaned reference
        mock_client.find_row.assert_called_with(
            Sheet.MAPPING_OVERRIDE,
            Column.MAPPING_OVERRIDE.OVERRIDE_ID,
            "OVR-12345-MAT-001",
        )

    # -----------------------------------------------------------------------
    # 14. SAP reference without dot is unchanged
    # -----------------------------------------------------------------------
    @patch(PATCH["resolve_email"], side_effect=lambda c, e: e)
    @patch(PATCH["fmt_dt"], return_value="2026-03-24T14:30:00")
    @patch(PATCH["now_uae"], side_effect=_fixed_now)
    @patch(PATCH["log_action"])
    @patch(PATCH["get_manifest"])
    @patch(PATCH["client_cls"])
    def test_sap_ref_without_dot_unchanged(
        self, mock_cls, mock_manifest, mock_log, mock_now, mock_fmt, mock_resolve
    ):
        from fn_resolve_sap_conflict import main

        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_manifest.return_value = MagicMock()
        mock_client.find_row.return_value = None
        mock_client.add_row.return_value = {"id": 1}

        req = _make_request({
            "action": "approve_sap_overrides",
            "sap_reference": "PTE-185",
            "approver": "pm@co.com",
            "conflict_MAT-001": "SAP-A",
        })

        result = main(req)
        body = json.loads(result.get_body())
        assert body["sap_reference"] == "PTE-185"

    # -----------------------------------------------------------------------
    # 15. Large numeric SAP reference float cleanup
    # -----------------------------------------------------------------------
    @patch(PATCH["resolve_email"], side_effect=lambda c, e: e)
    @patch(PATCH["fmt_dt"], return_value="2026-03-24T14:30:00")
    @patch(PATCH["now_uae"], side_effect=_fixed_now)
    @patch(PATCH["log_action"])
    @patch(PATCH["get_manifest"])
    @patch(PATCH["client_cls"])
    def test_large_numeric_sap_ref_cleanup(
        self, mock_cls, mock_manifest, mock_log, mock_now, mock_fmt, mock_resolve
    ):
        from fn_resolve_sap_conflict import main

        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_manifest.return_value = MagicMock()
        mock_client.find_row.return_value = None
        mock_client.add_row.return_value = {"id": 1}

        req = _make_request({
            "action": "approve_sap_overrides",
            "sap_reference": "99999999999.0",
            "approver": "pm@co.com",
            "conflict_CAN_001": "SAP-X",
        })

        result = main(req)
        body = json.loads(result.get_body())
        assert body["sap_reference"] == "99999999999"

    # -----------------------------------------------------------------------
    # 16. Deterministic override ID format
    # -----------------------------------------------------------------------
    @patch(PATCH["resolve_email"], side_effect=lambda c, e: e)
    @patch(PATCH["fmt_dt"], return_value="2026-03-24T14:30:00")
    @patch(PATCH["now_uae"], side_effect=_fixed_now)
    @patch(PATCH["log_action"])
    @patch(PATCH["get_manifest"])
    @patch(PATCH["client_cls"])
    def test_deterministic_override_id(
        self, mock_cls, mock_manifest, mock_log, mock_now, mock_fmt, mock_resolve
    ):
        from fn_resolve_sap_conflict import main
        from shared.logical_names import Sheet, Column

        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_manifest.return_value = MagicMock()
        mock_client.find_row.return_value = None
        mock_client.add_row.return_value = {"id": 1}

        req = _make_request({
            "action": "approve_sap_overrides",
            "sap_reference": "PTE-500",
            "approver": "pm@co.com",
            "conflict_CANON_INSUL_50": "SAP-INSUL",
        })

        main(req)

        # find_row should check for OVR-PTE-500-CANON_INSUL_50
        mock_client.find_row.assert_called_with(
            Sheet.MAPPING_OVERRIDE,
            Column.MAPPING_OVERRIDE.OVERRIDE_ID,
            "OVR-PTE-500-CANON_INSUL_50",
        )

    # -----------------------------------------------------------------------
    # 17. Audit log_user_action called per created override
    # -----------------------------------------------------------------------
    @patch(PATCH["resolve_email"], side_effect=lambda c, e: e)
    @patch(PATCH["fmt_dt"], return_value="2026-03-24T14:30:00")
    @patch(PATCH["now_uae"], side_effect=_fixed_now)
    @patch(PATCH["log_action"])
    @patch(PATCH["get_manifest"])
    @patch(PATCH["client_cls"])
    def test_audit_log_per_override(
        self, mock_cls, mock_manifest, mock_log, mock_now, mock_fmt, mock_resolve
    ):
        from fn_resolve_sap_conflict import main
        from shared.models import ActionType

        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_manifest.return_value = MagicMock()
        mock_client.find_row.return_value = None
        mock_client.add_row.return_value = {"id": 1}

        req = _make_request(_flat_payload())
        main(req)

        # 2 overrides created -> 2 log_user_action calls
        assert mock_log.call_count == 2

        # Each call should have OVERRIDE_CREATED action type
        for call_item in mock_log.call_args_list:
            kwargs = call_item.kwargs
            assert kwargs.get("action_type") == ActionType.OVERRIDE_CREATED
            assert kwargs.get("target_table") == "MAPPING_OVERRIDE"
            assert kwargs.get("user_id") == "pm@co.com"

    # -----------------------------------------------------------------------
    # 18. No audit log for skipped (idempotent) overrides
    # -----------------------------------------------------------------------
    @patch(PATCH["resolve_email"], side_effect=lambda c, e: e)
    @patch(PATCH["fmt_dt"], return_value="2026-03-24T14:30:00")
    @patch(PATCH["now_uae"], side_effect=_fixed_now)
    @patch(PATCH["log_action"])
    @patch(PATCH["get_manifest"])
    @patch(PATCH["client_cls"])
    def test_no_audit_log_for_skipped_overrides(
        self, mock_cls, mock_manifest, mock_log, mock_now, mock_fmt, mock_resolve
    ):
        from fn_resolve_sap_conflict import main

        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_manifest.return_value = MagicMock()

        # All overrides already exist
        mock_client.find_row.return_value = {"row_id": 100}

        req = _make_request(_flat_payload())
        main(req)

        # No new overrides -> no log_user_action calls
        mock_log.assert_not_called()

    # -----------------------------------------------------------------------
    # 19. Exception created on add_row failure
    # -----------------------------------------------------------------------
    @patch(PATCH["resolve_email"], side_effect=lambda c, e: e)
    @patch(PATCH["fmt_dt"], return_value="2026-03-24T14:30:00")
    @patch(PATCH["now_uae"], side_effect=_fixed_now)
    @patch(PATCH["create_exc"])
    @patch(PATCH["log_action"])
    @patch(PATCH["get_manifest"])
    @patch(PATCH["client_cls"])
    def test_add_row_failure_creates_exception(
        self, mock_cls, mock_manifest, mock_log, mock_exc, mock_now, mock_fmt, mock_resolve
    ):
        from fn_resolve_sap_conflict import main

        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_manifest.return_value = MagicMock()
        mock_client.find_row.return_value = None
        mock_client.add_row.side_effect = RuntimeError("API timeout")

        req = _make_request({
            "action": "approve_sap_overrides",
            "sap_reference": "PTE-185",
            "approver": "pm@co.com",
            "conflict_MAT-001": "SAP-X",
        })

        result = main(req)
        body = json.loads(result.get_body())

        # Function still returns 200 (partial success) with 0 created
        assert result.status_code == 200
        assert body["overrides_created"] == 0

        # Exception record was created
        mock_exc.assert_called_once()

    # -----------------------------------------------------------------------
    # 20. Mixed success: some created, some skipped
    # -----------------------------------------------------------------------
    @patch(PATCH["resolve_email"], side_effect=lambda c, e: e)
    @patch(PATCH["fmt_dt"], return_value="2026-03-24T14:30:00")
    @patch(PATCH["now_uae"], side_effect=_fixed_now)
    @patch(PATCH["log_action"])
    @patch(PATCH["get_manifest"])
    @patch(PATCH["client_cls"])
    def test_mixed_created_and_skipped(
        self, mock_cls, mock_manifest, mock_log, mock_now, mock_fmt, mock_resolve
    ):
        from fn_resolve_sap_conflict import main

        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_manifest.return_value = MagicMock()

        # First conflict exists, second is new
        mock_client.find_row.side_effect = [
            {"row_id": 100, "OVERRIDE_ID": "OVR-PTE-185-MAT-001"},  # Exists
            None,  # New
        ]
        mock_client.add_row.return_value = {"id": 999}

        req = _make_request(_flat_payload())
        result = main(req)
        body = json.loads(result.get_body())

        assert body["overrides_created"] == 1
        assert body["overrides_skipped"] == 1

        # Only one add_row call (the new one)
        assert mock_client.add_row.call_count == 1

    # -----------------------------------------------------------------------
    # 21. Unhandled exception returns 500 and creates exception record
    # -----------------------------------------------------------------------
    @patch(PATCH["create_exc"])
    @patch(PATCH["resolve_email"], side_effect=lambda c, e: e)
    @patch(PATCH["get_manifest"])
    @patch(PATCH["client_cls"])
    def test_unhandled_exception_returns_500(
        self, mock_cls, mock_manifest, mock_resolve, mock_exc
    ):
        from fn_resolve_sap_conflict import main

        # First SmartsheetClient() call raises; fallback in except block succeeds
        fallback_client = MagicMock()
        mock_cls.side_effect = [RuntimeError("Connection failed"), fallback_client]

        req = _make_request(_flat_payload())
        result = main(req)
        body = json.loads(result.get_body())

        assert result.status_code == 500
        assert "Connection failed" in body["error"]
        assert "trace_id" in body

        # Exception record should be created via fallback client
        mock_exc.assert_called_once()

    # -----------------------------------------------------------------------
    # 22. Flat payload uses "approver" field
    # -----------------------------------------------------------------------
    @patch(PATCH["resolve_email"], side_effect=lambda c, e: e)
    @patch(PATCH["log_action"])
    @patch(PATCH["get_manifest"])
    @patch(PATCH["client_cls"])
    def test_flat_payload_uses_approver_field(
        self, mock_cls, mock_manifest, mock_log, mock_resolve
    ):
        from fn_resolve_sap_conflict import main

        mock_cls.return_value = MagicMock()
        mock_manifest.return_value = MagicMock()

        req = _make_request({
            "action": "skip_sap_overrides",
            "sap_reference": "PTE-100",
            "approver": "flat.approver@co.com",
        })

        main(req)

        # resolve_user_email should have been called with the flat approver
        mock_resolve.assert_called_with(ANY, "flat.approver@co.com")

    # -----------------------------------------------------------------------
    # 23. Missing approver defaults to "system"
    # -----------------------------------------------------------------------
    @patch(PATCH["resolve_email"], side_effect=lambda c, e: e)
    @patch(PATCH["log_action"])
    @patch(PATCH["get_manifest"])
    @patch(PATCH["client_cls"])
    def test_missing_approver_defaults_to_system(
        self, mock_cls, mock_manifest, mock_log, mock_resolve
    ):
        from fn_resolve_sap_conflict import main

        mock_cls.return_value = MagicMock()
        mock_manifest.return_value = MagicMock()

        req = _make_request({
            "action": "skip_sap_overrides",
            "sap_reference": "PTE-100",
            # No "approver" key
        })

        main(req)

        mock_resolve.assert_called_with(ANY, "system")

    # -----------------------------------------------------------------------
    # 24. Multiple conflicts all new — correct count
    # -----------------------------------------------------------------------
    @patch(PATCH["resolve_email"], side_effect=lambda c, e: e)
    @patch(PATCH["fmt_dt"], return_value="2026-03-24T14:30:00")
    @patch(PATCH["now_uae"], side_effect=_fixed_now)
    @patch(PATCH["log_action"])
    @patch(PATCH["get_manifest"])
    @patch(PATCH["client_cls"])
    def test_multiple_new_conflicts(
        self, mock_cls, mock_manifest, mock_log, mock_now, mock_fmt, mock_resolve
    ):
        from fn_resolve_sap_conflict import main

        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_manifest.return_value = MagicMock()
        mock_client.find_row.return_value = None
        mock_client.add_row.return_value = {"id": 1}

        req = _make_request({
            "action": "approve_sap_overrides",
            "sap_reference": "PTE-600",
            "approver": "pm@co.com",
            "conflict_A": "SAP-A",
            "conflict_B": "SAP-B",
            "conflict_C": "SAP-C",
            "conflict_D": "SAP-D",
        })

        result = main(req)
        body = json.loads(result.get_body())

        assert body["overrides_created"] == 4
        assert body["overrides_skipped"] == 0
        assert mock_client.add_row.call_count == 4
        assert mock_log.call_count == 4

    # -----------------------------------------------------------------------
    # 25. Non-conflict keys are ignored
    # -----------------------------------------------------------------------
    @patch(PATCH["resolve_email"], side_effect=lambda c, e: e)
    @patch(PATCH["fmt_dt"], return_value="2026-03-24T14:30:00")
    @patch(PATCH["now_uae"], side_effect=_fixed_now)
    @patch(PATCH["log_action"])
    @patch(PATCH["get_manifest"])
    @patch(PATCH["client_cls"])
    def test_non_conflict_keys_ignored(
        self, mock_cls, mock_manifest, mock_log, mock_now, mock_fmt, mock_resolve
    ):
        from fn_resolve_sap_conflict import main

        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_manifest.return_value = MagicMock()
        mock_client.find_row.return_value = None
        mock_client.add_row.return_value = {"id": 1}

        req = _make_request({
            "action": "approve_sap_overrides",
            "sap_reference": "PTE-700",
            "approver": "pm@co.com",
            "conflict_MAT-001": "SAP-A",
            "some_other_key": "ignored",
            "trace_id": "also-ignored",
            "random": 42,
        })

        result = main(req)
        body = json.loads(result.get_body())

        # Only one conflict_ key -> only one override
        assert body["overrides_created"] == 1
        assert mock_client.add_row.call_count == 1
