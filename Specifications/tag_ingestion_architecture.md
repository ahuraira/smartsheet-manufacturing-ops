Below is a crisp, production-grade **Tag Ingestion Architecture & Flow Plan** you can hand to developers and implementors today. It’s written to satisfy your rule that **Power Automate is only orchestration** and **all deterministic logic + ID generation + validations + exception creation live in Azure Functions / API layer**. It also enforces idempotency, auditability, and operability from day one.


1. Environment layout (DEV / UAT / PROD) and resources
2. End-to-end Tag-Ingest flow (component responsibilities, exact sequence)
3. API contracts + example payloads (idempotent)
4. Exception & user_action_history handling rules (who writes them and when)
5. Id generation, idempotency, file hashing strategy
6. Error handling, retry, transaction and compensation guidance
7. Observability, monitoring & alerts for the flow
8. Acceptance tests & developer checklist


---

# 1) Environments & infra (must-have)

Create three logical environments (separate resources or separate namespaces):

* **DEV** — for developers & unit tests
* **UAT / PILOT** — for pilot with real users / pilot LPOs
* **PROD** — live

For each environment provision:

* Smartsheet workspace (sheets) — separate
* SharePoint library for LPOs — separate site/parent folder
* Azure Function App (stateless functions) + App Insights
* Azure SQL (or Dataverse) schema dedicated to env
* Power Automate environment connected to Smartsheet + SharePoint
* Key Vault for secrets, Managed Identity for Functions
* Power BI workspace for dashboards

Use IaC (Bicep/ARM/Terraform) and a CI/CD pipeline (GitHub Actions / Azure DevOps). Keep naming conventions consistent: `prod-fn-tags`, `pilot-shared-docs`, etc.

---

# 2) High-level principle for Tag Ingest flow

* **Trigger point (UI):** Smartsheet Tag Upload form OR manual file uploaded to SharePoint LPO folder.
* **Orchestration (Power Automate):** receive event → call Azure Function (`/api/tags/ingest`) with payload. No logic in Power Automate beyond orchestration, retries, and notifications.
* **Compute & Persistence (Azure Functions + SQL):** Azure Function performs all deterministic work: validate LPO, compute remaining capacity, generate TagID (if not provided), compute file hash, store file metadata, create ledger rows (tag_sheet), create `user_action_history`, create `exception_log` if needed — then return canonical response.
* **UI update (Power Automate):** receives response and updates Smartsheet rows, posts adaptive cards / emails for exceptions.

Everything that affects truth is written by Azure Functions into the ledger DB; Power Automate only reflects or notifies that result.

---

# 3) Tag Ingest — exact step-by-step sequence (SOTA)

### Actors & components

* Sales user (Smartsheet)
* Smartsheet form / or SharePoint file upload
* Power Automate (orchestration)
* Azure Function: `fn_ingest_tag` (authoritative)
* Ledger DB: `tag_sheet`, `file_store`, `user_action_history`, `exception_log`
* SharePoint: file storage (actual file); Function stores `file_url` and `file_hash` in DB
* Power BI / dashboards (read-only later)

### Flow (detailed)

1. **User uploads** Tag sheet via Smartsheet form OR drops a file into SharePoint `/LPOs/LPO_<id>/TagSheets/`.

   * Smartsheet form collects: `tag_reference_if_any` (optional), `lpo_id`, `requested_delivery_date`, `required_area_m2`, `uploaded_by` etc.
   * Form saves file to SharePoint (Power Automate helper if Smartsheet stores local attachments).

2. **Power Automate triggers** on Smartsheet submit OR SharePoint file create event. Its job:

   * Build the canonical ingestion request JSON.
   * Call the Azure Function `POST /api/tags/ingest`.
   * If the call succeeds, update Smartsheet row with status returned.
   * If a transient error, retry with backoff (Power Automate retry policy). If persistent, create a support ticket (Power Automate only orchestration).

3. **Azure Function: `fn_ingest_tag` (authoritative)** — responsibilities:

   * Validate request signature (Azure AD token); authenticate caller.
   * Normalize inputs.
   * Download file (if SharePoint file supplied) and compute SHA256 `file_hash`.
   * Idempotency check:

     * If `client_request_id` was previously processed → return stored response.
     * Else if `file_hash` exists for that LPO & same filename → create `exception_log` DUPLICATE and return `409 DUPLICATE`.
   * Business validation:

     * Look up `lpo_master` by `lpo_id` or `customer_lpo_ref`.
     * Compute `current_committed = delivered_quantity + sum(allocated_qty) + sum(planned_expected_for_future)` (all read from ledger).
     * If `current_committed + required_area > po_quantity` → create `exception_log` (INSUFFICIENT_PO_BALANCE), set `tag_sheet.status = BLOCKED`, return 422 with exception_id.
     * If `lpo_status == ON_HOLD` → create `exception_log` (LPO_ON_HOLD), set `tag_sheet.status = BLOCKED`, return 422.
   * ID generation:

     * If `tag_id` not provided, generate `tag_id` deterministically: `TAG-<YYYYMMDD>-<sequential>` using a DB-backed sequence per day or ULID. (Function is the only place that generates IDs.)
   * Persist:

     * Insert a `file_store` metadata row: (file_url, file_hash, uploaded_by, uploaded_at, env, original_filename).
     * Insert `tag_sheet` row with status `UPLOADED`, link to `file_store_id`.
     * Insert `user_action_history` row: action = TAG_UPLOAD, user, timestamp, request_id, trace_id.
   * Return 200 with `tag_id`, `status=UPLOADED`, `file_hash`, `trace_id`.
   * *If any of the DB writes fail, the function must rollback (transaction) or write a compensating exception*.

4. **Power Automate** receives response:

   * Update Smartsheet: set Tag row status = UPLOADED, TagID = `tag_id`, show link to file and file_hash.
   * If response included exception → publish adaptive card to PM/OPS and set Smartsheet `Status = BLOCKED` with link to exception record.

**Key rule:** All business decision and writes (status and exception creation) happen inside `fn_ingest_tag`. Power Automate only reflects the result.

---

# 4) API contract (authoritative, idempotent)

**Endpoint:** `POST /api/tags/ingest` (Azure Function, protected by Azure AD)

**Request JSON**

```json
{
  "client_request_id": "uuid-v4",    // caller-generated idempotency key
  "tag_id": "TAG-20260105-0001",    // optional (if client has one)
  "lpo_id": "LPO-1234",
  "customer_lpo_ref": "CUST-LPO-99", // optional fallback
  "required_area_m2": 120.25,
  "requested_delivery_date": "2026-02-01",
  "file_url": "https://tenant.sharepoint/.../TAG-...xlsx",
  "original_file_name": "TAG-123_cutexport_v1.xlsx",
  "uploaded_by": "sales.user@company.com",
  "metadata": { "truck_size": "small", "notes": "urgent" }
}
```

**Response 200 (success)**

```json
{
  "status": "UPLOADED",
  "tag_id": "TAG-20260105-0001",
  "file_hash": "abcd1234...",
  "trace_id": "trace-0001"
}
```

**Response 409 (duplicate)**

```json
{ "status":"DUPLICATE", "existing_tag_id":"TAG-20260105-0000", "trace_id":"..." }
```

**Response 422 (blocked with exception)**

```json
{ "status":"BLOCKED", "exception_id":"EX-2026-0001", "message":"LPO On Hold", "trace_id":"..." }
```

**Notes**

* `client_request_id` must be used by the Function to ensure idempotency.
* All response bodies include `trace_id` (correlation id) to trace logs & AppInsights.

---

# 5) Exception & user_action_history rules (who writes what)

**Who writes exceptions?**

* **All deterministic exceptions and validations are created by Azure Functions.**
  Examples: DUPLICATE_UPLOAD, MULTI_TAG_NEST, INSUFFICIENT_PO_BALANCE, LPO_ON_HOLD, PARSE_ERROR, SAP_CREATE_FAILED, etc.

**Who writes user_action_history?**

* **Azure Functions write `user_action_history` for every authoritative change** they make: tag creation, status changes, allocations, adjustments, DO creation, exception creation. Include `client_request_id`, `trace_id`, and `source` (`Smartsheet`,`PowerAutomate`,`API`).
* **Power Automate does not write user_action_history directly.** When a user uses a Smartsheet form or approval card, Power Automate calls the Azure Function API which then writes the `user_action_history`. This guarantees every authoritative action is logged by the authoritative component.

**Why this separation?**

* Ensures single source of truth for audit records — otherwise duplicate or inconsistent logs appear.
* Keeps logic and audit close together transactionally.

**Exception lifecycle**

* Exception records have: `exception_id, created_at, severity, source, related_tag_id, qty_impact, assigned_to, status, sla_due, attachments (file links), resolver_notes, resolution_txn_id`.
* Exceptions are created inside Azure Functions (or reconciliation job).
* Power Automate subscribes to "new exception" events (webhook) and notifies assigned owners (Teams adaptive card + email) and updates Smartsheet exception board (mirror).
* When user clicks an adaptive card to "Acknowledge" or "Resolve", Power Automate captures the action and calls Azure Function `POST /api/exceptions/action` with payload. Function validates approvers, appends `user_action_history`, performs any compensating inventory_txn (adjustment), and updates exception to RESOLVED.

**Important:** The resolution action that changes inventory (e.g., ADJUSTMENT txn) must be executed by Azure Functions so the `inventory_txn` ledger is authoritative.

---

# 6) ID generation & idempotency (concrete)

**ID generation**

* All system IDs are generated by Azure Functions, not by Power Automate.
* Use a hybrid approach for easy debugging:

  * For Tags: `TAG-YYYYMMDD-<sequence>` where `<sequence>` is a DB-backed sequential integer per day (guarantees readable ordered IDs).
  * For other technical IDs (cut_session, exceptions, txn) use GUIDs or ULIDs.
* The function generating the Tag ID should acquire a lightweight DB lock or use a DB sequence to ensure monotonic uniqueness.

**Idempotency**

* Every client request must supply `client_request_id` (UUID). The function stores processed `client_request_id` with the result. If a repeat request with same `client_request_id` arrives, function returns the previous response (HTTP 200) — no duplicate processing.
* For file uploads, also compute & store `file_hash` (SHA256). If same file hash for same LPO/tag appears, the function must detect and either reject as DUPLICATE or dedupe as per business rule.

**Why both?**

* `client_request_id` protects against retries & multi-call by the same client.
* `file_hash` protects against different clients uploading the same file or reuploads.

---

# 7) Transactions, compensation & failure modes

**Atomic writes where possible**

* For operations that require multiple DB writes (e.g., insert `cut_session` + `production_log` + create `allocation` entries), execute them inside a DB transaction in the function. If any step fails the transaction is rolled back.

**When external systems involved (SAP)**

* Use two-phase pattern: write local ledger row with status `PENDING_SAP` then call SAP asynchronously.
* On SAP success: write SAP reference and update status. On failure: set exception & retry.
* Do not block UI while waiting for external ack. Use `PENDING` states and clear communication.

**Compensation**

* If partial failure occurs after some side-effects (e.g., created DB rows but failed to write file metadata due to network), create `exception_log` with `recovery_action` and ensure there is a scheduled retry worker or manual queue for devs/ops to reprocess. Don't attempt fragile multi-system distributed transactions.

---

# 8) Observability & monitoring

**Logging**

* Structured logging in Azure Functions (JSON) with fields: `trace_id`, `client_request_id`, `function_name`, `step`, `duration_ms`, `status`, `error`.
* Persist critical logs to App Insights and to an `audit_log` table if required for long-term retention.

**Metrics**

* Expose metrics via App Insights custom metrics and dimension by env:

  * Tag uploads per hour (success/fail)
  * Duplicate uploads
  * Exceptions created (by type)
  * Average ingestion latency
* Configure alerts:

  * Exception rate > threshold in 5m → Teams alert
  * Reconciliation exceptions CRITICAL → paging to on-call
  * SAP connector failures > 3 in 1 hour → email admin

**Tracing**

* All services propagate `trace_id` (from Power Automate to Function to DB to SAP). Include it in emails and adaptive cards to speed troubleshooting.

---

# 9) Security & governance (brief)

* Protect functions with Azure AD; Power Automate uses a service principal to call Functions.
* Use Managed Identity for Azure Functions to access Key Vault, SQL.
* Least privilege on DB and SharePoint.
* Enforce role-based sections in Smartsheet; but authoritative access control is enforced at the API level as well (Functions validate roles/claims for operations like `release_tag`, `override`).

---

# 10) Acceptance tests & QA for Tag Ingest (must pass)

1. **Happy path** — upload unique file; function returns `UPLOADED`; `tag_sheet` inserted; `user_action_history` inserted; Smartsheet updated.
2. **Duplicate client_request_id** — repeat same request -> same 200 result with same `tag_id` (no duplicate).
3. **Duplicate file_hash** — uploading same file again produces `409 DUPLICATE` and creates exception record.
4. **LPO on hold** — ingest returns 422 BLOCKED with an exception row; Smartsheet shows BLOCKED.
5. **PO overcommit** — if `required_area + committed > PO quantity`, create exception INSUFFICIENT_PO_BALANCE and set tag to BLOCKED.
6. **Idempotency under retry** — Power Automate retries the POST 3x, function processes only once.
7. **End-to-end trace** — logs for the ingest show same `trace_id` across Function, DB write, and Power Automate update row.

---

# 11) Developer checklist for Tag Ingest implementation

* [ ] Provision Azure Function app with Managed Identity + App Insights
* [ ] Implement `POST /api/tags/ingest` function with input validation, file download, file_hash compute
* [ ] Implement DB schema: `tag_sheet`, `file_store`, `user_action_history`, `exception_log`, `lpo_master` (minimal), `client_request_log`
* [ ] Implement ID generation strategy (DB sequence + prefix)
* [ ] Implement idempotency table indexed by `client_request_id`
* [ ] Implement deterministic business validations (PO balance, LPO status)
* [ ] Implement transaction & rollback logic for DB writes
* [ ] Power Automate flow to call function, update Smartsheet, and handle responses (success / duplicate / blocked)
* [ ] Tests (unit, integration, end-to-end) including error cases
* [ ] Monitoring & alerts in App Insights

---

# 12) Minimal sample code & pseudo-sequence (for devs)

Pseudocode for function skeleton (already in previous message but condensed):

```python
def ingest_tag(request):
    trace_id = new_trace()
    client_request_id = request['client_request_id']
    if client_request_processed(client_request_id):
        return previous_response_for(client_request_id)

    file_url = request['file_url']
    file_bytes = download(file_url)
    file_hash = sha256(file_bytes)

    # check duplicate file
    if file_hash_exists(lpo_id=request['lpo_id'], file_hash=file_hash):
        ex = create_exception(..., reason='DUPLICATE_UPLOAD')
        store_client_request(client_request_id, result={'status':'DUPLICATE','exception':ex})
        return response(409, {..., 'exception_id':ex.id})

    # load LPO and compute committed
    lpo = get_lpo(request['lpo_id'])
    committed = lpo.delivered + sum_allocated(lpo) + sum_planned(lpo)
    if committed + request['required_area_m2'] > lpo.po_quantity:
        ex = create_exception(..., reason='INSUFFICIENT_PO_BALANCE')
        create_tag(status='BLOCKED', ...)
        store_client_request(...)
        return response(422, {..., 'exception_id':ex.id})

    # generate tag id if not supplied (DB sequence)
    tag_id = request.get('tag_id') or create_tag_id_seq()

    # begin db transaction
    with db.transaction():
        file_id = insert_file_store(...)
        tag_row = insert_tag_sheet(tag_id, file_id, status='UPLOADED', ...)
        write_client_request_log(client_request_id, tag_id, trace_id, ...)
        write_user_action_history(user=request['uploaded_by'], action='TAG_UPLOAD', ...)
    # commit

    return response(200, {'status':'UPLOADED','tag_id':tag_id, 'trace_id':trace_id})
```

---

# 13) Final recommendations & guardrails

* Keep Power Automate flows simple and idempotent: they translate Smartsheet events to API calls and notify users. No calculation, no decisioning.
* Version your Azure Functions (API versions) so parser or validation changes remain backward-compatible.
* Keep config (T-1 cutoff, thresholds) in DB with versioning; Functions read and cache these values.
* Log everything with a `trace_id` and surface this in Smartsheet responses so Ops can open a single trace across systems.
* Enforce the policy that **all authoritative writes must go through Azure Functions**. If a Smartsheet user edits a sheet directly, have a nightly job detect anomalies and create exceptions — but train users to not edit authoritative fields.

---


