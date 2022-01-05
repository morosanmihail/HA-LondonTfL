DOMAIN = 'london_tfl'

DEFAULT_NAME = 'London TfL'
CONF_STOPS = 'stops'

CONF_LINE = 'line'
CONF_STATION = 'station'
CONF_PLATFORM = 'platform'
CONF_MAX = 'max'
DEFAULT_MAX = 3

# This is currently dodgy as-is until / unless I get proper permission
# to use TfL's iconography around this.
LINE_IMAGES = {
    'default': '',
    'dlr': 'http://vignette3.wikia.nocookie.net/locomotive/images/6/66/2000px-DLR_roundel.svg.png/revision/latest?cb=20121228140928',  # noqa
}

TFL_LINES_URL = 'https://api.tfl.gov.uk/line/mode/tube,dlr,overground,cable-car,tflrail,tram'  # noqa
TFL_ARRIVALS_URL = 'https://api.tfl.gov.uk/line/{0}/arrivals/{1}?test={2}'
TFL_STATIONS_URL = 'https://api.tfl.gov.uk/line/{0}/stoppoints'
