"""
Other Components Extractor
===========================

Extracts consumables data from the "Other components" sheet.
This includes silicone, aluminum tape, glue, and other accessories.

Sheet Structure (side-by-side components):
- Columns 1-7: SILICONE (left side)
- Columns 9-15: JUNCTION GLUE (right side)  
- Row 12+: ALUMINUM TAPE (left) and FLANGES GLUE (right)

Anchor-based extraction with column-aware value finding.
"""

import pandas as pd
import logging
from typing import Optional, List, Tuple
from dataclasses import dataclass, field

from ..anchor_finder import AnchorFinder

logger = logging.getLogger(__name__)


@dataclass
class OtherComponentsData:
    """Data extracted from Other components sheet."""
    
    # Silicone
    silicone_consumption_kg: float = 0.0
    silicone_extra_pct: float = 0.0
    silicone_cost: float = 0.0
    
    # Aluminum Tape
    aluminum_tape_consumption_m: float = 0.0
    aluminum_tape_extra_pct: float = 0.0
    aluminum_tape_cost: float = 0.0
    
    # Glue
    glue_junction_kg: float = 0.0
    glue_junction_extra_pct: float = 0.0
    glue_junction_cost: float = 0.0
    
    glue_flange_kg: float = 0.0
    glue_flange_extra_pct: float = 0.0
    glue_flange_cost: float = 0.0
    
    # Totals
    total_consumables_cost: float = 0.0
    
    # Validation
    warnings: List[str] = field(default_factory=list)


class OtherComponentsExtractor:
    """
    Extracts consumables data from the Other components sheet.
    
    The sheet has a side-by-side layout:
    - Left columns (1-7): SILICONE, ALUMINUM TAPE
    - Right columns (9-15): JUNCTION GLUE, FLANGES GLUE
    
    Anchor-based extraction with column-area awareness.
    """
    
    SHEET_NAME = "Other components"
    
    # Component configurations with expected column areas
    # column_area: "left" = columns 1-7, "right" = columns 9-15
    COMPONENTS = {
        "silicone": {
            "header": "SILICONE",
            "total_anchor": "Total need of silicone",
            "consumption_field": "silicone_consumption_kg",
            "extra_field": "silicone_extra_pct",
            "column_area": "left",
        },
        "aluminum_tape": {
            "header": "ALUMINUM TAPE", 
            "total_anchor": "Total need of aluminum tape",
            "consumption_field": "aluminum_tape_consumption_m",
            "extra_field": "aluminum_tape_extra_pct",
            "column_area": "left",
        },
        "glue_junction": {
            "header": "JUNCTION GLUE",
            "total_anchor": "Total glue for Junctions",
            "consumption_field": "glue_junction_kg",
            "extra_field": "glue_junction_extra_pct",
            "column_area": "right",
        },
        "glue_flange": {
            "header": "FLANGES GLUE",
            "total_anchor": "Total glue for Flanges",
            "consumption_field": "glue_flange_kg",
            "extra_field": "glue_flange_extra_pct",
            "column_area": "right",
        },
    }
    
    def __init__(self, df: pd.DataFrame):
        """
        Initialize extractor.
        
        Args:
            df: DataFrame of the Other components sheet
        """
        self.df = df
        self.finder = AnchorFinder(df, self.SHEET_NAME)
    
    def extract(self) -> OtherComponentsData:
        """
        Extract all consumables data from the sheet.
        
        Returns:
            OtherComponentsData with extracted values
        """
        data = OtherComponentsData()
        
        # Extract each component
        for component_name, config in self.COMPONENTS.items():
            self._extract_component(data, config)
        
        # Extract total cost
        data.total_consumables_cost = self._extract_total_cost()
        
        return data
    
    def _extract_component(self, data: OtherComponentsData, config: dict) -> None:
        """Extract a single component's consumption and extra values."""
        
        total_anchor = config["total_anchor"]
        consumption_field = config["consumption_field"]
        extra_field = config["extra_field"]
        column_area = config["column_area"]
        
        # Define column ranges based on area
        if column_area == "left":
            col_min, col_max = 0, 8
        else:  # right
            col_min, col_max = 8, 16
        
        # Find the total anchor
        pos = self.finder.find_anchor(total_anchor)
        if not pos:
            logger.debug(f"Anchor '{total_anchor}' not found")
            return
        
        row, anchor_col = pos
        
        # Verify anchor is in expected column area
        if not (col_min <= anchor_col < col_max):
            logger.debug(f"Anchor '{total_anchor}' found at col {anchor_col}, expected in {column_area} area")
            return
        
        # Find the value in this row within the same column area
        consumption = self._find_value_in_row_area(row, col_min, col_max)
        if consumption is not None:
            setattr(data, consumption_field, consumption)
            logger.debug(f"Extracted {consumption_field} = {consumption}")
        
        # Look for "Extra" in the row above within the same column area
        extra_pct = self._find_extra_value(row, col_min, col_max)
        if extra_pct is not None:
            setattr(data, extra_field, extra_pct)
            logger.debug(f"Extracted {extra_field} = {extra_pct}")
    
    def _find_value_in_row_area(self, row: int, col_min: int, col_max: int) -> Optional[float]:
        """
        Find the numeric value in a row within a specific column area.
        
        Strategy: Find the unit marker (Kg, mt.), then get adjacent value.
        """
        unit_markers = ["kg", "mt.", "mt"]
        
        for col in range(col_min, min(col_max, len(self.df.columns))):
            val = self.df.iloc[row, col]
            if pd.notna(val):
                val_str = str(val).strip().lower()
                if val_str in unit_markers:
                    # Value is typically 1 column after the unit
                    for offset in [1, 2]:
                        check_col = col + offset
                        if col_min <= check_col < col_max and check_col < len(self.df.columns):
                            num_val = self.df.iloc[row, check_col]
                            if pd.notna(num_val):
                                try:
                                    return float(str(num_val).replace(',', ''))
                                except (ValueError, TypeError):
                                    continue
        
        # Fallback: look for any numeric value after column offset
        for col in range(col_min + 5, min(col_max, len(self.df.columns))):
            val = self.df.iloc[row, col]
            if pd.notna(val):
                try:
                    num = float(str(val).replace(',', ''))
                    if num >= 0:
                        return num
                except (ValueError, TypeError):
                    continue
        
        return None
    
    def _find_extra_value(self, total_row: int, col_min: int, col_max: int) -> Optional[float]:
        """
        Find the "Extra" value which is typically 1 row above the total row.
        """
        if total_row < 1:
            return None
        
        # Check row above for "Extra" anchor within the column area
        for col in range(col_min, min(col_max, len(self.df.columns))):
            val = self.df.iloc[total_row - 1, col]
            if pd.notna(val) and "extra" in str(val).lower():
                # Found "Extra", now find the value in same row within the area
                return self._find_value_in_row_area(total_row - 1, col_min, col_max)
        
        return None
    
    def _extract_total_cost(self) -> float:
        """Extract total consumables cost."""
        pos = self.finder.find_anchor("TOTAL COST OF OTHER")
        if pos:
            # Total cost spans the full row
            val = self._find_value_in_row_area(pos[0], 0, 16)
            if val is not None:
                return val
        return 0.0
