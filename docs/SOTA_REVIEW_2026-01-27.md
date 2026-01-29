# SOTA Review: Implementation vs Specifications

> **Review Date:** 2026-01-27 | **Reviewer:** Antigravity | **Version:** v1.6.0

---

## Executive Summary

This document captures the findings from a ruthless SOTA review comparing the current codebase against:
1. `Specifications/architecture_specification.md` - Overall system design
2. `Specifications/canonical_material_mapping_specification.md` - Material mapping system
3. `Specifications/nesting_parser_integration_specification.md` - Nesting parser orchestration

**Overall Compliance:** ~65% implemented, ~85% SOTA-compliant for what's built.

---

## 1. Architecture Specification Compliance

### ✅ Implemented Functions

| Function | Endpoint | Status | Notes |
|----------|----------|--------|-------|
| `fn_ingest_tag` | `POST /api/tags/ingest` | ✅ Complete | Full validation, idempotency, audit |
| `fn_parse_nesting` | `POST /api/nesting/parse` | ✅ Complete | Orchestration v2.0, fail-fast validation |
| `fn_lpo_ingest` | `POST /api/lpos/ingest` | ✅ Complete | Multi-file attachment, folder creation |
| `fn_lpo_update` | `PUT /api/lpos/update` | ✅ Complete | Partial update, conflict validation |
| `fn_schedule_tag` | `POST /api/production/schedule` | ✅ Complete | Machine validation, T-1 deadline |
| `fn_map_lookup` | `POST /api/map/lookup` | ✅ Complete | Deterministic mapping, caching |
| `fn_event_dispatcher` | `POST /api/events/process-row` | ✅ Complete | ID-based routing |

### ❌ Missing Functions (Spec Section 10 - Sprint 2)

| Function | Endpoint | Spec Section | Business Impact |
|----------|----------|--------------|-----------------|
| `fn_allocate` | `POST /api/allocations/create` | Sec 4 | **CRITICAL**: No inventory reservation |
| `fn_pick_confirm` | `POST /api/allocations/pick_confirm` | Sec 5 | Cannot confirm physical picks |
| `fn_submit_consumption` | `POST /api/consumption/submit` | Sec 6 | Cannot record actual usage |
| `fn_create_do` | `POST /api/do/create` | Sec 8 | Cannot generate delivery orders |
| `fn_pod_upload` | `POST /api/do/pod_upload` | Sec 9 | Cannot attach POD |
| `fn_reconcile` | Scheduled | Sec 11 | No daily variance reconciliation |

### ❌ Missing Data Tables

| Table | Purpose | Spec Section |
|-------|---------|--------------|
| `inventory_txn` | Append-only transaction ledger | Sec 3, 6 |
| `allocation` | Soft reservations per shift | Sec 4 |
| `sap_snapshot` | SAP inventory/PO/AR sync | Sec 11 |
| `physical_counts` | Cycle count data | Sec 11 |

---

## 2. Canonical Material Mapping Compliance

### ✅ Implemented Correctly

| Requirement | Location | Status |
|-------------|----------|--------|
| Deterministic exact-match lookup | `mapping_service.py` L197-219 | ✅ |
| Normalization (lowercase, trim, collapse spaces) | `_normalize_description()` | ✅ |
| Override precedence (LPO > PROJECT > CUSTOMER > PLANT) | `_check_overrides()` L321-383 | ✅ |
| Thread-safe singleton with cache | `MappingService.__new__()` | ✅ |
| Cache TTL (5 minutes) | `CACHE_TTL_SECONDS = 300` | ✅ |
| Mapping History logging | `_log_history()` | ✅ |
| Exception creation for unknown materials | `_create_exception()` | ✅ |
| BOM Generator (flattens nesting → BOM lines) | `bom_generator.py` | ✅ |
| BOM Orchestrator (maps + writes to sheet) | `bom_orchestrator.py` | ✅ |

### ⚠️ Partially Implemented

| Requirement | Issue | Impact | Fix |
|-------------|-------|--------|-----|
| **Idempotency per ingest_line_id** | No check for existing history before re-logging | Duplicate history rows on reprocess | Add `_check_existing_history()` before `_log_history()` |
| **Conversion factors in history** | `conversion_factor` captured but not written to history | Audit trail incomplete | Add field to `mapping_history` row_data |
| **Effective date filtering** | `TODO` comment at L367-368 | Overrides cannot be time-bounded | Implement date range check |

### ❌ Not Implemented

| Requirement | Spec Section | Priority |
|-------------|--------------|----------|
| **UnitService module** | "Unit conversion specification" | P1 |
| **`lpo_material_brand_map` usage for allocation** | "Allocation overview" | P1 |
| **`POST /api/map/manual`** admin endpoint | API summary | P2 |
| **`POST /api/allocation/run`** T-1 runner | API summary | P1 |
| **Power Automate exception notification** | Admin workflows | P2 |
| **`sap_material.conversion_to_canonical`** table | Unit conversion | P1 |

---

## 3. Nesting Parser Integration Compliance

### ✅ Fully Implemented

| Requirement | Status | Notes |
|-------------|--------|-------|
| 5-Phase orchestration (Idempotency → Parse → Validate → Log → Success) | ✅ | `fn_parse_nesting/__init__.py` |
| Fail-fast validation (Tag exists, LPO ownership) | ✅ | `validation.py` |
| Duplicate detection (file_hash, client_request_id) | ✅ | Idempotency checks |
| Exception creation at point of failure | ✅ | Uses shared audit module |
| Nesting Log write with column mapping | ✅ | `nesting_logger.py` |
| File attachment to Tag Registry | ✅ | URL attachment |
| Tag status update on success | ✅ | Updates to "Nesting Complete" |
| BOM generation integration (Step 7e) | ✅ | Non-blocking, returns stats |

### ⚠️ Robustness Fixes Applied (2026-01-22)

| Issue | Fix Applied |
|-------|-------------|
| Duplicate Tag IDs could cause wrong row update | `TAG_DUPLICATE` error on `len(rows) > 1` |
| Invalid `uploaded_by` could cause API failures | `get_safe_user_email()` fallback to admin |

### ❌ Architectural Gap

| Requirement | Spec Section | Status |
|-------------|--------------|--------|
| **Repository Pattern** | Sec 13 | ❌ Not implemented |

> **Note:** Current code is tightly coupled to `SmartsheetClient`. Migration to Azure SQL will require rewriting data access layer in all functions.

---

## 4. Priority Action Items

### P0 - Critical (Blocks Core Workflow)

1. **Implement `fn_allocate`** - Create soft reservations from BOM lines
2. **Create `inventory_txn` sheet/table** - Transaction ledger for all inventory movements
3. **Implement idempotency check in mapping lookup** - Prevent duplicate history

### P1 - High (Required for Production)

4. **Create UnitService module** - Centralized unit conversion
5. **Implement effective date filtering** - Override time-bounding
6. **Create `lpo_material_brand_map` usage** - SKU selection for allocation
7. **Implement `fn_pick_confirm`** - Storekeeper workflow
8. **Implement `fn_submit_consumption`** - Production consumption recording

### P2 - Medium (Operational Excellence)

9. **Add `POST /api/map/manual`** - Admin mapping CRUD
10. **Add Power Automate exception notification** - Mapping owner alerts
11. **Implement Repository Pattern** - Migration readiness
12. **Add SAP adapter** - Snapshot sync

### P3 - Low (Future Enhancement)

13. **Implement `fn_create_do`** - Delivery order generation
14. **Implement `fn_reconcile`** - Daily variance check
15. **Power BI dashboards** - T-1 compliance, exceptions, variance

---

## 5. Acceptance Test Readiness

| Test (Spec Section 11) | Status | Blocking Issue |
|------------------------|--------|----------------|
| T-1 happy path (full lifecycle) | ❌ | Missing fn_allocate, fn_pick_confirm, fn_submit_consumption, fn_create_do |
| Duplicate nesting detection | ✅ | - |
| Shortage behavior | ❌ | Missing fn_allocate |
| Overconsumption block | ❌ | Missing fn_submit_consumption |
| Legacy PS DO | ❌ | Missing fn_create_do |
| Reconciliation variance | ❌ | Missing fn_reconcile |

---

## 6. Technical Debt Tracker

| Debt Item | Location | Effort | Risk |
|-----------|----------|--------|------|
| TODO: Implement date range check | `mapping_service.py` L367 | 1h | Low |
| Idempotency check missing | `mapping_service.py` lookup() | 2h | Medium |
| Conversion factor not in history | `mapping_service.py` _log_history() | 1h | Low |
| Repository Pattern not implemented | All functions | 3d | High (migration) |
| SAP adapter not implemented | N/A | 2w | High (integration) |

---

## 7. Next Steps

1. **Immediate**: Implement idempotency check in mapping service (2h)
2. **This Sprint**: Create `UnitService` and `fn_allocate` skeleton
3. **Next Sprint**: Complete allocation → pick → consumption flow
4. **Future**: SAP integration and Repository Pattern refactor

---

*Document generated by Antigravity SOTA Review on 2026-01-27*
