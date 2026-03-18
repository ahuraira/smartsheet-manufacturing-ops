# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added

#### `fn_pending_items` — Adaptive Card `tag_choices` field (`GET /api/pending-items`)
- **`TagChoice` model** added to `shared/flow_models.py` — Pydantic model with `title` and `value` string fields, matching the `Input.ChoiceSet` choice schema expected by Power Automate adaptive cards.
- **`tag_choices: List[TagChoice]`** field added to `PendingItemsResponse` — a deduplicated, ordered list of unique tag IDs derived from `pending_tags`. Built with a `seen_tags` set so each tag ID appears exactly once even if it has multiple allocations.
- **`TagChoice` exported** from `shared/__init__.py`.

**Why:** Power Automate previously had to extract tag IDs from `pending_tags`, deduplicate them, and reshape them into `title`/`value` pairs before building the adaptive card `Input.ChoiceSet`. `tag_choices` provides this ready-to-use, eliminating all post-processing in the flow.

**Example response addition:**
```json
"tag_choices": [
  { "title": "TAG-1001", "value": "TAG-1001" },
  { "title": "TAG-1002", "value": "TAG-1002" }
]
```

#### `MARGIN_APPROVAL_LOG` Sheet Definition (`shared/logical_names.py`)
- **`Sheet.MARGIN_APPROVAL_LOG`** added to `Sheet` class
- **`Column.MARGIN_APPROVAL_LOG`** class added with 13 columns: `APPROVAL_ID`, `TAG_SHEET_ID`, `LPO_ID`, `MATERIAL_COST_AED`, `OTHER_COSTS_AED`, `GM_EXC_TAX_PCT`, `CORP_TAX_AED`, `TARGET_MARGIN_VARIANCE_PCT`, `STATUS`, `CLIENT_REQUEST_ID`, `CARD_JSON`, `CREATED_AT`
- Added to `SHEET_COLUMNS` mapping

**Why:** `margin_orchestrator.py` referenced `Sheet.MARGIN_APPROVAL_LOG` but it was never defined, causing `AttributeError` at runtime.

#### `INVENTORY_SNAPSHOT` Column Definition (`shared/logical_names.py`)
- **`Column.INVENTORY_SNAPSHOT`** class added with columns: `MATERIAL_CODE`, `SYSTEM_CLOSING`, `UOM`, `LAST_COUNT_DATE`

**Why:** `fn_stock_snapshot` was using hardcoded column name strings instead of manifest-based lookups.

#### `processed_submission_id` field (`shared/flow_models.py`)
- **`processed_submission_id: Optional[str]`** added to `SubmissionResult` model — allows clients to track submission ID for polling status.

#### Exception Logging in Endpoint Catch Blocks
- All v1.7.0+ endpoint functions now create `EXCEPTION_LOG` records via `create_exception()` in their outermost `except Exception` blocks: `fn_pending_items`, `fn_submission_status`, `fn_stock_snapshot`, `fn_allocations_aggregate`, `fn_submit_consumption`, `fn_confirm_submission`, `fn_submit_stock`.

**Why:** Previously, unhandled errors were logged to stdout only and never reached the Exception Log sheet.

#### Distributed Lock + Audit Logging in `fn_confirm_submission`
- Wrapped the read-update section in `AllocationLock` keyed on `processed_submission_id` to prevent concurrent approve/reject races.
- Added `log_user_action()` call for each approval/rejection status change.

**Why:** Two simultaneous approve/reject requests could race on the same submission rows. Approvals were not tracked in the User Action Log.

### Changed

#### Queue Lock Default Timeout (`shared/queue_lock.py`)
- Default timeout increased from **30s → 60s** to accommodate slow Smartsheet API calls.
- Added `is_likely_held()` method to `LockHandle` for callers to check if lock is still valid before critical writes.
- Added expiry warning in `release_allocation_lock()` when elapsed time exceeds timeout.
- Added docstring warning to `AllocationLock` about visibility timeout limitations and lack of automatic renewal.

#### Margin Orchestrator — Enum-Based Column References (`shared/margin_orchestrator.py`)
- Replaced all hardcoded string column names (e.g., `"APPROVAL_ID"`) with `Column.MARGIN_APPROVAL_LOG.*` enum references.
- Cached `get_all_column_ids()` result in a local variable instead of calling it 10 times.

### Planned
- `fn_allocate` - Inventory allocation function
- `fn_pick_confirm` - Pick confirmation function
- `fn_create_do` - Delivery order creation function

### Fixed
- Fixed `wastage_pct` datatype mismatch (sending as float instead of string) in LPO ingestion.
- Fixed `AttributeError` in `fn_ingest_tag` by ensuring `requested_delivery_date` is accessed as a dictionary key.

#### CRITICAL: Variance Calculation Used User-Submitted Allocation Qty (`shared/consumption_service.py`)
- **Before:** Variance check used `line.allocated_qty` from user's submission — user could bypass variance checks by submitting inflated qty.
- **After:** Uses `material_info.allocated_qty` from system allocation records (via `aggregate_materials()`).

#### CRITICAL: `fn_stock_snapshot` Hardcoded Column Names
- **Before:** Used `"Material Code"`, `"System Closing"`, `"UOM"`, `"Last Count Date"` string literals.
- **After:** Uses `manifest.get_column_name(Sheet.INVENTORY_SNAPSHOT, Column.INVENTORY_SNAPSHOT.*)`.

#### Allocation Already-Consumed Validation (`shared/consumption_service.py`)
- Added validation rejecting consumption against allocations with status `"Consumed"` — returns `ALREADY_SUBMITTED` error.

#### Non-Deterministic Tag Selection (`shared/consumption_service.py`)
- Changed `list(tag_ids)[0]` → `sorted(tag_ids)[0]` for deterministic behavior across runs.

#### Silent Skip on Unmapped Materials (`shared/consumption_service.py`)
- Added `logger.warning()` when a consumption line's material cannot be mapped to an allocation ID.

#### Margin Orchestrator Failure Now Creates Exception Record (`shared/consumption_service.py`)
- When `trigger_margin_approval_for_tag()` fails, now creates an `EXCEPTION_LOG` record instead of only logging to stdout.

#### BOM Conversion Factor Zero Handling (`fn_parse_nesting/bom_orchestrator.py`)
- Changed `if result.conversion_factor:` → `if result.conversion_factor is not None:` — `0.0` is falsy in Python and was silently skipping conversion.
- Added warning log for zero conversion factors.

#### Mapping Service Timezone Inconsistency (`fn_map_lookup/mapping_service.py`)
- Fixed override effective date validation: `datetime.now().date()` → `datetime.utcnow().date()` to match cache timestamps.

#### Mapping Service Stale Override Cache (`fn_map_lookup/mapping_service.py`)
- Override cache now resets timestamp on API failure to avoid hammering the API on every subsequent call.

#### Mapping Service Invalid Conversion Factor Logging (`fn_map_lookup/mapping_service.py`)
- Added `logger.warning()` for invalid conversion factor values (e.g., `"N/A"`, `"TBD"`) instead of silent `pass`.

#### `fn_stock_snapshot` Bare Except
- Replaced `except:` with `except Exception as e:` and added error details to the log.

#### BOM Orchestration Test Fix (`tests/integration/test_bom_orchestration.py`)
- Added missing `sap_uom="m"` to test mock `MappingResult` in `test_mapping_with_unit_conversion` — test was broken since v1.8.0 SAP UOM refactor.

#### CRITICAL: Margin Orchestrator Wrong Sheet Reference (`shared/margin_orchestrator.py`)
- **Before:** Used `Sheet.LPO_MASTER` / `Column.LPO_MASTER.DELIVERED_QUANTITY_SQM` to read SQM from TAG_REGISTRY rows — column name mismatch caused 0 SQM.
- **After:** Uses `Sheet.TAG_REGISTRY` / `Column.TAG_REGISTRY.TOTAL_AREA_SQM`.

#### CRITICAL: Margin Orchestrator KeyError on None Column (`shared/margin_orchestrator.py`)
- **Before:** `manifest.get_column_name()` returning `None` was used as dict key in `col_ids[None]` → `KeyError("None")`.
- **After:** Null guard validates all column resolutions, raises `ValueError` with missing column names.

#### `datetime.now()` → `datetime.utcnow()` Across Codebase
- Fixed 13 occurrences across `audit.py`, `models.py`, `manifest.py`, `id_generator.py`, `helpers.py`, `consumption_service.py`, `fn_lpo_ingest`, `fn_lpo_update`, `fn_ingest_tag`, `fn_schedule_tag`.

#### Bare `float()` → `parse_float_safe()` Across Codebase
- Fixed 25+ occurrences across `consumption_service.py`, `allocation_service.py`, `allocation_engine.py`, `stock_service.py`, `atomic_update.py`, `costing_service.py`, `fn_stock_snapshot`, `fn_pending_items`, event dispatcher handlers.

#### Costing Service f-string Syntax Error (`shared/costing_service.py`)
- **Before:** `f"Error reading config key {config_key}: str({e})"` — `str()` was literal text.
- **After:** `f"Error reading config key {config_key}: {e}"`.

#### `fn_process_manager_approval` Hardcoded Column Names
- Replaced 3 hardcoded column strings with `Column.MARGIN_APPROVAL_LOG.*` enum references.
- Replaced direct `client._make_request()` calls with proper `client.update_row()`.

#### `fn_confirm_submission` Idempotency
- Added idempotency check: if submission status already matches target, returns 200 `ALREADY_PROCESSED`.

### Added (Audit & Observability)

#### `log_user_action()` Across Write Operations
- Added audit logging in: `allocation_engine.py` (allocation creation), `consumption_service.py` (consumption submission + tag completion), `margin_orchestrator.py` (approval record creation), `fn_process_manager_approval` (DO creation, approval update, tag dispatch).

#### `create_exception()` in Error Paths
- Added exception records in: `allocation_engine.py` (row + txn write failures), `consumption_service.py` (orphaned materials, inventory txn failure), `margin_orchestrator.py` (pending tags, approval write, PA dispatch), `costing_service.py` (config, SAP price, LPO lookup), `fn_allocate` (outer catch), `fn_process_manager_approval` (10 error paths), `fn_confirm_submission` (submission not found).

#### Distributed Locking in `fn_process_manager_approval`
- Wrapped read-modify-write section with `AllocationLock` to prevent concurrent approval races.

#### Human-Readable Smartsheet Logging (`shared/smartsheet_client.py`)
- Log messages now show `SHEET_NAME (numeric_id)` instead of just `numeric_id` — e.g., `"Added row to sheet CONSUMPTION_LOG (728462)"`.

### Tests
- **431 tests passing** (all green after fixes)

---

## [1.8.0] - 2026-03-09

### Overview
Refactors the material mapping architecture to a **3-sheet lookup model**, fixes downstream BOM unit conversion to produce SAP-compatible quantities, and fixes the allocation engine to operate by SAP code (not canonical code) throughout — aligning it with SAP as the source of truth.

### Changed

#### Material Mapping — 3-Sheet Lookup (`fn_map_lookup/mapping_service.py`)

**Previous:** 05a Material Master contained both identity and conversion factors. Override sheet (05b) was the only source of SAP code differentiation. No brand awareness.

**New lookup flow:**
1. **05a Material Master** — resolves `nesting_description` → `canonical_code` + `default_sap_code` (identity only; no conversion factors)
2. **05b Mapping Override** — checks for brand/LPO overrides with strict precedence: `LPO > BRAND > PROJECT > CUSTOMER`. Effective date range enforced.
3. **05c SAP Material Catalog** — single source of truth for `SAP_CODE`, `SAP_UOM`, `UOM`, and `CONVERSION_FACTOR`. Looked up by the resolved SAP code (from override or default).

- Added `CatalogEntry` dataclass for 05c catalog entries
- Added `_catalog_cache` (keyed by `sap_code`, 5-minute TTL) alongside the existing material master and override caches
- Added `brand` parameter to `MappingService.lookup()` — enables brand-scoped override resolution
- `MaterialMasterEntry` no longer stores conversion factors (moved entirely to 05c)
- `invalidate_cache()` now clears all three caches atomically
- Added `get_cache_stats()` for monitoring

#### Brand Propagation (`fn_parse_nesting/`, `fn_parse_nesting/bom_orchestrator.py`)

- Added `brand` parameter to `process_bom_from_record()`, `BOMOrchestrator.process()`, and `BOMOrchestrator._map_lines()`
- `fn_parse_nesting/__init__.py` now passes `brand` (extracted from LPO details) through to BOM processing
- `brand` is forwarded to `MappingService.lookup()` enabling brand-specific SAP code resolution (e.g., WTI vs KIMMCO aluminium tape codes)

#### BOM Conversion — SAP-Compatible Output (`fn_parse_nesting/bom_orchestrator.py`)

- **Before:** `CANONICAL_QUANTITY` was converted to `result.uom` (nesting UOM — e.g., `m`). Not SAP-compatible.
- **After:** `CANONICAL_QUANTITY` is converted to `result.sap_uom` (e.g., `ROL`). `CANONICAL_UOM` now reflects SAP UOM.
- Conversion only runs when both `sap_uom` and `conversion_factor` are present (from 05c catalog).

| PARSED_BOM Column | Contents |
|---|---|
| `QUANTITY` + `UOM` | Raw from nesting file (e.g., 100 m) |
| `CANONICAL_QUANTITY` + `CANONICAL_UOM` | SAP-compatible (e.g., 4 ROL) |
| `SAP_CODE` | Resolved SAP material code |
| `CANONICAL_CODE` | Internal bridge code (lookup intermediary only) |

#### Allocation Engine — SAP Code Throughout (`shared/allocation_engine.py`)

- **Before:** Aggregated materials by `CANONICAL_CODE`, checked stock by `CANONICAL_CODE`, and wrote `CANONICAL_CODE` to `ALLOCATION_LOG` and `INVENTORY_TXN_LOG`. SAP inventory snapshot lookups would never match.
- **After:** Reads `SAP_CODE` from `PARSED_BOM`, aggregates by SAP code, checks available stock by SAP code, and writes SAP code to `ALLOCATION_LOG.MATERIAL_CODE` and `INVENTORY_TXN_LOG.MATERIAL_CODE`.
- `MaterialNeed.canonical_code` renamed to `MaterialNeed.sap_code`

### Fixed
- **Allocation stock check never matched** — `compute_available_qty()` was called with `canonical_code` but `SAP_INVENTORY_SNAPSHOT` is keyed by SAP code. Now correctly passes `sap_code`.
- **BOM quantities not SAP-compatible** — `CANONICAL_QUANTITY` was in nesting UOM; now always in SAP UOM from 05c catalog.

### Logical Names (`shared/logical_names.py`)
- Renamed `Sheet.LPO_MATERIAL_BRAND_MAP` → `Sheet.SAP_MATERIAL_CATALOG`
- Updated `Column.SAP_MATERIAL_CATALOG` with full 05c column set: `MAP_ID`, `NESTING_DESCRIPTION`, `CANONICAL_CODE`, `SAP_CODE`, `UOM`, `SAP_UOM`, `CONVERSION_FACTOR`, `NOT_TRACKED`, `ACTIVE`, `NOTES`, `UPDATED_AT`, `UPDATED_BY`

### Tests
- Rewrote `tests/unit/test_mapping_service.py` (13 tests) covering:
  - AUTO mapping with catalog conversion factor
  - Brand override selecting different SAP code
  - LPO override taking priority over BRAND override
  - Default fallback when no override matches
  - Missing catalog entry (no conversion factor, still succeeds)
  - Idempotency via Mapping History
  - Exception creation for unknown materials
  - Cache avoidance, cache invalidation, API refresh for all three caches
  - Cache statistics

---

## [1.7.0] - 2026-02-23

### Overview
Introduces 9 new Azure Functions to power Teams adaptive card workflows via Power Automate. Covers the full consumption lifecycle: query pending allocations, aggregate materials, submit/approve consumption, capture stock counts, and create exceptions.

### Added

#### Infrastructure
- **`shared/queue_lock.py`** - Distributed locking via Azure Queue Storage:
  - `acquire_allocation_lock()` - Locks allocation IDs using message visibility timeouts
  - `release_allocation_lock()` - Releases lock by deleting queue messages
  - `AllocationLock` - Context manager for automatic lock release on exit
  - Crash-resilient: locks auto-expire if process dies (no dangling locks)

- **`shared/flow_models.py`** - Pydantic request/response models for all flow endpoints:
  - Request models: `ConsumptionSubmission`, `ConsumptionLine`, `StockSubmission`, `StockLine`, `SubmissionConfirmRequest`, `AllocationAggregateRequest`, `ExceptionCreateRequest`
  - Response models: `SubmissionResult`, `SubmissionStatusResponse`, `PendingItemsResponse`, `AllocationAggregateResponse`, `StockSnapshotResponse`, `ExceptionCreateResponse`
  - Enums: `SubmissionStatus`, `WarningCode`, `ErrorCode`

- **`shared/allocation_service.py`** - DRY allocation business logic:
  - `get_pending_allocations()` - Shared query logic (status, date, shift filters)
  - `aggregate_materials()` - Aggregates qty across allocations; calculates consumed/remaining

- **`shared/consumption_service.py`** - Core consumption business logic:
  - `validate_consumption()` - Variance checking, material validation, allocation existence checks
  - `submit_consumption()` - Full flow: idempotency → lock → validate → atomic write
  - Variance thresholds: **5% WARN**, **10% ERROR** (configurable via env vars)

- **`shared/logical_names.py`** - Extended with:
  - `Column.ALLOCATION_LOG` (11 columns: `ALLOCATION_ID`, `TAG_SHEET_ID`, `MATERIAL_CODE`, `QUANTITY`, `PLANNED_DATE`, `SHIFT`, `STATUS`, `STOCK_CHECK_FLAG`, `ALLOCATED_AT`, `RESERVED_UNTIL`, `REMARKS`)
  - `Column.CONSUMPTION_LOG` (9 columns: `CONSUMPTION_ID`, `TAG_SHEET_ID`, `STATUS`, `CONSUMPTION_DATE`, `SHIFT`, `MATERIAL_CODE`, `QUANTITY`, `REMNANT_ID`, `REMARKS`)

- **`shared/__init__.py`** - Exported all new modules and models

#### Read Endpoints (Phase 1)
- **`fn_pending_items`** (`GET /api/pending-items?plant=&shift=&max=`) - Lists pending allocations for Teams card display. Filters by status (Submitted/Approved), date (today/yesterday), and optional shift.
- **`fn_submission_status`** (`GET /api/submission/status/{submission_id}`) - Returns submission status (PENDING_APPROVAL, APPROVED, REJECTED). Returns 404 if not found.
- **`fn_stock_snapshot`** (`GET /api/stock/snapshot?plant=`) - Returns current inventory snapshot with last count date for variance detection. Gracefully handles missing snapshot sheet.

#### Aggregation Endpoint (Phase 2)
- **`fn_allocations_aggregate`** (`POST /api/allocations/aggregate`) - Aggregates material quantities across selected allocations. Returns allocated/already-consumed/remaining quantities per material code. Used to populate Teams consumption input card.

#### Write Endpoints (Phase 3)
- **`fn_submit_consumption`** (`POST /api/submission/consumption`) - Critical path write with:
  - **Idempotency** via `submission_id` (safe to retry)
  - **Distributed locking** on `allocation_ids` (prevents race conditions)
  - **Variance validation** (WARN at 5%, ERROR blocks at 10%)
  - Writes one `CONSUMPTION_LOG` row per material line
  - Returns `SubmissionResult` with status (OK/WARN/ERROR), warnings, and errors

- **`fn_confirm_submission`** (`POST /api/submission/confirm`) - Supervisor approve/reject flow:
  - Updates status to `Approved` or `Adjustment Requested`
  - Records approver and notes in remarks
  - Rejected submissions retained as `REJECTED` (not deleted)

- **`fn_submit_stock`** (`POST /api/submission/stock`) - Stock count submission endpoint (placeholder; full write logic to be added with INVENTORY_SNAPSHOT integration)

- **`fn_create_exception_api`** (`POST /api/exception`) - Programmatic exception creation via existing `create_exception()` helper

### New Environment Variables
| Variable | Default | Description |
|---|---|---|
| `AZURE_STORAGE_CONNECTION_STRING` | *(required)* | Azure Queue Storage connection |
| `QUEUE_LOCK_TIMEOUT_MS` | `30000` | Lock visibility timeout in ms |
| `VARIANCE_WARN_THRESHOLD_PCT` | `5` | Variance % that triggers a warning |
| `VARIANCE_ERROR_THRESHOLD_PCT` | `10` | Variance % that blocks submission |

---

## [1.6.9] - 2026-02-04

### SOTA Review Fixes

This release addresses critical and major issues identified during ruthless SOTA review.

### Added
- **Atomic Update Helper** (`shared/atomic_update.py`):
  - `atomic_increment()` - Safe read-modify-write with retry on collision
  - `atomic_set_if_equals()` - Compare-and-swap operation
  - Exponential backoff with jitter (100ms-3s, max 5 retries)
  - Detects Smartsheet 4004 collision errors

- **Generic File Upload Flow** (Power Automate Abstraction):
  - `FileUploadItem` model - Standardized file/content/subfolder structure
  - `trigger_upload_files_flow()` - Generic helper to upload files to any LPO subfolder
  - `docs/flows/generic_file_upload_flow.md` - New documentation with JSON schema

- **Warnings in Response** - Non-fatal failures now tracked:
  - Response includes `warnings` array when issues occur
  - Each warning has `code` and `message` fields
  - Enables transparency without blocking success

- **fn_lpo_ingest** - SharePoint file upload integration:
  - Uploads files to SharePoint via Power Automate (single batched call)
  - PDF/other files → `LPO Documents` subfolder
  - Excel files (xlsx, xls, csv) → `Costing` subfolder

- **fn_ingest_tag** - SharePoint file upload integration:
  - Uploads tag files to SharePoint via Power Automate
  - All files → `Tag Sheets` subfolder
  - Uses LPO's `FOLDER_URL` to determine destination

### Changed
- **LPO ALLOCATED_QUANTITY Update** (CRITICAL FIX):
  - Previously: Unsafe read-modify-write caused race conditions
  - Now: Uses `atomic_increment()` with retry on collision
  - Prevents lost updates under concurrent access

- **Exception Creation for Failures** (CRITICAL FIX):
  - Production Planning update failure → LOW severity exception
  - LPO allocation update failure → MEDIUM severity exception
  - Blob storage upload failure → LOW severity exception
  - Power Automate trigger failure → LOW severity exception
  - All failures logged and tracked, not silently swallowed

- **Planning Row Selection** (`validate_tag_is_planned`):
  - Previously: Took first row without ordering (could select wrong row)
  - Now: Filters inactive statuses (Cancelled, Completed, Closed, Archived)
  - Now: Sorts by `planned_date` descending (most recent first)
  - Falls back to `modifiedAt` if planned_date not set

- **Thread-Safe Singleton** (`get_flow_client()`):
  - Added `threading.Lock()` for double-check locking pattern
  - Prevents race conditions during FlowClient initialization

- **Module-Level Imports** (`fn_parse_nesting/__init__.py`):
  - Moved inline imports to module level for performance
  - Reduces import overhead in hot paths

### Fixed
- `test_nesting_logger.py` - Updated expectations for `PLANNED_DATE` field
- `test_nesting_validation.py` - Added mocks for new v1.6.9 functions

---

## [1.6.8] - 2026-02-04

### Added
- **LPO ID Generation** - Auto-generated sequential IDs for new LPOs:
  - `ConfigKey.SEQ_LPO` - New sequence counter in Config sheet
  - `generate_next_lpo_id(client)` - New function in `id_generator.py`
  - `fn_lpo_ingest` now populates `LPO_ID` column (e.g., "LPO-0001")

- **Email Resolution Helper** - Centralized user ID to email conversion:
  - `resolve_user_email(client, user_id)` - New helper in `helpers.py`
  - Converts numeric Smartsheet user IDs to email addresses
  - Falls back to original value if resolution fails

- **Tag Ingestion Fields** - Additional fields from staging:
  - `TagIngestRequest.location` - Location from staging sheet
  - `TagIngestRequest.remarks` - Remarks from staging sheet

### Changed
- **LPOIngestRequest** - SAP reference coercion:
  - Numeric SAP references (e.g., `12345`) now coerced to strings
  - Avoids validation errors when Power Automate passes numbers

- **fn_lpo_ingest** - User field improvements:
  - `CREATED_BY` now resolved to email (not raw Smartsheet ID)

- **fn_ingest_tag** - Multiple fixes:
  - `SUBMITTED_BY` now resolved to email
  - `LPO_ALLOWABLE_WASTAGE` parsed with `parse_float_safe()` (fixes 0 issue)
  - `LOCATION` field now populated from request
  - `REMARKS` field now uses request.remarks instead of trace ID

---

## [1.6.7] - 2026-02-03

### Added
- **AREA_TYPE Column Support** - Internal/External billing area for LPO contracts:
  - `Column.LPO_MASTER.AREA_TYPE` - Added to `logical_names.py`
  - `Column.LPO_INGESTION_STAGING.AREA_TYPE` - Added to `logical_names.py`
  - `LPOIngestRequest.area_type` - New optional field (default: "External")
  - `fn_lpo_ingest` - Now persists `AREA_TYPE` to LPO record

- **Nesting Backtracking Flow** - Comprehensive data enrichment and backtracking:
  - `validate_tag_is_planned()` - Prerequisite validation (tag must be scheduled before nesting)
  - `get_lpo_details()` - Fail-fast LPO fetch with Brand/Area Type enrichment
  - `ValidationResult` model extended with `brand`, `area_type`, `lpo_row_id`, `planning_row_id`, `planned_date`

### Changed
- **fn_parse_nesting** - Enhanced with backtracking and enrichment:
  - Fails fast if tag is not scheduled in Production Planning
  - Fails fast if LPO is missing or Brand is not set
  - Logs `brand` and `planned_date` to NESTING_LOG
  - Updates Production Planning status to "Nesting Uploaded"
  - Increments LPO `ALLOCATED_QUANTITY` based on area type (Internal/External)
  - Response JSON now includes `enrichment` block with contextual data
  - Uploads nesting JSON AND original Excel file to Azure Blob Storage (`nesting-outputs` container)
  - Triggers Power Automate flow for email notification and file copying
  - Fetches LPO `FOLDER_URL` and passes it to response/PA (no longer constructed manually)
  - Updates Tag Registry `ESTIMATED_QUANTITY` with actual consumed area (Internal/External)
  - **SAP Reference now optional**: If not provided in payload, derived from Tag Registry `LPO_SAP_REFERENCE`

- **nesting_logger.log_execution()** - Now accepts `brand` and `planned_date` parameters

- **shared/power_automate.py** - Added `trigger_nesting_complete_flow()` for nesting completion

- **shared/blob_storage.py** - NEW: Azure Blob Storage helper for JSON uploads

---

## [1.6.6] - 2026-01-30

### Added
- **LPO Shared Services Module** - Centralized LPO operations for DRY compliance:
  - `shared/lpo_service.py` - New module with lookup, validation, and data extraction
  - `find_lpo_by_sap_reference()` - Lookup by SAP Reference
  - `find_lpo_by_customer_ref()` - Lookup by Customer LPO Reference
  - `find_lpo_flexible()` - Multi-field lookup (replaces duplicated `_find_lpo`)
  - `get_lpo_quantities()` - Returns `LPOQuantities` dataclass with po_qty, delivered, planned, allocated
  - `get_lpo_status()` - Extract normalized status
  - `validate_lpo_status()` - Check if LPO is on hold
  - `validate_po_balance()` - PO balance validation with 5% tolerance

- **Production Planning Staging Handler** - Automated scheduling via staging sheet:
  - `fn_event_dispatcher/handlers/schedule_handler.py` - New handler for staging sheet events
  - Routes to `fn_schedule_tag` when row created in staging sheet
  - Added `03h Production Planning Staging` to `create_workspace.py`
  - Routing already configured in `event_routing.json` as `schedule_tag`

- **Schedule Handler Tests** - Tests that verify actual behavior (not just mocks):
  - `test_fn_schedule_tag_is_actually_called` - Verifies main() is invoked
  - `test_dedup_returns_immediately` - Verifies early return on duplicate
  - `test_dispatch_result_uses_valid_fields_only` - Verifies model compatibility
  - `test_missing_tag_id_creates_exception` - Verifies exception creation

### Changed
- **fn_ingest_tag**: Now uses `find_lpo_flexible()` from shared service
- **fn_schedule_tag**: Now uses `get_lpo_quantities()` for PO balance check
- **Foundation for Phase 2**: Allocation, consumption, DO generation, invoicing

### Fixed
- **CRITICAL: schedule_handler.py silent failures** - Complete rewrite to match tag_handler pattern:
  - Handler was returning `READY` but never invoking fn_schedule_tag → now calls it directly
  - Handler was passing invalid fields to `DispatchResult` (silently ignored) → fixed to use only valid fields
  - Early dedup check was not returning → now returns immediately if already processed

- **CRITICAL: Robust Deduplication & Race Condition Handling** (v1.6.7) - Fixed concurrent retry issues:
  - **fn_lpo_ingest**: Detects race condition locally -> returns `ALREADY_PROCESSED` (200) instead of duplicate SAP exception (409) if client_request_id matches.
  - **fn_ingest_tag**: Detects race condition locally for file hash -> returns `ALREADY_PROCESSED` (200) instead of duplicate file exception (409).
  - **schedule_handler**: Implemented timestamp-based dedup for updates (`updated-{timestamp}`) to allow valid reschedules while preventing retry duplicates.

- **Missing Fields & Data Quality**:
  - **tag_handler**: New `RECEIVED_THROUGH` extraction from staging (defaults to 'API').
  - **lpo_handler**: Added `normalize_percentage()` for wastage (handles 18, 0.18, 18% -> 18.0).
  - **audit.py**: Added automatic email resolution for `log_user_action` (converts numeric Smartsheet user IDs to emails).

- **logical_names.py sync** - Updated `PRODUCTION_PLANNING_STAGING` columns to match manifest:
  - Removed: `REQUESTED_BY`, `API_STATUS`, `API_MESSAGE` (don't exist)
  - Added: `RESPONSE`, `EXCEPTION_ID`, `REMARKS` (exist in manifest)

- **New enum values added to models.py**:
  - `ExceptionSource.SCHEDULE` - For scheduling-related exceptions
  - `ReasonCode.SCHEDULE_INVALID_DATA` - For schedule validation errors
  - `ReasonCode.SYSTEM_ERROR` - For unexpected system errors

---

## [1.6.5] - 2026-01-30

### Fixed
- **CRITICAL: Duplicate Exception Creation** - Fixed webhook retries creating multiple exceptions:
  - Added `CLIENT_REQUEST_ID` column to `99 Exception Log` sheet
  - `create_exception()` now performs idempotency check before creating
  - If exception with same `client_request_id` exists, returns existing ID
  - Prevents duplicate exception rows when Power Automate retries a webhook

- **"Existing Tag: None" Error Message** - Fixed column name resolution in duplicate detection:
  - `fn_ingest_tag` now uses `_get_physical_column_name()` for tag ID lookup
  - Fallbacks to common column names (`Tag Sheet Name / Rev`) and row `id`
  - Exception message now correctly shows existing tag ID

### Added
- **Handler-Level Early Dedup Check** - Added at earliest point in processing:
  - `tag_handler.py`: Checks `TAG_REGISTRY.CLIENT_REQUEST_ID` before fetching row
  - `lpo_handler.py`: Checks `LPO_MASTER.CLIENT_REQUEST_ID` before fetching row
  - Returns `ALREADY_PROCESSED` immediately if staging row was already ingested
  - Prevents any duplicate processing on webhook retries

- **Exception Idempotency Parameter** - `create_exception()` accepts `client_request_id`:
  - Passed through from all exception calls in `fn_ingest_tag` and handlers
  - Enables exception deduplication without database-level constraints

### Changed
- **Schema Updates**:
  - `logical_names.py`: Added `CLIENT_REQUEST_ID` to `EXCEPTION_LOG` class
  - `create_workspace.py`: Added `Client Request ID` column to Exception Log sheet
  - `fetch_manifest.py`: Added column mapping for new Exception Log column
  - Added `LPO_INVALID_DATA` to Reason Code picklist options

### Technical Details
Root cause analysis identified 3 issues with duplicate processing:
1. No dedup at exception creation level (now fixed with `client_request_id` check)
2. Column name mismatch in duplicate detection message (now uses manifest resolution)
3. Handler dedup check happened after webhook retry entry (now at earliest point)

### DRY Compliance Refactor
- **Centralized `get_physical_column_name` helper** - Eliminated code duplication:
  - Moved duplicate function from 4 files to `shared/helpers.py`
  - Affected files: `fn_ingest_tag`, `fn_lpo_ingest`, `fn_lpo_update`, `fn_schedule_tag`
  - All modules now use `from shared import get_physical_column_name`
  - Added `_manifest = None` in function modules for test backward compatibility
  - Updated test patches to include `shared.manifest.get_manifest`
  - All 420 tests passing

---

## [1.6.4] - 2026-01-29

### Fixed
- **CRITICAL: Duplicate Record Creation** - Fixed non-deterministic `client_request_id` in handlers:
  - Removed timestamp from idempotency key: `staging-tag-{row_id}` instead of `staging-{row_id}-{timestamp}`
  - Same staging row now ALWAYS produces the same idempotency key
  - Prevents duplicate LPO/Tag creation on webhook retries or rapid event succession
  - Affected files: `tag_handler.py`, `lpo_handler.py`

- **LPO Over-Allocation** - Fixed balance check to include allocated quantity:
  - Formula now correctly uses: `delivered + allocated + planned <= PO Quantity`
  - Prevents tags from over-committing an LPO before delivery
  - Implements architecture spec §2 Step 2

### Changed
- **Override Table Caching** - `MappingService` now caches `MAPPING_OVERRIDE` sheet:
  - Prevents N+1 API calls during BOM processing (was 1 call per line)
  - Uses same 5-minute TTL as Material Master cache
  - Significantly reduces timeout risk for large BOMs (>100 lines)
  - **Date Logic Implemented**: Now correctly respects `EFFECTIVE_FROM` and `EFFECTIVE_TO` columns (v1.6.4)

- **Event Status Tracking** - `fn_event_processor` now updates event_log status:
  - Events now show `SUCCESS` or `FAILED` instead of staying `PENDING` forever
  - Closes the audit trail gap in the function adapter
  - Non-blocking: status update failure doesn't fail the event processing

---

## [1.6.3] - 2026-01-29

### Added
- **Multi-File Attachment Support for Tags** (SOTA - DRY principle):
  - `TagIngestRequest` now accepts `files: List[FileAttachment]` (reuses `FileAttachment` model).
  - `get_all_files()` helper method for backward compatibility with legacy single-file fields.
  - `fn_ingest_tag` uses `compute_combined_file_hash()` for duplicate detection (same as LPO).
  - All files attached to Tag Registry row after creation.
  - `tag_handler.py` fetches ALL row attachments from staging and passes them to ingestion.
- **Webhook Log Filtering** (SOTA - Log Hygiene):
  - `fn_webhook_receiver` now filters out events missing context (`sheet_id` or `row_id`).
  - Skips are logged at `DEBUG` level to reduce noise in main logs.
- **Robust Attachment Handling**:
  - Fixed `get_row_attachments` to fetch individual details (resolves missing URLs).
  - `attach_url_to_row` automatically downloads/re-uploads files if URL > 500 chars (overcoming Smartsheet API limit).
  - Refactored `lpo_handler.py` to use shared `extract_row_attachments_as_files` helper.

### Changed
- **Tag Name Mapping**: `tag_handler.py` now extracts `Tag Sheet Name/ Rev` from staging and populates `tag_name`.

---

## [1.6.2] - 2026-01-28

### Fixed
- **Tag Ingestion Validation** - Fixed `fn_ingest_tag` handler column mapping:
  - Corrected `REQUESTED_DELIVERY_DATE` -> `REQUIRED_DELIVERY_DATE`
  - Corrected `REQUIRED_AREA_M2` -> `ESTIMATED_QUANTITY`
  - Corrected `LPO_SAP_REFERENCE` -> `LPO_SAP_REFERENCE_LINK`
  - Resolves "Validation error in staging row" due to `None` values caused by incorrect logical names.

---

## [1.6.1] - 2026-01-28

### Added
- **`UnitService` Module** (`shared/unit_service.py`) - Centralized unit conversion:
  - Supports explicit conversion factors from Material Master
  - Fallback to standard conversions (mm ↔ m, cm ↔ m)
  - Used by `BOMOrchestrator` for accurate canonical quantities

### Changed
- **Mapping Service Idempotency** - Now checks `Mapping History` before processing:
  - Prevents duplicate history rows for re-uploaded files
  - Return existing decision if `ingest_line_id` already exists
- **BOM Orchestrator** - Now integrates `UnitService` for quantity calculation
- **History Logging** - Added persistence of `UOM` and `Conversion Factor` to `Mapping History` sheet

### Fixed
- **Idempotency Data Loop** - `MappingResult` now reconstructs UOM/Factor from history to ensure consistent BOM generation on replay

---

## [1.6.0] - 2026-01-27

### Added

#### Canonical Material Mapping System
- **`fn_map_lookup`** - New function for material mapping:
  - Endpoint: `POST /api/map/lookup`
  - Deterministic lookup: nesting description → canonical code → SAP code
  - Override support: LPO > PROJECT > CUSTOMER scope precedence
  - Thread-safe in-memory caching with TTL (5 min)
  - All lookups logged to Mapping History for audit trail
  - Unknown materials queued to Mapping Exception sheet

- **BOM Generator (`fn_parse_nesting/bom_generator.py`)** - Flattens nesting data:
  - Extracts panel materials, profiles, accessories, consumables
  - Normalizes descriptions (lowercase, trim, collapse spaces)
  - Returns typed `BOMLine` objects ready for mapping

- **BOM Orchestrator (`fn_parse_nesting/bom_orchestrator.py`)** - Full workflow:
  - Generates BOM lines from parsed record
  - Maps each line via mapping service
  - Writes results to `06a Parsed BOM` sheet
  - Returns processing stats (mapped/exception counts)

- **Parser Integration** - BOM processing now runs after parsing:
  - New step 7e in `fn_parse_nesting` orchestration
  - Non-blocking: BOM failures don't fail the parse
  - Response includes `bom_processing` stats object

#### Material Master Seeded (16 entries)
| Category | Materials |
|----------|-----------|
| Profiles (7) | Joint, Joint-PVC, Bayonet, U, F, H, Other |
| Consumables (4) | Silicone, Aluminum Tape, Glue Junction, Glue Flange |
| Accessories (2) | GI Corners, PVC Corners |
| Machine (3) | Blade 45, Blade 90, Blade 2x45 (not tracked) |

#### New Smartsheet Columns
- `05a Material Master`: `SAP UOM`, `Conversion Factor`

#### New SmartsheetClient Methods
- `get_all_rows()` - Fetch all rows from a sheet
- `add_rows_bulk()` - Add multiple rows in batches (with retry)

### Changed
- **Parser response** now includes `bom_processing` object with mapping stats
- **Material Master** extended with unit conversion columns

### Scripts
- `scripts/add_material_master_columns.py` - Adds SAP UOM + Conversion Factor
- `scripts/seed_material_master.py` - Populates Material Master with known materials

---

## [1.5.0] - 2026-01-22

### Added
- **`fn_parse_nesting` Orchestration** (v2.0.0) - Full SOTA implementation:
  - **Orchestration Layer**: Handles validation, parsing, logging, and exception creation.
  - **Idempotency**: Prevents duplicate processing via `client_request_id` and `file_hash` checks.
  - **Fail-Fast Validation**: Verifies Tag ID existence and LPO ownership before parsing.
  - **Smartsheet Integration**: Logs execution to `NESTING_LOG`, attaches files, and updates `TAG_REGISTRY`.
  - **Authoritative Exception Handling**: Creates exceptions in `99 Exception Log` using shared module.
- **`validation.py`** - New module for robust nesting validation logic:
  - `validate_tag_exists`, `validate_tag_lpo_ownership`, `check_duplicate_file`.
  - Safe column value lookup using logical-to-physical mapping.
- **`nesting_logger.py`** - New module for specialized Smartsheet logging operations.
- **`config.py`** - Configuration loader for nesting-specific settings (`nesting_config.json`).
- **`test_nesting_validation.py`** - Comprehensive unit and integration tests.

### Changed
- **`fn_parse_nesting/__init__.py`** - Rewritten to serve as the main orchestrator (previously just a parser wrapper).
- **`functions/tests/conftest.py`** - Added `NESTING_LOG` to mock manifest for testing.

### Fixed (Robustness Hardening)
- **Duplicate Tag Safety**: `validate_tag_exists` now detects duplicate Tag IDs and fails safely (prevents ambiguous updates).
- **User Validation**: `uploaded_by` falls back to default admin email if input is invalid/system (ensures API calls succeed).
- **Unit Tests**: Added test case for duplicate tag detection logic.

### Tests
- **Nesting Validation**: 21 new tests covering duplicate detection and orchestration failures.
- **Acceptance Criteria**: All 7 scenarios from specification Section 10 now covered.

### Fixed (Cross-Platform Compatibility)
- **pandas 2.x**: Fixed `df.apply(pd.to_numeric)` syntax for pandas 2.x compatibility.
- **Test Assertions**: Corrected `generate_lpo_folder_path` tests (returns relative path, not URL).
- **Pydantic 2.x**: Updated HTTP 400→422 expectation for validation errors.

### Infrastructure
- **Cross-Platform Test Runner**: Added `scripts/test.sh` with OS auto-detection.
- **.gitignore**: Added `venv_linux/`, `venv_macos/` for OS-specific environments.
- **Total Tests**: 387 passing.



## [1.4.2] - 2026-01-22

### Fixed
- **Service Bus queue name** - Aligned `events-main` across all configs
- **Routing table reference** - Fixed dict reference issue with `.clear()`
- **LPO Handler** - Extracts ALL fields (wastage_pct, terms, remarks) and row attachments

### Added
- **`get_row_attachments()`** - SmartsheetClient fetches row attachments
- **`get_user_email()`** - Resolves Smartsheet user ID → email (cached)
- **SOTA Exception Handling** - All handlers/functions log exceptions with `exception_id`
  - ValidationError catch in handlers, returns `EXCEPTION_LOGGED` → HTTP 200
- **Resilient RowEvent model** - Type coercion validators for all fields
- **3 new antigravity.md rules** - Exception handling best practices

### Tests
- **Smartsheet Client**: Added unit tests for `get_row_attachments` and `get_user_email` (with mock HTTP buffering).
- **LPO Handler**: Added validation tests for extraction of optional fields (wastage, terms) and attachment processing.
- **Resiliency**: Added unit tests for `RowEvent` type coercion (string/float/int) and Enum robustness.


---

## [1.4.0] - 2026-01-20

### Added
- **`fn_event_dispatcher`** - Central event router (`POST /api/events/process-row`)
  - Routes Smartsheet events to `fn_lpo_ingest`, `fn_ingest_tag`, etc.
  - ID-based routing (immune to sheet/column renames)
- **`event_routing.json`** - Externalized routing config (edit routes without code changes)
- **`LPO_SUBFOLDERS`** env var - Configurable folder structure
- **`SmartsheetClient.get_row()`** - New method for ID-based single row fetch
- **`shared/event_utils.py`** - Shared utilities for event processing:
  - `get_cell_value_by_logical_name()` - ID-based cell value extraction
  - `get_cell_value_by_column_id()` - Direct column ID access

### Changed
- Refactored `lpo_handler.py` and `tag_handler.py` to use shared `event_utils` (DRY)

### Tests Added
- **`test_event_dispatcher_models.py`** - 18 unit tests for Pydantic models:
  - `EventAction`, `ObjectType` enums
  - `RowEvent` model validation (required fields, defaults)
  - `RouteConfig`, `SheetRoute`, `HandlerConfig` models
  - `RoutingConfig` JSON parsing
- **`test_event_router.py`** - 11 unit tests for ID-based routing:
  - Config loading from `event_routing.json`
  - Routing table construction with mock manifest
  - Handler lookup by sheet_id + action
  - Disabled action handling
- **`test_event_utils.py`** - 9 unit tests for cell extraction:
  - `get_cell_value_by_column_id()` - direct ID lookup
  - `get_cell_value_by_logical_name()` - manifest-based lookup
- **`test_power_automate.py`** - 17 unit tests for FlowClient:
  - Environment variable configuration
  - Fire-and-forget timeout handling (success on timeout)
  - Connection error, HTTP error handling
  - Singleton pattern
- **Total: 350 tests (all passing)**

---

## [1.3.1] - 2026-01-19

### Added

#### Power Automate Integration (`shared/power_automate.py`)
- **FlowClient** - Shared client for calling Power Automate HTTP trigger flows:
  - Connection pooling via `requests.Session`
  - Automatic retry with exponential backoff (configurable)
  - Fire-and-forget pattern for async operations
  - Comprehensive error handling and structured logging
  - Timeout configuration (connect/read separately)
- **`trigger_create_lpo_folders()`** - Convenience function for LPO folder creation
- **Environment Variables** - New settings for flow configuration:
  - `POWER_AUTOMATE_CREATE_FOLDERS_URL` - Flow HTTP trigger URL
  - `FLOW_FIRE_AND_FORGET` - Enable async mode (default: true)
  - `FLOW_CONNECT_TIMEOUT`, `FLOW_READ_TIMEOUT`, `FLOW_MAX_RETRIES`

#### LPO Ingestion Enhancement (`fn_lpo_ingest`)
- **Automatic Folder Creation** - Triggers Power Automate flow after LPO creation:
  - Fire-and-forget pattern - LPO creation succeeds even if flow fails
  - Response includes `folder_creation_triggered` status
  - Subfolders: `01_LPO_Documents`, `02_Costing`, `03_Amendments`, `99_Other`

#### Nesting Parser Improvements (`fn_parse_nesting`)
- **Flange Accessories Extraction** - Added support for extracting accessories from "Flanges" sheet:
  - GI Corners (Quantity & Cost)
  - PVC Corners (Quantity & Cost)
- **Complex Consumables Layout** - Enhanced `OtherComponentsExtractor` to handle side-by-side layout:
  - Left column area: Silicone, Aluminum Tape
  - Right column area: Junction Glue, Flange Glue
  - Robust anchor-based extraction for all consumable types
- **Machine Telemetry** - Added extraction of `Time for 2x45° cuts` from Project Parameters
- **Extra Allowance Extraction** - Now captures "Extra" allowance percentages for all consumables
- **Finished Goods Geometry** - Fixed `length_m` extraction (converts mm to meters)

#### New Models
- `FlowClient`, `FlowClientConfig`, `FlowTriggerResult`, `FlowType` - Power Automate client types
- `FlangeAccessories` - Data model for corners and other flange accessories
- Updated `Consumables` - Added `extra_pct` fields for silicone, tape, and glues
- Updated `MachineTelemetry` - Added `time_2x45_cuts_sec` field

#### Validation & Robustness
- **Anchor-Based Extraction** - All new fields use robust anchor text search instead of hardcoded cell references
- **Unit Awareness** - Extractor now correctly identifies units (Kg, mt., AED) to find associated values

### Fixed
- Fixed `profiles_and_flanges` extraction to correctly identify all profile blocks in "Flanges" sheet
- Fixed "Total length" extraction for profiles using anchor-based search (no more hardcoded offsets)
- Fixed `DeliveryOrderExtractor` to handle multi-row headers for MOUTH A/B geometry

### Tests Added
- **`test_anchor_finder.py`** - 19 unit tests for Anchor & Offset strategy:
  - Basic anchor finding (exact, partial, case-insensitive)
  - Value extraction with type casting (string, float, int)
  - **Shifted file handling** (3+ rows inserted at top - critical spec requirement)
  - Multiple anchor detection for block iteration
  - Table extraction and header row detection
- **`test_extractors.py`** - 15 unit tests for sheet extractors:
  - ProjectParametersExtractor (identity, material, inventory, waste, telemetry)
  - Tag ID fallback logic (REFERENCE → NAME → UNKNOWN)
  - MachineInfoExtractor (cut lengths, times)
  - Missing/partial data handling
- **`test_nesting_parser.py`** - 16 integration tests for full parser:
  - Happy path (complete workbook parsing)
  - SUCCESS vs PARTIAL vs ERROR status determination
  - Missing sheets → PARTIAL with warnings
  - Missing Tag ID → ERROR (strict validation)
  - Shifted file → still extracts correctly
  - JSON serialization and numeric precision
- **Total test count: 295 (all passing)**

---

## [1.3.0] - 2026-01-13

### Added

#### Production Scheduling Function
- **`fn_schedule_tag`** - New function for production scheduling
  - Endpoint: `POST /api/production/schedule`
  - Tag validation (exists, not CANCELLED/CLOSED)
  - LPO validation (exists, not ON_HOLD)
  - **PO balance check** - Validates committed + planned ≤ PO quantity (5% tolerance)
  - **Machine validation** - Checks machine exists and is OPERATIONAL
  - Multiple tags allowed per machine/shift (no conflict blocking)
  - **T-1 deadline calculation** - Returns nesting cutoff (18:00 previous day)
  - Idempotency via `client_request_id`
  - Full audit trail (Exception Log + User Action Log with JSON new_value)

#### New Models
- `Shift` enum - Morning, Evening
- `ScheduleStatus` enum - Planned, Released for Nesting, Nesting Uploaded, Allocated, Cancelled, Delayed
- `MachineStatus` enum - Operational, Maintenance
- `ScheduleTagRequest` - Request model for scheduling
- `ScheduleTagResponse` - Response model with schedule_id and next_action_deadline

#### New Reason Codes
- `MACHINE_NOT_FOUND` - Machine ID not found in Machine Master
- `MACHINE_MAINTENANCE` - Machine is under maintenance
- `CAPACITY_WARNING` - Planned quantity exceeds machine capacity (soft warning)
- `DUPLICATE_SCHEDULE` - Tag already scheduled
- `T1_NESTING_DELAY` - Nesting not uploaded by T-1 cutoff
- `PLANNED_MISMATCH` - Planned qty differs from expected consumption
- `TAG_NOT_FOUND` - Tag ID not found
- `TAG_INVALID_STATUS` - Tag status is CANCELLED or CLOSED

#### New Action Types
- `SCHEDULE_CREATED` - Production schedule created
- `SCHEDULE_UPDATED` - Production schedule updated
- `SCHEDULE_CANCELLED` - Production schedule cancelled

#### New Logical Names
- `Sheet.MACHINE_MASTER` - Machine Master sheet reference
- `Column.MACHINE_MASTER` - All Machine Master columns
- `Column.PRODUCTION_PLANNING` - All Production Planning columns

#### ID Generation
- `generate_next_schedule_id()` - Generates SCHED-0001 format IDs
- Added `SEQ_SCHEDULE` to ConfigKey enum

### Changed
- **`fetch_manifest.py`** - Now includes PICKLIST options in manifest
  - Picklist/dropdown values available for validation
- **`log_user_action()`** - Now returns action_id (ACT-0001 format)
  - Added `ACTION_ID` column to User Action Log
- **Attachment methods** - Fixed `attach_file_to_row()` rate limiter attribute

### Fixed
- Fixed `_rate_limiter` typo in `SmartsheetClient.attach_file_to_row()`
- Fixed action ID generation (ACT-0001 format)
- Fixed row_id retrieval from `add_row()` result (uses `id` not `row_id`)

### Technical Debt Remediation
- **Refactored `fn_ingest_tag`** - Now uses shared `create_exception()` and `log_user_action()` from `shared/audit.py`
  - Removed 80+ lines of duplicate code
  - Now generates sequence-based action IDs (ACT-0001)
- **Refactored `fn_lpo_update`** - Now uses shared audit module
  - Removed 60+ lines of duplicate code
  - Consistent audit logging with all other functions
- **All 4 functions now use shared audit module**:
  - `fn_ingest_tag`, `fn_lpo_ingest`, `fn_lpo_update`, `fn_schedule_tag`

### Tests Added
- `test_schedule_models.py` - 14 unit tests for Shift, ScheduleStatus, MachineStatus enums and request/response models
- `test_schedule_tag.py` - 14 integration tests for fn_schedule_tag (happy path, validation, machine checks, PO balance)
- Updated `conftest.py` with MACHINE_MASTER and PRODUCTION_PLANNING mock sheets/columns
- **Total test count: 221 (all passing)**


---

## [1.2.0] - 2026-01-10

### Added

#### LPO Ingestion Functions
- **`fn_lpo_ingest`** - New function to create LPO records
  - SAP Reference as required external-facing ID
  - Idempotency via `client_request_id`
  - Duplicate SAP Reference detection → 409 DUPLICATE
  - **Multi-file attachment support** (LPO, Costing, Amendment, Other)
  - Combined file hash for duplicate detection
  - Files attached to Smartsheet row via API
  - SharePoint folder path generation
  - Initial status set to "Draft"
  - Full audit trail (Exception Log + User Action Log)

- **`fn_lpo_update`** - New function to update existing LPOs
  - Lookup by SAP Reference
  - Partial update support (only update provided fields)
  - Quantity conflict validation (can't reduce below delivered)
  - Old/new value tracking in audit log

#### New API Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/lpos/ingest` | POST | Create new LPO |
| `/api/lpos/update` | PUT | Update existing LPO |

#### New Models
- `FileType` enum - lpo, costing, amendment, other
- `FileAttachment` - Single file with type, url/content, name
- `LPOIngestRequest` - Request model with multi-file support
- `LPOUpdateRequest` - Request model for LPO update
- `LPOIngestResponse` / `LPOUpdateResponse` - Response models
- `Brand` enum - KIMMCO, WTI
- `TermsOfPayment` enum - Payment terms options

#### New Helpers
- `compute_combined_file_hash()` - Combined hash from multiple files
- `generate_lpo_folder_path()` - Generate SharePoint folder path
- `sanitize_folder_name()` - Clean strings for folder names
- `generate_lpo_subfolder_paths()` - Generate all LPO subfolders

#### New Reason Codes
- `DUPLICATE_SAP_REF` - SAP Reference already exists
- `SAP_REF_NOT_FOUND` - SAP Reference not found (for update)
- `LPO_INVALID_DATA` - Invalid LPO data
- `PO_QUANTITY_CONFLICT` - Quantity reduced below committed
- `DUPLICATE_LPO_FILE` - Same PO file(s) already uploaded

#### New Action Types
- `LPO_CREATED` - LPO creation logged
- `LPO_UPDATED` - LPO update logged

### Changed
- Extended `Column.LPO_MASTER` with additional column mappings
- Updated `create_workspace.py` with new LPO columns:
  - Source File Hash, Folder URL, Client Request ID, Created At, Updated At

### Refactored (SOTA Compliance - 2026-01-12)
- **Created `shared/audit.py`** - Centralized audit utilities (DRY principle)
  - `create_exception()` - Reusable exception creation function
  - `log_user_action()` - Reusable user action logging function
  - Previously duplicated across `fn_ingest_tag`, `fn_lpo_ingest`, `fn_lpo_update`
- **Aligned `logical_names.py` with manifest** - Added 5 missing LPO_MASTER columns:
  - `ESTIMATED_IN_PRODUCTION_3_DAYS`, `BALANCE_VALUE_AED`, `CURRENT_STATUS`
  - `NUMBER_OF_DELIVERIES`, `DELIVERED_DATE`
- **Fixed E2E test assertion** - Tag status now correctly expects "Validate" (v1.1.0 change)

### Tests Added
- `test_lpo_models.py` - 25 unit tests for LPO models, FileAttachment, helpers
- `test_lpo_ingest.py` - 13 integration tests for fn_lpo_ingest/fn_lpo_update
- Updated `conftest.py` with 11 new LPO_MASTER mock columns
- **Total test count: 193 (all passing)**

---

## [1.1.0] - 2026-01-09

### Added

#### Tag Ingestion Enhancements (`fn_ingest_tag`)
- **Base64 file content support** - Can now pass file content directly as base64 instead of URL
  - New `file_content` field in request
  - New `compute_file_hash_from_base64()` helper
  - New `attach_file_to_row()` method in SmartsheetClient
- **Complete Tag Registry field population**:
  - `TAG_ID` - Now properly saves in the Tag ID column
  - `DATE_TAG_SHEET_RECEIVED` - Automatically set to current timestamp
  - `RECEIVED_THROUGH` - Email, Whatsapp, or API (from request)
  - `PROJECT` - Copied from LPO's Project Name
  - `LPO_ALLOWABLE_WASTAGE` - Copied from LPO's Wastage setting
  - `PRODUCTION_GATE` - Defaults to "Green"
  - `STATUS` - Now starts at "Validate" instead of "Draft"
- **User remarks field** - Separate `user_remarks` field for user input (not mixed with system traces)

#### New Request Fields
```json
{
  "file_content": "base64...",     // Alternative to file_url
  "received_through": "Email",     // Email, Whatsapp, API
  "user_remarks": "User notes"     // Separate from system remarks
}
```

### Fixed
- **PO Quantity calculation** - Now correctly reads physical column names from manifest
  - Was returning 0 because it used logical names to read row data
  - Added `_get_physical_column_name()` helper function
- **Column name resolution** - All LPO data access now uses physical names via manifest lookup
- **Indentation issues** in file hash duplicate check logic
- **Removed duplicate JSON key** in ALREADY_PROCESSED response
- **Missing user action logs** - All BLOCKED scenarios now log user actions (LPO_NOT_FOUND, LPO_ON_HOLD, INSUFFICIENT_PO_BALANCE)
- **Code cleanup** - Removed duplicate comments and extra blank lines

### Changed
- **Tag status** now starts at "Validate" instead of "Draft"
- **Remarks column** now contains user input only, system trace moved internally
- **SmartsheetClient** - Enhanced error logging to show full API error messages

### Technical
- Added `WASTAGE_CONSIDERED_IN_COSTING` to `Column.LPO_MASTER`
- Extended `Column.TAG_REGISTRY` with all columns:
  - `DATE_TAG_SHEET_RECEIVED`, `RECEIVED_THROUGH`, `PROJECT`, `LOCATION`
  - `LPO_ALLOWABLE_WASTAGE`, `PRODUCTION_GATE`, `SHEETS_USED`, `WASTAGE_NESTED`
  - `PLANNED_CUT_DATE`, `ALLOCATION_BATCH_ID`
- Added `compute_file_hash_from_base64` export to shared module

---

## [1.0.0] - 2026-01-08

### Added

#### Core Infrastructure
- **Azure Functions project structure** with Python 3.9+ support
- **Shared library** (`functions/shared/`) with reusable components
- **Smartsheet client** with retry, rate limiting, and error handling
- **Sequence-based ID generator** for human-readable IDs (TAG-0001, EX-0001, etc.)
- **Pydantic models** for request/response validation
- **Configuration management** via Config sheet

#### Functions
- **`fn_ingest_tag`** - Tag sheet ingestion with:
  - Request parsing and validation
  - Idempotency via `client_request_id`
  - Duplicate file detection via SHA256 hash
  - LPO validation (exists, not on hold, sufficient balance)
  - Sequential Tag ID generation
  - Exception creation for validation failures
  - User action audit logging
  - Comprehensive error handling

#### Test Suite
- **Unit tests** for models, helpers, sheet config, ID generator
- **Integration tests** for tag ingestion flows
- **E2E acceptance tests** per specification
- **Mock Smartsheet client** for isolated testing
- **Test data factories** for consistent test data
- **pytest configuration** with markers and coverage

#### Documentation
- **Documentation Hub** (`docs/index.md`)
- **Quick Start Guide** for rapid onboarding
- **Architecture Overview** with diagrams
- **API Reference** with examples
- **Data Dictionary** with all models and schemas
- **Configuration Reference** for all settings
- **Error Code Reference** with troubleshooting
- **How-To Guides**:
  - Testing Guide
  - Adding New Functions
  - Deployment Guide
  - Troubleshooting Guide
- **Contributing Guide** with standards
- **Changelog** (this file)

#### Specifications
- **Architecture Specification** - Full system design
- **Data Structure Specification** - Data governance
- **Tag Ingestion Architecture** - Detailed flow spec
- **Flow Architecture** - Power Automate design

### Technical Details

#### Dependencies
- `azure-functions>=1.11.0`
- `pydantic>=2.0`
- `requests>=2.28`
- `pytest>=7.0` (dev)
- `pytest-cov>=4.0` (dev)

#### API Endpoints
| Endpoint | Method | Status |
|----------|--------|--------|
| `/api/tags/ingest` | POST | ✅ Implemented |

#### Sheet Support
| Sheet | Read | Write |
|-------|------|-------|
| Tag Sheet Registry | ✅ | ✅ |
| 01 LPO Master LOG | ✅ | - |
| 00a Config | ✅ | ✅ |
| 99 Exception Log | - | ✅ |
| 98 User Action Log | - | ✅ |

---

## [0.1.0] - 2026-01-05

### Added
- Initial project structure
- Basic Smartsheet workspace setup scripts
- Initial specifications documents

---

## Version History Summary

| Version | Date | Highlights |
|---------|------|------------|
| 1.6.0 | 2026-01-27 | Canonical Material Mapping, BOM Generator, fn_map_lookup |
| 1.5.0 | 2026-01-22 | Nesting Parser Orchestration (SOTA Integration) |
| 1.4.2 | 2026-01-22 | Service Bus queue fix, LPO Handler improvements |
| 1.4.0 | 2026-01-20 | Event Dispatcher, ID-based routing, event_routing.json config |
| 1.3.1 | 2026-01-19 | Power Automate FlowClient, LPO folder creation, Nesting Parser improvements |
| 1.3.0 | 2026-01-13 | Production scheduling function, machine validation, T-1 deadline calculation |
| 1.2.0 | 2026-01-10 | LPO ingestion/update functions, multi-file support, SharePoint folder generation |
| 1.1.0 | 2026-01-09 | Base64 file support, complete Tag Registry fields, PO balance fix |
| 1.0.0 | 2026-01-08 | Full tag ingestion, test suite, documentation |
| 0.1.0 | 2026-01-05 | Initial setup |

---

## Upgrade Notes

### Upgrading to 1.4.0

If upgrading from 1.3.x:

1. **New endpoint available**:
   - `POST /api/events/process-row` - Central event dispatcher

2. **New configuration file**:
   - `event_routing.json` - Externalized routing config
   - Edit routes without code changes

3. **New helper module**:
   - `shared/event_utils.py` - ID-based cell value extraction

4. **No breaking changes** - All existing integrations continue to work.

### Upgrading to 1.3.1

If upgrading from 1.3.0:

1. **New Power Automate integration**:
   - `shared/power_automate.py` - FlowClient for HTTP trigger flows
   - Automatic LPO folder creation after ingestion

2. **New environment variables** (optional):
   - `POWER_AUTOMATE_CREATE_FOLDERS_URL`
   - `FLOW_FIRE_AND_FORGET`, `FLOW_CONNECT_TIMEOUT`, etc.

3. **No breaking changes** - All existing integrations continue to work.

### Upgrading to 1.3.0

If upgrading from 1.2.0:

1. **New endpoint available**:
   - `POST /api/production/schedule` - Schedule tag for production

2. **New Config sheet entries required**:
   - `seq_action` - Sequence counter for User Action IDs (ACT-0001)
   - `seq_schedule` - Sequence counter for Schedule IDs (SCHED-0001)

3. **New sheets required**:
   - `00b Machine Master` - Machine definitions with status
   - `03 Production Planning` - Production schedules

4. **Updated audit logging**:
   - `log_user_action()` now returns `action_id` (ACT-0001 format)
   - Existing integrations are compatible (return value can be ignored)

5. **No breaking changes** - All existing integrations continue to work.

### Upgrading to 1.2.0

If upgrading from 1.1.0:

1. **New endpoints available**:
   - `POST /api/lpos/ingest` - Create LPO
   - `PUT /api/lpos/update` - Update LPO

2. **New shared audit module** (`shared/audit.py`):
   - `create_exception()` and `log_user_action()` now available as reusable utilities
   - If you have custom functions, consider using these for consistency

3. **Updated conftest.py**:
   - Mock manifest now includes 11 new LPO_MASTER columns
   - Update your test fixtures if testing LPO-related functionality

4. **No breaking changes** - All existing integrations continue to work.

### Upgrading to 1.1.0

If upgrading from 1.0.0:

1. **New request fields available** (optional):
   - `file_content` - Base64 file content (alternative to `file_url`)
   - `received_through` - Reception channel (Email/Whatsapp/API)
   - `user_remarks` - User-entered remarks

2. **Status behavior change**:
   - Tags processed by `fn_ingest_tag` now receive `Validate` status
   - This indicates the tag passed validation and is ready for nesting

3. **No breaking changes** - All existing integrations continue to work.

### Upgrading to 1.0.0

If upgrading from 0.1.0:

1. **Update dependencies:**
   ```bash
   pip install -r functions/requirements.txt
   ```

2. **Initialize Config sheet** with sequence counters:
   - Add rows for `seq_tag`, `seq_exception`, etc.
   - See `config_values.md` for full list

3. **Set environment variables:**
   - `SMARTSHEET_API_KEY`
   - `SMARTSHEET_WORKSPACE_ID`
   - `SMARTSHEET_BASE_URL`

---

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for how to contribute to this project.

---

<p align="center">
  <a href="./index.md">📚 Documentation Hub →</a>
</p>
