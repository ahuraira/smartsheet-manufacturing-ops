"""
Anchor Finder Utilities
=======================

Core utilities for anchor-based cell searching in Excel DataFrames.
This is the heart of the robust parsing strategy - no hardcoded cell references.
"""

import pandas as pd
import numpy as np
from typing import Optional, Tuple, Any, List, Union
import logging
import re

logger = logging.getLogger(__name__)


class AnchorNotFoundError(Exception):
    """Raised when a required anchor is not found."""
    pass


class AnchorFinder:
    """
    Utility class for finding values in DataFrames using anchor text.
    
    The "Anchor & Offset" strategy:
    1. Search for a known text anchor (e.g., "Utilized sheets")
    2. Apply row/column offsets to locate the actual value
    3. Cast to the expected type
    """
    
    def __init__(self, df: pd.DataFrame, sheet_name: str = ""):
        """
        Initialize with a DataFrame.
        
        Args:
            df: The pandas DataFrame to search
            sheet_name: Sheet name for logging context
        """
        self.df = df
        self.sheet_name = sheet_name
        self._cache: dict = {}  # Cache anchor positions
    
    def find_anchor(
        self,
        anchor_text: str,
        case_sensitive: bool = False,
        exact_match: bool = False
    ) -> Optional[Tuple[int, int]]:
        """
        Find the (row, col) position of anchor text in the DataFrame.
        
        Args:
            anchor_text: Text to search for
            case_sensitive: Whether to match case
            exact_match: If True, cell must exactly equal anchor_text
            
        Returns:
            Tuple of (row_index, col_index) or None if not found
        """
        cache_key = f"{anchor_text}_{case_sensitive}_{exact_match}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        search_text = anchor_text if case_sensitive else anchor_text.lower()
        
        for row_idx in range(len(self.df)):
            for col_idx in range(len(self.df.columns)):
                cell = self.df.iloc[row_idx, col_idx]
                
                # Skip NaN/None
                if pd.isna(cell):
                    continue
                
                cell_str = str(cell)
                compare_str = cell_str if case_sensitive else cell_str.lower()
                
                if exact_match:
                    if compare_str.strip() == search_text.strip():
                        result = (row_idx, col_idx)
                        self._cache[cache_key] = result
                        return result
                else:
                    if search_text in compare_str:
                        result = (row_idx, col_idx)
                        self._cache[cache_key] = result
                        return result
        
        self._cache[cache_key] = None
        return None
    
    def find_all_anchors(
        self,
        anchor_text: str,
        case_sensitive: bool = False
    ) -> List[Tuple[int, int]]:
        """
        Find ALL occurrences of anchor text in the DataFrame.
        
        Useful for block-based structures like the Flanges sheet.
        
        Returns:
            List of (row_index, col_index) tuples
        """
        results = []
        search_text = anchor_text if case_sensitive else anchor_text.lower()
        
        for row_idx in range(len(self.df)):
            for col_idx in range(len(self.df.columns)):
                cell = self.df.iloc[row_idx, col_idx]
                
                if pd.isna(cell):
                    continue
                
                cell_str = str(cell)
                compare_str = cell_str if case_sensitive else cell_str.lower()
                
                if search_text in compare_str:
                    results.append((row_idx, col_idx))
        
        return results
    
    def get_value_by_anchor(
        self,
        anchor_text: str,
        row_offset: int = 0,
        col_offset: int = 1,
        cast_type: type = str,
        required: bool = False,
        default: Any = None
    ) -> Any:
        """
        Find anchor and return value at specified offset.
        
        Args:
            anchor_text: Text to search for
            row_offset: Rows to move from anchor (positive = down)
            col_offset: Columns to move from anchor (positive = right)
            cast_type: Type to cast the value to (str, int, float)
            required: If True, raise error when anchor not found
            default: Default value if anchor not found or value is empty
            
        Returns:
            The extracted and cast value, or default
            
        Raises:
            AnchorNotFoundError: If required=True and anchor not found
        """
        anchor_pos = self.find_anchor(anchor_text)
        
        if anchor_pos is None:
            if required:
                raise AnchorNotFoundError(
                    f"Required anchor '{anchor_text}' not found in sheet '{self.sheet_name}'"
                )
            logger.debug(f"Anchor '{anchor_text}' not found, returning default")
            return default
        
        row_idx, col_idx = anchor_pos
        target_row = row_idx + row_offset
        target_col = col_idx + col_offset
        
        # Bounds check
        if target_row < 0 or target_row >= len(self.df):
            logger.warning(
                f"Row offset {row_offset} from anchor '{anchor_text}' is out of bounds"
            )
            return default
        
        if target_col < 0 or target_col >= len(self.df.columns):
            logger.warning(
                f"Column offset {col_offset} from anchor '{anchor_text}' is out of bounds"
            )
            return default
        
        value = self.df.iloc[target_row, target_col]
        
        return self._cast_value(value, cast_type, default)
    
    def get_value_at(
        self,
        row: int,
        col: int,
        cast_type: type = str,
        default: Any = None
    ) -> Any:
        """Get value at absolute position with casting."""
        if row < 0 or row >= len(self.df) or col < 0 or col >= len(self.df.columns):
            return default
        
        value = self.df.iloc[row, col]
        return self._cast_value(value, cast_type, default)
    
    def find_column_index(
        self,
        header_text: str,
        search_row: Optional[int] = None
    ) -> Optional[int]:
        """
        Find column index by header text.
        
        Args:
            header_text: Text to search for in headers
            search_row: Specific row to search, or None to search first 20 rows
            
        Returns:
            Column index or None
        """
        search_text = header_text.lower()
        
        if search_row is not None:
            rows_to_search = [search_row]
        else:
            rows_to_search = range(min(20, len(self.df)))
        
        for row_idx in rows_to_search:
            for col_idx in range(len(self.df.columns)):
                cell = self.df.iloc[row_idx, col_idx]
                if pd.isna(cell):
                    continue
                if search_text in str(cell).lower():
                    return col_idx
        
        return None
    
    def get_column_values(
        self,
        col_index: int,
        start_row: int,
        end_row: Optional[int] = None,
        cast_type: type = float,
        skip_empty: bool = True
    ) -> List[Any]:
        """
        Get all values in a column within a row range.
        
        Args:
            col_index: Column index
            start_row: Starting row (inclusive)
            end_row: Ending row (exclusive), None for end of DataFrame
            cast_type: Type to cast values to
            skip_empty: Whether to skip NaN/empty values
            
        Returns:
            List of values
        """
        if end_row is None:
            end_row = len(self.df)
        
        values = []
        for row_idx in range(start_row, min(end_row, len(self.df))):
            value = self.df.iloc[row_idx, col_index]
            
            if skip_empty and pd.isna(value):
                continue
            
            cast_val = self._cast_value(value, cast_type, None)
            if cast_val is not None or not skip_empty:
                values.append(cast_val)
        
        return values
    
    def sum_column_values(
        self,
        col_index: int,
        start_row: int,
        end_row: Optional[int] = None
    ) -> float:
        """Sum all numeric values in a column range."""
        values = self.get_column_values(
            col_index, start_row, end_row, cast_type=float, skip_empty=True
        )
        return sum(v for v in values if v is not None)
    
    def find_table_header_row(
        self,
        required_headers: List[str],
        max_rows: int = 30
    ) -> Optional[int]:
        """
        Find the row that contains table headers.
        
        Args:
            required_headers: List of header texts that must be present
            max_rows: Maximum rows to search
            
        Returns:
            Row index or None
        """
        for row_idx in range(min(max_rows, len(self.df))):
            row_values = [
                str(self.df.iloc[row_idx, col]).lower() 
                for col in range(len(self.df.columns))
                if not pd.isna(self.df.iloc[row_idx, col])
            ]
            row_text = " ".join(row_values)
            
            matches = sum(
                1 for header in required_headers 
                if header.lower() in row_text
            )
            
            if matches >= len(required_headers) * 0.8:  # 80% match threshold
                return row_idx
        
        return None
    
    def extract_table(
        self,
        header_row: int,
        column_mapping: dict,
        stop_on_empty_id: bool = True
    ) -> List[dict]:
        """
        Extract a structured table starting from header row.
        
        Args:
            header_row: Row index containing headers
            column_mapping: Dict mapping header text to output field name
            stop_on_empty_id: Stop when first column has empty value
            
        Returns:
            List of dictionaries representing table rows
        """
        # Find column indices for each header
        col_indices = {}
        for header_text, field_name in column_mapping.items():
            col_idx = self.find_column_index(header_text, search_row=header_row)
            if col_idx is not None:
                col_indices[field_name] = col_idx
        
        if not col_indices:
            logger.warning(f"No columns found for table at row {header_row}")
            return []
        
        # Determine first column for empty check
        first_field = list(column_mapping.values())[0]
        first_col_idx = col_indices.get(first_field)
        
        rows = []
        for row_idx in range(header_row + 1, len(self.df)):
            # Check stop condition
            if stop_on_empty_id and first_col_idx is not None:
                first_val = self.df.iloc[row_idx, first_col_idx]
                if pd.isna(first_val) or str(first_val).strip() == "":
                    break
            
            row_data = {}
            for field_name, col_idx in col_indices.items():
                value = self.df.iloc[row_idx, col_idx]
                row_data[field_name] = value if not pd.isna(value) else None
            
            # Skip completely empty rows
            if all(v is None for v in row_data.values()):
                continue
            
            rows.append(row_data)
        
        return rows
    
    @staticmethod
    def _cast_value(value: Any, cast_type: type, default: Any) -> Any:
        """
        Safely cast a value to the specified type.
        
        Handles NaN, empty strings, and type conversion errors.
        """
        if pd.isna(value):
            return default
        
        if isinstance(value, str) and value.strip() == "":
            return default
        
        try:
            if cast_type == float:
                # Handle numeric strings with commas
                if isinstance(value, str):
                    value = value.replace(",", "").strip()
                result = float(value)
                # Handle infinity and very large numbers
                if np.isinf(result) or np.isnan(result):
                    return default
                return result
            
            elif cast_type == int:
                if isinstance(value, str):
                    value = value.replace(",", "").strip()
                return int(float(value))
            
            elif cast_type == str:
                return str(value).strip()
            
            else:
                return cast_type(value)
                
        except (ValueError, TypeError) as e:
            logger.debug(f"Failed to cast '{value}' to {cast_type}: {e}")
            return default


def find_anchor_in_workbook(
    workbook: dict,
    anchor_text: str,
    sheet_names: Optional[List[str]] = None
) -> Optional[Tuple[str, int, int]]:
    """
    Find anchor across multiple sheets.
    
    Args:
        workbook: Dict of {sheet_name: DataFrame}
        anchor_text: Text to find
        sheet_names: Specific sheets to search, or None for all
        
    Returns:
        Tuple of (sheet_name, row, col) or None
    """
    sheets_to_search = sheet_names or list(workbook.keys())
    
    for sheet_name in sheets_to_search:
        if sheet_name not in workbook:
            continue
        
        finder = AnchorFinder(workbook[sheet_name], sheet_name)
        pos = finder.find_anchor(anchor_text)
        
        if pos is not None:
            return (sheet_name, pos[0], pos[1])
    
    return None
