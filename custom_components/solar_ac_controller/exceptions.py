# custom_components/solar_ac_controller/exceptions.py
"""Custom exceptions for Solar AC Controller."""


class SolarACError(Exception):
    """Base exception for Solar AC Controller."""


class SensorUnavailableError(SolarACError):
    """Raised when a required sensor is unavailable."""


class SensorInvalidError(SolarACError):
    """Raised when a sensor value is invalid."""


class ConfigurationError(SolarACError):
    """Raised when configuration is invalid."""


class StorageError(SolarACError):
    """Raised when storage operations fail."""
