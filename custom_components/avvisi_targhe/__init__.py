"""Avvisi Targhe: passaggi e stato dell'app di lettura targhe del ristorante."""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
import json
import logging

from aiohttp import web

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import CONF_WEBHOOK_ID, DOMAIN
from .hub import AvvisiTargheHub

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.EVENT, Platform.IMAGE, Platform.SENSOR]

#: Corpo massimo accettato: un passaggio con la miniatura sta sotto i 300 KB.
MAX_BODY = 1_000_000


@dataclass
class RuntimeData:
    hub: AvvisiTargheHub


type AvvisiTargheConfigEntry = ConfigEntry[RuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: AvvisiTargheConfigEntry) -> bool:
    hub = AvvisiTargheHub(hass, entry.entry_id)
    await hub.async_load()
    entry.runtime_data = RuntimeData(hub)

    async def handle(hass: HomeAssistant, webhook_id: str, request: web.Request) -> web.Response:
        if request.content_length is not None and request.content_length > MAX_BODY:
            return web.Response(status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        try:
            body = await request.json(loads=json.loads)
        except ValueError:
            return web.Response(status=HTTPStatus.BAD_REQUEST)
        if not isinstance(body, dict):
            return web.Response(status=HTTPStatus.BAD_REQUEST)
        kind = body.get("tipo")
        if kind == "prova":
            # L'app riconosce l'integrazione da questa risposta (un webhook sconosciuto dà 200 vuoto).
            _LOGGER.info("Prova di collegamento ricevuta dall'app Avvisi Targhe")
            return web.json_response({"ricevuto": True})
        if kind == "passaggio":
            raw = body.get("passaggio")
            photo = raw.pop("foto", None) if isinstance(raw, dict) else None
            ok = hub.async_passage(raw, photo)
        elif kind == "stato":
            ok = hub.async_status(body.get("stato"))
        else:
            ok = False
        return web.json_response({"ricevuto": ok}, status=HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST)

    # Solo dalla rete locale: l'app è sullo stesso posto di Home Assistant.
    webhook.async_register(hass, DOMAIN, "Avvisi Targhe", entry.data[CONF_WEBHOOK_ID], handle,
                           local_only=True, allowed_methods=["POST"])
    entry.async_on_unload(lambda: webhook.async_unregister(hass, entry.data[CONF_WEBHOOK_ID]))
    entry.async_on_unload(hub.async_unload)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AvvisiTargheConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.hub.async_flush()
    return unloaded
