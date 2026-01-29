"""
BOM Generator
=============

Flattens parsed nesting data into BOM (Bill of Materials) lines.
Each BOM line represents a material consumption that can be mapped
to canonical codes and SAP codes for inventory allocation.

Usage:
    from fn_parse_nesting.bom_generator import BOMGenerator, BOMLine
    
    generator = BOMGenerator()
    bom_lines = generator.generate(parsed_record)
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional
from uuid import uuid4

from .models import NestingExecutionRecord

logger = logging.getLogger(__name__)


@dataclass
class BOMLine:
    """
    Single line item in the Bill of Materials.
    
    Represents a material consumption extracted from a nesting file.
    This is the input to the mapping service.
    """
    
    # Identifiers
    line_number: int = 0
    line_id: str = field(default_factory=lambda: str(uuid4())[:8])
    
    # Material info (from parser)
    material_type: str = ""  # PANEL, PROFILE, CONSUMABLE, ACCESSORY
    nesting_description: str = ""  # Raw description from nesting file
    
    # Quantity
    quantity: float = 0.0
    uom: str = ""  # m, m2, kg, pcs
    
    # Mapping results (filled after mapping)
    canonical_code: Optional[str] = None
    sap_code: Optional[str] = None
    mapping_decision: Optional[str] = None  # AUTO, OVERRIDE, MANUAL, REVIEW
    history_id: Optional[str] = None
    
    # Conversion (filled after mapping)
    canonical_quantity: Optional[float] = None
    canonical_uom: Optional[str] = None
    
    # Metadata
    trace_id: Optional[str] = None


class BOMGenerator:
    """
    Generates BOM lines from a parsed NestingExecutionRecord.
    
    Extracts all materials from:
    1. Raw material panel (main board)
    2. Profiles and flanges
    3. Flange accessories (GI corners, PVC corners)
    4. Consumables (silicone, tape, glue)
    5. Machine wear (blades - optional, not tracked)
    """
    
    # Material type constants
    TYPE_PANEL = "PANEL"
    TYPE_PROFILE = "PROFILE"
    TYPE_ACCESSORY = "ACCESSORY"
    TYPE_CONSUMABLE = "CONSUMABLE"
    TYPE_MACHINE = "MACHINE"
    
    def __init__(self, include_machine_wear: bool = False):
        """
        Initialize BOM generator.
        
        Args:
            include_machine_wear: If True, include blade wear in BOM (not tracked in inventory)
        """
        self.include_machine_wear = include_machine_wear
    
    def generate(
        self,
        record: NestingExecutionRecord,
        trace_id: Optional[str] = None
    ) -> List[BOMLine]:
        """
        Generate BOM lines from a parsed nesting record.
        
        Args:
            record: Parsed NestingExecutionRecord from parser
            trace_id: Trace ID for distributed tracing
            
        Returns:
            List of BOMLine objects ready for mapping
        """
        trace_id = trace_id or str(uuid4())
        lines: List[BOMLine] = []
        line_number = 0
        
        logger.debug(f"[{trace_id}] Generating BOM from nesting record")
        
        # 1. Panel material
        if record.raw_material_panel:
            line_number += 1
            panel_line = self._extract_panel(record, line_number, trace_id)
            if panel_line:
                lines.append(panel_line)
        
        # 2. Profiles
        for profile in record.profiles_and_flanges:
            if profile.total_consumption_m > 0:
                line_number += 1
                lines.append(self._build_line(
                    line_number=line_number,
                    material_type=self.TYPE_PROFILE,
                    nesting_description=profile.profile_type,
                    quantity=profile.total_consumption_m,
                    uom="m",
                    trace_id=trace_id
                ))
        
        # 3. Flange accessories
        accessories = self._extract_accessories(record, line_number, trace_id)
        for acc in accessories:
            line_number += 1
            acc.line_number = line_number
            lines.append(acc)
        
        # 4. Consumables
        consumables = self._extract_consumables(record, line_number, trace_id)
        for cons in consumables:
            line_number += 1
            cons.line_number = line_number
            lines.append(cons)
        
        # 5. Machine wear (optional)
        if self.include_machine_wear:
            machine_wear = self._extract_machine_wear(record, line_number, trace_id)
            for wear in machine_wear:
                line_number += 1
                wear.line_number = line_number
                lines.append(wear)
        
        logger.info(
            f"[{trace_id}] Generated {len(lines)} BOM lines from nesting record"
        )
        
        return lines
    
    def _extract_panel(
        self,
        record: NestingExecutionRecord,
        line_number: int,
        trace_id: str
    ) -> Optional[BOMLine]:
        """Extract panel material from record."""
        panel = record.raw_material_panel
        if not panel or not panel.material_spec_name:
            return None
        
        # Use gross area as quantity (consumed area)
        quantity = panel.inventory_impact.gross_area_m2
        if quantity <= 0:
            return None
        
        return self._build_line(
            line_number=line_number,
            material_type=self.TYPE_PANEL,
            nesting_description=panel.material_spec_name,
            quantity=quantity,
            uom="m2",
            trace_id=trace_id
        )
    
    def _extract_accessories(
        self,
        record: NestingExecutionRecord,
        start_line: int,
        trace_id: str
    ) -> List[BOMLine]:
        """Extract flange accessories (corners, clips, etc.)."""
        lines = []
        accessories = record.flange_accessories
        
        if accessories.gi_corners_qty > 0:
            lines.append(self._build_line(
                line_number=0,  # Will be set later
                material_type=self.TYPE_ACCESSORY,
                nesting_description="gi corners",
                quantity=accessories.gi_corners_qty,
                uom="pcs",
                trace_id=trace_id
            ))
        
        if accessories.pvc_corners_qty > 0:
            lines.append(self._build_line(
                line_number=0,
                material_type=self.TYPE_ACCESSORY,
                nesting_description="pvc corners",
                quantity=accessories.pvc_corners_qty,
                uom="pcs",
                trace_id=trace_id
            ))
        
        return lines
    
    def _extract_consumables(
        self,
        record: NestingExecutionRecord,
        start_line: int,
        trace_id: str
    ) -> List[BOMLine]:
        """Extract consumables (silicone, tape, glue)."""
        lines = []
        cons = record.consumables
        
        if cons.silicone_consumption_kg > 0:
            lines.append(self._build_line(
                line_number=0,
                material_type=self.TYPE_CONSUMABLE,
                nesting_description="silicone",
                quantity=cons.silicone_consumption_kg,
                uom="kg",
                trace_id=trace_id
            ))
        
        if cons.aluminum_tape_consumption_m > 0:
            lines.append(self._build_line(
                line_number=0,
                material_type=self.TYPE_CONSUMABLE,
                nesting_description="aluminum tape",
                quantity=cons.aluminum_tape_consumption_m,
                uom="m",
                trace_id=trace_id
            ))
        
        if cons.glue_junction_kg > 0:
            lines.append(self._build_line(
                line_number=0,
                material_type=self.TYPE_CONSUMABLE,
                nesting_description="glue junction",
                quantity=cons.glue_junction_kg,
                uom="kg",
                trace_id=trace_id
            ))
        
        if cons.glue_flange_kg > 0:
            lines.append(self._build_line(
                line_number=0,
                material_type=self.TYPE_CONSUMABLE,
                nesting_description="glue flange",
                quantity=cons.glue_flange_kg,
                uom="kg",
                trace_id=trace_id
            ))
        
        return lines
    
    def _extract_machine_wear(
        self,
        record: NestingExecutionRecord,
        start_line: int,
        trace_id: str
    ) -> List[BOMLine]:
        """Extract machine wear (blades) - not inventory tracked."""
        lines = []
        telemetry = record.machine_telemetry
        
        if telemetry.blade_wear_45_m > 0:
            lines.append(self._build_line(
                line_number=0,
                material_type=self.TYPE_MACHINE,
                nesting_description="blade 45",
                quantity=telemetry.blade_wear_45_m,
                uom="m",
                trace_id=trace_id
            ))
        
        if telemetry.blade_wear_90_m > 0:
            lines.append(self._build_line(
                line_number=0,
                material_type=self.TYPE_MACHINE,
                nesting_description="blade 90",
                quantity=telemetry.blade_wear_90_m,
                uom="m",
                trace_id=trace_id
            ))
        
        if telemetry.blade_wear_2x45_m > 0:
            lines.append(self._build_line(
                line_number=0,
                material_type=self.TYPE_MACHINE,
                nesting_description="blade 2x45",
                quantity=telemetry.blade_wear_2x45_m,
                uom="m",
                trace_id=trace_id
            ))
        
        return lines
    
    def _build_line(
        self,
        line_number: int,
        material_type: str,
        nesting_description: str,
        quantity: float,
        uom: str,
        trace_id: str
    ) -> BOMLine:
        """Build a BOM line with consistent formatting."""
        return BOMLine(
            line_number=line_number,
            line_id=str(uuid4())[:8],
            material_type=material_type,
            nesting_description=nesting_description.lower().strip(),
            quantity=round(quantity, 4),
            uom=uom,
            trace_id=trace_id
        )
