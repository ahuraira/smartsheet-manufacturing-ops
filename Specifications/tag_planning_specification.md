
# 1 — Goals for this stage

* Enforce T-1 nesting discipline (nesting export uploaded ≥ cut-off).
* Capture planned consumption in LPO (planned area) and per shift so forecasts are real.
* Validate LPO commercial coverage before scheduling.
* Validate machine/time capacity and avoid scheduling conflicts.
* Notify supervisor and create SLA reminders.
* Keep all business logic in Azure Functions (Power Automate = orchestration only).
* Produce auditable events (user_action_history + exceptions).

---

# 2 — Data model additions & Smartsheet design

## New / updated tables (canonical ledger)

Add or update these tables in your ledger DB (Dataverse / Azure SQL):

### `production_planning`

* `schedule_id` (GUID PK)
* `tag_id` (FK -> tag_sheet)
* `planned_date` (date)
* `shift` (enum: MORNING / EVENING)
* `machine_id` (FK -> machine_master)
* `planned_qty_m2` (decimal) — from tag_sheet.expected_consumption or planner override
* `status` (enum: PLANNED / RELEASED_FOR_NESTING / NESTING_UPLOADED / ALLOCATED / CANCELLED)
* `created_by`, `created_at`, `updated_by`, `updated_at`, `client_request_id`, `trace_id`
* `notes` (text)
  Index on `planned_date, shift, machine_id` for conflict detection.

### `machine_master`

* `machine_id`, `name`, `vacuum_bed_dims` (LxW), `sqm_per_hour` (for rough capacity checks), `available_shifts` (list), `status` (OPERATIONAL/MAINTENANCE).

### small updates

* `lpo_master`: add `planned_total_area` (sum of planned across tags) — kept by functions not by manual edits.
* `tag_sheet`: `planned_date`, `planned_shift`, `planned_machine`, `planning_status` (mirrors production_planning.status).

## Smartsheet: `Production Planning` sheet (UI for PM)

Columns (exact):

* `Schedule ID` (hidden; UTF8 GUID)
* `Tag ID` (dropdown/lookup)
* `Planned Date` (date)
* `Shift` (Morning/Evening)
* `Machine Assigned` (dropdown from machine_master)
* `Planned Qty (m2)` (number) — optional; default to tag.expected_consumption if present
* `PM` (person)
* `Status` (PLANNED / RELEASED_FOR_NESTING / BLOCKED / CANCELLED)
* `Response` (API status message)
* `Exception ID` (if any)
* `Notes`

Rules:

* Only PM role can change scheduling columns. Make `Status` read-only (updated by system).

---

# 3 — Flow & who does what (detailed sequence)

### Step A — PM schedules a Tag (Smartsheet)

* PM adds row to `Production Planning` with `TagID`, `PlannedDate`, `Shift`, `MachineAssigned`, `PlannedQty`.
* Smartsheet triggers Power Automate `Flow: ProductionScheduleRequest`.

### Step B — Orchestration: Power Automate → Azure Function

* Power Automate constructs payload:

```json
{
  "client_request_id":"uuid",
  "tag_id":"TAG-0001",
  "planned_date":"2026-02-10",
  "shift":"MORNING",
  "machine_id":"MACHINE-01",
  "planned_qty_m2":118.3,
  "requested_by":"pm@company"
}
```

* Call `POST /api/production/schedule` (Azure Function).

### Step C — Azure Function `fn_schedule_tag` (authoritative)

Performs deterministic work:

1. **Auth & Idempotency**

   * Validate `client_request_id`.
   * If previously processed, return previous response.

2. **Read tag & LPO**

   * Load `tag_sheet` and `lpo_master`.
   * If `tag_sheet.status` is CANCELLED or CLOSED → return BLOCKED.

3. **Validate LPO limits (commercial check)**

   * Compute `current_committed = delivered_qty + allocated_qty + planned_total_area`.
   * Check `current_committed + planned_qty <= po_quantity` (use tolerances).
   * If violation → create `exception_log` `INSUFFICIENT_PO_BALANCE` and return 422 with exception id.

4. **Machine & capacity checks**

   * Check `machine_master` exists and is OPERATIONAL.
   * Detect conflicts: any other `production_planning` row for same `machine_id`,`planned_date`,`shift` -> if conflict, return `CONFLICT_MACHINE_BUSY` or propose alternate machines (optional).
   * Check rough capacity: `planned_qty <= shift_hours * sqm_per_hour` (configurable). If exceeds, flag `CAPACITY_WARNING` (medium severity) but still allow scheduling optionally after PM override.

5. **Create / update `production_planning` entry**

   * Insert schedule row (status = `RELEASED_FOR_NESTING`).
   * Update `tag_sheet` `planned_date`, `planned_shift`, `planned_machine`, `planned_qty`.
   * Update `lpo_master.planned_total_area += planned_qty` (transactionally).
   * Write `user_action_history` event.

6. **Return success**

   * payload includes `schedule_id`, `status: RELEASED_FOR_NESTING`, `next_action_deadline` (T-1 cutoff timestamp), `trace_id`.

### Step D — Power Automate updates Smartsheet & notifies Supervisor

* Update `Production Planning` row `Status = RELEASED_FOR_NESTING`, `Response = OK`.
* Send adaptive card to Supervisor with link to Tag and instruction: perform T-1 nesting by `next_action_deadline`.
* Create Planner/ToDo task for Supervisor with SLA.

### Step E — Supervisor nesting (T-1)

* Supervisor performs single-tag nesting in CutExpert and uploads export to SharePoint folder (LPO/Tag path).
* SharePoint file create triggers Power Automate `Flow: ParseNestingTrigger` that calls `POST /api/cuts/parse` — azure parser function.

(Allocation and further flows continue as previously designed.)

---

# 4 — API contract for scheduling

**Endpoint:** `POST /api/production/schedule`

**Payload**

```json
{
  "client_request_id": "uuid",
  "tag_id": "TAG-20260105-0001",
  "planned_date": "2026-02-10",
  "shift": "MORNING",
  "machine_id": "MACHINE-01",
  "planned_qty_m2": 120.0,
  "requested_by": "pm@company",
  "notes": "Needs vacuum bed"
}
```

**Responses**

* `200 OK`

```json
{ "status":"RELEASED_FOR_NESTING", "schedule_id":"SCHED-0001", "next_action_deadline":"2026-02-09T18:00Z", "trace_id":"..." }
```

* `409 CONFLICT` (machine busy or duplicate schedule)
* `422 BLOCKED` (LPO on hold or insufficient PO balance) → returns `exception_id`
* `400 BAD_REQUEST` (invalid date/shift)

**Idempotency:** client_request_id required.

---

# 5 — Business rules & validations (SOTA)

* **Hard gates (must block)**

  * LPO status = ON_HOLD → block schedule.
  * Tag is CLOSED / CANCELLED → block schedule.
  * Duplicate schedule for same tag → block.
* **Soft gates (warnings)**

  * Machine capacity exceeded → create warning but allow if PM forces (requires approval & log).
  * LPO near full (balance below threshold) → warning, not block.
* **T-1 Enforcement**

  * Nesting must be uploaded by `T-1 cutoff` (config e.g., 18:00 previous day). If not uploaded:

    * System marks `schedule.status = DELAYED` and raises `exception SHORT_NESTING`.
    * Escalation: send adaptive card to Ops Manager + email; optionally deprioritize tag.
* **Change control**

  * Rescheduling after `PICK_CONFIRMED` or after nesting upload should require 2-approver override.
* **Planned quantity vs expected consumption**

  * If PM's `planned_qty` differs from `tag_sheet.expected_consumption` by > tolerance (config), create `PLANNED_MISMATCH` exception for PM review.

---

# 6 — Exceptions (comprehensive list + owner + SLA)

| Exception               |                                        Trigger | Owner       | Severity |    SLA |
| ----------------------- | ---------------------------------------------: | ----------- | -------: | -----: |
| INSUFFICIENT_PO_BALANCE |                    scheduled causes overcommit | PM + Sales  |     HIGH | 24 hrs |
| LPO_ON_HOLD             |                   LPO on hold at schedule time | PM          |     HIGH | 24 hrs |
| MACHINE_BUSY            | another schedule exists for same machine/shift | PM          |   MEDIUM |  8 hrs |
| CAPACITY_WARNING        |           planned_qty > machine shift capacity | PM          |      LOW | 48 hrs |
| DUPLICATE_SCHEDULE      |                             schedule duplicate | PM          |      LOW |  8 hrs |
| T1_NESTING_DELAY        |                 nesting not uploaded by cutoff | Ops Manager |   MEDIUM |  4 hrs |
| PLANNED_MISMATCH        |               planned_qty vs expected mismatch | PM          |      LOW | 24 hrs |

All exceptions are written to `exception_log` by Azure Functions. Power Automate sends adaptive cards to `assigned_to` and creates Planner tasks if SLA missed.

Resolution actions are written to `user_action_history` and any inventory adjustments are effected by authoritative Azure Function endpoints (not by Power Automate).

---

# 7 — Machine scheduling & capacity model (SOTA)

* Represent machine capacity as `sqm_per_shift` or derive from `sqm_per_hour * shift_hours`.
* Use `vacuum_bed_dims` to check remnant usability: if remnant dims fit bed => can be used as a remnant asset.
* Maintain calendar view in Smartsheet (or Power BI) for machine schedules.
* Optionally add an auto-scheduler later (Phase 2) to suggest best machine and shift to minimize waste.

---

# 8 — Notifications & SLA automation (Power Automate)

Flows to implement:

1. `ProductionScheduleRequest` — Smartsheet → call `fn_schedule_tag` → update Smartsheet → notify Supervisor.
2. `T1CutoffWatchdog` — scheduled (daily): query `production_planning` for items with `status=RELEASED_FOR_NESTING` and `now > next_action_deadline` → create `exception_log` via `POST /api/exceptions/create` and notify Ops Manager.
3. `NestingUploadTrigger` — SharePoint file create → call parser function.
4. `PMOverrides` — adaptive card approval flow → Power Automate collects approvals -> call `POST /api/overrides/resolve` to Azure Function which logs and applies.

Power Automate only orchestrates: build logic (validations, adjustments) in Functions.

---

# 9 — KPIs & Dashboard tiles (Power BI)

* **T-1 Nesting Compliance** = % scheduled tags with nesting parsed by cutoff. Target ≥ 98%.
* **Schedule Accuracy** = (planned_qty − actual_consumption)/planned_qty mean per week.
* **Machine Utilization** per shift.
* **Open Scheduling Exceptions** (priority by SLA).
* **Planned vs LPO Balance** – show PO capacity used by planned+allocated+delivered.

---

# 10 — Acceptance tests (must pass to roll to pilot)

1. **Happy path**: PM creates schedule → fn_schedule_tag returns `RELEASED_FOR_NESTING` → Smartsheet updated → Supervisor receives notification → nesting uploaded and parser receives file.
2. **PO balance block**: PM schedules tag that would overcommit PO → function returns BLOCKED with exception logged.
3. **Machine conflict**: two PMs schedule same machine/time → second receives CONFLICT (409) and is offered alternate machines (if implemented).
4. **T-1 enforcement**: schedule released but no nesting uploaded by cutoff → `T1_NESTING_DELAY` exception auto-created and escalated.
5. **Change control**: after nesting uploaded, PM attempts to reschedule → system requires 2 approvers and logs override in `user_action_history`.

---

# 11 — Implementation plan & priorities (what to build next)

**Sprint 1 (core)** — implement this first:

* Add `production_planning` table and `machine_master`.
* Implement `fn_schedule_tag` (API), with idempotency, DB writes, LPO check, capacity check.
* Power Automate flow `ProductionScheduleRequest` to call function and notify supervisor.
* Update Smartsheet `Production Planning` to call flow.

**Sprint 2 (nesting integration)**:

* Implement `T1CutoffWatchdog` scheduled job.
* Hook SharePoint nesting upload → parser flow (already exists).
* Connect parser `PARSED_OK` to allocation engine.

**Sprint 3 (hardening)**:

* Approvals for overrides, dashboards, alerts, and acceptance tests.

---

# 12 — Edge cases & recommended handling

* **PM schedules without expected_consumption present**: accept but set `planned_qty` as not authoritative; parser post-parse will reconcile and create a `PLANNED_MISMATCH` exception if big difference.
* **Machine goes to maintenance after schedule**: maintenance event create `exception MACHINE_MAINTENANCE` and notify PM; reschedule required.
* **Tag moved between LPOs or PO revised**: LPO revision can create `PO_QUANTITY_CONFLICT`; require PM+Finance approval.
* **Multiple tags nested on same sheet (shouldn’t happen)**: parser rejects `MULTI_TAG_NEST` — returns exception and marks schedule `PARSE_REJECTED`.
* **Late urgent orders**: add `PRIORITY` flag for expedited handling; require override approvals and log cost-of-failure.

---

# 13 — Governance & process notes for PMs / Supervisors

* PM: schedule tags T-1 by cutoff; validate machine selection; respond to exceptions within SLA.
* Supervisor: perform single-tag nesting, upload to SharePoint, tag must be parsed.
* Storekeeper: confirm picks at start-of-shift; if a planned pick cannot be made, create exception immediately.
* Ops Manager: review T-1 delayed items daily.

Document this SOP and show PMs the Smartsheet view + Planner tasks as part of training.

---
