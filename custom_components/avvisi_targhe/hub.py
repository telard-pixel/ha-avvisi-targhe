"""Stato ricevuto dall'app Avvisi Targhe: ultimo passaggio, foto, stato dell'app.

Tutto arriva dal webhook (l'app chiama, Home Assistant non interroga nessuno). Lo stato si
salva in .storage, così dopo un riavvio di Home Assistant sensori e foto non ripartono vuoti.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from datetime import datetime
import logging
import re
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    APP_TIMEOUT,
    COMMAND_EXPIRY,
    COMMAND_RESEND,
    DOMAIN,
    EVENT_PASSAGE,
    EVENT_RESULT,
    MAX_PHOTO_BYTES,
    TIPI,
    VERSI,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
#: Passaggi tenuti per l'elenco in dashboard (attributo "ultimi" dell'ultima targa).
RECENT = 20
_PLATE = re.compile(r"^[A-Z0-9]{1,12}$")


def _text(value: Any, limit: int) -> str:
    return str(value).strip()[:limit] if isinstance(value, (str, int, float)) else ""


def _time(value: Any) -> datetime | None:
    """Ora con il fuso (come la manda l'app); senza fuso si assume quello di Home Assistant."""
    if not isinstance(value, str):
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.get_default_time_zone())
    return parsed


def _plate(value: Any) -> str:
    plate = _text(value, 12).upper().replace(" ", "")
    return plate if _PLATE.match(plate) else ""


def clean_passage(raw: Any) -> dict[str, Any] | None:
    """Passaggio controllato campo per campo; None se manca l'essenziale (targa e ora)."""
    if not isinstance(raw, dict):
        return None
    plate = _plate(raw.get("targa"))
    when = _time(raw.get("quando"))
    if not plate or when is None:
        return None
    verso = raw.get("verso") if raw.get("verso") in VERSI else "passaggio"
    tipo = raw.get("tipo") if raw.get("tipo") in TIPI else "sconosciuta"
    delay = raw.get("ritardo_s")
    delay = int(delay) if isinstance(delay, (int, float)) and 0 <= delay < 10**7 else 0
    return {
        "targa": plate,
        "quando": when.isoformat(),
        "verso": verso,
        "nome": _text(raw.get("nome"), 80),
        "targa_nota": _plate(raw.get("targa_nota")),
        "tipo": tipo,
        "ritardo_s": delay,
        # Più di 5 minuti dopo il passaggio (Home Assistant o rete giù): le automazioni lo sanno.
        "in_ritardo": delay > 300,
    }


def clean_photo(raw: Any) -> bytes | None:
    """JPEG dalla base64 dell'app; None se non c'è, è troppo grande o non è una JPEG."""
    if not isinstance(raw, str) or not raw or len(raw) > MAX_PHOTO_BYTES * 4 // 3 + 8:
        return None
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        return None
    return data if data[:3] == b"\xff\xd8\xff" and len(data) <= MAX_PHOTO_BYTES else None


def clean_status(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    inside = []
    for item in raw.get("dentro") or []:
        if isinstance(item, dict) and (plate := _plate(item.get("targa"))):
            since = _time(item.get("dal"))
            inside.append({"targa": plate, "nome": _text(item.get("nome"), 80), "dal": since.isoformat() if since else None})
        if len(inside) >= 100:
            break
    today = raw.get("passaggi_oggi")
    paused = _time(raw.get("pausa_fino"))
    last = _time(raw.get("ultima_targa"))
    return {
        "app": _text(raw.get("app"), 20) or "accesa",
        "versione": _text(raw.get("versione"), 30),
        "nvr": _text(raw.get("nvr"), 20).lower(),
        "dettaglio": _text(raw.get("dettaglio"), 200),
        "telecamera_muta": raw.get("telecamera_muta") is True,
        "ultima_targa": last.isoformat() if last else None,
        "passaggi_oggi": int(today) if isinstance(today, int) and 0 <= today < 100000 else None,
        "pausa_fino": paused.isoformat() if paused else None,
        "dentro": inside,
    }


class AvvisiTargheHub:
    """Stato condiviso da tutte le entità, con salvataggio su disco."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}")
        self._listeners: list[Callable[[], None]] = []
        self._passage_listeners: list[Callable[[dict[str, Any]], None]] = []
        self._unsub_timer: CALLBACK_TYPE | None = None
        self.last_passage: dict[str, Any] | None = None
        self.recent: list[dict[str, Any]] = []
        self.photo: bytes | None = None
        self.photo_at: datetime | None = None
        self.status: dict[str, Any] | None = None
        self.status_at: datetime | None = None
        self.today: int = 0
        self.today_date: str | None = None
        # Comandi per l'app (switch avvisi, pulsanti, servizi): partono nella risposta al webhook,
        # perché l'app chiama ogni pochi secondi e non ha porte aperte.
        self.commands: list[dict[str, Any]] = []
        self.next_command_id = 1
        self.last_result: dict[str, Any] | None = None
        self.pause_minutes = 60

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.last_passage = data.get("ultimo")
        self.recent = [p for p in data.get("ultimi") or [] if isinstance(p, dict)][:RECENT]
        self.status = data.get("stato")
        self.status_at = _time(data.get("stato_alle"))
        self.today = data.get("oggi", 0) if isinstance(data.get("oggi"), int) else 0
        self.today_date = data.get("oggi_data")
        self.next_command_id = data.get("prossimo_comando", 1) if isinstance(data.get("prossimo_comando"), int) else 1
        self.last_result = data.get("ultimo_esito")
        if isinstance(data.get("durata_pausa"), int):
            self.pause_minutes = data["durata_pausa"]
        if (photo := data.get("foto")) and (decoded := clean_photo(photo)):
            self.photo = decoded
            self.photo_at = _time(data.get("foto_alle"))
        # Ogni 30 s: "app attiva" diventa spenta da sola se lo stato smette di arrivare.
        self._unsub_timer = async_track_time_interval(self.hass, self._tick, APP_TIMEOUT / 6)

    def _data(self) -> dict[str, Any]:
        return {
            "ultimo": self.last_passage,
            "ultimi": self.recent,
            "stato": self.status,
            "stato_alle": self.status_at.isoformat() if self.status_at else None,
            "oggi": self.today,
            "oggi_data": self.today_date,
            "foto": base64.b64encode(self.photo).decode() if self.photo else None,
            "foto_alle": self.photo_at.isoformat() if self.photo_at else None,
            # I comandi in attesa non si salvano: dopo un riavvio di Home Assistant sarebbero vecchi.
            "prossimo_comando": self.next_command_id,
            "ultimo_esito": self.last_result,
            "durata_pausa": self.pause_minutes,
        }

    @callback
    def async_unload(self) -> None:
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None

    async def async_flush(self) -> None:
        await self._store.async_save(self._data())

    # ---- ascoltatori ----

    @callback
    def add_listener(self, update: Callable[[], None]) -> CALLBACK_TYPE:
        self._listeners.append(update)
        return lambda: self._listeners.remove(update)

    @callback
    def add_passage_listener(self, handler: Callable[[dict[str, Any]], None]) -> CALLBACK_TYPE:
        self._passage_listeners.append(handler)
        return lambda: self._passage_listeners.remove(handler)

    @callback
    def _notify(self) -> None:
        for update in list(self._listeners):
            update()

    @callback
    def _tick(self, _now: datetime) -> None:
        self._expire_commands()
        self._notify()

    # ---- comandi per l'app ----

    @callback
    def async_queue(self, kind: str, data: dict[str, Any], who: str) -> int:
        command_id = self.next_command_id
        self.next_command_id += 1
        self.commands.append({"id": command_id, "tipo": kind, "dati": data, "chi": who,
                              "creato": dt_util.utcnow(), "inviato": None})
        _LOGGER.info("Comando per l'app: %s %s (%s)", kind, data, who)
        self._notify()
        self._store.async_delay_save(self._data, 5)
        return command_id

    @callback
    def take_commands(self) -> list[dict[str, Any]]:
        """Comandi da mettere nella risposta: i nuovi, e quelli mandati da più di un minuto senza esito."""
        now = dt_util.utcnow()
        self._expire_commands()
        out = []
        for c in self.commands:
            if c["inviato"] is None or now - c["inviato"] >= COMMAND_RESEND:
                c["inviato"] = now
                out.append({k: c[k] for k in ("id", "tipo", "dati", "chi")})
        return out

    def pending(self, *kinds: str) -> dict[str, Any] | None:
        """L'ultimo comando ancora senza esito tra questi tipi (per mostrare subito lo stato voluto)."""
        found = [c for c in self.commands if c["tipo"] in kinds]
        return found[-1] if found else None

    @callback
    def _expire_commands(self) -> None:
        now = dt_util.utcnow()
        for c in [c for c in self.commands if now - c["creato"] >= COMMAND_EXPIRY]:
            self.commands.remove(c)
            self._result(c, False, "l'app non ha risposto entro 10 minuti: comando annullato")

    @callback
    def async_results(self, raw: Any) -> None:
        if not isinstance(raw, list):
            return
        changed = False
        for item in raw[:50]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), int):
                continue
            command = next((c for c in self.commands if c["id"] == item["id"]), None)
            if command is None:
                continue  # esito ripetuto o di un comando già scaduto
            self.commands.remove(command)
            self._result(command, item.get("ok") is True, _text(item.get("messaggio"), 250))
            changed = True
        if changed:
            self._notify()

    @callback
    def _result(self, command: dict[str, Any], ok: bool, message: str) -> None:
        self.last_result = {"id": command["id"], "tipo": command["tipo"], "ok": ok, "messaggio": message,
                            "chi": command["chi"], "quando": dt_util.utcnow().isoformat()}
        self.hass.bus.async_fire(EVENT_RESULT, dict(self.last_result))
        self._store.async_delay_save(self._data, 5)

    @callback
    def async_set_pause_minutes(self, minutes: int) -> None:
        self.pause_minutes = minutes
        self._notify()
        self._store.async_delay_save(self._data, 5)

    # ---- dal webhook ----

    @property
    def app_running(self) -> bool:
        """Accesa se lo stato è recente e l'app non ha detto di essersi chiusa."""
        if self.status is None or self.status_at is None:
            return False
        if self.status.get("app") in ("chiusa", "spegnimento"):
            return False
        return dt_util.utcnow() - self.status_at <= APP_TIMEOUT

    def _count_today(self, when: datetime) -> None:
        day = dt_util.as_local(when).date().isoformat()
        if self.today_date != day:
            self.today_date, self.today = day, 0
        self.today += 1

    @callback
    def async_passage(self, raw: Any, raw_photo: Any) -> bool:
        passage = clean_passage(raw)
        if passage is None:
            _LOGGER.warning("Passaggio non valido ricevuto dall'app, scartato")
            return False
        when = _time(passage["quando"])
        assert when is not None
        if dt_util.as_local(when).date() == dt_util.now().date():
            self._count_today(when)
        photo = clean_photo(raw_photo)
        # Il passaggio più recente resta "l'ultimo" anche se ne arrivano di arretrati.
        newer = self.last_passage is None or (
            (prev := _time(self.last_passage.get("quando"))) is None or when >= prev
        )
        if newer:
            self.last_passage = passage
            # Senza foto per questo passaggio si toglie anche la vecchia: mai una foto di un'altra auto.
            self.photo, self.photo_at = photo, when
        # Elenco dal più recente; un arretrato va al suo posto, non in cima.
        self.recent = sorted([*self.recent, passage], key=lambda p: p["quando"], reverse=True)[:RECENT]
        self.hass.bus.async_fire(EVENT_PASSAGE, {**passage, "ha_foto": photo is not None})
        for handler in list(self._passage_listeners):
            handler(passage)
        self._notify()
        self._store.async_delay_save(self._data, 5)
        return True

    @callback
    def async_status(self, raw: Any) -> bool:
        status = clean_status(raw)
        if status is None:
            return False
        self.status, self.status_at = status, dt_util.utcnow()
        # Il conteggio dell'app vale di più (registro del PC): corregge quello tenuto qui.
        if status["passaggi_oggi"] is not None:
            self.today_date, self.today = dt_util.now().date().isoformat(), status["passaggi_oggi"]
        self._notify()
        self._store.async_delay_save(self._data, 30)
        return True

    @property
    def passages_today(self) -> int:
        return self.today if self.today_date == dt_util.now().date().isoformat() else 0
