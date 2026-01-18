"""
Unit Tests for Anchor Finder Strategy (v1.3.1)

Tests the core "Anchor & Offset" strategy used by the Nesting Parser.
This is the CRITICAL component that ensures robust data extraction
even when Excel layouts shift.

Test Scenarios from Specification:
1. Happy Path - All anchors found in standard positions
2. Shifted File - 3 empty rows inserted at top (MUST still work)
3. Missing Anchor - Graceful handling without crash
4. Multiple Anchors - Finding all occurrences (for block iteration)
"""

import pytest
import pandas as pd
import numpy as np

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fn_parse_nesting.anchor_finder import AnchorFinder, AnchorNotFoundError


def create_project_parameters_df():
    """Create a realistic DataFrame mimicking Project Parameters sheet."""
    # Simulating the actual Excel layout with labels in col 0, values in col 5
    data = [
        ["PROJECT NAME", None, None, None, None, "Test Project Name", None, None],
        ["PROJECT REFERENCE", None, None, None, None, "TAG-1001", None, None],
        [None, None, None, None, None, None, None, None],
        ["Material", None, None, None, None, "GI 0.9mm", None, None],
        ["Thickness", None, None, None, None, 0.9, None, None],
        ["Sheet dimension X", None, None, None, None, 3000.0, None, None],
        ["Sheet dimension Y", None, None, None, None, 1500.0, None, None],
        [None, None, None, None, None, None, None, None],
        ["Utilized sheets", None, None, None, None, 15, None, None],
        ["Total area reusable", None, None, None, None, 2.35, None, None],
        ["Wastage due to nesting", None, None, None, None, 0.75, None, None],
        [None, None, None, None, None, None, None, None],
        ["Time for 45° cuts", None, None, None, None, 120.5, None, None],
        ["Time for 90° cuts", None, None, None, None, 85.3, None, None],
        ["Time for 2x45° cuts", None, None, None, None, 45.2, None, None],
    ]
    return pd.DataFrame(data)


def create_shifted_df(original_df: pd.DataFrame, shift_rows: int = 3):
    """Insert empty rows at the top of a DataFrame (simulates shifted file)."""
    empty_rows = pd.DataFrame([[None] * len(original_df.columns)] * shift_rows)
    shifted = pd.concat([empty_rows, original_df], ignore_index=True)
    return shifted


@pytest.mark.unit
class TestAnchorFinderBasics:
    """Basic functionality tests for AnchorFinder."""
    
    def test_find_anchor_exact_match(self):
        """Test finding anchor with exact text match."""
        df = create_project_parameters_df()
        finder = AnchorFinder(df, "Project parameters")
        
        pos = finder.find_anchor("PROJECT NAME", exact_match=True)
        assert pos is not None
        assert pos == (0, 0)  # First row, first column
        
    def test_find_anchor_partial_match(self):
        """Test finding anchor with partial text match."""
        df = create_project_parameters_df()
        finder = AnchorFinder(df, "Project parameters")
        
        # "dimension X" should match "Sheet dimension X"
        pos = finder.find_anchor("dimension X")
        assert pos is not None
        assert pos == (5, 0)
        
    def test_find_anchor_case_insensitive(self):
        """Test case-insensitive search (default behavior)."""
        df = create_project_parameters_df()
        finder = AnchorFinder(df, "Project parameters")
        
        # lowercase should still find uppercase anchor
        pos = finder.find_anchor("project name")
        assert pos is not None
        assert pos == (0, 0)
        
    def test_find_anchor_case_sensitive_fails(self):
        """Test case-sensitive search fails when case doesn't match."""
        df = create_project_parameters_df()
        finder = AnchorFinder(df, "Project parameters")
        
        pos = finder.find_anchor("project name", case_sensitive=True)
        assert pos is None  # Should not find it


@pytest.mark.unit
class TestAnchorFinderValueExtraction:
    """Tests for value extraction using anchor & offset."""
    
    def test_get_value_by_anchor_string(self):
        """Test extracting string value at offset from anchor."""
        df = create_project_parameters_df()
        finder = AnchorFinder(df, "Project parameters")
        
        # PROJECT REFERENCE is at (1, 0), value at (1, 5)
        value = finder.get_value_by_anchor(
            "PROJECT REFERENCE", 
            row_offset=0, 
            col_offset=5, 
            cast_type=str
        )
        assert value == "TAG-1001"
        
    def test_get_value_by_anchor_float(self):
        """Test extracting and casting to float."""
        df = create_project_parameters_df()
        finder = AnchorFinder(df, "Project parameters")
        
        value = finder.get_value_by_anchor(
            "Thickness", 
            row_offset=0, 
            col_offset=5, 
            cast_type=float
        )
        assert value == 0.9
        assert isinstance(value, float)
        
    def test_get_value_by_anchor_int(self):
        """Test extracting and casting to int."""
        df = create_project_parameters_df()
        finder = AnchorFinder(df, "Project parameters")
        
        value = finder.get_value_by_anchor(
            "Utilized sheets", 
            row_offset=0, 
            col_offset=5, 
            cast_type=int
        )
        assert value == 15
        assert isinstance(value, int)
        
    def test_get_value_default_when_not_found(self):
        """Test default value returned when anchor not found."""
        df = create_project_parameters_df()
        finder = AnchorFinder(df, "Project parameters")
        
        value = finder.get_value_by_anchor(
            "NonExistent Anchor", 
            default="DEFAULT_VALUE"
        )
        assert value == "DEFAULT_VALUE"


@pytest.mark.unit
class TestAnchorFinderShiftedFile:
    """CRITICAL: Tests for shifted file handling (Specification Requirement)."""
    
    def test_shifted_file_still_finds_anchors(self):
        """Test that anchors are found even with 3 empty rows at top."""
        original_df = create_project_parameters_df()
        shifted_df = create_shifted_df(original_df, shift_rows=3)
        
        finder = AnchorFinder(shifted_df, "Project parameters")
        
        # Should still find PROJECT REFERENCE (now at row 4 instead of row 1)
        pos = finder.find_anchor("PROJECT REFERENCE")
        assert pos is not None
        assert pos == (4, 0)  # Shifted by 3 rows
        
    def test_shifted_file_extracts_correct_value(self):
        """Test correct value extraction in shifted file."""
        original_df = create_project_parameters_df()
        shifted_df = create_shifted_df(original_df, shift_rows=3)
        
        finder = AnchorFinder(shifted_df, "Project parameters")
        
        # Value should still be extractable
        value = finder.get_value_by_anchor(
            "PROJECT REFERENCE", 
            row_offset=0, 
            col_offset=5, 
            cast_type=str
        )
        assert value == "TAG-1001"
        
    def test_shifted_file_all_anchors_work(self):
        """Test ALL anchors work correctly in shifted file."""
        original_df = create_project_parameters_df()
        shifted_df = create_shifted_df(original_df, shift_rows=5)  # 5 rows shift
        
        finder = AnchorFinder(shifted_df, "Project parameters")
        
        # Test multiple extractions
        assert finder.get_value_by_anchor("Material", col_offset=5) == "GI 0.9mm"
        assert finder.get_value_by_anchor("Thickness", col_offset=5, cast_type=float) == 0.9
        assert finder.get_value_by_anchor("Utilized sheets", col_offset=5, cast_type=int) == 15
        assert finder.get_value_by_anchor("Time for 2x45° cuts", col_offset=5, cast_type=float) == 45.2


@pytest.mark.unit
class TestAnchorFinderMultipleAnchors:
    """Tests for finding multiple occurrences (block iteration)."""
    
    def test_find_all_anchors(self):
        """Test finding multiple occurrences of same text."""
        # Create a DataFrame with repeating "PROFILE TYPE" blocks
        data = [
            ["PROFILE TYPE", None, "Description"],
            ["U PROFILE", None, "Standard U"],
            [None, None, None],
            ["PROFILE TYPE", None, "Description"],
            ["F PROFILE", None, "Standard F"],
            [None, None, None],
            ["PROFILE TYPE", None, "Description"],
            ["H PROFILE", None, "Standard H"],
        ]
        df = pd.DataFrame(data)
        finder = AnchorFinder(df, "Flanges")
        
        positions = finder.find_all_anchors("PROFILE TYPE")
        assert len(positions) == 3
        assert (0, 0) in positions
        assert (3, 0) in positions
        assert (6, 0) in positions


@pytest.mark.unit
class TestAnchorFinderErrorHandling:
    """Tests for error handling and edge cases."""
    
    def test_anchor_not_found_raises_when_required(self):
        """Test AnchorNotFoundError raised when required=True."""
        df = create_project_parameters_df()
        finder = AnchorFinder(df, "Project parameters")
        
        with pytest.raises(AnchorNotFoundError) as exc_info:
            finder.get_value_by_anchor("NonExistent", required=True)
        
        assert "NonExistent" in str(exc_info.value)
        
    def test_empty_dataframe_handling(self):
        """Test graceful handling of empty DataFrame."""
        df = pd.DataFrame()
        finder = AnchorFinder(df, "Empty")
        
        assert finder.find_anchor("Anything") is None
        assert finder.get_value_by_anchor("Anything", default="N/A") == "N/A"
        
    def test_nan_values_skipped(self):
        """Test that NaN values are skipped during search."""
        df = pd.DataFrame([
            [np.nan, "Material", np.nan],
            [None, "Value", None],
        ])
        finder = AnchorFinder(df)
        
        pos = finder.find_anchor("Material")
        assert pos == (0, 1)  # Found in col 1, not confused by NaN in col 0
        
    def test_numeric_cell_converted_to_string_for_search(self):
        """Test that numeric cells are searchable as strings."""
        df = pd.DataFrame([
            ["Code", 12345],
            ["Value", 100],
        ])
        finder = AnchorFinder(df)
        
        # Should be able to find the number 12345 as text
        pos = finder.find_anchor("12345")
        assert pos == (0, 1)


@pytest.mark.unit  
class TestAnchorFinderTableExtraction:
    """Tests for structured table extraction."""
    
    def test_find_table_header_row(self):
        """Test locating table header row."""
        data = [
            [None, None, None],
            ["Summary", None, None],
            [None, None, None],
            ["ID", "DESCRIPTION", "QTY"],  # Header row
            [1, "Item A", 10],
            [2, "Item B", 20],
        ]
        df = pd.DataFrame(data)
        finder = AnchorFinder(df)
        
        header_row = finder.find_table_header_row(["ID", "DESCRIPTION", "QTY"])
        assert header_row == 3
        
    def test_extract_table(self):
        """Test extracting structured table data."""
        data = [
            ["ID", "DESCRIPTION", "QTY", "AREA"],
            [1, "Duct Type A", 5, 10.5],
            [2, "Duct Type B", 3, 8.2],
            [3, "Duct Type C", 7, 15.0],
            [None, None, None, None],  # Empty row ends table
        ]
        df = pd.DataFrame(data)
        finder = AnchorFinder(df)
        
        mapping = {
            "ID": "line_id",
            "DESCRIPTION": "desc",
            "QTY": "quantity",
            "AREA": "area_m2",
        }
        
        rows = finder.extract_table(header_row=0, column_mapping=mapping)
        assert len(rows) == 3
        assert rows[0]["line_id"] == 1
        assert rows[0]["desc"] == "Duct Type A"
        assert rows[2]["area_m2"] == 15.0


@pytest.mark.unit
class TestAnchorFinderCaching:
    """Tests for caching behavior."""
    
    def test_cached_anchor_position(self):
        """Test that anchor positions are cached for performance."""
        df = create_project_parameters_df()
        finder = AnchorFinder(df)
        
        # First call
        pos1 = finder.find_anchor("PROJECT REFERENCE")
        # Second call should use cache
        pos2 = finder.find_anchor("PROJECT REFERENCE")
        
        assert pos1 == pos2
        # Verify it's in cache
        assert "PROJECT REFERENCE_False_False" in finder._cache
