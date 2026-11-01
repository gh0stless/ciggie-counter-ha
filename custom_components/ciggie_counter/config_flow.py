"""Config flow: one click to add, everything else in the options."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_COST,
    CONF_COUNT_CHECK_MINUTES,
    CONF_COUNT_CHECK_REMINDER,
    CONF_COUNT_SOURCES,
    CONF_DAILY_TARGET,
    CONF_DAY_START_HOUR,
    CONF_FIXED_MINUTES,
    CONF_INTERVAL_MODE,
    CONF_NEXT_ALLOWED_REMINDER,
    CONF_WAKE_HOURS,
    COUNT_SOURCE_DOMAINS,
    DEFAULTS,
    DOMAIN,
    MODE_COMPUTED,
    MODE_FIXED,
    MODE_OFF,
    NAME,
)


class CiggieConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title=NAME, data={}, options=dict(DEFAULTS))
        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return CiggieOptionsFlow()


def _number(minimum: float, maximum: float, step: float = 1) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(min=minimum, max=maximum, step=step, mode=selector.NumberSelectorMode.BOX)
    )


class CiggieOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        o = {**DEFAULTS, **self.config_entry.options}

        def opt(key: str) -> vol.Optional:
            # only pre-fill what is set, so optional fields can be cleared
            return vol.Optional(key, description={"suggested_value": o[key]} if o.get(key) is not None else None)

        schema = vol.Schema(
            {
                opt(CONF_COST): _number(0, 100, 0.01),
                vol.Required(CONF_DAY_START_HOUR, default=o[CONF_DAY_START_HOUR]): _number(0, 23),
                vol.Required(CONF_INTERVAL_MODE, default=o[CONF_INTERVAL_MODE]): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=[MODE_COMPUTED, MODE_FIXED, MODE_OFF],
                                                  translation_key=CONF_INTERVAL_MODE)
                ),
                opt(CONF_DAILY_TARGET): _number(1, 200),
                vol.Required(CONF_WAKE_HOURS, default=o[CONF_WAKE_HOURS]): _number(1, 24, 0.5),
                opt(CONF_FIXED_MINUTES): _number(1, 1440),
                vol.Required(CONF_NEXT_ALLOWED_REMINDER, default=o[CONF_NEXT_ALLOWED_REMINDER]): selector.BooleanSelector(),
                vol.Required(CONF_COUNT_CHECK_REMINDER, default=o[CONF_COUNT_CHECK_REMINDER]): selector.BooleanSelector(),
                vol.Required(CONF_COUNT_CHECK_MINUTES, default=o[CONF_COUNT_CHECK_MINUTES]): _number(1, 1440),
                vol.Optional(CONF_COUNT_SOURCES, default=o[CONF_COUNT_SOURCES]): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=COUNT_SOURCE_DOMAINS, multiple=True)
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
