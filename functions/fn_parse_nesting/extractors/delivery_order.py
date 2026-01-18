"""
Delivery Order Extractor
========================

Extracts finished goods line items from the "Delivery order" sheet.
Handles the complex multi-row header structure with MOUTH A/B/C/D sub-columns.
"""

import pandas as pd
import logging
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass, field

from ..anchor_finder import AnchorFinder

logger = logging.getLogger(__name__)


@dataclass
class DeliveryLineItem:
    """A single line item from the delivery order."""
    
    line_id: int = 0
    tag_id: Optional[str] = None
    description: str = ""
    page: Optional[str] = None
    
    # Geometry - MOUTH A
    mouth_a_x: Optional[float] = None
    mouth_a_y: Optional[float] = None
    mouth_a_fl: Optional[str] = None
    
    # Geometry - MOUTH B
    mouth_b_x: Optional[float] = None
    mouth_b_y: Optional[float] = None
    mouth_b_fl: Optional[str] = None
    
    # Length
    length_m: Optional[float] = None
    
    # Material info
    material: Optional[str] = None
    thickness_mm: Optional[float] = None
    
    # Quantities and areas
    qty: int = 1
    internal_area_m2: float = 0.0
    external_area_m2: float = 0.0


@dataclass
class DeliveryOrderData:
    """Data extracted from Delivery order sheet."""
    
    line_items: List[DeliveryLineItem] = field(default_factory=list)
    total_internal_area_m2: float = 0.0
    total_external_area_m2: float = 0.0
    total_items: int = 0
    warnings: List[str] = field(default_factory=list)


class DeliveryOrderExtractor:
    """
    Extracts finished goods line items from the Delivery order sheet.
    
    Actual column structure (with multi-row headers):
    Row 1: PART DESCRIPTION | ID | TAG | PAG | MOUTH A | | | MOUTH B | | | MOUTH C | | | MOUTH D | | | LEN. | MATERIAL | THICK. | INT AREA | EXT AREA | QTY
    Row 2: (sub-headers)    |    |     |     | X | Y | FL | X | Y | FL | X | Y | FL | X | Y | FL | mt |          |        | m2       | m2       |
    """
    
    SHEET_NAME = "Delivery order"
    
    def __init__(self, df: pd.DataFrame):
        """
        Initialize extractor.
        
        Args:
            df: DataFrame of the Delivery order sheet
        """
        self.df = df
        self.finder = AnchorFinder(df, self.SHEET_NAME)
        self._column_map: Dict[str, int] = {}
    
    def extract(self) -> DeliveryOrderData:
        """
        Extract all line items from the sheet.
        
        Returns:
            DeliveryOrderData with list of line items
        """
        data = DeliveryOrderData()
        
        # Find the header row containing "PART DESCRIPTION"
        header_info = self._find_header_rows()
        
        if header_info is None:
            data.warnings.append("Could not find header row in Delivery order sheet")
            return data
        
        main_header_row, sub_header_row, data_start_row = header_info
        logger.info(f"Found delivery order header at row {main_header_row}, data starts at row {data_start_row}")
        
        # Map columns based on the structure
        self._map_columns(main_header_row, sub_header_row)
        
        if not self._column_map:
            data.warnings.append("Could not map columns in Delivery order sheet")
            return data
        
        logger.debug(f"Column mapping: {self._column_map}")
        
        # Extract rows
        data.line_items = self._extract_rows(data_start_row)
        
        # Calculate totals
        data.total_items = len(data.line_items)
        data.total_internal_area_m2 = sum(
            item.internal_area_m2 * item.qty for item in data.line_items
        )
        data.total_external_area_m2 = sum(
            item.external_area_m2 * item.qty for item in data.line_items
        )
        
        return data
    
    def _find_header_rows(self) -> Optional[Tuple[int, int, int]]:
        """
        Find the main header row and sub-header row.
        
        Returns:
            Tuple of (main_header_row, sub_header_row, data_start_row) or None
        """
        # Try to find header row using multiple required columns (more robust)
        required_headers = ["PART DESCRIPTION", "ID", "TAG", "QTY"]
        main_header_row = self.finder.find_table_header_row(required_headers)
        
        if main_header_row is None:
            # Fallback: Look for "PART DESCRIPTION" specifically
            pos = self.finder.find_anchor("PART DESCRIPTION")
            if pos is None:
                return None
            main_header_row = pos[0]
        
        # Check if there's a sub-header row (look for "X" or "Y" in the next row)
        sub_header_row = main_header_row + 1
        if sub_header_row < len(self.df):
            row_values = []
            for col in range(len(self.df.columns)):
                cell = self.df.iloc[sub_header_row, col]
                if pd.notna(cell):
                    row_values.append(str(cell).strip().upper())
            
            if "X" in row_values or "Y" in row_values or "FL" in row_values:
                # There is a sub-header row
                data_start_row = sub_header_row + 1
            else:
                # No sub-header, data starts right after main header
                data_start_row = main_header_row + 1
                sub_header_row = main_header_row
        else:
            data_start_row = main_header_row + 1
            sub_header_row = main_header_row
        
        return (main_header_row, sub_header_row, data_start_row)
    
    def _map_columns(self, main_header_row: int, sub_header_row: int) -> None:
        """
        Map columns based on the multi-row header structure.
        
        The structure is:
        - Simple columns: PART DESCRIPTION, ID, TAG, PAG, LEN., MATERIAL, THICK., INT AREA, EXT AREA, QTY
        - Complex columns: MOUTH A (with X, Y, FL sub-columns at consecutive positions), MOUTH B, MOUTH C, MOUTH D
        
        Based on analysis of actual files:
        - MOUTH A at col 5 → X=5, Y=6, FL=7
        - MOUTH B at col 8 → X=8, Y=9, FL=10
        """
        self._column_map = {}
        
        mouth_a_col = None
        mouth_b_col = None
        
        # First, scan the main header row to find column positions
        for col_idx in range(len(self.df.columns)):
            cell = self.df.iloc[main_header_row, col_idx]
            if pd.isna(cell):
                continue
            
            cell_str = str(cell).strip().upper()
            
            # Direct mappings
            if "PART DESCRIPTION" in cell_str:
                self._column_map["description"] = col_idx
            elif cell_str == "ID":
                self._column_map["line_id"] = col_idx
            elif cell_str == "TAG":
                self._column_map["tag_id"] = col_idx
            elif cell_str == "PAG":
                self._column_map["page"] = col_idx
            elif cell_str in ["LEN.", "LEN", "LENGTH"]:
                self._column_map["length_m"] = col_idx
            elif cell_str == "MATERIAL":
                self._column_map["material"] = col_idx
            elif cell_str in ["THICK.", "THICK", "THICKNESS"]:
                self._column_map["thickness_mm"] = col_idx
            elif cell_str in ["INT AREA", "INT. AREA"]:
                self._column_map["internal_area_m2"] = col_idx
            elif cell_str in ["EXT AREA", "EXT. AREA"]:
                self._column_map["external_area_m2"] = col_idx
            elif cell_str == "QTY":
                self._column_map["qty"] = col_idx
            elif cell_str == "MOUTH A":
                mouth_a_col = col_idx
            elif cell_str == "MOUTH B":
                mouth_b_col = col_idx
        
        # For MOUTH A/B, the X, Y, FL are at consecutive positions:
        # MOUTH A header position = X column
        # MOUTH A header + 1 = Y column  
        # MOUTH A header + 2 = FL column
        if mouth_a_col is not None:
            self._column_map["mouth_a_x"] = mouth_a_col
            self._column_map["mouth_a_y"] = mouth_a_col + 1
            self._column_map["mouth_a_fl"] = mouth_a_col + 2
            logger.debug(f"MOUTH A mapped: X={mouth_a_col}, Y={mouth_a_col+1}, FL={mouth_a_col+2}")
        
        if mouth_b_col is not None:
            self._column_map["mouth_b_x"] = mouth_b_col
            self._column_map["mouth_b_y"] = mouth_b_col + 1
            self._column_map["mouth_b_fl"] = mouth_b_col + 2
            logger.debug(f"MOUTH B mapped: X={mouth_b_col}, Y={mouth_b_col+1}, FL={mouth_b_col+2}")
        
        logger.debug(f"Final column map: {self._column_map}")
    
    def _extract_rows(self, data_start_row: int) -> List[DeliveryLineItem]:
        """Extract all data rows from the table."""
        items = []
        id_col = self._column_map.get("line_id")
        desc_col = self._column_map.get("description")
        
        # Use description column as the primary stop indicator if ID is not available
        primary_col = id_col if id_col is not None else desc_col
        
        for row_idx in range(data_start_row, len(self.df)):
            # Check for end of data
            if primary_col is not None:
                val = self.df.iloc[row_idx, primary_col]
                if pd.isna(val) or str(val).strip() == "":
                    # Check if this is really the end
                    if self._is_table_ended(row_idx, primary_col):
                        break
                    continue
            
            # Extract row data
            item = self._extract_single_row(row_idx)
            
            if item and (item.line_id > 0 or item.description):
                items.append(item)
        
        logger.info(f"Extracted {len(items)} delivery order line items")
        return items
    
    def _is_table_ended(self, row_idx: int, check_col: int) -> bool:
        """Check if we've reached the end of the data table."""
        for offset in range(1, 4):
            check_row = row_idx + offset
            if check_row >= len(self.df):
                return True
            
            val = self.df.iloc[check_row, check_col]
            if pd.notna(val) and str(val).strip():
                return False
        
        return True
    
    def _extract_single_row(self, row_idx: int) -> Optional[DeliveryLineItem]:
        """Extract a single row into a DeliveryLineItem."""
        item = DeliveryLineItem()
        
        try:
            # Line ID
            if "line_id" in self._column_map:
                val = self.df.iloc[row_idx, self._column_map["line_id"]]
                if pd.notna(val):
                    try:
                        item.line_id = int(float(val))
                    except (ValueError, TypeError):
                        pass
            
            # Description
            if "description" in self._column_map:
                val = self.df.iloc[row_idx, self._column_map["description"]]
                if pd.notna(val):
                    item.description = str(val).strip()
            
            # Tag ID
            if "tag_id" in self._column_map:
                val = self.df.iloc[row_idx, self._column_map["tag_id"]]
                if pd.notna(val) and str(val).strip():
                    item.tag_id = str(val).strip()
            
            # Geometry - MOUTH A/B dimensions
            for field in ["mouth_a_x", "mouth_a_y", "mouth_b_x", "mouth_b_y"]:
                if field in self._column_map:
                    val = self.df.iloc[row_idx, self._column_map[field]]
                    if pd.notna(val):
                        try:
                            setattr(item, field, float(val))
                        except (ValueError, TypeError):
                            pass
            
            # Geometry - MOUTH A/B flange types (string values like "90", "UP", "HP")
            for field in ["mouth_a_fl", "mouth_b_fl"]:
                if field in self._column_map:
                    val = self.df.iloc[row_idx, self._column_map[field]]
                    if pd.notna(val) and str(val).strip():
                        setattr(item, field, str(val).strip())
            
            # Length - the column says "mt" but values are actually in mm, convert to meters
            if "length_m" in self._column_map:
                val = self.df.iloc[row_idx, self._column_map["length_m"]]
                if pd.notna(val):
                    try:
                        # Values are in mm despite "mt" label, convert to meters
                        length_mm = float(val)
                        item.length_m = length_mm / 1000.0
                    except (ValueError, TypeError):
                        pass
            
            # Material
            if "material" in self._column_map:
                val = self.df.iloc[row_idx, self._column_map["material"]]
                if pd.notna(val):
                    item.material = str(val).strip()
            
            # Thickness
            if "thickness_mm" in self._column_map:
                val = self.df.iloc[row_idx, self._column_map["thickness_mm"]]
                if pd.notna(val):
                    try:
                        item.thickness_mm = float(val)
                    except (ValueError, TypeError):
                        pass
            
            # Quantity
            if "qty" in self._column_map:
                val = self.df.iloc[row_idx, self._column_map["qty"]]
                if pd.notna(val):
                    try:
                        item.qty = int(float(val))
                    except (ValueError, TypeError):
                        item.qty = 1
            
            # Areas
            if "internal_area_m2" in self._column_map:
                val = self.df.iloc[row_idx, self._column_map["internal_area_m2"]]
                if pd.notna(val):
                    try:
                        item.internal_area_m2 = float(val)
                    except (ValueError, TypeError):
                        pass
            
            if "external_area_m2" in self._column_map:
                val = self.df.iloc[row_idx, self._column_map["external_area_m2"]]
                if pd.notna(val):
                    try:
                        item.external_area_m2 = float(val)
                    except (ValueError, TypeError):
                        pass
            
            return item
            
        except Exception as e:
            logger.warning(f"Error extracting row {row_idx}: {e}")
            return None
