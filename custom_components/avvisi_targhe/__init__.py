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
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import CONF_WEBHOOK_ID, DOMAIN
from .hub import AvvisiTargheHub
from .services import async_setup_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.EVENT, Platform.IMAGE, Platform.NUMBER,
             Platform.SELECT, Platform.SENSOR, Platform.SWITCH]

#: Corpo massimo accettato: un passaggio con la miniatura sta sotto i 300 KB.
MAX_BODY = 1_000_000


@dataclass
class RuntimeData:
    hub: AvvisiTargheHub


type AvvisiTargheConfigEntry = ConfigEntry[RuntimeData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_setup_services(hass)
    return True


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
            # Un passaggio scartato risponde comunque 200: con un errore l'app lo rimanderebbe per
            # sempre, ferma lì, senza più mandare né stato né esiti.
            if not hub.async_passage(raw, photo):
                hub.async_results(body.get("esiti"))
                return web.json_response({"ricevuto": False, "comandi": hub.take_commands()})
            ok = True
        elif kind == "stato":
            ok = hub.async_status(body.get("stato"))
        elif kind == "storico":
            if (added := hub.async_history(body.get("passaggi"))) is None:
                return web.json_response({"ricevuto": False}, status=HTTPStatus.BAD_REQUEST)
            hub.async_results(body.get("esiti"))
            return web.json_response({"ricevuto": True, "aggiunti": added, "comandi": hub.take_commands()})
        elif kind == "comandi":
            ok = True
        else:
            ok = False
        if not ok:
            return web.json_response({"ricevuto": False}, status=HTTPStatus.BAD_REQUEST)
        # Esiti dopo lo stato: con l'esito di "pausa" arriva anche lo stato nuovo, e l'interruttore
        # passa direttamente allo stato giusto invece di tornare un attimo indietro.
        hub.async_results(body.get("esiti"))
        return web.json_response({"ricevuto": True, "comandi": hub.take_commands()})

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


async def async_remove_entry(hass: HomeAssistant, entry: AvvisiTargheConfigEntry) -> None:
    """Integrazione tolta: via anche lo stato salvato (con l'ultima foto)."""
    await AvvisiTargheHub.async_remove_store(hass, entry.entry_id)
