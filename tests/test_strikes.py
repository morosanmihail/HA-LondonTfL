"""Strike information from TfL's line status API."""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from custom_components.london_tfl import strikes as strikes_module
from custom_components.london_tfl.strikes import (
    collect_strikes,
    describe,
    fetch_strikes,
    is_strike,
    is_strike_soon,
    is_strike_today,
    line_colour,
    next_strike_start,
    split_reason,
)

FIXTURES = Path(__file__).parent.parent / "custom_components" / "london_tfl" / "test"
# Shaped on TfL's replies on 26 November 2026, a Tube strike day with two more strikes announced for December:
#   strikes_status_now.json   - /Line/Mode/tube,elizabeth-line,overground,dlr,tram/Status
#   strikes_status_range.json - /Line/{ids}/Status/{from}/to/{to} for the next 30 days
NOW = json.loads((FIXTURES / "strikes_status_now.json").read_text())
RANGE = json.loads((FIXTURES / "strikes_status_range.json").read_text())
LONDON = ZoneInfo("Europe/London")
TUBE = [
    "Bakerloo", "Central", "Circle", "District", "Hammersmith & City", "Jubilee",
    "Metropolitan", "Northern", "Piccadilly", "Victoria", "Waterloo & City",
]


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=LONDON)


def no_live(data: dict) -> dict:
    """The same data on a day without a strike under way."""
    return {**data, "affected_now": []}


@pytest.fixture
def data() -> dict:
    return collect_strikes(NOW, RANGE, None)


def test_is_strike():
    assert is_strike("TUBE STRIKE: Some London Underground Tube drivers are planning to take strike action")
    assert is_strike("Circle Line: No service on the entire line due to strike action.")
    assert is_strike("Possible industrial action on the DLR")
    assert not is_strike("Severe delays due to an earlier signal failure")
    assert not is_strike("Part closure for planned engineering work at Strikeham")
    assert not is_strike(None)


def test_split_reason():
    assert split_reason("TUBE STRIKE: Some drivers are striking.") == ("Tube strike", "Some drivers are striking.")
    assert split_reason("Circle Line: No service due to strike action.") == (
        "Strike action",
        "Circle Line: No service due to strike action.",
    )


def test_announced_strikes_are_merged_across_lines(data):
    first, second = data["strikes"]
    assert first["title"] == "Tube strike"
    assert first["description"].startswith("Some London Underground Tube drivers")
    assert [line["name"] for line in first["lines"]] == TUBE
    assert first["start"] == datetime(2026, 12, 17, 12, 0, tzinfo=ZoneInfo("UTC"))
    assert second["start"] == datetime(2026, 12, 19, 12, 0, tzinfo=ZoneInfo("UTC"))
    assert first["lines"][0]["colour"] == "#B36305"


def test_planned_engineering_work_is_not_a_strike(data):
    assert all("engineering" not in s["description"] for s in data["strikes"])


def test_live_strike_disruption_shows_each_lines_worst_status(data):
    affected = {line["id"]: line["status"] for line in data["affected_now"]}
    assert affected == {"central": "Part Suspended", "circle": "Suspended", "district": "Minor Delays"}
    assert "mildmay" not in affected  # signal failure, not a strike


def test_watching_some_lines(data):
    picked = collect_strikes(NOW, RANGE, ["victoria", "circle"])
    assert [line["name"] for line in picked["strikes"][0]["lines"]] == ["Victoria", "Circle"]
    assert [line["id"] for line in picked["affected_now"]] == ["circle"]
    assert picked["watched_lines"] == ["Victoria", "Circle"]
    assert not picked["watched_all"]

    elizabeth = collect_strikes(NOW, RANGE, ["elizabeth"])
    assert elizabeth["strikes"] == [] and elizabeth["affected_now"] == []


def test_no_lines_chosen_means_every_line(data):
    assert data["watched_all"]
    assert len(data["watched_lines"]) == 20
    assert data["modes"] == ["DLR", "Elizabeth line", "Overground", "Tram", "Tube"]


def test_strike_today_on_the_day_of_a_live_strike(data):
    assert is_strike_today(data, at("2026-11-26T15:30"))
    assert is_strike_soon(data, at("2026-11-26T15:30"))


@pytest.mark.parametrize(
    "now, today, soon",
    [
        ("2026-12-09T10:00", False, False),  # strike more than a week away
        ("2026-12-11T08:00", False, True),  # 17 Dec is within 7 days
        ("2026-12-17T08:00", True, True),  # starts at noon today
        ("2026-12-18T09:00", True, True),  # under way, ends 11:59
        ("2026-12-18T13:00", False, True),  # between the two strikes
        ("2026-12-20T13:00", False, False),  # both over
    ],
)
def test_today_and_within_a_week(data, now, today, soon):
    quiet = no_live(data)
    assert is_strike_today(quiet, at(now)) is today
    assert is_strike_soon(quiet, at(now)) is soon


def test_next_strike_start(data):
    quiet = no_live(data)
    assert next_strike_start(quiet, at("2026-12-01T10:00")).day == 17
    assert next_strike_start(quiet, at("2026-12-18T09:00")).day == 17  # the one under way
    assert next_strike_start(quiet, at("2026-12-18T13:00")).day == 19
    assert next_strike_start(quiet, at("2026-12-21T09:00")) is None
    # On a live strike day: when today's disruption began, ahead of the strikes announced for later.
    assert next_strike_start(data, at("2026-11-26T15:30")).day == 26
    assert next_strike_start({"strikes": [], "affected_now": data["affected_now"]}, at("2026-11-26T15:30")).hour == 12


def test_describe(data):
    first = describe(data["strikes"][0], at("2026-12-18T09:00"))
    assert first["when"] == "Thu 17 Dec 12:00 – Fri 18 Dec 11:59"
    assert first["active"] is True
    assert first["start"] == "2026-12-17T12:00:00+00:00"
    assert first["lines"][:2] == ["Bakerloo", "Central"]
    assert first["line_ids"][0] == "bakerloo"
    assert first["line_colours"][0] == "#B36305"


def test_line_colours():
    assert line_colour("bakerloo") == "#B36305"
    assert line_colour("elizabeth", "elizabeth-line") == "#6950A1"
    assert line_colour("mildmay", "overground") == "#006FE6"
    assert line_colour("unknown-line", "dlr") == "#00AFAD"


async def test_fetch_asks_for_the_watched_lines_over_the_next_30_days(monkeypatch):
    urls = []

    async def fake_request(url):
        urls.append(url)
        return json.dumps(NOW if "/Mode/" in url else RANGE)

    monkeypatch.setattr(strikes_module, "request", fake_request)
    result = await fetch_strikes(["victoria", "circle"], at("2026-11-26T15:30"))
    assert urls == [
        "https://api.tfl.gov.uk/Line/Mode/tube,elizabeth-line,overground,dlr,tram/Status",
        "https://api.tfl.gov.uk/Line/victoria,circle/Status/2026-11-26T00:00:00/to/2026-12-26T23:59:00",
    ]
    assert len(result["strikes"]) == 2

    urls.clear()
    await fetch_strikes(None, at("2026-11-26T15:30"))
    assert urls[1].startswith("https://api.tfl.gov.uk/Line/bakerloo,central,circle,")


async def test_fetch_fails_cleanly_when_tfl_does_not_answer(monkeypatch):
    from homeassistant.helpers.update_coordinator import UpdateFailed

    async def no_reply(url):
        return None

    monkeypatch.setattr(strikes_module, "request", no_reply)
    with pytest.raises(UpdateFailed):
        await fetch_strikes(None)
