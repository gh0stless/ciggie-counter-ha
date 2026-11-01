"""Fixtures for Ciggie Counter tests."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ciggie_counter.const import DEFAULTS, DOMAIN


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: Any) -> Generator[None]:
    yield


@pytest.fixture(autouse=True)
async def berlin(hass: HomeAssistant) -> None:
    """Day boundaries are tested in a real local time zone, not the HA test default."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    hass.config.currency = "EUR"


@pytest.fixture
def options() -> dict[str, Any]:
    return dict(DEFAULTS)


@pytest.fixture
async def entry(hass: HomeAssistant, options: dict[str, Any]) -> MockConfigEntry:
    config_entry = MockConfigEntry(domain=DOMAIN, title="Ciggie Counter", data={}, options=options)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry
