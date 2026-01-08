# 🧪 Testing Guide

> **Document Type:** How-To | **Audience:** Developers | **Last Updated:** 2026-01-08

This guide explains how to write, run, and maintain tests for the Ducts Manufacturing Inventory Management System.

---

## Table of Contents

1. [Test Philosophy](#test-philosophy)
2. [Running Tests](#running-tests)
3. [Test Structure](#test-structure)
4. [Writing Tests](#writing-tests)
5. [Fixtures and Mocks](#fixtures-and-mocks)
6. [Coverage Requirements](#coverage-requirements)
7. [CI/CD Integration](#cicd-integration)

---

## Test Philosophy

### Testing Pyramid

```
    ┌─────────────────────┐
    │     E2E Tests       │  ← Fewer, expensive, comprehensive
    │     (Acceptance)    │
    ├─────────────────────┤
    │  Integration Tests  │  ← Component interactions
    │                     │
    ├─────────────────────┤
    │     Unit Tests      │  ← Many, fast, isolated
    │                     │
    └─────────────────────┘
```

### Principles

| Principle | Description |
|-----------|-------------|
| **Isolated** | Tests don't affect each other |
| **Deterministic** | Same result every run |
| **Fast** | Unit tests < 1 second each |
| **Documented** | Clear purpose in docstring |
| **Maintainable** | Easy to update with code changes |

---

## Running Tests

### Quick Start

```bash
# Navigate to functions directory
cd functions

# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific category
pytest -m unit
pytest -m integration
pytest -m e2e
pytest -m acceptance
```

### Common Commands

| Command | Purpose |
|---------|---------|
| `pytest` | Run all tests |
| `pytest -v` | Verbose output |
| `pytest -x` | Stop on first failure |
| `pytest --lf` | Run last failed tests |
| `pytest -k "test_name"` | Run tests matching name |
| `pytest tests/unit/` | Run tests in directory |
| `pytest --pdb` | Debug on failure |

### Coverage

```bash
# Run with coverage
pytest --cov=shared --cov=fn_ingest_tag

# Generate HTML report
pytest --cov=shared --cov=fn_ingest_tag --cov-report=html

# Open report
open htmlcov/index.html  # macOS
start htmlcov/index.html  # Windows
```

---

## Test Structure

### Directory Layout

```
functions/tests/
├── conftest.py              # Shared fixtures
├── __init__.py              # Test package init
├── unit/                    # Unit tests
│   ├── test_models.py
│   ├── test_helpers.py
│   ├── test_sheet_config.py
│   └── test_id_generator.py
├── integration/             # Integration tests
│   └── test_tag_ingest.py
└── e2e/                     # End-to-end tests
    └── test_acceptance_criteria.py
```

### File Naming

| Pattern | Purpose |
|---------|---------|
| `test_*.py` | Test modules |
| `Test*` | Test classes |
| `test_*` | Test functions |
| `conftest.py` | Shared fixtures |

---

## Writing Tests

### Unit Test Template

```python
import pytest

@pytest.mark.unit
class TestFeatureName:
    """Tests for feature X functionality."""
    
    def test_happy_path(self):
        """Test that X works correctly with valid input."""
        # Arrange
        input_data = {"key": "value"}
        
        # Act
        result = function_under_test(input_data)
        
        # Assert
        assert result.status == "success"
        assert result.value == expected_value
    
    def test_edge_case(self):
        """Test behavior with edge case input."""
        # Arrange
        input_data = {"key": ""}
        
        # Act
        result = function_under_test(input_data)
        
        # Assert
        assert result.status == "error"
    
    def test_raises_on_invalid_input(self):
        """Test that invalid input raises ValueError."""
        # Arrange
        invalid_data = {"key": None}
        
        # Act & Assert
        with pytest.raises(ValueError, match="key cannot be None"):
            function_under_test(invalid_data)
```

### Integration Test Template

```python
import pytest
from unittest.mock import patch

@pytest.mark.integration
class TestComponentInteraction:
    """Tests for component A and B integration."""
    
    def test_full_flow(self, mock_storage, factory, mock_http_request):
        """Test complete flow from request to response."""
        # Arrange - Set up test data
        lpo = factory.create_lpo(sap_reference="SAP-001", status="Active")
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        request_data = factory.create_tag_ingest_request(
            lpo_sap_reference="SAP-001",
            required_area_m2=50.0
        )
        
        # Act - Invoke the function
        with patch('shared.get_smartsheet_client') as mock_client:
            mock_client.return_value = MockSmartsheetClient(mock_storage)
            from fn_ingest_tag import main
            response = main(mock_http_request(request_data))
        
        # Assert - Verify results
        assert response.status_code == 200
        result = json.loads(response.get_body())
        assert result["status"] == "UPLOADED"
        
        # Verify side effects
        tags = mock_storage.find_rows("Tag Sheet Registry", 
                                       "Client Request ID", 
                                       request_data["client_request_id"])
        assert len(tags) == 1
```

### E2E/Acceptance Test Template

```python
import pytest

@pytest.mark.e2e
@pytest.mark.acceptance
class TestAcceptanceCriteria:
    """Tests verifying acceptance criteria from specification."""
    
    def test_acceptance_1_happy_path_unique_file(
        self, mock_storage, factory, mock_http_request
    ):
        """
        ACCEPTANCE CRITERION #1
        ---------------------
        Given: A unique tag file and valid LPO
        When: Tag is uploaded
        Then: Tag is created with status UPLOADED
              User action is logged
              No exception is created
        
        Reference: tag_ingestion_architecture.md Section 10.1
        """
        # Arrange
        lpo = factory.create_lpo(sap_reference="SAP-001", status="Active")
        mock_storage.add_row("01 LPO Master LOG", lpo)
        
        request = factory.create_tag_ingest_request(
            lpo_sap_reference="SAP-001"
        )
        
        # Act
        response = self._invoke_function(mock_storage, request)
        
        # Assert
        assert response.status_code == 200
        data = json.loads(response.get_body())
        assert data["status"] == "UPLOADED"
        assert "tag_id" in data
        
        # Verify user action logged
        actions = mock_storage.find_rows(
            "98 User Action Log", 
            "Action Type", 
            "TAG_CREATED"
        )
        assert len(actions) == 1
```

---

## Fixtures and Mocks

### Available Fixtures

| Fixture | Description | Scope |
|---------|-------------|-------|
| `mock_storage` | In-memory sheet storage | function |
| `mock_client` | Mock Smartsheet client | function |
| `factory` | Test data factory | function |
| `mock_http_request` | HTTP request factory | function |
| `assertions` | Assertion helpers | function |

### Using Mock Storage

```python
def test_with_mock_storage(mock_storage, factory):
    # Add test data
    lpo = factory.create_lpo(sap_reference="SAP-001")
    mock_storage.add_row("01 LPO Master LOG", lpo)
    
    # Query data
    rows = mock_storage.find_rows("01 LPO Master LOG", "SAP Reference", "SAP-001")
    assert len(rows) == 1
    
    # Update data
    mock_storage.update_row("01 LPO Master LOG", rows[0]["row_id"], {"Status": "Closed"})
```

### Using Test Data Factory

```python
def test_with_factory(factory):
    # Create tag request
    request = factory.create_tag_ingest_request(
        lpo_sap_reference="SAP-001",
        required_area_m2=100.0,
        uploaded_by="test@example.com"
    )
    
    # Create LPO
    lpo = factory.create_lpo(
        sap_reference="SAP-001",
        status="Active",
        po_quantity=500.0
    )
    
    # Create tag record
    tag = factory.create_tag_record(
        tag_name="TAG-001",
        status="Draft"
    )
```

### Creating Custom Mocks

```python
from unittest.mock import MagicMock, patch

def test_with_custom_mock():
    # Mock external service
    with patch('requests.get') as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            content=b"file content"
        )
        
        result = compute_file_hash_from_url("https://example.com/file.pdf")
        assert result is not None
        mock_get.assert_called_once()
```

---

## Coverage Requirements

### Targets

| Component | Target Coverage |
|-----------|-----------------|
| `shared/` core modules | ≥90% |
| `fn_ingest_tag/` | ≥85% |
| Exception paths | 100% |
| New code | ≥80% |

### Checking Coverage

```bash
# Generate report
pytest --cov=shared --cov=fn_ingest_tag --cov-report=term-missing

# Output shows uncovered lines:
# Name                  Stmts   Miss  Cover   Missing
# shared/models.py         45      3    93%   42-44
```

### Excluding Coverage

```python
# pragma: no cover - exclude from coverage
def debug_only_function():  # pragma: no cover
    """This is for debugging only."""
    pass
```

---

## Test Categories

### Markers

```python
@pytest.mark.unit         # Fast, isolated unit tests
@pytest.mark.integration  # Component interaction tests
@pytest.mark.e2e          # End-to-end tests
@pytest.mark.acceptance   # Spec acceptance criteria
@pytest.mark.slow         # Tests taking > 5 seconds
```

### Running by Category

```bash
# Unit tests only (fast, run frequently)
pytest -m unit

# Integration tests
pytest -m integration

# Acceptance tests (spec compliance)
pytest -m acceptance

# Skip slow tests
pytest -m "not slow"

# Combine markers
pytest -m "unit and not slow"
```

---

## CI/CD Integration

### GitHub Actions Workflow

```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    
    steps:
    - uses: actions/checkout@v3
    
    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.9'
    
    - name: Install dependencies
      run: |
        cd functions
        pip install -r requirements.txt
        pip install -r requirements-test.txt
    
    - name: Run tests
      run: |
        cd functions
        pytest --cov=shared --cov=fn_ingest_tag --cov-report=xml
    
    - name: Upload coverage
      uses: codecov/codecov-action@v3
      with:
        files: ./functions/coverage.xml
```

### Pre-commit Hook

```bash
# .git/hooks/pre-commit
#!/bin/bash
cd functions
pytest -m unit --tb=short
if [ $? -ne 0 ]; then
    echo "Unit tests failed. Commit aborted."
    exit 1
fi
```

---

## Best Practices

### Do

✅ Write tests alongside code (TDD/BDD)
✅ Use descriptive test names
✅ Test edge cases and error paths
✅ Keep tests isolated and independent
✅ Use fixtures for common setup
✅ Document test purpose in docstrings

### Don't

❌ Test implementation details
❌ Write tests that depend on other tests
❌ Use sleep() in tests (use mocks)
❌ Leave failing tests in main branch
❌ Skip tests without explanation
❌ Hardcode test data inline

---

## Related Documentation

| Document | Description |
|----------|-------------|
| [Test Suite README](../../functions/tests/README.md) | Detailed test suite docs |
| [Troubleshooting](./troubleshooting.md) | Test debugging |
| [Contributing](../CONTRIBUTING.md) | Test requirements for PRs |

---

<p align="center">
  <a href="./add_function.md">➕ Adding New Functions →</a>
</p>
