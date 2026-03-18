"""fn_submit_stock: Submit Stock Count | POST /api/submission/stock"""
import logging, json, azure.functions as func, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared import get_smartsheet_client, generate_trace_id, StockSubmission, SubmissionResult
logger = logging.getLogger(__name__)

def main(req: func.HttpRequest) -> func.HttpResponse:
    trace_id = generate_trace_id()
    try:
        body = req.get_json()
        submission = StockSubmission(**body)
        if submission.trace_id: trace_id = submission.trace_id
        logger.info(f"[{trace_id}] Stock submission for plant {submission.plant}")
        
        # Simplified: Write stock counts to INVENTORY_SNAPSHOT (user will enhance)
        client = get_smartsheet_client()
        
        result = SubmissionResult(
            warnings=[],
            errors=[],
            trace_id=trace_id
        )
        
        logger.info(f"[{trace_id}] Stock submission placeholder complete")
        return func.HttpResponse(result.model_dump_json(), status_code=200, mimetype="application/json")
    except Exception as e:
        logger.exception(f"[{trace_id}] Error: {e}")
        try:
            from shared.audit import create_exception
            from shared.models import ReasonCode, ExceptionSeverity, ExceptionSource
            create_exception(
                client=client,
                trace_id=trace_id,
                reason_code=ReasonCode.SYSTEM_ERROR,
                severity=ExceptionSeverity.CRITICAL,
                source=ExceptionSource.ALLOCATION,
                message=f"fn_submit_stock unhandled error: {str(e)}",
            )
        except Exception:
            logger.error(f"[{trace_id}] Failed to create exception record")
        return func.HttpResponse(json.dumps({"error": {"code": "SERVER_ERROR", "message": str(e)}, "trace_id": trace_id}), status_code=500, mimetype="application/json")
