# 🔧 Troubleshooting Guide

> **Document Type:** How-To | **Audience:** All Team Members | **Last Updated:** 2026-01-08

This guide helps you diagnose and resolve common issues in the Ducts Manufacturing Inventory Management System.

---

## Table of Contents

1. [Quick Diagnosis](#quick-diagnosis)
2. [Development Environment Issues](#development-environment-issues)
3. [Runtime Errors](#runtime-errors)
4. [Smartsheet Integration Issues](#smartsheet-integration-issues)
5. [Testing Issues](#testing-issues)
6. [Performance Issues](#performance-issues)
7. [Getting Help](#getting-help)

---

## Quick Diagnosis

### Issue Decision Tree

```
Error occurring?
├── During development setup?
│   └── See: Development Environment Issues
├── During test execution?
│   └── See: Testing Issues
├── In production/runtime?
│   ├── HTTP 4xx response?
│   │   └── See: Runtime Errors > Client Errors
│   ├── HTTP 5xx response?
│   │   └── See: Runtime Errors > Server Errors
│   └── Smartsheet API error?
│       └── See: Smartsheet Integration Issues
└── Performance related?
    └── See: Performance Issues
```

---

## Development Environment Issues

### Issue: "python is not recognized"

**Symptoms:**
```
'python' is not recognized as an internal or external command
```

**Solution:**
1. Verify Python is installed: Download from [python.org](https://python.org)
2. Add Python to PATH during installation
3. Or use `python3` instead of `python`
4. Restart terminal after installation

---

### Issue: "func is not recognized"

**Symptoms:**
```
'func' is not recognized as an internal or external command
```

**Solution:**
```bash
# Install Azure Functions Core Tools
npm install -g azure-functions-core-tools@4

# Verify installation
func --version
```

---

### Issue: "ModuleNotFoundError"

**Symptoms:**
```python
ModuleNotFoundError: No module named 'pydantic'
```

**Solution:**
1. Ensure virtual environment is activated:
   ```powershell
   # Windows
   .\venv\Scripts\Activate.ps1
   
   # macOS/Linux
   source venv/bin/activate
   ```

2. Install dependencies:
   ```bash
   pip install -r functions/requirements.txt
   ```

3. Verify installation:
   ```bash
   pip list | grep pydantic
   ```

---

### Issue: "SMARTSHEET_API_KEY not found"

**Symptoms:**
```
ValueError: SMARTSHEET_API_KEY environment variable is required
```

**Solution:**
1. Create/update `functions/local.settings.json`:
   ```json
   {
     "IsEncrypted": false,
     "Values": {
       "SMARTSHEET_API_KEY": "your_actual_key_here",
       "SMARTSHEET_WORKSPACE_ID": "your_workspace_id",
       "SMARTSHEET_BASE_URL": "https://api.smartsheet.eu/2.0"
     }
   }
   ```

2. Ensure you're running from the `functions/` directory:
   ```bash
   cd functions
   func start
   ```

---

### Issue: Virtual environment won't activate

**Symptoms:**
```
cannot be loaded because running scripts is disabled on this system
```

**Solution (Windows PowerShell):**
```powershell
# Run PowerShell as Administrator
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# Then activate
.\venv\Scripts\Activate.ps1
```

---

## Runtime Errors

### Issue: "400 Bad Request"

**Symptoms:**
```json
{
  "status": "ERROR",
  "message": "Validation error: ..."
}
```

**Diagnosis:**
- Missing required fields in request
- Invalid data types
- Malformed JSON

**Solution:**
1. Check request body against [API Reference](../reference/api_reference.md)
2. Verify all required fields are present:
   - `required_area_m2` (number)
   - `requested_delivery_date` (string, ISO format)
   - `uploaded_by` (string)
   - At least one LPO reference

3. Example valid request:
   ```json
   {
     "lpo_sap_reference": "SAP-001",
     "required_area_m2": 50.0,
     "requested_delivery_date": "2026-02-01",
     "uploaded_by": "user@company.com"
   }
   ```

---

### Issue: "409 Conflict - DUPLICATE"

**Symptoms:**
```json
{
  "status": "DUPLICATE",
  "existing_tag_id": "TAG-0001"
}
```

**Cause:** Same file (by hash) was already uploaded.

**Solution:**
1. This is expected behavior for duplicate detection
2. If intentional re-upload, rename file or modify content
3. Check existing tag record for status
4. Use different `client_request_id` for new logical request

---

### Issue: "422 BLOCKED - LPO_NOT_FOUND"

**Symptoms:**
```json
{
  "status": "BLOCKED",
  "message": "Referenced LPO not found"
}
```

**Solution:**
1. Verify LPO reference exists in `01 LPO Master LOG`
2. Check for typos in reference
3. Try different reference field:
   - `lpo_sap_reference` (SAP Reference column)
   - `customer_lpo_ref` (Customer LPO Ref column)
   - `lpo_id` (tries both)

---

### Issue: "422 BLOCKED - LPO_ON_HOLD"

**Cause:** Referenced LPO has status "On Hold".

**Solution:**
1. Contact sales/commercial team
2. Request LPO status change to "Active"
3. Wait for LPO release before retry

---

### Issue: "422 BLOCKED - INSUFFICIENT_PO_BALANCE"

**Symptoms:**
```json
{
  "message": "Insufficient PO balance. Required: 120.25, Available: 50.0"
}
```

**Solution:**
1. Review PO quantity vs delivered quantity
2. Options:
   - Increase PO quantity (amendment)
   - Split into smaller tags
   - Use different LPO

---

### Issue: "500 Internal Server Error"

**Symptoms:**
```json
{
  "status": "ERROR",
  "message": "Internal error: ..."
}
```

**Diagnosis:**
1. Check `trace_id` in response
2. Search logs for this trace_id
3. Common causes:
   - Smartsheet API connectivity
   - Database errors
   - Unhandled exceptions

**Solution:**
1. Retry the request (most are transient)
2. If persists, escalate with trace_id
3. Check Azure App Insights for details

---

## Smartsheet Integration Issues

### Issue: "Sheet 'X' not found in workspace"

**Cause:** Sheet name mismatch or missing sheet.

**Solution:**
1. Verify sheet exists in Smartsheet workspace
2. Check exact name (case-sensitive):
   ```python
   # Expected names
   SheetName.TAG_REGISTRY = "Tag Sheet Registry"
   SheetName.LPO_MASTER = "01 LPO Master LOG"
   ```

3. Refresh cache if sheet was recently created:
   ```python
   client.refresh_sheet_cache()
   ```

---

### Issue: "Column 'X' not found"

**Cause:** Column name mismatch.

**Solution:**
1. Open sheet in Smartsheet
2. Verify exact column name
3. Check `ColumnName` constants in `sheet_config.py`
4. Update sheet or code to match

---

### Issue: "Rate limit exceeded"

**Symptoms:**
```
SmartsheetRateLimitError: Rate limit exceeded
```

**Cause:** More than 300 requests/minute.

**Solution:**
1. Wait for rate limit reset (headers show reset time)
2. The client has automatic retry with backoff
3. If persists, check for infinite loops
4. Use batch operations where possible

---

### Issue: "Save collision" (4004)

**Cause:** Another user/process updated the same row.

**Solution:**
1. Automatic retry handles most cases
2. If persists, implement optimistic locking:
   ```python
   # Read latest
   row = client.find_row_by_column(sheet, col, value)
   # Make changes
   # Update (will fail if changed since read)
   client.update_row(sheet, row['row_id'], updates)
   ```

---

### Issue: Sequence collision errors

**Symptoms:**
```
SequenceCollisionError: Failed to generate ID after 5 attempts
```

**Cause:** High concurrent ID generation.

**Solution:**
1. This is rare; retry usually succeeds
2. If frequent, indicates:
   - Very high load
   - Need for database-backed sequences

3. Temporary workaround:
   ```python
   # Increase retry count in id_generator.py
   MAX_RETRIES = 10  # Default is 5
   ```

---

## Testing Issues

### Issue: "Tests fail with import errors"

**Solution:**
1. Ensure you're in the `functions/` directory:
   ```bash
   cd functions
   pytest
   ```

2. Install test dependencies:
   ```bash
   pip install -r requirements-test.txt
   ```

---

### Issue: "Tests fail due to missing environment variables"

**Solution:**
Test fixtures auto-set environment variables, but if failing:
```python
# In conftest.py, verify setup_test_environment fixture
@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    os.environ.setdefault("SMARTSHEET_API_KEY", "test-key")
    os.environ.setdefault("SMARTSHEET_WORKSPACE_ID", "test-workspace")
```

---

### Issue: "Tests pass locally but fail in CI"

**Possible causes:**
1. **Missing dependencies:** Check `requirements-test.txt` is installed
2. **Environment differences:** Ensure CI sets required env vars
3. **Path issues:** Use absolute paths or os.path.join
4. **Timezone differences:** Use UTC in tests

---

## Performance Issues

### Issue: Slow API responses

**Diagnosis:**
1. Check trace_id in response
2. Look for slow operations in logs
3. Common bottlenecks:
   - Sheet loading (first call)
   - Multiple find_rows calls
   - Large sheet scans

**Solutions:**
1. **Sheet caching:** Already implemented; sheets are cached
2. **Reduce find_rows:** Batch operations where possible
3. **Index columns:** Ensure lookup columns have unique values

---

### Issue: High Smartsheet API usage

**Diagnosis:**
Monitor in App Insights:
- Number of API calls per function invocation
- Rate limit hits

**Solutions:**
1. Cache sheet data within function execution
2. Use batch row operations
3. Implement local caching for config values

---

## Debugging Tools

### Enable Debug Logging

```python
import logging

# In function
logging.getLogger("shared").setLevel(logging.DEBUG)
logging.getLogger("shared.smartsheet_client").setLevel(logging.DEBUG)
```

### Trace a Request

Every response includes a `trace_id`. Use it to:
1. Search logs: `grep "trace-abc123" *.log`
2. Search App Insights: `traces | where message contains "trace-abc123"`
3. Correlate across services

### Local Testing

```bash
# Run specific test
pytest tests/unit/test_models.py -v

# Run with print output
pytest -v -s

# Run with coverage
pytest --cov=shared --cov=fn_ingest_tag

# Debug with pdb
pytest --pdb
```

---

## Getting Help

### Before Asking

1. ✅ Check this troubleshooting guide
2. ✅ Search existing GitHub issues
3. ✅ Include in your question:
   - `trace_id` from response
   - Full error message
   - Steps to reproduce
   - Relevant logs

### Support Channels

| Channel | Use For | Response Time |
|---------|---------|---------------|
| GitHub Issues | Bug reports, feature requests | 1-2 days |
| Teams Channel | Quick questions | Same day |
| Email | Sensitive issues | 1 day |

### Escalation Path

```
1. Self-service (this guide, docs)
       ↓
2. Team channel / peer help
       ↓
3. GitHub issue
       ↓
4. Tech lead escalation
```

---

## Related Documentation

| Document | Description |
|----------|-------------|
| [Error Codes](../reference/error_codes.md) | Complete error reference |
| [API Reference](../reference/api_reference.md) | API documentation |
| [Setup Guide](../setup_guide.md) | Environment setup |

---

<p align="center">
  <a href="../reference/error_codes.md">🚨 Error Code Reference →</a>
</p>
