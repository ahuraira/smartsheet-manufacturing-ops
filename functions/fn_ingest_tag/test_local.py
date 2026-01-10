"""
Test script for fn_ingest_tag Azure Function.
Run this locally to test the function before deploying.
"""

import json
import os
import sys

# Add functions directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

# Load environment from project root
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env'))


def test_tag_ingest():
    """Test the tag ingestion flow."""
    from shared import get_smartsheet_client, generate_trace_id, Sheet, Column
    
    print("=" * 60)
    print("Testing Tag Ingestion Function")
    print("=" * 60)
    
    # Initialize client
    print("\n1. Initializing Smartsheet client...")
    try:
        client = get_smartsheet_client()
        print("   ✓ Client initialized")
    except Exception as e:
        print(f"   ✗ Failed: {e}")
        return
    
    # Test LPO lookup
    print("\n2. Testing LPO lookup...")
    try:
        # Try to find an existing LPO
        lpo = client.find_row(Sheet.LPO_MASTER, Column.LPO_MASTER.LPO_STATUS, "Active")
        if lpo:
            print(f"   ✓ Found LPO: {lpo.get(Column.LPO_MASTER.CUSTOMER_LPO_REF)}")
            print(f"     - SAP Ref: {lpo.get(Column.LPO_MASTER.SAP_REFERENCE)}")
            print(f"     - Status: {lpo.get(Column.LPO_MASTER.LPO_STATUS)}")
            print(f"     - PO Qty: {lpo.get(Column.LPO_MASTER.PO_QUANTITY_SQM)}")
        else:
            print("   ⚠ No active LPO found (expected for empty dev)")
    except Exception as e:
        print(f"   ✗ Failed: {e}")
    
    # Test tag duplicate check
    print("\n3. Testing idempotency check...")
    try:
        test_request_id = "test-request-12345"
        existing = client.find_row(
            Sheet.TAG_REGISTRY, 
            Column.TAG_REGISTRY.CLIENT_REQUEST_ID, 
            test_request_id
        )
        if existing:
            # Try to get tag name using logical name, then physical name fallback if needed by accessing dict
            tag_name = existing.get(Column.TAG_REGISTRY.TAG_NAME)
            print(f"   → Found existing: {tag_name}")
        else:
            print("   ✓ No duplicate found (correct)")
    except Exception as e:
        print(f"   ✗ Failed: {e}")
    
    # Test creating a tag record
    print("\n4. Testing tag creation (dry run)...")
    trace_id = generate_trace_id()
    print(f"   Generated trace_id: {trace_id}")
    
    test_tag_data = {
        "Tag Sheet Name/ Rev": f"TEST-TAG-{trace_id[:8]}",
        "Status": "Draft",
        "Client Request ID": f"test-{trace_id}",
        "Remarks": f"Test tag - Trace: {trace_id}"
    }
    print(f"   Tag data prepared: {json.dumps(test_tag_data, indent=2)}")
    print("   (Not actually creating to avoid test pollution)")
    
    print("\n" + "=" * 60)
    print("Test complete!")
    print("=" * 60)
    
    return True


def test_api_call_simulation():
    """Simulate the full API request/response flow."""
    print("\n\nSimulating API Request")
    print("-" * 40)
    
    request_payload = {
        "client_request_id": "uuid-test-001",
        "lpo_sap_reference": "PTE-185",
        "required_area_m2": 50.5,
        "requested_delivery_date": "2026-02-01",
        "uploaded_by": "test.user@company.com",
        "tag_name": "TAG-TEST-001-Rev1",
        "metadata": {"notes": "Test upload"}
    }
    
    print(f"Request:\n{json.dumps(request_payload, indent=2)}")
    
    print("\nExpected Response (success):")
    success_response = {
        "status": "UPLOADED",
        "tag_id": "TAG-20260107-XXXX",
        "file_hash": None,
        "trace_id": "trace-abc123def456",
        "message": "Tag uploaded successfully"
    }
    print(json.dumps(success_response, indent=2))
    
    print("\nExpected Response (duplicate):")
    dup_response = {
        "status": "DUPLICATE",
        "existing_tag_id": "TAG-20260106-0001",
        "exception_id": "EX-20260107-ABC123",
        "trace_id": "trace-abc123def456",
        "message": "This file has already been uploaded"
    }
    print(json.dumps(dup_response, indent=2))


if __name__ == "__main__":
    test_tag_ingest()
    test_api_call_simulation()
