"""
Unit Tests for BOM Generator (v1.6.0)

Tests the flattening and normalization of nesting records into BOM lines.
Challenges assumptions about:
1. Material Normalization (case, trim)
2. Zero-Quantity filtering
3. Multi-type material extraction
4. Machine wear inclusion logic
"""

import pytest
from unittest.mock import MagicMock
from fn_parse_nesting.bom_generator import BOMGenerator, BOMLine
from fn_parse_nesting.models import (
    NestingExecutionRecord, RawMaterialPanel, MetaData, 
    InventoryImpact, EfficiencyMetrics, ProfileConsumption,
    FlangeAccessories, Consumables, MachineTelemetry
)

@pytest.fixture
def mock_full_record():
    return NestingExecutionRecord(
        meta_data=MetaData(
            project_ref_id="TAG-001",
            source_file_name="test.xlsx"
        ),
        raw_material_panel=RawMaterialPanel(
            material_spec_name="  GI 0.9mm  ",  # Needs normalization
            thickness_mm=0.9,
            inventory_impact=InventoryImpact(utilized_sheets_count=2, gross_area_m2=5.5),
            efficiency_metrics=EfficiencyMetrics(waste_pct=10.0)
        ),
        profiles_and_flanges=[
            ProfileConsumption(profile_type="u profile", total_consumption_m=10.0),
            ProfileConsumption(profile_type="joint", total_consumption_m=0.0) # Should be skipped
        ],
        flange_accessories=FlangeAccessories(
            gi_corners_qty=100,
            pvc_corners_qty=50
            # Costs are ignored by BOM generator, only qty matters
        ),
        consumables=Consumables(
            silicone_consumption_kg=2.5,
            aluminum_tape_consumption_m=20.0,
            glue_junction_kg=0.0, # Should be skipped
            glue_flange_kg=1.0
        ),
        machine_telemetry=MachineTelemetry(
            blade_wear_45_m=50.0,
            blade_wear_90_m=30.0
        )
    )

@pytest.mark.unit
class TestBOMGenerator:
    
    def test_generate_flat_structure(self, mock_full_record):
        """Test that all material types are flattened into a single list."""
        generator = BOMGenerator(include_machine_wear=False)
        lines = generator.generate(mock_full_record)
        
        # Expected Breakdown:
        # 1. Panel (1)
        # 2. Profiles (1 - 'u profile', 'joint' skipped due to 0 qty)
        # 3. Accessories (2 - gi, pvc)
        # 4. Consumables (3 - silicone, tape, glue_flange)
        # Total = 7 lines
        
        assert len(lines) == 7
        
        # Verify types present
        types = [l.material_type for l in lines]
        assert "PANEL" in types
        assert "PROFILE" in types
        assert "ACCESSORY" in types
        assert "CONSUMABLE" in types
        assert "MACHINE" not in types

    def test_normalization_logic(self, mock_full_record):
        """Test that descriptions are normalized (lowercase, trimmed)."""
        generator = BOMGenerator()
        lines = generator.generate(mock_full_record)
        
        # Panel input was "  GI 0.9mm  "
        panel_line = next(l for l in lines if l.material_type == "PANEL")
        assert panel_line.nesting_description == "gi 0.9mm"
        
        # Profile check
        profile_line = next(l for l in lines if l.material_type == "PROFILE")
        assert profile_line.nesting_description == "u profile"

    def test_zero_quantity_filtering(self, mock_full_record):
        """Test that items with 0 quantity are strictly excluded."""
        generator = BOMGenerator()
        lines = generator.generate(mock_full_record)
        
        descriptions = [l.nesting_description for l in lines]
        assert "joint" not in descriptions
        assert "glue junction" not in descriptions

    def test_machine_wear_inclusion(self, mock_full_record):
        """Test explicit inclusion of machine wear items."""
        generator = BOMGenerator(include_machine_wear=True)
        lines = generator.generate(mock_full_record)
        
        # Should add blade 45 and blade 90 (2 items)
        # 7 base + 2 machine = 9
        assert len(lines) == 9
        
        wear_lines = [l for l in lines if l.material_type == "MACHINE"]
        assert len(wear_lines) == 2
        assert any(l.nesting_description == "blade 45" for l in wear_lines)
        
    def test_empty_record(self):
        """Test handling of empty or minimal record."""
        # Minimal record with minimal valid raw_material_panel
        empty_record = NestingExecutionRecord(
            meta_data=MetaData(project_ref_id="TAG-EMPTY", source_file_name="empty.xlsx"),
            raw_material_panel=RawMaterialPanel(
                material_spec_name="Dummy",
                thickness_mm=20.0, 
                inventory_impact=InventoryImpact(),
                efficiency_metrics=EfficiencyMetrics()
            )
        )
        
        # Override generate to simulate empty components if needed, but here 
        # we actuaally want to test successful generation of 0 lines if sub-components are empty.
        # But wait, RawMaterialPanel IS a component.
        # Use generator that filters out dummy panel if needed, or just assert what IS generated.
        
        # Actually, let's look at BOMGenerator logic:
        # It checks `if record.raw_material_panel: ...`
        # But Pydantic requires raw_material_panel to be present.
        # So we provide it, but with 0 consumption.
        
        empty_record.raw_material_panel.inventory_impact.gross_area_m2 = 0
        
        generator = BOMGenerator()
        lines = generator.generate(empty_record)
        
        assert len(lines) == 0

    def test_line_numbering(self, mock_full_record):
        """Test that line numbers are sequential."""
        generator = BOMGenerator()
        lines = generator.generate(mock_full_record)
        
        line_numbers = [l.line_number for l in lines]
        assert line_numbers == [1, 2, 3, 4, 5, 6, 7]
        assert len(set(line_numbers)) == 7  # All unique

