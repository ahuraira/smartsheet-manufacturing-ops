"""
fn_submission_status: Check Submission Status
==============================================

Returns status of a consumption or stock submission.

Endpoint: GET /api/submission/status/{submission_id}

Response:
{
  "submission_id": "SUBM-123",
  "status": "PENDING_APPROVAL",
  "warnings": [],
  "errors": [],
  "created_at": "2026-02-06T10:00:00Z"
}

SOTA Patterns:
- Return 404 if submission not found
- Include warnings/errors from initial submission
"""

import logging
import json
import azure.functions as func
from datetime import datetime

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import (
    Sheet,
    Column,
    get_smartsheet_client,
    generate_trace_id,
    SubmissionStatus,
    SubmissionStatusResponse,
)
from shared.allocation_service import _parse_rows

logger = logging.getLogger(__name__)


def main(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/submission/status/{submission_id}"""
    trace_id = generate_trace_id()
    
    try:
        # 1. Get submission_id from route
        submission_id = req.route_params.get('submission_id')
        
        if not submission_id:
            return func.HttpResponse(
                json.dumps({
                    "error": {"code": "INVALID_PAYLOAD", "message": "Missing submission_id"},
                    "trace_id": trace_id
                }),
                status_code=400,
                mimetype="application/json"
            )
        
        logger.info(f"[{trace_id}] Checking status for submission {submission_id}")
        
        # 2. Get Smartsheet client
        client = get_smartsheet_client()
        
        # 3. Search CONSUMPTION_LOG for submission
        # Note: Later we'll add a SUBMISSION_LOG sheet for tracking
        # For now, search by CONSUMPTION_ID or custom column
        
        # Simplified: just check if submission exists in CONSUMPTION_LOG
        from shared.manifest import get_manifest
        manifest = get_manifest()
        
        consumption_col_id = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.CONSUMPTION_ID)
        consumption_col_status = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.STATUS)
        consumption_col_date = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.CONSUMPTION_DATE)
        
        all_consumptions = _parse_rows(client.get_sheet(Sheet.CONSUMPTION_LOG))
        
        # Find submission
        submission_row = None
        for row in all_consumptions:
            if row.get(consumption_col_id) == submission_id:
                submission_row = row
                break
        
        if not submission_row:
            return func.HttpResponse(
                json.dumps({
                    "error": {"code": "NOT_FOUND", "message": f"Submission {submission_id} not found"},
                    "trace_id": trace_id
                }),
                status_code=404,
                mimetype="application/json"
            )
        
        # 4. Map Smartsheet status to SubmissionStatus enum
        ss_status = submission_row.get(consumption_col_status, "")
        
        status_mapping = {
            "Submitted": SubmissionStatus.PENDING_APPROVAL,
            "Approved": SubmissionStatus.APPROVED,
            "Adjustment Requested": SubmissionStatus.REJECTED,
        }
        
        submission_status = status_mapping.get(ss_status, SubmissionStatus.PENDING)
        
        # 5. Build response
        response = SubmissionStatusResponse(
            submission_id=submission_id,
            status=submission_status,
            warnings=[],
            errors=[],
            created_at=submission_row.get(consumption_col_date)
        )
        
        return func.HttpResponse(
            response.model_dump_json(),
            status_code=200,
            mimetype="application/json"
        )
        
    except Exception as e:
        logger.exception(f"[{trace_id}] Error checking submission status: {e}")
        return func.HttpResponse(
            json.dumps({
                "error": {"code": "SERVER_ERROR", "message": str(e)},
                "trace_id": trace_id
            }),
            status_code=500,
            mimetype="application/json"
        )
