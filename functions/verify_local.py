
import requests
import json
import time

url = "http://localhost:7071/api/events/process-row"
payload = {
    "source": "smartsheet",
    "sheet_id": 7199466228680580,
    "row_id": 4545537202849668,
    "event_type": "created",
    "action": "created"
}

print(f"Sending request to {url}...")
print(f"Payload: {json.dumps(payload, indent=2)}")

try:
    response = requests.post(url, json=payload, timeout=30)
    print(f"Status Code: {response.status_code}")
    print("Response Body:")
    print(response.text)
except Exception as e:
    print(f"Error: {e}")
