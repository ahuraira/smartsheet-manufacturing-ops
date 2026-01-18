# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Planned
- `fn_parse_nesting` - Nesting file parser function
- `fn_allocate` - Inventory allocation function
- `fn_pick_confirm` - Pick confirmation function
- `fn_submit_consumption` - Consumption submission function
- `fn_create_do` - Delivery order creation function

---

## [1.3.1] - 2026-01-18

### Added

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
- `FlangeAccessories` - Data model for corners and other flange accessories
- Updated `Consumables` - Added `extra_pct` fields for silicone, tape, and glues
- Updated `MachineTelemetry` - Added `time_2x45_cuts_sec` field

#### Validation & Robustness
- **Anchor-Based Extraction** - All new fields use robust anchor text search instead of hardcoded cell references
- **Unit Awareness** - Extractor now correctly identifies units (Kg, mt., AED) to find associated values

### Fixed
- Fixed `profiles_and_flanges` extraction to correctly identify all profile blocks in "Flanges" sheet
- Fixed "Total length" extraction for profiles using anchor-based search (no more hardcoded offsets)
- Fixed `DeliveryOrderExtractor` to handle multi-row headers for MOUTH A/B geometry and improved header detection using fuzzy matching
- Fixed critical bug in `SmartsheetClient` where duplicate method definitions caused parameter shadowing

### Changed
- **Strict Identity Validation** - Missing Tag ID/Reference now triggers a CRITICAL ERROR instead of a warning, preventing invalid data ingestion

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
| 1.3.1 | 2026-01-18 | Nesting parser: flange accessories, consumables extraction, anchor-based robustness |
| 1.3.0 | 2026-01-13 | Production scheduling function, machine validation, T-1 deadline calculation |
| 1.2.0 | 2026-01-10 | LPO ingestion/update functions, multi-file support, SharePoint folder generation |
| 1.1.0 | 2026-01-09 | Base64 file support, complete Tag Registry fields, PO balance fix |
| 1.0.0 | 2026-01-08 | Full tag ingestion, test suite, documentation |
| 0.1.0 | 2026-01-05 | Initial setup |

---

## Upgrade Notes

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
