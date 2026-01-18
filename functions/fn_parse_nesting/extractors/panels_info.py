"""
Panels Info Extractor
=====================

Extracts data from the "Panels info" sheet.
Contains billing metrics and additional area calculations.
"""

import pandas as pd
import logging
from typing import Optional, List
from dataclasses import dataclass, field

from ..anchor_finder import AnchorFinder

logger = logging.getLogger(__name__)


@dataclass
class PanelsInfoData:
    """Data extracted from Panels info sheet."""
    
    # Material (may duplicate Project parameters)
    material: Optional[str] = None
    thickness_mm: float = 0.0
    dimension_x_mm: float = 0.0
    dimension_y_mm: float = 0.0
    
    # Areas
    total_utilized_area_m2: float = 0.0
    total_reusable_area_m2: float = 0.0
    gross_utilized_area_m2: float = 0.0
    external_dimensions_area_m2: float = 0.0
    internal_dimensions_area_m2: float = 0.0
    
    # Waste
    wastage_nesting_m2: float = 0.0
    wastage_45_and_2x45_m2: float = 0.0
    total_wastage_m2: float = 0.0
    
    # Percentages
    gross_area_pct: float = 0.0
    external_area_pct: float = 0.0
    internal_area_pct: float = 0.0
    nesting_waste_pct: float = 0.0
    total_waste_pct: float = 0.0
    
    # Counts
    utilized_panels_qty: int = 0
    
    # Validation
    warnings: List[str] = field(default_factory=list)


class PanelsInfoExtractor:
    """
    Extracts data from the Panels info sheet.
    
    This sheet contains more detailed area calculations than Project parameters,
    including billing-relevant metrics like internal/external areas.
    """
    
    SHEET_NAME = "Panels info"
    
    # Anchor configurations
    ANCHORS = {
        # Material info (cross-check with Project parameters)
        "material": ("Material", 1, 0, str),  # Value is below the label
        "thickness_mm": ("Thickness (mm)", 1, 0, float),
        "dimension_x_mm": ("Dimension X (mm)", 1, 0, float),
        "dimension_y_mm": ("Dimension Y (mm)", 1, 0, float),
        
        # Area values - these are in the first column below their labels
        "total_utilized_area_m2": ("Total area of utilized panels", 1, 0, float),
        "total_reusable_area_m2": ("Total area reusable material", 1, 0, float),
        "gross_utilized_area_m2": ("Gross area of utilized panels", 1, 0, float),
        "external_dimensions_area_m2": ("Area of external dimensions", 1, 0, float),
        "internal_dimensions_area_m2": ("Area of internal dimensions", 1, 0, float),
        "wastage_nesting_m2": ("Wastage due to nesting", 1, 0, float),
        "wastage_45_and_2x45_m2": ("Wastage due to 45° e 2x45°", 1, 0, float),
        "total_wastage_m2": ("Total wastage", 1, 0, float),
        
        # Count - look for "Utilized Panels" header and value below
        "utilized_panels_qty": ("Utilized Panels", 1, 0, int),
    }
    
    def __init__(self, df: pd.DataFrame):
        """
        Initialize extractor.
        
        Args:
            df: DataFrame of the Panels info sheet
        """
        self.df = df
        self.finder = AnchorFinder(df, self.SHEET_NAME)
    
    def extract(self) -> PanelsInfoData:
        """
        Extract all data from the sheet.
        
        Returns:
            PanelsInfoData with all extracted values
        """
        data = PanelsInfoData()
        
        # The Panels info sheet has a complex layout with values often
        # appearing in unexpected positions. We need to handle this carefully.
        
        # First try standard anchor extraction
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
                    
            except Exception as e:
                logger.debug(f"Standard extraction failed for {field_name}: {e}")
        
        # Try alternative extraction for key values that might be in different positions
        self._extract_with_fallbacks(data)
        
        # Extract percentages (usually in adjacent columns)
        self._extract_percentages(data)
        
        return data
    
    def _extract_with_fallbacks(self, data: PanelsInfoData) -> None:
        """
        Try alternative positions for key values.
        
        The Panels info sheet sometimes has values in different column positions.
        """
        # Try extracting material from "Fittings" or similar material names
        if not data.material:
            # Material value might be directly after "Material" label
            pos = self.finder.find_anchor("Material")
            if pos:
                # Check multiple column offsets
                for col_off in [1, 4, 5]:
                    val = self.finder.get_value_at(pos[0], pos[1] + col_off, str, None)
                    if val and val.lower() not in ["nan", "none", ""]:
                        data.material = val
                        break
        
        # Extract areas from table format
        # Look for pattern: Label | Value | (m2) | % | ...
        area_patterns = [
            ("Total area of utilized panels", "total_utilized_area_m2"),
            ("Gross area of utilized panels", "gross_utilized_area_m2"),
            ("Area of external dimensions", "external_dimensions_area_m2"),
            ("Area of internal dimensions", "internal_dimensions_area_m2"),
        ]
        
        for anchor_text, field_name in area_patterns:
            if getattr(data, field_name, 0) == 0:
                # Try finding the value in the same row
                pos = self.finder.find_anchor(anchor_text)
                if pos:
                    # Value is often 1 row below, first non-empty column
                    for col_off in range(-1, 3):
                        val = self.finder.get_value_at(
                            pos[0] + 1, pos[1] + col_off, float, None
                        )
                        if val is not None and val > 0:
                            setattr(data, field_name, val)
                            break
    
    def _extract_percentages(self, data: PanelsInfoData) -> None:
        """Extract percentage values from adjacent columns."""
        
        # Percentages are typically in the column after the m2 value
        pct_patterns = [
            ("Gross area of utilized panels", "gross_area_pct"),
            ("Area of external dimensions", "external_area_pct"),
            ("Area of internal dimensions", "internal_area_pct"),
            ("Wastage due to nesting", "nesting_waste_pct"),
            ("Total wastage", "total_waste_pct"),
        ]
        
        for anchor_text, field_name in pct_patterns:
            pos = self.finder.find_anchor(anchor_text)
            if pos:
                # Check for "%" column or value
                for row_off in [0, 1]:
                    for col_off in [4, 5, 6]:
                        val = self.finder.get_value_at(
                            pos[0] + row_off, pos[1] + col_off, float, None
                        )
                        if val is not None and 0 <= val <= 100:
                            setattr(data, field_name, val)
                            break
