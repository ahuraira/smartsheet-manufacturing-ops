"""
Unit Tests for SAPConflictService
==================================
Tests:
- __init__ sets client and manifest
- check_and_notify_conflicts:
  - No conflicts detected => returns None, no card sent
  - Conflicts found => builds card, dispatches via webhook, returns card JSON
  - Webhook URL from env var
  - Webhook URL fallback from CONFIG sheet
  - Webhook not configured => logs warning, still returns card
  - Webhook POST failure => creates exception, still returns card
  - Enriches conflicts with SAP descriptions
  - Fetches LPO details for card display
  - Logs user action (SAP_CONFLICT_DETECTED)
  - LPO fetch failure => continues with empty lpo_details
  - Card dispatch failure => creates exception record
"""

import pytest
from unittest.mock import MagicMock, patch
from dataclasses import dataclass
from typing import Optional

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.logical_names import Sheet, Column
from shared.models import ActionType, ReasonCode, ExceptionSeverity, ExceptionSource


# ============== Mock CatalogEntry ==============

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


# ============== Helpers ==============

SAMPLE_CONFLICTS = {
    "CANON-001": [
        MockCatalogEntry(1, "SAP-A", "CANON-001", "Panel Type A"),
        MockCatalogEntry(2, "SAP-B", "CANON-001", "Panel Type B"),
    ],
    "CANON-002": [
        MockCatalogEntry(3, "SAP-C", "CANON-002", "Profile X"),
        MockCatalogEntry(4, "SAP-D", "CANON-002", "Profile Y"),
    ],
}

SAMPLE_CARD = {"type": "AdaptiveCard", "version": "1.4", "body": [], "actions": []}

WEBHOOK_URL = "https://prod-00.westus.logic.azure.com/workflows/test"


def _make_lpo_row(manifest):
    """Build a mock LPO row dict with physical column names."""
    col = lambda c: manifest.get_column_name(Sheet.LPO_MASTER, c)
    return {
        "row_id": 5001,
        col(Column.LPO_MASTER.CUSTOMER_LPO_REF): "CUST-REF-001",
        col(Column.LPO_MASTER.PO_QUANTITY_SQM): "500",
        col(Column.LPO_MASTER.PO_VALUE): "25000",
        col(Column.LPO_MASTER.PRICE_PER_SQM): "50",
        col(Column.LPO_MASTER.WASTAGE_CONSIDERED_IN_COSTING): "5",
        col(Column.LPO_MASTER.PLANNED_GM_PCT): "12.5",
    }


def _make_config_row(manifest, value):
    """Build a mock CONFIG row dict."""
    col_val = manifest.get_column_name(Sheet.CONFIG, Column.CONFIG.CONFIG_VALUE)
    return {"row_id": 9001, col_val: value}


# ============== Test: __init__ ==============

@pytest.mark.unit
class TestSAPConflictServiceInit:

    @patch("shared.sap_conflict_service.get_manifest")
    def test_init_sets_client_and_manifest(self, mock_get_manifest):
        from shared.sap_conflict_service import SAPConflictService

        mock_manifest = MagicMock()
        mock_get_manifest.return_value = mock_manifest
        mock_client = MagicMock()

        service = SAPConflictService(mock_client)

        assert service.client is mock_client
        assert service.manifest is mock_manifest


# ============== Test: No Conflicts ==============

@pytest.mark.unit
class TestNoConflictsDetected:

    @patch("shared.sap_conflict_service.log_user_action")
    @patch("shared.sap_conflict_service.build_sap_conflict_card")
    @patch("shared.sap_conflict_service.get_manifest")
    def test_no_conflicts_returns_none(self, mock_get_manifest, mock_build_card, mock_log_action):
        from shared.sap_conflict_service import SAPConflictService

        mock_manifest = MagicMock()
        mock_get_manifest.return_value = mock_manifest
        mock_client = MagicMock()

        service = SAPConflictService(mock_client)

        with patch("shared.sap_conflict_service.MappingService", create=True) as mock_ms_cls:
            # Patch the lazy import path
            mock_ms = MagicMock()
            mock_ms.get_sap_conflicts.return_value = {}

            with patch.dict("sys.modules", {"fn_map_lookup": MagicMock(), "fn_map_lookup.mapping_service": MagicMock(MappingService=MagicMock(return_value=mock_ms))}):
                result = service.check_and_notify_conflicts(
                    sap_reference="PTE-100",
                    customer_name="Acme",
                    project_name="Project X",
                    brand="KIMMCO",
                    trace_id="trace-001",
                )

        assert result is None
        mock_build_card.assert_not_called()
        mock_log_action.assert_not_called()

    @patch("shared.sap_conflict_service.log_user_action")
    @patch("shared.sap_conflict_service.build_sap_conflict_card")
    @patch("shared.sap_conflict_service.get_manifest")
    def test_no_conflicts_returns_none_with_none_value(self, mock_get_manifest, mock_build_card, mock_log_action):
        """get_sap_conflicts returning None (instead of empty dict) should also be handled."""
        from shared.sap_conflict_service import SAPConflictService

        mock_get_manifest.return_value = MagicMock()
        mock_client = MagicMock()

        service = SAPConflictService(mock_client)

        with patch.dict("sys.modules", {"fn_map_lookup": MagicMock(), "fn_map_lookup.mapping_service": MagicMock(MappingService=MagicMock(return_value=MagicMock(get_sap_conflicts=MagicMock(return_value=None))))}):
            result = service.check_and_notify_conflicts(
                sap_reference="PTE-100",
                customer_name="Acme",
                project_name="Project X",
                brand="KIMMCO",
                trace_id="trace-002",
            )

        assert result is None
        mock_build_card.assert_not_called()


# ============== Test: Happy Path (Conflicts Found) ==============

@pytest.mark.unit
class TestConflictsFound:

    def _create_service_with_conflicts(self, mock_get_manifest, mock_client, conflicts):
        """Helper to set up a service with canned MappingService."""
        from shared.sap_conflict_service import SAPConflictService

        mock_manifest = MagicMock()
        mock_manifest.get_column_name = MagicMock(side_effect=lambda s, c: f"{s}.{c}")
        mock_get_manifest.return_value = mock_manifest

        mock_ms = MagicMock()
        mock_ms.get_sap_conflicts.return_value = conflicts
        mock_ms.get_material_description.side_effect = lambda cc: f"Description for {cc}"
        mock_ms.get_default_sap_code.side_effect = lambda cc: f"DEFAULT-{cc}"

        return SAPConflictService(mock_client), mock_ms

    @patch("shared.sap_conflict_service.requests")
    @patch("shared.sap_conflict_service.log_user_action")
    @patch("shared.sap_conflict_service.create_exception")
    @patch("shared.sap_conflict_service.build_sap_conflict_card", return_value=SAMPLE_CARD)
    @patch("shared.sap_conflict_service.get_manifest")
    @patch.dict(os.environ, {"POWER_AUTOMATE_SAP_CONFLICT_URL": WEBHOOK_URL})
    def test_conflicts_found_builds_and_dispatches_card(
        self, mock_get_manifest, mock_build_card, mock_create_exc, mock_log_action, mock_requests
    ):
        mock_client = MagicMock()
        mock_client.find_row.return_value = None  # No LPO row
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_requests.post.return_value = mock_resp

        service, mock_ms = self._create_service_with_conflicts(
            mock_get_manifest, mock_client, SAMPLE_CONFLICTS
        )

        with patch.dict("sys.modules", {"fn_map_lookup": MagicMock(), "fn_map_lookup.mapping_service": MagicMock(MappingService=MagicMock(return_value=mock_ms))}):
            result = service.check_and_notify_conflicts(
                sap_reference="PTE-200",
                customer_name="Test Corp",
                project_name="Big Project",
                brand="WTI",
                trace_id="trace-happy",
            )

        assert result == SAMPLE_CARD

        # Card builder called with enriched conflicts
        mock_build_card.assert_called_once()
        card_kwargs = mock_build_card.call_args
        enriched = card_kwargs[1].get("conflicts") or card_kwargs[0][4]
        assert "CANON-001" in enriched
        assert "CANON-002" in enriched

        # Webhook dispatched
        mock_requests.post.assert_called_once()
        post_kwargs = mock_requests.post.call_args
        assert post_kwargs[0][0] == WEBHOOK_URL
        payload = post_kwargs[1]["json"]
        assert payload["sap_reference"] == "PTE-200"
        assert payload["conflict_count"] == 2
        assert payload["trace_id"] == "trace-happy"

        # User action logged
        mock_log_action.assert_called_once()
        log_kwargs = mock_log_action.call_args[1]
        assert log_kwargs["action_type"] == ActionType.SAP_CONFLICT_DETECTED
        assert log_kwargs["target_id"] == "PTE-200"

    @patch("shared.sap_conflict_service.requests")
    @patch("shared.sap_conflict_service.log_user_action")
    @patch("shared.sap_conflict_service.create_exception")
    @patch("shared.sap_conflict_service.build_sap_conflict_card", return_value=SAMPLE_CARD)
    @patch("shared.sap_conflict_service.get_manifest")
    @patch.dict(os.environ, {"POWER_AUTOMATE_SAP_CONFLICT_URL": WEBHOOK_URL})
    def test_enriches_conflicts_with_descriptions(
        self, mock_get_manifest, mock_build_card, mock_create_exc, mock_log_action, mock_requests
    ):
        mock_client = MagicMock()
        mock_client.find_row.return_value = None
        mock_requests.post.return_value = MagicMock(raise_for_status=MagicMock())

        service, mock_ms = self._create_service_with_conflicts(
            mock_get_manifest, mock_client, SAMPLE_CONFLICTS
        )

        with patch.dict("sys.modules", {"fn_map_lookup": MagicMock(), "fn_map_lookup.mapping_service": MagicMock(MappingService=MagicMock(return_value=mock_ms))}):
            service.check_and_notify_conflicts(
                sap_reference="PTE-300",
                customer_name="Test",
                project_name="Test",
                brand="KIMMCO",
                trace_id="trace-enrich",
            )

        # Check that build_sap_conflict_card received enriched data
        card_call = mock_build_card.call_args
        enriched = card_call[1].get("conflicts") or card_call[0][4]
        assert enriched["CANON-001"]["sap_description"] == "Description for CANON-001"
        assert enriched["CANON-001"]["default_sap_code"] == "DEFAULT-CANON-001"
        assert enriched["CANON-002"]["sap_description"] == "Description for CANON-002"

        # MappingService methods called for each canonical code
        assert mock_ms.get_material_description.call_count == 2
        assert mock_ms.get_default_sap_code.call_count == 2


# ============== Test: LPO Details ==============

@pytest.mark.unit
class TestLPODetailsFetch:

    @patch("shared.sap_conflict_service.requests")
    @patch("shared.sap_conflict_service.log_user_action")
    @patch("shared.sap_conflict_service.create_exception")
    @patch("shared.sap_conflict_service.build_sap_conflict_card", return_value=SAMPLE_CARD)
    @patch("shared.sap_conflict_service.get_manifest")
    @patch.dict(os.environ, {"POWER_AUTOMATE_SAP_CONFLICT_URL": WEBHOOK_URL})
    def test_fetches_lpo_details_for_card(
        self, mock_get_manifest, mock_build_card, mock_create_exc, mock_log_action, mock_requests
    ):
        from shared.sap_conflict_service import SAPConflictService
        from tests.conftest import MockWorkspaceManifest

        mock_manifest = MockWorkspaceManifest()
        mock_get_manifest.return_value = mock_manifest

        lpo_row = _make_lpo_row(mock_manifest)
        mock_client = MagicMock()
        mock_client.find_row.return_value = lpo_row
        mock_requests.post.return_value = MagicMock(raise_for_status=MagicMock())

        mock_ms = MagicMock()
        mock_ms.get_sap_conflicts.return_value = {"CANON-001": [MockCatalogEntry(1, "SAP-A", "CANON-001")]}
        mock_ms.get_material_description.return_value = "Panel"
        mock_ms.get_default_sap_code.return_value = "SAP-A"

        service = SAPConflictService(mock_client)

        with patch.dict("sys.modules", {"fn_map_lookup": MagicMock(), "fn_map_lookup.mapping_service": MagicMock(MappingService=MagicMock(return_value=mock_ms))}):
            result = service.check_and_notify_conflicts(
                sap_reference="PTE-400",
                customer_name="Test",
                project_name="Test",
                brand="KIMMCO",
                trace_id="trace-lpo",
            )

        assert result == SAMPLE_CARD

        # find_row should have been called for LPO lookup
        mock_client.find_row.assert_called_with(
            Sheet.LPO_MASTER, Column.LPO_MASTER.SAP_REFERENCE, "PTE-400"
        )

        # Card builder should receive lpo_details with numeric fields
        card_call = mock_build_card.call_args
        lpo_details = card_call[1].get("lpo_details") or card_call[0][5]
        assert lpo_details["customer_lpo_ref"] == "CUST-REF-001"
        assert lpo_details["po_quantity_sqm"] == 500.0
        assert lpo_details["po_value"] == 25000.0

    @patch("shared.sap_conflict_service.requests")
    @patch("shared.sap_conflict_service.log_user_action")
    @patch("shared.sap_conflict_service.create_exception")
    @patch("shared.sap_conflict_service.build_sap_conflict_card", return_value=SAMPLE_CARD)
    @patch("shared.sap_conflict_service.get_manifest")
    @patch.dict(os.environ, {"POWER_AUTOMATE_SAP_CONFLICT_URL": WEBHOOK_URL})
    def test_lpo_fetch_failure_continues_with_empty_details(
        self, mock_get_manifest, mock_build_card, mock_create_exc, mock_log_action, mock_requests
    ):
        from shared.sap_conflict_service import SAPConflictService

        mock_manifest = MagicMock()
        mock_manifest.get_column_name = MagicMock(return_value="SomeCol")
        mock_get_manifest.return_value = mock_manifest

        mock_client = MagicMock()
        mock_client.find_row.side_effect = RuntimeError("Smartsheet API timeout")
        mock_requests.post.return_value = MagicMock(raise_for_status=MagicMock())

        mock_ms = MagicMock()
        mock_ms.get_sap_conflicts.return_value = {"CANON-001": [MockCatalogEntry(1, "SAP-A", "CANON-001")]}
        mock_ms.get_material_description.return_value = "Panel"
        mock_ms.get_default_sap_code.return_value = None

        service = SAPConflictService(mock_client)

        with patch.dict("sys.modules", {"fn_map_lookup": MagicMock(), "fn_map_lookup.mapping_service": MagicMock(MappingService=MagicMock(return_value=mock_ms))}):
            result = service.check_and_notify_conflicts(
                sap_reference="PTE-500",
                customer_name="Test",
                project_name="Test",
                brand="WTI",
                trace_id="trace-lpo-fail",
            )

        # Should still return card (not None), just with empty lpo_details
        assert result == SAMPLE_CARD
        mock_build_card.assert_called_once()

        # lpo_details should be empty dict (exception caught)
        card_call = mock_build_card.call_args
        # lpo_details may be passed as kwarg or positional; empty dict is falsy so use 'in'
        if "lpo_details" in card_call[1]:
            lpo_details = card_call[1]["lpo_details"]
        else:
            lpo_details = card_call[0][5]
        assert lpo_details == {}

    @patch("shared.sap_conflict_service.requests")
    @patch("shared.sap_conflict_service.log_user_action")
    @patch("shared.sap_conflict_service.create_exception")
    @patch("shared.sap_conflict_service.build_sap_conflict_card", return_value=SAMPLE_CARD)
    @patch("shared.sap_conflict_service.get_manifest")
    @patch.dict(os.environ, {"POWER_AUTOMATE_SAP_CONFLICT_URL": WEBHOOK_URL})
    def test_lpo_row_not_found_gives_empty_details(
        self, mock_get_manifest, mock_build_card, mock_create_exc, mock_log_action, mock_requests
    ):
        from shared.sap_conflict_service import SAPConflictService

        mock_manifest = MagicMock()
        mock_manifest.get_column_name = MagicMock(return_value="SomeCol")
        mock_get_manifest.return_value = mock_manifest

        mock_client = MagicMock()
        mock_client.find_row.return_value = None  # LPO not found
        mock_requests.post.return_value = MagicMock(raise_for_status=MagicMock())

        mock_ms = MagicMock()
        mock_ms.get_sap_conflicts.return_value = {"CANON-001": [MockCatalogEntry(1, "SAP-A", "CANON-001")]}
        mock_ms.get_material_description.return_value = None
        mock_ms.get_default_sap_code.return_value = None

        service = SAPConflictService(mock_client)

        with patch.dict("sys.modules", {"fn_map_lookup": MagicMock(), "fn_map_lookup.mapping_service": MagicMock(MappingService=MagicMock(return_value=mock_ms))}):
            result = service.check_and_notify_conflicts(
                sap_reference="PTE-MISSING",
                customer_name="Test",
                project_name="Test",
                brand="KIMMCO",
                trace_id="trace-lpo-none",
            )

        assert result == SAMPLE_CARD
        card_call = mock_build_card.call_args
        if "lpo_details" in card_call[1]:
            lpo_details = card_call[1]["lpo_details"]
        else:
            lpo_details = card_call[0][5]
        assert lpo_details == {}


# ============== Test: Webhook Dispatch ==============

@pytest.mark.unit
class TestWebhookDispatch:

    def _setup_service_with_conflicts(self, mock_get_manifest, mock_client):
        from shared.sap_conflict_service import SAPConflictService

        mock_manifest = MagicMock()
        mock_manifest.get_column_name = MagicMock(return_value="SomeCol")
        mock_get_manifest.return_value = mock_manifest

        mock_client.find_row.return_value = None  # No LPO

        mock_ms = MagicMock()
        mock_ms.get_sap_conflicts.return_value = {"C-1": [MockCatalogEntry(1, "S-1", "C-1")]}
        mock_ms.get_material_description.return_value = "Desc"
        mock_ms.get_default_sap_code.return_value = None

        return SAPConflictService(mock_client), mock_ms

    @patch("shared.sap_conflict_service.requests")
    @patch("shared.sap_conflict_service.log_user_action")
    @patch("shared.sap_conflict_service.create_exception")
    @patch("shared.sap_conflict_service.build_sap_conflict_card", return_value=SAMPLE_CARD)
    @patch("shared.sap_conflict_service.get_manifest")
    @patch.dict(os.environ, {"POWER_AUTOMATE_SAP_CONFLICT_URL": WEBHOOK_URL})
    def test_webhook_url_from_env_var(
        self, mock_get_manifest, mock_build_card, mock_create_exc, mock_log_action, mock_requests
    ):
        mock_client = MagicMock()
        mock_requests.post.return_value = MagicMock(raise_for_status=MagicMock())

        service, mock_ms = self._setup_service_with_conflicts(mock_get_manifest, mock_client)

        with patch.dict("sys.modules", {"fn_map_lookup": MagicMock(), "fn_map_lookup.mapping_service": MagicMock(MappingService=MagicMock(return_value=mock_ms))}):
            service.check_and_notify_conflicts(
                sap_reference="PTE-W1",
                customer_name="T",
                project_name="T",
                brand="B",
                trace_id="trace-env",
            )

        mock_requests.post.assert_called_once()
        assert mock_requests.post.call_args[0][0] == WEBHOOK_URL

    @patch("shared.sap_conflict_service.requests")
    @patch("shared.sap_conflict_service.log_user_action")
    @patch("shared.sap_conflict_service.create_exception")
    @patch("shared.sap_conflict_service.build_sap_conflict_card", return_value=SAMPLE_CARD)
    @patch("shared.sap_conflict_service.get_manifest")
    @patch.dict(os.environ, {}, clear=False)
    def test_webhook_url_fallback_from_config_sheet(
        self, mock_get_manifest, mock_build_card, mock_create_exc, mock_log_action, mock_requests
    ):
        from shared.sap_conflict_service import SAPConflictService
        from tests.conftest import MockWorkspaceManifest

        mock_manifest = MockWorkspaceManifest()
        mock_get_manifest.return_value = mock_manifest

        config_url = "https://fallback-webhook.example.com"
        config_row = _make_config_row(mock_manifest, config_url)

        mock_client = MagicMock()
        # First call is for LPO lookup (return None), second for config
        def find_row_router(sheet, column, value):
            if sheet == Sheet.CONFIG:
                return config_row
            return None
        mock_client.find_row.side_effect = find_row_router
        mock_requests.post.return_value = MagicMock(raise_for_status=MagicMock())

        mock_ms = MagicMock()
        mock_ms.get_sap_conflicts.return_value = {"C-1": [MockCatalogEntry(1, "S-1", "C-1")]}
        mock_ms.get_material_description.return_value = "Desc"
        mock_ms.get_default_sap_code.return_value = None

        # Clear the env var so it falls back
        env_patch = patch.dict(os.environ, {"POWER_AUTOMATE_SAP_CONFLICT_URL": ""})
        env_patch.start()

        service = SAPConflictService(mock_client)

        with patch.dict("sys.modules", {"fn_map_lookup": MagicMock(), "fn_map_lookup.mapping_service": MagicMock(MappingService=MagicMock(return_value=mock_ms))}):
            result = service.check_and_notify_conflicts(
                sap_reference="PTE-W2",
                customer_name="T",
                project_name="T",
                brand="B",
                trace_id="trace-fallback",
            )

        env_patch.stop()

        assert result == SAMPLE_CARD
        mock_requests.post.assert_called_once()
        assert mock_requests.post.call_args[0][0] == config_url

    @patch("shared.sap_conflict_service.requests")
    @patch("shared.sap_conflict_service.log_user_action")
    @patch("shared.sap_conflict_service.create_exception")
    @patch("shared.sap_conflict_service.build_sap_conflict_card", return_value=SAMPLE_CARD)
    @patch("shared.sap_conflict_service.get_manifest")
    @patch.dict(os.environ, {"POWER_AUTOMATE_SAP_CONFLICT_URL": ""})
    def test_webhook_not_configured_still_returns_card(
        self, mock_get_manifest, mock_build_card, mock_create_exc, mock_log_action, mock_requests
    ):
        mock_client = MagicMock()
        mock_client.find_row.return_value = None  # No LPO, no config row

        mock_manifest = MagicMock()
        mock_manifest.get_column_name = MagicMock(return_value="SomeCol")
        mock_get_manifest.return_value = mock_manifest

        mock_ms = MagicMock()
        mock_ms.get_sap_conflicts.return_value = {"C-1": [MockCatalogEntry(1, "S-1", "C-1")]}
        mock_ms.get_material_description.return_value = "Desc"
        mock_ms.get_default_sap_code.return_value = None

        from shared.sap_conflict_service import SAPConflictService
        service = SAPConflictService(mock_client)

        with patch.dict("sys.modules", {"fn_map_lookup": MagicMock(), "fn_map_lookup.mapping_service": MagicMock(MappingService=MagicMock(return_value=mock_ms))}):
            result = service.check_and_notify_conflicts(
                sap_reference="PTE-W3",
                customer_name="T",
                project_name="T",
                brand="B",
                trace_id="trace-no-webhook",
            )

        assert result == SAMPLE_CARD
        # Webhook NOT called
        mock_requests.post.assert_not_called()

    @patch("shared.sap_conflict_service.requests")
    @patch("shared.sap_conflict_service.log_user_action")
    @patch("shared.sap_conflict_service.create_exception")
    @patch("shared.sap_conflict_service.build_sap_conflict_card", return_value=SAMPLE_CARD)
    @patch("shared.sap_conflict_service.get_manifest")
    @patch.dict(os.environ, {"POWER_AUTOMATE_SAP_CONFLICT_URL": WEBHOOK_URL})
    def test_webhook_post_failure_creates_exception_still_returns_card(
        self, mock_get_manifest, mock_build_card, mock_create_exc, mock_log_action, mock_requests
    ):
        mock_client = MagicMock()
        mock_client.find_row.return_value = None

        mock_manifest = MagicMock()
        mock_manifest.get_column_name = MagicMock(return_value="SomeCol")
        mock_get_manifest.return_value = mock_manifest

        # Webhook POST raises
        mock_requests.post.side_effect = ConnectionError("Network unreachable")

        mock_ms = MagicMock()
        mock_ms.get_sap_conflicts.return_value = {"C-1": [MockCatalogEntry(1, "S-1", "C-1")]}
        mock_ms.get_material_description.return_value = "Desc"
        mock_ms.get_default_sap_code.return_value = None

        from shared.sap_conflict_service import SAPConflictService
        service = SAPConflictService(mock_client)

        with patch.dict("sys.modules", {"fn_map_lookup": MagicMock(), "fn_map_lookup.mapping_service": MagicMock(MappingService=MagicMock(return_value=mock_ms))}):
            result = service.check_and_notify_conflicts(
                sap_reference="PTE-W4",
                customer_name="T",
                project_name="T",
                brand="B",
                trace_id="trace-post-fail",
            )

        # Card still returned
        assert result == SAMPLE_CARD

        # Exception record created
        mock_create_exc.assert_called_once()
        exc_kwargs = mock_create_exc.call_args[1]
        assert exc_kwargs["reason_code"] == ReasonCode.SAP_CODE_CONFLICT
        assert exc_kwargs["severity"] == ExceptionSeverity.MEDIUM
        assert exc_kwargs["source"] == ExceptionSource.INGEST
        assert "PTE-W4" in exc_kwargs["message"]

    @patch("shared.sap_conflict_service.requests")
    @patch("shared.sap_conflict_service.log_user_action")
    @patch("shared.sap_conflict_service.create_exception")
    @patch("shared.sap_conflict_service.build_sap_conflict_card", return_value=SAMPLE_CARD)
    @patch("shared.sap_conflict_service.get_manifest")
    @patch.dict(os.environ, {"POWER_AUTOMATE_SAP_CONFLICT_URL": WEBHOOK_URL})
    def test_webhook_raise_for_status_failure_creates_exception(
        self, mock_get_manifest, mock_build_card, mock_create_exc, mock_log_action, mock_requests
    ):
        mock_client = MagicMock()
        mock_client.find_row.return_value = None

        mock_manifest = MagicMock()
        mock_manifest.get_column_name = MagicMock(return_value="SomeCol")
        mock_get_manifest.return_value = mock_manifest

        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("HTTP 502 Bad Gateway")
        mock_requests.post.return_value = mock_resp

        mock_ms = MagicMock()
        mock_ms.get_sap_conflicts.return_value = {"C-1": [MockCatalogEntry(1, "S-1", "C-1")]}
        mock_ms.get_material_description.return_value = "Desc"
        mock_ms.get_default_sap_code.return_value = None

        from shared.sap_conflict_service import SAPConflictService
        service = SAPConflictService(mock_client)

        with patch.dict("sys.modules", {"fn_map_lookup": MagicMock(), "fn_map_lookup.mapping_service": MagicMock(MappingService=MagicMock(return_value=mock_ms))}):
            result = service.check_and_notify_conflicts(
                sap_reference="PTE-W5",
                customer_name="T",
                project_name="T",
                brand="B",
                trace_id="trace-502",
            )

        assert result == SAMPLE_CARD
        mock_create_exc.assert_called_once()

    @patch("shared.sap_conflict_service.requests")
    @patch("shared.sap_conflict_service.log_user_action")
    @patch("shared.sap_conflict_service.create_exception")
    @patch("shared.sap_conflict_service.build_sap_conflict_card", return_value=SAMPLE_CARD)
    @patch("shared.sap_conflict_service.get_manifest")
    @patch.dict(os.environ, {"POWER_AUTOMATE_SAP_CONFLICT_URL": WEBHOOK_URL})
    def test_create_exception_failure_during_webhook_error_is_swallowed(
        self, mock_get_manifest, mock_build_card, mock_create_exc, mock_log_action, mock_requests
    ):
        """If both webhook POST and create_exception fail, the function should not crash."""
        mock_client = MagicMock()
        mock_client.find_row.return_value = None

        mock_manifest = MagicMock()
        mock_manifest.get_column_name = MagicMock(return_value="SomeCol")
        mock_get_manifest.return_value = mock_manifest

        mock_requests.post.side_effect = ConnectionError("Down")
        mock_create_exc.side_effect = RuntimeError("Exception logging also failed")

        mock_ms = MagicMock()
        mock_ms.get_sap_conflicts.return_value = {"C-1": [MockCatalogEntry(1, "S-1", "C-1")]}
        mock_ms.get_material_description.return_value = "Desc"
        mock_ms.get_default_sap_code.return_value = None

        from shared.sap_conflict_service import SAPConflictService
        service = SAPConflictService(mock_client)

        with patch.dict("sys.modules", {"fn_map_lookup": MagicMock(), "fn_map_lookup.mapping_service": MagicMock(MappingService=MagicMock(return_value=mock_ms))}):
            result = service.check_and_notify_conflicts(
                sap_reference="PTE-W6",
                customer_name="T",
                project_name="T",
                brand="B",
                trace_id="trace-double-fail",
            )

        # Should still return the card (exception swallowed)
        assert result == SAMPLE_CARD


# ============== Test: User Action Logging ==============

@pytest.mark.unit
class TestUserActionLogging:

    @patch("shared.sap_conflict_service.requests")
    @patch("shared.sap_conflict_service.log_user_action")
    @patch("shared.sap_conflict_service.create_exception")
    @patch("shared.sap_conflict_service.build_sap_conflict_card", return_value=SAMPLE_CARD)
    @patch("shared.sap_conflict_service.get_manifest")
    @patch.dict(os.environ, {"POWER_AUTOMATE_SAP_CONFLICT_URL": WEBHOOK_URL})
    def test_logs_sap_conflict_detected_action(
        self, mock_get_manifest, mock_build_card, mock_create_exc, mock_log_action, mock_requests
    ):
        mock_client = MagicMock()
        mock_client.find_row.return_value = None

        mock_manifest = MagicMock()
        mock_manifest.get_column_name = MagicMock(return_value="SomeCol")
        mock_get_manifest.return_value = mock_manifest
        mock_requests.post.return_value = MagicMock(raise_for_status=MagicMock())

        mock_ms = MagicMock()
        mock_ms.get_sap_conflicts.return_value = SAMPLE_CONFLICTS
        mock_ms.get_material_description.return_value = "Desc"
        mock_ms.get_default_sap_code.return_value = None

        from shared.sap_conflict_service import SAPConflictService
        service = SAPConflictService(mock_client)

        with patch.dict("sys.modules", {"fn_map_lookup": MagicMock(), "fn_map_lookup.mapping_service": MagicMock(MappingService=MagicMock(return_value=mock_ms))}):
            service.check_and_notify_conflicts(
                sap_reference="PTE-LOG",
                customer_name="Logger",
                project_name="Proj",
                brand="B",
                trace_id="trace-log",
            )

        mock_log_action.assert_called_once()
        kwargs = mock_log_action.call_args[1]
        assert kwargs["client"] is mock_client
        assert kwargs["user_id"] == "system"
        assert kwargs["action_type"] == ActionType.SAP_CONFLICT_DETECTED
        assert kwargs["target_table"] == "SAP_MATERIAL_CATALOG"
        assert kwargs["target_id"] == "PTE-LOG"
        assert kwargs["trace_id"] == "trace-log"
        assert "2" in kwargs["notes"]  # "Detected 2 canonical codes..."

    @patch("shared.sap_conflict_service.requests")
    @patch("shared.sap_conflict_service.log_user_action")
    @patch("shared.sap_conflict_service.create_exception")
    @patch("shared.sap_conflict_service.build_sap_conflict_card", return_value=SAMPLE_CARD)
    @patch("shared.sap_conflict_service.get_manifest")
    @patch.dict(os.environ, {"POWER_AUTOMATE_SAP_CONFLICT_URL": WEBHOOK_URL})
    def test_log_action_failure_does_not_crash(
        self, mock_get_manifest, mock_build_card, mock_create_exc, mock_log_action, mock_requests
    ):
        mock_client = MagicMock()
        mock_client.find_row.return_value = None

        mock_manifest = MagicMock()
        mock_manifest.get_column_name = MagicMock(return_value="SomeCol")
        mock_get_manifest.return_value = mock_manifest
        mock_requests.post.return_value = MagicMock(raise_for_status=MagicMock())

        # log_user_action raises
        mock_log_action.side_effect = RuntimeError("Audit log write failed")

        mock_ms = MagicMock()
        mock_ms.get_sap_conflicts.return_value = {"C-1": [MockCatalogEntry(1, "S-1", "C-1")]}
        mock_ms.get_material_description.return_value = "Desc"
        mock_ms.get_default_sap_code.return_value = None

        from shared.sap_conflict_service import SAPConflictService
        service = SAPConflictService(mock_client)

        with patch.dict("sys.modules", {"fn_map_lookup": MagicMock(), "fn_map_lookup.mapping_service": MagicMock(MappingService=MagicMock(return_value=mock_ms))}):
            result = service.check_and_notify_conflicts(
                sap_reference="PTE-LOG2",
                customer_name="T",
                project_name="T",
                brand="B",
                trace_id="trace-log-fail",
            )

        # Should still return card despite audit log failure
        assert result == SAMPLE_CARD


# ============== Test: Enrichment Edge Cases ==============

@pytest.mark.unit
class TestEnrichmentEdgeCases:

    @patch("shared.sap_conflict_service.requests")
    @patch("shared.sap_conflict_service.log_user_action")
    @patch("shared.sap_conflict_service.create_exception")
    @patch("shared.sap_conflict_service.build_sap_conflict_card", return_value=SAMPLE_CARD)
    @patch("shared.sap_conflict_service.get_manifest")
    @patch.dict(os.environ, {"POWER_AUTOMATE_SAP_CONFLICT_URL": WEBHOOK_URL})
    def test_missing_description_falls_back_to_canonical_code(
        self, mock_get_manifest, mock_build_card, mock_create_exc, mock_log_action, mock_requests
    ):
        mock_client = MagicMock()
        mock_client.find_row.return_value = None

        mock_manifest = MagicMock()
        mock_manifest.get_column_name = MagicMock(return_value="SomeCol")
        mock_get_manifest.return_value = mock_manifest
        mock_requests.post.return_value = MagicMock(raise_for_status=MagicMock())

        mock_ms = MagicMock()
        mock_ms.get_sap_conflicts.return_value = {"CANON-NODESC": [MockCatalogEntry(1, "S-1", "CANON-NODESC")]}
        mock_ms.get_material_description.return_value = None  # No description found
        mock_ms.get_default_sap_code.return_value = None

        from shared.sap_conflict_service import SAPConflictService
        service = SAPConflictService(mock_client)

        with patch.dict("sys.modules", {"fn_map_lookup": MagicMock(), "fn_map_lookup.mapping_service": MagicMock(MappingService=MagicMock(return_value=mock_ms))}):
            service.check_and_notify_conflicts(
                sap_reference="PTE-E1",
                customer_name="T",
                project_name="T",
                brand="B",
                trace_id="trace-enrich-fallback",
            )

        card_call = mock_build_card.call_args
        enriched = card_call[1].get("conflicts") or card_call[0][4]
        # When description is None, should fall back to canonical_code
        assert enriched["CANON-NODESC"]["sap_description"] == "CANON-NODESC"

    @patch("shared.sap_conflict_service.requests")
    @patch("shared.sap_conflict_service.log_user_action")
    @patch("shared.sap_conflict_service.create_exception")
    @patch("shared.sap_conflict_service.build_sap_conflict_card", return_value=SAMPLE_CARD)
    @patch("shared.sap_conflict_service.get_manifest")
    @patch.dict(os.environ, {"POWER_AUTOMATE_SAP_CONFLICT_URL": WEBHOOK_URL})
    def test_card_builder_receives_all_parameters(
        self, mock_get_manifest, mock_build_card, mock_create_exc, mock_log_action, mock_requests
    ):
        mock_client = MagicMock()
        mock_client.find_row.return_value = None

        mock_manifest = MagicMock()
        mock_manifest.get_column_name = MagicMock(return_value="SomeCol")
        mock_get_manifest.return_value = mock_manifest
        mock_requests.post.return_value = MagicMock(raise_for_status=MagicMock())

        mock_ms = MagicMock()
        mock_ms.get_sap_conflicts.return_value = {"C-1": [MockCatalogEntry(1, "S-1", "C-1")]}
        mock_ms.get_material_description.return_value = "Material Name"
        mock_ms.get_default_sap_code.return_value = "S-DEFAULT"

        from shared.sap_conflict_service import SAPConflictService
        service = SAPConflictService(mock_client)

        with patch.dict("sys.modules", {"fn_map_lookup": MagicMock(), "fn_map_lookup.mapping_service": MagicMock(MappingService=MagicMock(return_value=mock_ms))}):
            service.check_and_notify_conflicts(
                sap_reference="PTE-PARAMS",
                customer_name="Customer X",
                project_name="Project Y",
                brand="Brand Z",
                trace_id="trace-params",
            )

        mock_build_card.assert_called_once()
        call_kwargs = mock_build_card.call_args[1]
        assert call_kwargs["sap_reference"] == "PTE-PARAMS"
        assert call_kwargs["customer_name"] == "Customer X"
        assert call_kwargs["project_name"] == "Project Y"
        assert call_kwargs["brand"] == "Brand Z"
        assert call_kwargs["trace_id"] == "trace-params"

    @patch("shared.sap_conflict_service.requests")
    @patch("shared.sap_conflict_service.log_user_action")
    @patch("shared.sap_conflict_service.create_exception")
    @patch("shared.sap_conflict_service.build_sap_conflict_card", return_value=SAMPLE_CARD)
    @patch("shared.sap_conflict_service.get_manifest")
    @patch.dict(os.environ, {"POWER_AUTOMATE_SAP_CONFLICT_URL": WEBHOOK_URL})
    def test_webhook_payload_has_correct_conflict_count(
        self, mock_get_manifest, mock_build_card, mock_create_exc, mock_log_action, mock_requests
    ):
        mock_client = MagicMock()
        mock_client.find_row.return_value = None

        mock_manifest = MagicMock()
        mock_manifest.get_column_name = MagicMock(return_value="SomeCol")
        mock_get_manifest.return_value = mock_manifest
        mock_requests.post.return_value = MagicMock(raise_for_status=MagicMock())

        three_conflicts = {
            "C-1": [MockCatalogEntry(1, "S-1", "C-1")],
            "C-2": [MockCatalogEntry(2, "S-2", "C-2")],
            "C-3": [MockCatalogEntry(3, "S-3", "C-3")],
        }
        mock_ms = MagicMock()
        mock_ms.get_sap_conflicts.return_value = three_conflicts
        mock_ms.get_material_description.return_value = "Desc"
        mock_ms.get_default_sap_code.return_value = None

        from shared.sap_conflict_service import SAPConflictService
        service = SAPConflictService(mock_client)

        with patch.dict("sys.modules", {"fn_map_lookup": MagicMock(), "fn_map_lookup.mapping_service": MagicMock(MappingService=MagicMock(return_value=mock_ms))}):
            service.check_and_notify_conflicts(
                sap_reference="PTE-COUNT",
                customer_name="T",
                project_name="T",
                brand="B",
                trace_id="trace-count",
            )

        payload = mock_requests.post.call_args[1]["json"]
        assert payload["conflict_count"] == 3

    @patch("shared.sap_conflict_service.requests")
    @patch("shared.sap_conflict_service.log_user_action")
    @patch("shared.sap_conflict_service.create_exception")
    @patch("shared.sap_conflict_service.build_sap_conflict_card", return_value=SAMPLE_CARD)
    @patch("shared.sap_conflict_service.get_manifest")
    @patch.dict(os.environ, {"POWER_AUTOMATE_SAP_CONFLICT_URL": WEBHOOK_URL})
    def test_webhook_timeout_is_ten_seconds(
        self, mock_get_manifest, mock_build_card, mock_create_exc, mock_log_action, mock_requests
    ):
        mock_client = MagicMock()
        mock_client.find_row.return_value = None

        mock_manifest = MagicMock()
        mock_manifest.get_column_name = MagicMock(return_value="SomeCol")
        mock_get_manifest.return_value = mock_manifest
        mock_requests.post.return_value = MagicMock(raise_for_status=MagicMock())

        mock_ms = MagicMock()
        mock_ms.get_sap_conflicts.return_value = {"C-1": [MockCatalogEntry(1, "S-1", "C-1")]}
        mock_ms.get_material_description.return_value = "Desc"
        mock_ms.get_default_sap_code.return_value = None

        from shared.sap_conflict_service import SAPConflictService
        service = SAPConflictService(mock_client)

        with patch.dict("sys.modules", {"fn_map_lookup": MagicMock(), "fn_map_lookup.mapping_service": MagicMock(MappingService=MagicMock(return_value=mock_ms))}):
            service.check_and_notify_conflicts(
                sap_reference="PTE-TO",
                customer_name="T",
                project_name="T",
                brand="B",
                trace_id="trace-timeout",
            )

        call_kwargs = mock_requests.post.call_args[1]
        assert call_kwargs["timeout"] == 10
