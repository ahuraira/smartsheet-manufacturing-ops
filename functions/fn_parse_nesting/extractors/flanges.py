"""
Flanges Extractor
=================

Extracts profile/flange consumption data from the "Flanges" sheet.

This is the most complex sheet to parse due to its block-based structure:
- Multiple profile types stacked vertically
- Each block starts with "PROFILE TYPE" marker
- Within each block: lengths, quantities, totals, and remnants
"""

import pandas as pd
import logging
from typing import List, Optional, Tuple
from dataclasses import dataclass, field

from ..anchor_finder import AnchorFinder

logger = logging.getLogger(__name__)


@dataclass
class ProfileData:
    """Data for a single profile type."""
    
    profile_type: str = ""
    thickness_mm: Optional[float] = None
    total_consumption_m: float = 0.0
    remnant_generated_m: float = 0.0
    bar_count: int = 0
    flange_count: int = 0
    cost_per_m: Optional[float] = None


@dataclass
class FlangesData:
    """Data extracted from Flanges sheet."""
    
    profiles: List[ProfileData] = field(default_factory=list)
    
    # Flange accessories
    gi_corners_qty: int = 0
    gi_corners_cost: float = 0.0
    pvc_corners_qty: int = 0
    pvc_corners_cost: float = 0.0
    
    total_cost: float = 0.0
    warnings: List[str] = field(default_factory=list)


class FlangesExtractor:
    """
    Extracts profile/flange data from the Flanges sheet.
    
    Block Detection Strategy:
    1. Find ALL occurrences of "PROFILE TYPE" to identify block starts
    2. For each block, extract:
       - Profile name (cell below "PROFILE TYPE")
       - Thickness (if present)
       - Total length (sum of TOTAL LENGHT (mm) column, converted to m)
       - Remnant (from "Remaining * profile" anchor)
       - Bar count (from "N° of bars" anchor)
       - Flange count (from "Total number of * Flanges" anchor)
    """
    
    SHEET_NAME = "Flanges"
    
    # Known profile types
    KNOWN_PROFILES = [
        "JOINT PROFILE",
        "JOINT PROFILE - PVC",
        "BAYONET",
        "U PROFILE",
        "F PROFILE",
        "H PROFILE",
        "OTHER PROFILE",
    ]
    
    def __init__(self, df: pd.DataFrame):
        """
        Initialize extractor.
        
        Args:
            df: DataFrame of the Flanges sheet
        """
        self.df = df
        self.finder = AnchorFinder(df, self.SHEET_NAME)
    
    def extract(self) -> FlangesData:
        """
        Extract all profile data from the sheet.
        
        Returns:
            FlangesData with list of profiles
        """
        data = FlangesData()
        
        # Find all profile blocks
        block_markers = self.finder.find_all_anchors("PROFILE TYPE")
        
        if not block_markers:
            data.warnings.append("No PROFILE TYPE markers found in Flanges sheet")
            return data
        
        logger.info(f"Found {len(block_markers)} profile blocks in Flanges sheet")
        
        # Determine block boundaries
        blocks = self._identify_blocks(block_markers)
        
        # Extract each profile block
        for start_row, end_row, profile_row, profile_col in blocks:
            try:
                profile = self._extract_profile_block(
                    start_row, end_row, profile_row, profile_col
                )
                if profile and profile.profile_type:
                    # Only include if there's actual consumption
                    if profile.total_consumption_m > 0 or profile.flange_count > 0:
                        data.profiles.append(profile)
                    else:
                        logger.debug(
                            f"Skipping empty profile block: {profile.profile_type}"
                        )
            except Exception as e:
                logger.warning(f"Error extracting profile block at row {start_row}: {e}")
                data.warnings.append(f"Failed to extract profile at row {start_row}")
        
        # Extract flange accessories (GI Corners, PVC Corners)
        self._extract_accessories(data)
        
        # Extract total cost
        data.total_cost = self._extract_total_cost()
        
        return data
    
    def _identify_blocks(
        self, 
        markers: List[Tuple[int, int]]
    ) -> List[Tuple[int, int, int, int]]:
        """
        Identify block boundaries from PROFILE TYPE markers.
        
        Returns:
            List of (start_row, end_row, profile_name_row, profile_name_col)
        """
        blocks = []
        
        for i, (row, col) in enumerate(markers):
            # Start row is the marker row
            start_row = row
            
            # End row is either the next marker or end of data
            if i + 1 < len(markers):
                end_row = markers[i + 1][0]
            else:
                # Find end by looking for empty section
                end_row = self._find_block_end(start_row)
            
            # Profile name is typically in the row below the marker, same column
            profile_row = row + 1
            profile_col = col
            
            blocks.append((start_row, end_row, profile_row, profile_col))
        
        return blocks
    
    def _find_block_end(self, start_row: int) -> int:
        """Find where a block ends (consecutive empty rows or EOF)."""
        empty_count = 0
        max_empty = 5  # Consider block ended after 5 consecutive empty rows
        
        for row in range(start_row + 1, len(self.df)):
            # Check if row is mostly empty
            row_data = self.df.iloc[row]
            non_empty = sum(1 for v in row_data if pd.notna(v) and str(v).strip())
            
            if non_empty <= 2:
                empty_count += 1
            else:
                empty_count = 0
            
            if empty_count >= max_empty:
                return row - max_empty
        
        return len(self.df)
    
    def _extract_profile_block(
        self,
        start_row: int,
        end_row: int,
        profile_row: int,
        profile_col: int
    ) -> Optional[ProfileData]:
        """
        Extract data for a single profile block.
        
        Based on actual file structure:
        - Row with "PROFILE TYPE" also has the profile name at column offset +5 or +6
        - "Total length" is a summary row with value already in meters at column offset +2 or +3
        - "Remaining X profile (mt.)" has remnant value
        - "N° of bars" has bar count
        - "Total number of X" has flange count
        """
        profile = ProfileData()
        
        # Extract profile type name - it's in the SAME row as "PROFILE TYPE", at an offset
        # Based on actual data: Row 177 Col 1: PROFILE TYPE, Col 6: U PROFILE
        profile_name = None
        for col_offset in [5, 6, 7]:
            name = self.finder.get_value_at(start_row, profile_col + col_offset, str, None)
            if name and name.upper() not in ["NAN", "NONE", "MM", ""]:
                # Check if it's a known profile type
                name_upper = name.upper()
                if any(p in name_upper for p in ["PROFILE", "BAYONET"]):
                    profile_name = name
                    break
        
        if not profile_name:
            # Fallback: try the row below (original logic)
            profile_name = self.finder.get_value_at(profile_row, profile_col, str, None)
        
        if not profile_name or profile_name.upper() in ["NAN", "NONE", ""]:
            logger.debug(f"No profile name found at row {start_row}")
            return None
        
        profile.profile_type = str(profile_name).strip()
        logger.debug(f"Extracting profile: {profile.profile_type} (rows {start_row}-{end_row})")
        
        # Extract thickness (usually in the same row as "PROFILE TYPE" marker)
        thickness_col = self._find_thickness_column(start_row)
        if thickness_col is not None:
            profile.thickness_mm = self.finder.get_value_at(
                start_row, thickness_col, float, None
            )
        
        # Find "Total length" in this block - value is already in meters
        # Located at col 17 "Total length" with value at col 19-20
        profile.total_consumption_m = self._extract_total_length(start_row, end_row)
        
        # Extract remnant - look for "Remaining * profile"
        profile.remnant_generated_m = self._extract_remnant(start_row, end_row, profile.profile_type)
        
        # Extract bar count
        profile.bar_count = self._extract_bar_count(start_row, end_row)
        
        # Extract flange count
        profile.flange_count = self._extract_flange_count(start_row, end_row)
        
        # Extract cost per meter
        profile.cost_per_m = self._extract_cost_per_m(start_row, end_row)
        
        logger.debug(f"  -> consumption={profile.total_consumption_m}m, remnant={profile.remnant_generated_m}m, bars={profile.bar_count}, flanges={profile.flange_count}")
        
        return profile
    
    def _extract_total_length(self, start_row: int, end_row: int) -> float:
        """
        Extract total consumption length for this profile block.
        
        Robust anchor-based approach:
        1. Find "Total length" text in the block
        2. In the same row, find "mt." (the unit marker)
        3. Get the numeric value after "mt." 
        """
        # Search within the block for "Total length"
        for row in range(start_row, min(end_row, start_row + 25)):
            total_length_col = None
            mt_col = None
            
            # First pass: find "Total length" and "mt." positions in this row
            for col in range(len(self.df.columns)):
                val = self.df.iloc[row, col]
                if pd.notna(val):
                    val_str = str(val).strip().lower()
                    if "total length" in val_str:
                        total_length_col = col
                    elif val_str == "mt.":
                        mt_col = col
            
            # If we found both markers in this row, look for the value after "mt."
            if total_length_col is not None and mt_col is not None:
                # The numeric value should be right after "mt."
                for offset in range(1, 4):
                    if mt_col + offset < len(self.df.columns):
                        raw_val = self.df.iloc[row, mt_col + offset]
                        if pd.notna(raw_val):
                            try:
                                length_val = float(str(raw_val).replace(',', ''))
                                if length_val >= 0:
                                    return length_val
                            except (ValueError, TypeError):
                                continue
        
        # Fallback: sum TOTAL LENGHT (mm) column and convert to meters
        total_length_col = self.finder.find_column_index("TOTAL LENGHT", search_row=start_row + 1)
        if total_length_col is not None:
            total_length_mm = self.finder.sum_column_values(total_length_col, start_row + 2, end_row)
            return total_length_mm / 1000.0
        
        return 0.0
    
    def _find_thickness_column(self, row: int) -> Optional[int]:
        """Find column containing thickness value (usually with 'mm' nearby)."""
        for col in range(len(self.df.columns)):
            val = self.df.iloc[row, col]
            if pd.notna(val) and str(val).strip().lower() == "mm":
                # Thickness is usually 1 column to the left
                return col - 1
        return None
    
    def _extract_remnant(
        self, 
        start_row: int, 
        end_row: int,
        profile_type: str
    ) -> float:
        """
        Extract remnant value for a profile block.
        
        Looks for patterns like:
        - "Remaining U profile (mt.)"
        - "Remaining JP profile (mt.)"
        """
        # Create a sub-finder for just this block
        block_df = self.df.iloc[start_row:end_row]
        block_finder = AnchorFinder(block_df, f"Flanges-{profile_type}")
        
        # Try various anchor patterns
        patterns = [
            "Remaining",  # Generic pattern
            f"Remaining {profile_type}",
        ]
        
        for pattern in patterns:
            pos = block_finder.find_anchor(pattern)
            if pos:
                # Value is usually in a nearby column
                for col_offset in [4, 5, 6]:
                    val = block_finder.get_value_at(
                        pos[0], pos[1] + col_offset, float, None
                    )
                    if val is not None and val >= 0:
                        return val
        
        return 0.0
    
    def _extract_bar_count(self, start_row: int, end_row: int) -> int:
        """Extract number of bars used in this block."""
        block_df = self.df.iloc[start_row:end_row]
        block_finder = AnchorFinder(block_df, "Flanges-bars")
        
        pos = block_finder.find_anchor("N° of bars")
        if pos:
            for col_offset in [4, 5, 6]:
                val = block_finder.get_value_at(pos[0], pos[1] + col_offset, int, None)
                if val is not None and val >= 0:
                    return val
        
        return 0
    
    def _extract_flange_count(self, start_row: int, end_row: int) -> int:
        """Extract total number of flanges in this block."""
        block_df = self.df.iloc[start_row:end_row]
        block_finder = AnchorFinder(block_df, "Flanges-count")
        
        pos = block_finder.find_anchor("Total number of")
        if pos:
            for col_offset in [4, 5, 6]:
                val = block_finder.get_value_at(pos[0], pos[1] + col_offset, int, None)
                if val is not None and val >= 0:
                    return val
        
        return 0
    
    def _extract_cost_per_m(self, start_row: int, end_row: int) -> Optional[float]:
        """Extract cost per meter for this block."""
        block_df = self.df.iloc[start_row:end_row]
        block_finder = AnchorFinder(block_df, "Flanges-cost")
        
        pos = block_finder.find_anchor("Cost per mt")
        if pos:
            for col_offset in [2, 3, 4]:
                val = block_finder.get_value_at(pos[0], pos[1] + col_offset, float, None)
                if val is not None and val > 0:
                    return val
        
        return None
    
    def _extract_accessories(self, data: 'FlangesData') -> None:
        """
        Extract flange accessories (GI Corners, PVC Corners).
        
        Anchor-based approach:
        1. Find "FLANGES ACCESSORIES" anchor
        2. Below it, find "GI corners" and "PVC Corners" rows
        3. For each, find the QUANTITY column value
        """
        # Find the FLANGES ACCESSORIES section
        pos = self.finder.find_anchor("FLANGES ACCESSORIES")
        if not pos:
            logger.debug("FLANGES ACCESSORIES section not found")
            return
        
        start_row = pos[0]
        
        # Search below for GI corners and PVC Corners
        for row in range(start_row, min(start_row + 15, len(self.df))):
            for col in range(len(self.df.columns)):
                val = self.df.iloc[row, col]
                if pd.notna(val):
                    val_str = str(val).strip().lower()
                    
                    if "gi corner" in val_str:
                        # Find QUANTITY column (usually col 10 based on data)
                        qty = self._find_quantity_in_row(row)
                        if qty is not None:
                            data.gi_corners_qty = int(qty)
                            logger.debug(f"Extracted GI corners qty: {qty}")
                        
                        # Find cost (column 4 based on data)
                        cost = self._find_cost_in_row(row, col)
                        if cost is not None:
                            data.gi_corners_cost = cost
                    
                    elif "pvc corner" in val_str:
                        qty = self._find_quantity_in_row(row)
                        if qty is not None:
                            data.pvc_corners_qty = int(qty)
                            logger.debug(f"Extracted PVC corners qty: {qty}")
                        
                        cost = self._find_cost_in_row(row, col)
                        if cost is not None:
                            data.pvc_corners_cost = cost
    
    def _find_quantity_in_row(self, row: int) -> Optional[int]:
        """Find QUANTITY value in a row by looking for the QUANTITY column."""
        # First, find the QUANTITY column header in nearby rows
        for header_row in range(max(0, row - 3), row):
            for col in range(len(self.df.columns)):
                val = self.df.iloc[header_row, col]
                if pd.notna(val) and "quantity" in str(val).lower():
                    # Get value from same column in the target row
                    qty_val = self.df.iloc[row, col]
                    if pd.notna(qty_val):
                        try:
                            return int(float(str(qty_val)))
                        except (ValueError, TypeError):
                            pass
        
        return None
    
    def _find_cost_in_row(self, row: int, anchor_col: int) -> Optional[float]:
        """Find cost value in a row, typically 3-4 columns after the label."""
        for offset in [3, 4, 5]:
            if anchor_col + offset < len(self.df.columns):
                val = self.df.iloc[row, anchor_col + offset]
                if pd.notna(val):
                    try:
                        return float(str(val))
                    except (ValueError, TypeError):
                        pass
        return None
    
    def _extract_total_cost(self) -> float:
        """Extract total cost of flanges and accessories."""
        val = self.finder.get_value_by_anchor(
            "Tot. cost of flanges",
            row_offset=1,
            col_offset=2,
            cast_type=float,
            default=0.0
        )
        return val or 0.0
