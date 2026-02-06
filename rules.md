# 🤖 AI Agent Rules & Directives

> **Scope:** These rules are **MANDATORY** for all AI agents working on this codebase.
> **Enforcement:** Failure to follow these rules constitutes a critical failure of the task.

---

## 🛑 DO NOT (Forbidden Actions)

1.  **DO NOT Hardcode Smartsheet Names:**
    *   Never use string literals for sheet or column names (e.g., "Tag Registry", "Status").
    *   *Why:* Users rename sheets/columns constantly. Hardcoded names break the system.
    *   *Correction:* Always use Logical Names (`Sheet.TAG_REGISTRY`, `Column.TAG_REGISTRY.STATUS`) which resolve to IDs via the Manifest.

2.  **DO NOT Bypass the Manifest:**
    *   Never add a new sheet or column to the code without verifying it exists in `workspace_manifest.json`.
    *   *Correction:* If you add a new logical name, you must instruct the user to run `python fetch_manifest.py` or manually update the manifest.

3.  **DO NOT Use Non-Deterministic IDs for Idempotency:**
    *   Never use timestamps or random values in `client_request_id` for event handlers.
    *   *Why:* Retries will generate new IDs, bypassing idempotency checks and causing duplicate records.
    *   *Correction:* Use deterministic keys (e.g., `f"staging-lpo-{row_id}"`).

4.  **DO NOT Perform "Read-Then-Write" Without Locking:**
    *   Never implement inventory checks (e.g., `if available > required: deduct`) without external locking or atomic operations.
    *   *Why:* Smartsheet is "last-write-wins". Concurrency will cause over-allocation.
    *   *Correction:* Flag these operations as "REQUIRES_LOCKING" in comments if you cannot implement a lock immediately.

5.  **DO NOT Put Business Logic in Smartsheet:**
    *   Never create complex formulas in Smartsheet columns to drive system behavior.
    *   *Why:* Formulas are fragile, untestable, and hidden from version control.
    *   *Correction:* All logic must reside in Azure Functions (Python).

---

## ✅ MUST DO (Mandatory Patterns)

1.  **MUST Use ID-First Architecture:**
    *   Always import `Sheet` and `Column` from `shared.logical_names`.
    *   Always use `get_manifest()` to resolve IDs.
    *   *Example:* `client.get_row(Sheet.TAG_REGISTRY, row_id)` (Not `client.get_row("Tag Registry", ...)`).

2.  **MUST Implement Idempotency:**
    *   Every `POST` or `PUT` operation must accept and check a `client_request_id`.
    *   If the ID exists, return `200 OK` with the existing resource (do not error, do not duplicate).

3.  **MUST Log User Actions:**
    *   Every state change (Create, Update, Delete) must be logged to the `User Action Log` using `log_user_action()`.
    *   *Why:* Auditability is a core requirement.

4.  **MUST Create Exceptions for Failures:**
    *   Never fail silently or just log to console.
    *   Create a record in the `Exception Log` using `create_exception()` for any business rule violation (e.g., Validation Error, LPO Mismatch).

5.  **MUST Follow DRY Principles:**
    *   Check `functions/shared/` before writing new helper functions.
    *   If you write a utility, place it in `shared/` if it's used by more than one function.

---

## 📂 File & Directory Structure Rules

*   **`functions/`**: Core Business Logic (The "Brain").
*   **`function_adapter/`**: Ingestion & Routing (The "Nervous System").
*   **`shared/`**: Common code. **Do not duplicate logic between adapter and core.**
*   **`workspace_manifest.json`**: The Source of Truth for IDs. Treat as read-only for code; updated by scripts.

---

## 🧪 Testing Requirements

1.  **Mock External Services:** Never hit the real Smartsheet API in unit tests. Use `MockSmartsheetClient`.
2.  **Test Idempotency:** Every new write function must have a test case for "duplicate request returns success".
3.  **Test Concurrency:** Critical sections (e.g., allocation) require tests that simulate race conditions.

---

## 🔍 Self-Correction Checklist

Before marking a task as complete, ask:
1.  [ ] Did I use a hardcoded string for a column name? (If yes -> Fix it)
2.  [ ] Did I handle what happens if the user clicks the button twice? (Idempotency)
3.  [ ] If I added a new column, is it in the manifest?
4.  [ ] Did I log the action to the Audit Log?
