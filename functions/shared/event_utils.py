"""
Event Processing Utilities
===========================

Shared utilities for event dispatcher handlers.
All access is ID-based for resilience to renames.
"""

import logging
from typing import Any, Optional

from .manifest import get_manifest

logger = logging.getLogger(__name__)


def get_cell_value_by_column_id(
    row_data: dict,
    column_id: int
) -> Optional[Any]:
    """
    Get cell value from row data using column ID.
    
    Args:
        row_data: Dict from SmartsheetClient.get_row() (column_id -> value)
        column_id: Immutable Smartsheet column ID
    
    Returns:
        Cell value or None if not found
    """
    return row_data.get(column_id) or row_data.get(str(column_id))


def get_cell_value_by_logical_name(
    row_data: dict,
    sheet_logical: str,
    column_logical: str
) -> Optional[Any]:
    """
    Get cell value using logical column name.
    
    Manifest translates logical name to immutable ID.
    This is the SOTA method - immune to column renames.
    
    Args:
        row_data: Dict from SmartsheetClient.get_row() (column_id -> value)
        sheet_logical: Logical sheet name (e.g., "01H_LPO_INGESTION")
        column_logical: Logical column name (e.g., "SAP_REFERENCE")
    
    Returns:
        Cell value or None if column not found
    """
    manifest = get_manifest()
    column_id = manifest.get_column_id(sheet_logical, column_logical)
    
    if column_id is None:
        logger.warning(
            f"Column '{column_logical}' not found in manifest for sheet '{sheet_logical}'"
        )
        return None
    
    # Row data is keyed by column_id (as int or string)
    return row_data.get(column_id) or row_data.get(str(column_id))
