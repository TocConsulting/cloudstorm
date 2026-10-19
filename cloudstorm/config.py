"""
Configuration management for cloudstorm.
"""
import copy
from typing import Any

import yaml

from .constants import CONFIG_DIR, CONFIG_FILE, DEFAULT_CONFIG, RESULTS_DIR
from .utils.display import print_error


def ensure_directories():
    """Ensure configuration directories exist."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    """
    Load configuration from file.

    Returns:
        Configuration dictionary.
    """
    ensure_directories()

    if not CONFIG_FILE.exists():
        save_config(copy.deepcopy(DEFAULT_CONFIG))
        return copy.deepcopy(DEFAULT_CONFIG)

    try:
        with open(CONFIG_FILE) as f:
            config = yaml.safe_load(f) or {}

        # Merge with defaults to ensure all keys exist
        merged = copy.deepcopy(DEFAULT_CONFIG)
        _deep_merge(merged, config)
        return merged

    except Exception as e:
        print_error(f"Failed to load config: {e}")
        return copy.deepcopy(DEFAULT_CONFIG)


def save_config(config: dict) -> bool:
    """
    Save configuration to file.

    Args:
        config: Configuration dictionary.

    Returns:
        True if successful.
    """
    ensure_directories()

    try:
        with open(CONFIG_FILE, 'w') as f:
            yaml.dump(config, f, default_flow_style=False)
        return True
    except Exception as e:
        print_error(f"Failed to save config: {e}")
        return False


def get_config_value(key_path: str, default: Any = None) -> Any:
    """
    Get a configuration value by dot-separated path.

    Args:
        key_path: Dot-separated path (e.g., "aws.profile").
        default: Default value if key doesn't exist.

    Returns:
        Configuration value.
    """
    config = load_config()
    keys = key_path.split('.')

    value = config
    for key in keys:
        if isinstance(value, dict) and key in value:
            value = value[key]
        else:
            return default

    return value


def _deep_merge(base: dict, override: dict):
    """Deep merge override into base."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
