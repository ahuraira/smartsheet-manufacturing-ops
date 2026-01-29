"""
Unit Tests for Smartsheet Client Attachments (v1.6.3)

Tests robust attachment handling:
1. Fetching attachment details (deep fetch) to get URLs.
2. Handling long URL (>500 chars) limitation by fallback mechanism.
"""

import pytest
from unittest.mock import MagicMock, patch
from shared.smartsheet_client import SmartsheetClient

@pytest.mark.unit
class TestClientAttachments:
    
    @pytest.fixture(autouse=True)
    def setup_env(self):
        with patch.dict("os.environ", {
            "SMARTSHEET_API_KEY": "test-key",
            "SMARTSHEET_WORKSPACE_ID": "12345"
        }):
            yield

    @patch("shared.smartsheet_client.get_manifest") 
    def test_get_row_attachments_deep_fetch(self, mock_get_manifest):
        """
        Verify that get_row_attachments calls detailed endpoint for each attachment
        to retrieve the temporary download URL (which is missing in list endpoint).
        """
        client = SmartsheetClient(manifest=MagicMock())
        client._make_request = MagicMock()
        
        # Mock List Response (URLs missing)
        list_resp = MagicMock()
        list_resp.json.return_value = {
            "data": [{"id": 1, "name": "a.pdf"}, {"id": 2, "name": "b.pdf"}]
        }
        
        # Mock Detail Responses (URLs present)
        detail_resp_1 = MagicMock()
        detail_resp_1.json.return_value = {"id": 1, "name": "a.pdf", "url": "http://real-url-1"}
        
        detail_resp_2 = MagicMock()
        detail_resp_2.json.return_value = {"id": 2, "name": "b.pdf", "url": "http://real-url-2"}
        
        # First call is LIST sheets/.../attachments
        # Subsequent calls are GET sheets/.../attachments/{id}
        client._make_request.side_effect = [list_resp, detail_resp_1, detail_resp_2]
        
        # Execute
        # Sheet ID resolution mocked inside client or by passing int
        attachments = client.get_row_attachments(123, 456)
        
        assert len(attachments) == 2
        assert attachments[0]["url"] == "http://real-url-1"
        assert attachments[1]["url"] == "http://real-url-2"
        
        # Verify call count: 1 list + 2 details = 3 calls
        assert client._make_request.call_count == 3

    @patch("shared.smartsheet_client.get_manifest") 
    def test_attach_url_long_url_fallback(self, mock_get_manifest):
        """
        Verify that if URL > 500 chars (implied logic, or explicit check),
        client falls back to downloading and uploading as file.
        
        NOTE: The actual SmartSheetClient.attach_url_to_row method needs to contain this logic.
        This test assumes the logic exists or will be added. 
        If logic is in `fn_ingest_tag` or helper, test there. 
        
        Checking `smartsheet_client.py`:
        User CHANGELOG says: "attach_url_to_row automatically downloads/re-uploads files if URL > 500 chars"
        So logic IS in client.
        """
        client = SmartsheetClient(manifest=MagicMock())
        client._make_request = MagicMock()
        client.attach_file_to_row = MagicMock() # Mock fallback method
        
        # Setup Long URL
        long_url = "http://example.com/" + "a" * 1000
        
        
        # Mock download of the long URL AND the upload POST
        with patch("requests.get") as mock_download, \
             patch("requests.post") as mock_upload:
            
            mock_download.return_value.content = b"file-content"
            mock_download.return_value.status_code = 200
            
            mock_upload.return_value.status_code = 200
            mock_upload.return_value.json.return_value = {"result": {"id": 789}}
            
            # Execute
            client.attach_url_to_row(123, 456, long_url, "long.pdf")
            
            # Verify File Upload called via internal logic or fallthrough to _download_and_attach_file
            # The code calls _download_and_attach_file -> requests.post
            mock_upload.assert_called_once()
            
            # Verify correct URL used for upload
            assert "sheets/123/rows/456/attachments" in mock_upload.call_args[0][0]
            
            # Verify _make_request (URL attach API) was NOT called for attachment
            client._make_request.assert_not_called()

    @patch("shared.smartsheet_client.get_manifest") 
    def test_attach_url_normal(self, mock_get_manifest):
        """Verify normal short URL uses standard API."""
        client = SmartsheetClient(manifest=MagicMock())
        client._make_request = MagicMock()
        client.attach_file_to_row = MagicMock()
        
        short_url = "http://short.com/file"
        
        client.attach_url_to_row(123, 456, short_url, "short.pdf")
        
        # Verify API called
        client._make_request.assert_called_once()
        # Verify NO fallback
        client.attach_file_to_row.assert_not_called()
