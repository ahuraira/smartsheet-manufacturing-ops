# Documentation Enhancement Backlog (Nice-to-Have)

> **Priority:** LOW | **Status:** Optional | **Target:** v1.7.0+

These enhancements are **NOT REQUIRED** for current production use. Current documentation is already **world-class (4.75/5)**. Tackle these when you have spare cycles or want to level up to 5/5.

---

## Low-Effort, High-Value (Do These First)

### 1. Smartsheet Screenshots (Effort: 1 hour)
**Value:** ⭐⭐⭐⭐ | **Priority:** MEDIUM

Add screenshots to Data Dictionary showing actual sheet layouts.

**Files to update:**
- [ ] `docs/reference/data/sheets-core.md` - Add screenshot of Config sheet
- [ ] `docs/reference/data/sheets-production.md` - Add Tag Registry screenshot
- [ ] `docs/reference/data/sheets-mapping.md` - Add Material Master screenshot
- [ ] `docs/reference/data/sheets-governance.md` - Add Exception Log screenshot

**How to do it:**
1. Take screenshots of key sheets in Smartsheet
2. Save to `docs/assets/screenshots/` (create folder if needed)
3. Embed with: `![Tag Registry Example](../../assets/screenshots/tag_registry.png)`

---

### 2. "Your First API Integration" Tutorial (Effort: 2 hours)
**Value:** ⭐⭐⭐⭐ | **Priority:** MEDIUM

Create step-by-step tutorial for new developers.

**File to create:**
- [ ] `docs/tutorials/first_api_integration.md`

**Content outline:**
```markdown
# Your First API Integration

## Prerequisites
- Python 3.11
- Postman or curl
- Test credentials

## Step 1: Upload Your First Tag
1. Get API credentials
2. Prepare tag file (base64 encode)
3. Call POST /api/tags/ingest
4. Verify in Smartsheet

## Step 2: Schedule for Production
...

## Next Steps
- Read full API reference
- Explore other endpoints
```

**Update:**
- [ ] Add link to `docs/index.md` under "Getting Started"

---

### 3. Power Automate Flow Screenshots (Effort: 45 min)
**Value:** ⭐⭐⭐ | **Priority:** LOW

Add visual examples of flow designs.

**Files to update:**
- [ ] `docs/flows/generic_file_upload_flow.md` - Add flow diagram screenshot
- [ ] `docs/flows/nesting_complete_flow.md` - Add flow diagram screenshot

---

## Medium-Effort Enhancements

### 4. OpenAPI Specification (Effort: 3 hours)
**Value:** ⭐⭐⭐ | **Priority:** LOW

Generate interactive API playground.

**Steps:**
1. Create `docs/openapi.yaml` from existing API docs
2. Deploy Swagger UI to `/docs/api-playground/`
3. Link from API index

**Tools:**
- Use AI to convert markdown → OpenAPI spec
- Or manual: https://editor.swagger.io/

**Benefits:**
- Interactive "Try it out" for each endpoint
- Auto-generated client libraries

---

### 5. Postman Collection (Effort: 1 hour)
**Value:** ⭐⭐⭐ | **Priority:** LOW

Pre-built API collection for testing.

**Steps:**
1. Create Postman collection with all 7 endpoints
2. Add example requests for each
3. Export as JSON
4. Save to `docs/postman/ducts_api_collection.json`
5. Add "Run in Postman" button to API index

---

### 6. Video Walkthrough (Effort: 2 hours)
**Value:** ⭐⭐⭐⭐ | **Priority:** LOW

5-minute Loom/YouTube video showing end-to-end flow.

**Content:**
1. Upload tag via Smartsheet form
2. View in Tag Registry
3. Schedule for production
4. Upload nesting file
5. View BOM

**Embed in:**
- [ ] `docs/index.md` (top of page)
- [ ] `docs/quick_start.md`

---

## Advanced (Only if Building Public Product Docs)

### 7. Multi-language Code Examples (Effort: 4 hours)
**Value:** ⭐⭐ | **Priority:** VERY LOW

Add Java, Ruby, Go examples to current curl/Python/PowerShell.

**Current coverage:** 3 languages ✅  
**Big Tech standard:** 6-8 languages

**Status:** NOT NEEDED for internal/B2B docs

---

### 8. Interactive Playground with Live API (Effort: 8 hours)
**Value:** ⭐⭐ | **Priority:** VERY LOW

Full Swagger UI with live sandbox environment.

**Requires:**
- Dedicated sandbox Azure environment
- OAuth/API key management UI
- Rate limiting

**Status:** NOT NEEDED for current use case

---

## Quick Wins (Do Anytime)

- [ ] Add "Edit this page on GitHub" links to all docs
- [ ] Create `CONTRIBUTING.md` for docs (how to add new endpoint)
- [ ] Add "Last updated" auto-timestamp script
- [ ] Link to actual test coverage report from docs

---

## Summary

**Current State:** ✅ **WORLD-CLASS (4.75/5)**  
**With Screenshots + Tutorial:** ⭐⭐⭐⭐⭐ **5/5 (Perfect)**

**Recommendation:**
1. ✅ **Ship current state** - production-ready
2. ⏰ Do #1 (screenshots) + #2 (tutorial) in Q2 2026
3. 🎁 Rest are "nice-to-have" for v2.0

**You're done with docs! Focus on code now.** 🚀
