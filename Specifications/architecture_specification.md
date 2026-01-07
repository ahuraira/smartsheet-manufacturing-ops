Two *key decisions* I made (and why):

* **Nesting = planning (T-1).** A nesting export is *the engineering truth* for expected consumption and remnant detection, and must be done ≥1 day before production.
* **Allocation = soft reservation at T-1 (after parse).** Allocation is created immediately after successful parse to reserve material per shift. Physical stock is only consumed when storekeeper *confirms pick* (after production has completed). This keeps planning predictable, prevents double-booking, and keeps physical consumption aligned to reality.

Below is the full spec.

---

# 0. Overview (one-liner)

Tag Sheet uploaded → validate LPO/commercial coverage → release for T-1 nesting → nesting file parsed → allocation (soft reserve) created for morning/evening → pre-shift pick confirmation on production day → consumption events recorded → DO / partial DO flow → POD upload → snapshots & reconciliation → exceptions blocked until resolved.

---

# 1. System components & responsibilities

* **Smartsheet (UI only)**

  * Forms & sheets for Tag Upload, Nesting Upload, Allocation Sheets, Shift Consumption, Load Builder, Exceptions.
  * *Rule:* Do not compute business logic in Smartsheet cells. All writes go through APIs/Power Automate.

* **SharePoint / OneDrive**

  * Immutable file store for tag PDFs, CutExpert exports, DO/POD/invoice PDFs. Files linked by URL + SHA256 hash stored in ledger.

* **Power Automate (Orchestration)**

  * Triggers on Smartsheet rows & SharePoint file uploads, routes events, initiates approvals, posts adaptive cards / e-mails, calls Azure Functions.

* **Azure Functions (Compute / Deterministic Logic)**

  * All parsing, allocation engine, reconciliation, SAP adapters, idempotency logic. Stateless, idempotent functions.

* **Ledger DB (Dataverse or Azure SQL)**

  * Canonical append-only ledgers: tag_sheet, cut_session, production_log, allocation, inventory_txn, remnant_log, delivery_order, exception_log, sap_snapshot, inventory_snapshot, invoice_log, user_action_history, config.

* **SAP Connector (Logic App / Azure Function)**

  * Pull SAP snapshots, push DO/invoice when allowed; treat SAP as a peer.

* **Power BI**

  * Dashboards for T-1 compliance, Variance, Exceptions, AR exposure.

---

# 2. High-level sequence (step-by-step with gating)

1. **Tag Upload**

   * Actor: Sales/Planning uses Smartsheet form to upload Tag PDF + metadata (TagID, LPO, required area, requested delivery).
   * Power Automate → call `POST /api/tags/ingest`.
   * Azure Function `fn_ingest_tag`:

     * Compute SHA256(file). If file_hash exists → create `exception_log (DUPLICATE_UPLOAD)` and return DUPLICATE.
     * Validate LPO exists and `lpo.remaining_area >= planned_threshold` (configurable). If LPO ON_HOLD or insufficient → set Tag status `BLOCKED` and create exception.
     * Insert `tag_sheet` (status = UPLOADED). Log user_action_history.
   * Result: Tag row visible in Smartsheet with status.

2. **PM Release → T-1 Planning**

   * Actor: Production Manager marks Tag `RELEASED_FOR_NESTING` by T-1 cut-off.
   * Power Automate triggers `POST /api/tags/release`.
   * Azure Function `fn_release_tag`:

     * Validate LPO again (delivered + allocated + planned <= PO Quantity). If fails → exception.
     * Insert planned record for shifts: planned_qty_morning/planned_qty_evening default split per rule.
   * Result: Tag ready for nesting.

3. **Nesting (T-1)**

   * Actor: Supervisor does single-tag nesting in CutExpert and exports file.
   * Upload file to SharePoint folder `/LPOs/LPO_<id>/CutSessions/`.
   * Power Automate triggers `POST /api/cuts/parse` with file URL + tag_id.
   * Azure Function `fn_parse_nesting`:

     * Idempotency: compute file_hash; if file_hash seen for same tag -> return DUPLICATE.
     * Validate single-tag (if multi-tag → create `exception_log (MULTI_TAG_NEST)` and return PARSE_REJECTED).
     * Extract: parts list (each part: id, area, dimensions), sheet usage, remnant candidates (dims & area), filler parts, total sheet area, net_parts_area, waste_area.
     * Compute `expected_consumption` per tag (net parts + allocated waste).
     * Compute `remnant` list (offcuts >= config.min_remnant_area).
     * Return JSON object: `{cut_session_id, expected_consumption, remnant_list, wastage_pct, produced_area_by_sheet}`
     * Insert `cut_session` (status = PARSED_OK) + `production_log` entries for parts.
     * Log parser output to audit file in SharePoint `.../Audit/parse_TAG...json`.
   * Result: Tag status = PARSED_OK. Expected consumption is definitive engineering truth.

4. **Allocation Engine (immediately after parse)**

   * Triggered by `cut_session.PARSED_OK`.
   * Azure Function `fn_allocate`:

     * Query `sap_snapshot` (latest), `inventory_txn` (since last snapshot) to compute `available_qty` for material_code.
     * If `available_qty >= expected_consumption`:

       * Create `allocation` rows: one per shift (morning/evening) with allocated_qty; create `inventory_txn` rows with `txn_type = ALLOCATE` (negative logical reservation). These are *soft reservations*.
       * Set `tag_sheet.status = ALLOCATED`.
     * Else if partial availability:

       * Create partial `allocation` for what is available; create `exception_log (SHORTAGE)` with recommended remediation: use remnant pool, re-schedule, expedite PO.
     * Allocation rows include `reserve_until` (e.g., T morning 07:00) and `reserved_by`.
     * Write `user_action_history`.
   * Result: Smartsheet shows Allocation Sheet with bins + quantities for picks.

5. **Pre-shift pick confirmation (Production Day)**

   * Actor: Storekeeper uses Smartsheet allocation sheet or mobile UI to confirm pick.
   * On pick confirm, Power Automate posts `POST /api/allocations/pick_confirm`.
   * Azure Function `fn_pick_confirm`:

     * Validate that allocation is active (not expired), and physical bin has stock (optionally confirm with handheld scan).
     * Write `inventory_txn` record `txn_type = PICK` (quantity moved to production preparation). Update `allocation.status = PICKED`.
     * If pick would cause negative physical system stock -> create `exception_log (PICK_NEGATIVE)` and block.
   * Result: Allocation status = PICKED; items are physically staged.

6. **Production / Consumption (During shift)**

   * Actor: Production operator records consumption by Tag + shift (Smartsheet form; may be batch at end of shift).
   * Power Automate triggers `POST /api/consumption/submit`.
   * Azure Function `fn_submit_consumption`:

     * Append `inventory_txn` entries `txn_type = ISSUE` for actual consumption (negative). If remnant used, `txn_type = REMNANT_USE`.
     * Validate: `actual_consumption <= allocated + remnant_available + tolerance`. If exceeded -> create `exception_log (OVERCONSUMPTION)` and block downstream DO generation for that Tag until resolved.
     * Update `production_log` and `tag_sheet` consumption aggregates.
     * If consumption < allocated → the remainder stays as `allocation.balance`.
   * Result: System records ground-truth consumption. (Note: for prototype this is manual submission; future CNC logs may auto-populate.)

7. **Dispatch Decision: Partial vs Full**

   * After consumption, PM checks Tag production status.
   * If `production_log.balance_remaining == 0` → PM marks Tag `READY_FOR_DISPATCH_FULL`.

     * Logistics generates DO for full Tag (Flow 8).
   * If `balance_remaining > 0` → PM decides Partial:

     * PM uses Smartsheet Load Builder to choose which parts to ship now (pick line items).
     * System generates partial DO (Flow 8) with those lines.
   * If PM wants to delay shipping, they mark Tag `PENDING`.

8. **DO generation & SAP / Virtual DO handling**

   * Actor: Logistics confirms DO via Smartsheet Load Builder.
   * Power Automate calls `POST /api/do/create`.
   * Azure Function `fn_create_do`:

     * Validate selected lines exist and `production_log.qty_shipped + do_qty <= qty_produced`.
     * Create `delivery_order` ledger record with lines (`delivery_order_lines`).
     * If LPO `status != LEGACY_PS` and SAP available -> call SAP Adapter to create DO in SAP (BAPI).

       * On success: record `sap_do_number` and set `delivery_order.status = SAP_CREATED`.
       * On SAP error: set `delivery_order.status = PENDING_SAP`, create `exception_log (SAP_CREATE_FAILED)` and queue retry.
     * If `LPO.status == LEGACY_PS` -> create `delivery_order` as `VIRTUAL_DO` and set `sap_blocked = true`.
     * Reserve DO quantities in `inventory_txn` as `txn_type = DO_ISSUE` (to prevent double dispatch).
   * Power Automate returns DO PDF to Logistics, saves to SharePoint LPO folder.
   * Result: DO created (SAP or VIRTUAL).

9. **POD (Proof of Delivery)**

   * Driver obtains signed POD (photo or e-sign).
   * Upload to SharePoint or Smartsheet; Power Automate triggers `POST /api/do/pod_upload`.
   * Azure Function `fn_pod_upload`:

     * Attach POD link to `delivery_order`, record `pod_timestamp`, `signed_by`.
     * Set `delivery_order.status = POD_UPLOADED`.
     * Signal Finance (Power Automate) to invoice DO (subject to gating rules).
   * Result: POD attached in LPO folder; invoice flow triggered.

10. **Invoicing / AR**

    * Finance either uses SAP to create invoice if DO in SAP, or for `VIRTUAL_DO` follows consolidated invoice process per legacy PS rules.
    * Ledger row `invoice_log` created with invoice number and DO links.
    * AR snapshot updated by scheduled SAP sync.

11. **Daily Physical Counting & Reconciliation**

    * Scheduled job `fn_reconcile`:

      * Pull `sap_snapshot` (inventory, PO, AR) via SAP connector.
      * Compute system view `system_closing_qty` from `inventory_txn`.
      * Pull `physical_counts` (from cycle count sheet).
      * Compute `variance = physical - system`.
      * Append `inventory_snapshot` row.
      * If `abs(variance) > tolerance`: create `exception_log (PHYSICAL_VARIANCE)` and assign to Store Manager.
    * Reconciliation includes mapping open virtual DOs (legacy PS) and unbilled DOs to exposed value.
    * Exceptions must be resolved (ADJUSTMENT transaction with 2-approver) or a SAP correction posted.

12. **Exception resolution**

    * All exception records route to Exception Board in Smartsheet (Power Automate cards).
    * Owner investigates (attach evidence: cut session, DO, POD, photos, CCTV).
    * Resolution actions:

      * Create `inventory_txn` ADJUSTMENT with approvers (for physical corrections). OR
      * Create SAP correction (via SAP Adapter). OR
      * Reconcile by moving remnant or adjusting allocations.
    * Once resolved, update exception status to RESOLVED and log user_action_history.

---

# 3. Deterministic data write order & ACID considerations

All critical sequences must be atomic or compensated. Use DB transactions where possible in Azure Function writes.

**Example: On successful parse → allocation**

1. Begin DB transaction.
2. Insert `cut_session` (status PARSED_OK).
3. Insert `production_log` rows.
4. Call `fn_compute_allocation` → may insert `allocation` rows and `inventory_txn` ALLOCATE events.
5. Commit transaction.
6. If step 4 fails or detects shortage → rollback and insert `exception_log` in a separate guaranteed write (append-only); notify PM.

**Important:** Ledger writes should be append-only (inventory_txn). Updates to `tag_sheet` statuses are permitted but should store previous state in `user_action_history` or a history table.

---

# 4. API Contracts (HTTP JSON) — minimal & idempotent

All POST endpoints accept `client_request_id` (UUID) for idempotency.

1. `POST /api/tags/ingest`

   * Payload:

```json
{
  "client_request_id": "uuid",
  "tag_id": "TAG-20260105-0001",
  "lpo_id": "LPO-1234",
  "required_area_m2": 120,
  "requested_delivery_date": "2026-01-10",
  "file_url": "https://sharepoint/...",
  "uploaded_by": "user@company"
}
```

* Responses:

  * 200 {status: "UPLOADED", tag_id, note}
  * 409 {status: "DUPLICATE", existing_tag_link}
  * 400 {error: "..."}

2. `POST /api/cuts/parse`

   * Payload: `{ client_request_id, tag_id, file_url, parser_version }`
   * Responses:

     * 200 { status: "PARSED_OK", cut_session_id, expected_consumption_m2, remnant_list: [...] }
     * 422 { status: "PARSE_REJECTED", reason: "MULTI_TAG" }

3. `POST /api/allocations/create` (usually triggered internally after parse)

   * Payload: `{ client_request_id, cut_session_id }`
   * Response:

     * 200 { status: "ALLOCATED", allocation_ids: [...], details }
     * 207 { status: "PARTIAL_ALLOCATED", allocation_ids: [...], shortages: [...] }
     * 409 { status: "SHORTAGE", suggested_actions: [...] }

4. `POST /api/allocations/pick_confirm`

   * Payload: `{ client_request_id, allocation_id, picked_by, picked_qty, bin_id }`
   * Response: 200 / 400 / 409

5. `POST /api/consumption/submit`

   * Payload:

```json
{
  "client_request_id":"uuid",
  "tag_id":"TAG-..",
  "shift":"M",
  "consumed_items":[{"material_code":"PH25","qty_m2":18.0, "remnant_id":null}],
  "submitted_by":"operator@company"
}
```

* Response:

  * 200 {txn_ids: [...], status: "ACCEPTED"}
  * 422 {status: "EXCEPTION", exception_id}

6. `POST /api/do/create`

   * Payload: `{client_request_id, tag_id, lines:[{prod_id, qty}], truck_id, driver_id}`
   * Response: 200 {do_id, sap_do_number:null|xxx, status}

7. `POST /api/do/pod_upload`

   * Payload: `{do_id, pod_file_url, uploaded_by}`
   * Response: 200 {status:"POD_UPLOADED"}

8. `GET /api/snapshots/inventory?date=2026-01-06`

   * Response: list of snapshot rows.

All responses contain `trace_id` for support.

---

# 5. Error handling & exception semantics (deterministic)

* **Idempotency:** All endpoints must accept `client_request_id`. If repeated, return same result for same client_request_id.
* **Partial failures:** If ledger writes fail mid-flow:

  * Use DB transaction where possible.
  * If DB transaction not possible across external writes (e.g., SAP call), mark primary ledger row and create `exception_log` with `recovery_action` and `retryable`.
* **Blocking gates:** DO generation and invoicing are blocked when:

  * Tag has open HIGH/CRITICAL exceptions.
  * Consumption for tag is unapproved or overconsumed.
  * LPO is ON_HOLD.
* **Retries:** All Azure Function calls to SAP are retried with exponential backoff; failed attempts generate `exception_log (SAP_CREATE_FAILED)`.
* **Alerts:** Power Automate sends adaptive card to assigned role and escalates on SLA expiry.

---

# 6. Data mappings — key tables affected per flow (developer checklist)

* Tag ingest: write `tag_sheet`.
* Parse nesting: write `cut_session`, `production_log`, `remnant_log` (if any), parser audit file.
* Allocate: write `allocation`, `inventory_txn (ALLOCATE)`.
* Pick confirm: write `inventory_txn (PICK)`, update `allocation.status`.
* Submit consumption: write `inventory_txn (ISSUE/REMA.. )`, update `production_log`, `tag_sheet.consumed_qty`.
* DO create: write `delivery_order`, `delivery_order_lines`, `inventory_txn (DO_ISSUE)`.
* POD upload: update `delivery_order` with POD link and status.
* SAP sync: write `sap_snapshot` (hourly/daily).
* Reconciliation: write `inventory_snapshot`, `exception_log`.

---

# 7. Config table (must be accessible & versioned)

* `min_remnant_area_m2`
* `vacuum_bed_dimensions` (LxW) — used to classify remnant usability
* `t1_cutoff_time_local` (e.g., 18:00) and `tz`
* `allocation_expiry_minutes`
* `variance_tolerance_pct`
* `remnant_value_fraction`
* `truck_capacity_by_type`
* All config changes must be stored with `effective_from`, `changed_by` and `approved_by`.

---

# 8. Security & governance

* Use **Azure AD** for authentication. All service calls use Managed Identity.
* Secrets (SAP credentials, DB connection strings) in **Key Vault**.
* RBAC:

  * Production Manager: release tags, approve overrides.
  * Supervisor: upload nesting.
  * Storekeeper: pick confirm & cycle counts.
  * Logistics: DO build & POD upload.
  * Finance: invoice approvals.
* All user actions logged in `user_action_history`. Approvals require two approvers for overrides (configurable).

---

# 9. Monitoring, metrics & alerts (SLA)

* **Health:** AppInsights for Azure Functions (exceptions, latency).
* **KPI dashboards (Power BI):**

  * T-1 nesting compliance rate (% of released tags parsed by cutoff).
  * Allocation fulfillment rate.
  * Unbilled produced value.
  * Inventory variance trend (daily).
  * Exception backlog by severity and age.
* **Alerts:**

  * Exception with severity CRITICAL -> Teams + pager.
  * Reconciliation variance > threshold -> daily email + exception created.
  * SAP connector failures -> admin page + email.

---

# 10. Developer tasks & priorities (how to start)

**Sprint 0 (setup)**

* Provision resources: Dataverse/Azure SQL, Azure Functions project, Power Automate environment, SharePoint library, Smartsheet sheets.
* Create config table entries.

**Sprint 1 (MVP core flows)**

* Implement `fn_ingest_tag` + Smartsheet Tag upload form + Power Automate trigger.
* Implement `fn_parse_nesting` parser & `cut_session` writes (use sample CutExpert files).
* Implement `fn_allocate` (simple availability check using mock `sap_snapshot`) and write allocation rows.
* Implement `inventory_txn` append logic & `inventory_snapshot` simple queries.
* Build Exception Log sheet + simple Power Automate notification.

**Sprint 2**

* Implement pick_confirm, consumption submit, DO create (virtual only), POD upload flow, and reconciliation job (mock SAP first).
* Implement dashboards and acceptance tests.

**Sprint 3**

* Integrate with SAP (BAPI/IDoc) and implement robust retry + exception processing.
* Harden security and approval flows.

---

# 11. Acceptance tests (must pass for prototype demo)

1. **T-1 happy path:** Upload Tag → Release → Nesting upload → Parse → Allocation created → Pick confirm → Submit consumption → create DO → upload POD → invoice log created. All steps produce expected ledger rows and no exceptions.
2. **Duplicate nesting:** Upload same nesting file twice → second attempt returns DUPLICATE and no double allocations.
3. **Shortage behavior:** Parse shows expected > available → partial allocate + exception created with remediation suggested.
4. **Overconsumption block:** Consumption submitted exceeding (allocated + remnant + tolerance) → creates exception and blocks DO creation for tag.
5. **Legacy PS DO:** Create DO for LPO marked LEGACY_PS → delivery_order is VIRTUAL_DO and SAP not called; POD upload works and invoice must be consolidated manually later.
6. **Reconciliation variance:** Post-shift physical count triggers variance > tolerance → creates exception and blocks invoicing for affected material until resolved.

---

# 12. Helpful code skeletons (pseudocode)

**Azure Function: parse nesting (simplified)**

```python
def fn_parse_nesting(request):
    client_request_id = request.json['client_request_id']
    tag_id = request.json['tag_id']
    file_url = request.json['file_url']

    file_bytes = download_file(file_url)
    file_hash = sha256(file_bytes)
    if file_hash_exists(tag_id, file_hash):
        return { 'status':'DUPLICATE', 'existing': get_existing() }

    data = parse_cutexpert(file_bytes)  # returns parts, remnant_candidates, sheet_usage
    if data.references_multi_tag():
        create_exception(tag_id, 'MULTI_TAG_NEST', severity='HIGH', data=summary)
        update_tag_status(tag_id, 'PARSE_REJECTED')
        return { 'status': 'PARSE_REJECTED' }

    cut_session_id = insert_cut_session(tag_id, file_hash, data.summary)
    insert_production_log(cut_session_id, data.parts)
    if data.remnants:
        for r in data.remnants:
            insert_remnant(cut_session_id, r)

    # return parse result to orchestrator
    return { 'status':'PARSED_OK', 'cut_session_id': cut_session_id, 'expected_consumption':data.total_consumption }
```

**Azure Function: allocation engine (simplified)**

```python
def fn_allocate(cut_session_id):
    data = get_cut_session(cut_session_id)
    material = data.material_code
    expected = data.expected_consumption
    available = compute_available(material)  # sap_snapshot + txns
    if available >= expected:
        create_allocation(tag_id=data.tag_id, qty=expected, shift='M/E', reserved_until=cfg['reserve_until'])
        create_inventory_txn('ALLOCATE', material, -expected, ref=cut_session_id)
        update_tag_status(data.tag_id, 'ALLOCATED')
        return { 'status':'ALLOCATED' }
    else:
        # partial alloc logic or shortage
        create_exception(data.tag_id, 'SHORTAGE', ...) 
        partial = max(0, available)
        if partial>0:
            create_allocation(... qty=partial)
        return { 'status':'PARTIAL', 'allocated':partial }
```

---

# 13. Migration & future extension notes

* The prototype must keep ledger field names and semantics identical to planned Azure SQL/Dataverse schema to avoid heavy migration later.
* Keep parser outputs and ledger writes deterministic and versioned (parser_version stored in `cut_session`).
* Later enhancements: CNC/CAM logs ingestion, barcode scanning for remnant & picks, ML remnant matching, truck loading optimizer.

---

# 14. Final checklist for developers

* [ ] Implement DB schema (tables listed in Data Structure doc)
* [ ] Implement Azure Functions: ingest_tag, parse_nesting, allocate, pick_confirm, submit_consumption, create_do, pod_upload, reconcile
* [ ] Implement Power Automate flows to call functions and present approvals
* [ ] Implement SharePoint structure and Smartsheet forms
* [ ] Implement idempotency using client_request_id & file_hash
* [ ] Implement exception_log with adaptive-card notifications
* [ ] Implement scheduled SAP snapshot job
* [ ] Create Power BI dashboards for T-1 compliance, exceptions, variance
* [ ] Provide test harness and run acceptance tests

---
