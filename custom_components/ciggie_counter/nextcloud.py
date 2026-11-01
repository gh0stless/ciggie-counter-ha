"""Optional sync with a Nextcloud running the app "cigarettecounter" (sync v2, from app 0.9.11).

Home Assistant sends its own new entries and deletions (and those it got from a phone) and
fetches only what changed on Nextcloud since the last sync. The first sync fetches everything.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from http import HTTPStatus
import logging
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.debounce import Debouncer
from homeassistant.util import dt as dt_util

from .counter import CiggieCounter

_LOGGER = logging.getLogger(__name__)

INTERVAL = timedelta(minutes=5)
TIMEOUT = aiohttp.ClientTimeout(total=20)


class NextcloudError(Exception):
    """Sync failed; the reason is a translation key: cannot_connect, invalid_auth, app_missing."""


class NextcloudSync:
    def __init__(self, hass: HomeAssistant, counter: CiggieCounter, url: str, user: str, password: str) -> None:
        self.hass = hass
        self.counter = counter
        self._base = url.rstrip("/") + "/index.php/apps/cigarettecounter/api"
        self._auth = aiohttp.BasicAuth(user, password)
        self._lock = asyncio.Lock()
        # several changes in a row (e.g. a batch from the Butt Bin) -> one sync
        self.debouncer = Debouncer(hass, _LOGGER, cooldown=10, immediate=False, function=self.async_sync_quietly)

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        session = async_get_clientsession(self.hass)
        # OCS-APIRequest: without it Nextcloud wants a CSRF token (HTTP 412)
        headers = {"OCS-APIRequest": "true", "Accept": "application/json"}
        try:
            async with session.request(
                method, self._base + path, auth=self._auth, headers=headers, timeout=TIMEOUT,
                allow_redirects=False, **kwargs,
            ) as resp:
                if resp.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FOUND):
                    raise NextcloudError("invalid_auth")
                if resp.status == HTTPStatus.NOT_FOUND:
                    raise NextcloudError("app_missing")
                if resp.status != HTTPStatus.OK:
                    raise NextcloudError("cannot_connect")
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise NextcloudError("cannot_connect") from err

    async def async_test(self) -> None:
        """For the options flow: login works and the server knows sync v2."""
        await self._request("GET", "/v2/entries", params={"since": "999999999"})

    async def async_sync(self) -> None:
        async with self._lock:
            counter = self.counter
            dirty = counter.dirty_records()
            if dirty:
                await self._request("POST", "/v2/entries", json={"entries": dirty})
                counter.async_mark_clean(dirty)

            if counter.mute_dirty:
                await self._request("POST", "/mute", data={"muted": "1" if counter.muted else "0"})
                counter.mute_dirty = False
            else:
                remote = await self._request("GET", "/mute")
                counter.async_set_muted(bool(remote.get("muted")), from_hub=True)

            # Butt Bin state (battery …): the phone reports it to Nextcloud, take it over if newer
            remote_ashtray = await self._request("GET", "/ashtray")
            remote_at = dt_util.parse_datetime(str(remote_ashtray.get("updatedAt") or ""))
            local_at = dt_util.parse_datetime(str(counter.ashtray.get("updatedAt") or ""))
            if remote_at is not None and (local_at is None or remote_at > local_at):
                counter.async_update_ashtray(remote_ashtray, remote_at, from_hub=True)

            changes = await self._request("GET", "/v2/entries", params={"since": str(counter.hub_rev)})
            counter.async_merge(changes.get("entries", []), source="nextcloud", from_hub=True)
            counter.async_hub_synced(int(changes.get("rev", counter.hub_rev)))

    async def async_sync_quietly(self) -> None:
        """Timer and after changes: errors are logged, the next run tries again."""
        try:
            await self.async_sync()
        except NextcloudError as err:
            _LOGGER.warning("Sync with Nextcloud failed: %s", err)

    @callback
    def async_schedule(self) -> None:
        self.hass.async_create_task(self.debouncer.async_call())
