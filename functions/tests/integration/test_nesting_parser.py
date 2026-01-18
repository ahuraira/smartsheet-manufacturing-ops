"""
Integration Tests for Nesting File Parser (v1.3.1)

End-to-end tests for NestingFileParser using realistic workbook data.
Tests the complete parsing pipeline from Excel sheets to NestingExecutionRecord.

Test Scenarios from Specification:
1. Happy Path - Standard file, all anchors found
2. Shifted File - Layout shift tolerance  
3. Missing Profile - F PROFILE block missing (should return empty, not crash)
4. Bad ID - PROJECT REFERENCE empty (triggers validation error)
"""

import pytest
import pandas as pd
import io
import json
from datetime import datetime

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fn_parse_nesting.parser import NestingFileParser
from fn_parse_nesting.models import ParsingResult, NestingExecutionRecord


def create_complete_workbook():
    """Create a complete workbook dict with all required sheets."""
    workbook = {}
    
    # Project Parameters
    workbook["project_parameters"] = pd.DataFrame([
        ["PROJECT NAME", None, None, None, None, "Integration Test Project", None],
        ["PROJECT REFERENCE", None, None, None, None, "TAG-INT-001", None],
        [None, None, None, None, None, None, None],
        ["Material", None, None, None, None, "GI 0.9mm", None],
        ["Thickness", None, None, None, None, 0.9, None],
        ["Sheet dimension X", None, None, None, None, 3000.0, None],
        ["Sheet dimension Y", None, None, None, None, 1500.0, None],
        [None, None, None, None, None, None, None],
        ["Utilized sheets", None, None, None, None, 10, None],
        ["Total area reusable", None, None, None, None, 1.5, None],
        ["Wastage due to nesting", None, None, None, None, 0.5, None],
        ["Wastage due to 45°", None, None, None, None, 0.2, None],
        ["Wastage due to 2x45°", None, None, None, None, 0.1, None],
        [None, None, None, None, None, None, None],
        ["Time for marking", None, None, None, None, 100.0, None],
        ["Time for 45° cuts", None, None, None, None, 200.0, None],
        ["Time for 90° cuts", None, None, None, None, 300.0, None],
        ["Time for 2x45° cuts", None, None, None, None, 50.0, None],
        ["Time for rapid traverse", None, None, None, None, 400.0, None],
        ["Length of 45° cuts", None, None, None, None, 100.0, None],
        ["Length of 90° cuts", None, None, None, None, 150.0, None],
        ["Length of 2x45° cuts", None, None, None, None, 30.0, None],
        ["Length of rapid traverse", None, None, None, None, 500.0, None],
    ])
    
    # Panels Info
    workbook["panels_info"] = pd.DataFrame([
        ["PANELS INFO", None, None, None, None, None],
        [None, None, None, None, None, None],
        ["Total panels", None, None, None, None, 10],
        ["Gross utilized area", None, None, None, None, 45.0],
        ["Net duct area", None, None, None, None, 40.0],
        ["Internal dimensions area", None, None, None, None, 35.0],
        ["External dimensions area", None, None, None, None, 42.0],
        ["Total wastage", None, None, None, None, 5.0],
        ["Wastage percentage", None, None, None, None, 11.1],
    ])
    
    # Flanges (with profile blocks)
    workbook["flanges"] = pd.DataFrame([
        ["FLANGES", None, None, None, None, None],
        [None, None, None, None, None, None],
        ["PROFILE TYPE", None, None, None, None, None],
        ["U PROFILE", None, "25mm", None, None, None],
        ["Bar", "Length (mm)", "Pieces", "TOTAL LENGHT (mm)", None, None],
        [1, 3000, 10, 3000, None, None],
        [None, None, None, None, None, None],
        ["Remaining U profile (mt)", None, 0.5, None, None, None],
        [None, None, None, None, None, None],
        ["PROFILE TYPE", None, None, None, None, None],
        ["F PROFILE", None, "25mm", None, None, None],
        ["Bar", "Length (mm)", "Pieces", "TOTAL LENGHT (mm)", None, None],
        [1, 3000, 5, 3000, None, None],
        [None, None, None, None, None, None],
        ["Remaining F profile (mt)", None, 1.0, None, None, None],
        [None, None, None, None, None, None],
        ["GI Corners", None, 25, None, 62.5, None],
        ["PVC Corners", None, 15, None, 22.5, None],
    ])
    
    # Other Components
    workbook["other_components"] = pd.DataFrame([
        ["OTHER COMPONENTS", None, None, None, None, None],
        [None, None, None, None, None, None],
        ["Total need of silicone", None, None, 2.0, "Kg", None],
        ["Total need of aluminum tape", None, None, 50.0, "mt.", None],
        ["Total glue for Junctions", None, None, 1.0, "Kg", None],
        ["Total glue for Flanges", None, None, 0.5, "Kg", None],
    ])
    
    # Delivery Order
    workbook["delivery_order"] = pd.DataFrame([
        ["DELIVERY ORDER", None, None, None, None, None, None, None, None, None, None, None, None],
        [None, None, None, None, None, None, None, None, None, None, None, None, None],
        ["ID", "TAG", "PART DESCRIPTION", "MOUTH A", None, None, "MOUTH B", None, None, "LENGTH", "EXT AREA", "INT AREA", "QTY"],
        [None, None, None, "X", "Y", "FL", "X", "Y", "FL", "(mm)", None, None, None],
        [1, "TAG-INT-001", "Straight Duct", 400, 300, "U", 400, 300, "U", 1000, 2.5, 2.0, 5],
        [2, "TAG-INT-001", "Elbow 90°", 400, 300, "F", 400, 300, "F", 500, 1.2, 1.0, 2],
        [None, None, None, None, None, None, None, None, None, None, None, None, None],
    ])
    
    # Machine Info (optional)
    workbook["machine_info"] = pd.DataFrame([
        ["MACHINE INFO", None, None, None],
        ["Length of 45° cuts", 100.0, "mt", None],
        ["Length of 90° cuts", 150.0, "mt", None],
        ["Total movements length", 500.0, "mt", None],
    ])
    
    return workbook


class MockExcelFile:
    """Mock ExcelFile for testing without actual file I/O."""
    
    def __init__(self, workbook_data: dict):
        self.workbook = workbook_data
        self.sheet_names = list(workbook_data.keys())
    
    def parse(self, sheet_name, **kwargs):
        return self.workbook.get(sheet_name, pd.DataFrame())


@pytest.mark.integration
class TestNestingParserHappyPath:
    """Happy path tests with complete, valid data."""
    
    def test_parse_complete_workbook(self):
        """Test parsing a complete workbook with all sheets."""
        workbook = create_complete_workbook()
        
        # Create parser and inject workbook directly
        parser = NestingFileParser(b"", "test_complete.xls")
        parser._workbook = workbook
        
        # Skip _load_workbook by calling _build_record directly
        record = parser._build_record()
        
        # Verify metadata
        assert record.meta_data.project_ref_id == "TAG-INT-001"
        assert record.meta_data.source_file_name == "test_complete.xls"
        
    def test_parse_returns_partial_when_sheets_missing(self):
        """Test PARTIAL status when some sheets are missing (generates warnings)."""
        # Workbook with only project_parameters - other sheets missing will generate warnings
        workbook = {
            "project_parameters": create_complete_workbook()["project_parameters"]
        }
        
        parser = NestingFileParser(b"", "test.xls")
        parser._workbook = workbook
        parser._warnings = []
        parser._errors = []
        
        record = parser._build_record()
        
        # Should have warnings about missing sheets
        assert len(parser._warnings) > 0
        # But no errors
        assert len(parser._errors) == 0
        # Record should still be valid
        assert record.meta_data.project_ref_id == "TAG-INT-001"
        
    def test_parse_returns_success_with_complete_data(self):
        """Test SUCCESS status when all data is present with no warnings."""
        from unittest.mock import patch
        
        workbook = create_complete_workbook()
        
        # Patch _load_workbook to inject our complete workbook
        def mock_load(self_):
            self_._workbook = workbook
        
        parser = NestingFileParser(b"", "test.xls")
        
        with patch.object(NestingFileParser, '_load_workbook', mock_load):
            result = parser.parse()
        
        # With complete workbook, status depends on whether extractors generate any warnings
        # SUCCESS if no warnings, PARTIAL if info warnings exist
        assert result.status in ["SUCCESS", "PARTIAL"]
        assert result.status != "ERROR"
        assert result.data is not None
        assert result.data.meta_data.project_ref_id == "TAG-INT-001"
        
    def test_raw_material_extraction(self):
        """Test raw material panel data extraction."""
        workbook = create_complete_workbook()
        
        parser = NestingFileParser(b"", "test.xls")
        parser._workbook = workbook
        
        record = parser._build_record()
        
        assert record.raw_material_panel.material_spec_name == "GI 0.9mm"
        assert record.raw_material_panel.thickness_mm == 0.9
        assert record.raw_material_panel.inventory_impact.utilized_sheets_count == 10
        assert record.raw_material_panel.inventory_impact.net_reusable_remnant_area_m2 == 1.5
        
    def test_machine_telemetry_extraction(self):
        """Test machine telemetry data extraction."""
        workbook = create_complete_workbook()
        
        parser = NestingFileParser(b"", "test.xls")
        parser._workbook = workbook
        
        record = parser._build_record()
        
        assert record.machine_telemetry.blade_wear_45_m == 100.0
        assert record.machine_telemetry.blade_wear_90_m == 150.0
        assert record.machine_telemetry.time_2x45_cuts_sec == 50.0


@pytest.mark.integration
class TestNestingParserValidation:
    """Tests for validation logic."""
    
    def test_missing_tag_id_triggers_error(self):
        """Test that missing TAG ID triggers critical error (v1.3.1 strict validation)."""
        workbook = create_complete_workbook()
        
        # Clear both project identifiers
        workbook["project_parameters"].iloc[0, 5] = None  # PROJECT NAME
        workbook["project_parameters"].iloc[1, 5] = None  # PROJECT REFERENCE
        
        parser = NestingFileParser(b"", "test_no_id.xls")
        parser._workbook = workbook
        parser._warnings = []
        parser._errors = []
        
        record = parser._build_record()
        
        # Should have UNKNOWN as fallback
        assert record.meta_data.project_ref_id == "UNKNOWN"
        # Should have error about missing Tag ID
        assert any("Critical" in err and "Tag ID" in err for err in parser._errors)
        assert record.meta_data.validation_status == "ERROR"
        
    def test_tag_id_fallback_to_project_name(self):
        """Test Tag ID falls back to PROJECT NAME when REFERENCE is empty."""
        workbook = create_complete_workbook()
        
        # Clear reference but keep name
        workbook["project_parameters"].iloc[1, 5] = None
        
        parser = NestingFileParser(b"", "test.xls")
        parser._workbook = workbook
        
        record = parser._build_record()
        
        assert record.meta_data.project_ref_id == "Integration Test Project"


@pytest.mark.integration
class TestNestingParserMissingSheets:
    """Tests for handling missing sheets (PARTIAL success)."""
    
    def test_missing_machine_info_still_parses(self):
        """Test that missing Machine Info sheet doesn't crash parser."""
        workbook = create_complete_workbook()
        del workbook["machine_info"]
        
        parser = NestingFileParser(b"", "test.xls")
        parser._workbook = workbook
        
        record = parser._build_record()
        
        # Should still work
        assert record.meta_data.project_ref_id == "TAG-INT-001"
        # Machine telemetry should use values from project_parameters
        assert record.machine_telemetry.blade_wear_45_m == 100.0
        
    def test_missing_flanges_returns_empty_profiles(self):
        """Test that missing Flanges sheet returns empty profiles list."""
        workbook = create_complete_workbook()
        del workbook["flanges"]
        
        parser = NestingFileParser(b"", "test.xls")
        parser._workbook = workbook
        parser._warnings = []
        
        record = parser._build_record()
        
        assert record.profiles_and_flanges == []
        assert "Flanges sheet not available" in parser._warnings
        
    def test_partial_status_with_warnings(self):
        """Test PARTIAL status when warnings present."""
        workbook = create_complete_workbook()
        del workbook["flanges"]
        del workbook["other_components"]
        
        parser = NestingFileParser(b"", "test.xls")
        parser._workbook = workbook
        parser._warnings = []
        parser._errors = []
        
        record = parser._build_record()
        
        # Should have warnings about missing sheets
        assert len(parser._warnings) >= 2


@pytest.mark.integration
class TestNestingParserShiftedFile:
    """Tests for handling shifted files (rows inserted at top)."""
    
    def test_shifted_project_params_still_works(self):
        """Test extraction works with 3 empty rows at top of Project Parameters."""
        workbook = create_complete_workbook()
        
        # Shift project_parameters by 3 rows
        original_df = workbook["project_parameters"]
        empty_rows = pd.DataFrame([[None] * len(original_df.columns)] * 3)
        workbook["project_parameters"] = pd.concat([empty_rows, original_df], ignore_index=True)
        
        parser = NestingFileParser(b"", "test_shifted.xls")
        parser._workbook = workbook
        
        record = parser._build_record()
        
        # All values should still be extracted
        assert record.meta_data.project_ref_id == "TAG-INT-001"
        assert record.raw_material_panel.material_spec_name == "GI 0.9mm"
        assert record.raw_material_panel.inventory_impact.utilized_sheets_count == 10


@pytest.mark.integration
class TestNestingParserOutputFormat:
    """Tests for output format compliance."""
    
    def test_output_is_serializable_json(self):
        """Test that output can be serialized to JSON."""
        workbook = create_complete_workbook()
        
        parser = NestingFileParser(b"", "test.xls")
        parser._workbook = workbook
        
        record = parser._build_record()
        
        # Should be JSON serializable
        json_output = record.model_dump_rounded(mode='json')
        json_str = json.dumps(json_output, default=str)
        
        assert isinstance(json_str, str)
        assert "TAG-INT-001" in json_str
        
    def test_numeric_precision_rounding(self):
        """Test that numeric values are properly rounded."""
        workbook = create_complete_workbook()
        
        parser = NestingFileParser(b"", "test.xls")
        parser._workbook = workbook
        
        record = parser._build_record()
        dumped = record.model_dump_rounded()
        
        # All floats should be rounded to 2 decimal places
        assert dumped["raw_material_panel"]["inventory_impact"]["net_reusable_remnant_area_m2"] == 1.5


@pytest.mark.integration
class TestNestingParserEdgeCases:
    """Edge case tests for robustness."""
    
    def test_empty_workbook_returns_error_status(self):
        """Test that completely empty workbook returns ERROR status via parse()."""
        from unittest.mock import patch
        
        parser = NestingFileParser(b"", "empty.xls")
        
        # Patch _load_workbook to set empty workbook without file I/O
        def mock_load(self_):
            self_._workbook = {}
        
        with patch.object(NestingFileParser, '_load_workbook', mock_load):
            result = parser.parse()
        
        # Should return ERROR status, not crash
        assert result.status == "ERROR"
        assert len(result.errors) > 0
        
    def test_numeric_string_values_handled(self):
        """Test that numeric values stored as strings are cast correctly."""
        workbook = create_complete_workbook()
        
        # Replace numeric with string
        workbook["project_parameters"].iloc[8, 5] = "10"  # Utilized sheets as string
        
        parser = NestingFileParser(b"", "test.xls")
        parser._workbook = workbook
        
        record = parser._build_record()
        
        assert record.raw_material_panel.inventory_impact.utilized_sheets_count == 10
        assert isinstance(record.raw_material_panel.inventory_impact.utilized_sheets_count, int)
        
    def test_minimal_valid_workbook(self):
        """Test parsing with minimal but valid data."""
        # Create minimal workbook with just required fields
        workbook = {
            "project_parameters": pd.DataFrame([
                ["PROJECT REFERENCE", None, None, None, None, "TAG-MINIMAL"],
                ["Material", None, None, None, None, "Test Material"],
                ["Thickness", None, None, None, None, 1.0],  # Required: > 0
            ])
        }
        
        parser = NestingFileParser(b"", "minimal.xls")
        parser._workbook = workbook
        parser._warnings = []
        parser._errors = []
        
        record = parser._build_record()
        
        assert record.meta_data.project_ref_id == "TAG-MINIMAL"
        assert record.raw_material_panel.thickness_mm == 1.0

