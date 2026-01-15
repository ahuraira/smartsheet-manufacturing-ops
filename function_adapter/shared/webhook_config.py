"""
Webhook Configuration
=====================

Intuitive configuration for which Smartsheet sheets and columns to monitor.

**Integration with workspace_manifest.json:**
- Sheet IDs and column IDs are loaded from the manifest at startup
- Configuration uses logical names (e.g., "STATUS", "TAG_REGISTRY")
- Manifest provides immutable IDs that don't break on renames

Usage:
    from shared.webhook_config import init_config, get_watched_sheet
    
    # Initialize at startup (loads manifest)
    init_config()
    
    # Get sheet config with IDs populated
    sheet = get_watched_sheet("TAG_REGISTRY")
    print(sheet.sheet_id)  # 123456789 (from manifest)
"""

import os
import json
import logging
from typing import Dict, List, Optional, Any
from enum import Enum
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


class EventAction(str, Enum):
    """Smartsheet event actions."""
    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"


class ObjectType(str, Enum):
    """Smartsheet object types that can trigger events."""
    ROW = "row"
    CELL = "cell"
    ATTACHMENT = "attachment"
    COMMENT = "comment"
    COLUMN = "column"
    SHEET = "sheet"


@dataclass
class WatchedColumn:
    """A column to monitor for changes."""
    logical_name: str  # e.g., "STATUS"
    column_id: Optional[int] = None  # Populated from manifest
    physical_name: Optional[str] = None  # Populated from manifest
    triggers_processing: bool = True
    description: str = ""


@dataclass 
class WatchedSheet:
    """Configuration for a sheet that the webhook monitors."""
    logical_name: str  # e.g., "TAG_REGISTRY"
    sheet_id: Optional[int] = None  # Populated from manifest
    physical_name: Optional[str] = None  # Populated from manifest
    
    # Columns to watch (logical names - IDs populated from manifest)
    watched_columns: List[WatchedColumn] = field(default_factory=list)
    
    # Which event types to process
    process_row_created: bool = True
    process_row_updated: bool = True
    process_row_deleted: bool = False
    process_attachments: bool = True
    process_comments: bool = False
    
    # Handler function name
    handler_name: str = ""
    
    # Description
    description: str = ""


# =============================================================================
# WATCHED SHEETS CONFIGURATION
# =============================================================================
# Define which sheets and columns to monitor.
# Sheet IDs and column IDs are populated from workspace_manifest.json at startup.

WATCHED_SHEETS_CONFIG: Dict[str, WatchedSheet] = {
    
    "TAG_REGISTRY": WatchedSheet(
        logical_name="TAG_REGISTRY",
        handler_name="process_tag_event",
        description="Monitor tag uploads, status changes, and file attachments",
        process_attachments=True,
        watched_columns=[
            WatchedColumn("STATUS", triggers_processing=True, description="Tag status changes"),
            WatchedColumn("ESTIMATED_QUANTITY", triggers_processing=True),
            WatchedColumn("LPO_SAP_REFERENCE", triggers_processing=True),
        ]
    ),
    
    "PRODUCTION_PLANNING": WatchedSheet(
        logical_name="PRODUCTION_PLANNING",
        handler_name="process_schedule_event",
        description="Monitor production schedule status and machine assignments",
        process_attachments=True,
        watched_columns=[
            WatchedColumn("STATUS", triggers_processing=True),
            WatchedColumn("MACHINE_ASSIGNED", triggers_processing=True),
            WatchedColumn("PLANNED_DATE", triggers_processing=True),
            WatchedColumn("SHIFT", triggers_processing=True),
        ]
    ),
    
    "LPO_MASTER": WatchedSheet(
        logical_name="LPO_MASTER",
        handler_name="process_lpo_event",
        description="Monitor LPO status and quantity changes",
        process_attachments=True,
        watched_columns=[
            WatchedColumn("LPO_STATUS", triggers_processing=True),
            WatchedColumn("PO_QUANTITY_SQM", triggers_processing=True),
        ]
    ),
    
    # -------------------------------------------------------------------------
    # INGESTION / STAGING SHEETS (Form Entries)
    # -------------------------------------------------------------------------
    "TAG_SHEET_STAGING": WatchedSheet(
        logical_name="02H_TAG_SHEET_STAGING",  # Matches manifest key
        handler_name="process_tag_staging_event",
        description="Monitor tag form submissions for validation/move",
        process_row_created=True,
        process_attachments=True,
        watched_columns=[
            WatchedColumn("TAG_SHEET_NAME_REV", triggers_processing=True),
            WatchedColumn("STATUS", triggers_processing=True),
        ]
    ),
    
    "LPO_INGESTION": WatchedSheet(
        logical_name="01H_LPO_INGESTION",  # Matches manifest key
        handler_name="process_lpo_ingestion_event",
        description="Monitor LPO form submissions for validation/move",
        process_row_created=True,
        process_attachments=True,
        watched_columns=[
            WatchedColumn("CUSTOMER_LPO_REF", triggers_processing=True),
            WatchedColumn("PO_QUANTITY_SQM", triggers_processing=True),
        ]
    ),

    "PRODUCTION_PLANNING_STAGING": WatchedSheet(
        logical_name="03H_PRODUCTION_PLANNING_STAGING", # Matches manifest key
        handler_name="process_planning_staging_event",
        description="Monitor planning staging for validation",
        process_row_created=True,
        watched_columns=[
            WatchedColumn("STATUS", triggers_processing=True),
            WatchedColumn("PLANNED_DATE", triggers_processing=True),
        ]
    ),

    # -------------------------------------------------------------------------
    # EXCEPTION LOG - Monitor exception resolutions
    # -------------------------------------------------------------------------
    "EXCEPTION_LOG": WatchedSheet(
        logical_name="EXCEPTION_LOG",
        handler_name="process_exception_event",
        description="Monitor exception resolutions",
        process_attachments=False,
        watched_columns=[
            WatchedColumn("STATUS", triggers_processing=True),
            WatchedColumn("RESOLUTION_ACTION", triggers_processing=True),
        ]
    ),
}


# =============================================================================
# MANIFEST INTEGRATION
# =============================================================================

_manifest: Optional[Dict[str, Any]] = None
_initialized: bool = False

# Path to manifest
# Prioritize local file (for deployment), fallback to relative path (for local dev)
MANIFEST_PATH = os.getenv(
    "WORKSPACE_MANIFEST_PATH",
    str(Path(__file__).parent.parent / "workspace_manifest.json") 
)

# Fallback to sibling project if not found locally
if not os.path.exists(MANIFEST_PATH):
    MANIFEST_PATH = str(Path(__file__).parent.parent.parent / "functions" / "workspace_manifest.json")


def load_manifest(path: str = None) -> Dict[str, Any]:
    """Load the workspace manifest from file."""
    global _manifest
    
    manifest_path = path or MANIFEST_PATH
    
    if not os.path.exists(manifest_path):
        logger.warning(f"Manifest not found at {manifest_path}")
        return {}
    
    with open(manifest_path, "r", encoding="utf-8") as f:
        _manifest = json.load(f)
    
    logger.info(f"Loaded manifest from {manifest_path}")
    return _manifest


def init_config(manifest_path: str = None) -> None:
    """
    Initialize configuration by loading manifest and populating IDs.
    
    Call this at function startup to map logical names to Smartsheet IDs.
    """
    global _initialized
    
    if _initialized:
        return
    
    manifest = load_manifest(manifest_path)
    if not manifest:
        logger.error("Failed to load manifest - configuration incomplete")
        return
    
    sheets = manifest.get("sheets", {})
    
    for logical_name, config in WATCHED_SHEETS_CONFIG.items():
        sheet_data = sheets.get(logical_name)
        
        if not sheet_data:
            logger.warning(f"Sheet '{logical_name}' not found in manifest")
            continue
        
        # Populate sheet ID and name
        config.sheet_id = sheet_data.get("id")
        config.physical_name = sheet_data.get("name")
        
        logger.info(f"Loaded sheet {logical_name}: id={config.sheet_id}, name='{config.physical_name}'")
        
        # Populate column IDs
        columns = sheet_data.get("columns", {})
        for watched_col in config.watched_columns:
            col_data = columns.get(watched_col.logical_name)
            if col_data:
                watched_col.column_id = col_data.get("id")
                watched_col.physical_name = col_data.get("name")
                logger.debug(f"  Column {watched_col.logical_name}: id={watched_col.column_id}")
            else:
                logger.warning(f"  Column '{watched_col.logical_name}' not found in {logical_name}")
    
    _initialized = True
    logger.info(f"Configuration initialized with {len(WATCHED_SHEETS_CONFIG)} watched sheets")


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_watched_sheet(logical_name: str) -> Optional[WatchedSheet]:
    """Get watched sheet configuration by logical name."""
    if not _initialized:
        init_config()
    return WATCHED_SHEETS_CONFIG.get(logical_name)


def get_watched_sheet_by_id(sheet_id: int) -> Optional[WatchedSheet]:
    """Get watched sheet configuration by Smartsheet sheet ID."""
    if not _initialized:
        init_config()
    
    for sheet in WATCHED_SHEETS_CONFIG.values():
        if sheet.sheet_id == sheet_id:
            return sheet
    return None


def get_column_by_id(sheet_logical: str, column_id: int) -> Optional[WatchedColumn]:
    """Get a watched column by its Smartsheet column ID."""
    sheet = get_watched_sheet(sheet_logical)
    if not sheet:
        return None
    
    for col in sheet.watched_columns:
        if col.column_id == column_id:
            return col
    return None


def is_watched_column_id(sheet_id: int, column_id: int) -> bool:
    """Check if a column ID is in the watched list for a sheet."""
    sheet = get_watched_sheet_by_id(sheet_id)
    if not sheet:
        return False
    
    return any(col.column_id == column_id for col in sheet.watched_columns)


def get_all_sheet_ids() -> List[int]:
    """Get all sheet IDs that should have webhooks registered."""
    if not _initialized:
        init_config()
    return [s.sheet_id for s in WATCHED_SHEETS_CONFIG.values() if s.sheet_id]


def get_manifest() -> Optional[Dict[str, Any]]:
    """Get the loaded manifest."""
    return _manifest


# =============================================================================
# SYSTEM ACTORS (to ignore self-triggered events)
# =============================================================================

SYSTEM_ACTORS = os.getenv("SYSTEM_ACTOR_EMAILS", "automation@ducts.ae,system@ducts.ae").split(",")


def is_system_actor(actor_id) -> bool:
    """
    Check if the actor is a system account (to avoid processing loops).
    
    Note: Smartsheet webhooks send userId as a numeric ID, not email.
    We compare against known system user IDs or emails.
    """
    if actor_id is None:
        return False
    
    # Convert to string for comparison
    actor_str = str(actor_id).lower().strip()
    
    # Check against system actors (can be emails or user IDs)
    return actor_str in [a.lower().strip() for a in SYSTEM_ACTORS]

