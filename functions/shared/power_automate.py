"""
Power Automate Flow Client
==========================

Shared module for calling Power Automate HTTP trigger flows.

SOTA Implementation:
- Retry with exponential backoff
- Configurable timeout
- Fire-and-forget pattern (default)
- Synchronous mode (optional)
- Structured logging
- Idempotency via correlation_id

Usage:
    from shared.power_automate import FlowClient, FlowTriggerRequest

    client = FlowClient()
    result = client.trigger_create_folders(
        sap_reference="PTE-185",
        customer_name="Acme Corp",
        folder_path="/LPOs/PTE-185_Acme",
        correlation_id="trace-123"
    )
"""

import os
import logging
import time
from typing import Optional, Dict, Any
from dataclasses import dataclass, field
from enum import Enum
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

# Default LPO subfolders (can be overridden via LPO_SUBFOLDERS env var)
DEFAULT_LPO_SUBFOLDERS = [
    "LPO Documents",
    "Costing",
    "Tag Sheets",
    "Cut Sessions",
    "BOMs",
    "Deliveries",
    "PODs",
    "Invoices"
]


class FlowType(str, Enum):
    """Supported Power Automate flow types."""
    CREATE_LPO_FOLDERS = "create_lpo_folders"
    CREATE_TAG_FOLDERS = "create_tag_folders"  # Future use
    SEND_NOTIFICATION = "send_notification"     # Future use
    NESTING_COMPLETE = "nesting_complete"       # v1.6.7: Nesting completion flow
    UPLOAD_FILES = "upload_files"               # v1.6.9: Generic directory file upload


@dataclass
class FlowTriggerResult:
    """Result of a Power Automate flow trigger."""
    
    success: bool
    flow_type: FlowType
    correlation_id: str
    response_status: Optional[int] = None
    response_body: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    retry_count: int = 0
    elapsed_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "flow_type": self.flow_type.value,
            "correlation_id": self.correlation_id,
            "response_status": self.response_status,
            "error_message": self.error_message,
            "retry_count": self.retry_count,
            "elapsed_ms": round(self.elapsed_ms, 2)
        }


@dataclass
class FlowClientConfig:
    """Configuration for Power Automate client."""
    
    # Flow URLs (from environment)
    create_folders_url: Optional[str] = None
    nesting_complete_url: Optional[str] = None  # v1.6.7
    upload_files_url: Optional[str] = None      # v1.6.9: Generic upload flow URL
    
    # LPO subfolder structure (configurable via LPO_SUBFOLDERS env var)
    lpo_subfolders: list = field(default_factory=lambda: DEFAULT_LPO_SUBFOLDERS.copy())
    
    # Retry settings
    max_retries: int = 3
    retry_backoff_factor: float = 0.5  # 0.5, 1.0, 2.0 seconds
    retry_status_codes: tuple = (429, 500, 502, 503, 504)
    
    # Timeout settings (seconds)
    connect_timeout: float = 5.0
    read_timeout: float = 10.0  # Short for fire-and-forget
    
    # Fire-and-forget mode
    fire_and_forget: bool = True
    
    @classmethod
    def from_environment(cls) -> "FlowClientConfig":
        """
        Load configuration from environment variables.
        
        LPO_SUBFOLDERS can be comma-separated list, e.g.:
        LPO_SUBFOLDERS=LPO Documents,Costing,Tag Sheets,BOMs
        """
        # Parse subfolders from env var (comma-separated)
        subfolders_env = os.environ.get("LPO_SUBFOLDERS", "")
        if subfolders_env.strip():
            subfolders = [s.strip() for s in subfolders_env.split(",") if s.strip()]
        else:
            subfolders = DEFAULT_LPO_SUBFOLDERS.copy()
        
        return cls(
            create_folders_url=os.environ.get("POWER_AUTOMATE_CREATE_FOLDERS_URL"),
            nesting_complete_url=os.environ.get("POWER_AUTOMATE_NESTING_COMPLETE_URL"),  # v1.6.7
            upload_files_url=os.environ.get("POWER_AUTOMATE_UPLOAD_FILES_URL"),          # v1.6.9
            lpo_subfolders=subfolders,
            max_retries=int(os.environ.get("FLOW_MAX_RETRIES", "3")),
            connect_timeout=float(os.environ.get("FLOW_CONNECT_TIMEOUT", "5.0")),
            read_timeout=float(os.environ.get("FLOW_READ_TIMEOUT", "10.0")),
            fire_and_forget=os.environ.get("FLOW_FIRE_AND_FORGET", "true").lower() == "true"
        )


class FlowClient:
    """
    Client for triggering Power Automate flows.
    
    Implements industry-standard patterns:
    - Connection pooling via requests.Session
    - Automatic retry with exponential backoff
    - Configurable timeouts
    - Fire-and-forget mode for async operations
    - Comprehensive error handling and logging
    """
    
    def __init__(self, config: Optional[FlowClientConfig] = None):
        """
        Initialize Flow client.
        
        Args:
            config: Optional configuration. Loads from environment if not provided.
        """
        self.config = config or FlowClientConfig.from_environment()
        self._session = self._create_session()
    
    def _create_session(self) -> requests.Session:
        """Create a session with retry configuration."""
        session = requests.Session()
        
        # Configure retry strategy
        retry_strategy = Retry(
            total=self.config.max_retries,
            backoff_factor=self.config.retry_backoff_factor,
            status_forcelist=list(self.config.retry_status_codes),
            allowed_methods=["POST"],
            raise_on_status=False  # Don't raise, let us handle
        )
        
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        
        # Set default headers
        session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json"
        })
        
        return session
    
    def trigger_create_folders(
        self,
        sap_reference: str,
        customer_name: str,
        folder_path: str,
        correlation_id: str,
        additional_data: Optional[Dict[str, Any]] = None
    ) -> FlowTriggerResult:
        """
        Trigger the Create LPO Folders flow.
        
        Args:
            sap_reference: The SAP reference for the LPO
            customer_name: Customer name for folder naming
            folder_path: Full SharePoint folder path
            correlation_id: Trace ID for correlation
            additional_data: Optional extra data to pass to flow
        
        Returns:
            FlowTriggerResult with success/failure info
        """
        if not self.config.create_folders_url:
            logger.warning(
                f"[{correlation_id}] POWER_AUTOMATE_CREATE_FOLDERS_URL not configured - skipping flow trigger"
            )
            return FlowTriggerResult(
                success=False,
                flow_type=FlowType.CREATE_LPO_FOLDERS,
                correlation_id=correlation_id,
                error_message="Flow URL not configured"
            )
        
        payload = {
            "sap_reference": sap_reference,
            "customer_name": customer_name,
            "folder_path": folder_path,
            "correlation_id": correlation_id,
            "subfolders": self.config.lpo_subfolders,
            **(additional_data or {})
        }
        
        return self._trigger_flow(
            flow_type=FlowType.CREATE_LPO_FOLDERS,
            url=self.config.create_folders_url,
            payload=payload,
            correlation_id=correlation_id
        )
    
    def _trigger_flow(
        self,
        flow_type: FlowType,
        url: str,
        payload: Dict[str, Any],
        correlation_id: str
    ) -> FlowTriggerResult:
        """
        Internal method to trigger a flow with proper error handling.
        
        Implements fire-and-forget pattern:
        - Short timeout to avoid blocking caller
        - Logs all attempts for observability
        - Never raises exceptions - always returns result
        """
        start_time = time.time()
        
        try:
            logger.info(
                f"[{correlation_id}] Triggering {flow_type.value} flow",
                extra={"flow_type": flow_type.value, "correlation_id": correlation_id}
            )
            
            response = self._session.post(
                url,
                json=payload,
                timeout=(self.config.connect_timeout, self.config.read_timeout)
            )
            
            elapsed_ms = (time.time() - start_time) * 1000
            
            # Parse response body if possible
            response_body = None
            try:
                response_body = response.json()
            except Exception:
                pass
            
            if response.status_code in (200, 201, 202):
                logger.info(
                    f"[{correlation_id}] Flow {flow_type.value} triggered successfully "
                    f"(status={response.status_code}, elapsed={elapsed_ms:.0f}ms)"
                )
                return FlowTriggerResult(
                    success=True,
                    flow_type=flow_type,
                    correlation_id=correlation_id,
                    response_status=response.status_code,
                    response_body=response_body,
                    elapsed_ms=elapsed_ms
                )
            else:
                logger.warning(
                    f"[{correlation_id}] Flow {flow_type.value} returned non-success "
                    f"(status={response.status_code}, elapsed={elapsed_ms:.0f}ms)"
                )
                return FlowTriggerResult(
                    success=False,
                    flow_type=flow_type,
                    correlation_id=correlation_id,
                    response_status=response.status_code,
                    response_body=response_body,
                    error_message=f"Flow returned status {response.status_code}",
                    elapsed_ms=elapsed_ms
                )
        
        except requests.exceptions.Timeout as e:
            elapsed_ms = (time.time() - start_time) * 1000
            
            if self.config.fire_and_forget:
                # In fire-and-forget mode, timeout might be OK
                # The flow might still be running
                logger.info(
                    f"[{correlation_id}] Flow {flow_type.value} timed out (fire-and-forget mode) "
                    f"- flow may still complete"
                )
                return FlowTriggerResult(
                    success=True,  # Optimistic - flow was triggered
                    flow_type=flow_type,
                    correlation_id=correlation_id,
                    error_message="Timeout (fire-and-forget - flow may still complete)",
                    elapsed_ms=elapsed_ms
                )
            else:
                logger.error(f"[{correlation_id}] Flow {flow_type.value} timed out: {e}")
                return FlowTriggerResult(
                    success=False,
                    flow_type=flow_type,
                    correlation_id=correlation_id,
                    error_message=f"Timeout: {str(e)}",
                    elapsed_ms=elapsed_ms
                )
        
        except requests.exceptions.ConnectionError as e:
            elapsed_ms = (time.time() - start_time) * 1000
            logger.error(f"[{correlation_id}] Flow {flow_type.value} connection error: {e}")
            return FlowTriggerResult(
                success=False,
                flow_type=flow_type,
                correlation_id=correlation_id,
                error_message=f"Connection error: {str(e)}",
                elapsed_ms=elapsed_ms
            )
        
        except Exception as e:
            elapsed_ms = (time.time() - start_time) * 1000
            logger.exception(f"[{correlation_id}] Flow {flow_type.value} unexpected error: {e}")
            return FlowTriggerResult(
                success=False,
                flow_type=flow_type,
                correlation_id=correlation_id,
                error_message=f"Unexpected error: {str(e)}",
                elapsed_ms=elapsed_ms
            )
    
    def close(self):
        """Close the session and release resources."""
        if self._session:
            self._session.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# Module-level singleton for convenience (thread-safe v1.6.9)
import threading
_flow_client: Optional[FlowClient] = None
_flow_client_lock = threading.Lock()


def get_flow_client() -> FlowClient:
    """
    Get the singleton FlowClient instance (thread-safe).
    
    Returns:
        FlowClient instance configured from environment
    """
    global _flow_client
    if _flow_client is None:
        with _flow_client_lock:
            # Double-check locking pattern
            if _flow_client is None:
                _flow_client = FlowClient()
    return _flow_client


def trigger_create_lpo_folders(
    sap_reference: str,
    customer_name: str,
    folder_path: str,
    correlation_id: str
) -> FlowTriggerResult:
    """
    Convenience function to trigger folder creation flow.
    
    This is a fire-and-forget call - the LPO creation should not
    fail even if folder creation fails.
    
    Args:
        sap_reference: The SAP reference for the LPO
        customer_name: Customer name for folder naming
        folder_path: Full SharePoint folder path
        correlation_id: Trace ID for correlation
    
    Returns:
        FlowTriggerResult with trigger status
    """
    client = get_flow_client()
    return client.trigger_create_folders(
        sap_reference=sap_reference,
        customer_name=customer_name,
        folder_path=folder_path,
        correlation_id=correlation_id
    )


def trigger_nesting_complete_flow(
    nest_session_id: str,
    tag_id: str,
    sap_lpo_reference: str,
    brand: str,
    json_blob_url: Optional[str],
    excel_file_url: Optional[str],
    uploaded_by: str,
    planned_date: Optional[str],
    area_consumed: float,
    area_type: str,
    correlation_id: str,
    lpo_folder_url: Optional[str] = None  # v1.6.7
) -> FlowTriggerResult:
    """
    Trigger the Nesting Complete flow to:
    1. Copy files to LPO folder in SharePoint
    2. Send acknowledgment email to uploader
    
    Args:
        nest_session_id: Nesting session ID
        tag_id: Tag ID processed
        sap_lpo_reference: LPO SAP Reference
        brand: Brand (KIMMCO/WTI)
        json_blob_url: URL to JSON in blob storage
        excel_file_url: URL to original Excel file
        uploaded_by: Email of uploader
        planned_date: Planned production date
        area_consumed: Area consumed (m²)
        area_type: Internal or External
        correlation_id: Trace ID for correlation
        lpo_folder_url: Optional override for LPO SharePoint URL
    
    Returns:
        FlowTriggerResult with trigger status
    """
    client = get_flow_client()
    
    if not client.config.nesting_complete_url:
        logger.warning(
            f"[{correlation_id}] POWER_AUTOMATE_NESTING_COMPLETE_URL not configured - skipping flow trigger"
        )
        return FlowTriggerResult(
            success=False,
            flow_type=FlowType.NESTING_COMPLETE,
            correlation_id=correlation_id,
            error_message="Flow URL not configured"
        )
    
    payload = {
        "nest_session_id": nest_session_id,
        "tag_id": tag_id,
        "sap_lpo_reference": sap_lpo_reference,
        "brand": brand,
        "json_blob_url": json_blob_url,
        "excel_file_url": excel_file_url,
        "uploaded_by": uploaded_by,
        "planned_date": planned_date,
        "area_consumed": area_consumed,
        "area_type": area_type,
        "correlation_id": correlation_id,
        "lpo_folder_url": lpo_folder_url
    }
    
    return client._trigger_flow(
        flow_type=FlowType.NESTING_COMPLETE,
        url=client.config.nesting_complete_url,
        payload=payload,
        correlation_id=correlation_id
    )


def trigger_upload_files_flow(
    lpo_folder_url: str,
    files: list,
    correlation_id: str
) -> FlowTriggerResult:
    """
    Trigger generic flow to upload files to SharePoint.
    
    v1.6.9: Created to abstract file updates.
    
    Args:
        lpo_folder_url: Target LPO root folder URL
        files: List of FileUploadItem objects (file_name, content, subfolder)
        correlation_id: Trace ID for logging
        
    Returns:
        FlowTriggerResult with status
    """
    client = get_flow_client()
    
    if not client.config.upload_files_url:
        logger.warning(
            f"[{correlation_id}] POWER_AUTOMATE_UPLOAD_FILES_URL not configured - skipping upload trigger"
        )
        return FlowTriggerResult(
            success=False,
            flow_type=FlowType.UPLOAD_FILES,
            correlation_id=correlation_id,
            error_message="Flow URL not configured"
        )
    
    # Convert FileUploadItem objects to dict list
    files_payload = []
    for f in files:
        if hasattr(f, 'model_dump'):
            files_payload.append(f.model_dump())
        else:
            files_payload.append(f)  # Assume dict if not model
            
    payload = {
        "lpo_folder_url": lpo_folder_url,
        "files": files_payload,
        "correlation_id": correlation_id
    }
    
    return client._trigger_flow(
        flow_type=FlowType.UPLOAD_FILES,
        url=client.config.upload_files_url,
        payload=payload,
        correlation_id=correlation_id
    )


