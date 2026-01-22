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


def compute_file_hash_from_base64(file_content_base64: str) -> Optional[str]:
    """
    Compute hash from base64 encoded file content.
    Returns None if decoding fails.
    """
    import base64
    try:
        file_bytes = base64.b64decode(file_content_base64)
        return compute_file_hash(file_bytes)
    except Exception as e:
        logger.error(f"Failed to decode base64 content for hashing: {e}")
        return None


def compute_combined_file_hash(files: list) -> Optional[str]:
    """
    Compute a deterministic combined hash from multiple files.
    
    Files are sorted by file_type for consistent ordering.
    Individual hashes are combined and hashed again.
    
    Args:
        files: List of FileAttachment objects
        
    Returns:
        Combined SHA256 hash string, or None if no valid hashes
    """
    if not files:
        return None
    
    # Sort by file_type for deterministic ordering
    sorted_files = sorted(files, key=lambda f: f.file_type.value if hasattr(f.file_type, 'value') else str(f.file_type))
    
    individual_hashes = []
    for f in sorted_files:
        file_hash = None
        if hasattr(f, 'file_content') and f.file_content:
            file_hash = compute_file_hash_from_base64(f.file_content)
        elif hasattr(f, 'file_url') and f.file_url:
            file_hash = compute_file_hash_from_url(f.file_url)
        
        if file_hash:
            individual_hashes.append(file_hash)
    
    if not individual_hashes:
        return None
    
    # Combine hashes with separator and hash again
    combined = "|".join(individual_hashes)
    return hashlib.sha256(combined.encode()).hexdigest()


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


def sanitize_folder_name(name: str) -> str:
    """
    Sanitize a string for use in SharePoint folder paths.
    Removes/replaces characters that are invalid in folder names.
    """
    if not name:
        return "Unknown"
    
    # Replace invalid characters with underscore
    invalid_chars = ['/', '\\', ':', '*', '?', '"', '<', '>', '|', '#', '%']
    result = name
    for char in invalid_chars:
        result = result.replace(char, '_')
    
    # Remove leading/trailing spaces and dots
    result = result.strip(' .')
    
    # Replace multiple underscores with single
    while '__' in result:
        result = result.replace('__', '_')
    
    # Truncate to reasonable length (SharePoint limit considerations)
    return result[:50] if result else "Unknown"


def generate_lpo_folder_path(
    sap_reference: str, 
    customer_name: str,
    base_url: Optional[str] = None
) -> str:
    """
    Generate canonical folder path for LPO (relative path for Power Automate).
    
    Format: LPOs/{sap_reference}_{customer_name}
    
    Args:
        sap_reference: SAP reference number (e.g., PTE-185)
        customer_name: Customer name
        base_url: DEPRECATED - no longer used (path is relative)
    
    Returns:
        Relative folder path (e.g., "LPOs/PTE-185_Acme_Corp")
    
    Example:
        >>> generate_lpo_folder_path("PTE-185", "Acme Corp")
        "LPOs/PTE-185_Acme_Corp"
    """
    # Sanitize names for folder path
    safe_customer = sanitize_folder_name(customer_name)
    safe_sap = sanitize_folder_name(sap_reference)
    
    # Build relative path (Power Automate/SharePoint will handle encoding)
    folder_name = f"{safe_sap}_{safe_customer}"
    return f"LPOs/{folder_name}"


def generate_lpo_folder_url(
    sap_reference: str,
    customer_name: str,
    base_url: Optional[str] = None
) -> str:
    """
    Generate properly encoded SharePoint URL for LPO folder.
    
    This URL is safe to store in Smartsheet and will be clickable.
    Uses URL encoding as per SharePoint/Power Automate requirements.
    
    Args:
        sap_reference: SAP reference number (e.g., PTE-185)
        customer_name: Customer name
        base_url: SharePoint document library base URL (uses env var if not provided)
    
    Returns:
        Full encoded URL (e.g., "https://tenant.sharepoint.com/sites/.../LPOs/PTE-185_Acme%20Corp")
    
    Example:
        >>> generate_lpo_folder_url("PTE-185", "Acme Corp")
        "https://algurguae.sharepoint.com/sites/DuctsFabricationPlant/Ducts/LPOs/PTE-185_Acme_Corp"
    """
    import os
    from urllib.parse import quote
    
    # Get base URL from env if not provided
    if not base_url:
        base_url = os.environ.get(
            "SHAREPOINT_BASE_URL",
            "https://algurguae.sharepoint.com/sites/DuctsFabricationPlant/Ducts"
        )
    
    # Get relative path
    relative_path = generate_lpo_folder_path(sap_reference, customer_name)
    
    # URL encode the path (safe='/' keeps slashes unencoded)
    # This matches SharePoint's encodeUriComponent behavior
    encoded_path = quote(relative_path, safe='/')
    
    # Build full URL
    return f"{base_url.rstrip('/')}/{encoded_path}"


def generate_lpo_subfolder_paths(lpo_folder_path: str) -> Dict[str, str]:
    """
    Generate all subfolder paths for an LPO folder.
    
    Returns dict with keys: TagSheets, CutSessions, Deliveries, Invoices, Remnants, Audit
    """
    return {
        "TagSheets": f"{lpo_folder_path}/TagSheets",
        "CutSessions": f"{lpo_folder_path}/CutSessions",
        "Deliveries": f"{lpo_folder_path}/Deliveries",
        "Invoices": f"{lpo_folder_path}/Invoices",
        "Remnants": f"{lpo_folder_path}/Remnants",
        "Audit": f"{lpo_folder_path}/Audit",
    }
