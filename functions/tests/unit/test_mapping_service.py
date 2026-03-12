"""
Unit Tests for Mapping Service (v2.0 — 3-Sheet Lookup)

Tests the core mapping logic with the refactored architecture:
- 05a Material Master (identity + default SAP code)
- 05b Mapping Override (brand/LPO overrides)
- 05c SAP Material Catalog (conversion factors)

RUTHLESSLY validates:
1. Scope Precedence (LPO > BRAND > PROJECT > CUSTOMER)
2. Conversion factors always from 05c (SAP Catalog)
3. Cache Invalidation and Refresh
4. Idempotency (History Reconstruction)
5. Failure Handling (Review Decision)
"""

import pytest
from unittest.mock import MagicMock, patch, call
from datetime import datetime, timedelta
from fn_map_lookup.mapping_service import (
    MappingService, MappingResult, MaterialMasterEntry, CatalogEntry
)


@pytest.fixture
def mock_smartsheet_client():
    client = MagicMock()
    # Mock lookup calls to return empty usually, overridden in tests
    client.get_all_rows.return_value = []
    client.find_row.return_value = None
    return client


@pytest.fixture
def mapping_service(mock_smartsheet_client):
    # Reset singleton for fresh test state
    MappingService._instance = None
    service = MappingService(mock_smartsheet_client)
    # Clear caches explicitly
    service._material_master_cache = {}
    service._catalog_cache = {}
    service._override_cache = []
    service._cache_timestamp = None
    service._catalog_cache_timestamp = None
    service._override_cache_timestamp = None
    return service


# ── Helper: Populate 05a Material Master cache directly ────────────────
def _seed_master_cache(service, entries):
    """Seed Material Master cache with test entries."""
    for normalized_desc, canonical_code, default_sap_code in entries:
        service._material_master_cache[normalized_desc] = MaterialMasterEntry(
            row_id=100,
            nesting_description=normalized_desc,
            canonical_code=canonical_code,
            default_sap_code=default_sap_code,
        )
    service._cache_timestamp = datetime.utcnow()


# ── Helper: Populate 05c SAP Catalog cache directly ────────────────────
def _seed_catalog_cache(service, entries):
    """Seed SAP Catalog cache with test entries."""
    for sap_code, canonical_code, uom, sap_uom, conv_factor in entries:
        service._catalog_cache[sap_code] = CatalogEntry(
            row_id=200,
            sap_code=sap_code,
            canonical_code=canonical_code,
            uom=uom,
            sap_uom=sap_uom,
            conversion_factor=conv_factor,
        )
    service._catalog_cache_timestamp = datetime.utcnow()


@pytest.mark.unit
class TestMappingServiceV2:

    # ── Core 3-Sheet Flow ──────────────────────────────────────────────

    def test_auto_mapping_with_catalog_conversion(self, mapping_service):
        """
        05a match + no override → use default SAP code → 05c provides conversion.
        """
        _seed_master_cache(mapping_service, [
            ("aluminum tape", "CAN-TAPE-001", "10001234"),
        ])
        _seed_catalog_cache(mapping_service, [
            ("10001234", "CAN-TAPE-001", "m", "ROL", 50.0),
        ])

        result = mapping_service.lookup("Aluminum Tape")

        assert result.success is True
        assert result.decision == "AUTO"
        assert result.canonical_code == "CAN-TAPE-001"
        assert result.sap_code == "10001234"
        assert result.uom == "m"
        assert result.sap_uom == "ROL"
        assert result.conversion_factor == 50.0

    def test_brand_override_picks_different_sap_code(self, mapping_service, mock_smartsheet_client):
        """
        05a match → 05b BRAND override → different SAP code → 05c conversion for THAT code.
        """
        _seed_master_cache(mapping_service, [
            ("aluminum tape", "CAN-TAPE-001", "10001234"),
        ])
        _seed_catalog_cache(mapping_service, [
            ("10001234", "CAN-TAPE-001", "m", "ROL", 50.0),  # WTI default
            ("10005678", "CAN-TAPE-001", "m", "ROL", 25.0),  # KIMMCO
        ])

        # Mock override cache with BRAND override
        with patch.object(mapping_service, '_get_override_column_ids', return_value={
            "SCOPE_TYPE": 1, "SCOPE_VALUE": 2, "NESTING_DESCRIPTION": 3,
            "CANONICAL_CODE": 4, "SAP_CODE": 5, "ACTIVE": 6,
            "EFFECTIVE_FROM": 7, "EFFECTIVE_TO": 8,
        }):
            mapping_service._override_cache = [
                {"cells": [
                    {"columnId": 1, "value": "BRAND"},
                    {"columnId": 2, "value": "KIMMCO"},
                    {"columnId": 3, "value": "aluminum tape"},
                    {"columnId": 4, "value": "CAN-TAPE-001"},
                    {"columnId": 5, "value": "10005678"},
                    {"columnId": 6, "value": "Yes"},
                ]},
            ]
            mapping_service._override_cache_timestamp = datetime.utcnow()

            result = mapping_service.lookup("Aluminum Tape", brand="KIMMCO")

        assert result.success is True
        assert result.decision == "OVERRIDE"
        assert result.sap_code == "10005678"
        assert result.conversion_factor == 25.0  # From 05c for KIMMCO SAP code

    def test_lpo_override_beats_brand_override(self, mapping_service):
        """
        LPO override should win over BRAND override per precedence.
        """
        _seed_master_cache(mapping_service, [
            ("aluminum tape", "CAN-TAPE-001", "10001234"),
        ])
        _seed_catalog_cache(mapping_service, [
            ("10001234", "CAN-TAPE-001", "m", "ROL", 50.0),
            ("10005678", "CAN-TAPE-001", "m", "ROL", 25.0),
            ("10009999", "CAN-TAPE-001", "m", "ROL", 30.0),
        ])

        with patch.object(mapping_service, '_get_override_column_ids', return_value={
            "SCOPE_TYPE": 1, "SCOPE_VALUE": 2, "NESTING_DESCRIPTION": 3,
            "CANONICAL_CODE": 4, "SAP_CODE": 5, "ACTIVE": 6,
            "EFFECTIVE_FROM": 7, "EFFECTIVE_TO": 8,
        }):
            mapping_service._override_cache = [
                # BRAND override → 10005678
                {"cells": [
                    {"columnId": 1, "value": "BRAND"},
                    {"columnId": 2, "value": "WTI"},
                    {"columnId": 3, "value": "aluminum tape"},
                    {"columnId": 4, "value": "CAN-TAPE-001"},
                    {"columnId": 5, "value": "10005678"},
                    {"columnId": 6, "value": "Yes"},
                ]},
                # LPO override → 10009999
                {"cells": [
                    {"columnId": 1, "value": "LPO"},
                    {"columnId": 2, "value": "LPO-001"},
                    {"columnId": 3, "value": "aluminum tape"},
                    {"columnId": 4, "value": "CAN-TAPE-001"},
                    {"columnId": 5, "value": "10009999"},
                    {"columnId": 6, "value": "Yes"},
                ]},
            ]
            mapping_service._override_cache_timestamp = datetime.utcnow()

            result = mapping_service.lookup(
                "Aluminum Tape", brand="WTI", lpo_id="LPO-001"
            )

        assert result.decision == "OVERRIDE"
        assert result.sap_code == "10009999"  # LPO wins
        assert result.conversion_factor == 30.0

    def test_no_override_falls_back_to_default(self, mapping_service):
        """
        No matching override → use default_sap_code from 05a → 05c conversion.
        """
        _seed_master_cache(mapping_service, [
            ("silicone", "CAN-SIL-001", "20001111"),
        ])
        _seed_catalog_cache(mapping_service, [
            ("20001111", "CAN-SIL-001", "kg", "KG", 1.0),
        ])

        # Empty override cache
        mapping_service._override_cache = []
        mapping_service._override_cache_timestamp = datetime.utcnow()

        result = mapping_service.lookup("Silicone", brand="WTI")

        assert result.success is True
        assert result.decision == "AUTO"
        assert result.sap_code == "20001111"
        assert result.conversion_factor == 1.0

    def test_sap_code_not_in_catalog_still_succeeds(self, mapping_service):
        """
        05a match but SAP code not in 05c → result succeeds but no conversion factor.
        """
        _seed_master_cache(mapping_service, [
            ("gi corners", "CAN-GI-001", "30001111"),
        ])
        # Empty catalog
        mapping_service._catalog_cache = {}
        mapping_service._catalog_cache_timestamp = datetime.utcnow()

        result = mapping_service.lookup("GI Corners")

        assert result.success is True
        assert result.canonical_code == "CAN-GI-001"
        assert result.sap_code == "30001111"
        assert result.conversion_factor is None  # Not in catalog

    # ── Idempotency ────────────────────────────────────────────────────

    def test_idempotency_history_reconstruction(self, mapping_service, mock_smartsheet_client):
        """
        If history exists, return it EXACTLY without re-running lookup logic.
        """
        mock_smartsheet_client.find_row.return_value = {
            "Canonical Code": "HIST-CODE",
            "SAP Code": "HIST-SAP",
            "Decision": "MANUAL",
            "Conversion Factor": 50.0
        }

        result = mapping_service.lookup("mat", ingest_line_id="EXISTING-ID")

        assert result.success is True
        assert result.canonical_code == "HIST-CODE"
        assert result.decision == "MANUAL"
        assert result.conversion_factor == 50.0
        mock_smartsheet_client.get_all_rows.assert_not_called()

    # ── No Match ───────────────────────────────────────────────────────

    def test_no_match_creates_exception(self, mapping_service, mock_smartsheet_client):
        """Unknown material creates exception and returns REVIEW."""
        mapping_service._material_master_cache = {}
        mapping_service._cache_timestamp = datetime.utcnow()

        with patch.object(mapping_service, '_get_exception_column_ids', return_value={
            "EXCEPTION_ID": 1, "INGEST_LINE_ID": 2, "NESTING_DESCRIPTION": 3,
            "STATUS": 4, "CREATED_AT": 5, "TRACE_ID": 6
        }):
            with patch.object(mapping_service, '_get_history_column_ids', return_value={
                "HISTORY_ID": 1, "INGEST_LINE_ID": 2, "NESTING_DESCRIPTION": 3,
                "CANONICAL_CODE": 4, "SAP_CODE": 5, "DECISION": 6,
                "TRACE_ID": 7, "CREATED_AT": 8, "NOTES": 9
            }):
                result = mapping_service.lookup("Unobtainium")

        assert result.success is False
        assert result.decision == "REVIEW"
        assert result.exception_id.startswith("MAPEX-")

    # ── Cache Logic ────────────────────────────────────────────────────

    def test_cache_avoids_api_calls(self, mapping_service, mock_smartsheet_client):
        """Fresh cache should NOT trigger API call."""
        mapping_service._cache_timestamp = datetime.utcnow()
        mapping_service._lookup_material_master("test")
        mock_smartsheet_client.get_all_rows.assert_not_called()

    def test_catalog_cache_avoids_api_calls(self, mapping_service, mock_smartsheet_client):
        """Fresh catalog cache should NOT trigger API call."""
        mapping_service._catalog_cache_timestamp = datetime.utcnow()
        mapping_service._lookup_catalog("SAP-123")
        mock_smartsheet_client.get_all_rows.assert_not_called()

    def test_cache_invalidation_clears_all_caches(self, mapping_service):
        """Invalidate should clear all three cache timestamps."""
        mapping_service._cache_timestamp = datetime.utcnow()
        mapping_service._catalog_cache_timestamp = datetime.utcnow()
        mapping_service._override_cache_timestamp = datetime.utcnow()

        mapping_service.invalidate_cache()

        assert mapping_service._cache_timestamp is None
        assert mapping_service._catalog_cache_timestamp is None
        assert mapping_service._override_cache_timestamp is None

    def test_master_refresh_from_smartsheet(self, mapping_service, mock_smartsheet_client):
        """Verify Material Master cache is populated from Smartsheet API."""
        with patch.object(mapping_service, '_get_material_master_column_ids', return_value={
            "NESTING_DESCRIPTION": 1, "CANONICAL_CODE": 2, "ACTIVE": 3,
            "DEFAULT_SAP_CODE": 4, "NOT_TRACKED": 5
        }):
            mock_smartsheet_client.get_all_rows.return_value = [{
                "id": 100,
                "cells": [
                    {"columnId": 1, "value": "pir 25mm"},
                    {"columnId": 2, "value": "CAN-PIR-25"},
                    {"columnId": 3, "value": "Yes"},
                    {"columnId": 4, "value": "SAP-PIR-25"},
                    {"columnId": 5, "value": "No"},
                ]
            }]

            mapping_service._cache_timestamp = None
            mapping_service._lookup_material_master("pir 25mm")

            assert "pir 25mm" in mapping_service._material_master_cache
            entry = mapping_service._material_master_cache["pir 25mm"]
            assert entry.canonical_code == "CAN-PIR-25"
            assert entry.default_sap_code == "SAP-PIR-25"

    def test_catalog_refresh_from_smartsheet(self, mapping_service, mock_smartsheet_client):
        """Verify SAP Catalog cache is populated from Smartsheet API."""
        with patch.object(mapping_service, '_get_catalog_column_ids', return_value={
            "SAP_CODE": 1, "CANONICAL_CODE": 2, "NESTING_DESCRIPTION": 3,
            "UOM": 4, "SAP_UOM": 5, "CONVERSION_FACTOR": 6,
            "ACTIVE": 7, "NOT_TRACKED": 8
        }):
            mock_smartsheet_client.get_all_rows.return_value = [{
                "id": 200,
                "cells": [
                    {"columnId": 1, "value": "SAP-PIR-25"},
                    {"columnId": 2, "value": "CAN-PIR-25"},
                    {"columnId": 3, "value": "pir 25mm"},
                    {"columnId": 4, "value": "m2"},
                    {"columnId": 5, "value": "SHT"},
                    {"columnId": 6, "value": "3.72"},
                    {"columnId": 7, "value": "Yes"},
                    {"columnId": 8, "value": "No"},
                ]
            }]

            mapping_service._catalog_cache_timestamp = None
            result = mapping_service._lookup_catalog("SAP-PIR-25")

            assert result is not None
            assert result.sap_code == "SAP-PIR-25"
            assert result.uom == "m2"
            assert result.sap_uom == "SHT"
            assert result.conversion_factor == 3.72

    # ── Cache Stats ────────────────────────────────────────────────────

    def test_get_cache_stats(self, mapping_service):
        """Verify cache stats report all three caches."""
        _seed_master_cache(mapping_service, [("test", "CAN-TEST", "SAP-TEST")])
        _seed_catalog_cache(mapping_service, [("SAP-TEST", "CAN-TEST", "m", "M", 1.0)])
        mapping_service._override_cache = [{"cells": []}]

        stats = mapping_service.get_cache_stats()

        assert stats["material_master_entries"] == 1
        assert stats["catalog_entries"] == 1
        assert stats["override_entries"] == 1
        assert stats["is_stale"] is False
