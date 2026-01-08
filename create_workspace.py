"""
Smartsheet Workspace Creator
Creates a new workspace with all sheets from the current dev environment.
Uses metadata JSON to replicate the complete structure.
"""

import os
import requests
import json
import time
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configuration - Smartsheet API (from environment)
API_KEY = os.getenv("SMARTSHEET_API_KEY")
BASE_URL = os.getenv("SMARTSHEET_BASE_URL", "https://api.smartsheet.eu/2.0")

if not API_KEY:
    raise ValueError("SMARTSHEET_API_KEY environment variable is required. Copy .env.example to .env and set your API key.")

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

# Folder structure to create
FOLDER_STRUCTURE = [
    "01. Commercial and Demand",
    "02. Tag Sheet Registry",
    "03. Production Planning",
    "04. Production and Delivery"
]

# Sheet definitions based on current metadata
SHEET_DEFINITIONS = {
    # Root level sheets
    "00 Reference Data": {
        "folder": None,
        "columns": [
            {"title": "Customer Name", "type": "TEXT_NUMBER", "primary": True},
            {"title": "Terms of Payment ID", "type": "TEXT_NUMBER"},
            {"title": "Terms of Payment", "type": "TEXT_NUMBER"},
            {"title": "Currency Code", "type": "TEXT_NUMBER"}
        ]
    },
    "00a Config": {
        "folder": None,
        "columns": [
            {"title": "config_key", "type": "TEXT_NUMBER", "primary": True},
            {"title": "config_value", "type": "TEXT_NUMBER"},
            {"title": "effective_from", "type": "TEXT_NUMBER"},
            {"title": "changed_by", "type": "TEXT_NUMBER"}
        ]
    },
    
    # 01. Commercial and Demand
    "01 LPO Master LOG": {
        "folder": "01. Commercial and Demand",
        "columns": [
            {"title": "LPO ID", "type": "TEXT_NUMBER", "systemColumnType": "AUTO_NUMBER"},
            {"title": "Customer LPO Ref", "type": "TEXT_NUMBER", "primary": True},
            {"title": "SAP Reference", "type": "TEXT_NUMBER"},
            {"title": "Customer Name", "type": "TEXT_NUMBER"},
            {"title": "Project Name", "type": "TEXT_NUMBER"},
            {"title": "LPO Status", "type": "PICKLIST", "options": ["Draft", "Pending Approval", "Active", "On Hold", "Closed"]},
            {"title": "Brand", "type": "PICKLIST", "options": ["KIMMCO", "WTI"]},
            {"title": "Wastage Considered in Costing", "type": "TEXT_NUMBER"},
            {"title": "Price (AED per Sqm)", "type": "TEXT_NUMBER"},
            {"title": "PO Quantity (Sqm)", "type": "TEXT_NUMBER"},
            {"title": "PO Value", "type": "TEXT_NUMBER"},
            {"title": "Terms of Payment", "type": "PICKLIST", "options": ["30 Days Credit", "60 Days Credit", "90 Days Credit", "Immediate Payment"]},
            {"title": "Hold Reason", "type": "TEXT_NUMBER"},
            {"title": "Total Allocated Cost", "type": "TEXT_NUMBER"},
            {"title": "Delivered Quantity (Sqm)", "type": "TEXT_NUMBER"},
            {"title": "Delivered Value", "type": "TEXT_NUMBER"},
            {"title": "Estimated in-production (3 days)", "type": "TEXT_NUMBER"},
            {"title": "PO Balance Quantity", "type": "TEXT_NUMBER"},
            {"title": "Balance Value (AED)", "type": "TEXT_NUMBER"},
            {"title": "Current Status", "type": "TEXT_NUMBER"},
            {"title": "Remarks", "type": "TEXT_NUMBER"},
            {"title": "Delivered Date", "type": "DATE"},
            {"title": "Approval Status", "type": "PICKLIST", "options": ["Submitted", "Approved", "Declined"]},
            {"title": "Number of Deliveries", "type": "TEXT_NUMBER"}
        ]
    },
    "01 LPO Audit LOG": {
        "folder": "01. Commercial and Demand",
        "columns": [
            {"title": "Snapshot Timestamp", "type": "DATE"},
            {"title": "Action Type", "type": "TEXT_NUMBER"},
            {"title": "Actor", "type": "TEXT_NUMBER"},
            {"title": "LPO ID", "type": "TEXT_NUMBER", "systemColumnType": "AUTO_NUMBER"},
            {"title": "Customer LPO Ref", "type": "TEXT_NUMBER", "primary": True},
            {"title": "SAP Reference", "type": "TEXT_NUMBER"},
            {"title": "Customer Name", "type": "TEXT_NUMBER"},
            {"title": "Project Name", "type": "TEXT_NUMBER"},
            {"title": "LPO Status", "type": "PICKLIST", "options": ["Draft", "Pending Approval", "Active", "On Hold", "Closed"]},
            {"title": "Brand", "type": "PICKLIST", "options": ["KIMMCO", "WTI"]},
            {"title": "Remarks", "type": "TEXT_NUMBER"}
        ]
    },
    
    # 02. Tag Sheet Registry
    "Tag Sheet Registry": {
        "folder": "02. Tag Sheet Registry",
        "columns": [
            {"title": "Tag ID", "type": "TEXT_NUMBER", "systemColumnType": "AUTO_NUMBER"},
            {"title": "Date Tag Sheet Received", "type": "DATETIME", "systemColumnType": "CREATED_DATE"},
            {"title": "Tag Sheet Name/ Rev", "type": "TEXT_NUMBER", "primary": True},
            {"title": "Required Delivery Date", "type": "DATE"},
            {"title": "LPO SAP Reference Link", "type": "TEXT_NUMBER"},
            {"title": "LPO Status", "type": "TEXT_NUMBER"},
            {"title": "LPO Allowable Wastage", "type": "TEXT_NUMBER"},
            {"title": "Production Gate", "type": "PICKLIST", "options": ["Red", "Yellow", "Green"]},
            {"title": "Brand", "type": "PICKLIST", "options": ["KIMMCO", "WTI"]},
            {"title": "Customer Name", "type": "TEXT_NUMBER"},
            {"title": "Project", "type": "TEXT_NUMBER"},
            {"title": "Location", "type": "TEXT_NUMBER"},
            {"title": "Estimated Quantity", "type": "TEXT_NUMBER"},
            {"title": "Sheets Used", "type": "TEXT_NUMBER"},
            {"title": "Wastage Nested", "type": "TEXT_NUMBER"},
            {"title": "Status", "type": "PICKLIST", "options": ["Draft", "Validate", "Sent to Nesting", "Nesting Complete", "Planned Queued", "WIP", "Complete", "Partial Dispatch", "Dispatched", "Closed", "Revision Pending", "Hold", "Cancelled"]},
            {"title": "Planned Cut Date", "type": "DATE"},
            {"title": "Allocation Batch ID", "type": "TEXT_NUMBER"},
            {"title": "Submitted By", "type": "CONTACT_LIST", "systemColumnType": "CREATED_BY"},
            {"title": "Received Through", "type": "PICKLIST", "options": ["Email", "Whatsapp"]},
            {"title": "Remarks", "type": "TEXT_NUMBER"},
            {"title": "File Hash", "type": "TEXT_NUMBER"},
            {"title": "Client Request ID", "type": "TEXT_NUMBER"}
        ]
    },
    
    # 03. Production Planning
    "03 Production Planning": {
        "folder": "03. Production Planning",
        "columns": [
            {"title": "Schedule ID", "type": "TEXT_NUMBER", "primary": True},
            {"title": "Tag Sheet ID", "type": "TEXT_NUMBER"},
            {"title": "Planned Date", "type": "DATE"},
            {"title": "Shift", "type": "PICKLIST", "options": ["Morning", "Evening"]},
            {"title": "Machine Assigned", "type": "PICKLIST", "options": ["1", "2"]},
            {"title": "Allocation Status", "type": "PICKLIST", "options": ["Draft", "Approved", "Issued", "Complete"]}
        ]
    },
    "04 Nesting Execution Log": {
        "folder": "03. Production Planning",
        "columns": [
            {"title": "Nest Session ID", "type": "TEXT_NUMBER", "primary": True},
            {"title": "Tag Sheet ID", "type": "TEXT_NUMBER"},
            {"title": "Timestamp", "type": "DATE"},
            {"title": "Brand", "type": "PICKLIST", "options": ["KIMMCO", "WTI"]},
            {"title": "Sheets Consumed Virtual", "type": "TEXT_NUMBER"},
            {"title": "Expected Consumption m2", "type": "TEXT_NUMBER"},
            {"title": "Wastage Percentage", "type": "TEXT_NUMBER"},
            {"title": "Planned Date", "type": "DATE"},
            {"title": "Remnant ID Generated", "type": "TEXT_NUMBER"},
            {"title": "Filler IDs Generated", "type": "TEXT_NUMBER"},
            {"title": "File Hash", "type": "TEXT_NUMBER"},
            {"title": "Client Request ID", "type": "TEXT_NUMBER"}
        ]
    },
    "05 Allocation Log": {
        "folder": "03. Production Planning",
        "columns": [
            {"title": "Allocation ID", "type": "TEXT_NUMBER", "primary": True},
            {"title": "Tag Sheet ID", "type": "TEXT_NUMBER"},
            {"title": "Material Code", "type": "TEXT_NUMBER"},
            {"title": "Quantity", "type": "TEXT_NUMBER"},
            {"title": "Planned Date", "type": "DATE"},
            {"title": "Shift", "type": "PICKLIST", "options": ["Morning", "Evening"]},
            {"title": "Status", "type": "PICKLIST", "options": ["Submitted", "Approved", "Released", "Expired"]},
            {"title": "Stock Check Flag", "type": "PICKLIST", "options": ["Red", "Yellow", "Green"]},
            {"title": "Allocated At", "type": "DATE"},
            {"title": "Reserved Until", "type": "DATE"},
            {"title": "Remarks", "type": "TEXT_NUMBER"}
        ]
    },
    
    # 04. Production and Delivery
    "06 Consumption Log": {
        "folder": "04. Production and Delivery",
        "columns": [
            {"title": "Consumption ID", "type": "TEXT_NUMBER", "primary": True},
            {"title": "Tag Sheet ID", "type": "TEXT_NUMBER"},
            {"title": "Status", "type": "PICKLIST", "options": ["Submitted", "Approved", "Adjustment Requested"]},
            {"title": "Consumption Date", "type": "DATE"},
            {"title": "Shift", "type": "PICKLIST", "options": ["Morning", "Evening"]},
            {"title": "Material Code", "type": "TEXT_NUMBER"},
            {"title": "Quantity", "type": "TEXT_NUMBER"},
            {"title": "Remnant ID", "type": "TEXT_NUMBER"},
            {"title": "Remarks", "type": "TEXT_NUMBER"}
        ]
    },
    "06a Remnant Log": {
        "folder": "04. Production and Delivery",
        "columns": [
            {"title": "Remnant ID", "type": "TEXT_NUMBER", "primary": True},
            {"title": "Session ID", "type": "TEXT_NUMBER"},
            {"title": "Status", "type": "PICKLIST", "options": ["Available", "Reserved", "Consumed"]},
            {"title": "Material Code", "type": "TEXT_NUMBER"},
            {"title": "Dimensions", "type": "TEXT_NUMBER"},
            {"title": "Area m2", "type": "TEXT_NUMBER"},
            {"title": "Created At", "type": "TEXT_NUMBER"},
            {"title": "Consumption Date", "type": "DATE"},
            {"title": "Remarks", "type": "TEXT_NUMBER"}
        ]
    },
    "06b Filler Log": {
        "folder": "04. Production and Delivery",
        "columns": [
            {"title": "Filler ID", "type": "TEXT_NUMBER", "primary": True},
            {"title": "Session ID", "type": "TEXT_NUMBER"},
            {"title": "SAP PO Reference", "type": "TEXT_NUMBER"},
            {"title": "Status", "type": "PICKLIST", "options": ["Available", "Reserved", "Used"]},
            {"title": "Material Code", "type": "TEXT_NUMBER"},
            {"title": "Type", "type": "PICKLIST", "options": ["SCRAP", "SMALL_FILL"]},
            {"title": "Dimensions", "type": "TEXT_NUMBER"},
            {"title": "Area m2", "type": "TEXT_NUMBER"},
            {"title": "Created At", "type": "TEXT_NUMBER"},
            {"title": "Consumption Date", "type": "DATE"},
            {"title": "Remarks", "type": "TEXT_NUMBER"}
        ]
    },
    "07 Delivery Log": {
        "folder": "04. Production and Delivery",
        "columns": [
            {"title": "Delivery ID", "type": "TEXT_NUMBER", "primary": True},
            {"title": "SAP DO Number", "type": "TEXT_NUMBER"},
            {"title": "Tag Sheet ID", "type": "TEXT_NUMBER"},
            {"title": "SAP Invoice Number", "type": "TEXT_NUMBER"},
            {"title": "Status", "type": "PICKLIST", "options": ["Pending SAP", "SAP Created", "Virtual", "POD Uploaded", "Invoiced", "Closed"]},
            {"title": "Lines", "type": "TEXT_NUMBER"},  # JSON field
            {"title": "Quantity", "type": "TEXT_NUMBER"},
            {"title": "Value", "type": "TEXT_NUMBER"},
            {"title": "Vehicle ID", "type": "TEXT_NUMBER"},
            {"title": "Created At", "type": "TEXT_NUMBER"},
            {"title": "Remarks", "type": "TEXT_NUMBER"}
        ]
    },
    "08 Invoice Log": {
        "folder": "04. Production and Delivery",
        "columns": [
            {"title": "Invoice ID", "type": "TEXT_NUMBER", "primary": True},
            {"title": "SAP Invoice Number", "type": "TEXT_NUMBER"},
            {"title": "Invoice Date", "type": "DATE"},
            {"title": "Status", "type": "PICKLIST", "options": ["Sent", "Paid", "Disputed"]},
            {"title": "Payment Date", "type": "DATE"},
            {"title": "DO ID(s)", "type": "TEXT_NUMBER"},
            {"title": "SAP DO Number(s)", "type": "TEXT_NUMBER"},
            {"title": "Terms of Payment", "type": "TEXT_NUMBER"},
            {"title": "Payment Due Date", "type": "TEXT_NUMBER"},
            {"title": "Remarks", "type": "TEXT_NUMBER"}
        ]
    },
    "90 Inventory Txn Log": {
        "folder": "04. Production and Delivery",
        "columns": [
            {"title": "Txn ID", "type": "TEXT_NUMBER", "primary": True},
            {"title": "Txn Date", "type": "DATETIME", "systemColumnType": "CREATED_DATE"},
            {"title": "Txn Type", "type": "PICKLIST", "options": ["Receipt", "Allocation", "Issue", "Consumption", "Pick", "Adjustment", "DO Issue", "Remnant Create", "Remnant Return"]},
            {"title": "Material Code", "type": "TEXT_NUMBER"},
            {"title": "Quantity", "type": "TEXT_NUMBER"},
            {"title": "Reference Doc", "type": "TEXT_NUMBER"},
            {"title": "Source System", "type": "PICKLIST", "options": ["Smartsheet", "AzureFunc", "SAP", "Manual"]},
            {"title": "Created By", "type": "TEXT_NUMBER"},
            {"title": "Trace ID", "type": "TEXT_NUMBER"},
            {"title": "Client Request ID", "type": "TEXT_NUMBER"}
        ]
    },
    "91 Inventory Snapshot": {
        "folder": "04. Production and Delivery",
        "columns": [
            {"title": "Snapshot ID", "type": "TEXT_NUMBER", "primary": True},
            {"title": "Snapshot Timestamp", "type": "DATETIME", "systemColumnType": "CREATED_DATE"},
            {"title": "Snapshot Type", "type": "TEXT_NUMBER"},
            {"title": "Plant", "type": "TEXT_NUMBER"},
            {"title": "Material Code", "type": "TEXT_NUMBER"},
            {"title": "UOM", "type": "TEXT_NUMBER"},
            {"title": "SAP Quantity", "type": "TEXT_NUMBER"},
            {"title": "Allocated Quantity", "type": "TEXT_NUMBER"},
            {"title": "Planned Quantity", "type": "TEXT_NUMBER"},
            {"title": "Actual Consumption", "type": "TEXT_NUMBER"},
            {"title": "System Closing Quantity", "type": "TEXT_NUMBER"},
            {"title": "Physical Closing Quantity", "type": "TEXT_NUMBER"},
            {"title": "Variance Quantity", "type": "TEXT_NUMBER"}
        ]
    },
    "92 SAP Inventory Snapshot": {
        "folder": "04. Production and Delivery",
        "columns": [
            {"title": "SAP Snapshot ID", "type": "TEXT_NUMBER", "primary": True},
            {"title": "Snapshot Timestamp", "type": "DATETIME", "systemColumnType": "CREATED_DATE"},
            {"title": "Material Code", "type": "TEXT_NUMBER"},
            {"title": "UOM", "type": "TEXT_NUMBER"},
            {"title": "Unrestricted Quantity", "type": "TEXT_NUMBER"},
            {"title": "In Transit Quantity", "type": "TEXT_NUMBER"},
            {"title": "WIP Quantity", "type": "TEXT_NUMBER"}
        ]
    },
    "93 Physical Inventory Snapshot": {
        "folder": "04. Production and Delivery",
        "columns": [
            {"title": "Physical Snapshot ID", "type": "TEXT_NUMBER", "primary": True},
            {"title": "Snapshot Timestamp", "type": "DATETIME", "systemColumnType": "CREATED_DATE"},
            {"title": "Plant", "type": "TEXT_NUMBER"},
            {"title": "Material Code", "type": "TEXT_NUMBER"},
            {"title": "UOM", "type": "TEXT_NUMBER"},
            {"title": "Physical Quantity", "type": "TEXT_NUMBER"},
            {"title": "Counted By", "type": "TEXT_NUMBER"},
            {"title": "Variance Posted", "type": "PICKLIST", "options": ["Yes", "No"]}
        ]
    },
    "97 Override Log": {
        "folder": "04. Production and Delivery",
        "columns": [
            {"title": "Override ID", "type": "TEXT_NUMBER", "primary": True},
            {"title": "Timestamp", "type": "DATETIME", "systemColumnType": "CREATED_DATE"},
            {"title": "Requested By", "type": "TEXT_NUMBER"},
            {"title": "Reason", "type": "TEXT_NUMBER"},
            {"title": "Related Entity", "type": "TEXT_NUMBER"},
            {"title": "Entity ID", "type": "TEXT_NUMBER"},
            {"title": "Requested Action", "type": "TEXT_NUMBER"},
            {"title": "Approval", "type": "TEXT_NUMBER"},
            {"title": "Decision", "type": "TEXT_NUMBER"},
            {"title": "Decision Timestamp", "type": "DATE"}
        ]
    },
    "98 User Action Log": {
        "folder": "04. Production and Delivery",
        "columns": [
            {"title": "Action ID", "type": "TEXT_NUMBER", "primary": True},
            {"title": "Timestamp", "type": "DATETIME", "systemColumnType": "CREATED_DATE"},
            {"title": "User ID", "type": "TEXT_NUMBER"},
            {"title": "Action Type", "type": "TEXT_NUMBER"},
            {"title": "Target Table", "type": "TEXT_NUMBER"},
            {"title": "Target ID", "type": "TEXT_NUMBER"},
            {"title": "Old Value", "type": "TEXT_NUMBER"},
            {"title": "New Value", "type": "TEXT_NUMBER"},
            {"title": "Notes", "type": "TEXT_NUMBER"}
        ]
    },
    "99 Exception Log": {
        "folder": "04. Production and Delivery",
        "columns": [
            {"title": "Exception ID", "type": "TEXT_NUMBER", "primary": True},
            {"title": "Created At", "type": "DATETIME", "systemColumnType": "CREATED_DATE"},
            {"title": "Source", "type": "PICKLIST", "options": ["Parser", "Allocation", "Reconcile", "Manual", "SAP Sync", "Ingest"]},
            {"title": "Related Tag ID", "type": "TEXT_NUMBER"},
            {"title": "Related Txn ID", "type": "TEXT_NUMBER"},
            {"title": "Material Code", "type": "TEXT_NUMBER"},
            {"title": "Quantity", "type": "TEXT_NUMBER"},
            {"title": "Reason Code", "type": "PICKLIST", "options": ["DUPLICATE_UPLOAD", "MULTI_TAG_NEST", "SHORTAGE", "OVERCONSUMPTION", "PHYSICAL_VARIANCE", "SAP_CREATE_FAILED", "PICK_NEGATIVE", "LPO_NOT_FOUND", "LPO_ON_HOLD", "INSUFFICIENT_PO_BALANCE", "PARSE_FAILED"]},
            {"title": "Severity", "type": "PICKLIST", "options": ["LOW", "MEDIUM", "HIGH", "CRITICAL"]},
            {"title": "Assigned To", "type": "TEXT_NUMBER"},
            {"title": "Status", "type": "PICKLIST", "options": ["Open", "Acknowledged", "In Progress", "Resolved", "Rejected"]},
            {"title": "Approvals", "type": "TEXT_NUMBER"},
            {"title": "SLA Due", "type": "DATE"},
            {"title": "Attachment Links", "type": "TEXT_NUMBER"},
            {"title": "Resolution Action", "type": "TEXT_NUMBER"}
        ]
    }
}


def create_workspace(name):
    """Create a new workspace."""
    url = f"{BASE_URL}/workspaces"
    payload = {"name": name}
    response = requests.post(url, headers=HEADERS, json=payload)
    response.raise_for_status()
    result = response.json()
    print(f"✓ Created workspace: {name} (ID: {result['result']['id']})")
    return result['result']['id']


def create_folder(workspace_id, folder_name):
    """Create a folder in the workspace."""
    url = f"{BASE_URL}/workspaces/{workspace_id}/folders"
    payload = {"name": folder_name}
    response = requests.post(url, headers=HEADERS, json=payload)
    response.raise_for_status()
    result = response.json()
    print(f"  📁 Created folder: {folder_name}")
    return result['result']['id']


def create_sheet_in_workspace(workspace_id, sheet_name, columns):
    """Create a sheet directly in the workspace (root level)."""
    url = f"{BASE_URL}/workspaces/{workspace_id}/sheets"
    payload = {
        "name": sheet_name,
        "columns": columns
    }
    response = requests.post(url, headers=HEADERS, json=payload)
    response.raise_for_status()
    result = response.json()
    print(f"    📄 Created sheet: {sheet_name}")
    return result['result']['id']


def create_sheet_in_folder(folder_id, sheet_name, columns):
    """Create a sheet in a folder."""
    url = f"{BASE_URL}/folders/{folder_id}/sheets"
    payload = {
        "name": sheet_name,
        "columns": columns
    }
    response = requests.post(url, headers=HEADERS, json=payload)
    response.raise_for_status()
    result = response.json()
    print(f"    📄 Created sheet: {sheet_name}")
    return result['result']['id']


def prepare_columns(column_defs):
    """Convert column definitions to Smartsheet API format."""
    columns = []
    for col in column_defs:
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
        columns.append(column)
    return columns


def main():
    print("=" * 60)
    print("Smartsheet Workspace Creator")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print("=" * 60)
    
    # Get workspace name from user
    workspace_name = input("\nEnter new workspace name (e.g., 'Ducts Production'): ").strip()
    if not workspace_name:
        workspace_name = f"Ducts Workspace {datetime.now().strftime('%Y%m%d_%H%M')}"
    
    print(f"\nCreating workspace: {workspace_name}")
    print("-" * 40)
    
    # Step 1: Create workspace
    workspace_id = create_workspace(workspace_name)
    time.sleep(0.5)  # Rate limiting
    
    # Step 2: Create folders
    print("\n[1/3] Creating folder structure...")
    folder_ids = {}
    for folder_name in FOLDER_STRUCTURE:
        folder_id = create_folder(workspace_id, folder_name)
        folder_ids[folder_name] = folder_id
        time.sleep(0.3)
    
    # Step 3: Create sheets
    print("\n[2/3] Creating sheets...")
    created_sheets = []
    
    for sheet_name, definition in SHEET_DEFINITIONS.items():
        folder = definition.get("folder")
        columns = prepare_columns(definition["columns"])
        
        try:
            if folder is None:
                # Root level sheet
                sheet_id = create_sheet_in_workspace(workspace_id, sheet_name, columns)
            else:
                # Sheet in folder
                folder_id = folder_ids[folder]
                sheet_id = create_sheet_in_folder(folder_id, sheet_name, columns)
            
            created_sheets.append({"name": sheet_name, "id": sheet_id, "folder": folder})
            time.sleep(0.3)  # Rate limiting
            
        except Exception as e:
            print(f"    ❌ Error creating {sheet_name}: {e}")
    
    # Save results
    print("\n[3/3] Saving workspace info...")
    result = {
        "created_at": datetime.now().isoformat(),
        "workspace_name": workspace_name,
        "workspace_id": workspace_id,
        "folders": folder_ids,
        "sheets": created_sheets
    }
    
    output_file = f"workspace_created_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    
    print(f"\n✓ Workspace info saved to: {output_file}")
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Workspace: {workspace_name}")
    print(f"Workspace ID: {workspace_id}")
    print(f"Folders created: {len(folder_ids)}")
    print(f"Sheets created: {len(created_sheets)}")
    print(f"\nOpen workspace: https://app.smartsheet.eu/browse/workspaces/{workspace_id}")


if __name__ == "__main__":
    main()
