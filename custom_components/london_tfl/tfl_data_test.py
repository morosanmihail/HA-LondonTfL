import json
import unittest
from unittest.mock import Mock
from datetime import datetime, timedelta
from custom_components.london_tfl.tfl_data import (
    TfLData,
)


class TestTfLData(unittest.TestCase):
    def setUp(self):
        with open("custom_components/london_tfl/test/underground.json", "r") as file:
            test_data = file.read()
        json_data = json.loads(test_data)
        self.tfl_data = TfLData(method="tube", line="jubilee")
        self.tfl_data.populate(json_data, filter_platform="")
        self.tfl_data.sort_data(5)

    def test_get_departures(
        self,
    ):
        departures = self.tfl_data.get_departures()

        self.assertEqual(len(departures), 5)
        self.assertEqual(departures[0]["destination"], "Stratford")
        self.assertEqual(departures[0]["type"], "Metros")

        self.assertEqual(departures[1]["destination"], "Stratford")
        self.assertEqual(departures[1]["type"], "Metros")

        self.assertEqual(departures[2]["destination"], "Stratford")
        self.assertEqual(departures[2]["type"], "Metros")

        self.assertEqual(departures[3]["destination"], "Stratford")
        self.assertEqual(departures[3]["type"], "Metros")

        self.assertGreater(departures[1]["expected"], departures[0]["expected"])
        self.assertGreater(departures[2]["expected"], departures[1]["expected"])
        self.assertGreater(departures[3]["expected"], departures[2]["expected"])
        self.assertGreater(departures[4]["expected"], departures[3]["expected"])

    def test_get_station_name(self):
        self.tfl_data.get_departures()
        station_name = self.tfl_data.get_station_name()

        self.assertEqual(station_name, "Stratford Underground Station")

    def test_get_line_colours(self):
        self.tfl_data.get_departures()
        colours = self.tfl_data.get_line_colours()

        self.assertEqual(
            {
                "r": 0,
                "g": 25,
                "b": 168,
            },
            colours,
        )


if __name__ == "__main__":
    unittest.main()
