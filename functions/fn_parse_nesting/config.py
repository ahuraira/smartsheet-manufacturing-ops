import json
import os
from pathlib import Path
from functools import lru_cache
from typing import Dict, Any

def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """
    Recursively merge two dictionaries.
    Values in 'override' replace those in 'base'.
    """
    result = base.copy()
    for key, value in override.items():
        if isinstance(value, dict) and key in result and isinstance(result[key], dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result

@lru_cache(maxsize=1)
def load_nesting_config() -> Dict[str, Any]:
    """
    Load nesting configuration from JSON file.
    
    In a future phase, this will also fetch runtime overrides from
    Smartsheet's '00a Config' sheet using the pattern:
    
       runtime_config = client.get_config_values(prefix="nesting.")
       config = deep_merge(config, runtime_config)
       
    For now (Phase 1), it returns the file-based config.
    """
    try:
        config_path = Path(__file__).parent / "nesting_config.json"
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        return config
    except Exception as e:
        # Fallback to defaults or re-raise depending on criticality
        # For now, we'll log via print/logging and return an empty dict or known defaults
        # But properly we want to fail fast if config is missing.
        raise RuntimeError(f"Failed to load nesting_config.json: {e}")

def get_exception_assignee(reason_code: str) -> str:
    """Get the assigned email for a given exception reason code."""
    config = load_nesting_config()
    assignments = config.get("exception_assignment", {})
    return assignments.get(reason_code, assignments.get("default", "admin@company.com"))

def get_sla_hours(severity: str) -> int:
    """Get SLA hours for a given severity."""
    config = load_nesting_config()
    slas = config.get("sla_hours", {})
    return slas.get(severity, 24)  # Default to 24 hours if unknown


def get_safe_user_email(user_input: str) -> str:
    """
    Ensure we have a valid email for Smartsheet user fields.
    If input is 'system' or invalid, return default admin email.
    """
    if not user_input or "@" not in user_input or user_input.lower() == "system":
        return get_exception_assignee("default")
    return user_input
