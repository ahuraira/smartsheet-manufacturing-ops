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
    generate_next_exception_id,
    generate_next_allocation_id,
    generate_next_consumption_id,
    generate_next_delivery_id,
    generate_next_nesting_id,
    generate_next_remnant_id,
    generate_next_filler_id,
    generate_next_txn_id,
)

# Helpers
from .helpers import (
    generate_trace_id,
    compute_file_hash,
    compute_file_hash_from_url,
    calculate_sla_due,
    format_datetime_for_smartsheet,
    parse_float_safe,
    parse_int_safe,
    safe_get,
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
    "calculate_sla_due",
    "format_datetime_for_smartsheet",
    "parse_float_safe",
    "parse_int_safe",
    "safe_get",
]
