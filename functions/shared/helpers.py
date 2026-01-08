"""
Helper utilities for Azure Functions.
Includes hashing, SLA calculation, and utility functions.

Note: ID generation functions have moved to id_generator.py
which uses sequence-based IDs stored in the Config sheet.
"""

import hashlib
import uuid
import logging
from datetime import datetime, timedelta
from typing import Optional, Any, Dict
import requests

from .models import ExceptionSeverity

logger = logging.getLogger(__name__)


def generate_trace_id() -> str:
    """Generate a unique trace ID for correlation across systems."""
    return f"trace-{uuid.uuid4().hex[:12]}"


def compute_file_hash(file_content: bytes) -> str:
    """Compute SHA256 hash of file content."""
    return hashlib.sha256(file_content).hexdigest()


def compute_file_hash_from_url(file_url: str, auth_headers: Optional[Dict[str, str]] = None) -> Optional[str]:
    """
    Download file from URL and compute its hash.
    Returns None if download fails.
    """
    try:
        headers = auth_headers or {}
        response = requests.get(file_url, headers=headers, timeout=30)
        response.raise_for_status()
        return compute_file_hash(response.content)
    except Exception as e:
        logger.error(f"Failed to download file for hashing: {e}")
        return None


def calculate_sla_due(severity: ExceptionSeverity, created_at: Optional[datetime] = None) -> datetime:
    """
    Calculate SLA due date based on severity.
    - CRITICAL: +4 hours
    - HIGH: +24 hours
    - MEDIUM: +48 hours
    - LOW: +72 hours
    """
    base_time = created_at or datetime.now()
    
    sla_hours = {
        ExceptionSeverity.CRITICAL: 4,
        ExceptionSeverity.HIGH: 24,
        ExceptionSeverity.MEDIUM: 48,
        ExceptionSeverity.LOW: 72,
    }
    
    hours = sla_hours.get(severity, 48)
    return base_time + timedelta(hours=hours)


def format_datetime_for_smartsheet(dt: datetime) -> str:
    """Format datetime for Smartsheet API."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def parse_float_safe(value: Any, default: float = 0.0) -> float:
    """Safely parse a value to float."""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def parse_int_safe(value: Any, default: int = 0) -> int:
    """Safely parse a value to int."""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def safe_get(d: Dict, *keys, default=None) -> Any:
    """Safely get nested dictionary values."""
    for key in keys:
        if isinstance(d, dict):
            d = d.get(key)
        else:
            return default
    return d if d is not None else default
