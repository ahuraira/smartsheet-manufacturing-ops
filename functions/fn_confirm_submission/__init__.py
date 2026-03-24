"""
fn_confirm_submission: Approve/Reject Consumption Submission
=============================================================

Allows supervisor to approve or reject a pending consumption submission.

Endpoint: POST /api/submission/confirm

Request:
{
  "processed_submission_id": "SUBM-123",
  "approver": "supervisor@company.com",
  "decision": "APPROVE" | "REJECT",
  "notes": "..."
}

Response:
{
  "status": "OK",
  "submission_id": "SUBM-123",
  "trace_id": "..."
}

SOTA Patterns:
- Update status in CONSUMPTION_LOG
- Record approver and decision
- Rejected submissions stay as REJECTED (not deleted)
"""

import logging
import json
import azure.functions as func

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import (
    Sheet,
    Column,
    get_smartsheet_client,
    generate_trace_id,
    SubmissionConfirmRequest,
)
from shared.allocation_service import _parse_rows
from shared.queue_lock import AllocationLock
from shared.audit import log_user_action, create_exception
from shared.models import ActionType, ExceptionSeverity, ExceptionSource, ReasonCode
from shared.helpers import resolve_user_email

logger = logging.getLogger(__name__)


def main(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/submission/confirm"""
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
            request = SubmissionConfirmRequest(**body)
        except Exception as e:
            return func.HttpResponse(
                json.dumps({
                    "error": {"code": "INVALID_PAYLOAD", "message": str(e)},
                    "trace_id": trace_id
                }),
                status_code=400,
                mimetype="application/json"
            )
        
        if request.trace_id:
            trace_id = request.trace_id
        
        logger.info(
            f"[{trace_id}] Confirm submission {request.processed_submission_id}: "
            f"{request.decision} by {request.approver}"
        )
        
        # 2. Get Smartsheet client
        client = get_smartsheet_client()
        request.approver = resolve_user_email(client, request.approver)

        # 3. Find submission in CONSUMPTION_LOG
        from shared.manifest import get_manifest
        manifest = get_manifest()
        
        col_cons_id = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.CONSUMPTION_ID)
        col_status = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.STATUS)
        col_remarks = manifest.get_column_name(Sheet.CONSUMPTION_LOG, Column.CONSUMPTION_LOG.REMARKS)
        
        # Lock on submission ID to prevent concurrent approve/reject races
        with AllocationLock([request.processed_submission_id], timeout_ms=60000, trace_id=trace_id) as lock:
            if not lock.success:
                return func.HttpResponse(
                    json.dumps({
                        "error": {"code": "LOCK_TIMEOUT", "message": "Failed to acquire lock on submission"},
                        "trace_id": trace_id
                    }),
                    status_code=409,
                    mimetype="application/json"
                )

            all_consumptions = _parse_rows(client.get_sheet(Sheet.CONSUMPTION_LOG))

            submission_rows = []
            for cons in all_consumptions:
                # Match by CONSUMPTION_ID or check remarks for submission_id
                remarks = cons.get(col_remarks, "")
                if request.processed_submission_id in remarks or cons.get(col_cons_id) == request.processed_submission_id:
                    submission_rows.append(cons)

            if not submission_rows:
                try:
                    create_exception(
                        client=client,
                        trace_id=trace_id,
                        reason_code=ReasonCode.TAG_NOT_FOUND,
                        severity=ExceptionSeverity.HIGH,
                        source=ExceptionSource.ALLOCATION,
                        message=f"fn_confirm_submission: Submission {request.processed_submission_id} not found in CONSUMPTION_LOG",
                    )
                except Exception:
                    logger.error(f"[{trace_id}] Failed to create exception record")
                return func.HttpResponse(
                    json.dumps({
                        "error": {"code": "NOT_FOUND", "message": f"Submission {request.processed_submission_id} not found"},
                        "trace_id": trace_id
                    }),
                    status_code=404,
                    mimetype="application/json"
                )

            # 4. Idempotency check — if already at target status, return success
            target_status = "Approved" if request.decision == "APPROVE" else "Adjustment Requested"
            current_status = submission_rows[0].get(col_status)
            if current_status == target_status:
                return func.HttpResponse(
                    json.dumps({
                        "status": "ALREADY_PROCESSED",
                        "message": f"Submission already {target_status}",
                        "trace_id": trace_id
                    }),
                    status_code=200,
                    mimetype="application/json"
                )

            # 5. Update status
            new_status = target_status

            for row in submission_rows:
                row_id = row["id"]  # Smartsheet row ID

                updates = {
                    Column.CONSUMPTION_LOG.STATUS: new_status
                }

                if request.notes:
                    current_remarks = row.get(col_remarks, "")
                    updates[Column.CONSUMPTION_LOG.REMARKS] = f"{current_remarks} | Approver: {request.approver} - {request.notes}"

                client.update_row(Sheet.CONSUMPTION_LOG, row_id, updates)

                # Audit log each status change — use human-readable CON-xxxx ID
                consumption_id = row.get(col_cons_id) or request.processed_submission_id
                log_user_action(
                    client=client,
                    user_id=request.approver,
                    action_type=ActionType.LPO_UPDATED,
                    target_table="CONSUMPTION_LOG",
                    target_id=str(consumption_id),
                    old_value=row.get(col_status, ""),
                    new_value=new_status,
                    notes=f"Decision: {request.decision}. {request.notes or ''}",
                    trace_id=trace_id,
                )

            logger.info(f"[{trace_id}] Updated {len(submission_rows)} rows to {new_status}")
        
        # 5. Return success
        return func.HttpResponse(
            json.dumps({
                "status": "OK",
                "submission_id": request.processed_submission_id,
                "new_status": new_status,
                "trace_id": trace_id
            }),
            status_code=200,
            mimetype="application/json"
        )
        
    except Exception as e:
        logger.exception(f"[{trace_id}] Error confirming submission: {e}")
        try:
            create_exception(
                client=client,
                trace_id=trace_id,
                reason_code=ReasonCode.SYSTEM_ERROR,
                severity=ExceptionSeverity.CRITICAL,
                source=ExceptionSource.ALLOCATION,
                message=f"fn_confirm_submission unhandled error: {str(e)}",
            )
        except Exception:
            logger.error(f"[{trace_id}] Failed to create exception record")
        return func.HttpResponse(
            json.dumps({
                "error": {"code": "SERVER_ERROR", "message": str(e)},
                "trace_id": trace_id
            }),
            status_code=500,
            mimetype="application/json"
        )
