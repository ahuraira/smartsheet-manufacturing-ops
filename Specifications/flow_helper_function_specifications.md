# Specification: Azure Functions that Serve Data to Power Automate

**Goal:** A small set of simple, robust, production-grade HTTP functions that provide everything Power Automate needs for the Teams adaptive-card loop (pending items, aggregated allocation, submission intake, confirmation, stock snapshot, exception creation, submission status).

Follow **SOTA** principles (secure, observable, idempotent, concurrency-safe) and **KISS**: keep each function responsibility tiny and deterministic. The flow control and UX live in Power Automate; the Functions are *stateless compute + authoritative logic*.

> [!NOTE]
> **Data Layer:** All persistent data lives in **Smartsheet** sheets (via the Smartsheet API and `workspace_manifest.json`). There is no SQL database — Smartsheet is the system of record.

---

## Summary: Functions to Implement

1. `GET /api/pending-items` — list pending tag allocations + stock flag
2. `POST /api/allocations/aggregate` — return aggregated canonical materials for selected allocation(s)
3. `POST /api/submission/consumption` — accept consumption submission (idempotent)
4. `POST /api/submission/confirm` — confirm a submission previously flagged WARN (approver action)
5. `GET /api/submission/status/{submission_id}` — fetch submission status and details
6. `GET /api/stock/snapshot?plant=...` — current system stock snapshot for building stock card
7. `POST /api/submission/stock` — accept stock count submission
8. `POST /api/exception` — create an exception (used by Flow to escalate)
9. (Optional) `GET /api/allocation/{allocation_id}` — return single allocation detail (if PA needs)

Each function is an Azure Function (HTTP trigger), authenticated by **Azure Function keys** (host key or function-level key). No Azure AD or user licenses required.

---

## General Design Principles (apply to all functions)

* **Single responsibility:** each endpoint does one thing only. Keep logic small.
* **Idempotency:** clients (Power Automate) provide `submission_id` (GUID) for writes; functions must be idempotent if same `submission_id` reposted.
* **Concurrency:** writes use **Azure Queue Storage distributed locks** (`queue_lock.py` → `AllocationLock` context manager) to serialize on `allocation_id`. This prevents race conditions when multiple operators submit concurrently.
* **Data access:** all reads/writes go through the **`SmartsheetClient`** (`shared/smartsheet_client.py`), which uses the workspace manifest for ID-first sheet/column resolution. Smartsheet API rate limiting (290 req/min) is handled automatically.
* **Validation:** strict schema validation using **Pydantic** models defined in `shared/flow_models.py`. Return clear error codes and messages.
* **Error model:** return HTTP 2xx for success, 4xx for client error, 5xx for server error. Include `trace_id` in every response.
* **Observability:** structured Python logging with `trace_id` in every log line. Application Insights integration via the Azure Functions host.
* **Backpressure & retries:** client (Power Automate) should retry on transient 5xx with exponential backoff. Server protects with Azure Queue locks to avoid race conditions. The `SmartsheetClient` has built-in retry with exponential backoff for 429/5xx.
* **Payloads:** small, deterministic JSON. Keep arrays concise. Avoid nested complexity; Power Automate deals better with flat structures.
* **Testing:** unit tests (Pydantic model validation), integration tests (mocked Smartsheet API), and contract tests. Provide sample payloads.
* **Exception logging:** all v1.7.0+ endpoints create `EXCEPTION_LOG` records in their outermost catch blocks via `create_exception()`. This ensures every unhandled error is persisted to the exception sheet for audit and alerting.

---

## Data Layer: Smartsheet Sheets (reference)

Primary sheets involved (defined in `workspace_manifest.json`, accessed via logical names in `shared/logical_names.py`):

| Logical Name | Purpose |
|---|---|
| `TAG_REGISTRY` | Tag sheet registry (status, planning data) |
| `LPO_MASTER` | LPO master list |
| `ALLOCATION_LOG` | Allocation records (status, quantities, dates) |
| `CONSUMPTION_LOG` | Consumption submission records |
| `STOCK_SNAPSHOT` | Stock count submissions |
| `EXCEPTION_LOG` | Exception/escalation records |
| `MATERIAL_MAPPING` | Canonical material mapping |
| `NESTING_LOG` | Nesting/cut session records |
| `MARGIN_APPROVAL_LOG` | Margin approval records (status, metrics, card JSON) |

**Important access patterns:**

* **Reads:** `client.list_rows(Sheet.ALLOCATION_LOG)` — returns all rows as dicts. Filter in memory (Smartsheet doesn't support complex server-side queries).
* **Writes:** `client.add_row(Sheet.CONSUMPTION_LOG, row_data)` or `client.update_row(...)`.
* **Concurrency guard:** before writing to allocation-related sheets, acquire a distributed lock:

  ```python
  from shared.queue_lock import AllocationLock

  with AllocationLock(allocation_ids, trace_id=trace_id) as lock:
      if not lock.success:
          return 409, {"error": "LOCK_TIMEOUT", "trace_id": trace_id}
      # safe to read + write
  ```

* **Optimistic concurrency:** Smartsheet returns `SmartsheetSaveCollisionError` (HTTP 409) on conflicting row updates — the client handles this via retry.

---

## Function-by-Function Spec

### 1) `GET /api/pending-items`

**Purpose:** Return pending allocations (tags) for the user/plant so the initial selection card can present choices.

**Auth:** Azure Function key (query param `code` or `x-functions-key` header)

**Query params:**

* `plant` (required)
* `user` (string, optional) — used to filter personal queue (if needed)
* `shift` (optional)
* `max` (optional, default 50, capped at 100)

**Response (200):**

```json
{
  "trace_id":"<guid>",
  "timestamp":"2026-02-20T08:00:00Z",
  "pending_tags":[
    {"allocation_id":"A-123","tag_id":"TAG-1001","brief":"TAG-1001 - Allocation A-123","alloc_date":"2026-02-20","alloc_qty":50.0}
  ],
  "allow_stock_submission": true
}
```

**Errors:**

* 400 Bad Request — missing plant
* 500 Server Error — include `trace_id`

**Implementation notes:**

* Query `ALLOCATION_LOG` sheet via `client.list_rows(Sheet.ALLOCATION_LOG)`.
* Filter in memory: status `Submitted` or `Approved`, date is today or yesterday (configurable).
* Cap scan to 200 rows for performance.
* Response model: `PendingItemsResponse` (from `shared/flow_models.py`).

---

### 2) `POST /api/allocations/aggregate`

**Purpose:** Given a list of allocation IDs or tag IDs, return aggregated required canonical material lines (allocated minus already consumed).

**Payload:**

```json
{
  "allocation_ids": ["A-123","A-124"],
  "trace_id":"<guid>"
}
```

**Response (200):**

```json
{
 "trace_id":"...",
 "allocations":[
   {"allocation_id":"A-123","tag_id":"TAG-1001"},
   {"allocation_id":"A-124","tag_id":"TAG-1002"}
 ],
 "aggregated_materials":[
   {"canonical_code":"CAN_TAPE_AL","allocated_qty":110.55,"already_consumed":10.0,"remaining_qty":100.55,"uom":"m"},
   {"canonical_code":"CAN_PANEL_20","allocated_qty":38.06,"already_consumed":0.0,"remaining_qty":38.06,"uom":"m2"}
 ]
}
```

**Errors:** 400 if no ids; 404 allocation unknown; 500 on server error.

**Implementation notes:**

* Read allocation rows from `ALLOCATION_LOG` and consumption rows from `CONSUMPTION_LOG`.
* Compute `remaining_qty = max(0, allocated - cumulative_consumed)` per canonical code.
* For multiple allocations mapping to the same `canonical_code`, sum across allocations → one aggregated row.
* Request model: `AllocationAggregateRequest`; response model: `AllocationAggregateResponse`.

---

### 3) `POST /api/submission/consumption`

**Purpose:** Accept a user-submitted consumption payload (one or more allocations/tags). Must be idempotent and validate.

**Payload (required):**

```json
{
 "submission_id":"uuid",
 "user":"user@company",
 "plant":"PLANT-A",
 "shift":"MORNING",
 "allocation_ids":["A-123","A-124"],
 "lines":[
   {"canonical_code":"CAN_TAPE_AL","allocated_qty":110.55,"actual_qty":105.0,"uom":"m","remarks":""},
   {"canonical_code":"CAN_PANEL_20","allocated_qty":38.06,"actual_qty":38.06,"uom":"m2","remarks":""}
 ],
 "trace_id":"guid",
 "source":"TEAMS"
}
```

**Response:**

* `200 OK` when accepted and no exceptions:

```json
{ "status":"OK", "processed_submission_id":"<guid>", "warnings":[], "errors":[], "trace_id":"..." }
```

* `200 OK` + `warnings` if variances found that need approval:

```json
{ "status":"WARN", "processed_submission_id":"<guid>", "warnings":[{"code":"VARIANCE_WARN","message":"Tape variance 12%"}], "trace_id":"..." }
```

* `409 Conflict` if submission_id already exists with different payload (client retry protection).
* `400 Bad Request` for validation errors.
* `500 Server Error` for unexpected errors.

**Core logic (server):**

1. Validate payload with `ConsumptionSubmission` Pydantic model.
2. **Idempotency:** search `CONSUMPTION_LOG` sheet for existing `submission_id`.
   * If exists with same payload → return existing record with 200.
   * If exists with different payload → return 409.
3. **Acquire distributed lock** via `AllocationLock(allocation_ids)`.
4. For each canonical line:
   * Read current cumulative consumed from `CONSUMPTION_LOG` for the affected allocation_ids.
   * Compute `new_total = current_consumed + actual_qty`.
   * If `new_total > allocated + tolerance` → flag error.
5. If any **blocking errors**: release lock, return 400 with error details.
6. If no blocking errors: add rows to `CONSUMPTION_LOG` sheet and update allocation status.
7. Release lock.
8. Evaluate **variance thresholds** (variance is calculated using **system allocation qty** from `aggregate_materials()`, not the user-submitted `allocated_qty`):
   * If variance > WARN threshold (5–10%) → return `WARN`, add row to `EXCEPTION_LOG`.
   * If variance > CRITICAL threshold (>10%) → block and create exception.
   * If all OK → return `OK`.
9. (Future) Push event to **Azure Queue Storage** for downstream processing.

**Models:** `ConsumptionSubmission` (request), `SubmissionResult` (response — includes `processed_submission_id: Optional[str]`).

---

### 4) `POST /api/submission/confirm`

**Purpose:** Approver confirms a WARN submission, enabling final posting.

**Payload:**

```json
{ "processed_submission_id":"guid", "approver":"manager@company", "decision":"APPROVE", "notes":"approved due to remnant", "trace_id":"guid" }
```

**Response:** 200 OK + updated submission status.

**Server logic:**

* **Acquire distributed lock** via `AllocationLock(processed_submission_id)` before reading or updating consumption rows.
* Load submission from `CONSUMPTION_LOG`; must be in `PENDING_APPROVAL` status.
* If APPROVE: update status to `COMPLETED`.
* If REJECT: update status to `REJECTED`.
* Update corresponding `EXCEPTION_LOG` row.
* **Log user action** via `log_user_action()` to the User Action Log.

**Model:** `SubmissionConfirmRequest` (request).

---

### 5) `GET /api/submission/status/{submission_id}`

**Purpose:** Let Flow poll submission status and show to user.

**Response:**

```json
{ "submission_id":"...", "status":"OK|WARN|PENDING_APPROVAL|ERROR|COMPLETED", "warnings":[], "errors":[], "created_at":"..." }
```

**Model:** `SubmissionStatusResponse`.

---

### 6) `GET /api/stock/snapshot?plant=...`

**Purpose:** Return canonical physical stock snapshot to populate stock card.

**Response:**

```json
{
 "trace_id":"...",
 "plant":"PLANT-A",
 "snapshot_time":"...",
 "lines":[
   {"canonical_code":"CAN_TAPE_AL","system_physical_closing":120.5,"uom":"m","last_count":"2026-02-19"}
 ]
}
```

**Implementation notes:** read latest rows from `STOCK_SNAPSHOT` sheet filtered by plant. Uses manifest-based column lookups (via `workspace_manifest.json`) instead of hardcoded column name strings.

**Model:** `StockSnapshotResponse`.

---

### 7) `POST /api/submission/stock`

**Purpose:** Accept stock counts (similar to consumption submission).

**Payload:**

```json
{
 "submission_id":"uuid",
 "user":"user@company",
 "plant":"PLANT-A",
 "shift":"MORNING",
 "snapshot_date":"2026-02-20",
 "lines":[{"canonical_code":"CAN_TAPE_AL","counted_qty":121.0,"uom":"m"}],
 "trace_id":"..."
}
```

**Logic:** validate with `StockSubmission` model, idempotent via `submission_id`, add rows to `STOCK_SNAPSHOT` sheet, compute variances against last known system stock, create exceptions in `EXCEPTION_LOG` for out-of-tolerance variance, return OK/WARN.

---

### 8) `POST /api/exception`

**Purpose:** Create an exception programmatically (Flow can call to escalate or create manual exceptions).

**Payload:**

```json
{ "type":"CONSUMPTION_VARIANCE", "reference":"submission_id or allocation_id", "severity":"HIGH", "note":"escalate", "created_by":"user@company", "trace_id":"..." }
```

**Response:** `exception_id` + `trace_id`.

**Model:** `ExceptionCreateRequest` (request), `ExceptionCreateResponse` (response).

---

### 9) `GET /api/allocation/{allocation_id}`

**Purpose:** Single allocation detail (optional helper used by Power Automate if needed).

**Response:** allocation lines including per-canonical allocated & remaining quantities.

**Model:** `AllocationDetailResponse`.

---

## Security & Auth

* **Auth:** Azure Function keys. Power Automate calls functions with the function/host key in the `x-functions-key` header or `code` query parameter. No Azure AD required.
* **Smartsheet API:** authenticated via `SMARTSHEET_API_KEY` environment variable (stored in Azure Function App Settings / Key Vault for production).
* **Azure Storage:** accessed via `AzureWebJobsStorage` connection string (for Queue locks and Blob Storage).
* **Power Automate flows:** triggered via HTTP POST to pre-configured flow URLs (stored as environment variables: `POWER_AUTOMATE_CREATE_FOLDERS_URL`, `POWER_AUTOMATE_UPLOAD_FILES_URL`, etc.).
* **Secrets management:** for production, store secrets in Azure Key Vault and reference via App Settings.

---

## Concurrency & Distributed Locking

* **Azure Queue Storage locks** (`shared/queue_lock.py`):
  * Lock queue: `allocation-locks`
  * Mechanism: send message with `allocation_id` as content; visibility timeout acts as lock duration (default 60s, max 5min).
  * Release: delete message. If process crashes, lock auto-releases after timeout.
  * Context manager: `AllocationLock(allocation_ids, timeout_ms, trace_id)`.
* **Smartsheet optimistic concurrency:** the API returns 409 on save collisions; `SmartsheetClient` retries automatically with backoff.
* There is **no SQL transaction** — atomicity is achieved by lock-then-write-then-release pattern on Smartsheet rows.

---

## Logging, Tracing & Monitoring

* Use structured Python `logging` with `trace_id` in every log line.
* Application Insights collects logs automatically via the Azure Functions host (`host.json`).
* Log key fields: `trace_id`, `user`, `allocation_ids`, `submission_id`, `plant`, `shift`.
* Create Azure Monitor alerts for:
  * Exception count > threshold
  * API error rate > 1%
  * Function execution duration > 30s

---

## Error Codes & Consistent Responses

Design a simple error structure:

```json
{
 "error":{
   "code":"VARIANCE_HIGH",
   "message":"Tape variance 12% exceeds warn threshold 10%",
   "details":[{"field":"CAN_TAPE_AL","allocated":100,"actual":112}]
 },
 "trace_id":"..."
}
```

Map common error codes (defined in `shared/flow_models.py`):

* `INVALID_PAYLOAD` (400)
* `ALLOCATION_NOT_FOUND` (404)
* `ALREADY_SUBMITTED` (409)
* `VARIANCE_HIGH` / `VARIANCE_WARN` (200 WARN)
* `VARIANCE_CRITICAL` (400/409)
* `INSUFFICIENT_STOCK` (400/409)
* `LOCK_TIMEOUT` (409)
* `SERVER_ERROR` (500)

---

## Idempotency Patterns

* Require client `submission_id` GUID for every write (consumption or stock). If missing, reject with 400.
* On POST:

  * If a record exists with same `submission_id`, return existing state with 200 (idempotent).
  * If different payload sent with same `submission_id`, return 409 and include existing record.

---

## Testing & Acceptance Criteria

**Unit tests**

* Pydantic model validation for each request/response model.
* Lock acquire/release flow (mock Azure Queue).
* Idempotency tests: repost same submission_id and confirm no duplicate writes.

**Integration tests**

* Flow simulation: call `GET /pending-items` → select tags → `allocations/aggregate` → post `submission/consumption` → verify Smartsheet rows updated.
* WARN path: deliberately submit variance > warn threshold and confirm exception created and status `PENDING_APPROVAL`.

**Load tests**

* Simulate concurrent submissions for same allocation IDs; verify distributed lock prevents race conditions.

**Acceptance**

* All happy flows complete under 2s for reads; writes complete under 5s (inclusive of Smartsheet API latency).
* No duplicate rows for duplicate `submission_id`.
* WARNs trigger exceptions and produce approval flow.

---

## Deployment & Infra Notes

* All functions are grouped in a single Azure Function App (Python, Consumption Plan).
* Dependencies: `azure-functions`, `requests`, `pydantic`, `smartsheet-python-sdk`, `azure-storage-queue`.
* Environment variables (via `local.settings.json` locally, App Settings in Azure):
  * `SMARTSHEET_API_KEY` — Smartsheet API bearer token
  * `SMARTSHEET_BASE_URL` — `https://api.smartsheet.eu/2.0`
  * `SMARTSHEET_WORKSPACE_ID` — workspace ID for manifest resolution
  * `AzureWebJobsStorage` — Azure Storage connection string (used for queue locks)
  * `POWER_AUTOMATE_*_URL` — Power Automate flow trigger URLs
  * `FLOW_FIRE_AND_FORGET` / `FLOW_CONNECT_TIMEOUT` / `FLOW_READ_TIMEOUT` / `FLOW_MAX_RETRIES` — PA client config
* Use Azure Key Vault for production secrets.
* Functions are deployed via `func azure functionapp publish` (see `deploy.ps1`).

---

## Example: Pseudocode for `POST /api/submission/consumption`

```python
def post_consumption(req):
    payload = ConsumptionSubmission(**req.json())  # Pydantic validation
    trace_id = payload.trace_id or generate_trace_id()

    # 1. Idempotency check
    client = get_smartsheet_client()
    existing = find_submission_in_sheet(client, payload.submission_id)
    if existing:
        return 200, existing_result(existing)

    # 2. Acquire distributed lock
    with AllocationLock(payload.allocation_ids, trace_id=trace_id) as lock:
        if not lock.success:
            return 409, {"error": "LOCK_TIMEOUT", "trace_id": trace_id}

        # 3. Read current consumed from CONSUMPTION_LOG
        for line in payload.lines:
            current_consumed = sum_consumed_for_allocations(
                client, payload.allocation_ids, line.canonical_code
            )
            new_total = current_consumed + line.actual_qty
            allocated = sum_allocated_for_canonical(
                client, payload.allocation_ids, line.canonical_code
            )
            if new_total > allocated + tolerance:
                raise ValidationError('INSUFFICIENT_STOCK', details)

        # 4. Write submission + lines to CONSUMPTION_LOG sheet
        add_consumption_rows(client, payload)
        update_allocation_status(client, payload.allocation_ids)

    # Lock auto-released by context manager

    # 5. Evaluate variance thresholds
    warnings = check_variance_thresholds(payload)
    if warnings:
        add_exception_rows(client, payload, warnings)
        return 200, {"status": "WARN", "warnings": warnings, "trace_id": trace_id}

    return 200, {"status": "OK", "processed_submission_id": payload.submission_id, "trace_id": trace_id}
```

---