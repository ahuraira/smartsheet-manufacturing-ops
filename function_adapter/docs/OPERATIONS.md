# Operations & Development Guide

## Local Development Setup

### Prerequisites
-   Python 3.10+
-   Azure Functions Core Tools (`npm i -g azure-functions-core-tools@4`)
-   ODBC Driver 18 for SQL Server (`sudo apt-get install msodbcsql18`)
-   Git

### 1. Clone & Install
```bash
git clone <repo-url>
cd function_adapter
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configuration
Create a `local.settings.json` file in the root directory:
```json
{
  "IsEncrypted": false,
  "Values": {
    "AzureWebJobsStorage": "UseDevelopmentStorage=true",
    "FUNCTIONS_WORKER_RUNTIME": "python",
    "SERVICE_BUS_CONNECTION": "<YOUR_SB_CONNECTION_STRING>",
    "SERVICE_BUS_QUEUE_NAME": "event-main",
    "SQL_CONNECTION_STRING": "<YOUR_SQL_CONNECTION_STRING>",
    "SMARTSHEET_API_KEY": "<YOUR_API_TOKEN>",
    "CORE_FUNCTIONS_BASE_URL": "http://localhost:7072",
    "CORE_FUNCTIONS_KEY": "dev"
  }
}
```

### 3. Run Locally
```bash
func start
```

### 4. Simulate Webhooks
Use `curl` to send a test event to your local instance:
```bash
curl -X POST http://localhost:7071/api/webhook/smartsheet \
  -H "Content-Type: application/json" \
  -d '{
    "webhookId": "test-hook",
    "scopeObjectId": "TEST_SHEET_ID",
    "events": [{"objectType": "row", "eventType": "created", "id": "123"}]
  }'
```

---

## Database Schema

The adapter uses a single table in Azure SQL for idempotency and logging.

### Table: `event_log`

| Column | Type | Description |
|--------|------|-------------|
| `event_id` | VARCHAR(100) | **PK**. Format: `sm_{webhookId}_{timestamp}_{index}` |
| `source` | VARCHAR(50) | 'SMARTSHEET' |
| `status` | VARCHAR(20) | `PENDING`, `COMPLETED`, `FAILED` |
| `received_at` | DATETIME2 | UTC timestamp of ingestion |
| `payload` | NVARCHAR(MAX) | Raw JSON payload (optional/truncated) |
| `trace_id` | VARCHAR(50) | Correlation ID for logs |

**Indexes:**
-   `IX_event_log_status`: For querying failed/pending events.
-   `IX_event_log_received_at`: For time-based auditing.

---

## Monitoring & Troubleshooting

### Log Queries (Application Insights)

**Find failed processing attempts:**
```kusto
traces
| where message startswith "Core function failed"
| project timestamp, operation_Id, message
```

**Trace an event end-to-end:**
```kusto
traces
| where customDimensions.trace_id == "trace-a1b2c3d4e5"
| order by timestamp asc
```

### Common Issues

1.  **Duplicate Events:**
    -   *Symptom:* Same row processed twice.
    -   *Check:* `SELECT count(*) FROM event_log WHERE row_id = '...'`. If count > 1, check if `event_id` is different. If `event_id` is same, the DB constraint failed (unlikely). If different, Smartsheet sent it as two distinct events.

2.  **Service Bus Dead Letters:**
    -   *Symptom:* Messages in `event-main/$DeadLetterQueue`.
    -   *Action:* Check the `DeadLetterReason`. Usually means `fn_event_processor` failed 10 times (Core App down or bug).

3.  **Authentication Errors:**
    -   *Symptom:* 401/403 in logs.
    -   *Action:* Rotate `CORE_FUNCTIONS_KEY` or `SMARTSHEET_API_KEY`.
