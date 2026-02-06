"""
Pydantic Models for Nesting Execution Record
=============================================

Strictly typed output models matching the JSON schema specification.
All numeric values use appropriate rounding for precision control.
"""

from datetime import datetime
from typing import Optional, List, Literal
from pydantic import BaseModel, Field, field_validator
import re


class MetaData(BaseModel):
    """Contextual traceability information."""
    
    project_ref_id: str = Field(
        ...,
        description="The Tag ID extracted from PROJECT REFERENCE or PROJECT NAME"
    )
    project_name: Optional[str] = Field(
        None,
        description="Operational reference string"
    )
    source_file_name: str = Field(
        ...,
        description="Original Excel filename"
    )
    extraction_timestamp_utc: datetime = Field(
        default_factory=datetime.utcnow,
        description="When this extraction was performed"
    )
    validation_status: Literal["OK", "WARNING", "ERROR"] = Field(
        "OK",
        description="Overall validation status of the extraction"
    )
    validation_messages: List[str] = Field(
        default_factory=list,
        description="Any validation warnings or errors"
    )
    
    @field_validator('project_ref_id')
    @classmethod
    def validate_project_ref_id(cls, v: str) -> str:
        """Validate project reference ID format."""
        if not v or v.strip() == "":
            raise ValueError("project_ref_id cannot be empty")
        return v.strip()


class InventoryImpact(BaseModel):
    """Inventory deduction and asset creation data."""
    
    utilized_sheets_count: int = Field(
        0,
        ge=0,
        description="GROSS consumption count for SAP deduction"
    )
    gross_area_m2: float = Field(
        0.0,
        ge=0,
        description="Total area of utilized panels in m²"
    )
    net_reusable_remnant_area_m2: float = Field(
        0.0,
        ge=0,
        description="Reusable material area - triggers Asset Creation"
    )


class EfficiencyMetrics(BaseModel):
    """Waste and efficiency tracking."""
    
    total_waste_m2: float = Field(0.0, ge=0)
    waste_pct: float = Field(0.0, ge=0, le=100)
    nesting_waste_m2: float = Field(0.0, ge=0)
    tech_waste_45_deg_m2: float = Field(0.0, ge=0)
    tech_waste_2x45_deg_m2: float = Field(0.0, ge=0)


class RawMaterialPanel(BaseModel):
    """Main board/panel consumption data (from Project parameters & Panels info)."""
    
    material_spec_name: str = Field(
        ...,
        description="Material specification (e.g., 'Fittings 25')"
    )
    thickness_mm: float = Field(
        ...,
        gt=0,
        description="Panel thickness in millimeters"
    )
    sheet_dim_x_mm: float = Field(
        0,
        ge=0,
        description="Panel X dimension in mm"
    )
    sheet_dim_y_mm: float = Field(
        0,
        ge=0,
        description="Panel Y dimension in mm"
    )
    inventory_impact: InventoryImpact = Field(
        default_factory=InventoryImpact
    )
    efficiency_metrics: EfficiencyMetrics = Field(
        default_factory=EfficiencyMetrics
    )


class BillingMetrics(BaseModel):
    """Contractual area calculations for billing."""
    
    total_internal_area_m2: float = Field(
        0.0,
        ge=0,
        description="Net airflow area"
    )
    total_external_area_m2: float = Field(
        0.0,
        ge=0,
        description="Surface area for paint/cladding"
    )


class ProfileConsumption(BaseModel):
    """Linear consumption for a single profile type (U, F, H, etc.)."""
    
    profile_type: str = Field(
        ...,
        description="Profile type name (e.g., 'U PROFILE', 'F PROFILE')"
    )
    thickness_mm: Optional[float] = Field(
        None,
        description="Profile thickness if specified"
    )
    total_consumption_m: float = Field(
        0.0,
        ge=0,
        description="Total consumption in meters - for inventory deduction"
    )
    remnant_generated_m: float = Field(
        0.0,
        ge=0,
        description="Remaining profile length - Asset Creation trigger"
    )
    bar_count: int = Field(
        0,
        ge=0,
        description="Number of bars used"
    )
    flange_count: int = Field(
        0,
        ge=0,
        description="Total number of flanges/pieces"
    )


class FlangeAccessories(BaseModel):
    """Flange accessories (corners, clips, etc.)."""
    
    gi_corners_qty: int = Field(0, ge=0, description="GI (Galvanized Iron) corners quantity")
    gi_corners_cost: float = Field(0.0, ge=0)
    pvc_corners_qty: int = Field(0, ge=0, description="PVC corners quantity")
    pvc_corners_cost: float = Field(0.0, ge=0)


class Consumables(BaseModel):
    """Accessory usage (silicone, tape, glue)."""
    
    silicone_consumption_kg: float = Field(0.0, ge=0)
    silicone_extra_pct: float = Field(0.0, ge=0, description="Extra allowance percentage")
    aluminum_tape_consumption_m: float = Field(0.0, ge=0)
    aluminum_tape_extra_pct: float = Field(0.0, ge=0, description="Extra allowance percentage")
    glue_junction_kg: float = Field(0.0, ge=0)
    glue_junction_extra_pct: float = Field(0.0, ge=0, description="Extra allowance percentage")
    glue_flange_kg: float = Field(0.0, ge=0)
    glue_flange_extra_pct: float = Field(0.0, ge=0, description="Extra allowance percentage")


class MachineTelemetry(BaseModel):
    """Predictive maintenance data from Machine info sheet."""
    
    blade_wear_45_m: float = Field(
        0.0,
        ge=0,
        description="Length of 45° cuts in meters"
    )
    blade_wear_90_m: float = Field(
        0.0,
        ge=0,
        description="Length of 90° cuts in meters"
    )
    blade_wear_2x45_m: float = Field(
        0.0,
        ge=0,
        description="Length of 2x45° cuts in meters"
    )
    gantry_travel_rapid_m: float = Field(
        0.0,
        ge=0,
        description="Total rapid traverse length in meters"
    )
    time_marking_sec: float = Field(0.0, ge=0)
    time_45_cuts_sec: float = Field(0.0, ge=0)
    time_90_cuts_sec: float = Field(0.0, ge=0)
    time_2x45_cuts_sec: float = Field(0.0, ge=0, description="Time for 2x45° cuts in seconds")
    time_rapid_traverse_sec: float = Field(0.0, ge=0)


class FinishedGoodsGeometry(BaseModel):
    """Dimensional data for a finished goods item."""
    
    mouth_a_x: Optional[float] = Field(None, description="Mouth A X dimension (mm)")
    mouth_a_y: Optional[float] = Field(None, description="Mouth A Y dimension (mm)")
    mouth_a_fl: Optional[str] = Field(None, description="Mouth A flange type")
    mouth_b_x: Optional[float] = Field(None, description="Mouth B X dimension (mm)")
    mouth_b_y: Optional[float] = Field(None, description="Mouth B Y dimension (mm)")
    mouth_b_fl: Optional[str] = Field(None, description="Mouth B flange type")
    length_m: Optional[float] = Field(None, description="Length in meters")


class FinishedGoodsLine(BaseModel):
    """A single line item from the Delivery Order."""
    
    line_id: int = Field(..., description="Unique line identifier")
    tag_id: Optional[str] = Field(None, description="TAG reference if present")
    description: str = Field("", description="Part description")
    geometry: FinishedGoodsGeometry = Field(
        default_factory=FinishedGoodsGeometry
    )
    qty_produced: int = Field(1, ge=0)
    internal_area_m2: float = Field(0.0, ge=0)
    external_area_m2: float = Field(0.0, ge=0)


class NestingExecutionRecord(BaseModel):
    """
    The complete output record for a parsed nesting file.
    
    This is the "Authoritative Truth" extracted from Eurosoft CutExpert.
    """
    
    meta_data: MetaData
    raw_material_panel: RawMaterialPanel
    billing_metrics: BillingMetrics = Field(
        default_factory=BillingMetrics
    )
    profiles_and_flanges: List[ProfileConsumption] = Field(
        default_factory=list
    )
    flange_accessories: FlangeAccessories = Field(
        default_factory=FlangeAccessories
    )
    consumables: Consumables = Field(
        default_factory=Consumables
    )
    machine_telemetry: MachineTelemetry = Field(
        default_factory=MachineTelemetry
    )
    finished_goods_manifest: List[FinishedGoodsLine] = Field(
        default_factory=list
    )
    
    def model_dump_rounded(self, **kwargs) -> dict:
        """
        Export with proper rounding as per specification:
        - Dimensions: 2 decimal places
        - Weights: 4 decimal places
        """
        data = self.model_dump(**kwargs)
        return self._round_recursively(data)
    
    @staticmethod
    def _round_recursively(obj, decimals=2):
        """Recursively round all float values."""
        if isinstance(obj, dict):
            return {k: NestingExecutionRecord._round_recursively(v, decimals) 
                    for k, v in obj.items()}
        elif isinstance(obj, list):
            return [NestingExecutionRecord._round_recursively(item, decimals) 
                    for item in obj]
        elif isinstance(obj, float):
            return round(obj, decimals)
        return obj


class AttachmentInfo(BaseModel):
    """Information about a file attachment."""
    target: str
    row_id: int
    name: str


class ParsingResult(BaseModel):
    """
    Wrapper for parsing operation result.
    
    Allows partial success with warnings.
    """
    
    status: Literal["SUCCESS", "PARTIAL", "ERROR", "DUPLICATE", "VALIDATION_ERROR", "PARSE_ERROR"] = "SUCCESS"
    data: Optional[NestingExecutionRecord] = None
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    source_file: str = ""
    processing_time_ms: float = 0.0
    
    # New fields for v2 integration
    request_id: Optional[str] = None
    tag_id: Optional[str] = None
    nest_session_id: Optional[str] = None
    nesting_row_id: Optional[int] = None
    tag_row_id: Optional[int] = None
    file_hash: Optional[str] = None
    attachments: List[AttachmentInfo] = Field(default_factory=list)
    expected_consumption_m2: Optional[float] = None
    wastage_percentage: Optional[float] = None
    trace_id: Optional[str] = None
    
    # Error fields
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    exception_id: Optional[str] = None
    existing_nest_session_id: Optional[str] = None


class ValidationResult(BaseModel):
    """Result of a validation check."""
    is_valid: bool
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    tag_row_id: Optional[int] = None
    tag_lpo_ref: Optional[str] = None
    # LPO enrichment fields (v1.6.7)
    lpo_row_id: Optional[int] = None
    brand: Optional[str] = None
    area_type: Optional[str] = None  # "Internal" or "External"
    lpo_folder_url: Optional[str] = None  # v1.6.7: Custom SharePoint URL
    # Production Planning enrichment fields (v1.6.7)
    planning_row_id: Optional[int] = None
    planned_date: Optional[str] = None


