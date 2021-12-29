# HA-LondonTfL

WARNING: still under construction and subject to change often

![Departure details from Canning Town Station Platform 4](https://github.com/morosanmihail/HA-LondonTfL/blob/main/images/example.png?raw=true)

Simple Home Assistant sensor to retrieve departures from Transport for London stations.

Just drop into your `custom_components` folder.

Sensor state is the minutes and seconds until the next train departing from the given station. 
Attributes contain up to `max` departures.

Demo configuration:

```
sensor:
  - platform: london_tfl
    name: London TfL
    stops:
      - line: dlr  # Required
        station: 940GZZDLRVC  # Required
      - line: dlr
        station: 940GZZDLCGT
        platform: Platform 4  # Optional. All platforms by default
        max: 3  # Optional. 3 items by default
```

TODO:
- Add support as a custom HACS repo
- Explain how to get station codes
- Expand with `/Journey/JourneyResults` API
