# HA-LondonTfL - Home Assistant London TfL Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)

![Example of HASL Card](https://github.com/morosanmihail/HA-LondonTfL/blob/main/images/hasl_card.png?raw=true)

## Introduction

Simple Home Assistant sensor to retrieve departures from Transport for London stations.
Each station creates its own sensor.

## Installation

Easiest method is to get it via HACS! If you're unfamiliar with HACS, go [here](https://hacs.xyz/docs/setup/prerequisites).
Should be easily found under Integrations - `London TfL`.

Alternatively, you can manually drop it into your `custom_components` folder.

## Setup

You can add integration via the Integrations menu by searching for `London TfL`.
It will auto-populate the line list, then auto-populate the station list with all stations on that line.
It will allow you to add as many stations as needed.
The expected format for the platform filter is to use the full name of the platform (most often similar to `Platform 3`).

Sensor state is the next train departure time from the given station and platform (if set).
Attributes contain up to `max` departures.

Sensor name will change to the name of the first departure's destination station.
If you do not want this behaviour, you can change the name of the sensor manually.

![Departure details from Canary Wharf Jubilee station](https://github.com/morosanmihail/HA-LondonTfL/blob/main/images/example_2.png?raw=true)

### Alternate setup

You can still manually configure the integration, but it's not recommended.
Check https://github.com/morosanmihail/HA-LondonTfL/wiki/Manual-setup if you insist.

## Viewing

Check out https://github.com/morosanmihail/HA-LondonTfL/wiki/Cards
