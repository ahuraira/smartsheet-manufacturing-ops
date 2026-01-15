---

# Webhook Adapter App — Final Specification (SOTA, developer-ready)

> Goal: reliably receive Smartsheet EU webhooks, protect against duplicates/noise, persist authoritative event stubs, enqueue canonical events to Service Bus for the Core app, and provide robust handling for attachments and registration lifecycle. The adapter is intentionally minimal and always fast (acknowledge Smartsheet quickly), with durable processing delegated to the Core worker via Service Bus.

---

## 1 — High-level responsibilities (adapter app only)

* Accept Smartsheet webhook callbacks and handle verification handshake. ([developers.smartsheet.com][1])
* Do minimal validation and **immediate** enqueue to Service Bus (set messageId, correlationId).
* Insert an `event_log` stub in SQL (for idempotency & observability).
* Return `200 OK` (or proper verification response) quickly.
* Provide a secure **admin endpoint** to create/list/delete webhooks programmatically. ([developers.smartsheet.com][5])
* Provide a small helper endpoint for Core app to fetch Smartsheet resources if required (optional).
* NEVER run heavy business logic (no LPO checks, no allocations) inside adapter.

---

## 2 — HTTP endpoints (names, purpose, auth)

1. `POST /api/webhook/smartsheet` — **Webhook receiver** (public).

   * Handles verification challenge and incoming event callbacks.
   * No auth for callbacks (Smartsheet verification). Must validate challenge header on registration & subsequent pings. ([developers.smartsheet.com][1])

2. `POST /api/webhooks/register` — **Admin** (private)

   * Creates a Smartsheet webhook (registers to sheets/workspace/plan).
   * Protected via AAD & role (only admins). Use this to automatically register callbackUrl (optionally). ([developers.smartsheet.com][5])

3. `GET /api/webhooks` — list active webhooks (admin).

4. `POST /api/webhook/health` — lightweight health check for the webhook service (internal monitoring).

5. (Optional) `GET /api/resource/sheet/{sheetId}/row/{rowId}` — helper proxy to fetch row details with the adapter's API token (used only by trusted Core functions if desired).

---

## 3 — Incoming webhook behavior — verification & callback handling

### Verification handshake

* On webhook creation Smartsheet sends a **verification POST** with:

  * header: `Smartsheet-Hook-Challenge` (unique random value)
  * body: `{"challenge":"<value>", "webhookId": "<id>"}`
* **Adapter must respond** with HTTP 200 **and** echo the challenge value in the response header `Smartsheet-Hook-Response` (or the same header name Smartsheet expects) or include the value in the response body per Smartsheet docs. This completes verification. ([developers.smartsheet.com][1])

Implementation note: Smartsheet docs show both header and body challenge; implement both safe-guards (echo header if present; if request body contains `challenge`, respond with same).

### Normal callbacks

* Callback body contains a list of event objects (`events`) describing the change (objectType e.g., row/attachment/etc.). The callback is a notification — you must fetch the resource via Smartsheet API for full details. ([developers.smartsheet.com][6])

Adapter steps when receiving a normal callback:

1. Validate JSON & extract `eventId` (if Smartsheet provides a unique id) or generate `adapter_event_id` (use stable combination: `sm_{webhook_id}_{timestamp}_{eventIndex}`), `sheetId`, `rowId`, `objectType`, `action` (create/update/delete), `actor` if provided, `timestamp`.
2. Insert stub in `event_log` (SQL) with status `PENDING` (captures payload; helps tracing).
3. Enqueue canonical message to Service Bus `events-main`:

   * **messageId** = eventId (or adapter_event_id) — *important for broker duplicate detection if SB supports it*.
   * **correlationId** = trace_id (GUID you generate).
   * **contentType** = `application/json`.
   * **body** = canonical event JSON (see schema below).
4. Return `200 OK` to Smartsheet immediately.

Important: **do not** download attachments or fetch row data in this request — delegate to the Core worker to avoid timeouts and to keep the callback fast and idempotent.

---

## 4 — Canonical Service Bus message schema (JSON)

All messages must be compact, self-contained, and tagged with `trace_id`.

```json
{
  "event_id": "string",            // smartsheet event id or adapter-generated
  "source": "SMARTSHEET",
  "sheet_id": "string",
  "row_id": "string|null",
  "object_type":"row|attachment|cell|sheet|comment",
  "action":"ADD|UPDATE|DELETE",
  "timestamp_utc":"ISO8601",
  "actor":"user@company.com|null",
  "payload_summary": { "changed_columns": ["Col1","Col2"] }, // optional
  "trace_id":"uuid-v4"
}
```

Service Bus message meta:

* `messageId` = `event_id` (duplicates avoided at broker level if Standard duplicate detection enabled).
* `correlationId` = `trace_id`.

---

## 5 — Database: tables (DDL) — required for adapter

You asked specifically about SQL. The adapter must persist `event_log` and `row_snapshot`. Deliver the DDL below to DB team.

### 5.1 `event_log`

```sql
CREATE TABLE event_log (
  event_id VARCHAR(100) PRIMARY KEY,
  source VARCHAR(50) NOT NULL,
  sheet_id VARCHAR(50),
  row_id VARCHAR(50),
  object_type VARCHAR(50),
  action VARCHAR(20),
  received_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  processed_at DATETIME2 NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'PENDING', -- PENDING/PROCESSING/SUCCESS/FAILED/NOOP
  attempt_count INT NOT NULL DEFAULT 0,
  payload NVARCHAR(MAX) NULL,
  trace_id UNIQUEIDENTIFIER NULL
);
CREATE INDEX IX_event_log_status ON event_log(status);
CREATE INDEX IX_event_log_sheet_row ON event_log(sheet_id, row_id);
```

### 5.2 `row_snapshot` (adapter supports storing watched-hash if you want adapter to maintain last-known row snapshot for earlier quick screening — but Core will own final snapshots)

```sql
CREATE TABLE row_snapshot (
  sheet_id VARCHAR(50) NOT NULL,
  row_id VARCHAR(50) NOT NULL,
  watched_hash VARCHAR(128) NOT NULL,
  last_actor VARCHAR(200) NULL,
  last_modified_at DATETIME2 NULL,
  last_processed_at DATETIME2 NULL,
  PRIMARY KEY (sheet_id, row_id)
);
```

Notes:

* The adapter writes `event_log` stub when enqueuing a message. The Core worker will update `processed_at` & `status` after successful processing.
* `row_snapshot` may be seeded by Core or the adapter if you want a fast filter in adapter; but primary snapshot updates should be performed by Core to avoid race conditions.

---

## 6 — Attachment handling (how Core will fetch files — adapter role)

* Webhook signals `attachment` events but **does not include the file**. To download attachments use Smartsheet API endpoints: list attachments for sheet/row, then `GET /attachments/{attachmentId}/download` to fetch binary (requires API token). ([developers.smartsheet.com][3])
* **Adapter role:** only enqueue the attachment event. The Core worker will:

  1. call Smartsheet API (using stored EU token) to list attachments for the row (or use the attachment id from the webhook if provided),
  2. choose the correct attachment (by filename/type),
  3. download binary and move it to canonical storage (SharePoint or blob) with metadata,
  4. compute SHA256 `file_hash` and store in `file_store` table,
  5. call the parser job (or enqueue parser).

Permissions note: ensure the service account token used by the adapter/Core has permission to read attachments (row-level vs sheet-level differences flagged in community posts — validate with Smartsheet account). ([Smartsheet Community][7])

---

## 7 — Duplicate/noise protection (how adapter + Service Bus + SQL work together)

**Why needed:** Smartsheet retries, multiple subscribers, and system updates can produce duplicate callbacks.

**Design:**

* **At broker level**: if using Service Bus Standard, enable duplicate detection and set `messageId` to the `event_id`. This prevents duplicate messages for the same event window. (Adapter sets messageId).
* **At app level**: the adapter writes `event_log` entry with PK `event_id`. Before enqueuing, check if `event_log` exists; if so, short-circuit and return 200. This double-guards duplicates even if Basic tier is used.
* **Actor filter:** Do not process events where `actor` equals one of your system accounts (Power Automate service principal, function identity) — this avoids loops when the system updates Smartsheet.
* **Watched-column filter (Core)**: Because Smartsheet webhooks are per object (not per column), Core must fetch the row and compute `watched_hash` (hash of canonicalized watched columns) and compare to `row_snapshot` to detect actual meaningful changes. (See community & best-practice guidance). ([Smartsheet Community][4])

---

## 8 — Error handling, retries & DLQ

**Adapter responsibility**:

* Return 200 quickly after enqueue. If enqueue fails due to transient error, retry a small number of times (exponential backoff) before returning 500 to Smartsheet (Smartsheet will retry depending on webhook behavior). But prefer to return 200 only when message successfully enqueued and `event_log` stub written.

**Service Bus + Core worker**:

* Use Service Bus `MaxDeliveryCount` (e.g., 10). After exceeding max deliveries, messages land in DLQ.
* DLQ handler function must run on schedule and do:

  * Read DLQ item,
  * Create `exception_log` CRITICAL entry (full message stored),
  * Notify Ops (Teams/email) with trace_id and message details,
  * Provide admin UI or command to reprocess or discard.

**Why this matters**: webhooks are real-time signals; we must ensure eventual processing and visible failures (no silent drops).

---

## 9 — Security & tokens

* Store Smartsheet API token(s) in Key Vault (do not put in app settings). For EU account use region-specific base URL `https://api.smartsheet.eu/2.0` (use the EU token).
* Adapter admin endpoints must be protected by Azure AD and only callable by admin principals.
* A small allowlist of IP ranges is **not reliable** for Smartsheet because callbacks can come from changing infrastructure; rely on challenge verification and secure endpoints instead.
* Use managed identity for the app to access Key Vault.

---

## 10 — Observability & telemetry

* Every incoming callback must receive a `trace_id` (UUID) — generate one if not provided. Include this ID in logs, the `event_log` row, Service Bus correlationId, and all subsequent calls so you can trace an event end-to-end.
* Log structured JSON: `{trace_id, event_id, sheet_id, row_id, action, status, duration_ms}`.
* App Insights: track counts (webhook received, enqueued, enqueue fail), Service Bus queue depth, DLQ count. Alert when DLQ > 0 or enqueue latency > threshold.

---

## 11 — Admin tooling & lifecycle

* Provide `webhooks/register` admin endpoint (protected) that creates webhooks programmatically using Smartsheet API (use `callbackUrl`, `scope` and `events` list). Keep a local registry table of registered webhookIds & status. ([developers.smartsheet.com][5])
* Provide `webhooks/refresh` to re-validate subscriptions on startup (Smartsheet may require periodic revalidation).
* Provide a small admin view (Smartsheet or simple web page) to see the last N events & DLQ items with trace_id links.

---

## 12 — Tests & acceptance criteria (deploy checklist)

**Unit / integration tests**

* Simulated Smartsheet verification request: adapter responds correctly (echo header/body) — must pass. ([developers.smartsheet.com][1])
* Normal webhook POST: `event_log` inserted, Service Bus message enqueued with messageId set.
* Duplicate webhook POST: second call is idempotent (no additional enqueue, 200 returned).
* Attachment event: adapter enqueues; Core worker downloads via attachments endpoint and stores file (test with sample file). ([developers.smartsheet.com][3])
* System actor update: webhook from the system account is ignored or marked NOOP (configurable).

**Operational checks**

* DLQ handler moves dead messages to `exception_log` and notifies Ops.
* Admin register endpoint can list webhooks & handle verification challenge.
* Traceability: given `trace_id`, you can find the event in adapter logs, the service bus message, and Core processing result.

---

## 13 — Pseudocode: webhook_receiver (minimal, production-ready)

```python
def webhook_receiver(request):
    trace_id = request.headers.get('X-Trace-Id') or uuid4()
    # 1) Verification handshake
    challenge = request.headers.get('Smartsheet-Hook-Challenge') or request.json().get('challenge')
    if challenge:
        # Respond with header and body as Smartsheet docs instruct
        return Response(status=200, headers={'Smartsheet-Hook-Response': challenge}, body={'challenge': challenge})

    # 2) Normal event callback
    body = request.json()
    events = body.get('events', [])
    for idx, e in enumerate(events):
        event_id = e.get('id') or f"sm_{body.get('webhookId')}_{body.get('timestamp')}_{idx}"
        # Idempotency: check event_log
        if db.event_exists(event_id):
            continue
        db.insert_event_stub(event_id, source='SMARTSHEET', sheet_id=e.get('sheetId'), row_id=e.get('rowId'), payload=body, trace_id=trace_id)
        message = { ... canonical message ... , 'trace_id': trace_id }
        servicebus.send(queue='events-main', body=json.dumps(message), message_id=event_id, correlation_id=trace_id)
    return Response(status=200)
```

---

## 14 — Implementation notes & gotchas

* Smartsheet webhooks can be triggered for many object types — design code to ignore irrelevant events quickly (objectType not in your interest list). ([developers.smartsheet.com][6])
* Some organizations place attachments on comments or sheets; your Core handlers must robustly iterate attachments for row vs sheet. (Community posts show row-level attachment permission caveats — validate in your account). ([Smartsheet Community][7])
* Because Smartsheet webhooks are per-object, **column-level filtering must be implemented by fetching the row and comparing watched columns** — you cannot rely on Smartsheet to send only column-specific callbacks. ([Smartsheet Community][4])

---

## 15 — Deliverables for developers (concrete)

1. **Adapter repository module** with:

   * `webhook_receiver` function
   * admin endpoints `webhooks/register`, `webhooks/list`
   * config for Smartsheet token Key Vault ref, allowed system accounts, event scope list
2. **SQL migrations** for `event_log` and `row_snapshot`
3. **Service Bus** producer code with messageId & correlationId
4. **Unit tests** for handshake, normal callback, duplicate behavior
5. **Operational runbook** for webhooks: how to register a webhook manually (curl), how to inspect DLQ and reprocess
6. **Documentation**: sequence diagram and traceability guide (how to use trace_id)

---

### Useful Smartsheet docs I used while building these rules

* Webhook verification & callback handling. ([developers.smartsheet.com][1])
* Webhook event types & sheet object types. ([developers.smartsheet.com][2])
* Attachments API (how to download attachments). ([developers.smartsheet.com][3])
* Best practices for incremental updates & webhook-driven syncs (fetch row on event). ([developers.smartsheet.com][8])
* Community notes about column-level subscription limitations and attachment quirks. ([Smartsheet Community][4])

---


[1]: https://developers.smartsheet.com/api/smartsheet/guides/webhooks/webhook-verification?utm_source=chatgpt.com "Webhook verification"
[2]: https://developers.smartsheet.com/api/smartsheet/guides/webhooks?utm_source=chatgpt.com "Webhooks"
[3]: https://developers.smartsheet.com/api/smartsheet/openapi/attachments?utm_source=chatgpt.com "Attachments"
[4]: https://community.smartsheet.com/discussion/109341/subscope-for-row-update-webhook?utm_source=chatgpt.com "Subscope for row update webhook"
[5]: https://developers.smartsheet.com/api/smartsheet/guides/webhooks/launch-a-webhook?utm_source=chatgpt.com "Launch a webhook"
[6]: https://developers.smartsheet.com/api/smartsheet/openapi/schemas/sheet_webhookevent?utm_source=chatgpt.com "Sheet webhook event"
[7]: https://community.smartsheet.com/discussion/117633/downloading-attachments-through-api?utm_source=chatgpt.com "Downloading Attachments through API"
[8]: https://developers.smartsheet.com/api/smartsheet/guides/best-practices/incremental-data-updates?utm_source=chatgpt.com "Handling incremental Smartsheet data updates"
