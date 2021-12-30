import voluptuous as vol
import homeassistant.helpers.config_validation as cv


DEFAULT_NAME = 'London TfL'
CONF_STOPS = 'stops'

CONF_LINE = 'line'
CONF_STATION = 'station'
CONF_PLATFORM = 'platform'
CONF_MAX = 'max'

CONFIG_STOP = vol.Schema({
    vol.Required(CONF_LINE): cv.string,
    vol.Required(CONF_STATION): cv.string,
    vol.Optional(CONF_PLATFORM, default=''): cv.string,
    vol.Optional(CONF_MAX, default=3): cv.positive_int,
})

# This is currently dodgy as-is until / unless I get proper permission to use TfL's iconography around this.
LINE_IMAGES = {
    'default': '',
    'dlr': 'http://vignette3.wikia.nocookie.net/locomotive/images/6/66/2000px-DLR_roundel.svg.png/revision/latest?cb=20121228140928',
}
