"""
Handlers Package
================

Event handlers that transform staging data and call core functions.
All handlers use ID-based extraction for resilience.
"""

from .lpo_handler import handle_lpo_ingest, handle_lpo_update
from .tag_handler import handle_tag_ingest
from .schedule_handler import handle_schedule_ingest

__all__ = [
    "handle_lpo_ingest",
    "handle_lpo_update",
    "handle_tag_ingest",
    "handle_schedule_ingest",
]
