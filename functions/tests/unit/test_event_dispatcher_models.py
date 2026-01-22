"""
Unit Tests for Event Dispatcher Models (v1.4.0)

Tests Pydantic models used by fn_event_dispatcher for event routing.
All models use immutable Smartsheet IDs (not names) for resilience.
"""

import pytest
from pydantic import ValidationError

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fn_event_dispatcher.models import (
    EventAction,
    ObjectType,
    RowEvent,
    RouteConfig,
    SheetRoute,
    HandlerConfig,
    GlobalSettings,
    RoutingConfig,
    DispatchResult,
)


@pytest.mark.unit
class TestEventActionEnum:
    """Tests for EventAction enum."""
    
    def test_event_action_values(self):
        """Test all EventAction enum values exist."""
        assert EventAction.CREATED.value == "created"
        assert EventAction.UPDATED.value == "updated"
        assert EventAction.DELETED.value == "deleted"
        
    def test_event_action_count(self):
        """Test expected number of actions."""
        assert len(EventAction) == 3


@pytest.mark.unit
class TestObjectTypeEnum:
    """Tests for ObjectType enum."""
    
    def test_object_type_values(self):
        """Test all ObjectType enum values."""
        assert ObjectType.ROW.value == "row"
        assert ObjectType.CELL.value == "cell"
        assert ObjectType.ATTACHMENT.value == "attachment"
        assert ObjectType.COMMENT.value == "comment"


@pytest.mark.unit
class TestRowEventModel:
    """Tests for RowEvent model - the core event structure."""
    
    def test_row_event_minimal_valid(self):
        """Test RowEvent with minimal required fields."""
        event = RowEvent(
            sheet_id=1234567890123456,
            row_id=9876543210987654,
            action=EventAction.CREATED
        )
        
        assert event.sheet_id == 1234567890123456
        assert event.row_id == 9876543210987654
        assert event.action == "created"
        assert event.object_type == "row"  # Default
        assert event.source == "WEBHOOK_ADAPTER"  # Default
        
    def test_row_event_full(self):
        """Test RowEvent with all fields populated."""
        event = RowEvent(
            event_id="evt-123",
            source="MANUAL_TRIGGER",
            sheet_id=1111111111111111,
            row_id=2222222222222222,
            action=EventAction.UPDATED,
            object_type=ObjectType.CELL,
            actor_id="user@example.com",
            timestamp_utc="2026-01-20T12:00:00Z",
            trace_id="trace-abc-123"
        )
        
        assert event.event_id == "evt-123"
        assert event.trace_id == "trace-abc-123"
        assert event.actor_id == "user@example.com"
        
    def test_row_event_missing_required_fields(self):
        """Test RowEvent validation fails without required fields."""
        with pytest.raises(ValidationError) as exc_info:
            RowEvent(action=EventAction.CREATED)  # Missing sheet_id and row_id
        
        errors = exc_info.value.errors()
        error_fields = [e["loc"][0] for e in errors]
        assert "sheet_id" in error_fields
        assert "row_id" in error_fields
        
    def test_row_event_uses_enum_values(self):
        """Test that enum values are serialized as strings."""
        event = RowEvent(
            sheet_id=123,
            row_id=456,
            action=EventAction.DELETED
        )
        
        data = event.model_dump()
        assert data["action"] == "deleted"
        assert data["object_type"] == "row"


@pytest.mark.unit
class TestRouteConfigModel:
    """Tests for RouteConfig - single action routing config."""
    
    def test_route_config_defaults(self):
        """Test RouteConfig default values."""
        config = RouteConfig(handler="handle_lpo_ingest")
        
        assert config.handler == "handle_lpo_ingest"
        assert config.enabled is True  # Default
        assert config.comment is None
        
    def test_route_config_disabled(self):
        """Test disabled route configuration."""
        config = RouteConfig(
            handler="handle_future",
            enabled=False,
            comment="Not yet implemented"
        )
        
        assert config.enabled is False


@pytest.mark.unit
class TestSheetRouteModel:
    """Tests for SheetRoute - routing config for a sheet."""
    
    def test_sheet_route_with_actions(self):
        """Test SheetRoute with multiple actions."""
        route = SheetRoute(
            logical_sheet="01H_LPO_INGESTION",
            description="LPO ingestion handling",
            actions={
                "created": RouteConfig(handler="handle_lpo_ingest"),
                "updated": RouteConfig(handler="handle_lpo_update"),
            }
        )
        
        assert route.logical_sheet == "01H_LPO_INGESTION"
        assert len(route.actions) == 2
        assert route.actions["created"].handler == "handle_lpo_ingest"


@pytest.mark.unit
class TestHandlerConfigModel:
    """Tests for HandlerConfig - handler function settings."""
    
    def test_handler_config_defaults(self):
        """Test HandlerConfig default values."""
        config = HandlerConfig(function="fn_lpo_ingest")
        
        assert config.function == "fn_lpo_ingest"
        assert config.timeout_seconds == 30
        assert config.retry_on_failure is True
        assert config.not_implemented is False
        
    def test_handler_config_not_implemented(self):
        """Test marking handler as not implemented."""
        config = HandlerConfig(
            function="fn_future",
            not_implemented=True
        )
        
        assert config.not_implemented is True


@pytest.mark.unit
class TestGlobalSettingsModel:
    """Tests for GlobalSettings - system-wide settings."""
    
    def test_global_settings_defaults(self):
        """Test GlobalSettings default values."""
        settings = GlobalSettings()
        
        assert settings.ignore_system_actors is True
        assert settings.log_ignored_events is False
        assert settings.default_timeout_seconds == 30


@pytest.mark.unit
class TestRoutingConfigModel:
    """Tests for RoutingConfig - complete routing configuration."""
    
    def test_routing_config_empty(self):
        """Test empty RoutingConfig (valid for missing config file)."""
        config = RoutingConfig()
        
        assert config.routes == []
        assert config.handler_config == {}
        assert config.global_settings.default_timeout_seconds == 30
        
    def test_routing_config_from_dict(self):
        """Test RoutingConfig parsing from dict (simulating JSON load)."""
        data = {
            "routes": [
                {
                    "logical_sheet": "01H_LPO_INGESTION",
                    "description": "LPO events",
                    "actions": {
                        "created": {"handler": "handle_lpo_ingest", "enabled": True}
                    }
                }
            ],
            "handler_config": {
                "handle_lpo_ingest": {
                    "function": "fn_lpo_ingest",
                    "timeout_seconds": 60
                }
            },
            "global_settings": {
                "ignore_system_actors": True
            }
        }
        
        config = RoutingConfig(**data)
        
        assert len(config.routes) == 1
        assert config.routes[0].logical_sheet == "01H_LPO_INGESTION"
        assert config.handler_config["handle_lpo_ingest"].timeout_seconds == 60


@pytest.mark.unit
class TestDispatchResultModel:
    """Tests for DispatchResult - event processing result."""
    
    def test_dispatch_result_success(self):
        """Test successful dispatch result."""
        result = DispatchResult(
            status="OK",
            handler="handle_lpo_ingest",
            message="Processed successfully",
            trace_id="trace-123",
            processing_time_ms=45.5
        )
        
        assert result.status == "OK"
        assert result.processing_time_ms == 45.5
        
    def test_dispatch_result_ignored(self):
        """Test ignored event result."""
        result = DispatchResult(
            status="IGNORED",
            message="No route configured for sheet"
        )
        
        assert result.status == "IGNORED"
        assert result.handler is None
        
    def test_dispatch_result_not_implemented(self):
        """Test not implemented handler result."""
        result = DispatchResult(
            status="NOT_IMPLEMENTED",
            handler="handle_future",
            message="Handler marked as not_implemented"
        )
        
        assert result.status == "NOT_IMPLEMENTED"


@pytest.mark.unit
class TestRowEventResilience:
    """Tests for resilient validator logic in RowEvent."""

    def test_id_coercion_string_to_int(self):
        """Test converting string IDs to integers."""
        event = RowEvent(
            sheet_id="123456",
            row_id="987654",
            action="created"
        )
        assert isinstance(event.sheet_id, int)
        assert event.sheet_id == 123456
        assert isinstance(event.row_id, int) 
        assert event.row_id == 987654

    def test_id_coercion_float_to_int(self):
        """Test converting float IDs to integers."""
        event = RowEvent(
            sheet_id=123.0,
            row_id=456.0,
            action="created"
        )
        assert event.sheet_id == 123
        assert event.row_id == 456

    def test_action_normalization(self):
        """Test action case insensitivity."""
        event = RowEvent(
            sheet_id=1,
            row_id=2,
            action="CREATED" 
        )
        assert event.action == "created"
        assert event.object_type == "row"

    def test_optional_field_coercion(self):
        """Test coercion of optional fields to string."""
        event = RowEvent(
            sheet_id=1,
            row_id=2,
            action="created",
            actor=12345, # Passed as int via alias
            trace_id=999
        )
        assert event.actor_id == "12345"
        assert event.trace_id == "999"

    def test_extra_field_resilience(self):
        """Test ignoring extra fields without error."""
        event = RowEvent(
            sheet_id=1,
            row_id=2,
            action="created",
            extra_field="should_be_ignored"
        )
        # Should not raise validation error
        assert event.sheet_id == 1
