# Screenshots Guide

This directory contains visual assets for documentation.

## Required Screenshots

To complete the documentation, add screenshots for:

### Data Dictionary Sheets
1. **Config Sheet (00a)** - `config_sheet.png`
   - Show: Key, Value, Description columns
   - Highlight: SEQ_TAG, SEQ_LPO rows

2. **Tag Registry (02)** - `tag_registry.png`
   - Show: Tag ID, LPO SAP Reference, Status, Required Area columns
   - Highlight: A completed tag with Status = "Validate"

3. **Material Master (05a)** - `material_master.png`
   - Show: Nesting Description, Canonical Code, SAP Code columns
   - Highlight: Example material mapping (e.g., "PIR 25mm" → "PIR-025")

4. **Exception Log (99)** - `exception_log.png`
   - Show: Exception ID, Severity, Source, Error Message columns
   - Highlight: Example exception with Status = "OPEN"

### Power Automate Flows
5. **Generic File Upload Flow** - `file_upload_flow.png`
   - Show: Full flow diagram from trigger to completion
   - Highlight: Parse JSON and Create File actions

6. **Nesting Complete Flow** - `nesting_complete_flow.png`
   - Show: Email notification flow
   - Highlight: Send Email and Copy File actions

## How to Add Screenshots

1. Take screenshot in Smartsheet/Power Automate
2. Crop to show relevant columns/actions only
3. Resize to max width: 1200px
4. Save with descriptive filename (see above)
5. Place in this directory: `docs/assets/screenshots/`
6. Embed in doc: `![Description](../../assets/screenshots/filename.png)`

## Embedding Example

```markdown
### Tag Registry Sheet

![Tag Registry Example](../../assets/screenshots/tag_registry.png)

The Tag Registry stores all uploaded tags with their validation status...
```

## Image Guidelines

- **Format:** PNG (screenshots), SVG (diagrams)
- **Max size:** 500KB per image
- **Resolution:** 2x (retina-ready)
- **Annotations:** Use red boxes/arrows sparingly to highlight key areas
- **Accessibility:** Always include descriptive alt text in `![]()` syntax
