"""
Unit Tests for Sheet Configuration

Tests all configuration constants for:
- Sheet names consistency
- Column definitions completeness
- Config key definitions
- Folder structure mappings
"""

import pytest

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.sheet_config import (
    SheetName,
    ColumnName,
    ConfigKey,
    ID_PREFIXES,
    DEFAULT_CONFIG,
    FOLDER_STRUCTURE,
    SHEET_FOLDER_MAP,
)


class TestSheetNames:
    """Tests for SheetName enum."""
    
    @pytest.mark.unit
    def test_all_required_sheets_defined(self):
        """Verify all required sheets are defined."""
        required_sheets = [
            "00 Reference Data",
            "00a Config",
            "01 LPO Master LOG",
            "01 LPO Audit LOG",
            "02 Tag Sheet Registry",
            "03 Production Planning",
            "04 Nesting Execution Log",
            "05 Allocation Log",
            "06 Consumption Log",
            "06a Remnant Log",
            "06b Filler Log",
            "07 Delivery Log",
            "08 Invoice Log",
            "90 Inventory Txn Log",
            "91 Inventory Snapshot",
            "92 SAP Inventory Snapshot",
            "93 Physical Inventory Snapshot",
            "97 Override Log",
            "98 User Action Log",
            "99 Exception Log",
        ]
        sheet_values = [s.value for s in SheetName]
        for sheet in required_sheets:
            assert sheet in sheet_values, f"Missing sheet: {sheet}"
    
    @pytest.mark.unit
    def test_sheet_count(self):
        """Verify expected number of sheets."""
        assert len(SheetName) == 20, "Expected 20 sheets defined"
    
    @pytest.mark.unit
    def test_sheet_enum_is_str(self):
        """Test SheetName inherits from str for easy use."""
        assert isinstance(SheetName.TAG_REGISTRY, str)
        assert SheetName.TAG_REGISTRY == "02 Tag Sheet Registry"


class TestColumnNames:
    """Tests for ColumnName constants."""
    
    @pytest.mark.unit
    def test_common_columns_defined(self):
        """Verify common columns are defined."""
        assert ColumnName.STATUS == "Status"
        assert ColumnName.CREATED_AT == "Created At"
        assert ColumnName.REMARKS == "Remarks"
    
    @pytest.mark.unit
    def test_tag_columns_defined(self):
        """Verify Tag-specific columns are defined."""
        assert ColumnName.TAG_ID == "Tag ID"
        assert ColumnName.TAG_NAME == "Tag Sheet Name/ Rev"
        assert ColumnName.FILE_HASH == "File Hash"
        assert ColumnName.CLIENT_REQUEST_ID == "Client Request ID"
        assert ColumnName.SUBMITTED_BY == "Submitted By"
    
    @pytest.mark.unit
    def test_lpo_columns_defined(self):
        """Verify LPO-specific columns are defined."""
        assert ColumnName.LPO_ID == "LPO ID"
        assert ColumnName.CUSTOMER_LPO_REF == "Customer LPO Ref"
        assert ColumnName.SAP_REFERENCE == "SAP Reference"
        assert ColumnName.LPO_STATUS == "LPO Status"
        assert ColumnName.PO_QUANTITY_SQM == "PO Quantity (Sqm)"
        assert ColumnName.DELIVERED_QUANTITY_SQM == "Delivered Quantity (Sqm)"
    
    @pytest.mark.unit
    def test_config_columns_defined(self):
        """Verify Config-specific columns are defined."""
        assert ColumnName.CONFIG_KEY == "config_key"
        assert ColumnName.CONFIG_VALUE == "config_value"
        assert ColumnName.EFFECTIVE_FROM == "effective_from"
        assert ColumnName.CHANGED_BY == "changed_by"
    
    @pytest.mark.unit
    def test_exception_columns_defined(self):
        """Verify Exception-specific columns are defined."""
        assert ColumnName.EXCEPTION_ID == "Exception ID"
        assert ColumnName.SOURCE == "Source"
        assert ColumnName.RELATED_TAG_ID == "Related Tag ID"
        assert ColumnName.RELATED_TXN_ID == "Related Txn ID"
        assert ColumnName.MATERIAL_CODE == "Material Code"
        assert ColumnName.QUANTITY == "Quantity"
        assert ColumnName.REASON_CODE == "Reason Code"
        assert ColumnName.SEVERITY == "Severity"
        assert ColumnName.SLA_DUE == "SLA Due"
        assert ColumnName.RESOLUTION_ACTION == "Resolution Action"
    
    @pytest.mark.unit
    def test_user_action_columns_defined(self):
        """Verify User Action-specific columns are defined."""
        assert ColumnName.ACTION_ID == "Action ID"
        assert ColumnName.TIMESTAMP == "Timestamp"
        assert ColumnName.USER_ID == "User ID"
        assert ColumnName.ACTION_TYPE == "Action Type"
        assert ColumnName.TARGET_TABLE == "Target Table"
        assert ColumnName.TARGET_ID == "Target ID"
        assert ColumnName.OLD_VALUE == "Old Value"
        assert ColumnName.NEW_VALUE == "New Value"
        assert ColumnName.NOTES == "Notes"


class TestConfigKeys:
    """Tests for ConfigKey enum."""
    
    @pytest.mark.unit
    def test_sequence_keys_defined(self):
        """Verify all sequence keys are defined."""
        sequence_keys = [
            "seq_tag", "seq_exception", "seq_allocation",
            "seq_consumption", "seq_delivery", "seq_nesting",
            "seq_remnant", "seq_filler", "seq_txn"
        ]
        config_values = [c.value for c in ConfigKey]
        for key in sequence_keys:
            assert key in config_values, f"Missing sequence key: {key}"
    
    @pytest.mark.unit
    def test_business_config_keys_defined(self):
        """Verify business configuration keys are defined."""
        business_keys = [
            "min_remnant_area_m2",
            "t1_cutoff_time_local",
            "t1_cutoff_timezone",
            "allocation_expiry_minutes",
            "variance_tolerance_pct",
            "consumption_tolerance_pct",
            "remnant_value_fraction",
            "parser_version_current",
        ]
        config_values = [c.value for c in ConfigKey]
        for key in business_keys:
            assert key in config_values, f"Missing business config key: {key}"
    
    @pytest.mark.unit
    def test_machine_config_keys_defined(self):
        """Verify machine configuration keys are defined."""
        assert ConfigKey.VACUUM_BED_LENGTH_MM.value == "vacuum_bed_length_mm"
        assert ConfigKey.VACUUM_BED_WIDTH_MM.value == "vacuum_bed_width_mm"
    
    @pytest.mark.unit
    def test_shift_config_keys_defined(self):
        """Verify shift configuration keys are defined."""
        assert ConfigKey.SHIFT_MORNING_START.value == "shift_morning_start"
        assert ConfigKey.SHIFT_MORNING_END.value == "shift_morning_end"
        assert ConfigKey.SHIFT_EVENING_START.value == "shift_evening_start"
        assert ConfigKey.SHIFT_EVENING_END.value == "shift_evening_end"
    
    @pytest.mark.unit
    def test_sla_config_keys_defined(self):
        """Verify SLA configuration keys are defined."""
        assert ConfigKey.SLA_EXCEPTION_CRITICAL_HOURS.value == "sla_exception_critical_hours"
        assert ConfigKey.SLA_EXCEPTION_HIGH_HOURS.value == "sla_exception_high_hours"


class TestIDPrefixes:
    """Tests for ID_PREFIXES dictionary."""
    
    @pytest.mark.unit
    def test_all_sequence_keys_have_prefixes(self):
        """Verify all sequence keys have ID prefixes defined."""
        sequence_keys = [
            ConfigKey.SEQ_TAG,
            ConfigKey.SEQ_EXCEPTION,
            ConfigKey.SEQ_ALLOCATION,
            ConfigKey.SEQ_CONSUMPTION,
            ConfigKey.SEQ_DELIVERY,
            ConfigKey.SEQ_NESTING,
            ConfigKey.SEQ_REMNANT,
            ConfigKey.SEQ_FILLER,
            ConfigKey.SEQ_TXN,
        ]
        for key in sequence_keys:
            assert key in ID_PREFIXES, f"Missing ID prefix for: {key}"
            assert isinstance(ID_PREFIXES[key], str)
            assert len(ID_PREFIXES[key]) > 0
    
    @pytest.mark.unit
    def test_prefix_values(self):
        """Verify expected prefix values."""
        assert ID_PREFIXES[ConfigKey.SEQ_TAG] == "TAG"
        assert ID_PREFIXES[ConfigKey.SEQ_EXCEPTION] == "EX"
        assert ID_PREFIXES[ConfigKey.SEQ_ALLOCATION] == "ALLOC"
        assert ID_PREFIXES[ConfigKey.SEQ_CONSUMPTION] == "CON"
        assert ID_PREFIXES[ConfigKey.SEQ_DELIVERY] == "DO"
        assert ID_PREFIXES[ConfigKey.SEQ_NESTING] == "NEST"
        assert ID_PREFIXES[ConfigKey.SEQ_REMNANT] == "REM"
        assert ID_PREFIXES[ConfigKey.SEQ_FILLER] == "FILL"
        assert ID_PREFIXES[ConfigKey.SEQ_TXN] == "TXN"


class TestDefaultConfig:
    """Tests for DEFAULT_CONFIG dictionary."""
    
    @pytest.mark.unit
    def test_all_sequence_counters_initialized(self):
        """Verify all sequence counters start at 0."""
        sequence_keys = [
            ConfigKey.SEQ_TAG, ConfigKey.SEQ_EXCEPTION, ConfigKey.SEQ_ALLOCATION,
            ConfigKey.SEQ_CONSUMPTION, ConfigKey.SEQ_DELIVERY, ConfigKey.SEQ_NESTING,
            ConfigKey.SEQ_REMNANT, ConfigKey.SEQ_FILLER, ConfigKey.SEQ_TXN,
        ]
        for key in sequence_keys:
            assert key in DEFAULT_CONFIG, f"Missing default for: {key}"
            assert DEFAULT_CONFIG[key] == "0", f"Sequence {key} should start at 0"
    
    @pytest.mark.unit
    def test_business_defaults_reasonable(self):
        """Verify business defaults have reasonable values."""
        assert float(DEFAULT_CONFIG[ConfigKey.MIN_REMNANT_AREA_M2]) > 0
        assert float(DEFAULT_CONFIG[ConfigKey.VARIANCE_TOLERANCE_PCT]) > 0
        assert float(DEFAULT_CONFIG[ConfigKey.CONSUMPTION_TOLERANCE_PCT]) > 0
    
    @pytest.mark.unit
    def test_shift_times_format(self):
        """Verify shift times are in expected format."""
        import re
        time_pattern = r'^\d{2}:\d{2}$'
        assert re.match(time_pattern, DEFAULT_CONFIG[ConfigKey.SHIFT_MORNING_START])
        assert re.match(time_pattern, DEFAULT_CONFIG[ConfigKey.SHIFT_MORNING_END])
        assert re.match(time_pattern, DEFAULT_CONFIG[ConfigKey.SHIFT_EVENING_START])
        assert re.match(time_pattern, DEFAULT_CONFIG[ConfigKey.SHIFT_EVENING_END])
    
    @pytest.mark.unit
    def test_timezone_default(self):
        """Verify timezone default is set."""
        assert DEFAULT_CONFIG[ConfigKey.T1_CUTOFF_TIMEZONE] == "Asia/Dubai"


class TestFolderStructure:
    """Tests for FOLDER_STRUCTURE list."""
    
    @pytest.mark.unit
    def test_expected_folders(self):
        """Verify expected folders are defined."""
        expected = [
            "01. Commercial and Demand",
            "02. Tag Sheet Registry",
            "03. Production Planning",
            "04. Production and Delivery",
        ]
        assert FOLDER_STRUCTURE == expected
    
    @pytest.mark.unit
    def test_folder_count(self):
        """Verify expected number of folders."""
        assert len(FOLDER_STRUCTURE) == 4


class TestSheetFolderMap:
    """Tests for SHEET_FOLDER_MAP dictionary."""
    
    @pytest.mark.unit
    def test_all_sheets_have_mapping(self):
        """Verify all sheets have folder mapping."""
        for sheet in SheetName:
            assert sheet in SHEET_FOLDER_MAP, f"Missing folder mapping for: {sheet}"
    
    @pytest.mark.unit
    def test_root_level_sheets(self):
        """Verify root level sheets have None mapping."""
        assert SHEET_FOLDER_MAP[SheetName.REFERENCE_DATA] is None
        assert SHEET_FOLDER_MAP[SheetName.CONFIG] is None
    
    @pytest.mark.unit
    def test_commercial_folder_assignments(self):
        """Verify Commercial folder assignments."""
        assert SHEET_FOLDER_MAP[SheetName.LPO_MASTER] == "01. Commercial and Demand"
        assert SHEET_FOLDER_MAP[SheetName.LPO_AUDIT] == "01. Commercial and Demand"
    
    @pytest.mark.unit
    def test_tag_folder_assignment(self):
        """Verify Tag Registry folder assignment."""
        assert SHEET_FOLDER_MAP[SheetName.TAG_REGISTRY] == "02. Tag Sheet Registry"
    
    @pytest.mark.unit
    def test_production_folder_assignments(self):
        """Verify Production Planning folder assignments."""
        assert SHEET_FOLDER_MAP[SheetName.PRODUCTION_PLANNING] == "03. Production Planning"
        assert SHEET_FOLDER_MAP[SheetName.NESTING_LOG] == "03. Production Planning"
        assert SHEET_FOLDER_MAP[SheetName.ALLOCATION_LOG] == "03. Production Planning"
    
    @pytest.mark.unit
    def test_delivery_folder_assignments(self):
        """Verify Production and Delivery folder assignments."""
        delivery_sheets = [
            SheetName.CONSUMPTION_LOG, SheetName.REMNANT_LOG, SheetName.FILLER_LOG,
            SheetName.DELIVERY_LOG, SheetName.INVOICE_LOG, SheetName.INVENTORY_TXN_LOG,
            SheetName.INVENTORY_SNAPSHOT, SheetName.SAP_INVENTORY_SNAPSHOT,
            SheetName.PHYSICAL_INVENTORY_SNAPSHOT, SheetName.OVERRIDE_LOG,
            SheetName.USER_ACTION_LOG, SheetName.EXCEPTION_LOG,
        ]
        for sheet in delivery_sheets:
            assert SHEET_FOLDER_MAP[sheet] == "04. Production and Delivery", f"Wrong folder for: {sheet}"
    
    @pytest.mark.unit
    def test_all_folders_are_valid(self):
        """Verify all folder mappings use valid folder names."""
        valid_folders = FOLDER_STRUCTURE + [None]
        for sheet, folder in SHEET_FOLDER_MAP.items():
            assert folder in valid_folders, f"Invalid folder '{folder}' for sheet {sheet}"
