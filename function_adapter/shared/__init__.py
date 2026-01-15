"""
Shared module for Webhook Adapter Functions.

This module provides:
- Configuration for watched sheets/columns (from workspace_manifest.json)
- Helper functions for event routing
- System actor detection
"""

from .webhook_config import (
    # Enums
    EventAction,
    ObjectType,
    
    # Dataclasses
    WatchedColumn,
    WatchedSheet,
    
    # Configuration
    WATCHED_SHEETS_CONFIG,
    SYSTEM_ACTORS,
    MANIFEST_PATH,
    
    # Initialization
    init_config,
    load_manifest,
    get_manifest,
    
    # Helper functions
    get_watched_sheet,
    get_watched_sheet_by_id,
    get_column_by_id,
    is_watched_column_id,
    get_all_sheet_ids,
    is_system_actor,
)

__all__ = [
    "EventAction",
    "ObjectType",
    "WatchedColumn",
    "WatchedSheet",
    "WATCHED_SHEETS_CONFIG",
    "SYSTEM_ACTORS",
    "MANIFEST_PATH",
    "init_config",
    "load_manifest",
    "get_manifest",
    "get_watched_sheet",
    "get_watched_sheet_by_id",
    "get_column_by_id",
    "is_watched_column_id",
    "get_all_sheet_ids",
    "is_system_actor",
]
