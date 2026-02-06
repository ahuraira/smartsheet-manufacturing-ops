# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Planned
- `fn_allocate` - Inventory allocation function
- `fn_pick_confirm` - Pick confirmation function
- `fn_submit_consumption` - Consumption submission function
- `fn_create_do` - Delivery order creation function

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
