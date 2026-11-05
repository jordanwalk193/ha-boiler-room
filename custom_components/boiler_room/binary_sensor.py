"""Election day binary sensor."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import BoilerRoomConfigEntry
from .coordinator import ElectionsCoordinator
from .entity import MidnightRefresh


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BoilerRoomConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([ElectionDaySensor(entry.runtime_data.elections, entry)])


class ElectionDaySensor(MidnightRefresh, BinarySensorEntity):
    """On when an election covering home is happening today."""

    coordinator: ElectionsCoordinator
    _attr_translation_key = "election_day"
    _attr_icon = "mdi:vote"

    def __init__(self, coordinator: ElectionsCoordinator, entry) -> None:
        super().__init__(coordinator, entry, "elections", "election_day")

    def _today(self) -> list[dict[str, Any]]:
        today = dt_util.now().date()
        return [e for e in self.coordinator.data or [] if e["date"] == today]

    @property
    def is_on(self) -> bool:
        return bool(self._today())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"elections": [e["name"] for e in self._today()]}
