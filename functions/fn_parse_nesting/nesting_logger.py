import logging
import json
from datetime import datetime
from typing import List, Dict, Any, Optional

from shared.smartsheet_client import SmartsheetClient
from shared.logical_names import Sheet, Column
from .models import NestingExecutionRecord, AttachmentInfo

logger = logging.getLogger(__name__)

class NestingLogger:
    def __init__(self, client: SmartsheetClient):
        self.client = client

    def log_execution(
        self,
        record: NestingExecutionRecord,
        nest_session_id: str,
        tag_id: str,
        file_hash: str,
        client_request_id: str,
        sap_lpo_reference: Optional[str] = None,
        brand: Optional[str] = None,  # v1.6.7: LPO Brand
        planned_date: Optional[str] = None  # v1.6.7: From Production Planning
    ) -> int:
        """
        Log the successful nesting execution to the Nesting Execution Log sheet.
        
        Returns:
            int: The Row ID of the created row.
        """
        # 1. Prepare computed values
        inventory_impact = record.raw_material_panel.inventory_impact
        efficiency = record.raw_material_panel.efficiency_metrics
        
        # 2. Build row data mapping to Logical Columns
        row_data = {
            Column.NESTING_LOG.NEST_SESSION_ID: nest_session_id,
            Column.NESTING_LOG.TAG_SHEET_ID: tag_id,
            Column.NESTING_LOG.TIMESTAMP: datetime.utcnow().isoformat(),
            Column.NESTING_LOG.BRAND: brand or "",  # v1.6.7: Now populated
            Column.NESTING_LOG.SHEETS_CONSUMED_VIRTUAL: inventory_impact.utilized_sheets_count,
            Column.NESTING_LOG.EXPECTED_CONSUMPTION_M2: inventory_impact.gross_area_m2,
            Column.NESTING_LOG.WASTAGE_PERCENTAGE: efficiency.waste_pct,
            Column.NESTING_LOG.PLANNED_DATE: planned_date or "",  # v1.6.7: From planning
            Column.NESTING_LOG.FILE_HASH: file_hash,
            Column.NESTING_LOG.CLIENT_REQUEST_ID: client_request_id,
        }
        
        # Add optional generated IDs if available (stub for now, can be expanded)
        # row_data[Column.NESTING_LOG.REMNANT_ID_GENERATED] = ...
        
        logger.info(f"Logging nesting execution for session {nest_session_id}")
        
        # 3. Add row to Smartsheet
        try:
            result = self.client.add_row(Sheet.NESTING_LOG, row_data)
            return result.get("id")
        except Exception as e:
            logger.error(f"Failed to log nesting execution: {e}")
            raise

    def attach_file(
        self,
        sheet_ref: str,
        row_id: int,
        file_url: str,
        filename: str,
        description: str
    ) -> Optional[AttachmentInfo]:
        """
        Attach the nesting file to a Smartsheet row.
        """
        if not file_url:
            logger.warning("No file_url provided for attachment")
            return None
            
        try:
            logger.info(f"Attaching {filename} to {sheet_ref} row {row_id}")
            self.client.attach_url_to_row(
                sheet_ref=sheet_ref,
                row_id=row_id,
                url=file_url,
                name=filename,
                description=description
            )
            return AttachmentInfo(
                target=sheet_ref,
                row_id=row_id,
                name=filename
            )
        except Exception as e:
            logger.error(f"Failed to attach file to {sheet_ref}: {e}")
            # We swallow the error here to not fail the whole process if just attachment fails
            # But we log it.
            return None

    def update_tag_status(
        self,
        tag_row_id: int,
        sheets_used: float,
        wastage: float,
        area_consumed: Optional[float] = None  # v1.6.7: Internal/External area
    ) -> bool:
        """
        Update the Tag Registry row with nesting results.
        
        Args:
            tag_row_id: Row ID in Tag Registry
            sheets_used: Number of sheets utilized
            wastage: Wastage percentage
            area_consumed: Calculated area (Internal/External) to update ESTIMATED_QUANTITY
            
        Returns:
            bool: True if successful
        """
        try:
            updates = {
                Column.TAG_REGISTRY.SHEETS_USED: sheets_used,
                Column.TAG_REGISTRY.WASTAGE_NESTED: wastage,
                Column.TAG_REGISTRY.STATUS: "Nested"
            }
            
            # v1.6.7: Update Estimated Quantity with actual consumption (Internal/External)
            if area_consumed is not None:
                updates[Column.TAG_REGISTRY.ESTIMATED_QUANTITY] = area_consumed
            
            logger.info(f"Updating Tag row {tag_row_id} with nesting metrics")
            self.client.update_row(
                Sheet.TAG_REGISTRY,
                tag_row_id,
                updates
            )
            return True
        except Exception as e:
            logger.error(f"Failed to update Tag Registry status: {e}")
            return False
