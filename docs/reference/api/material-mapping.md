---
title: Material Mapping API
description: Deterministic lookup service for resolving nesting descriptions to canonical SAP codes
keywords: [material, mapping, lookup, api, canonical, sap]
category: api-reference
version: 1.6.0+
last_updated: 2026-02-06
---

[Home](../../index.md) > [API Reference](./index.md) > Material Mapping

# Material Mapping API

> **Endpoint:** `POST /api/map/lookup` | **Version:** 1.6.0+ | **Last Updated:** 2026-02-06

Deterministic lookup service for resolving raw nesting descriptions to canonical material codes and SAP codes.

---

## Quick Reference

```bash
curl -X POST "{BASE_URL}/api/map/lookup" \
  -H "Content-Type: application/json" \
  -d '{
    "nesting_description": "PIR 25mm",
    "lpo_id": "LPO-100"
  }'
```

---

## Key Features

- ✅ **Audit Trail**: Every lookup logged to `MAPPING_HISTORY` (05d)
- ✅ **Idempotency**: Re-processing same file/line uses cached history for consistency
- ✅ **Override Precedence**: LPO > PROJECT > CUSTOMER > MATERIAL_MASTER
- ✅ **Exception Handling**: Failed lookups create `MAPPING_EXCEPTION` records
- ✅ **Unit Conversion**: Integrated with Unit Service for UOM transformations

---

## Request Schema

### Endpoint

```http
POST /api/map/lookup
Content-Type: application/json
```

### Request Body

```json
{
  "client_request_id": "uuid-v4",
  "nesting_description": "PIR 25mm",
  "lpo_id": "LPO-100",
  "project_name": "Villa 34",
  "customer_name": "BuildCo"
}
```

### Request Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `client_request_id` | string (UUID) | No | Idempotency key (auto-generated if not provided) |
| `nesting_description` | string | Yes | Raw material description from nesting file |
| `lpo_id` | string | No | LPO identifier for override lookup |
| `project_name` | string | No | Project name for override lookup |
| `customer_name` | string | No | Customer name for override lookup |

---

## Response Schemas

### Success (200 OK)

Material found in system with canonical code and SAP code.

```json
{
  "status": "SUCCESS",
  "trace_id": "trace-abc123",
  "data": {
    "canonical_code": "PIR-025",
    "sap_code": "MAT-PIR-025",
    "decision": "FOUND",
    "mapping_history_id": "HIST-12345"
  }
}
```

### Override Applied (200 OK)

LPO/Project/Customer override was applied.

```json
{
  "status": "SUCCESS",
  "trace_id": "trace-abc123",
  "data": {
    "canonical_code": "PIR-025-PREMIUM",
    "sap_code": "MAT-PIR-025-PREM",
    "decision": "OVERRIDE",
    "override_source": "LPO_MATERIAL_BRAND_MAP",
    "mapping_history_id": "HIST-12346"
  }
}
```

### Exception Created (200 OK)

Material not found, exception created for manual resolution.

```json
{
  "status": "SUCCESS",
  "trace_id": "trace-abc123",
  "data": {
    "canonical_code": null,
    "sap_code": null,
    "decision": "EXCEPTION",
    "exception_id": "EX-0789",
    "mapping_history_id": "HIST-12347"
  }
}
```

### Validation Error (400 Bad Request)

```json
{
  "status": "ERROR",
  "error_message": "nesting_description is required",
  "trace_id": "trace-abc123"
}
```

---

## Lookup Logic

### Precedence Order

1. **LPO Material Brand Map (05c)** - If `lpo_id` provided
2. **Mapping Override (05b)** - Project or Customer specific
3. **Material Master (05a)** - Default canonical mapping
4. **Exception (05e)** - If no match found, create exception

### Idempotency

- Subsequent lookups with same `client_request_id` return cached `mapping_history_id`
- Prevents duplicate BOM entries during re-processing

### Audit Trail

All lookups logged to `MAPPING_HISTORY` (05d) with:
- Timestamp
- Input description
- Resolved codes
- Decision path (FOUND/OVERRIDE/EXCEPTION)
- Override source (if applicable)

---

## Integration

### Nesting Parser Integration

Automatically called during nesting parsing (v1.6.0+) to generate BOM:

```python
# Automatic BOM generation during nesting parse
for material_line in nesting_data.materials:
    mapping = await map_lookup(
        nesting_description=material_line.description,
        lpo_id=tag.lpo_id,
        project_name=lpo.project_name,
        customer_name=lpo.customer_name
    )
    create_bom_record(mapping, material_line.quantity)
```

### Unit Conversion

Integrated with Unit Service (v1.6.1) for automatic UOM conversion:
- Reads `CONVERSION_FACTOR` from Material Master
- Converts nesting UOM to SAP UOM
- Falls back to standard conversions (mm↔m, cm↔m)

---

## Related Documentation

- [Material Mapping Sheets](../data/sheets-mapping.md) - Sheet schemas (05a-05e)
- [Unit Service](../data/services.md#unit-service) - UOM conversion logic
- [Nesting Parser API](./nesting-parser.md) - BOM generation flow
- [Mapping Service Code](../data/services.md#mapping-service) - Implementation details
