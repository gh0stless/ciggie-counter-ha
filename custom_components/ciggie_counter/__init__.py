"""Ciggie Counter: count cigarettes in Home Assistant, standalone or synced with the apps."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, Platform
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .const import (
    CONF_COUNT_SOURCES,
    CONF_NC_PASSWORD,
    CONF_NC_URL,
    CONF_NC_USER,
    CONF_RULES_UPDATED_AT,
    DEFAULTS,
    DOMAIN,
    RULE_KEYS,
)
from .counter import CiggieCounter
from .http import async_register_views
from .nextcloud import INTERVAL, NextcloudSync

_LOGGER = logging.getLogger(__name__)

_RULE_OPTIONS = {*RULE_KEYS.values(), CONF_RULES_UPDATED_AT}

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR, Platform.SWITCH]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type CiggieConfigEntry = ConfigEntry[CiggieCounter]

SERVICE_COUNT = "count"
SERVICE_DELETE_LAST = "delete_last"


def get_counter(hass: HomeAssistant) -> CiggieCounter | None:
    """The counter of the (single) config entry, None while not set up."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data
    return None


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Services and the sync API exist once for the domain."""
    async_register_views(hass)

    async def count(call: ServiceCall) -> None:
        counter = _require(hass)
        when: datetime | None = call.data.get("timestamp")
        if when is not None and when.tzinfo is None:
            when = when.replace(tzinfo=dt_util.get_default_time_zone())
        counter.async_add(when, call.data.get("source", "service"))

    async def delete_last(call: ServiceCall) -> None:
        _require(hass).async_delete_last()

    hass.services.async_register(
        DOMAIN,
        SERVICE_COUNT,
        count,
        schema=vol.Schema({vol.Optional("timestamp"): cv.datetime, vol.Optional("source"): cv.string}),
    )
    hass.services.async_register(DOMAIN, SERVICE_DELETE_LAST, delete_last)
    return True


def _require(hass: HomeAssistant) -> CiggieCounter:
    counter = get_counter(hass)
    if counter is None:
        raise vol.Invalid("Ciggie Counter is not set up")
    return counter


async def async_setup_entry(hass: HomeAssistant, entry: CiggieConfigEntry) -> bool:
    counter = CiggieCounter(hass, entry.entry_id, dict(entry.options))
    await counter.async_load()
    entry.runtime_data = counter

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # every minute: reminders and time-based sensors (break length shrinks over time)
    @callback
    def tick(now: datetime) -> None:
        counter.async_tick()

    entry.async_on_unload(async_track_time_interval(hass, tick, timedelta(minutes=1)))

    o = entry.options
    if o.get(CONF_NC_URL) and o.get(CONF_NC_USER) and o.get(CONF_NC_PASSWORD):
        sync = NextcloudSync(hass, counter, o[CONF_NC_URL], o[CONF_NC_USER], o[CONF_NC_PASSWORD])
        counter.async_use_hub(f"{o[CONF_NC_URL].rstrip('/')}|{o[CONF_NC_USER]}")
        counter.on_local_change = sync.async_schedule

        @callback
        def sync_tick(now: datetime) -> None:
            entry.async_create_background_task(hass, sync.async_sync_quietly(), "ciggie_counter nextcloud sync")

        entry.async_on_unload(async_track_time_interval(hass, sync_tick, INTERVAL))
        entry.async_on_unload(sync.debouncer.async_shutdown)
        entry.async_create_background_task(hass, sync.async_sync_quietly(), "ciggie_counter nextcloud sync")

    # never our own entities: the count button already counts by itself (would count twice)
    registry = er.async_get(hass)
    sources = [
        entity_id for entity_id in o.get(CONF_COUNT_SOURCES, [])
        if (reg := registry.async_get(entity_id)) is None or reg.platform != DOMAIN
    ]
    if sources:
        entry.async_on_unload(async_track_state_change_event(hass, sources, _source_listener(counter)))

    entry.async_on_unload(entry.add_update_listener(_options_updated))
    return True


def _source_listener(counter: CiggieCounter):
    """Count when a chosen entity fires: on/off sensors when turning on, buttons/events on press."""

    @callback
    def handle(event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        if new is None or old is None or new.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return
        if old.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return
        domain = new.entity_id.split(".", 1)[0]
        if domain in ("binary_sensor", "input_boolean"):
            if not (old.state == "off" and new.state == "on"):
                return
        elif new.state == old.state:
            # event, button, input_button: the state is the time of the last press
            return
        counter.async_add(source=new.entity_id)

    return handle


def _without_rules(options: dict) -> dict:
    return {k: v for k, v in {**DEFAULTS, **options}.items() if k not in _RULE_OPTIONS}


async def _options_updated(hass: HomeAssistant, entry: CiggieConfigEntry) -> None:
    counter = entry.runtime_data
    if _without_rules(entry.options) == _without_rules(counter.options):
        # only the rules changed (options flow or another device): no reload needed
        counter.async_use_options(dict(entry.options))
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: CiggieConfigEntry) -> bool:
    entry.runtime_data.async_shutdown()
    await entry.runtime_data.async_flush()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
