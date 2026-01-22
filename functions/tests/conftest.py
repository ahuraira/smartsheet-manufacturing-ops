"""
Pytest Configuration and Fixtures for Ducts Manufacturing Tests

Version 2.0 - Updated for v1.1.0 refactor with:
- ID-first architecture support
- Manifest mock
- Logical names (Sheet, Column)
- Base64 file content support
- New tag fields

This file provides:
- Mock Smartsheet client that simulates the real API
- Test data factories for various entity types
- HTTP request mocking for Azure Functions
- Assertion helpers for common test patterns
"""

import pytest
import json
import uuid
import base64
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from unittest.mock import MagicMock, patch

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============== Custom Pytest Markers ==============

def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "unit: Unit tests for individual modules")
    config.addinivalue_line("markers", "integration: Integration tests")
    config.addinivalue_line("markers", "e2e: End-to-end tests")
    config.addinivalue_line("markers", "acceptance: Acceptance criteria tests")
    config.addinivalue_line("markers", "slow: Slow running tests")


# ============== Mock Manifest ==============

class MockWorkspaceManifest:
    """Mock workspace manifest for tests."""
    
    def __init__(self):
        # Define sheet mappings
        self._sheets = {
            "TAG_REGISTRY": {"id": 1001, "name": "Tag Sheet Registry"},
            "LPO_MASTER": {"id": 1002, "name": "01 LPO Master LOG"},
            "CONFIG": {"id": 1003, "name": "00a Config"},
            "EXCEPTION_LOG": {"id": 1004, "name": "99 Exception Log"},
            "USER_ACTION_LOG": {"id": 1005, "name": "98 User Action Log"},
            "REFERENCE_DATA": {"id": 1006, "name": "00 Reference Data"},
            # v1.3.0 new sheets
            "MACHINE_MASTER": {"id": 1007, "name": "00b Machine Master"},
            "PRODUCTION_PLANNING": {"id": 1008, "name": "03 Production Planning"},
            "NESTING_LOG": {"id": 1009, "name": "04 Nesting Execution Log"},
            # Alias for physical name lookup in fn_schedule_tag
            "00B_MACHINE_MASTER": {"id": 1007, "name": "00b Machine Master"},
        }
        
        # Define column mappings per sheet
        self._columns = {
            "TAG_REGISTRY": {
                "TAG_ID": {"id": 2001, "name": "Tag ID"},
                "TAG_NAME": {"id": 2002, "name": "Tag Sheet Name/ Rev"},
                "DATE_TAG_SHEET_RECEIVED": {"id": 2003, "name": "Date Tag Sheet Received"},
                "REQUIRED_DELIVERY_DATE": {"id": 2004, "name": "Required Delivery Date"},
                "LPO_SAP_REFERENCE": {"id": 2005, "name": "LPO SAP Reference Link"},
                "LPO_STATUS": {"id": 2006, "name": "LPO Status"},
                "LPO_ALLOWABLE_WASTAGE": {"id": 2007, "name": "LPO Allowable Wastage"},
                "PRODUCTION_GATE": {"id": 2008, "name": "Production Gate"},
                "BRAND": {"id": 2009, "name": "Brand"},
                "CUSTOMER_NAME": {"id": 2010, "name": "Customer Name"},
                "PROJECT": {"id": 2011, "name": "Project"},
                "LOCATION": {"id": 2012, "name": "Location"},
                "ESTIMATED_QUANTITY": {"id": 2013, "name": "Estimated Quantity"},
                "STATUS": {"id": 2014, "name": "Status"},
                "SUBMITTED_BY": {"id": 2015, "name": "Submitted By"},
                "RECEIVED_THROUGH": {"id": 2016, "name": "Received Through"},
                "REMARKS": {"id": 2017, "name": "Remarks"},
                "FILE_HASH": {"id": 2018, "name": "File Hash"},
                "CLIENT_REQUEST_ID": {"id": 2019, "name": "Client Request ID"},
            },
            "LPO_MASTER": {
                "LPO_ID": {"id": 3001, "name": "LPO ID"},
                "CUSTOMER_LPO_REF": {"id": 3002, "name": "Customer LPO Ref"},
                "SAP_REFERENCE": {"id": 3003, "name": "SAP Reference"},
                "CUSTOMER_NAME": {"id": 3004, "name": "Customer Name"},
                "PROJECT_NAME": {"id": 3005, "name": "Project Name"},
                "LPO_STATUS": {"id": 3006, "name": "LPO Status"},
                "BRAND": {"id": 3007, "name": "Brand"},
                "WASTAGE_CONSIDERED_IN_COSTING": {"id": 3008, "name": "Wastage Considered in Costing"},
                "PO_QUANTITY_SQM": {"id": 3009, "name": "PO Quantity (Sqm)"},
                "DELIVERED_QUANTITY_SQM": {"id": 3010, "name": "Delivered Quantity (Sqm)"},
                "REMARKS": {"id": 3011, "name": "Remarks"},
                # v1.2.0 new columns
                "SOURCE_FILE_HASH": {"id": 3012, "name": "Source File Hash"},
                "FOLDER_URL": {"id": 3013, "name": "Folder URL"},
                "CLIENT_REQUEST_ID": {"id": 3014, "name": "Client Request ID"},
                "CREATED_BY": {"id": 3015, "name": "Created By"},
                "UPDATED_AT": {"id": 3016, "name": "Updated At"},
                "PRICE_PER_SQM": {"id": 3017, "name": "Price per Sqm"},
                "PO_VALUE": {"id": 3018, "name": "PO Value"},
                "TERMS_OF_PAYMENT": {"id": 3019, "name": "Terms of Payment"},
                "HOLD_REASON": {"id": 3020, "name": "Hold Reason"},
                "DELIVERED_VALUE": {"id": 3021, "name": "Delivered Value"},
                "PO_BALANCE_QUANTITY": {"id": 3022, "name": "PO Balance Quantity"},
                # v1.3.0 new columns
                "PLANNED_QUANTITY": {"id": 3023, "name": "Planned Quantity"},
                "ALLOCATED_QUANTITY": {"id": 3024, "name": "Allocated Quantity"},
                "UPDATED_BY": {"id": 3025, "name": "Updated By"},
            },
            "CONFIG": {
                "CONFIG_KEY": {"id": 4001, "name": "config_key"},
                "CONFIG_VALUE": {"id": 4002, "name": "config_value"},
                "EFFECTIVE_FROM": {"id": 4003, "name": "effective_from"},
                "CHANGED_BY": {"id": 4004, "name": "changed_by"},
            },
            "EXCEPTION_LOG": {
                "EXCEPTION_ID": {"id": 5001, "name": "Exception ID"},
                "CREATED_AT": {"id": 5002, "name": "Created At"},
                "SOURCE": {"id": 5003, "name": "Source"},
                "RELATED_TAG_ID": {"id": 5004, "name": "Related Tag ID"},
                "RELATED_TXN_ID": {"id": 5005, "name": "Related Txn ID"},
                "MATERIAL_CODE": {"id": 5006, "name": "Material Code"},
                "QUANTITY": {"id": 5007, "name": "Quantity"},
                "REASON_CODE": {"id": 5008, "name": "Reason Code"},
                "SEVERITY": {"id": 5009, "name": "Severity"},
                "STATUS": {"id": 5010, "name": "Status"},
                "SLA_DUE": {"id": 5011, "name": "SLA Due"},
                "RESOLUTION_ACTION": {"id": 5012, "name": "Resolution Action"},
            },
            "USER_ACTION_LOG": {
                "ACTION_ID": {"id": 6001, "name": "Action ID"},
                "TIMESTAMP": {"id": 6002, "name": "Timestamp"},
                "USER_ID": {"id": 6003, "name": "User ID"},
                "ACTION_TYPE": {"id": 6004, "name": "Action Type"},
                "TARGET_TABLE": {"id": 6005, "name": "Target Table"},
                "TARGET_ID": {"id": 6006, "name": "Target ID"},
                "OLD_VALUE": {"id": 6007, "name": "Old Value"},
                "NEW_VALUE": {"id": 6008, "name": "New Value"},
                "NOTES": {"id": 6009, "name": "Notes"},
            },
            # v1.3.0 new sheet columns
            "MACHINE_MASTER": {
                "MACHINE_ID": {"id": 7001, "name": "Machine ID"},
                "NAME": {"id": 7002, "name": "Machine Name"},
                "STATUS": {"id": 7003, "name": "Status"},
                "SQM_PER_HOUR": {"id": 7004, "name": "Sqm per Hour"},
                "AVAILABLE_SHIFTS": {"id": 7005, "name": "Available Shifts"},
            },
            "00B_MACHINE_MASTER": {  # Alias for physical name lookup
                "MACHINE_ID": {"id": 7001, "name": "Machine ID"},
                "NAME": {"id": 7002, "name": "Machine Name"},
                "STATUS": {"id": 7003, "name": "Status"},
                "SQM_PER_HOUR": {"id": 7004, "name": "Sqm per Hour"},
                "AVAILABLE_SHIFTS": {"id": 7005, "name": "Available Shifts"},
            },
            "PRODUCTION_PLANNING": {
                "SCHEDULE_ID": {"id": 8001, "name": "Schedule ID"},
                "TAG_SHEET_ID": {"id": 8002, "name": "Tag Sheet ID"},
                "PLANNED_DATE": {"id": 8003, "name": "Planned Date"},
                "SHIFT": {"id": 8004, "name": "Shift"},
                "MACHINE_ASSIGNED": {"id": 8005, "name": "Machine Assigned"},
                "PLANNED_QUANTITY": {"id": 8006, "name": "Planned Quantity"},
                "STATUS": {"id": 8007, "name": "Status"},
                "CREATED_BY": {"id": 8008, "name": "Created By"},
                "CREATED_AT": {"id": 8009, "name": "Created At"},
                "CLIENT_REQUEST_ID": {"id": 8010, "name": "Client Request ID"},
                "TRACE_ID": {"id": 8011, "name": "Trace ID"},
                "REMARKS": {"id": 8012, "name": "Remarks"},
            },
            "NESTING_LOG": {
                "NEST_SESSION_ID": {"id": 9001, "name": "Nest Session ID"},
                "TAG_SHEET_ID": {"id": 9002, "name": "Tag Sheet ID"},
                "TIMESTAMP": {"id": 9003, "name": "Timestamp"},
                "BRAND": {"id": 9004, "name": "Brand"},
                "SHEETS_CONSUMED_VIRTUAL": {"id": 9005, "name": "Sheets Consumed (Virtual)"},
                "EXPECTED_CONSUMPTION_M2": {"id": 9006, "name": "Expected Consumption (m2)"},
                "WASTAGE_PERCENTAGE": {"id": 9007, "name": "Wastage Percentage"},
                "PLANNED_DATE": {"id": 9008, "name": "Planned Date"},
                "FILE_HASH": {"id": 9009, "name": "File Hash"},
                "CLIENT_REQUEST_ID": {"id": 9010, "name": "Client Request ID"},
            },
        }
    
    def get_sheet_id(self, logical_name: str) -> Optional[int]:
        sheet = self._sheets.get(logical_name)
        return sheet["id"] if sheet else None
    
    def get_sheet_name(self, logical_name: str) -> Optional[str]:
        sheet = self._sheets.get(logical_name)
        return sheet["name"] if sheet else None
    
    def get_column_id(self, sheet_logical: str, column_logical: str) -> Optional[int]:
        cols = self._columns.get(sheet_logical, {})
        col = cols.get(column_logical)
        return col["id"] if col else None
    
    def get_column_name(self, sheet_logical: str, column_logical: str) -> Optional[str]:
        cols = self._columns.get(sheet_logical, {})
        col = cols.get(column_logical)
        return col["name"] if col else None
    
    def has_sheet(self, logical_name: str) -> bool:
        return logical_name in self._sheets
    
    def is_loaded(self) -> bool:
        return True
    
    def is_empty(self) -> bool:
        return False


# ============== Mock Smartsheet Storage ==============

class MockSmartsheetStorage:
    """In-memory storage simulating Smartsheet sheets."""
    
    def __init__(self):
        self._row_counter = 1000
        self.manifest = MockWorkspaceManifest()
        
        # Initialize sheets with columns based on manifest
        self.sheets: Dict[str, Dict] = {}
        self._init_sheets()
    
    def _init_sheets(self):
        """Initialize all sheets with their columns."""
        for sheet_logical, sheet_info in self.manifest._sheets.items():
            physical_name = sheet_info["name"]
            columns = []
            for col_logical, col_info in self.manifest._columns.get(sheet_logical, {}).items():
                columns.append({"id": col_info["id"], "title": col_info["name"]})
            
            self.sheets[physical_name] = {
                "id": sheet_info["id"],
                "columns": columns,
                "rows": []
            }
        
        # Pre-populate config with sequence counters
        config_sheet = self.sheets.get("00a Config")
        if config_sheet:
            sequences = ["seq_tag", "seq_exception", "seq_allocation", "seq_consumption",
                        "seq_delivery", "seq_nesting", "seq_remnant", "seq_filler", "seq_txn"]
            for seq in sequences:
                self._row_counter += 1
                config_sheet["rows"].append({
                    "id": self._row_counter,
                    "cells": [
                        {"columnId": 4001, "value": seq},
                        {"columnId": 4002, "value": "0"},
                        {"columnId": 4003, "value": datetime.now().strftime("%Y-%m-%d")},
                        {"columnId": 4004, "value": "system"},
                    ]
                })
    
    def _get_sheet(self, sheet_ref) -> Dict:
        """Get sheet by physical name or ID."""
        # Try direct physical name lookup
        if isinstance(sheet_ref, str):
            if sheet_ref in self.sheets:
                return self.sheets[sheet_ref]
            # Try logical name lookup via manifest
            physical = self.manifest.get_sheet_name(sheet_ref)
            if physical and physical in self.sheets:
                return self.sheets[physical]
        # Try ID lookup
        if isinstance(sheet_ref, int):
            for sheet in self.sheets.values():
                if sheet.get("id") == sheet_ref:
                    return sheet
        raise KeyError(f"Sheet not found: {sheet_ref}")
    
    def add_row(self, sheet_ref, row_data: Dict) -> Dict:
        """Add a row to a sheet."""
        sheet = self._get_sheet(sheet_ref)
        self._row_counter += 1
        
        # Build cells from row_data
        cells = []
        col_name_to_id = {c["title"]: c["id"] for c in sheet["columns"]}
        
        for col_name, value in row_data.items():
            col_id = col_name_to_id.get(col_name)
            if col_id and value is not None:
                cells.append({"columnId": col_id, "value": value})
        
        row = {"id": self._row_counter, "cells": cells}
        sheet["rows"].append(row)
        return {"id": self._row_counter}
    
    def find_rows(self, sheet_ref, column_name: str, value: Any) -> List[Dict]:
        """Find rows by column value."""
        sheet = self._get_sheet(sheet_ref)
        
        # Get column ID
        col_name_to_id = {c["title"]: c["id"] for c in sheet["columns"]}
        col_id = col_name_to_id.get(column_name)
        
        if not col_id:
            return []
        
        results = []
        for row in sheet["rows"]:
            for cell in row.get("cells", []):
                if cell.get("columnId") == col_id and cell.get("value") == value:
                    # Convert row to dict with column names
                    row_dict = {"row_id": row["id"]}
                    col_id_to_name = {c["id"]: c["title"] for c in sheet["columns"]}
                    for c in row.get("cells", []):
                        name = col_id_to_name.get(c.get("columnId"))
                        if name:
                            row_dict[name] = c.get("value")
                    results.append(row_dict)
                    break
        return results
    
    def update_row(self, sheet_ref, row_id: int, updates: Dict):
        """Update a row."""
        sheet = self._get_sheet(sheet_ref)
        col_name_to_id = {c["title"]: c["id"] for c in sheet["columns"]}
        
        for row in sheet["rows"]:
            if row["id"] == row_id:
                for col_name, value in updates.items():
                    col_id = col_name_to_id.get(col_name)
                    if col_id:
                        # Update existing cell or add new one
                        found = False
                        for cell in row["cells"]:
                            if cell.get("columnId") == col_id:
                                cell["value"] = value
                                found = True
                                break
                        if not found:
                            row["cells"].append({"columnId": col_id, "value": value})
                return
        raise KeyError(f"Row {row_id} not found")


# ============== Mock Smartsheet Client ==============

class MockSmartsheetClient:
    """Mock Smartsheet client for testing."""
    
    def __init__(self, storage: MockSmartsheetStorage = None):
        self.storage = storage or MockSmartsheetStorage()
        self._manifest = self.storage.manifest
    
    def resolve_sheet_id(self, sheet_ref) -> int:
        """Resolve sheet reference to ID."""
        if isinstance(sheet_ref, int):
            return sheet_ref
        sheet_id = self._manifest.get_sheet_id(sheet_ref)
        if sheet_id:
            return sheet_id
        # Try physical name
        sheet = self.storage._get_sheet(sheet_ref)
        return sheet.get("id", 0)
    
    def find_row(self, sheet_ref, column_ref: str, value: Any) -> Optional[Dict]:
        """Find a single row by column value."""
        # Resolve column name via manifest
        if isinstance(sheet_ref, str) and self._manifest.has_sheet(sheet_ref):
            physical_col = self._manifest.get_column_name(sheet_ref, column_ref)
            physical_sheet = self._manifest.get_sheet_name(sheet_ref)
            if physical_col and physical_sheet:
                rows = self.storage.find_rows(physical_sheet, physical_col, value)
                return rows[0] if rows else None
        
        # Direct lookup
        rows = self.storage.find_rows(sheet_ref, column_ref, value)
        return rows[0] if rows else None
    
    def find_rows(self, sheet_ref, column_ref: str, value: Any) -> List[Dict]:
        """Find all rows by column value."""
        if isinstance(sheet_ref, str) and self._manifest.has_sheet(sheet_ref):
            physical_col = self._manifest.get_column_name(sheet_ref, column_ref)
            physical_sheet = self._manifest.get_sheet_name(sheet_ref)
            if physical_col and physical_sheet:
                return self.storage.find_rows(physical_sheet, physical_col, value)
        return self.storage.find_rows(sheet_ref, column_ref, value)
    
    def find_row_by_column(self, sheet_ref, column_ref: str, value: Any) -> Optional[Dict]:
        """Deprecated alias for find_row."""
        return self.find_row(sheet_ref, column_ref, value)
    
    def add_row(self, sheet_ref, row_data: Dict) -> Dict:
        """Add a row to a sheet."""
        # Resolve logical column names to physical
        if isinstance(sheet_ref, str) and self._manifest.has_sheet(sheet_ref):
            physical_sheet = self._manifest.get_sheet_name(sheet_ref)
            resolved_data = {}
            for key, value in row_data.items():
                physical_col = self._manifest.get_column_name(sheet_ref, key)
                resolved_data[physical_col or key] = value
            return self.storage.add_row(physical_sheet, resolved_data)
        return self.storage.add_row(sheet_ref, row_data)
    
    def update_row(self, sheet_ref, row_id: int, updates: Dict) -> Dict:
        """Update a row."""
        if isinstance(sheet_ref, str) and self._manifest.has_sheet(sheet_ref):
            physical_sheet = self._manifest.get_sheet_name(sheet_ref)
            resolved_updates = {}
            for key, value in updates.items():
                physical_col = self._manifest.get_column_name(sheet_ref, key)
                resolved_updates[physical_col or key] = value
            self.storage.update_row(physical_sheet, row_id, resolved_updates)
            return {"id": row_id}
        self.storage.update_row(sheet_ref, row_id, updates)
        return {"id": row_id}
    
    def get_config_value(self, config_key: str) -> Optional[str]:
        """Get config value by key."""
        row = self.find_row("CONFIG", "CONFIG_KEY", config_key)
        if row:
            return row.get("config_value") or row.get("CONFIG_VALUE")
        return None
    
    def attach_url_to_row(self, sheet_ref, row_id: int, url: str, name: str = None) -> Dict:
        """Mock URL attachment."""
        return {"attachmentType": "LINK", "url": url, "name": name}
    
    def attach_file_to_row(self, sheet_ref, row_id: int, file_content_base64: str, 
                           file_name: str, content_type: str = None) -> Dict:
        """Mock file attachment."""
        return {"attachmentType": "FILE", "name": file_name}


# ============== Test Data Factory ==============

class TestDataFactory:
    """Factory for creating test data."""
    
    @staticmethod
    def create_tag_ingest_request(
        client_request_id: str = None,
        lpo_sap_reference: str = "SAP-TEST-001",
        required_area_m2: float = 50.0,
        requested_delivery_date: str = "2026-02-01",
        uploaded_by: str = "test@company.com",
        file_url: str = None,
        file_content: str = None,
        tag_name: str = None,
        received_through: str = "API",
        user_remarks: str = None,
        **kwargs
    ) -> Dict:
        """Create a tag ingest request."""
        return {
            "client_request_id": client_request_id or str(uuid.uuid4()),
            "lpo_sap_reference": lpo_sap_reference,
            "required_area_m2": required_area_m2,
            "requested_delivery_date": requested_delivery_date,
            "uploaded_by": uploaded_by,
            "file_url": file_url,
            "file_content": file_content,
            "tag_name": tag_name,
            "received_through": received_through,
            "user_remarks": user_remarks,
            **kwargs
        }
    
    @staticmethod
    def create_lpo(
        lpo_id: str = None,
        customer_lpo_ref: str = None,
        sap_reference: str = None,
        customer_name: str = "Test Customer",
        project_name: str = "Test Project",
        status: str = "Active",
        brand: str = "Test Brand",
        wastage: str = "5%",
        po_quantity: float = 500.0,
        delivered_quantity: float = 0.0,
    ) -> Dict:
        """Create an LPO record using physical column names."""
        lpo_id = lpo_id or f"LPO-{uuid.uuid4().hex[:8].upper()}"
        return {
            "LPO ID": lpo_id,
            "Customer LPO Ref": customer_lpo_ref or f"CUST-{uuid.uuid4().hex[:6].upper()}",
            "SAP Reference": sap_reference or f"SAP-{uuid.uuid4().hex[:8].upper()}",
            "Customer Name": customer_name,
            "Project Name": project_name,
            "LPO Status": status,
            "Brand": brand,
            "Wastage Considered in Costing": wastage,
            "PO Quantity (Sqm)": po_quantity,
            "Delivered Quantity (Sqm)": delivered_quantity,
        }
    
    @staticmethod
    def create_tag_record(
        tag_id: str = None,
        tag_name: str = "Test Tag",
        status: str = "Validate",
        client_request_id: str = None,
        file_hash: str = None,
    ) -> Dict:
        """Create a tag record using physical column names."""
        return {
            "Tag ID": tag_id or f"TAG-{uuid.uuid4().hex[:8].upper()}",
            "Tag Sheet Name/ Rev": tag_name,
            "Status": status,
            "Client Request ID": client_request_id or str(uuid.uuid4()),
            "File Hash": file_hash,
        }
    
    @staticmethod
    def create_base64_file_content(content: str = "test file content") -> str:
        """Create base64 encoded file content."""
        return base64.b64encode(content.encode()).decode()


# ============== Mock HTTP Request ==============

class MockHttpRequest:
    """Mock Azure Functions HttpRequest."""
    
    def __init__(self, body: Dict = None):
        self._body = json.dumps(body or {}).encode()
    
    def get_json(self) -> Dict:
        return json.loads(self._body)
    
    def get_body(self) -> bytes:
        return self._body


# ============== Fixtures ==============

@pytest.fixture
def mock_manifest():
    """Get mock manifest instance."""
    return MockWorkspaceManifest()

@pytest.fixture
def mock_storage():
    """Get fresh mock storage for each test."""
    return MockSmartsheetStorage()

@pytest.fixture
def mock_client(mock_storage):
    """Get mock Smartsheet client."""
    return MockSmartsheetClient(mock_storage)

@pytest.fixture
def factory():
    """Get test data factory."""
    return TestDataFactory()

@pytest.fixture
def mock_http_request():
    """Factory for creating mock HTTP requests."""
    def _create(body: Dict) -> MockHttpRequest:
        return MockHttpRequest(body)
    return _create

@pytest.fixture
def setup_test_environment():
    """Set up environment variables for testing."""
    original_env = os.environ.copy()
    os.environ["SMARTSHEET_API_KEY"] = "test-api-key"
    os.environ["SMARTSHEET_WORKSPACE_ID"] = "12345"
    os.environ["SMARTSHEET_BASE_URL"] = "https://api.smartsheet.eu/2.0"
    yield
    os.environ.clear()
    os.environ.update(original_env)

@pytest.fixture
def patched_manifest(mock_manifest):
    """Patch manifest getter to return mock."""
    with patch('shared.manifest.get_manifest', return_value=mock_manifest):
        with patch('shared.manifest._manifest', mock_manifest):
            yield mock_manifest

@pytest.fixture
def patched_client(mock_client, patched_manifest):
    """Patch client getter to return mock."""
    with patch('shared.smartsheet_client.get_smartsheet_client', return_value=mock_client):
        with patch('shared.smartsheet_client._client', mock_client):
            yield mock_client
