"""
Unit Tests for Helper Utilities

Tests all helper functions for:
- Trace ID generation
- File hash computation (bytes, URL, and base64)
- SLA calculation
- Datetime formatting
- Safe parsing utilities

Updated for v1.1.0 with base64 file content support.
"""

import pytest
import hashlib
import base64
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.helpers import (
    generate_trace_id,
    compute_file_hash,
    compute_file_hash_from_url,
    compute_file_hash_from_base64,
    calculate_sla_due,
    format_datetime_for_smartsheet,
    parse_float_safe,
    safe_get,
)
from shared.models import ExceptionSeverity


class TestGenerateTraceId:
    """Tests for trace ID generation."""
    
    @pytest.mark.unit
    def test_trace_id_format(self):
        """Test trace ID follows expected format."""
        trace_id = generate_trace_id()
        assert trace_id.startswith("trace-")
        assert len(trace_id) == 18  # "trace-" (6) + 12 hex chars
    
    @pytest.mark.unit
    def test_trace_id_uniqueness(self):
        """Test that trace IDs are unique."""
        ids = [generate_trace_id() for _ in range(100)]
        assert len(set(ids)) == 100, "All trace IDs should be unique"
    
    @pytest.mark.unit
    def test_trace_id_hex_suffix(self):
        """Test trace ID suffix is valid hex."""
        trace_id = generate_trace_id()
        suffix = trace_id.replace("trace-", "")
        int(suffix, 16)  # Should not raise


class TestComputeFileHash:
    """Tests for file hash computation."""
    
    @pytest.mark.unit
    def test_sha256_hash(self):
        """Test SHA256 hash is computed correctly."""
        content = b"test file content"
        expected = hashlib.sha256(content).hexdigest()
        actual = compute_file_hash(content)
        assert actual == expected
    
    @pytest.mark.unit
    def test_empty_file_hash(self):
        """Test hash of empty content."""
        content = b""
        expected = hashlib.sha256(content).hexdigest()
        actual = compute_file_hash(content)
        assert actual == expected
    
    @pytest.mark.unit
    def test_same_content_same_hash(self):
        """Test same content produces same hash."""
        content = b"identical content here"
        hash1 = compute_file_hash(content)
        hash2 = compute_file_hash(content)
        assert hash1 == hash2
    
    @pytest.mark.unit
    def test_different_content_different_hash(self):
        """Test different content produces different hash."""
        hash1 = compute_file_hash(b"content A")
        hash2 = compute_file_hash(b"content B")
        assert hash1 != hash2
    
    @pytest.mark.unit
    def test_hash_length(self):
        """Test SHA256 hash is 64 hex characters."""
        hash_value = compute_file_hash(b"any content")
        assert len(hash_value) == 64


class TestComputeFileHashFromUrl:
    """Tests for computing file hash from URL."""
    
    @pytest.mark.unit
    def test_successful_download_and_hash(self):
        """Test successful file download and hash computation."""
        with patch('shared.helpers.requests.get') as mock_get:
            mock_response = MagicMock()
            mock_response.content = b"test content"
            mock_response.raise_for_status = MagicMock()
            mock_get.return_value = mock_response
            
            result = compute_file_hash_from_url("https://example.com/file.xlsx")
            
            assert result is not None
            assert result == hashlib.sha256(b"test content").hexdigest()
    
    @pytest.mark.unit
    def test_download_failure_returns_none(self):
        """Test that download failure returns None."""
        with patch('shared.helpers.requests.get') as mock_get:
            mock_get.side_effect = Exception("Connection error")
            
            result = compute_file_hash_from_url("https://example.com/file.xlsx")
            
            assert result is None
    
    @pytest.mark.unit
    def test_auth_headers_passed(self):
        """Test that auth headers are passed to request."""
        with patch('shared.helpers.requests.get') as mock_get:
            mock_response = MagicMock()
            mock_response.content = b"content"
            mock_response.raise_for_status = MagicMock()
            mock_get.return_value = mock_response
            
            auth_headers = {"Authorization": "Bearer token123"}
            compute_file_hash_from_url("https://example.com/file.xlsx", auth_headers)
            
            mock_get.assert_called_once()
            call_kwargs = mock_get.call_args[1]
            assert call_kwargs["headers"] == auth_headers


class TestComputeFileHashFromBase64:
    """Tests for computing file hash from base64 content (v1.1.0 feature)."""
    
    @pytest.mark.unit
    def test_valid_base64_hash(self):
        """Test successful hash computation from base64."""
        content = b"test file content for base64"
        b64_content = base64.b64encode(content).decode()
        
        result = compute_file_hash_from_base64(b64_content)
        expected = hashlib.sha256(content).hexdigest()
        
        assert result == expected
    
    @pytest.mark.unit
    def test_empty_base64(self):
        """Test hash of empty base64 content."""
        content = b""
        b64_content = base64.b64encode(content).decode()
        
        result = compute_file_hash_from_base64(b64_content)
        expected = hashlib.sha256(content).hexdigest()
        
        assert result == expected
    
    @pytest.mark.unit
    def test_invalid_base64_returns_none(self):
        """Test that invalid base64 returns None."""
        result = compute_file_hash_from_base64("not-valid-base64!!!")
        assert result is None
    
    @pytest.mark.unit
    def test_same_content_same_hash(self):
        """Test that same content produces same hash regardless of encoding method."""
        content = b"identical content"
        b64_content = base64.b64encode(content).decode()
        
        # Hash from base64
        hash_b64 = compute_file_hash_from_base64(b64_content)
        # Hash from bytes directly
        hash_bytes = compute_file_hash(content)
        
        assert hash_b64 == hash_bytes
    
    @pytest.mark.unit
    def test_pdf_like_content(self):
        """Test with PDF-like binary content."""
        # Simulate PDF header
        content = b"%PDF-1.4 binary content here with special bytes \x00\x01\x02"
        b64_content = base64.b64encode(content).decode()
        
        result = compute_file_hash_from_base64(b64_content)
        
        assert result is not None
        assert len(result) == 64  # SHA256 hex length


class TestCalculateSlaDue:
    """Tests for SLA due date calculation."""
    
    @pytest.mark.unit
    def test_critical_severity_4_hours(self):
        """Test CRITICAL severity gives 4 hour SLA."""
        base = datetime(2026, 1, 7, 10, 0, 0)
        due = calculate_sla_due(ExceptionSeverity.CRITICAL, base)
        expected = base + timedelta(hours=4)
        assert due == expected
    
    @pytest.mark.unit
    def test_high_severity_24_hours(self):
        """Test HIGH severity gives 24 hour SLA."""
        base = datetime(2026, 1, 7, 10, 0, 0)
        due = calculate_sla_due(ExceptionSeverity.HIGH, base)
        expected = base + timedelta(hours=24)
        assert due == expected
    
    @pytest.mark.unit
    def test_medium_severity_48_hours(self):
        """Test MEDIUM severity gives 48 hour SLA."""
        base = datetime(2026, 1, 7, 10, 0, 0)
        due = calculate_sla_due(ExceptionSeverity.MEDIUM, base)
        expected = base + timedelta(hours=48)
        assert due == expected
    
    @pytest.mark.unit
    def test_low_severity_72_hours(self):
        """Test LOW severity gives 72 hour SLA."""
        base = datetime(2026, 1, 7, 10, 0, 0)
        due = calculate_sla_due(ExceptionSeverity.LOW, base)
        expected = base + timedelta(hours=72)
        assert due == expected
    
    @pytest.mark.unit
    def test_default_created_at_now(self):
        """Test that created_at defaults to now."""
        before = datetime.now()
        due = calculate_sla_due(ExceptionSeverity.CRITICAL)
        after = datetime.now()
        
        # Due should be ~4 hours from now
        expected_min = before + timedelta(hours=4)
        expected_max = after + timedelta(hours=4)
        assert expected_min <= due <= expected_max


class TestFormatDatetimeForSmartsheet:
    """Tests for Smartsheet datetime formatting."""
    
    @pytest.mark.unit
    def test_format_output(self):
        """Test datetime is formatted correctly."""
        dt = datetime(2026, 1, 7, 14, 30, 45)
        formatted = format_datetime_for_smartsheet(dt)
        assert formatted == "2026-01-07T14:30:45"
    
    @pytest.mark.unit
    def test_midnight(self):
        """Test formatting of midnight."""
        dt = datetime(2026, 1, 1, 0, 0, 0)
        formatted = format_datetime_for_smartsheet(dt)
        assert formatted == "2026-01-01T00:00:00"
    
    @pytest.mark.unit
    def test_end_of_day(self):
        """Test formatting of end of day."""
        dt = datetime(2026, 12, 31, 23, 59, 59)
        formatted = format_datetime_for_smartsheet(dt)
        assert formatted == "2026-12-31T23:59:59"


class TestParseFloatSafe:
    """Tests for safe float parsing."""
    
    @pytest.mark.unit
    def test_parse_float(self):
        """Test parsing float value."""
        assert parse_float_safe(42.5) == 42.5
    
    @pytest.mark.unit
    def test_parse_int(self):
        """Test parsing integer value."""
        assert parse_float_safe(42) == 42.0
    
    @pytest.mark.unit
    def test_parse_string_float(self):
        """Test parsing string float."""
        assert parse_float_safe("123.45") == 123.45
    
    @pytest.mark.unit
    def test_parse_string_int(self):
        """Test parsing string integer."""
        assert parse_float_safe("123") == 123.0
    
    @pytest.mark.unit
    def test_parse_none_returns_default(self):
        """Test None returns default value."""
        assert parse_float_safe(None) == 0.0
        assert parse_float_safe(None, default=99.9) == 99.9
    
    @pytest.mark.unit
    def test_parse_invalid_string_returns_default(self):
        """Test invalid string returns default."""
        assert parse_float_safe("not a number") == 0.0
        assert parse_float_safe("abc", default=-1.0) == -1.0
    
    @pytest.mark.unit
    def test_parse_empty_string_returns_default(self):
        """Test empty string returns default."""
        assert parse_float_safe("") == 0.0
    
    @pytest.mark.unit
    def test_parse_negative(self):
        """Test parsing negative values."""
        assert parse_float_safe(-42.5) == -42.5
        assert parse_float_safe("-42.5") == -42.5


class TestSafeGet:
    """Tests for safe nested dictionary access."""
    
    @pytest.mark.unit
    def test_single_level(self):
        """Test getting single level key."""
        d = {"key": "value"}
        assert safe_get(d, "key") == "value"
    
    @pytest.mark.unit
    def test_nested_access(self):
        """Test getting nested keys."""
        d = {"level1": {"level2": {"level3": "deep value"}}}
        assert safe_get(d, "level1", "level2", "level3") == "deep value"
    
    @pytest.mark.unit
    def test_missing_key_returns_default(self):
        """Test missing key returns default."""
        d = {"key": "value"}
        assert safe_get(d, "missing") is None
        assert safe_get(d, "missing", default="fallback") == "fallback"
    
    @pytest.mark.unit
    def test_none_value_returns_default(self):
        """Test None value returns default."""
        d = {"key": None}
        assert safe_get(d, "key") is None
        assert safe_get(d, "key", default="fallback") == "fallback"
    
    @pytest.mark.unit
    def test_nested_missing_returns_default(self):
        """Test nested missing key returns default."""
        d = {"level1": {"level2": "value"}}
        assert safe_get(d, "level1", "missing") is None
        assert safe_get(d, "level1", "level2", "level3", default="fallback") == "fallback"
    
    @pytest.mark.unit
    def test_non_dict_intermediate_returns_default(self):
        """Test non-dict intermediate returns default."""
        d = {"key": "string value"}
        assert safe_get(d, "key", "nested") is None
