# Power Automate Flow: Nesting Complete Handler

This document provides the flow definition for the **Nesting Complete** Power Automate flow.

## Purpose

When `fn_parse_nesting` completes, it triggers this flow to:
1. Copy the nesting JSON from Blob Storage to the LPO SharePoint folder
2. Send an acknowledgment email to the uploader

## Environment Variable

Add this to your Azure Function App settings:

```
POWER_AUTOMATE_NESTING_COMPLETE_URL=<HTTP trigger URL from Power Automate>
```

## Flow Definition

### Trigger

**HTTP Request (When a HTTP request is received)**

Request Body JSON Schema:
```json
{
    "type": "object",
    "properties": {
        "nest_session_id": { "type": "string" },
        "tag_id": { "type": "string" },
        "sap_lpo_reference": { "type": "string" },
        "brand": { "type": "string" },
        "json_blob_url": { "type": ["string", "null"] },
        "excel_file_url": { "type": ["string", "null"] },
        "uploaded_by": { "type": "string" },
        "planned_date": { "type": ["string", "null"] },
        "area_consumed": { "type": "number" },
        "area_type": { "type": "string" },
        "correlation_id": { "type": "string" },
        "lpo_folder_url": { "type": ["string", "null"] }
    }
}
```

### Actions

#### 1. Initialize Variables

| Variable Name | Type | Value |
|---------------|------|-------|
| `lpoFolderUrl` | String | `@{triggerBody()?['lpo_folder_url']}` |
| `nestingSubfolder` | String | `Cut Sessions` |

*Note: If `lpo_folder_url` is null, you can add a Condition to fallback to constructing it.*

---

#### 2. Copy JSON and Excel to SharePoint

**Condition A**: `@not(empty(triggerBody()?['json_blob_url']))`
- **Action**: SharePoint - Create file
  - Folder Path: `@{concat('/LPOs/', triggerBody()?['sap_lpo_reference'], '/', variables('nestingSubfolder'))}`
  - File Name: `@{triggerBody()?['nest_session_id']}.json`
  - File Content: HTTP GET `json_blob_url`

**Condition B**: `@not(empty(triggerBody()?['excel_file_url']))` AND `@contains(triggerBody()?['excel_file_url'], 'blob.core.windows.net')`
- **Action**: SharePoint - Create file
  - Folder Path: `@{concat('/LPOs/', triggerBody()?['sap_lpo_reference'], '/', variables('nestingSubfolder'))}`
  - File Name: `@{triggerBody()?['tag_id']}_Nesting.xlsx`
  - File Content: HTTP GET `excel_file_url`

---

#### 4. Send Email Notification

**Action**: Office 365 Outlook - Send an email (V2)

| Field | Value |
|-------|-------|
| To | `@{triggerBody()?['uploaded_by']}` |
| Subject | `Nesting Complete: @{triggerBody()?['tag_id']} - @{triggerBody()?['nest_session_id']}` |
| Body | See template below |

**Email Body (HTML)**:
```html
<h2>Nesting File Processed Successfully</h2>

<table border="1" cellpadding="8" style="border-collapse: collapse;">
<tr><td><b>Nest Session</b></td><td>@{triggerBody()?['nest_session_id']}</td></tr>
<tr><td><b>Tag ID</b></td><td>@{triggerBody()?['tag_id']}</td></tr>
<tr><td><b>LPO</b></td><td>@{triggerBody()?['sap_lpo_reference']}</td></tr>
<tr><td><b>Brand</b></td><td>@{triggerBody()?['brand']}</td></tr>
<tr><td><b>Planned Date</b></td><td>@{triggerBody()?['planned_date']}</td></tr>
<tr><td><b>Area Consumed</b></td><td>@{triggerBody()?['area_consumed']} m² (@{triggerBody()?['area_type']})</td></tr>
</table>

<p><a href="@{variables('lpoFolderUrl')}">View LPO Folder</a></p>

<hr>
<p style="color: gray; font-size: 12px;">Trace ID: @{triggerBody()?['correlation_id']}</p>
```

---

#### 4. Response

**Action**: Response - 200 OK

```json
{
    "status": "success",
    "message": "Nesting complete notification sent",
    "correlation_id": "@{triggerBody()?['correlation_id']}"
}
```

## Quick Setup Steps

1. Go to [Power Automate](https://make.powerautomate.com)
2. Create new **Instant cloud flow** with HTTP trigger
3. Add the actions above
4. Save and copy the **HTTP POST URL**
5. Add URL to Azure Function App as `POWER_AUTOMATE_NESTING_COMPLETE_URL`

## Storage Account Config

Also add these to your Function App settings:

```
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=stgductsprod001;AccountKey=...;EndpointSuffix=core.windows.net
BLOB_CONTAINER_NAME=nesting-outputs
```
