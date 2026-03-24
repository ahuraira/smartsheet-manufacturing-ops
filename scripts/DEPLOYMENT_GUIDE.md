# Ducts Manufacturing — Deployment & Environment Guide

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Environment Matrix](#2-environment-matrix)
3. [Initial Setup: Dev Environment](#3-initial-setup-dev-environment)
4. [Smartsheet Workspace Setup](#4-smartsheet-workspace-setup)
5. [Power Automate Solution Setup](#5-power-automate-solution-setup)
6. [Daily Development Workflow](#6-daily-development-workflow)
7. [Deploying to Dev](#7-deploying-to-dev)
8. [Promoting Dev to Production](#8-promoting-dev-to-production)
9. [Environment Variables Reference](#9-environment-variables-reference)
10. [Troubleshooting](#10-troubleshooting)
11. [Rollback Procedures](#11-rollback-procedures)

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                        Production                                │
│                                                                  │
│  Smartsheet EU ──► Power Automate ──► fn-ducts-prod              │
│  (Workspace A)     (Prod Solution)    ├── 18 HTTP functions      │
│                                       ├── stgductsprod001        │
│                                       ├── ai-ducts-prod          │
│                                       └── SharePoint (Ducts/)    │
├──────────────────────────────────────────────────────────────────┤
│                        Development                               │
│                                                                  │
│  Smartsheet EU ──► Power Automate ──► fn-ducts-dev               │
│  (Workspace B)     (Dev Solution)     ├── 18 HTTP functions      │
│                                       ├── stgductsdev001         │
│                                       ├── ai-ducts-dev           │
│                                       └── SharePoint (Ducts-DEV/)│
└──────────────────────────────────────────────────────────────────┘
```

**What is isolated per environment:**

| Resource | Isolated? | Why |
|----------|-----------|-----|
| Function App | Yes | Different code versions, different config |
| Storage Account | Yes | Queue locks are per-storage — prevents cross-env locking |
| Smartsheet Workspace | Yes | Dev data doesn't pollute prod sheets |
| Power Automate Flows | Yes | Dev flows callback to dev function app |
| App Insights | Yes | Clean monitoring per environment |
| Smartsheet API Key | Shared | Same key accesses both workspaces |
| SharePoint | Recommended | Use separate folder (`Ducts-DEV/`) |
| workspace_manifest.json | Per workspace | Each workspace has different sheet/column IDs |

---

## 2. Environment Matrix

### Azure Resources

| Resource | Production | Development |
|----------|-----------|-------------|
| Resource Group | `rg-ducts-inventory` | `rg-ducts-dev` |
| Function App | `fn-ducts-prod` | `fn-ducts-dev` |
| Storage Account | `stgductsprod001` | `stgductsdev001` |
| App Insights | `ai-ducts-prod` | `ai-ducts-dev` |
| Log Analytics | `law-ducts-prod` | `law-ducts-dev` |
| Region | East US | East US |
| Plan | Consumption (Linux) | Consumption (Linux) |
| Runtime | Python 3.11 | Python 3.11 |

### External Services

| Service | Production | Development |
|---------|-----------|-------------|
| Smartsheet Base URL | `https://api.smartsheet.eu/2.0` | Same |
| Smartsheet Workspace | `4909940948133763` | `<DEV-WORKSPACE-ID>` |
| SharePoint Site | `.../Ducts` | `.../Ducts-DEV` |
| Power Automate | Prod solution | Dev solution |

### Estimated Monthly Cost (Dev, Consumption Plan)

| Resource | Cost |
|----------|------|
| Function App (Consumption) | ~$0 (1M free executions) |
| Storage Account (LRS) | ~$1-2 |
| App Insights | ~$0-5 |
| **Total** | **$2-7/month** |

---

## 3. Initial Setup: Dev Environment

### Prerequisites

```bash
# Azure CLI
az --version              # Requires 2.50+
az login                  # Login to Azure
az account set -s "Ducts Fabrication Plant"  # Select subscription

# Azure Functions Core Tools
func --version            # Requires 4.x

# Python
python --version          # Requires 3.11+
```

### Run the Setup Script

```bash
cd smartsheet-manufacturing-ops
./scripts/setup-env.sh dev
```

The script will:
1. Ask for your Smartsheet API key (auto-reads from prod if accessible)
2. Ask for dev Smartsheet workspace ID (create workspace first — see Section 4)
3. Ask for SharePoint dev URL (optional)
4. Create: Resource Group → Storage Account → Queue → Log Analytics → App Insights → Function App
5. Configure all environment variables
6. Generate `functions/local.settings.dev.json`

### After the Script

```bash
# To use dev settings locally:
cd functions
cp local.settings.dev.json local.settings.json

# Start locally against dev workspace:
func start
```

---

## 4. Smartsheet Workspace Setup

### Create Dev Workspace

1. Go to Smartsheet EU → Home → **+ Create** → **Workspace**
2. Name: `Ducts Manufacturing - DEV`
3. Copy the workspace ID from the URL:
   ```
   https://app.smartsheet.eu/workspaces/XXXXXXXXXX  ← this number
   ```

### Copy Sheets from Production

**Option A: Manual (UI)**
1. Open prod workspace
2. For each sheet: Right-click → **Save as New** → select dev workspace
3. This copies structure + column definitions (no data, which is what you want)

**Option B: API Script**
```bash
# List prod workspace sheets
curl -s -H "Authorization: Bearer $SMARTSHEET_API_KEY" \
  "https://api.smartsheet.eu/2.0/workspaces/4909940948133763" | \
  python3 -c "
import sys, json
ws = json.load(sys.stdin)
for s in ws.get('sheets', []):
    print(f\"{s['id']}  {s['name']}\")
"
```

### Refresh Manifest for Dev Workspace

After all sheets are copied:

```bash
# Update SMARTSHEET_WORKSPACE_ID in local.settings.json to dev workspace ID
# Then run:
cd functions
python fetch_manifest.py

# Verify:
python -c "import json; m=json.load(open('workspace_manifest.json')); print(f'{len(m[\"sheets\"])} sheets loaded')"
```

**Important:** The manifest must match the target workspace. When deploying to prod, the manifest must be regenerated against the prod workspace.

---

## 5. Power Automate Solution Setup

### Solution Structure

Create a single Power Automate solution containing all flows. This lets you export/import between environments.

**Flows in the solution:**

| Flow Name | Trigger | Calls | Env Var |
|-----------|---------|-------|---------|
| Ducts - Create LPO Folders | HTTP POST | SharePoint | `POWER_AUTOMATE_CREATE_FOLDERS_URL` |
| Ducts - Nesting Complete | HTTP POST | SharePoint + Email | `POWER_AUTOMATE_NESTING_COMPLETE_URL` |
| Ducts - Upload Files | HTTP POST | SharePoint | `POWER_AUTOMATE_UPLOAD_FILES_URL` |
| Ducts - Manager Approval | HTTP POST | Teams Adaptive Card → callback to Azure Function | `POWER_AUTOMATE_MANAGER_APPROVAL_URL` |
| Ducts - SAP Conflict | HTTP POST | Teams Adaptive Card → callback to Azure Function | `POWER_AUTOMATE_SAP_CONFLICT_URL` |

### Creating the Dev Solution

**Step 1: Export from prod**
1. Power Automate → Solutions → select your prod solution
2. Export → **Unmanaged** (so you can edit in dev)
3. Download the .zip file

**Step 2: Import into dev environment**
1. Power Automate → Solutions → Import
2. Upload the .zip
3. Map connections (SharePoint, Teams, Office 365)

**Step 3: Update callback URLs in dev flows**

Every flow that calls back to Azure Functions must point to `fn-ducts-dev`:

| Find (prod) | Replace (dev) |
|-------------|---------------|
| `fn-ducts-prod.azurewebsites.net` | `fn-ducts-dev.azurewebsites.net` |

Flows to update:
- Manager Approval → HTTP action → URL
- SAP Conflict → HTTP action → URL

**Step 4: Update SharePoint connections**

For flows that write to SharePoint:
- Change site URL from `Ducts/` to `Ducts-DEV/`
- Or create a `Ducts-DEV` document library

**Step 5: Get new trigger URLs and update Azure**

For each flow:
1. Open flow → click trigger → copy the HTTP POST URL
2. Update the function app:

```bash
az functionapp config appsettings set \
  --name fn-ducts-dev --resource-group rg-ducts-dev \
  --settings \
    POWER_AUTOMATE_CREATE_FOLDERS_URL="https://..." \
    POWER_AUTOMATE_NESTING_COMPLETE_URL="https://..." \
    POWER_AUTOMATE_UPLOAD_FILES_URL="https://..." \
    POWER_AUTOMATE_MANAGER_APPROVAL_URL="https://..." \
    POWER_AUTOMATE_SAP_CONFLICT_URL="https://..."
```

### Using Solution Environment Variables (Advanced)

Instead of hardcoding Azure Function URLs in flows, use Power Automate environment variables:

1. In your solution → New → Environment Variable
2. Name: `AzureFunctionBaseUrl`
3. Default: `https://fn-ducts-prod.azurewebsites.net`
4. Current (dev): `https://fn-ducts-dev.azurewebsites.net`

Then in each flow's HTTP action, use: `@{parameters('AzureFunctionBaseUrl')}/api/...`

This way, importing the solution into dev/prod automatically uses the right URL.

---

## 6. Daily Development Workflow

### Local Development

```bash
cd functions

# Use dev settings
cp local.settings.dev.json local.settings.json

# Run locally
func start

# Run tests
pytest -x -q

# Run a specific test
pytest tests/unit/test_sap_conflict.py -v

# Run with coverage
pytest --cov=shared --cov-report=html
```

### Git Branching Strategy

```
master (production)
  └── dev (development)
        └── feature/xyz (feature branches)
```

```bash
# Start a feature
git checkout dev
git pull origin dev
git checkout -b feature/sap-conflict-flow

# Work on feature...
git add . && git commit -m "feat: SAP conflict resolution"

# Merge to dev
git checkout dev
git merge feature/sap-conflict-flow
git push origin dev

# Deploy to dev
./scripts/deploy.sh dev

# When stable, promote to prod
./scripts/promote.sh
```

---

## 7. Deploying to Dev

```bash
# Deploy with pre-flight checks
./scripts/deploy.sh dev
```

**Pre-flight checks performed:**
1. Git status clean
2. All tests pass
3. All `fn_*/` have `function.json`
4. Azure target exists
5. Required env vars set
6. Manifest valid
7. Function inventory comparison
8. No confirmation needed for dev

### Manual Deployment (Alternative)

```bash
cd functions
func azure functionapp publish fn-ducts-dev --python
```

### Verify Deployment

```bash
# List deployed functions
az functionapp function list --name fn-ducts-dev -g rg-ducts-dev -o table

# Test an endpoint
curl -s "https://fn-ducts-dev.azurewebsites.net/api/stock/snapshot?plant=PLANT-A" | python3 -m json.tool

# Stream logs
az functionapp log tail --name fn-ducts-dev --resource-group rg-ducts-dev
```

---

## 8. Promoting Dev to Production

### Pre-Promotion Checklist

Run the dry run first:

```bash
./scripts/promote.sh --dry-run
```

This shows:
- Test results
- New environment variables (add to prod BEFORE deploying code)
- New/removed functions
- New Smartsheet columns
- Manifest freshness

### Step-by-Step Promotion

**Step 1: Sync environment variables**

If `promote.sh --dry-run` shows new variables:

```bash
# Copy specific setting from dev to prod
DEV_VAL=$(az functionapp config appsettings list \
  --name fn-ducts-dev -g rg-ducts-dev \
  --query "[?name=='NEW_VARIABLE'].value" -o tsv)

az functionapp config appsettings set \
  --name fn-ducts-prod -g rg-ducts-inventory \
  --settings NEW_VARIABLE="$DEV_VAL"
```

**For Power Automate URLs:** Dev flow URLs are different from prod. Don't copy these — prod flows have their own URLs.

**Step 2: Refresh manifest for prod workspace**

If new Smartsheet columns were added:

```bash
# Set workspace ID to PROD
# Run manifest refresh
# Verify sheet count matches
```

**Step 3: Update Power Automate flows (if changed)**

If new flows were added:
1. Add the flow to the prod solution
2. Get the trigger URL
3. Add the env var to prod function app

**Step 4: Deploy**

```bash
./scripts/promote.sh
# Type 'promote-to-prod' when prompted
```

**Step 5: Verify**

```bash
# Stream prod logs
az functionapp log tail --name fn-ducts-prod --resource-group rg-ducts-inventory

# Test a known endpoint
curl -s "https://fn-ducts-prod.azurewebsites.net/api/stock/snapshot?plant=PLANT-A"
```

---

## 9. Environment Variables Reference

### Required Variables

| Variable | Description | Example | Same across envs? |
|----------|-------------|---------|-------------------|
| `SMARTSHEET_API_KEY` | Smartsheet API token | `sk_...` | Yes (shared key) |
| `SMARTSHEET_BASE_URL` | Smartsheet API endpoint | `https://api.smartsheet.eu/2.0` | Yes |
| `SMARTSHEET_WORKSPACE_ID` | Target workspace | `4909940948133763` | **No** (per workspace) |
| `AZURE_STORAGE_CONNECTION_STRING` | Storage for queues | `DefaultEndpointsProtocol=...` | **No** (per storage) |
| `AzureWebJobsStorage` | Function runtime storage | Same as above | **No** |
| `FUNCTIONS_WORKER_RUNTIME` | Language runtime | `python` | Yes |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Monitoring | `InstrumentationKey=...` | **No** |

### Power Automate URLs

| Variable | Flow | Same across envs? |
|----------|------|-------------------|
| `POWER_AUTOMATE_CREATE_FOLDERS_URL` | Create LPO Folders | **No** |
| `POWER_AUTOMATE_NESTING_COMPLETE_URL` | Nesting Complete notification | **No** |
| `POWER_AUTOMATE_UPLOAD_FILES_URL` | Upload Files to SharePoint | **No** |
| `POWER_AUTOMATE_MANAGER_APPROVAL_URL` | Margin Approval card | **No** |
| `POWER_AUTOMATE_SAP_CONFLICT_URL` | SAP Conflict Resolution card | **No** |

### Optional Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `LOG_LEVEL` | Logging verbosity | `INFO` (prod), `DEBUG` (dev) |
| `FLOW_CONNECT_TIMEOUT` | HTTP connect timeout | `5.0` |
| `FLOW_READ_TIMEOUT` | HTTP read timeout | `10.0` |
| `FLOW_MAX_RETRIES` | Retry count for flow triggers | `3` |
| `FLOW_FIRE_AND_FORGET` | Don't wait for flow response | `true` |
| `LPO_SUBFOLDERS` | SharePoint folder structure | `LPO Documents,Costing,...` |
| `SHAREPOINT_BASE_URL` | SharePoint site | `https://...sharepoint.com/...` |
| `SHAREPOINT_DOC_LIBRARY_URL` | Document library | `https://...sharepoint.com/.../LPOs` |

### Adding a New Environment Variable

When you add a new env var to the codebase:

1. **Add to `local.settings.json`** for local dev
2. **Add to `setup-env.sh`** so new environments get it automatically
3. **Add to `deploy.sh` REQUIRED_VARS or FLOW_VARS** so pre-flight checks catch it
4. **Set in dev:** `az functionapp config appsettings set --name fn-ducts-dev ...`
5. **Set in prod:** `az functionapp config appsettings set --name fn-ducts-prod ...`
6. **`promote.sh` will warn** if dev has a variable that prod doesn't

---

## 10. Troubleshooting

### Function App Not Starting

```bash
# Check function app status
az functionapp show --name fn-ducts-dev -g rg-ducts-dev --query "state" -o tsv

# Check deployment logs
az functionapp deployment list-publishing-credentials \
  --name fn-ducts-dev -g rg-ducts-dev -o json

# Stream live logs
az functionapp log tail --name fn-ducts-dev -g rg-ducts-dev
```

### Queue Lock Issues

```bash
# Check if queue exists
az storage queue list --connection-string "$CONN" -o table

# Peek at messages (stuck locks)
az storage message peek --queue-name allocation-locks \
  --connection-string "$CONN" --num-messages 10 -o table

# Clear all locks (CAUTION)
az storage message clear --queue-name allocation-locks \
  --connection-string "$CONN"
```

### Manifest Mismatch

Symptoms: `Column 'X' not found in sheet Y` errors

```bash
# Verify manifest sheet count
python3 -c "import json; m=json.load(open('functions/workspace_manifest.json')); print(f'{len(m[\"sheets\"])} sheets')"

# Check if a specific column exists
python3 -c "
import json
m = json.load(open('functions/workspace_manifest.json'))
sheet = m['sheets'].get('TAG_REGISTRY', {})
print('Columns:', list(sheet.get('columns', {}).keys()))
"

# Fix: regenerate manifest against correct workspace
python fetch_manifest.py
```

### Power Automate Flow Not Triggering

1. Check the env var is set: `az functionapp config appsettings list --name fn-ducts-dev -g rg-ducts-dev --query "[?name=='POWER_AUTOMATE_*']"`
2. Check the value isn't `PLACEHOLDER_SET_AFTER_FLOW_IMPORT`
3. Check Azure Function logs for `"Failed to dispatch"` messages
4. Check Power Automate flow run history for errors

### Tests Failing After Manifest Change

If you refreshed the manifest for a different workspace, tests use mock manifests from `tests/conftest.py` — they don't read `workspace_manifest.json`. Tests should still pass. If they don't, the issue is in the code, not the manifest.

---

## 11. Rollback Procedures

### Code Rollback

```bash
# Find the last good commit
git log --oneline -10

# Deploy a specific commit to prod
git checkout <commit-hash>
cd functions && func azure functionapp publish fn-ducts-prod --python

# Go back to latest
git checkout master
```

### Environment Variable Rollback

```bash
# View current settings
az functionapp config appsettings list --name fn-ducts-prod -g rg-ducts-inventory -o table

# Revert a specific setting
az functionapp config appsettings set --name fn-ducts-prod -g rg-ducts-inventory \
  --settings VARIABLE_NAME="old-value"

# Delete a setting
az functionapp config appsettings delete --name fn-ducts-prod -g rg-ducts-inventory \
  --setting-names VARIABLE_NAME
```

### Full Environment Teardown

```bash
# Destroy dev environment completely
./scripts/setup-env.sh dev --destroy

# This deletes the entire resource group and ALL resources within it
# Smartsheet workspace and Power Automate solution are NOT affected (separate services)
```

---

## Quick Reference Card

```bash
# ── Setup ──
./scripts/setup-env.sh dev                    # Create dev environment

# ── Develop ──
cd functions && cp local.settings.dev.json local.settings.json
func start                                     # Run locally
pytest -x -q                                   # Run tests

# ── Deploy ──
./scripts/deploy.sh dev                        # Deploy to dev
./scripts/deploy.sh prod                       # Deploy to prod (with checks)

# ── Promote ──
./scripts/promote.sh --dry-run                 # Preview changes
./scripts/promote.sh                           # Deploy dev → prod

# ── Monitor ──
az functionapp log tail --name fn-ducts-dev -g rg-ducts-dev
az functionapp log tail --name fn-ducts-prod -g rg-ducts-inventory

# ── Destroy ──
./scripts/setup-env.sh dev --destroy           # Tear down dev
```
