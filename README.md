# HA-LondonTfL - Home Assistant London TfL Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)

<img width="961" height="600" alt="Example of HASL Card" src="https://github.com/user-attachments/assets/2a7f3f05-1309-4863-8957-5b232d26a61d" />


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
After picking a station you can optionally filter to a single platform. The dropdown lists the platforms TfL is currently
reporting departures for at that station; if yours isn't listed (e.g. nothing is due there right now) you can type it in.
The filter matches on the platform name as TfL reports it, with the `Platform ` prefix removed — e.g. `Eastbound - 2` or `14`.
For bus stops the "platform" is the route number.

To follow one direction of a station on the Underground, set the platform filter to part of the platform name shown in the sensor's `platform` attribute (for example `Westbound`). A stop with a platform filter is named `Station (filter)` and its entity id includes the filter, so the two directions of the same station and line get different, stable names. Without a filter the name is `Station to <destination of the next train>` and covers every platform.

### Elizabeth line: direction and calling-at filters

The Elizabeth line feed has no direction field, and its platform information is unreliable: in central London (Liverpool Street, Farringdon, Paddington and others) TfL reports every train as `Platform Unknown`, and at some stations (Stratford, for example) the same platform is reported as `Platform 5` for some trains and `Platform Unknown` for others. A platform filter therefore either can't split the two directions or silently drops trains.

Elizabeth line stops instead offer two extra options on the platform step (and under **Configure → Edit stop**), both worked out from TfL's route data, fetched once a day, rather than the platform or destination:

- **Direction of travel** (`Eastbound` / `Westbound`) keeps every train heading that way. It keeps working when trains are diverted to another platform, change destination by time of day or terminate early. This is usually all you need.
- **Calling at** keeps only trains whose route from your station passes through (or ends at) the chosen station. Use it when one direction splits into branches you don't want: from Liverpool Street, `Calling at: Stratford` keeps the Shenfield trains and leaves out the Abbey Wood ones.

Things to know:

- **Leave the platform filter blank** on Elizabeth line stops and use these instead.
- **Liverpool Street and Paddington have two parts on the line**: the Elizabeth line platforms underground and the main line station above. TfL treats each part as a separate station, but the Calling at list shows each of them once, and choosing it matches both. That includes trains that are diverted to terminate at the main line station, as happens at Liverpool Street during disruption.
- **Calling at follows the route, not the individual train.** TfL doesn't publish each train's stopping pattern, so a train is kept if the route to its destination passes the chosen station.
- **Direction and Calling at must agree.** If you set both, the calling-at station has to lie in that direction from your stop; the form refuses a combination that could never show a train. Setting just one of them is normally enough.
- **If TfL's route data can't be loaded**, a filtered stop shows no trains rather than a mix of both directions, and tries again 30 minutes later.
- **Changing a filter gives the sensor a new entity ID**, so update any cards or automations that use it.

Examples (the sensor is named after its filters, e.g. `Liverpool Street (via Stratford)`):

| Journey | Filter | Entity ID |
|---|---|---|
| Liverpool Street → Stratford | Calling at: Stratford | `sensor.london_tfl_elizabeth_910glivstll_via_910gstfd` |
| Stratford → Liverpool Street | Direction: Westbound | `sensor.london_tfl_elizabeth_910gstfd_westbound` |
| Stratford → Liverpool Street (either level) | Calling at: Liverpool Street | `sensor.london_tfl_elizabeth_910gstfd_via_hublst` |
| Hayes & Harlington → central London | Direction: Eastbound | `sensor.london_tfl_elizabeth_910ghayesah_eastbound` |

Sensor state is the next train departure time from the given station and platform (if set).
Attributes contain up to `max` departures.

Sensor name will change to the name of the first departure's destination station.
If you do not want this behaviour, you can change the name of the sensor manually.

![Departure details from Canary Wharf Jubilee station](https://github.com/morosanmihail/HA-LondonTfL/blob/main/images/example_2.png?raw=true)

### Alternate setup

You can still manually configure the integration, but it's not recommended.
Check https://github.com/morosanmihail/HA-LondonTfL/wiki/Manual-setup if you insist.

## Strike alerts

The integration can also tell you about strikes on the lines you use. It reads them from TfL's own line status data, the same information the [TfL strikes page](https://tfl.gov.uk/status-updates/strikes) shows, and checks hourly.

Turn it on from the integration's **Configure → Strike alerts**:

- **Track strikes**: adds the three entities below (turning it off removes them).
- **Lines to watch**: pick the lines you travel on. Leave it empty to watch every Tube, Elizabeth line, Overground, DLR and tram line.

| Entity | State | Attributes |
|---|---|---|
| `sensor.london_tfl_strikes` | When the next strike starts (or the one under way). `unknown` when none is announced in the next 30 days | `strikes`: each announced strike with `title`, `when` (e.g. `Thu 17 Dec 12:00 – Fri 18 Dec 11:59`), `start`, `end`, `active`, `lines`, `line_ids`, `line_colours`, `description` (TfL's text). `affected_now`: on a strike day, each line's live status (`line`, `status` such as `Part Suspended`, `description`, `until`, `colour`). Also `strike_count`, `watched_lines` |
| `binary_sensor.london_tfl_strike_today` | `on` when a strike affects a watched line for the rest of today: under way, starting later today, or reported in TfL's live status | `strikes`, `affected_now` |
| `binary_sensor.london_tfl_strike_within_7_days` | `on` when a strike is under way or starts within the next 7 days | `next_strike`, `strikes` |

Things to know:

- **Announced strikes and the day itself look different in TfL's data.** Before a strike, TfL publishes the announcement ("TUBE STRIKE: …") with its exact start and end. On the day, TfL replaces it with each line's live status ("Circle Line: No service on the entire line due to strike action"), which is what `affected_now` shows.
- Strikes are announced per line, so one strike covering all 11 Tube lines appears once, listing those lines.
- If TfL can't be reached, the strike entities show as unavailable until the next hourly check. Departure sensors are not affected.

### Dashboard cards

These use only the cards built into Home Assistant. Add a **Manual** card and paste the YAML. The Markdown card's `content: |` matters: with `content: >` YAML joins all the lines into one and the headings and line list run together.

A card listing every announced strike, which only appears when there is one:

```yaml
type: markdown
title: TfL strikes
visibility:
  - condition: state
    entity: sensor.london_tfl_strikes
    state_not: unknown
  - condition: state
    entity: sensor.london_tfl_strikes
    state_not: unavailable
content: |
  {% set live = state_attr('sensor.london_tfl_strikes', 'affected_now') or [] %}
  {% set strikes = state_attr('sensor.london_tfl_strikes', 'strikes') or [] %}
  {% if live %}
  ### 🚨 Strike action today
  {% for l in live %}
  <font color="{{ l.colour }}">■</font> **{{ l.line }}**: {{ l.status }}
  {% endfor %}
  {% endif %}
  {% for s in strikes %}
  ### {{ '🔴' if s.active else '🗓️' }} {{ s.title }}
  **{{ s.when }}**

  {% for i in range(s.lines | length) %}<font color="{{ s.line_colours[i] }}">■</font> {{ s.lines[i] }}{{ ', ' if not loop.last }}{% endfor %}

  {{ s.description }}
  {% endfor %}
```

<img src="images/strike_card.png" alt="Strike list card on a strike day" width="420">

*On a strike day: each line's live status, then the announced strikes (cropped after the first; the 19–20 December strike follows in the same format).*

At-a-glance tiles:

```yaml
type: grid
columns: 2
square: false
cards:
  - type: tile
    entity: binary_sensor.london_tfl_strike_today
    name: Strike today
  - type: tile
    entity: binary_sensor.london_tfl_strike_within_7_days
    name: Strike this week
```

<img src="images/strike_tiles.png" alt="Strike today and strike this week tiles" width="500">

### Notification example

```yaml
alias: TfL strike warning
triggers:
  - trigger: time
    at: "07:00:00"
conditions:
  - condition: state
    entity_id: binary_sensor.london_tfl_strike_today
    state: "on"
actions:
  - action: notify.notify
    data:
      title: TfL strike today
      message: >
        {% set live = state_attr('sensor.london_tfl_strikes', 'affected_now') or [] %}
        {% if live %}{{ live | map(attribute='line') | join(', ') }} affected.{% else %}Strike starts today: {{ state_attr('binary_sensor.london_tfl_strike_today', 'strikes')[0].when }}.{% endif %}
```

## Viewing

Check out https://github.com/morosanmihail/HA-LondonTfL/wiki/Cards
