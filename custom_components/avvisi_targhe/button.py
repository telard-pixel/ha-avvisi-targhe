"""Pulsanti: ricollega NVR, riavvia app, controlla e installa aggiornamenti (come nel pannello)."""

from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import AvvisiTargheConfigEntry
from .entity import AvvisiTargheEntity
from .services import who_of

BUTTONS = (
    ("ricollega_nvr", "ricollega-nvr", "mdi:lan-connect", None),
    ("riavvia_app", "riavvia-app", None, ButtonDeviceClass.RESTART),
    ("controlla_aggiornamenti", "aggiornamenti-controlla", "mdi:update", None),
    ("installa_aggiornamento", "aggiornamenti-installa", "mdi:download", ButtonDeviceClass.UPDATE),
)


async def async_setup_entry(hass: HomeAssistant, entry: AvvisiTargheConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    hub = entry.runtime_data.hub
    async_add_entities(CommandButton(hub, *b) for b in BUTTONS)


class CommandButton(AvvisiTargheEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, hub, key: str, kind: str, icon: str | None, device_class: ButtonDeviceClass | None) -> None:
        super().__init__(hub, key)
        self._kind = kind
        self._attr_icon = icon
        self._attr_device_class = device_class

    @property
    def available(self) -> bool:
        return self.hub.app_running

    async def async_press(self) -> None:
        if not self.hub.app_running:
            raise HomeAssistantError("L'app Avvisi Targhe non è accesa: il comando non arriverebbe")
        self.hub.async_queue(self._kind, {}, await who_of(self.hass, self._context))
