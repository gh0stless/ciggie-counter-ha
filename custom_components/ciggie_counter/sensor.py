"""Sensors: counts, cost, break, Butt Bin battery."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CiggieConfigEntry
from .counter import CiggieCounter
from .entity import CiggieEntity


@dataclass(frozen=True, kw_only=True)
class CiggieSensorDescription(SensorEntityDescription):
    value: Callable[[CiggieCounter], Any]


CIGARETTES = "cigarettes"

SENSORS: tuple[CiggieSensorDescription, ...] = (
    CiggieSensorDescription(key="today", native_unit_of_measurement=CIGARETTES,
                            state_class=SensorStateClass.MEASUREMENT, icon="mdi:smoking",
                            value=lambda c: c.count_today()),
    CiggieSensorDescription(key="yesterday", native_unit_of_measurement=CIGARETTES, icon="mdi:smoking",
                            value=lambda c: c.count_yesterday()),
    CiggieSensorDescription(key="month", native_unit_of_measurement=CIGARETTES,
                            state_class=SensorStateClass.MEASUREMENT, icon="mdi:calendar-month",
                            value=lambda c: c.count_month()),
    CiggieSensorDescription(key="year", native_unit_of_measurement=CIGARETTES,
                            state_class=SensorStateClass.MEASUREMENT, icon="mdi:calendar",
                            value=lambda c: c.count_year()),
    CiggieSensorDescription(key="cost_today", device_class=SensorDeviceClass.MONETARY, icon="mdi:cash",
                            value=lambda c: c.cost_today()),
    CiggieSensorDescription(key="break", native_unit_of_measurement=UnitOfTime.MINUTES,
                            suggested_display_precision=0, icon="mdi:timer-outline",
                            value=lambda c: c.interval_minutes()),
    CiggieSensorDescription(key="next_allowed", device_class=SensorDeviceClass.TIMESTAMP, icon="mdi:timer-sand",
                            value=lambda c: c.next_allowed()),
    CiggieSensorDescription(key="last_cigarette", device_class=SensorDeviceClass.TIMESTAMP, icon="mdi:history",
                            value=lambda c: c.last),
    CiggieSensorDescription(key="ashtray_battery", device_class=SensorDeviceClass.BATTERY,
                            native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT,
                            value=lambda c: c.ashtray["battery"]),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: CiggieConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(CiggieSensor(entry.runtime_data, entry.entry_id, d) for d in SENSORS)


class CiggieSensor(CiggieEntity, SensorEntity):
    entity_description: CiggieSensorDescription

    def __init__(self, counter: CiggieCounter, entry_id: str, description: CiggieSensorDescription) -> None:
        super().__init__(counter, entry_id, description.key)
        self.entity_description = description
        if description.device_class is SensorDeviceClass.MONETARY:
            self._attr_native_unit_of_measurement = counter.hass.config.currency

    @property
    def native_value(self) -> Any:
        return self.entity_description.value(self.counter)
