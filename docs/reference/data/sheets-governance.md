# Governance Sheets

> **Document Type:** Reference | **Version:** 1.6.9 | **Last Updated:** 2026-02-06

Schemas for audit log and exception tracking sheets.

---

## User Action Log (98)

Audit trail of all user actions in the system.

| Logical Name | Physical Column | Type | Description |
|--------------|-----------------|------|-------------|
| `ACTION_ID` | Action ID | Text (Auto) | Auto-generated action ID |
| `ACTION_TYPE` | Action Type | Dropdown | See ActionType enum |
| `USER_EMAIL` | User Email | Contact List | User who performed action |
| `TIMESTAMP` | Timestamp | Date/Time | Action timestamp |
| `ENTITY_ID` | Entity ID | Text | Related entity (TAG-0001, LPO-0024, etc.) |
| `DETAILS` | Details | Text (Long) | JSON or text details of the action |
| `TRACE_ID` | Trace ID | Text | Correlation ID for request tracing |
| `IP_ADDRESS` | IP Address | Text | User IP address (if available) |

### ActionType Values

See [ActionType enum](./enums.md#actiontype) for complete list including:
- TAG_CREATED, TAG_UPDATED, TAG_RELEASED
- LPO_CREATED, LPO_UPDATED (v1.2.0)
- SCHEDULE_CREATED, SCHEDULE_UPDATED (v1.3.0)
- EXCEPTION_CREATED, EXCEPTION_RESOLVED

---

## Exception Log (99)

System and business rule exceptions requiring manual intervention.

| Logical Name | Physical Column | Type | Description |
|--------------|-----------------|------|-------------|
| `EXCEPTION_ID` | Exception ID | Text (Auto) | Auto-generated exception ID (EX-NNNN) |
| `SEVERITY` | Severity | Dropdown | LOW, MEDIUM, HIGH, CRITICAL |
| `SOURCE` | Source | Dropdown | Ingest, Parser, Allocation, Schedule, etc. |
| `REASON_CODE` | Reason Code | Dropdown | See ReasonCode enum |
| `ENTITY_ID` | Entity ID | Text | Related entity (TAG-0001, LPO-0024, etc.) |
| `ERROR_MESSAGE` | Error Message | Text (Long) | Human-readable error description |
| `ERROR_DETAILS` | Error Details | Text (Long) | Technical details/stack trace |
| `STATUS` | Status | Dropdown | OPEN, IN_PROGRESS, RESOLVED, CLOSED, IGNORED |
| `ASSIGNED_TO` | Assigned To | Contact List | User assigned to resolve |
| `RESOLVED_BY` | Resolved By | Contact List | User who resolved |
| `RESOLUTION_NOTE` | Resolution Note | Text (Long) | Resolution details |
| `TRACE_ID` | Trace ID | Text | Correlation ID for request tracing |
| `CREATED_AT` | Created At | Date/Time | Exception creation time |
| `RESOLVED_AT` | Resolved At | Date/Time | Resolution timestamp |
| ` SLA_DEADLINE` | SLA Deadline | Date/Time | Calculated from severity |

### SLA Thresholds

| Severity | SLA | Description |
|----------|-----|-------------|
| CRITICAL | 4 hours | System-blocking issues |
| HIGH | 24 hours | Major business impact |
| MEDIUM | 48 hours | Moderate impact |
| LOW | 72 hours | Minor issues |

### Common Reason Codes

See [ReasonCode enum](./enums.md#reasoncode) for complete list including:
- **File Processing**: DUPLICATE_UPLOAD, PARSE_FAILED
- **LPO Issues**: LPO_NOT_FOUND, LPO_ON_HOLD, INSUFFICIENT_PO_BALANCE
- **Scheduling**: MACHINE_MAINTENANCE, T1_NESTING_DELAY
- **Mapping**: Unmapped materials (see Mapping Exception sheet)

---

## Related Documentation

- [Enumerations](./enums.md) - ActionType, ReasonCode, ExceptionSeverity
- [Exception Handling](../api/index.md#error-handling) - API error patterns
- [Event Dispatcher](../api/event-dispatcher.md) - Exception creation flow (v1.4.2)
