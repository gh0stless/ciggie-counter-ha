"""Tests for the optional sync with the Nextcloud app (mocked server)."""

from __future__ import annotations

import json
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.ciggie_counter.const import CONF_NC_PASSWORD, CONF_NC_URL, CONF_NC_USER, DEFAULTS, DOMAIN
from custom_components.ciggie_counter.nextcloud import NextcloudSync

NC = "https://cloud.example.org"
API = f"{NC}/index.php/apps/cigarettecounter/api"


def _server_records(n: int) -> list[dict[str, Any]]:
    return [
        {"id": f"nc-0000-{i:04d}", "ts": f"2026-10-{1 + i % 28:02d}T10:00:00+02:00", "source": "legacy",
         "deleted": False, "rev": i + 1}
        for i in range(n)
    ]


async def _setup(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> MockConfigEntry:
    aioclient_mock.get(f"{API}/v2/entries", json={"rev": 5, "entries": _server_records(5)})
    aioclient_mock.get(f"{API}/mute", json={"muted": False})
    aioclient_mock.post(f"{API}/v2/entries", json={"rev": 6, "accepted": 1})
    aioclient_mock.post(f"{API}/mute", json={"muted": True})
    aioclient_mock.get(f"{API}/ashtray", json={"battery": 87, "firmware": "0.4", "updatedAt": "2026-11-01T08:00:00+00:00"})
    options = dict(DEFAULTS) | {CONF_NC_URL: NC, CONF_NC_USER: "andreas", CONF_NC_PASSWORD: "secret"}
    entry = MockConfigEntry(domain=DOMAIN, data={}, options=options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_first_sync_takes_over_server(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry = await _setup(hass, aioclient_mock)
    counter = entry.runtime_data
    assert len(counter.active) == 5
    assert counter.hub_rev == 5
    assert counter.dirty_records() == []  # nothing from Nextcloud is sent back
    assert counter.ashtray["battery"] == 87  # Butt Bin battery reported by the phone
    calls = [(m, str(u)) for m, u, *_ in aioclient_mock.mock_calls]
    assert ("GET", f"{API}/v2/entries?since=0") in calls
    assert not any(m == "POST" and u.endswith("/v2/entries") for m, u in calls)


async def test_own_entries_are_sent(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry = await _setup(hass, aioclient_mock)
    counter = entry.runtime_data
    counter.async_add(source="button")
    assert len(counter.dirty_records()) == 1
    await NextcloudSync(hass, counter, NC, "andreas", "secret").async_sync()
    posts = [c for c in aioclient_mock.mock_calls if c[0] == "POST" and str(c[1]).endswith("/v2/entries")]
    assert len(posts) == 1
    sent = posts[0][2]["entries"]
    assert len(sent) == 1 and sent[0]["source"] == "button" and sent[0]["deleted"] is False
    assert counter.dirty_records() == []
    assert counter.hub_rev == 5  # the mocked server still answers rev 5 for since=5


async def test_phone_entries_are_forwarded(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry = await _setup(hass, aioclient_mock)
    counter = entry.runtime_data
    counter.async_merge([{"id": "phone-0000-0001", "ts": dt_util.now().isoformat(), "deleted": False}], source="sync")
    assert [r["id"] for r in counter.dirty_records()] == ["phone-0000-0001"]


async def test_mute_goes_to_nextcloud(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry = await _setup(hass, aioclient_mock)
    counter = entry.runtime_data
    counter.async_set_muted(True)
    assert counter.mute_dirty
    await NextcloudSync(hass, counter, NC, "andreas", "secret").async_sync()
    posts = [c for c in aioclient_mock.mock_calls if c[0] == "POST" and str(c[1]).endswith("/mute")]
    assert len(posts) == 1
    assert not counter.mute_dirty


async def test_deleted_while_sending_stays_dirty(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry = await _setup(hass, aioclient_mock)
    counter = entry.runtime_data
    counter.async_add(source="button")
    sent = counter.dirty_records()
    counter.async_delete_last()  # deleted after the snapshot was taken
    counter.async_mark_clean(sent)
    assert [r["deleted"] for r in counter.dirty_records()] == [True]


async def test_options_flow_checks_nextcloud(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={}, options=dict(DEFAULTS))
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    aioclient_mock.get(f"{API}/v2/entries", status=401)

    base = {k: v for k, v in DEFAULTS.items() if k != "count_sources"}
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], base | {CONF_NC_URL: NC})
    assert result["errors"] == {"base": "nextcloud_incomplete"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], base | {CONF_NC_URL: NC, CONF_NC_USER: "andreas", CONF_NC_PASSWORD: "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/v2/entries", json={"rev": 0, "entries": []})
    aioclient_mock.get(f"{API}/mute", json={"muted": False})
    aioclient_mock.get(f"{API}/ashtray", json={"battery": None, "updatedAt": None})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], base | {CONF_NC_URL: NC, CONF_NC_USER: "andreas", CONF_NC_PASSWORD: "right"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
