# 🚀 Quick Start Guide

> **Time Required:** ~15 minutes | **Difficulty:** Beginner

This guide gets you from zero to running the tag ingestion function locally in 15 minutes or less.

---

## Prerequisites

Before you begin, ensure you have:

- [ ] **Python 3.9+** installed ([Download](https://www.python.org/downloads/))
- [ ] **Git** installed ([Download](https://git-scm.com/downloads))
- [ ] **Azure Functions Core Tools** v4+ ([Install Guide](https://docs.microsoft.com/azure/azure-functions/functions-run-local))
- [ ] **Smartsheet API Key** (from Personal Settings → API Access)
- [ ] **Workspace ID** (from Smartsheet Workspace Properties)

> **💡 Tip:** If you don't have Azure Functions Core Tools, install via npm:
> ```bash
> npm install -g azure-functions-core-tools@4
> ```

---

## Step 1: Clone the Repository

```bash
git clone <repository-url>
cd ducts_manufacturing_inventory_management
```

---

## Step 2: Create Virtual Environment

### Windows (PowerShell)
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### macOS/Linux
```bash
python -m venv venv
source venv/bin/activate
```

> **✅ Success indicator:** Your terminal prompt should show `(venv)` prefix.

---

## Step 3: Install Dependencies

```bash
# Core dependencies
pip install -r requirements.txt

# Azure Functions dependencies
pip install -r functions/requirements.txt

# Test dependencies (optional, recommended)
pip install -r functions/requirements-test.txt
```

---

## Step 4: Configure Environment

Create the local settings file for Azure Functions:

```bash
# Navigate to functions directory
cd functions

# Create local.settings.json (if it doesn't exist)
```

Edit `functions/local.settings.json` with your credentials:

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

> **⚠️ Important:** Never commit `local.settings.json` to git. It's already in `.gitignore`.

---

## Step 5: Start the Function App

```bash
# Make sure you're in the functions directory
cd functions

# Start the function app
func start
```

**Expected Output:**
```
Azure Functions Core Tools
...
Functions:

    fn_ingest_tag: [POST] http://localhost:7071/api/tags/ingest

For detailed output, run func with --verbose flag.
```

> **✅ Success indicator:** You see the `fn_ingest_tag` endpoint listed.

---

## Step 6: Test the Endpoint

Open a new terminal and test with a sample request:

### Using curl
```bash
curl -X POST http://localhost:7071/api/tags/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "client_request_id": "test-001",
    "lpo_sap_reference": "YOUR_LPO_REFERENCE",
    "required_area_m2": 50.0,
    "requested_delivery_date": "2026-02-01",
    "uploaded_by": "test@company.com"
  }'
```

### Using PowerShell
```powershell
$body = @{
    client_request_id = "test-001"
    lpo_sap_reference = "YOUR_LPO_REFERENCE"
    required_area_m2 = 50.0
    requested_delivery_date = "2026-02-01"
    uploaded_by = "test@company.com"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:7071/api/tags/ingest" `
    -Method Post `
    -Body $body `
    -ContentType "application/json"
```

### Expected Response
```json
{
  "status": "UPLOADED",
  "tag_id": "TAG-0001",
  "trace_id": "trace-abc123...",
  "message": "Tag uploaded successfully"
}
```

---

## Step 7: Run Tests

```bash
# From the functions directory
cd functions

# Run all tests
pytest

# Run with verbose output
pytest -v

# Run only unit tests
pytest -m unit

# Run with coverage
pytest --cov=shared --cov=fn_ingest_tag
```

---

## 🎉 Congratulations!

You've successfully:
- ✅ Set up the development environment
- ✅ Started the Azure Functions locally
- ✅ Tested the tag ingestion endpoint
- ✅ Verified the test suite works

---

## Next Steps

| Action | Document |
|--------|----------|
| Understand the architecture | [Architecture Overview](./architecture_overview.md) |
| Complete environment setup | [Setup Guide](./setup_guide.md) |
| Learn the API | [API Reference](./reference/api_reference.md) |
| Add new functionality | [Adding a New Function](./howto/add_function.md) |

---

## Common Issues

### "Module not found" errors
```bash
# Ensure you're in the virtual environment
# Windows:
.\venv\Scripts\Activate.ps1
# macOS/Linux:
source venv/bin/activate

# Reinstall dependencies
pip install -r functions/requirements.txt
```

### "SMARTSHEET_API_KEY not found"
Ensure `local.settings.json` exists in the `functions/` directory with valid credentials.

### Azure Functions won't start
```bash
# Update Azure Functions Core Tools
npm update -g azure-functions-core-tools@4
```

For more issues, see the [Troubleshooting Guide](./howto/troubleshooting.md).

---

<p align="center">
  <a href="./setup_guide.md">📖 Complete Setup Guide →</a>
</p>
