
import pytest
from unittest.mock import MagicMock, ANY
from fn_parse_nesting.nesting_logger import NestingLogger
from shared.logical_names import Sheet, Column
from fn_parse_nesting.models import (
    NestingExecutionRecord, MetaData, RawMaterialPanel, 
    InventoryImpact, EfficiencyMetrics
)

@pytest.fixture
def mock_client():
    client = MagicMock()
    return client

@pytest.fixture
def logger_instance(mock_client):
    return NestingLogger(mock_client)

@pytest.fixture
def mock_record():
    return NestingExecutionRecord(
        meta_data=MetaData(
            project_ref_id="TAG-001",
            source_file_name="test.xlsx"
        ),
        raw_material_panel=RawMaterialPanel(
            material_spec_name="GI 0.9",
            thickness_mm=0.9,
            inventory_impact=InventoryImpact(utilized_sheets_count=5, gross_area_m2=15.0),
            efficiency_metrics=EfficiencyMetrics(waste_pct=10.0)
        )
    )

class TestNestingLogger:

    def test_log_execution_success(self, logger_instance, mock_client, mock_record):
        # Setup
        mock_client.add_row.return_value = {"id": 12345}
        
        # Act
        row_id = logger_instance.log_execution(
            record=mock_record,
            nest_session_id="NEST-001",
            tag_id="TAG-001",
            file_hash="hash123",
            client_request_id="req-001",
            sap_lpo_reference="LPO-A"
        )
        
        # Assert
        assert row_id == 12345
        mock_client.add_row.assert_called_once_with(
            Sheet.NESTING_LOG,
            {
                Column.NESTING_LOG.NEST_SESSION_ID: "NEST-001",
                Column.NESTING_LOG.TAG_SHEET_ID: "TAG-001",
                Column.NESTING_LOG.TIMESTAMP: ANY,
                Column.NESTING_LOG.BRAND: "",
                Column.NESTING_LOG.SHEETS_CONSUMED_VIRTUAL: 5,
                Column.NESTING_LOG.EXPECTED_CONSUMPTION_M2: 15.0,
                Column.NESTING_LOG.WASTAGE_PERCENTAGE: 10.0,
                Column.NESTING_LOG.FILE_HASH: "hash123",
                Column.NESTING_LOG.CLIENT_REQUEST_ID: "req-001",
            }
        )

    def test_log_execution_error(self, logger_instance, mock_client, mock_record):
        # Setup error
        mock_client.add_row.side_effect = Exception("DB Error")
        
        # Act & Assert
        with pytest.raises(Exception) as excinfo:
            logger_instance.log_execution(
                record=mock_record,
                nest_session_id="NEST-001",
                tag_id="TAG-001",
                file_hash="hash123",
                client_request_id="req-001"
            )
        assert "DB Error" in str(excinfo.value)

    def test_attach_file_success(self, logger_instance, mock_client):
        # Act
        result = logger_instance.attach_file(
            sheet_ref="TAG_REGISTRY",
            row_id=100,
            file_url="http://files.com/1.xlsx",
            filename="1.xlsx",
            description="Test File"
        )
        
        # Assert
        assert result.target == "TAG_REGISTRY"
        mock_client.attach_url_to_row.assert_called_once_with(
            sheet_ref="TAG_REGISTRY",
            row_id=100,
            url="http://files.com/1.xlsx",
            name="1.xlsx",
            description="Test File"
        )

    def test_attach_file_error(self, logger_instance, mock_client):
        # Setup error
        mock_client.attach_url_to_row.side_effect = Exception("Attach Failed")
        
        # Act
        result = logger_instance.attach_file(
            sheet_ref="TAG_REGISTRY",
            row_id=100,
            file_url="http://url",
            filename="f",
            description="d"
        )
        
        # Assert (swallows error)
        assert result is None

    def test_update_tag_status_success(self, logger_instance, mock_client):
        # Act
        logger_instance.update_tag_status(
            tag_row_id=200,
            sheets_used=5,
            wastage=12.5
        )
        
        # Assert
        # Need to check Column keys, they might not be in mock manifest if logic expects real ones?
        # But wait, Column keys are strings from logical_names.
        # Oh, in Column.TAG_REGISTRY, keys are like "STATUS".
        # Let's check update_row call.
        
        mock_client.update_row.assert_called_once()
        args, _ = mock_client.update_row.call_args
        assert args[0] == Sheet.TAG_REGISTRY
        assert args[1] == 200
        # args[2] is update dict
        updates = args[2]
        assert updates[Column.TAG_REGISTRY.STATUS] == "Nesting Complete"
        assert updates[Column.TAG_REGISTRY.SHEETS_USED] == 5
        assert updates[Column.TAG_REGISTRY.WASTAGE_NESTED] == 12.5

    def test_update_tag_status_error(self, logger_instance, mock_client):
        # Setup error
        mock_client.update_row.side_effect = Exception("Update Failed")
        
        # Act (should not raise)
        logger_instance.update_tag_status(200, 5, 10.0)
