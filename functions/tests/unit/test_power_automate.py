"""
Unit Tests for Power Automate FlowClient (v1.3.1)

Tests the Power Automate HTTP trigger client implementation:
- Configuration from environment variables
- Retry with exponential backoff
- Fire-and-forget timeout handling
- Connection pooling
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
import os

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.power_automate import (
    FlowClient,
    FlowClientConfig,
    FlowTriggerResult,
    FlowType,
    get_flow_client,
    trigger_create_lpo_folders,
    DEFAULT_LPO_SUBFOLDERS,
)


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset FlowClient singleton before each test."""
    import shared.power_automate as pa
    pa._flow_client = None
    yield
    pa._flow_client = None


@pytest.fixture
def clean_env():
    """Fixture to ensure clean environment variables."""
    original_env = os.environ.copy()
    # Clear power automate related env vars
    for key in list(os.environ.keys()):
        if key.startswith(("POWER_AUTOMATE_", "FLOW_", "LPO_SUBFOLDERS")):
            del os.environ[key]
    yield
    os.environ.clear()
    os.environ.update(original_env)


@pytest.mark.unit
class TestFlowType:
    """Tests for FlowType enum."""
    
    def test_flow_types(self):
        """Test all FlowType enum values."""
        assert FlowType.CREATE_LPO_FOLDERS.value == "create_lpo_folders"
        assert FlowType.CREATE_TAG_FOLDERS.value == "create_tag_folders"
        assert FlowType.SEND_NOTIFICATION.value == "send_notification"


@pytest.mark.unit
class TestFlowTriggerResult:
    """Tests for FlowTriggerResult dataclass."""
    
    def test_success_result(self):
        """Test creating a success result."""
        result = FlowTriggerResult(
            success=True,
            flow_type=FlowType.CREATE_LPO_FOLDERS,
            correlation_id="trace-123",
            response_status=202,
            elapsed_ms=45.5
        )
        
        assert result.success is True
        assert result.response_status == 202
        
    def test_error_result(self):
        """Test creating an error result."""
        result = FlowTriggerResult(
            success=False,
            flow_type=FlowType.CREATE_LPO_FOLDERS,
            correlation_id="trace-456",
            error_message="Connection refused"
        )
        
        assert result.success is False
        assert result.error_message == "Connection refused"
        
    def test_to_dict(self):
        """Test serialization to dictionary."""
        result = FlowTriggerResult(
            success=True,
            flow_type=FlowType.CREATE_LPO_FOLDERS,
            correlation_id="trace-789",
            elapsed_ms=99.876
        )
        
        data = result.to_dict()
        
        assert data["success"] is True
        assert data["flow_type"] == "create_lpo_folders"
        assert data["elapsed_ms"] == 99.88  # Rounded to 2 decimal places


@pytest.mark.unit
class TestFlowClientConfig:
    """Tests for FlowClientConfig."""
    
    def test_default_config(self, clean_env):
        """Test default configuration values."""
        config = FlowClientConfig.from_environment()
        
        assert config.create_folders_url is None
        assert config.max_retries == 3
        assert config.connect_timeout == 5.0
        assert config.read_timeout == 10.0
        assert config.fire_and_forget is True
        assert config.lpo_subfolders == DEFAULT_LPO_SUBFOLDERS
        
    def test_config_from_environment(self, clean_env):
        """Test configuration from environment variables."""
        os.environ["POWER_AUTOMATE_CREATE_FOLDERS_URL"] = "https://prod.flow.microsoft.com/trigger"
        os.environ["FLOW_MAX_RETRIES"] = "5"
        os.environ["FLOW_CONNECT_TIMEOUT"] = "10.0"
        os.environ["FLOW_READ_TIMEOUT"] = "30.0"
        os.environ["FLOW_FIRE_AND_FORGET"] = "false"
        
        config = FlowClientConfig.from_environment()
        
        assert config.create_folders_url == "https://prod.flow.microsoft.com/trigger"
        assert config.max_retries == 5
        assert config.connect_timeout == 10.0
        assert config.read_timeout == 30.0
        assert config.fire_and_forget is False
        
    def test_custom_subfolders(self, clean_env):
        """Test custom LPO subfolders from environment."""
        os.environ["LPO_SUBFOLDERS"] = "Docs,Costings,Tags"
        
        config = FlowClientConfig.from_environment()
        
        assert config.lpo_subfolders == ["Docs", "Costings", "Tags"]


@pytest.mark.unit
class TestFlowClient:
    """Tests for FlowClient."""
    
    def test_client_initialization(self, clean_env):
        """Test client initialization with config."""
        config = FlowClientConfig(
            create_folders_url="https://test.flow.com/trigger",
            max_retries=2
        )
        
        client = FlowClient(config)
        
        assert client.config.create_folders_url == "https://test.flow.com/trigger"
        assert client.config.max_retries == 2
        
    def test_trigger_without_url_configured(self, clean_env):
        """Test triggering flow when URL is not configured."""
        client = FlowClient(FlowClientConfig())  # No URL
        
        result = client.trigger_create_folders(
            sap_reference="PTE-001",
            customer_name="Test Corp",
            folder_path="/LPOs/PTE-001",
            correlation_id="trace-001"
        )
        
        assert result.success is False
        assert "not configured" in result.error_message
        
    @patch("shared.power_automate.requests.Session")
    def test_trigger_success(self, mock_session_class, clean_env):
        """Test successful flow trigger."""
        # Setup mock
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.json.return_value = {"status": "accepted"}
        mock_session.post.return_value = mock_response
        mock_session_class.return_value = mock_session
        
        config = FlowClientConfig(
            create_folders_url="https://test.flow.com/trigger"
        )
        client = FlowClient(config)
        
        result = client.trigger_create_folders(
            sap_reference="PTE-002",
            customer_name="Acme Corp",
            folder_path="/LPOs/PTE-002_Acme",
            correlation_id="trace-002"
        )
        
        assert result.success is True
        assert result.response_status == 202
        
    @patch("shared.power_automate.requests.Session")
    def test_trigger_fire_and_forget_timeout(self, mock_session_class, clean_env):
        """Test fire-and-forget mode treats timeout as success."""
        import requests
        
        # Setup mock to raise timeout
        mock_session = MagicMock()
        mock_session.post.side_effect = requests.exceptions.Timeout("Connection timed out")
        mock_session_class.return_value = mock_session
        
        config = FlowClientConfig(
            create_folders_url="https://test.flow.com/trigger",
            fire_and_forget=True  # Fire-and-forget mode
        )
        client = FlowClient(config)
        
        result = client.trigger_create_folders(
            sap_reference="PTE-003",
            customer_name="Test Corp",
            folder_path="/LPOs/PTE-003",
            correlation_id="trace-003"
        )
        
        # In fire-and-forget mode, timeout is treated as success
        # (the flow was triggered, we just didn't wait for response)
        assert result.success is True
        assert "fire-and-forget" in result.error_message.lower()
        
    @patch("shared.power_automate.requests.Session")
    def test_trigger_sync_timeout_is_failure(self, mock_session_class, clean_env):
        """Test synchronous mode treats timeout as failure."""
        import requests
        
        mock_session = MagicMock()
        mock_session.post.side_effect = requests.exceptions.Timeout("Timeout")
        mock_session_class.return_value = mock_session
        
        config = FlowClientConfig(
            create_folders_url="https://test.flow.com/trigger",
            fire_and_forget=False  # Synchronous mode
        )
        client = FlowClient(config)
        
        result = client.trigger_create_folders(
            sap_reference="PTE-004",
            customer_name="Test Corp",
            folder_path="/LPOs/PTE-004",
            correlation_id="trace-004"
        )
        
        # In sync mode, timeout is failure
        assert result.success is False
        assert "timeout" in result.error_message.lower()
        
    @patch("shared.power_automate.requests.Session")
    def test_trigger_connection_error(self, mock_session_class, clean_env):
        """Test connection error handling."""
        import requests
        
        mock_session = MagicMock()
        mock_session.post.side_effect = requests.exceptions.ConnectionError("Failed to connect")
        mock_session_class.return_value = mock_session
        
        config = FlowClientConfig(
            create_folders_url="https://test.flow.com/trigger"
        )
        client = FlowClient(config)
        
        result = client.trigger_create_folders(
            sap_reference="PTE-005",
            customer_name="Corp",
            folder_path="/path",
            correlation_id="trace-005"
        )
        
        assert result.success is False
        assert "connection error" in result.error_message.lower()
        
    @patch("shared.power_automate.requests.Session")
    def test_trigger_non_success_status(self, mock_session_class, clean_env):
        """Test handling of non-success HTTP status."""
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {"error": "Internal error"}
        mock_session.post.return_value = mock_response
        mock_session_class.return_value = mock_session
        
        config = FlowClientConfig(
            create_folders_url="https://test.flow.com/trigger"
        )
        client = FlowClient(config)
        
        result = client.trigger_create_folders(
            sap_reference="PTE-006",
            customer_name="Corp",
            folder_path="/path",
            correlation_id="trace-006"
        )
        
        assert result.success is False
        assert result.response_status == 500
        
    def test_client_context_manager(self, clean_env):
        """Test client as context manager."""
        config = FlowClientConfig()
        
        with FlowClient(config) as client:
            assert client is not None
            assert client._session is not None


@pytest.mark.unit
class TestFlowClientSingleton:
    """Tests for FlowClient singleton pattern."""
    
    def test_get_flow_client_singleton(self, clean_env):
        """Test singleton pattern returns same instance."""
        client1 = get_flow_client()
        client2 = get_flow_client()
        
        assert client1 is client2
        
    @patch("shared.power_automate.requests.Session")
    def test_convenience_function(self, mock_session_class, clean_env):
        """Test trigger_create_lpo_folders convenience function."""
        # Setup mock
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 202
        mock_response.json.return_value = {}
        mock_session.post.return_value = mock_response
        mock_session_class.return_value = mock_session
        
        os.environ["POWER_AUTOMATE_CREATE_FOLDERS_URL"] = "https://test.flow.com"
        
        result = trigger_create_lpo_folders(
            sap_reference="PTE-007",
            customer_name="Test",
            folder_path="/path",
            correlation_id="trace-007"
        )
        
        assert result.success is True
