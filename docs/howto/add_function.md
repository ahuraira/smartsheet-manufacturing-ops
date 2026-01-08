# ➕ Adding a New Azure Function

> **Document Type:** How-To | **Audience:** Developers | **Last Updated:** 2026-01-08

This guide walks you through adding a new Azure Function to the Ducts Manufacturing Inventory Management System.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Step-by-Step Guide](#step-by-step-guide)
3. [Function Template](#function-template)
4. [Testing Your Function](#testing-your-function)
5. [Documentation Requirements](#documentation-requirements)
6. [Checklist](#checklist)

---

## Prerequisites

Before starting, ensure you have:

- [ ] Development environment set up ([Setup Guide](../setup_guide.md))
- [ ] Understanding of the architecture ([Architecture Overview](../architecture_overview.md))
- [ ] Familiarity with existing patterns (review `fn_ingest_tag`)
- [ ] Clear requirements for the new function

---

## Step-by-Step Guide

### Step 1: Create Function Directory

```bash
cd functions

# Create function directory
mkdir fn_your_function_name
```

### Step 2: Create function.json

Create `functions/fn_your_function_name/function.json`:

```json
{
  "scriptFile": "__init__.py",
  "bindings": [
    {
      "authLevel": "function",
      "type": "httpTrigger",
      "direction": "in",
      "name": "req",
      "methods": ["post"],
      "route": "your/endpoint/path"
    },
    {
      "type": "http",
      "direction": "out",
      "name": "$return"
    }
  ]
}
```

**Key settings:**

| Setting | Options | Description |
|---------|---------|-------------|
| `authLevel` | `anonymous`, `function`, `admin` | Authentication requirement |
| `methods` | `["get", "post", "put", "delete"]` | Allowed HTTP methods |
| `route` | Custom path | URL path after `/api/` |

### Step 3: Create the Function Code

Create `functions/fn_your_function_name/__init__.py`:

```python
"""
fn_your_function_name: Brief description

Detailed description of what this function does.

Endpoint: POST /api/your/endpoint/path
"""

import logging
import json
import azure.functions as func
from datetime import datetime
from typing import Optional

# Add parent to path for shared imports
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import (
    # Config
    Sheet,
    Column,
    # Models - import what you need
    # Client
    get_smartsheet_client,
    # Helpers
    generate_trace_id,
)

logger = logging.getLogger(__name__)


def main(req: func.HttpRequest) -> func.HttpResponse:
    """
    Main entry point for [function description].
    
    Flow:
    1. Parse and validate request
    2. [Step 2]
    3. [Step 3]
    4. Return response
    
    Args:
        req: Azure Functions HTTP request
        
    Returns:
        HTTP response with JSON body
    """
    trace_id = generate_trace_id()
    logger.info(f"[{trace_id}] Function invoked")
    
    try:
        # 1. Parse request
        request_data = req.get_json()
        # Add validation here
        
        # 2. Get Smartsheet client
        client = get_smartsheet_client()
        
        # 3. Business logic here
        # ...
        
        # 4. Return success
        return func.HttpResponse(
            json.dumps({
                "status": "SUCCESS",
                "trace_id": trace_id,
                "message": "Operation completed successfully"
            }),
            status_code=200,
            mimetype="application/json"
        )
    
    except ValueError as e:
        logger.error(f"[{trace_id}] Validation error: {e}")
        return func.HttpResponse(
            json.dumps({
                "status": "ERROR",
                "trace_id": trace_id,
                "message": f"Validation error: {str(e)}"
            }),
            status_code=400,
            mimetype="application/json"
        )
    
    except Exception as e:
        logger.exception(f"[{trace_id}] Unexpected error: {e}")
        return func.HttpResponse(
            json.dumps({
                "status": "ERROR",
                "trace_id": trace_id,
                "message": f"Internal error: {str(e)}"
            }),
            status_code=500,
            mimetype="application/json"
        )
```

### Step 4: Add Pydantic Request Model (if needed)

Add to `functions/shared/models.py`:

```python
class YourFunctionRequest(BaseModel):
    """Request payload for your function API."""
    client_request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    required_field: str
    optional_field: Optional[str] = None
```

Update `functions/shared/__init__.py` to export the new model.

### Step 5: Create Unit Tests

Create `functions/tests/unit/test_your_function.py`:

```python
"""Unit tests for fn_your_function_name."""

import pytest
from datetime import datetime

@pytest.mark.unit
class TestYourFunction:
    """Tests for your function logic."""
    
    def test_happy_path(self):
        """Test successful operation."""
        # Arrange
        # Act
        # Assert
        pass
    
    def test_validation_error(self):
        """Test validation failure."""
        pass
    
    def test_error_handling(self):
        """Test error handling."""
        pass
```

### Step 6: Create Integration Tests

Create `functions/tests/integration/test_your_function_flow.py`:

```python
"""Integration tests for fn_your_function_name flows."""

import pytest
import json
from unittest.mock import patch

@pytest.mark.integration
class TestYourFunctionIntegration:
    """Integration tests for your function."""
    
    def test_full_flow(self, mock_storage, factory, mock_http_request):
        """Test complete flow from request to response."""
        # Setup
        # Execute
        # Verify
        pass
```

### Step 7: Update Documentation

1. Add to [API Reference](../reference/api_reference.md)
2. Update [Architecture Overview](../architecture_overview.md) if needed
3. Add to function inventory in specs

---

## Function Template

Here's a complete template for common patterns:

### CRUD Function Template

```python
"""
fn_create_entity: Create a new entity record

Endpoint: POST /api/entities/create
"""

import logging
import json
import azure.functions as func
from datetime import datetime

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import (
    Sheet,
    Column,
    get_smartsheet_client,
    generate_trace_id,
    format_datetime_for_smartsheet,
    ActionType,
)

logger = logging.getLogger(__name__)


def main(req: func.HttpRequest) -> func.HttpResponse:
    """Create a new entity."""
    trace_id = generate_trace_id()
    logger.info(f"[{trace_id}] Create entity request received")
    
    try:
        # Parse and validate
        data = req.get_json()
        _validate_request(data)
        
        # Idempotency check
        client = get_smartsheet_client()
        existing = client.find_row(
            Sheet.YOUR_SHEET,
            Column.YOUR_SHEET.CLIENT_REQUEST_ID,
            data.get('client_request_id')
        )
        if existing:
            return _already_processed_response(existing, trace_id)
        
        # Business logic
        entity_id = _generate_id(client)
        entity_data = _build_entity_data(data, entity_id, trace_id)
        
        # Create record
        created = client.add_row(Sheet.YOUR_SHEET, entity_data)
        
        # Log action
        _log_user_action(client, data.get('user'), entity_id, trace_id)
        
        # Return success
        return func.HttpResponse(
            json.dumps({
                "status": "CREATED",
                "entity_id": entity_id,
                "trace_id": trace_id
            }),
            status_code=200,
            mimetype="application/json"
        )
        
    except ValueError as e:
        return _error_response(400, str(e), trace_id)
    except Exception as e:
        logger.exception(f"[{trace_id}] Error: {e}")
        return _error_response(500, str(e), trace_id)


def _validate_request(data: dict):
    """Validate request data."""
    required_fields = ['field1', 'field2']
    for field in required_fields:
        if not data.get(field):
            raise ValueError(f"{field} is required")


def _error_response(status: int, message: str, trace_id: str):
    """Build error response."""
    return func.HttpResponse(
        json.dumps({"status": "ERROR", "message": message, "trace_id": trace_id}),
        status_code=status,
        mimetype="application/json"
    )
```

---

## Testing Your Function

### Local Testing

```bash
# Start function app
cd functions
func start

# Test endpoint
curl -X POST http://localhost:7071/api/your/endpoint \
  -H "Content-Type: application/json" \
  -d '{"field1": "value1", "field2": "value2"}'
```

### Run Unit Tests

```bash
cd functions
pytest tests/unit/test_your_function.py -v
```

### Run Integration Tests

```bash
pytest tests/integration/test_your_function_flow.py -v
```

---

## Documentation Requirements

Every new function must have:

1. **Docstring** in the main function explaining:
   - Purpose
   - Flow steps
   - Parameters
   - Return values

2. **API documentation** in `docs/reference/api_reference.md`:
   - Endpoint
   - Request/Response schemas
   - Examples

3. **Test coverage** ≥80%

---

## Checklist

### Before Submitting PR

- [ ] Function directory created with proper structure
- [ ] `function.json` configured correctly
- [ ] `__init__.py` with main function
- [ ] Request model in `shared/models.py` (if needed)
- [ ] Logical Names defined in `shared/logical_names.py`
- [ ] Exports updated in `shared/__init__.py`
- [ ] Unit tests written and passing
- [ ] Integration tests written and passing
- [ ] Test coverage ≥80%
- [ ] Docstrings complete
- [ ] API reference updated
- [ ] Local testing verified

### Code Quality

- [ ] Follows existing patterns
- [ ] Uses shared utilities
- [ ] Proper error handling
- [ ] Logging with trace_id
- [ ] Idempotency implemented
- [ ] User actions logged

---

## Related Documentation

| Document | Description |
|----------|-------------|
| [Architecture Overview](../architecture_overview.md) | System design |
| [API Reference](../reference/api_reference.md) | API patterns |
| [Testing Guide](./testing.md) | Test requirements |
| [fn_ingest_tag](../../functions/fn_ingest_tag/__init__.py) | Reference implementation |

---

<p align="center">
  <a href="./deployment.md">🚀 Deployment Guide →</a>
</p>
