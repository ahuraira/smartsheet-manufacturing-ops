"""
Azure Blob Storage Helper
=========================

Provides utilities for uploading files to Azure Blob Storage.
Used by fn_parse_nesting to store JSON outputs.

Environment Variables:
- AZURE_STORAGE_CONNECTION_STRING: Storage account connection string
- BLOB_CONTAINER_NAME: Container name (default: nesting-outputs)

Usage:
    from shared.blob_storage import upload_json_blob, get_blob_url
    
    blob_url = upload_json_blob(
        data=record.model_dump(),
        blob_name=f"{nest_session_id}.json",
        trace_id=trace_id
    )
"""

import os
import json
import logging
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

# Default container name
DEFAULT_CONTAINER_NAME = "nesting-outputs"


def get_blob_service_client():
    """
    Get Azure Blob Service Client.
    
    Returns:
        BlobServiceClient or None if not configured
    """
    connection_string = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    
    if not connection_string:
        logger.warning("AZURE_STORAGE_CONNECTION_STRING not configured")
        return None
    
    try:
        from azure.storage.blob import BlobServiceClient
        return BlobServiceClient.from_connection_string(connection_string)
    except ImportError:
        logger.error("azure-storage-blob package not installed")
        return None
    except Exception as e:
        logger.error(f"Failed to create BlobServiceClient: {e}")
        return None


def get_container_name() -> str:
    """Get the container name from environment or default."""
    return os.environ.get("BLOB_CONTAINER_NAME", DEFAULT_CONTAINER_NAME)


def upload_content_blob(
    content: bytes,
    blob_name: str,
    trace_id: str,
    folder_path: Optional[str] = None,
    content_type: str = "application/octet-stream",
    metadata: Optional[Dict[str, str]] = None
) -> Optional[str]:
    """
    Upload raw content (bytes) to Azure Blob Storage.
    
    Args:
        content: File content in bytes
        blob_name: Name of the blob file
        trace_id: Trace ID for logging
        folder_path: Optional folder path
        content_type: MIME type
        metadata: Optional metadata dict
    
    Returns:
        Blob URL if successful, None otherwise
    """
    service_client = get_blob_service_client()
    if not service_client:
        logger.warning(f"[{trace_id}] Blob storage not configured - skipping upload")
        return None
    
    container_name = get_container_name()
    
    # Build full blob path
    if folder_path:
        full_blob_name = f"{folder_path}/{blob_name}"
    else:
        full_blob_name = blob_name
    
    try:
        container_client = service_client.get_container_client(container_name)
        blob_client = container_client.get_blob_client(full_blob_name)
        
        from azure.storage.blob import ContentSettings
        
        # Merge default metadata with provided
        final_meta = {
            "trace_id": trace_id,
            "uploaded_at": datetime.utcnow().isoformat(),
            "source": "fn_parse_nesting"
        }
        if metadata:
            final_meta.update(metadata)
            
        blob_client.upload_blob(
            data=content,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
            metadata=final_meta
        )
        
        blob_url = blob_client.url
        logger.info(f"[{trace_id}] Uploaded blob: {full_blob_name} -> {blob_url}")
        
        return blob_url
        
    except Exception as e:
        logger.error(f"[{trace_id}] Failed to upload blob {full_blob_name}: {e}")
        return None


def upload_json_blob(
    data: Dict[str, Any],
    blob_name: str,
    trace_id: str,
    folder_path: Optional[str] = None,
    content_type: str = "application/json"
) -> Optional[str]:
    """
    Upload JSON data to Azure Blob Storage.
    """
    try:
        json_content = json.dumps(data, indent=2, default=str).encode('utf-8')
        return upload_content_blob(
            content=json_content,
            blob_name=blob_name,
            trace_id=trace_id,
            folder_path=folder_path,
            content_type=content_type
        )
    except Exception as e:
        logger.error(f"[{trace_id}] Failed to serialize JSON for blob upload: {e}")
        return None


def upload_nesting_json(
    record_data: Dict[str, Any],
    nest_session_id: str,
    sap_lpo_reference: str,
    trace_id: str
) -> Optional[str]:
    """
    Convenience function to upload nesting execution record.
    
    Stores the JSON in a folder structure: {sap_reference}/{nest_session_id}.json
    
    Args:
        record_data: NestingExecutionRecord as dict
        nest_session_id: Nesting session ID (becomes filename)
        sap_lpo_reference: LPO SAP reference (becomes folder)
        trace_id: Trace ID for logging
    
    Returns:
        Blob URL if successful, None otherwise
    """
    blob_name = f"{nest_session_id}.json"
    
    return upload_json_blob(
        data=record_data,
        blob_name=blob_name,
        trace_id=trace_id,
        folder_path=sap_lpo_reference
    )


def get_blob_url(
    blob_name: str,
    folder_path: Optional[str] = None
) -> Optional[str]:
    """
    Get the URL for a blob without uploading.
    
    Args:
        blob_name: Name of the blob
        folder_path: Optional folder path
    
    Returns:
        Blob URL
    """
    service_client = get_blob_service_client()
    if not service_client:
        return None
    
    container_name = get_container_name()
    
    if folder_path:
        full_blob_name = f"{folder_path}/{blob_name}"
    else:
        full_blob_name = blob_name
    
    try:
        container_client = service_client.get_container_client(container_name)
        blob_client = container_client.get_blob_client(full_blob_name)
        return blob_client.url
    except Exception:
        return None
