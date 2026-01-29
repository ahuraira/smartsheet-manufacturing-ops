"""
Nesting File Parser
===================

Main orchestrator that coordinates all sheet extractors and produces
the final NestingExecutionRecord.
"""

import pandas as pd
import logging
import io
from datetime import datetime
from typing import Optional, Dict, List, Tuple

from .models import (
    NestingExecutionRecord,
    MetaData,
    RawMaterialPanel,
    InventoryImpact,
    EfficiencyMetrics,
    BillingMetrics,
    ProfileConsumption,
    FlangeAccessories,
    Consumables,
    MachineTelemetry,
    FinishedGoodsLine,
    FinishedGoodsGeometry,
    ParsingResult,
)
from .extractors import (
    ProjectParametersExtractor,
    PanelsInfoExtractor,
    FlangesExtractor,
    OtherComponentsExtractor,
    DeliveryOrderExtractor,
    MachineInfoExtractor,
)

logger = logging.getLogger(__name__)


class NestingFileParser:
    """
    Main parser for Eurosoft CutExpert nesting files.
    
    Orchestrates extraction from all sheets and produces a unified
    NestingExecutionRecord.
    
    Usage:
        parser = NestingFileParser(file_bytes, filename)
        result = parser.parse()
        
        if result.status == "SUCCESS":
            record = result.data
    """
    
    # Sheet names as they appear in CutExpert exports
    SHEET_NAMES = {
        "project_parameters": "Project parameters",
        "user_parameters": "User parameters",
        "panels_info": "Panels info",
        "flanges": "Flanges",
        "other_components": "Other components",
        "delivery_order": "Delivery order",
        "machine_info": "Machine info",
    }
    
    def __init__(
        self,
        file_content: bytes,
        filename: str,
        strict_mode: bool = False
    ):
        """
        Initialize the parser.
        
        Args:
            file_content: Raw bytes of the Excel file
            filename: Original filename for reference
            strict_mode: If True, fail on any missing required data
        """
        self.file_content = file_content
        self.filename = filename
        self.strict_mode = strict_mode
        
        self._workbook: Dict[str, pd.DataFrame] = {}
        self._warnings: List[str] = []
        self._errors: List[str] = []
        self._start_time: float = 0
    
    def parse(self) -> ParsingResult:
        """
        Parse the Excel file and return a structured result.
        
        Returns:
            ParsingResult containing the NestingExecutionRecord or errors
        """
        import time
        self._start_time = time.time()
        
        result = ParsingResult(source_file=self.filename)
        
        try:
            # Load the workbook
            self._load_workbook()
            
            # Extract data from each sheet
            record = self._build_record()
            
            result.data = record
            result.warnings = self._warnings
            result.errors = self._errors
            
            # Determine status
            if self._errors:
                result.status = "ERROR" if self.strict_mode else "PARTIAL"
            elif self._warnings:
                result.status = "PARTIAL"
            else:
                result.status = "SUCCESS"
                
        except Exception as e:
            logger.exception(f"Critical error parsing {self.filename}")
            result.status = "ERROR"
            result.errors = [f"Critical parsing error: {str(e)}"]
        
        result.processing_time_ms = (time.time() - self._start_time) * 1000
        logger.info(
            f"Parsed {self.filename} in {result.processing_time_ms:.2f}ms "
            f"- status: {result.status}"
        )
        
        return result
    
    def _load_workbook(self) -> None:
        """Load all sheets from the Excel file into DataFrames."""
        file_io = io.BytesIO(self.file_content)
        
        # Detect file type
        is_xls = self.filename.lower().endswith('.xls')
        engine = 'xlrd' if is_xls else 'openpyxl'
        
        try:
            # Load all sheets
            excel_file = pd.ExcelFile(file_io, engine=engine)
            available_sheets = excel_file.sheet_names
            
            logger.info(f"Loading workbook with sheets: {available_sheets}")
            
            for key, sheet_name in self.SHEET_NAMES.items():
                if sheet_name in available_sheets:
                    df = pd.read_excel(
                        excel_file,
                        sheet_name=sheet_name,
                        header=None,  # No automatic header detection
                        dtype=str,    # Read all as strings initially
                    )
                    # Convert back to appropriate types for numeric operations
                    # Using lambda to ensure errors='ignore' is passed correctly in pandas 2.x
                    df = df.apply(lambda col: pd.to_numeric(col, errors='coerce').fillna(col))
                    self._workbook[key] = df
                    logger.debug(f"Loaded sheet '{sheet_name}' with {len(df)} rows")
                else:
                    logger.warning(f"Sheet '{sheet_name}' not found in workbook")
                    self._warnings.append(f"Sheet '{sheet_name}' not found")
                    
        except Exception as e:
            raise ValueError(f"Failed to load Excel file: {str(e)}")
    
    def _build_record(self) -> NestingExecutionRecord:
        """Build the complete NestingExecutionRecord from extracted data."""
        
        # Extract from each sheet
        project_data = self._extract_project_parameters()
        panels_data = self._extract_panels_info()
        flanges_data = self._extract_flanges()
        other_data = self._extract_other_components()
        delivery_data = self._extract_delivery_order()
        machine_data = self._extract_machine_info()
        
        # Build the record
        tag_id, tag_warnings = self._get_tag_id(project_data)
        self._warnings.extend(tag_warnings)
        
        # Treat missing Tag ID as a critical error
        if tag_id == "UNKNOWN":
            self._errors.append("Critical: Failed to identify Tag ID (PROJECT REFERENCE and PROJECT NAME are missing or empty)")
        
        # Determine validation status
        validation_status = "OK"
        if self._errors:
            validation_status = "ERROR"
        elif self._warnings:
            validation_status = "WARNING"
        
        # Meta data
        meta_data = MetaData(
            project_ref_id=tag_id,
            project_name=project_data.project_name if project_data else None,
            source_file_name=self.filename,
            extraction_timestamp_utc=datetime.utcnow(),
            validation_status=validation_status,
            validation_messages=self._warnings + self._errors,
        )
        
        # Raw material panel
        raw_material = RawMaterialPanel(
            material_spec_name=project_data.material if project_data else "UNKNOWN",
            thickness_mm=project_data.thickness_mm if project_data else 0,
            sheet_dim_x_mm=project_data.sheet_dim_x_mm if project_data else 0,
            sheet_dim_y_mm=project_data.sheet_dim_y_mm if project_data else 0,
            inventory_impact=InventoryImpact(
                utilized_sheets_count=project_data.utilized_sheets if project_data else 0,
                gross_area_m2=panels_data.gross_utilized_area_m2 if panels_data else 0,
                net_reusable_remnant_area_m2=project_data.total_reusable_area_m2 if project_data else 0,
            ),
            efficiency_metrics=EfficiencyMetrics(
                total_waste_m2=panels_data.total_wastage_m2 if panels_data else 0,
                waste_pct=panels_data.total_waste_pct if panels_data else 0,
                nesting_waste_m2=project_data.wastage_nesting_m2 if project_data else 0,
                tech_waste_45_deg_m2=project_data.wastage_45_deg_m2 if project_data else 0,
                tech_waste_2x45_deg_m2=project_data.wastage_2x45_deg_m2 if project_data else 0,
            ),
        )
        
        # Billing metrics
        billing = BillingMetrics(
            total_internal_area_m2=panels_data.internal_dimensions_area_m2 if panels_data else 0,
            total_external_area_m2=panels_data.external_dimensions_area_m2 if panels_data else 0,
        )
        
        # Profiles and flanges
        profiles = []
        if flanges_data:
            for p in flanges_data.profiles:
                profiles.append(ProfileConsumption(
                    profile_type=p.profile_type,
                    thickness_mm=p.thickness_mm,
                    total_consumption_m=round(p.total_consumption_m, 2),
                    remnant_generated_m=round(p.remnant_generated_m, 2),
                    bar_count=p.bar_count,
                    flange_count=p.flange_count,
                ))
        
        # Flange accessories
        flange_accessories = FlangeAccessories(
            gi_corners_qty=flanges_data.gi_corners_qty if flanges_data else 0,
            gi_corners_cost=round(flanges_data.gi_corners_cost, 2) if flanges_data else 0,
            pvc_corners_qty=flanges_data.pvc_corners_qty if flanges_data else 0,
            pvc_corners_cost=round(flanges_data.pvc_corners_cost, 2) if flanges_data else 0,
        )
        
        # Consumables
        consumables = Consumables(
            silicone_consumption_kg=other_data.silicone_consumption_kg if other_data else 0,
            silicone_extra_pct=other_data.silicone_extra_pct if other_data else 0,
            aluminum_tape_consumption_m=other_data.aluminum_tape_consumption_m if other_data else 0,
            aluminum_tape_extra_pct=other_data.aluminum_tape_extra_pct if other_data else 0,
            glue_junction_kg=other_data.glue_junction_kg if other_data else 0,
            glue_junction_extra_pct=other_data.glue_junction_extra_pct if other_data else 0,
            glue_flange_kg=other_data.glue_flange_kg if other_data else 0,
            glue_flange_extra_pct=other_data.glue_flange_extra_pct if other_data else 0,
        )
        
        # Machine telemetry - prefer values from Project parameters, fallback to Machine info
        machine_telemetry = MachineTelemetry(
            blade_wear_45_m=project_data.length_45_cuts_m if project_data else (machine_data.length_45_cuts_m if machine_data else 0),
            blade_wear_90_m=project_data.length_90_cuts_m if project_data else (machine_data.length_90_cuts_m if machine_data else 0),
            blade_wear_2x45_m=project_data.length_2x45_cuts_m if project_data else (machine_data.length_2x45_cuts_m if machine_data else 0),
            gantry_travel_rapid_m=project_data.length_rapid_traverse_m if project_data else (machine_data.rapid_traverse_length_m if machine_data else 0),
            time_marking_sec=project_data.time_marking_sec if project_data else (machine_data.time_marking_sec if machine_data else 0),
            time_45_cuts_sec=project_data.time_45_cuts_sec if project_data else (machine_data.time_45_cuts_sec if machine_data else 0),
            time_90_cuts_sec=project_data.time_90_cuts_sec if project_data else (machine_data.time_90_cuts_sec if machine_data else 0),
            time_2x45_cuts_sec=project_data.time_2x45_cuts_sec if project_data else 0,
            time_rapid_traverse_sec=project_data.time_rapid_traverse_sec if project_data else (machine_data.time_rapid_traverse_sec if machine_data else 0),
        )
        
        # Finished goods manifest
        finished_goods = []
        if delivery_data:
            for i, item in enumerate(delivery_data.line_items):
                finished_goods.append(FinishedGoodsLine(
                    line_id=item.line_id or (i + 1),
                    tag_id=item.tag_id,
                    description=item.description,
                    geometry=FinishedGoodsGeometry(
                        mouth_a_x=item.mouth_a_x,
                        mouth_a_y=item.mouth_a_y,
                        mouth_a_fl=getattr(item, 'mouth_a_fl', None),
                        mouth_b_x=item.mouth_b_x,
                        mouth_b_y=item.mouth_b_y,
                        mouth_b_fl=getattr(item, 'mouth_b_fl', None),
                        length_m=getattr(item, 'length_m', None),
                    ),
                    qty_produced=item.qty,
                    internal_area_m2=round(item.internal_area_m2, 4),
                    external_area_m2=round(item.external_area_m2, 4),
                ))
        
        return NestingExecutionRecord(
            meta_data=meta_data,
            raw_material_panel=raw_material,
            billing_metrics=billing,
            profiles_and_flanges=profiles,
            flange_accessories=flange_accessories,
            consumables=consumables,
            machine_telemetry=machine_telemetry,
            finished_goods_manifest=finished_goods,
        )
    
    def _extract_project_parameters(self):
        """Extract from Project parameters sheet."""
        if "project_parameters" not in self._workbook:
            self._warnings.append("Project parameters sheet not available")
            return None
        
        extractor = ProjectParametersExtractor(self._workbook["project_parameters"])
        data = extractor.extract()
        self._warnings.extend(data.warnings)
        return data
    
    def _extract_panels_info(self):
        """Extract from Panels info sheet."""
        if "panels_info" not in self._workbook:
            self._warnings.append("Panels info sheet not available")
            return None
        
        extractor = PanelsInfoExtractor(self._workbook["panels_info"])
        data = extractor.extract()
        self._warnings.extend(data.warnings)
        return data
    
    def _extract_flanges(self):
        """Extract from Flanges sheet."""
        if "flanges" not in self._workbook:
            self._warnings.append("Flanges sheet not available")
            return None
        
        extractor = FlangesExtractor(self._workbook["flanges"])
        data = extractor.extract()
        self._warnings.extend(data.warnings)
        return data
    
    def _extract_other_components(self):
        """Extract from Other components sheet."""
        if "other_components" not in self._workbook:
            self._warnings.append("Other components sheet not available")
            return None
        
        extractor = OtherComponentsExtractor(self._workbook["other_components"])
        data = extractor.extract()
        self._warnings.extend(data.warnings)
        return data
    
    def _extract_delivery_order(self):
        """Extract from Delivery order sheet."""
        if "delivery_order" not in self._workbook:
            self._warnings.append("Delivery order sheet not available")
            return None
        
        extractor = DeliveryOrderExtractor(self._workbook["delivery_order"])
        data = extractor.extract()
        self._warnings.extend(data.warnings)
        return data
    
    def _extract_machine_info(self):
        """Extract from Machine info sheet."""
        if "machine_info" not in self._workbook:
            # This is optional, don't warn
            return None
        
        extractor = MachineInfoExtractor(self._workbook["machine_info"])
        data = extractor.extract()
        self._warnings.extend(data.warnings)
        return data
    
    def _get_tag_id(self, project_data) -> Tuple[str, List[str]]:
        """Get the Tag ID from project data with fallback logic."""
        warnings = []
        
        if not project_data:
            return "UNKNOWN", ["No project data available for Tag ID extraction"]
        
        # Try PROJECT REFERENCE first
        ref = project_data.project_reference
        if ref and str(ref).strip() and str(ref).strip().lower() not in ["nan", "none", "0"]:
            return str(ref).strip(), warnings
        
        # Fallback to PROJECT NAME
        name = project_data.project_name
        if name and str(name).strip() and str(name).strip().lower() not in ["nan", "none", "0"]:
            warnings.append("Using PROJECT NAME as Tag ID (PROJECT REFERENCE was empty)")
            return str(name).strip(), warnings
        
        # No valid ID found
        return "UNKNOWN", ["No valid project identifier found (both PROJECT REFERENCE and PROJECT NAME are empty)"]
