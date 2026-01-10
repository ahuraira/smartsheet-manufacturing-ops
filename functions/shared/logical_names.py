"""
Logical Names for Sheets and Columns
=====================================

These are the **code-facing** names used throughout the application.
They map to physical names/IDs via the workspace manifest.

Why Logical Names?
------------------
- Physical names in Smartsheet can change (user renames)
- Physical IDs are numbers (not readable in code)
- Logical names are stable, readable constants

Mapping Flow
------------
Code → Logical Name → Manifest → Physical ID → Smartsheet API

Example
-------
>>> from shared.logical_names import Sheet, Column
>>> from shared.manifest import get_manifest
>>> 
>>> manifest = get_manifest()
>>> sheet_id = manifest.get_sheet_id(Sheet.TAG_REGISTRY)
>>> column_id = manifest.get_column_id(Sheet.TAG_REGISTRY, Column.TAG_REGISTRY.FILE_HASH)

Naming Convention
-----------------
- Sheet names: UPPER_SNAKE_CASE (e.g., TAG_REGISTRY, LPO_MASTER)
- Column names: UPPER_SNAKE_CASE (e.g., FILE_HASH, SAP_REFERENCE)
- Folder names: UPPER_SNAKE_CASE with number prefix for ordering
"""


class Sheet:
    """Logical sheet names used in code."""
    
    # Root level
    REFERENCE_DATA = "REFERENCE_DATA"
    CONFIG = "CONFIG"
    
    # 01. Commercial and Demand
    LPO_MASTER = "LPO_MASTER"
    LPO_AUDIT = "LPO_AUDIT"
    
    # 02. Tag Sheet Registry
    TAG_REGISTRY = "TAG_REGISTRY"
    
    # 03. Production Planning
    PRODUCTION_PLANNING = "PRODUCTION_PLANNING"
    NESTING_LOG = "NESTING_LOG"
    ALLOCATION_LOG = "ALLOCATION_LOG"
    
    # 04. Production and Delivery
    CONSUMPTION_LOG = "CONSUMPTION_LOG"
    REMNANT_LOG = "REMNANT_LOG"
    FILLER_LOG = "FILLER_LOG"
    DELIVERY_LOG = "DELIVERY_LOG"
    INVOICE_LOG = "INVOICE_LOG"
    INVENTORY_TXN_LOG = "INVENTORY_TXN_LOG"
    INVENTORY_SNAPSHOT = "INVENTORY_SNAPSHOT"
    SAP_INVENTORY_SNAPSHOT = "SAP_INVENTORY_SNAPSHOT"
    PHYSICAL_INVENTORY_SNAPSHOT = "PHYSICAL_INVENTORY_SNAPSHOT"
    OVERRIDE_LOG = "OVERRIDE_LOG"
    USER_ACTION_LOG = "USER_ACTION_LOG"
    EXCEPTION_LOG = "EXCEPTION_LOG"


class Folder:
    """Logical folder names."""
    
    COMMERCIAL_AND_DEMAND = "01_COMMERCIAL_AND_DEMAND"
    TAG_SHEET_REGISTRY = "02_TAG_SHEET_REGISTRY"
    PRODUCTION_PLANNING = "03_PRODUCTION_PLANNING"
    PRODUCTION_AND_DELIVERY = "04_PRODUCTION_AND_DELIVERY"


class Column:
    """
    Logical column names organized by sheet.
    
    Usage:
        >>> Column.TAG_REGISTRY.FILE_HASH
        'FILE_HASH'
    """
    
    class COMMON:
        """Columns used across multiple sheets."""
        STATUS = "STATUS"
        CREATED_AT = "CREATED_AT"
        REMARKS = "REMARKS"
        CLIENT_REQUEST_ID = "CLIENT_REQUEST_ID"
    
    class CONFIG:
        """00a Config sheet columns."""
        CONFIG_KEY = "CONFIG_KEY"
        CONFIG_VALUE = "CONFIG_VALUE"
        EFFECTIVE_FROM = "EFFECTIVE_FROM"
        CHANGED_BY = "CHANGED_BY"
    
    class LPO_MASTER:
        """01 LPO Master LOG columns."""
        LPO_ID = "LPO_ID"
        CUSTOMER_LPO_REF = "CUSTOMER_LPO_REF"
        SAP_REFERENCE = "SAP_REFERENCE"
        CUSTOMER_NAME = "CUSTOMER_NAME"
        PROJECT_NAME = "PROJECT_NAME"
        LPO_STATUS = "LPO_STATUS"
        BRAND = "BRAND"
        WASTAGE_CONSIDERED_IN_COSTING = "WASTAGE_CONSIDERED_IN_COSTING"  # Allowable wastage
        PO_QUANTITY_SQM = "PO_QUANTITY_SQM"
        DELIVERED_QUANTITY_SQM = "DELIVERED_QUANTITY_SQM"
        REMARKS = "REMARKS"
    
    class TAG_REGISTRY:
        """02 Tag Sheet Registry columns."""
        TAG_ID = "TAG_ID"
        TAG_NAME = "TAG_NAME"
        DATE_TAG_SHEET_RECEIVED = "DATE_TAG_SHEET_RECEIVED"
        REQUIRED_DELIVERY_DATE = "REQUIRED_DELIVERY_DATE"
        LPO_SAP_REFERENCE = "LPO_SAP_REFERENCE"
        LPO_STATUS = "LPO_STATUS"
        LPO_ALLOWABLE_WASTAGE = "LPO_ALLOWABLE_WASTAGE"
        PRODUCTION_GATE = "PRODUCTION_GATE"
        BRAND = "BRAND"
        CUSTOMER_NAME = "CUSTOMER_NAME"
        PROJECT = "PROJECT"
        LOCATION = "LOCATION"
        ESTIMATED_QUANTITY = "ESTIMATED_QUANTITY"
        SHEETS_USED = "SHEETS_USED"
        WASTAGE_NESTED = "WASTAGE_NESTED"
        STATUS = "STATUS"
        PLANNED_CUT_DATE = "PLANNED_CUT_DATE"
        ALLOCATION_BATCH_ID = "ALLOCATION_BATCH_ID"
        SUBMITTED_BY = "SUBMITTED_BY"
        RECEIVED_THROUGH = "RECEIVED_THROUGH"
        REMARKS = "REMARKS"  # User remarks
        FILE_HASH = "FILE_HASH"
        CLIENT_REQUEST_ID = "CLIENT_REQUEST_ID"
    
    class EXCEPTION_LOG:
        """99 Exception Log columns."""
        EXCEPTION_ID = "EXCEPTION_ID"
        CREATED_AT = "CREATED_AT"
        SOURCE = "SOURCE"
        RELATED_TAG_ID = "RELATED_TAG_ID"
        RELATED_TXN_ID = "RELATED_TXN_ID"
        MATERIAL_CODE = "MATERIAL_CODE"
        QUANTITY = "QUANTITY"
        REASON_CODE = "REASON_CODE"
        SEVERITY = "SEVERITY"
        STATUS = "STATUS"
        SLA_DUE = "SLA_DUE"
        RESOLUTION_ACTION = "RESOLUTION_ACTION"
    
    class USER_ACTION_LOG:
        """98 User Action Log columns."""
        ACTION_ID = "ACTION_ID"
        TIMESTAMP = "TIMESTAMP"
        USER_ID = "USER_ID"
        ACTION_TYPE = "ACTION_TYPE"
        TARGET_TABLE = "TARGET_TABLE"
        TARGET_ID = "TARGET_ID"
        OLD_VALUE = "OLD_VALUE"
        NEW_VALUE = "NEW_VALUE"
        NOTES = "NOTES"


# Mapping from Sheet logical name to Column class
SHEET_COLUMNS = {
    Sheet.CONFIG: Column.CONFIG,
    Sheet.LPO_MASTER: Column.LPO_MASTER,
    Sheet.TAG_REGISTRY: Column.TAG_REGISTRY,
    Sheet.EXCEPTION_LOG: Column.EXCEPTION_LOG,
    Sheet.USER_ACTION_LOG: Column.USER_ACTION_LOG,
}
