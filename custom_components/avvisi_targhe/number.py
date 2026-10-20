"""Durata della pausa usata dall'interruttore "Avvisi" (in minuti)."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import AvvisiTargheConfigEntry
from .entity import AvvisiTargheEntity


async def async_setup_entry(hass: HomeAssistant, entry: AvvisiTargheConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([PauseMinutes(entry.runtime_data.hub)])


class PauseMinutes(AvvisiTargheEntity, NumberEntity):
    _attr_icon = "mdi:timer-sand"
    _attr_native_min_value = 5
    _attr_native_max_value = 7 * 24 * 60
    _attr_native_step = 5
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_mode = NumberMode.BOX

    def __init__(self, hub) -> None:
        super().__init__(hub, "durata_pausa")

    @property
    def native_value(self) -> float:
        return self.hub.pause_minutes

    async def async_set_native_value(self, value: float) -> None:
        self.hub.async_set_pause_minutes(int(value))
