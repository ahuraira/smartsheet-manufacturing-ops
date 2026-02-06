---
title: Production Scheduling API
description: Schedule tags for production with automatic deadline calculation and PO validation
keywords: [scheduling, production, api, planning, manufacturing]
category: api-reference
version: 1.3.0+
last_updated: 2026-02-06
---

[Home](../../index.md) > [API Reference](./index.md) > Production Scheduling

# Production Scheduling API

> **Endpoint:** `POST /api/production/schedule` | **Version:** 1.3.0+ | **Last Updated:** 2026-02-06

Schedule tags for production on specific machines and shifts with automatic deadline calculation and PO balance validation.

---

## Quick Reference

```bash
curl -X POST "{BASE_URL}/api/production/schedule" \
  -H "Content-Type: application/json" \
  -d '{
    "tag_id": "TAG-0001  
    "machine_id": "CUT-001",
    "planned_date": "2026-01-15",
    "shift": "Morning",
    "planned_quantity_sqm": 50.0,
    "scheduled_by": "planner@company.com"
  }'
```

---

## Request Schema

### End point

```http
POST /api/production/schedule
Content-Type: application/json
```

### Request Body

```json
{
  "client_request_id": "uuid-v4",
  "tag_id": "TAG-0001",
  "machine_id": "CUT-001",
  "planned_date": "2026-01-15",
  "shift": "Morning",
  "planned_quantity_sqm": 50.0,
  "scheduled_by": "planner@company.com",
  "notes": "Priority order"
}
```

### Request Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `client_request_id` | string (UUID) | Yes¹ | Idempotency key |
| `tag_id` | string | Yes | Tag ID to schedule (e.g., TAG-0001) |
| `machine_id` | string | Yes | Machine ID (e.g., CUT-001) |
| `planned_date` | string (ISO) | Yes | Planned production date |
| `shift` | string | Yes | Shift: `Morning` or `Evening` |
| `planned_quantity_sqm` | number | Yes | Planned quantity in sqm (> 0) |
| `scheduled_by` | string | Yes | User creating the schedule |
| `notes` | string | No | Additional notes |

> ¹ Auto-generated if not provided

---

## Validations Performed

1. **Tag Validation**: Tag must exist and status ≠ CANCELLED/CLOSED
2. **Machine Validation**: Machine must exist and be OPERATIONAL
3. **PO Balance Check**: (committed + planned) ≤ PO quantity (with 5% tolerance)
4. **Duplicate Check**: Same tag already scheduled → 409 DUPLICATE

---

## Response Schemas

### Success (200 OK)

```json
{
  "status": "OK",
  "schedule_id": "SCHED-0001",
  "tag_id": "TAG-0001",
  "machine_id": "CUT-001",
  "planned_date": "2026-01-15",
  "shift": "Morning",
  "next_action_deadline": "2026-01-14T18:00:00",
  "trace_id": "trace-abc123def456",
  "message": "Schedule created. Nesting file required by 2026-01-14 18:00"
}
```

> **T-1 Deadline**: `next_action_deadline` indicates when the nesting file must be uploaded (18:00 the day before).

### Tag Not Found (422 Unprocessable)

```json
{
  "status": "BLOCKED",
  "exception_id": "EX-0007",
  "trace_id": "trace-abc123def456",
  "message": "Tag TAG-9999 not found"
}
```

### Machine Under Maintenance (422 Unprocessable)

```json
{
  "status": "BLOCKED",
  "exception_id": "EX-0008",
  "trace_id": "trace-abc123def456",
  "message": "Machine CUT-001 is under maintenance"
}
```

---

## Business Rules

1. **Idempotency**: Duplicate `client_request_id` returns cached response
2. **T-1 Deadline Rule**: Nest ing file must be uploaded by 18:00 the day before production
3. **PO Balance**: Validates sufficient quantity available (with 5% tolerance)
4. **Machine Availability**: Only OPERATIONAL machines can be scheduled
5. **Tag Status**: Only PLANNED tags can be scheduled

---

## Related Documentation

- [Tag Ingestion](./tag-ingestion.md) - Create tags before scheduling
- [Production Planning Sheet](../data/sheets-production.md#production-planning) - Sheet schema
- [Schedule Handler](../data/services.md#schedule-handler) - Event dispatcher integration (v1.6.6)
