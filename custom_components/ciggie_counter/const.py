"""Constants for Ciggie Counter."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "ciggie_counter"
NAME: Final = "Ciggie Counter"

STORAGE_VERSION: Final = 1

# Options (all optional, defaults below)
CONF_COST: Final = "cost_per_cigarette"
CONF_DAY_START_HOUR: Final = "day_start_hour"
CONF_INTERVAL_MODE: Final = "interval_mode"
CONF_DAILY_TARGET: Final = "daily_target"
CONF_WAKE_HOURS: Final = "wake_hours"
CONF_FIXED_MINUTES: Final = "fixed_interval_minutes"
CONF_NEXT_ALLOWED_REMINDER: Final = "next_allowed_reminder"
CONF_COUNT_CHECK_REMINDER: Final = "count_check_reminder"
CONF_COUNT_CHECK_MINUTES: Final = "count_check_minutes"
CONF_COUNT_SOURCES: Final = "count_sources"

MODE_COMPUTED: Final = "computed"
MODE_FIXED: Final = "fixed"
MODE_OFF: Final = "off"

DEFAULTS: Final[dict] = {
    CONF_DAY_START_HOUR: 0,
    CONF_INTERVAL_MODE: MODE_COMPUTED,
    CONF_WAKE_HOURS: 18.0,
    CONF_NEXT_ALLOWED_REMINDER: True,
    CONF_COUNT_CHECK_REMINDER: False,
    CONF_COUNT_CHECK_MINUTES: 10,
    CONF_COUNT_SOURCES: [],
}

# Entities that can count a cigarette when they fire (Zigbee buttons, contacts, ...)
COUNT_SOURCE_DOMAINS: Final = ["binary_sensor", "input_boolean", "event", "input_button", "button"]

# Events fired on the HA bus
EVENT_COUNTED: Final = f"{DOMAIN}_counted"
EVENT_REMINDER: Final = f"{DOMAIN}_reminder"
REMINDER_NEXT_ALLOWED: Final = "next_allowed"
REMINDER_COUNT_CHECK: Final = "count_check"

# Dispatcher signal: data changed, entities refresh
SIGNAL_UPDATE: Final = f"{DOMAIN}_update"

# Butt Bin device state; fields the hardware cannot report yet stay None (placeholders)
ASHTRAY_FIELDS: Final[dict[str, type]] = {
    "battery": int,  # %
    "batteryMv": int,
    "firmware": str,
    "rssi": int,
    "airQuality": int,  # IAQ index, placeholder
    "voc": float,  # ppm, placeholder
    "temperature": float,  # degC, placeholder
    "humidity": float,  # % rH, placeholder
    "sprayLevel": int,  # %, placeholder
    "sprayCount": int,  # placeholder
    "lastSprayAt": str,  # ISO time, placeholder
    "display": str,  # placeholder
}
