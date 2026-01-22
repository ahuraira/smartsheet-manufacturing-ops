import pytest
import uuid
import json
import base64
from unittest.mock import MagicMock, patch

from fn_parse_nesting import main
from fn_parse_nesting.validation import (
    validate_tag_exists,
    validate_tag_lpo_ownership,
    check_duplicate_file,
    check_duplicate_request_id,
    ValidationResult
)
from fn_parse_nesting.models import AttachmentInfo
from shared.logical_names import Sheet, Column

# Mock NestingExecutionRecord for parser simulation
from fn_parse_nesting.models import (
    NestingExecutionRecord,
    MetaData,
    RawMaterialPanel,
    InventoryImpact,
    EfficiencyMetrics
)

@pytest.fixture
def mock_nesting_record():
    return NestingExecutionRecord(
        meta_data=MetaData(
            project_ref_id="TAG-001",
            source_file_name="test.xlsx"
        ),
        raw_material_panel=RawMaterialPanel(
            material_spec_name="GI 0.9",
            thickness_mm=0.9,
            inventory_impact=InventoryImpact(utilied_sheets_count=5, gross_area_m2=15.0),
            efficiency_metrics=EfficiencyMetrics(waste_pct=10.0)
        )
    )

class TestValidation:
    
    def test_validate_tag_exists_found(self, patched_client):
        # Setup
        tag_id = "TAG-001"
        patched_client.add_row(
            Sheet.TAG_REGISTRY, 
            {
                Column.TAG_REGISTRY.TAG_ID: tag_id,
                Column.TAG_REGISTRY.LPO_SAP_REFERENCE: "PTE-185"
            }
        )
        
        # Act
        result = validate_tag_exists(patched_client, tag_id)
        
        # Assert
        assert result.is_valid is True
        assert result.tag_lpo_ref == "PTE-185"

    def test_validate_tag_exists_not_found(self, patched_client):
        # Act
        result = validate_tag_exists(patched_client, "TAG-999")
        
        # Assert
        assert result.is_valid is False
        assert result.error_code == "TAG_NOT_FOUND"

    def test_validate_lpo_ownership_success(self, patched_client):
        # Setup
        val_result = ValidationResult(is_valid=True, tag_row_id=1, tag_lpo_ref="PTE-185")
        
        # Act
        result = validate_tag_lpo_ownership(val_result, "PTE-185")
        
        # Assert
        assert result.is_valid is True

    def test_validate_lpo_ownership_mismatch(self, patched_client):
        # Setup
        val_result = ValidationResult(is_valid=True, tag_row_id=1, tag_lpo_ref="PTE-185")
        
        # Act
        result = validate_tag_lpo_ownership(val_result, "PTE-200")
        
        # Assert
        assert result.is_valid is False
        assert result.error_code == "LPO_MISMATCH"

    def test_check_duplicate_file(self, patched_client):
        # Setup duplicate in Nesting Log
        patched_client.add_row(
            Sheet.NESTING_LOG,
            {
                Column.NESTING_LOG.FILE_HASH: "hash123",
                Column.NESTING_LOG.NEST_SESSION_ID: "NEST-001"
            }
        )
        
        # Act
        session_id = check_duplicate_file(patched_client, "hash123", "PTE-185")
        
        # Assert
        assert session_id == "NEST-001"
        
    def test_check_duplicate_request_id(self, patched_client):
         # Setup
        patched_client.add_row(
            Sheet.NESTING_LOG,
            {
                Column.NESTING_LOG.CLIENT_REQUEST_ID: "req-123",
                Column.NESTING_LOG.NEST_SESSION_ID: "NEST-001"
            }
        )
        
        # Act
        session_id = check_duplicate_request_id(patched_client, "req-123")
        
        # Assert
        assert session_id == "NEST-001"

@pytest.mark.integration
class TestFnParseNestingIntegration:
    
    @patch('fn_parse_nesting.NestingFileParser')
    @patch('fn_parse_nesting.create_exception')
    def test_main_success_flow(
        self, 
        mock_create_exception, 
        mock_parser_cls,
        patched_client
    ):
        # Setup Mocks
        mock_parser = mock_parser_cls.return_value
        mock_parser.parse.return_value.status = "SUCCESS"
        mock_parser.parse.return_value.data = NestingExecutionRecord(
            meta_data=MetaData(project_ref_id="TAG-001", source_file_name="f.xlsx"),
            raw_material_panel=RawMaterialPanel(
                material_spec_name="GI", thickness_mm=1, 
                inventory_impact={'utilized_sheets_count': 1, 'gross_area_m2': 10}
            )
        )
        mock_parser.parse.return_value.processing_time_ms = 100
        
        # Setup Smartsheet Data
        patched_client.add_row( # Use patched_client here too
            Sheet.TAG_REGISTRY, 
            {Column.TAG_REGISTRY.TAG_ID: "TAG-001", Column.TAG_REGISTRY.LPO_SAP_REFERENCE: "PTE-185"}
        )
        
        # Setup Request using json
        req = MagicMock()
        req.headers = {}
        # Important: Ensure req.files evaluates to False or is empty
        req.files = {} 
        req.get_json.return_value = {
            "client_request_id": "req-new-001",
            "file_content_base64": base64.b64encode(b"dummy_content").decode(), # Valid base64
            "filename": "nesting.xlsx",
            "sap_lpo_reference": "PTE-185",
            "uploaded_by": "user@test.com"
        }
        
        # Act
        resp = main(req)
        
        # Assert
        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body['status'] == "SUCCESS"
        assert body['tag_id'] == "TAG-001"
