"""Fetch a specific row from Smartsheet."""
import os
import sys
import json

# Set environment variables
os.environ["SMARTSHEET_API_KEY"] = "9FCvJqnE9l0hQW5FaO1utmihj5Xc3sJs7RGBY"
os.environ["SMARTSHEET_BASE_URL"] = "https://api.smartsheet.eu/2.0"
os.environ["SMARTSHEET_WORKSPACE_ID"] = "4909940948133763"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shared import get_smartsheet_client

client = get_smartsheet_client()

sheet_id = 5094137684690820
row_id = 407615812798340

print(f"Fetching row {row_id} from sheet {sheet_id}...")
row = client.get_row(sheet_id, row_id)

print("\nRow data (column_id -> value):")
print(json.dumps(row, indent=2, default=str))

# Also fetch attachments
print("\nFetching attachments...")
attachments = client.get_row_attachments(sheet_id, row_id)
print(f"Attachments ({len(attachments)}):")
print(json.dumps(attachments, indent=2, default=str))
