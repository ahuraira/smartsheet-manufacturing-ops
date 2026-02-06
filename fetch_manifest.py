"""
Fetch Workspace Manifest
========================

Fetches sheet and column IDs from an existing Smartsheet workspace
and generates a workspace_manifest.json file for the application to use.

This is the "one-click setup" for connecting to an existing workspace.

Usage
-----
1. Set environment variables:
   - SMARTSHEET_API_KEY
   - SMARTSHEET_WORKSPACE_ID

2. Run:
   python fetch_manifest.py

3. The script will generate: functions/workspace_manifest.json

Name Mapping
------------
The script tries to match physical sheet names to logical names using:
1. Exact match (after normalization)
2. Contains match (for sheets with prefixes like "02 Tag Sheet Registry")

If a sheet cannot be mapped, it will be skipped with a warning.
You can manually add such sheets to the manifest.
"""

import os
import sys
import json
import re
import requests
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Add shared module to path
sys.path.insert(0, str(Path(__file__).parent / "functions"))

# Load environment variables
load_dotenv()

# Configuration
API_KEY = os.getenv("SMARTSHEET_API_KEY")
BASE_URL = os.getenv("SMARTSHEET_BASE_URL", "https://api.smartsheet.eu/2.0")
WORKSPACE_ID = os.getenv("SMARTSHEET_WORKSPACE_ID")

if not API_KEY:
    raise ValueError("SMARTSHEET_API_KEY environment variable is required")
if not WORKSPACE_ID:
    raise ValueError("SMARTSHEET_WORKSPACE_ID environment variable is required")

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

# Output paths - save to both functions and function_adapter
OUTPUT_PATH = Path(__file__).parent / "functions" / "workspace_manifest.json"
OUTPUT_PATH_ADAPTER = Path(__file__).parent / "function_adapter" / "workspace_manifest.json"


# ============== Physical to Logical Name Mapping ==============

# Map physical sheet names to logical names
SHEET_NAME_MAP = {
    # Root level
    "00 Reference Data": "REFERENCE_DATA",
    "00a Config": "CONFIG",
    "00b Machine Master": "MACHINE_MASTER",
    
    # 01. Commercial and Demand
    "01 LPO Master LOG": "LPO_MASTER",
    "01 LPO Audit LOG": "LPO_AUDIT",
    "01h LPO Ingestion": "01H_LPO_INGESTION",  # Staging sheet
    
    # 02. Tag Sheet Registry
    "Tag Sheet Registry": "TAG_REGISTRY",
    "Tag Sheet Registery": "TAG_REGISTRY",  # Handle typo in existing sheet
    "02 Tag Sheet Registry": "TAG_REGISTRY",
    "02h Tag Sheet Staging": "02H_TAG_SHEET_STAGING",  # Staging sheet
    
    # 03. Production Planning
    "03 Production Planning": "PRODUCTION_PLANNING",
    "03h Production Planning Staging": "03H_PRODUCTION_PLANNING_STAGING",  # Staging sheet
    "04 Nesting Execution Log": "NESTING_LOG",
    "05 Allocation Log": "ALLOCATION_LOG",
    
    # 04. Production and Delivery
    "06 Consumption Log": "CONSUMPTION_LOG",
    "06a Remnant Log": "REMNANT_LOG",
    "06b Filler Log": "FILLER_LOG",
    "07 Delivery Log": "DELIVERY_LOG",
    "08 Invoice Log": "INVOICE_LOG",
    "90 Inventory Txn Log": "INVENTORY_TXN_LOG",
    "91 Inventory Snapshot": "INVENTORY_SNAPSHOT",
    "92 SAP Inventory Snapshot": "SAP_INVENTORY_SNAPSHOT",
    "93 Physical Inventory Snapshot": "PHYSICAL_INVENTORY_SNAPSHOT",
    "97 Override Log": "OVERRIDE_LOG",
    "98 User Action Log": "USER_ACTION_LOG",
    "99 Exception Log": "EXCEPTION_LOG",
    
    # 05. Material Mapping
    "05a Material Master": "MATERIAL_MASTER",
    "05b Mapping Override": "MAPPING_OVERRIDE",
    "05c LPO Material Brand Map": "LPO_MATERIAL_BRAND_MAP",
    "05d Mapping History": "MAPPING_HISTORY",
    "05e Mapping Exception": "MAPPING_EXCEPTION",
    "06a Parsed BOM": "PARSED_BOM",
}

# Map physical folder names to logical names
FOLDER_NAME_MAP = {
    "01. Commercial and Demand": "01_COMMERCIAL_AND_DEMAND",
    "02. Tag Sheet Registry": "02_TAG_SHEET_REGISTRY",
    "03. Production Planning": "03_PRODUCTION_PLANNING",
    "04. Production and Delivery": "04_PRODUCTION_AND_DELIVERY",
    "05. Material Mapping": "05_MATERIAL_MAPPING",
}

# Map physical column names to logical names (per sheet)
# Format: { "SHEET_LOGICAL_NAME": { "Physical Name": "LOGICAL_NAME" } }
COLUMN_NAME_MAP = {
    "CONFIG": {
        "config_key": "CONFIG_KEY",
        "config_value": "CONFIG_VALUE",
        "effective_from": "EFFECTIVE_FROM",
        "changed_by": "CHANGED_BY",
    },
    "LPO_MASTER": {
        "LPO ID": "LPO_ID",
        "Customer LPO Ref": "CUSTOMER_LPO_REF",
        "SAP Reference": "SAP_REFERENCE",
        "Customer Name": "CUSTOMER_NAME",
        "Project Name": "PROJECT_NAME",
        "LPO Status": "LPO_STATUS",
        "Brand": "BRAND",
        "PO Quantity (Sqm)": "PO_QUANTITY_SQM",
        "Delivered Quantity (Sqm)": "DELIVERED_QUANTITY_SQM",
        "Remarks": "REMARKS",
    },
    "TAG_REGISTRY": {
        "Tag ID": "TAG_ID",
        "Tag Sheet Name/ Rev": "TAG_NAME",
        "Required Delivery Date": "REQUIRED_DELIVERY_DATE",
        "LPO SAP Reference Link": "LPO_SAP_REFERENCE",
        "LPO Status": "LPO_STATUS",
        "Production Gate": "PRODUCTION_GATE",
        "Brand": "BRAND",
        "Customer Name": "CUSTOMER_NAME",
        "Estimated Quantity": "ESTIMATED_QUANTITY",
        "Status": "STATUS",
        "Submitted By": "SUBMITTED_BY",
        "File Hash": "FILE_HASH",
        "Client Request ID": "CLIENT_REQUEST_ID",
        "Remarks": "REMARKS",
    },
    "EXCEPTION_LOG": {
        "Exception ID": "EXCEPTION_ID",
        "Client Request ID": "CLIENT_REQUEST_ID",  # For deduplication (v1.6.5)
        "Created At": "CREATED_AT",
        "Source": "SOURCE",
        "Related Tag ID": "RELATED_TAG_ID",
        "Related Txn ID": "RELATED_TXN_ID",
        "Material Code": "MATERIAL_CODE",
        "Quantity": "QUANTITY",
        "Reason Code": "REASON_CODE",
        "Severity": "SEVERITY",
        "Status": "STATUS",
        "SLA Due": "SLA_DUE",
        "Resolution Action": "RESOLUTION_ACTION",
        "Assigned To": "ASSIGNED_TO",
        "Attachment Links": "ATTACHMENT_LINKS",
    },
    "USER_ACTION_LOG": {
        "Action ID": "ACTION_ID",
        "Timestamp": "TIMESTAMP",
        "User ID": "USER_ID",
        "Action Type": "ACTION_TYPE",
        "Target Table": "TARGET_TABLE",
        "Target ID": "TARGET_ID",
        "Old Value": "OLD_VALUE",
        "New Value": "NEW_VALUE",
        "Notes": "NOTES",
    },
    "MATERIAL_MASTER": {
        "Mapping ID": "MAPPING_ID",
        "Nesting Description": "NESTING_DESCRIPTION",
        "Canonical Code": "CANONICAL_CODE",
        "Default SAP Code": "DEFAULT_SAP_CODE",
        "UOM": "UOM",
        "SAP UOM": "SAP_UOM",
        "Conversion Factor": "CONVERSION_FACTOR",
        "Not Tracked": "NOT_TRACKED",
        "Active": "ACTIVE",
        "Notes": "NOTES",
        "Updated At": "UPDATED_AT",
        "Updated By": "UPDATED_BY",
    },
    "MAPPING_OVERRIDE": {
        "Override ID": "OVERRIDE_ID",
        "Scope Type": "SCOPE_TYPE",
        "Scope Value": "SCOPE_VALUE",
        "Nesting Description": "NESTING_DESCRIPTION",
        "Canonical Code": "CANONICAL_CODE",
        "SAP Code": "SAP_CODE",
        "Active": "ACTIVE",
        "Effective From": "EFFECTIVE_FROM",
        "Effective To": "EFFECTIVE_TO",
        "Created By": "CREATED_BY",
        "Created At": "CREATED_AT",
    },
    "LPO_MATERIAL_BRAND_MAP": {
        "Map ID": "MAP_ID",
        "LPO ID": "LPO_ID",
        "Canonical Code": "CANONICAL_CODE",
        "SAP Code": "SAP_CODE",
        "Priority": "PRIORITY",
        "Active": "ACTIVE",
        "Notes": "NOTES",
    },
    "MAPPING_HISTORY": {
        "History ID": "HISTORY_ID",
        "Ingest Line ID": "INGEST_LINE_ID",
        "Nesting Description": "NESTING_DESCRIPTION",
        "Canonical Code": "CANONICAL_CODE",
        "SAP Code": "SAP_CODE",
        "Decision": "DECISION",
        "User ID": "USER_ID",
        "Trace ID": "TRACE_ID",
        "Created At": "CREATED_AT",
        "Notes": "NOTES",
    },
    "MAPPING_EXCEPTION": {
        "Exception ID": "EXCEPTION_ID",
        "Ingest Line ID": "INGEST_LINE_ID",
        "Nesting Description": "NESTING_DESCRIPTION",
        "Status": "STATUS",
        "Assigned To": "ASSIGNED_TO",
        "Created At": "CREATED_AT",
        "Trace ID": "TRACE_ID",
        "Resolution Notes": "RESOLUTION_NOTES",
    },
    "PARSED_BOM": {
        "BOM Line ID": "BOM_LINE_ID",
        "Nest Session ID": "NEST_SESSION_ID",
        "Line Number": "LINE_NUMBER",
        "Material Type": "MATERIAL_TYPE",
        "Nesting Description": "NESTING_DESCRIPTION",
        "Canonical Code": "CANONICAL_CODE",
        "SAP Code": "SAP_CODE",
        "Quantity": "QUANTITY",
        "UOM": "UOM",
        "Canonical Quantity": "CANONICAL_QUANTITY",
        "Canonical UOM": "CANONICAL_UOM",
        "Mapping Decision": "MAPPING_DECISION",
        "History ID": "HISTORY_ID",
        "Created At": "CREATED_AT",
        "Trace ID": "TRACE_ID",
    },
}


def get_workspace():
    """Fetch workspace details."""
    url = f"{BASE_URL}/workspaces/{WORKSPACE_ID}?include=sheets,folders"
    response = requests.get(url, headers=HEADERS)
    response.raise_for_status()
    return response.json()


def get_folder(folder_id):
    """Fetch folder details."""
    url = f"{BASE_URL}/folders/{folder_id}?include=sheets,folders"
    response = requests.get(url, headers=HEADERS)
    response.raise_for_status()
    return response.json()


def get_sheet_columns(sheet_id):
    """Fetch sheet with columns."""
    url = f"{BASE_URL}/sheets/{sheet_id}?include=columns"
    response = requests.get(url, headers=HEADERS)
    response.raise_for_status()
    return response.json()


def find_logical_sheet_name(physical_name: str) -> str:
    """Find logical name for a physical sheet name."""
    # Direct match
    if physical_name in SHEET_NAME_MAP:
        return SHEET_NAME_MAP[physical_name]
    
    # Normalized match (remove prefix numbers)
    normalized = re.sub(r'^\d+[a-z]?\s*', '', physical_name)
    if normalized in SHEET_NAME_MAP:
        return SHEET_NAME_MAP[normalized]
    
    # Fallback: convert to UPPER_SNAKE_CASE
    fallback = re.sub(r'[^a-zA-Z0-9]+', '_', physical_name).upper().strip('_')
    print(f"  ⚠ No mapping for sheet '{physical_name}', using '{fallback}'")
    return fallback


def find_logical_folder_name(physical_name: str) -> str:
    """Find logical name for a physical folder name."""
    if physical_name in FOLDER_NAME_MAP:
        return FOLDER_NAME_MAP[physical_name]
    
    # Fallback
    fallback = re.sub(r'[^a-zA-Z0-9]+', '_', physical_name).upper().strip('_')
    print(f"  ⚠ No mapping for folder '{physical_name}', using '{fallback}'")
    return fallback


def find_logical_column_name(sheet_logical_name: str, physical_name: str) -> str:
    """Find logical name for a physical column name."""
    sheet_columns = COLUMN_NAME_MAP.get(sheet_logical_name, {})
    
    if physical_name in sheet_columns:
        return sheet_columns[physical_name]
    
    # Fallback: convert to UPPER_SNAKE_CASE
    fallback = re.sub(r'[^a-zA-Z0-9]+', '_', physical_name).upper().strip('_')
    return fallback


def process_sheet(sheet_info, manifest, folder_logical_name=None):
    """Process a sheet and add to manifest."""
    sheet_id = sheet_info["id"]
    sheet_name = sheet_info["name"]
    
    print(f"  📄 {sheet_name}")
    
    logical_name = find_logical_sheet_name(sheet_name)
    
    # Get column details
    try:
        sheet_detail = get_sheet_columns(sheet_id)
        columns = sheet_detail.get("columns", [])
    except Exception as e:
        print(f"    ❌ Error fetching columns: {e}")
        columns = []
    
    # Build columns dict
    columns_dict = {}
    for col in columns:
        col_logical = find_logical_column_name(logical_name, col["title"])
        col_info = {
            "id": col["id"],
            "name": col["title"],
            "type": col.get("type", "TEXT_NUMBER"),
            "primary": col.get("primary", False),
            "index": col.get("index", 0),
        }
        
        # Include picklist options if available
        if col.get("type") == "PICKLIST" and col.get("options"):
            col_info["options"] = col["options"]
        
        columns_dict[col_logical] = col_info
    
    # Add to manifest
    manifest["sheets"][logical_name] = {
        "id": sheet_id,
        "name": sheet_name,
        "folder": folder_logical_name,
        "columns": columns_dict
    }


def process_folder(folder_info, manifest, parent_path=""):
    """Recursively process a folder."""
    folder_id = folder_info["id"]
    folder_name = folder_info["name"]
    folder_logical = find_logical_folder_name(folder_name)
    
    current_path = f"{parent_path}/{folder_name}" if parent_path else folder_name
    print(f"\n📁 {current_path}")
    
    # Add folder to manifest
    manifest["folders"][folder_logical] = {
        "id": folder_id,
        "name": folder_name
    }
    
    # Get full folder details
    try:
        folder_detail = get_folder(folder_id)
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return
    
    # Process sheets in folder
    for sheet in folder_detail.get("sheets", []):
        process_sheet(sheet, manifest, folder_logical)
    
    # Recursively process subfolders
    for subfolder in folder_detail.get("folders", []):
        process_folder(subfolder, manifest, current_path)


def main():
    print("=" * 60)
    print("Workspace Manifest Generator")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print("=" * 60)
    
    # Initialize manifest
    manifest = {
        "_meta": {
            "description": "Smartsheet Workspace Manifest - Maps logical names to immutable IDs",
            "version": "1.0.0",
            "generated_at": datetime.now().isoformat(),
            "generated_by": "fetch_manifest.py",
            "workspace_id": int(WORKSPACE_ID),
            "important": "IDs are immutable. Names are for reference only."
        },
        "workspace": {},
        "folders": {},
        "sheets": {}
    }
    
    # Fetch workspace
    print("\n[1/3] Fetching workspace...")
    workspace = get_workspace()
    
    manifest["workspace"] = {
        "id": int(WORKSPACE_ID),
        "name": workspace.get("name")
    }
    
    print(f"Workspace: {workspace.get('name')}")
    
    # Process root-level sheets
    print("\n[2/3] Processing root-level sheets...")
    for sheet in workspace.get("sheets", []):
        process_sheet(sheet, manifest)
    
    # Process folders
    print("\n[3/3] Processing folders...")
    for folder in workspace.get("folders", []):
        process_folder(folder, manifest)
    
    # Save manifest to both locations
    print("\n" + "-" * 60)
    print("Saving manifest...")
    
    # Save to functions/
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print(f"✓ Manifest saved to: {OUTPUT_PATH}")
    
    # Save to function_adapter/
    OUTPUT_PATH_ADAPTER.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH_ADAPTER, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print(f"✓ Manifest saved to: {OUTPUT_PATH_ADAPTER}")
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Workspace: {manifest['workspace']['name']}")
    print(f"Workspace ID: {manifest['workspace']['id']}")
    print(f"Folders: {len(manifest['folders'])}")
    print(f"Sheets: {len(manifest['sheets'])}")
    
    print("\nSheets in manifest:")
    for logical_name, sheet_info in manifest["sheets"].items():
        col_count = len(sheet_info.get("columns", {}))
        print(f"  • {logical_name}: {sheet_info['name']} ({col_count} columns)")
    
    print(f"\n✅ Manifest ready! Update your .env or local.settings.json with:")
    print(f"   SMARTSHEET_WORKSPACE_ID={manifest['workspace']['id']}")


if __name__ == "__main__":
    main()
