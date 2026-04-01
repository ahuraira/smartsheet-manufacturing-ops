"""
Unit tests for shared.blob_storage module.

Tests get_blob_service_client, get_container_name, upload_content_blob,
upload_json_blob, upload_nesting_json, and get_blob_url.
"""

import pytest
import json
import sys
import os
from unittest.mock import patch, MagicMock, ANY

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


# ============== get_blob_service_client ==============

class TestGetBlobServiceClient:
    """Tests for get_blob_service_client."""

    @pytest.mark.unit
    def test_no_connection_string_returns_none(self):
        """Missing AZURE_STORAGE_CONNECTION_STRING returns None."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove the key if present
            os.environ.pop("AZURE_STORAGE_CONNECTION_STRING", None)
            from shared.blob_storage import get_blob_service_client
            result = get_blob_service_client()
            assert result is None

    @pytest.mark.unit
    def test_empty_connection_string_returns_none(self):
        """Empty string for AZURE_STORAGE_CONNECTION_STRING returns None."""
        with patch.dict(os.environ, {"AZURE_STORAGE_CONNECTION_STRING": ""}):
            from shared.blob_storage import get_blob_service_client
            result = get_blob_service_client()
            assert result is None

    @pytest.mark.unit
    def test_valid_connection_string_returns_client(self):
        """Valid connection string returns a BlobServiceClient instance."""
        mock_client = MagicMock()
        with patch.dict(os.environ, {"AZURE_STORAGE_CONNECTION_STRING": "DefaultEndpointsProtocol=https;AccountName=test"}):
            with patch("shared.blob_storage.BlobServiceClient", create=True) as mock_cls:
                # Patch the import inside the function
                mock_module = MagicMock()
                mock_module.BlobServiceClient.from_connection_string.return_value = mock_client
                with patch.dict("sys.modules", {"azure.storage.blob": mock_module}):
                    from shared.blob_storage import get_blob_service_client
                    result = get_blob_service_client()
                    assert result is mock_client
                    mock_module.BlobServiceClient.from_connection_string.assert_called_once_with(
                        "DefaultEndpointsProtocol=https;AccountName=test"
                    )

    @pytest.mark.unit
    def test_import_error_returns_none(self):
        """ImportError (azure-storage-blob not installed) returns None."""
        with patch.dict(os.environ, {"AZURE_STORAGE_CONNECTION_STRING": "valid-conn-string"}):
            with patch.dict("sys.modules", {"azure.storage.blob": None}):
                # Force re-import to trigger ImportError
                import importlib
                import shared.blob_storage as blob_mod
                importlib.reload(blob_mod)
                result = blob_mod.get_blob_service_client()
                assert result is None

    @pytest.mark.unit
    def test_exception_during_creation_returns_none(self):
        """Exception during BlobServiceClient creation returns None."""
        with patch.dict(os.environ, {"AZURE_STORAGE_CONNECTION_STRING": "bad-conn-string"}):
            mock_module = MagicMock()
            mock_module.BlobServiceClient.from_connection_string.side_effect = Exception("Connection failed")
            with patch.dict("sys.modules", {"azure.storage.blob": mock_module}):
                from shared.blob_storage import get_blob_service_client
                result = get_blob_service_client()
                assert result is None


# ============== get_container_name ==============

class TestGetContainerName:
    """Tests for get_container_name."""

    @pytest.mark.unit
    def test_env_var_set(self):
        """Returns BLOB_CONTAINER_NAME when set."""
        with patch.dict(os.environ, {"BLOB_CONTAINER_NAME": "my-custom-container"}):
            from shared.blob_storage import get_container_name
            assert get_container_name() == "my-custom-container"

    @pytest.mark.unit
    def test_default_when_not_set(self):
        """Returns 'nesting-outputs' when env var not set."""
        env = os.environ.copy()
        env.pop("BLOB_CONTAINER_NAME", None)
        with patch.dict(os.environ, env, clear=True):
            from shared.blob_storage import get_container_name
            assert get_container_name() == "nesting-outputs"


# ============== upload_content_blob ==============

class TestUploadContentBlob:
    """Tests for upload_content_blob."""

    @pytest.mark.unit
    def test_blob_service_not_configured_returns_none(self):
        """Returns None when blob service client is not available."""
        with patch("shared.blob_storage.get_blob_service_client", return_value=None):
            from shared.blob_storage import upload_content_blob
            result = upload_content_blob(
                content=b"test data",
                blob_name="test.bin",
                trace_id="T-001",
            )
            assert result is None

    @pytest.mark.unit
    def test_success_returns_blob_url(self):
        """Successful upload returns the blob URL string."""
        mock_blob_client = MagicMock()
        mock_blob_client.url = "https://storage.blob.core.windows.net/container/test.bin"

        mock_container_client = MagicMock()
        mock_container_client.get_blob_client.return_value = mock_blob_client

        mock_service = MagicMock()
        mock_service.get_container_client.return_value = mock_container_client

        with patch("shared.blob_storage.get_blob_service_client", return_value=mock_service):
            with patch("shared.blob_storage.get_container_name", return_value="test-container"):
                # Patch ContentSettings import inside the function
                mock_cs_module = MagicMock()
                with patch.dict("sys.modules", {"azure.storage.blob": mock_cs_module}):
                    from shared.blob_storage import upload_content_blob
                    result = upload_content_blob(
                        content=b"file content",
                        blob_name="test.bin",
                        trace_id="T-001",
                    )
                    assert result == "https://storage.blob.core.windows.net/container/test.bin"
                    mock_service.get_container_client.assert_called_once_with("test-container")
                    mock_container_client.get_blob_client.assert_called_once_with("test.bin")

    @pytest.mark.unit
    def test_with_folder_path(self):
        """folder_path prepends to blob_name as '{folder_path}/{blob_name}'."""
        mock_blob_client = MagicMock()
        mock_blob_client.url = "https://storage.blob.core.windows.net/c/reports/output.json"

        mock_container_client = MagicMock()
        mock_container_client.get_blob_client.return_value = mock_blob_client

        mock_service = MagicMock()
        mock_service.get_container_client.return_value = mock_container_client

        with patch("shared.blob_storage.get_blob_service_client", return_value=mock_service):
            with patch("shared.blob_storage.get_container_name", return_value="c"):
                mock_cs_module = MagicMock()
                with patch.dict("sys.modules", {"azure.storage.blob": mock_cs_module}):
                    from shared.blob_storage import upload_content_blob
                    upload_content_blob(
                        content=b"data",
                        blob_name="output.json",
                        trace_id="T-002",
                        folder_path="reports",
                    )
                    mock_container_client.get_blob_client.assert_called_once_with("reports/output.json")

    @pytest.mark.unit
    def test_without_folder_path_uses_blob_name_directly(self):
        """Without folder_path, blob_name is used as-is."""
        mock_blob_client = MagicMock()
        mock_blob_client.url = "https://storage.blob.core.windows.net/c/raw.bin"

        mock_container_client = MagicMock()
        mock_container_client.get_blob_client.return_value = mock_blob_client

        mock_service = MagicMock()
        mock_service.get_container_client.return_value = mock_container_client

        with patch("shared.blob_storage.get_blob_service_client", return_value=mock_service):
            with patch("shared.blob_storage.get_container_name", return_value="c"):
                mock_cs_module = MagicMock()
                with patch.dict("sys.modules", {"azure.storage.blob": mock_cs_module}):
                    from shared.blob_storage import upload_content_blob
                    upload_content_blob(
                        content=b"data",
                        blob_name="raw.bin",
                        trace_id="T-003",
                    )
                    mock_container_client.get_blob_client.assert_called_once_with("raw.bin")

    @pytest.mark.unit
    def test_upload_failure_returns_none(self):
        """Exception during upload returns None instead of raising."""
        mock_blob_client = MagicMock()
        mock_blob_client.upload_blob.side_effect = Exception("Network error")

        mock_container_client = MagicMock()
        mock_container_client.get_blob_client.return_value = mock_blob_client

        mock_service = MagicMock()
        mock_service.get_container_client.return_value = mock_container_client

        with patch("shared.blob_storage.get_blob_service_client", return_value=mock_service):
            with patch("shared.blob_storage.get_container_name", return_value="c"):
                mock_cs_module = MagicMock()
                with patch.dict("sys.modules", {"azure.storage.blob": mock_cs_module}):
                    from shared.blob_storage import upload_content_blob
                    result = upload_content_blob(
                        content=b"data",
                        blob_name="fail.bin",
                        trace_id="T-004",
                    )
                    assert result is None

    @pytest.mark.unit
    def test_metadata_merged_with_defaults(self):
        """Custom metadata is merged with default trace_id, uploaded_at, source."""
        mock_blob_client = MagicMock()
        mock_blob_client.url = "https://example.com/blob"

        mock_container_client = MagicMock()
        mock_container_client.get_blob_client.return_value = mock_blob_client

        mock_service = MagicMock()
        mock_service.get_container_client.return_value = mock_container_client

        with patch("shared.blob_storage.get_blob_service_client", return_value=mock_service):
            with patch("shared.blob_storage.get_container_name", return_value="c"):
                mock_cs_module = MagicMock()
                with patch.dict("sys.modules", {"azure.storage.blob": mock_cs_module}):
                    from shared.blob_storage import upload_content_blob
                    upload_content_blob(
                        content=b"data",
                        blob_name="test.bin",
                        trace_id="T-005",
                        metadata={"custom_key": "custom_value"},
                    )

                    # Inspect the metadata kwarg passed to upload_blob
                    call_kwargs = mock_blob_client.upload_blob.call_args
                    meta = call_kwargs.kwargs.get("metadata") or call_kwargs[1].get("metadata")
                    assert meta["trace_id"] == "T-005"
                    assert "uploaded_at" in meta
                    assert meta["source"] == "fn_parse_nesting"
                    assert meta["custom_key"] == "custom_value"

    @pytest.mark.unit
    def test_default_metadata_without_custom(self):
        """Without custom metadata, defaults are still present."""
        mock_blob_client = MagicMock()
        mock_blob_client.url = "https://example.com/blob"

        mock_container_client = MagicMock()
        mock_container_client.get_blob_client.return_value = mock_blob_client

        mock_service = MagicMock()
        mock_service.get_container_client.return_value = mock_container_client

        with patch("shared.blob_storage.get_blob_service_client", return_value=mock_service):
            with patch("shared.blob_storage.get_container_name", return_value="c"):
                mock_cs_module = MagicMock()
                with patch.dict("sys.modules", {"azure.storage.blob": mock_cs_module}):
                    from shared.blob_storage import upload_content_blob
                    upload_content_blob(
                        content=b"data",
                        blob_name="test.bin",
                        trace_id="T-006",
                    )
                    call_kwargs = mock_blob_client.upload_blob.call_args
                    meta = call_kwargs.kwargs.get("metadata") or call_kwargs[1].get("metadata")
                    assert meta["trace_id"] == "T-006"
                    assert meta["source"] == "fn_parse_nesting"
                    assert "uploaded_at" in meta
                    # No custom keys beyond defaults
                    assert len(meta) == 3

    @pytest.mark.unit
    def test_upload_called_with_overwrite_true(self):
        """Upload is called with overwrite=True."""
        mock_blob_client = MagicMock()
        mock_blob_client.url = "https://example.com/blob"

        mock_container_client = MagicMock()
        mock_container_client.get_blob_client.return_value = mock_blob_client

        mock_service = MagicMock()
        mock_service.get_container_client.return_value = mock_container_client

        with patch("shared.blob_storage.get_blob_service_client", return_value=mock_service):
            with patch("shared.blob_storage.get_container_name", return_value="c"):
                mock_cs_module = MagicMock()
                with patch.dict("sys.modules", {"azure.storage.blob": mock_cs_module}):
                    from shared.blob_storage import upload_content_blob
                    upload_content_blob(
                        content=b"data",
                        blob_name="test.bin",
                        trace_id="T-007",
                    )
                    call_kwargs = mock_blob_client.upload_blob.call_args
                    assert call_kwargs.kwargs.get("overwrite") is True or call_kwargs[1].get("overwrite") is True

    @pytest.mark.unit
    def test_content_type_passed_through(self):
        """Custom content_type is passed to ContentSettings."""
        mock_blob_client = MagicMock()
        mock_blob_client.url = "https://example.com/blob"

        mock_container_client = MagicMock()
        mock_container_client.get_blob_client.return_value = mock_blob_client

        mock_service = MagicMock()
        mock_service.get_container_client.return_value = mock_container_client

        mock_cs = MagicMock()
        mock_cs_module = MagicMock()
        mock_cs_module.ContentSettings.return_value = mock_cs

        with patch("shared.blob_storage.get_blob_service_client", return_value=mock_service):
            with patch("shared.blob_storage.get_container_name", return_value="c"):
                with patch.dict("sys.modules", {"azure.storage.blob": mock_cs_module}):
                    from shared.blob_storage import upload_content_blob
                    upload_content_blob(
                        content=b"data",
                        blob_name="test.json",
                        trace_id="T-008",
                        content_type="application/json",
                    )
                    mock_cs_module.ContentSettings.assert_called_once_with(content_type="application/json")


# ============== upload_json_blob ==============

class TestUploadJsonBlob:
    """Tests for upload_json_blob."""

    @pytest.mark.unit
    def test_serializes_and_delegates_to_upload_content_blob(self):
        """Serializes data to JSON bytes and calls upload_content_blob."""
        test_data = {"key": "value", "count": 42}

        with patch("shared.blob_storage.upload_content_blob", return_value="https://example.com/blob") as mock_upload:
            from shared.blob_storage import upload_json_blob
            result = upload_json_blob(
                data=test_data,
                blob_name="output.json",
                trace_id="T-010",
                folder_path="results",
            )
            assert result == "https://example.com/blob"
            mock_upload.assert_called_once()
            call_kwargs = mock_upload.call_args
            # Verify the content is valid JSON
            content_bytes = call_kwargs.kwargs.get("content") or call_kwargs[0][0]
            parsed = json.loads(content_bytes.decode("utf-8"))
            assert parsed["key"] == "value"
            assert parsed["count"] == 42
            # Verify other params forwarded
            assert (call_kwargs.kwargs.get("blob_name") or call_kwargs[0][1]) == "output.json"
            assert (call_kwargs.kwargs.get("trace_id") or call_kwargs[0][2]) == "T-010"

    @pytest.mark.unit
    def test_json_serialization_error_returns_none(self):
        """Non-serializable data returns None instead of raising."""
        # Create a truly non-serializable object that json.dumps(..., default=str) still fails on
        # Since default=str handles most types, we need to trigger an error before default is called
        class BadEncoder:
            def __str__(self):
                raise RuntimeError("Cannot convert to string")

        # Patch json.dumps to raise for this test
        with patch("shared.blob_storage.json.dumps", side_effect=TypeError("Object not serializable")):
            from shared.blob_storage import upload_json_blob
            result = upload_json_blob(
                data={"bad": "data"},
                blob_name="fail.json",
                trace_id="T-011",
            )
            assert result is None

    @pytest.mark.unit
    def test_default_content_type_is_json(self):
        """Default content_type passed to upload_content_blob is application/json."""
        with patch("shared.blob_storage.upload_content_blob", return_value="url") as mock_upload:
            from shared.blob_storage import upload_json_blob
            upload_json_blob(data={"a": 1}, blob_name="x.json", trace_id="T-012")
            call_kwargs = mock_upload.call_args
            ct = call_kwargs.kwargs.get("content_type")
            assert ct == "application/json"

    @pytest.mark.unit
    def test_folder_path_forwarded(self):
        """folder_path is forwarded to upload_content_blob."""
        with patch("shared.blob_storage.upload_content_blob", return_value="url") as mock_upload:
            from shared.blob_storage import upload_json_blob
            upload_json_blob(data={}, blob_name="x.json", trace_id="T-013", folder_path="my-folder")
            call_kwargs = mock_upload.call_args
            fp = call_kwargs.kwargs.get("folder_path")
            assert fp == "my-folder"

    @pytest.mark.unit
    def test_none_folder_path_forwarded(self):
        """None folder_path is forwarded as None."""
        with patch("shared.blob_storage.upload_content_blob", return_value="url") as mock_upload:
            from shared.blob_storage import upload_json_blob
            upload_json_blob(data={}, blob_name="x.json", trace_id="T-014")
            call_kwargs = mock_upload.call_args
            fp = call_kwargs.kwargs.get("folder_path")
            assert fp is None

    @pytest.mark.unit
    def test_json_uses_default_str_for_non_serializable(self):
        """json.dumps uses default=str so datetime objects serialize."""
        from datetime import datetime
        test_data = {"timestamp": datetime(2026, 1, 15, 10, 30)}

        with patch("shared.blob_storage.upload_content_blob", return_value="url") as mock_upload:
            from shared.blob_storage import upload_json_blob
            result = upload_json_blob(data=test_data, blob_name="x.json", trace_id="T-015")
            assert result == "url"
            # Verify content was serialized (datetime as string)
            content_bytes = mock_upload.call_args.kwargs.get("content") or mock_upload.call_args[0][0]
            parsed = json.loads(content_bytes.decode("utf-8"))
            assert "2026" in parsed["timestamp"]


# ============== upload_nesting_json ==============

class TestUploadNestingJson:
    """Tests for upload_nesting_json."""

    @pytest.mark.unit
    def test_delegates_to_upload_json_blob(self):
        """Calls upload_json_blob with correct folder and filename."""
        with patch("shared.blob_storage.upload_json_blob", return_value="https://example.com/blob") as mock_upload:
            from shared.blob_storage import upload_nesting_json
            result = upload_nesting_json(
                record_data={"session": "data"},
                nest_session_id="NEST-001",
                sap_lpo_reference="SAP-REF-123",
                trace_id="T-020",
            )
            assert result == "https://example.com/blob"
            mock_upload.assert_called_once_with(
                data={"session": "data"},
                blob_name="NEST-001.json",
                trace_id="T-020",
                folder_path="SAP-REF-123",
            )

    @pytest.mark.unit
    def test_blob_name_format(self):
        """Blob name is '{nest_session_id}.json'."""
        with patch("shared.blob_storage.upload_json_blob", return_value="url") as mock_upload:
            from shared.blob_storage import upload_nesting_json
            upload_nesting_json(
                record_data={},
                nest_session_id="SESSION-XYZ",
                sap_lpo_reference="REF",
                trace_id="T-021",
            )
            assert mock_upload.call_args.kwargs["blob_name"] == "SESSION-XYZ.json"

    @pytest.mark.unit
    def test_folder_path_is_sap_reference(self):
        """folder_path is set to sap_lpo_reference."""
        with patch("shared.blob_storage.upload_json_blob", return_value="url") as mock_upload:
            from shared.blob_storage import upload_nesting_json
            upload_nesting_json(
                record_data={},
                nest_session_id="N1",
                sap_lpo_reference="PTE-185",
                trace_id="T-022",
            )
            assert mock_upload.call_args.kwargs["folder_path"] == "PTE-185"

    @pytest.mark.unit
    def test_record_data_forwarded(self):
        """record_data dict is forwarded as data parameter."""
        record = {"field_a": 1, "field_b": "value"}
        with patch("shared.blob_storage.upload_json_blob", return_value="url") as mock_upload:
            from shared.blob_storage import upload_nesting_json
            upload_nesting_json(
                record_data=record,
                nest_session_id="N2",
                sap_lpo_reference="REF",
                trace_id="T-023",
            )
            assert mock_upload.call_args.kwargs["data"] == record


# ============== get_blob_url ==============

class TestGetBlobUrl:
    """Tests for get_blob_url."""

    @pytest.mark.unit
    def test_blob_service_not_configured_returns_none(self):
        """Returns None when blob service client is not available."""
        with patch("shared.blob_storage.get_blob_service_client", return_value=None):
            from shared.blob_storage import get_blob_url
            result = get_blob_url("some-blob.json")
            assert result is None

    @pytest.mark.unit
    def test_returns_blob_url(self):
        """Returns the URL from the blob client."""
        mock_blob_client = MagicMock()
        mock_blob_client.url = "https://storage.blob.core.windows.net/container/test.json"

        mock_container_client = MagicMock()
        mock_container_client.get_blob_client.return_value = mock_blob_client

        mock_service = MagicMock()
        mock_service.get_container_client.return_value = mock_container_client

        with patch("shared.blob_storage.get_blob_service_client", return_value=mock_service):
            with patch("shared.blob_storage.get_container_name", return_value="my-container"):
                from shared.blob_storage import get_blob_url
                result = get_blob_url("test.json")
                assert result == "https://storage.blob.core.windows.net/container/test.json"
                mock_container_client.get_blob_client.assert_called_once_with("test.json")

    @pytest.mark.unit
    def test_with_folder_path(self):
        """With folder_path, uses '{folder_path}/{blob_name}' as full path."""
        mock_blob_client = MagicMock()
        mock_blob_client.url = "https://storage.blob.core.windows.net/c/folder/file.json"

        mock_container_client = MagicMock()
        mock_container_client.get_blob_client.return_value = mock_blob_client

        mock_service = MagicMock()
        mock_service.get_container_client.return_value = mock_container_client

        with patch("shared.blob_storage.get_blob_service_client", return_value=mock_service):
            with patch("shared.blob_storage.get_container_name", return_value="c"):
                from shared.blob_storage import get_blob_url
                result = get_blob_url("file.json", folder_path="folder")
                mock_container_client.get_blob_client.assert_called_once_with("folder/file.json")
                assert result == "https://storage.blob.core.windows.net/c/folder/file.json"

    @pytest.mark.unit
    def test_without_folder_path(self):
        """Without folder_path, blob_name is used directly."""
        mock_blob_client = MagicMock()
        mock_blob_client.url = "https://example.com/c/direct.bin"

        mock_container_client = MagicMock()
        mock_container_client.get_blob_client.return_value = mock_blob_client

        mock_service = MagicMock()
        mock_service.get_container_client.return_value = mock_container_client

        with patch("shared.blob_storage.get_blob_service_client", return_value=mock_service):
            with patch("shared.blob_storage.get_container_name", return_value="c"):
                from shared.blob_storage import get_blob_url
                get_blob_url("direct.bin")
                mock_container_client.get_blob_client.assert_called_once_with("direct.bin")

    @pytest.mark.unit
    def test_exception_returns_none(self):
        """Exception during URL retrieval returns None."""
        mock_service = MagicMock()
        mock_service.get_container_client.side_effect = Exception("Storage error")

        with patch("shared.blob_storage.get_blob_service_client", return_value=mock_service):
            with patch("shared.blob_storage.get_container_name", return_value="c"):
                from shared.blob_storage import get_blob_url
                result = get_blob_url("fail.json")
                assert result is None
