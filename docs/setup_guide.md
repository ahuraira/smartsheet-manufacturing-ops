# 🛠️ Development Environment Setup Guide

> **Document Type:** Tutorial | **Audience:** Developers | **Last Updated:** 2026-01-08

This guide provides complete instructions for setting up the development environment for the Ducts Manufacturing Inventory Management System.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Local Development Setup](#1-local-development-setup)
3. [Azure Functions Configuration](#2-azure-functions-configuration)
4. [Smartsheet Configuration](#3-smartsheet-configuration)
5. [Deploying to Azure](#4-deploying-to-azure)
6. [Power Automate Setup](#5-power-automate-setup)
7. [Verification Checklist](#verification-checklist)

---

## Prerequisites

### Required Software

| Software | Version | Download | Purpose |
|----------|---------|----------|---------|
| Python | 3.9+ | [python.org](https://www.python.org/downloads/) | Runtime |
| Git | Latest | [git-scm.com](https://git-scm.com/downloads) | Version control |
| VS Code | Latest | [code.visualstudio.com](https://code.visualstudio.com/) | IDE (recommended) |
| Azure Functions Core Tools | v4+ | [docs.microsoft.com](https://docs.microsoft.com/azure/azure-functions/functions-run-local) | Local development |
| Node.js | 18+ | [nodejs.org](https://nodejs.org/) | For Azure Functions Tools |

### Required Accounts & Access

| Account | Purpose | How to Get |
|---------|---------|------------|
| Smartsheet | Data storage | Contact team lead |
| Smartsheet API Key | API access | Smartsheet → Account → Personal Settings → API Access |
| Azure Subscription | Cloud hosting (optional) | Contact IT |

### VS Code Extensions (Recommended)

- Azure Functions
- Python
- Pylance
- GitLens

---

## 1. Local Development Setup

### Step 1.1: Clone Repository

```bash
# Clone the repository
git clone <repository-url>
cd ducts_manufacturing_inventory_management
```

### Step 1.2: Create Virtual Environment

#### Windows (PowerShell)
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

#### macOS/Linux
```bash
python -m venv venv
source venv/bin/activate
```

> **Note:** If you get an execution policy error on Windows:
> ```powershell
> Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
> ```

### Step 1.3: Install Dependencies

```bash
# Core project dependencies
pip install -r requirements.txt

# Azure Functions dependencies
pip install -r functions/requirements.txt

# Test dependencies (recommended)
pip install -r functions/requirements-test.txt
```

### Step 1.4: Verify Installation

```bash
# Check Python version
python --version  # Should be 3.9+

# Check Azure Functions tools
func --version  # Should be 4.x

# Verify packages installed
pip list | grep -E "pydantic|azure-functions|requests"
```

---

## 2. Azure Functions Configuration

### Step 2.1: Create Local Settings

Create `functions/local.settings.json`:

```json
{
  "IsEncrypted": false,
  "Values": {
    "AzureWebJobsStorage": "UseDevelopmentStorage=true",
    "FUNCTIONS_WORKER_RUNTIME": "python",
    "SMARTSHEET_API_KEY": "YOUR_API_KEY_HERE",
    "SMARTSHEET_WORKSPACE_ID": "YOUR_WORKSPACE_ID_HERE",
    "SMARTSHEET_BASE_URL": "https://api.smartsheet.eu/2.0"
  }
}
```

> **⚠️ Security:** Never commit `local.settings.json`. It's already in `.gitignore`.

### Step 2.2: Start Functions Locally

```bash
cd functions
func start
```

**Expected output:**
```
Azure Functions Core Tools
...
Functions:

    fn_ingest_tag: [POST] http://localhost:7071/api/tags/ingest

For detailed output, run func with --verbose flag.
```

### Step 2.3: Test Endpoint

```bash
curl -X POST http://localhost:7071/api/tags/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "client_request_id": "test-001",
    "lpo_sap_reference": "YOUR_LPO_REF",
    "required_area_m2": 50.0,
    "requested_delivery_date": "2026-02-01",
    "uploaded_by": "test@company.com"
  }'
```

---

## 3. Smartsheet Configuration

### Step 3.1: Get API Key

1. Log in to Smartsheet
2. Go to **Account** → **Personal Settings** → **API Access**
3. Click **Generate new access token**
4. Copy and save securely

### Step 3.2: Get Workspace ID

1. Open your Smartsheet workspace
2. Click **Workspace Properties** (⚙️ icon)
3. Copy the **Workspace ID**

### Step 3.3: Verify Sheets Exist

Ensure these sheets exist in your workspace:

| Sheet Name | Required |
|------------|----------|
| `00a Config` | ✅ Yes |
| `01 LPO Master LOG` | ✅ Yes |
| `02 Tag Sheet Registry` | ✅ Yes |
| `98 User Action Log` | ✅ Yes |
| `99 Exception Log` | ✅ Yes |

### Step 3.4: Initialize Config Sheet

Add these rows to `00a Config`:

| config_key | config_value | effective_from | changed_by |
|------------|--------------|----------------|------------|
| `seq_tag` | `0` | 2026-01-08 | admin |
| `seq_exception` | `0` | 2026-01-08 | admin |

See [config_values.md](../config_values.md) for complete list.

### Step 3.5: Generate Workspace Manifest

The application requires a `workspace_manifest.json` file to map logical names to physical IDs.

1.  Generate the manifest using `python fetch_manifest.py` (if available).
2.  Or manually create `workspace_manifest.json` in the project root, mapping your new sheet IDs to their logical names.
3.  The file **must** follow this structure (keys must be Logical Names):
    ```json
    {
      "workspace": { "id": 123456789, "name": "Ducts Workspace" },
      "sheets": {
        "TAG_REGISTRY": {
          "id": 123,
          "name": "02 Tag Sheet Registry",
          "columns": {
            "FILE_HASH": { "id": 456, "name": "File Hash", "type": "TEXT_NUMBER" }
          }
        },
        "LPO_MASTER": {
          "id": 789,
          "name": "01 LPO Master LOG",
          "columns": { ... }
        }
      }
    }
    ```

---

## 4. Deploying to Azure

### Step 4.1: Automated Deployment (Recommended)

We have provided a comprehensive workflow and script to automate deployment.

Please follow the **[Deploy to Azure Workflow](../.agent/workflows/deploy_to_azure.md)**.

Basic usage:
```powershell
cd functions
./deploy.ps1 -AppName "<YOUR_UNIQUE_APP_NAME>" -Location "uaenorth"
```

### Step 4.2: Manual Deployment (Alternative)

#### 1. Create Function App
1. Go to **Azure Portal** → **Create a resource** → **Function App**
2. Configure:
   - **Runtime stack:** Python 3.11
   - **Hosting:** Consumption Plan (Serverless)
   - **Region:** Same as your users
3. Enable **Application Insights**
4. Create

#### 2. Configure Application Settings
1. Go to **Configuration** → **Application settings**
2. Add:
   - `SMARTSHEET_ACCESS_TOKEN` (Note: we use `ACCESS_TOKEN`, relying on `local.settings.json` mapping)

#### 3. Deploy Code
```bash
cd functions
func azure functionapp publish YOUR_APP_NAME
```

---

## 5. Power Automate Setup

### Step 5.1: Create Solution

1. Go to **make.powerautomate.com** → **Solutions**
2. Create new solution: `Ducts Manufacturing Ops`

### Step 5.2: Create Connection References

- **Smartsheet:** For row triggers
- **HTTP:** For Azure Function calls

### Step 5.3: Create Tag Upload Flow

**Trigger:** Smartsheet - When a new row is created
**Sheet:** Tag Sheet Registry

**Actions:**

1. **Initialize Variable [Client Request ID]**
   - Value: `guid()` (Expression)

2. **HTTP Request** (Call Azure Function)
   ```
   Method: POST
   URI: https://your-app.azurewebsites.net/api/tags/ingest
   Headers:
     x-functions-key: <your-function-key>
     Content-Type: application/json
   Body: {
     "client_request_id": "@{variables('Client Request ID')}",
     "lpo_sap_reference": "@{triggerOutputs()?['body/LPO SAP Reference Link']}",
     ...
   }
   ```

3. **Parse JSON Response**

4. **Condition:** Update row based on response status

### Step 5.4: Handling Attachments

Smartsheet triggers don't include attachments directly. Options:

1. **SharePoint Upload:** User uploads to SharePoint, triggers flow from there
2. **Get Attachments Action:** Add step to retrieve attachments after trigger
3. **Base64 Pass-through:** Download in Power Automate, pass as base64 (limit 10MB)

**Recommended:** Use SharePoint trigger approach for large files.

---

## Verification Checklist

### Environment Setup

- [ ] Python 3.9+ installed and in PATH
- [ ] Virtual environment created and activated
- [ ] All dependencies installed
- [ ] Azure Functions Core Tools v4+ installed

### Configuration

- [ ] `local.settings.json` created with valid credentials
- [ ] Smartsheet API key works (test with curl)
- [ ] Workspace ID is correct
- [ ] Required sheets exist

### Local Testing

- [ ] `func start` runs without errors
- [ ] Endpoints are listed in console
- [ ] Test request returns valid response
- [ ] Tests pass: `pytest`

### Azure Deployment (if applicable)

- [ ] Function App created
- [ ] Application settings configured
- [ ] Code deployed
- [ ] Endpoints accessible
- [ ] Function keys generated

---

## Common Issues

### "SMARTSHEET_API_KEY environment variable is required"

Ensure `local.settings.json` exists in `functions/` directory with valid credentials.

### "Sheet 'X' not found in workspace"

Verify sheet exists and name matches exactly (case-sensitive).

### "func: command not found"

Install Azure Functions Core Tools:
```bash
npm install -g azure-functions-core-tools@4
```

### Rate limit errors

The client has built-in rate limiting. If still occurring, check for tight loops.

---

## Next Steps

| Action | Document |
|--------|----------|
| Understand the architecture | [Architecture Overview](./architecture_overview.md) |
| Learn the API | [API Reference](./reference/api_reference.md) |
| Write tests | [Testing Guide](./howto/testing.md) |
| Deploy to Azure | [Deployment Guide](./howto/deployment.md) |

---

<p align="center">
  <a href="./index.md">📚 Documentation Hub →</a>
</p>
