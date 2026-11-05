"""HTTP API for the Android app (and anything else): same sync v2 as the Nextcloud app.

All views need Home Assistant authentication (long-lived access token as Bearer).
Base path: /api/ciggie_counter

  GET  /today             summary (counts, cost, break, mute, Butt Bin)
  GET  /v2/entries?since=N  records changed after revision N -> {"rev", "entries"}
  POST /v2/entries        {"entries": [{"id", "ts", "source", "deleted"}]} -> {"rev", "accepted"}
  GET  /mute              {"muted": bool}
  POST /mute              muted=1|0 (form or JSON) -> {"muted": bool}
  GET  /ashtray           Butt Bin device state
  POST /ashtray           partial update (form or JSON), optional "at"
  GET  /v2/settings       rules (cost, breaks, …) with "updatedAt"
  POST /v2/settings       JSON rules, taken over if "updatedAt" is newer -> current rules
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.http import KEY_HASS
from homeassistant.util import dt as dt_util

from .const import DOMAIN

BASE = f"/api/{DOMAIN}"


@callback
def async_register_views(hass: HomeAssistant) -> None:
    for view in (TodayView, EntriesView, MuteView, AshtrayView, SettingsView):
        hass.http.register_view(view())


def _counter(request: web.Request):
    from . import get_counter  # import here: __init__ imports this module

    return get_counter(request.app[KEY_HASS])


async def _params(request: web.Request) -> dict[str, Any]:
    if request.content_type == "application/json":
        data = await request.json()
        return data if isinstance(data, dict) else {}
    return dict(await request.post())


class _Base(HomeAssistantView):
    requires_auth = True

    def unavailable(self) -> web.Response:
        return self.json_message("Ciggie Counter is not set up", HTTPStatus.SERVICE_UNAVAILABLE)


class TodayView(_Base):
    url = f"{BASE}/today"
    name = f"api:{DOMAIN}:today"

    async def get(self, request: web.Request) -> web.Response:
        if (counter := _counter(request)) is None:
            return self.unavailable()
        return self.json(counter.summary())


class EntriesView(_Base):
    url = f"{BASE}/v2/entries"
    name = f"api:{DOMAIN}:entries"

    async def get(self, request: web.Request) -> web.Response:
        if (counter := _counter(request)) is None:
            return self.unavailable()
        try:
            since = max(0, int(request.query.get("since", "0")))
        except ValueError:
            return self.json_message("since must be a number", HTTPStatus.BAD_REQUEST)
        return self.json(counter.records_since(since))

    async def post(self, request: web.Request) -> web.Response:
        if (counter := _counter(request)) is None:
            return self.unavailable()
        try:
            data = await request.json()
        except ValueError:
            return self.json_message("JSON body expected", HTTPStatus.BAD_REQUEST)
        entries = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            return self.json_message("entries missing", HTTPStatus.BAD_REQUEST)
        return self.json(counter.async_merge(entries))


class MuteView(_Base):
    url = f"{BASE}/mute"
    name = f"api:{DOMAIN}:mute"

    async def get(self, request: web.Request) -> web.Response:
        if (counter := _counter(request)) is None:
            return self.unavailable()
        return self.json({"muted": counter.muted})

    async def post(self, request: web.Request) -> web.Response:
        if (counter := _counter(request)) is None:
            return self.unavailable()
        value = (await _params(request)).get("muted")
        counter.async_set_muted(str(value).lower() in ("1", "true", "on"))
        return self.json({"muted": counter.muted})


class AshtrayView(_Base):
    url = f"{BASE}/ashtray"
    name = f"api:{DOMAIN}:ashtray"

    async def get(self, request: web.Request) -> web.Response:
        if (counter := _counter(request)) is None:
            return self.unavailable()
        return self.json(counter.ashtray)

    async def post(self, request: web.Request) -> web.Response:
        if (counter := _counter(request)) is None:
            return self.unavailable()
        params = await _params(request)
        at = dt_util.parse_datetime(str(params.get("at", ""))) if params.get("at") else None
        return self.json(counter.async_update_ashtray(params, at))


class SettingsView(_Base):
    url = f"{BASE}/v2/settings"
    name = f"api:{DOMAIN}:settings"

    async def get(self, request: web.Request) -> web.Response:
        if (counter := _counter(request)) is None:
            return self.unavailable()
        return self.json(counter.rules())

    async def post(self, request: web.Request) -> web.Response:
        if (counter := _counter(request)) is None:
            return self.unavailable()
        try:
            params = await _params(request)
        except ValueError:
            return self.json_message("JSON body expected", HTTPStatus.BAD_REQUEST)
        if "updatedAt" not in params:
            return self.json_message("updatedAt missing", HTTPStatus.BAD_REQUEST)
        counter.async_apply_rules(params)
        return self.json(counter.rules())
