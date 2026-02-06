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
    # v1.6.9 SOTA: Moved from inline imports
    atomic_increment,
    AtomicUpdateResult,
)
from shared.models import ScheduleStatus  # v1.6.9: Moved from inline
from shared.manifest import get_manifest  # v1.6.9: Moved from inline
from shared.logical_names import Sheet, Column  # v1.6.9: For atomic updates
from shared.blob_storage import upload_nesting_json, upload_content_blob  # v1.6.9
from shared.power_automate import trigger_nesting_complete_flow  # v1.6.9

from .parser import NestingFileParser
from .models import ParsingResult, NestingExecutionRecord
from .validation import (
    validate_tag_exists,
    validate_tag_lpo_ownership,
    check_duplicate_file,
    check_duplicate_request_id,
    calculate_file_hash,
    validate_tag_is_planned,  # v1.6.7: Prerequisite validation
    get_lpo_details,  # v1.6.7: Fail-fast LPO enrichment
)
from .nesting_logger import NestingLogger
from .config import get_exception_assignee, get_sla_hours, get_safe_user_email
from .bom_orchestrator import process_bom_from_record  # v1.6.9: Moved from inline

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
        
        # v1.6.7: Derive SAP Reference from Tag Registry if not provided in payload
        if not sap_lpo_reference:
            sap_lpo_reference = tag_validation.tag_lpo_ref
            if sap_lpo_reference:
                logger.info(f"SAP Reference derived from Tag Registry: {sap_lpo_reference}", extra={"trace_id": trace_id})
            else:
                logger.warning(f"Tag {tag_id} has no linked LPO in Tag Registry", extra={"trace_id": trace_id})
                # Still proceed - LPO details validation will handle the error gracefully
            
        # 6b. Validate LPO Ownership (only if both are present)
        if sap_lpo_reference and tag_validation.tag_lpo_ref:
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

        # 6c. PREREQUISITE: Validate Tag is Planned (v1.6.7)
        planning_validation = validate_tag_is_planned(client, tag_id)
        if not planning_validation.is_valid:
            logger.warning(f"Tag not planned: {planning_validation.error_message}", extra={"trace_id": trace_id})
            
            exception_id = create_exception(
                client=client,
                trace_id=trace_id,
                source=ExceptionSource.PARSER,
                reason_code=ReasonCode.TAG_NOT_FOUND,  # Closest match
                severity=ExceptionSeverity.HIGH,
                related_tag_id=tag_id,
                message=f"{planning_validation.error_message}. URL: {file_url}"
            )
            
            return _validation_error_response(
                status="VALIDATION_ERROR",
                error_code=planning_validation.error_code,
                error_message=planning_validation.error_message,
                exception_id=exception_id,
                trace_id=trace_id,
                request_id=client_request_id
            )
        
        # 6d. FAIL-FAST: Get LPO Details (v1.6.7)
        lpo_details = get_lpo_details(client, sap_lpo_reference)
        if not lpo_details.is_valid:
            logger.warning(f"LPO validation failed: {lpo_details.error_message}", extra={"trace_id": trace_id})
            
            # Map error code to ReasonCode
            rc = ReasonCode.LPO_NOT_FOUND if lpo_details.error_code == "LPO_NOT_FOUND" else ReasonCode.LPO_INVALID_DATA
            
            exception_id = create_exception(
                client=client,
                trace_id=trace_id,
                source=ExceptionSource.PARSER,
                reason_code=rc,
                severity=ExceptionSeverity.HIGH,
                related_tag_id=tag_id,
                message=f"{lpo_details.error_message}. URL: {file_url}"
            )
            
            return _validation_error_response(
                status="VALIDATION_ERROR",
                error_code=lpo_details.error_code,
                error_message=lpo_details.error_message,
                exception_id=exception_id,
                trace_id=trace_id,
                request_id=client_request_id
            )
        
        # Enrichment data for logging and response
        brand = lpo_details.brand
        area_type = lpo_details.area_type
        lpo_row_id = lpo_details.lpo_row_id
        lpo_folder_url = lpo_details.lpo_folder_url
        planned_date = planning_validation.planned_date
        planning_row_id = planning_validation.planning_row_id

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
            sap_lpo_reference=sap_lpo_reference,
            brand=brand,  # v1.6.7: From LPO
            planned_date=planned_date  # v1.6.7: From Production Planning
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
        
        # Calculate consumed area (Internal/External) for updates
        billing = record.billing_metrics
        consumed_area = billing.total_external_area_m2 if area_type == "External" else billing.total_internal_area_m2
        
        # 7d. Update Tag Status (v1.6.7: Update area)
        if tag_validation.tag_row_id:
            impact = record.raw_material_panel.inventory_impact
            efficiency = record.raw_material_panel.efficiency_metrics
            nest_logger.update_tag_status(
                tag_row_id=tag_validation.tag_row_id,
                sheets_used=impact.utilized_sheets_count,
                wastage=efficiency.waste_pct,
                area_consumed=consumed_area  # v1.6.7: Update ESTIMATED_QUANTITY
            )
        
        # 7d2. Update Production Planning Status (v1.6.7)
        # v1.6.9 SOTA: Track warnings instead of silently swallowing
        warnings = []
        
        if planning_row_id:
            try:
                client.update_row(
                    Sheet.PRODUCTION_PLANNING,
                    planning_row_id,
                    {Column.PRODUCTION_PLANNING.STATUS: ScheduleStatus.NESTING_UPLOADED.value}
                )
                logger.info(f"Updated Production Planning row {planning_row_id} to 'Nesting Uploaded'", extra={"trace_id": trace_id})
            except Exception as pp_err:
                logger.error(f"Failed to update Production Planning status: {pp_err}", extra={"trace_id": trace_id})
                # v1.6.9 SOTA: Create exception for tracking
                create_exception(
                    client=client,
                    trace_id=trace_id,
                    source=ExceptionSource.PARSER,
                    reason_code=ReasonCode.SYSTEM_ERROR,
                    severity=ExceptionSeverity.LOW,
                    related_tag_id=tag_id,
                    message=f"Failed to update Production Planning status: {pp_err}"
                )
                warnings.append({"code": "PP_UPDATE_FAILED", "message": str(pp_err)})
        
        # 7d3. Update LPO Allocated Quantity (v1.6.7)
        # v1.6.9 SOTA: Use atomic_increment to prevent race conditions
        if lpo_row_id and consumed_area > 0:
            alloc_result = atomic_increment(
                client=client,
                sheet_ref=Sheet.LPO_MASTER,
                row_id=lpo_row_id,
                column_ref=Column.LPO_MASTER.ALLOCATED_QUANTITY,
                increment_by=consumed_area,
                trace_id=trace_id
            )
            
            if alloc_result.success:
                logger.info(
                    f"Updated LPO {sap_lpo_reference} ALLOCATED_QUANTITY: "
                    f"{alloc_result.old_value} -> {alloc_result.new_value} "
                    f"(area_type={area_type}, retries={alloc_result.retries_used})",
                    extra={"trace_id": trace_id}
                )
            else:
                logger.error(f"Failed to update LPO allocated quantity: {alloc_result.error_message}", extra={"trace_id": trace_id})
                # v1.6.9 SOTA: Create exception for tracking
                create_exception(
                    client=client,
                    trace_id=trace_id,
                    source=ExceptionSource.PARSER,
                    reason_code=ReasonCode.SYSTEM_ERROR,
                    severity=ExceptionSeverity.MEDIUM,  # MEDIUM because affects inventory
                    related_tag_id=tag_id,
                    message=f"Failed to update LPO ALLOCATED_QUANTITY: {alloc_result.error_message}"
                )
                warnings.append({"code": "LPO_ALLOC_FAILED", "message": alloc_result.error_message})
        
        # 7e. Generate BOM and Map Materials
        bom_result = None
        try:
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
            logger.error(f"BOM processing failed: {bom_err}", extra={"trace_id": trace_id})
            warnings.append({"code": "BOM_FAILED", "message": str(bom_err)})
            
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
        
        # v1.6.7: Add enrichment data for Power Automate
        response_data["enrichment"] = {
            "brand": brand,
            "area_type": area_type,
            "planned_date": planned_date,
            "lpo_row_id": lpo_row_id,
            "lpo_folder_url": lpo_folder_url,
            "planning_row_id": planning_row_id,
            "sap_lpo_reference": sap_lpo_reference,
            "uploaded_by": uploaded_by
        }
        
        # 8a. Upload Files to Blob Storage (v1.6.7)
        json_blob_url = None
        excel_blob_url = None
        
        try:
            # v1.6.9: Imports moved to module level
            # Upload JSON Output
            json_blob_url = upload_nesting_json(
                record_data=record.model_dump_rounded(),
                nest_session_id=nest_session_id,
                sap_lpo_reference=sap_lpo_reference,
                trace_id=trace_id
            )
            if json_blob_url:
                response_data["json_blob_url"] = json_blob_url
                logger.info(f"JSON uploaded to blob: {json_blob_url}", extra={"trace_id": trace_id})
                
            # Upload Original Excel Input
            if file_bytes:
                excel_blob_url = upload_content_blob(
                    content=file_bytes,
                    blob_name=filename,
                    trace_id=trace_id,
                    folder_path=sap_lpo_reference,
                    content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
                if excel_blob_url:
                    response_data["excel_blob_url"] = excel_blob_url
                    logger.info(f"Excel uploaded to blob: {excel_blob_url}", extra={"trace_id": trace_id})
                    
        except Exception as blob_err:
            logger.error(f"Failed to upload files to blob: {blob_err}", extra={"trace_id": trace_id})
            # v1.6.9 SOTA: Create exception for tracking
            create_exception(
                client=client,
                trace_id=trace_id,
                source=ExceptionSource.PARSER,
                reason_code=ReasonCode.FILE_UPLOAD_ERROR,
                severity=ExceptionSeverity.LOW,
                related_tag_id=tag_id,
                message=f"Failed to upload files to blob storage: {blob_err}"
            )
            warnings.append({"code": "BLOB_UPLOAD_FAILED", "message": str(blob_err)})
        
        # 8b. Trigger Power Automate flow (v1.6.7)
        # v1.6.9 SOTA: consumed_area already calculated above (DRY fix)
        try:
            pa_result = trigger_nesting_complete_flow(
                nest_session_id=nest_session_id,
                tag_id=tag_id,
                sap_lpo_reference=sap_lpo_reference,
                brand=brand,
                json_blob_url=json_blob_url,
                excel_file_url=excel_blob_url or file_url,  # Prefer blob URL for PA
                uploaded_by=uploaded_by,
                planned_date=planned_date,
                area_consumed=consumed_area,
                area_type=area_type,
                correlation_id=trace_id,
                lpo_folder_url=lpo_folder_url
            )
            response_data["power_automate"] = pa_result.to_dict()
        except Exception as pa_err:
            logger.error(f"Failed to trigger Power Automate: {pa_err}", extra={"trace_id": trace_id})
            # v1.6.9 SOTA: Create exception for tracking
            create_exception(
                client=client,
                trace_id=trace_id,
                source=ExceptionSource.PARSER,
                reason_code=ReasonCode.SYSTEM_ERROR,
                severity=ExceptionSeverity.LOW,
                related_tag_id=tag_id,
                message=f"Failed to trigger Power Automate flow: {pa_err}"
            )
            warnings.append({"code": "PA_TRIGGER_FAILED", "message": str(pa_err)})
        
        # v1.6.9 SOTA: Include warnings in response for transparency
        if warnings:
            response_data["warnings"] = warnings
        
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
