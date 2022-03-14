from datetime import datetime
from dateutil import parser


def get_destination(entry):
    if 'destinationName' in entry:
        return entry['destinationName']
    else:
        if 'towards' in entry:
            return entry['towards']
    return ''


def time_to_station(entry, with_destination=True, style='{0}m {1}s'):
    next_departure_time = (
        parser.parse(entry['expectedArrival']).replace(tzinfo=None) -
        datetime.now().replace(tzinfo=None)
    ).seconds
    next_departure_dest = get_destination(entry)
    return style.format(
        int(next_departure_time / 60),
        int(next_departure_time % 60)
    ) + (' to ' + next_departure_dest if with_destination else '')


class TfLData:
    def __init__(self):
        self._raw_result = []
        self._last_update = None
        self._api_json = []
        self._station_name = ""

    def populate(self, json_data, filter_platform):
        self._raw_result = json_data
        self.filter_by_platform(filter_platform)
        self._last_update = datetime.now()

    def is_data_stale(self, max_items):
        if len(self._raw_result) > 0:
            # check if there are enough already stored to skip a request
            now = datetime.now().timestamp()
            after_now = [
                item for item in self._raw_result
                if parser.parse(item['expectedArrival']).timestamp() > now
            ]

            if len(after_now) >= max_items:
                self._raw_result = after_now
                return False
        return True

    def filter_by_platform(self, filter_platform):
        if filter_platform != '':
            self._raw_result = [
                item for item in self._raw_result
                if filter_platform in item['platformName']
            ]

    def sort_data(self, max_items):
        self._api_json = sorted(
            self._raw_result, key=lambda i: i['expectedArrival'], reverse=False
        )[:max_items]

    def get_state(self):
        if len(self._api_json) > 0:
            return parser.parse(
                self._api_json[0]['expectedArrival']
            ).strftime('%H:%M')
        return 'None'

    def is_empty(self):
        return len(self._api_json) == 0

    def get_departures(self):
        departures = []
        for item in self._api_json:
            departure = {
                'time_to_station': time_to_station(item, False),
                'platform': (
                    item['platformName'] if 'platformName' in item else ''
                ),
                'line': item['platformName'] if 'platformName' in item else '',
                'direction': 0,
                'departure': item['expectedArrival'],
                'destination': get_destination(item),
                'time': time_to_station(item, False, '{0}'),
                'expected': item['expectedArrival'],
                'type': 'Metros',
                'groupofline': '',
                'icon': 'mdi:train',
            }

            departures.append(departure)

            if len(self._station_name) == 0:
                self._station_name = item['stationName']

        return departures

    def get_station_name(self):
        return self._station_name

    def get_last_update(self):
        return self._last_update
