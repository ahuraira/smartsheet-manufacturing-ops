"""
Event Log Module
================

Handles database operations for the event_log table with:
- Connection pooling for efficiency
- Thread-safe operations
- Retry logic for transient failures
"""

import os
import json
import logging
import time
import threading
import pyodbc
from datetime import datetime
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Connection string from environment
_RAW_CONNECTION_STRING = os.getenv("SQL_CONNECTION_STRING", "")

# Connection pool settings
_connection: Optional[pyodbc.Connection] = None
_connection_lock = threading.Lock()

# Retry settings
MAX_RETRIES = 3
INITIAL_BACKOFF_SECONDS = 0.5


def _build_odbc_connection_string(raw_conn_str: str) -> str:
    """
    Convert ADO.NET style connection string to ODBC format if needed.
    """
    if not raw_conn_str:
        return ""
        
    # If it's ADO.NET style (has 'Server=' but no 'Driver='), convert it
    if "Server=" in raw_conn_str and "Driver=" not in raw_conn_str:
        parts = {}
        for part in raw_conn_str.split(";"):
            if "=" in part:
                key, value = part.split("=", 1)
                parts[key.strip()] = value.strip()
        
        server = parts.get("Server", "").replace("tcp:", "")
        database = parts.get("Initial Catalog", "")
        user = parts.get("UserID", parts.get("User ID", ""))
        password = parts.get("Password", "")
        
        return (
            f"Driver={{ODBC Driver 18 for SQL Server}};"
            f"Server={server};"
            f"Database={database};"
            f"Uid={user};"
            f"Pwd={password};"
            f"Encrypt=yes;"
            f"TrustServerCertificate=no;"
        )
    
    return raw_conn_str


def get_connection() -> pyodbc.Connection:
    """
    Get a database connection with connection pooling.
    
    Reuses existing connection if available and healthy.
    Creates new connection if needed.
    """
    global _connection
    
    if not _RAW_CONNECTION_STRING:
        raise ValueError("SQL_CONNECTION_STRING is not set")
    
    with _connection_lock:
        # Check if existing connection is healthy
        if _connection is not None:
            try:
                # Quick health check
                _connection.execute("SELECT 1")
                return _connection
            except Exception:
                # Connection is stale, close and recreate
                try:
                    _connection.close()
                except Exception:
                    pass
                _connection = None
        
        # Create new connection
        conn_str = _build_odbc_connection_string(_RAW_CONNECTION_STRING)
        _connection = pyodbc.connect(conn_str, autocommit=True)
        logger.info("Database connection established")
        return _connection


def _execute_with_retry(operation, trace_id: str = "unknown"):
    """
    Execute a database operation with retry logic.
    
    Args:
        operation: Callable that takes a connection and executes the query
        trace_id: For logging
        
    Returns:
        Result of the operation
    """
    last_error = None
    
    for attempt in range(MAX_RETRIES):
        try:
            conn = get_connection()
            return operation(conn)
            
        except pyodbc.Error as e:
            last_error = e
            error_code = e.args[0] if e.args else ""
            
            # Check if it's a transient error that should be retried
            transient_errors = ["08S01", "08001", "08003", "40001", "40613"]
            if any(code in str(error_code) for code in transient_errors):
                backoff = INITIAL_BACKOFF_SECONDS * (2 ** attempt)
                logger.warning(f"[{trace_id}] Transient DB error (attempt {attempt + 1}/{MAX_RETRIES}): {e}. "
                              f"Retrying in {backoff}s...")
                
                # Reset connection for retry
                global _connection
                with _connection_lock:
                    if _connection is not None:
                        try:
                            _connection.close()
                        except Exception:
                            pass
                        _connection = None
                
                if attempt < MAX_RETRIES - 1:
                    time.sleep(backoff)
            else:
                # Non-transient error, don't retry
                raise
                
    raise last_error


def event_exists(event_id: str) -> bool:
    """
    Check if an event has already been logged.
    Returns True if exists, False otherwise.
    """
    if not event_id:
        return False
    
    def check_existence(conn):
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1 FROM event_log WHERE event_id = ?", event_id)
            return cursor.fetchone() is not None
    
    try:
        return _execute_with_retry(check_existence, f"check-{event_id}")
    except Exception as e:
        logger.error(f"Error checking event existence: {e}")
        # Fail-open: allow processing if DB check fails
        # This ensures availability over strict consistency
        return False


def insert_event_stub(
    event_id: str,
    source: str,
    webhook_id: str,
    sheet_id: Optional[str],
    row_id: Optional[str],
    column_id: Optional[str],
    object_type: str,
    action: str,
    payload: Dict[str, Any],
    trace_id: str
) -> bool:
    """
    Insert a new event stub with status PENDING.
    Returns True if successful, False otherwise.
    """
    payload_json = json.dumps(payload)
    
    def do_insert(conn):
        sql = """
            INSERT INTO event_log (
                event_id, source, webhook_id, sheet_id, row_id, column_id,
                object_type, action, payload, trace_id, status, received_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', SYSUTCDATETIME())
        """
        
        with conn.cursor() as cursor:
            cursor.execute(
                sql,
                event_id, source, webhook_id, sheet_id, row_id, column_id,
                object_type, action, payload_json, trace_id
            )
        return True
    
    try:
        return _execute_with_retry(do_insert, trace_id)
    except pyodbc.IntegrityError:
        # Duplicate key - event already exists (idempotency)
        logger.warning(f"[{trace_id}] Event {event_id} already exists in DB")
        return False
    except Exception as e:
        logger.error(f"[{trace_id}] Error inserting event stub: {e}")
        return False


def update_event_status(
    event_id: str,
    status: str,
    error_message: Optional[str] = None
) -> bool:
    """
    Update the status of an event.
    """
    def do_update(conn):
        sql = """
            UPDATE event_log 
            SET status = ?, 
                processed_at = CASE WHEN ? IN ('SUCCESS', 'FAILED') THEN SYSUTCDATETIME() ELSE processed_at END,
                error_message = ?
            WHERE event_id = ?
        """
        
        with conn.cursor() as cursor:
            cursor.execute(sql, status, status, error_message, event_id)
        return True
    
    try:
        return _execute_with_retry(do_update, f"update-{event_id}")
    except Exception as e:
        logger.error(f"Error updating event status: {e}")
        return False


def close_connection():
    """Close the database connection (for graceful shutdown)."""
    global _connection
    
    with _connection_lock:
        if _connection is not None:
            try:
                _connection.close()
                logger.info("Database connection closed")
            except Exception as e:
                logger.error(f"Error closing database connection: {e}")
            finally:
                _connection = None
