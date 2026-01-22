"""
Unit Tests for Event Router (v1.4.0)

Tests the ID-based event routing logic.
All routing uses immutable Smartsheet IDs for resilience to renames.
"""

import pytest
import json
from unittest.mock import MagicMock, patch, mock_open
from pathlib import Path

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fn_event_dispatcher.models import (
    RowEvent,
    EventAction,
    RoutingConfig,
    SheetRoute,
    RouteConfig,
    HandlerConfig,
)
from fn_event_dispatcher.router import (
    load_routing_config,
    build_routing_table,
    get_handler_for_event,
    get_handler_config,
    is_handler_implemented,
    reset_routing,
)


@pytest.fixture(autouse=True)
def reset_router_state():
    """Reset router singleton state before each test."""
    reset_routing()
    yield
    reset_routing()


@pytest.fixture
def mock_manifest():
    """Create a mock manifest with sheet ID mappings."""
    manifest = MagicMock()
    manifest.get_sheet_id.side_effect = lambda name: {
        "01H_LPO_INGESTION": 1111111111111111,
        "02_TAG_REGISTRY": 2222222222222222,
        "03_TAG_MASTER": 3333333333333333,
    }.get(name)
    return manifest


@pytest.fixture
def sample_routing_json():
    """Sample routing configuration JSON."""
    return {
        "routes": [
            {
                "logical_sheet": "01H_LPO_INGESTION",
                "description": "LPO ingestion events",
                "actions": {
                    "created": {"handler": "handle_lpo_ingest", "enabled": True},
                    "updated": {"handler": "handle_lpo_update", "enabled": True}
                }
            },
            {
                "logical_sheet": "02_TAG_REGISTRY",
                "description": "Tag registry events",
                "actions": {
                    "created": {"handler": "handle_tag_ingest", "enabled": True},
                    "updated": {"handler": "handle_tag_update", "enabled": False}
                }
            }
        ],
        "handler_config": {
            "handle_lpo_ingest": {"function": "fn_lpo_ingest", "timeout_seconds": 30},
            "handle_lpo_update": {"function": "fn_lpo_update", "timeout_seconds": 30},
            "handle_tag_ingest": {"function": "fn_ingest_tag", "timeout_seconds": 60},
            "handle_tag_update": {"function": "fn_update_tag", "not_implemented": True}
        },
        "global_settings": {
            "ignore_system_actors": True,
            "log_ignored_events": False
        }
    }


@pytest.mark.unit
class TestLoadRoutingConfig:
    """Tests for load_routing_config function."""
    
    def test_load_routing_config_success(self, sample_routing_json):
        """Test successful loading of routing config."""
        json_content = json.dumps(sample_routing_json)
        
        with patch("builtins.open", mock_open(read_data=json_content)):
            with patch.object(Path, "exists", return_value=True):
                config = load_routing_config()
        
        assert len(config.routes) == 2
        assert config.routes[0].logical_sheet == "01H_LPO_INGESTION"
        
    def test_load_routing_config_missing_file(self):
        """Test handling of missing config file."""
        with patch.object(Path, "exists", return_value=False):
            config = load_routing_config()
        
        # Should return empty config, not crash
        assert config.routes == []
        assert config.handler_config == {}


@pytest.mark.unit
class TestBuildRoutingTable:
    """Tests for build_routing_table function."""
    
    def test_build_routing_table_success(self, mock_manifest, sample_routing_json):
        """Test building routing table from config and manifest."""
        json_content = json.dumps(sample_routing_json)
        
        with patch("builtins.open", mock_open(read_data=json_content)):
            with patch.object(Path, "exists", return_value=True):
                build_routing_table(mock_manifest)
        
        # Test routing by creating an event
        event = RowEvent(
            sheet_id=1111111111111111,  # 01H_LPO_INGESTION
            row_id=999,
            action=EventAction.CREATED
        )
        
        handler, logical_sheet = get_handler_for_event(event)
        
        assert handler == "handle_lpo_ingest"
        assert logical_sheet == "01H_LPO_INGESTION"
        
    def test_build_routing_table_disabled_action(self, mock_manifest, sample_routing_json):
        """Test that disabled actions are not routed."""
        json_content = json.dumps(sample_routing_json)
        
        with patch("builtins.open", mock_open(read_data=json_content)):
            with patch.object(Path, "exists", return_value=True):
                build_routing_table(mock_manifest)
        
        # Tag update is disabled
        event = RowEvent(
            sheet_id=2222222222222222,  # 02_TAG_REGISTRY
            row_id=999,
            action=EventAction.UPDATED
        )
        
        handler, logical_sheet = get_handler_for_event(event)
        
        assert handler is None  # Disabled action


@pytest.mark.unit
class TestGetHandlerForEvent:
    """Tests for get_handler_for_event function."""
    
    def test_get_handler_unknown_sheet(self, mock_manifest, sample_routing_json):
        """Test handling of unknown sheet ID."""
        json_content = json.dumps(sample_routing_json)
        
        with patch("builtins.open", mock_open(read_data=json_content)):
            with patch.object(Path, "exists", return_value=True):
                build_routing_table(mock_manifest)
        
        event = RowEvent(
            sheet_id=9999999999999999,  # Unknown sheet
            row_id=999,
            action=EventAction.CREATED
        )
        
        handler, logical_sheet = get_handler_for_event(event)
        
        assert handler is None
        assert logical_sheet is None
        
    def test_get_handler_unknown_action(self, mock_manifest, sample_routing_json):
        """Test handling of unknown action for known sheet."""
        json_content = json.dumps(sample_routing_json)
        
        with patch("builtins.open", mock_open(read_data=json_content)):
            with patch.object(Path, "exists", return_value=True):
                build_routing_table(mock_manifest)
        
        event = RowEvent(
            sheet_id=1111111111111111,  # Known sheet
            row_id=999,
            action=EventAction.DELETED  # No handler for delete
        )
        
        handler, logical_sheet = get_handler_for_event(event)
        
        assert handler is None


@pytest.mark.unit
class TestGetHandlerConfig:
    """Tests for get_handler_config function."""
    
    def test_get_handler_config_exists(self, sample_routing_json):
        """Test getting config for existing handler."""
        json_content = json.dumps(sample_routing_json)
        
        with patch("builtins.open", mock_open(read_data=json_content)):
            with patch.object(Path, "exists", return_value=True):
                load_routing_config()  # Populate singleton
                
                config = get_handler_config("handle_lpo_ingest")
        
        assert config is not None
        assert config.function == "fn_lpo_ingest"
        assert config.timeout_seconds == 30
        
    def test_get_handler_config_not_exists(self, sample_routing_json):
        """Test getting config for non-existent handler."""
        json_content = json.dumps(sample_routing_json)
        
        with patch("builtins.open", mock_open(read_data=json_content)):
            with patch.object(Path, "exists", return_value=True):
                load_routing_config()
                
                config = get_handler_config("handle_nonexistent")
        
        assert config is None


@pytest.mark.unit
class TestIsHandlerImplemented:
    """Tests for is_handler_implemented function."""
    
    def test_handler_implemented(self, sample_routing_json):
        """Test checking implemented handler."""
        json_content = json.dumps(sample_routing_json)
        
        with patch("builtins.open", mock_open(read_data=json_content)):
            with patch.object(Path, "exists", return_value=True):
                load_routing_config()
                
                assert is_handler_implemented("handle_lpo_ingest") is True
                
    def test_handler_not_implemented(self, sample_routing_json):
        """Test checking not-implemented handler."""
        json_content = json.dumps(sample_routing_json)
        
        with patch("builtins.open", mock_open(read_data=json_content)):
            with patch.object(Path, "exists", return_value=True):
                load_routing_config()
                
                # handle_tag_update has not_implemented: true
                assert is_handler_implemented("handle_tag_update") is False
                
    def test_handler_unknown(self, sample_routing_json):
        """Test checking unknown handler."""
        json_content = json.dumps(sample_routing_json)
        
        with patch("builtins.open", mock_open(read_data=json_content)):
            with patch.object(Path, "exists", return_value=True):
                load_routing_config()
                
                assert is_handler_implemented("handle_unknown") is False
