# System Architecture

> **Document Type:** Explanation | **Version:** 1.6.9 | **Last Updated:** 2026-02-06

High-level overview of the Ducts Manufacturing Inventory Management System architecture, components, and data flow.

---

## Component Architecture

```mermaid
graph TB
    subgraph "External Systems"
        SS[📊 Smartsheet<br/>Master Data Storage]
        SP[📁 SharePoint<br/>Document Storage]
        SAP[💼 SAP<br/>ERP System]
    end
    
    subgraph "Azure Cloud"
        subgraph "Ingestion Layer"
            WH[🔔 Webhook Receiver]
            ED[⚙️ Event Dispatcher]
        end
        
        subgraph "Business Logic (Azure Functions)"
            TAG[Tag Ingestion]
            LPO[LPO Ingestion]
            NEST[Nesting Parser]
            MAP[Material Mapping]
            SCHED[Scheduling]
        end
        
        subgraph "Shared Services"
            LPOM[LPO Service]
            UNIT[Unit Service]
            ATOM[Atomic Update]
            BLOB[Blob Storage]
        end
        
        subgraph "Integration Layer"
            PA[Power Automate Flows]
        end
    end
    
    %% External connections
    SS -->|webhooks| WH
    WH --> ED
    ED -->|tag events| TAG
    ED -->|lpo events| LPO
    ED -->|schedule events| SCHED
    
    %% Business logic
    TAG -->|read/write sheets| SS
    LPO -->|read/write sheets| SS
    NEST -->|parse & map| SS
    SCHED -->|plan production| SS
    
    %% Service dependencies
    TAG -.->|validate LPO| LPOM
    SCHED -.->|check balance| LPOM
    NEST -.->|lookup materials| MAP
    MAP -.->|convert units| UNIT
    LPO -.->|increment ID| ATOM
    
    %% File handling
    TAG -->|upload files| PA
    LPO -->|upload files| PA
    NEST -->|store json| BLOB
    PA -->|save documents| SP
    
    %% Data sync
    SS -.->|inventory sync| SAP
    
    style SS fill:#E8F5E9
    style SP fill:#E3F2FD
    style SAP fill:#FFF3E0
    style ED fill:#FF9800,color:#fff
    style TAG fill:#2196F3,color:#fff
    style LPO fill:#2196F3,color:#fff
    style NEST fill:#2196F3,color:#fff
    style MAP fill:#2196F3,color:#fff
    style SCHED fill:#2196F3,color:#fff
```

---

## Data Flow: Tag to Production

```mermaid
sequenceDiagram
    actor User
    participant SS as Smartsheet
    participant Azure as Azure Functions
    participant SP as SharePoint
    
    User->>SS: Upload tag via form
    SS->>Azure: Webhook (tag created)
    Azure->>SS: Validate & link LPO
    Azure->>SP: Upload tag file
    Azure-->>User: Tag ID confirmation
    
    Note over User,SP: Production Planning
    
    User->>SS: Schedule tag
    SS->>Azure: Webhook (schedule created)
    Azure->>SS: Validate capacity & PO balance
    Azure-->>User: Schedule confirmed
    
    Note over User,SP: Nesting
    
    User->>Azure: Upload nesting file
    Azure->>Azure: Parse & extract BOM
    Azure->>Azure: Map materials to SAP codes
    Azure->>SS: Update Tag Registry
    Azure->>SS: Create BOM records
    Azure->>SP: Store nesting outputs
    Azure-->>User: Nesting complete
```

---

## Sheet Inventory

### Master Data (00-01)
- `00 Reference Data` - Static lookup tables
- `00a Config` - System configuration & ID sequences
- `01 LPO Master LOG` - Purchase order records
- `01 LPO Audit LOG` - LPO change history

###Production Flow (02-04)
- `02 Tag Sheet Registry` - Tag records
- `02h Tag Ingestion Staging` - Tag upload queue
- `03 Production Planning` - Shift-level schedules
- `03h Production Planning Staging` - Schedule requests
- `04 Nesting Execution Log` - Nesting sessions

### Material Mapping (05a-06a)
- `05a Material Master` - Canonical material definitions
- `05b Mapping Override` - Customer/project overrides
- `05c LPO Material Brand Map` - LPO-specific mappings
- `05d Mapping History` - Mapping audit trail
- `05e Mapping Exception` - Unresolved materials
- `06a Parsed BOM` - Bill of materials from nesting

### Governance (97-99)
- `97 Override Log` - Approval records
- `98 User Action Log` - Audit trail
- `99 Exception Log` - Exception tracking

---

## Key Design Patterns

### 1. Event-Driven Architecture
- **Pattern**: Smartsheet webhooks → Event Dispatcher → Handler functions
- **Benefit**: Decoupled, scalable processing
- **Implementation**: ID-based routing (`event_routing.json`)

### 2. Ledger-First Data Model
- **Pattern**: Immutable transactions + derived snapshots
- **Benefit**: Full audit trail, time-travel queries
- **Example**: `ALLOCATED_QUANTITY` is sum of allocation log entries

### 3. Idempotency
- **Pattern**: `client_request_id` for all mutations
- **Benefit**: Safe webhook retries, no duplicate records
- **Implementation**: Dedup check before processing

### 4. ID-First Architecture
- **Pattern**: Logical names (code) → Physical IDs (manifest)
- **Benefit**: Rename sheets without code changes
- **Example**: `Sheet.TAG_REGISTRY` → manifest → actual sheet ID

### 5. Centralized Services
- **Pattern**: Shared business logic in `functions/shared/`
- **Benefit**: DRY compliance, consistent behavior
- **Services**: `lpo_service`, `unit_service`, `atomic_update`

---

## Technology Stack

| Layer | Technology | Purpose |
|-------|------------|---------|
| **Compute** | Azure Functions (Python 3.11) | Serverless business logic |
| **Storage** | Smartsheet | Master data & workflow |
| **Documents** | SharePoint | File storage & folders |
| **Orchestration** | Power Automate | Email, file copying |
| **Blob Storage** | Azure Blob Storage | Nesting outputs (v1.6.7) |
| **Monitoring** | Application Insights | Logging & tracing |

---

## Related Documentation

- [API Reference](./reference/api/index.md) - Function endpoints
- [Data Dictionary](./reference/data/index.md) - Sheet schemas
- [Architecture Specification](../Specifications/architecture_specification.md) - Detailed design
