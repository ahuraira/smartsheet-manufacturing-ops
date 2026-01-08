# Test Suite for Ducts Manufacturing Inventory Management

This directory contains a comprehensive test suite designed to ensure the system meets **SOTA (State-of-the-Art) quality standards** as specified in the architecture documentation.

## 📁 Test Structure

```
tests/
├── conftest.py              # Shared fixtures, mock clients, factories
├── unit/                    # Unit tests for individual modules
│   ├── test_models.py       # Pydantic model validation tests
│   ├── test_helpers.py      # Utility function tests
│   ├── test_sheet_config.py # Configuration constant tests
│   └── test_id_generator.py # Sequence-based ID generation tests
├── integration/             # Integration tests
│   └── test_tag_ingest.py   # Tag ingestion flow tests
└── e2e/                     # End-to-end acceptance tests
    └── test_acceptance_criteria.py  # Specification compliance tests
```

## 🧪 Running Tests

### Prerequisites

```bash
# Install test dependencies
pip install pytest pytest-cov pytest-xdist

# Make sure you're in the functions directory
cd functions
```

### Run All Tests

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run with coverage report
pytest --cov=shared --cov=fn_ingest_tag --cov-report=html
```

### Run Specific Test Categories

```bash
# Unit tests only
pytest -m unit

# Integration tests only
pytest -m integration

# End-to-end tests only
pytest -m e2e

# Acceptance criteria tests (spec compliance)
pytest -m acceptance
```

### Run Specific Test Files

```bash
# Run model tests
pytest tests/unit/test_models.py

# Run a specific test
pytest tests/e2e/test_acceptance_criteria.py::TestAcceptanceCriteria::test_acceptance_1_happy_path_unique_file
```

## ✅ Acceptance Criteria Coverage

Based on **tag_ingestion_architecture.md Section 10** and **architecture_specification.md Section 11**:

| # | Criterion | Test File | Status |
|---|-----------|-----------|--------|
| 1 | Happy path - UPLOADED | `test_acceptance_criteria.py` | ✅ |
| 2 | Duplicate client_request_id - idempotency | `test_acceptance_criteria.py` | ✅ |
| 3 | Duplicate file_hash - 409 DUPLICATE | `test_acceptance_criteria.py` | ✅ |
| 4 | LPO on hold - 422 BLOCKED | `test_acceptance_criteria.py` | ✅ |
| 5 | PO overcommit - INSUFFICIENT_PO_BALANCE | `test_acceptance_criteria.py` | ✅ |
| 6 | Idempotency under retry - processes once | `test_acceptance_criteria.py` | ✅ |
| 7 | End-to-end trace_id consistency | `test_acceptance_criteria.py` | ✅ |

## 🎯 Quality Standards

### Code Coverage Target
- **≥90%** line coverage for core business logic (`fn_ingest_tag`, `shared/`)
- **100%** coverage for exception handling paths

### Test Quality Requirements
- Each test must be **independent** (no shared state between tests)
- Each test must be **deterministic** (same result every run)
- Each test must be **fast** (< 1 second for unit tests)
- Each test must have a **clear purpose** (documented in docstring)

## 🔧 Mock Infrastructure

The test suite uses a **mock Smartsheet client** (`MockSmartsheetClient`) that provides:
- In-memory storage simulating all sheets
- Pre-initialized sequence counters
- Full CRUD operations (add_row, find_rows, update_row)
- Proper column/row structure matching production

### Key Fixtures

| Fixture | Description |
|---------|-------------|
| `mock_storage` | In-memory MockSmartsheetStorage instance |
| `mock_client` | MockSmartsheetClient with storage |
| `factory` | TestDataFactory for creating test data |
| `mock_http_request` | Factory for Azure Functions HttpRequest mocks |
| `assertions` | Helper methods for common assertions |

## 📋 Adding New Tests

### Unit Test Template

```python
@pytest.mark.unit
def test_feature_description(self):
    """Test that [feature] does [expected behavior]."""
    # Arrange
    input_data = create_test_data()
    
    # Act
    result = function_under_test(input_data)
    
    # Assert
    assert result == expected_value
```

### Integration Test Template

```python
@pytest.mark.integration
def test_component_interaction(self, mock_storage, factory, mock_http_request):
    """Test that [components] interact correctly when [scenario]."""
    # Arrange: Set up test data in mock storage
    lpo = factory.create_lpo(sap_reference="TEST-001", status="Active")
    mock_storage.add_row("01 LPO Master LOG", lpo)
    
    # Act: Invoke the system
    with patch('fn_ingest_tag.get_smartsheet_client', return_value=MockSmartsheetClient(mock_storage)):
        response = main(mock_http_request(request_data))
    
    # Assert: Verify results across components
    assert response.status_code == 200
    tags = mock_storage.find_rows("Tag Sheet Registry", "Client Request ID", client_request_id)
    assert len(tags) == 1
```

## 🚀 CI/CD Integration

Add to your GitHub Actions workflow:

```yaml
- name: Run Tests
  run: |
    cd functions
    pip install -r requirements.txt
    pip install pytest pytest-cov
    pytest --cov=shared --cov=fn_ingest_tag --cov-report=xml
    
- name: Upload Coverage
  uses: codecov/codecov-action@v3
  with:
    files: ./functions/coverage.xml
```

## 📊 Test Metrics

To generate a coverage report:

```bash
pytest --cov=shared --cov=fn_ingest_tag --cov-report=html
# Open htmlcov/index.html in a browser
```

The report shows:
- Line coverage percentage
- Branch coverage
- Missing lines highlighted
- Per-file breakdown
