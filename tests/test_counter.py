"""Tests for counting, reminders, breaks and sync v2 of Ciggie Counter."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.ciggie_counter.const import (
    CONF_COST,
    CONF_COUNT_CHECK_MINUTES,
    CONF_COUNT_CHECK_REMINDER,
    CONF_COUNT_SOURCES,
    CONF_DAILY_TARGET,
    CONF_DAY_START_HOUR,
    CONF_FIXED_MINUTES,
    CONF_INTERVAL_MODE,
    CONF_RULES_UPDATED_AT,
    CONF_WAKE_HOURS,
    DEFAULTS,
    DOMAIN,
    EVENT_COUNTED,
    EVENT_REMINDER,
    MODE_COMPUTED,
    MODE_FIXED,
)

TODAY = "sensor.ciggie_counter_today"


def _value(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state.state


async def _press(hass: HomeAssistant) -> None:
    await hass.services.async_call("button", "press", {"entity_id": "button.ciggie_counter_count_cigarette"}, blocking=True)
    await hass.async_block_till_done()


async def test_config_flow_single_instance(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT


async def test_options_flow(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_DAY_START_HOUR: 4, CONF_INTERVAL_MODE: MODE_FIXED, CONF_FIXED_MINUTES: 30, CONF_WAKE_HOURS: 18,
         "next_allowed_reminder": True, CONF_COUNT_CHECK_REMINDER: False, CONF_COUNT_CHECK_MINUTES: 10},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_FIXED_MINUTES] == 30
    assert _value(hass, "sensor.ciggie_counter_break") == "30.0"


async def test_button_counts_and_fires_event(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    events = async_capture_events(hass, EVENT_COUNTED)
    await _press(hass)
    await _press(hass)
    assert _value(hass, TODAY) == "2"
    assert [e.data["number"] for e in events] == [1, 2]
    assert events[0].data["source"] == "button"


@pytest.mark.parametrize("options", [{CONF_COST: 0.15}])
async def test_cost(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await _press(hass)
    await _press(hass)
    assert _value(hass, "sensor.ciggie_counter_cost_today") == "0.3"
    assert hass.states.get("sensor.ciggie_counter_cost_today").attributes["unit_of_measurement"] == "EUR"


async def test_backfill_and_delete_last(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    events = async_capture_events(hass, EVENT_COUNTED)
    earlier = dt_util.now() - timedelta(hours=1)
    await hass.services.async_call(DOMAIN, "count", {"timestamp": earlier}, blocking=True)
    await hass.async_block_till_done()
    assert _value(hass, TODAY) in ("1", "0")  # 0 only right after the day change
    assert events == []  # backfills are not announced
    await _press(hass)
    await hass.services.async_call(DOMAIN, "delete_last", {}, blocking=True)
    await hass.async_block_till_done()
    counter = entry.runtime_data
    assert len(counter.active) == 1
    assert counter.active[0] == earlier.replace(microsecond=0)


async def test_count_source_entities(hass: HomeAssistant, options: dict[str, Any]) -> None:
    hass.states.async_set("binary_sensor.ashtray_contact", "off")
    hass.states.async_set("event.zigbee_button", "2026-11-01T08:00:00+00:00")
    options[CONF_COUNT_SOURCES] = ["binary_sensor.ashtray_contact", "event.zigbee_button"]
    config_entry = MockConfigEntry(domain=DOMAIN, data={}, options=options)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("binary_sensor.ashtray_contact", "on")  # counts
    hass.states.async_set("binary_sensor.ashtray_contact", "off")
    hass.states.async_set("binary_sensor.ashtray_contact", "unavailable")
    hass.states.async_set("binary_sensor.ashtray_contact", "on")  # back from unavailable: no count
    hass.states.async_set("event.zigbee_button", "2026-11-01T09:00:00+00:00")  # counts
    await hass.async_block_till_done()
    assert len(config_entry.runtime_data.active) == 2


async def test_mute_suppresses_reminders(
    hass: HomeAssistant, options: dict[str, Any], freezer: FrozenDateTimeFactory
) -> None:
    options.update({CONF_INTERVAL_MODE: MODE_FIXED, CONF_FIXED_MINUTES: 30})
    config_entry = MockConfigEntry(domain=DOMAIN, data={}, options=options)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    reminders = async_capture_events(hass, EVENT_REMINDER)

    await _press(hass)
    assert _value(hass, "binary_sensor.ciggie_counter_allowed") == "off"
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.ciggie_counter_mute_reminders"}, blocking=True)
    freezer.tick(timedelta(minutes=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert reminders == []
    assert _value(hass, "binary_sensor.ciggie_counter_allowed") == "on"

    # unmuted: the break that ended while muted does not come afterwards
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.ciggie_counter_mute_reminders"}, blocking=True)
    freezer.tick(timedelta(minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert reminders == []

    await _press(hass)
    freezer.tick(timedelta(minutes=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert [e.data["type"] for e in reminders] == ["next_allowed"]


async def test_count_check_restarts_on_count(
    hass: HomeAssistant, options: dict[str, Any], freezer: FrozenDateTimeFactory
) -> None:
    options.update({CONF_COUNT_CHECK_REMINDER: True, CONF_COUNT_CHECK_MINUTES: 10})
    config_entry = MockConfigEntry(domain=DOMAIN, data={}, options=options)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    reminders = async_capture_events(hass, EVENT_REMINDER)

    freezer.tick(timedelta(minutes=1))
    async_fire_time_changed(hass)  # starts the clock
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=8))
    async_fire_time_changed(hass)
    await _press(hass)  # counting restarts the 10 minutes
    freezer.tick(timedelta(minutes=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert reminders == []
    freezer.tick(timedelta(minutes=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert [e.data["type"] for e in reminders] == ["count_check"]


async def test_computed_break(hass: HomeAssistant, options: dict[str, Any], freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to("2026-11-01T08:00:00+01:00")
    options.update({CONF_INTERVAL_MODE: MODE_COMPUTED, CONF_DAILY_TARGET: 18, CONF_WAKE_HOURS: 18})
    config_entry = MockConfigEntry(domain=DOMAIN, data={}, options=options)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    counter = config_entry.runtime_data

    assert counter.interval_minutes() == pytest.approx(60)  # 18 h / 18 before the first one
    await _press(hass)  # 08:00, 17 left for 18 h
    assert counter.interval_minutes() == pytest.approx(18 * 60 / 17)
    await _press(hass)
    await _press(hass)  # smoked faster: 15 left for the same time, longer breaks
    assert counter.interval_minutes() == pytest.approx(18 * 60 / 15)


async def test_computed_next_allowed_is_fixed(
    hass: HomeAssistant, options: dict[str, Any], freezer: FrozenDateTimeFactory
) -> None:
    """The shown time does not move: it is where last + shrinking break meets the clock."""
    freezer.move_to("2026-11-01T08:00:00+01:00")
    options.update({CONF_INTERVAL_MODE: MODE_COMPUTED, CONF_DAILY_TARGET: 3, CONF_WAKE_HOURS: 4})
    config_entry = MockConfigEntry(domain=DOMAIN, data={}, options=options)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    counter = config_entry.runtime_data

    await _press(hass)  # 08:00, day ends 12:00, 2 left -> (08:00 * 2 + 12:00) / 3 = 09:20
    expected = dt_util.parse_datetime("2026-11-01T09:20:00+01:00")
    assert counter.next_allowed() == expected
    freezer.move_to("2026-11-01T09:00:00+01:00")
    assert counter.next_allowed() == expected  # same time later on
    freezer.move_to("2026-11-01T09:20:00+01:00")
    # at that time the moving "last + break" meets it as well
    assert counter.last + timedelta(minutes=counter.interval_minutes()) == expected


async def test_day_start_hour(hass: HomeAssistant, options: dict[str, Any], freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to("2026-11-02T01:30:00+01:00")
    options[CONF_DAY_START_HOUR] = 4
    config_entry = MockConfigEntry(domain=DOMAIN, data={}, options=options)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    counter = config_entry.runtime_data
    await _press(hass)
    assert str(counter.today()) == "2026-11-01"  # 01:30 still belongs to Sunday
    freezer.move_to("2026-11-02T04:10:00+01:00")
    assert counter.count_today() == 0
    assert counter.count_yesterday() == 1


async def test_sync_api(hass: HomeAssistant, entry: MockConfigEntry, hass_client) -> None:
    client = await hass_client()
    await _press(hass)

    resp = await client.get("/api/ciggie_counter/v2/entries?since=0")
    assert resp.status == 200
    data = await resp.json()
    assert data["rev"] == 1 and len(data["entries"]) == 1
    own = data["entries"][0]

    now = dt_util.now().isoformat()
    resp = await client.post("/api/ciggie_counter/v2/entries", json={"entries": [
        {"id": "phone-0000-0001", "ts": now, "source": "ashtray", "deleted": False},
        {"id": "phone-0000-0002", "ts": now, "source": "manual", "deleted": False},  # same second, still two
        {"id": own["id"], "deleted": True},
        {"id": "bad", "ts": now},
    ]})
    assert (await resp.json()) == {"rev": 4, "accepted": 3}
    await hass.async_block_till_done()
    assert _value(hass, TODAY) == "2"

    # sending again changes nothing, a tombstone wins
    resp = await client.post("/api/ciggie_counter/v2/entries", json={"entries": [
        {"id": own["id"], "ts": now, "deleted": False},
        {"id": "phone-0000-0001", "ts": now, "deleted": False},
    ]})
    assert (await resp.json())["rev"] == 4
    resp = await client.get("/api/ciggie_counter/v2/entries?since=1")
    assert len((await resp.json())["entries"]) == 3

    resp = await client.post("/api/ciggie_counter/mute", data={"muted": "1"})
    assert (await resp.json()) == {"muted": True}
    assert _value(hass, "switch.ciggie_counter_mute_reminders") == "on"

    resp = await client.post("/api/ciggie_counter/ashtray", data={"battery": "137", "firmware": "0.4", "bogus": "x"})
    state = await resp.json()
    assert state["battery"] == 100 and state["firmware"] == "0.4" and "bogus" not in state
    await hass.async_block_till_done()
    assert _value(hass, "sensor.ciggie_counter_butt_bin_battery") == "100"

    resp = await client.get("/api/ciggie_counter/today")
    summary = await resp.json()
    assert summary["todayCount"] == 2 and summary["muted"] is True


async def test_api_requires_auth(hass: HomeAssistant, entry: MockConfigEntry, hass_client_no_auth) -> None:
    client = await hass_client_no_auth()
    resp = await client.get("/api/ciggie_counter/v2/entries")
    assert resp.status == 401


async def test_data_survives_reload(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await _press(hass)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert _value(hass, TODAY) == "1"


async def test_own_button_is_no_count_source(hass: HomeAssistant, options: dict[str, Any]) -> None:
    """Configuring the integration's own button as a source must not count twice."""
    options[CONF_COUNT_SOURCES] = ["button.ciggie_counter_count_cigarette"]
    config_entry = MockConfigEntry(domain=DOMAIN, data={}, options=options)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    # the entity exists only after the first setup: reload so the filter sees it in the registry
    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    await _press(hass)
    assert len(config_entry.runtime_data.active) == 1


async def test_rules_api(hass: HomeAssistant, entry: MockConfigEntry, hass_client) -> None:
    """The phone (Home Assistant as sync target) exchanges the rules like with Nextcloud."""
    client = await hass_client()
    resp = await client.get("/api/ciggie_counter/v2/settings")
    assert (await resp.json())["updatedAt"] == 0

    resp = await client.post("/api/ciggie_counter/v2/settings", json={
        "updatedAt": 1_900_000_000, "intervalMode": "off", "dayStartHour": 3, "countCheckIntervalMinutes": 15})
    data = await resp.json()
    assert data["updatedAt"] == 1_900_000_000 and data["intervalMode"] == "off" and data["dayStartHour"] == 3
    assert data["dailyTarget"] is None
    counter = entry.runtime_data
    assert counter.day_start_hour == 3

    # older rules are ignored, the answer is the valid state
    resp = await client.post("/api/ciggie_counter/v2/settings", json={"updatedAt": 5, "intervalMode": "fixed"})
    assert (await resp.json())["intervalMode"] == "off"


async def test_options_flow_marks_rule_changes(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    base = {k: v for k, v in DEFAULTS.items() if k != "count_sources"}
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], base)
    assert result["data"][CONF_RULES_UPDATED_AT] == 0  # nothing changed
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], base | {CONF_DAILY_TARGET: 10})
    assert result["data"][CONF_RULES_UPDATED_AT] > 1_700_000_000


async def test_invalid_rules_are_ignored(hass: HomeAssistant, entry: MockConfigEntry, hass_client) -> None:
    client = await hass_client()
    resp = await client.post("/api/ciggie_counter/v2/settings", data="{not json", headers={"Content-Type": "application/json"})
    assert resp.status == 400
    resp = await client.post("/api/ciggie_counter/v2/settings", json={"dailyTarget": 5})
    assert resp.status == 400
    resp = await client.post("/api/ciggie_counter/v2/settings", json={
        "updatedAt": 1_900_000_000, "wakeHours": "nan", "dailyTarget": 1e300, "costPerCigarette": -1})
    data = await resp.json()
    assert data["wakeHours"] == 18.0 and data["dailyTarget"] is None and data["costPerCigarette"] is None
    resp = await client.get("/api/ciggie_counter/today")
    assert resp.status == 200


async def test_rules_only_options_change_keeps_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    counter = entry.runtime_data
    base = {k: v for k, v in DEFAULTS.items() if k != "count_sources"}
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(result["flow_id"], base | {CONF_DAILY_TARGET: 12})
    await hass.async_block_till_done()
    assert entry.runtime_data is counter and counter.options[CONF_DAILY_TARGET] == 12
