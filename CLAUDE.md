# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Ducts Manufacturing Inventory Management System — a Smartsheet-first manufacturing inventory platform built on Python Azure Functions. The system manages tag-based production planning, ledger-first inventory control, and exception-driven operations with full audit trails.

**Stack:** Python 3.x, Azure Functions v4, Smartsheet API (EU region), Power Automate, SharePoint, Pydantic v2.

## Development Commands

All commands run from the `functions/` directory:

```bash
# Install dependencies
pip install -r requirements.txt
pip install -r requirements-test.txt

# Run all tests
cd functions && pytest

# Run unit tests only
cd functions && pytest -m unit

# Run a single test file
cd functions && pytest tests/unit/test_helpers.py

# Run a specific test
cd functions && pytest tests/unit/test_helpers.py::test_function_name

# Run with coverage
cd functions && pytest --cov=shared --cov-report=html

# Run tests in parallel
cd functions && pytest -n auto

# Start Azure Functions locally
cd functions && func start
```

**Manifest refresh** (when sheets/columns change in Smartsheet):
```bash
python fetch_manifest.py
```

## Architecture

```
Smartsheet (UI + Data) → Power Automate (Orchestration) → Azure Functions (Business Logic)
                                                         → SharePoint (File Storage)
                                                         → SAP Connector (Integration)
```

- **Smartsheet** — UI only. No business logic in formulas.
- **Power Automate** — Triggers, routing, notifications. No computation.
- **Azure Functions** (`functions/`) — All business logic and validation. 17 HTTP-triggered functions.
- **Function Adapter** (`function_adapter/`) — Webhook ingestion and Service Bus routing layer.
- **Shared modules** (`functions/shared/`) — Common code used across functions.

### Key Shared Modules

| Module | Purpose |
|--------|---------|
| `smartsheet_client.py` | Thread-safe Smartsheet API wrapper with retry logic |
| `models.py` | Pydantic v2 data models (TagIngestRequest, etc.) |
| `logical_names.py` | `Sheet` and `Column` enums for ID-first architecture |
| `manifest.py` | Loads `workspace_manifest.json`, resolves logical names → physical IDs |
| `helpers.py` | Utility functions (hashing, SLA calc, formatting) |
| `allocation_engine.py` | Material allocation algorithm |
| `consumption_service.py` | Consumption event tracking and ledger updates |
| `inventory_service.py` | Inventory transaction logging |
| `power_automate.py` | Adaptive card builders, flow helpers |
| `flow_models.py` | Flow request/response models |

## Mandatory Rules

These are **non-negotiable** patterns enforced across the codebase. See `rules.md` for full details.

### Never Do

1. **Never hardcode sheet/column names** — Use `Sheet.TAG_REGISTRY`, `Column.TAG_REGISTRY.STATUS` from `shared.logical_names`, resolved via manifest.
2. **Never bypass the manifest** — All sheet/column IDs come from `workspace_manifest.json`. If adding a new logical name, the manifest must be refreshed.
3. **Never use non-deterministic IDs for idempotency** — `client_request_id` must be deterministic (e.g., `f"staging-lpo-{row_id}"`), never timestamps or random values.
4. **Never read-then-write without locking** — Smartsheet is last-write-wins. Flag with `REQUIRES_LOCKING` comment if lock not yet implemented.
5. **Never put business logic in Smartsheet** — All logic in Azure Functions.

### Always Do

1. **ID-First Architecture** — Import `Sheet`/`Column` from `shared.logical_names`, use `get_manifest()` to resolve IDs.
2. **Idempotency** — Every POST/PUT accepts `client_request_id`. Duplicate requests return 200 with existing resource.
3. **Audit Logging** — Every state change logs to User Action Log via `log_user_action()`.
4. **Exception Records** — Business rule violations create Exception Log records via `create_exception()`. Never fail silently.
5. **DRY** — Check `functions/shared/` before writing new helpers. Shared utilities go in `shared/` if used by more than one function.

## Testing Patterns

- **Mock external services** — Use `MockSmartsheetClient` from `conftest.py` (never hit real Smartsheet API in unit tests).
- **Test idempotency** — Every write function needs a "duplicate request returns success" test.
- **Markers:** `unit`, `integration`, `e2e`, `acceptance`, `slow`.
- **Fixtures** in `functions/tests/conftest.py` — MockSmartsheetClient, test data factories, HTTP request mocking.

## Configuration

- `functions/workspace_manifest.json` — 84KB manifest mapping logical names to physical Smartsheet IDs (19 sheets). Treat as read-only; updated by `fetch_manifest.py`.
- `functions/local.settings.json` — Local dev settings (SMARTSHEET_API_KEY, WORKSPACE_ID, BASE_URL).
- `functions/host.json` — Azure Functions config. Queue batch size is 1 (critical for idempotency).
- Smartsheet region: EU (`api.smartsheet.eu`).

## Function Naming Convention

All Azure Functions are prefixed `fn_` (e.g., `fn_ingest_tag`, `fn_lpo_ingest`, `fn_allocate`). Each function has its own directory under `functions/` containing `__init__.py` and `function.json`.

---

## Agent Workflow: Adding or Editing Code

Follow this workflow for every code change. The steps are ordered — do not skip ahead.

### Phase 1: Understand Before Touching

1. **Read `rules.md`** — mandatory rules that override everything else.
2. **Read the relevant specification** in `Specifications/` — find the spec that covers the feature you're building or modifying. Specs define the expected behavior, not the code.
3. **Read `functions/shared/` modules** before writing any new helper. Key modules to check:
   - `helpers.py` — utility functions (hashing, date formatting, `parse_float_safe`, `get_physical_column_name`)
   - `audit.py` — `create_exception()`, `log_user_action()` — use these, never duplicate
   - `models.py` — Pydantic models, enums (`ExceptionSeverity`, `ExceptionSource`, `ReasonCode`, `ActionType`)
   - `flow_models.py` — request/response models for flow endpoints
   - `logical_names.py` — `Sheet` and `Column` enums — check if yours already exists
   - `allocation_service.py` — `_parse_rows()`, `aggregate_materials()`, `get_allocation_details_by_tag()`
   - `lpo_service.py` — LPO lookup/validation helpers
   - `id_generator.py` — sequential ID generation (`generate_next_*_id()`)
4. **Read the function you're modifying** — understand the full flow before making changes.
5. **Read `functions/tests/conftest.py`** — understand available fixtures and mock patterns.

### Phase 2: Design the Change

Before writing code, verify:

- [ ] Every sheet/column reference uses `Sheet.*` / `Column.*` from `logical_names.py`, resolved via manifest
- [ ] If you need a new sheet or column enum, add it to `logical_names.py` AND `SHEET_COLUMNS` mapping
- [ ] If adding a write endpoint: plan the idempotency key (must be deterministic)
- [ ] If adding a read-then-write: plan the locking strategy (use `AllocationLock` from `queue_lock.py`)
- [ ] If adding a state change: plan the `log_user_action()` call and which `ActionType` to use
- [ ] If adding validation that can fail: plan the `create_exception()` call with appropriate `ReasonCode` and `ExceptionSeverity`
- [ ] Check if any existing shared module already does what you need — **do not duplicate**

### Phase 3: Implement

#### Adding a New Azure Function

Follow the canonical structure (use `fn_submit_consumption` as the reference pattern):

```python
"""
fn_your_function: Short Description
====================================
Endpoint: METHOD /api/your/route
"""
import logging, json, azure.functions as func
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import (
    Sheet, Column, get_smartsheet_client, generate_trace_id,
    # Import Pydantic models you need
)

logger = logging.getLogger(__name__)

def main(req: func.HttpRequest) -> func.HttpResponse:
    trace_id = generate_trace_id()
    try:
        # 1. Parse + validate (Pydantic)
        body = req.get_json()
        request = YourRequestModel(**body)
        if request.trace_id:
            trace_id = request.trace_id

        # 2. Get client
        client = get_smartsheet_client()

        # 3. Idempotency check (for writes)
        # 4. Acquire lock if read-then-write
        # 5. Business logic (delegate to shared/ service)
        # 6. Audit log: log_user_action(...)
        # 7. Return response

    except Exception as e:
        logger.exception(f"[{trace_id}] Error: {e}")
        try:
            from shared.audit import create_exception
            from shared.models import ReasonCode, ExceptionSeverity, ExceptionSource
            create_exception(
                client=client, trace_id=trace_id,
                reason_code=ReasonCode.SYSTEM_ERROR,
                severity=ExceptionSeverity.CRITICAL,
                source=ExceptionSource.INGEST,  # use appropriate source
                message=f"fn_your_function unhandled error: {str(e)}",
            )
        except Exception:
            logger.error(f"[{trace_id}] Failed to create exception record")
        return func.HttpResponse(
            json.dumps({"error": {"code": "SERVER_ERROR", "message": str(e)}, "trace_id": trace_id}),
            status_code=500, mimetype="application/json"
        )
```

Required files for each new function:
- `functions/fn_your_function/__init__.py` — function code
- `functions/fn_your_function/function.json` — Azure trigger config
- `functions/tests/unit/test_your_function.py` — unit tests

#### Editing Shared Modules

- **Never change function signatures** of existing shared functions without checking all callers (grep for the function name across `functions/`)
- **Add new parameters as optional with defaults** to avoid breaking existing callers
- **If adding a new shared module**, export it from `shared/__init__.py`

#### Writing to Smartsheet

Always follow this pattern:
```python
manifest = get_manifest()
# Resolve column names via manifest — NEVER hardcode
col_status = manifest.get_column_name(Sheet.YOUR_SHEET, Column.YOUR_SHEET.STATUS)
# Use parse_float_safe() for numeric values from Smartsheet (they can be strings, None, or "N/A")
qty = parse_float_safe(row.get(col_qty), default=0.0)
```

#### Distributed Locking (for writes that read-then-modify)

```python
from shared.queue_lock import AllocationLock

with AllocationLock(lock_keys, timeout_ms=60000, trace_id=trace_id) as lock:
    if not lock.success:
        return error_response("LOCK_TIMEOUT", 409)
    # All reads AND writes inside the lock
```

Default timeout is 60s. Never read outside the lock and write inside — both must be within the lock scope.

#### Variance and Quantity Calculations

- Always use **system allocation data** (from `aggregate_materials()`) for variance calculations, never user-submitted values
- Use `parse_float_safe()` for any numeric value from Smartsheet — never bare `float()`
- Use `datetime.utcnow()` consistently — never `datetime.now()` for timestamps or date comparisons

### Phase 4: Test

1. **Run existing tests first** — `cd functions && pytest -x -q` — ensure you haven't broken anything
2. **Write tests for your change:**
   - Idempotency: "duplicate request returns success without side effects"
   - Happy path: valid input → correct output + correct Smartsheet writes
   - Validation failure: invalid input → correct error code + exception record created
   - Lock failure: if using locking, test the "lock not acquired" path
3. **Use `MockSmartsheetClient` from `conftest.py`** — never hit real API in tests
4. **Mock manifest** is pre-configured in `conftest.py` with common sheets — add new mock columns if needed
5. **Run full suite** — `cd functions && pytest -q` — all tests must pass before committing

### Phase 5: Document

1. **Update `docs/CHANGELOG.md`** — add entry in `[Unreleased]` under appropriate category (Added/Changed/Fixed)
2. **Update specifications** if behavior changed — check `Specifications/` for the relevant spec
3. **Update `docs/reference/`** if you added new models, sheets, error codes, or config variables
4. **If you added a new sheet/column enum**, document it in `docs/reference/data/sheets-*.md`

### Self-Correction Checklist

Run through this before marking any task complete:

- [ ] No hardcoded sheet/column name strings anywhere in my changes
- [ ] Every POST/PUT endpoint has idempotency via `client_request_id` or `trace_id`
- [ ] Every state change calls `log_user_action()`
- [ ] Every validation failure calls `create_exception()`
- [ ] Every outermost `except Exception` block calls `create_exception()` (wrapped in its own try/except)
- [ ] No bare `except:` — always `except Exception as e:`
- [ ] No bare `float()` on Smartsheet values — use `parse_float_safe()`
- [ ] No `datetime.now()` for UTC comparisons — use `datetime.utcnow()`
- [ ] Read-then-write operations are inside a distributed lock
- [ ] New shared code is in `shared/` and exported from `__init__.py`
- [ ] All existing tests still pass (`pytest -x -q`)
- [ ] New tests cover idempotency, happy path, and failure cases
- [ ] CHANGELOG.md updated
