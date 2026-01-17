"""Season detection and outdoor temperature helpers for Solar AC Controller."""
from __future__ import annotations

import logging
from typing import Any
from collections import deque

from homeassistant.util import dt as dt_util

from .const import CONF_OUTSIDE_SENSOR

_LOGGER = logging.getLogger(__name__)

# 7 days in seconds (for rolling mean calculation)
_SEVEN_DAYS_SECONDS = 7 * 24 * 60 * 60


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
        
        # Disable auto-season if outside sensor not configured
        sensor_id = config.get(CONF_OUTSIDE_SENSOR)
        if not sensor_id or not sensor_id.strip():
            self.enable_auto_season = False
        else:
            self.enable_auto_season = enable_auto_season
        
        self.heat_on_below = heat_on_below
        self.heat_off_above = heat_off_above
        self.cool_on_above = cool_on_above
        self.cool_off_below = cool_off_below
        self.band_cold_max = band_cold_max
        self.band_mild_cold_max = band_mild_cold_max
        self.band_mild_hot_max = band_mild_hot_max
        self.last_season_mode: str | None = None
        
        # 7-day rolling mean: store (timestamp, temp) tuples
        self.temp_history: deque[tuple[float, float]] = deque(maxlen=10080)  # ~1 sample/minute for 7 days
        self.rolling_mean: float | None = None

    def read_outside_temp(self) -> float | None:
        """Read outside temperature if sensor configured and update rolling mean."""
        sensor_id = self.config.get(CONF_OUTSIDE_SENSOR)
        if not sensor_id:
            return None
        st = self.hass.states.get(sensor_id)
        if not st or st.state in ("unknown", "unavailable", ""):
            return None
        try:
            temp = float(st.state)
            # Record temperature with timestamp
            now_ts = dt_util.utcnow().timestamp()
            self.temp_history.append((now_ts, temp))
            # Update rolling mean
            self._update_rolling_mean(now_ts)
            return temp
        except (TypeError, ValueError):
            return None

    def _update_rolling_mean(self, now_ts: float) -> None:
        """Compute 7-day rolling mean, discarding samples older than 7 days."""
        if not self.temp_history:
            self.rolling_mean = None
            return
        
        cutoff_ts = now_ts - _SEVEN_DAYS_SECONDS
        temps = [temp for ts, temp in self.temp_history if ts >= cutoff_ts]
        
        if temps:
            self.rolling_mean = sum(temps) / len(temps)
        else:
            self.rolling_mean = None

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
        """
        Determine season mode using hysteresis when auto-season is enabled.
        Uses 7-day rolling mean if available; falls back to instantaneous temp if history is short.
        """
        if not self.enable_auto_season or temp is None:
            return self.last_season_mode

        # Use rolling mean if available (>= 1 sample); fall back to instantaneous temp
        decision_temp = self.rolling_mean if self.rolling_mean is not None else temp

        last = self.last_season_mode

        # Maintain hysteresis per last mode
        if last == "heat":
            if decision_temp >= self.heat_off_above:
                self.last_season_mode = "neutral"
                return "neutral"
            return "heat"
        if last == "cool":
            if decision_temp <= self.cool_off_below:
                self.last_season_mode = "neutral"
                return "neutral"
            return "cool"

        # Neutral or undefined: decide based on thresholds
        if decision_temp <= self.heat_on_below:
            self.last_season_mode = "heat"
            return "heat"
        if decision_temp >= self.cool_on_above:
            self.last_season_mode = "cool"
            return "cool"
        self.last_season_mode = "neutral"
        return "neutral"
