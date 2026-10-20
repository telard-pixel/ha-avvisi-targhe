"""Servizi: gli stessi comandi del bot Telegram, da automazioni, script e dashboard."""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, TIPI_TARGA
from .hub import AvvisiTargheHub

_PLATE = re.compile(r"^[A-Z0-9]{4,10}$")


def _plate(value: Any) -> str:
    plate = re.sub(r"[\s.\-]", "", str(value)).upper()
    if not _PLATE.match(plate):
        raise vol.Invalid("targa non valida: da 4 a 10 lettere e numeri")
    return plate


PAUSE_SCHEMA = vol.Schema({vol.Optional("minuti", default=60): vol.All(vol.Coerce(int), vol.Range(min=1, max=7 * 24 * 60))})
SAVE_SCHEMA = vol.Schema({
    vol.Required("targa"): _plate,
    vol.Optional("nome", default=""): vol.All(cv.string, vol.Length(max=60)),
    vol.Optional("nota", default=""): vol.All(cv.string, vol.Length(max=200)),
    vol.Optional("tipo", default="normale"): vol.In(TIPI_TARGA),
})
REMOVE_SCHEMA = vol.Schema({vol.Required("targa"): _plate})
EMPTY = vol.Schema({})

#: servizio → (tipo di comando per l'app, schema)
SERVICES: dict[str, tuple[str, vol.Schema]] = {
    "pausa": ("pausa", PAUSE_SCHEMA),
    "riprendi": ("riprendi", EMPTY),
    "salva_targa": ("targa-salva", SAVE_SCHEMA),
    "togli_targa": ("targa-togli", REMOVE_SCHEMA),
    "ricollega_nvr": ("ricollega-nvr", EMPTY),
    "riavvia_app": ("riavvia-app", EMPTY),
    "controlla_aggiornamenti": ("aggiornamenti-controlla", EMPTY),
    "installa_aggiornamento": ("aggiornamenti-installa", EMPTY),
}


def hub_of(hass: HomeAssistant) -> AvvisiTargheHub:
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data.hub
    raise ServiceValidationError("Avvisi Targhe non è configurato", translation_domain=DOMAIN,
                                 translation_key="non_configurato")


async def who_of(hass: HomeAssistant, call_context) -> str:
    """Chi ha dato il comando, come appare nel registro dell'app (es. "Roberto da Home Assistant")."""
    if call_context is not None and call_context.user_id and (user := await hass.auth.async_get_user(call_context.user_id)):
        return user.name or "Home Assistant"
    return "automazione"


def command_data(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    if kind == "pausa":
        return {"minuti": data["minuti"]}
    if kind == "targa-salva":
        # L'app vuole il tipo come nel suo elenco (Normale, Segui, Silenziosa, Nera).
        return {"targa": data["targa"], "nome": data["nome"], "nota": data["nota"], "tipo": data["tipo"].capitalize()}
    if kind == "targa-togli":
        return {"targa": data["targa"]}
    return {}


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    for name, (kind, schema) in SERVICES.items():

        async def handle(call: ServiceCall, kind: str = kind) -> None:
            hub = hub_of(hass)
            if not hub.app_running:
                raise ServiceValidationError("L'app Avvisi Targhe non è accesa: il comando non arriverebbe",
                                             translation_domain=DOMAIN, translation_key="app_ferma")
            hub.async_queue(kind, command_data(kind, dict(call.data)), await who_of(hass, call.context))

        hass.services.async_register(DOMAIN, name, handle, schema=schema)
