"""
Script to register Smartsheet webhooks for the deployed adapter.
Usage: python register_webhooks.py
"""

import os
import json
import requests
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Configuration
# -----------------------------------------------------------------------------
# 1. Load API Key from local settings
SETTINGS_PATH = "functions/local.settings.json"
try:
    with open(SETTINGS_PATH, "r") as f:
        settings = json.load(f)
        API_KEY = settings.get("Values", {}).get("SMARTSHEET_API_KEY")
        if not API_KEY:
             raise ValueError("SMARTSHEET_API_KEY not found in local.settings.json")
except Exception as e:
    logger.error(f"Failed to load settings: {e}")
    exit(1)

# 2. Deployed Callback URL
CALLBACK_URL = "https://duct-smartsheet-adapter-ggeabqftcybhb6ex.eastus-01.azurewebsites.net/api/webhook/smartsheet"

# 3. Base URL
BASE_URL = "https://api.smartsheet.eu/2.0"

# 4. Sheets to Watch (Logical Name -> Description)
WATCHED_SHEETS = {
    "TAG_REGISTRY": "Webhook: Tag Registry (Azure Adapter)",
    "PRODUCTION_PLANNING": "Webhook: Production Planning (Azure Adapter)",
    "LPO_MASTER": "Webhook: LPO Master (Azure Adapter)",
    "EXCEPTION_LOG": "Webhook: Exception Log (Azure Adapter)",
    "01H_LPO_INGESTION": "Webhook: LPO Ingestion (Azure Adapter)",
    "02H_TAG_SHEET_STAGING": "Webhook: Tag Sheet Staging (Azure Adapter)",
    "03H_PRODUCTION_PLANNING_STAGING": "Webhook: Production Planning Staging (Azure Adapter)"
}

# -----------------------------------------------------------------------------

def get_headers():
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

def get_sheet_id(logical_name):
    # Load from manifest
    try:
        with open("functions/workspace_manifest.json", "r") as f:
            manifest = json.load(f)
            return manifest["sheets"][logical_name]["id"]
    except Exception as e:
        logger.error(f"Failed to load manifest for {logical_name}: {e}")
        return None

def list_webhooks():
    url = f"{BASE_URL}/webhooks"
    res = requests.get(url, headers=get_headers())
    res.raise_for_status()
    return res.json().get("data", [])

def delete_webhook(webhook_id):
    url = f"{BASE_URL}/webhooks/{webhook_id}"
    requests.delete(url, headers=get_headers())
    logger.info(f"Deleted webhook {webhook_id}")

def create_webhook(sheet_id, name):
    url = f"{BASE_URL}/webhooks"
    payload = {
        "name": name,
        "callbackUrl": CALLBACK_URL,
        "scope": "sheet",
        "scopeObjectId": sheet_id,
        "events": ["*.*"],
        "version": 1
    }
    
    try:
        # 1. Create
        res = requests.post(url, headers=get_headers(), json=payload)
        res.raise_for_status()
        webhook = res.json().get("result")
        webhook_id = webhook["id"]
        logger.info(f"Created webhook '{name}' (ID: {webhook_id})")
        
        # 2. Enable (triggers verification)
        enable_url = f"{BASE_URL}/webhooks/{webhook_id}"
        requests.put(enable_url, headers=get_headers(), json={"enabled": True})
        logger.info(f"Enabled webhook '{name}' - Verification Successful!")
        return True
        
    except requests.exceptions.HTTPError as e:
        logger.error(f"Failed to create/enable webhook '{name}': {e.response.text}")
        return False

def main():
    logger.info(f"Registering webhooks to: {CALLBACK_URL}")
    
    # Check existing webhooks
    existing = list_webhooks()
    logger.info(f"Found {len(existing)} existing webhooks")
    
    # Clean up old webhooks with same names (Avoid duplicates)
    for wh in existing:
        if "Azure Adapter" in wh.get("name", ""):
            if wh.get("callbackUrl") != CALLBACK_URL:
                 logger.warning(f"Deleting old webhook '{wh['name']}' pointing to {wh['callbackUrl']}")
                 delete_webhook(wh["id"])
            # Optional: Delete even if same URL to force recreate
            # delete_webhook(wh["id"]) 

    # Register New Webhooks
    for logical_name, name in WATCHED_SHEETS.items():
        sheet_id = get_sheet_id(logical_name)
        if not sheet_id:
            continue
            
        # Check if already exists
        exists = False
        for wh in existing:
             if wh.get("scopeObjectId") == sheet_id and wh.get("callbackUrl") == CALLBACK_URL:
                 logger.info(f"Webhook for {logical_name} already exists. Skipping.")
                 exists = True
                 break
        
        if not exists:
            logger.info(f"Registering {logical_name} (ID: {sheet_id})...")
            create_webhook(sheet_id, name)

if __name__ == "__main__":
    main()
