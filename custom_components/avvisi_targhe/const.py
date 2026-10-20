"""Costanti dell'integrazione Avvisi Targhe."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "avvisi_targhe"
CONF_WEBHOOK_ID = "webhook_id"

#: Evento sul bus di Home Assistant a ogni passaggio (per le automazioni).
EVENT_PASSAGE = "avvisi_targhe_passaggio"

#: L'app manda lo stato ogni minuto: oltre questo silenzio la si considera ferma.
APP_TIMEOUT = timedelta(minutes=3)

#: Una miniatura più grande di così non arriva dall'app: si scarta.
MAX_PHOTO_BYTES = 200_000

VERSI = ("entrata", "uscita", "passaggio")
TIPI = ("sconosciuta", "nota", "segui", "silenziosa", "nera")

#: Esito di un comando mandato all'app (pausa, targhe, riavvio...).
EVENT_RESULT = "avvisi_targhe_esito"

#: Un comando senza esito si rimanda dopo un minuto e si annulla dopo 10 (come nel pannello).
COMMAND_RESEND = timedelta(minutes=1)
COMMAND_EXPIRY = timedelta(minutes=10)

#: Tipi di targa per il servizio salva_targa (come nell'app).
TIPI_TARGA = ("normale", "segui", "silenziosa", "nera")
