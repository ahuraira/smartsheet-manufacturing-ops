"""
fn_webhook_admin: Webhook Administration Endpoints
===================================================

Provides admin endpoints for managing Smartsheet webhooks:
- POST /api/webhooks/register - Register a new webhook
- GET /api/webhooks - List all webhooks
- DELETE /api/webhooks/{id} - Delete a webhook
- POST /api/webhooks/refresh - Refresh/re-enable webhooks

These endpoints call the Smartsheet API to manage webhook subscriptions.

Smartsheet Webhook Lifecycle:
1. Create webhook (POST /webhooks) - status: NEW_NOT_VERIFIED
2. Enable webhook (PUT /webhooks/{id}) - triggers verification
3. Smartsheet sends verification challenge to callbackUrl
4. On success, webhook status: ENABLED
5. Webhook receives callbacks for specified scope/events

References:
- https://developers.smartsheet.com/api/smartsheet/guides/webhooks/launch-a-webhook
"""

import logging
import json
import os
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from typing import Optional, Dict, Any, List

import azure.functions as func

logger = logging.getLogger(__name__)

# Smartsheet API configuration
SMARTSHEET_BASE_URL = os.getenv("SMARTSHEET_BASE_URL", "https://api.smartsheet.eu/2.0")
SMARTSHEET_API_KEY = os.getenv("SMARTSHEET_API_KEY", "")
WEBHOOK_CALLBACK_URL = os.getenv("WEBHOOK_CALLBACK_URL", "")

# Retry settings
MAX_RETRIES = 3
BACKOFF_FACTOR = 1.0

# Singleton session
_session = None


def get_session() -> requests.Session:
    """
    Get a requests session with retry logic built-in.
    
    Retries on:
    - 429: Too Many Requests (rate limit)
    - 500, 502, 503, 504: Server errors
    """
    global _session
    
    if _session is None:
        _session = requests.Session()
        
        retry_strategy = Retry(
            total=MAX_RETRIES,
            backoff_factor=BACKOFF_FACTOR,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["POST", "GET", "PUT", "DELETE"]
        )
        
        adapter = HTTPAdapter(max_retries=retry_strategy)
        _session.mount("http://", adapter)
        _session.mount("https://", adapter)
    
    return _session


def get_headers() -> Dict[str, str]:
    """Get headers for Smartsheet API requests."""
    return {
        "Authorization": f"Bearer {SMARTSHEET_API_KEY}",
        "Content-Type": "application/json"
    }


def create_webhook(sheet_id: int, name: str) -> Dict[str, Any]:
    """
    Create a new webhook for a sheet.
    
    Smartsheet API: POST /webhooks
    
    Args:
        sheet_id: The Smartsheet sheet ID to monitor
        name: A descriptive name for the webhook
    
    Returns:
        The created webhook object
    """
    url = f"{SMARTSHEET_BASE_URL}/webhooks"
    
    payload = {
        "name": name,
        "callbackUrl": WEBHOOK_CALLBACK_URL,
        "scope": "sheet",
        "scopeObjectId": sheet_id,
        "events": ["*.*"],  # All events - we filter in the receiver
        "version": 1
    }
    
    response = get_session().post(url, headers=get_headers(), json=payload)
    response.raise_for_status()
    
    result = response.json()
    return result.get("result", result)


def enable_webhook(webhook_id: int) -> Dict[str, Any]:
    """
    Enable a webhook (triggers verification).
    
    Smartsheet API: PUT /webhooks/{webhookId}
    
    Args:
        webhook_id: The webhook ID to enable
    
    Returns:
        The updated webhook object
    """
    url = f"{SMARTSHEET_BASE_URL}/webhooks/{webhook_id}"
    
    payload = {
        "enabled": True
    }
    
    response = get_session().put(url, headers=get_headers(), json=payload)
    response.raise_for_status()
    
    result = response.json()
    return result.get("result", result)


def list_webhooks() -> List[Dict[str, Any]]:
    """
    List all webhooks for the current user.
    
    Smartsheet API: GET /webhooks
    
    Returns:
        List of webhook objects
    """
    url = f"{SMARTSHEET_BASE_URL}/webhooks"
    
    response = get_session().get(url, headers=get_headers())
    response.raise_for_status()
    
    result = response.json()
    return result.get("data", [])


def delete_webhook(webhook_id: int) -> bool:
    """
    Delete a webhook.
    
    Smartsheet API: DELETE /webhooks/{webhookId}
    
    Args:
        webhook_id: The webhook ID to delete
    
    Returns:
        True if successful
    """
    url = f"{SMARTSHEET_BASE_URL}/webhooks/{webhook_id}"
    
    response = get_session().delete(url, headers=get_headers())
    response.raise_for_status()
    
    return True


def get_webhook(webhook_id: int) -> Dict[str, Any]:
    """
    Get a specific webhook.
    
    Smartsheet API: GET /webhooks/{webhookId}
    
    Args:
        webhook_id: The webhook ID
    
    Returns:
        The webhook object
    """
    url = f"{SMARTSHEET_BASE_URL}/webhooks/{webhook_id}"
    
    response = get_session().get(url, headers=get_headers())
    response.raise_for_status()
    
    result = response.json()
    return result.get("result", result)


# =============================================================================
# HTTP HANDLERS
# =============================================================================

def handle_register(req: func.HttpRequest) -> func.HttpResponse:
    """
    Register a new webhook for a sheet.
    
    POST /api/webhooks/register
    Body: { "sheet_id": 123456, "name": "My Webhook" }
    """
    try:
        body = req.get_json()
        sheet_id = body.get("sheet_id")
        name = body.get("name", f"Webhook for sheet {sheet_id}")
        
        if not sheet_id:
            return func.HttpResponse(
                json.dumps({"error": "sheet_id is required"}),
                status_code=400,
                mimetype="application/json"
            )
        
        # Step 1: Create the webhook
        webhook = create_webhook(sheet_id, name)
        webhook_id = webhook.get("id")
        
        logger.info(f"Created webhook {webhook_id} for sheet {sheet_id}")
        
        # Step 2: Enable it (triggers verification)
        try:
            enabled_webhook = enable_webhook(webhook_id)
            logger.info(f"Enabled webhook {webhook_id}, status: {enabled_webhook.get('status')}")
        except Exception as enable_err:
            logger.warning(f"Could not enable webhook {webhook_id}: {enable_err}")
            enabled_webhook = webhook
        
        return func.HttpResponse(
            json.dumps({
                "status": "created",
                "webhook": enabled_webhook,
                "message": "Webhook created. Check status - should be ENABLED after verification."
            }),
            status_code=201,
            mimetype="application/json"
        )
    
    except requests.HTTPError as e:
        logger.error(f"Smartsheet API error: {e.response.text}")
        return func.HttpResponse(
            json.dumps({"error": str(e), "details": e.response.text}),
            status_code=e.response.status_code,
            mimetype="application/json"
        )
    except Exception as e:
        logger.exception(f"Error registering webhook: {e}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json"
        )


def handle_list(req: func.HttpRequest) -> func.HttpResponse:
    """
    List all webhooks.
    
    GET /api/webhooks
    """
    try:
        webhooks = list_webhooks()
        
        return func.HttpResponse(
            json.dumps({
                "count": len(webhooks),
                "webhooks": webhooks
            }),
            status_code=200,
            mimetype="application/json"
        )
    
    except requests.HTTPError as e:
        logger.error(f"Smartsheet API error: {e.response.text}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=e.response.status_code,
            mimetype="application/json"
        )
    except Exception as e:
        logger.exception(f"Error listing webhooks: {e}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json"
        )


def handle_delete(req: func.HttpRequest, webhook_id: str) -> func.HttpResponse:
    """
    Delete a webhook.
    
    DELETE /api/webhooks/{id}
    """
    try:
        delete_webhook(int(webhook_id))
        
        return func.HttpResponse(
            json.dumps({"status": "deleted", "webhook_id": webhook_id}),
            status_code=200,
            mimetype="application/json"
        )
    
    except requests.HTTPError as e:
        logger.error(f"Smartsheet API error: {e.response.text}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=e.response.status_code,
            mimetype="application/json"
        )
    except Exception as e:
        logger.exception(f"Error deleting webhook: {e}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json"
        )


def handle_refresh(req: func.HttpRequest) -> func.HttpResponse:
    """
    Refresh/re-enable all webhooks.
    
    POST /api/webhooks/refresh
    """
    try:
        webhooks = list_webhooks()
        results = []
        
        for wh in webhooks:
            wh_id = wh.get("id")
            status = wh.get("status")
            
            if status != "ENABLED":
                try:
                    enabled = enable_webhook(wh_id)
                    results.append({
                        "id": wh_id,
                        "name": wh.get("name"),
                        "action": "enabled",
                        "new_status": enabled.get("status")
                    })
                except Exception as e:
                    results.append({
                        "id": wh_id,
                        "name": wh.get("name"),
                        "action": "failed",
                        "error": str(e)
                    })
            else:
                results.append({
                    "id": wh_id,
                    "name": wh.get("name"),
                    "action": "skipped",
                    "reason": "Already enabled"
                })
        
        return func.HttpResponse(
            json.dumps({
                "status": "refreshed",
                "results": results
            }),
            status_code=200,
            mimetype="application/json"
        )
    
    except Exception as e:
        logger.exception(f"Error refreshing webhooks: {e}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json"
        )


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    Main router for webhook admin endpoints.
    """
    method = req.method
    route = req.route_params.get("action", "")
    
    logger.info(f"Admin request: {method} /api/webhooks/{route}")
    
    if method == "POST" and route == "register":
        return handle_register(req)
    elif method == "POST" and route == "refresh":
        return handle_refresh(req)
    elif method == "GET" and not route:
        return handle_list(req)
    elif method == "DELETE" and route:
        return handle_delete(req, route)
    else:
        return func.HttpResponse(
            json.dumps({"error": f"Unknown route: {method} /api/webhooks/{route}"}),
            status_code=404,
            mimetype="application/json"
        )
