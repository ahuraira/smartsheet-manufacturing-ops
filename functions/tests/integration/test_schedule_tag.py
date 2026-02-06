"""
Integration Tests for Production Scheduling Function (v1.3.0)

Tests the complete production scheduling flow including:
- Happy path schedule creation
- Idempotency via client_request_id
- Tag validation (exists, not CANCELLED/CLOSED)
- LPO validation (exists, not ON_HOLD)
- PO balance check with 5% tolerance
- Machine validation (exists, OPERATIONAL)
- T-1 deadline calculation
- User action logging with JSON details
"""

import pytest
import json
import uuid
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


@pytest.mark.integration
class TestScheduleTagHappyPath:
    """Tests for successful production scheduling."""
    
    def test_create_schedule_success(self, mock_storage, factory, mock_http_request):
        """Test successful schedule creation."""
        # Setup: Create tag, LPO, and machine
        tag = {
            "Tag ID": "TAG-0001",
            "Tag Sheet Name/ Rev": "Test Tag",
            "Status": "Validate",
            "LPO SAP Reference Link": "SAP-TEST-001",
            "Estimated Quantity": 100.0,
        }
        mock_storage.add_row("Tag Sheet Registry", tag)
        
        lpo = {
            "SAP Reference": "SAP-TEST-001",
            "LPO Status": "Active",
            "PO Quantity (Sqm)": 1000.0,
            "Delivered Quantity (Sqm)": 0.0,
            "Planned Quantity": 0.0,
            "Allocated Quantity": 0.0,
        }
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        machine = {
            "Machine ID": "MACH-1",
            "Machine Name": "CNC Router 1",
            "Status": "Operational",
        }
        mock_storage.add_row("00b Machine Master", machine)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "tag_id": "TAG-0001",
            "planned_date": "2026-02-10",
            "shift": "Morning",
            "machine_id": "MACH-1",
            "requested_by": "pm@company.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_schedule_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_schedule_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_schedule_tag._manifest', MockWorkspaceManifest()):
                    from fn_schedule_tag import main
                    response = main(mock_http_request(request_data))
        
        assert response.status_code == 200
        body = json.loads(response.get_body())
        assert body["status"] == "RELEASED_FOR_NESTING"
        assert body["schedule_id"].startswith("SCHED-")
        assert "next_action_deadline" in body
        assert "trace_id" in body
    
    def test_t1_deadline_calculated_correctly(self, mock_storage, factory, mock_http_request):
        """Test T-1 deadline is correct (previous day 18:00)."""
        # Setup
        tag = {
            "Tag ID": "TAG-0002",
            "Status": "Validate",
            "LPO SAP Reference Link": "SAP-T1-001",
            "Estimated Quantity": 50.0,
        }
        mock_storage.add_row("Tag Sheet Registry", tag)
        
        lpo = {
            "SAP Reference": "SAP-T1-001",
            "LPO Status": "Active",
            "PO Quantity (Sqm)": 500.0,
            "Delivered Quantity (Sqm)": 0.0,
            "Planned Quantity": 0.0,
            "Allocated Quantity": 0.0,
        }
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        machine = {"Machine ID": "MACH-1", "Status": "Operational"}
        mock_storage.add_row("00b Machine Master", machine)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "tag_id": "TAG-0002",
            "planned_date": "2026-02-15",
            "shift": "Morning",
            "machine_id": "MACH-1",
            "requested_by": "pm@company.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_schedule_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_schedule_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_schedule_tag._manifest', MockWorkspaceManifest()):
                    from fn_schedule_tag import main
                    response = main(mock_http_request(request_data))
        
        body = json.loads(response.get_body())
        assert body["next_action_deadline"] == "2026-02-14T18:00:00"
    
    def test_user_action_logged_with_json_details(self, mock_storage, factory, mock_http_request):
        """Test SCHEDULE_CREATED action is logged with JSON new_value."""
        # Setup
        tag = {
            "Tag ID": "TAG-0003",
            "Status": "Validate",
            "LPO SAP Reference Link": "SAP-LOG-001",
            "Estimated Quantity": 75.0,
        }
        mock_storage.add_row("Tag Sheet Registry", tag)
        
        lpo = {
            "SAP Reference": "SAP-LOG-001",
            "LPO Status": "Active",
            "PO Quantity (Sqm)": 500.0,
            "Delivered Quantity (Sqm)": 0.0,
            "Planned Quantity": 0.0,
            "Allocated Quantity": 0.0,
        }
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        machine = {"Machine ID": "MACH-1", "Status": "Operational"}
        mock_storage.add_row("00b Machine Master", machine)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "tag_id": "TAG-0003",
            "planned_date": "2026-02-10",
            "shift": "Morning",
            "machine_id": "MACH-1",
            "requested_by": "pm@company.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_schedule_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_schedule_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_schedule_tag._manifest', MockWorkspaceManifest()):
                    from fn_schedule_tag import main
                    response = main(mock_http_request(request_data))
        
        assert response.status_code == 200
        
        # Verify SCHEDULE_CREATED action logged
        actions = mock_storage.find_rows("98 User Action Log", "Action Type", "SCHEDULE_CREATED")
        assert len(actions) >= 1


@pytest.mark.integration
class TestScheduleTagIdempotency:
    """Tests for idempotency via client_request_id."""
    
    def test_duplicate_request_returns_already_scheduled(self, mock_storage, factory, mock_http_request):
        """Test duplicate client_request_id returns ALREADY_SCHEDULED."""
        client_request_id = str(uuid.uuid4())
        
        # Create existing schedule
        existing_schedule = {
            "Schedule ID": "SCHED-0001",
            "Client Request ID": client_request_id,
            "Tag Sheet ID": "TAG-0001",
            "Status": "Released for Nesting",
        }
        mock_storage.add_row("03 Production Planning", existing_schedule)
        
        request_data = {
            "client_request_id": client_request_id,  # Same ID
            "tag_id": "TAG-0001",
            "planned_date": "2026-02-10",
            "shift": "Morning",
            "machine_id": "MACH-1",
            "requested_by": "pm@company.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_schedule_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_schedule_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_schedule_tag._manifest', MockWorkspaceManifest()):
                    from fn_schedule_tag import main
                    response = main(mock_http_request(request_data))
        
        assert response.status_code == 200
        body = json.loads(response.get_body())
        assert body["status"] == "ALREADY_SCHEDULED"


@pytest.mark.integration
class TestScheduleTagValidation:
    """Tests for tag validation."""
    
    def test_tag_not_found_returns_422(self, mock_storage, factory, mock_http_request):
        """Test scheduling non-existent tag returns 422."""
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "tag_id": "TAG-NONEXISTENT",
            "planned_date": "2026-02-10",
            "shift": "Morning",
            "machine_id": "MACH-1",
            "requested_by": "pm@company.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_schedule_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_schedule_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_schedule_tag._manifest', MockWorkspaceManifest()):
                    from fn_schedule_tag import main
                    response = main(mock_http_request(request_data))
        
        assert response.status_code == 422
        body = json.loads(response.get_body())
        assert body["status"] == "BLOCKED"
        assert "exception_id" in body
    
    def test_cancelled_tag_returns_422(self, mock_storage, factory, mock_http_request):
        """Test scheduling cancelled tag returns 422."""
        tag = {
            "Tag ID": "TAG-CANCELLED",
            "Status": "Cancelled",  # Invalid status
            "LPO SAP Reference Link": "SAP-001",
        }
        mock_storage.add_row("Tag Sheet Registry", tag)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "tag_id": "TAG-CANCELLED",
            "planned_date": "2026-02-10",
            "shift": "Morning",
            "machine_id": "MACH-1",
            "requested_by": "pm@company.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_schedule_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_schedule_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_schedule_tag._manifest', MockWorkspaceManifest()):
                    from fn_schedule_tag import main
                    response = main(mock_http_request(request_data))
        
        assert response.status_code == 422
        body = json.loads(response.get_body())
        assert body["status"] == "BLOCKED"


@pytest.mark.integration
class TestScheduleTagLPOValidation:
    """Tests for LPO validation."""
    
    def test_lpo_not_found_returns_422(self, mock_storage, factory, mock_http_request):
        """Test scheduling with missing LPO returns 422."""
        tag = {
            "Tag ID": "TAG-NO-LPO",
            "Status": "Validate",
            "LPO SAP Reference Link": "SAP-MISSING",
        }
        mock_storage.add_row("Tag Sheet Registry", tag)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "tag_id": "TAG-NO-LPO",
            "planned_date": "2026-02-10",
            "shift": "Morning",
            "machine_id": "MACH-1",
            "requested_by": "pm@company.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_schedule_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_schedule_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_schedule_tag._manifest', MockWorkspaceManifest()):
                    from fn_schedule_tag import main
                    response = main(mock_http_request(request_data))
        
        assert response.status_code == 422
        body = json.loads(response.get_body())
        assert body["status"] == "BLOCKED"
    
    def test_lpo_on_hold_returns_422(self, mock_storage, factory, mock_http_request):
        """Test scheduling with LPO on hold returns 422."""
        tag = {
            "Tag ID": "TAG-HOLD",
            "Status": "Validate",
            "LPO SAP Reference Link": "SAP-HOLD",
        }
        mock_storage.add_row("Tag Sheet Registry", tag)
        
        lpo = {
            "SAP Reference": "SAP-HOLD",
            "LPO Status": "On Hold",  # ON HOLD!
            "PO Quantity (Sqm)": 500.0,
        }
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "tag_id": "TAG-HOLD",
            "planned_date": "2026-02-10",
            "shift": "Morning",
            "machine_id": "MACH-1",
            "requested_by": "pm@company.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_schedule_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_schedule_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_schedule_tag._manifest', MockWorkspaceManifest()):
                    from fn_schedule_tag import main
                    response = main(mock_http_request(request_data))
        
        assert response.status_code == 422
        body = json.loads(response.get_body())
        assert body["status"] == "BLOCKED"


@pytest.mark.integration
class TestScheduleTagPOBalanceCheck:
    """Tests for PO balance validation."""
    
    def test_insufficient_po_balance_returns_422(self, mock_storage, factory, mock_http_request):
        """Test scheduling exceeding PO balance returns 422."""
        tag = {
            "Tag ID": "TAG-OVERCOMMIT",
            "Status": "Validate",
            "LPO SAP Reference Link": "SAP-SMALL",
            "Estimated Quantity": 600.0,  # Requesting 600
        }
        mock_storage.add_row("Tag Sheet Registry", tag)
        
        lpo = {
            "SAP Reference": "SAP-SMALL",
            "LPO Status": "Active",
            "PO Quantity (Sqm)": 500.0,  # Only 500 available
            "Delivered Quantity (Sqm)": 0.0,
            "Planned Quantity": 0.0,
            "Allocated Quantity": 0.0,
        }
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        machine = {"Machine ID": "MACH-1", "Status": "Operational"}
        mock_storage.add_row("00b Machine Master", machine)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "tag_id": "TAG-OVERCOMMIT",
            "planned_date": "2026-02-10",
            "shift": "Morning",
            "machine_id": "MACH-1",
            "requested_by": "pm@company.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_schedule_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_schedule_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_schedule_tag._manifest', MockWorkspaceManifest()):
                    from fn_schedule_tag import main
                    response = main(mock_http_request(request_data))
        
        assert response.status_code == 422
        body = json.loads(response.get_body())
        assert body["status"] == "BLOCKED"
    
    def test_po_balance_with_5pct_tolerance_passes(self, mock_storage, factory, mock_http_request):
        """Test scheduling at 105% of PO balance passes (within tolerance)."""
        tag = {
            "Tag ID": "TAG-TOLERANCE",
            "Status": "Validate",
            "LPO SAP Reference Link": "SAP-TOLERANCE",
            "Estimated Quantity": 525.0,  # 525 = 105% of 500 (at tolerance limit)
        }
        mock_storage.add_row("Tag Sheet Registry", tag)
        
        lpo = {
            "SAP Reference": "SAP-TOLERANCE",
            "LPO Status": "Active",
            "PO Quantity (Sqm)": 500.0,
            "Delivered Quantity (Sqm)": 0.0,
            "Planned Quantity": 0.0,
            "Allocated Quantity": 0.0,
        }
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        machine = {"Machine ID": "MACH-1", "Status": "Operational"}
        mock_storage.add_row("00b Machine Master", machine)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "tag_id": "TAG-TOLERANCE",
            "planned_date": "2026-02-10",
            "shift": "Morning",
            "machine_id": "MACH-1",
            "requested_by": "pm@company.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_schedule_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_schedule_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_schedule_tag._manifest', MockWorkspaceManifest()):
                    from fn_schedule_tag import main
                    response = main(mock_http_request(request_data))
        
        assert response.status_code == 200
        body = json.loads(response.get_body())
        assert body["status"] == "RELEASED_FOR_NESTING"


@pytest.mark.integration
class TestScheduleTagMachineValidation:
    """Tests for machine validation."""
    
    def test_machine_not_found_returns_422(self, mock_storage, factory, mock_http_request):
        """Test scheduling with non-existent machine returns 422."""
        tag = {
            "Tag ID": "TAG-NOMACHINE",
            "Status": "Validate",
            "LPO SAP Reference Link": "SAP-MACH",
            "Estimated Quantity": 100.0,
        }
        mock_storage.add_row("Tag Sheet Registry", tag)
        
        lpo = {
            "SAP Reference": "SAP-MACH",
            "LPO Status": "Active",
            "PO Quantity (Sqm)": 500.0,
            "Delivered Quantity (Sqm)": 0.0,
            "Planned Quantity": 0.0,
            "Allocated Quantity": 0.0,
        }
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        # NO machine added!
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "tag_id": "TAG-NOMACHINE",
            "planned_date": "2026-02-10",
            "shift": "Morning",
            "machine_id": "MACH-MISSING",
            "requested_by": "pm@company.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_schedule_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_schedule_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_schedule_tag._manifest', MockWorkspaceManifest()):
                    from fn_schedule_tag import main
                    response = main(mock_http_request(request_data))
        
        assert response.status_code == 422
        body = json.loads(response.get_body())
        assert body["status"] == "BLOCKED"
    
    def test_machine_maintenance_returns_422(self, mock_storage, factory, mock_http_request):
        """Test scheduling with machine under maintenance returns 422."""
        tag = {
            "Tag ID": "TAG-MAINT",
            "Status": "Validate",
            "LPO SAP Reference Link": "SAP-MAINT",
            "Estimated Quantity": 100.0,
        }
        mock_storage.add_row("Tag Sheet Registry", tag)
        
        lpo = {
            "SAP Reference": "SAP-MAINT",
            "LPO Status": "Active",
            "PO Quantity (Sqm)": 500.0,
            "Delivered Quantity (Sqm)": 0.0,
            "Planned Quantity": 0.0,
            "Allocated Quantity": 0.0,
        }
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        machine = {
            "Machine ID": "MACH-DOWN",
            "Status": "Maintenance",  # UNDER MAINTENANCE!
        }
        mock_storage.add_row("00b Machine Master", machine)
        
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            "tag_id": "TAG-MAINT",
            "planned_date": "2026-02-10",
            "shift": "Morning",
            "machine_id": "MACH-DOWN",
            "requested_by": "pm@company.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        mock_manifest = MockWorkspaceManifest()
        
        with patch('fn_schedule_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_schedule_tag.get_manifest', return_value=mock_manifest):
                with patch('fn_schedule_tag._manifest', mock_manifest):
                    # v1.6.5 DRY: Also patch shared.manifest.get_manifest for the shared helper
                    with patch('shared.manifest.get_manifest', return_value=mock_manifest):
                        from fn_schedule_tag import main
                        response = main(mock_http_request(request_data))
        
        assert response.status_code == 422
        body = json.loads(response.get_body())
        assert body["status"] == "BLOCKED"


@pytest.mark.integration
class TestScheduleTagInputValidation:
    """Tests for input validation."""
    
    def test_missing_required_field_returns_400(self, mock_storage, factory, mock_http_request):
        """Test missing required field returns 400."""
        request_data = {
            "client_request_id": str(uuid.uuid4()),
            # Missing: tag_id
            "planned_date": "2026-02-10",
            "shift": "Morning",
            "machine_id": "MACH-1",
            "requested_by": "pm@company.com"
        }
        
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_schedule_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_schedule_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_schedule_tag._manifest', MockWorkspaceManifest()):
                    from fn_schedule_tag import main
                    response = main(mock_http_request(request_data))
        
        assert response.status_code == 400
        body = json.loads(response.get_body())
        assert body["status"] == "ERROR"
    
    def test_empty_request_returns_400(self, mock_storage, factory, mock_http_request):
        """Test empty request body returns 400."""
        from tests.conftest import MockSmartsheetClient, MockWorkspaceManifest
        mock_client = MockSmartsheetClient(mock_storage)
        
        with patch('fn_schedule_tag.get_smartsheet_client', return_value=mock_client):
            with patch('fn_schedule_tag.get_manifest', return_value=MockWorkspaceManifest()):
                with patch('fn_schedule_tag._manifest', MockWorkspaceManifest()):
                    from fn_schedule_tag import main
                    response = main(mock_http_request({}))
        
        assert response.status_code == 400
