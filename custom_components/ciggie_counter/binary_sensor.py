"""Binary sensor: is the next cigarette allowed (break over)?"""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import CiggieConfigEntry
from .entity import CiggieEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: CiggieConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([AllowedSensor(entry.runtime_data, entry.entry_id, "allowed")])


class AllowedSensor(CiggieEntity, BinarySensorEntity):
    _attr_icon = "mdi:smoking"

    @property
    def is_on(self) -> bool:
        nxt = self.counter.next_allowed()
        return nxt is None or nxt <= dt_util.now()
