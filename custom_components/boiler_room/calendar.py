"""Elections calendar."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
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
    async_add_entities([ElectionsCalendar(entry.runtime_data.elections, entry)])


class ElectionsCalendar(MidnightRefresh, CalendarEntity):
    """All-day events for upcoming elections."""

    coordinator: ElectionsCoordinator
    _attr_translation_key = "elections"
    _attr_icon = "mdi:calendar-star"

    def __init__(self, coordinator: ElectionsCoordinator, entry) -> None:
        super().__init__(coordinator, entry, "elections", "elections_calendar")

    def _events(self) -> list[CalendarEvent]:
        return [
            CalendarEvent(
                start=e["date"],
                end=e["date"] + timedelta(days=1),
                summary=e["name"],
                description=("Check your county board of elections for polling places and hours."),
                uid=f"{e['date'].isoformat()}-{e['scope']}",
            )
            for e in self.coordinator.data or []
        ]

    @property
    def event(self) -> CalendarEvent | None:
        today = dt_util.now().date()
        return next((ev for ev in self._events() if ev.end > today), None)

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        start: date = dt_util.as_local(start_date).date()
        end: date = dt_util.as_local(end_date).date()
        return [ev for ev in self._events() if ev.end > start and ev.start <= end]
