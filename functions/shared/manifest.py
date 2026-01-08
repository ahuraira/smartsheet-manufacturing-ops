"""
Workspace Manifest Manager
==========================

Manages the workspace manifest that maps logical names to immutable Smartsheet IDs.

SOTA Architecture
-----------------
1. **IDs are primary** - Once a workspace is created, we use IDs everywhere
2. **Names are fallback** - Only used when IDs not in manifest (new sheets)
3. **Manifest is generated** - By create_workspace.py or fetch_manifest.py
4. **Manifest is immutable** - Never edit IDs manually, regenerate if needed

This ensures:
- Sheet/column renames don't break the application
- Clear separation between logical names (code) and physical names (Smartsheet)
- Easy migration between environments (just swap manifest)

Usage
-----
>>> from shared.manifest import WorkspaceManifest
>>> 
>>> manifest = WorkspaceManifest.load()
>>> sheet_id = manifest.get_sheet_id("TAG_REGISTRY")  # Returns numeric ID
>>> column_id = manifest.get_column_id("TAG_REGISTRY", "FILE_HASH")
"""

import os
import json
import logging
from typing import Dict, Optional, Any
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


class ManifestError(Exception):
    """Raised when manifest operations fail."""
    pass


class ManifestNotFoundError(ManifestError):
    """Raised when manifest file doesn't exist."""
    pass


class SheetNotInManifestError(ManifestError):
    """Raised when a sheet is not found in manifest."""
    pass


class ColumnNotInManifestError(ManifestError):
    """Raised when a column is not found in manifest."""
    pass


class WorkspaceManifest:
    """
    Manages the workspace manifest containing immutable Smartsheet IDs.
    
    The manifest maps logical names (used in code) to physical IDs (Smartsheet).
    This decouples the application from sheet/column name changes.
    
    Manifest Structure:
    {
        "_meta": { "version": "1.0.0", "generated_at": "...", ... },
        "workspace": { "id": 123456, "name": "Production Workspace" },
        "folders": {
            "01_COMMERCIAL": { "id": 111, "name": "01. Commercial and Demand" },
            ...
        },
        "sheets": {
            "TAG_REGISTRY": {
                "id": 222,
                "name": "02 Tag Sheet Registry",
                "folder": "02_TAG_REGISTRY",
                "columns": {
                    "TAG_ID": { "id": 333, "name": "Tag ID", "type": "TEXT_NUMBER" },
                    "FILE_HASH": { "id": 444, "name": "File Hash", "type": "TEXT_NUMBER" },
                    ...
                }
            },
            ...
        }
    }
    """
    
    # Default manifest file locations (checked in order)
    DEFAULT_LOCATIONS = [
        "workspace_manifest.json",                    # Current directory
        "../workspace_manifest.json",                 # Parent directory
        "functions/workspace_manifest.json",          # From project root
    ]
    
    # Environment variable for manifest path override
    ENV_MANIFEST_PATH = "SMARTSHEET_MANIFEST_PATH"
    
    def __init__(self, manifest_path: Optional[str] = None):
        """
        Initialize manifest manager.
        
        Args:
            manifest_path: Path to manifest file. If None, searches default locations.
        """
        self._manifest_path = manifest_path
        self._data: Optional[Dict[str, Any]] = None
        self._loaded = False
    
    @classmethod
    def load(cls, path: Optional[str] = None) -> "WorkspaceManifest":
        """
        Load manifest from file.
        
        Args:
            path: Optional explicit path. If None, searches default locations.
        
        Returns:
            Loaded WorkspaceManifest instance
        
        Raises:
            ManifestNotFoundError: If manifest cannot be found
        """
        instance = cls(path)
        instance._load()
        return instance
    
    @classmethod
    def load_or_empty(cls, path: Optional[str] = None) -> "WorkspaceManifest":
        """
        Load manifest or return empty manifest if not found.
        Useful for scenarios where manifest is optional (name fallback).
        """
        try:
            return cls.load(path)
        except ManifestNotFoundError:
            logger.warning("Manifest not found, using empty manifest (name fallback mode)")
            instance = cls(path)
            instance._data = cls._empty_manifest()
            instance._loaded = True
            return instance
    
    @staticmethod
    def _empty_manifest() -> Dict[str, Any]:
        """Create an empty manifest structure."""
        return {
            "_meta": {
                "version": "1.0.0",
                "generated_at": None,
                "mode": "fallback"
            },
            "workspace": {"id": None, "name": None},
            "folders": {},
            "sheets": {}
        }
    
    def _find_manifest_path(self) -> Optional[str]:
        """Find manifest file in default locations."""
        # Check environment variable first
        env_path = os.environ.get(self.ENV_MANIFEST_PATH)
        if env_path and os.path.exists(env_path):
            return env_path
        
        # Check relative to this file
        this_dir = Path(__file__).parent
        for location in self.DEFAULT_LOCATIONS:
            check_path = this_dir / location
            if check_path.exists():
                return str(check_path)
        
        # Check relative to current working directory
        for location in self.DEFAULT_LOCATIONS:
            if os.path.exists(location):
                return location
        
        return None
    
    def _load(self):
        """Load manifest from file."""
        path = self._manifest_path or self._find_manifest_path()
        
        if not path or not os.path.exists(path):
            raise ManifestNotFoundError(
                f"Workspace manifest not found. Searched locations: {self.DEFAULT_LOCATIONS}. "
                f"Run 'python create_workspace.py' or 'python fetch_manifest.py' to generate one."
            )
        
        try:
            with open(path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
            self._manifest_path = path
            self._loaded = True
            logger.info(f"Loaded workspace manifest from: {path}")
        except json.JSONDecodeError as e:
            raise ManifestError(f"Invalid JSON in manifest file: {e}")
    
    def save(self, path: Optional[str] = None):
        """Save manifest to file."""
        save_path = path or self._manifest_path
        if not save_path:
            raise ManifestError("No path specified for saving manifest")
        
        self._data["_meta"]["generated_at"] = datetime.now().isoformat()
        
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Saved workspace manifest to: {save_path}")
    
    @property
    def workspace_id(self) -> Optional[int]:
        """Get workspace ID."""
        return self._data.get("workspace", {}).get("id") if self._data else None
    
    @property
    def workspace_name(self) -> Optional[str]:
        """Get workspace name."""
        return self._data.get("workspace", {}).get("name") if self._data else None
    
    def get_sheet_id(self, logical_name: str) -> Optional[int]:
        """
        Get sheet ID by logical name.
        
        Args:
            logical_name: Logical sheet name (e.g., "TAG_REGISTRY", "LPO_MASTER")
        
        Returns:
            Sheet ID or None if not in manifest
        """
        sheets = self._data.get("sheets", {}) if self._data else {}
        sheet_info = sheets.get(logical_name)
        return sheet_info.get("id") if sheet_info else None
    
    def get_sheet_name(self, logical_name: str) -> Optional[str]:
        """Get physical sheet name by logical name."""
        sheets = self._data.get("sheets", {}) if self._data else {}
        sheet_info = sheets.get(logical_name)
        return sheet_info.get("name") if sheet_info else None
    
    def get_column_id(self, sheet_logical_name: str, column_logical_name: str) -> Optional[int]:
        """
        Get column ID by sheet and column logical names.
        
        Args:
            sheet_logical_name: Logical sheet name (e.g., "TAG_REGISTRY")
            column_logical_name: Logical column name (e.g., "FILE_HASH")
        
        Returns:
            Column ID or None if not in manifest
        """
        sheets = self._data.get("sheets", {}) if self._data else {}
        sheet_info = sheets.get(sheet_logical_name)
        if not sheet_info:
            return None
        
        columns = sheet_info.get("columns", {})
        column_info = columns.get(column_logical_name)
        return column_info.get("id") if column_info else None
    
    def get_column_name(self, sheet_logical_name: str, column_logical_name: str) -> Optional[str]:
        """Get physical column name by logical names."""
        sheets = self._data.get("sheets", {}) if self._data else {}
        sheet_info = sheets.get(sheet_logical_name)
        if not sheet_info:
            return None
        
        columns = sheet_info.get("columns", {})
        column_info = columns.get(column_logical_name)
        return column_info.get("name") if column_info else None
    
    def get_all_sheet_ids(self) -> Dict[str, int]:
        """Get all sheet IDs as {logical_name: id} dict."""
        sheets = self._data.get("sheets", {}) if self._data else {}
        return {name: info.get("id") for name, info in sheets.items() if info.get("id")}
    
    def get_all_column_ids(self, sheet_logical_name: str) -> Dict[str, int]:
        """Get all column IDs for a sheet as {logical_name: id} dict."""
        sheets = self._data.get("sheets", {}) if self._data else {}
        sheet_info = sheets.get(sheet_logical_name)
        if not sheet_info:
            return {}
        
        columns = sheet_info.get("columns", {})
        return {name: info.get("id") for name, info in columns.items() if info.get("id")}
    
    def has_sheet(self, logical_name: str) -> bool:
        """Check if sheet exists in manifest."""
        sheets = self._data.get("sheets", {}) if self._data else {}
        return logical_name in sheets and sheets[logical_name].get("id") is not None
    
    def is_loaded(self) -> bool:
        """Check if manifest is loaded."""
        return self._loaded
    
    def is_empty(self) -> bool:
        """Check if manifest has no sheets defined."""
        sheets = self._data.get("sheets", {}) if self._data else {}
        return len(sheets) == 0
    
    # ============== Builder Methods (for create_workspace.py) ==============
    
    def set_workspace(self, workspace_id: int, workspace_name: str):
        """Set workspace info."""
        if not self._data:
            self._data = self._empty_manifest()
        self._data["workspace"] = {"id": workspace_id, "name": workspace_name}
    
    def add_folder(self, logical_name: str, folder_id: int, folder_name: str):
        """Add folder to manifest."""
        if not self._data:
            self._data = self._empty_manifest()
        self._data["folders"][logical_name] = {"id": folder_id, "name": folder_name}
    
    def add_sheet(self, logical_name: str, sheet_id: int, sheet_name: str, folder_logical_name: Optional[str] = None):
        """Add sheet to manifest."""
        if not self._data:
            self._data = self._empty_manifest()
        self._data["sheets"][logical_name] = {
            "id": sheet_id,
            "name": sheet_name,
            "folder": folder_logical_name,
            "columns": {}
        }
    
    def add_column(self, sheet_logical_name: str, column_logical_name: str, 
                   column_id: int, column_name: str, column_type: str):
        """Add column to sheet in manifest."""
        if sheet_logical_name not in self._data.get("sheets", {}):
            raise ManifestError(f"Sheet '{sheet_logical_name}' not in manifest. Add sheet first.")
        
        self._data["sheets"][sheet_logical_name]["columns"][column_logical_name] = {
            "id": column_id,
            "name": column_name,
            "type": column_type
        }


# Singleton for easy access
_manifest: Optional[WorkspaceManifest] = None


def get_manifest(force_reload: bool = False) -> WorkspaceManifest:
    """
    Get the singleton workspace manifest.
    
    Uses load_or_empty to support fallback mode when manifest doesn't exist.
    """
    global _manifest
    if _manifest is None or force_reload:
        _manifest = WorkspaceManifest.load_or_empty()
    return _manifest


def reset_manifest():
    """Reset the singleton manifest (useful for testing)."""
    global _manifest
    _manifest = None
