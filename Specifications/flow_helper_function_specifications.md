# Specification: Azure Functions that Serve Data to Power Automate

**Goal:** a small set of simple, robust, production-grade HTTP functions that provide everything Power Automate needs for the Teams adaptive-card loop (pending items, aggregated allocation, submission intake, confirmation, stock snapshot, exception creation, submission status).
Follow **SOTA** principles (secure, observable, idempotent, transactional) and **KISS**: keep each function responsibility tiny and deterministic. The flow control and UX live in Power Automate; the Functions are *stateless compute + authoritative logic*.

---

## Summary: functions to implement

1. `GET /api/pending-items` — list pending tag allocations + stock flag
2. `POST /api/allocations/aggregate` — return aggregated canonical materials for selected allocation(s)
3. `POST /api/submission/consumption` — accept consumption submission (idempotent)
4. `POST /api/submission/confirm` — confirm a submission previously flagged WARN (approver action)
5. `GET /api/submission/status/{submission_id}` — fetch submission status and details
6. `GET /api/stock/snapshot?plant=...` — current system stock snapshot for building stock card
7. `POST /api/submission/stock` — accept stock count submission
8. `POST /api/exception` — create an exception (used by Flow to escalate)
9. (Optional) `GET /api/allocation/{allocation_id}` — return single allocation detail (if PA needs)

Each function is an Azure Function (HTTP trigger), authenticated by Azure AD (managed identity/service principal); no user licenses required on Power Automate to *respond* to cards the Flow posts.

---

## General design principles (global, apply to all functions)

* **Single responsibility:** each endpoint does one thing only. Keep logic small.
* **Idempotency:** clients (Power Automate) provide `submission_id` (GUID) for writes; functions must be idempotent if same `submission_id` reposted.
* **Atomicity / Concurrency:** all writes use a DB transaction and concurrency checks (compare cumulative consumed vs allocated inside a single transaction). If multi-step, use SQL transaction or `sp_getapplock` to serialize on `allocation_id`.
* **Auth & security:** Azure AD OAuth2 - require token from Flow/service principal. Validate `scp` or app role. Use managed identity + Key Vault for DB connection strings.
* **Validation:** strict schema validation (JSON Schema or model binding). Return clear error codes and messages.
* **Error model:** return HTTP 2xx for success, 4xx for client error, 5xx for server error. Include `trace_id` in every response.
* **Observability:** structured logging (App Insights), metrics (mapping_count, submission_count, exceptions_created), distributed tracing with `trace_id`.
* **Caching:** use Redis/Memory for read-heavy lookup (pending items, allocation aggregation); TTL short (30–120s). Invalidate when relevant updates occur.
* **Backpressure & retries:** client (Power Automate) should retry on transient 5xx with exponential backoff. Server protects with DB locks to avoid race conditions.
* **Payloads:** small, deterministic JSON. Keep arrays concise. Avoid nested complexity; Power Automate deals better with flat structures.
* **Testing:** unit tests, contract tests, integration tests with test DB, load tests for peak usage. Provide sample payloads and Postman collection.

---

## Database interactions / constraints (reference)

Primary tables involved (exist in your model): `allocation` / `allocation_line`, `bom_line`, `consumption_submission`, `consumption_line`, `stock_snapshot`, `mapping_history`, `exception_log`, `bom_snapshot_blob`. Ensure proper indexes on `allocation_id`, `tag_id`, `submission_id`, `plant+shift+date`.

Important DB patterns:

* To accept consumption safely:

  1. Begin transaction.
  2. SELECT current cumulative_consumed FROM consumption_agg WHERE allocation_id = X FOR UPDATE (or use `sp_getapplock('alloc_X')`).
  3. Validate `cumulative_consumed + sum(new) <= allocated + remnant + tolerance`.
  4. INSERT consumption lines and update aggregation table.
  5. COMMIT.
* Use `rowversion` on critical rows if needed; or use application lock to ensure correctness.

---

## Function-by-function spec

### 1) `GET /api/pending-items`

**Purpose:** Return pending allocations (tags) for the user/plant so the initial selection card can present choices.

**Auth:** Azure AD (Delegated app or Service Principal)

**Query params:**

* `plant` (required)
* `user` (string, optional) — used to filter personal queue (if needed)
* `shift` (optional)
* `max` (optional, default 50)

**Response (200):**

```json
{
  "trace_id":"<guid>",
  "timestamp":"2026-02-20T08:00:00Z",
  "pending_tags":[
    {"allocation_id":"A-123","tag_id":"TAG-1001","brief":"TAG-1001 - 5 ducts - LPO-55","alloc_date":"2026-02-20","alloc_qty":50.0},
    ...
  ],
  "allow_stock_submission": true
}
```

**Errors:**

* 401 Unauthorized — invalid token
* 400 Bad Request — missing plant
* 500 Server Error — include `trace_id`

**Implementation notes:**

* Query `allocation` table: status `ALLOCATED` and `alloc_date = today` or `alloc_date >= today-1` (configurable).
* Include `alloc_qty` summary, and `already_consumed` optionally in aggregate.
* Cache results for 30–60 seconds.

---

### 2) `POST /api/allocations/aggregate`

**Purpose:** Given a list of allocation IDs or tag IDs, return aggregated required canonical material lines (allocated minus already consumed).

**Payload:**

```json
{
  "allocation_ids": ["A-123","A-124"],   // OR "tag_ids": [...]
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

* The server computes `remaining_qty = max(0, allocated - cumulative_consumed)` per canonical code over provided allocations.
* For multiple allocations that map to same canonical_code, sum allocated & consumed across allocations and present one aggregated row. This makes the consumption card simple: one row per canonical code.
* Use a single DB query with GROUP BY canonical_code over `allocation_line` joined with `consumption_agg` (or compute sum(consumption_line) grouped).
* Cache or memoize ephemeral queries during flow run.

---

### 3) `POST /api/submission/consumption`

**Purpose:** Accept a user-submitted consumption payload (one or more allocations/tags). Must be idempotent and validate.

**Payload (required):**

```json
{
 "submission_id":"uuid",         // client-supplied idempotency key
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
{ "status":"OK", "processed_submission_id":"<guid>", "warnings":[], "errors":[] , "trace_id":"..." }
```

* `200 OK` + `warnings` if variances found that need approval:

```json
{ "status":"WARN", "processed_submission_id":"<guid>", "warnings":[{"code":"VAR_HIGH","detail":"Tape variance 12%"}], "trace_id":"..." }
```

* `409 Conflict` if submission_id already exists with different payload (client retry protection). Response should include existing `processed_submission_id`.
* `400 Bad Request` for validation errors.
* `500 Server Error` for unexpected errors.

**Core logic (server):**

1. Validate payload schema and units. Reject if missing `submission_id`.
2. Idempotency: check `consumption_submission` table for `submission_id`.

   * If exists, return existing record and 200 (idempotent).
3. Begin DB transaction.
4. For each canonical line:

   * Compute `sum(current_consumed)` for the affected allocation_ids (use select with lock).
   * Compute new cumulative = current_consumed + incoming_actual.
   * If `new_cumulative > allocated + remnant + tolerance`, **flag error** for this line (not auto-accept). Collect all errors.
5. If any **errors**: rollback and return 400 with error details (or create `exception_log` and return WARN depending on policy).
6. If no blocking errors: insert `consumption_submission` and `consumption_line` rows and update `consumption_agg` (or `allocation_line.picked_qty`) accordingly.
7. Commit transaction.
8. Evaluate `variance_thresholds`:

   * If any variance > WARN threshold but not blocking, return status `WARN`. Record submission as PENDING_APPROVAL and create an `exception_log` with `status=OPEN`.
   * If all OK, return `OK`.
9. Push event `submission_received` to Service Bus (for downstream reconciliation, reporting).

**Important:** All DB writes must be in transaction to prevent race conditions. Use application locks or `SELECT ... FOR UPDATE` equivalent.

---

### 4) `POST /api/submission/confirm`

**Purpose:** Approver confirms a WARN submission, enabling final posting.

**Payload:**

```json
{ "processed_submission_id":"guid", "approver":"manager@company", "decision":"APPROVE", "notes":"approved due to remnant", "trace_id":"guid" }
```

**Response:** 200 OK + updated submission status.

**Server logic:**

* Validate approver role and auth.
* Load submission; must be in `PENDING_APPROVAL` state.
* If APPROVE: finalize submission (set status COMPLETED), create `inventory_txn` records of type `ISSUE` or call SAP if required.
* If REJECT: set status REJECTED, possibly create a task for submitter to revise.
* Update `exception_log` and `mapping_history` if needed. Emit event.

---

### 5) `GET /api/submission/status/{submission_id}`

**Purpose:** Let Flow poll submission status and show to user.

**Response:**

```json
{ "submission_id":"...", "status":"OK|WARN|PENDING_APPROVAL|ERROR|COMPLETED", "warnings":[], "errors":[], "created_at":"..." }
```

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
   {"canonical_code":"CAN_TAPE_AL","system_physical_closing":120.5,"uom":"m","last_count":"2026-02-19"},
   ...
 ]
}
```

**Implementation notes:** read the latest `inventory_snapshot` table (SAP snapshot merge) and cache for short TTL.

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

**Logic:** validate, idempotent via `submission_id`, insert `stock_snapshot_submission` and `stock_snapshot_line`, compute variances, create exceptions for out-of-tolerance variance, return OK/WARN.

---

### 8) `POST /api/exception`

**Purpose:** Create an exception programmatically (Flow can call to escalate or create manual exceptions).

**Payload:**

```json
{ "type":"CONSUMPTION_VARIANCE", "reference":"submission_id or allocation_id", "severity":"HIGH", "note":"escalate", "created_by":"user@company", "trace_id":"..." }
```

**Response:** exception_id.

---

### 9) `GET /api/allocation/{allocation_id}`

**Purpose:** Single allocation detail (optional helper used by Power Automate if needed).

**Response:** allocation lines including per-canonical allocated & remaining quantities.

---

## Security & auth details

* **Auth:** Azure AD bearer tokens. The Power Automate flow runs as a Service Principal (app registration) or uses the Flow’s built-in connection using a system account. Functions validate the token via AAD.
* **Scopes/roles:** Use app roles (e.g., `consumption.submit`, `consumption.approve`, `allocation.read`).
* **Managed identity:** use function app managed identity to fetch keys from Key Vault (DB connection strings, Redis keys).
* **Least privilege**: the Flow’s service principal only needs to call these endpoints; approvals use manager account tokens.

---

## Caching & performance

* Cache `GET /api/pending-items` and `GET /api/allocations/aggregate` results in Redis or in-memory for 30–60s to reduce DB load.
* But **do not** cache writes. For aggregated read, use cache invalidation on allocation create/update events.
* Ensure functions have adequate timeouts (e.g., 120s) and small memory; heavy jobs (reporting, reconciliation) run elsewhere.

---

## Logging, tracing & monitoring

* Use Application Insights:

  * Track custom metrics: `consumption_submissions`, `warnings_count`, `exceptions_created`, `pending_items_count`.
  * Emit trace logs with `trace_id` and helpful fields (`user`, `allocation_ids`, `submission_id`).
* Correlate logs with Service Bus events using trace_id.
* Create alerts for:

  * Exception queue length > threshold
  * API error rate > 1%
  * Critical DB lock wait > threshold

---

## Error codes & consistent responses

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

Map common error codes:

* `INVALID_PAYLOAD` (400)
* `ALLOCATION_NOT_FOUND` (404)
* `ALREADY_SUBMITTED` (409)
* `VARIANCE_HIGH` (200 WARN or 409 depending)
* `INSUFFICIENT_STOCK` (400/409)
* `AUTH_ERROR` (401/403)
* `SERVER_ERROR` (500)

---

## Idempotency patterns

* Require client `submission_id` GUID for every write (consumption or stock). If missing, the server can generate one but prefer client-provided.
* On POST:

  * If a record exists with same `submission_id`, return existing state with 200 (idempotent).
  * If different payload sent with same `submission_id`, return 409 and include existing record.

---

## Testing & acceptance criteria (developer handover)

**Unit tests**

* Schema validation for each endpoint.
* DB transaction race tests: simulate concurrent submissions for same allocation; second should fail or be accepted if within tolerance.
* Idempotency tests: repost same submission_id and confirm no duplicate writes.

**Integration tests**

* Flow simulation: call `GET /pending-items` -> select tags -> `allocations/aggregate` -> post `submission/consumption` -> ensure DB updated and `consumption_agg` correct.
* WARN path: deliberately submit variance > warn threshold and confirm exception created and status PENDING_APPROVAL.

**Load tests**

* Simulate 200 concurrent users clicking cards and submitting over 2 shifts, measure latency and DB throughput.

**Acceptance**

* All happy flows complete under 1.5s for reads; writes complete under 2–3s under normal load.
* No duplicate consumption lines for duplicate submission_id.
* WARNs trigger exceptions and produce approval flow.

---

## Deployment & infra notes

* Use 1 Function App for mapping/reads and another for writes? You may group them; keep scale plan predictable.
* Use Premium plan if Durable Functions or longer cold starts needed; otherwise Consumption Plan with Proxies is fine.
* Ensure Service Bus and Redis are in same region.
* Use App Configuration or Key Vault for thresholds and feature flags (warn_pct, tolerance absolute units).

---

## Example: pseudocode for `POST /api/submission/consumption`

```python
def post_consumption(req):
    payload = req.json()
    validate_schema(payload)
    submission_id = payload['submission_id']
    if exists_submission(submission_id):
        return 200, get_submission_status(submission_id)

    trace_id = payload.get('trace_id') or new_guid()
    begin_transaction()
    try:
        # 1. compute per allocation current consumed
        for alloc in payload['allocation_ids']:
            lock_allocation(alloc) # e.g., sp_getapplock or SELECT FOR UPDATE
        # 2. compute cumulative and validate
        for line in payload['lines']:
            allocated = sum_allocated_for_canonical(payload['allocation_ids'], line['canonical_code'])
            current_consumed = sum_consumed_for_allocations(...)
            new_total = current_consumed + line['actual_qty']
            if new_total > allocated + remnant + tolerance:
                raise ValidationError('INSUFFICIENT_STOCK', details)
        # 3. insert submission and lines
        insert_submission(...)
        insert_lines(...)
        update_consumption_agg(...) # update cumulative
        commit_transaction()
    except ValidationError as e:
        rollback_transaction()
        return 400 or 200-WARN as per policy
    except Exception:
        rollback_transaction()
        log.exception(trace_id)
        return 500
    # 4. trigger event and return OK/WARN
    publish_event('submission_received', submission_id)
    return 200, {"status":"OK","processed_submission_id":submission_id,"trace_id":trace_id}
```

---