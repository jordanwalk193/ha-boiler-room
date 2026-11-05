"""Shared entity base."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .const import DOMAIN

GROUPS = {
    "reps": "Representatives",
    "legislation": "Legislation",
    "markets": "Prediction Markets",
    "ratings": "Race Ratings",
    "news": "News",
    "elections": "Elections",
}


class BoilerRoomEntity(CoordinatorEntity[DataUpdateCoordinator[Any]]):
    """Each source gets its own service device so dashboards group neatly."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[Any],
        entry: ConfigEntry,
        group: str,
        key: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_{group}")},
            name=f"Boiler Room {GROUPS[group]}",
            manufacturer="Boiler Room",
            model=GROUPS[group],
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://github.com/jordanwalk193/ha-boiler-room",
        )


class MidnightRefresh(BoilerRoomEntity):
    """Re-evaluate date-based state just after local midnight."""

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_time_change(self.hass, self._at_midnight, hour=0, minute=0, second=5)
        )

    @callback
    def _at_midnight(self, _now: datetime) -> None:
        self.async_write_ha_state()


def truncate(text: str | None, limit: int = 255) -> str | None:
    """Entity states max out at 255 characters."""
    if text is None:
        return None
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
