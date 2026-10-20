"""App attiva, telecamera muta, pausa degli avvisi."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import AvvisiTargheConfigEntry
from .entity import AvvisiTargheEntity


async def async_setup_entry(hass: HomeAssistant, entry: AvvisiTargheConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    hub = entry.runtime_data.hub
    async_add_entities([AppRunning(hub), SilentCamera(hub), Paused(hub)])


class AppRunning(AvvisiTargheEntity, BinarySensorEntity):
    """Spento se l'app non manda lo stato da 3 minuti (PC spento, app bloccata, rete giù)."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, hub) -> None:
        super().__init__(hub, "app_attiva")

    @property
    def is_on(self) -> bool:
        return self.hub.app_running

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        status = self.hub.status or {}
        return {
            "ultimo_contatto": self.hub.status_at.isoformat() if self.hub.status_at else None,
            "chiusa_apposta": status.get("app") in ("chiusa", "spegnimento"),
            "versione": status.get("versione"),
        }


class SilentCamera(AvvisiTargheEntity, BinarySensorEntity):
    """Problema: l'NVR è collegato ma la telecamera non legge targhe da ore nell'orario di apertura."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, hub) -> None:
        super().__init__(hub, "telecamera_muta")

    @property
    def available(self) -> bool:
        return self.hub.app_running

    @property
    def is_on(self) -> bool:
        return bool((self.hub.status or {}).get("telecamera_muta"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"ultima_targa_letta": (self.hub.status or {}).get("ultima_targa")}


class Paused(AvvisiTargheEntity, BinarySensorEntity):
    _attr_icon = "mdi:bell-sleep"

    def __init__(self, hub) -> None:
        super().__init__(hub, "pausa")

    @property
    def available(self) -> bool:
        return self.hub.app_running

    @property
    def is_on(self) -> bool:
        return bool((self.hub.status or {}).get("pausa_fino"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"fino_a": (self.hub.status or {}).get("pausa_fino")}
