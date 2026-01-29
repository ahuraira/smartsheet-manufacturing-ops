"""
Material Mapping Lookup Function
=================================

HTTP trigger for looking up canonical material mappings.

Endpoint: POST /api/map/lookup

Request:
{
    "nesting_description": "aluminum tape",
    "lpo_id": "LPO-555",           // optional
    "project_id": "PRJ-001",       // optional
    "ingest_line_id": "uuid",      // optional, auto-generated if missing
    "trace_id": "uuid"             // optional, auto-generated if missing
}

Response (success):
{
    "success": true,
    "decision": "AUTO",
    "canonical_code": "CAN_CONS_AL_TAPE",
    "sap_code": "UL181AFST",
    "uom": "m",
    "sap_uom": "roll",
    "conversion_factor": 30.0,
    "not_tracked": false,
    "history_id": "abc123"
}

Response (no match):
{
    "success": false,
    "decision": "REVIEW",
    "exception_id": "MAPEX-abc123",
    "error": "No mapping found for: aluminum tape"
}
"""

import json
import logging
import azure.functions as func
from uuid import uuid4

# Import shared modules
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.smartsheet_client import SmartsheetClient
from shared.audit import create_exception
from .mapping_service import MappingService, MappingResult

logger = logging.getLogger(__name__)


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    HTTP trigger for material mapping lookup.
    
    POST /api/map/lookup
    """
    trace_id = req.headers.get("X-Trace-ID", str(uuid4()))
    
    logger.info(f"[{trace_id}] Material mapping lookup request received")
    
    try:
        # Parse request body
        try:
            body = req.get_json()
        except ValueError as e:
            return _error_response(
                "Invalid JSON in request body",
                trace_id,
                status_code=400
            )
        
        # Validate required fields
        nesting_description = body.get("nesting_description")
        if not nesting_description:
            return _error_response(
                "Missing required field: nesting_description",
                trace_id,
                status_code=400
            )
        
        # Extract optional fields
        lpo_id = body.get("lpo_id")
        project_id = body.get("project_id")
        customer_id = body.get("customer_id")
        ingest_line_id = body.get("ingest_line_id", str(uuid4()))
        
        # Initialize service
        client = SmartsheetClient()
        service = MappingService(client)
        
        # Perform lookup
        result = service.lookup(
            nesting_description=nesting_description,
            lpo_id=lpo_id,
            project_id=project_id,
            customer_id=customer_id,
            ingest_line_id=ingest_line_id,
            trace_id=trace_id,
        )
        
        # Build response
        response_data = _build_response(result, trace_id)
        
        status_code = 200 if result.success else 404
        
        logger.info(
            f"[{trace_id}] Mapping lookup complete: "
            f"decision={result.decision}, canonical={result.canonical_code}"
        )
        
        return func.HttpResponse(
            body=json.dumps(response_data),
            status_code=status_code,
            mimetype="application/json",
            headers={"X-Trace-ID": trace_id}
        )
        
    except Exception as e:
        logger.exception(f"[{trace_id}] Error in mapping lookup: {e}")
        
        # Log exception to audit system
        exception_id = None
        try:
            client = SmartsheetClient()
            exception_id = create_exception(
                client=client,
                error_type="MAPPING_LOOKUP_ERROR",
                error_message=str(e),
                trace_id=trace_id,
                context={"endpoint": "/api/map/lookup"}
            )
        except Exception:
            pass
        
        return _error_response(
            f"Internal server error: {str(e)}",
            trace_id,
            status_code=500,
            exception_id=exception_id
        )


def _build_response(result: MappingResult, trace_id: str) -> dict:
    """Build HTTP response from mapping result."""
    response = {
        "success": result.success,
        "decision": result.decision,
        "trace_id": trace_id,
    }
    
    if result.canonical_code:
        response["canonical_code"] = result.canonical_code
    if result.sap_code:
        response["sap_code"] = result.sap_code
    if result.uom:
        response["uom"] = result.uom
    if result.sap_uom:
        response["sap_uom"] = result.sap_uom
    if result.conversion_factor is not None:
        response["conversion_factor"] = result.conversion_factor
    if result.not_tracked:
        response["not_tracked"] = result.not_tracked
    if result.history_id:
        response["history_id"] = result.history_id
    if result.exception_id:
        response["exception_id"] = result.exception_id
    if result.error:
        response["error"] = result.error
    
    return response


def _error_response(
    message: str,
    trace_id: str,
    status_code: int = 500,
    exception_id: str = None
) -> func.HttpResponse:
    """Build error response."""
    response_data = {
        "success": False,
        "error": message,
        "trace_id": trace_id,
    }
    
    if exception_id:
        response_data["exception_id"] = exception_id
    
    return func.HttpResponse(
        body=json.dumps(response_data),
        status_code=status_code,
        mimetype="application/json",
        headers={"X-Trace-ID": trace_id}
    )
