"""
Unit Tests for LPO Models and Helpers (v1.2.0)

Tests for new models, enums, and helper functions added in v1.2.0:
- FileType enum
- FileAttachment model
- LPOIngestRequest model
- LPOUpdateRequest model
- compute_combined_file_hash
- generate_lpo_folder_path
- sanitize_folder_name
"""

import pytest
import uuid
import base64
import hashlib
from datetime import datetime

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


@pytest.mark.unit
class TestFileTypeEnum:
    """Tests for FileType enum."""
    
    def test_file_type_values(self):
        """Test all FileType values exist."""
        from shared.models import FileType
        
        assert FileType.LPO.value == "lpo"
        assert FileType.COSTING.value == "costing"
        assert FileType.AMENDMENT.value == "amendment"
        assert FileType.OTHER.value == "other"


@pytest.mark.unit
class TestFileAttachment:
    """Tests for FileAttachment model."""
    
    def test_valid_file_attachment_with_url(self):
        """Test creating FileAttachment with URL."""
        from shared.models import FileAttachment, FileType
        
        attachment = FileAttachment(
            file_type=FileType.LPO,
            file_url="https://sharepoint.com/file.pdf",
            file_name="po_document.pdf"
        )
        assert attachment.file_type == FileType.LPO
        assert attachment.file_url == "https://sharepoint.com/file.pdf"
    
    def test_valid_file_attachment_with_content(self):
        """Test creating FileAttachment with base64 content."""
        from shared.models import FileAttachment, FileType
        
        content = base64.b64encode(b"file content").decode()
        attachment = FileAttachment(
            file_type=FileType.COSTING,
            file_content=content,
            file_name="costing.xlsx"
        )
        assert attachment.file_content == content
    
    def test_file_attachment_requires_source(self):
        """Test that either file_url or file_content is required."""
        from shared.models import FileAttachment, FileType
        from pydantic import ValidationError
        
        with pytest.raises(ValidationError):
            FileAttachment(
                file_type=FileType.OTHER,
                file_name="test.txt"
                # Missing both file_url and file_content
            )


@pytest.mark.unit
class TestLPOIngestRequest:
    """Tests for LPOIngestRequest model (v1.2.0)."""
    
    def test_valid_minimal_request(self):
        """Test creating request with minimal required fields."""
        from shared.models import LPOIngestRequest
        
        request = LPOIngestRequest(
            sap_reference="PTE-185",
            customer_name="Acme Corp",
            project_name="Project X",
            brand="KIMMCO",
            po_quantity_sqm=1000.0,
            price_per_sqm=150.0,
            uploaded_by="user@company.com"
        )
        
        assert request.sap_reference == "PTE-185"
        assert request.brand == "KIMMCO"
        assert request.po_quantity_sqm == 1000.0
        assert request.client_request_id is not None  # Auto-generated
    
    def test_valid_full_request(self):
        """Test creating request with all fields."""
        from shared.models import LPOIngestRequest, FileAttachment, FileType
        
        request = LPOIngestRequest(
            client_request_id="custom-uuid",
            sap_reference="PTE-200",
            customer_name="Test Customer",
            project_name="Test Project",
            brand="WTI",
            po_quantity_sqm=500.0,
            price_per_sqm=200.0,
            customer_lpo_ref="CUST-001",
            terms_of_payment="60 Days Credit",
            wastage_pct=5.0,
            hold_reason=None,
            remarks="Test remarks",
            file_url="https://test.com/file.pdf",
            uploaded_by="admin@company.com"
        )
        
        assert request.terms_of_payment == "60 Days Credit"
        assert request.wastage_pct == 5.0
    
    def test_po_quantity_must_be_positive(self):
        """Test that po_quantity_sqm must be positive."""
        from shared.models import LPOIngestRequest
        from pydantic import ValidationError
        
        with pytest.raises(ValidationError):
            LPOIngestRequest(
                sap_reference="PTE-185",
                customer_name="Test",
                project_name="Test",
                brand="KIMMCO",
                po_quantity_sqm=-100.0,  # Invalid!
                price_per_sqm=150.0,
                uploaded_by="user@company.com"
            )
    
    def test_wastage_pct_bounds(self):
        """Test wastage_pct must be between 0 and 20."""
        from shared.models import LPOIngestRequest
        from pydantic import ValidationError
        
        # Valid at 20%
        request = LPOIngestRequest(
            sap_reference="PTE-185",
            customer_name="Test",
            project_name="Test",
            brand="KIMMCO",
            po_quantity_sqm=100.0,
            price_per_sqm=150.0,
            wastage_pct=20.0,  # Max allowed
            uploaded_by="user@company.com"
        )
        assert request.wastage_pct == 20.0
        
        # Invalid at 25%
        with pytest.raises(ValidationError):
            LPOIngestRequest(
                sap_reference="PTE-185",
                customer_name="Test",
                project_name="Test",
                brand="KIMMCO",
                po_quantity_sqm=100.0,
                price_per_sqm=150.0,
                wastage_pct=25.0,  # Exceeds max!
                uploaded_by="user@company.com"
            )
    
    def test_get_all_files_with_multi_files(self):
        """Test get_all_files combines files list and legacy fields."""
        from shared.models import LPOIngestRequest, FileAttachment, FileType
        
        request = LPOIngestRequest(
            sap_reference="PTE-185",
            customer_name="Test",
            project_name="Test",
            brand="KIMMCO",
            po_quantity_sqm=100.0,
            price_per_sqm=150.0,
            uploaded_by="user@company.com",
            files=[
                FileAttachment(
                    file_type=FileType.COSTING,
                    file_url="https://test.com/costing.xlsx"
                )
            ],
            file_url="https://test.com/po.pdf"  # Legacy single file
        )
        
        all_files = request.get_all_files()
        assert len(all_files) == 2
        assert all_files[0].file_type == FileType.LPO  # Legacy converted
        assert all_files[1].file_type == FileType.COSTING


@pytest.mark.unit
class TestLPOUpdateRequest:
    """Tests for LPOUpdateRequest model (v1.2.0)."""
    
    def test_valid_update_request(self):
        """Test creating update request."""
        from shared.models import LPOUpdateRequest
        
        request = LPOUpdateRequest(
            sap_reference="PTE-185",
            po_quantity_sqm=1200.0,
            updated_by="admin@company.com"
        )
        
        assert request.sap_reference == "PTE-185"
        assert request.po_quantity_sqm == 1200.0
    
    def test_partial_update_only_some_fields(self):
        """Test that only provided fields are set."""
        from shared.models import LPOUpdateRequest
        
        request = LPOUpdateRequest(
            sap_reference="PTE-185",
            lpo_status="Active",
            updated_by="admin@company.com"
        )
        
        assert request.lpo_status == "Active"
        assert request.po_quantity_sqm is None
        assert request.price_per_sqm is None


@pytest.mark.unit
class TestComputeCombinedFileHash:
    """Tests for compute_combined_file_hash helper (v1.2.0)."""
    
    def test_single_file_hash(self):
        """Test hash computation for single file."""
        from shared.helpers import compute_combined_file_hash
        from shared.models import FileAttachment, FileType
        
        content = base64.b64encode(b"test content").decode()
        files = [
            FileAttachment(
                file_type=FileType.LPO,
                file_content=content
            )
        ]
        
        result = compute_combined_file_hash(files)
        assert result is not None
        assert len(result) == 64  # SHA256 hex
    
    def test_multiple_files_deterministic(self):
        """Test that hash is deterministic regardless of input order."""
        from shared.helpers import compute_combined_file_hash
        from shared.models import FileAttachment, FileType
        
        content1 = base64.b64encode(b"file1 content").decode()
        content2 = base64.b64encode(b"file2 content").decode()
        
        # Order 1
        files1 = [
            FileAttachment(file_type=FileType.LPO, file_content=content1),
            FileAttachment(file_type=FileType.COSTING, file_content=content2),
        ]
        
        # Order 2 (reversed)
        files2 = [
            FileAttachment(file_type=FileType.COSTING, file_content=content2),
            FileAttachment(file_type=FileType.LPO, file_content=content1),
        ]
        
        # Should produce same hash due to sorting by file_type
        hash1 = compute_combined_file_hash(files1)
        hash2 = compute_combined_file_hash(files2)
        
        assert hash1 == hash2
    
    def test_empty_files_returns_none(self):
        """Test that empty file list returns None."""
        from shared.helpers import compute_combined_file_hash
        
        result = compute_combined_file_hash([])
        assert result is None
    
    def test_none_returns_none(self):
        """Test that None input returns None."""
        from shared.helpers import compute_combined_file_hash
        
        result = compute_combined_file_hash(None)
        assert result is None


@pytest.mark.unit
class TestSanitizeFolderName:
    """Tests for sanitize_folder_name helper (v1.2.0)."""
    
    def test_basic_sanitization(self):
        """Test basic character replacement."""
        from shared.helpers import sanitize_folder_name
        
        result = sanitize_folder_name("Test/Company")
        assert "/" not in result
        assert result == "Test_Company"
    
    def test_special_characters_replaced(self):
        """Test all special characters are replaced."""
        from shared.helpers import sanitize_folder_name
        
        # All invalid SharePoint chars
        input_name = 'Test\\:*?"<>|#%Name'
        result = sanitize_folder_name(input_name)
        
        invalid_chars = ['/', '\\', ':', '*', '?', '"', '<', '>', '|', '#', '%']
        for char in invalid_chars:
            assert char not in result
    
    def test_empty_returns_unknown(self):
        """Test empty string returns 'Unknown'."""
        from shared.helpers import sanitize_folder_name
        
        assert sanitize_folder_name("") == "Unknown"
        assert sanitize_folder_name(None) == "Unknown"
    
    def test_truncation_at_50_chars(self):
        """Test long names are truncated."""
        from shared.helpers import sanitize_folder_name
        
        long_name = "A" * 100
        result = sanitize_folder_name(long_name)
        assert len(result) <= 50


@pytest.mark.unit
class TestGenerateLpoFolderPath:
    """Tests for generate_lpo_folder_path helper (v1.2.0)."""
    
    def test_basic_folder_path(self):
        """Test basic folder path generation."""
        from shared.helpers import generate_lpo_folder_path
        
        result = generate_lpo_folder_path(
            sap_reference="PTE-185",
            customer_name="Acme Corp"
        )
        
        # Returns relative path, not full URL
        assert result.startswith("LPOs/PTE-185_")
        assert "PTE-185" in result
        assert "Acme" in result
    
    def test_special_chars_in_customer_name(self):
        """Test customer names with special chars are sanitized."""
        from shared.helpers import generate_lpo_folder_path
        
        result = generate_lpo_folder_path(
            sap_reference="PTE-185",
            customer_name="Test/Company:Ltd"
        )
        
        # Extract just the folder name portion after LPOs/
        folder_name = result.split("LPOs/")[1]
        assert "/" not in folder_name
        assert ":" not in folder_name


@pytest.mark.unit
class TestAuditModuleFunctions:
    """Tests for shared/audit.py functions (v1.2.0)."""
    
    def test_create_exception_returns_id(self, mock_storage):
        """Test create_exception returns exception ID."""
        from shared.audit import create_exception
        from shared.models import ReasonCode, ExceptionSeverity
        from tests.conftest import MockSmartsheetClient
        
        client = MockSmartsheetClient(mock_storage)
        
        exception_id = create_exception(
            client=client,
            trace_id="trace-test",
            reason_code=ReasonCode.DUPLICATE_UPLOAD,
            severity=ExceptionSeverity.MEDIUM,
            message="Test exception"
        )
        
        assert exception_id.startswith("EX-")
    
    def test_log_user_action_success(self, mock_storage):
        """Test log_user_action creates record."""
        from shared.audit import log_user_action
        from shared.models import ActionType
        from tests.conftest import MockSmartsheetClient
        
        client = MockSmartsheetClient(mock_storage)
        
        # Should not raise
        log_user_action(
            client=client,
            user_id="test@company.com",
            action_type=ActionType.LPO_CREATED,
            target_table="LPO_MASTER",
            target_id="PTE-185",
            trace_id="trace-test"
        )
        
        # Verify action was logged
        actions = mock_storage.find_rows("98 User Action Log", "Action Type", "LPO_CREATED")
        assert len(actions) >= 1


@pytest.mark.unit
class TestNewReasonCodes:
    """Tests for new reason codes added in v1.2.0."""
    
    def test_lpo_reason_codes_exist(self):
        """Test all new LPO-related reason codes exist."""
        from shared.models import ReasonCode
        
        # v1.2.0 new reason codes
        assert ReasonCode.DUPLICATE_SAP_REF.value == "DUPLICATE_SAP_REF"
        assert ReasonCode.SAP_REF_NOT_FOUND.value == "SAP_REF_NOT_FOUND"
        assert ReasonCode.LPO_INVALID_DATA.value == "LPO_INVALID_DATA"
        assert ReasonCode.PO_QUANTITY_CONFLICT.value == "PO_QUANTITY_CONFLICT"
        assert ReasonCode.DUPLICATE_LPO_FILE.value == "DUPLICATE_LPO_FILE"


@pytest.mark.unit
class TestNewActionTypes:
    """Tests for new action types added in v1.2.0."""
    
    def test_lpo_action_types_exist(self):
        """Test all new LPO-related action types exist."""
        from shared.models import ActionType
        
        # v1.2.0 new action types
        assert ActionType.LPO_CREATED.value == "LPO_CREATED"
        assert ActionType.LPO_UPDATED.value == "LPO_UPDATED"
