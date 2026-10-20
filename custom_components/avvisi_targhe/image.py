"""Foto dell'ultimo passaggio (la miniatura mandata dall'app)."""

from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import AvvisiTargheConfigEntry
from .entity import AvvisiTargheEntity


async def async_setup_entry(hass: HomeAssistant, entry: AvvisiTargheConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([LastPhoto(hass, entry.runtime_data.hub)])


class LastPhoto(AvvisiTargheEntity, ImageEntity):
    _attr_content_type = "image/jpeg"

    def __init__(self, hass: HomeAssistant, hub) -> None:
        AvvisiTargheEntity.__init__(self, hub, "ultima_foto")
        ImageEntity.__init__(self, hass)
        self._attr_image_last_updated = hub.photo_at

    @property
    def available(self) -> bool:
        return self.hub.photo is not None

    def _handle_update(self) -> None:
        if self.hub.photo_at != self._attr_image_last_updated:
            self._attr_image_last_updated = self.hub.photo_at
            # La nuova foto: il browser deve chiederla di nuovo, non usare quella in cache.
            self._cached_image = None
        self.async_write_ha_state()

    async def async_image(self) -> bytes | None:
        return self.hub.photo
