"""
BOM Orchestrator
================

Orchestrates the full BOM generation and mapping workflow:
1. Generate BOM lines from parsed nesting record
2. Map each line to canonical codes using mapping service
3. Write mapped BOM lines to Parsed BOM sheet

This is the main entry point for integrating material mapping
with the nesting parser.

Usage:
    from fn_parse_nesting.bom_orchestrator import BOMOrchestrator
    
    orchestrator = BOMOrchestrator(smartsheet_client)
    result = orchestrator.process(parsed_record, nest_session_id, lpo_id)
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .bom_generator import BOMGenerator, BOMLine
from .models import NestingExecutionRecord

logger = logging.getLogger(__name__)


@dataclass
class BOMProcessingResult:
    """Result of BOM processing."""
    
    success: bool = False
    nest_session_id: str = ""
    total_lines: int = 0
    mapped_lines: int = 0
    exception_lines: int = 0
    bom_lines: List[BOMLine] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    trace_id: str = ""


class BOMOrchestrator:
    """
    Orchestrates BOM generation, mapping, and persistence.
    
    Thread-safe: Can be reused across requests.
    """
    
    def __init__(self, smartsheet_client: Any):
        """
        Initialize orchestrator.
        
        Args:
            smartsheet_client: SmartsheetClient for API calls
        """
        self._client = smartsheet_client
        self._generator = BOMGenerator(include_machine_wear=False)
        self._mapping_service = None  # Lazy init
    
    def process(
        self,
        record: NestingExecutionRecord,
        nest_session_id: str,
        lpo_id: Optional[str] = None,
        project_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        write_to_sheet: bool = True,
    ) -> BOMProcessingResult:
        """
        Process a nesting record: generate BOM, map materials, persist.
        
        Args:
            record: Parsed NestingExecutionRecord
            nest_session_id: Unique ID for this nesting session
            lpo_id: Optional LPO ID for override lookup
            project_id: Optional project ID for override lookup
            trace_id: Optional trace ID for distributed tracing
            write_to_sheet: If True, write BOM lines to Parsed BOM sheet
            
        Returns:
            BOMProcessingResult with mapped lines and statistics
        """
        trace_id = trace_id or str(uuid4())
        result = BOMProcessingResult(
            nest_session_id=nest_session_id,
            trace_id=trace_id
        )
        
        logger.info(
            f"[{trace_id}] Processing BOM for session {nest_session_id}"
        )
        
        try:
            # Step 1: Generate BOM lines
            bom_lines = self._generator.generate(record, trace_id)
            result.total_lines = len(bom_lines)
            
            if not bom_lines:
                logger.warning(f"[{trace_id}] No BOM lines generated")
                result.success = True
                return result
            
            # Step 2: Map each line
            mapped_lines = self._map_lines(bom_lines, lpo_id, project_id, trace_id)
            result.bom_lines = mapped_lines
            
            # Count mapping results
            for line in mapped_lines:
                if line.mapping_decision in ["AUTO", "OVERRIDE"]:
                    result.mapped_lines += 1
                elif line.mapping_decision == "REVIEW":
                    result.exception_lines += 1
            
            # Step 3: Write to sheet
            if write_to_sheet:
                self._write_to_sheet(nest_session_id, mapped_lines, trace_id)
            
            result.success = True
            logger.info(
                f"[{trace_id}] BOM processing complete: "
                f"{result.mapped_lines}/{result.total_lines} mapped, "
                f"{result.exception_lines} exceptions"
            )
            
        except Exception as e:
            logger.exception(f"[{trace_id}] BOM processing failed: {e}")
            result.errors.append(str(e))
        
        return result
    
    def _get_mapping_service(self):
        """Lazy initialization of mapping service."""
        if self._mapping_service is None:
            # Import here to avoid circular imports
            import sys
            from pathlib import Path
            sys.path.insert(0, str(Path(__file__).parent.parent))
            
            from fn_map_lookup.mapping_service import MappingService
            self._mapping_service = MappingService(self._client)
        
        return self._mapping_service
    
    def _map_lines(
        self,
        lines: List[BOMLine],
        lpo_id: Optional[str],
        project_id: Optional[str],
        trace_id: str
    ) -> List[BOMLine]:
        """Map each BOM line to canonical codes."""
        service = self._get_mapping_service()
        
        for line in lines:
            try:
                result = service.lookup(
                    nesting_description=line.nesting_description,
                    lpo_id=lpo_id,
                    project_id=project_id,
                    ingest_line_id=line.line_id,
                    trace_id=trace_id
                )
                
                line.canonical_code = result.canonical_code
                line.sap_code = result.sap_code
                line.mapping_decision = result.decision
                line.history_id = result.history_id
                
                # Apply conversion using UnitService
                if result.uom:
                    from shared.unit_service import UnitService
                    
                    line.canonical_quantity = UnitService.convert(
                        quantity=line.quantity, 
                        from_uom=line.uom, 
                        to_uom=result.uom, 
                        conversion_factor=result.conversion_factor
                    )
                    line.canonical_uom = result.uom
                
            except Exception as e:
                logger.warning(
                    f"[{trace_id}] Mapping failed for '{line.nesting_description}': {e}"
                )
                line.mapping_decision = "REVIEW"
        
        return lines
    
    def _write_to_sheet(
        self,
        nest_session_id: str,
        lines: List[BOMLine],
        trace_id: str
    ) -> None:
        """Write BOM lines to Parsed BOM sheet."""
        from shared.manifest import get_manifest
        from shared.logical_names import Sheet
        
        manifest = get_manifest()
        sheet_id = manifest.get_sheet_id("PARSED_BOM")
        
        if not sheet_id:
            logger.warning(f"[{trace_id}] PARSED_BOM sheet not in manifest, skipping write")
            return
        
        # Get column IDs
        col_ids = manifest.get_all_column_ids("PARSED_BOM")
        
        rows = []
        for line in lines:
            cells = [
                {"columnId": col_ids["BOM_LINE_ID"], "value": line.line_id},
                {"columnId": col_ids["NEST_SESSION_ID"], "value": nest_session_id},
                {"columnId": col_ids["LINE_NUMBER"], "value": str(line.line_number)},
                {"columnId": col_ids["MATERIAL_TYPE"], "value": line.material_type},
                {"columnId": col_ids["NESTING_DESCRIPTION"], "value": line.nesting_description},
                {"columnId": col_ids["CANONICAL_CODE"], "value": line.canonical_code or ""},
                {"columnId": col_ids["SAP_CODE"], "value": line.sap_code or ""},
                {"columnId": col_ids["QUANTITY"], "value": str(line.quantity)},
                {"columnId": col_ids["UOM"], "value": line.uom},
                {"columnId": col_ids["CANONICAL_QUANTITY"], "value": str(line.canonical_quantity) if line.canonical_quantity else ""},
                {"columnId": col_ids["CANONICAL_UOM"], "value": line.canonical_uom or ""},
                {"columnId": col_ids["MAPPING_DECISION"], "value": line.mapping_decision or ""},
                {"columnId": col_ids["HISTORY_ID"], "value": line.history_id or ""},
                {"columnId": col_ids["CREATED_AT"], "value": datetime.now(timezone.utc).isoformat()},
                {"columnId": col_ids["TRACE_ID"], "value": trace_id},
            ]
            rows.append({"toBottom": True, "cells": cells})
        
        # Add rows to sheet
        self._client.add_rows_bulk(Sheet.PARSED_BOM, rows)
        
        logger.info(f"[{trace_id}] Wrote {len(rows)} BOM lines to Parsed BOM sheet")


def process_bom_from_record(
    client: Any,
    record: NestingExecutionRecord,
    nest_session_id: str,
    lpo_id: Optional[str] = None,
    project_id: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> BOMProcessingResult:
    """
    Convenience function to process BOM from a nesting record.
    
    This is the main entry point for integrating with the parser.
    
    Args:
        client: SmartsheetClient instance
        record: Parsed NestingExecutionRecord
        nest_session_id: Unique nesting session ID
        lpo_id: Optional LPO ID for brand resolution
        project_id: Optional project ID
        trace_id: Optional trace ID
        
    Returns:
        BOMProcessingResult with mapping statistics
    """
    orchestrator = BOMOrchestrator(client)
    return orchestrator.process(
        record=record,
        nest_session_id=nest_session_id,
        lpo_id=lpo_id,
        project_id=project_id,
        trace_id=trace_id,
        write_to_sheet=True
    )
