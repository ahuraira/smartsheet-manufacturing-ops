# ⚡ Power Automate Flow Architecture

> **Document Type:** Specification | **Version:** 1.0.0 | **Last Updated:** 2026-01-08

---

## 📋 Quick Links

| Related Documents |
|-------------------|
| [Architecture Specification](./architecture_specification.md) - Overall system architecture |
| [Tag Ingestion Architecture](./tag_ingestion_architecture.md) - Tag flow details |
| [Setup Guide](../docs/setup_guide.md) - Power Automate setup |
| [Troubleshooting](../docs/howto/troubleshooting.md) - Common issues |

---

## Design Principles

### 1. Single Responsibility
Each flow does ONE thing well. No monolithic flows.

### 2. Orchestrator Pattern
- **Trigger Flows** → Detect events, minimal logic, call worker flows
- **Worker Flows** → Business logic, data writes, exception creation
- **Utility Flows** → Reusable components (logging, exceptions, notifications)

### 3. Idempotency Everywhere
- Every write operation checks for duplicates first
- `client_request_id` passed through entire chain
- Same input = same output (no side effects on retry)

### 4. Fail-Fast with Recovery
- Validate early, fail early
- Create exception records before failing
- All failures are recoverable via exception resolution

---

## Flow Inventory

```
┌─────────────────────────────────────────────────────────────────┐
│                      TRIGGER FLOWS                               │
├─────────────────────────────────────────────────────────────────┤
│ TRG_Tag_Upload          - Smartsheet form submission            │
│ TRG_Nesting_Upload      - SharePoint file created               │
│ TRG_Status_Change       - Smartsheet cell changed               │
│ TRG_Scheduled_Reconcile - Daily schedule (18:00)                │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      WORKER FLOWS                                │
├─────────────────────────────────────────────────────────────────┤
│ WRK_Ingest_Tag          - Validate & create tag record          │
│ WRK_Parse_Nesting       - Parse file, create cut session        │
│ WRK_Allocate            - Reserve inventory for shift           │
│ WRK_Pick_Confirm        - Confirm physical pick                 │
│ WRK_Submit_Consumption  - Record actual usage                   │
│ WRK_Create_DO           - Generate delivery order               │
│ WRK_Reconcile           - Compare SAP/System/Physical           │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      UTILITY FLOWS                               │
├─────────────────────────────────────────────────────────────────┤
│ UTL_Log_User_Action     - Append to User Action Log             │
│ UTL_Create_Exception    - Create exception with notification    │
│ UTL_Send_Notification   - Teams/Email adaptive cards            │
│ UTL_Check_Idempotency   - Verify client_request_id uniqueness   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Flow Details

### TRG_Tag_Upload (Trigger)

**Trigger:** Smartsheet form submission (Tag Sheet Registry)

**Actions:**
1. Extract row data from trigger
2. Generate `client_request_id` (GUID)
3. Call `WRK_Ingest_Tag` with payload
4. Return response to user (success/error)

**No business logic here** - just routing.

---

### WRK_Ingest_Tag (Worker)

**Input:**
```json
{
  "client_request_id": "uuid",
  "tag_name": "TAG-001-Rev1",
  "lpo_sap_reference": "PTE-185",
  "required_delivery_date": "2026-01-15",
  "estimated_quantity_m2": 120,
  "file_url": "https://sharepoint/.../tag.pdf",
  "submitted_by": "user@company.com"
}
```

**Flow Steps:**

```
┌──────────────────────────────────────────────────────────┐
│ 1. IDEMPOTENCY CHECK                                     │
│    └─ Query Tag Sheet: WHERE client_request_id = input   │
│    └─ If exists → Return existing record (no duplicate)  │
└──────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────┐
│ 2. FILE HASH CHECK                                       │
│    └─ Download file, compute SHA256                      │
│    └─ Query Tag Sheet: WHERE file_hash = computed        │
│    └─ If exists → Call UTL_Create_Exception(DUPLICATE)   │
│                 → Return {status: "DUPLICATE"}           │
└──────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────┐
│ 3. LPO VALIDATION                                        │
│    └─ Query LPO Master: WHERE sap_reference = input      │
│    └─ If not found → Call UTL_Create_Exception(LPO_NA)   │
│    └─ If status = "On Hold" → Exception(LPO_ON_HOLD)     │
│    └─ If remaining_qty < threshold → Exception(LPO_LOW)  │
└──────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────┐
│ 4. CREATE TAG RECORD                                     │
│    └─ Add row to Tag Sheet Registry                      │
│       - tag_id: AUTO_NUMBER                              │
│       - status: "Draft"                                  │
│       - file_hash: computed                              │
│       - client_request_id: input                         │
└──────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────┐
│ 5. LOG USER ACTION                                       │
│    └─ Call UTL_Log_User_Action                           │
│       - action_type: "TAG_CREATED"                       │
│       - target_table: "Tag Sheet Registry"               │
│       - target_id: new_tag_id                            │
└──────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────┐
│ 6. RETURN SUCCESS                                        │
│    └─ {status: "UPLOADED", tag_id: "...", message: "..."}│
└──────────────────────────────────────────────────────────┘
```

---

### UTL_Log_User_Action (Utility)

**Called by:** ALL worker flows after any data change

**Input:**
```json
{
  "user_id": "user@company.com",
  "action_type": "TAG_CREATED | TAG_UPDATED | ALLOCATION_CREATED | ...",
  "target_table": "Tag Sheet Registry",
  "target_id": "TAG-001",
  "old_value": null,
  "new_value": "{...json...}",
  "notes": "Created via form submission",
  "trace_id": "correlation-guid"
}
```

**Actions:**
1. Generate `action_id` (GUID)
2. Add row to `98 User Action Log`
3. Return success

**This flow is fire-and-forget** - caller doesn't wait for response.

---

### UTL_Create_Exception (Utility)

**Called by:** Worker flows when validation fails or anomalies detected

**Input:**
```json
{
  "source": "WRK_Ingest_Tag",
  "related_tag_id": "TAG-001",
  "related_txn_id": null,
  "material_code": null,
  "quantity": null,
  "reason_code": "DUPLICATE_UPLOAD",
  "severity": "MEDIUM",
  "assigned_to": "production.manager@company.com",
  "attachment_links": "https://sharepoint/...",
  "trace_id": "correlation-guid"
}
```

**Actions:**
1. Generate `exception_id` (GUID)
2. Calculate `sla_due` based on severity:
   - CRITICAL: +4 hours
   - HIGH: +24 hours
   - MEDIUM: +48 hours
   - LOW: +72 hours
3. Add row to `99 Exception Log`
4. Call `UTL_Send_Notification` with adaptive card
5. Return `exception_id`

---

### UTL_Check_Idempotency (Utility)

**Called by:** Every worker flow as FIRST step

**Input:**
```json
{
  "client_request_id": "uuid",
  "target_table": "Tag Sheet Registry"
}
```

**Actions:**
1. Query target table for matching `client_request_id`
2. If found → Return `{exists: true, existing_record: {...}}`
3. If not → Return `{exists: false}`

**Caller decides** what to do with duplicate.

---

## Exception Reason Codes

| Code | Severity | Source | Description |
|------|----------|--------|-------------|
| `DUPLICATE_UPLOAD` | MEDIUM | WRK_Ingest_Tag | Same file hash already exists |
| `LPO_NOT_FOUND` | HIGH | WRK_Ingest_Tag | Referenced LPO doesn't exist |
| `LPO_ON_HOLD` | HIGH | WRK_Ingest_Tag | LPO is currently on hold |
| `LPO_INSUFFICIENT` | HIGH | WRK_Ingest_Tag | LPO remaining qty too low |
| `MULTI_TAG_NEST` | HIGH | WRK_Parse_Nesting | Nesting contains multiple tags |
| `PARSE_FAILED` | CRITICAL | WRK_Parse_Nesting | Could not parse nesting file |
| `SHORTAGE` | HIGH | WRK_Allocate | Insufficient inventory |
| `PICK_NEGATIVE` | CRITICAL | WRK_Pick_Confirm | Would cause negative stock |
| `OVERCONSUMPTION` | HIGH | WRK_Submit_Consumption | Exceeded allocation + tolerance |
| `PHYSICAL_VARIANCE` | MEDIUM | WRK_Reconcile | Physical ≠ System count |
| `SAP_CREATE_FAILED` | HIGH | WRK_Create_DO | SAP API call failed |

---

## Role Assignments

| Exception Type | Primary Assignee | Escalation |
|----------------|------------------|------------|
| LPO issues | Sales Manager | Commercial Head |
| Nesting issues | Production Supervisor | PM |
| Inventory shortage | Store Manager | PM |
| Consumption variance | Production Supervisor | PM |
| SAP failures | IT Support | IT Manager |

---

## Notification Strategy

### Adaptive Cards (Teams)
- Sent for HIGH and CRITICAL exceptions
- Contains: Exception details, action buttons, links to related records

### Email Digest
- Sent daily at 08:00
- Summary of open exceptions by severity

### In-App (Smartsheet)
- All exceptions visible in Exception Log sheet
- Filtered views per role

---

## Error Handling Pattern

Every worker flow follows this pattern:

```
TRY:
    1. Idempotency check
    2. Validation
    3. Business logic
    4. Data writes
    5. User action log
    6. Return success
    
CATCH:
    1. Create exception record
    2. Log user action (OPERATION_FAILED)
    3. Return error with exception_id
    
FINALLY:
    - All paths return a response
    - No silent failures
```

---

## Data Write Order (Atomicity)

Since Power Automate doesn't support transactions, we use this order:

1. **Log intent** (User Action Log with status "IN_PROGRESS")
2. **Perform writes** (main data)
3. **Update intent** (User Action Log with status "COMPLETED")
4. If step 2 fails → Create exception, update intent to "FAILED"

This creates an audit trail even for partial failures.

---

## Next Steps

1. ✅ Review and approve this architecture
2. Create UTL_Log_User_Action flow
3. Create UTL_Create_Exception flow
4. Create UTL_Check_Idempotency flow
5. Create WRK_Ingest_Tag flow
6. Create TRG_Tag_Upload flow
7. Test end-to-end tag ingestion
