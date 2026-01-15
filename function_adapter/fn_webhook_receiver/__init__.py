import logging
import json
import os
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List

import azure.functions as func

# Import shared modules
try:
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from shared.webhook_config import is_system_actor, init_config
    from shared.event_log import insert_event_stub, event_exists
    from shared.service_bus import send_event
    
    # Pre-initialize manifest during module load (reduces cold start latency)
    # Wrapped in try/except to prevent deployment health check failures
    try:
        init_config()
    except Exception as init_err:
        # Log but don't crash - manifest can be loaded later on first request
        logging.warning(f"Manifest pre-load failed (will retry on first request): {init_err}")
    
except ImportError as e:
    # Fallbacks for syntax checking if modules missing
    logging.warning(f"Module import failed: {e}")
    def is_system_actor(a): return False
    def insert_event_stub(*args, **kwargs): return True
    def event_exists(a): return False
    def send_event(*args, **kwargs): return True

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def generate_trace_id() -> str:
    """Generate a unique trace ID for request tracking."""
    return f"trace-{uuid.uuid4().hex[:12]}"


def generate_event_id(webhook_id: str, timestamp: str, index: int) -> str:
    """
    Generate a stable event ID for idempotency.
    Format: sm_{webhook_id}_{timestamp}_{index}
    """
    # Clean timestamp to remove special characters
    clean_ts = timestamp.replace(":", "").replace("-", "").replace("T", "").replace("Z", "").replace(".", "")[:17]
    return f"sm_{webhook_id}_{clean_ts}_{index}"


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    Webhook receiver endpoint.
    
    Handles:
    1. Verification challenge (during webhook registration)
    2. Normal event callbacks
    
    Returns 200 quickly to acknowledge receipt.
    """
    trace_id = generate_trace_id()
    logger.info(f"[{trace_id}] Webhook received from {req.headers.get('x-forwarded-for', 'unknown')}")
    
    try:
        # =================================================================
        # STEP 1: VERIFICATION CHALLENGE
        # =================================================================
        # Check header first (primary method)
        challenge = req.headers.get('Smartsheet-Hook-Challenge')
        
        # Also check body (backup method per Smartsheet docs)
        if not challenge:
            try:
                body = req.get_json()
                challenge = body.get('challenge')
            except:
                pass
        
        if challenge:
            logger.info(f"[{trace_id}] Verification challenge received: {challenge[:20]}...")
            
            # Respond with the challenge in header AND body
            return func.HttpResponse(
                body=json.dumps({
                    "smartsheetHookResponse": challenge
                }),
                status_code=200,
                headers={
                    "Smartsheet-Hook-Response": challenge,
                    "Content-Type": "application/json"
                }
            )
        
        # =================================================================
        # STEP 2: PARSE EVENT CALLBACK
        # =================================================================
        try:
            body = req.get_json()
        except Exception as e:
            logger.error(f"[{trace_id}] Failed to parse JSON body: {e}")
            return func.HttpResponse(
                body=json.dumps({"error": "Invalid JSON"}),
                status_code=400,
                mimetype="application/json"
            )
        
        webhook_id = str(body.get("webhookId", "unknown"))
        scope = body.get("scope", "")
        scope_object_id = str(body.get("scopeObjectId", ""))
        timestamp = body.get("timestamp", datetime.utcnow().isoformat())
        events = body.get("events", [])
        
        logger.info(f"[{trace_id}] Received {len(events)} events from webhook {webhook_id}")
        
        if not events:
            return func.HttpResponse(
                body=json.dumps({"status": "OK", "message": "No events to process"}),
                status_code=200,
                mimetype="application/json"
            )
        
        # =================================================================
        # STEP 3: PROCESS EACH EVENT
        # =================================================================
        processed_count = 0
        skipped_count = 0
        
        for idx, event in enumerate(events):
            event_id = generate_event_id(webhook_id, timestamp, idx)
            
            object_type = event.get("objectType", "unknown")
            event_type = event.get("eventType", "unknown")  # created, updated, deleted
            
            # Row ID extraction:
            # - For "row" events: the row ID is in event.id
            # - For "cell" events: the row ID is in event.rowId
            # - For "attachment"/"comment" events: may have rowId if attached to row
            if object_type == "row":
                row_id = str(event.get("id", "")) if event.get("id") else None
            else:
                row_id = str(event.get("rowId", "")) if event.get("rowId") else None
            
            column_id = str(event.get("columnId", "")) if event.get("columnId") else None
            user_id = event.get("userId")  # numeric user ID
            
            # System Actor Check - skip loop
            if is_system_actor(user_id):
                logger.info(f"[{trace_id}] Skipping system actor event: {user_id}")
                skipped_count += 1
                continue

            # =================================================================
            # EVENT TYPE FILTER
            # =================================================================
            # Smartsheet sends VERY granular events:
            # - sheet.updated: Not useful (just means something changed)
            # - cell.created/updated: Too granular (one per cell!)
            # - row.created/updated/deleted: What we actually want
            # - attachment.created: Useful for file uploads
            # - comment.created: Usually not needed
            #
            # We only process ROW and ATTACHMENT events to avoid flood.
            # =================================================================
            
            ALLOWED_OBJECT_TYPES = {"row", "attachment"}
            
            if object_type not in ALLOWED_OBJECT_TYPES:
                logger.debug(f"[{trace_id}] Skipping {object_type}.{event_type} (filtered)")
                skipped_count += 1
                continue

            # Idempotency Check
            if event_exists(event_id):
                 logger.info(f"[{trace_id}] Event {event_id} already exists (duplicate delivery)")
                 skipped_count += 1
                 continue

            logger.info(f"[{trace_id}] Processing event {idx}: {object_type}.{event_type} row={row_id}")
            
            # Build canonical event message
            canonical_event = {
                "event_id": event_id,
                "source": "SMARTSHEET",
                "webhook_id": webhook_id,
                "sheet_id": scope_object_id,
                "row_id": row_id,
                "column_id": column_id,
                "object_type": object_type,
                "action": event_type.upper() if event_type else "UNKNOWN",
                "timestamp_utc": timestamp,
                "actor": user_id,
                "trace_id": trace_id,
                "raw_event": event
            }
            
            # Insert Stub (Log to DB)
            db_success = insert_event_stub(
                event_id=event_id,
                source="SMARTSHEET",
                webhook_id=webhook_id,
                sheet_id=scope_object_id,
                row_id=row_id,
                column_id=column_id,
                object_type=object_type,
                action=canonical_event["action"],
                payload=event,
                trace_id=trace_id
            )
            
            if not db_success:
                # If DB insert failed (and check failed), we might proceed anyway 
                # but it's risky for duplicate processing. 
                # Assuming DB failure means we should probably still try to enqueue 
                # to ensure we don't drop the event.
                logger.warning(f"[{trace_id}] DB insert failed for {event_id}, invoking fail-open")

            # Enqueue to Service Bus
            sb_success = send_event(canonical_event)
            
            if sb_success:
                processed_count += 1
            else:
                logger.error(f"[{trace_id}] Failed to enqueue event {event_id}")
                # We failed to enqueue. If we inserted into DB, we have a "PENDING" event 
                # that will never move. This is a partial failure state. 
                # Without SB, we technically failed this event.
        
        # =================================================================
        # STEP 4: RETURN SUCCESS
        # =================================================================
        return func.HttpResponse(
            body=json.dumps({
                "status": "OK",
                "trace_id": trace_id,
                "processed": processed_count,
                "skipped": skipped_count
            }),
            status_code=200,
            mimetype="application/json"
        )
    
    except Exception as e:
        logger.exception(f"[{trace_id}] Unexpected error: {e}")
        return func.HttpResponse(
            body=json.dumps({
                "status": "ERROR",
                "trace_id": trace_id,
                "error": str(e)
            }),
            status_code=200,
            mimetype="application/json"
        )
