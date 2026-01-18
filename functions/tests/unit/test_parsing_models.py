"""
Unit Tests for Nesting Parser Models (v1.3.1)

Tests for the Pydantic models used in the Nesting Parser.
Ensures correct data validation, rounding, and structure.
"""

import pytest
from datetime import datetime
from pydantic import ValidationError

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


@pytest.mark.unit
class TestMetaDataModel:
    """Tests for MetaData model."""
    
    def test_valid_metadata(self):
        """Test creating valid MetaData."""
        from fn_parse_nesting.models import MetaData
        
        meta = MetaData(
            project_ref_id="TAG-001",
            project_name="Test Project",
            source_file_name="test.xls"
        )
        assert meta.project_ref_id == "TAG-001"
        assert meta.validation_status == "OK"
        assert isinstance(meta.extraction_timestamp_utc, datetime)

    def test_project_ref_id_required(self):
        """Test that project_ref_id cannot be empty."""
        from fn_parse_nesting.models import MetaData
        
        with pytest.raises(ValidationError):
            MetaData(
                project_ref_id="",  # Empty
                source_file_name="test.xls"
            )

    def test_validation_status_literals(self):
        """Test validation_status restricts values."""
        from fn_parse_nesting.models import MetaData
        
        with pytest.raises(ValidationError):
            MetaData(
                project_ref_id="TAG-001",
                source_file_name="test.xls",
                validation_status="INVALID_STATUS"  # Not OK/WARNING/ERROR
            )


@pytest.mark.unit
class TestProfileConsumptionModel:
    """Tests for ProfileConsumption model."""
    
    def test_profile_consumption_defaults(self):
        """Test defaults are set correctly."""
        from fn_parse_nesting.models import ProfileConsumption
        
        pc = ProfileConsumption(profile_type="U PROFILE")
        assert pc.total_consumption_m == 0.0
        assert pc.remnant_generated_m == 0.0
        assert pc.bar_count == 0


@pytest.mark.unit
class TestNestingExecutionRecord:
    """Tests for the main NestingExecutionRecord."""
    
    def test_record_structure(self):
        """Test complete record structure."""
        from fn_parse_nesting.models import (
            NestingExecutionRecord, MetaData, RawMaterialPanel, 
            ProfileConsumption, FlangeAccessories, Consumables
        )
        
        record = NestingExecutionRecord(
            meta_data=MetaData(project_ref_id="TAG-100", source_file_name="file.xls"),
            raw_material_panel=RawMaterialPanel(
                material_spec_name="GI 0.9", 
                thickness_mm=0.9
            ),
            profiles_and_flanges=[
                ProfileConsumption(profile_type="U", total_consumption_m=10.5)
            ],
            flange_accessories=FlangeAccessories(gi_corners_qty=100),
            consumables=Consumables(silicone_consumption_kg=5.5)
        )
        
        assert record.meta_data.project_ref_id == "TAG-100"
        assert len(record.profiles_and_flanges) == 1
        assert record.flange_accessories.gi_corners_qty == 100
        assert record.consumables.silicone_consumption_kg == 5.5

    def test_recursive_rounding(self):
        """Test model_dump_rounded performs recursive rounding."""
        from fn_parse_nesting.models import (
            NestingExecutionRecord, MetaData, RawMaterialPanel, 
            BillingMetrics
        )
        
        record = NestingExecutionRecord(
            meta_data=MetaData(project_ref_id="TAG-100", source_file_name="file.xls"),
            raw_material_panel=RawMaterialPanel(
                material_spec_name="GI 0.9", 
                thickness_mm=0.9
            ),
            billing_metrics=BillingMetrics(
                total_internal_area_m2=123.456789,
                total_external_area_m2=987.654321
            )
        )
        
        dumped = record.model_dump_rounded()
        assert dumped["billing_metrics"]["total_internal_area_m2"] == 123.46  # Rounded to 2 decimals
        assert dumped["billing_metrics"]["total_external_area_m2"] == 987.65


@pytest.mark.unit
class TestConsumablesModel:
    """Tests for Consumables model (v1.3.1 updates)."""
    
    def test_consumables_extra_fields(self):
        """Test new extra_pct fields in Consumables."""
        from fn_parse_nesting.models import Consumables
        
        c = Consumables(
            silicone_consumption_kg=10.0,
            silicone_extra_pct=5.0,
            aluminum_tape_consumption_m=50.0,
            aluminum_tape_extra_pct=2.5
        )
        
        assert c.silicone_extra_pct == 5.0
        assert c.aluminum_tape_extra_pct == 2.5
        assert c.glue_junction_extra_pct == 0.0  # Default


@pytest.mark.unit
class TestMachineTelemetryModel:
    """Tests for MachineTelemetry model (v1.3.1 updates)."""
    
    def test_machine_telemetry_fields(self):
        """Test new telemetry fields."""
        from fn_parse_nesting.models import MachineTelemetry
        
        mt = MachineTelemetry(
            time_2x45_cuts_sec=120.5,
            blade_wear_45_m=50.0
        )
        
        assert mt.time_2x45_cuts_sec == 120.5
        assert mt.blade_wear_45_m == 50.0
