"""
Machine Info Extractor
======================

Extracts machine telemetry data from the "Machine info" sheet.
Used for predictive maintenance tracking.
"""

import pandas as pd
import logging
from typing import List
from dataclasses import dataclass, field

from ..anchor_finder import AnchorFinder

logger = logging.getLogger(__name__)


@dataclass
class MachineInfoData:
    """Data extracted from Machine info sheet."""
    
    # Blade wear (cut lengths)
    length_45_cuts_m: float = 0.0
    length_90_cuts_m: float = 0.0
    length_2x45_cuts_m: float = 0.0
    
    # Gantry/traverse
    rapid_traverse_length_m: float = 0.0
    
    # Times
    time_marking_sec: float = 0.0
    time_45_cuts_sec: float = 0.0
    time_90_cuts_sec: float = 0.0
    time_2x45_cuts_sec: float = 0.0
    time_rapid_traverse_sec: float = 0.0
    
    # Loading/unloading
    loading_time_sec: float = 0.0
    
    # Costs
    machine_cost_per_hour: float = 0.0
    
    # Validation
    warnings: List[str] = field(default_factory=list)


class MachineInfoExtractor:
    """
    Extracts machine telemetry data from the Machine info sheet.
    
    Data is used for:
    - Blade wear tracking
    - Machine utilization analysis
    - Predictive maintenance
    """
    
    SHEET_NAME = "Machine info"
    
    # Anchor configurations
    ANCHORS = {
        # Cut lengths
        "length_45_cuts_m": ("Length of 45° cuts", 0, 1, float),
        "length_90_cuts_m": ("Length of 90° cuts", 0, 1, float),
        "length_2x45_cuts_m": ("Length of 2x45° cuts", 0, 1, float),
        "rapid_traverse_length_m": ("Total movements length", 0, 1, float),
        
        # Times
        "time_marking_sec": ("Time for marking", 0, 1, float),
        "time_45_cuts_sec": ("Time for 45° cuts", 0, 1, float),
        "time_90_cuts_sec": ("Time for 90° cuts", 0, 1, float),
        "time_2x45_cuts_sec": ("Time for 2x45° cuts", 0, 1, float),
        "time_rapid_traverse_sec": ("Time for rapid traverse", 0, 1, float),
        
        # Other
        "loading_time_sec": ("Loading/unloading time", 0, 1, float),
        "machine_cost_per_hour": ("Machine cost per hour", 0, 1, float),
    }
    
    def __init__(self, df: pd.DataFrame):
        """
        Initialize extractor.
        
        Args:
            df: DataFrame of the Machine info sheet
        """
        self.df = df
        self.finder = AnchorFinder(df, self.SHEET_NAME)
    
    def extract(self) -> MachineInfoData:
        """
        Extract all machine telemetry data from the sheet.
        
        Returns:
            MachineInfoData with extracted values
        """
        data = MachineInfoData()
        
        for field_name, (anchor, row_off, col_off, cast_type) in self.ANCHORS.items():
            try:
                # Try multiple column offsets
                for offset in [col_off, 2, 3, 4, 5]:
                    value = self.finder.get_value_by_anchor(
                        anchor_text=anchor,
                        row_offset=row_off,
                        col_offset=offset,
                        cast_type=cast_type,
                        required=False,
                        default=None
                    )
                    
                    if value is not None and value > 0:
                        setattr(data, field_name, value)
                        break
                        
            except Exception as e:
                logger.debug(f"Error extracting {field_name}: {e}")
        
        # Also try to extract from Project parameters if machine info sheet 
        # doesn't have all values (they're duplicated in some exports)
        
        return data
