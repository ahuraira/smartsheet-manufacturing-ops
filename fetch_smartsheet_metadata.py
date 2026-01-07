"""
Smartsheet Workspace Metadata Fetcher
Pulls all sheets and their column structures from the workspace,
including nested folders, for gap analysis against SOTA specification.
"""

import os
import requests
import json
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configuration - Smartsheet API (from environment)
API_KEY = os.getenv("SMARTSHEET_API_KEY")
BASE_URL = os.getenv("SMARTSHEET_BASE_URL", "https://api.smartsheet.eu/2.0")
WORKSPACE_ID = os.getenv("SMARTSHEET_WORKSPACE_ID")

if not API_KEY:
    raise ValueError("SMARTSHEET_API_KEY environment variable is required. Copy .env.example to .env and set your API key.")

if not WORKSPACE_ID:
    raise ValueError("SMARTSHEET_WORKSPACE_ID environment variable is required. Copy .env.example to .env and set your workspace ID.")

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

def get_workspace():
    """Fetch workspace details including all sheets and folders."""
    url = f"{BASE_URL}/workspaces/{WORKSPACE_ID}?include=sheets,folders"
    response = requests.get(url, headers=HEADERS)
    response.raise_for_status()
    return response.json()

def get_folder(folder_id):
    """Fetch folder details including nested sheets and subfolders."""
    url = f"{BASE_URL}/folders/{folder_id}?include=sheets,folders"
    response = requests.get(url, headers=HEADERS)
    response.raise_for_status()
    return response.json()

def get_sheet_columns(sheet_id):
    """Fetch detailed column information for a sheet."""
    url = f"{BASE_URL}/sheets/{sheet_id}?include=columns"
    response = requests.get(url, headers=HEADERS)
    response.raise_for_status()
    return response.json()

def process_sheet(sheet, path=""):
    """Process a single sheet and extract its metadata."""
    sheet_id = sheet.get("id")
    sheet_name = sheet.get("name")
    full_path = f"{path}/{sheet_name}" if path else sheet_name
    print(f"  - Processing: {full_path}")
    
    try:
        sheet_detail = get_sheet_columns(sheet_id)
        columns = sheet_detail.get("columns", [])
        
        return {
            "sheet_id": sheet_id,
            "sheet_name": sheet_name,
            "path": full_path,
            "permalink": sheet.get("permalink"),
            "row_count": sheet_detail.get("totalRowCount", 0),
            "columns": [
                {
                    "id": col.get("id"),
                    "title": col.get("title"),
                    "type": col.get("type"),
                    "primary": col.get("primary", False),
                    "index": col.get("index"),
                    "options": col.get("options"),
                    "systemColumnType": col.get("systemColumnType")
                }
                for col in columns
            ]
        }
    except Exception as e:
        print(f"    ERROR: {e}")
        return {
            "sheet_id": sheet_id,
            "sheet_name": sheet_name,
            "path": full_path,
            "error": str(e)
        }

def process_folder(folder, path="", all_sheets=None):
    """Recursively process a folder and all its contents."""
    if all_sheets is None:
        all_sheets = []
    
    folder_id = folder.get("id")
    folder_name = folder.get("name")
    current_path = f"{path}/{folder_name}" if path else folder_name
    print(f"\n📁 Folder: {current_path}")
    
    # Get full folder details (with sheets and subfolders)
    try:
        folder_detail = get_folder(folder_id)
    except Exception as e:
        print(f"  ERROR accessing folder: {e}")
        return all_sheets
    
    # Process sheets in this folder
    for sheet in folder_detail.get("sheets", []):
        sheet_info = process_sheet(sheet, current_path)
        all_sheets.append(sheet_info)
    
    # Recursively process subfolders
    for subfolder in folder_detail.get("folders", []):
        process_folder(subfolder, current_path, all_sheets)
    
    return all_sheets

def main():
    print("=" * 60)
    print("Smartsheet Workspace Metadata Fetcher")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print("=" * 60)
    
    # Fetch workspace
    print("\n[1/3] Fetching workspace details...")
    workspace = get_workspace()
    
    workspace_info = {
        "fetch_timestamp": datetime.now().isoformat(),
        "workspace_id": int(WORKSPACE_ID),
        "workspace_name": workspace.get("name"),
        "sheets": []
    }
    
    print(f"Workspace: {workspace.get('name')}")
    
    # Process root-level sheets
    root_sheets = workspace.get("sheets", [])
    print(f"\n[2/3] Processing {len(root_sheets)} root-level sheets...")
    for sheet in root_sheets:
        sheet_info = process_sheet(sheet)
        workspace_info["sheets"].append(sheet_info)
    
    # Process folders recursively
    folders = workspace.get("folders", [])
    print(f"\n[3/3] Processing {len(folders)} folders recursively...")
    for folder in folders:
        folder_sheets = process_folder(folder)
        workspace_info["sheets"].extend(folder_sheets)
    
    # Save to file
    print("\n" + "-" * 60)
    print("Saving metadata to file...")
    output_file = "smartsheet_workspace_metadata.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(workspace_info, f, indent=2, ensure_ascii=False)
    
    print(f"✓ Metadata saved to: {output_file}")
    
    # Print summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Workspace: {workspace_info['workspace_name']}")
    print(f"Total Sheets: {len(workspace_info['sheets'])}")
    print("\nSheets found:")
    for sheet in workspace_info["sheets"]:
        col_count = len(sheet.get("columns", []))
        row_count = sheet.get("row_count", "?")
        path = sheet.get("path", sheet.get("sheet_name"))
        print(f"  • {path} ({col_count} columns, {row_count} rows)")

if __name__ == "__main__":
    main()
