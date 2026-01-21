# custom_components/solar_ac_controller/config_manager.py
"""Configuration management utilities for Solar AC Controller."""

from typing import Any, Dict, List, Optional

from homeassistant.config_entries import ConfigEntry


class ConfigManager:
    """Manages configuration access with proper fallbacks and type conversion."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize config manager."""
        self.data = config_entry.data
        self.options = config_entry.options
        self._config = {**dict(self.data), **dict(self.options)}

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value with fallback to default."""
        return self._config.get(key, default)

    def get_float(self, key: str, default: float) -> float:
        """Get configuration value as float."""
        try:
            return float(self.options.get(key, self.data.get(key, default)))
        except (TypeError, ValueError):
            return default

    def get_int(self, key: str, default: int) -> int:
        """Get configuration value as int."""
        try:
            return int(self.options.get(key, self.data.get(key, default)))
        except (TypeError, ValueError):
            return default

    def get_bool(self, key: str, default: bool) -> bool:
        """Get configuration value as bool."""
        try:
            return bool(self.options.get(key, self.data.get(key, default)))
        except (TypeError, ValueError):
            return default

    def get_list(self, key: str, default: Optional[List] = None) -> List:
        """Get configuration value as list."""
        if default is None:
            default = []
        value = self.options.get(key, self.data.get(key, default))
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            # Handle comma-separated strings
            return [item.strip() for item in value.split(",") if item.strip()]
        return default

    def get_dict(self, key: str, default: Optional[Dict] = None) -> Dict:
        """Get configuration value as dict."""
        if default is None:
            default = {}
        value = self.options.get(key, self.data.get(key, default))
        return value if isinstance(value, dict) else default

    @property
    def config(self) -> Dict[str, Any]:
        """Get the combined config dict."""
        return self._config.copy()
