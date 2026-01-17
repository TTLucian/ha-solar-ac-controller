"""Season detection and outdoor temperature helpers for Solar AC Controller."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.util import dt as dt_util

from .const import CONF_OUTSIDE_SENSOR

_LOGGER = logging.getLogger(__name__)


class SeasonManager:
    """Manages season detection, outdoor temperature reading, and temperature bands."""

    def __init__(
        self,
        hass: Any,
        config: dict[str, Any],
        heat_on_below: float,
        heat_off_above: float,
        cool_on_above: float,
        cool_off_below: float,
        band_cold_max: float,
        band_mild_cold_max: float,
        band_mild_hot_max: float,
        enable_auto_season: bool,
    ) -> None:
        """Initialize season manager with temperature thresholds and bands."""
        self.hass = hass
        self.config = config
        self.heat_on_below = heat_on_below
        self.heat_off_above = heat_off_above
        self.cool_on_above = cool_on_above
        self.cool_off_below = cool_off_below
        self.band_cold_max = band_cold_max
        self.band_mild_cold_max = band_mild_cold_max
        self.band_mild_hot_max = band_mild_hot_max
        self.enable_auto_season = enable_auto_season
        self.last_season_mode: str | None = None

    def read_outside_temp(self) -> float | None:
        """Read outside temperature if sensor configured."""
        sensor_id = self.config.get(CONF_OUTSIDE_SENSOR)
        if not sensor_id:
            return None
        st = self.hass.states.get(sensor_id)
        if not st or st.state in ("unknown", "unavailable", ""):
            return None
        try:
            return float(st.state)
        except (TypeError, ValueError):
            return None

    def select_outside_band(self, temp: float | None) -> str | None:
        """Return band name based on outside temperature."""
        if temp is None:
            return None
        if temp < self.band_cold_max:
            return "cold"
        if temp < self.band_mild_cold_max:
            return "mild_cold"
        if temp < self.band_mild_hot_max:
            return "mild_hot"
        return "hot"

    def update_season_mode(self, temp: float | None) -> str | None:
        """Determine season mode using hysteresis when auto-season is enabled."""
        if not self.enable_auto_season or temp is None:
            return self.last_season_mode

        last = self.last_season_mode

        # Maintain hysteresis per last mode
        if last == "heat":
            if temp >= self.heat_off_above:
                self.last_season_mode = "neutral"
                return "neutral"
            return "heat"
        if last == "cool":
            if temp <= self.cool_off_below:
                self.last_season_mode = "neutral"
                return "neutral"
            return "cool"

        # Neutral or undefined: decide based on thresholds
        if temp <= self.heat_on_below:
            self.last_season_mode = "heat"
            return "heat"
        if temp >= self.cool_on_above:
            self.last_season_mode = "cool"
            return "cool"
        self.last_season_mode = "neutral"
        return "neutral"
