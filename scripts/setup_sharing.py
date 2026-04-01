#!/usr/bin/env python3
"""
setup_sharing.py — Configure Smartsheet sheet sharing permissions.

Sets up VIEWER access for Production Team, PM, and Sales Team
based on the agreed access matrix.

Usage:
    python scripts/setup_sharing.py                  # Dry run (preview)
    python scripts/setup_sharing.py --apply           # Apply changes
    python scripts/setup_sharing.py --revoke          # Preview revocation
    python scripts/setup_sharing.py --revoke --apply  # Revoke managed shares

Requires:
    SMARTSHEET_API_KEY env var (or in functions/local.settings.json)
"""

import json
import os
import sys
import time
import argparse
import requests
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = os.environ.get("SMARTSHEET_BASE_URL", "https://api.smartsheet.eu/2.0")
MANIFEST_PATH = Path(__file__).resolve().parent.parent / "functions" / "workspace_manifest.json"
LOCAL_SETTINGS_PATH = Path(__file__).resolve().parent.parent / "functions" / "local.settings.json"

# Team definitions
TEAMS = {
    "Production Team": [
        "aslam.ca@tte.ae",
        "manu.nair@tte.ae",
        "sheri.wilson@tte.ae",
        "sumit.kavade@tte.ae",
    ],
    "PM": [
        "raqibudeen.n@tte.ae",
    ],
    "Sales Team": [
        "mohammed.rizwan@tte.ae",
        "bobby.b@tte.ae",
        "boni.phillip@tte.ae",
    ],
}

# Access matrix: sheet_logical_name -> { team_name: access_level }
# Only VIEWER sheets listed here. Form-only sheets (H sheets) are handled
# separately — forms are shared via URL, not sheet-level permissions.
ACCESS_MATRIX = {
    # 01 - Commercial
    "LPO_MASTER": {
        "Production Team": "VIEWER",
        "PM": "VIEWER",
        "Sales Team": "VIEWER",
    },
    # 02 - Tag Registry
    "TAG_REGISTRY": {
        "Production Team": "VIEWER",
        "PM": "VIEWER",
        "Sales Team": "VIEWER",
    },
    # 03 - Production Planning
    "PRODUCTION_PLANNING": {
        "Production Team": "VIEWER",
        "PM": "VIEWER",
        "Sales Team": "VIEWER",
    },
    # 04 - Nesting Log
    "NESTING_LOG": {
        "Production Team": "VIEWER",
        "PM": "VIEWER",
    },
    # 05 - Allocation Log
    "ALLOCATION_LOG": {
        "Production Team": "VIEWER",
        "PM": "VIEWER",
    },
    # 05a - Material Master
    "MATERIAL_MASTER": {
        "Production Team": "VIEWER",
        "PM": "VIEWER",
    },
    # 06 - Consumption Log
    "CONSUMPTION_LOG": {
        "Production Team": "VIEWER",
        "PM": "VIEWER",
    },
    # 06c - Margin Approval Log (PM only)
    "06C_MARGIN_APPROVAL_LOG": {
        "PM": "VIEWER",
    },
    # 07 - Delivery Log
    "DELIVERY_LOG": {
        "Production Team": "VIEWER",
        "PM": "VIEWER",
        "Sales Team": "VIEWER",
    },
    # 91 - Inventory Snapshot
    "INVENTORY_SNAPSHOT": {
        "Production Team": "VIEWER",
        "PM": "VIEWER",
    },
    # 00a - Config (PM/admin only)
    "CONFIG": {
        "PM": "VIEWER",
    },
    # 99 - Exception Log
    "EXCEPTION_LOG": {
        "Production Team": "VIEWER",
        "PM": "VIEWER",
    },
    # 98 - User Action Log (PM only)
    "USER_ACTION_LOG": {
        "PM": "VIEWER",
    },
}

# Form-only sheets — no sheet sharing; just print the form URLs for reference
FORM_ONLY_SHEETS = {
    "01H_LPO_INGESTION": ["Production Team", "Sales Team"],
    "02H_TAG_SHEET_STAGING": ["Production Team"],
    "03H_PRODUCTION_PLANNING_STAGING": ["Production Team"],
    "07H_DELIVERY_LOG_INGESTION": ["Production Team"],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_api_key() -> str:
    """Get API key from env or local.settings.json."""
    key = os.environ.get("SMARTSHEET_API_KEY")
    if key:
        return key
    if LOCAL_SETTINGS_PATH.exists():
        with open(LOCAL_SETTINGS_PATH) as f:
            settings = json.load(f)
        key = settings.get("Values", {}).get("SMARTSHEET_API_KEY")
        if key:
            return key
    print("ERROR: SMARTSHEET_API_KEY not found in env or local.settings.json")
    sys.exit(1)


def load_manifest() -> dict:
    """Load workspace manifest."""
    if not MANIFEST_PATH.exists():
        print(f"ERROR: Manifest not found at {MANIFEST_PATH}")
        sys.exit(1)
    with open(MANIFEST_PATH) as f:
        return json.load(f)


def resolve_sheet_id(manifest: dict, logical_name: str) -> int:
    """Resolve logical sheet name to numeric ID."""
    sheet = manifest.get("sheets", {}).get(logical_name)
    if not sheet:
        print(f"  WARNING: Sheet '{logical_name}' not found in manifest — skipping")
        return 0
    return sheet["id"]


def get_display_name(manifest: dict, logical_name: str) -> str:
    """Get the human-readable sheet name."""
    sheet = manifest.get("sheets", {}).get(logical_name)
    return sheet["name"] if sheet else logical_name


class SmartsheetShareManager:
    """Handles Smartsheet sharing API calls with rate limiting."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._request_count = 0

    def _rate_limit(self):
        """Basic rate limiting — Smartsheet allows 300 req/min."""
        self._request_count += 1
        if self._request_count % 10 == 0:
            time.sleep(1)

    def get_existing_shares(self, sheet_id: int) -> list:
        """Get current shares for a sheet."""
        self._rate_limit()
        url = f"{BASE_URL}/sheets/{sheet_id}/shares"
        resp = requests.get(url, headers=self.headers)
        resp.raise_for_status()
        return resp.json().get("data", [])

    def share_sheet(self, sheet_id: int, email: str, access_level: str) -> dict:
        """Share a sheet with a user. Returns API response."""
        self._rate_limit()
        url = f"{BASE_URL}/sheets/{sheet_id}/shares"
        payload = [{"email": email, "accessLevel": access_level}]
        params = {"sendEmail": "false"}
        resp = requests.post(url, headers=self.headers, json=payload, params=params)
        resp.raise_for_status()
        return resp.json()

    def update_share(self, sheet_id: int, share_id: str, access_level: str) -> dict:
        """Update an existing share's access level."""
        self._rate_limit()
        url = f"{BASE_URL}/sheets/{sheet_id}/shares/{share_id}"
        payload = {"accessLevel": access_level}
        resp = requests.put(url, headers=self.headers, json=payload)
        resp.raise_for_status()
        return resp.json()

    def delete_share(self, sheet_id: int, share_id: str) -> dict:
        """Remove a share."""
        self._rate_limit()
        url = f"{BASE_URL}/sheets/{sheet_id}/shares/{share_id}"
        resp = requests.delete(url, headers=self.headers)
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def build_desired_shares(manifest: dict) -> list:
    """
    Build a flat list of desired shares from the access matrix.
    Returns: [{"sheet": logical_name, "sheet_id": int, "email": str, "access": str}, ...]
    """
    desired = []
    for sheet_logical, team_access in ACCESS_MATRIX.items():
        sheet_id = resolve_sheet_id(manifest, sheet_logical)
        if not sheet_id:
            continue
        for team_name, access_level in team_access.items():
            for email in TEAMS.get(team_name, []):
                desired.append({
                    "sheet": sheet_logical,
                    "sheet_id": sheet_id,
                    "display_name": get_display_name(manifest, sheet_logical),
                    "email": email.lower().strip(),
                    "access": access_level,
                    "team": team_name,
                })
    return desired


def run_apply(manifest: dict, manager: SmartsheetShareManager):
    """Apply the access matrix — create/update shares."""
    desired = build_desired_shares(manifest)

    # Group by sheet for efficient API calls
    sheets = {}
    for d in desired:
        sheets.setdefault(d["sheet_id"], []).append(d)

    created, updated, skipped = 0, 0, 0

    for sheet_id, entries in sheets.items():
        display = entries[0]["display_name"]
        print(f"\n--- {display} (ID: {sheet_id}) ---")

        # Get existing shares
        try:
            existing = manager.get_existing_shares(sheet_id)
        except requests.HTTPError as e:
            print(f"  ERROR fetching shares: {e}")
            continue

        # Build lookup: email -> share info
        existing_map = {}
        for share in existing:
            email = share.get("email", "").lower()
            if email:
                existing_map[email] = share

        for entry in entries:
            email = entry["email"]
            desired_access = entry["access"]

            if email in existing_map:
                current = existing_map[email]
                current_access = current.get("accessLevel", "")
                if current_access == desired_access:
                    print(f"  SKIP  {email} — already {desired_access}")
                    skipped += 1
                else:
                    share_id = current["id"]
                    try:
                        manager.update_share(sheet_id, share_id, desired_access)
                        print(f"  UPDATE {email}: {current_access} -> {desired_access}")
                        updated += 1
                    except requests.HTTPError as e:
                        print(f"  ERROR updating {email}: {e}")
            else:
                try:
                    manager.share_sheet(sheet_id, email, desired_access)
                    print(f"  ADD    {email} as {desired_access}")
                    created += 1
                except requests.HTTPError as e:
                    print(f"  ERROR sharing with {email}: {e}")

    print(f"\n{'='*50}")
    print(f"Done! Created: {created}, Updated: {updated}, Skipped: {skipped}")


def run_dry(manifest: dict):
    """Preview what would be applied."""
    desired = build_desired_shares(manifest)

    print("\n=== DRY RUN — No changes will be made ===\n")

    # Group by sheet
    sheets = {}
    for d in desired:
        sheets.setdefault(d["display_name"], []).append(d)

    for display_name, entries in sorted(sheets.items()):
        print(f"  {display_name}:")
        for e in sorted(entries, key=lambda x: x["email"]):
            print(f"    {e['email']:35s} {e['access']:10s} ({e['team']})")
        print()

    print(f"Total shares to configure: {len(desired)}")

    # Form-only sheets
    print("\n=== FORM-ONLY SHEETS (share form URLs, not sheet access) ===\n")
    for sheet_logical, teams in FORM_ONLY_SHEETS.items():
        display = get_display_name(manifest, sheet_logical)
        sheet_id = resolve_sheet_id(manifest, sheet_logical)
        team_str = ", ".join(teams)
        print(f"  {display} (ID: {sheet_id})")
        print(f"    Share form URL with: {team_str}")
        print()

    print("Run with --apply to execute these changes.")


def run_revoke(manifest: dict, manager: SmartsheetShareManager, apply: bool):
    """Revoke all shares that this script manages."""
    desired = build_desired_shares(manifest)

    # Collect all managed emails
    managed_emails = set()
    for d in desired:
        managed_emails.add(d["email"])

    # Group by sheet
    sheet_ids = set(d["sheet_id"] for d in desired)

    revoked, skipped = 0, 0

    for sheet_id in sheet_ids:
        display = next(d["display_name"] for d in desired if d["sheet_id"] == sheet_id)
        print(f"\n--- {display} (ID: {sheet_id}) ---")

        try:
            existing = manager.get_existing_shares(sheet_id)
        except requests.HTTPError as e:
            print(f"  ERROR: {e}")
            continue

        for share in existing:
            email = share.get("email", "").lower()
            if email in managed_emails:
                if apply:
                    try:
                        manager.delete_share(sheet_id, share["id"])
                        print(f"  REVOKED {email}")
                        revoked += 1
                    except requests.HTTPError as e:
                        print(f"  ERROR revoking {email}: {e}")
                else:
                    print(f"  WOULD REVOKE {email} ({share.get('accessLevel')})")
                    revoked += 1

    if not apply:
        print(f"\nWould revoke {revoked} shares. Run with --revoke --apply to execute.")
    else:
        print(f"\nRevoked {revoked} shares.")


def main():
    parser = argparse.ArgumentParser(description="Configure Smartsheet sharing permissions")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default is dry run)")
    parser.add_argument("--revoke", action="store_true", help="Revoke all managed shares")
    args = parser.parse_args()

    manifest = load_manifest()
    api_key = get_api_key()

    print("Smartsheet Sharing Configuration")
    print(f"  Base URL:  {BASE_URL}")
    print(f"  Manifest:  {MANIFEST_PATH}")
    print(f"  Workspace: {manifest.get('workspace', {}).get('name', 'Unknown')}")
    print()

    # Print team summary
    for team, members in TEAMS.items():
        print(f"  {team}: {', '.join(members)}")
    print()

    if args.revoke:
        manager = SmartsheetShareManager(api_key)
        run_revoke(manifest, manager, apply=args.apply)
    elif args.apply:
        manager = SmartsheetShareManager(api_key)
        run_apply(manifest, manager)
    else:
        run_dry(manifest)


if __name__ == "__main__":
    main()
