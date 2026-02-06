import logging
import hashlib
from typing import Optional, Tuple, Any
from shared.smartsheet_client import SmartsheetClient
from shared.logical_names import Sheet, Column
from .models import ValidationResult

from shared.manifest import get_manifest

logger = logging.getLogger(__name__)

def calculate_file_hash(file_content: bytes) -> str:
    """Calculate SHA256 hash of file content."""
    return hashlib.sha256(file_content).hexdigest()

def get_row_value(row: dict, sheet_logical: str, col_logical: str) -> Optional[Any]:
    """Helper to get value from row dict using logical column name."""
    manifest = get_manifest()
    physical_name = manifest.get_column_name(sheet_logical, col_logical)
    if not physical_name:
        logger.warning(f"Could not resolve logical column {col_logical} in {sheet_logical}")
        return None
    return row.get(physical_name)

def validate_tag_exists(client: SmartsheetClient, tag_id: str) -> ValidationResult:
    """
    Validate that the Tag ID exists in the Tag Sheet Registry.
    
    CRITICAL SOTA UPDATE:
    Now explicitly checks for duplicate Tag IDs. Previously assumed uniqueness 
    and took 'rows[0]', which could lead to updates on the wrong row if duplicates existed.
    """
    try:
        rows = client.find_rows(
            sheet_ref=Sheet.TAG_REGISTRY,
            column_ref=Column.TAG_REGISTRY.TAG_ID,
            value=tag_id
        )
        
        if not rows:
            return ValidationResult(
                is_valid=False,
                error_code="TAG_NOT_FOUND",
                error_message=f"Tag ID '{tag_id}' not found in Tag Registry"
            )
            
        # SOTA Check: Ambiguity Resolution
        if len(rows) > 1:
            logger.error(f"Duplicate Tag IDs found for '{tag_id}' - Count: {len(rows)}")
            return ValidationResult(
                is_valid=False,
                error_code="TAG_DUPLICATE",
                error_message=f"Critical Error: Multiple records found for Tag ID '{tag_id}'. Cannot safely identify target."
            )
            
        # Tag found and unique
        tag_row = rows[0]
        lpo_ref = get_row_value(tag_row, Sheet.TAG_REGISTRY, Column.TAG_REGISTRY.LPO_SAP_REFERENCE)
                  
        return ValidationResult(
            is_valid=True,
            tag_row_id=tag_row.get("id") or tag_row.get("row_id"), # Handle mock vs real variations
            tag_lpo_ref=lpo_ref
        )
        
    except Exception as e:
        logger.error(f"Error validating tag existence: {e}")
        # Fail safe - if we can't validate, we should block processing
        return ValidationResult(
            is_valid=False,
            error_code="VALIDATION_SYSTEM_ERROR",
            error_message=f"System error validation tag: {str(e)}"
        )

def validate_tag_lpo_ownership(
    validation_result: ValidationResult, 
    expected_lpo_sap_ref: str
) -> ValidationResult:
    """
    Validate that the Tag belongs to the expected LPO.
    
    Args:
        validation_result: Result from validate_tag_exists (containing tag context)
        expected_lpo_sap_ref: SAP Reference extracted from folder path
        
    Returns:
        ValidationResult: Validity status
    """
    if not validation_result.is_valid:
        return validation_result
        
    tag_lpo_ref = validation_result.tag_lpo_ref
    
    # Normalize for comparison (handle potential None or whitespace)
    tag_lpo_clean = str(tag_lpo_ref).strip().upper() if tag_lpo_ref else ""
    expected_lpo_clean = str(expected_lpo_sap_ref).strip().upper() if expected_lpo_sap_ref else ""
    
    # Simple direct match
    if tag_lpo_clean == expected_lpo_clean:
        return ValidationResult(
            is_valid=True,
            tag_row_id=validation_result.tag_row_id,
            tag_lpo_ref=tag_lpo_ref
        )
        
    # Validation failed
    msg = (f"Tag belongs to LPO '{tag_lpo_ref}', "
           f"but file was uploaded to LPO '{expected_lpo_sap_ref}' folder")
           
    return ValidationResult(
        is_valid=False,
        error_code="LPO_MISMATCH",
        error_message=msg,
        tag_row_id=validation_result.tag_row_id
    )

def check_duplicate_file(
    client: SmartsheetClient, 
    file_hash: str, 
    sap_lpo_ref: str
) -> Optional[str]:
    """
    Check if a file with the same hash has already been processed for this LPO.
    """
    try:
        # We search primarily by Hash as it's highly specific
        rows = client.find_rows(
            sheet_ref=Sheet.NESTING_LOG,
            column_ref=Column.NESTING_LOG.FILE_HASH,
            value=file_hash
        )
        
        if not rows:
            return None
            
        # Potentially multiple hits (if same file used across LPOs? Unlikely for nesting)
        # But let's check basic validity or return the first match's Session ID
        existing_row = rows[0]
        return get_row_value(existing_row, Sheet.NESTING_LOG, Column.NESTING_LOG.NEST_SESSION_ID)
        
    except Exception as e:
        logger.warning(f"Error checking duplicate hash: {e}")
        return None

def check_duplicate_request_id(
    client: SmartsheetClient, 
    client_request_id: str
) -> Optional[str]:
    """
    Check if a request with the same client_request_id has already been processed.
    """
    if not client_request_id:
        return None
        
    try:
        rows = client.find_rows(
            sheet_ref=Sheet.NESTING_LOG,
            column_ref=Column.NESTING_LOG.CLIENT_REQUEST_ID,
            value=client_request_id
        )
        
        if rows:
            return get_row_value(rows[0], Sheet.NESTING_LOG, Column.NESTING_LOG.NEST_SESSION_ID)
            
        return None
        
    except Exception as e:
        logger.warning(f"Error checking duplicate request ID: {e}")
        return None


def validate_tag_is_planned(
    client: SmartsheetClient,
    tag_id: str
) -> ValidationResult:
    """
    Validate that the Tag has been scheduled in Production Planning.
    
    PREREQUISITE: Tag must be planned before nesting file can be processed.
    
    SOTA v1.6.9: Robust row selection:
    - Filters to active statuses (not Cancelled/Completed)
    - Sorts by planned_date descending (most recent first)
    - Falls back to modifiedAt if planned_date is not set
    
    Args:
        client: SmartsheetClient instance
        tag_id: Tag ID to validate
        
    Returns:
        ValidationResult with planning_row_id and planned_date if valid
    """
    # Active statuses that allow nesting (exclude terminal states)
    INACTIVE_STATUSES = {"Cancelled", "Completed", "Closed", "Archived"}
    
    try:
        rows = client.find_rows(
            sheet_ref=Sheet.PRODUCTION_PLANNING,
            column_ref=Column.PRODUCTION_PLANNING.TAG_SHEET_ID,
            value=tag_id
        )
        
        if not rows:
            logger.warning(f"Tag {tag_id} has not been scheduled in Production Planning")
            return ValidationResult(
                is_valid=False,
                error_code="TAG_NOT_PLANNED",
                error_message=f"Tag '{tag_id}' must be scheduled before nesting file can be processed"
            )
        
        # v1.6.9 SOTA: Filter to active rows only
        active_rows = []
        for row in rows:
            status = get_row_value(row, Sheet.PRODUCTION_PLANNING, Column.PRODUCTION_PLANNING.STATUS)
            if status and str(status).strip() in INACTIVE_STATUSES:
                logger.debug(f"Skipping planning row with status '{status}'")
                continue
            active_rows.append(row)
        
        if not active_rows:
            logger.warning(f"Tag {tag_id} has planning entries but all are inactive")
            return ValidationResult(
                is_valid=False,
                error_code="TAG_NOT_PLANNED",
                error_message=f"Tag '{tag_id}' has no active planning entries (all are Cancelled/Completed)"
            )
        
        # v1.6.9 SOTA: Sort by createdAt descending (most recent first)
        # User Change: Prefer system creation date over planned_date
        def get_sort_key(row):
            created_at = row.get("createdAt") or ""
            modified_at = row.get("modifiedAt") or ""
            planned_date = get_row_value(row, Sheet.PRODUCTION_PLANNING, Column.PRODUCTION_PLANNING.PLANNED_DATE) or ""
            # Priority: CreatedAt -> ModifiedAt -> PlannedDate
            return created_at or modified_at or planned_date
        
        # Sort descending (most recent first)
        active_rows.sort(key=get_sort_key, reverse=True)
        
        # Take the most recent active planning entry
        planning_row = active_rows[0]
        row_id = planning_row.get("id") or planning_row.get("row_id")
        planned_date = get_row_value(planning_row, Sheet.PRODUCTION_PLANNING, Column.PRODUCTION_PLANNING.PLANNED_DATE)
        status = get_row_value(planning_row, Sheet.PRODUCTION_PLANNING, Column.PRODUCTION_PLANNING.STATUS)
        
        logger.info(
            f"Tag {tag_id} is planned for {planned_date} (status: {status}). "
            f"Selected from {len(active_rows)} active row(s)."
        )
        
        return ValidationResult(
            is_valid=True,
            planning_row_id=row_id,
            planned_date=str(planned_date) if planned_date else None
        )
        
    except Exception as e:
        logger.error(f"Error validating tag is planned: {e}")
        return ValidationResult(
            is_valid=False,
            error_code="VALIDATION_SYSTEM_ERROR",
            error_message=f"System error checking production planning: {str(e)}"
        )


def get_lpo_details(
    client: SmartsheetClient,
    sap_lpo_reference: str
) -> ValidationResult:
    """
    Fetch LPO details including Brand and Area Type.
    
    FAIL-FAST: If LPO not found or Brand is missing, returns invalid result.
    
    Args:
        client: SmartsheetClient instance
        sap_lpo_reference: SAP Reference to lookup
        
    Returns:
        ValidationResult with brand, lpo_row_id, and area_type if valid
    """
    if not sap_lpo_reference:
        return ValidationResult(
            is_valid=False,
            error_code="LPO_NOT_FOUND",
            error_message="SAP LPO Reference is required but was not provided"
        )
    
    try:
        rows = client.find_rows(
            sheet_ref=Sheet.LPO_MASTER,
            column_ref=Column.LPO_MASTER.SAP_REFERENCE,
            value=sap_lpo_reference
        )
        
        if not rows:
            logger.warning(f"LPO with SAP Reference '{sap_lpo_reference}' not found")
            return ValidationResult(
                is_valid=False,
                error_code="LPO_NOT_FOUND",
                error_message=f"LPO with SAP Reference '{sap_lpo_reference}' not found in LPO Master"
            )
        
        lpo_row = rows[0]
        row_id = lpo_row.get("id") or lpo_row.get("row_id")
        brand = get_row_value(lpo_row, Sheet.LPO_MASTER, Column.LPO_MASTER.BRAND)
        area_type = get_row_value(lpo_row, Sheet.LPO_MASTER, Column.LPO_MASTER.AREA_TYPE)
        folder_url = get_row_value(lpo_row, Sheet.LPO_MASTER, Column.LPO_MASTER.FOLDER_URL)
        
        # FAIL-FAST: Brand is required
        if not brand:
            logger.warning(f"LPO {sap_lpo_reference} is missing Brand")
            return ValidationResult(
                is_valid=False,
                error_code="LPO_INVALID_DATA",
                error_message=f"LPO '{sap_lpo_reference}' is missing required Brand field"
            )
        
        logger.info(f"LPO {sap_lpo_reference}: Brand={brand}, AreaType={area_type}, Folder={folder_url}")
        
        return ValidationResult(
            is_valid=True,
            lpo_row_id=row_id,
            brand=brand,
            area_type=area_type or "External",  # Default to External if not set
            lpo_folder_url=folder_url
        )
        
    except Exception as e:
        logger.error(f"Error fetching LPO details: {e}")
        return ValidationResult(
            is_valid=False,
            error_code="VALIDATION_SYSTEM_ERROR",
            error_message=f"System error fetching LPO details: {str(e)}"
        )

