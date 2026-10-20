"""Interruttore "Avvisi": spento = avvisi in pausa (per la durata scelta), acceso = avvisi attivi."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import AvvisiTargheConfigEntry
from .entity import AvvisiTargheEntity
from .services import who_of


async def async_setup_entry(hass: HomeAssistant, entry: AvvisiTargheConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([AlertsSwitch(entry.runtime_data.hub)])


class AlertsSwitch(AvvisiTargheEntity, SwitchEntity):
    _attr_icon = "mdi:bell-ring"

    def __init__(self, hub) -> None:
        super().__init__(hub, "avvisi")

    @property
    def available(self) -> bool:
        return self.hub.app_running

    @property
    def is_on(self) -> bool:
        # Comando appena dato e non ancora confermato dall'app: si mostra già lo stato voluto.
        if pending := self.hub.pending("pausa", "riprendi"):
            return pending["tipo"] == "riprendi"
        return not self.hub.paused

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"pausa_fino": (self.hub.status or {}).get("pausa_fino"),
                "in_attesa_dell_app": self.hub.pending("pausa", "riprendi") is not None}

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._check()
        self.hub.async_queue("riprendi", {}, await who_of(self.hass, self._context))

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._check()
        self.hub.async_queue("pausa", {"minuti": self.hub.pause_minutes}, await who_of(self.hass, self._context))

    def _check(self) -> None:
        if not self.hub.app_running:
            raise HomeAssistantError("L'app Avvisi Targhe non è accesa: il comando non arriverebbe")
