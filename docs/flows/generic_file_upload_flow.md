# Generic File Upload Flow

## Overview
This flow allows uploading multiple files to specific subfolders within a target LPO folder in SharePoint. It abstracts the folder structure logic, making it reusable for various functions (LPO Ingestion, Tag Ingestion, Nesting, etc.).

## Trigger
- **Type**: HTTP Request
- **Method**: POST
- **URL Configuration**: `POWER_AUTOMATE_UPLOAD_FILES_URL`

## JSON Schema

```json
{
    "type": "object",
    "properties": {
        "lpo_folder_url": {
            "type": "string",
            "description": "Full SharePoint URL to the root LPO folder (or target execution folder)"
        },
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file_name": {
                        "type": "string",
                        "description": "Name of the file including extension"
                    },
                    "file_content": {
                        "type": "string",
                        "description": "Base64 encoded file content"
                    },
                    "subfolder": {
                        "type": ["string", "null"],
                        "description": "Destination subfolder name relative to lpo_folder_url. If null, uploads to root."
                    }
                },
                "required": [
                    "file_name",
                    "file_content"
                ]
            }
        },
        "correlation_id": {
            "type": "string",
            "description": "Trace ID for logging and coordination"
        }
    },
    "required": [
        "lpo_folder_url",
        "files",
        "correlation_id"
    ]
}
```

## Example Payload

```json
{
    "lpo_folder_url": "https://algurguae.sharepoint.com/sites/DuctsFabricationPlant/Ducts/LPOs/PTE-185_Acme_Corp",
    "files": [
        {
            "file_name": "LPO_Document.pdf",
            "file_content": "JVBERi0xLjQK...",
            "subfolder": "LPO Documents"
        },
        {
            "file_name": "BOQ.xlsx",
            "file_content": "UEsDBBQAAAAI...",
            "subfolder": "Costing"
        },
        {
            "file_name": "README.txt",
            "file_content": "VGhpcyBpcyBh...",
            "subfolder": null
        }
    ],
    "correlation_id": "trace-a1b2c3d4e5f6"
}
```

## Function Integration

Use the shared helper `trigger_upload_files_flow`:

```python
from shared import trigger_upload_files_flow, FileUploadItem

result = trigger_upload_files_flow(
    lpo_folder_url="https://...",
    files=[
        FileUploadItem(
            file_name="doc.pdf",
            file_content="base64...",
            subfolder="LPO Documents"
        )
    ],
    correlation_id="trace-123"
)
```
