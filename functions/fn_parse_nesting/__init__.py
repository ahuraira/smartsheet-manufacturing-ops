"""
Azure Function: fn_parse_nesting
================================

HTTP-triggered function for parsing Eurosoft CutExpert nesting files.
Designed to be called from Power Automate when files are uploaded to SharePoint.

Endpoints:
    POST /api/nesting/parse - Parse an Excel file and return structured JSON

Version: 2.0.0 (SOTA Integration)
"""

import azure.functions as func
import logging
import json
import base64
import traceback
import uuid
from datetime import datetime
from typing import Optional, Tuple, Dict, Any

from shared import (
    get_smartsheet_client,
    generate_trace_id,
    create_exception,
    log_user_action,
    generate_next_nesting_id,
    ReasonCode,
    ExceptionSeverity,
    ExceptionSource,
    ActionType,
)
from .parser import NestingFileParser
from .models import ParsingResult, NestingExecutionRecord
from .validation import (
    validate_tag_exists,
    validate_tag_lpo_ownership,
    check_duplicate_file,
    check_duplicate_request_id,
    calculate_file_hash,
)
from .nesting_logger import NestingLogger
from .config import get_exception_assignee, get_sla_hours, get_safe_user_email

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    Azure Function entry point for nesting file parsing.
    authoritative: Handles validation, parsing, logging, and exception creation.
    """
    logger.info("=== fn_parse_nesting (v2) invoked ===")
    
    # 1. Initialization & Context
    trace_id = req.headers.get("x-request-id") or generate_trace_id()
    client_request_id = "unknown"
    
    try:
        # 2. Extract Payload
        payload = _extract_payload(req)
        
        # Basic validation of payload
        if not payload.get("file_content_bytes"):
            return _error_response(
                "No file content provided.", 
                status_code=400, 
                request_id=trace_id,
                error_code="MISSING_FILE"
            )

        # Extract context variables
        file_bytes = payload["file_content_bytes"]
        filename = payload.get("filename", f"nesting_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx")
        client_request_id = payload.get("client_request_id", str(uuid.uuid4()))
        sap_lpo_reference = payload.get("sap_lpo_reference")
        
        # SOTA Update: Ensure valid user email via configuration
        raw_user = payload.get("uploaded_by", "system")
        uploaded_by = get_safe_user_email(raw_user)
        file_url = payload.get("file_url")
        
        logger.info(f"Processing request: {client_request_id} for file: {filename}", extra={"trace_id": trace_id})
        
        # 3. Client Initialization
        client = get_smartsheet_client()
        
        # 4. Phase 1: Idempotency & Deduplication
        
        # 4a. Check Client Request ID
        existing_session_id = check_duplicate_request_id(client, client_request_id)
        if existing_session_id:
            logger.info(f"Duplicate request ID detected: {client_request_id}. Returning success.", extra={"trace_id": trace_id})
            return _idempotent_success_response(existing_session_id, trace_id, client_request_id)

        # 4b. Calculate File Hash
        file_hash = calculate_file_hash(file_bytes)
        
        # 4c. Check Duplicate File Hash (for this LPO)
        if sap_lpo_reference:
            dup_session_id = check_duplicate_file(client, file_hash, sap_lpo_reference)
            if dup_session_id:
                logger.warning(f"Duplicate file hash detected for LPO {sap_lpo_reference}", extra={"trace_id": trace_id})
                return _error_response(
                    f"Duplicate file detected. Already processed in session {dup_session_id}",
                    status_code=409,
                    request_id=trace_id,
                    error_code="DUPLICATE_NESTING_FILE",
                    existing_session_id=dup_session_id
                )

        # 5. Phase 2: Parse File
        parser = NestingFileParser(
            file_content=file_bytes,
            filename=filename,
            strict_mode=False 
        )
        parse_result = parser.parse()
        
        if parse_result.status == "ERROR" or not parse_result.data:
            # Critical parsing failure
            logger.error(f"Parsing failed: {parse_result.errors}", extra={"trace_id": trace_id})
            
            # Format message with extras since custom fields aren't supported
            err_msg_full = f"Parsing failed for {filename}: {'; '.join(parse_result.errors[:3])}. URL: {file_url}"
            
            # Create Exception
            exception_id = create_exception(
                client=client,
                trace_id=trace_id,
                source=ExceptionSource.PARSER,
                reason_code=ReasonCode.PARSE_FAILED,
                severity=ExceptionSeverity.CRITICAL,
                message=err_msg_full
            )
            
            return _validation_error_response(
                status="PARSE_ERROR",
                error_code="PARSE_FAILED_CRITICAL",
                error_message=f"Failed to parse nesting file: {parse_result.errors}",
                exception_id=exception_id,
                trace_id=trace_id,
                request_id=client_request_id
            )

        # Extract Key Data
        record = parse_result.data
        tag_id = record.meta_data.project_ref_id
        logger.info(f"Extracted Tag ID: {tag_id}", extra={"trace_id": trace_id})

        # 6. Phase 3: Validation (Fail-Fast)
        
        # 6a. Validate Tag Exists
        tag_validation = validate_tag_exists(client, tag_id)
        if not tag_validation.is_valid:
            logger.warning(f"Validation failed: {tag_validation.error_message}", extra={"trace_id": trace_id})
            
            msg = f"{tag_validation.error_message}. URL: {file_url}"
            exception_id = create_exception(
                client=client,
                trace_id=trace_id,
                source=ExceptionSource.PARSER,
                reason_code=ReasonCode.TAG_NOT_FOUND,
                severity=ExceptionSeverity.HIGH,
                related_tag_id=tag_id,
                message=msg
            )
            
            return _validation_error_response(
                status="VALIDATION_ERROR",
                error_code=tag_validation.error_code,
                error_message=tag_validation.error_message,
                exception_id=exception_id,
                trace_id=trace_id,
                request_id=client_request_id
            )
            
        # 6b. Validate LPO Ownership
        if sap_lpo_reference:
            lpo_validation = validate_tag_lpo_ownership(tag_validation, sap_lpo_reference)
            if not lpo_validation.is_valid:
                logger.warning(f"LPO Mismatch: {lpo_validation.error_message}", extra={"trace_id": trace_id})
                
                msg = f"{lpo_validation.error_message}. Found in LPO: {sap_lpo_reference}"
                exception_id = create_exception(
                    client=client,
                    trace_id=trace_id,
                    source=ExceptionSource.PARSER,
                    reason_code=ReasonCode.LPO_MISMATCH,
                    severity=ExceptionSeverity.HIGH,
                    related_tag_id=tag_id,
                    message=msg
                )
                
                return _validation_error_response(
                    status="VALIDATION_ERROR",
                    error_code=lpo_validation.error_code,
                    error_message=lpo_validation.error_message,
                    exception_id=exception_id,
                    trace_id=trace_id,
                    request_id=client_request_id
                )

        # 7. Phase 4: Execution & Logging
        
        # 7a. Generate Nest Session ID
        nest_session_id = generate_next_nesting_id(client)
        
        # 7b. Log to Nesting Log
        nest_logger = NestingLogger(client)
        nesting_row_id = nest_logger.log_execution(
            record=record,
            nest_session_id=nest_session_id,
            tag_id=tag_id,
            file_hash=file_hash,
            client_request_id=client_request_id,
            sap_lpo_reference=sap_lpo_reference
        )
        
        # 7c. Attach Files
        attachments = []
        if file_url:
            att1 = nest_logger.attach_file(
                "NESTING_LOG", nesting_row_id, file_url, filename, "CutExpert Output"
            )
            if att1: attachments.append(att1)
            
            tag_row_id = tag_validation.tag_row_id
            if tag_row_id:
                att2 = nest_logger.attach_file(
                    "TAG_REGISTRY", tag_row_id, file_url, filename, "Nesting File"
                )
                if att2: attachments.append(att2)
        
        # 7d. Update Tag Status
        if tag_validation.tag_row_id:
            impact = record.raw_material_panel.inventory_impact
            efficiency = record.raw_material_panel.efficiency_metrics
            nest_logger.update_tag_status(
                tag_row_id=tag_validation.tag_row_id,
                sheets_used=impact.utilized_sheets_count,
                wastage=efficiency.waste_pct
            )
        
        # 7e. Generate BOM and Map Materials
        bom_result = None
        try:
            from .bom_orchestrator import process_bom_from_record
            
            # Get LPO ID from tag validation if available
            lpo_id = tag_validation.lpo_id if hasattr(tag_validation, 'lpo_id') else sap_lpo_reference
            
            bom_result = process_bom_from_record(
                client=client,
                record=record,
                nest_session_id=nest_session_id,
                lpo_id=lpo_id,
                trace_id=trace_id
            )
            
            logger.info(
                f"BOM processing: {bom_result.mapped_lines}/{bom_result.total_lines} mapped, "
                f"{bom_result.exception_lines} exceptions",
                extra={"trace_id": trace_id}
            )
        except Exception as bom_err:
            # BOM processing failure should not fail the entire parse
            logger.error(f"BOM processing failed (non-fatal): {bom_err}", extra={"trace_id": trace_id})
            
        # 7f. Log User Action
        log_user_action(
            client=client,
            action_type=ActionType.TAG_UPDATED,
            user_id=uploaded_by,
            target_table="TAG_REGISTRY",
            target_id=tag_id,
            notes=f"Nesting completed. Session: {nest_session_id}",
            trace_id=trace_id
        )

        # 8. Success Response
        logger.info(f"Nesting processed successfully: {nest_session_id}", extra={"trace_id": trace_id})
        
        result_wrapper = ParsingResult(
            status="SUCCESS",
            data=record,
            request_id=client_request_id,
            tag_id=tag_id,
            nest_session_id=nest_session_id,
            nesting_row_id=nesting_row_id,
            tag_row_id=tag_validation.tag_row_id,
            file_hash=file_hash,
            attachments=attachments,
            expected_consumption_m2=record.raw_material_panel.inventory_impact.gross_area_m2,
            wastage_percentage=record.raw_material_panel.efficiency_metrics.waste_pct,
            trace_id=trace_id,
            processing_time_ms=parse_result.processing_time_ms
        )
        
        # Build response with optional BOM stats
        response_data = result_wrapper.model_dump(mode="json")
        
        if bom_result:
            response_data["bom_processing"] = {
                "total_lines": bom_result.total_lines,
                "mapped_lines": bom_result.mapped_lines,
                "exception_lines": bom_result.exception_lines,
                "success": bom_result.success
            }
        
        return func.HttpResponse(
            body=json.dumps(response_data, indent=2),
            status_code=200,
            mimetype="application/json",
            headers={"x-request-id": trace_id}
        )
        
    except Exception as e:
        logger.exception(f"Unexpected error in fn_parse_nesting: {str(e)}")
        return _error_response(
            f"Internal server error: {str(e)}",
            status_code=500,
            request_id=trace_id,
            error_code="INTERNAL_ERROR"
        )


def _extract_payload(req: func.HttpRequest) -> Dict[str, Any]:
    """Helper to extract payload from various content types."""
    payload = {}
    
    # Try JSON first
    try:
        json_body = req.get_json()
        if json_body:
            payload.update(json_body)
            # Handle base64 file content
            if "file_content_base64" in json_body:
                payload["file_content_bytes"] = base64.b64decode(json_body["file_content_base64"])
    except ValueError:
        pass
        
    # Check multipart if no bytes yet
    if "file_content_bytes" not in payload and req.files:
        file_item = req.files.get("file") or req.files.get("document")
        if file_item:
            payload["file_content_bytes"] = file_item.read()
            payload["filename"] = file_item.filename
            
    # If using legacy raw body (rare but supported)
    if "file_content_bytes" not in payload:
        body_bytes = req.get_body()
        if body_bytes and len(body_bytes) > 100:  # Arbitrary check for content
             payload["file_content_bytes"] = body_bytes
             
    return payload


def _error_response(message: str, status_code: int, request_id: str, error_code: str = "ERROR", existing_session_id: str = None) -> func.HttpResponse:
    """Standardized error response."""
    body = {
        "status": "ERROR" if status_code == 500 else "DUPLICATE" if status_code == 409 else "VALIDATION_ERROR",
        "error_code": error_code,
        "error_message": message,
        "trace_id": request_id
    }
    if existing_session_id:
        body["existing_nest_session_id"] = existing_session_id
        
    return func.HttpResponse(
        body=json.dumps(body, indent=2),
        status_code=status_code,
        mimetype="application/json",
        headers={"x-request-id": request_id}
    )

def _validation_error_response(status, error_code, error_message, exception_id, trace_id, request_id):
    """Specific response structure for validation failures as per spec."""
    body = {
        "status": status,
        "request_id": request_id,
        "error_code": error_code,
        "error_message": error_message,
        "exception_id": exception_id,
        "trace_id": trace_id
    }
    return func.HttpResponse(
        body=json.dumps(body, indent=2),
        status_code=422,
        mimetype="application/json",
        headers={"x-request-id": trace_id}
    )

def _idempotent_success_response(session_id, trace_id, client_request_id):
    """Response for idempotent success."""
    body = {
        "status": "SUCCESS",
        "message": "Nesting already processed (Idempotent)",
        "nest_session_id": session_id,
        "request_id": client_request_id,
        "trace_id": trace_id
    }
    return func.HttpResponse(
        body=json.dumps(body, indent=2),
        status_code=200,
        mimetype="application/json",
        headers={"x-request-id": trace_id}
    )
