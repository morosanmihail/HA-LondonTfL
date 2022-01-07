DOMAIN = 'london_tfl'

DEFAULT_NAME = 'London TfL'
CONF_STOPS = 'stops'

CONF_LINE = 'line'
CONF_STATION = 'station'
CONF_PLATFORM = 'platform'
CONF_MAX = 'max'
DEFAULT_MAX = 3

LINE_IMAGES = {
    'default': 'https://tfl.gov.uk/tfl/common/images/logos/London%20Underground/Roundel/LULRoundel.jpg',  # noqa
    'dlr': 'https://tfl.gov.uk/tfl/common/images/logos/Docklands%20Light%20Railway/Roundel/DLRRoundel.jpg',  # noqa
    'london-overground': 'https://tfl.gov.uk/tfl/common/images/logos/London%20Overground/Roundel/OvergroundRoundel.jpg',  # noqa
    'tram': 'https://tfl.gov.uk/tfl/common/images/logos/London%20Tramlink/Roundel/TramlinkRoundel.jpg',  # noqa
    'tfl-rail': 'https://tfl.gov.uk/tfl/common/images/logos/TfL%20Rail/Roundel/TfLRailRoundel.jpg',  # noqa
}

TFL_LINES_URL = 'https://api.tfl.gov.uk/line/mode/tube,dlr,overground,cable-car,tflrail,tram'  # noqa
TFL_ARRIVALS_URL = 'https://api.tfl.gov.uk/line/{0}/arrivals/{1}?test={2}'
TFL_STATIONS_URL = 'https://api.tfl.gov.uk/line/{0}/stoppoints'

def get_line_image(line):
    return LINE_IMAGES[line] if line in LINE_IMAGES else LINE_IMAGES['default']
