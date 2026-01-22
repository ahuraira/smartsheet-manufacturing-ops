"""
Unit Tests for Event Utilities (v1.4.0)

Tests shared utilities for ID-based cell value extraction.
These utilities are immune to column renames (use IDs, not names).
"""

import pytest
from unittest.mock import MagicMock, patch

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.event_utils import (
    get_cell_value_by_column_id,
    get_cell_value_by_logical_name,
)


@pytest.fixture
def sample_row_data():
    """Sample row data as returned by SmartsheetClient.get_row()."""
    return {
        1111111111111111: "SAP-PTE-185",
        2222222222222222: "Acme Corporation",
        3333333333333333: 25000.50,
        4444444444444444: None,
    }


@pytest.fixture
def mock_manifest():
    """Mock manifest for logical name resolution."""
    manifest = MagicMock()
    manifest.get_column_id.side_effect = lambda sheet, col: {
        ("01H_LPO_INGESTION", "SAP_REFERENCE"): 1111111111111111,
        ("01H_LPO_INGESTION", "CUSTOMER_NAME"): 2222222222222222,
        ("01H_LPO_INGESTION", "PO_VALUE"): 3333333333333333,
        ("01H_LPO_INGESTION", "NONEXISTENT"): None,
    }.get((sheet, col))
    return manifest


@pytest.mark.unit
class TestGetCellValueByColumnId:
    """Tests for get_cell_value_by_column_id function."""
    
    def test_get_value_string(self, sample_row_data):
        """Test getting string value by column ID."""
        value = get_cell_value_by_column_id(sample_row_data, 1111111111111111)
        assert value == "SAP-PTE-185"
        
    def test_get_value_numeric(self, sample_row_data):
        """Test getting numeric value by column ID."""
        value = get_cell_value_by_column_id(sample_row_data, 3333333333333333)
        assert value == 25000.50
        
    def test_get_value_none(self, sample_row_data):
        """Test getting None value by column ID."""
        value = get_cell_value_by_column_id(sample_row_data, 4444444444444444)
        assert value is None
        
    def test_get_value_missing_column(self, sample_row_data):
        """Test getting value for non-existent column ID."""
        value = get_cell_value_by_column_id(sample_row_data, 9999999999999999)
        assert value is None
        
    def test_get_value_string_key(self):
        """Test getting value when column ID stored as string."""
        row_data = {
            "1111111111111111": "Value from string key"
        }
        value = get_cell_value_by_column_id(row_data, 1111111111111111)
        assert value == "Value from string key"


@pytest.mark.unit
class TestGetCellValueByLogicalName:
    """Tests for get_cell_value_by_logical_name function."""
    
    def test_get_value_by_logical_name(self, sample_row_data, mock_manifest):
        """Test getting value using logical column name."""
        with patch("shared.event_utils.get_manifest", return_value=mock_manifest):
            value = get_cell_value_by_logical_name(
                sample_row_data,
                "01H_LPO_INGESTION",
                "SAP_REFERENCE"
            )
        
        assert value == "SAP-PTE-185"
        
    def test_get_value_by_logical_name_numeric(self, sample_row_data, mock_manifest):
        """Test getting numeric value using logical name."""
        with patch("shared.event_utils.get_manifest", return_value=mock_manifest):
            value = get_cell_value_by_logical_name(
                sample_row_data,
                "01H_LPO_INGESTION",
                "PO_VALUE"
            )
        
        assert value == 25000.50
        
    def test_get_value_column_not_in_manifest(self, sample_row_data, mock_manifest):
        """Test handling of column not found in manifest."""
        with patch("shared.event_utils.get_manifest", return_value=mock_manifest):
            value = get_cell_value_by_logical_name(
                sample_row_data,
                "01H_LPO_INGESTION",
                "NONEXISTENT"
            )
        
        assert value is None
        
    def test_get_value_sheet_not_in_manifest(self, sample_row_data, mock_manifest):
        """Test handling of sheet not found in manifest."""
        with patch("shared.event_utils.get_manifest", return_value=mock_manifest):
            value = get_cell_value_by_logical_name(
                sample_row_data,
                "UNKNOWN_SHEET",
                "SOME_COLUMN"
            )
        
        assert value is None
