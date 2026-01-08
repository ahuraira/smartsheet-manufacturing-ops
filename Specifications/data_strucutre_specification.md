# 📊 Data Structure & Data Governance Specification

> **Document Type:** Specification | **Version:** 1.0.0 | **Last Updated:** 2026-01-08

---

## 📋 Quick Links

| Related Documents |
|-------------------|
| [Architecture Specification](./architecture_specification.md) - Overall system architecture |
| [Data Dictionary](../docs/reference/data_dictionary.md) - Complete data reference |
| [Configuration Reference](../docs/reference/configuration.md) - Config options |

---

## 1. Purpose of This Document

This document defines the **authoritative data structures**, their **relationships**, **lifecycle roles**, and **governance principles** for the Manufacturing Planning, Inventory, and Order Fulfilment system.

It is designed to:
- Serve as a **System of Record (SoR) specification** for the prototype and production
- Demonstrate **state-of-the-art (SOTA)** robustness, auditability, and scalability
- Ensure zero redesign when migrating from Smartsheet → Dataverse / Azure SQL
- Enable SAP reconciliation, inventory accuracy, and exception-driven control

This document intentionally treats **data as the product**.

## 2\. Core Design Principles

### 2.1 Ledger-first Architecture

- Every material movement, reservation, or correction is recorded as an **immutable transaction**
- No quantity is ever silently overwritten

### 2.2 Separation of Intent vs Reality

- Planned ≠ Allocated ≠ Consumed
- System vs SAP vs Physical stocks are explicitly stored

### 2.3 Snapshot + Transaction Hybrid Model

- **Transaction logs** explain _how_ quantities changed
- **Snapshots** explain _what the world looked like_ at a point in time

### 2.4 Exception-driven Control

- The system assumes mismatches will happen
- Exceptions are first-class citizens with lifecycle, ownership, and resolution

## 3\. Master & Control Tables

### 3.1 LPO Log (Commercial Anchor)

**Purpose:** Commercial and contractual control point. All production must trace back to an LPO.

**Key Columns & Meaning** - LPO ID - Internal unique identifier - Customer LPO Ref - Customer reference - SAP Reference - SAP Sales Order / Project reference - Customer / Project / Brand - Commercial dimensions - PO Quantity / PO Value - Contractual commitment - Wastage Considered in Costing - Commercial allowance - Total Allocated Cost - Sum of allocations linked to this LPO - Delivered Quantity / Value - What has physically left - Estimated In-Production (3 days) - Short-term WIP signal - PO Balance Quantity / Value - Contractual remaining - Current Status - Draft/ Pending Approval/ Active/ On Hold/ Closed

**Why it exists** - Prevents production without commercial coverage - Enables value leakage detection - Drives finance reconciliation

### 3.2 Configuration Table (Implicit but Mandatory)

**Purpose:** Externalize business rules without code change

Examples: - Minimum remnant area - Allocation expiry window - Variance tolerance thresholds - Shift definitions - Truck capacities

**Governance:** Versioned, effective-dated, approval-controlled

## 4\. Planning & Execution Tables

### 4.1 Tag Sheet Registry (Production Intent)

**Purpose:** Represents a _request to produce_

**Key Columns** - Tag ID - Unique production unit - Tag Sheet Name / Revision - LPO SAP Reference Link - Commercial traceability - Required Delivery Date - Allowable Wastage - Production Gate - Planned Cut Date - Allocation Batch ID - Status - Draft/ Validated/ Sent to Nesting/ Planned Queued/ WIP/ Complete/ Partial Dispatch/ Dispatched/ Closed/ Revision Pending/ Hold/ Cancelled.

**Why it exists** - Separates customer demand from execution - Enables pre-planning and sequencing

### 4.2 Production Planning

**Purpose:** Shift-level execution plan

**Key Columns** - Schedule ID - Tag Sheet ID - Planned Date - Shift - Morning / Evening - Machine Assigned - Allocation Status - Draft/ Approved/ Issued/ Complete

**Why it exists** - Enables shift KPIs - Supports advance nesting (T-1 rule)

### 4.3 Nesting Execution Log

**Purpose:** Engineering truth from nesting software

**Key Columns** - Nest Session ID - Versioned - Tag Sheet ID - Sheets Consumed Virtual - Wastage Percentage - Remnant IDs Generated - Filler IDs Generated Planned Date

**Why it exists** - Locks expected consumption before production - Prevents guessing material usage

## 5\. Inventory Control Tables

### 5.1 Allocation Log (Material Reservation)

**Purpose:** Hard reservation of stock for a future shift

**Key Columns** - Allocation ID - Tag Sheet ID - Material Code - Quantity - Planned Date / Shift - Status - Submitted / Approved/ Released / EXPIRED

**Why it exists** - Prevents double booking - Separates intent from usage

### 5.2 Consumption Log (Ground Truth)

**Purpose:** Actual material usage

**Key Columns** - Consumption ID - Tag Sheet ID - Material Code - Quantity - Shift - Remnant ID (optional)

**Why it exists** - Only source of truth for real consumption - Drives inventory reduction

### 5.3 Inventory Transaction Log (System Ledger)

**Purpose:** Immutable movement ledger

**Txn Types** - ALLOCATE - PICK - ISSUE - REMNANT_CREATE - REMNANT_USE - ADJUSTMENT

**Why it exists** - Auditability - Root cause analysis

## 6\. Delivery & Commercial Fulfilment

### 6.1 Delivery Log

**Purpose:** Physical dispatch control

**Key Columns** - Delivery ID - SAP DO Number (nullable) - Tag Sheet ID - SAP Invoice Number - Quantity / Value - DO Lines (JSON) - Vehicle ID - Status - DRAFT / DISPATCHED / POD RECEIVED / INVOICED

**Why it exists** - Supports partial deliveries - Decouples logistics from invoicing

## 7\. Inventory State & Reconciliation

### 7.1 Inventory Snapshot Log (System View)

**Purpose:** Daily authoritative inventory state

**Key Columns** - Snapshot Timestamp - Material Code - SAP Quantity - Allocated Quantity - Planned Quantity - Actual Consumption - System Closing Quantity - Physical Closing Quantity - Variance Quantity - Snapshot Type - Plant

**Why it exists** - Single place to see all perspectives - Enables tight variance control

### 7.2 SAP Inventory Snapshot Log

**Purpose:** Raw SAP truth

**Key Columns** - Unrestricted Quantity - In Transit Quantity - WIP Quantity

**Why it exists** - SAP reconciliation - Regulatory and finance alignment

## 8\. Remnants & Fillers

### 8.1 Remnant Log

**Purpose:** Track reusable leftovers

**Key Columns** - Remnant ID - Dimensions / Area Created Date - Consumption Date - Status - AVAILABLE / Reserved/ Consumed

### 8.2 Filler Log

**Purpose:** Non-standard consumption

**Key Columns** - Filler ID - Type - SCRAP / SMALL_FILL - SAP PO Reference

## 9\. Exception & Governance (Implicit but Critical)

### 9.1 Exception Log (Mandatory Table)

**Purpose:** Controlled handling of mismatches

**Key Columns** - Exception ID - Source / Related Tag ID / Related Txn ID/ Material Code/ Quantity/ Reason Code/ Severity/ Assigned To/ SLA Due/ Approvals/ Attachment Links/ Resolution Action/ Created Date - Status - Open/ Acknowledged/ In Progress/ Resolved/ Rejected

**Exception Types** - Allocation Shortage - Over Consumption - SAP vs System Mismatch - Legacy PS DO Block

**Lifecycle** OPEN → INVESTIGATING → APPROVED OVERRIDE → RESOLVED

## 10\. Audit & Compliance

### 10.1 User Action History

- Who did what
- When
- From where
- With before/after values

### 10.1 User Action Log (Mandatory Table)

**Purpose:** Immutable accountability log

**Key Columns** - Action ID - User ID / Action Type / Target Table / Target ID/ Old Value / New Value / Notes

### 10.2 Override Log (Mandatory Table)

**Purpose:** Immutable accountability log

**Key Columns** - Override ID - Requested By / Reason / Related Entity / Entity ID/ Requested Action / Approvals (JSON) / Decision/ Decision Timestamp

## 11\. What Makes This SOTA

- Full separation of planning, allocation, consumption, and delivery
- Ledger-based inventory with snapshots
- SAP treated as peer system, not magic truth
- Exception-driven operations
- Zero silent adjustments
- Fully migration-ready to Azure SQL / Dataverse

## 12\. Prototype = Production-Grade

Nothing in this document is a placeholder.

The prototype: - Uses the same data contracts - Uses the same lifecycle - Uses the same audit model

Only **scale and connectors** change in production.

**End of Document**