"""
Pytest Configuration and Shared Fixtures
=========================================

This module provides the test infrastructure for the Ducts Manufacturing
Inventory Management System test suite.

Updated for ID-First Architecture
---------------------------------
The mock client now supports both:
- Logical names (Sheet.TAG_REGISTRY) via manifest lookup
- Physical names ("02 Tag Sheet Registry") via fallback
- Numeric IDs directly

Key Components
--------------
MockManifest
    In-memory manifest simulating workspace_manifest.json

MockSmartsheetStorage
    In-memory storage simulating Smartsheet behavior

MockSmartsheetClient
    Mock client matching the new v2 API (find_row, resolve_sheet_id, etc.)

Available Fixtures
------------------
mock_manifest : MockManifest
    Mock workspace manifest (function scope)

mock_storage : MockSmartsheetStorage
    Fresh in-memory storage for each test (function scope)

mock_client : MockSmartsheetClient
    Mock Smartsheet client using the storage (function scope)

patched_client : MockSmartsheetClient
    Client with get_smartsheet_client patched globally (function scope)

factory : TestDataFactory
    Factory for creating test data (function scope)
"""

import pytest
import os
import json
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Union
from unittest.mock import MagicMock, patch

# Add parent directory to path for imports
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.sheet_config import SheetName, ColumnName


# ============================================================================
# Environment Setup
# ============================================================================

@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """Set up test environment variables before any tests run."""
    os.environ.setdefault("SMARTSHEET_API_KEY", "test-api-key-12345")
    os.environ.setdefault("SMARTSHEET_WORKSPACE_ID", "test-workspace-12345")
    os.environ.setdefault("SMARTSHEET_BASE_URL", "https://api.smartsheet.eu/2.0")
    yield


# ============================================================================
# Mock Manifest (ID-First Architecture)
# ============================================================================

class MockManifest:
    """
    Mock workspace manifest for testing.
    
    Maps logical names to mock IDs, simulating workspace_manifest.json.
    """
    
    def __init__(self):
        self._data = {
            "_meta": {
                "description": "Mock Manifest",
                "version": "1.0.0",
                "generated_at": datetime.now().isoformat(),
                "workspace_id": 1000,
            },
            "workspace": {
                "id": 1000,
                "name": "Mock Workspace"
            },
            "folders": {
                "01_COMMERCIAL_AND_DEMAND": {"id": 101, "name": "01. Commercial and Demand"},
                "02_TAG_SHEET_REGISTRY": {"id": 102, "name": "02. Tag Sheet Registry"},
                "03_PRODUCTION_PLANNING": {"id": 103, "name": "03. Production Planning"},
                "04_PRODUCTION_AND_DELIVERY": {"id": 104, "name": "04. Production and Delivery"},
            },
            "sheets": {
                "REFERENCE_DATA": {
                    "id": 1000,
                    "name": SheetName.REFERENCE_DATA.value,
                    "folder": None,
                    "columns": {
                        "CUSTOMER_NAME": {"id": 10001, "name": "Customer Name", "type": "TEXT_NUMBER"},
                        "TERMS_OF_PAYMENT_ID": {"id": 10002, "name": "Terms of Payment ID", "type": "TEXT_NUMBER"},
                    }
                },
                "CONFIG": {
                    "id": 1001,
                    "name": SheetName.CONFIG.value,
                    "folder": None,
                    "columns": {
                        "CONFIG_KEY": {"id": 10011, "name": "config_key", "type": "TEXT_NUMBER"},
                        "CONFIG_VALUE": {"id": 10012, "name": "config_value", "type": "TEXT_NUMBER"},
                        "EFFECTIVE_FROM": {"id": 10013, "name": "effective_from", "type": "DATE"},
                        "CHANGED_BY": {"id": 10014, "name": "changed_by", "type": "TEXT_NUMBER"},
                    }
                },
                "LPO_MASTER": {
                    "id": 1002,
                    "name": SheetName.LPO_MASTER.value,
                    "folder": "01_COMMERCIAL_AND_DEMAND",
                    "columns": {
                        "LPO_ID": {"id": 10021, "name": "LPO ID", "type": "TEXT_NUMBER"},
                        "CUSTOMER_LPO_REF": {"id": 10022, "name": "Customer LPO Ref", "type": "TEXT_NUMBER"},
                        "SAP_REFERENCE": {"id": 10023, "name": "SAP Reference", "type": "TEXT_NUMBER"},
                        "CUSTOMER_NAME": {"id": 10024, "name": "Customer Name", "type": "TEXT_NUMBER"},
                        "BRAND": {"id": 10025, "name": "Brand", "type": "TEXT_NUMBER"},
                        "LPO_STATUS": {"id": 10026, "name": "LPO Status", "type": "PICKLIST"},
                        "PO_QUANTITY_SQM": {"id": 10027, "name": "PO Quantity (Sqm)", "type": "TEXT_NUMBER"},
                        "DELIVERED_QUANTITY_SQM": {"id": 10028, "name": "Delivered Quantity (Sqm)", "type": "TEXT_NUMBER"},
                    }
                },
                "TAG_REGISTRY": {
                    "id": 1003,
                    "name": SheetName.TAG_REGISTRY.value,  # Note: matches actual Smartsheet typo
                    "folder": "02_TAG_SHEET_REGISTRY",
                    "columns": {
                        "TAG_NAME": {"id": 10031, "name": "Tag Sheet Name/ Rev", "type": "TEXT_NUMBER"},
                        "STATUS": {"id": 10032, "name": "Status", "type": "PICKLIST"},
                        "LPO_SAP_REFERENCE": {"id": 10033, "name": "LPO SAP Reference Link", "type": "TEXT_NUMBER"},
                        "REQUIRED_DELIVERY_DATE": {"id": 10034, "name": "Required Delivery Date", "type": "DATE"},
                        "ESTIMATED_QUANTITY": {"id": 10035, "name": "Estimated Quantity", "type": "TEXT_NUMBER"},
                        "FILE_HASH": {"id": 10036, "name": "File Hash", "type": "TEXT_NUMBER"},
                        "CLIENT_REQUEST_ID": {"id": 10037, "name": "Client Request ID", "type": "TEXT_NUMBER"},
                        "SUBMITTED_BY": {"id": 10038, "name": "Submitted By", "type": "TEXT_NUMBER"},
                        "CUSTOMER_NAME": {"id": 10039, "name": "Customer Name", "type": "TEXT_NUMBER"},
                        "BRAND": {"id": 100310, "name": "Brand", "type": "TEXT_NUMBER"},
                        "REMARKS": {"id": 100311, "name": "Remarks", "type": "TEXT_NUMBER"},
                    }
                },
                "EXCEPTION_LOG": {
                    "id": 1004,
                    "name": SheetName.EXCEPTION_LOG.value,
                    "folder": "04_PRODUCTION_AND_DELIVERY",
                    "columns": {
                        "EXCEPTION_ID": {"id": 10041, "name": "Exception ID", "type": "TEXT_NUMBER"},
                        "CREATED_AT": {"id": 10042, "name": "Created At", "type": "DATETIME"},
                        "SOURCE": {"id": 10043, "name": "Source", "type": "PICKLIST"},
                        "RELATED_TAG_ID": {"id": 10044, "name": "Related Tag ID", "type": "TEXT_NUMBER"},
                        "REASON_CODE": {"id": 10045, "name": "Reason Code", "type": "PICKLIST"},
                        "SEVERITY": {"id": 10046, "name": "Severity", "type": "PICKLIST"},
                        "STATUS": {"id": 10047, "name": "Status", "type": "PICKLIST"},
                        "SLA_DUE": {"id": 10048, "name": "SLA Due", "type": "DATETIME"},
                        "RESOLUTION_ACTION": {"id": 10049, "name": "Resolution Action", "type": "TEXT_NUMBER"},
                        "QUANTITY": {"id": 100410, "name": "Quantity", "type": "TEXT_NUMBER"},
                        "MATERIAL_CODE": {"id": 100411, "name": "Material Code", "type": "TEXT_NUMBER"},
                        "RELATED_TXN_ID": {"id": 100412, "name": "Related Txn ID", "type": "TEXT_NUMBER"},
                    }
                },
                "USER_ACTION_LOG": {
                    "id": 1005,
                    "name": SheetName.USER_ACTION_LOG.value,
                    "folder": "04_PRODUCTION_AND_DELIVERY",
                    "columns": {
                        "ACTION_ID": {"id": 10051, "name": "Action ID", "type": "TEXT_NUMBER"},
                        "TIMESTAMP": {"id": 10052, "name": "Timestamp", "type": "DATETIME"},
                        "USER_ID": {"id": 10053, "name": "User ID", "type": "TEXT_NUMBER"},
                        "ACTION_TYPE": {"id": 10054, "name": "Action Type", "type": "PICKLIST"},
                        "TARGET_TABLE": {"id": 10055, "name": "Target Table", "type": "TEXT_NUMBER"},
                        "TARGET_ID": {"id": 10056, "name": "Target ID", "type": "TEXT_NUMBER"},
                        "OLD_VALUE": {"id": 10057, "name": "Old Value", "type": "TEXT_NUMBER"},
                        "NEW_VALUE": {"id": 10058, "name": "New Value", "type": "TEXT_NUMBER"},
                        "NOTES": {"id": 10059, "name": "Notes", "type": "TEXT_NUMBER"},
                    }
                },
            }
        }
    
    def is_loaded(self) -> bool:
        return self._loaded
    
    def is_empty(self) -> bool:
        return len(self._data.get("sheets", {})) == 0
    
    def get_sheet_id(self, logical_name: str) -> Optional[int]:
        """Get sheet ID by logical name."""
        sheets = self._data.get("sheets", {})
        sheet_info = sheets.get(logical_name)
        return sheet_info.get("id") if sheet_info else None
    
    def get_sheet_name(self, logical_name: str) -> Optional[str]:
        """Get physical sheet name by logical name."""
        sheets = self._data.get("sheets", {})
        sheet_info = sheets.get(logical_name)
        return sheet_info.get("name") if sheet_info else None
    
    def get_column_id(self, sheet_logical_name: str, column_logical_name: str) -> Optional[int]:
        """Get column ID by logical names."""
        sheets = self._data.get("sheets", {})
        sheet_info = sheets.get(sheet_logical_name)
        if not sheet_info:
            return None
        columns = sheet_info.get("columns", {})
        column_info = columns.get(column_logical_name)
        return column_info.get("id") if column_info else None
    
    def get_column_name(self, sheet_logical_name: str, column_logical_name: str) -> Optional[str]:
        """Get physical column name by logical names."""
        sheets = self._data.get("sheets", {})
        sheet_info = sheets.get(sheet_logical_name)
        if not sheet_info:
            return None
        columns = sheet_info.get("columns", {})
        column_info = columns.get(column_logical_name)
        return column_info.get("name") if column_info else None


@pytest.fixture
def mock_manifest():
    """Create a mock manifest for each test."""
    return MockManifest()


# ============================================================================
# Mock Smartsheet Storage
# ============================================================================

class MockSmartsheetStorage:
    """In-memory storage that simulates Smartsheet behavior."""
    
    def __init__(self, manifest: MockManifest = None):
        self.manifest = manifest or MockManifest()
        self.sheets: Dict[str, Dict] = {}
        self._row_id_counter = 1000
        self._initialize_sheets()
    
    def _initialize_sheets(self):
        """Initialize all sheets with proper column structures from manifest."""
        # Build sheets from manifest
        for logical_name, sheet_info in self.manifest._data.get("sheets", {}).items():
            physical_name = sheet_info["name"]
            columns = []
            for col_logical, col_info in sheet_info.get("columns", {}).items():
                columns.append({
                    "id": col_info["id"],
                    "title": col_info["name"]
                })
            
            self.sheets[physical_name] = {
                "id": sheet_info["id"],
                "columns": columns,
                "rows": []
            }
        
        # Initialize sequence counters in config
        self._init_sequences()
    
    def _init_sequences(self):
        """Initialize sequence counters."""
        sequences = [
            ("seq_tag", "0"),
            ("seq_exception", "0"),
            ("seq_allocation", "0"),
            ("seq_consumption", "0"),
            ("seq_delivery", "0"),
            ("seq_nesting", "0"),
            ("seq_remnant", "0"),
            ("seq_filler", "0"),
            ("seq_txn", "0"),
        ]
        for key, value in sequences:
            self._add_row_internal("00a Config", {
                "config_key": key,
                "config_value": value,
                "effective_from": datetime.now().strftime("%Y-%m-%d"),
                "changed_by": "system"
            })
    
    def _get_next_row_id(self) -> int:
        self._row_id_counter += 1
        return self._row_id_counter
    
    def _resolve_sheet_name(self, sheet_ref: Union[str, int]) -> str:
        """Resolve a sheet reference to its physical name."""
        if isinstance(sheet_ref, int):
            # Find by ID
            for name, data in self.sheets.items():
                if data.get("id") == sheet_ref:
                    return name
            raise ValueError(f"Sheet ID {sheet_ref} not found")
        
        # Try as logical name first
        physical_name = self.manifest.get_sheet_name(sheet_ref)
        if physical_name and physical_name in self.sheets:
            return physical_name
        
        # Try as physical name
        if sheet_ref in self.sheets:
            return sheet_ref
        
        raise ValueError(f"Sheet '{sheet_ref}' not found")
    
    def _add_row_internal(self, sheet_name: str, row_data: Dict[str, Any]) -> Dict:
        """Internal method to add a row."""
        sheet = self.sheets.get(sheet_name)
        if not sheet:
            raise ValueError(f"Sheet '{sheet_name}' not found")
        
        row_id = self._get_next_row_id()
        cells = []
        for col in sheet["columns"]:
            col_name = col["title"]
            if col_name in row_data:
                cells.append({
                    "columnId": col["id"],
                    "value": row_data[col_name]
                })
        
        row = {"id": row_id, "cells": cells}
        sheet["rows"].append(row)
        return {"id": row_id, **row_data}
    
    def add_row(self, sheet_ref: Union[str, int], row_data: Dict[str, Any]) -> Dict:
        """Add a row to a sheet."""
        sheet_name = self._resolve_sheet_name(sheet_ref)
        return self._add_row_internal(sheet_name, row_data)
    
    def find_rows(self, sheet_ref: Union[str, int], column_name: str, value: Any) -> List[Dict]:
        """Find rows where column matches value."""
        sheet_name = self._resolve_sheet_name(sheet_ref)
        sheet = self.sheets.get(sheet_name)
        if not sheet:
            raise ValueError(f"Sheet '{sheet_name}' not found")
        
        col_id = None
        for col in sheet["columns"]:
            if col["title"] == column_name:
                col_id = col["id"]
                break
        
        if col_id is None:
            raise ValueError(f"Column '{column_name}' not found")
        
        results = []
        for row in sheet["rows"]:
            for cell in row["cells"]:
                if cell.get("columnId") == col_id and cell.get("value") == value:
                    row_dict = self._row_to_dict(row, sheet["columns"])
                    results.append(row_dict)
                    break
        
        return results
    
    def update_row(self, sheet_ref: Union[str, int], row_id: int, updates: Dict[str, Any]) -> Dict:
        """Update a row."""
        sheet_name = self._resolve_sheet_name(sheet_ref)
        sheet = self.sheets.get(sheet_name)
        if not sheet:
            raise ValueError(f"Sheet '{sheet_name}' not found")
        
        for row in sheet["rows"]:
            if row["id"] == row_id:
                for col in sheet["columns"]:
                    col_name = col["title"]
                    if col_name in updates:
                        found = False
                        for cell in row["cells"]:
                            if cell["columnId"] == col["id"]:
                                cell["value"] = updates[col_name]
                                found = True
                                break
                        if not found:
                            row["cells"].append({
                                "columnId": col["id"],
                                "value": updates[col_name]
                            })
                return self._row_to_dict(row, sheet["columns"])
        
        raise ValueError(f"Row {row_id} not found")
    
    def _row_to_dict(self, row: Dict, columns: List[Dict]) -> Dict:
        """Convert row to dictionary."""
        col_map = {col["id"]: col["title"] for col in columns}
        result = {"row_id": row["id"]}
        for cell in row.get("cells", []):
            col_name = col_map.get(cell.get("columnId"))
            if col_name:
                result[col_name] = cell.get("value")
        return result
    
    def clear(self):
        """Clear all data and reinitialize."""
        self._row_id_counter = 1000
        self._initialize_sheets()


# ============================================================================
# Mock Smartsheet Client (v2 API)
# ============================================================================

class MockSmartsheetClient:
    """
    Mock Smartsheet client that matches the v2 API.
    
    Supports:
    - Logical names (via manifest)
    - Physical names (direct lookup)
    - Numeric IDs
    """
    
    def __init__(self, storage: MockSmartsheetStorage, manifest: MockManifest = None):
        self.storage = storage
        self._manifest = manifest or storage.manifest
        self.api_key = "test-api-key"
        self.base_url = "https://api.smartsheet.eu/2.0"
        self.workspace_id = "test-workspace"
    
    def resolve_sheet_id(self, sheet_ref: Union[str, int]) -> int:
        """Resolve a sheet reference to its numeric ID."""
        if isinstance(sheet_ref, int):
            return sheet_ref
        
        # Try manifest first
        manifest_id = self._manifest.get_sheet_id(sheet_ref)
        if manifest_id:
            return manifest_id
        
        # Fallback to name lookup
        for name, data in self.storage.sheets.items():
            if name == sheet_ref:
                return data.get("id", 0)
        
        raise ValueError(f"Sheet '{sheet_ref}' not found")
    
    def get_sheet(self, sheet_ref: Union[str, int], include_columns: bool = True) -> Dict:
        """Get sheet data."""
        sheet_name = self.storage._resolve_sheet_name(sheet_ref)
        sheet = self.storage.sheets.get(sheet_name)
        if not sheet:
            raise ValueError(f"Sheet '{sheet_ref}' not found")
        return sheet
    
    def find_rows(self, sheet_ref: Union[str, int], column_ref: str, value: Any) -> List[Dict]:
        """Find rows matching column value."""
        sheet_name = self.storage._resolve_sheet_name(sheet_ref)
        
        # Resolve column name (try physical first, then logical via manifest)
        col_name = column_ref
        if isinstance(sheet_ref, str):
            physical_col = self._manifest.get_column_name(sheet_ref, column_ref)
            if physical_col:
                col_name = physical_col
        
        return self.storage.find_rows(sheet_name, col_name, value)
    
    def find_row(self, sheet_ref: Union[str, int], column_ref: str, value: Any) -> Optional[Dict]:
        """Find first row matching column value."""
        rows = self.find_rows(sheet_ref, column_ref, value)
        return rows[0] if rows else None
    
    # Backward compatibility alias
    def find_row_by_column(self, sheet_name: str, column_name: str, value: Any) -> Optional[Dict]:
        """Backward compatible method - maps to find_row."""
        return self.find_row(sheet_name, column_name, value)
    
    def add_row(self, sheet_ref: Union[str, int], row_data: Dict[str, Any]) -> Dict:
        """Add a row to a sheet."""
        sheet_name = self.storage._resolve_sheet_name(sheet_ref)
        
        # Resolve logical column names to physical names
        resolved_data = {}
        for key, val in row_data.items():
            if val is None:
                continue
            
            # Try to find physical name via manifest
            if isinstance(sheet_ref, str):
                physical_name = self._manifest.get_column_name(sheet_ref, key)
                if physical_name:
                    resolved_data[physical_name] = val
                    continue
            
            resolved_data[key] = val
        
        return self.storage.add_row(sheet_name, resolved_data)
    
    def update_row(self, sheet_ref: Union[str, int], row_id: int, updates: Dict[str, Any]) -> Dict:
        """Update a row."""
        sheet_name = self.storage._resolve_sheet_name(sheet_ref)
        
        # Resolve logical column names to physical names
        resolved_updates = {}
        for key, val in updates.items():
            if isinstance(sheet_ref, str):
                physical_name = self._manifest.get_column_name(sheet_ref, key)
                if physical_name:
                    resolved_updates[physical_name] = val
                    continue
            resolved_updates[key] = val
        
        return self.storage.update_row(sheet_name, row_id, resolved_updates)
    
    def get_config_value(self, config_key: str) -> Optional[str]:
        """Get a configuration value by key."""
        row = self.find_row("CONFIG", "CONFIG_KEY", config_key)
        if not row:
            # Try physical name fallback
            row = self.find_row("00a Config", "config_key", config_key)
        
        if row:
            return row.get("config_value") or row.get("CONFIG_VALUE")
        return None
    
    def refresh_caches(self):
        """Clear all caches (no-op for mock)."""
        pass


@pytest.fixture
def mock_storage(mock_manifest):
    """Create a fresh mock storage for each test."""
    storage = MockSmartsheetStorage(mock_manifest)
    return storage


@pytest.fixture
def mock_client(mock_storage, mock_manifest):
    """Create a mock Smartsheet client."""
    return MockSmartsheetClient(mock_storage, mock_manifest)


@pytest.fixture
def patched_client(mock_client, mock_manifest):
    """Patch the get_smartsheet_client and get_manifest functions to return mocks."""
    with patch('shared.smartsheet_client.get_smartsheet_client', return_value=mock_client):
        with patch('shared.get_smartsheet_client', return_value=mock_client):
            with patch('shared.manifest.get_manifest', return_value=mock_manifest):
                with patch('shared.get_manifest', return_value=mock_manifest):
                    yield mock_client


# ============================================================================
# Test Data Factories
# ============================================================================

class TestDataFactory:
    """Factory for creating test data objects."""
    
    @staticmethod
    def create_tag_ingest_request(
        client_request_id: str = None,
        lpo_sap_reference: str = "SAP-TEST-001",
        required_area_m2: float = 50.0,
        requested_delivery_date: str = None,
        uploaded_by: str = "test.user@company.com",
        **kwargs
    ) -> Dict[str, Any]:
        """Create a TagIngestRequest payload."""
        return {
            "client_request_id": client_request_id or str(uuid.uuid4()),
            "lpo_sap_reference": lpo_sap_reference,
            "required_area_m2": required_area_m2,
            "requested_delivery_date": requested_delivery_date or (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d"),
            "uploaded_by": uploaded_by,
            **kwargs
        }
    
    @staticmethod
    def create_lpo(
        sap_reference: str = "SAP-TEST-001",
        customer_lpo_ref: str = "CUST-LPO-001",
        status: str = "Active",
        po_quantity: float = 500.0,
        delivered_quantity: float = 0.0,
        **kwargs
    ) -> Dict[str, Any]:
        """Create an LPO record."""
        return {
            "LPO ID": f"LPO-{uuid.uuid4().hex[:8].upper()}",
            "Customer LPO Ref": customer_lpo_ref,
            "SAP Reference": sap_reference,
            "Customer Name": kwargs.get("customer_name", "Test Customer"),
            "Brand": kwargs.get("brand", "Test Brand"),
            "LPO Status": status,
            "PO Quantity (Sqm)": po_quantity,
            "Delivered Quantity (Sqm)": delivered_quantity,
        }
    
    @staticmethod
    def create_tag_record(
        tag_name: str = "TEST-TAG-001",
        status: str = "Draft",
        **kwargs
    ) -> Dict[str, Any]:
        """Create a Tag record."""
        return {
            "Tag Sheet Name/ Rev": tag_name,
            "Status": status,
            "Client Request ID": kwargs.get("client_request_id", str(uuid.uuid4())),
            "File Hash": kwargs.get("file_hash"),
            "Submitted By": kwargs.get("submitted_by", "test.user@company.com"),
            **{k: v for k, v in kwargs.items() if k not in ["client_request_id", "file_hash", "submitted_by"]}
        }


@pytest.fixture
def factory():
    """Provide test data factory."""
    return TestDataFactory()


# ============================================================================
# HTTP Request Mock
# ============================================================================

class MockHttpRequest:
    """Mock Azure Functions HttpRequest."""
    
    def __init__(self, body: Dict[str, Any] = None, headers: Dict[str, str] = None):
        self._body = json.dumps(body or {}).encode()
        self.headers = headers or {}
    
    def get_json(self) -> Dict:
        return json.loads(self._body)
    
    def get_body(self) -> bytes:
        return self._body


@pytest.fixture
def mock_http_request():
    """Factory for creating mock HTTP requests."""
    def _create(body: Dict = None, headers: Dict = None):
        return MockHttpRequest(body, headers)
    return _create


# ============================================================================
# Assertion Helpers
# ============================================================================

class AssertionHelpers:
    """Helper methods for test assertions."""
    
    @staticmethod
    def assert_tag_created(storage: MockSmartsheetStorage, client_request_id: str):
        """Assert that a tag was created with the given client request ID."""
        tags = storage.find_rows("02 Tag Sheet Registry", "Client Request ID", client_request_id)
        assert len(tags) == 1, f"Expected 1 tag with client_request_id={client_request_id}, found {len(tags)}"
        return tags[0]
    
    @staticmethod
    def assert_exception_created(storage: MockSmartsheetStorage, reason_code: str):
        """Assert that an exception was created with the given reason code."""
        exceptions = storage.find_rows("99 Exception Log", "Reason Code", reason_code)
        assert len(exceptions) >= 1, f"Expected exception with reason_code={reason_code}"
        return exceptions[-1]  # Return latest
    
    @staticmethod
    def assert_user_action_logged(storage: MockSmartsheetStorage, action_type: str):
        """Assert that a user action was logged."""
        actions = storage.find_rows("98 User Action Log", "Action Type", action_type)
        assert len(actions) >= 1, f"Expected user action with action_type={action_type}"
        return actions[-1]


@pytest.fixture
def assertions():
    """Provide assertion helpers."""
    return AssertionHelpers()


# ============================================================================
# Test Markers
# ============================================================================

def pytest_configure(config):
    """Configure custom pytest markers."""
    config.addinivalue_line("markers", "unit: mark test as unit test")
    config.addinivalue_line("markers", "integration: mark test as integration test")
    config.addinivalue_line("markers", "e2e: mark test as end-to-end test")
    config.addinivalue_line("markers", "slow: mark test as slow running")
    config.addinivalue_line("markers", "acceptance: mark test as acceptance criteria test")
