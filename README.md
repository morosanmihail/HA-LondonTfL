# HA-LondonTfL

Simple Home Assistant sensor to retrieve departures from a single Transport for London station.

Just drop into your `custom_components` folder.

Sensor state is the minutes and seconds until the next train departing from the given station. 
Attributes contain up to `max` departures.

Demo configuration:

```
- platform: london_tfl
  name: DLR from Royal Victoria Platform 2
  line: dlr  # Required
  station: 940GZZDLRVC  # Required
  platform_filter: Platform 2  # Optional. All platforms by default
  max: 3  # Optional. 3 items by default
```

TODO:
- Add support as a custom HACS repo
- Explain how to get station codes
