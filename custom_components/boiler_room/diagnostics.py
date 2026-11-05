"""Diagnostics download (keys redacted)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import BoilerRoomConfigEntry
from .const import (
    CONF_CIVIC_KEY,
    CONF_CONGRESS_KEY,
    CONF_COOK_EMAIL,
    CONF_COOK_PASSWORD,
    CONF_OPENSTATES_KEY,
)

REDACT = {
    CONF_CONGRESS_KEY,
    CONF_OPENSTATES_KEY,
    CONF_CIVIC_KEY,
    CONF_COOK_EMAIL,
    CONF_COOK_PASSWORD,
    "email",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: BoilerRoomConfigEntry
) -> dict[str, Any]:
    data = entry.runtime_data
    return {
        "data": async_redact_data(dict(entry.data), REDACT),
        "options": dict(entry.options),
        "coordinators": {
            c.name: {
                "last_update_success": c.last_update_success,
                "last_exception": repr(c.last_exception) if c.last_exception else None,
            }
            for c in data.coordinators
        },
        "home_state": data.reps.home_state if data.reps else None,
        "reps": async_redact_data(data.reps.data, REDACT) if data.reps else None,
        "federal_bills": data.federal_bills.data if data.federal_bills else None,
        "state_bills": data.state_bills.data if data.state_bills else None,
        "markets": data.markets.data if data.markets else None,
        "news_failed_sources": data.news.data.failed_sources if data.news else None,
        # Field names Cook actually returned, to check the parser against.
        "cook_fields": data.cook.data.raw_keys if data.cook and data.cook.data else None,
        "cook_summaries": data.cook.data.summaries if data.cook and data.cook.data else None,
        "elections": [{**e, "date": e["date"].isoformat()} for e in data.elections.data or []],
    }
