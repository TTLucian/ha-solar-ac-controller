## Installation

1. Install this repo as a custom repository via HACS (or copy `custom_components/solar_ac_controller` into your `custom_components` folder).
2. Restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration** and search for **Solar AC Controller**.
4. Configure:
   - Solar power sensor
   - Grid power sensor
   - AC power sensor
   - AC main switch
   - Climate zones
5. (Recommended) Include the provided package:

   ```yaml
   homeassistant:
     packages:
       solar_ac_controller: !include solar_ac_package.yaml
