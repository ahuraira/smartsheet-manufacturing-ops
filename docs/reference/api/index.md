---
title: API Reference
description: Complete API documentation for all Azure Functions endpoints
keywords: [api, rest, azure functions, endpoints, reference]
category: api-reference
version: 1.6.9
last_updated: 2026-02-06
---

[Home](../../index.md) > API Reference

# 📘 API Reference

> **Document Type:** Reference | **Version:** 1.6.9 | **Last Updated:** 2026-02-06

Complete API documentation for all Azure Functions endpoints in the Ducts Manufacturing Inventory Management System.

---

## Overview

### Base URLs

| Environment | Base URL |
|-------------|----------|
| Local Development | `http://localhost:7071` |
| Development | `https://dev-fn-ducts.azurewebsites.net` |
| UAT | `https://uat-fn-ducts.azurewebsites.net` |
| Production | `https://prod-fn-ducts.azurewebsites.net` |

### API Versioning

Currently, the API is at version 1.0. Future versions will use URL path versioning:
- `/api/v1/tags/ingest`
- `/api/v2/tags/ingest`

---

## Authentication

### Azure AD Authentication (Production)

Production endpoints are secured with Azure AD:

```http
Authorization: Bearer <JWT_TOKEN>
```

**Required Scopes:**
- `api://ducts-inventory/read` - For GET operations
- `api://ducts-inventory/write` - For POST/PUT operations

### API Key Authentication (Development)

Development environments use API keys:

```http
x-functions-key: <FUNCTION_KEY>
```

---

## Common Headers

All requests should include:

| Header | Value | Required | Description |
|--------|-------|----------|-------------|
| `Content-Type` | `application/json` | Yes | Request body format |
| `Accept` | `application/json` | No | Response format |
| `x-client-id` | UUID string | Recommended | Client identification for tracing |

---

## Response Format

### Success Response

All successful responses follow this structure:

```json
{
  "status": "SUCCESS",
  "trace_id": "uuid",
  "data": { ... },
  "warnings": []
}
```

### Error Response

All error responses include:

```json
{
  "status": "ERROR",
  "error_message": "Human-readable message",
  "validation_errors": ["Field-specific errors"],
  "trace_id": "uuid"
}
```

### HTTP Status Codes

| Code | Meaning | When Used |
|------|---------|-----------|
| 200 | OK | Successful request (even with logical errors - check `status` field) |
| 400 | Bad Request | Invalid request format |
| 401 | Unauthorized | Missing or invalid authentication |
| 403 | Forbidden | Insufficient permissions |
| 429 | Too Many Requests | Rate limit exceeded |
| 500 | Internal Server Error | System failure |

---

## Rate Limiting

- **Development:** No limits
- **Production:** 100 requests/minute per API key
- Rate limit headers included in responses:
  - `X-RateLimit-Limit`: Maximum requests
  - `X-RateLimit-Remaining`: Remaining requests
  - `X-RateLimit-Reset`: Unix timestamp for reset

---

## API Endpoints

## Navigation

### Interactive Tools
- [🎮 API Playground](../../api-playground/index.html) - Try endpoints interactively (Swagger UI)

### Endpoint Documentation
- [Tag Ingestion](./tag-ingestion.md) - Upload tag sheets with validation
- [LPO Ingestion](./lpo-ingestion.md) - Create Local Purchase Orders  
- [Nesting Parser](./nesting-parser.md) - Parse nesting files and extract BOM (v2.0.0)
- [Material Mapping](./material-mapping.md) - Lookup material codes (v1.6.0)
- [Production Scheduling](./scheduling.md) - Schedule tags for production shifts
- [Event Dispatcher](./event-dispatcher.md) - Webhook event routing (v1.4.0)

---

## Related Documentation

- [Data Models](../data/models.md) - Request/response schemas
- [Error Codes](../error_codes.md) - Detailed error reference
- [Configuration](../configuration.md) - Environment variables
