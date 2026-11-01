"""Switch: mute everything (no reminders, no count announcement events with muted=false)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CiggieConfigEntry
from .entity import CiggieEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: CiggieConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([MuteSwitch(entry.runtime_data, entry.entry_id, "muted")])


class MuteSwitch(CiggieEntity, SwitchEntity):
    _attr_icon = "mdi:bell-off"

    @property
    def is_on(self) -> bool:
        return self.counter.muted

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.counter.async_set_muted(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.counter.async_set_muted(False)
