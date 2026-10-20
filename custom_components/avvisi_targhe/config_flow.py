"""Configurazione di Avvisi Targhe: genera il codice del webhook da copiare nell'app."""

from __future__ import annotations

import secrets
from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import CONF_WEBHOOK_ID, DOMAIN


def _placeholders(webhook_id: str) -> dict[str, str]:
    return {"codice": webhook_id}


class AvvisiTargheConfigFlow(ConfigFlow, domain=DOMAIN):
    """Un solo passo da fare: copiare il codice nell'app (Opzioni → Home Assistant)."""

    VERSION = 1

    def __init__(self) -> None:
        self._webhook_id = secrets.token_hex(16)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title="Avvisi Targhe", data={CONF_WEBHOOK_ID: self._webhook_id})
        return self.async_show_form(step_id="user", description_placeholders=_placeholders(self._webhook_id))

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Per rivedere il codice (es. reinstallando l'app): non lo cambia."""
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            return self.async_abort(reason="codice_mostrato")
        return self.async_show_form(step_id="reconfigure",
                                    description_placeholders=_placeholders(entry.data[CONF_WEBHOOK_ID]))
