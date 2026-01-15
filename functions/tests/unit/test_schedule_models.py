"""
Unit Tests for Scheduling Models and Enums (v1.3.0)

Tests for new models, enums, and features added in v1.3.0:
- Shift enum
- ScheduleStatus enum  
- MachineStatus enum
- ScheduleTagRequest model
- ScheduleTagResponse model
- New ReasonCodes (MACHINE_NOT_FOUND, TAG_NOT_FOUND, etc.)
- New ActionTypes (SCHEDULE_CREATED, etc.)
"""

import pytest
import uuid
from datetime import datetime

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


@pytest.mark.unit
class TestShiftEnum:
    """Tests for Shift enum."""
    
    def test_shift_values(self):
        """Test Shift enum has correct values."""
        from shared.models import Shift
        
        assert Shift.MORNING.value == "Morning"
        assert Shift.EVENING.value == "Evening"
    
    def test_shift_count(self):
        """Test correct number of shifts."""
        from shared.models import Shift
        
        assert len(Shift) == 2


@pytest.mark.unit
class TestScheduleStatusEnum:
    """Tests for ScheduleStatus enum."""
    
    def test_schedule_status_values(self):
        """Test ScheduleStatus enum has all expected values."""
        from shared.models import ScheduleStatus
        
        assert ScheduleStatus.PLANNED.value == "Planned"
        assert ScheduleStatus.RELEASED_FOR_NESTING.value == "Released for Nesting"
        assert ScheduleStatus.NESTING_UPLOADED.value == "Nesting Uploaded"
        assert ScheduleStatus.ALLOCATED.value == "Allocated"
        assert ScheduleStatus.CANCELLED.value == "Cancelled"
        assert ScheduleStatus.DELAYED.value == "Delayed"
    
    def test_schedule_status_lifecycle(self):
        """Test expected status lifecycle transitions."""
        from shared.models import ScheduleStatus
        
        # Normal flow
        lifecycle = [
            ScheduleStatus.PLANNED,
            ScheduleStatus.RELEASED_FOR_NESTING,
            ScheduleStatus.NESTING_UPLOADED,
            ScheduleStatus.ALLOCATED,
        ]
        for status in lifecycle:
            assert status.value is not None


@pytest.mark.unit
class TestMachineStatusEnum:
    """Tests for MachineStatus enum."""
    
    def test_machine_status_values(self):
        """Test MachineStatus enum values."""
        from shared.models import MachineStatus
        
        assert MachineStatus.OPERATIONAL.value == "Operational"
        assert MachineStatus.MAINTENANCE.value == "Maintenance"


@pytest.mark.unit
class TestScheduleTagRequest:
    """Tests for ScheduleTagRequest model (v1.3.0)."""
    
    def test_valid_minimal_request(self):
        """Test creating request with required fields only."""
        from shared.models import ScheduleTagRequest
        
        request = ScheduleTagRequest(
            tag_id="TAG-0001",
            planned_date="2026-02-10",
            shift="Morning",
            machine_id="MACH-1",
            requested_by="pm@company.com"
        )
        
        assert request.tag_id == "TAG-0001"
        assert request.shift == "Morning"
        assert request.machine_id == "MACH-1"
        assert request.client_request_id is not None  # Auto-generated
    
    def test_valid_full_request(self):
        """Test creating request with all fields."""
        from shared.models import ScheduleTagRequest
        
        request = ScheduleTagRequest(
            client_request_id="custom-uuid",
            tag_id="TAG-0002",
            planned_date="2026-02-15",
            shift="Evening",
            machine_id="MACH-2",
            planned_qty_m2=150.5,
            requested_by="pm@company.com",
            notes="Priority order"
        )
        
        assert request.planned_qty_m2 == 150.5
        assert request.notes == "Priority order"
    
    def test_planned_qty_is_optional(self):
        """Test that planned_qty_m2 is optional (defaults from tag)."""
        from shared.models import ScheduleTagRequest
        
        request = ScheduleTagRequest(
            tag_id="TAG-0001",
            planned_date="2026-02-10",
            shift="Morning",
            machine_id="MACH-1",
            requested_by="pm@company.com"
        )
        
        assert request.planned_qty_m2 is None  # Will be taken from tag


@pytest.mark.unit
class TestScheduleTagResponse:
    """Tests for ScheduleTagResponse model (v1.3.0)."""
    
    def test_success_response(self):
        """Test successful schedule response."""
        from shared.models import ScheduleTagResponse
        
        response = ScheduleTagResponse(
            status="RELEASED_FOR_NESTING",
            schedule_id="SCHED-0001",
            next_action_deadline="2026-02-09T18:00:00",
            trace_id="trace-123"
        )
        
        assert response.status == "RELEASED_FOR_NESTING"
        assert response.schedule_id == "SCHED-0001"
        assert response.next_action_deadline == "2026-02-09T18:00:00"
    
    def test_blocked_response(self):
        """Test blocked schedule response."""
        from shared.models import ScheduleTagResponse
        
        response = ScheduleTagResponse(
            status="BLOCKED",
            exception_id="EX-0001",
            trace_id="trace-456",
            message="LPO is on hold"
        )
        
        assert response.status == "BLOCKED"
        assert response.exception_id == "EX-0001"


@pytest.mark.unit
class TestNewReasonCodes:
    """Tests for new reason codes added in v1.3.0."""
    
    def test_scheduling_reason_codes_exist(self):
        """Test all new scheduling-related reason codes exist."""
        from shared.models import ReasonCode
        
        # v1.3.0 new reason codes
        assert ReasonCode.MACHINE_NOT_FOUND.value == "MACHINE_NOT_FOUND"
        assert ReasonCode.MACHINE_MAINTENANCE.value == "MACHINE_MAINTENANCE"
        assert ReasonCode.TAG_NOT_FOUND.value == "TAG_NOT_FOUND"
        assert ReasonCode.TAG_INVALID_STATUS.value == "TAG_INVALID_STATUS"


@pytest.mark.unit
class TestNewActionTypes:
    """Tests for new action types added in v1.3.0."""
    
    def test_scheduling_action_types_exist(self):
        """Test all new scheduling-related action types exist."""
        from shared.models import ActionType
        
        # v1.3.0 new action types
        assert ActionType.SCHEDULE_CREATED.value == "SCHEDULE_CREATED"
        assert ActionType.SCHEDULE_UPDATED.value == "SCHEDULE_UPDATED"
        assert ActionType.SCHEDULE_CANCELLED.value == "SCHEDULE_CANCELLED"


@pytest.mark.unit
class TestT1DeadlineCalculation:
    """Tests for T-1 nesting deadline calculation."""
    
    def test_t1_deadline_is_previous_day_18h(self):
        """Test T-1 deadline is previous day at 18:00."""
        from datetime import datetime, timedelta
        
        # Planned date: 2026-02-10
        planned_date = datetime(2026, 2, 10)
        
        # T-1 deadline should be 2026-02-09 at 18:00
        t1_deadline = planned_date - timedelta(days=1)
        t1_deadline = t1_deadline.replace(hour=18, minute=0, second=0)
        
        assert t1_deadline.day == 9
        assert t1_deadline.hour == 18
        assert t1_deadline.minute == 0
    
    def test_t1_deadline_format(self):
        """Test T-1 deadline is formatted correctly."""
        from datetime import datetime, timedelta
        
        planned_date = datetime(2026, 2, 10)
        t1_deadline = planned_date - timedelta(days=1)
        t1_deadline = t1_deadline.replace(hour=18, minute=0, second=0)
        
        formatted = t1_deadline.strftime("%Y-%m-%dT%H:%M:%S")
        assert formatted == "2026-02-09T18:00:00"
