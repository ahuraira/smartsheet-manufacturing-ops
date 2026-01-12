## **Comprehensive LPO Ingestion Specification** (SOTA, developer-ready)

This is a full spec you can hand to developers and automation engineers. It follows the same structure we used for `fn_ingest_tag` and enforces idempotency, auditability, deterministic naming, and SharePoint folder creation via Power Automate orchestration.

---

### 1) Purpose & scope

**Goal:** Provide a deterministic, auditable, idempotent LPO ingestion flow that:

* populates `lpo_master` (canonical commercial record),
* enforces business validation (no duplicates, consistent SAP mapping),
* provisions SharePoint LPO folder structure consistently,
* returns canonical `lpo_id` and folder URLs,
* emits `user_action_history` and `exception_log` entries on validation failures.

This supports downstream flows: Tag ingestion (validates LPO), allocation, reconciliation, invoicing.

---

### 2) High-level flow (who does what)

1. **Trigger** — Sales/Admin fills an LPO Smartsheet form or uploads a purchase-order PDF/Excel to a designated intake SharePoint folder.
2. **Power Automate** (orchestration):

   * Collects the LPO form data or file metadata and calls the authoritative API: `POST /api/lpos/ingest` (Azure Function).
3. **Azure Function `fn_lpo_ingest`** (authoritative logic):

   * Validates payload, checks duplicates, validates SAP ref format (if provided), generates canonical `lpo_id` (if absent), writes `lpo_master` row, creates `user_action_history` entry.
   * Returns `lpo_id`, canonical `folder_name`, and `trace_id`.
4. **Power Automate** receives response:

   * Creates SharePoint LPO folder structure using returned `folder_name`.
   * Uploads (or moves) the LPO file into the new folder (if file-based trigger).
   * Writes back folder URLs into Smartsheet LPO row.
   * Optionally posts a Teams adaptive card to notify stakeholders.
5. **If validation fails**, `fn_lpo_ingest` creates an `exception_log` row and returns 4xx; Power Automate updates Smartsheet to show BLOCKED and notifies owner.

---

### 3) API contract — `POST /api/lpos/ingest`

**Purpose:** create or update canonical LPO.

**Request JSON:**

```json
{
  "client_request_id": "uuid-v4",
  "customer_lpo_ref": "CUST-LPO-1234",       // mandatory or either sap_reference
  "sap_reference": "SAP-PO-9999999",        // optional (but recommended if exists)
  "customer_name": "Acme Utilities",
  "project_name": "Project X",
  "brand": "BrandA",
  "po_quantity_sqm": 1250.5,
  "price_per_sqm": 150.00,
  "terms_of_payment": "30 days",
  "allowable_wastage_pct": 3.0,
  "hold_reason": null,
  "requested_delivery_dates": ["2026-02-10", "2026-02-18"],  // optional windows
  "file_url": "https://tenant.sharepoint/.../po.pdf",        // optional
  "uploaded_by": "sales@company.com",
  "metadata": { "currency": "AED", "notes": "priority" }
}
```

**Responses:**

* `200 OK` (created/updated)

```json
{
  "status":"OK",
  "lpo_id":"LPO-20260110-001",
  "folder_url":"https://.../LPOs/LPO-20260110-001_AcmeUtilities/",
  "trace_id":"trace-0001"
}
```

* `409 CONFLICT` (duplicate)

```json
{
  "status":"DUPLICATE",
  "existing_lpo_id":"LPO-20250101-019",
  "trace_id":"..."
}
```

* `422 UNPROCESSABLE_ENTITY` (validation failure)

```json
{
  "status":"BLOCKED",
  "exception_id":"EX-2026-0005",
  "message":"PO quantity exceeds allowed threshold or SAP mismatch",
  "trace_id":"..."
}
```

**Idempotency:** `client_request_id` required. Function writes processed request IDs and returns same result for re-sent request_id.

---

### 4) DB Schema (minimal core table `lpo_master`)

Use Dataverse or Azure SQL. Example DDL (Azure SQL):

```sql
CREATE TABLE lpo_master (
  lpo_id VARCHAR(50) PRIMARY KEY,       -- e.g. LPO-YYYYMMDD-XXX
  customer_lpo_ref VARCHAR(100),
  sap_reference VARCHAR(100),
  customer_name VARCHAR(200),
  project_name VARCHAR(200),
  brand VARCHAR(100),
  po_quantity DECIMAL(12,4),
  price_per_sqm DECIMAL(12,4),
  po_value AS (po_quantity * price_per_sqm) PERSISTED,
  allowable_wastage_pct DECIMAL(5,2),
  terms_of_payment VARCHAR(200),
  hold_reason VARCHAR(400),
  status VARCHAR(50),                   -- OPEN|ON_HOLD|LEGACY_PS|CLOSED
  folder_url VARCHAR(500),
  created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  created_by VARCHAR(200),
  updated_at DATETIME2 NULL,
  updated_by VARCHAR(200),
  source_file_hash VARCHAR(128) NULL,
  CONSTRAINT UQ_lpo_customer_ref UNIQUE(customer_lpo_ref)
);
```

Also ensure tables:

* `user_action_history(action_id, user_id, action_type, target_table, target_id, timestamp, payload, trace_id)`
* `exception_log(exception_id, created_at, source, related_lpo_id, reason_code, severity, status, assigned_to, sla_due, details, trace_id)`
* `client_request_log(client_request_id, endpoint, response_code, response_payload, processed_at)`

---

### 5) Validation rules & business logic (function-side)

`fn_lpo_ingest` must:

1. **Authenticate** the caller (Azure AD token or function key) and capture `uploaded_by`.
2. **Normalize** `customer_lpo_ref` and `sap_reference` (trim, uppercase).
3. **Idempotency check**: if `client_request_id` processed, return prior result.
4. **Duplicate detection**:

   * If `customer_lpo_ref` already exists → return `409 DUPLICATE` with existing `lpo_id` unless update semantics allowed.
   * If `sap_reference` already exists → return `409` or attempt merge if `client_request_id` suggests update.
   * If file_url provided → compute file_hash and compare with stored `source_file_hash` if exists.
5. **Sanity checks**:

   * `po_quantity_sqm` > 0, `price_per_sqm` > 0.
   * `allowable_wastage_pct` within reasonable bounds (0–20% or config).
6. **Business checks**:

   * If `status` provided and equals `LEGACY_PS` — set special handling flags.
   * Check `current_committed = delivered + allocated + planned` for the same LPO (if updating PO). If new `po_quantity` < `current_committed`, create exception `PO_QUANTITY_REDUCED_BELOW_COMMITTED`.
7. **Generate canonical LPO ID** if not provided:

   * Format: `LPO-YYYYMMDD-<seq>` where `<seq>` is DB sequence number for that day. Function uses DB sequence or a small table with atomic increment.
8. **Insert or update `lpo_master`** inside DB transaction.
9. **Write `user_action_history`** (ingest created/updated).
10. **Return canonical `lpo_id`** and suggested SharePoint folder name.

---

### 6) SharePoint folder creation (Power Automate responsibilities)

**Why Power Automate**: It has native SharePoint connectors and is ideal to create folders and move files.

**Power Automate flow (steps):**

1. Trigger: Smartsheet row added OR HTTP response from `fn_lpo_ingest` (if using file upload to intake folder).
2. Call `fn_lpo_ingest` API. If status OK:

   * Compose canonical folder path: e.g. `LPOs/LPO-{lpo_id}_{SAFE_CUSTOMER_NAME}` (Function already returned `folder_url` and canonical name; use that)
   * Create SharePoint folder(s):

     ```
     /LPOs/LPO_<LPOID>_<CustRef>/
       /TagSheets
       /CutSessions
       /Deliveries
       /Invoices
       /Remnants
       /Audit
     ```
   * Move or copy uploaded file into `/TagSheets` or root as `PO.pdf`.
   * Update Smartsheet LPO row `folder_url` and `lpo_id`.
   * Send Teams adaptive card to ops stating folder created and LPO created.
3. On 4xx BLOCKED response:

   * Update Smartsheet `Status=BLOCKED`, `ExceptionID=...`
   * Post adaptive card to LPO owner with link to exception

**Important**:

* The Power Automate flow **does not** decide LPO ID or business logic — it just calls the function and acts on function output.
* If folder creation fails, Power Automate should create an `exception` in the ledger via `POST /api/exceptions/create` or call `fn_record_exception`. So error is centrally recorded.

---

### 7) Exception handling & catalog (LPO ingestion)

`fn_lpo_ingest` must create exceptions (and return corresponding HTTP codes) in these cases:

* `DUPLICATE_LPO_REF` (409) — LPO with same customer_lpo_ref exists
* `DUPLICATE_SAP_REF` (409) — SAP ref already exists
* `INVALID_DATA` (422) — missing required fields or invalid numeric values
* `PO_QUANTITY_CONFLICT` (422) — new PO quantity < already committed amounts
* `LPO_ON_HOLD` (422) — LPO flagged as hold
* `FILE_DUPLICATE` (409) — same file hash already stored
* `SYSTEM_ERROR` (500) — unexpected

Each exception record must include:

* severity (LOW/MEDIUM/HIGH/CRITICAL),
* assigned_to (role or person),
* SLA due (e.g., 48 hours for HIGH),
* attachments link (file_url, other artifacts),
* created_by and trace_id.

Power Automate subscribes to new exceptions and routes notifications / escalations.

---

### 8) Security & access control

* Function is protected by Azure AD (recommended). Power Automate uses a service principal to call it. For prototype you can use function keys but switch to AAD for production.
* Key Vault stores any secrets (SharePoint app credentials if needed).
* Manage access to SharePoint folders by role: e.g., `LPO Managers`, `Production Supervisors`, `Finance`.

---

### 9) Acceptance tests (must pass)

1. **Create LPO happy path**

   * Submit valid payload → 200 OK → lpo_master row inserted → folder created by Power Automate → Smartsheet updated → user_action_history created.

2. **Duplicate LPO**

   * Submit same `customer_lpo_ref` with different client_request_id → 409 DUPLICATE with existing `lpo_id`.

3. **Idempotency**

   * Send same `client_request_id` twice → first creates LPO, second returns same 200 result (no duplicate row).

4. **Invalid data**

   * Send negative `po_quantity` → 422 UNPROCESSABLE with `INVALID_DATA` exception.

5. **Quantity conflict**

   * Insert LPO with `po_quantity` less than previously `committed` entries → 422 PO_QUANTITY_CONFLICT and exception created.

6. **File duplicate**

   * Upload same file twice → second call returns `409 FILE_DUPLICATE` and an exception is recorded.

7. **Folder creation failure**

   * Simulate SharePoint transient failure — Power Automate should retry and eventually create exception if still failed; ledger should show an exception record for manual remediation.

---

### 10) Developer checklist & steps to implement

1. Implement `fn_lpo_ingest` (HTTP Azure Function):

   * Input validation, idempotency table, generate `lpo_id` (DB sequence), write `lpo_master`, write `user_action_history`, compute `folder_name`, return result.
2. Create `lpo_master` DB table + `client_request_log` + `user_action_history` + `exception_log`.
3. Create Power Automate flow for Smartsheet trigger → call `fn_lpo_ingest` → create SharePoint folders → update Smartsheet.
4. Ensure File Hash compute and store for incoming PO files.
5. Create dashboards: LPOs Pending, Exceptions, Folder URLs.
6. Add tests (unit tests for function, integration tests for DB).
7. Add CI: linting, unit tests, static security scan (bandit).

---

### 11) Sample function pseudocode (Python) — `fn_lpo_ingest`

```python
def fn_lpo_ingest(request):
    trace_id = new_trace_id()
    payload = request.json()
    client_request_id = payload['client_request_id']

    # Idempotency
    existing = client_request_lookup(client_request_id)
    if existing:
        return existing['response']  # return prior response

    # Validate required
    validate_payload(payload)

    # Normalize keys
    cust_ref = normalize(payload['customer_lpo_ref'])
    sap_ref = normalize(payload.get('sap_reference'))

    # Duplicate checks
    if lpo_exists_by_customer_ref(cust_ref):
        ex = create_exception(..., reason='DUPLICATE_LPO_REF')
        save_client_request(client_request_id, ex.response)
        return 409, ex

    if sap_ref and lpo_exists_by_sap_ref(sap_ref):
        ex = create_exception(..., reason='DUPLICATE_SAP_REF')
        save_client_request(...)
        return 409, ex

    # business checks (committed etc)
    if check_committed_exceeds_new_po(payload):
        ex = create_exception(..., reason='PO_QUANTITY_CONFLICT')
        save_client_request(...)
        return 422, ex

    # generate lpo_id
    lpo_id = generate_lpo_id()

    # compute file_hash if file_url provided
    file_hash = None
    if payload.get('file_url'):
        file_bytes = download_file(payload['file_url'])
        file_hash = sha256(file_bytes)
        if file_hash_exists(file_hash):
            ex = create_exception(..., reason='FILE_DUPLICATE')
            save_client_request(...)
            return 409, ex

    # insert LPO inside DB transaction
    with db.transaction():
        insert_lpo_master(...)
        insert_user_action_history(...)
        save_client_request(client_request_id, success_response)

    return 200, success_response
```

---

### 12) Edge cases & legacy PS treatment

* **Legacy PS LPOs**: mark `lpo_master.status = LEGACY_PS` on ingest if known. These LPOs should be allowed but DO creation in SAP will be blocked; DOs should be created as `VIRTUAL_DO` in ledger and linked to LPO. Finance handles consolidated invoicing.
* **PO revisions**: If a customer sends revised PO with same `customer_lpo_ref`, implement update path: check delta (increase/decrease), validate decrease vs committed, create exception if necessary.
* **Partial ingestion**: Accept partial data and create `provisional` LPO state; require finalization step to move to `OPEN`.

---