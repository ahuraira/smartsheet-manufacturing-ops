---
title: Your First API Integration
description: Step-by-step tutorial for uploading your first tag and scheduling it for production
keywords: [tutorial, getting started, api, tag ingestion, curl, python]
category: tutorial
version: 1.0.0
last_updated: 2026-02-06
---

[Home](../index.md) > Tutorials > First API Integration

# Your First API Integration

> **Document Type:** Tutorial | **Time:** 15 minutes | **Level:** Beginner

Learn how to integrate with the Ducts Manufacturing API by uploading a tag sheet and scheduling it for production.

---

## What You'll Learn

By the end of this tutorial, you'll be able to:
- ✅ Authenticate with the API
- ✅ Upload a tag sheet file
- ✅ Verify the tag in Smartsheet
- ✅ Schedule the tag for production
- ✅ Handle API responses and errors

---

## Prerequisites

Before starting, ensure you have:
- [ ] **API Credentials** - Base URL and authentication headers
- [ ] **Test LPO** - An active LPO in the system (e.g., `LPO-0001`)
- [ ] **Sample Tag File** - PDF/Excel tag sheet to upload
- [ ] **Tool** - Postman, curl, or Python 3.11+

> **Don't have test data?** Contact your admin for sandbox credentials.

---

## Step 1: Setup Your Environment

### Option A: Using curl (Quick Start)

```bash
# Set your base URL
export API_BASE="https://your-function-app.azurewebsites.net"

# Test connectivity
curl "$API_BASE/api/health"
# Expected: {"status": "healthy"}
```

### Option B: Using Python

```python
# install requests
# pip install requests

import requests
import base64

API_BASE = "https://your-function-app.azurewebsites.net"

# Test connectivity
response = requests.get(f"{API_BASE}/api/health")
print(response.json())  # {"status": "healthy"}
```

---

## Step 2: Prepare Your Tag File

You need to base64-encode your file before uploading.

### Using Command Line

```bash
# Encode your tag file
base64 -i tag_sheet.pdf -o tag_encoded.txt

# Or inline:
FILE_CONTENT=$(base64 -i tag_sheet.pdf)
```

### Using Python

```python
import base64

with open("tag_sheet.pdf", "rb") as f:
    file_content = base64.b64encode(f.read()).decode('utf-8')
    
print(f"Encoded {len(file_content)} characters")
```

---

## Step 3: Upload Your First Tag

### Request: POST /api/tags/ingest

#### curl Example

```bash
curl -X POST "$API_BASE/api/tags/ingest" \
  -H "Content-Type: application/json" \
  -d '{
    "lpo_sap_reference": "PTE-185",
    "required_area_m2": 150.5,
    "requested_delivery_date": "2026-03-15",
    "files": [
      {
        "file_type": "other",
        "file_content": "'$FILE_CONTENT'",
        "file_name": "tag_sheet.pdf"
      }
    ],
    "uploaded_by": "john.doe@company.com",
    "tag_name": "Villa A - Ductwork"
  }'
```

#### Python Example

```python
import requests

payload = {
    "lpo_sap_reference": "PTE-185",
    "required_area_m2": 150.5,
    "requested_delivery_date": "2026-03-15",
    "files": [
        {
            "file_type": "other",
            "file_content": file_content,  # from Step 2
            "file_name": "tag_sheet.pdf"
        }
    ],
    "uploaded_by": "john.doe@company.com",
    "tag_name": "Villa A - Ductwork"
}

response = requests.post(
    f"{API_BASE}/api/tags/ingest",
    json=payload
)

print(response.status_code)  # 200
print(response.json())
```

### Expected Response (Success)

```json
{
  "status": "UPLOADED",
  "tag_id": "TAG-20260206-0001",
  "file_hash": "sha256:abc123...",
  "trace_id": "trace-xyz789",
  "message": "Tag uploaded successfully"
}
```

**🎉 Success!** Save the `tag_id` for the next step.

### Common Errors

#### LPO Not Found (422)

```json
{
  "status": "BLOCKED",
  "exception_id": "EX-0002",
  "message": "LPO with SAP reference PTE-999 not found"
}
```

**Fix:** Verify the SAP reference exists in the LPO Master sheet.

#### Duplicate File (409)

```json
{
  "status": "DUPLICATE",
  "tag_id": "TAG-20260205-0042",
  "message": "File already uploaded"
}
```

**Fix:** This file was already uploaded. Use the existing `tag_id`.

---

## Step 4: Verify in Smartsheet

1. Open **Smartsheet** workspace
2. Navigate to **02 Tag Sheet Registry**
3. Find your tag by ID: `TAG-20260206-0001`
4. Verify:
   - ✅ Status = `Validate`
   - ✅ LPO SAP Reference = `PTE-185`
   - ✅ Required Area = `150.5`
   - ✅ File attached

---

## Step 5: Schedule for Production

Now let's schedule your tag for a production shift.

### Request: POST /api/production/schedule

#### curl Example

```bash
curl -X POST "$API_BASE/api/production/schedule" \
  -H "Content-Type: application/json" \
  -d '{
    "tag_id": "TAG-20260206-0001",
    "machine_id": "CUT-001",
    "planned_date": "2026-02-10",
    "shift": "Morning",
    "planned_quantity_sqm": 150.5,
    "scheduled_by": "john.doe@company.com"
  }'
```

#### Python Example

```python
schedule_payload = {
    "tag_id": "TAG-20260206-0001",  # from Step 3
    "machine_id": "CUT-001",
    "planned_date": "2026-02-10",
    "shift": "Morning",
    "planned_quantity_sqm": 150.5,
    "scheduled_by": "john.doe@company.com"
}

response = requests.post(
    f"{API_BASE}/api/production/schedule",
    json=schedule_payload
)

print(response.json())
```

### Expected Response

```json
{
  "status": "OK",
  "schedule_id": "SCHED-0001",
  "tag_id": "TAG-20260206-0001",
  "machine_id": "CUT-001",
  "planned_date": "2026-02-10",
  "shift": "Morning",
  "next_action_deadline": "2026-02-09T18:00:00",
  "trace_id": "trace-abc123",
  "message": "Schedule created. Nesting file required by 2026-02-09 18:00"
}
```

**Important:** Note the `next_action_deadline` - nesting file must be uploaded by **18:00 the day before production** (T-1 rule).

---

## Step 6: Verify Production Schedule

1. Open **03 Production Planning** sheet
2. Find your schedule: `SCHED-0001`
3. Verify:
   - ✅ Tag ID = `TAG-20260206-0001`
   - ✅ Machine = `CUT-001`
   - ✅ Planned Date = `2026-02-10`
   - ✅ Shift = `Morning`
   - ✅ Status = `Planned`

---

## Next Steps

🎓 You've successfully completed your first API integration! Here's what to explore next:

### Immediate Next Actions
1. **Upload Nesting File** - Parse your nesting file with [Nesting Parser API](../reference/api/nesting-parser.md)
2. **Check PO Balance** - Verify remaining LPO capacity
3. **View Exceptions** - Check if any exceptions were created

### Deep Dive
- [API Reference Overview](../reference/api/index.md) - Explore all 7 endpoints
- [Data Dictionary](../reference/data/index.md) - Understand sheet schemas
- [Error Codes](../reference/error_codes.md) - Handle all error scenarios

### Advanced Topics
- [Material Mapping](../reference/api/material-mapping.md) - Map nesting descriptions to SAP codes
- [Event Dispatcher](../reference/api/event-dispatcher.md) - Webhook-driven automation
- [Idempotency Guide](../howto/idempotency.md) - Safe retry strategies

---

## Troubleshooting

### API Returns 401 Unauthorized
- **Cause:** Missing or invalid authentication
- **Fix:** Verify API key/token in headers

### Tag Upload Returns 400 Bad Request
- **Cause:** Invalid request payload
- **Fix:** Check required fields: `lpo_sap_reference`, `required_area_m2`, `requested_delivery_date`, `files`

### Schedule Returns "Insufficient PO Balance"
- **Cause:** LPO doesn't have enough available quantity
- **Fix:** Check LPO Master → `PO Quantity - Delivered - Committed - Planned`

### Need More Help?
- Review [Troubleshooting Guide](../howto/troubleshooting.md)
- Check [Exception Log (99)](../reference/data/sheets-governance.md#exception-log) for details
- Contact your system admin

---

## Summary

In this tutorial, you:
- ✅ Set up your API environment
- ✅ Uploaded a tag sheet via API
- ✅ Verified the tag in Smartsheet
- ✅ Scheduled the tag for production
- ✅ Learned to handle common errors

**Time Invested:** 15 minutes  
**Skills Gained:** REST API integration, Base64 encoding, Error handling

**Ready for more?** Check out the [How-To Guides](../howto/) for advanced scenarios!
