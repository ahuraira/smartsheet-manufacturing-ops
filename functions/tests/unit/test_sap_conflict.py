"""
Unit Tests for SAP Conflict Resolution Flow
=============================================
Tests:
- MappingService.get_sap_conflicts() conflict detection
- build_sap_conflict_card() adaptive card structure
- fn_resolve_sap_conflict endpoint (approve + skip + idempotency)
"""

import pytest
import json
from unittest.mock import MagicMock, patch, ANY
from dataclasses import dataclass
from typing import Optional

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "fn_map_lookup"))


# ============== Mock CatalogEntry for tests ==============

@dataclass
class MockCatalogEntry:
    row_id: int
    sap_code: str
    canonical_code: str
    nesting_description: str = ""
    uom: Optional[str] = None
    sap_uom: Optional[str] = None
    conversion_factor: Optional[float] = None
    not_tracked: bool = False
    active: bool = True


# ============== Card Builder Tests ==============

@pytest.mark.unit
class TestBuildSAPConflictCard:

    def test_card_has_correct_structure(self):
        from shared.adaptive_card_builder import build_sap_conflict_card

        conflicts = {
            "CANON-001": {
                "entries": [
                    MockCatalogEntry(1, "SAP-A", "CANON-001", "Panel Type A"),
                    MockCatalogEntry(2, "SAP-B", "CANON-001", "Panel Type B"),
                ],
                "sap_description": "Insulation Panel 50mm",
                "default_sap_code": "SAP-A",
            },
        }

        card = build_sap_conflict_card(
            sap_reference="PTE-100",
            customer_name="Acme Corp",
            project_name="Project X",
            brand="KIMMCO",
            conflicts=conflicts,
            trace_id="test-trace",
        )

        assert card["type"] == "AdaptiveCard"
        assert card["version"] == "1.4"
        assert len(card["actions"]) == 2
        assert card["actions"][0]["data"]["action"] == "approve_sap_overrides"
        assert card["actions"][1]["data"]["action"] == "skip_sap_overrides"
        assert card["actions"][0]["data"]["sap_reference"] == "PTE-100"

    def test_card_has_choice_set_per_conflict(self):
        from shared.adaptive_card_builder import build_sap_conflict_card

        conflicts = {
            "CANON-001": {
                "entries": [
                    MockCatalogEntry(1, "SAP-A", "CANON-001", "Type A"),
                    MockCatalogEntry(2, "SAP-B", "CANON-001", "Type B"),
                ],
                "sap_description": "Panel 50mm",
                "default_sap_code": "SAP-A",
            },
            "CANON-002": {
                "entries": [
                    MockCatalogEntry(3, "SAP-C", "CANON-002", "Profile X"),
                    MockCatalogEntry(4, "SAP-D", "CANON-002", "Profile Y"),
                ],
                "sap_description": "Steel Profile",
                "default_sap_code": None,
            },
        }

        card = build_sap_conflict_card(
            sap_reference="PTE-200",
            customer_name="Test",
            project_name="Test",
            brand="WTI",
            conflicts=conflicts,
            trace_id="test",
        )

        # Find all ChoiceSet elements
        choice_sets = [b for b in card["body"] if b.get("type") == "Input.ChoiceSet"]
        assert len(choice_sets) == 2

        # First conflict should have default value set
        cs1 = next(cs for cs in choice_sets if cs["id"] == "conflict_CANON-001")
        assert cs1["value"] == "SAP-A"
        assert len(cs1["choices"]) == 2

        # Second conflict should have no default (default_sap_code is None)
        cs2 = next(cs for cs in choice_sets if cs["id"] == "conflict_CANON-002")
        assert "value" not in cs2
        assert len(cs2["choices"]) == 2

    def test_card_includes_sap_descriptions(self):
        from shared.adaptive_card_builder import build_sap_conflict_card

        conflicts = {
            "CANON-001": {
                "entries": [
                    MockCatalogEntry(1, "SAP-A", "CANON-001", "Type A"),
                    MockCatalogEntry(2, "SAP-B", "CANON-001", "Type B"),
                ],
                "sap_description": "Insulation Panel 50mm",
                "default_sap_code": None,
            },
        }

        card = build_sap_conflict_card(
            sap_reference="PTE-300",
            customer_name="Test",
            project_name="Test",
            brand="KIMMCO",
            conflicts=conflicts,
            trace_id="test",
        )

        # Find the TextBlock with the canonical code + description
        text_blocks = [b for b in card["body"] if b.get("type") == "TextBlock"]
        desc_block = [t for t in text_blocks if "CANON-001" in t.get("text", "") and "Insulation Panel" in t.get("text", "")]
        assert len(desc_block) == 1

    def test_card_caps_at_20_conflicts(self):
        from shared.adaptive_card_builder import build_sap_conflict_card

        conflicts = {}
        for i in range(25):
            conflicts[f"CANON-{i:03d}"] = {
                "entries": [
                    MockCatalogEntry(i * 2, f"SAP-{i}A", f"CANON-{i:03d}"),
                    MockCatalogEntry(i * 2 + 1, f"SAP-{i}B", f"CANON-{i:03d}"),
                ],
                "sap_description": f"Material {i}",
                "default_sap_code": None,
            }

        card = build_sap_conflict_card(
            sap_reference="PTE-400",
            customer_name="Test",
            project_name="Test",
            brand="KIMMCO",
            conflicts=conflicts,
            trace_id="test",
        )

        choice_sets = [b for b in card["body"] if b.get("type") == "Input.ChoiceSet"]
        assert len(choice_sets) == 20  # Capped

        # Should have overflow message
        overflow = [b for b in card["body"] if "more conflicts" in b.get("text", "")]
        assert len(overflow) == 1


# ============== fn_resolve_sap_conflict Endpoint Tests ==============

@pytest.mark.unit
class TestResolveSAPConflict:

    def _make_request(self, body: dict) -> MagicMock:
        req = MagicMock()
        req.get_json.return_value = body
        return req

    @patch("fn_resolve_sap_conflict.SmartsheetClient")
    @patch("fn_resolve_sap_conflict.get_manifest")
    @patch("fn_resolve_sap_conflict.log_user_action")
    def test_skip_returns_200(self, mock_log, mock_manifest, mock_client_cls):
        from fn_resolve_sap_conflict import main

        mock_client_cls.return_value = MagicMock()
        mock_manifest.return_value = MagicMock()

        req = self._make_request({
            "action": "skip_sap_overrides",
            "sap_reference": "PTE-100",
            "approver": "pm@test.com",
        })

        result = main(req)
        body = json.loads(result.get_body())

        assert result.status_code == 200
        assert body["status"] == "SKIPPED"

    @patch("fn_resolve_sap_conflict.SmartsheetClient")
    @patch("fn_resolve_sap_conflict.get_manifest")
    @patch("fn_resolve_sap_conflict.log_user_action")
    @patch("fn_resolve_sap_conflict.format_datetime_for_smartsheet")
    def test_approve_creates_overrides(self, mock_fmt, mock_log, mock_manifest, mock_client_cls):
        from fn_resolve_sap_conflict import main

        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_manifest.return_value = MagicMock()
        mock_fmt.return_value = "2026-03-23T00:00:00"

        # No existing overrides
        mock_client.find_row.return_value = None
        mock_client.add_row.return_value = {"id": 999}

        req = self._make_request({
            "action": "approve_sap_overrides",
            "sap_reference": "PTE-100",
            "approver": "pm@test.com",
            "conflict_CANON-001": "SAP-A",
            "conflict_CANON-002": "SAP-C",
        })

        result = main(req)
        body = json.loads(result.get_body())

        assert result.status_code == 200
        assert body["status"] == "OK"
        assert body["overrides_created"] == 2
        assert body["overrides_skipped"] == 0

        # Verify add_row called twice (one per conflict)
        assert mock_client.add_row.call_count == 2

    @patch("fn_resolve_sap_conflict.SmartsheetClient")
    @patch("fn_resolve_sap_conflict.get_manifest")
    @patch("fn_resolve_sap_conflict.log_user_action")
    def test_approve_idempotent(self, mock_log, mock_manifest, mock_client_cls):
        from fn_resolve_sap_conflict import main

        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_manifest.return_value = MagicMock()

        # Override already exists
        mock_client.find_row.return_value = {"row_id": 123, "OVERRIDE_ID": "OVR-PTE-100-CANON-001"}

        req = self._make_request({
            "action": "approve_sap_overrides",
            "sap_reference": "PTE-100",
            "approver": "pm@test.com",
            "conflict_CANON-001": "SAP-A",
        })

        result = main(req)
        body = json.loads(result.get_body())

        assert result.status_code == 200
        assert body["overrides_created"] == 0
        assert body["overrides_skipped"] == 1
        mock_client.add_row.assert_not_called()

    @patch("fn_resolve_sap_conflict.SmartsheetClient")
    @patch("fn_resolve_sap_conflict.get_manifest")
    def test_unknown_action_returns_400(self, mock_manifest, mock_client_cls):
        from fn_resolve_sap_conflict import main

        mock_client_cls.return_value = MagicMock()
        mock_manifest.return_value = MagicMock()

        req = self._make_request({
            "action": "invalid_action",
            "sap_reference": "PTE-100",
        })

        result = main(req)
        assert result.status_code == 400

    @patch("fn_resolve_sap_conflict.SmartsheetClient")
    @patch("fn_resolve_sap_conflict.get_manifest")
    def test_no_selections_returns_400(self, mock_manifest, mock_client_cls):
        from fn_resolve_sap_conflict import main

        mock_client_cls.return_value = MagicMock()
        mock_manifest.return_value = MagicMock()

        req = self._make_request({
            "action": "approve_sap_overrides",
            "sap_reference": "PTE-100",
            # No conflict_ keys
        })

        result = main(req)
        assert result.status_code == 400

    def test_invalid_json_returns_400(self):
        from fn_resolve_sap_conflict import main

        req = MagicMock()
        req.get_json.side_effect = ValueError("bad json")

        result = main(req)
        assert result.status_code == 400

    @patch("fn_resolve_sap_conflict.SmartsheetClient")
    @patch("fn_resolve_sap_conflict.get_manifest")
    @patch("fn_resolve_sap_conflict.log_user_action")
    @patch("fn_resolve_sap_conflict.format_datetime_for_smartsheet")
    def test_full_teams_response_format(self, mock_fmt, mock_log, mock_manifest, mock_client_cls):
        """Test that the full Teams card response body is accepted directly."""
        from fn_resolve_sap_conflict import main

        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_manifest.return_value = MagicMock()
        mock_fmt.return_value = "2026-03-24T00:00:00"
        mock_client.find_row.return_value = None
        mock_client.add_row.return_value = {"id": 999}

        # Exact format from Power Automate Teams card response
        req = self._make_request({
            "responseTime": "2026-03-24T05:32:20.9998295Z",
            "responder": {
                "objectId": "1bd54ec6-4c36-4736-870b-5f9413e874fd",
                "tenantId": "bcee2e31-22e2-46ec-8449-e6a922982362",
                "email": "abu.mukhtar@tte.ae",
                "userPrincipalName": "abu.mukhtar@tte.ae",
                "displayName": "Abu Huraira Mukhtar"
            },
            "submitActionId": "Approve Overrides",
            "data": {
                "conflict_CAN_CONS_AL_TAPE": "UL181AFST",
                "conflict_CAN_PANEL_FITTINGS": "CLIMNETO",
                "action": "approve_sap_overrides",
                "sap_reference": "99999999999.0",
                "trace_id": "trace-54cab8273951"
            }
        })

        result = main(req)
        body = json.loads(result.get_body())

        assert result.status_code == 200
        assert body["status"] == "OK"
        assert body["overrides_created"] == 2
        # sap_reference should have .0 stripped
        assert body["sap_reference"] == "99999999999"
        assert mock_client.add_row.call_count == 2

    @patch("fn_resolve_sap_conflict.SmartsheetClient")
    @patch("fn_resolve_sap_conflict.get_manifest")
    @patch("fn_resolve_sap_conflict.log_user_action")
    def test_sap_ref_float_cleanup(self, mock_log, mock_manifest, mock_client_cls):
        """Test that .0 suffix is stripped from sap_reference."""
        from fn_resolve_sap_conflict import main
        from shared.logical_names import Sheet, Column

        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_manifest.return_value = MagicMock()
        mock_client.find_row.return_value = None
        mock_client.add_row.return_value = {"id": 1}

        req = self._make_request({
            "action": "approve_sap_overrides",
            "sap_reference": "PTE-185.0",
            "approver": "pm@test.com",
            "conflict_CANON-001": "SAP-A",
        })

        result = main(req)
        body = json.loads(result.get_body())

        assert body["sap_reference"] == "PTE-185"
        # Override ID should use cleaned reference
        mock_client.find_row.assert_called_with(
            Sheet.MAPPING_OVERRIDE,
            Column.MAPPING_OVERRIDE.OVERRIDE_ID,
            "OVR-PTE-185-CANON-001"
        )
