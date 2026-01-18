This is a **Technical Specification Document** designed for a Senior Python Developer or Solution Architect. It abstracts the business context and focuses purely on the Input/Output transformation, parsing logic, and robustness requirements required for an Azure Function.

You can hand this document directly to your development team.

***

# Technical Specification: `fn_ParseNestingFile`
**Version:** 1.0
**Type:** Azure Function (Python 3.9+)
**Trigger:** HTTP Request (or Blob Trigger via Event Grid)
**Output:** Application/JSON

---

## 1. Executive Summary
The objective is to develop a **stateless, idempotent Azure Function** that accepts a raw Excel export from Eurosoft CutExpert (nesting software) and transforms it into a strictly typed, hierarchical JSON payload.

This function acts as the **"Forensic Bridge"** between the Engineering / Shop Floor data and the Financial ERP. It must handle messy human-readable Excel formats robustly using an "Anchor-Based" search strategy, ensuring no data is lost even if row/column positions shift slightly in future software updates.

---

## 2. Input Specification
*   **File Format:** `.xls` or `.xlsx` (Excel 97-2003 or Modern Workbook).
*   **Content:** Multi-sheet workbook containing merged cells, floating headers, and non-tabular layouts.
*   **Key Sheets to Parse:**
    1.  `Project parameters` (General Stats)
    2.  `Panels info` (Sheet Consumption)
    3.  `Flanges` (Profile Consumption)
    4.  `Other components` (Consumables)
    5.  `Machine info` (Telemetry/Maintenance)
    6.  `Delivery order` (Line Items for Shipping)

**Critical Constraint:**
*   **Identity Mapping:** The field labeled **PROJECT REFERENCE** (or sometimes PROJECT NAME depending on Operator input) will contain the **Tag ID** (e.g., `TAG-1001`).
*   **Project Name:** Treat as "Operational Reference" string only.
*   **Tag ID** is the Primary Key for the entire downstream logic.

---

## 3. Parsing Strategy: "Anchor & Offset"
**Do NOT use hardcoded cell references (e.g., `A5`, `D12`).** The Eurosoft export format varies based on the number of profiles used.

**Requirement:** Implement a robust searching algorithm using `pandas` or `openpyxl`.

### 3.1 The "Find Anchor" Logic
Create a helper function `get_value_by_anchor(sheet, anchor_string, col_offset, row_offset)`:
1.  Scan the specific DataFrame/Sheet for the `anchor_string` (e.g., "Utilized sheets").
2.  Locate the coordinates $(x, y)$.
3.  Return the value at $(x + row\_offset, y + col\_offset)$.
4.  **Error Handling:** If anchor is not found, log a warning and return `null` (do not crash the function unless it is a mandatory field like Tag ID).

---

## 4. Extraction Logic (Sheet by Sheet)

### Sheet 1: `Project parameters` & `Panels info`
**Goal:** Extract Material Specifications and Gross Consumption.

| Data Point | Anchor Text | Logic/Transformation | Target JSON Field |
| :--- | :--- | :--- | :--- |
| **Material** | "Material" | Extract value. | `raw_material_panel.material_spec_name` |
| **Thickness** | "Thickness" | Cast to Float. | `raw_material_panel.thickness_mm` |
| **Gross Qty** | "Utilized sheets" | Cast to Int. **CRITICAL:** This implies inventory deduction. | `raw_material_panel.inventory_impact.utilized_sheets_count` |
| **Panel Offcut** | "Total area reusable" | Cast to Float. This creates a Remnant Asset. | `raw_material_panel.inventory_impact.net_reusable_remnant_area_m2` |
| **Waste Area** | "Wastage due to nesting" | Cast to Float. | `raw_material_panel.efficiency_metrics.nesting_waste_m2` |

### Sheet 2: `Flanges` (Complex Layout)
**Goal:** Extract Linear Consumption and Remnants for Profiles (U, F, H, etc.).
**Layout Warning:** This sheet contains multiple small tables stacked vertically.

**Logic:**
1.  Iterate through the sheet rows.
2.  Identify a **Block Start** by finding the text `PROFILE TYPE`.
3.  The cell immediately below `PROFILE TYPE` is the **Profile Name** (e.g., "U PROFILE").
4.  Within that block (until the next `PROFILE TYPE` or End of Sheet):
    *   Find Anchor `TOTAL LENGHT (mm)` -> Sum the values in that column. Convert `mm` to `m`. -> **Consumption**.
    *   Find Anchor `Remaining [type] profile (mt)` -> Extract value. -> **Asset (Remnant)**.
    *   **Validation:** Verify that `Consumption + Remnant ≈ Total Issued Stock` (Logic handled downstream, but extraction must be precise).

### Sheet 3: `Other components`
**Goal:** Extract Consumables (Tape, Glue, Silicone).
**Logic:** Use key-value pair extraction.

| Anchor Text | Value Location | JSON Target |
| :--- | :--- | :--- |
| "Total need of silicone" | Column + 2 (Kg) | `consumables.silicone.consumption_kg` |
| "Total need of aluminum tape" | Column + 2 (mt) | `consumables.aluminum_tape.consumption_m` |
| "Total glue for Junctions" | Column + 2 (Kg) | `consumables.glue_junction_kg` |

### Sheet 4: `Delivery order`
**Goal:** Extract Finished Goods Line Items.
**Layout:** This is a structured table.

**Logic:**
1.  Identify the Header Row containing `PART DESCRIPTION`, `ID`, `TAG`.
2.  Read the table into a List of Dictionaries.
3.  **Sanitization:** Remove rows where `ID` is null or empty.
4.  **Field Mapping:**
    *   `ID` -> `line_id`
    *   `PART DESCRIPTION` -> `description`
    *   `MOUTH A X`, `MOUTH A Y` -> `geometry.mouth_a_x`, `geometry.mouth_a_y`
    *   `QTY` -> `qty_produced`
    *   `EXT AREA` -> `area_m2`

### Sheet 5: `Machine info`
**Goal:** Extract Telemetry for Predictive Maintenance.

| Anchor Text | Value Location | JSON Target |
| :--- | :--- | :--- |
| "Length of 45° cuts" | Column + 1 (mt) | `machine_telemetry.blade_wear_45_m` |
| "Length of 90° cuts" | Column + 1 (mt) | `machine_telemetry.blade_wear_90_m` |
| "Total movements length" | Column + 1 (mt) | `machine_telemetry.gantry_travel_rapid_m` |

---

## 5. Validation & "Project ID" Handling

The function must perform a **Header Check** before processing:

1.  **Locate ID:** Search `Sheet: Project parameters` for `PROJECT REFERENCE` (primary) or `PROJECT NAME` (fallback).
2.  **Validation:**
    *   Extract the value.
    *   **Rule:** Value *must* not be empty.
    *   **Rule:** Value should ideally match regex `^TAG-\d+`.
    *   If validation fails, mark `meta_data.validation_status = "WARNING"` in the JSON output (don't crash, but flag it).
3.  **Map to Output:**
    *   `meta_data.project_ref_id` = Extracted Value (The Tag ID).
    *   `meta_data.project_name` = The operational name string.

---

## 6. Output Specification (The Contract)
The output **must** strictly adhere to the JSON Schema provided in the Attachment.

*   **Numeric Precision:** Round all dimensions to 2 decimal places. Round weights to 4 decimal places.
*   **Null Handling:** Do not output keys for null values; use `0` for numeric fields if empty.
*   **Dates:** ISO 8601 format (`YYYY-MM-DDTHH:mm:ssZ`).

---

## 7. Python Implementation Guidance (SOTA Practices)

### Libraries
*   **`pandas`**: For heavy lifting and table extraction.
*   **`openpyxl`**: For inspecting cell properties if needed (unlikely, pandas usually suffices).
*   **`pydantic`**: **Mandatory.** Use Pydantic models to define the output schema. This ensures the output JSON is valid and types are correct before the function returns.

### Error Handling Pattern
The function should return a `200 OK` even if parsing is partial, but the JSON body must contain a `status` block.

```python
try:
    # Parsing Logic
    data = extract_data(excel_file)
    return func.HttpResponse(
        body=json.dumps({"status": "SUCCESS", "data": data}),
        mimetype="application/json"
    )
except Exception as e:
    # Forensic Logging
    logging.error(f"Parsing failed: {str(e)}", exc_info=True)
    return func.HttpResponse(
        body=json.dumps({
            "status": "ERROR", 
            "error_message": str(e), 
            "file_name": input_filename
        }),
        status_code=400
    )
```

### Performance
*   Do not write the Excel file to disk. Process it in memory using `io.BytesIO`.
*   Expected execution time: < 2 seconds per file.

---

## 8. Testing Scenarios
The developer must verify the function against these scenarios:

1.  **The "Happy Path":** Standard file, all anchors found.
2.  **The "Shifted" File:** Insert 3 empty rows at the top of the Excel sheet. The logic *must* still find "Utilized sheets" and extract the correct value.
3.  **The "Missing Profile":** A file that has no "F PROFILE" block. The JSON should return `f_profile: null` or empty list, not crash.
4.  **The "Bad ID":** File where PROJECT REFERENCE is empty. Function should return a JSON with `validation_error: "Missing Tag ID"`.

---

**End of Specification**