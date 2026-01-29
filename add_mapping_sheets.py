"""
Add Material Mapping Sheets to Existing Workspace
==================================================

This script adds the 6 new material mapping sheets to an existing Smartsheet workspace.
Run this when you already have a workspace but need to add the mapping sheets.

Usage:
    1. Set SMARTSHEET_API_KEY and SMARTSHEET_WORKSPACE_ID in .env
    2. Run: python add_mapping_sheets.py
    
The script will:
    1. Create the "05. Material Mapping" folder if it doesn't exist
    2. Create the 6 material mapping sheets inside the folder
    3. Print the IDs for updating the manifest
"""

import os
import requests
import json
import time
from datetime import datetime
from dotenv import load_dotenv

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

# Material Mapping Sheet Definitions (from canonical_material_mapping_specification.md)
# All columns are TEXT_NUMBER to avoid type mismatch errors - data consistency is maintained by scripts
# No AUTO_NUMBER to avoid ID conflicts when writing via API
MAPPING_SHEETS = {
    "05a Material Master": {
        "columns": [
            {"title": "Mapping ID", "type": "TEXT_NUMBER"},
            {"title": "Nesting Description", "type": "TEXT_NUMBER", "primary": True},
            {"title": "Canonical Code", "type": "TEXT_NUMBER"},
            {"title": "Default SAP Code", "type": "TEXT_NUMBER"},
            {"title": "UOM", "type": "TEXT_NUMBER"},
            {"title": "Not Tracked", "type": "TEXT_NUMBER"},
            {"title": "Active", "type": "TEXT_NUMBER"},
            {"title": "Notes", "type": "TEXT_NUMBER"},
            {"title": "Updated At", "type": "TEXT_NUMBER"},
            {"title": "Updated By", "type": "TEXT_NUMBER"}
        ]
    },
    "05b Mapping Override": {
        "columns": [
            {"title": "Override ID", "type": "TEXT_NUMBER"},
            {"title": "Scope Type", "type": "TEXT_NUMBER", "primary": True},
            {"title": "Scope Value", "type": "TEXT_NUMBER"},
            {"title": "Nesting Description", "type": "TEXT_NUMBER"},
            {"title": "Canonical Code", "type": "TEXT_NUMBER"},
            {"title": "SAP Code", "type": "TEXT_NUMBER"},
            {"title": "Active", "type": "TEXT_NUMBER"},
            {"title": "Effective From", "type": "TEXT_NUMBER"},
            {"title": "Effective To", "type": "TEXT_NUMBER"},
            {"title": "Created By", "type": "TEXT_NUMBER"},
            {"title": "Created At", "type": "TEXT_NUMBER"}
        ]
    },
    "05c LPO Material Brand Map": {
        "columns": [
            {"title": "Map ID", "type": "TEXT_NUMBER"},
            {"title": "LPO ID", "type": "TEXT_NUMBER", "primary": True},
            {"title": "Canonical Code", "type": "TEXT_NUMBER"},
            {"title": "SAP Code", "type": "TEXT_NUMBER"},
            {"title": "Priority", "type": "TEXT_NUMBER"},
            {"title": "Active", "type": "TEXT_NUMBER"},
            {"title": "Notes", "type": "TEXT_NUMBER"}
        ]
    },
    "05d Mapping History": {
        "columns": [
            {"title": "History ID", "type": "TEXT_NUMBER"},
            {"title": "Ingest Line ID", "type": "TEXT_NUMBER", "primary": True},
            {"title": "Nesting Description", "type": "TEXT_NUMBER"},
            {"title": "Canonical Code", "type": "TEXT_NUMBER"},
            {"title": "SAP Code", "type": "TEXT_NUMBER"},
            {"title": "Decision", "type": "TEXT_NUMBER"},
            {"title": "User ID", "type": "TEXT_NUMBER"},
            {"title": "Trace ID", "type": "TEXT_NUMBER"},
            {"title": "Created At", "type": "TEXT_NUMBER"},
            {"title": "Notes", "type": "TEXT_NUMBER"}
        ]
    },
    "05e Mapping Exception": {
        "columns": [
            {"title": "Exception ID", "type": "TEXT_NUMBER"},
            {"title": "Ingest Line ID", "type": "TEXT_NUMBER", "primary": True},
            {"title": "Nesting Description", "type": "TEXT_NUMBER"},
            {"title": "Status", "type": "TEXT_NUMBER"},
            {"title": "Assigned To", "type": "TEXT_NUMBER"},
            {"title": "Created At", "type": "TEXT_NUMBER"},
            {"title": "Trace ID", "type": "TEXT_NUMBER"},
            {"title": "Resolution Notes", "type": "TEXT_NUMBER"}
        ]
    },
    "06a Parsed BOM": {
        "columns": [
            {"title": "BOM Line ID", "type": "TEXT_NUMBER"},
            {"title": "Nest Session ID", "type": "TEXT_NUMBER", "primary": True},
            {"title": "Line Number", "type": "TEXT_NUMBER"},
            {"title": "Material Type", "type": "TEXT_NUMBER"},
            {"title": "Nesting Description", "type": "TEXT_NUMBER"},
            {"title": "Canonical Code", "type": "TEXT_NUMBER"},
            {"title": "SAP Code", "type": "TEXT_NUMBER"},
            {"title": "Quantity", "type": "TEXT_NUMBER"},
            {"title": "UOM", "type": "TEXT_NUMBER"},
            {"title": "Canonical Quantity", "type": "TEXT_NUMBER"},
            {"title": "Canonical UOM", "type": "TEXT_NUMBER"},
            {"title": "Mapping Decision", "type": "TEXT_NUMBER"},
            {"title": "History ID", "type": "TEXT_NUMBER"},
            {"title": "Created At", "type": "TEXT_NUMBER"},
            {"title": "Trace ID", "type": "TEXT_NUMBER"}
        ]
    }
}


def get_workspace():
    """Fetch workspace and check for existing folder."""
    url = f"{BASE_URL}/workspaces/{WORKSPACE_ID}?include=folders"
    response = requests.get(url, headers=HEADERS)
    response.raise_for_status()
    return response.json()


def create_folder(folder_name):
    """Create a folder in the workspace."""
    url = f"{BASE_URL}/workspaces/{WORKSPACE_ID}/folders"
    payload = {"name": folder_name}
    response = requests.post(url, headers=HEADERS, json=payload)
    response.raise_for_status()
    result = response.json()
    print(f"✓ Created folder: {folder_name} (ID: {result['result']['id']})")
    return result['result']['id']


def create_sheet(folder_id, sheet_name, columns):
    """Create a sheet in a folder."""
    url = f"{BASE_URL}/folders/{folder_id}/sheets"
    
    # Prepare columns for API
    api_columns = []
    for col in columns:
        column = {
            "title": col["title"],
            "type": col["type"]
        }
        if col.get("primary"):
            column["primary"] = True
        if col.get("options"):
            column["options"] = col["options"]
        if col.get("systemColumnType"):
            column["systemColumnType"] = col["systemColumnType"]
        api_columns.append(column)
    
    payload = {
        "name": sheet_name,
        "columns": api_columns
    }
    
    response = requests.post(url, headers=HEADERS, json=payload)
    response.raise_for_status()
    result = response.json()
    print(f"  📄 Created sheet: {sheet_name} (ID: {result['result']['id']})")
    return result['result']['id']


def main():
    print("=" * 60)
    print("Add Material Mapping Sheets to Existing Workspace")
    print(f"Workspace ID: {WORKSPACE_ID}")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print("=" * 60)
    
    # Step 1: Check if folder exists
    print("\n[1/3] Checking workspace for existing folder...")
    workspace = get_workspace()
    
    folder_id = None
    for folder in workspace.get("folders", []):
        if folder["name"] == "05. Material Mapping":
            folder_id = folder["id"]
            print(f"  ℹ️ Folder already exists (ID: {folder_id})")
            break
    
    # Step 2: Create folder if needed
    if not folder_id:
        print("\n[2/3] Creating Material Mapping folder...")
        folder_id = create_folder("05. Material Mapping")
        time.sleep(0.3)
    else:
        print("\n[2/3] Using existing folder...")
    
    # Step 3: Create sheets
    print("\n[3/3] Creating sheets...")
    created_sheets = []
    
    for sheet_name, definition in MAPPING_SHEETS.items():
        try:
            sheet_id = create_sheet(folder_id, sheet_name, definition["columns"])
            created_sheets.append({"name": sheet_name, "id": sheet_id})
            time.sleep(0.3)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 409:  # Already exists
                print(f"  ⚠️ Sheet already exists: {sheet_name}")
            else:
                print(f"  ❌ Error creating {sheet_name}: {e}")
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Folder ID: {folder_id}")
    print(f"Sheets created: {len(created_sheets)}")
    
    if created_sheets:
        print("\nCreated sheets:")
        for sheet in created_sheets:
            print(f"  • {sheet['name']}: {sheet['id']}")
    
    print("\n✅ NEXT STEPS:")
    print("   1. Run: python fetch_manifest.py")
    print("   2. The manifest will be updated with the new sheet IDs")
    print("   3. Your application is ready to use the mapping sheets!")


if __name__ == "__main__":
    main()
