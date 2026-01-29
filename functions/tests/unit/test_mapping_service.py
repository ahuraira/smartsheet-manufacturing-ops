"""
Unit Tests for Mapping Service (v1.6.0)

Tests the core mapping logic, cache behavior, and data reconstruction.
RUTHLESSLY validates:
1. Scope Precedence (LPO > PROJECT > CUSTOMER)
2. Cache Invalidation and Refresh
3. Idempotency (History Reconstruction)
4. Failure Handling (Review Decision)
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
from fn_map_lookup.mapping_service import MappingService, MappingResult, MaterialMasterEntry

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
    # Clear cache explicitly
    service._material_master_cache = {}
    return service

@pytest.mark.unit
class TestMappingService:
    
    def test_lookup_precedence_lpo_wins(self, mapping_service, mock_smartsheet_client):
        """
        Verify LPO Override beats Project and Customer.
        Setup: Override table has matches for LPO, Project, and Customer.
        Expectation: LPO override is chosen.
        """
        # Mock Override Table Response
        # We need to mock _get_override_column_ids to avoid manifest dependency in unit test
        with patch.object(mapping_service, '_get_override_column_ids', return_value={
            "SCOPE_TYPE": 1, "SCOPE_VALUE": 2, "NESTING_DESCRIPTION": 3, 
            "CANONICAL_CODE": 4, "SAP_CODE": 5, "ACTIVE": 6
        }):
            # Rows for LPO, Project, Customer matches
            mock_smartsheet_client.get_all_rows.return_value = [
                {"cells": [{"columnId": 1, "value": "CUSTOMER"}, {"columnId": 2, "value": "CUST-1"}, {"columnId": 3, "value": "mat"}, {"columnId": 4, "value": "CUST-CODE"}]},
                {"cells": [{"columnId": 1, "value": "PROJECT"}, {"columnId": 2, "value": "PROJ-1"}, {"columnId": 3, "value": "mat"}, {"columnId": 4, "value": "PROJ-CODE"}]},
                {"cells": [{"columnId": 1, "value": "LPO"}, {"columnId": 2, "value": "LPO-1"}, {"columnId": 3, "value": "mat"}, {"columnId": 4, "value": "LPO-CODE"}]},
            ]

            result = mapping_service.lookup("mat", lpo_id="LPO-1", project_id="PROJ-1", customer_id="CUST-1")
            
            assert result.success is True
            assert result.decision == "OVERRIDE"
            assert result.canonical_code == "LPO-CODE"

    def test_lookup_precedence_project_wins_over_customer(self, mapping_service, mock_smartsheet_client):
        """Verify Project Override beats Customer when LPO is missing."""
        with patch.object(mapping_service, '_get_override_column_ids', return_value={
            "SCOPE_TYPE": 1, "SCOPE_VALUE": 2, "NESTING_DESCRIPTION": 3, 
            "CANONICAL_CODE": 4, "SAP_CODE": 5, "ACTIVE": 6
        }):
            mock_smartsheet_client.get_all_rows.return_value = [
                {"cells": [{"columnId": 1, "value": "CUSTOMER"}, {"columnId": 2, "value": "CUST-1"}, {"columnId": 3, "value": "mat"}, {"columnId": 4, "value": "CUST-CODE"}]},
                {"cells": [{"columnId": 1, "value": "PROJECT"}, {"columnId": 2, "value": "PROJ-1"}, {"columnId": 3, "value": "mat"}, {"columnId": 4, "value": "PROJ-CODE"}]},
            ]

            result = mapping_service.lookup("mat", lpo_id="LPO-NONE", project_id="PROJ-1", customer_id="CUST-1")
            
            assert result.canonical_code == "PROJ-CODE"

    def test_lookup_master_exact_match(self, mapping_service, mock_smartsheet_client):
        """Verify fallback to Material Master if no overrides."""
        # Mock Manifest Column IDs
        # Instead of patching _get_material_master_column_ids which calls manifest[], 
        # let's try populating the cache directly (which bypasses API lookup)
        # BUT we want to verify lookup logic.
        
        # The error was: 'WorkspaceManifest' object is not subscriptable in _get_material_master_column_ids
        # This implies MockWorkspaceManifest does not support __getitem__.
        # We should patch the helper method to return simple dict.
        
        with patch.object(mapping_service, '_get_material_master_column_ids', return_value={
            "NESTING_DESCRIPTION": 1, "CANONICAL_CODE": 2, "ACTIVE": 3,
            "DEFAULT_SAP_CODE": 4, "UOM": 5, "CONVERSION_FACTOR": 6
        }):
            # Now we mock the SMARTSHEET response
            mock_smartsheet_client.get_all_rows.return_value = [{
                "id": 100,
                "cells": [
                    {"columnId": 1, "value": "alum tape"},
                    {"columnId": 2, "value": "MAT-MASTER-CODE"},
                    {"columnId": 3, "value": "Yes"},
                    {"columnId": 4, "value": "SAP-111"},
                    {"columnId": 5, "value": "roll"},
                    {"columnId": 6, "value": "30.0"}
                ]
            }]
            
            # Clear cache to force lookup
            mapping_service._material_master_cache = {}
            mapping_service._cache_timestamp = None
            
            result = mapping_service.lookup("Alum Tape")
            
            assert result.success is True
            assert result.decision == "AUTO"
            assert result.canonical_code == "MAT-MASTER-CODE"
            assert result.conversion_factor == 30.0

    def test_cache_logic_avoids_api_calls(self, mapping_service, mock_smartsheet_client):
        """Verify API is NOT called if cache is fresh."""
        mapping_service._cache_timestamp = datetime.utcnow() # Fresh timestamp
        
        # Should NOT call get_all_rows(MATERIAL_MASTER)
        mapping_service._lookup_material_master("test")
        
        mock_smartsheet_client.get_all_rows.assert_not_called()

    def test_cache_invalidation_forces_refresh(self, mapping_service, mock_smartsheet_client):
        """Verify invalidation forces API call."""
        mapping_service.invalidate_cache()
        
        # Mocking refresh dependencies
        with patch.object(mapping_service, '_get_material_master_column_ids', return_value={
            "NESTING_DESCRIPTION": 1, "CANONICAL_CODE": 2, "ACTIVE": 3
        }):
            mock_smartsheet_client.get_all_rows.return_value = []
            
            mapping_service._lookup_material_master("test")
            
            # Should have called API now
            mock_smartsheet_client.get_all_rows.assert_called_once()

    def test_idempotency_histor_reconstruction(self, mapping_service, mock_smartsheet_client):
        """
        Critical Test: Verify that if history exists, we return it EXACTLY
        without looking up logic again.
        """
        # Simulate existing history row
        mock_smartsheet_client.find_row.return_value = {
            "Canonical Code": "HIST-CODE",
            "SAP Code": "HIST-SAP",
            "Decision": "MANUAL", # Different from AUTO
            "Conversion Factor": 50.0 # Stored factor
        }
        
        result = mapping_service.lookup("mat", ingest_line_id="EXISTING-ID")
        
        assert result.success is True
        assert result.canonical_code == "HIST-CODE"
        assert result.decision == "MANUAL" # Preserved previous decision
        assert result.conversion_factor == 50.0
        
        # Verify no overrides or master lookup happened
        mock_smartsheet_client.get_all_rows.assert_not_called() # No override check

    def test_lookup_no_match_creates_exception(self, mapping_service, mock_smartsheet_client):
        """Verify unknown material creates exception and returns REVIEW."""
        mock_smartsheet_client.get_all_rows.return_value = [] # No overrides
        mapping_service._material_master_cache = {} # Empty cache
        
        # Mock column helpers
        with patch.object(mapping_service, '_get_exception_column_ids', return_value={"EXCEPTION_ID": 1, "INGEST_LINE_ID": 2, "NESTING_DESCRIPTION": 3, "STATUS": 4, "CREATED_AT": 5, "TRACE_ID": 6}):
            with patch.object(mapping_service, '_get_history_column_ids', return_value={"HISTORY_ID": 1, "INGEST_LINE_ID": 2, "NESTING_DESCRIPTION": 3, "CANONICAL_CODE": 4, "SAP_CODE": 5, "DECISION": 6, "TRACE_ID": 7, "CREATED_AT": 8, "NOTES": 9}):
                
                result = mapping_service.lookup("Unobtainium")
                
                assert result.success is False
                assert result.decision == "REVIEW"
                assert result.exception_id.startswith("MAPEX-")
                
                # Verify exception row added
                args, _ = mock_smartsheet_client.add_row.call_args_list[0] # Exception add
                # Mock client uses logical names
                assert args[0] == "MAPPING_EXCEPTION"

