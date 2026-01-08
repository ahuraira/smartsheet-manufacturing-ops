# 🤝 Contributing Guide

> **Version:** 1.0.0 | **Last Updated:** 2026-01-08

Thank you for your interest in contributing to the Ducts Manufacturing Inventory Management System! This guide will help you get started.

---

## Table of Contents

1. [Code of Conduct](#code-of-conduct)
2. [Getting Started](#getting-started)
3. [Development Workflow](#development-workflow)
4. [Pull Request Process](#pull-request-process)
5. [Coding Standards](#coding-standards)
6. [Documentation Standards](#documentation-standards)
7. [Testing Requirements](#testing-requirements)

---

## Code of Conduct

### Our Standards

- Be respectful and inclusive
- Provide constructive feedback
- Accept criticism gracefully
- Focus on what's best for the project
- Show empathy towards others

---

## Getting Started

### Prerequisites

1. Read the [Architecture Overview](./architecture_overview.md)
2. Set up your [development environment](./setup_guide.md)
3. Familiarize yourself with the codebase
4. Check [existing issues](../../issues) for something to work on

### First Contribution?

Look for issues labeled:
- `good first issue` - Simple, well-defined tasks
- `help wanted` - Need community help
- `documentation` - Documentation improvements

---

## Development Workflow

### 1. Fork and Clone

```bash
# Fork the repository on GitHub, then:
git clone https://github.com/YOUR_USERNAME/ducts-manufacturing-inventory.git
cd ducts-manufacturing-inventory
git remote add upstream https://github.com/ORIGINAL/ducts-manufacturing-inventory.git
```

### 2. Create a Branch

```bash
# Get latest changes
git fetch upstream
git checkout main
git merge upstream/main

# Create feature branch
git checkout -b feature/your-feature-name
```

**Branch naming conventions:**

| Type | Pattern | Example |
|------|---------|---------|
| Feature | `feature/description` | `feature/add-allocation-api` |
| Bug fix | `fix/description` | `fix/duplicate-tag-handling` |
| Documentation | `docs/description` | `docs/update-api-reference` |
| Refactor | `refactor/description` | `refactor/smartsheet-client` |

### 3. Make Changes

- Write code following [coding standards](#coding-standards)
- Write tests for new functionality
- Update documentation as needed

### 4. Commit Changes

```bash
# Stage changes
git add .

# Commit with descriptive message
git commit -m "feat: add allocation endpoint with validation"
```

**Commit message format:**

```
<type>: <short description>

[optional body]

[optional footer]
```

| Type | Purpose |
|------|---------|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation |
| `refactor` | Code refactoring |
| `test` | Test additions/changes |
| `chore` | Maintenance tasks |

### 5. Push and Create PR

```bash
git push origin feature/your-feature-name
```

Then create a Pull Request on GitHub.

---

## Pull Request Process

### PR Template

```markdown
## Description
Brief description of changes.

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Documentation update
- [ ] Refactoring

## Testing
- [ ] Unit tests pass
- [ ] Integration tests pass
- [ ] Manual testing completed

## Checklist
- [ ] Code follows project style
- [ ] Self-reviewed code
- [ ] Updated documentation
- [ ] Added tests for new features
- [ ] All tests passing
```

### Review Process

1. **Automated checks** must pass:
   - Linting
   - Unit tests
   - Integration tests
   - Coverage threshold

2. **Code review** by at least one maintainer:
   - Code quality
   - Architecture alignment
   - Test coverage
   - Documentation

3. **Approval** from maintainer before merge

### Merge Requirements

- ✅ All CI checks passing
- ✅ At least one approval
- ✅ No unresolved comments
- ✅ Up to date with main branch

---

## Coding Standards

### Python Style

We follow [PEP 8](https://pep8.org/) with these additions:

```python
# Maximum line length: 100 characters
# Use type hints
def process_tag(tag_id: str, area: float) -> Dict[str, Any]:
    """Process a tag and return result."""
    pass

# Use docstrings for all public functions
def validate_lpo(lpo_id: str) -> bool:
    """
    Validate that an LPO exists and is active.
    
    Args:
        lpo_id: The LPO identifier to validate
        
    Returns:
        True if valid and active, False otherwise
        
    Raises:
        ValueError: If lpo_id is None or empty
    """
    pass

# Use constants from sheet_config.py
from shared import SheetName, ColumnName

# Good
sheet = SheetName.TAG_REGISTRY.value

# Bad
sheet = "Tag Sheet Registry"  # Don't hardcode
```

### Naming Conventions

| Type | Convention | Example |
|------|------------|---------|
| Functions | `snake_case` | `process_tag_request` |
| Classes | `PascalCase` | `TagIngestRequest` |
| Constants | `UPPER_SNAKE` | `MAX_RETRIES` |
| Variables | `snake_case` | `tag_id` |
| Files | `snake_case.py` | `smartsheet_client.py` |

### Error Handling

```python
# Always use try/except for external calls
try:
    result = client.add_row(sheet, data)
except SmartsheetError as e:
    logger.error(f"[{trace_id}] Smartsheet error: {e}")
    raise

# Create exceptions for business logic failures
if not lpo:
    exception_id = _create_exception(
        client=client,
        trace_id=trace_id,
        reason_code=ReasonCode.LPO_NOT_FOUND,
        severity=ExceptionSeverity.HIGH
    )
    return error_response(422, "LPO not found", exception_id)
```

### Logging

```python
import logging
logger = logging.getLogger(__name__)

# Always include trace_id
logger.info(f"[{trace_id}] Processing tag: {tag_id}")
logger.warning(f"[{trace_id}] LPO on hold: {lpo_id}")
logger.error(f"[{trace_id}] Failed to create tag: {e}")
```

---

## Documentation Standards

### Code Documentation

```python
def create_tag(
    client: SmartsheetClient,
    request: TagIngestRequest,
    trace_id: str
) -> Dict[str, Any]:
    """
    Create a new tag record in the Tag Sheet Registry.
    
    This function validates the request, checks for duplicates,
    generates a unique tag ID, and creates the record.
    
    Args:
        client: Smartsheet client instance
        request: Validated tag ingest request
        trace_id: Correlation ID for logging
        
    Returns:
        Dictionary containing:
            - tag_id: Generated tag identifier
            - row_id: Smartsheet row ID
            - status: Creation status
            
    Raises:
        SmartsheetError: If unable to create the row
        ValueError: If request validation fails
        
    Example:
        >>> request = TagIngestRequest(...)
        >>> result = create_tag(client, request, "trace-123")
        >>> print(result['tag_id'])
        'TAG-0001'
    """
    pass
```

### README/Doc Updates

When adding features, update:
1. [API Reference](./reference/api_reference.md) for new endpoints
2. [Data Dictionary](./reference/data_dictionary.md) for new models
3. [Architecture Overview](./architecture_overview.md) if architecture changes
4. Main README.md if user-facing features change

---

## Testing Requirements

### Coverage Requirements

| Component | Minimum Coverage |
|-----------|------------------|
| New functions | 80% |
| Core shared modules | 90% |
| Bug fixes | 100% of fixed path |

### Required Tests

For new features:
- [ ] Unit tests for core logic
- [ ] Integration tests for component interaction
- [ ] Edge case tests
- [ ] Error handling tests

For bug fixes:
- [ ] Regression test that would have caught the bug
- [ ] Related edge case tests

### Running Tests

```bash
# Run all tests
cd functions
pytest

# Run with coverage
pytest --cov=shared --cov=fn_ingest_tag

# Run specific category
pytest -m unit
pytest -m integration
```

See [Testing Guide](./howto/testing.md) for details.

---

## Questions?

- Check existing [documentation](./index.md)
- Search [closed issues](../../issues?q=is%3Aissue+is%3Aclosed)
- Ask in team channel
- Open a new issue for discussion

---

## Recognition

Contributors are recognized in:
- [CHANGELOG.md](./CHANGELOG.md)
- Release notes
- Project documentation

Thank you for contributing! 🙏

---

<p align="center">
  <a href="./index.md">📚 Documentation Hub →</a>
</p>
