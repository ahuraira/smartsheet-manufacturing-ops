"""
Unit Tests for Nesting Parser Extractors (v1.3.1)

Tests the actual extraction logic from each sheet type using
realistic DataFrame structures that mimic actual Excel exports.

This ensures the parser will work correctly on real Eurosoft CutExpert files.
"""

import pytest
import pandas as pd
import numpy as np

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fn_parse_nesting.extractors import (
    ProjectParametersExtractor,
    PanelsInfoExtractor,
    FlangesExtractor,
    OtherComponentsExtractor,
    DeliveryOrderExtractor,
    MachineInfoExtractor,
)


def create_project_parameters_sheet():
    """Create realistic Project Parameters sheet DataFrame."""
    # Mimics actual CutExpert export layout: Labels in col 0, values in col 5
    data = [
        ["PROJECT NAME", None, None, None, None, "Office HVAC System", None, None],
        ["PROJECT REFERENCE", None, None, None, None, "TAG-2024-0501", None, None],
        [None, None, None, None, None, None, None, None],
        ["Material", None, None, None, None, "GI 0.9mm Pre-Insulated", None, None],
        ["Thickness", None, None, None, None, 0.9, "mm", None],
        ["Sheet dimension X", None, None, None, None, 3000.0, "mm", None],
        ["Sheet dimension Y", None, None, None, None, 1500.0, "mm", None],
        [None, None, None, None, None, None, None, None],
        ["Utilized sheets", None, None, None, None, 12, None, None],
        ["Total area reusable", None, None, None, None, 1.85, "sqm", None],
        ["Wastage due to nesting", None, None, None, None, 0.42, "sqm", None],
        ["Wastage due to 45°", None, None, None, None, 0.15, "sqm", None],
        ["Wastage due to 2x45°", None, None, None, None, 0.08, "sqm", None],
        [None, None, None, None, None, None, None, None],
        ["Time for marking", None, None, None, None, 180.5, "sec", None],
        ["Time for 45° cuts", None, None, None, None, 245.3, "sec", None],
        ["Time for 90° cuts", None, None, None, None, 320.7, "sec", None],
        ["Time for 2x45° cuts", None, None, None, None, 95.2, "sec", None],
        ["Time for rapid traverse", None, None, None, None, 450.0, "sec", None],
        [None, None, None, None, None, None, None, None],
        ["Length of 45° cuts", None, None, None, None, 125.5, "m", None],
        ["Length of 90° cuts", None, None, None, None, 185.2, "m", None],
        ["Length of 2x45° cuts", None, None, None, None, 45.8, "m", None],
        ["Length of rapid traverse", None, None, None, None, 892.3, "m", None],
    ]
    return pd.DataFrame(data)


def create_panels_info_sheet():
    """Create realistic Panels Info sheet DataFrame."""
    data = [
        ["PANELS INFO", None, None, None, None, None],
        [None, None, None, None, None, None],
        ["Total panels", None, None, None, None, 12],
        ["Gross utilized area", None, None, None, None, 54.0],  # 12 * 3m * 1.5m = 54 sqm
        ["Net duct area", None, None, None, None, 48.5],
        [None, None, None, None, None, None],
        ["Internal dimensions area", None, None, None, None, 42.3],
        ["External dimensions area", None, None, None, None, 51.8],
        [None, None, None, None, None, None],
        ["Total wastage", None, None, None, None, 5.5],
        ["Wastage percentage", None, None, None, None, 10.2],
    ]
    return pd.DataFrame(data)


def create_flanges_sheet():
    """Create realistic Flanges sheet with multiple profile blocks."""
    data = [
        ["FLANGES INFORMATION", None, None, None, None, None],
        [None, None, None, None, None, None],
        # U Profile Block
        ["PROFILE TYPE", None, None, None, None, None],
        ["U PROFILE", None, "25mm", None, None, None],
        ["Bar", "Length (mm)", "Pieces", "TOTAL LENGHT (mm)", None, None],
        [1, 3000, 15, 3000, None, None],
        [2, 3000, 12, 3000, None, None],
        [None, None, None, None, None, None],
        ["Remaining U profile (mt)", None, 0.8, None, None, None],
        [None, None, None, None, None, None],
        # F Profile Block
        ["PROFILE TYPE", None, None, None, None, None],
        ["F PROFILE", None, "25mm", None, None, None],
        ["Bar", "Length (mm)", "Pieces", "TOTAL LENGHT (mm)", None, None],
        [1, 3000, 8, 3000, None, None],
        [None, None, None, None, None, None],
        ["Remaining F profile (mt)", None, 1.2, None, None, None],
        [None, None, None, None, None, None],
        # Accessories section
        ["GI Corners", None, 50, None, 125.0, None],  # Qty and Cost
        ["PVC Corners", None, 30, None, 45.0, None],
    ]
    return pd.DataFrame(data)


def create_other_components_sheet():
    """Create realistic Other Components sheet with consumables."""
    # Side-by-side layout as per v1.3.1 update
    data = [
        ["OTHER COMPONENTS", None, None, None, None, None, None, None],
        [None, None, None, None, None, None, None, None],
        # Left side: Silicone & Tape | Right side: Junction Glue & Flange Glue
        ["Component", "Need", "Extra", "Kg", None, "Component", "Need", "Kg"],
        ["Total need of silicone", 2.5, 5.0, 2.625, None, "Total glue for Junctions", 1.8, 1.8],
        ["Total need of aluminum tape", 85.0, 2.5, 87.125, None, "Total glue for Flanges", 0.9, 0.9],
        [None, None, None, None, None, None, None, None],
    ]
    return pd.DataFrame(data)


def create_delivery_order_sheet():
    """Create realistic Delivery Order sheet with finished goods."""
    data = [
        ["DELIVERY ORDER", None, None, None, None, None, None, None, None, None, None],
        [None, None, None, None, None, None, None, None, None, None, None],
        # Header row
        ["ID", "TAG", "PART DESCRIPTION", "MOUTH A", None, None, "MOUTH B", None, None, "LENGTH", "QTY"],
        [None, None, None, "X", "Y", "FL", "X", "Y", "FL", "(mm)", None],
        # Data rows
        [1, "TAG-001", "Straight Duct 400x300", 400, 300, "U25", 400, 300, "U25", 2000, 5],
        [2, "TAG-001", "Elbow 90° 400x300", 400, 300, "F25", 400, 300, "F25", 500, 2],
        [3, "TAG-001", "Reducer 400x300 to 300x200", 400, 300, "U25", 300, 200, "U25", 300, 3],
        [None, None, None, None, None, None, None, None, None, None, None],
    ]
    return pd.DataFrame(data)


def create_machine_info_sheet():
    """Create realistic Machine Info sheet."""
    data = [
        ["MACHINE INFORMATION", None, None, None],
        [None, None, None, None],
        ["Length of 45° cuts", 125.5, "mt", None],
        ["Length of 90° cuts", 185.2, "mt", None],
        ["Total movements length", 892.3, "mt", None],
        [None, None, None, None],
        ["Time for marking", 180.5, "sec", None],
        ["Time for 45° cuts", 245.3, "sec", None],
        ["Time for 90° cuts", 320.7, "sec", None],
        ["Time for rapid traverse", 450.0, "sec", None],
    ]
    return pd.DataFrame(data)


@pytest.mark.unit
class TestProjectParametersExtractor:
    """Tests for ProjectParametersExtractor."""
    
    def test_extract_project_identity(self):
        """Test extraction of project name and reference."""
        df = create_project_parameters_sheet()
        extractor = ProjectParametersExtractor(df)
        data = extractor.extract()
        
        assert data.project_name == "Office HVAC System"
        assert data.project_reference == "TAG-2024-0501"
        
    def test_extract_material_specs(self):
        """Test extraction of material specifications."""
        df = create_project_parameters_sheet()
        extractor = ProjectParametersExtractor(df)
        data = extractor.extract()
        
        assert data.material == "GI 0.9mm Pre-Insulated"
        assert data.thickness_mm == 0.9
        assert data.sheet_dim_x_mm == 3000.0
        assert data.sheet_dim_y_mm == 1500.0
        
    def test_extract_inventory_impact(self):
        """Test extraction of inventory deduction data."""
        df = create_project_parameters_sheet()
        extractor = ProjectParametersExtractor(df)
        data = extractor.extract()
        
        assert data.utilized_sheets == 12
        assert data.total_reusable_area_m2 == 1.85
        
    def test_extract_waste_metrics(self):
        """Test extraction of waste/efficiency data."""
        df = create_project_parameters_sheet()
        extractor = ProjectParametersExtractor(df)
        data = extractor.extract()
        
        assert data.wastage_nesting_m2 == 0.42
        assert data.wastage_45_deg_m2 == 0.15
        assert data.wastage_2x45_deg_m2 == 0.08
        
    def test_extract_machine_telemetry_time(self):
        """Test extraction of machine time telemetry."""
        df = create_project_parameters_sheet()
        extractor = ProjectParametersExtractor(df)
        data = extractor.extract()
        
        assert data.time_marking_sec == 180.5
        assert data.time_45_cuts_sec == 245.3
        assert data.time_90_cuts_sec == 320.7
        assert data.time_2x45_cuts_sec == 95.2
        assert data.time_rapid_traverse_sec == 450.0
        
    def test_extract_machine_telemetry_length(self):
        """Test extraction of machine length telemetry."""
        df = create_project_parameters_sheet()
        extractor = ProjectParametersExtractor(df)
        data = extractor.extract()
        
        assert data.length_45_cuts_m == 125.5
        assert data.length_90_cuts_m == 185.2
        assert data.length_2x45_cuts_m == 45.8
        assert data.length_rapid_traverse_m == 892.3
        
    def test_get_tag_id_with_valid_reference(self):
        """Test Tag ID extraction with valid reference."""
        df = create_project_parameters_sheet()
        extractor = ProjectParametersExtractor(df)
        tag_id, warnings = extractor.get_tag_id()
        
        assert tag_id == "TAG-2024-0501"
        
    def test_get_tag_id_fallback_to_name(self):
        """Test Tag ID falls back to project name when reference empty."""
        df = create_project_parameters_sheet()
        # Clear the reference
        df.iloc[1, 5] = None
        
        extractor = ProjectParametersExtractor(df)
        tag_id, warnings = extractor.get_tag_id()
        
        assert tag_id == "Office HVAC System"
        
    def test_get_tag_id_unknown_when_both_empty(self):
        """Test UNKNOWN returned when both name and reference empty."""
        df = create_project_parameters_sheet()
        df.iloc[0, 5] = None  # Clear name
        df.iloc[1, 5] = None  # Clear reference
        
        extractor = ProjectParametersExtractor(df)
        tag_id, warnings = extractor.get_tag_id()
        
        assert tag_id == "UNKNOWN"
        
    def test_shifted_file_extraction(self):
        """Test extraction still works with shifted file (3 empty rows)."""
        df = create_project_parameters_sheet()
        # Insert 3 empty rows at top
        empty_rows = pd.DataFrame([[None] * len(df.columns)] * 3)
        shifted_df = pd.concat([empty_rows, df], ignore_index=True)
        
        extractor = ProjectParametersExtractor(shifted_df)
        data = extractor.extract()
        
        # All values should still be extracted correctly
        assert data.project_reference == "TAG-2024-0501"
        assert data.thickness_mm == 0.9
        assert data.utilized_sheets == 12


@pytest.mark.unit
class TestMachineInfoExtractor:
    """Tests for MachineInfoExtractor."""
    
    def test_extract_cut_lengths(self):
        """Test extraction of cutting lengths."""
        df = create_machine_info_sheet()
        extractor = MachineInfoExtractor(df)
        data = extractor.extract()
        
        assert data.length_45_cuts_m == 125.5
        assert data.length_90_cuts_m == 185.2
        assert data.rapid_traverse_length_m == 892.3
        
    def test_extract_times(self):
        """Test extraction of operation times."""
        df = create_machine_info_sheet()
        extractor = MachineInfoExtractor(df)
        data = extractor.extract()
        
        assert data.time_marking_sec == 180.5
        assert data.time_45_cuts_sec == 245.3
        assert data.time_90_cuts_sec == 320.7
        assert data.time_rapid_traverse_sec == 450.0


@pytest.mark.unit
class TestMissingDataHandling:
    """Tests for graceful handling of missing or malformed data."""
    
    def test_empty_sheet_returns_defaults(self):
        """Test empty sheet returns default values without crashing."""
        df = pd.DataFrame()
        extractor = ProjectParametersExtractor(df)
        data = extractor.extract()
        
        assert data.project_reference is None
        assert data.thickness_mm == 0.0
        assert data.utilized_sheets == 0
        
    def test_partial_data_extracts_available(self):
        """Test partial data extraction (some anchors missing)."""
        # Only include some anchors
        data = [
            ["PROJECT REFERENCE", None, None, None, None, "TAG-PARTIAL"],
            ["Thickness", None, None, None, None, 1.2],
            # Missing: Material, Utilized sheets, etc.
        ]
        df = pd.DataFrame(data)
        extractor = ProjectParametersExtractor(df)
        result = extractor.extract()
        
        assert result.project_reference == "TAG-PARTIAL"
        assert result.thickness_mm == 1.2
        assert result.material is None  # Not found
        assert result.utilized_sheets == 0  # Default


@pytest.mark.unit
class TestParsingModelsValidation:
    """Tests for Pydantic model validation in parsing context."""
    
    def test_nesting_execution_record_creation(self):
        """Test full record creation with extracted data."""
        from fn_parse_nesting.models import (
            NestingExecutionRecord, MetaData, RawMaterialPanel,
            InventoryImpact, EfficiencyMetrics
        )
        
        # Build from extracted project data
        df = create_project_parameters_sheet()
        extractor = ProjectParametersExtractor(df)
        project_data = extractor.extract()
        
        # Create the record
        record = NestingExecutionRecord(
            meta_data=MetaData(
                project_ref_id=project_data.project_reference,
                source_file_name="test.xls"
            ),
            raw_material_panel=RawMaterialPanel(
                material_spec_name=project_data.material,
                thickness_mm=project_data.thickness_mm,
                inventory_impact=InventoryImpact(
                    utilized_sheets_count=project_data.utilized_sheets,
                    net_reusable_remnant_area_m2=project_data.total_reusable_area_m2
                ),
                efficiency_metrics=EfficiencyMetrics(
                    nesting_waste_m2=project_data.wastage_nesting_m2,
                    tech_waste_45_deg_m2=project_data.wastage_45_deg_m2
                )
            )
        )
        
        assert record.meta_data.project_ref_id == "TAG-2024-0501"
        assert record.raw_material_panel.material_spec_name == "GI 0.9mm Pre-Insulated"
        assert record.raw_material_panel.inventory_impact.utilized_sheets_count == 12
