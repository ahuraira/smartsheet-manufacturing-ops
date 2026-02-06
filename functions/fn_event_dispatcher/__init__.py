"""
fn_event_dispatcher: Central Event Router
==========================================

This function receives Smartsheet webhook events from the adapter
and dispatches to appropriate core functions based on:
- Sheet ID (immutable)
- Action type (created/updated/deleted)
- Routing configuration (event_routing.json)

DOOMSDAY-PROOF DESIGN:
- Uses immutable Smartsheet IDs (not names)
- Routing config is externalized (JSON, not code)
- Sheet/column renames don't affect routing
- Manifest provides ID mapping

Architecture:
    Smartsheet Webhook → Adapter → Service Bus → fn_event_processor
        → POST /api/events/process-row (this function)
        → Internal dispatch to fn_lpo_ingest / fn_ingest_tag / etc.
"""

import logging
import json
import time
import azure.functions as func

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import (
    get_manifest,
    generate_trace_id,
    get_smartsheet_client,
)

from .models import RowEvent, DispatchResult, EventAction
from .router import (
    build_routing_table,
    get_handler_for_event,
    is_handler_implemented,
    load_routing_config,
)
from .handlers import (
    handle_lpo_ingest,
    handle_lpo_update,
    handle_tag_ingest,
    handle_schedule_ingest,
)

logger = logging.getLogger(__name__)

# Handler registry - maps handler names to functions
HANDLER_REGISTRY = {
    "lpo_ingest": handle_lpo_ingest,
    "lpo_update": handle_lpo_update,
    "tag_ingest": handle_tag_ingest,
    "schedule_tag": handle_schedule_ingest,
    # Add more handlers as implemented
}

# Initialize routing table on cold start
_initialized = False


def ensure_initialized():
    """Initialize routing table on first call."""
    global _initialized
    if not _initialized:
        try:
            manifest = get_manifest()
            build_routing_table(manifest)
            _initialized = True
            logger.info("Event dispatcher initialized")
        except Exception as e:
            logger.error(f"Failed to initialize routing table: {e}")
            raise


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    Main entry point for event processing.
    
    Receives events from adapter, routes to appropriate handler.
    """
    start_time = time.time()
    trace_id = req.headers.get("x-trace-id", generate_trace_id())
    
    try:
        # Initialize on first call
        ensure_initialized()
        
        # Parse event
        try:
            body = req.get_json()
            event = RowEvent(**body)
            event.trace_id = trace_id
        except Exception as e:
            logger.error(f"[{trace_id}] Failed to parse event: {e}")
            return func.HttpResponse(
                json.dumps({
                    "status": "ERROR",
                    "message": f"Invalid event format: {str(e)}",
                    "trace_id": trace_id
                }),
                status_code=400,
                mimetype="application/json"
            )
        
        # Resolve actor_id to email (SOTA: business-friendly logs)
        if event.actor_id and event.actor_id.isdigit():
            try:
                client = get_smartsheet_client()
                email = client.get_user_email(int(event.actor_id))
                if email:
                    event.actor_id = email
                    logger.debug(f"[{trace_id}] Resolved actor to {email}")
            except Exception as e:
                logger.warning(f"[{trace_id}] Failed to resolve actor email: {e}")
        
        logger.info(
            f"[{trace_id}] Received event: sheet={event.sheet_id}, "
            f"row={event.row_id}, action={event.action}"
        )
        
        # Get handler for this event
        handler_name, logical_sheet = get_handler_for_event(event)
        
        if handler_name is None:
            # No route for this event - ignore
            elapsed = (time.time() - start_time) * 1000
            logger.info(f"[{trace_id}] No route for sheet {event.sheet_id} - ignoring")
            return func.HttpResponse(
                json.dumps({
                    "status": "IGNORED",
                    "message": "No route configured for this event",
                    "trace_id": trace_id,
                    "processing_time_ms": round(elapsed, 2)
                }),
                status_code=200,
                mimetype="application/json"
            )
        
        # Check if handler is implemented
        if not is_handler_implemented(handler_name):
            elapsed = (time.time() - start_time) * 1000
            logger.info(f"[{trace_id}] Handler '{handler_name}' not implemented - ignoring")
            return func.HttpResponse(
                json.dumps({
                    "status": "NOT_IMPLEMENTED",
                    "handler": handler_name,
                    "message": f"Handler '{handler_name}' is not yet implemented",
                    "trace_id": trace_id,
                    "processing_time_ms": round(elapsed, 2)
                }),
                status_code=200,
                mimetype="application/json"
            )
        
        # Get handler function
        handler_func = HANDLER_REGISTRY.get(handler_name)
        
        if handler_func is None:
            elapsed = (time.time() - start_time) * 1000
            logger.error(f"[{trace_id}] Handler '{handler_name}' not in registry")
            return func.HttpResponse(
                json.dumps({
                    "status": "ERROR",
                    "message": f"Handler '{handler_name}' not found in registry",
                    "trace_id": trace_id,
                    "processing_time_ms": round(elapsed, 2)
                }),
                status_code=500,
                mimetype="application/json"
            )
        
        # Dispatch to handler
        logger.info(f"[{trace_id}] Dispatching to '{handler_name}' for {logical_sheet}")
        result = handler_func(event)
        
        elapsed = (time.time() - start_time) * 1000
        result.processing_time_ms = elapsed
        
        logger.info(f"[{trace_id}] Handler '{handler_name}' completed: {result.status}")
        
        # Return result
        # SOTA: EXCEPTION_LOGGED means exception was already logged - ack message (no retry)
        success_statuses = ("OK", "ALREADY_PROCESSED", "EXCEPTION_LOGGED")
        return func.HttpResponse(
            json.dumps(result.model_dump()),
            status_code=200 if result.status in success_statuses else 422,
            mimetype="application/json"
        )
        
    except Exception as e:
        elapsed = (time.time() - start_time) * 1000
        logger.exception(f"[{trace_id}] Unexpected error: {e}")
        return func.HttpResponse(
            json.dumps({
                "status": "ERROR",
                "message": str(e),
                "trace_id": trace_id,
                "processing_time_ms": round(elapsed, 2)
            }),
            status_code=500,
            mimetype="application/json"
        )
