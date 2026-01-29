# Canonical Material Mapping — Implementation Specification (SOTA, Developer Guide)

**Purpose:** authoritative, production-ready specification for implementing the canonical material mapping and LPO-driven allocation feature. This document is focused, deterministic, and designed for the pilot (Smartsheet-first) and production (Azure SQL) migration. It includes unit-conversion guidance and all developer-facing details needed to implement reliably.

---

# Executive summary (one line)

Map *nesting functional descriptions* → **canonical material codes**, then map canonical → **SAP SKU(s)** using **LPO-driven brand selection** (overrides), with exact-match mapping only, clear audit trail, and deterministic allocation; do unit conversions centrally and consistently.

---

# Design principles (non-negotiable)

1. **Deterministic first:** exact-string match + scoped overrides. No fuzzy/regex automation.
2. **LPO-driven brand selection:** brand & SAP SKU selection is driven by the LPO; mapping uses this info on allocation.
3. **Two-layer mapping:** (a) Nesting → Canonical; (b) Canonical → SAP (via LPO map).
4. **Auditability:** every mapping decision stored in `mapping_history` (trace_id, who, when, why).
5. **Separation of roles:** Procurement/Stores manage mapping data; Developers build deterministic service.
6. **Idempotency & replay:** same inputs and mapping DB state produce same outputs; actions idempotent.
7. **Pilot-first, migration-ready:** implement using Smartsheet tables now, migrate to SQL with identical schema later.
8. **Unit conversion:** centralized, authoritative conversion factors; conversions recorded in history.

---

# High-level architecture

```
Nesting Parser (Azure Function) 
  └─ POST /map/lookup -> Mapping Service (Azure Function) -> reads Mapping Sheets (Smartsheet) or SQL (prod)
       ├─ material_master (canonical)
       ├─ mapping_override (LPO/Project/Customer/Plant)
       └─ lpo_material_brand_map (LPO → canonical → SAP sku list)
Mapping Service returns mapping result -> Parser stores mapping + mapping_history -> Allocation Engine uses mapping + SAP snapshot -> Allocations created
```

Components:

* **Mapping Service**: stateless HTTP API + queue consumer (reads mapping sources; caches).
* **Material Mapping Store**: Phase 1: Smartsheet sheets (Material Mapping, Overrides, LPO Material Map, History, Exceptions). Phase 2: Azure SQL tables (schema below).
* **Allocation Engine**: uses LPO brand map + SAP snapshot to create allocations at day-of-production.
* **Power Automate**: human flows for exception resolution and admin updates (UI).
* **Blob Storage**: raw parsed files for audit.

---

# Data model (canonical, migration-ready)

> Phase 1: Smartsheet sheets with these columns (exact names).
> Phase 2: SQL tables with same columns.

### `material_master` (canonical definitions)

* `nesting_description` (string, normalized lowercase) — **unique**
* `canonical_code` (string) — internal canonical id
* `default_sap_code` (string, nullable)
* `uom` (string) — `m`, `m2`, `kg`, `pcs`
* `not_tracked` (bool) — true for blades, wd40
* `active` (bool)
* `notes`, `updated_at`, `updated_by`

### `mapping_override`

* `scope_type` (`LPO`/`PROJECT`/`CUSTOMER`/`PLANT`)
* `scope_value` (string)
* `nesting_description` (string)
* `canonical_code` (string)
* `sap_code` (string, optional)
* `active`, `effective_from`, `effective_to`, `created_by`, `created_at`

### `lpo_material_brand_map` (LPO → canonical → SAP SKUs)

* `lpo_id`
* `canonical_code`
* `sap_code`
* `priority` (int, 1 preferred)
* `active`, `notes`

### `mapping_history` (immutable audit)

* `history_id`
* `ingest_line_id` (parser id)
* `nesting_description`
* `canonical_code` (nullable)
* `sap_code` (nullable)
* `decision` (`AUTO`/`OVERRIDE`/`MANUAL`/`REVIEW`)
* `user_id` (if manual)
* `trace_id`
* `created_at`
* `notes` (reason, CSV of candidates if used)

### `mapping_exception` (work queue)

* `exception_id`, `ingest_line_id`, `nesting_description`, `status`, `assigned_to`, `created_at`, `trace_id`

---

# Exact mapping runtime behavior (step-by-step)

**API contract:** `POST /api/map/lookup` (JSON)

**Request payload:**

```json
{
  "ingest_line_id":"uuid",
  "nesting_description":"Aluminum Tape",
  "tag_id":"TAG-1001",
  "lpo_id":"LPO-555",
  "project_id":"PROJ-001",
  "plant_id":"PLANT-A",
  "qty":110.55,
  "uom":"m",
  "trace_id":"uuid"
}
```

**Processing steps (deterministic):**

1. **Normalize** `nesting_description` → trimmed, lowercased; remove extra spaces. *Do not* remove business words.
2. **Check overrides (highest precedence)**:

   * Query `mapping_override` for nesting_description + active overrides in this order: `LPO`, `PROJECT`, `PLANT`, `CUSTOMER`.
   * If override exists and active → return `canonical_code` and `sap_code` if provided; record `mapping_history` with `decision = OVERRIDE`.
3. **Exact-match in `material_master`:**

   * Lookup row where `nesting_description = normalized` and `active = true`.
   * If found:

     * If `not_tracked = true`: return canonical & default_sap (if exists) with flag `not_tracked=true`. `decision = AUTO`.
     * Else return canonical + default_sap (may be null) with `decision = AUTO`.
   * Append `mapping_history`.
4. **No match → create `mapping_exception`:**

   * Insert row in `mapping_exception`.
   * Emit event for Power Automate notification to mapping owners (Adaptive Card with link to create mapping).
   * Return response: `decision = REVIEW`, `exception_id`.
5. **Idempotency:** If `mapping_history` already exists for `ingest_line_id`, return existing mapping (do not duplicate).
6. **Return response** (see below).

**Synchronous response (success):**

```json
{
  "decision":"AUTO",
  "canonical_code":"CAN_TAPE_AL",
  "sap_code":"UL181AFST",    // may be null
  "not_tracked": false,
  "history_id": 123,
  "trace_id":"uuid"
}
```

**REVIEW response:**

```json
{ "decision":"REVIEW", "exception_id": 987, "trace_id":"uuid" }
```

---

# Allocation (day-of-production) overview

* The Allocation Engine runs T-1 (or on demand) for tags planned for a date/shift.
* For each `bom_line` (canonical_code, qty, uom):

  1. Query `lpo_material_brand_map` for the `lpo_id` + `canonical_code` sorted by `priority`.
  2. For each SAP code in priority order:

     * Check `sap_snapshot.unrestricted_qty` (latest).
     * Allocate min(available, required).
     * Reduce `required`.
  3. If required remains > 0 after all SAP codes:

     * Create `allocation_exception` (insufficient stock).
     * Mark allocation partial and notify procurement/PM.
* Save `allocation_log` lines (canonical → SAP code, qty allocated).
* Upstream systems (pick lists, DO generation) use `allocation_log`.

**Important:** Allocation does not change mapping. It uses LPO mapping to pick SKUs. Mapping and allocation decisions are audited separately.

---

# Unit conversion (specification & implementation)

**Principles:**

* Centralize conversions in a single service/module (UnitService).
* Store canonical UOM per canonical code (in `material_master.uom`).
* Store conversion factors per SAP SKU in `sap_material` table: `conversion_to_canonical` (float).

  * Example: For a tape sold in rolls (1 roll = 30 m), conversion factor = 30 (rolls → meters).
  * For panels, if SAP track per sheet, conversion factor = sheet_area_m2.
* All calculations normalize to **canonical UOM** (the `material_master.uom`) before summing/allocating.

**Flow:**

1. Parser outputs `qty` in nesting UOM (e.g., m2, m).
2. Mapping service returns `canonical_code` with `canonical_uom`.
3. Invocation of `UnitService.convert(qty, from_uom, to_uom)` returns normalized quantity.

   * If `from_uom == to_uom`: return same value.
   * If `from_uom == 'sheet'` and `canonical_uom == 'm2'`: multiply by sheet_area (sheet dims from parse).
   * Use stored conversion factors or master table (for each SAP SKU): `sap_material.conversion_to_canonical`.
4. Allocation engine uses normalized canonical quantity to allocate SAP SKUs:

   * compute required_canonical_qty
   * for a candidate SAP sku, determine `available_canonical_qty = sap_unrestricted_qty * conversion_to_canonical`
   * allocate accordingly and record both SAP qty and canonical qty in `allocation_log`.
5. **Store conversion detail in history**: every `mapping_history` and `allocation_log` row must include conversion factor used and both unit values (sap_qty, canonical_qty) for audit.

**UnitService requirements:**

* Accurate, immutable conversion factors stored in `sap_material` or `conversion_master`.
* Support compound conversions: area → linear (rare), use explicit rules only.
* Use decimal (high precision) math and round only at last display.

---

# Smartsheet vs SQL (where to store what)

**Phase 1 — Smartsheet-first (pilot)**

* **Material Management sheets**: `Material Master`, `Mapping Overrides`, `LPO Material Map`, `Mapping History`, `Mapping Exceptions`.
* **Parser** writes mapping results into BOM rows in a Smartsheet `Parsed BOM` or a separate `Ingested BOM` sheet.
* **Mapping Service** can read Smartsheet sheets directly (use caching) and write mapping_history & exceptions back to Smartsheet.

**Phase 2 — SQL (production)**

* Migrate sheets to **Azure SQL** with the schema above.
* Ensure identical column names and semantics; write migration job to move Smartsheet rows to SQL.
* Mapping Service switches to SQL reads; caching layer (Redis) remains.

**Guidelines:**

* Files (raw parsed JSON) go to Blob storage as immutable evidence.
* Smartsheet is the *editable UI* for procurement/stores; SQL is the single source of truth.

---

# Admin & human workflows

1. **Seed the Material Master** with canonical rows (procurement). Use supplied Excel to bulk import.
2. **Manage Overrides**: when a customer/LPO requires a specific brand, procurement adds an override row (scope LPO).
3. **Exception triage**: mapping exceptions produce Teams Adaptive Card (Power Automate) to mapping owner; owner picks canonical + sap code; update `Material Master` or add `mapping_override` for future deterministic picks.
4. **Reconciliation**: daily report of `allocated_qty vs sap_reserved_qty vs actual_consumed` produced and exceptions escalated.

---

# API summary (developer reference)

* `POST /api/map/lookup` — core mapping lookup (see payload above). Synchronous.
* `POST /api/map/manual` — admin route to insert mapping (updates material_master); requires auth.
* `GET /api/map/history?ingest_line_id=` — audit.
* `POST /api/allocation/run` — run allocation for date/shift (protected).
* `GET /api/allocation/status?tag_id=` — allocation status.

All APIs must require Azure AD auth (system principals for services; user auth for UI). Include `trace_id` and return it in responses.

---

# Implementation guidance & best practices

* **Caching:** cache material_master, overrides, and lpo_material_map for N minutes (config). On update, invalidate cache (Power Automate or webhook).
* **Concurrency:** mapping service must check mapping_history for existing `ingest_line_id` to ensure idempotency.
* **Retries:** mapping service must tolerate Smartsheet API transients; implement exponential backoff for Smartsheet reads.
* **Logging:** structured logs with `trace_id`, `ingest_line_id`, `action`, `duration_ms`, `outcome`.
* **Security:** store keys in Key Vault; use managed identity for SQL/Blob; role-based access for admin UI.
* **Testing:** provide unit tests for normalization, override precedence, idempotency, conversion, and allocation splits.
* **Monitoring:** metrics: auto_map_rate, exception_rate, mapping_latency, override_count, allocation_success_rate.
* **Rollout plan:** seed mapping table, run historical re-maps to minimize exceptions, pilot with one production line.

---

# Acceptance criteria (must pass)

1. `Material Master` covers ≥90% of pilot nesting descriptions (seeded).
2. Overrides apply deterministically by LPO (tests: create override, confirm mapping for that LPO).
3. Mapping service returns within 200ms for cached data.
4. All mapping decisions recorded in `mapping_history` with `trace_id`.
5. Unit conversion module used for all calculations; conversion stored in history.
6. Allocations created T-1 for all planned tags; allocation logs persisted and can generate pick lists.
7. Exceptions are routed to mapping owners via Power Automate; resolution creates mapping rows and prevents duplicates.

---

# Examples (quick)

**Material Master row (Smartsheet row):**

* nesting_description = `aluminum tape`
* canonical_code = `CAN_TAPE_AL`
* default_sap_code = `UL181AFST`
* uom = `m`
* not_tracked = `No`

**Override row (LPO-specific):**

* scope_type = `LPO`
* scope_value = `LPO-555`
* nesting_description = `aluminum tape`
* canonical_code = `CAN_TAPE_AL`
* sap_code = `UL181AFMY`
* active = `Yes`

**Mapping History entry:**

* ingest_line_id = `uuid`
* nesting_description = `aluminum tape`
* canonical_code = `CAN_TAPE_AL`
* sap_code = `UL181AFMY`
* decision = `OVERRIDE`
* user_id = `procurement@company`
* trace_id = `...`

---

# Deliverables for developers (what to implement)

1. Smartsheet sheets: Material Master, Mapping Overrides, LPO Material Map, Mapping History, Mapping Exceptions (column names exactly as spec).
2. Azure Function `map/lookup` implementing the deterministic algorithm. Caching layer for mapping sheets.
3. UnitService for conversions and conversion data table (`sap_material.conversion_to_canonical`).
4. Allocation Engine (T-1 runner) that consumes canonical BOMs and LPO brand maps and writes `allocation_log`.
5. Power Automate flows: (a) mapping exception notification & resolution; (b) admin cache invalidation on mapping update.
6. SQL DDL to migrate sheets later and services to read SQL in prod.
7. Tests: unit and integration (mapping + override + conversions + allocation splits).
8. Monitoring dashboards and alerts.

---

# Appendix — minimal SQL DDL (practical)

```sql
CREATE TABLE material_master (
  mapping_id INT IDENTITY PRIMARY KEY,
  nesting_description NVARCHAR(500) NOT NULL UNIQUE,
  canonical_code VARCHAR(50) NOT NULL,
  default_sap_code VARCHAR(50) NULL,
  uom VARCHAR(10) NULL,
  not_tracked BIT DEFAULT 0,
  active BIT DEFAULT 1,
  created_by VARCHAR(200),
  created_at DATETIME2 DEFAULT SYSUTCDATETIME(),
  updated_at DATETIME2
);

CREATE TABLE mapping_override (
  override_id INT IDENTITY PRIMARY KEY,
  scope_type VARCHAR(20),
  scope_value VARCHAR(200),
  nesting_description NVARCHAR(500),
  canonical_code VARCHAR(50),
  sap_code VARCHAR(50) NULL,
  active BIT DEFAULT 1,
  effective_from DATE NULL,
  effective_to DATE NULL,
  created_by VARCHAR(200), created_at DATETIME2 DEFAULT SYSUTCDATETIME()
);

CREATE TABLE lpo_material_brand_map (
  id INT IDENTITY PRIMARY KEY,
  lpo_id VARCHAR(100),
  canonical_code VARCHAR(50),
  sap_code VARCHAR(50),
  priority INT DEFAULT 1,
  active BIT DEFAULT 1
);

CREATE TABLE mapping_history (
  history_id INT IDENTITY PRIMARY KEY,
  ingest_line_id UNIQUEIDENTIFIER,
  nesting_description NVARCHAR(500),
  canonical_code VARCHAR(50),
  sap_code VARCHAR(50),
  decision VARCHAR(20),
  user_id VARCHAR(200) NULL,
  trace_id UNIQUEIDENTIFIER,
  created_at DATETIME2 DEFAULT SYSUTCDATETIME(),
  notes NVARCHAR(MAX) NULL
);

CREATE TABLE mapping_exception (
  exception_id INT IDENTITY PRIMARY KEY,
  ingest_line_id UNIQUEIDENTIFIER,
  nesting_description NVARCHAR(500),
  status VARCHAR(20) DEFAULT 'OPEN',
  assigned_to VARCHAR(200),
  created_at DATETIME2 DEFAULT SYSUTCDATETIME(),
  trace_id UNIQUEIDENTIFIER
);
```

---


