<!--
  ONBOARDING EMAIL DRAFT
  Replace placeholders marked with {{...}} before sending.
  Can be sent as HTML or pasted into Outlook/Teams.
-->

**Subject:** Ducts Production System — You're All Set Up! Here's How to Get Started

---

Hi Team,

Following up on yesterday's walkthrough — your accounts are now live. This email has everything you need to get started: your links, your forms, and a simple step-by-step plan to bring all our existing work into the system.

---

### Your Teams Channels

You've been added to two channels in Microsoft Teams where the system will send you action cards:

| Channel | What You'll See There |
|---------|----------------------|
| **Pending DOs** | After PM approves margins, a card appears here with all the details needed to create a Delivery Order in SAP (customer, tag sheets, billed area, material breakdown). |
| **Submit Consumption** | When production is complete for a tag sheet, a card appears here showing allocated materials. You review, adjust if needed, and submit. |

**Action required:** Please open Teams and confirm you can see both channels. If not, let me know.

---

### Your Smartsheet Access

You now have **view access** to the following sheets. Bookmark these — they're your live dashboards:

| Sheet | What It Shows | Link |
|-------|---------------|------|
| **LPO Master** | All purchase orders, customer details, pricing | [Open](https://app.smartsheet.eu/sheets/qvJ8w6RXjFxvmXr6Mx74hMg8MxVCVcfgCm2rwCp1) |
| **Tag Sheet Registry** | All tag sheets, status, linked LPOs | [Open](https://app.smartsheet.eu/sheets/942hChHq4c2vm3QpwqGp9QCgcFG6V4XJfmgWqMF1) |
| **Production Planning** | Scheduled production, machine assignments, dates | [Open](https://app.smartsheet.eu/sheets/9798Jj3GM6HpCG7jRhhfq9CCw52865Hh9rGRcR81) |
| **Nesting Log** | Nesting execution results, material usage | [Open](https://app.smartsheet.eu/sheets/WRH43j9j3xfqwrQH7Pg9wFhGqVP6hjVjjqrG5261) |
| **Allocation Log** | Material allocations per tag sheet | [Open](https://app.smartsheet.eu/sheets/4xr3RHrqQ92gHrQPxHH8Xg86vCv4mmqH6wFWxM91) |
| **Material Master** | Material codes, SAP mappings, descriptions | [Open](https://app.smartsheet.eu/sheets/vpV8q89W4QCvfHwPFFq7vH7PRP3RWcjgP6QGR4R1) |
| **Consumption Log** | What was consumed per tag sheet | [Open](https://app.smartsheet.eu/sheets/GpgXH23W2VFWHm97WPM4VvpCPxHJqq8vj79389J1) |
| **Delivery Log** | Deliveries, SAP invoice numbers, POD status | [Open](https://app.smartsheet.eu/sheets/CrfWm78jfR4jrQ9qV7qGG4g2vCC25xVvGcf97QM1) |
| **Inventory Snapshot** | Current stock levels | [Open](https://app.smartsheet.eu/sheets/4McC49q6MgPVCr765jFp3HG3QV2HPqXFP97wq5h1) |
| **Exception Log** | System errors and validation issues | [Open](https://app.smartsheet.eu/sheets/JPvQ8Hxchq9Mx3vp6r79Gr8qMG3xp8VqF2RGP2P1) |

These are **view-only** — all data entry happens through the forms below.

---

### Your Forms (Data Entry)

All data entry is done through these forms. Bookmark them:

| Form | When to Use | Link |
|------|-------------|------|
| **LPO Submission** | When a new purchase order is received from the customer | [Open Form](https://app.smartsheet.eu/b/form/581b309e39e1495c873be0564a47df9e) |
| **Tag Sheet Submission** | When a new tag sheet is created for an LPO | [Open Form](https://app.smartsheet.eu/b/form/789323476a22446e8a9c2aa97a61ec6c) |
| **Production Planning** | When scheduling a tag sheet for production | [Open Form](https://app.smartsheet.eu/b/form/e4d97d2de7ce4ee9b7ec527e20339dde) |
| **Delivery Log** | When recording a delivery against a completed order | [Open Form](https://app.smartsheet.eu/b/form/019d1ec8cf0b7fac87ba6a3ff717d5fc) |

---

### Getting Started — 4 Phases

We'll bring everything into the system in order. **Please follow these phases sequentially** — each step depends on the previous one.

---

**Phase 1: Enter All Open LPOs** (This Week)

Start with all existing/open purchase orders that are currently active.

- Open the **LPO Submission Form** (link above)
- For each open LPO, fill in: SAP reference, customer name, project, brand, price per sqm, and total area
- Attach the LPO document (PDF) if available
- The system will create the LPO record and set up the SharePoint folder automatically

*Who:* Sales / Commercial team
*Goal:* Every active LPO should be in the system by end of this week.

---

**Phase 2: Enter All Active Tag Sheets** (After Phase 1)

Once the LPOs are in, submit the tag sheets that are currently in production or pending production.

- Open the **Tag Sheet Submission Form**
- For each tag sheet, fill in: LPO reference (SAP ref from Phase 1), tag sheet name, area, and attach the file
- The system will link it to the LPO, parse the nesting file, and allocate materials automatically

*Who:* Production team
*Goal:* Every active tag sheet should be in the system and linked to its LPO.

---

**Phase 3: PM Plans Production** (After Phase 2)

Once tag sheets are in, the PM will schedule them for production.

- Open the **Production Planning Form**
- Assign machine, planned start date, and priority for each tag sheet
- The system will track SLA and progress automatically

*Who:* PM (Raqibudeen)
*Goal:* All active tag sheets should have a production schedule.

---

**Phase 4: Normal Operations Begin**

From here on, the workflow runs naturally:

1. **New LPO comes in** --> Sales submits via form --> system creates record
2. **Tag sheet created** --> Production submits via form --> system parses nesting, allocates materials
3. **PM plans production** --> Schedules via form
4. **Production completes** --> Consumption card appears in Teams --> team reviews and submits
5. **PM approves margins** --> Approval card appears in Teams --> PM reviews and approves
6. **DO card appears** --> Supervisor creates DO in SAP using the details provided
7. **Delivery happens** --> Production submits delivery via form --> system tracks POD and invoicing

---

### Quick Reference: What Happens Automatically

You don't need to worry about these — the system handles them behind the scenes:

- Nesting file parsing and material allocation
- Inventory tracking (consumption, adjustments)
- Margin calculations and variance analysis
- Audit trail (every action is logged)
- Exception alerts (if something goes wrong, it shows up in the Exception Log)
- SharePoint file organization

---

### If Something Goes Wrong

- **Check the Exception Log** first — it usually tells you what happened: [Open](https://app.smartsheet.eu/sheets/JPvQ8Hxchq9Mx3vp6r79Gr8qMG3xp8VqF2RGP2P1)
- If the issue isn't clear, reach out to me with the **tag sheet ID** or **LPO reference** and I'll investigate

---

Please confirm once you've:
1. Verified access to both Teams channels
2. Opened at least one Smartsheet link to confirm access
3. Bookmarked the forms

Happy to jump on a quick call if anyone has questions.

Best regards,
{{YOUR_NAME}}
