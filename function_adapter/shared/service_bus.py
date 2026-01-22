"""
Service Bus Module
==================

Handles sending messages to Azure Service Bus with:
- Singleton client (connection pooling)
- Retry logic with exponential backoff
- Thread-safe operations
"""

import os
import json
import logging
import time
import threading
from azure.servicebus import ServiceBusClient, ServiceBusMessage
from azure.servicebus.exceptions import ServiceBusError
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

CONNECTION_STRING = os.getenv("SERVICE_BUS_CONNECTION")
QUEUE_NAME = os.getenv("SERVICE_BUS_QUEUE_NAME", "events-main")

# Max retry settings
MAX_RETRIES = 3
INITIAL_BACKOFF_SECONDS = 1.0

# Singleton client
_client: Optional[ServiceBusClient] = None
_client_lock = threading.Lock()


def get_client() -> ServiceBusClient:
    """
    Get a thread-safe singleton Service Bus client.
    
    Uses connection pooling for efficiency.
    """
    global _client
    
    if _client is None:
        with _client_lock:
            if _client is None:  # Double-check locking
                if not CONNECTION_STRING:
                    raise ValueError("SERVICE_BUS_CONNECTION is not set")
                _client = ServiceBusClient.from_connection_string(CONNECTION_STRING)
                logger.info("Service Bus client initialized (singleton)")
    
    return _client


def send_event(event: Dict[str, Any], queue_name: str = None) -> bool:
    """
    Send an event to the Service Bus queue with retry logic.
    
    Uses 'event_id' as the message_id for deduplication.
    Implements exponential backoff on transient failures.
    
    Args:
        event: Event dictionary to send
        queue_name: Optional queue name override
        
    Returns:
        True if successful, False otherwise
    """
    if not queue_name:
        queue_name = QUEUE_NAME
        
    event_id = event.get("event_id")
    trace_id = event.get("trace_id", "unknown")
    
    message = ServiceBusMessage(
        body=json.dumps(event),
        message_id=event_id,
        correlation_id=trace_id,
        content_type="application/json"
    )
    
    last_error = None
    
    for attempt in range(MAX_RETRIES):
        try:
            client = get_client()
            
            with client.get_queue_sender(queue_name) as sender:
                sender.send_messages(message)
                
            logger.info(f"[{trace_id}] Enqueued event {event_id} to {queue_name}")
            return True
            
        except ServiceBusError as e:
            last_error = e
            backoff = INITIAL_BACKOFF_SECONDS * (2 ** attempt)
            logger.warning(f"[{trace_id}] Service Bus error (attempt {attempt + 1}/{MAX_RETRIES}): {e}. "
                          f"Retrying in {backoff}s...")
            
            if attempt < MAX_RETRIES - 1:
                time.sleep(backoff)
                
        except Exception as e:
            last_error = e
            logger.error(f"[{trace_id}] Unexpected error sending to Service Bus: {e}")
            break
    
    logger.error(f"[{trace_id}] Failed to enqueue event {event_id} after {MAX_RETRIES} attempts: {last_error}")
    return False


def close_client():
    """Close the singleton client (for graceful shutdown)."""
    global _client
    
    with _client_lock:
        if _client is not None:
            try:
                _client.close()
                logger.info("Service Bus client closed")
            except Exception as e:
                logger.error(f"Error closing Service Bus client: {e}")
            finally:
                _client = None
