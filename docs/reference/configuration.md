# ⚙️ Configuration Reference

> **Document Type:** Reference | **Version:** 1.0.0 | **Last Updated:** 2026-01-08

This document provides a complete reference of all configuration options in the Ducts Manufacturing Inventory Management System.

---

## Table of Contents

1. [Environment Variables](#environment-variables)
2. [Config Sheet Values](#config-sheet-values)
3. [Function Settings](#function-settings)
4. [Local Development](#local-development)

---

## Environment Variables

### Required Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `SMARTSHEET_API_KEY` | Smartsheet API access token | `abc123...` |
| `SMARTSHEET_WORKSPACE_ID` | Target workspace ID | `1234567890123456` |
| `SMARTSHEET_BASE_URL` | API base URL | `https://api.smartsheet.eu/2.0` |

### Optional Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `LOG_LEVEL` | Logging verbosity | `INFO` |
| `FUNCTIONS_WORKER_RUNTIME` | Azure Functions runtime | `python` |
| `AzureWebJobsStorage` | Storage connection | Required for Azure |

### Webhook Variables (function_adapter)

| Variable | Description | Example |
|----------|-------------|---------|
| `WEBHOOK_CALLBACK_URL` | Public URL for Smartsheet webhook callbacks | `https://my-func.azurewebsites.net/api/webhooks/receive` |
| `SERVICEBUS_CONNECTION` | Azure Service Bus connection string | `Endpoint=sb://...` |
| `SERVICEBUS_QUEUE_NAME` | Queue name for events | `smartsheet-events` |

### Setting Environment Variables

#### Local Development (local.settings.json)

```json
{
  "IsEncrypted": false,
  "Values": {
    "AzureWebJobsStorage": "UseDevelopmentStorage=true",
    "FUNCTIONS_WORKER_RUNTIME": "python",
    "SMARTSHEET_API_KEY": "your_api_key_here",
    "SMARTSHEET_WORKSPACE_ID": "your_workspace_id_here",
    "SMARTSHEET_BASE_URL": "https://api.smartsheet.eu/2.0"
  }
}
```

#### Azure (Application Settings)

1. Navigate to Azure Portal → Function App → Configuration
2. Add each variable as Application Setting
3. Click "Save" and restart the function app

---

## Workspace Manifest

The application relies on a `workspace_manifest.json` file to map logical names (in code) to immutable Smartsheet IDs.

### Manifest File Structure

```json
{
  "_meta": {
    "version": "1.0.0",
    "generated_at": "2026-01-08T12:00:00",
    "workspace_id": 123456789
  },
  "sheets": {
    "TAG_REGISTRY": {
      "id": 123456,
      "name": "02 Tag Sheet Registry",
      "folder": "02_TAG_SHEET_REGISTRY",
      "columns": {
        "FILE_HASH": { "id": 987654, "name": "File Hash", "type": "TEXT_NUMBER" },
        "TAG_ID": { "id": 987655, "name": "Tag ID", "type": "TEXT_NUMBER" }
      }
    }
  }
}
```

### Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `SMARTSHEET_MANIFEST_PATH` | Path to manifest file | `workspace_manifest.json` (root/parent) |

### Managing the Manifest

The manifest should be treated as **infrastructure-as-code**.

1.  **Generation**: Run the workspace helper script (or fetch script) to generate it.
2.  **Commit**: Commit the manifest to the repository.
3.  **Deploy**: Ensure the manifest is deployed with the functions.
4.  **Immutability**: Do not edit IDs manually. If the workspace structure changes, regenerate the manifest.

---

## Config Sheet Values

All business configuration is stored in the `00a Config` sheet, making it editable without code changes.

### Sequence Counters

| config_key | Description | Default | Usage |
|------------|-------------|---------|-------|
| `seq_tag` | Tag ID counter | `0` | Generates TAG-0001, TAG-0002, etc. |
| `seq_exception` | Exception ID counter | `0` | Generates EX-0001, EX-0002, etc. |
| `seq_allocation` | Allocation ID counter | `0` | Generates ALLOC-0001, etc. |
| `seq_consumption` | Consumption ID counter | `0` | Generates CON-0001, etc. |
| `seq_delivery` | Delivery ID counter | `0` | Generates DO-0001, etc. |
| `seq_nesting` | Nesting ID counter | `0` | Generates NEST-0001, etc. |
| `seq_remnant` | Remnant ID counter | `0` | Generates REM-0001, etc. |
| `seq_filler` | Filler ID counter | `0` | Generates FILL-0001, etc. |
| `seq_txn` | Transaction ID counter | `0` | Generates TXN-0001, etc. |

### Business Rules

| config_key | Description | Default | Unit |
|------------|-------------|---------|------|
| `min_remnant_area_m2` | Minimum area for usable remnant | `0.5` | sqm |
| `variance_tolerance_pct` | Max variance before exception | `2.0` | % |
| `consumption_tolerance_pct` | Allowed overconsumption | `5.0` | % |
| `remnant_value_fraction` | Value multiplier for remnants | `0.7` | - |
| `parser_version_current` | Current nesting file parser | `1.0.0` | - |

### Time Configuration

| config_key | Description | Default |
|------------|-------------|---------|
| `t1_cutoff_time_local` | Daily T-1 planning cutoff | `18:00` |
| `t1_cutoff_timezone` | Timezone for cutoff | `Asia/Dubai` |
| `allocation_expiry_minutes` | Allocation validity period | `720` (12 hours) |

### Shift Configuration

| config_key | Description | Default |
|------------|-------------|---------|
| `shift_morning_start` | Morning shift start | `07:00` |
| `shift_morning_end` | Morning shift end | `15:00` |
| `shift_evening_start` | Evening shift start | `15:00` |
| `shift_evening_end` | Evening shift end | `23:00` |

### Machine Configuration

| config_key | Description | Default | Unit |
|------------|-------------|---------|------|
| `vacuum_bed_length_mm` | Vacuum bed length | `6000` | mm |
| `vacuum_bed_width_mm` | Vacuum bed width | `3200` | mm |

### Truck Capacity

| config_key | Description | Default | Unit |
|------------|-------------|---------|------|
| `truck_capacity_10ton_m2` | 10-ton truck capacity | `180` | sqm |
| `truck_capacity_3ton_m2` | 3-ton truck capacity | `60` | sqm |

### SLA Configuration

| config_key | Description | Default | Unit |
|------------|-------------|---------|------|
| `sla_exception_critical_hours` | Critical SLA | `4` | hours |
| `sla_exception_high_hours` | High SLA | `24` | hours |
| `approval_required_min_value_aed` | Approval threshold | `50000` | AED |

### Adding Config Values

To add configuration to the Config sheet:

1. Open `00a Config` in Smartsheet
2. Add a new row with:
   - `config_key`: The key name (snake_case)
   - `config_value`: The value (as string)
   - `effective_from`: Date when config takes effect
   - `changed_by`: Your username

Example:
```
| config_key           | config_value | effective_from | changed_by |
|----------------------|--------------|----------------|------------|
| min_remnant_area_m2  | 0.5          | 2026-01-08     | admin      |
```

---

## Function Settings

### host.json

Azure Functions host configuration:

```json
{
  "version": "2.0",
  "logging": {
    "logLevel": {
      "default": "Information"
    }
  },
  "extensions": {
    "http": {
      "responseTimeout": "00:05:00"
    }
  }
}
```

| Setting | Description | Default |
|---------|-------------|---------|
| `responseTimeout` | HTTP timeout | 5 minutes |
| `logLevel.default` | Logging level | Information |

### function.json (per function)

Example for `fn_ingest_tag`:

```json
{
  "scriptFile": "__init__.py",
  "bindings": [
    {
      "authLevel": "function",
      "type": "httpTrigger",
      "direction": "in",
      "name": "req",
      "methods": ["post"],
      "route": "tags/ingest"
    },
    {
      "type": "http",
      "direction": "out",
      "name": "$return"
    }
  ]
}
```

| Setting | Description | Options |
|---------|-------------|---------|
| `authLevel` | Authentication requirement | `anonymous`, `function`, `admin` |
| `methods` | Allowed HTTP methods | `["get", "post", ...]` |
| `route` | URL path | Custom route |

### pytest.ini

Test configuration:

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
markers =
    unit: Unit tests
    integration: Integration tests
    e2e: End-to-end tests
    acceptance: Acceptance criteria tests
    slow: Slow running tests
addopts = -v --tb=short
```

---

## Local Development

### Development Setup

1. Create `local.settings.json` in `functions/` directory
2. Add environment variables as shown above
3. Never commit this file (it's in `.gitignore`)

### Switching Environments

For different environments, maintain separate credential files:

```
functions/
├── local.settings.json          # Current active config
├── local.settings.dev.json      # DEV credentials
├── local.settings.uat.json      # UAT credentials
└── local.settings.prod.json     # PROD credentials (secure!)
```

To switch:
```bash
cp local.settings.uat.json local.settings.json
```

### Debugging Configuration

Enable verbose logging:

```json
{
  "IsEncrypted": false,
  "Values": {
    "LOG_LEVEL": "DEBUG",
    ...
  }
}
```

### Rate Limiting for Development

The Smartsheet client includes built-in rate limiting:

| Setting | Value | Purpose |
|---------|-------|---------|
| Requests/minute | 290 | Under 300 limit |
| Retry attempts | 3 | For transient errors |
| Base delay | 0.5s | Initial backoff |
| Max delay | 30s | Maximum backoff |

---

## Configuration Best Practices

### Security

1. **Never commit secrets** to version control
2. **Use Azure Key Vault** for production secrets
3. **Rotate API keys** regularly
4. **Minimum permissions** for service accounts

### Change Management

1. **Document changes** in `changed_by` field
2. **Set future dates** for `effective_from` to plan changes
3. **Review config changes** before production
4. **Keep changelog** of configuration changes

### Validation

Before deploying config changes:

```python
# Validate config values
from shared.sheet_config import ConfigKey, DEFAULT_CONFIG

def validate_config(client):
    """Validate all required config keys exist."""
    for key in ConfigKey:
        value = client.get_config_value(key.value)
        if value is None:
            print(f"Missing config: {key.value}")
            print(f"Default: {DEFAULT_CONFIG.get(key)}")
```

---

## Related Documentation

| Document | Description |
|----------|-------------|
| [Setup Guide](../setup_guide.md) | Development setup |
| [Data Dictionary](./data_dictionary.md) | Data models |
| [Config Values](../../config_values.md) | Initial config entries |

---

<p align="center">
  <a href="./error_codes.md">🚨 Error Code Reference →</a>
</p>
