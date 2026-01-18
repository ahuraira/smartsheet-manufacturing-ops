"""
Tests for fn_parse_nesting
==========================

Unit and integration tests for the nesting file parser.
"""

import pytest
import pandas as pd
import io
import json
from datetime import datetime
from pathlib import Path

# Import parser components (run pytest from functions directory)
from fn_parse_nesting.models import (
    NestingExecutionRecord,
    MetaData,
    RawMaterialPanel,
    InventoryImpact,
    EfficiencyMetrics,
    ProfileConsumption,
    Consumables,
    MachineTelemetry,
    FinishedGoodsLine,
    ParsingResult,
)
from fn_parse_nesting.anchor_finder import AnchorFinder, AnchorNotFoundError
from fn_parse_nesting.extractors.project_parameters import ProjectParametersExtractor
from fn_parse_nesting.extractors.flanges import FlangesExtractor
from fn_parse_nesting.parser import NestingFileParser


class TestAnchorFinder:
    """Tests for the AnchorFinder utility."""
    
    def test_find_anchor_simple(self):
        """Test finding a simple text anchor."""
        df = pd.DataFrame([
            ["Header", "Value"],
            ["Material", "Fittings 25"],
            ["Thickness", 25.0],
        ])
        
        finder = AnchorFinder(df, "test")
        pos = finder.find_anchor("Material")
        
        assert pos is not None
        assert pos == (1, 0)
    
    def test_find_anchor_case_insensitive(self):
        """Test case-insensitive anchor search."""
        df = pd.DataFrame([
            ["MATERIAL", "Fittings 25"],
        ])
        
        finder = AnchorFinder(df, "test")
        pos = finder.find_anchor("material", case_sensitive=False)
        
        assert pos is not None
        assert pos == (0, 0)
    
    def test_find_anchor_not_found(self):
        """Test when anchor is not found."""
        df = pd.DataFrame([
            ["Header", "Value"],
        ])
        
        finder = AnchorFinder(df, "test")
        pos = finder.find_anchor("NonExistent")
        
        assert pos is None
    
    def test_get_value_by_anchor(self):
        """Test extracting value by anchor with offset."""
        df = pd.DataFrame([
            ["Label", None, None, None, None, "Value"],
            ["Material", None, None, None, None, "Fittings 25"],
            ["Thickness", None, None, None, None, 25.0],
        ])
        
        finder = AnchorFinder(df, "test")
        value = finder.get_value_by_anchor("Material", row_offset=0, col_offset=5, cast_type=str)
        
        assert value == "Fittings 25"
    
    def test_get_value_with_type_casting(self):
        """Test type casting of extracted values."""
        df = pd.DataFrame([
            ["Thickness", None, None, None, None, "25.5"],
        ])
        
        finder = AnchorFinder(df, "test")
        value = finder.get_value_by_anchor("Thickness", col_offset=5, cast_type=float)
        
        assert value == 25.5
        assert isinstance(value, float)
    
    def test_find_all_anchors(self):
        """Test finding multiple occurrences of an anchor."""
        df = pd.DataFrame([
            ["PROFILE TYPE", "U PROFILE"],
            ["Data row", 100],
            ["PROFILE TYPE", "F PROFILE"],
            ["Data row", 200],
        ])
        
        finder = AnchorFinder(df, "test")
        positions = finder.find_all_anchors("PROFILE TYPE")
        
        assert len(positions) == 2
        assert (0, 0) in positions
        assert (2, 0) in positions
    
    def test_extract_table(self):
        """Test table extraction with column mapping."""
        df = pd.DataFrame([
            ["ID", "DESCRIPTION", "QTY"],
            [1, "Part A", 5],
            [2, "Part B", 10],
            [None, None, None],  # End of table
        ])
        
        finder = AnchorFinder(df, "test")
        rows = finder.extract_table(
            header_row=0,
            column_mapping={
                "ID": "line_id",
                "DESCRIPTION": "desc",
                "QTY": "quantity",
            }
        )
        
        assert len(rows) == 2
        assert rows[0]["line_id"] == 1
        assert rows[1]["desc"] == "Part B"


class TestPydanticModels:
    """Tests for Pydantic model validation."""
    
    def test_meta_data_valid(self):
        """Test valid MetaData creation."""
        meta = MetaData(
            project_ref_id="TAG-12345",
            project_name="Test Project",
            source_file_name="test.xlsx",
        )
        
        assert meta.project_ref_id == "TAG-12345"
        assert meta.validation_status == "OK"
    
    def test_meta_data_empty_ref_fails(self):
        """Test that empty project_ref_id fails validation."""
        with pytest.raises(ValueError):
            MetaData(
                project_ref_id="",
                source_file_name="test.xlsx",
            )
    
    def test_raw_material_panel(self):
        """Test RawMaterialPanel with nested models."""
        panel = RawMaterialPanel(
            material_spec_name="Fittings 25",
            thickness_mm=25.0,
            sheet_dim_x_mm=4000,
            sheet_dim_y_mm=1500,
        )
        
        assert panel.material_spec_name == "Fittings 25"
        assert panel.inventory_impact.utilized_sheets_count == 0  # Default
    
    def test_profile_consumption(self):
        """Test ProfileConsumption model."""
        profile = ProfileConsumption(
            profile_type="U PROFILE",
            total_consumption_m=125.5,
            remnant_generated_m=3.2,
            bar_count=5,
        )
        
        assert profile.profile_type == "U PROFILE"
        assert profile.total_consumption_m == 125.5
    
    def test_nesting_record_model_dump_rounded(self):
        """Test rounding in model export."""
        record = NestingExecutionRecord(
            meta_data=MetaData(
                project_ref_id="TAG-123",
                source_file_name="test.xlsx",
            ),
            raw_material_panel=RawMaterialPanel(
                material_spec_name="Test",
                thickness_mm=25.123456789,
            ),
        )
        
        data = record.model_dump_rounded()
        assert data["raw_material_panel"]["thickness_mm"] == 25.12


class TestParsingResult:
    """Tests for ParsingResult wrapper."""
    
    def test_success_result(self):
        """Test successful parsing result."""
        result = ParsingResult(
            status="SUCCESS",
            source_file="test.xlsx",
            processing_time_ms=150.5,
        )
        
        assert result.status == "SUCCESS"
        assert result.processing_time_ms == 150.5
        assert result.errors == []
    
    def test_partial_result(self):
        """Test partial parsing result with warnings."""
        result = ParsingResult(
            status="PARTIAL",
            source_file="test.xlsx",
            warnings=["Missing optional sheet"],
        )
        
        assert result.status == "PARTIAL"
        assert len(result.warnings) == 1


class TestNestingFileParser:
    """Integration tests for the full parser."""
    
    @pytest.fixture
    def sample_workbook(self):
        """Create a sample multi-sheet Excel workbook in memory."""
        output = io.BytesIO()
        
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            # Project parameters sheet
            project_params = pd.DataFrame([
                ["PROJECT NAME", None, None, None, None, "TAG-12345"],
                ["PROJECT REFERENCE", None, None, None, None, "TAG-12345"],
                ["Material", None, None, None, None, "Fittings 25"],
                ["Thickness", None, None, None, None, 25.0],
                ["Sheet dimension X", None, None, None, None, 4000],
                ["Sheet dimension Y", None, None, None, None, 1500],
                ["Utilized sheets", None, None, None, None, 3],
                ["Total area reusable", None, None, None, None, 1.5],
                ["Wastage due to nesting", None, None, None, None, 0.8],
                ["Wastage due to 45°", None, None, None, None, 0.2],
                ["Length of 45° cuts", None, None, None, None, 45.5],
                ["Length of 90° cuts", None, None, None, None, 120.3],
                ["Length of rapid traverse", None, None, None, None, 250.0],
            ])
            project_params.to_excel(writer, sheet_name="Project parameters", index=False, header=False)
            
            # Panels info sheet
            panels_info = pd.DataFrame([
                ["Material", None, None, None, "Fittings 25"],
                ["Thickness (mm)", None, None, None, 25.0],
                ["Total area of utilized panels", None, None, None, 18.0],
                ["Area of internal dimensions", None, None, None, 15.5],
                ["Area of external dimensions", None, None, None, 16.2],
                ["Total wastage", None, None, None, 0.8],
            ])
            panels_info.to_excel(writer, sheet_name="Panels info", index=False, header=False)
            
            # Delivery order sheet
            delivery = pd.DataFrame([
                ["ID", "PART DESCRIPTION", "MOUTH A X", "MOUTH A Y", "QTY", "TAG"],
                [1, "Straight Duct 300x200", 300, 200, 5, "TAG-12345"],
                [2, "Elbow 90° 250x150", 250, 150, 2, "TAG-12345"],
                [3, "Transition 300>250", 300, 200, 1, "TAG-12345"],
            ])
            delivery.to_excel(writer, sheet_name="Delivery order", index=False, header=False)
            
            # Flanges sheet (simplified)
            flanges = pd.DataFrame([
                ["PROFILE TYPE", None, None, None, None, "mm", 25],
                ["U PROFILE", None, None, None, None, None, None],
                [None, None, "Description", "TOTAL LENGHT (mm)", None, None, None],
                [None, None, "Flange 1", 500, None, None, None],
                [None, None, "Flange 2", 750, None, None, None],
                ["Remaining U profile (mt.)", None, None, None, 1.2, None, None],
            ])
            flanges.to_excel(writer, sheet_name="Flanges", index=False, header=False)
            
            # Other components sheet
            other = pd.DataFrame([
                ["Total need of silicone", None, 2.5],
                ["Total need of aluminum tape", None, 15.0],
            ])
            other.to_excel(writer, sheet_name="Other components", index=False, header=False)
        
        return output.getvalue()
    
    def test_parse_sample_workbook(self, sample_workbook):
        """Test parsing a sample Excel workbook."""
        parser = NestingFileParser(
            file_content=sample_workbook,
            filename="test_nesting.xlsx",
        )
        
        result = parser.parse()
        
        assert result.status in ["SUCCESS", "PARTIAL"]
        assert result.data is not None
        assert result.data.meta_data.project_ref_id == "TAG-12345"
        assert result.data.raw_material_panel.material_spec_name == "Fittings 25"
        assert result.data.raw_material_panel.thickness_mm == 25.0
    
    def test_parse_missing_sheets(self):
        """Test parsing when sheets are missing."""
        # Create minimal workbook with only Project parameters
        output = io.BytesIO()
        
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            project_params = pd.DataFrame([
                ["PROJECT REFERENCE", None, None, None, None, "TAG-99999"],
                ["Material", None, None, None, None, "Test Material"],
                ["Thickness", None, None, None, None, 20.0],
            ])
            project_params.to_excel(writer, sheet_name="Project parameters", index=False, header=False)
        
        parser = NestingFileParser(
            file_content=output.getvalue(),
            filename="minimal.xlsx",
        )
        
        result = parser.parse()
        
        # Should still parse with warnings
        assert result.status == "PARTIAL"
        assert result.data is not None
        assert result.data.meta_data.project_ref_id == "TAG-99999"
        assert len(result.warnings) > 0


# Run tests if executed directly
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
