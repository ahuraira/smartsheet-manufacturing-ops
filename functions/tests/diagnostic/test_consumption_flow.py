"""
Diagnostic test: Full consumption → tag completion → margin approval flow.

Run: cd functions && pytest tests/diagnostic/test_consumption_flow.py -v -s

Tests the complete chain:
1. Consumption submission writes rows to CONSUMPTION_LOG
2. Allocation statuses update based on 80% rule
3. Tag status flips to "Complete" when all allocations consumed
4. MarginOrchestrator triggers with correct data
5. MARGIN_APPROVAL_LOG row is written
6. Adaptive card is built and dispatched
"""
import pytest
import uuid
import json
from unittest.mock import patch, MagicMock
from datetime import datetime

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.logical_names import Sheet, Column
from shared.manifest import WorkspaceManifest


# ── Helpers ──────────────────────────────────────────────────────────────────

def _build_manifest():
    """Build a minimal manifest with the sheets/columns the flow needs."""
    return {
        "workspace_id": 1,
        "sheets": {
            # ALLOCATION_LOG
            "ALLOCATION_LOG": {
                "id": 2000,
                "name": "Allocation Log",
                "columns": {
                    "ALLOCATION_ID": {"id": 2001, "name": "Allocation ID"},
                    "TAG_SHEET_ID": {"id": 2002, "name": "Tag Sheet ID"},
                    "MATERIAL_CODE": {"id": 2003, "name": "Material Code"},
                    "SAP_CODE": {"id": 2004, "name": "SAP Code"},
                    "QUANTITY": {"id": 2005, "name": "Quantity"},
                    "UOM": {"id": 2006, "name": "UOM"},
                    "STATUS": {"id": 2007, "name": "Status"},
                    "SESSION_ID": {"id": 2008, "name": "Session ID"},
                    "RAW_QUANTITY": {"id": 2009, "name": "Raw Quantity"},
                    "RAW_UOM": {"id": 2010, "name": "Raw UOM"},
                }
            },
            # CONSUMPTION_LOG
            "CONSUMPTION_LOG": {
                "id": 3000,
                "name": "Consumption Log",
                "columns": {
                    "CONSUMPTION_ID": {"id": 3001, "name": "Consumption ID"},
                    "TAG_SHEET_ID": {"id": 3002, "name": "Tag Sheet ID"},
                    "STATUS": {"id": 3003, "name": "Status"},
                    "CONSUMPTION_DATE": {"id": 3004, "name": "Consumption Date"},
                    "SHIFT": {"id": 3005, "name": "Shift"},
                    "MATERIAL_CODE": {"id": 3006, "name": "Material Code"},
                    "QUANTITY": {"id": 3007, "name": "Quantity"},
                    "UOM": {"id": 3008, "name": "UOM"},
                    "ALLOCATION_ID": {"id": 3009, "name": "Allocation ID"},
                    "REMARKS": {"id": 3010, "name": "Remarks"},
                    "CONSUMPTION_TYPE": {"id": 3011, "name": "Consumption Type"},
                    "RAW_QUANTITY": {"id": 3012, "name": "Raw Quantity"},
                    "RAW_UOM": {"id": 3013, "name": "Raw UOM"},
                    "TRACE_ID": {"id": 3014, "name": "Trace ID"},
                }
            },
            # TAG_REGISTRY
            "TAG_REGISTRY": {
                "id": 4000,
                "name": "Tag Registry",
                "columns": {
                    "TAG_ID": {"id": 4001, "name": "Tag ID"},
                    "STATUS": {"id": 4002, "name": "Status"},
                    "LPO_SAP_REFERENCE": {"id": 4003, "name": "LPO SAP Reference Link"},
                    "ESTIMATED_QUANTITY": {"id": 4004, "name": "Estimated Quantity"},
                    "BRAND": {"id": 4005, "name": "Brand"},
                }
            },
            # CONFIG
            "CONFIG": {
                "id": 5000,
                "name": "Config",
                "columns": {
                    "CONFIG_KEY": {"id": 5001, "name": "config_key"},
                    "CONFIG_VALUE": {"id": 5002, "name": "config_value"},
                    "EFFECTIVE_FROM": {"id": 5003, "name": "effective_from"},
                    "CHANGED_BY": {"id": 5004, "name": "changed_by"},
                }
            },
            # MARGIN_APPROVAL_LOG — key must match Sheet enum value
            "06C_MARGIN_APPROVAL_LOG": {
                "id": 6000,
                "name": "06C Margin Approval Log",
                "columns": {
                    "APPROVAL_ID": {"id": 6001, "name": "Approval ID"},
                    "TAG_SHEET_ID": {"id": 6002, "name": "Tag Sheet ID"},
                    "LPO_ID": {"id": 6003, "name": "LPO ID"},
                    "TOTAL_COST": {"id": 6004, "name": "Total Cost"},
                    "ACCESSORY_COST": {"id": 6005, "name": "Accessory Cost"},
                    "EQ_ACCESSORY_SQM": {"id": 6006, "name": "Eq Accessory Sqm"},
                    "BASELINE_MARGIN_PCT": {"id": 6007, "name": "Baseline Margin pct"},
                    "PM_ADJUSTED_PCT": {"id": 6008, "name": "PM Adjusted pct"},
                    "TARGET_MARGIN_PCT": {"id": 6009, "name": "Target Margin pct"},
                    "FINAL_MARGIN_PCT": {"id": 6010, "name": "Final Margin pct"},
                    "STATUS": {"id": 6011, "name": "Status"},
                    "CLIENT_REQUEST_ID": {"id": 6012, "name": "Client Request ID"},
                    "CREATED_DATE": {"id": 6013, "name": "Created Date"},
                    "DECISION_DATE": {"id": 6014, "name": "Decision Date"},
                    "REMARKS": {"id": 6015, "name": "Remarks"},
                    "CARD_JSON": {"id": 6016, "name": "Card JSON"},
                }
            },
            # USER_ACTION_LOG
            "USER_ACTION_LOG": {
                "id": 7000,
                "name": "User Action Log",
                "columns": {
                    "ACTION_ID": {"id": 7001, "name": "Action ID"},
                    "USER_ID": {"id": 7002, "name": "User ID"},
                    "ACTION_TYPE": {"id": 7003, "name": "Action Type"},
                    "TARGET_TABLE": {"id": 7004, "name": "Target Table"},
                    "TARGET_ID": {"id": 7005, "name": "Target ID"},
                    "PREVIOUS_VALUE": {"id": 7006, "name": "Previous Value"},
                    "NEW_VALUE": {"id": 7007, "name": "New Value"},
                    "NOTES": {"id": 7008, "name": "Notes"},
                    "TIMESTAMP": {"id": 7009, "name": "Timestamp"},
                    "TRACE_ID": {"id": 7010, "name": "Trace ID"},
                }
            },
            # EXCEPTION_LOG
            "EXCEPTION_LOG": {
                "id": 8000,
                "name": "Exception Log",
                "columns": {
                    "EXCEPTION_ID": {"id": 8001, "name": "Exception ID"},
                    "TRACE_ID": {"id": 8002, "name": "Trace ID"},
                    "REASON_CODE": {"id": 8003, "name": "Reason Code"},
                    "SEVERITY": {"id": 8004, "name": "Severity"},
                    "SOURCE": {"id": 8005, "name": "Source"},
                    "RELATED_TAG_ID": {"id": 8006, "name": "Related Tag ID"},
                    "MESSAGE": {"id": 8007, "name": "Message"},
                    "TIMESTAMP": {"id": 8008, "name": "Timestamp"},
                }
            },
            # INVENTORY_TXN_LOG
            "INVENTORY_TXN_LOG": {
                "id": 9000,
                "name": "Inventory Txn Log",
                "columns": {
                    "TXN_ID": {"id": 9001, "name": "Txn ID"},
                    "TXN_TYPE": {"id": 9002, "name": "Txn Type"},
                    "MATERIAL_CODE": {"id": 9003, "name": "Material Code"},
                    "QUANTITY": {"id": 9004, "name": "Quantity"},
                    "UOM": {"id": 9005, "name": "UOM"},
                    "REFERENCE_DOC": {"id": 9006, "name": "Reference Doc"},
                    "TIMESTAMP": {"id": 9007, "name": "Timestamp"},
                    "TRACE_ID": {"id": 9008, "name": "Trace ID"},
                }
            },
            # LPO_MASTER
            "LPO_MASTER": {
                "id": 10000,
                "name": "LPO Master",
                "columns": {
                    "SAP_REFERENCE": {"id": 10001, "name": "SAP Reference"},
                    "SELLING_PRICE_PER_SQM": {"id": 10002, "name": "Selling Price Per SQM"},
                    "MARGIN_PCT": {"id": 10003, "name": "Margin PCT"},
                }
            },
            # SAP_INVENTORY_SNAPSHOT
            "SAP_INVENTORY_SNAPSHOT": {
                "id": 11000,
                "name": "SAP Inventory Snapshot",
                "columns": {
                    "MATERIAL_CODE": {"id": 11001, "name": "Material Code"},
                    "VALUE_AED": {"id": 11002, "name": "Value AED"},
                    "QUANTITY": {"id": 11003, "name": "Quantity"},
                }
            },
        }
    }


def _make_sheet_response(sheet_key, manifest_data, rows):
    """Build a Smartsheet API-style sheet response from row dicts keyed by logical col name."""
    sheet_def = manifest_data["sheets"][sheet_key]
    columns = [{"id": c["id"], "title": c["name"]} for c in sheet_def["columns"].values()]

    # Build logical→column_id map
    logical_to_id = {lname: cdef["id"] for lname, cdef in sheet_def["columns"].items()}

    api_rows = []
    for row in rows:
        row_id = row.pop("_row_id", hash(str(row)) % 10**10)
        cells = []
        for lname, val in row.items():
            if lname in logical_to_id:
                cells.append({"columnId": logical_to_id[lname], "value": val})
        api_rows.append({"id": row_id, "cells": cells})

    return {"id": sheet_def["id"], "name": sheet_def.get("name", sheet_key), "columns": columns, "rows": api_rows}


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def manifest_data():
    return _build_manifest()


@pytest.fixture
def manifest(manifest_data):
    m = WorkspaceManifest.__new__(WorkspaceManifest)
    m._data = manifest_data
    m._sheets = manifest_data["sheets"]
    return m


# ── Test 1: Sheet enum → manifest key resolution ────────────────────────────

class TestManifestAlignment:
    """Verify that every Sheet enum value resolves to an actual manifest key."""

    def test_margin_approval_log_key_matches(self, manifest):
        """The Sheet enum value must match the manifest key exactly."""
        sheet_id = manifest.get_sheet_id(Sheet.MARGIN_APPROVAL_LOG)
        assert sheet_id == 6000, (
            f"Sheet.MARGIN_APPROVAL_LOG ('{Sheet.MARGIN_APPROVAL_LOG}') did not resolve. "
            f"Check that the enum value matches the manifest key."
        )

    def test_all_margin_columns_resolve(self, manifest):
        """Every Column.MARGIN_APPROVAL_LOG.* must resolve to a physical name."""
        cols = Column.MARGIN_APPROVAL_LOG
        for attr in dir(cols):
            if attr.startswith("_"):
                continue
            logical = getattr(cols, attr)
            physical = manifest.get_column_name(Sheet.MARGIN_APPROVAL_LOG, logical)
            assert physical is not None, (
                f"Column.MARGIN_APPROVAL_LOG.{attr} ('{logical}') not found in manifest. "
                f"Available: {list(manifest._sheets.get(Sheet.MARGIN_APPROVAL_LOG, {}).get('columns', {}).keys())}"
            )

    def test_tag_registry_total_area_sqm_resolves(self, manifest):
        """TOTAL_AREA_SQM should map to ESTIMATED_QUANTITY in manifest."""
        physical = manifest.get_column_name(Sheet.TAG_REGISTRY, Column.TAG_REGISTRY.TOTAL_AREA_SQM)
        assert physical is not None, (
            f"Column.TAG_REGISTRY.TOTAL_AREA_SQM ('{Column.TAG_REGISTRY.TOTAL_AREA_SQM}') "
            f"not found in manifest."
        )


# ── Test 2: _parse_rows preserves row_id ────────────────────────────────────

class TestParseRows:
    def test_row_id_preserved(self, manifest_data):
        """_parse_rows must include 'row_id' in each parsed row."""
        from shared.allocation_service import _parse_rows
        sheet = _make_sheet_response("ALLOCATION_LOG", manifest_data, [
            {"_row_id": 99999, "ALLOCATION_ID": "ALLOC-001", "TAG_SHEET_ID": "TAG-0013", "STATUS": "Active"},
        ])
        rows = _parse_rows(sheet)
        assert len(rows) == 1
        assert rows[0]["row_id"] == 99999, "row_id not preserved by _parse_rows()"

    def test_physical_column_names_used(self, manifest_data):
        """_parse_rows should key by physical column names (from sheet columns)."""
        from shared.allocation_service import _parse_rows
        sheet = _make_sheet_response("ALLOCATION_LOG", manifest_data, [
            {"_row_id": 1, "ALLOCATION_ID": "ALLOC-X", "STATUS": "Active"},
        ])
        rows = _parse_rows(sheet)
        # Physical name is "Allocation ID" not "ALLOCATION_ID"
        assert "Allocation ID" in rows[0], f"Expected physical names, got: {list(rows[0].keys())}"


# ── Test 3: Tag completion triggers margin ──────────────────────────────────

class TestTagCompletionTriggersMargin:
    """End-to-end: consumption submission → tag Complete → margin orchestrator called."""

    def test_all_consumed_triggers_margin(self, manifest, manifest_data):
        """When all allocations for a tag reach 'Consumed', margin orchestrator fires."""
        from shared.consumption_service import submit_consumption
        from shared.flow_models import ConsumptionSubmission, ConsumptionLine

        # Build mock Smartsheet data
        alloc_rows = [
            {"_row_id": 100, "ALLOCATION_ID": "ALLOC-001", "TAG_SHEET_ID": "TAG-0013",
             "MATERIAL_CODE": "MAT-A", "SAP_CODE": "SAP-A", "QUANTITY": 10.0, "UOM": "m2",
             "STATUS": "Active", "RAW_QUANTITY": 10.0, "RAW_UOM": "m2"},
        ]
        tag_rows = [
            {"_row_id": 200, "TAG_ID": "TAG-0013", "STATUS": "In Progress",
             "LPO_SAP_REFERENCE": "LPO-TEST", "ESTIMATED_QUANTITY": 50.0},
        ]
        # No existing consumptions
        cons_rows = []

        alloc_sheet = _make_sheet_response("ALLOCATION_LOG", manifest_data, alloc_rows)
        tag_sheet = _make_sheet_response("TAG_REGISTRY", manifest_data, tag_rows)
        cons_sheet = _make_sheet_response("CONSUMPTION_LOG", manifest_data, cons_rows)

        # Mock client
        mock_client = MagicMock()

        def mock_get_sheet(sheet_ref, **kwargs):
            ref = str(sheet_ref)
            if "ALLOCATION" in ref:
                return alloc_sheet
            elif "TAG" in ref:
                return tag_sheet
            elif "CONSUMPTION" in ref:
                return cons_sheet
            elif "CONFIG" in ref:
                return _make_sheet_response("CONFIG", manifest_data, [
                    {"_row_id": 300, "CONFIG_KEY": "NEXT_CONSUMPTION_ID", "CONFIG_VALUE": "CONS-0100"},
                ])
            return {"id": 0, "columns": [], "rows": []}

        mock_client.get_sheet.side_effect = mock_get_sheet
        mock_client.find_rows.return_value = []
        mock_client.add_rows_bulk.return_value = [{"id": 500}]
        mock_client.update_row.return_value = {}
        mock_client.add_row.return_value = {"id": 501}

        # Build submission: consume 100% of ALLOC-001
        submission = ConsumptionSubmission(
            allocation_ids=["ALLOC-001"],
            lines=[
                ConsumptionLine(
                    allocation_id="ALLOC-001",
                    canonical_code="MAT-A",
                    allocated_qty=10.0,
                    actual_qty=10.0,
                    uom="m2",
                )
            ],
            user="test@example.com",
            plant="PLANT-A",
            shift="Morning",
        )

        margin_called = {"called": False, "args": {}}

        with patch("shared.consumption_service.get_manifest", return_value=manifest), \
             patch("shared.consumption_service.AllocationLock") as mock_lock, \
             patch("shared.margin_orchestrator.MarginOrchestrator") as MockOrch:

            # Lock always succeeds
            lock_ctx = MagicMock()
            lock_ctx.success = True
            mock_lock.return_value.__enter__ = MagicMock(return_value=lock_ctx)
            mock_lock.return_value.__exit__ = MagicMock(return_value=False)

            # Track margin orchestrator calls
            mock_orch_instance = MagicMock()
            MockOrch.return_value = mock_orch_instance

            def capture_margin_call(**kwargs):
                margin_called["called"] = True
                margin_called["args"] = kwargs

            mock_orch_instance.trigger_margin_approval_for_tag.side_effect = capture_margin_call

            result = submit_consumption(mock_client, submission, trace_id="test-trace-001")

        # Verify: consumption rows were written
        assert mock_client.add_rows_bulk.called, "Consumption rows not written"

        # Verify: allocation status was updated
        update_calls = [c for c in mock_client.update_row.call_args_list
                        if str(c[0][0]) == str(Sheet.ALLOCATION_LOG)]
        assert len(update_calls) > 0, "Allocation status not updated"

        # Verify: tag was marked Complete
        tag_update_calls = [c for c in mock_client.update_row.call_args_list
                           if str(c[0][0]) == str(Sheet.TAG_REGISTRY)]
        assert len(tag_update_calls) > 0, (
            "Tag NOT marked Complete. This is the bug — all allocations consumed but tag "
            "completion check failed. Check _parse_rows row_id, col_alloc_tag matching, "
            "and status comparison logic."
        )

        # Verify: margin orchestrator was called
        assert margin_called["called"], (
            "MarginOrchestrator.trigger_margin_approval_for_tag() NOT called. "
            "Tag was marked Complete but margin flow didn't trigger."
        )
        assert margin_called["args"]["tag_sheet_id"] == "TAG-0013"
        assert margin_called["args"]["lpo_sap_ref"] == "LPO-TEST"
        assert margin_called["args"]["delivered_sqm"] == 50.0

    def test_partial_consumed_does_not_trigger(self, manifest, manifest_data):
        """When only some allocations consumed, tag stays in progress."""
        from shared.consumption_service import submit_consumption
        from shared.flow_models import ConsumptionSubmission, ConsumptionLine

        alloc_rows = [
            {"_row_id": 100, "ALLOCATION_ID": "ALLOC-001", "TAG_SHEET_ID": "TAG-0013",
             "MATERIAL_CODE": "MAT-A", "SAP_CODE": "SAP-A", "QUANTITY": 100.0, "UOM": "m2",
             "STATUS": "Active", "RAW_QUANTITY": 100.0, "RAW_UOM": "m2"},
            {"_row_id": 101, "ALLOCATION_ID": "ALLOC-002", "TAG_SHEET_ID": "TAG-0013",
             "MATERIAL_CODE": "MAT-B", "SAP_CODE": "SAP-B", "QUANTITY": 50.0, "UOM": "m2",
             "STATUS": "Active", "RAW_QUANTITY": 50.0, "RAW_UOM": "m2"},
        ]
        tag_rows = [
            {"_row_id": 200, "TAG_ID": "TAG-0013", "STATUS": "In Progress",
             "LPO_SAP_REFERENCE": "LPO-TEST", "ESTIMATED_QUANTITY": 50.0},
        ]

        alloc_sheet = _make_sheet_response("ALLOCATION_LOG", manifest_data, alloc_rows)
        tag_sheet = _make_sheet_response("TAG_REGISTRY", manifest_data, tag_rows)
        cons_sheet = _make_sheet_response("CONSUMPTION_LOG", manifest_data, [])

        mock_client = MagicMock()

        def mock_get_sheet(sheet_ref, **kwargs):
            ref = str(sheet_ref)
            if "ALLOCATION" in ref:
                return alloc_sheet
            elif "TAG" in ref:
                return tag_sheet
            elif "CONSUMPTION" in ref:
                return cons_sheet
            elif "CONFIG" in ref:
                return _make_sheet_response("CONFIG", manifest_data, [
                    {"_row_id": 300, "CONFIG_KEY": "NEXT_CONSUMPTION_ID", "CONFIG_VALUE": "CONS-0100"},
                ])
            return {"id": 0, "columns": [], "rows": []}

        mock_client.get_sheet.side_effect = mock_get_sheet
        mock_client.find_rows.return_value = []
        mock_client.add_rows_bulk.return_value = [{"id": 500}]
        mock_client.update_row.return_value = {}
        mock_client.add_row.return_value = {"id": 501}

        # Only consume ALLOC-001 (10% of 100), leave ALLOC-002 untouched
        submission = ConsumptionSubmission(
            allocation_ids=["ALLOC-001"],
            lines=[
                ConsumptionLine(
                    allocation_id="ALLOC-001",
                    canonical_code="MAT-A",
                    allocated_qty=10.0,
                    actual_qty=10.0,
                    uom="m2",
                )
            ],
            user="test@example.com",
            plant="PLANT-A",
            shift="Morning",
        )

        with patch("shared.consumption_service.get_manifest", return_value=manifest), \
             patch("shared.consumption_service.AllocationLock") as mock_lock, \
             patch("shared.margin_orchestrator.MarginOrchestrator") as MockOrch:

            lock_ctx = MagicMock()
            lock_ctx.success = True
            mock_lock.return_value.__enter__ = MagicMock(return_value=lock_ctx)
            mock_lock.return_value.__exit__ = MagicMock(return_value=False)

            result = submit_consumption(mock_client, submission, trace_id="test-trace-002")

        # Tag should NOT be marked Complete (ALLOC-002 is still Active)
        tag_update_calls = [c for c in mock_client.update_row.call_args_list
                           if str(c[0][0]) == str(Sheet.TAG_REGISTRY)]
        assert len(tag_update_calls) == 0, "Tag should NOT be Complete when allocations remain unconsumed"


# ── Test 4: Margin orchestrator column resolution ───────────────────────────

class TestMarginOrchestratorColumns:
    """Verify margin orchestrator can resolve all MARGIN_APPROVAL_LOG columns."""

    def test_builds_row_without_keyerror(self, manifest):
        """MarginOrchestrator must not KeyError on None column names."""
        from shared.margin_orchestrator import MarginOrchestrator

        mock_client = MagicMock()
        mock_client.add_rows_bulk.return_value = [{"id": 700}]
        mock_client.add_row.return_value = {"id": 701}
        mock_client.find_rows.return_value = []

        orch = MarginOrchestrator(mock_client)
        orch.manifest = manifest

        # Mock costing_service
        with patch.object(orch, "costing_service") as mock_cs:
            mock_cs.calculate_margin.return_value = {
                "delivered_sqm": 50.0,
                "material_cost_aed": 1000.0,
                "fixed_cost_aed": 200.0,
                "credit_risk_aed": 50.0,
                "total_cost_aed": 1250.0,
                "selling_price_per_sqm": 30.0,
                "total_revenue_aed": 1500.0,
                "gross_profit_aed": 250.0,
                "gm_pct": 0.1667,
                "corp_tax_aed": 22.5,
                "target_margin_pct": 0.15,
                "required_billing_area": 55.0,
                "area_variation_pct": 0.10,
                "suggested_manager_penalty_pct": 10.0,
            }

            # Mock adaptive card builder and requests (imported locally inside margin_orchestrator)
            with patch("shared.margin_orchestrator.build_margin_approval_card", return_value={"type": "test"}):
                with patch.dict("sys.modules", {"requests": MagicMock()}):
                    import sys
                    sys.modules["requests"].post.return_value = MagicMock(status_code=202)

                    # This should NOT raise KeyError
                    try:
                        orch.trigger_margin_approval_for_tag(
                            tag_sheet_id="TAG-0013",
                            delivered_sqm=50.0,
                            lpo_sap_ref="LPO-TEST",
                            trace_id="test-trace-003"
                        )
                    except Exception as e:
                        pytest.fail(
                            f"MarginOrchestrator raised {type(e).__name__}: {e}. "
                            f"This is the production error — column names don't match manifest."
                        )

        # Verify add_row was called with correct sheet
        add_row_calls = [c for c in mock_client.add_row.call_args_list
                        if str(c[0][0]) == str(Sheet.MARGIN_APPROVAL_LOG)]
        assert len(add_row_calls) > 0, "MARGIN_APPROVAL_LOG row not written via add_row()"


# ── Test 5: Costing service column resolution ───────────────────────────────

class TestCostingServiceColumns:
    """Verify costing_service resolves columns correctly against manifest."""

    def test_config_lookup(self, manifest):
        """Config value lookup should use correct column names."""
        from shared.costing_service import CostingService

        mock_client = MagicMock()

        # CONFIG rows
        config_rows = [
            {"config_key": "DEFAULT_SELLING_PRICE_PER_SQM", "config_value": "25.0", "row_id": 1},
            {"config_key": "DEFAULT_MARGIN_PCT", "config_value": "0.15", "row_id": 2},
            {"config_key": "FIXED_COST_PER_SQM", "config_value": "4.0", "row_id": 3},
            {"config_key": "CREDIT_RISK_PCT", "config_value": "0.02", "row_id": 4},
            {"config_key": "CORPORATE_TAX_PCT", "config_value": "0.09", "row_id": 5},
        ]

        def mock_find_rows(sheet_ref, col_ref, value):
            for row in config_rows:
                if row["config_key"] == value:
                    return [row]
            return []

        mock_client.find_rows.side_effect = mock_find_rows

        cs = CostingService(mock_client)
        cs.manifest = manifest

        # This should not raise
        val = cs._get_config_value("DEFAULT_SELLING_PRICE_PER_SQM", "0.0")
        assert val is not None


# ── Test 6: Lock timeout adequacy ───────────────────────────────────────────

class TestLockTimeout:
    """Verify lock timeout is sufficient for realistic payloads."""

    def test_lock_timeout_configured(self):
        """Lock timeout in consumption_service should be >= 60s for 9+ allocations."""
        import inspect
        import re
        from shared import consumption_service

        source = inspect.getsource(consumption_service.submit_consumption)
        match = re.search(r"timeout_ms=(\d+)", source)
        assert match, "AllocationLock timeout_ms not found in submit_consumption"
        timeout = int(match.group(1))
        assert timeout >= 60000, (
            f"AllocationLock timeout is {timeout}ms but should be >= 60000ms. "
            f"Production showed 45s elapsed > 30s timeout for 9 allocations."
        )


# ── Test 7: Inventory transaction logging ────────────────────────────────────

class TestInventoryTransactionLogging:
    """Verify inventory transactions are actually written, not silently skipped."""

    def test_batch_writes_rows_via_add_row(self, manifest):
        """log_inventory_transactions_batch must call client.add_row for each txn."""
        from shared.inventory_service import log_inventory_transactions_batch

        mock_client = MagicMock()
        mock_client.add_row.return_value = {"id": 12345}

        txns = [
            {"txn_type": "Consumption", "material_code": "MAT-A", "quantity": -10.0,
             "reference_doc": "ALLOC-001", "source_system": "AzureFunc"},
            {"txn_type": "Consumption", "material_code": "MAT-B", "quantity": -5.0,
             "reference_doc": "ALLOC-002", "source_system": "AzureFunc"},
        ]

        ids = log_inventory_transactions_batch(mock_client, txns, trace_id="test-inv-001")

        # Must have returned 2 generated IDs
        assert len(ids) == 2, f"Expected 2 txn IDs, got {len(ids)}"
        assert all(id.startswith("TXN-") for id in ids)

        # Must have called add_row twice (once per txn)
        add_row_calls = [c for c in mock_client.add_row.call_args_list
                        if str(c[0][0]) == str(Sheet.INVENTORY_TXN_LOG)]
        assert len(add_row_calls) == 2, (
            f"Expected 2 add_row calls to INVENTORY_TXN_LOG, got {len(add_row_calls)}. "
            f"This was the silent failure bug — rows were not written because "
            f"get_all_column_ids() returns logical keys but row_data used physical names."
        )

        # Verify the row data contains logical column names
        first_call_data = add_row_calls[0][0][1]  # second positional arg = row_data dict
        assert Column.INVENTORY_TXN_LOG.TXN_TYPE in first_call_data
        assert Column.INVENTORY_TXN_LOG.MATERIAL_CODE in first_call_data
        assert first_call_data[Column.INVENTORY_TXN_LOG.QUANTITY] == -10.0

    def test_empty_transactions_returns_empty(self):
        """Empty transaction list should return empty without calling client."""
        from shared.inventory_service import log_inventory_transactions_batch

        mock_client = MagicMock()
        ids = log_inventory_transactions_batch(mock_client, [], trace_id="test-inv-002")
        assert ids == []
        mock_client.add_row.assert_not_called()

    def test_consumption_submission_creates_inventory_txns(self, manifest, manifest_data):
        """Full flow: consumption submission must produce inventory transaction rows."""
        from shared.consumption_service import submit_consumption
        from shared.flow_models import ConsumptionSubmission, ConsumptionLine

        alloc_rows = [
            {"_row_id": 100, "ALLOCATION_ID": "ALLOC-001", "TAG_SHEET_ID": "TAG-0013",
             "MATERIAL_CODE": "MAT-A", "SAP_CODE": "SAP-A", "QUANTITY": 10.0, "UOM": "m2",
             "STATUS": "Active", "RAW_QUANTITY": 10.0, "RAW_UOM": "m2"},
        ]

        alloc_sheet = _make_sheet_response("ALLOCATION_LOG", manifest_data, alloc_rows)
        tag_sheet = _make_sheet_response("TAG_REGISTRY", manifest_data, [
            {"_row_id": 200, "TAG_ID": "TAG-0013", "STATUS": "In Progress",
             "LPO_SAP_REFERENCE": "LPO-TEST", "ESTIMATED_QUANTITY": 50.0},
        ])
        cons_sheet = _make_sheet_response("CONSUMPTION_LOG", manifest_data, [])

        mock_client = MagicMock()

        def mock_get_sheet(sheet_ref, **kwargs):
            ref = str(sheet_ref)
            if "ALLOCATION" in ref:
                return alloc_sheet
            elif "TAG" in ref:
                return tag_sheet
            elif "CONSUMPTION" in ref:
                return cons_sheet
            elif "CONFIG" in ref:
                return _make_sheet_response("CONFIG", manifest_data, [
                    {"_row_id": 300, "CONFIG_KEY": "NEXT_CONSUMPTION_ID", "CONFIG_VALUE": "CONS-0100"},
                ])
            return {"id": 0, "columns": [], "rows": []}

        mock_client.get_sheet.side_effect = mock_get_sheet
        mock_client.find_rows.return_value = []
        mock_client.add_rows_bulk.return_value = [{"id": 500}]
        mock_client.update_row.return_value = {}
        mock_client.add_row.return_value = {"id": 501}

        submission = ConsumptionSubmission(
            allocation_ids=["ALLOC-001"],
            lines=[
                ConsumptionLine(
                    allocation_id="ALLOC-001",
                    canonical_code="MAT-A",
                    allocated_qty=10.0,
                    actual_qty=10.0,  # Must be within 10% variance of allocated_qty
                    accessories_qty=1.0,
                    uom="m2",
                )
            ],
            user="test@example.com",
            plant="PLANT-A",
            shift="Morning",
        )

        # Counter for deterministic consumption IDs
        cons_counter = {"n": 0}
        def mock_gen_cons_id(client):
            cons_counter["n"] += 1
            return f"CONS-TEST-{cons_counter['n']:04d}"

        with patch("shared.consumption_service.get_manifest", return_value=manifest), \
             patch("shared.manifest.get_manifest", return_value=manifest), \
             patch("shared.consumption_service.AllocationLock") as mock_lock, \
             patch("shared.margin_orchestrator.MarginOrchestrator") as MockOrch:

            lock_ctx = MagicMock()
            lock_ctx.success = True
            mock_lock.return_value.__enter__ = MagicMock(return_value=lock_ctx)
            mock_lock.return_value.__exit__ = MagicMock(return_value=False)
            MockOrch.return_value = MagicMock()

            submit_consumption(mock_client, submission, trace_id="test-inv-003")

        # Verify: add_row was called for INVENTORY_TXN_LOG
        inv_calls = [c for c in mock_client.add_row.call_args_list
                    if str(c[0][0]) == str(Sheet.INVENTORY_TXN_LOG)]
        assert len(inv_calls) >= 1, (
            f"Expected at least 1 inventory transaction (got {len(inv_calls)}). "
            f"Consumption of 8.0 actual + 2.0 accessories should produce 2 txn rows. "
            f"All add_row calls: {[(str(c[0][0]), list(c[0][1].keys()) if len(c[0]) > 1 else 'N/A') for c in mock_client.add_row.call_args_list]}"
        )
