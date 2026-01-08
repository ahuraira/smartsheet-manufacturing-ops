# Power Automate Flow Design: Tag Ingestion

> **Document Type:** Guide | **Version:** 1.0.0 | **Author:** Antigravity

This guide details how to build the **robust, production-grade** Power Automate flow for Tag Ingestion. It implements the **Try-Catch pattern** for error handling, ensuring no request is ever lost without a trace.

---

## 1. Flow Overview

- **Trigger**: Smartsheet (When a new row is created in "Tag Sheet Registry")
- **Authentication**: Usage of Azure Function Token
- **Error Handling**: Scopes (Try/Catch)
- **Retry Policy**: Exponential Backoff

---

## 2. Step-by-Step Implementation

### Phase 1: The Trigger

1.  **Trigger**: `When a new row is created` (Smartsheet Connector)
    *   **Sheet**: Select `Tag Sheet Registry`

### Phase 2: Initialization (Variables)

2.  **Action**: `Initialize Variable`
    *   **Name**: `Client Request ID`
    *   **Type**: `String`
    *   **Value**: `guid()` (Expression)
    *   *Why? This ID ensures if the flow retries, the Azure Function knows it's the same request (Idempotency).*

3.  **Action**: `Initialize Variable`
    *   **Name**: `Processing Status`
    *   **Type**: `String`
    *   **Value**: `Started`

### Phase 3: The "Try" Scope (Main Logic)

4.  **Action**: `Scope` (Naming it: **Scope - Main Logic**)
    *   *Add all the following actions INSIDE this scope.*

    a.  **Action**: `HTTP` (Naming it: **Call Azure Function**)
        *   **Method**: `POST`
        *   **URI**: `https://<YOUR-APP-NAME>.azurewebsites.net/api/tags/ingest`
        *   **Headers**:
            *   `x-functions-key`: `<YOUR-FUNCTION-KEY>`
            *   `Content-Type`: `application/json`
        *   **Body**:
            ```json
            {
              "client_request_id": "@{variables('Client Request ID')}",
              "lpo_sap_reference": "@{triggerOutputs()?['body/LPO SAP Reference Link']}",
              "required_area_m2": @{float(triggerOutputs()?['body/Estimated Quantity'] ?? 0)},
              "requested_delivery_date": "@{triggerOutputs()?['body/Required Delivery Date']}",
              "uploaded_by": "@{triggerOutputs()?['body/Created By']}",
              "tag_name": "@{triggerOutputs()?['body/Tag Sheet Name/ Rev']}"
            }
            ```
        *   **Settings (Three dots ... -> Settings)**:
            *   **Retry Policy**: Default is usually fine, or set to "Exponential Interval" (Count: 3, Interval: PT10S).

    b.  **Action**: `Parse JSON`
        *   **Content**: `@{body('Call_Azure_Function')}`
        *   **Schema**:
            ```json
            {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "tag_id": {"type": "string"},
                    "exception_id": {"type": "string"},
                    "message": {"type": "string"},
                    "trace_id": {"type": "string"}
                }
            }
            ```

    c.  **Action**: `Condition` (Check Status)
        *   **Expression**: `body('Parse_JSON')?['status']` **is equal to** `UPLOADED`
        
        *   **If Yes (Success)**:
            *   **Action**: `Update Row` (Smartsheet)
                *   **Row Id**: `@{triggerOutputs()?['body/ID']}`
                *   **Status**: `Active` (or `Ready`)
                *   **Tag ID**: `@{body('Parse_JSON')?['tag_id']}`
                *   **Remarks**: `Success. Trace: @{body('Parse_JSON')?['trace_id']}`
        
        *   **If No (Business Error)**:
            *   **Action**: `Update Row` (Smartsheet)
                *   **Row Id**: `@{triggerOutputs()?['body/ID']}`
                *   **Status**: `Blocked`
                *   **Remarks**: `Error: @{body('Parse_JSON')?['message']} (Ref: @{body('Parse_JSON')?['exception_id']})`

### Phase 4: The "Catch" Scope (System Error Handling)

5.  **Action**: `Scope` (Naming it: **Scope - Error Handler**)
    *   **Configure Run After**: Click the three dots (...) on this scope -> **Configure run after**.
    *   Select **Only when 'Scope - Main Logic' has failed** or **timed out**.

    a.  **Action**: `Update Row` (Smartsheet)
        *   **Row Id**: `@{triggerOutputs()?['body/ID']}`
        *   **Status**: `System Error`
        *   **Remarks**: `System Failure: Flow failed. Request ID: @{variables('Client Request ID')}`

    b.  **Action**: `Terminate`
        *   **Status**: `Failed`
        *   **Message**: `Flow failed in Main Logic scope.`

---

## 3. Best Practices Summary

| Feature | Why it's important |
| :--- | :--- |
| **Scopes (Try/Catch)** | Ensures "System Error" status is written to Smartsheet even if the API call crashes or timeouts. |
| **Client Request ID** | Guarantees that if the flow retries the HTTP call, the Azure Function won't create duplicates. |
| **Secure Input/Output** | In the HTTP action settings, enable "Secure Inputs" if you are passing sensitive tokens (though here we use headers). |
| **Float Conversion** | `float(...)` in the expression ensures numbers are passed correctly to Python. |

## 4. Testing Your Flow

1.  **Save** the flow.
2.  Click **Test** -> **Manually**.
3.  Go to Smartsheet -> **Tag Sheet Registry**.
4.  Add a new row manually (mimicking a form submission).
5.  Watch the flow run!
    *   Check if it went into the "Success" or "Failure" branch.
    *   Verify the row in Smartsheet was updated.
