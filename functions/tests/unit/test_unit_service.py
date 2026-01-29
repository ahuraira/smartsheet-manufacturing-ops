"""
Unit Tests for UnitService (v1.6.1)

Tests the centralized unit conversion logic, covering:
1. Identity conversions
2. Explicit conversion factors (highest precedence)
3. Standard hardcoded conversions (mm<->m, cm<->m)
4. Edge cases (None inputs, mixed case, whitespace)
"""

import pytest
from shared.unit_service import UnitService

@pytest.mark.unit
class TestUnitService:
    
    def test_convert_identity(self):
        """Test that same units return original quantity."""
        assert UnitService.convert(10.0, "m", "m") == 10.0
        assert UnitService.convert(5.5, "KG", "kg") == 5.5
        assert UnitService.convert(0, "pcs", "PCS") == 0

    def test_convert_explicit_factor(self):
        """Test that explicit factor takes precedence and calculates correctly."""
        # 1 Roll = 30m. Qty 2 Rolls -> 60m
        assert UnitService.convert(2.0, "roll", "m", conversion_factor=30.0) == 60.0
        
        # Factor with decimal
        assert UnitService.convert(10.0, "box", "pcs", conversion_factor=0.5) == 5.0
        
        # Factor overriding standard conversion (unlikely but logic check)
        # Standard mm->m is /1000. If factor is 2, result should be 20.
        assert UnitService.convert(10.0, "mm", "m", conversion_factor=2.0) == 20.0

    def test_convert_standard_mm_to_m(self):
        """Test standard mm to m conversion."""
        assert UnitService.convert(1000.0, "mm", "m") == 1.0
        assert UnitService.convert(500.0, "mm", "m") == 0.5
        assert UnitService.convert(1.0, "mm", "m") == 0.001

    def test_convert_standard_m_to_mm(self):
        """Test standard m to mm conversion."""
        assert UnitService.convert(1.0, "m", "mm") == 1000.0
        assert UnitService.convert(0.5, "m", "mm") == 500.0

    def test_convert_standard_cm_to_m(self):
        """Test standard cm to m conversion."""
        assert UnitService.convert(100.0, "cm", "m") == 1.0
        assert UnitService.convert(50.0, "cm", "m") == 0.5

    def test_convert_case_normalization(self):
        """Test that UOMs are normalized before checking."""
        assert UnitService.convert(1000.0, "MM", "M") == 1.0
        assert UnitService.convert(100.0, "Cm", "M") == 1.0
        assert UnitService.convert(1.0, "M", "Mm") == 1000.0

    def test_convert_whitespace_handling(self):
        """Test that whitespace is trimmed."""
        assert UnitService.convert(1000.0, " mm ", "m") == 1.0
        assert UnitService.convert(1.0, "m", " mm ") == 1000.0

    def test_convert_unknown_units(self):
        """Test that unknown conversions return original quantity."""
        # kg to m without factor is impossible
        assert UnitService.convert(10.0, "kg", "m") == 10.0
        
        # unknown units
        assert UnitService.convert(5.0, "foo", "bar") == 5.0

    def test_convert_none_inputs(self):
        """Test handling of None inputs."""
        assert UnitService.convert(None, "m", "m") == 0.0
        # None UOMs treated as empty strings -> not equal -> unknown path -> return qty
        assert UnitService.convert(10.0, None, "m") == 10.0
        assert UnitService.convert(10.0, "m", None) == 10.0
        assert UnitService.convert(10.0, None, None) == 10.0

    def test_convert_zero_factor(self):
        """
        Test that factor of 0 is ignored (invalid).
        If factor is 0, it should fall back to standard or return original.
        """
        # mm->m with factor 0 should perform standard conversion
        assert UnitService.convert(1000.0, "mm", "m", conversion_factor=0.0) == 1.0
        
        # unknowns with factor 0 return original (can't multiply by 0)
        assert UnitService.convert(10.0, "foo", "bar", conversion_factor=0.0) == 10.0
