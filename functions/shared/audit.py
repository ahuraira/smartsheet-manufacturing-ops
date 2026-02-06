"""
Shared Audit Utilities
======================

Centralized functions for audit logging and exception creation.
Used by all Azure Functions to ensure consistent audit trail.

DRY Principle: This module eliminates duplicate _create_exception and
_log_user_action implementations across multiple functions.
"""

import logging
from datetime import datetime
from typing import Optional

from .logical_names import Sheet, Column
from .models import ExceptionSeverity, ExceptionSource, ReasonCode, ActionType
from .id_generator import generate_next_exception_id, generate_next_action_id
from .helpers import calculate_sla_due, format_datetime_for_smartsheet

logger = logging.getLogger(__name__)


def create_exception(
    client,
    trace_id: str,
    reason_code: ReasonCode,
    severity: ExceptionSeverity,
    source: ExceptionSource = ExceptionSource.INGEST,
    related_tag_id: Optional[str] = None,
    related_txn_id: Optional[str] = None,
    material_code: Optional[str] = None,
    quantity: Optional[float] = None,
    message: Optional[str] = None,
    client_request_id: Optional[str] = None,  # For deduplication (v1.6.5)
) -> str:
    """
    Create an exception record and return the exception_id.
    
    This is the authoritative function for exception creation.
    All functions should use this instead of duplicating the logic.
    
    IDEMPOTENCY (v1.6.5):
    If client_request_id is provided, the function will first check if an
    exception with this ID already exists. If so, it returns the existing
    exception_id without creating a duplicate. This prevents multiple exceptions
    from webhook retries processing the same event.
    
    Args:
        client: SmartsheetClient instance
        trace_id: Correlation ID for tracing
        reason_code: Exception reason (from ReasonCode enum)
        severity: Exception severity (from ExceptionSeverity enum)
        source: Exception source (from ExceptionSource enum)
        related_tag_id: Related tag ID if applicable
        related_txn_id: Related transaction ID if applicable
        material_code: Material code if applicable
        quantity: Quantity involved if applicable
        message: Human-readable message for resolution action
        client_request_id: Idempotency key - if provided, dedup check is performed
        
    Returns:
        The generated exception_id (e.g., "EX-0001")
    """
    # DEDUP CHECK (v1.6.5): If client_request_id provided, check for existing exception
    if client_request_id:
        try:
            existing = client.find_row(
                Sheet.EXCEPTION_LOG,
                Column.EXCEPTION_LOG.CLIENT_REQUEST_ID,
                client_request_id
            )
            if existing:
                existing_id = existing.get(Column.EXCEPTION_LOG.EXCEPTION_ID) or existing.get("Exception ID")
                logger.info(f"[{trace_id}] Exception already exists for {client_request_id}: {existing_id}")
                return existing_id
        except Exception as e:
            logger.warning(f"[{trace_id}] Exception dedup check failed: {e} - proceeding with creation")
    
    exception_id = generate_next_exception_id(client)
    now = datetime.now()
    
    exception_data = {
        Column.EXCEPTION_LOG.EXCEPTION_ID: exception_id,
        Column.EXCEPTION_LOG.CREATED_AT: format_datetime_for_smartsheet(now),
        Column.EXCEPTION_LOG.SOURCE: source.value,
        Column.EXCEPTION_LOG.REASON_CODE: reason_code.value,
        Column.EXCEPTION_LOG.SEVERITY: severity.value,
        Column.EXCEPTION_LOG.STATUS: "Open",
        Column.EXCEPTION_LOG.SLA_DUE: format_datetime_for_smartsheet(calculate_sla_due(severity, now)),
    }
    
    # Optional fields
    if client_request_id:
        exception_data[Column.EXCEPTION_LOG.CLIENT_REQUEST_ID] = client_request_id
    if related_tag_id:
        exception_data[Column.EXCEPTION_LOG.RELATED_TAG_ID] = related_tag_id
    if related_txn_id:
        exception_data[Column.EXCEPTION_LOG.RELATED_TXN_ID] = related_txn_id
    if material_code:
        exception_data[Column.EXCEPTION_LOG.MATERIAL_CODE] = material_code
    if quantity is not None:
        exception_data[Column.EXCEPTION_LOG.QUANTITY] = quantity
    if message:
        exception_data[Column.EXCEPTION_LOG.RESOLUTION_ACTION] = message
    
    try:
        client.add_row(Sheet.EXCEPTION_LOG, exception_data)
        logger.info(f"[{trace_id}] Exception created: {exception_id}")
    except Exception as e:
        logger.error(f"[{trace_id}] Failed to create exception: {e}")
    
    return exception_id


def log_user_action(
    client,
    user_id: str,
    action_type: ActionType,
    target_table: str,
    target_id: str,
    old_value: Optional[str] = None,
    new_value: Optional[str] = None,
    notes: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> Optional[str]:
    """
    Log a user action to the audit trail.
    
    This is the authoritative function for audit logging.
    All functions should use this instead of duplicating the logic.
    
    EMAIL RESOLUTION (v1.6.7):
    If user_id is a numeric Smartsheet user ID, it is automatically
    resolved to an email address for business-friendly logs.
    
    Args:
        client: SmartsheetClient instance
        user_id: User email/identifier who performed the action
        action_type: Type of action (from ActionType enum)
        target_table: Name of the affected table/sheet
        target_id: ID of the affected record
        old_value: Previous value (for updates)
        new_value: New value (for creates/updates)
        notes: Additional notes about the action
        trace_id: Correlation ID for tracing
        
    Returns:
        The generated action_id (e.g., "ACT-0001") or None if failed
    """
    # Email resolution (v1.6.7): Convert numeric user IDs to email
    if user_id and str(user_id).isdigit():
        try:
            email = client.get_user_email(int(user_id))
            if email:
                user_id = email
        except Exception as e:
            logger.debug(f"Failed to resolve user_id {user_id} to email: {e}")
    
    # Generate action ID
    action_id = generate_next_action_id(client)
    
    target_table_str = target_table
    if hasattr(target_table, 'value'):
        target_table_str = target_table.value
    elif not isinstance(target_table, str):
        target_table_str = str(target_table)
    
    action_data = {
        Column.USER_ACTION_LOG.ACTION_ID: action_id,
        Column.USER_ACTION_LOG.TIMESTAMP: format_datetime_for_smartsheet(datetime.now()),
        Column.USER_ACTION_LOG.USER_ID: user_id,
        Column.USER_ACTION_LOG.ACTION_TYPE: action_type.value,
        Column.USER_ACTION_LOG.TARGET_TABLE: target_table_str,
        Column.USER_ACTION_LOG.TARGET_ID: target_id,
    }
    
    # Optional fields
    if old_value:
        action_data[Column.USER_ACTION_LOG.OLD_VALUE] = old_value
    if new_value:
        action_data[Column.USER_ACTION_LOG.NEW_VALUE] = new_value
    if notes:
        action_data[Column.USER_ACTION_LOG.NOTES] = notes
    elif trace_id:
        action_data[Column.USER_ACTION_LOG.NOTES] = f"Trace: {trace_id}"
    
    try:
        client.add_row(Sheet.USER_ACTION_LOG, action_data)
        logger.info(f"[{trace_id}] User action logged: {action_id} - {action_type.value}")
        return action_id
    except Exception as e:
        logger.error(f"[{trace_id}] Failed to log user action: {e}")
        return None

