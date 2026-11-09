"""Entries, statistics and reminders of Ciggie Counter.

Same model as the Nextcloud app "cigarettecounter" and the Android app, so all of them can
sync with each other (sync v2): every cigarette is a record with a random id, deletions are
kept as tombstones, every change gets a new revision number.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta
import logging
import math
from typing import Any
import uuid

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    ASHTRAY_FIELDS,
    CONF_COST,
    CONF_COUNT_CHECK_MINUTES,
    CONF_COUNT_CHECK_REMINDER,
    CONF_DAILY_TARGET,
    CONF_DAY_START_HOUR,
    CONF_FIXED_MINUTES,
    CONF_INTERVAL_MODE,
    CONF_NEXT_ALLOWED_REMINDER,
    CONF_RULES_UPDATED_AT,
    CONF_WAKE_HOURS,
    DEFAULTS,
    DOMAIN,
    EVENT_COUNTED,
    EVENT_REMINDER,
    MODE_COMPUTED,
    MODE_FIXED,
    MODE_OFF,
    REMINDER_COUNT_CHECK,
    REMINDER_NEXT_ALLOWED,
    RULE_KEYS,
    SIGNAL_UPDATE,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

# Entries this much older than "now" are backfills: no announcement, no reminder reset
FRESH = timedelta(minutes=10)
# A "next allowed" this far in the past (e.g. after a restart) is not announced any more
STALE = timedelta(minutes=15)


def _parse(ts: str) -> datetime | None:
    parsed = dt_util.parse_datetime(ts)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.get_default_time_zone())
    return parsed


def _number(value: Any) -> float | None:
    """A finite number from JSON or a form, None otherwise (also for NaN and infinity)."""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _rule_value(key: str, value: Any) -> Any:
    """A rule as clean JSON value: None for "not set" or out of range, numbers as int or float."""
    if value is None or value == "":
        return None
    if key == CONF_INTERVAL_MODE:
        return value if value in (MODE_COMPUTED, MODE_FIXED, MODE_OFF) else MODE_COMPUTED
    number = _number(value)
    if number is None:
        return None
    if key == CONF_COST:
        return number if 0 <= number <= 100 else None
    if key == CONF_WAKE_HOURS:
        return number if 1 <= number <= 24 else None
    if key == CONF_DAY_START_HOUR:
        return int(number) if 0 <= number <= 23 else None
    # daily target, fixed minutes, count reminder interval
    return int(number) if 1 <= number <= 1440 else None


class CiggieCounter:
    """All data of one Ciggie Counter instance."""

    def __init__(self, hass: HomeAssistant, entry_id: str, options: dict[str, Any]) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.options: dict[str, Any] = {**DEFAULTS, **options}
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}")
        self.rev = 0
        self.records: list[dict[str, Any]] = []
        self.muted = False
        self.ashtray: dict[str, Any] = {key: None for key in ASHTRAY_FIELDS} | {"updatedAt": None}
        self._last_notified_for: str | None = None
        self._last_count_check_at: str | None = None
        self._active: list[datetime] = []
        # Nextcloud as hub (optional): last revision fetched, mute changed here and not yet sent
        self.hub_rev = 0
        self.hub_key: str | None = None
        self.mute_dirty = False
        self.last_hub_sync: datetime | None = None
        # called after local changes, e.g. to sync soon
        self.on_local_change: Callable[[], None] | None = None
        # timer for the next due reminder (to the second, like the phone's exact alarms)
        self._wake_unsub: Callable[[], None] | None = None

    # ------------------------------------------------------------------ storage

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.rev = int(data.get("rev", 0))
        self.records = list(data.get("records", []))
        self.muted = bool(data.get("muted", False))
        self.ashtray |= {k: v for k, v in data.get("ashtray", {}).items() if k in self.ashtray}
        self._last_notified_for = data.get("last_notified_for")
        self._last_count_check_at = data.get("last_count_check_at")
        self.hub_rev = int(data.get("hub_rev", 0))
        self.hub_key = data.get("hub_key")
        self.mute_dirty = bool(data.get("mute_dirty", False))
        self._rebuild()

    def _data(self) -> dict[str, Any]:
        return {
            "rev": self.rev,
            "records": self.records,
            "muted": self.muted,
            "ashtray": self.ashtray,
            "last_notified_for": self._last_notified_for,
            "last_count_check_at": self._last_count_check_at,
            "hub_rev": self.hub_rev,
            "hub_key": self.hub_key,
            "mute_dirty": self.mute_dirty,
        }

    @callback
    def _changed(self, local: bool = True) -> None:
        self._rebuild()
        self._store.async_delay_save(self._data, 1)
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)
        self._schedule_wake()
        if local and self.on_local_change is not None:
            self.on_local_change()

    def _rebuild(self) -> None:
        active = [p for r in self.records if not r.get("deleted") and (p := _parse(r["ts"])) is not None]
        self._active = sorted(active)

    async def async_flush(self) -> None:
        await self._store.async_save(self._data())

    # ------------------------------------------------------------------ rules

    @property
    def rules_updated_at(self) -> int:
        return int(self.options.get(CONF_RULES_UPDATED_AT) or 0)

    def rules(self) -> dict[str, Any]:
        """The rules in the format of /v2/settings (Nextcloud app 0.11.0, Android 1.0.13)."""
        out: dict[str, Any] = {"updatedAt": self.rules_updated_at}
        for api, key in RULE_KEYS.items():
            out[api] = _rule_value(key, self.options.get(key))
        return out

    @callback
    def async_apply_rules(self, rules: dict[str, Any], from_hub: bool = False) -> bool:
        """Take over the rules of another device if they are newer. A rule that is missing or
        empty means "not set". Rules from a phone (from_hub False) go on to Nextcloud."""
        stamp = _number(rules.get("updatedAt"))
        if stamp is None:
            return False
        at = int(stamp)
        if at <= self.rules_updated_at:
            return False
        changes: dict[str, Any] = {CONF_RULES_UPDATED_AT: at}
        for api, key in RULE_KEYS.items():
            changes[key] = _rule_value(key, rules.get(api))
        self.options = {**self.options, **changes}
        for key, value in changes.items():
            if value is None:
                self.options[key] = DEFAULTS.get(key)

        entry = self.hass.config_entries.async_get_entry(self.entry_id)
        if entry is not None:
            options = {**entry.options, **changes}
            for key, value in changes.items():
                if value is None:
                    options.pop(key, None)
            # only rules change: the update listener sees that and does not reload the entry
            self.hass.config_entries.async_update_entry(entry, options=options)
        self._changed(local=not from_hub)
        return True

    # ------------------------------------------------------------------ days

    @property
    def day_start_hour(self) -> int:
        return int(self.options.get(CONF_DAY_START_HOUR, 0))

    def logical_date(self, when: datetime) -> date:
        """Counting day: before the day-start hour a cigarette still belongs to the day before."""
        local = dt_util.as_local(when)
        day = local.date()
        return day - timedelta(days=1) if local.hour < self.day_start_hour else day

    def today(self, now: datetime | None = None) -> date:
        return self.logical_date(now or dt_util.now())

    @property
    def active(self) -> list[datetime]:
        """All cigarettes (not deleted), oldest first."""
        return self._active

    def on_day(self, day: date) -> list[datetime]:
        return [ts for ts in self._active if self.logical_date(ts) == day]

    def count_today(self) -> int:
        return len(self.on_day(self.today()))

    def count_yesterday(self) -> int:
        return len(self.on_day(self.today() - timedelta(days=1)))

    def count_month(self) -> int:
        today = self.today()
        return sum(1 for ts in self._active if (d := self.logical_date(ts)).year == today.year and d.month == today.month)

    def count_year(self) -> int:
        today = self.today()
        return sum(1 for ts in self._active if self.logical_date(ts).year == today.year)

    @property
    def cost_per_cigarette(self) -> float | None:
        cost = self.options.get(CONF_COST)
        return float(cost) if cost not in (None, "") else None

    def cost_today(self) -> float | None:
        cost = self.cost_per_cigarette
        return round(self.count_today() * cost, 2) if cost is not None else None

    @property
    def last(self) -> datetime | None:
        return self._active[-1] if self._active else None

    # ------------------------------------------------------------------ breaks

    def interval_minutes(self, now: datetime | None = None) -> float | None:
        """Break between cigarettes, like the Nextcloud app.

        computed: today's remaining allowance is spread over the rest of the waking time
        (anchored at the first cigarette of the day); smoking faster makes breaks longer.
        """
        mode = self.options.get(CONF_INTERVAL_MODE)
        if mode == MODE_OFF:
            return None
        if mode == MODE_FIXED:
            fixed = self.options.get(CONF_FIXED_MINUTES)
            return float(fixed) if fixed else None
        target = self.options.get(CONF_DAILY_TARGET)
        if not target or int(target) <= 0:
            return None
        target = int(target)
        wake = float(self.options.get(CONF_WAKE_HOURS) or 18.0) * 3600
        now = now or dt_util.now()
        today = self.on_day(self.today(now))
        if not today:
            return wake / target / 60
        remaining = max(target - len(today), 1)
        seconds_left = (today[0] + timedelta(seconds=wake) - now).total_seconds()
        if seconds_left <= 0:
            return wake / target / 60  # waking time over: the basic break, not ever shorter ones
        return seconds_left / remaining / 60

    def next_allowed(self, now: datetime | None = None) -> datetime | None:
        """From when the next cigarette is allowed.

        computed: the break shrinks over time (less waking time left), so "last + current
        break" moves. Allowed is the point where they meet: t = (last * rest + day end) / (rest + 1),
        the same fixed time as in the Android and Nextcloud apps.
        """
        minutes = self.interval_minutes(now)
        last = self.last
        if minutes is None or last is None:
            return None
        if self.options.get(CONF_INTERVAL_MODE) == MODE_COMPUTED:
            now = now or dt_util.now()
            target = int(self.options[CONF_DAILY_TARGET])
            wake = timedelta(hours=float(self.options.get(CONF_WAKE_HOURS) or 18.0))
            today = self.on_day(self.today(now))
            if not today:
                return last + wake / target
            day_end = today[0] + wake
            if last >= day_end:
                return last + wake / target  # smoked after the waking time: the basic break
            remaining = max(target - len(today), 1)
            return last + (day_end - last) / (remaining + 1)
        return last + timedelta(seconds=round(minutes * 60))

    # ------------------------------------------------------------------ counting

    @callback
    def async_add(self, when: datetime | None = None, source: str = "home_assistant") -> None:
        """Count a cigarette (now or a backfill)."""
        when = when or dt_util.now()
        self.rev += 1
        self.records.append(
            {"id": str(uuid.uuid4()), "ts": dt_util.as_local(when).isoformat(timespec="seconds"),
             "source": source, "deleted": False, "rev": self.rev, "dirty": True}
        )
        self._counted([when], source)
        self._changed()

    @callback
    def async_delete_last(self) -> bool:
        """Delete the newest cigarette (tombstone)."""
        newest = None
        for record in self.records:
            if record.get("deleted") or (ts := _parse(record["ts"])) is None:
                continue
            if newest is None or ts >= newest[0]:
                newest = (ts, record)
        if newest is None:
            return False
        self.rev += 1
        newest[1]["deleted"] = True
        newest[1]["rev"] = self.rev
        newest[1]["dirty"] = True
        self._changed()
        return True

    @callback
    def _counted(self, added: list[datetime], source: str) -> None:
        """Side effects of new entries: count-check restarts, announcement event for fresh ones."""
        fresh = [ts for ts in added if ts >= dt_util.now() - FRESH]
        if not fresh:
            return
        newest = max(fresh)
        # the count reminder restarts from the time of the cigarette, not from when it got here:
        # so all devices run the same interval
        last_check = _parse(self._last_count_check_at) if self._last_count_check_at else None
        if last_check is None or newest > last_check:
            self._last_count_check_at = newest.isoformat()
        self._rebuild()
        number = sum(1 for ts in self.on_day(self.today()) if ts <= newest)
        self.hass.bus.async_fire(
            EVENT_COUNTED,
            {"number": number, "source": source, "timestamp": newest.isoformat(), "muted": self.muted},
        )

    # ------------------------------------------------------------------ sync v2

    def records_since(self, since: int) -> dict[str, Any]:
        return {"rev": self.rev, "entries": [r for r in self.records if r["rev"] > since]}

    @callback
    def async_merge(self, incoming: Iterable[Any], source: str = "sync", from_hub: bool = False) -> dict[str, int]:
        """Take over records of another device. A tombstone always wins; times never change.

        from_hub: the records come from Nextcloud and need not be sent back; records from a phone
        (from_hub False) are forwarded to Nextcloud with the next sync.
        """
        index = {r["id"]: r for r in self.records}
        accepted = 0
        added: list[datetime] = []
        changed = False
        for item in incoming:
            if not isinstance(item, dict):
                continue
            rid = item.get("id")
            if not isinstance(rid, str) or not 8 <= len(rid) <= 64 or not all(c.isalnum() or c == "-" for c in rid):
                continue
            deleted = bool(item.get("deleted", False))
            known = index.get(rid)
            if known is not None:
                accepted += 1
                if deleted and not known.get("deleted"):
                    self.rev += 1
                    known["deleted"] = True
                    known["rev"] = self.rev
                    known["dirty"] = not from_hub
                    changed = True
                continue
            when = _parse(str(item.get("ts", "")))
            if when is None:
                continue
            accepted += 1
            self.rev += 1
            record = {"id": rid, "ts": when.isoformat(timespec="seconds"),
                      "source": str(item.get("source") or source)[:32], "deleted": deleted, "rev": self.rev,
                      "dirty": not from_hub}
            self.records.append(record)
            index[rid] = record
            changed = True
            if not deleted:
                added.append(when)
        if changed:
            if added:
                self._counted(added, source)
            self._changed(local=not from_hub)
        return {"rev": self.rev, "accepted": accepted}

    def dirty_records(self) -> list[dict[str, Any]]:
        """Records not yet sent to Nextcloud."""
        return [{k: r[k] for k in ("id", "ts", "source", "deleted")} for r in self.records if r.get("dirty")]

    @callback
    def async_mark_clean(self, sent: Iterable[dict[str, Any]]) -> None:
        """Sent successfully. A record deleted while it was on its way stays dirty."""
        state = {r["id"]: r["deleted"] for r in sent}
        for record in self.records:
            if record["id"] in state and bool(record.get("deleted")) == state[record["id"]]:
                record.pop("dirty", None)
        self._store.async_delay_save(self._data, 1)

    @callback
    def async_use_hub(self, key: str) -> None:
        """Another Nextcloud (or user) than before: fetch everything, send everything once."""
        if self.hub_key == key:
            return
        self.hub_key = key
        self.hub_rev = 0
        for record in self.records:
            record["dirty"] = True
        self.mute_dirty = False
        self._store.async_delay_save(self._data, 1)

    @callback
    def async_hub_synced(self, rev: int) -> None:
        self.hub_rev = rev
        self.last_hub_sync = dt_util.now()
        self._store.async_delay_save(self._data, 1)
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    # ------------------------------------------------------------------ mute, device

    @callback
    def async_set_muted(self, muted: bool, from_hub: bool = False) -> None:
        if self.muted != muted:
            self.muted = muted
            if not from_hub:
                self.mute_dirty = True
            self._changed(local=not from_hub)

    @callback
    def async_update_ashtray(
        self, values: dict[str, Any], at: datetime | None = None, from_hub: bool = False
    ) -> dict[str, Any]:
        """Partial update; only known fields, wrong types are ignored."""
        for key, kind in ASHTRAY_FIELDS.items():
            if key not in values or values[key] == "":
                continue
            value = values[key]
            if value is None:
                self.ashtray[key] = None
                continue
            try:
                self.ashtray[key] = kind(value)[:64] if kind is str else kind(value)
            except (TypeError, ValueError):
                continue
        if self.ashtray["battery"] is not None:
            self.ashtray["battery"] = max(0, min(100, self.ashtray["battery"]))
        self.ashtray["updatedAt"] = (at or dt_util.now()).isoformat()
        self._changed(local=not from_hub)
        return self.ashtray

    # ------------------------------------------------------------------ reminders

    @callback
    def async_tick(self, now: datetime | None = None) -> None:
        """Called every minute: fire due reminders as events, refresh time-based sensors."""
        now = now or dt_util.now()
        dirty = False

        if self.options.get(CONF_NEXT_ALLOWED_REMINDER):
            nxt = self.next_allowed(now)
            if nxt is not None and nxt <= now and self.last is not None:
                # once per break: keyed on the last cigarette, not on the time (that can jump,
                # e.g. when the waking time is over)
                key = self.last.isoformat()
                if self._last_notified_for != key:
                    # once per break; a break that ended long ago (restart) is not announced
                    self._last_notified_for = key
                    dirty = True
                    if not self.muted and nxt >= now - STALE:
                        self._remind(REMINDER_NEXT_ALLOWED)

        if self.options.get(CONF_COUNT_CHECK_REMINDER):
            minutes = int(self.options.get(CONF_COUNT_CHECK_MINUTES) or 10)
            last = _parse(self._last_count_check_at) if self._last_count_check_at else None
            if last is None:
                self._last_count_check_at = now.isoformat()
                dirty = True
            elif now - last >= timedelta(minutes=minutes):
                self._last_count_check_at = now.isoformat()
                dirty = True
                if not self.muted:
                    self._remind(REMINDER_COUNT_CHECK)

        if dirty:
            self._store.async_delay_save(self._data, 1)
        async_dispatcher_send(self.hass, SIGNAL_UPDATE)
        self._schedule_wake(now)

    def _due_times(self, now: datetime) -> list[datetime]:
        due = []
        if self.options.get(CONF_NEXT_ALLOWED_REMINDER):
            nxt = self.next_allowed(now)
            if nxt is not None:
                due.append(nxt)
        if self.options.get(CONF_COUNT_CHECK_REMINDER) and self._last_count_check_at:
            last = _parse(self._last_count_check_at)
            if last is not None:
                due.append(last + timedelta(minutes=int(self.options.get(CONF_COUNT_CHECK_MINUTES) or 10)))
        return due

    @callback
    def _schedule_wake(self, now: datetime | None = None) -> None:
        """Wake up exactly when the next reminder is due (the minute tick stays as a fallback)."""
        if self._wake_unsub is not None:
            self._wake_unsub()
            self._wake_unsub = None
        now = now or dt_util.now()
        upcoming = [t for t in self._due_times(now) if t > now]
        if upcoming:
            self._wake_unsub = async_track_point_in_time(self.hass, self._wake, min(upcoming))

    @callback
    def _wake(self, now: datetime) -> None:
        self._wake_unsub = None
        self.async_tick(now)

    @callback
    def async_shutdown(self) -> None:
        if self._wake_unsub is not None:
            self._wake_unsub()
            self._wake_unsub = None

    @callback
    def async_use_options(self, options: dict[str, Any]) -> None:
        """New options that need no reload (only the rules changed): use them right away."""
        self.options = {**DEFAULTS, **options}
        self._changed(local=False)

    @callback
    def _remind(self, kind: str) -> None:
        self.hass.bus.async_fire(EVENT_REMINDER, {"type": kind})

    # ------------------------------------------------------------------ summary

    def summary(self) -> dict[str, Any]:
        """Like GET /api/today of the Nextcloud app (subset), for the apps and dashboards."""
        nxt = self.next_allowed()
        return {
            "todayCount": self.count_today(),
            "yesterdayCount": self.count_yesterday(),
            "monthCount": self.count_month(),
            "yearCount": self.count_year(),
            "todayCost": self.cost_today(),
            "costPerCigarette": self.cost_per_cigarette,
            "intervalMinutes": self.interval_minutes(),
            "nextAllowedAt": nxt.isoformat() if nxt else None,
            "dayStartHour": self.day_start_hour,
            "muted": self.muted,
            "ashtray": self.ashtray,
            "ashtrayBattery": self.ashtray["battery"],
        }
