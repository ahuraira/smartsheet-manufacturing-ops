"""
fn_submit_consumption: Submit Material Consumption
==================================================

Accepts consumption submission from Power Automate Teams cards.

Endpoint: POST /api/submission/consumption

Request:
{
  "submission_id": "GUID",
  "user": "user@company.com",
  "plant": "PLANT-A",
  "shift": "Morning",
  "allocation_ids": ["A-123"],
  "lines": [
    {
      "canonical_code": "MAT-001",
      "allocated_qty": 100.0,
      "actual_qty": 95.0,
      "uom": "SQM",
      "remarks": "..."
    }
  ]
}

Response:
{
  "status": "OK" | "WARN" | "ERROR",
  "processed_submission_id": "GUID",
  "warnings": [...],
  "errors": [...],
  "trace_id": "..."
}

CRITICAL:
- Distributed locking to prevent race conditions
- Idempotency via submission_id
- Variance validation with configurable thresholds
"""

import logging
import json
import azure.functions as func

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import (
    get_smartsheet_client,
    generate_trace_id,
    ConsumptionSubmission,
)
from shared.consumption_service import submit_consumption

logger = logging.getLogger(__name__)


def main(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/submission/consumption"""
    trace_id = generate_trace_id()
    
    try:
        # 1. Parse and validate request
        try:
            body = req.get_json()
        except ValueError:
            return func.HttpResponse(
                json.dumps({
                    "error": {"code": "INVALID_PAYLOAD", "message": "Invalid JSON"},
                    "trace_id": trace_id
                }),
                status_code=400,
                mimetype="application/json"
            )
        
        try:
            submission = ConsumptionSubmission(**body)
        except Exception as e:
            logger.error(f"[{trace_id}] Validation error: {e}")
            return func.HttpResponse(
                json.dumps({
                    "error": {"code": "INVALID_PAYLOAD", "message": str(e)},
                    "trace_id": trace_id
                }),
                status_code=400,
                mimetype="application/json"
            )
        
        if submission.trace_id:
            trace_id = submission.trace_id
        
        logger.info(
            f"[{trace_id}] Consumption submission: {submission.submission_id} "
            f"by {submission.user} for {len(submission.allocation_ids)} allocations"
        )
        
        # 2. Get Smartsheet client
        client = get_smartsheet_client()
        
        # 3. Submit consumption (handles locking, validation, writes)
        result = submit_consumption(client, submission, trace_id)
        
        # 4. Map to HTTP status code
        status_code = 200
        if result.status == "ERROR":
            status_code = 400  # Validation errors
        
        logger.info(f"[{trace_id}] Submission result: {result.status}")
        
        return func.HttpResponse(
            result.model_dump_json(),
            status_code=status_code,
            mimetype="application/json"
        )
        
    except Exception as e:
        logger.exception(f"[{trace_id}] Error submitting consumption: {e}")
        return func.HttpResponse(
            json.dumps({
                "error": {"code": "SERVER_ERROR", "message": str(e)},
                "trace_id": trace_id
            }),
            status_code=500,
            mimetype="application/json"
        )
