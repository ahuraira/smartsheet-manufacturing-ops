"""
Project Parameters Extractor
=============================

Extracts data from the "Project parameters" sheet.
This is the primary sheet containing project identification and key metrics.
"""

import pandas as pd
import logging
from typing import Optional, Tuple, List
from dataclasses import dataclass, field

from ..anchor_finder import AnchorFinder, AnchorNotFoundError

logger = logging.getLogger(__name__)


@dataclass
class ProjectParametersData:
    """Data extracted from Project parameters sheet."""
    
    # Identity
    project_name: Optional[str] = None
    project_reference: Optional[str] = None
    
    # Material Specification
    material: Optional[str] = None
    thickness_mm: float = 0.0
    sheet_dim_x_mm: float = 0.0
    sheet_dim_y_mm: float = 0.0
    
    # Inventory Impact
    utilized_sheets: int = 0
    total_reusable_area_m2: float = 0.0
    
    # Waste Metrics
    wastage_nesting_m2: float = 0.0
    wastage_45_deg_m2: float = 0.0
    wastage_2x45_deg_m2: float = 0.0
    
    # Machine Telemetry
    time_marking_sec: float = 0.0
    time_45_cuts_sec: float = 0.0
    time_90_cuts_sec: float = 0.0
    time_rapid_traverse_sec: float = 0.0
    time_2x45_cuts_sec: float = 0.0
    
    length_45_cuts_m: float = 0.0
    length_90_cuts_m: float = 0.0
    length_rapid_traverse_m: float = 0.0
    length_2x45_cuts_m: float = 0.0
    
    # Validation
    warnings: List[str] = field(default_factory=list)


class ProjectParametersExtractor:
    """
    Extracts data from the Project parameters sheet.
    
    Anchor-based extraction:
    - "PROJECT NAME" -> Project name
    - "PROJECT REFERENCE" -> Project reference (Tag ID)
    - "Material" -> Material specification
    - "Thickness" -> Panel thickness
    - etc.
    """
    
    SHEET_NAME = "Project parameters"
    
    # Anchor configurations: (anchor_text, row_offset, col_offset, cast_type)
    ANCHORS = {
        "project_name": ("PROJECT NAME", 0, 5, str),
        "project_reference": ("PROJECT REFERENCE", 0, 5, str),
        "material": ("Material", 0, 5, str),
        "thickness_mm": ("Thickness", 0, 5, float),
        "sheet_dim_x_mm": ("Sheet dimension X", 0, 5, float),
        "sheet_dim_y_mm": ("Sheet dimension Y", 0, 5, float),
        "utilized_sheets": ("Utilized sheets", 0, 5, int),
        "total_reusable_area_m2": ("Total area reusable", 0, 5, float),
        "wastage_nesting_m2": ("Wastage due to nesting", 0, 5, float),
        "wastage_45_deg_m2": ("Wastage due to 45°", 0, 5, float),
        "wastage_2x45_deg_m2": ("Wastage due to 2x45°", 0, 5, float),
        "time_marking_sec": ("Time for marking", 0, 5, float),
        "time_45_cuts_sec": ("Time for 45° cuts", 0, 5, float),
        "time_90_cuts_sec": ("Time for 90° cuts", 0, 5, float),
        "time_rapid_traverse_sec": ("Time for rapid traverse", 0, 5, float),
        "time_2x45_cuts_sec": ("Time for 2x45° cuts", 0, 5, float),
        "length_45_cuts_m": ("Length of 45° cuts", 0, 5, float),
        "length_90_cuts_m": ("Length of 90° cuts", 0, 5, float),
        "length_rapid_traverse_m": ("Length of rapid traverse", 0, 5, float),
        "length_2x45_cuts_m": ("Length of 2x45° cuts", 0, 5, float),
    }
    
    def __init__(self, df: pd.DataFrame):
        """
        Initialize extractor.
        
        Args:
            df: DataFrame of the Project parameters sheet
        """
        self.df = df
        self.finder = AnchorFinder(df, self.SHEET_NAME)
    
    def extract(self) -> ProjectParametersData:
        """
        Extract all data from the sheet.
        
        Returns:
            ProjectParametersData with all extracted values
        """
        data = ProjectParametersData()
        
        for field_name, (anchor, row_off, col_off, cast_type) in self.ANCHORS.items():
            try:
                value = self.finder.get_value_by_anchor(
                    anchor_text=anchor,
                    row_offset=row_off,
                    col_offset=col_off,
                    cast_type=cast_type,
                    required=False,
                    default=None
                )
                
                if value is not None:
                    setattr(data, field_name, value)
                else:
                    logger.debug(f"No value found for {field_name}")
                    
            except Exception as e:
                logger.warning(f"Error extracting {field_name}: {e}")
                data.warnings.append(f"Failed to extract {field_name}: {str(e)}")
        
        # Validate project identification
        data = self._validate_project_id(data)
        
        return data
    
    def _validate_project_id(self, data: ProjectParametersData) -> ProjectParametersData:
        """
        Validate project identification.
        
        The Tag ID should be in PROJECT REFERENCE, but some operators
        put it in PROJECT NAME instead.
        """
        has_ref = bool(data.project_reference and str(data.project_reference).strip())
        has_name = bool(data.project_name and str(data.project_name).strip())
        
        if not has_ref and not has_name:
            data.warnings.append("WARNING: Both PROJECT REFERENCE and PROJECT NAME are empty")
        elif not has_ref and has_name:
            # Try to use PROJECT NAME as fallback
            data.warnings.append(
                "INFO: PROJECT REFERENCE is empty, using PROJECT NAME as identifier"
            )
        
        # Check for TAG-XXXX pattern
        ref = data.project_reference or data.project_name or ""
        
        import re
        if ref and not re.match(r'^TAG-\d+', str(ref), re.IGNORECASE):
            # Not a critical error, just a warning
            data.warnings.append(
                f"INFO: Project identifier '{ref}' does not match expected TAG-XXXX pattern"
            )
        
        return data
    
    def get_tag_id(self) -> Tuple[str, List[str]]:
        """
        Get the Tag ID with fallback logic.
        
        Returns:
            Tuple of (tag_id, list_of_warnings)
        """
        data = self.extract()
        
        # Priority: PROJECT REFERENCE > PROJECT NAME
        if data.project_reference and str(data.project_reference).strip():
            ref = str(data.project_reference).strip()
            if ref != "0" and ref.lower() != "nan":
                return ref, data.warnings
        
        if data.project_name and str(data.project_name).strip():
            name = str(data.project_name).strip()
            if name != "0" and name.lower() != "nan":
                return name, data.warnings
        
        # No valid ID found
        return "UNKNOWN", data.warnings + ["ERROR: No valid project identifier found"]
