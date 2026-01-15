# Smartsheet Webhook Adapter

## Overview

The **Smartsheet Webhook Adapter** is an Azure Functions application that replaces Power Automate for Smartsheet EU integrations. It receives real-time events from Smartsheet webhooks, filters them, and queues them for processing.

**Why we built this:**
- Power Automate doesn't support Smartsheet EU region
- Need real-time event-driven automation for manufacturing workflows
- Scalable, cost-effective, and maintainable solution

---

## Architecture

```
┌─────────────────┐     ┌──────────────────────┐     ┌─────────────────┐     ┌──────────────────┐
│   Smartsheet    │────▶│  fn_webhook_receiver │────▶│  Service Bus    │────▶│ fn_event_processor│
│   (EU Region)   │     │  (HTTP Triggered)    │     │  Queue          │     │ (Queue Triggered) │
└─────────────────┘     └──────────────────────┘     └─────────────────┘     └────────┬─────────┘
                                  │                                                   │
                                  ▼                                                   │ HTTP POST
                        ┌─────────────────┐                                           ▼
                        │   SQL Database  │                                  ┌──────────────────┐
                        │   (event_log)   │                                  │  Core Functions  │
                        └─────────────────┘                                  │  (process-row)   │
                                                                             └──────────────────┘
```

---

## Components

### 1. `fn_webhook_receiver` (HTTP Triggered)
**Purpose:** Receives webhook callbacks from Smartsheet

**What it does:**
1. Handles verification challenges (required for webhook registration)
2. Parses incoming events
3. **Filters events** (only `row.*` and `attachment.*` - skips cell/sheet level noise)
4. Checks if event already processed (idempotency via SQL)
5. Enqueues valid events to Service Bus

**Endpoint:** `https://duct-smartsheet-adapter-*.azurewebsites.net/api/webhook/smartsheet`

**Authentication:** Anonymous (Smartsheet requires public endpoint)

---

### 2. `fn_event_processor` (Service Bus Triggered)
**Purpose:** Forwards events to core functions app with retry logic

**What it does:**
1. Reads messages from `event-main` queue
2. Forwards ALL events to single endpoint: `/api/events/process-row`
3. **Retry with exponential backoff** on transient failures
4. Failed messages go to Dead Letter Queue (DLQ) after max retries
5. Zero business logic - core functions handle all routing

**Payload sent to core functions:**
```json
{
  "event_id": "sm_12345_20260114_0",
  "source": "WEBHOOK_ADAPTER",
  "sheet_id": "5094137684690820",
  "row_id": "5414475226810244",
  "action": "CREATED",
  "object_type": "row",
  "actor_id": "8428292257671043",
  "timestamp_utc": "2026-01-14T09:42:57.496+00:00",
  "trace_id": "trace-291994349509"
}
```

---

### 3. `fn_webhook_admin` (HTTP Triggered)
**Purpose:** Administrative endpoints for managing webhooks

**Endpoints:**
- `POST /api/webhooks/register` - Register a new webhook for a sheet
- `GET /api/webhooks` - List all webhooks
- `DELETE /api/webhooks/{id}` - Delete a webhook
- `POST /api/webhooks/refresh` - Re-enable all webhooks

**Authentication:** Function-level key required

---

## Monitored Sheets

| Sheet Name | Logical Key | What We Watch |
|------------|-------------|---------------|
| 02 Tag Sheet Registry | `TAG_REGISTRY` | STATUS, attachments |
| 02h Tag Sheet Staging | `TAG_SHEET_STAGING` | New rows (form entries) |
| 01 LPO Master LOG | `LPO_MASTER` | LPO_STATUS, PO_QUANTITY_SQM |
| 01h LPO Ingestion | `LPO_INGESTION` | New rows (form entries) |
| 03 Production Planning | `PRODUCTION_PLANNING` | STATUS, MACHINE_ASSIGNED |
| 03h Production Planning Staging | `PRODUCTION_PLANNING_STAGING` | New rows (form entries) |
| Exception Log | `EXCEPTION_LOG` | STATUS, RESOLUTION_ACTION |

---

## How to Test

### Test 1: Webhook Verification
```bash
curl -X POST "https://duct-smartsheet-adapter-*.azurewebsites.net/api/webhook/smartsheet" \
  -H "Smartsheet-Hook-Challenge: test-123" \
  -d '{"challenge": "test-123"}'
```
**Expected:** `{"smartsheetHookResponse": "test-123"}`

### Test 2: End-to-End Flow
1. Go to **01h LPO Ingestion** sheet in Smartsheet
2. Add a new row with some data
3. Check Azure Portal:
   - **Service Bus** → `event-main` queue → Should have new messages
   - **SQL Database** → Query: `SELECT TOP 10 * FROM event_log ORDER BY received_at DESC`
   - **Function App** → Log stream → Should show processing

### Test 3: Check Registered Webhooks
```bash
python register_webhooks.py
```
This will list existing webhooks and register any missing ones.

---

## Key Assumptions

### 1. Smartsheet Webhooks are "Skinny"
- Webhooks only send **metadata** (rowId, columnId, eventType)
- They do **NOT** send the actual cell values
- To get values, you must call `GET /sheets/{sheetId}/rows/{rowId}`

### 2. Event Granularity
Smartsheet sends very granular events:
- 1 row creation = 1 `sheet.updated` + 1 `row.created` + N `cell.created` + attachments
- **We filter to only process `row.*` and `attachment.*` events**

### 3. Retry Behavior
- Smartsheet retries on non-200 responses
- We use Service Bus **duplicate detection** (10-minute window)
- We use SQL `event_log` for **idempotency** (check before processing)

### 4. System Actors
- Events from `automation@ducts.ae` and `system@ducts.ae` are **skipped**
- This prevents infinite loops when our system updates Smartsheet

---

## Azure Resources

| Resource | Name | Purpose |
|----------|------|---------|
| Function App | `duct-smartsheet-adapter` | Hosts the webhook functions |
| Service Bus | `smartsheetwebhook` | Message queuing |
| Queue | `event-main` | Primary event queue |
| SQL Database | `ducts_webhook_adapter` | Event logging & idempotency |
| SQL Table | `event_log` | Tracks processed events |

---

## Configuration

All configuration is in Azure Function App Settings (or `local.settings.json` for local dev):

| Setting | Description |
|---------|-------------|
| `SMARTSHEET_API_KEY` | Smartsheet API token |
| `SMARTSHEET_BASE_URL` | `https://api.smartsheet.eu/2.0` |
| `SERVICE_BUS_CONNECTION` | Service Bus connection string |
| `SERVICE_BUS_QUEUE_NAME` | `event-main` |
| `SQL_CONNECTION_STRING` | Azure SQL connection string |
| `WEBHOOK_CALLBACK_URL` | Public URL for webhook callbacks |
| `SYSTEM_ACTOR_EMAILS` | Comma-separated emails to ignore |

---

## Monitoring & Troubleshooting

### View Logs
1. Azure Portal → Function App → **Log stream** (real-time)
2. Azure Portal → Function App → **Application Insights** → Logs

### Check Event Log
```sql
-- Recent events
SELECT TOP 20 * FROM event_log ORDER BY received_at DESC;

-- Failed events
SELECT * FROM event_log WHERE status = 'FAILED';

-- Events per sheet
SELECT sheet_id, COUNT(*) FROM event_log GROUP BY sheet_id;
```

### Check Service Bus Queue
Azure Portal → Service Bus → `event-main` → **Service Bus Explorer**
- Active Messages: Waiting to be processed
- Dead Letter: Failed after max retries

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| `ODBC Driver not found` | Wrong driver version | Fixed: Using ODBC Driver 18 |
| `Invalid object name 'event_log'` | Table not created | Run migration SQL script |
| `MessagingEntityNotFound` | Queue doesn't exist | Create `event-main` queue |
| Duplicate events | Smartsheet retries | Idempotency via SQL (working) |
| Health check fails during deploy | Cold start timeout | Ignore - code still deploys |

---

## Current Status & Next Steps

### ✅ Completed
- [x] Webhook receiver with verification
- [x] Event filtering (row/attachment only)
- [x] Service Bus integration
- [x] SQL event logging
- [x] Webhook registration for all 7 sheets
- [x] System actor filtering

### 🚧 In Progress / TODO
- [ ] Implement actual processing logic in handlers
- [ ] Fetch row data from Smartsheet API when processing
- [ ] Dead Letter Queue handler (`fn_dlq_handler`)
- [ ] Exception logging to Smartsheet EXCEPTION_LOG
- [ ] Power Automate notification integration

---

## Files Structure

```
function_adapter/
├── fn_webhook_receiver/       # HTTP-triggered webhook endpoint
│   ├── __init__.py
│   └── function.json
├── fn_event_processor/        # Service Bus-triggered processor
│   ├── __init__.py
│   └── function.json
├── fn_webhook_admin/          # Admin API for webhook management
│   ├── __init__.py
│   └── function.json
├── shared/                    # Shared modules
│   ├── __init__.py
│   ├── webhook_config.py      # Sheet/column configuration
│   ├── event_log.py           # SQL database operations
│   └── service_bus.py         # Service Bus operations
├── migrations/
│   └── 001_create_event_log.sql
├── workspace_manifest.json    # Sheet/column IDs
├── local.settings.json        # Local configuration
├── host.json                  # Azure Functions config
└── requirements.txt           # Python dependencies
```

---

## Security Considerations

1. **Webhook Receiver is public** - Required for Smartsheet callbacks
2. **Admin endpoints require Function Key** - Protected from unauthorized access
3. **Credentials in Azure Key Vault** - Recommended for production
4. **SQL uses encrypted connection** - TLS enabled
5. **Service Bus uses SAS authentication** - Connection string secured

---

## Cost Estimates

| Resource | Tier | Est. Monthly Cost |
|----------|------|-------------------|
| Function App | Consumption | ~$5-15 (based on invocations) |
| Service Bus | Standard | ~$10 |
| SQL Database | Serverless | Free tier (100K vCore seconds) |
| **Total** | | **~$15-25/month** |

---

## Support & Maintenance

**Deployed to:** `duct-smartsheet-adapter` (East US)
**Last Updated:** 2026-01-14
**Created By:** AI Assistant + Human Developer

For issues, check:
1. Azure Portal → Function App → Diagnose and solve problems
2. Log stream for real-time debugging
3. SQL `event_log` table for event history
