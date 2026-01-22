"""
Event Dispatcher Models
=======================

Pydantic models for event processing.
All IDs are immutable Smartsheet IDs (not names).
"""

from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field, field_validator


class EventAction(str, Enum):
    """Smartsheet event actions."""
    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"


class ObjectType(str, Enum):
    """Smartsheet object types."""
    ROW = "row"
    CELL = "cell"
    ATTACHMENT = "attachment"
    COMMENT = "comment"


class RowEvent(BaseModel):
    """
    Incoming event from Service Bus via adapter.
    
    RESILIENT MODEL - handles any reasonable input type:
    - IDs: accepts string or int, stores as int
    - Strings: accepts any type, converts to string
    - Action: normalizes to lowercase
    """
    event_id: Optional[str] = None
    source: str = "WEBHOOK_ADAPTER"
    
    # Immutable IDs (accept string or int, store as int)
    sheet_id: int = Field(..., description="Immutable Smartsheet sheet ID")
    row_id: int = Field(..., description="Immutable Smartsheet row ID")
    
    # Event details (action normalized to lowercase)
    action: str  # created, updated, deleted
    object_type: str = "row"  # row, cell, attachment
    
    # Actor and timestamp
    actor_id: Optional[str] = Field(default=None, alias="actor")
    timestamp_utc: Optional[str] = None
    
    # Correlation
    trace_id: str = Field(default="", description="Trace ID for logging")
    
    # =========================================================================
    # RESILIENT VALIDATORS - Handle any reasonable input type
    # =========================================================================
    
    @field_validator('sheet_id', 'row_id', mode='before')
    @classmethod
    def coerce_to_int(cls, v):
        """Convert any reasonable value to int."""
        if v is None:
            raise ValueError("ID cannot be None")
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            return int(v)
        if isinstance(v, float):
            return int(v)
        return int(str(v))  # Last resort
    
    @field_validator('action', 'object_type', mode='before')
    @classmethod
    def coerce_to_lowercase_string(cls, v):
        """Convert to lowercase string."""
        if v is None:
            return ""
        if isinstance(v, Enum):
            return str(v.value).lower()
        return str(v).lower()
    
    @field_validator('actor_id', 'event_id', 'source', 'timestamp_utc', 'trace_id', mode='before')
    @classmethod
    def coerce_to_string(cls, v):
        """Convert any value to string (or None)."""
        if v is None:
            return None
        if isinstance(v, str):
            return v
        return str(v)
    
    class Config:
        populate_by_name = True  # Accept both "actor" and "actor_id"
        extra = "ignore"  # Ignore unknown fields (resilient to extra data)


class RouteConfig(BaseModel):
    """Configuration for a single route action."""
    handler: str
    enabled: bool = True
    comment: Optional[str] = None


class SheetRoute(BaseModel):
    """Routing configuration for a sheet."""
    logical_sheet: str
    description: str = ""
    actions: Dict[str, RouteConfig] = Field(default_factory=dict)


class HandlerConfig(BaseModel):
    """Configuration for a handler function."""
    function: str
    timeout_seconds: int = 30
    retry_on_failure: bool = True
    not_implemented: bool = False


class GlobalSettings(BaseModel):
    """Global routing settings."""
    ignore_system_actors: bool = True
    log_ignored_events: bool = False
    default_timeout_seconds: int = 30


class RoutingConfig(BaseModel):
    """Complete routing configuration from event_routing.json."""
    routes: List[SheetRoute] = Field(default_factory=list)
    handler_config: Dict[str, HandlerConfig] = Field(default_factory=dict)
    global_settings: GlobalSettings = Field(default_factory=GlobalSettings)


class DispatchResult(BaseModel):
    """Result of event dispatch."""
    status: str  # OK, IGNORED, ERROR, NOT_IMPLEMENTED
    handler: Optional[str] = None
    message: str = ""
    trace_id: str = ""
    processing_time_ms: float = 0.0
    details: Optional[Dict[str, Any]] = None
