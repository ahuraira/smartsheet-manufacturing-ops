"""
Smartsheet API Client v2 - ID-First Architecture
=================================================

SOTA Features:
- **ID-first lookup**: Uses manifest IDs (immutable) as primary reference
- **Name fallback**: Falls back to name lookup only when ID not in manifest
- **Thread-safe singleton**: Safe for Azure Functions concurrent execution
- **Retry with exponential backoff**: Handles rate limits, transient errors
- **Rate limiting**: Respects Smartsheet API limits (300 req/min)
- **Column ID caching**: Avoids repeated column lookups

Architecture
------------
1. Load manifest at startup (workspace_manifest.json)
2. Use sheet IDs from manifest for all operations
3. Cache column IDs per sheet for efficient cell operations
4. Fall back to name lookup only for sheets not in manifest

This ensures:
- Sheet/column renames don't break the application
- Fast lookups (IDs cached, no repeated API calls)
- Clear error messages when something is missing
"""

import os
import logging
import time
import threading
import functools
from typing import Optional, List, Dict, Any, Callable, TypeVar, Union
from datetime import datetime
import requests
from requests.exceptions import RequestException

from .manifest import WorkspaceManifest, get_manifest, ManifestNotFoundError
from .logical_names import Sheet, Column

logger = logging.getLogger(__name__)

# Type variable for generic retry decorator
T = TypeVar('T')


# ============== Custom Exceptions ==============

class SmartsheetError(Exception):
    """Base exception for Smartsheet operations."""
    pass


class SmartsheetRateLimitError(SmartsheetError):
    """Raised when API rate limit is exceeded."""
    def __init__(self, reset_time: Optional[int] = None):
        self.reset_time = reset_time
        super().__init__(f"Rate limit exceeded. Reset at: {reset_time}")


class SmartsheetSaveCollisionError(SmartsheetError):
    """Raised when a save collision occurs (concurrent update)."""
    pass


class SmartsheetNotFoundError(SmartsheetError):
    """Raised when a resource is not found."""
    pass


# ============== Retry Decorator ==============

def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 30.0,
    exponential_base: float = 2.0,
    retryable_status_codes: tuple = (429, 500, 502, 503, 504),
    retryable_exceptions: tuple = (RequestException, SmartsheetRateLimitError),
) -> Callable:
    """
    Decorator that retries a function with exponential backoff.
    
    Handles:
    - HTTP 429 rate limit (respects X-RateLimit-Reset header)
    - HTTP 5xx server errors
    - Network errors
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_exception = None
            
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                    
                except requests.HTTPError as e:
                    response = e.response
                    status_code = response.status_code if response is not None else 0
                    
                    if status_code not in retryable_status_codes:
                        # Non-retryable error - check for specific codes
                        if "4004" in str(e):
                            raise SmartsheetSaveCollisionError(str(e))
                        raise
                    
                    last_exception = e
                    
                    if status_code == 429:
                        # Rate limit - check for reset header
                        reset_time = response.headers.get('X-RateLimit-Reset')
                        if reset_time:
                            wait_time = max(0, int(reset_time) - int(time.time()))
                            wait_time = min(wait_time, max_delay)
                        else:
                            wait_time = min(base_delay * (exponential_base ** attempt), max_delay)
                        logger.warning(f"Rate limit hit. Waiting {wait_time}s before retry {attempt + 1}/{max_retries}")
                    else:
                        wait_time = min(base_delay * (exponential_base ** attempt), max_delay)
                        logger.warning(f"HTTP {status_code}. Retry {attempt + 1}/{max_retries} in {wait_time}s")
                    
                    if attempt < max_retries:
                        time.sleep(wait_time)
                        
                except retryable_exceptions as e:
                    last_exception = e
                    wait_time = min(base_delay * (exponential_base ** attempt), max_delay)
                    logger.warning(f"Transient error: {e}. Retry {attempt + 1}/{max_retries} in {wait_time}s")
                    
                    if attempt < max_retries:
                        time.sleep(wait_time)
            
            # All retries exhausted
            raise last_exception or SmartsheetError("Max retries exceeded")
        
        return wrapper
    return decorator


# ============== Rate Limiter ==============

class RateLimiter:
    """
    Simple rate limiter to respect Smartsheet API limits.
    Limits to ~290 requests/minute (with buffer) = ~4.8 req/sec.
    """
    
    def __init__(self, requests_per_minute: int = 290):
        self.min_interval = 60.0 / requests_per_minute
        self._last_request_time = 0.0
        self._lock = threading.Lock()
    
    def wait(self):
        """Wait if necessary to respect rate limit."""
        with self._lock:
            now = time.time()
            elapsed = now - self._last_request_time
            
            if elapsed < self.min_interval:
                sleep_time = self.min_interval - elapsed
                time.sleep(sleep_time)
            
            self._last_request_time = time.time()


# ============== Smartsheet Client v2 ==============

class SmartsheetClient:
    """
    SOTA Smartsheet API client with ID-first architecture.
    
    Features:
    - Uses manifest IDs as primary reference (immutable)
    - Falls back to name lookup when ID not available
    - Thread-safe with proper locking
    - Retry logic with exponential backoff
    - Rate limiting to respect API limits
    
    Usage:
        >>> client = get_smartsheet_client()
        >>> 
        >>> # Using logical names (recommended)
        >>> row = client.find_row(Sheet.TAG_REGISTRY, Column.TAG_REGISTRY.FILE_HASH, hash_value)
        >>> 
        >>> # Using sheet ID directly
        >>> row = client.find_row_by_id(sheet_id, column_name, value)
    """
    
    def __init__(self, manifest: Optional[WorkspaceManifest] = None):
        """
        Initialize client.
        
        Args:
            manifest: Optional workspace manifest. If None, loads from default location.
        """
        self.api_key = os.environ.get("SMARTSHEET_API_KEY")
        self.base_url = os.environ.get("SMARTSHEET_BASE_URL", "https://api.smartsheet.eu/2.0")
        self.workspace_id = os.environ.get("SMARTSHEET_WORKSPACE_ID")
        
        if not self.api_key:
            raise ValueError("SMARTSHEET_API_KEY environment variable is required")
        
        if not self.workspace_id:
            raise ValueError("SMARTSHEET_WORKSPACE_ID environment variable is required")
        
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        # Load manifest (ID-first approach)
        self._manifest = manifest or get_manifest()
        
        # Cache for name-based lookups (fallback)
        self._sheet_name_to_id: Dict[str, int] = {}
        self._sheet_name_to_id_lock = threading.Lock()
        
        # Cache for column name-to-id mapping per sheet
        self._column_cache: Dict[int, Dict[str, int]] = {}
        self._column_cache_lock = threading.Lock()
        
        # Rate limiter
        self._rate_limiter = RateLimiter()
        
        # User email cache (user_id -> email)
        self._user_email_cache: Dict[int, Optional[str]] = {}
        
        logger.info(f"SmartsheetClient initialized. Manifest loaded: {self._manifest.is_loaded()}")
    
    # ============== User Methods ==============
    
    @retry_with_backoff(max_retries=3)
    def _fetch_user(self, user_id: int) -> Optional[str]:
        """Internal method to fetch user email with retry."""
        response = self._make_request("GET", f"{self.base_url}/users/{user_id}")
        return response.json().get("email")
    
    def get_user_email(self, user_id: int) -> Optional[str]:
        """
        Get user email by Smartsheet user ID.
        
        Args:
            user_id: Numeric Smartsheet user ID
            
        Returns:
            Email address or None if not found
        """
        # Check cache first
        if user_id in self._user_email_cache:
            return self._user_email_cache[user_id]
        
        try:
            email = self._fetch_user(user_id)
            self._user_email_cache[user_id] = email
            return email
        except Exception as e:
            logger.warning(f"Failed to get email for user {user_id}: {e}")
            self._user_email_cache[user_id] = None
            return None
    
    # ============== Low-level API Methods ==============
    
    def _make_request(
        self,
        method: str,
        url: str,
        json: Optional[Dict] = None,
        params: Optional[Dict] = None
    ) -> requests.Response:
        """Make an API request with rate limiting."""
        self._rate_limiter.wait()
        
        response = requests.request(
            method=method,
            url=url,
            headers=self.headers,
            json=json,
            params=params,
            timeout=30
        )
        
        # Log detailed error info before raising
        if not response.ok:
            try:
                error_body = response.json()
                logger.error(f"Smartsheet API error: {response.status_code} - {error_body}")
            except Exception:
                logger.error(f"Smartsheet API error: {response.status_code} - {response.text[:500]}")
        
        response.raise_for_status()
        return response
    
    # ============== Sheet ID Resolution ==============
    
    def resolve_sheet_id(self, sheet_ref: Union[str, int]) -> int:
        """
        Resolve a sheet reference to its numeric ID.
        
        Args:
            sheet_ref: Can be:
                - Logical name (str): "TAG_REGISTRY" - looks up in manifest
                - Physical name (str): "02 Tag Sheet Registry" - looks up via API
                - Numeric ID (int): Returns as-is
        
        Returns:
            Numeric sheet ID
        
        Raises:
            SmartsheetNotFoundError: If sheet cannot be found
        """
        # Already an ID
        if isinstance(sheet_ref, int):
            return sheet_ref
        
        # Try manifest first (ID-first approach)
        manifest_id = self._manifest.get_sheet_id(sheet_ref)
        if manifest_id:
            return manifest_id
        
        # Fallback to name lookup
        return self._resolve_sheet_by_name(sheet_ref)
    
    @retry_with_backoff(max_retries=3)
    def _resolve_sheet_by_name(self, sheet_name: str) -> int:
        """Resolve sheet ID by name (fallback method)."""
        with self._sheet_name_to_id_lock:
            # Check cache first
            if sheet_name in self._sheet_name_to_id:
                return self._sheet_name_to_id[sheet_name]
            
            # Load from workspace
            if not self._sheet_name_to_id:
                self._load_all_sheet_names()
            
            if sheet_name not in self._sheet_name_to_id:
                raise SmartsheetNotFoundError(
                    f"Sheet '{sheet_name}' not found in workspace. "
                    f"Available sheets: {list(self._sheet_name_to_id.keys())[:5]}..."
                )
            
            return self._sheet_name_to_id[sheet_name]
    
    def _load_all_sheet_names(self):
        """Load all sheet names from workspace (for fallback)."""
        url = f"{self.base_url}/workspaces/{self.workspace_id}"
        params = {"include": "sheets,folders"}
        
        response = self._make_request("GET", url, params=params)
        workspace = response.json()
        
        # Root level sheets
        for sheet in workspace.get("sheets", []):
            self._sheet_name_to_id[sheet["name"]] = sheet["id"]
        
        # Sheets in folders
        for folder in workspace.get("folders", []):
            self._load_folder_sheet_names(folder["id"])
        
        logger.info(f"Loaded {len(self._sheet_name_to_id)} sheet names for fallback lookup")
    
    def _load_folder_sheet_names(self, folder_id: int):
        """Load sheet names from a folder."""
        url = f"{self.base_url}/folders/{folder_id}"
        params = {"include": "sheets,folders"}
        
        response = self._make_request("GET", url, params=params)
        folder = response.json()
        
        for sheet in folder.get("sheets", []):
            self._sheet_name_to_id[sheet["name"]] = sheet["id"]
        
        for subfolder in folder.get("folders", []):
            self._load_folder_sheet_names(subfolder["id"])
    
    # ============== Column ID Resolution ==============
    
    def resolve_column_id(self, sheet_ref: Union[str, int], column_ref: str) -> int:
        """
        Resolve a column reference to its numeric ID.
        
        Args:
            sheet_ref: Sheet reference (logical name, physical name, or ID)
            column_ref: Column reference (logical name or physical name)
        
        Returns:
            Numeric column ID
        """
        sheet_id = self.resolve_sheet_id(sheet_ref)
        
        # Try manifest first (only if sheet_ref is a logical name)
        if isinstance(sheet_ref, str):
            manifest_col_id = self._manifest.get_column_id(sheet_ref, column_ref)
            if manifest_col_id:
                return manifest_col_id
        
        # Fallback to name lookup
        return self._resolve_column_by_name(sheet_id, column_ref)
    
    def _resolve_column_by_name(self, sheet_id: int, column_name: str) -> int:
        """Resolve column ID by name."""
        with self._column_cache_lock:
            # Check cache
            if sheet_id in self._column_cache:
                columns = self._column_cache[sheet_id]
                if column_name in columns:
                    return columns[column_name]
        
        # Load columns for sheet
        self._load_sheet_columns(sheet_id)
        
        with self._column_cache_lock:
            if sheet_id in self._column_cache and column_name in self._column_cache[sheet_id]:
                return self._column_cache[sheet_id][column_name]
        
        raise SmartsheetNotFoundError(f"Column '{column_name}' not found in sheet {sheet_id}")
    
    def _load_sheet_columns(self, sheet_id: int):
        """Load and cache column names for a sheet."""
        url = f"{self.base_url}/sheets/{sheet_id}"
        params = {"include": "columns"}
        
        response = self._make_request("GET", url, params=params)
        sheet_data = response.json()
        
        with self._column_cache_lock:
            self._column_cache[sheet_id] = {
                col["title"]: col["id"] for col in sheet_data.get("columns", [])
            }
    
    def get_column_name_map(self, sheet_ref: Union[str, int]) -> Dict[int, str]:
        """Get mapping of column ID to column name for a sheet."""
        sheet_id = self.resolve_sheet_id(sheet_ref)
        
        url = f"{self.base_url}/sheets/{sheet_id}"
        params = {"include": "columns"}
        
        response = self._make_request("GET", url, params=params)
        sheet_data = response.json()
        
        return {col["id"]: col["title"] for col in sheet_data.get("columns", [])}
    
    # ============== Core CRUD Operations ==============
    
    @retry_with_backoff(max_retries=3)
    def get_sheet(self, sheet_ref: Union[str, int], include_columns: bool = True) -> Dict[str, Any]:
        """Get full sheet data."""
        sheet_id = self.resolve_sheet_id(sheet_ref)
        url = f"{self.base_url}/sheets/{sheet_id}"
        params = {"include": "columns"} if include_columns else {}
        
        response = self._make_request("GET", url, params=params)
        return response.json()
    
    @retry_with_backoff(max_retries=3)
    def get_row(
        self, 
        sheet_ref: Union[str, int], 
        row_id: int
    ) -> Optional[Dict[int, Any]]:
        """
        Get a single row by ID, returning cell values keyed by column_id.
        
        This is the SOTA method for ID-based row access - immune to column renames.
        
        Args:
            sheet_ref: Sheet reference (logical name, physical name, or ID)
            row_id: Immutable Smartsheet row ID
        
        Returns:
            Dict mapping column_id (int) -> cell_value, or None if row not found
        """
        sheet_id = self.resolve_sheet_id(sheet_ref)
        url = f"{self.base_url}/sheets/{sheet_id}/rows/{row_id}"
        
        try:
            response = self._make_request("GET", url)
            row_data = response.json()
            
            # Build column_id -> value mapping (ID-based, rename-proof)
            result = {}
            for cell in row_data.get("cells", []):
                col_id = cell.get("columnId")
                value = cell.get("value") or cell.get("displayValue")
                if col_id is not None:
                    result[col_id] = value
            
            return result
            
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                logger.warning(f"Row {row_id} not found in sheet {sheet_id}")
                return None
            raise

    @retry_with_backoff(max_retries=3)
    def get_row_attachments(
        self, 
        sheet_ref: Union[str, int], 
        row_id: int
    ) -> List[Dict[str, Any]]:
        """
        Get all attachments for a specific row WITH URLs.
        
        NOTE: Smartsheet's list attachments endpoint only returns metadata.
        We must fetch each attachment individually to get the actual URL.
        
        Args:
            sheet_ref: Sheet reference (logical name, physical name, or ID)
            row_id: Immutable Smartsheet row ID
        
        Returns:
            List of attachment dictionaries with 'url', 'name', 'id', etc.
        """
        sheet_id = self.resolve_sheet_id(sheet_ref)
        list_url = f"{self.base_url}/sheets/{sheet_id}/rows/{row_id}/attachments"
        
        try:
            response = self._make_request("GET", list_url)
            data = response.json()
            attachment_list = data.get("data", [])
            
            # Smartsheet quirk: list endpoint doesn't include URLs
            # Must fetch each attachment individually for the actual URL
            enriched_attachments = []
            for att in attachment_list:
                att_id = att.get("id")
                if att_id:
                    try:
                        detail = self._get_attachment_detail(sheet_id, att_id)
                        if detail:
                            enriched_attachments.append(detail)
                    except Exception as e:
                        logger.warning(f"Failed to get attachment {att_id} detail: {e}")
                        # Fallback to basic info
                        enriched_attachments.append(att)
            
            return enriched_attachments
            
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                logger.warning(f"Row {row_id} not found in sheet {sheet_id}")
                return []
            raise
    
    @retry_with_backoff(max_retries=2)
    def _get_attachment_detail(self, sheet_id: int, attachment_id: int) -> Optional[Dict[str, Any]]:
        """Fetch individual attachment details including URL."""
        url = f"{self.base_url}/sheets/{sheet_id}/attachments/{attachment_id}"
        response = self._make_request("GET", url)
        return response.json()

    @retry_with_backoff(max_retries=3)
    def find_rows(
        self, 
        sheet_ref: Union[str, int], 
        column_ref: str, 
        value: Any
    ) -> List[Dict[str, Any]]:
        """
        Find rows where column matches value.
        
        Args:
            sheet_ref: Sheet reference (logical name, physical name, or ID)
            column_ref: Column reference (logical name or physical name)
            value: Value to search for
        
        Returns:
            List of matching rows as dictionaries
        """
        sheet_data = self.get_sheet(sheet_ref)
        sheet_id = sheet_data["id"]
        columns = sheet_data.get("columns", [])
        
        # Build column name map
        col_id_to_name = {col["id"]: col["title"] for col in columns}
        col_name_to_id = {col["title"]: col["id"] for col in columns}
        
        # Resolve column - try as name first, then as logical name in manifest
        target_column_id = col_name_to_id.get(column_ref)
        if not target_column_id:
            # Try manifest lookup
            sheet_logical = None
            for ln in [sheet_ref] if isinstance(sheet_ref, str) else []:
                if self._manifest.get_sheet_id(ln):
                    sheet_logical = ln
                    break
            
            if sheet_logical:
                physical_name = self._manifest.get_column_name(sheet_logical, column_ref)
                if physical_name:
                    target_column_id = col_name_to_id.get(physical_name)
        
        if not target_column_id:
            raise SmartsheetNotFoundError(f"Column '{column_ref}' not found in sheet")
        
        # Search rows
        matching_rows = []
        for row in sheet_data.get("rows", []):
            for cell in row.get("cells", []):
                if cell.get("columnId") == target_column_id:
                    cell_value = cell.get("value") or cell.get("displayValue")
                    if cell_value == value:
                        matching_rows.append(self._row_to_dict(row, col_id_to_name))
                    break
        
        return matching_rows
    
    def find_row(
        self, 
        sheet_ref: Union[str, int], 
        column_ref: str, 
        value: Any
    ) -> Optional[Dict[str, Any]]:
        """Find first row matching column value. Returns None if not found."""
        rows = self.find_rows(sheet_ref, column_ref, value)
        return rows[0] if rows else None

    def find_row_by_column(
        self, 
        sheet_ref: Union[str, int], 
        column_ref: str, 
        value: Any
    ) -> Optional[Dict[str, Any]]:
        """
        [DEPRECATED] Alias for find_row. Maintained for backward compatibility.
        Use find_row(Sheet.NAME, Column.NAME.COL, value) instead.
        """
        return self.find_row(sheet_ref, column_ref, value)
    
    @retry_with_backoff(max_retries=3)
    def add_row(
        self, 
        sheet_ref: Union[str, int], 
        row_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Add a new row to a sheet.
        
        Args:
            sheet_ref: Sheet reference
            row_data: Dict mapping column names (physical or logical) to values
        
        Returns:
            Created row data
        """
        sheet_id = self.resolve_sheet_id(sheet_ref)
        sheet_data = self.get_sheet(sheet_id)
        columns = sheet_data.get("columns", [])
        
        col_name_to_id = {col["title"]: col["id"] for col in columns}
        
        # Resolve logical column names if using manifest
        resolved_row_data = {}
        sheet_logical = sheet_ref if isinstance(sheet_ref, str) and self._manifest.get_sheet_id(sheet_ref) else None
        
        for key, val in row_data.items():
            if val is None:
                continue
            
            # Try as physical name first
            if key in col_name_to_id:
                resolved_row_data[key] = val
            elif sheet_logical:
                # Try as logical name
                physical_name = self._manifest.get_column_name(sheet_logical, key)
                if physical_name and physical_name in col_name_to_id:
                    resolved_row_data[physical_name] = val
                else:
                    # Keep as-is, might be physical name
                    resolved_row_data[key] = val
            else:
                resolved_row_data[key] = val
        
        # Build cells array
        cells = []
        for col in columns:
            col_name = col["title"]
            if col_name in resolved_row_data:
                cells.append({
                    "columnId": col["id"],
                    "value": resolved_row_data[col_name]
                })
        
        url = f"{self.base_url}/sheets/{sheet_id}/rows"
        payload = {"toBottom": True, "cells": cells}
        
        response = self._make_request("POST", url, json=payload)
        result = response.json()
        created_row = result.get("result", {})
        
        logger.info(f"Added row to sheet {sheet_id}: row_id={created_row.get('id')}")
        return created_row
    
    @retry_with_backoff(max_retries=3)
    def update_row(
        self, 
        sheet_ref: Union[str, int], 
        row_id: int, 
        updates: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Update an existing row.
        
        Args:
            sheet_ref: Sheet reference
            row_id: Row ID to update
            updates: Dict mapping column names to new values
        
        Returns:
            Updated row data
        """
        sheet_id = self.resolve_sheet_id(sheet_ref)
        sheet_data = self.get_sheet(sheet_id)
        columns = sheet_data.get("columns", [])
        
        col_name_to_id = {col["title"]: col["id"] for col in columns}
        
        # Resolve logical column names if using manifest
        resolved_updates = {}
        sheet_logical = sheet_ref if isinstance(sheet_ref, str) and self._manifest.get_sheet_id(sheet_ref) else None
        
        for key, val in updates.items():
            if key in col_name_to_id:
                resolved_updates[key] = val
            elif sheet_logical:
                physical_name = self._manifest.get_column_name(sheet_logical, key)
                if physical_name:
                    resolved_updates[physical_name] = val
        
        # Build cells array
        cells = []
        for col in columns:
            col_name = col["title"]
            if col_name in resolved_updates:
                cells.append({
                    "columnId": col["id"],
                    "value": resolved_updates[col_name]
                })
        
        url = f"{self.base_url}/sheets/{sheet_id}/rows"
        payload = [{"id": row_id, "cells": cells}]
        
        response = self._make_request("PUT", url, json=payload)
        result = response.json()
        
        logger.info(f"Updated row {row_id} in sheet {sheet_id}")
        return result.get("result", [{}])[0]
    
    def _row_to_dict(self, row: Dict[str, Any], col_id_to_name: Dict[int, str]) -> Dict[str, Any]:
        """Convert a Smartsheet row to a dictionary."""
        result = {"row_id": row["id"]}
        
        # Include system columns/metadata
        for key in ["createdAt", "modifiedAt", "rowNumber", "version", "accessLevel"]:
            if key in row:
                result[key] = row[key]
        
        for cell in row.get("cells", []):
            col_name = col_id_to_name.get(cell.get("columnId"))
            if col_name:
                result[col_name] = cell.get("value") or cell.get("displayValue")
        
        return result
    
    @retry_with_backoff(max_retries=3)
    def get_all_rows(self, sheet_ref: Union[str, int]) -> List[Dict[str, Any]]:
        """
        Get all rows from a sheet.
        
        Args:
            sheet_ref: Sheet reference (logical name, physical name, or ID)
            
        Returns:
            List of row dicts with 'id' and 'cells' keys
        """
        sheet_data = self.get_sheet(sheet_ref)
        return sheet_data.get("rows", [])
    
    @retry_with_backoff(max_retries=3)
    def add_rows_bulk(
        self, 
        sheet_ref: Union[str, int], 
        rows: List[Dict[str, Any]],
        batch_size: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Add multiple rows to a sheet in batches.
        
        Args:
            sheet_ref: Sheet reference (logical name, physical name, or ID)
            rows: List of row dicts with 'toBottom' and 'cells' keys
                  Each cell should have 'columnId' and 'value'
            batch_size: Number of rows per API call (max 500)
            
        Returns:
            List of created row data
        """
        sheet_id = self.resolve_sheet_id(sheet_ref)
        url = f"{self.base_url}/sheets/{sheet_id}/rows"
        
        created_rows = []
        
        # Process in batches
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            
            try:
                response = self._make_request("POST", url, json=batch)
                result = response.json()
                created = result.get("result", [])
                created_rows.extend(created if isinstance(created, list) else [created])
                
                logger.debug(f"Added batch of {len(batch)} rows to sheet {sheet_id}")
            except Exception as e:
                logger.error(f"Failed to add batch starting at row {i}: {e}")
                raise
        
        logger.info(f"Added {len(created_rows)} rows to sheet {sheet_id}")
        return created_rows
    
    # ============== Convenience Methods ==============
    # These use logical names from the Sheet and Column classes
    
    def get_config_value(self, config_key: str) -> Optional[str]:
        """Get a configuration value by key."""
        row = self.find_row(Sheet.CONFIG, Column.CONFIG.CONFIG_KEY, config_key)
        if row:
            # Try to get by logical name first, then physical
            val = row.get("CONFIG_VALUE") or row.get("config_value")
            return val
        return None
    
    @retry_with_backoff(max_retries=3)
    def attach_url_to_row(
        self,
        sheet_ref: Union[str, int],
        row_id: int,
        url: str,
        name: Optional[str] = None,
        description: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Attach a URL to a row in a sheet.
        
        NOTE: Smartsheet has a 500-char limit for attachment URLs.
        If URL is longer (e.g., temporary download URLs), we download
        the file and re-upload as binary content.
        
        Args:
            sheet_ref: Sheet reference (logical name, physical name, or ID)
            row_id: Row ID to attach to
            url: URL to attach
            name: Optional name for the attachment
            description: Optional description
        
        Returns:
            Attachment info from Smartsheet API
        """
        sheet_id = self.resolve_sheet_id(sheet_ref)
        file_name = name or url.split("/")[-1].split("?")[0][:100]  # Strip query params
        
        # Smartsheet API limit: attachment URLs must be <= 500 chars
        if len(url) > 500:
            logger.info(f"URL too long ({len(url)} chars), downloading and re-uploading as file")
            return self._download_and_attach_file(sheet_id, row_id, url, file_name)
        
        api_url = f"{self.base_url}/sheets/{sheet_id}/rows/{row_id}/attachments"
        payload = {
            "attachmentType": "LINK",
            "url": url,
            "name": file_name
        }
        if description:
            payload["description"] = description
        
        response = self._make_request("POST", api_url, json=payload)
        result = response.json()
        
        logger.info(f"Attached URL to row {row_id} in sheet {sheet_id}")
        return result.get("result", {})
    
    @retry_with_backoff(max_retries=3)
    def attach_file_to_row(
        self,
        sheet_ref: Union[str, int],
        row_id: int,
        file_content_base64: str,
        file_name: str,
        content_type: str = "application/octet-stream"
    ) -> Dict[str, Any]:
        """
        Attach a file (from base64 content) to a row in a sheet.
        
        Args:
            sheet_ref: Sheet reference (logical name, physical name, or ID)
            row_id: Row ID to attach to
            file_content_base64: Base64 encoded file content
            file_name: Name for the attachment file
            content_type: MIME type of the file
        
        Returns:
            Attachment info from Smartsheet API
        """
        import base64
        
        sheet_id = self.resolve_sheet_id(sheet_ref)
        
        # Decode base64 content
        try:
            file_bytes = base64.b64decode(file_content_base64)
        except Exception as e:
            raise ValueError(f"Invalid base64 content: {e}")
        
        api_url = f"{self.base_url}/sheets/{sheet_id}/rows/{row_id}/attachments"
        
        # For file uploads, we need to use multipart form data
        # Temporarily remove JSON content-type for multipart
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Disposition": f'attachment; filename="{file_name}"',
            "Content-Type": content_type
        }
        
        self._rate_limiter.wait()
        
        response = requests.post(
            api_url,
            headers=headers,
            data=file_bytes,
            timeout=60  # Longer timeout for file uploads
        )
        
        if not response.ok:
            try:
                error_body = response.json()
                logger.error(f"Smartsheet API error: {response.status_code} - {error_body}")
            except Exception:
                logger.error(f"Smartsheet API error: {response.status_code} - {response.text[:500]}")
        
        response.raise_for_status()
        result = response.json()
        
        logger.info(f"Attached file '{file_name}' to row {row_id} in sheet {sheet_id}")
        return result.get("result", {})
    
    def _download_and_attach_file(
        self,
        sheet_id: int,
        row_id: int,
        url: str,
        file_name: str
    ) -> Dict[str, Any]:
        """
        Download file from URL and attach as binary content.
        
        Used when URL is too long (>500 chars) for Smartsheet's LINK attachment type.
        """
        import base64
        import mimetypes
        
        # Download file
        try:
            self._rate_limiter.wait()
            response = requests.get(url, timeout=60)
            response.raise_for_status()
            file_bytes = response.content
        except Exception as e:
            raise SmartsheetError(f"Failed to download file from URL: {e}")
        
        # Guess content type
        content_type, _ = mimetypes.guess_type(file_name)
        content_type = content_type or "application/octet-stream"
        
        # Upload as binary
        api_url = f"{self.base_url}/sheets/{sheet_id}/rows/{row_id}/attachments"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Disposition": f'attachment; filename="{file_name}"',
            "Content-Type": content_type
        }
        
        self._rate_limiter.wait()
        upload_response = requests.post(
            api_url,
            headers=headers,
            data=file_bytes,
            timeout=60
        )
        
        if not upload_response.ok:
            try:
                error_body = upload_response.json()
                logger.error(f"Smartsheet API error: {upload_response.status_code} - {error_body}")
            except Exception:
                logger.error(f"Smartsheet API error: {upload_response.status_code} - {upload_response.text[:500]}")
        
        upload_response.raise_for_status()
        result = upload_response.json()
        
        logger.info(f"Downloaded and attached file '{file_name}' to row {row_id}")
        return result.get("result", {})

    def refresh_caches(self):
        """Clear all caches and force reload."""
        with self._sheet_name_to_id_lock:
            self._sheet_name_to_id.clear()
        
        with self._column_cache_lock:
            self._column_cache.clear()
        
        logger.info("Cleared all caches")


# ============== Aliases for backward compatibility ==============
# These map old physical names to the new logical name system

def find_row_by_column(client: SmartsheetClient, sheet_name: str, column_name: str, value: Any) -> Optional[Dict[str, Any]]:
    """Backward compatible wrapper."""
    return client.find_row(sheet_name, column_name, value)


# ============== Thread-safe Singleton ==============

_client: Optional[SmartsheetClient] = None
_client_lock = threading.Lock()


def get_smartsheet_client(reset: bool = False) -> SmartsheetClient:
    """
    Get or create the singleton Smartsheet client.
    Thread-safe implementation.
    
    Args:
        reset: If True, creates a new client instance
    
    Returns:
        SmartsheetClient instance
    """
    global _client
    
    with _client_lock:
        if reset or _client is None:
            _client = SmartsheetClient()
        return _client


def reset_smartsheet_client():
    """Reset the singleton client."""
    global _client
    with _client_lock:
        _client = None
