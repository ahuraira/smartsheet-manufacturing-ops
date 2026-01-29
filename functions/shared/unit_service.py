"""
Unit Service
===========

Provides centralized unit conversion logic for the application.
Aligns with SOTA specification for authoritative conversions.

Usage:
    converted = UnitService.convert(10, 'roll', 'm', 30.0)
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

class UnitService:
    """
    Service for handling unit conversions.
    """
    
    @staticmethod
    def convert(
        quantity: float, 
        from_uom: str, 
        to_uom: str, 
        conversion_factor: Optional[float] = None
    ) -> float:
        """
        Convert quantity from one UOM to another.
        
        Args:
            quantity: The numerical amount to convert
            from_uom: Source unit of measure (e.g. 'roll', 'sheet')
            to_uom: Target unit of measure (e.g. 'm', 'm2')
            conversion_factor: Explicit factor to multiply by (if available)
            
        Returns:
            Converted quantity. Returns original quantity if units match
            or conversion impossible.
        """
        if quantity is None:
            return 0.0
            
        # Normalize UOMs
        src = str(from_uom).lower().strip() if from_uom else ""
        dst = str(to_uom).lower().strip() if to_uom else ""
        
        # 1. Identity Check
        if src == dst:
            return quantity
        
        # 2. explicit conversion factor (highest precedence)
        if conversion_factor is not None and conversion_factor != 0:
            # Assumption: Factor is always a multiplier to reach the target UOM
            # e.g. 1 Roll = 30m. Factor = 30. Qty(2) * 30 = 60m.
            return quantity * conversion_factor
            
        # 3. Standard conversions (Hardcoded fallback for safety)
        # mm -> m
        if src == 'mm' and dst == 'm':
            return quantity / 1000.0
        if src == 'm' and dst == 'mm':
            return quantity * 1000.0
            
        # cm -> m
        if src == 'cm' and dst == 'm':
            return quantity / 100.0
        
        # If no path found, return original (and log warning)
        logger.warning(f"No conversion path found for {src} -> {dst} (qty: {quantity})")
        return quantity
