import pytest
import sys
import os
from unittest.mock import MagicMock, patch

# Adjust path to find modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from fn_parse_nesting.validation import validate_tag_exists, ValidationResult
from shared.logical_names import Sheet, Column

@pytest.fixture
def mock_client():
    return MagicMock()

@pytest.fixture
def mock_manifest():
    return MagicMock()

def test_validate_tag_exists_duplicate_handling(mock_client, mock_manifest):
    """
    Test that finding multiple rows for a single Tag ID triggers a proper error.
    This validates the SOTA fix for preventing updates on ambiguous records.
    """
    # Arrange
    tag_id = "TAG-DUPLICATE"
    
    # Mock find_rows returning two records
    mock_client.find_rows.return_value = [
        {"id": 101, "cells": []},
        {"id": 102, "cells": []}
    ]
    
    # Act
    result = validate_tag_exists(mock_client, tag_id)
    
    # Assert
    assert result.is_valid is False
    assert result.error_code == "TAG_DUPLICATE"
    assert "Multiple records found" in result.error_message
