"""Giorno mostrato nella pagina "Storico passaggi": oggi, ieri e i 5 giorni prima."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import AvvisiTargheConfigEntry
from .const import HISTORY_DAYS
from .entity import AvvisiTargheEntity

GIORNI = ("Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica")


async def async_setup_entry(hass: HomeAssistant, entry: AvvisiTargheConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([DaySelect(entry.runtime_data.hub)])


def day_label(offset: int) -> str:
    """"Oggi", "Ieri", poi "Sabato 17/10": le scelte cambiano a mezzanotte, il giorno scelto resta relativo."""
    if offset == 0:
        return "Oggi"
    if offset == 1:
        return "Ieri"
    day = dt_util.now().date() - timedelta(days=offset)
    return f"{GIORNI[day.weekday()]} {day:%d/%m}"


class DaySelect(AvvisiTargheEntity, SelectEntity):
    _attr_icon = "mdi:calendar-search"

    def __init__(self, hub) -> None:
        super().__init__(hub, "giorno")

    @property
    def options(self) -> list[str]:
        return [day_label(i) for i in range(HISTORY_DAYS)]

    @property
    def current_option(self) -> str:
        return day_label(self.hub.day_offset)

    async def async_select_option(self, option: str) -> None:
        self.hub.async_set_day(self.options.index(option))
