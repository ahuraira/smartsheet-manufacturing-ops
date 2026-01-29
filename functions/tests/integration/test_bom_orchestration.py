"""
Integration Tests for BOM Orchestration (v1.6.0)

Tests the full workflow: Parsing -> Generation -> Mapping -> Persistence.
Verifies the integration points between components and resilience to partial failures.
"""

import pytest
from unittest.mock import MagicMock, patch
from fn_parse_nesting.bom_orchestrator import BOMOrchestrator, BOMProcessingResult
from fn_parse_nesting.models import NestingExecutionRecord, RawMaterialPanel, MetaData, InventoryImpact
from fn_parse_nesting.bom_generator import BOMLine
from fn_map_lookup.mapping_service import MappingResult

@pytest.fixture
def mock_record():
    return NestingExecutionRecord(
        meta_data=MetaData(project_ref_id="TAG-001", source_file_name="test.xlsx"),
        raw_material_panel=RawMaterialPanel(
            material_spec_name="GI 0.9mm",
            thickness_mm=0.9,
            inventory_impact=InventoryImpact(utilized_sheets_count=1, gross_area_m2=3.0)
        )
    )

@pytest.mark.integration
class TestBOMOrchestration:
    
    @patch('fn_parse_nesting.bom_orchestrator.BOMGenerator')
    @patch('fn_map_lookup.mapping_service.MappingService') 
    def test_full_flow_success(self, MockMappingService, MockBOMGenerator, mock_record):
        """
        Verify the chain: Generator -> Mapping Service -> Sheet Write.
        """
        # 1. Setup Generator Mock
        mock_generator = MockBOMGenerator.return_value
        mock_line = BOMLine(line_id="L1", nesting_description="gi 0.9", quantity=3.0, uom="m2")
        mock_generator.generate.return_value = [mock_line]
        
        # 2. Setup Mapping Service Mock
        mock_map_service = MockMappingService.return_value
        mock_map_service.lookup.return_value = MappingResult(
            success=True, 
            decision="AUTO", 
            canonical_code="CAN-GI-09", 
            sap_code="SAP-GI-09",
            uom="m2",
            conversion_factor=1.0,
            history_id="HIST-1"
        )
        
        # 3. Setup Client Mock
        mock_client = MagicMock()
        
        # 4. Execute
        orchestrator = BOMOrchestrator(mock_client)
        # We need to ensure _get_mapping_service uses our mock.
        # Since we patched the class, internal instantiation should use the mock.
        
        result = orchestrator.process(mock_record, "NEST-1", lpo_id="LPO-1")
        
        # 5. Assertions
        assert result.success is True
        assert result.total_lines == 1
        assert result.mapped_lines == 1
        assert result.exception_lines == 0
        
        # Verify Generator Called
        mock_generator.generate.assert_called_once()
        
        # Verify Mapping Called
        mock_map_service.lookup.assert_called_once()
        call_args = mock_map_service.lookup.call_args[1]
        assert call_args["nesting_description"] == "gi 0.9"
        assert call_args["lpo_id"] == "LPO-1"
        
        # Verify Sheet Write
        mock_client.add_rows_bulk.assert_called_once()
        
    @patch('fn_parse_nesting.bom_orchestrator.BOMGenerator')
    @patch('fn_map_lookup.mapping_service.MappingService')
    def test_mapping_with_unit_conversion(self, MockMappingService, MockBOMGenerator, mock_record):
        """
        Verify that UnitService logic is applied when mapping returns UOM/Factor.
        Scenario: Nesting has 'roll', Mapping has 'm' and factor 30.
        Expectation: Canonical Quantity = 2 * 30 = 60.
        """
        mock_gen = MockBOMGenerator.return_value
        mock_gen.generate.return_value = [
            BOMLine(line_id="L1", nesting_description="tape", quantity=2.0, uom="roll")
        ]
        
        mock_map = MockMappingService.return_value
        mock_map.lookup.return_value = MappingResult(
            success=True, decision="AUTO", 
            uom="m", conversion_factor=30.0
        )
        
        orchestrator = BOMOrchestrator(MagicMock())
        result = orchestrator.process(mock_record, "NEST-1")
        
        line = result.bom_lines[0]
        assert line.canonical_quantity == 60.0
        assert line.canonical_uom == "m"

    @patch('fn_parse_nesting.bom_orchestrator.BOMGenerator')
    @patch('fn_map_lookup.mapping_service.MappingService') 
    def test_partial_failure_handling(self, MockMappingService, MockBOMGenerator, mock_record):
        """
        Verify that if 1 line maps and 1 fails, the process succeeds overall 
        but counts exceptions correctly.
        """
        mock_gen = MockBOMGenerator.return_value
        mock_gen.generate.return_value = [
            BOMLine(nesting_description="good"),
            BOMLine(nesting_description="bad")
        ]
        
        mock_map = MockMappingService.return_value
        # Side effect to return different results for calls
        mock_map.lookup.side_effect = [
            MappingResult(success=True, decision="AUTO"),  # Good
            MappingResult(success=False, decision="REVIEW") # Bad
        ]
        
        orchestrator = BOMOrchestrator(MagicMock())
        result = orchestrator.process(mock_record, "NEST-1")
        
        assert result.success is True # Overall success (didn't crash)
        assert result.mapped_lines == 1
        assert result.exception_lines == 1
        assert result.total_lines == 2

    @patch('fn_parse_nesting.bom_orchestrator.BOMGenerator')
    def test_write_failure_resilience(self, MockBOMGenerator, mock_record):
        """
        Verify strict resilience: if Writing fails, we catch it but might mark whole ops as error 
        OR log it. The code currently logs error and adds to result.errors.
        """
        mock_gen = MockBOMGenerator.return_value
        mock_gen.generate.return_value = [BOMLine(nesting_description="item")]
        
        mock_client = MagicMock()
        mock_client.add_rows_bulk.side_effect = Exception("API Error")
        
        # Mock mapping to pass
        with patch('fn_map_lookup.mapping_service.MappingService') as MockMap:
            MockMap.return_value.lookup.return_value = MappingResult(success=True, decision="AUTO")
            
            orchestrator = BOMOrchestrator(mock_client)
            result = orchestrator.process(mock_record, "NEST-1")
            
            # The exception in `process` catches ALL exceptions, so success=False here?
            # Let's check code: `logger.exception... result.errors.append...`
            # `result.success` is set at the END of try block.
            # So if exception happens in write_to_sheet, it jumps to except, so success remains False.
            
            assert result.success is False
            assert "API Error" in result.errors[0]
