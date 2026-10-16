"""Sensor names and entity ids for stops that differ only by direction (platform filter)."""

from custom_components.london_tfl.sensor import LondonTfLSensor
from custom_components.london_tfl.tfl_data import TfLData


def make(platform: str = "", mode: str = "realtime", destination: str = "") -> LondonTfLSensor:
    data = TfLData(method="tube", line="piccadilly", station="940GZZLUWOG")
    data._station_name = "Wood Green Underground Station"
    sensor = LondonTfLSensor(
        name="London TfL",
        method="tube",
        line="piccadilly",
        station="940GZZLUWOG",
        platform_filter=platform,
        max=3,
        shortenStationNames=False,
        departure_mode=mode,
        tfl_data=data,
    )
    sensor._destination = destination
    return sensor


def test_filtered_stops_have_distinct_stable_names_and_entity_ids():
    west = make("Westbound", destination="Heathrow T123 + 5")
    east = make("Eastbound", destination="Cockfosters")
    assert west.name == "Wood Green Underground Station (Westbound)"
    assert east.name == "Wood Green Underground Station (Eastbound)"
    assert west.entity_id != east.entity_id
    assert west.entity_id == "sensor.london_tfl_piccadilly_940gzzluwog_westbound"
    # the name does not follow the next train
    west._destination = "Rayners Lane"
    assert west.name == "Wood Green Underground Station (Westbound)"


def test_mode_suffix_still_applies_to_filtered_stops():
    assert make("Westbound", mode="all").name.endswith("(Westbound) (All)")
    assert make("Westbound", mode="scheduled").entity_id.endswith("_westbound_scheduled")


def test_unfiltered_stop_keeps_its_existing_name_and_entity_id():
    sensor = make(destination="Cockfosters")
    assert sensor.name == "Wood Green Underground Station to Cockfosters"
    assert sensor.entity_id == "sensor.london_tfl_piccadilly_940gzzluwog"
    assert make(mode="all").entity_id == "sensor.london_tfl_piccadilly_940gzzluwog_all"
