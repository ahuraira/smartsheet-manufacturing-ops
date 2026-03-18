---
title: Shared Services
description: Centralized business logic modules and helper functions
keywords: [services, lpo service, unit service, helpers]
category: data-reference
version: 1.6.9
---

[Home](../../index.md) > [Data Dictionary](./index.md) > Shared Services

# Shared Services

> **Document Type:** Reference | **Version:** 1.6.9 | **Last Updated:** 2026-02-06

Centralized business logic modules and helper functions.

---

## LPO Service (v1.6.6)

Centralized LPO operations for DRY compliance.

**Module:** `functions/shared/lpo_service.py`

### Key Functions

#### `find_lpo_flexible(client, sap_ref, customer_ref)`
Multi-field LPO lookup supporting both SAP and customer references.

**Returns:** `LPORow` or `None`

#### `get_lpo_quantities(lpo_row)`
Extract quantity data from LPO row.

**Returns:** `LPOQuantities` dataclass
```python
@dataclass
class LPOQuantities:
    po_qty: float
    delivered: float
    allocated: float = 0.0
    planned: float = 0.0
```

#### `get_lpo_status(lpo_row)`
Extract normalized LPO status.

**Returns:** `str` (ACTIVE, ON_HOLD, CLOSED, etc.)

#### `validate_lpo_status(lpo_row)`
Check if LPO is available (not on hold).

**Returns:** `bool`

#### `validate_po_balance(lpo_quantities, requested_area)`
Validate sufficient PO balance with 5% tolerance.

**Returns:** `tuple[bool, str]` - (is_valid, error_message)

---

## Unit Service (v1.6.1)

Centralized unit conversion with Material Master integration.

**Module:** `functions/shared/unit_service.py`

### Key Functions

#### `convert_units(quantity, from_uom, to_uom, conversion_factor=None)`
Convert quantities between units of measure.

**Parameters:**
- `quantity`: Source quantity
- `from_uom`: Source UOM (e.g., "mm", "Lm")
- `to_uom`: Target UOM (e.g., "m", "Sqm")
- `conversion_factor`: Optional explicit factor from Material Master

**Returns:** `float` - Converted quantity

**Supported Conversions:**
- Length: mm ↔ m, cm ↔ m, Lm ↔ m
- Area: Sqm (no conversion needed)
- Custom: Via Material Master `CONVERSION_FACTOR` column

---

## Atomic Update (v1.6.9)

Safe read-modify-write operations with collision handling.

**Module:** `functions/shared/atomic_update.py`

### Key Functions

#### `atomic_increment(client, sheet, row_id, column, amount)`
Safe increment with automatic retry on collision.

**Parameters:**
- `client`: SmartsheetClient instance
- `sheet`: Sheet enum (e.g., `Sheet.CONFIG`)
- `row_id`: Row ID to update
- `column`: Column name
- `amount`: Increment amount

**Returns:** `new_value: int`

**Features:**
- Detects Smartsheet 4004 collision errors
- Exponential backoff with jitter (100ms-3s)
- Max 5 retries

#### `atomic_set_if_equals(client, sheet, row_id, column, expected,  new_value)`
Compare-and-swap operation.

**Returns:** `bool` - True if updated, False if collision

---

## Power Automate Integration (v1.6.9)

**Module:** `functions/shared/power_automate.py`

### Models

#### FileUploadItem
```python
class FileUploadItem(BaseModel):
    file_name: str
    file_content: str  # Base64 encoded
    subfolder: str  # e.g., "01_LPO_Documents"
```

### Functions

#### `trigger_upload_files_flow(client, lpo_folder_url, files)`
Generic file upload to SharePoint via Power Automate.

**Parameters:**
- `lpo_folder_url`: SharePoint folder URL
- `files`: List[FileUploadItem]

#### `trigger_nesting_complete_flow(client, data)` (v1.6.7)
Trigger nesting completion Power Automate flow.

**Parameters:**
- `data`: Nesting parse result with tag_id, blob_url, etc.

---

## Helper Functions

**Module:** `functions/shared/helpers.py`

### `resolve_user_email(client, user_id)` (v1.6.8)
Convert Smartsheet user ID to email address.

**Returns:** `str` - User email

### `generate_next_lpo_id(client)` (v1.6.8)
Auto-generate next LPO ID using `SEQ_LPO` config key.

**Returns:** `str` - e.g., "LPO-0024"

**Implementation:**
- Uses `atomic_increment()` for thread-safe sequence generation
- Format: `LPO-{:04d}` (zero-padded 4 digits)

### `get_physical_column_name(logical_name, sheet_id)`
Resolve logical column name to physical name via manifest.

**Returns:** `str` - Physical column name

### `compute_combined_file_hash(files)` (v1.6.3)
Generate SHA-256 hash for multi-file deduplication.

**Parameters:**
- `files`: List[FileAttachment]

**Returns:** `str` - Combined hash (e.g., "sha256:abcd1234...")

---

## Consumption Service

Handles material consumption recording and variance tracking.

**Module:** `functions/shared/consumption_service.py`

### Key Behaviors

- **Variance calculation**: Variance is calculated from system allocation quantity (not user-submitted quantity)
- **Consumed rejection**: Allocations with status "Consumed" are rejected to prevent double-processing
- **Unmapped material logging**: Unmapped materials now log warnings instead of being silently skipped
- **Exception creation on margin failure**: Margin orchestrator failures create exception records in the Exception Log

---

## Queue Lock

Distributed lock for serializing concurrent operations.

**Module:** `functions/shared/queue_lock.py`

### Key Behaviors

- **Default timeout**: 60 seconds (updated from 30s)
- **`is_likely_held()` method**: Check if the lock is likely held by another process without attempting acquisition

---

## Mapping Service

Material code mapping with caching.

**Module:** `functions/shared/mapping_service.py`

### Key Behaviors

- **Timezone fix**: Timestamps are now consistently handled in UTC
- **Stale cache fix**: Cache invalidation corrected to prevent serving outdated mappings
- **Conversion factor logging**: Conversion factor lookups are now logged for traceability

---

## Related Documentation

- [Data Models](./models.md) - LPOQuantities, FileUploadItem schemas
- [Configuration](../configuration.md) - Config keys (SEQ_LPO, etc.)
- [LPO Ingestion API](../api/lpo-ingestion.md) - LPO Service usage
- [Nesting Parser API](../api/nesting-parser.md) - Unit Service integration
