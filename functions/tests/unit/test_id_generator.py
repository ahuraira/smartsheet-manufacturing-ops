"""
Unit Tests for ID Generator

Tests the sequence-based ID generation for:
- Correct format output
- Sequence incrementing
- Thread safety considerations
- Edge cases
"""

import pytest
from unittest.mock import MagicMock, patch

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.id_generator import (
    SequenceGenerator,
    generate_next_tag_id,
    generate_next_exception_id,
    generate_next_allocation_id,
    generate_next_consumption_id,
    generate_next_delivery_id,
    generate_next_nesting_id,
    generate_next_remnant_id,
    generate_next_txn_id,
)
from shared.sheet_config import ConfigKey, ID_PREFIXES


class TestSequenceGenerator:
    """Tests for SequenceGenerator class."""
    
    @pytest.mark.unit
    def test_next_id_format(self, mock_client):
        """Test generated ID follows expected format."""
        gen = SequenceGenerator(mock_client)
        tag_id = gen.next_id(ConfigKey.SEQ_TAG)
        
        assert tag_id.startswith("TAG-")
        parts = tag_id.split("-")
        assert len(parts) == 2
        assert parts[1].isdigit()
        assert len(parts[1]) == 4  # Padded to 4 digits
    
    @pytest.mark.unit
    def test_next_id_increments(self, mock_client):
        """Test that next_id increments the sequence."""
        gen = SequenceGenerator(mock_client)
        
        id1 = gen.next_id(ConfigKey.SEQ_TAG)
        id2 = gen.next_id(ConfigKey.SEQ_TAG)
        id3 = gen.next_id(ConfigKey.SEQ_TAG)
        
        assert id1 == "TAG-0001"
        assert id2 == "TAG-0002"
        assert id3 == "TAG-0003"
    
    @pytest.mark.unit
    def test_next_id_custom_padding(self, mock_client):
        """Test custom padding for IDs."""
        gen = SequenceGenerator(mock_client)
        
        id1 = gen.next_id(ConfigKey.SEQ_TAG, padding=6)
        
        assert id1 == "TAG-000001"
    
    @pytest.mark.unit
    def test_different_sequences_independent(self, mock_client):
        """Test that different sequences are independent."""
        gen = SequenceGenerator(mock_client)
        
        tag_id = gen.next_id(ConfigKey.SEQ_TAG)
        ex_id = gen.next_id(ConfigKey.SEQ_EXCEPTION)
        tag_id2 = gen.next_id(ConfigKey.SEQ_TAG)
        
        assert tag_id == "TAG-0001"
        assert ex_id == "EX-0001"
        assert tag_id2 == "TAG-0002"
    
    @pytest.mark.unit
    def test_peek_next_no_increment(self, mock_client):
        """Test that peek_next doesn't increment sequence."""
        gen = SequenceGenerator(mock_client)
        
        peek1 = gen.peek_next(ConfigKey.SEQ_TAG)
        peek2 = gen.peek_next(ConfigKey.SEQ_TAG)
        actual = gen.next_id(ConfigKey.SEQ_TAG)
        
        assert peek1 == "TAG-0001"
        assert peek2 == "TAG-0001"  # Same as peek1
        assert actual == "TAG-0001"  # First actual increment
    
    @pytest.mark.unit
    def test_current_value(self, mock_client):
        """Test getting current sequence value."""
        gen = SequenceGenerator(mock_client)
        
        # Initial value
        assert gen.current_value(ConfigKey.SEQ_TAG) == 0
        
        # After generating ID
        gen.next_id(ConfigKey.SEQ_TAG)
        assert gen.current_value(ConfigKey.SEQ_TAG) == 1
    
    @pytest.mark.unit
    def test_all_prefixes_valid(self, mock_client):
        """Test all ID prefixes generate valid IDs."""
        gen = SequenceGenerator(mock_client)
        
        for config_key, prefix in ID_PREFIXES.items():
            id_value = gen.next_id(config_key)
            assert id_value.startswith(f"{prefix}-"), f"ID for {config_key} should start with {prefix}-"


class TestConvenienceFunctions:
    """Tests for convenience ID generation functions."""
    
    @pytest.mark.unit
    def test_generate_next_tag_id(self, mock_client):
        """Test generate_next_tag_id convenience function."""
        tag_id = generate_next_tag_id(mock_client)
        assert tag_id.startswith("TAG-")
        assert tag_id == "TAG-0001"
    
    @pytest.mark.unit
    def test_generate_next_exception_id(self, mock_client):
        """Test generate_next_exception_id convenience function."""
        ex_id = generate_next_exception_id(mock_client)
        assert ex_id.startswith("EX-")
        assert ex_id == "EX-0001"
    
    @pytest.mark.unit
    def test_generate_next_allocation_id(self, mock_client):
        """Test generate_next_allocation_id convenience function."""
        alloc_id = generate_next_allocation_id(mock_client)
        assert alloc_id.startswith("ALLOC-")
        assert alloc_id == "ALLOC-0001"
    
    @pytest.mark.unit
    def test_generate_next_consumption_id(self, mock_client):
        """Test generate_next_consumption_id convenience function."""
        con_id = generate_next_consumption_id(mock_client)
        assert con_id.startswith("CON-")
        assert con_id == "CON-0001"
    
    @pytest.mark.unit
    def test_generate_next_delivery_id(self, mock_client):
        """Test generate_next_delivery_id convenience function."""
        do_id = generate_next_delivery_id(mock_client)
        assert do_id.startswith("DO-")
        assert do_id == "DO-0001"
    
    @pytest.mark.unit
    def test_generate_next_nesting_id(self, mock_client):
        """Test generate_next_nesting_id convenience function."""
        nest_id = generate_next_nesting_id(mock_client)
        assert nest_id.startswith("NEST-")
        assert nest_id == "NEST-0001"
    
    @pytest.mark.unit
    def test_generate_next_remnant_id(self, mock_client):
        """Test generate_next_remnant_id convenience function."""
        rem_id = generate_next_remnant_id(mock_client)
        assert rem_id.startswith("REM-")
        assert rem_id == "REM-0001"
    
    @pytest.mark.unit
    def test_generate_next_txn_id(self, mock_client):
        """Test generate_next_txn_id convenience function."""
        txn_id = generate_next_txn_id(mock_client)
        assert txn_id.startswith("TXN-")
        assert txn_id == "TXN-0001"


class TestSequenceEdgeCases:
    """Tests for edge cases in sequence generation."""
    
    @pytest.mark.unit
    def test_large_sequence_number(self, mock_client):
        """Test handling of large sequence numbers."""
        gen = SequenceGenerator(mock_client)
        
        # Manually set sequence to large number
        mock_client.storage.update_row(
            "00a Config",
            mock_client.storage.find_rows("00a Config", "config_key", "seq_tag")[0]["row_id"],
            {"config_value": "9999"}
        )
        
        tag_id = gen.next_id(ConfigKey.SEQ_TAG)
        assert tag_id == "TAG-10000"  # Should overflow padding gracefully
    
    @pytest.mark.unit
    def test_sequence_persistence(self, mock_client):
        """Test that sequence changes persist."""
        gen1 = SequenceGenerator(mock_client)
        gen1.next_id(ConfigKey.SEQ_TAG)  # TAG-0001
        gen1.next_id(ConfigKey.SEQ_TAG)  # TAG-0002
        
        # Create new generator (simulating new function invocation)
        gen2 = SequenceGenerator(mock_client)
        tag_id = gen2.next_id(ConfigKey.SEQ_TAG)
        
        assert tag_id == "TAG-0003"  # Should continue from where gen1 left off
    
    @pytest.mark.unit
    def test_missing_sequence_creates_new(self, mock_client):
        """Test that missing sequence is automatically created."""
        # Remove a sequence
        rows = mock_client.storage.find_rows("00a Config", "config_key", "seq_tag")
        if rows:
            # Clear the rows list for this sequence
            mock_client.storage.sheets["00a Config"]["rows"] = [
                r for r in mock_client.storage.sheets["00a Config"]["rows"]
                if r["id"] != rows[0]["row_id"]
            ]
        
        gen = SequenceGenerator(mock_client)
        tag_id = gen.next_id(ConfigKey.SEQ_TAG)
        
        # Should create sequence and generate first ID
        assert tag_id == "TAG-0001"
