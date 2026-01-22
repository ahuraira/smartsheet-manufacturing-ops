"""
Event Router
=============

ID-based routing from event_routing.json config.
Loads config at startup, maps sheet IDs to handlers.

RESILIENCE:
- Uses immutable Smartsheet IDs (not names)
- Routing config is externalized (JSON, not code)
- Sheet/column renames don't affect routing
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, Optional, Tuple, Callable

from .models import (
    RowEvent,
    RoutingConfig,
    SheetRoute,
    HandlerConfig,
    DispatchResult,
)

logger = logging.getLogger(__name__)

# Singleton routing table (use .clear() and .update(), not reassignment!)
_routing_table: Dict[int, Dict[str, str]] = {}  # sheet_id -> {action -> handler}
_routing_config: Optional[RoutingConfig] = None
_sheet_id_to_logical: Dict[int, str] = {}  # For logging


def load_routing_config() -> RoutingConfig:
    """
    Load routing configuration from event_routing.json.
    
    This file is the single source of truth for routing.
    Edit it to add/modify routing without code changes.
    """
    global _routing_config
    
    if _routing_config is not None:
        return _routing_config
    
    # Find config file
    config_path = Path(__file__).parent.parent / "event_routing.json"
    
    if not config_path.exists():
        logger.error(f"Routing config not found: {config_path}")
        _routing_config = RoutingConfig()
        return _routing_config
    
    try:
        with open(config_path, 'r') as f:
            data = json.load(f)
        
        _routing_config = RoutingConfig(**data)
        logger.info(f"Loaded routing config with {len(_routing_config.routes)} routes")
        return _routing_config
        
    except Exception as e:
        logger.error(f"Error loading routing config: {e}")
        _routing_config = RoutingConfig()
        return _routing_config


def build_routing_table(manifest) -> Dict[int, Dict[str, str]]:
    """
    Build ID-based routing table from config + manifest.
    
    The routing table maps:
        sheet_id (immutable) -> { action -> handler_name }
    
    This is called once at startup.
    
    Returns:
        The built routing table
    """
    global _routing_config
    
    config = load_routing_config()
    
    # Clear and rebuild (don't reassign to keep reference)
    _routing_table.clear()
    _sheet_id_to_logical.clear()
    
    for route in config.routes:
        try:
            # Get immutable sheet ID from manifest
            sheet_id = manifest.get_sheet_id(route.logical_sheet)
            
            if sheet_id is None:
                logger.warning(f"Sheet '{route.logical_sheet}' not found in manifest")
                continue
            
            # Build action -> handler mapping
            action_handlers = {}
            for action, route_config in route.actions.items():
                if route_config.enabled:
                    action_handlers[action] = route_config.handler
                    logger.debug(
                        f"Route: {route.logical_sheet}/{action} -> {route_config.handler}"
                    )
            
            if action_handlers:
                _routing_table[sheet_id] = action_handlers
                _sheet_id_to_logical[sheet_id] = route.logical_sheet
                
        except Exception as e:
            logger.error(f"Error building route for {route.logical_sheet}: {e}")
    
    logger.info(f"Built routing table with {len(_routing_table)} sheets")
    return _routing_table


def get_handler_for_event(event: RowEvent) -> Tuple[Optional[str], Optional[str]]:
    """
    Get handler name for an event based on sheet_id and action.
    
    Returns:
        (handler_name, logical_sheet_name) or (None, None) if no route
    """
    sheet_handlers = _routing_table.get(event.sheet_id)
    
    if not sheet_handlers:
        return None, None
    
    handler = sheet_handlers.get(event.action)
    logical_sheet = _sheet_id_to_logical.get(event.sheet_id)
    
    return handler, logical_sheet


def get_handler_config(handler_name: str) -> Optional[HandlerConfig]:
    """Get configuration for a handler."""
    config = load_routing_config()
    return config.handler_config.get(handler_name)


def is_handler_implemented(handler_name: str) -> bool:
    """Check if a handler is implemented (not marked as not_implemented)."""
    handler_config = get_handler_config(handler_name)
    if handler_config is None:
        return False
    return not handler_config.not_implemented


def get_routing_table() -> Dict[int, Dict[str, str]]:
    """Get the current routing table."""
    return _routing_table


def reset_routing():
    """Reset routing table (for testing)."""
    global _routing_config
    _routing_table.clear()
    _routing_config = None
    _sheet_id_to_logical.clear()

