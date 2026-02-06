"""
Centralized Sheet Configuration
==============================

This module defines all sheet names, column mappings, and schema definitions.
It serves as the **single source of truth** for Smartsheet structure.

Purpose
-------
- Eliminate hardcoded strings throughout codebase
- Ensure consistency between workspace creation and runtime
- Enable easy refactoring if sheet/column names change
- Provide migration-ready canonical names

Usage
-----
Always use these constants instead of hardcoding:

    >>> from shared import SheetName, ColumnName
    >>> 
    >>> # Good - use constants
    >>> client.find_row_by_column(
    ...     SheetName.TAG_REGISTRY.value,
    ...     ColumnName.FILE_HASH,
    ...     hash_value
    ... )
    >>> 
    >>> # Bad - hardcoded strings (don't do this!)
    >>> client.find_row_by_column("Tag Sheet Registry", "File Hash", hash_value)

Configuration Keys
------------------
Use ConfigKey for config table lookups:

    >>> from shared import ConfigKey
    >>> 
    >>> min_remnant = client.get_config_value(ConfigKey.MIN_REMNANT_AREA_M2.value)
    >>> seq_value = client.get_config_value(ConfigKey.SEQ_TAG.value)

Module Contents
---------------
SheetName : Enum
    All sheet names in the workspace
ColumnName : class
    Standard column names used across sheets
ConfigKey : Enum
    Configuration keys stored in Config sheet
ID_PREFIXES : dict
    Mapping of sequence keys to ID prefixes
DEFAULT_CONFIG : dict
    Default configuration values for initialization
FOLDER_STRUCTURE : list
    Folder names for workspace organization
SHEET_FOLDER_MAP : dict
    Mapping of sheets to their containing folders

See Also
--------
- docs/reference/data_dictionary.md : Complete schema documentation
- config_values.md : Initial configuration values
"""

from typing import Dict, List, Any
from enum import Enum


class SheetName(str, Enum):
    """
    Canonical sheet names.
    Use these constants instead of hardcoding strings.
    """
    # Root level
    REFERENCE_DATA = "00 Reference Data"
    CONFIG = "00a Config"
    
    # 01. Commercial and Demand
    LPO_MASTER = "01 LPO Master LOG"
    LPO_AUDIT = "01 LPO Audit LOG"
    
    # 02. Tag Sheet Registry
    TAG_REGISTRY = "02 Tag Sheet Registry"
    
    # 03. Production Planning
    PRODUCTION_PLANNING = "03 Production Planning"
    NESTING_LOG = "04 Nesting Execution Log"
    ALLOCATION_LOG = "05 Allocation Log"
    
    # 04. Production and Delivery
    CONSUMPTION_LOG = "06 Consumption Log"
    REMNANT_LOG = "06a Remnant Log"
    FILLER_LOG = "06b Filler Log"
    DELIVERY_LOG = "07 Delivery Log"
    INVOICE_LOG = "08 Invoice Log"
    INVENTORY_TXN_LOG = "90 Inventory Txn Log"
    INVENTORY_SNAPSHOT = "91 Inventory Snapshot"
    SAP_INVENTORY_SNAPSHOT = "92 SAP Inventory Snapshot"
    PHYSICAL_INVENTORY_SNAPSHOT = "93 Physical Inventory Snapshot"
    OVERRIDE_LOG = "97 Override Log"
    USER_ACTION_LOG = "98 User Action Log"
    EXCEPTION_LOG = "99 Exception Log"


class ColumnName:
    """
    Standard column names used across sheets.
    Helps maintain consistency and makes refactoring easier.
    """
    # Common columns
    STATUS = "Status"
    CREATED_AT = "Created At"
    REMARKS = "Remarks"
    
    # Tag Sheet columns
    TAG_ID = "Tag ID"
    TAG_NAME = "Tag Sheet Name/ Rev"
    FILE_HASH = "File Hash"
    CLIENT_REQUEST_ID = "Client Request ID"
    SUBMITTED_BY = "Submitted By"
    ESTIMATED_QUANTITY = "Estimated Quantity"
    REQUIRED_DELIVERY_DATE = "Required Delivery Date"
    LPO_SAP_REFERENCE = "LPO SAP Reference Link"
    BRAND = "Brand"
    CUSTOMER_NAME = "Customer Name"
    
    # LPO columns
    LPO_ID = "LPO ID"
    CUSTOMER_LPO_REF = "Customer LPO Ref"
    SAP_REFERENCE = "SAP Reference"
    LPO_STATUS = "LPO Status"
    PO_QUANTITY_SQM = "PO Quantity (Sqm)"
    DELIVERED_QUANTITY_SQM = "Delivered Quantity (Sqm)"
    
    # Config columns
    CONFIG_KEY = "config_key"
    CONFIG_VALUE = "config_value"
    EFFECTIVE_FROM = "effective_from"
    CHANGED_BY = "changed_by"
    
    # Exception columns
    EXCEPTION_ID = "Exception ID"
    SOURCE = "Source"
    RELATED_TAG_ID = "Related Tag ID"
    RELATED_TXN_ID = "Related Txn ID"
    MATERIAL_CODE = "Material Code"
    QUANTITY = "Quantity"
    REASON_CODE = "Reason Code"
    SEVERITY = "Severity"
    SLA_DUE = "SLA Due"
    RESOLUTION_ACTION = "Resolution Action"
    
    # User Action columns
    ACTION_ID = "Action ID"
    TIMESTAMP = "Timestamp"
    USER_ID = "User ID"
    ACTION_TYPE = "Action Type"
    TARGET_TABLE = "Target Table"
    TARGET_ID = "Target ID"
    OLD_VALUE = "Old Value"
    NEW_VALUE = "New Value"
    NOTES = "Notes"


class ConfigKey(str, Enum):
    """
    Configuration keys stored in Config sheet.
    """
    # Sequence counters for ID generation
    SEQ_TAG = "seq_tag"
    SEQ_LPO = "seq_lpo"  # v1.6.8: LPO ID sequence
    SEQ_EXCEPTION = "seq_exception"
    SEQ_ALLOCATION = "seq_allocation"
    SEQ_CONSUMPTION = "seq_consumption"
    SEQ_DELIVERY = "seq_delivery"
    SEQ_NESTING = "seq_nesting"
    SEQ_REMNANT = "seq_remnant"
    SEQ_FILLER = "seq_filler"
    SEQ_TXN = "seq_txn"
    SEQ_ACTION = "seq_action"  # User action log
    SEQ_SCHEDULE = "seq_schedule"  # Production schedule
    
    # Business configuration
    MIN_REMNANT_AREA_M2 = "min_remnant_area_m2"
    T1_CUTOFF_TIME_LOCAL = "t1_cutoff_time_local"
    T1_CUTOFF_TIMEZONE = "t1_cutoff_timezone"
    ALLOCATION_EXPIRY_MINUTES = "allocation_expiry_minutes"
    VARIANCE_TOLERANCE_PCT = "variance_tolerance_pct"
    CONSUMPTION_TOLERANCE_PCT = "consumption_tolerance_pct"
    REMNANT_VALUE_FRACTION = "remnant_value_fraction"
    PARSER_VERSION_CURRENT = "parser_version_current"
    
    # Machine configs
    VACUUM_BED_LENGTH_MM = "vacuum_bed_length_mm"
    VACUUM_BED_WIDTH_MM = "vacuum_bed_width_mm"
    
    # Truck capacity
    TRUCK_CAPACITY_10TON_M2 = "truck_capacity_10ton_m2"
    TRUCK_CAPACITY_3TON_M2 = "truck_capacity_3ton_m2"
    
    # Shift times
    SHIFT_MORNING_START = "shift_morning_start"
    SHIFT_MORNING_END = "shift_morning_end"
    SHIFT_EVENING_START = "shift_evening_start"
    SHIFT_EVENING_END = "shift_evening_end"
    
    # SLA settings
    SLA_EXCEPTION_CRITICAL_HOURS = "sla_exception_critical_hours"
    SLA_EXCEPTION_HIGH_HOURS = "sla_exception_high_hours"


# ID Prefixes for each entity type
ID_PREFIXES = {
    ConfigKey.SEQ_TAG: "TAG",
    ConfigKey.SEQ_LPO: "LPO",  # v1.6.8
    ConfigKey.SEQ_EXCEPTION: "EX",
    ConfigKey.SEQ_ALLOCATION: "ALLOC",
    ConfigKey.SEQ_CONSUMPTION: "CON",
    ConfigKey.SEQ_DELIVERY: "DO",
    ConfigKey.SEQ_NESTING: "NEST",
    ConfigKey.SEQ_REMNANT: "REM",
    ConfigKey.SEQ_FILLER: "FILL",
    ConfigKey.SEQ_TXN: "TXN",
    ConfigKey.SEQ_ACTION: "ACT",
    ConfigKey.SEQ_SCHEDULE: "SCHED",
}


# Default configuration values (used for initial setup)
DEFAULT_CONFIG = {
    # Sequence counters (start at 0, first ID will be 1)
    ConfigKey.SEQ_TAG: "0",
    ConfigKey.SEQ_LPO: "0",  # v1.6.8
    ConfigKey.SEQ_EXCEPTION: "0",
    ConfigKey.SEQ_ALLOCATION: "0",
    ConfigKey.SEQ_CONSUMPTION: "0",
    ConfigKey.SEQ_DELIVERY: "0",
    ConfigKey.SEQ_NESTING: "0",
    ConfigKey.SEQ_REMNANT: "0",
    ConfigKey.SEQ_FILLER: "0",
    ConfigKey.SEQ_TXN: "0",
    ConfigKey.SEQ_ACTION: "0",
    
    # Business rules
    ConfigKey.MIN_REMNANT_AREA_M2: "0.5",
    ConfigKey.T1_CUTOFF_TIME_LOCAL: "18:00",
    ConfigKey.T1_CUTOFF_TIMEZONE: "Asia/Dubai",
    ConfigKey.ALLOCATION_EXPIRY_MINUTES: "720",
    ConfigKey.VARIANCE_TOLERANCE_PCT: "2.0",
    ConfigKey.CONSUMPTION_TOLERANCE_PCT: "5.0",
    ConfigKey.REMNANT_VALUE_FRACTION: "0.7",
    ConfigKey.PARSER_VERSION_CURRENT: "1.0.0",
    
    # Machine
    ConfigKey.VACUUM_BED_LENGTH_MM: "6000",
    ConfigKey.VACUUM_BED_WIDTH_MM: "3200",
    
    # Truck
    ConfigKey.TRUCK_CAPACITY_10TON_M2: "180",
    ConfigKey.TRUCK_CAPACITY_3TON_M2: "60",
    
    # Shifts
    ConfigKey.SHIFT_MORNING_START: "07:00",
    ConfigKey.SHIFT_MORNING_END: "15:00",
    ConfigKey.SHIFT_EVENING_START: "15:00",
    ConfigKey.SHIFT_EVENING_END: "23:00",
    
    # SLA
    ConfigKey.SLA_EXCEPTION_CRITICAL_HOURS: "4",
    ConfigKey.SLA_EXCEPTION_HIGH_HOURS: "24",
}


# Folder structure for workspace creation
FOLDER_STRUCTURE = [
    "01. Commercial and Demand",
    "02. Tag Sheet Registry",
    "03. Production Planning",
    "04. Production and Delivery",
]


# Sheet to folder mapping
SHEET_FOLDER_MAP = {
    SheetName.REFERENCE_DATA: None,  # Root level
    SheetName.CONFIG: None,
    SheetName.LPO_MASTER: "01. Commercial and Demand",
    SheetName.LPO_AUDIT: "01. Commercial and Demand",
    SheetName.TAG_REGISTRY: "02. Tag Sheet Registry",
    SheetName.PRODUCTION_PLANNING: "03. Production Planning",
    SheetName.NESTING_LOG: "03. Production Planning",
    SheetName.ALLOCATION_LOG: "03. Production Planning",
    SheetName.CONSUMPTION_LOG: "04. Production and Delivery",
    SheetName.REMNANT_LOG: "04. Production and Delivery",
    SheetName.FILLER_LOG: "04. Production and Delivery",
    SheetName.DELIVERY_LOG: "04. Production and Delivery",
    SheetName.INVOICE_LOG: "04. Production and Delivery",
    SheetName.INVENTORY_TXN_LOG: "04. Production and Delivery",
    SheetName.INVENTORY_SNAPSHOT: "04. Production and Delivery",
    SheetName.SAP_INVENTORY_SNAPSHOT: "04. Production and Delivery",
    SheetName.PHYSICAL_INVENTORY_SNAPSHOT: "04. Production and Delivery",
    SheetName.OVERRIDE_LOG: "04. Production and Delivery",
    SheetName.USER_ACTION_LOG: "04. Production and Delivery",
    SheetName.EXCEPTION_LOG: "04. Production and Delivery",
}
