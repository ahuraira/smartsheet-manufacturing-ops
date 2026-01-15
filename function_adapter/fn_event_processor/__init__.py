"""
fn_event_processor: Service Bus Event Processor
================================================

This function is triggered by messages from the Service Bus queue
and forwards ALL events to the core functions app.

IMPORTANT: This is a PURE PASS-THROUGH. Zero business logic.
All routing and processing happens in the core functions app.

Retry Strategy:
- HTTP calls to core functions use retry with exponential backoff
- On persistent failure, message goes to Dead Letter Queue (DLQ)
"""

import logging
import json
import os
import time
from typing import Dict, Any

import azure.functions as func
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# Configuration
CORE_FUNCTIONS_BASE_URL = os.getenv("CORE_FUNCTIONS_BASE_URL", "")
CORE_FUNCTIONS_TIMEOUT = int(os.getenv("CORE_FUNCTIONS_TIMEOUT_SECONDS", "30"))
PROCESS_ROW_ENDPOINT = "/api/events/process-row"

# Retry settings for HTTP calls
MAX_HTTP_RETRIES = 3
INITIAL_BACKOFF_SECONDS = 1.0


def create_session_with_retry() -> requests.Session:
    """
    Create a requests session with retry logic built-in.
    
    Retries on:
    - 429: Too Many Requests
    - 500, 502, 503, 504: Server errors
    """
    session = requests.Session()
    
    retry_strategy = Retry(
        total=MAX_HTTP_RETRIES,
        backoff_factor=INITIAL_BACKOFF_SECONDS,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["POST", "GET", "PUT"]
    )
    
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    return session


# Singleton session for connection pooling
_session = None


def get_session() -> requests.Session:
    """Get singleton HTTP session with retry logic."""
    global _session
    if _session is None:
        _session = create_session_with_retry()
    return _session


def forward_to_core_functions(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Forward event to core functions app with retry logic.
    
    Returns response from core function or error dict.
    """
    if not CORE_FUNCTIONS_BASE_URL:
        logger.error("CORE_FUNCTIONS_BASE_URL is not configured")
        return {"status": "ERROR", "message": "Core functions URL not configured"}
    
    url = f"{CORE_FUNCTIONS_BASE_URL}{PROCESS_ROW_ENDPOINT}"
    trace_id = event.get("trace_id", "unknown")
    
    # Build minimal payload - no enrichment
    payload = {
        "event_id": event.get("event_id"),
        "source": "WEBHOOK_ADAPTER",
        "sheet_id": event.get("sheet_id"),
        "row_id": event.get("row_id"),
        "action": event.get("action"),
        "object_type": event.get("object_type"),
        "actor_id": event.get("actor"),
        "timestamp_utc": event.get("timestamp_utc"),
        "trace_id": trace_id
    }
    
    logger.info(f"[{trace_id}] Forwarding to {url}")
    
    try:
        session = get_session()
        
        response = session.post(
            url,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "x-client-request-id": event.get("event_id", trace_id),
                "x-trace-id": trace_id
            },
            timeout=CORE_FUNCTIONS_TIMEOUT
        )
        
        if response.status_code == 200:
            result = response.json()
            logger.info(f"[{trace_id}] Core function response: {result}")
            return result
        else:
            logger.error(f"[{trace_id}] Core function returned {response.status_code}: {response.text}")
            return {
                "status": "ERROR",
                "message": f"Core function returned {response.status_code}",
                "details": response.text[:500]
            }
            
    except requests.exceptions.Timeout:
        logger.error(f"[{trace_id}] Core function timed out after {CORE_FUNCTIONS_TIMEOUT}s")
        return {"status": "ERROR", "message": "Timeout calling core function"}
        
    except requests.exceptions.RequestException as e:
        logger.error(f"[{trace_id}] HTTP error calling core function: {e}")
        return {"status": "ERROR", "message": str(e)}


def main(msg: func.ServiceBusMessage) -> None:
    """
    Process a message from Service Bus.
    
    Strategy:
    - Forward to core functions with retry
    - On success: message completes automatically
    - On failure: raise exception to trigger Service Bus retry/DLQ
    """
    try:
        # Parse message
        body = msg.get_body().decode('utf-8')
        event = json.loads(body)
        
        trace_id = event.get("trace_id", "unknown")
        event_id = event.get("event_id", "unknown")
        
        logger.info(f"[{trace_id}] Received event {event_id}")
        logger.info(f"[{trace_id}] Event details: sheet={event.get('sheet_id')}, "
                   f"row={event.get('row_id')}, action={event.get('action')}")
        
        # Forward to core functions with retry
        result = forward_to_core_functions(event)
        
        status = result.get("status", "UNKNOWN")
        logger.info(f"[{trace_id}] Processing complete: {status}")
        
        # If core function failed, raise to trigger Service Bus retry
        if status == "ERROR":
            error_msg = result.get("message", "Unknown error")
            logger.error(f"[{trace_id}] Core function failed: {error_msg}")
            # Raise exception to trigger Service Bus retry mechanism
            # After max delivery attempts, message goes to DLQ
            raise RuntimeError(f"Core function failed: {error_msg}")
        
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse message body: {e}")
        # Don't retry malformed messages - let them go to DLQ immediately
        raise
        
    except Exception as e:
        logger.exception(f"Error processing message: {e}")
        # Re-raise to trigger Service Bus retry
        raise
