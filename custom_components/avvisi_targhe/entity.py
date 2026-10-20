"""Entità di base: tutte appartengono al dispositivo "Avvisi Targhe" e si aggiornano dal webhook."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .hub import AvvisiTargheHub


class AvvisiTargheEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hub: AvvisiTargheHub, key: str) -> None:
        self.hub = hub
        self._attr_translation_key = key
        self._attr_unique_id = f"{hub.entry_id}_{key}"
        # Nomi delle entità fissi e in italiano, qualunque sia la lingua di Home Assistant
        # (le automazioni e la dashboard li usano). La piattaforma è il nome del modulo: sensor, image…
        self.entity_id = f"{type(self).__module__.rsplit('.', 1)[1]}.avvisi_targhe_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hub.entry_id)},
            name="Avvisi Targhe",
            manufacturer="Avvisi Targhe",
            model="Lettura targhe (NVR Dahua)",
            entry_type=DeviceEntryType.SERVICE,
            sw_version=(hub.status or {}).get("versione") or None,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.hub.add_listener(self._handle_update))

    def _handle_update(self) -> None:
        self.async_write_ha_state()
