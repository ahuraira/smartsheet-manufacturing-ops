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
