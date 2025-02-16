"""The habitica integration."""
import logging

from habitipy.aio import HabitipyAsync
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_NAME,
    CONF_API_KEY,
    CONF_NAME,
    CONF_SENSORS,
    CONF_URL,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_ARGS,
    ATTR_DATA,
    ATTR_PATH,
    CONF_API_USER,
    DEFAULT_URL,
    DOMAIN,
    EVENT_API_CALL_SUCCESS,
    SERVICE_API_CALL,
)
from .sensor import SENSORS_TYPES

_LOGGER = logging.getLogger(__name__)

INSTANCE_SCHEMA = vol.All(
    cv.deprecated(CONF_SENSORS),
    vol.Schema(
        {
            vol.Optional(CONF_URL, default=DEFAULT_URL): cv.url,
            vol.Optional(CONF_NAME): cv.string,
            vol.Required(CONF_API_USER): cv.string,
            vol.Required(CONF_API_KEY): cv.string,
            vol.Optional(CONF_SENSORS, default=list(SENSORS_TYPES)): vol.All(
                cv.ensure_list, vol.Unique(), [vol.In(list(SENSORS_TYPES))]
            ),
        }
    ),
)

has_unique_values = vol.Schema(vol.Unique())
# because we want a handy alias


def has_all_unique_users(value):
    """Validate that all API users are unique."""
    api_users = [user[CONF_API_USER] for user in value]
    has_unique_values(api_users)
    return value


def has_all_unique_users_names(value):
    """Validate that all user's names are unique and set if any is set."""
    names = [user.get(CONF_NAME) for user in value]
    if None in names and any(name is not None for name in names):
        raise vol.Invalid("user names of all users must be set if any is set")
    if not all(name is None for name in names):
        has_unique_values(names)
    return value


INSTANCE_LIST_SCHEMA = vol.All(
    cv.ensure_list, has_all_unique_users, has_all_unique_users_names, [INSTANCE_SCHEMA]
)
CONFIG_SCHEMA = vol.Schema({DOMAIN: INSTANCE_LIST_SCHEMA}, extra=vol.ALLOW_EXTRA)

PLATFORMS = [Platform.SENSOR]

SERVICE_API_CALL_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_NAME): str,
        vol.Required(ATTR_PATH): vol.All(cv.ensure_list, [str]),
        vol.Optional(ATTR_ARGS): dict,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Habitica service."""
    configs = config.get(DOMAIN, [])

    for conf in configs:
        if conf.get(CONF_URL) is None:
            conf[CONF_URL] = DEFAULT_URL

        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_IMPORT}, data=conf
            )
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up habitica from a config entry."""

    class HAHabitipyAsync(HabitipyAsync):
        """Closure API class to hold session."""

        def __call__(self, **kwargs):
            return super().__call__(websession, **kwargs)

    async def handle_api_call(call):
        name = call.data[ATTR_NAME]
        path = call.data[ATTR_PATH]
        entries = hass.config_entries.async_entries(DOMAIN)
        api = None
        for entry in entries:
            if entry.data[CONF_NAME] == name:
                api = hass.data[DOMAIN].get(entry.entry_id)
                break
        if api is None:
            _LOGGER.error("API_CALL: User '%s' not configured", name)
            return
        try:
            for element in path:
                api = api[element]
        except KeyError:
            _LOGGER.error(
                "API_CALL: Path %s is invalid for API on '{%s}' element", path, element
            )
            return
        kwargs = call.data.get(ATTR_ARGS, {})
        data = await api(**kwargs)
        hass.bus.async_fire(
            EVENT_API_CALL_SUCCESS, {ATTR_NAME: name, ATTR_PATH: path, ATTR_DATA: data}
        )

    data = hass.data.setdefault(DOMAIN, {})
    config = entry.data
    websession = async_get_clientsession(hass)
    url = config[CONF_URL]
    username = config[CONF_API_USER]
    password = config[CONF_API_KEY]
    name = config.get(CONF_NAME)
    config_dict = {"url": url, "login": username, "password": password}
    api = HAHabitipyAsync(config_dict)
    user = await api.user.get()
    if name is None:
        name = user["profile"]["name"]
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_NAME: name},
        )
    data[entry.entry_id] = api

    hass.config_entries.async_setup_platforms(entry, PLATFORMS)

    if not hass.services.has_service(DOMAIN, SERVICE_API_CALL):
        hass.services.async_register(
            DOMAIN, SERVICE_API_CALL, handle_api_call, schema=SERVICE_API_CALL_SCHEMA
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    if len(hass.config_entries.async_entries(DOMAIN)) == 1:
        hass.services.async_remove(DOMAIN, SERVICE_API_CALL)
    return unload_ok
