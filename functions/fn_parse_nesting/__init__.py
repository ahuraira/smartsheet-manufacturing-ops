"""
Azure Function: fn_parse_nesting
================================

HTTP-triggered function for parsing Eurosoft CutExpert nesting files.
Designed to be called from Power Automate when files are uploaded to SharePoint.

Endpoints:
    POST /api/nesting/parse - Parse an Excel file and return structured JSON

Request Format:
    Content-Type: multipart/form-data
    Body: file attachment (Excel .xls or .xlsx)
    
    OR
    
    Content-Type: application/json
    Body: {"file_content_base64": "...", "filename": "example.xlsx"}

Response Format:
    {
        "status": "SUCCESS" | "PARTIAL" | "ERROR",
        "data": { ... },  // NestingExecutionRecord
        "warnings": [...],
        "errors": [...],
        "processing_time_ms": 123.45
    }
"""

import azure.functions as func
import logging
import json
import base64
from datetime import datetime

from .parser import NestingFileParser
from .models import ParsingResult

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    Azure Function entry point for nesting file parsing.
    
    Accepts files via:
    1. multipart/form-data (file upload)
    2. application/json with base64-encoded content
    """
    logger.info("=== fn_parse_nesting invoked ===")
    request_id = req.headers.get("x-request-id", datetime.utcnow().isoformat())
    
    try:
        # Extract file content and filename
        file_content, filename = _extract_file_from_request(req)
        
        if not file_content:
            return _error_response(
                "No file content provided. Send file as multipart/form-data or JSON with file_content_base64.",
                status_code=400,
                request_id=request_id
            )
        
        if not filename:
            filename = f"nesting_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
        
        logger.info(f"Parsing file: {filename} ({len(file_content)} bytes)")
        
        # Parse the file
        parser = NestingFileParser(
            file_content=file_content,
            filename=filename,
            strict_mode=False  # Allow partial success
        )
        
        result = parser.parse()
        
        # Build response
        response_body = _build_response(result, request_id)
        
        status_code = 200 if result.status in ["SUCCESS", "PARTIAL"] else 422
        
        return func.HttpResponse(
            body=json.dumps(response_body, indent=2, default=str),
            status_code=status_code,
            mimetype="application/json",
            headers={
                "x-request-id": request_id,
                "x-processing-time-ms": str(result.processing_time_ms),
            }
        )
        
    except ValueError as e:
        logger.warning(f"Validation error: {e}")
        return _error_response(str(e), status_code=400, request_id=request_id)
        
    except Exception as e:
        logger.exception(f"Unexpected error in fn_parse_nesting")
        return _error_response(
            f"Internal server error: {str(e)}",
            status_code=500,
            request_id=request_id
        )


def _extract_file_from_request(req: func.HttpRequest) -> tuple:
    """
    Extract file content and filename from request.
    
    Supports:
    - multipart/form-data (file field named 'file' or 'document')
    - application/json with file_content_base64 and optional filename
    
    Returns:
        Tuple of (file_content_bytes, filename)
    """
    content_type = req.headers.get("Content-Type", "").lower()
    
    # Try multipart/form-data first
    if "multipart/form-data" in content_type:
        file = req.files.get("file") or req.files.get("document")
        if file:
            return (file.read(), file.filename)
    
    # Try JSON body
    try:
        body = req.get_json()
        
        if not body:
            return (None, None)
        
        # Base64 encoded content
        if "file_content_base64" in body:
            content_b64 = body["file_content_base64"]
            content = base64.b64decode(content_b64)
            filename = body.get("filename", "nesting.xlsx")
            return (content, filename)
        
        # Raw bytes as hex (fallback)
        if "file_content_hex" in body:
            content_hex = body["file_content_hex"]
            content = bytes.fromhex(content_hex)
            filename = body.get("filename", "nesting.xlsx")
            return (content, filename)
            
    except (ValueError, json.JSONDecodeError):
        pass
    
    # Try raw body for simple cases
    body_bytes = req.get_body()
    if body_bytes and len(body_bytes) > 0:
        # Check if it looks like an Excel file (OLE or ZIP signature)
        if body_bytes[:4] in [b'\xd0\xcf\x11\xe0', b'PK\x03\x04']:
            filename = req.headers.get("x-filename", "nesting.xlsx")
            return (body_bytes, filename)
    
    return (None, None)


def _build_response(result: ParsingResult, request_id: str) -> dict:
    """Build the JSON response from parsing result."""
    response = {
        "request_id": request_id,
        "status": result.status,
        "processing_time_ms": round(result.processing_time_ms, 2),
        "source_file": result.source_file,
        "warnings": result.warnings,
        "errors": result.errors,
    }
    
    if result.data:
        # Use model_dump for Pydantic v2 compatibility
        response["data"] = result.data.model_dump_rounded(mode="json")
    else:
        response["data"] = None
    
    return response


def _error_response(message: str, status_code: int, request_id: str) -> func.HttpResponse:
    """Build an error response."""
    body = {
        "request_id": request_id,
        "status": "ERROR",
        "errors": [message],
        "warnings": [],
        "data": None,
    }
    
    return func.HttpResponse(
        body=json.dumps(body, indent=2),
        status_code=status_code,
        mimetype="application/json",
        headers={"x-request-id": request_id}
    )
