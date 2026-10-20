"""Evento "passaggio": entrata, uscita o passaggio senza verso, con targa e nome negli attributi."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import AvvisiTargheConfigEntry
from .const import VERSI
from .entity import AvvisiTargheEntity


async def async_setup_entry(hass: HomeAssistant, entry: AvvisiTargheConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([PassageEvent(entry.runtime_data.hub)])


class PassageEvent(AvvisiTargheEntity, EventEntity):
    _attr_event_types = list(VERSI)
    _attr_icon = "mdi:boom-gate"

    def __init__(self, hub) -> None:
        super().__init__(hub, "passaggio")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.hub.add_passage_listener(self._on_passage))

    def _on_passage(self, passage: dict[str, Any]) -> None:
        self._trigger_event(passage["verso"], {k: v for k, v in passage.items() if k != "verso"})
        self.async_write_ha_state()

    def _handle_update(self) -> None:
        # Lo stato dell'evento cambia solo con un passaggio, non con lo stato dell'app.
        pass
