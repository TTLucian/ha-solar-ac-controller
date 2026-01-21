# custom_components/solar_ac_controller/zone_config_parser.py
"""Zone configuration parsing utilities."""

from typing import Dict, List

from homeassistant.config_entries import ConfigEntry


class ZoneConfigParser:
    """Parses zone-related configuration from config entries."""

    @staticmethod
    def parse_temp_sensors(
        config_entry: ConfigEntry, zones: List[str]
    ) -> Dict[str, str]:
        """Parse zone temperature sensor mappings."""
        zone_temp_sensors_list = (
            config_entry.options.get(
                "zone_temp_sensors", config_entry.data.get("zone_temp_sensors", [])
            )
            or []
        )

        zone_temp_sensors = {}
        for idx, zone_id in enumerate(zones):
            if idx < len(zone_temp_sensors_list) and zone_temp_sensors_list[idx]:
                zone_temp_sensors[zone_id] = zone_temp_sensors_list[idx]

        return zone_temp_sensors

    @staticmethod
    def parse_manual_power(
        config_entry: ConfigEntry, zones: List[str]
    ) -> Dict[str, float]:
        """Parse zone manual power mappings."""
        raw_manual = config_entry.options.get(
            "zone_manual_power", config_entry.data.get("zone_manual_power", [])
        )

        zone_manual_power = {}

        if isinstance(raw_manual, str):
            parts = [p.strip() for p in raw_manual.split(",") if p.strip()]
            # If all parts are numbers, map by index to zones
            if all(part.replace(".", "", 1).isdigit() for part in parts):
                for idx, val in enumerate(parts):
                    if idx < len(zones):
                        try:
                            zone_manual_power[zones[idx]] = float(val)
                        except Exception:
                            continue
            else:
                # Legacy: zone_id:power
                for part in parts:
                    if ":" in part:
                        zone, val = part.split(":", 1)
                        try:
                            zone_manual_power[zone.strip()] = float(val)
                        except Exception:
                            continue
        elif isinstance(raw_manual, (list, tuple)):
            # If all items are numbers, map by index
            if all(
                isinstance(item, (int, float))
                or (isinstance(item, str) and item.replace(".", "", 1).isdigit())
                for item in raw_manual
            ):
                for idx, val in enumerate(raw_manual):
                    if idx < len(zones):
                        try:
                            zone_manual_power[zones[idx]] = float(val)
                        except Exception:
                            continue
            else:
                for item in list(raw_manual):
                    if isinstance(item, str) and ":" in item:
                        zone, val = item.split(":", 1)
                        try:
                            zone_manual_power[zone.strip()] = float(val)
                        except Exception:
                            continue

        return zone_manual_power
