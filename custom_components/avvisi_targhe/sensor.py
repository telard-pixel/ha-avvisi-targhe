"""Sensori: ultima targa, ultimo passaggio, passaggi di oggi, veicoli dentro, stato dell'NVR."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import AvvisiTargheConfigEntry
from .entity import AvvisiTargheEntity


async def async_setup_entry(hass: HomeAssistant, entry: AvvisiTargheConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    hub = entry.runtime_data.hub
    async_add_entities([LastPlate(hub), LastPassage(hub), PassagesToday(hub), Inside(hub), NvrState(hub)])


class LastPlate(AvvisiTargheEntity, SensorEntity):
    _attr_icon = "mdi:car-search"

    def __init__(self, hub) -> None:
        super().__init__(hub, "ultima_targa")

    @property
    def native_value(self) -> str | None:
        return (self.hub.last_passage or {}).get("targa")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        p = self.hub.last_passage or {}
        return {k: p.get(k) for k in ("nome", "verso", "tipo", "quando", "targa_nota", "in_ritardo")}


class LastPassage(AvvisiTargheEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, hub) -> None:
        super().__init__(hub, "ultimo_passaggio")

    @property
    def native_value(self) -> datetime | None:
        when = (self.hub.last_passage or {}).get("quando")
        return dt_util.parse_datetime(when) if when else None


class PassagesToday(AvvisiTargheEntity, SensorEntity):
    _attr_icon = "mdi:counter"
    _attr_native_unit_of_measurement = "passaggi"

    def __init__(self, hub) -> None:
        super().__init__(hub, "passaggi_oggi")

    @property
    def native_value(self) -> int:
        return self.hub.passages_today


class Inside(AvvisiTargheEntity, SensorEntity):
    _attr_icon = "mdi:garage-open-variant"
    _attr_native_unit_of_measurement = "veicoli"

    def __init__(self, hub) -> None:
        super().__init__(hub, "veicoli_dentro")

    @property
    def available(self) -> bool:
        return self.hub.status is not None

    @property
    def native_value(self) -> int | None:
        return len((self.hub.status or {}).get("dentro") or [])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"elenco": (self.hub.status or {}).get("dentro") or []}


class NvrState(AvvisiTargheEntity, SensorEntity):
    _attr_icon = "mdi:cctv"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["collegato", "scollegato", "avvio"]

    def __init__(self, hub) -> None:
        super().__init__(hub, "stato_nvr")

    @property
    def available(self) -> bool:
        # Con l'app ferma lo stato dell'NVR non si conosce.
        return self.hub.app_running

    @property
    def native_value(self) -> str | None:
        value = (self.hub.status or {}).get("nvr")
        return value if value in self._attr_options else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"dettaglio": (self.hub.status or {}).get("dettaglio")}
