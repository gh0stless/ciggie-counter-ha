"""Button: count a cigarette."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CiggieConfigEntry
from .entity import CiggieEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: CiggieConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([CountButton(entry.runtime_data, entry.entry_id, "count")])


class CountButton(CiggieEntity, ButtonEntity):
    _attr_icon = "mdi:plus-circle"

    async def async_press(self) -> None:
        self.counter.async_add(source="button")
