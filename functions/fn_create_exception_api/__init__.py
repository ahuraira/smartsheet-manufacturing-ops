"""fn_create_exception_api: Create Exception Programmatically | POST /api/exception"""
import logging, json, azure.functions as func, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared import get_smartsheet_client, generate_trace_id, ExceptionCreateRequest, ExceptionCreateResponse, create_exception
logger = logging.getLogger(__name__)

def main(req: func.HttpRequest) -> func.HttpResponse:
    trace_id = generate_trace_id()
    try:
        body = req.get_json()
        request = ExceptionCreateRequest(**body)
        if request.trace_id: trace_id = request.trace_id
        
        client = get_smartsheet_client()
        
        # Use existing create_exception helper
        exception_id = create_exception(
            client=client,
            exception_type=request.type,
            reference_id=request.reference,
            severity=request.severity.value,
            message=request.note or "",
            trace_id=trace_id
        )
        
        response = ExceptionCreateResponse(exception_id=exception_id, trace_id=trace_id)
        logger.info(f"[{trace_id}] Created exception {exception_id}")
        return func.HttpResponse(response.model_dump_json(), status_code=201, mimetype="application/json")
    except Exception as e:
        logger.exception(f"[{trace_id}] Error: {e}")
        return func.HttpResponse(json.dumps({"error": {"code": "SERVER_ERROR", "message": str(e)}, "trace_id": trace_id}), status_code=500, mimetype="application/json")
