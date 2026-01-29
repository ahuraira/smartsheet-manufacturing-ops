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
        
        
    def test_validate_tag_exists_duplicate(self, patched_client):
        """Test that duplicate tags return a specific error code."""
        # Setup duplicate tag in mock storage
        tag_id = "TAG-DUP"
        # Row 1
        patched_client.add_row(
            Sheet.TAG_REGISTRY,
            {
                Column.TAG_REGISTRY.TAG_ID: tag_id,
                Column.TAG_REGISTRY.LPO_SAP_REFERENCE: "PTE-185"
            }
        )
        # Row 2 (Duplicate)
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
        assert result.is_valid is False
        assert result.error_code == "TAG_DUPLICATE"
        assert "Multiple records found" in result.error_message

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

    def test_main_missing_file(self):
        """Test returning 400 when file content is missing."""
        req = MagicMock()
        req.headers = {}
        # Ensure payload extraction fails to find content
        req.get_json.return_value = {}
        req.files = {}
        req.get_body.return_value = b""
        
        resp = main(req)
        
        assert resp.status_code == 400
        assert json.loads(resp.get_body())["error_code"] == "MISSING_FILE"

    def test_main_duplicate_request_id(self, patched_client):
        """Test returning 200 idempotent when request ID exists."""
        # Setup existing log
        patched_client.add_row(
            Sheet.NESTING_LOG,
            {
                Column.NESTING_LOG.CLIENT_REQUEST_ID: "req-dup",
                Column.NESTING_LOG.NEST_SESSION_ID: "NEST-OLD"
            }
        )
        
        req = MagicMock()
        req.headers = {}
        req.get_json.return_value = {
            "client_request_id": "req-dup",
            "file_content_base64": base64.b64encode(b"content").decode(),
            "filename": "f.xlsx"
        }
        
        resp = main(req)
        
        assert resp.status_code == 200
        body = json.loads(resp.get_body())
        assert body["status"] == "SUCCESS"
        assert body["nest_session_id"] == "NEST-OLD"
        assert "Idempotent" in body["message"]

    def test_main_duplicate_file_hash(self, patched_client):
        """Test returning 409 when file hash exists for LPO."""
        # Pre-calc hash for "content"
        content = b"content"
        f_hash = "ed7002b439e9ac845f22357d822bac1444730fbdb6016d3ec9432297b9ec9f73"
        
        patched_client.add_row(
            Sheet.NESTING_LOG,
            {
                Column.NESTING_LOG.FILE_HASH: f_hash,
                Column.NESTING_LOG.NEST_SESSION_ID: "NEST-OLD"
            }
        )
        
        req = MagicMock()
        req.headers = {}
        req.get_json.return_value = {
            "client_request_id": "req-new",
            "file_content_base64": base64.b64encode(content).decode(),
            "sap_lpo_reference": "LPO-1",
            "filename": "f.xlsx"
        }
        
        resp = main(req)
        
        assert resp.status_code == 409
        assert json.loads(resp.get_body())["error_code"] == "DUPLICATE_NESTING_FILE"

    @patch('fn_parse_nesting.NestingFileParser')
    @patch('fn_parse_nesting.create_exception')
    def test_main_parse_error(self, mock_create, mock_parser_cls, patched_client):
        """Test returning 422 when parser fails."""
        mock_parser = mock_parser_cls.return_value
        mock_parser.parse.return_value.status = "ERROR"
        mock_parser.parse.return_value.errors = ["Critical syntax error"]
        mock_parser.parse.return_value.data = None
        mock_create.return_value = "EX-001"
        
        req = MagicMock()
        req.headers = {}
        req.get_json.return_value = {
            "file_content_base64": base64.b64encode(b"bad_content").decode(),
            "filename": "bad.xlsx"
        }
        
        resp = main(req)
        
        assert resp.status_code == 422
        body = json.loads(resp.get_body())
        assert body["error_code"] == "PARSE_FAILED_CRITICAL"
        assert body["exception_id"] == "EX-001"
        mock_create.assert_called_once()

    @patch('fn_parse_nesting.NestingFileParser')
    @patch('fn_parse_nesting.create_exception')
    def test_main_tag_not_found(self, mock_create, mock_parser_cls, patched_client):
        """Test returning 422 when tag validation fails."""
        # Parser success
        mock_parser = mock_parser_cls.return_value
        mock_parser.parse.return_value.status = "SUCCESS"
        mock_parser.parse.return_value.data = NestingExecutionRecord(
            meta_data=MetaData(project_ref_id="TAG-999", source_file_name="f.xlsx"),
            raw_material_panel=RawMaterialPanel(
                material_spec_name="GI", thickness_mm=1, 
                inventory_impact={'utilized_sheets_count': 1, 'gross_area_m2': 10}
            )
        )
        mock_create.return_value = "EX-002"
        
        # Tag 999 NOT in registry
        
        req = MagicMock()
        req.headers = {}
        req.get_json.return_value = {
            "file_content_base64": base64.b64encode(b"content").decode(),
        }
        
        resp = main(req)
        
        assert resp.status_code == 422
        body = json.loads(resp.get_body())
        assert body["error_code"] == "TAG_NOT_FOUND"
        mock_create.assert_called_once()

    @patch('fn_parse_nesting.NestingFileParser')
    @patch('fn_parse_nesting.create_exception', return_value="EX-LPO-001")
    def test_main_lpo_mismatch(self, mock_create, mock_parser_cls, patched_client):
        """Test returning 422 when LPO ownership mismatch."""
        # Parser success
        mock_parser = mock_parser_cls.return_value
        mock_parser.parse.return_value.status = "SUCCESS"
        mock_parser.parse.return_value.data = NestingExecutionRecord(
            meta_data=MetaData(project_ref_id="TAG-001", source_file_name="f.xlsx"),
            raw_material_panel=RawMaterialPanel(
                material_spec_name="GI", thickness_mm=1, 
                inventory_impact={'utilized_sheets_count': 1, 'gross_area_m2': 10}
            )
        )
        
        # Tag exists but for different LPO
        patched_client.add_row(
            Sheet.TAG_REGISTRY,
            {Column.TAG_REGISTRY.TAG_ID: "TAG-001", Column.TAG_REGISTRY.LPO_SAP_REFERENCE: "LPO-REAL"}
        )
        
        req = MagicMock()
        req.headers = {}
        req.get_json.return_value = {
            "file_content_base64": base64.b64encode(b"content").decode(),
            "sap_lpo_reference": "LPO-WRONG" # Mismatch
        }
        
        resp = main(req)
        
        assert resp.status_code == 422
        assert json.loads(resp.get_body())["error_code"] == "LPO_MISMATCH"

    @patch('fn_parse_nesting.validation.check_duplicate_request_id', side_effect=Exception("Simulated Crash"))
    def test_main_general_exception(self, mock_check):
        """Test 500 on unexpected error."""
        req = MagicMock()
        req.headers = {}
        req.get_json.return_value = {
            "client_request_id": "req-crash",
            "file_content_base64": base64.b64encode(b"C").decode()
        }
        
        resp = main(req)
        
        assert resp.status_code == 500
        assert json.loads(resp.get_body())["error_code"] == "INTERNAL_ERROR"
