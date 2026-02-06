"""
Shared Library for Azure Functions
===================================

This module provides shared utilities, models, and clients for all Azure Functions
in the Ducts Manufacturing Inventory Management System.

Modules
-------
sheet_config
    Sheet names, column names, and configuration constants
models
    Pydantic models for request/response validation
smartsheet_client
    Thread-safe Smartsheet API client with retry logic
id_generator
    Sequence-based ID generation (TAG-0001, EX-0001, etc.)
helpers
    Utility functions for hashing, SLA calculation, etc.

Quick Start
-----------
>>> from shared import (
...     get_smartsheet_client,
...     TagIngestRequest,
...     generate_trace_id,
...     SheetName,
...     ColumnName,
... )
>>> 
>>> client = get_smartsheet_client()
>>> trace_id = generate_trace_id()
>>> lpo = client.find_row_by_column(
...     SheetName.LPO_MASTER.value,
...     ColumnName.SAP_REFERENCE,
...     "SAP-001"
... )

ID Generation
-------------
>>> from shared import generate_next_tag_id, get_smartsheet_client
>>> client = get_smartsheet_client()
>>> tag_id = generate_next_tag_id(client)  # Returns "TAG-0001"

Models
------
>>> from shared import TagIngestRequest
>>> request = TagIngestRequest(
...     lpo_sap_reference="SAP-001",
...     required_area_m2=50.0,
...     requested_delivery_date="2026-02-01",
...     uploaded_by="user@company.com"
... )

See Also
--------
- docs/reference/data_dictionary.md : Data model documentation
- docs/reference/api_reference.md : API documentation
- docs/architecture_overview.md : Architecture overview
"""

# Sheet configuration
from .sheet_config import (
    SheetName,
    ColumnName,
    ConfigKey,
    ID_PREFIXES,
    DEFAULT_CONFIG,
    FOLDER_STRUCTURE,
    SHEET_FOLDER_MAP,
)

# Data models
from .models import (
    TagStatus,
    LPOStatus,
    ExceptionSeverity,
    ExceptionSource,
    ReasonCode,
    ActionType,
    TagIngestRequest,
    TagIngestResponse,
    ExceptionRecord,
    UserActionRecord,
    LPORecord,
    TagRecord,
    # LPO models (v1.1.0+)
    Brand,
    TermsOfPayment,
    FileType,
    FileAttachment,
    LPOIngestRequest,
    LPOUpdateRequest,
    LPOIngestResponse,
    LPOUpdateResponse,
    # Scheduling models (v1.3.0+)
    Shift,
    ScheduleStatus,
    MachineStatus,
    ScheduleTagRequest,
    ScheduleTagRequest,
    ScheduleTagResponse,
    # Generic File Upload (v1.6.9)
    FileUploadItem,
)

# Smartsheet client and exceptions
from .smartsheet_client import (
    SmartsheetClient,
    get_smartsheet_client,
    reset_smartsheet_client,
    SmartsheetError,
    SmartsheetRateLimitError,
    SmartsheetSaveCollisionError,
    SmartsheetNotFoundError,
)

# Manifest (ID-first architecture)
from .manifest import (
    WorkspaceManifest,
    get_manifest,
    reset_manifest,
    ManifestError,
    ManifestNotFoundError,
    SheetNotInManifestError,
    ColumnNotInManifestError,
)

# Logical names (code-facing constants)
from .logical_names import (
    Sheet,
    Folder,
    Column,
    SHEET_COLUMNS,
)

# ID generation
from .id_generator import (
    SequenceGenerator,
    SequenceCollisionError,
    generate_next_tag_id,
    generate_next_lpo_id,  # v1.6.8
    generate_next_exception_id,
    generate_next_allocation_id,
    generate_next_consumption_id,
    generate_next_delivery_id,
    generate_next_nesting_id,
    generate_next_remnant_id,
    generate_next_filler_id,
    generate_next_txn_id,
    generate_next_action_id,
    generate_next_schedule_id,
)

# Helpers
from .helpers import (
    generate_trace_id,
    compute_file_hash,
    compute_file_hash_from_url,
    compute_file_hash_from_base64,
    compute_combined_file_hash,
    calculate_sla_due,
    format_datetime_for_smartsheet,
    parse_float_safe,
    parse_int_safe,
    safe_get,
    # LPO folder helpers (v1.1.0+)
    sanitize_folder_name,
    generate_lpo_folder_path,
    generate_lpo_folder_url,
    generate_lpo_subfolder_paths,
    # Multi-file attachments (v1.6.3+)
    extract_row_attachments_as_files,
    # Column name resolution (v1.6.5+ DRY)
    get_physical_column_name,
    # Percentage normalization (v1.6.7+)
    normalize_percentage,
    # User email resolution (v1.6.8+)
    resolve_user_email,
)

# Audit utilities (DRY - shared across functions)
from .audit import (
    create_exception,
    log_user_action,
)

# LPO Service (v1.6.6 - DRY compliance)
from .lpo_service import (
    # Lookup functions
    find_lpo_by_sap_reference,
    find_lpo_by_customer_ref,
    find_lpo_flexible,
    # Data extraction
    get_lpo_quantities,
    get_lpo_status,
    get_lpo_sap_reference,
    # Validation
    validate_lpo_status,
    validate_po_balance,
    # Data classes
    LPOQuantities,
    LPOValidationResult,
    LPOValidationStatus,
)

# Power Automate client (v1.3.1+)
from .power_automate import (
    FlowClient,
    FlowClientConfig,
    FlowTriggerResult,
    FlowType,
    get_flow_client,
    get_flow_client,
    trigger_create_lpo_folders,
    trigger_nesting_complete_flow,  # v1.6.7
    trigger_upload_files_flow,      # v1.6.9
)

# Atomic update helpers (v1.6.9 - SOTA fix for race conditions)
from .atomic_update import (
    atomic_increment,
    atomic_set_if_equals,
    AtomicUpdateResult,
)

# Event processing utilities (v1.4.0+)
from .event_utils import (
    get_cell_value_by_column_id,
    get_cell_value_by_logical_name,
)

__all__ = [
    # Sheet config (legacy - use logical_names for new code)
    "SheetName",
    "ColumnName",
    "ConfigKey",
    "ID_PREFIXES",
    "DEFAULT_CONFIG",
    "FOLDER_STRUCTURE",
    "SHEET_FOLDER_MAP",
    # Models
    "TagStatus",
    "LPOStatus",
    "ExceptionSeverity",
    "ExceptionSource",
    "ReasonCode",
    "ActionType",
    "TagIngestRequest",
    "TagIngestResponse",
    "ExceptionRecord",
    "UserActionRecord",
    "LPORecord",
    "TagRecord",
    # LPO models (v1.1.0+)
    "Brand",
    "TermsOfPayment",
    "LPOIngestRequest",
    "LPOUpdateRequest",
    "LPOIngestResponse",
    "LPOUpdateResponse",
    "LPOUpdateResponse",
    # Generic File Upload (v1.6.9)
    "FileUploadItem",
    # Client and exceptions
    "SmartsheetClient",
    "get_smartsheet_client",
    "reset_smartsheet_client",
    "SmartsheetError",
    "SmartsheetRateLimitError",
    "SmartsheetSaveCollisionError",
    "SmartsheetNotFoundError",
    # Manifest (ID-first)
    "WorkspaceManifest",
    "get_manifest",
    "reset_manifest",
    "ManifestError",
    "ManifestNotFoundError",
    "SheetNotInManifestError",
    "ColumnNotInManifestError",
    # Logical names
    "Sheet",
    "Folder",
    "Column",
    "SHEET_COLUMNS",
    # ID generation
    "SequenceGenerator",
    "SequenceCollisionError",
    "generate_next_tag_id",
    "generate_next_exception_id",
    "generate_next_allocation_id",
    "generate_next_consumption_id",
    "generate_next_delivery_id",
    "generate_next_nesting_id",
    "generate_next_remnant_id",
    "generate_next_filler_id",
    "generate_next_txn_id",
    # Helpers
    "generate_trace_id",
    "compute_file_hash",
    "compute_file_hash_from_url",
    "compute_file_hash_from_base64",
    "calculate_sla_due",
    "format_datetime_for_smartsheet",
    "parse_float_safe",
    "parse_int_safe",
    "safe_get",
    # LPO folder helpers (v1.1.0+)
    "sanitize_folder_name",
    "generate_lpo_folder_path",
    "generate_lpo_subfolder_paths",
    # Column name resolution (v1.6.5+ DRY)
    "get_physical_column_name",
    # Audit utilities (DRY - v1.2.0+)
    "create_exception",
    "log_user_action",
    # Power Automate (v1.3.1+)
    "FlowClient",
    "FlowClientConfig",
    "FlowTriggerResult",
    "FlowType",
    "get_flow_client",
    "trigger_create_lpo_folders",
    "trigger_nesting_complete_flow", # v1.6.7
    "trigger_upload_files_flow",     # v1.6.9
    # Event utils (v1.4.0+)
    "get_cell_value_by_column_id",
    "get_cell_value_by_logical_name",
    # LPO Service (v1.6.6+ DRY)
    "find_lpo_by_sap_reference",
    "find_lpo_by_customer_ref",
    "find_lpo_flexible",
    "get_lpo_quantities",
    "get_lpo_status",
    "get_lpo_sap_reference",
    "validate_lpo_status",
    "validate_po_balance",
    "LPOQuantities",
    "LPOValidationResult",
    "LPOValidationStatus",
]
