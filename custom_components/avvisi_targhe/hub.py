"""Stato ricevuto dall'app Avvisi Targhe: ultimo passaggio, foto, stato dell'app.

Tutto arriva dal webhook (l'app chiama, Home Assistant non interroga nessuno). Lo stato si
salva in .storage, così dopo un riavvio di Home Assistant sensori e foto non ripartono vuoti.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from datetime import datetime, timedelta
import logging
import re
import time
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
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
    HISTORY_DAYS,
    HISTORY_MAX,
    MAX_PHOTO_BYTES,
    TIPI,
    VERSI,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
#: Passaggi nell'elenco breve della dashboard (attributo "ultimi" dell'ultima targa).
RECENT = 20
#: Passaggi accettati in una sola richiesta "storico" (riempimento dello storico dall'app).
HISTORY_BATCH = 500
_PLATE = re.compile(r"^[A-Z0-9]{1,12}$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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
        "passaggi_data": day if isinstance(day := raw.get("passaggi_data"), str) and _DAY.match(day) else None,
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
        # Storico degli ultimi HISTORY_DAYS giorni, dal più recente; i primi RECENT sono "gli ultimi".
        self.history: list[dict[str, Any]] = []
        self._history_keys: set[tuple[str, str, str]] = set()
        #: Giorno mostrato nella pagina dello storico: 0 oggi, 1 ieri... (relativo, così a mezzanotte scorre).
        self.day_offset = 0
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
        # Comandi già consegnati all'app ma scaduti o rimasti da prima di un riavvio: se l'esito
        # arriva lo stesso, si mostra (l'app può averli eseguiti davvero).
        self.awaiting: dict[int, dict[str, Any]] = {}
        self.pause_minutes = 60

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self.last_passage = data.get("ultimo")
        # Prima della 1.3.0 c'erano solo gli ultimi 20: diventano l'inizio dello storico.
        saved = data.get("storico") if isinstance(data.get("storico"), list) else data.get("ultimi")
        self._add_history(clean_passage(p) for p in saved or [])
        if isinstance(data.get("giorno"), int) and 0 <= data["giorno"] < HISTORY_DAYS:
            self.day_offset = data["giorno"]
        self.status = data.get("stato")
        self.status_at = _time(data.get("stato_alle"))
        self.today = data.get("oggi", 0) if isinstance(data.get("oggi"), int) else 0
        self.today_date = data.get("oggi_data")
        self.next_command_id = data.get("prossimo_comando", 1) if isinstance(data.get("prossimo_comando"), int) else 1
        self.last_result = data.get("ultimo_esito")
        for c in data.get("consegnati") or []:
            if isinstance(c, dict) and isinstance(c.get("id"), int):
                self.awaiting[c["id"]] = {k: c.get(k) for k in ("id", "tipo", "chi")}
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
            "storico": self.history,
            "giorno": self.day_offset,
            "stato": self.status,
            "stato_alle": self.status_at.isoformat() if self.status_at else None,
            "oggi": self.today,
            "oggi_data": self.today_date,
            "foto": base64.b64encode(self.photo).decode() if self.photo else None,
            "foto_alle": self.photo_at.isoformat() if self.photo_at else None,
            # I comandi in attesa non si salvano: dopo un riavvio di Home Assistant sarebbero vecchi.
            "prossimo_comando": self.next_command_id,
            "ultimo_esito": self.last_result,
            "consegnati": [{k: c[k] for k in ("id", "tipo", "chi")} for c in self.commands if c["inviato"]]
            + list(self.awaiting.values()),
            "durata_pausa": self.pause_minutes,
        }

    @staticmethod
    async def async_remove_store(hass: HomeAssistant, entry_id: str) -> None:
        await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}").async_remove()

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
        self._prune_history()
        self._notify()

    # ---- storico ----

    @property
    def recent(self) -> list[dict[str, Any]]:
        return self.history[:RECENT]

    @staticmethod
    def _key(p: dict[str, Any]) -> tuple[str, str, str]:
        return (p["quando"], p["targa"], p["verso"])

    def _add_history(self, passages: Any) -> int:
        """Aggiunge i passaggi nuovi (stessa ora, targa e verso = già visto) e rimette in ordine."""
        added = 0
        for p in passages:
            if p is None or (key := self._key(p)) in self._history_keys:
                continue
            self.history.append(p)
            self._history_keys.add(key)
            added += 1
        if added:
            # Ordine per ora vera, non per testo: due fusi diversi (ora legale) non si mescolano.
            self.history.sort(key=lambda p: _time(p["quando"]) or dt_util.utc_from_timestamp(0), reverse=True)
            self._prune_history()
        return added

    def _prune_history(self) -> None:
        start = self.day_start(HISTORY_DAYS - 1)
        keep = [p for p in self.history[:HISTORY_MAX] if (when := _time(p["quando"])) and when >= start]
        if len(keep) != len(self.history):
            self.history = keep
            self._history_keys = {self._key(p) for p in keep}

    @staticmethod
    def day_start(offset: int) -> datetime:
        """Mezzanotte (ora di Home Assistant) di "offset" giorni fa."""
        return dt_util.start_of_local_day(dt_util.now().date() - timedelta(days=offset))

    def passages_of_day(self, offset: int) -> list[dict[str, Any]]:
        start, end = self.day_start(offset), self.day_start(offset - 1)
        return [p for p in self.history if (when := _time(p["quando"])) and start <= when < end]

    @callback
    def async_set_day(self, offset: int) -> None:
        self.day_offset = offset
        self._notify()
        self._store.async_delay_save(self._data, 5)

    @callback
    def async_history(self, raw: Any) -> int | None:
        """Passaggi già successi mandati dall'app per riempire lo storico: niente eventi né conteggi."""
        if not isinstance(raw, list):
            return None
        added = self._add_history(clean_passage(p) for p in raw[:HISTORY_BATCH])
        if added:
            self._notify()
            self._store.async_delay_save(self._data, 5)
        return added

    # ---- comandi per l'app ----

    @callback
    def async_queue(self, kind: str, data: dict[str, Any], who: str) -> int:
        # Id dall'orologio (millisecondi): con uno Store nuovo (integrazione rimessa, backup vecchio,
        # salvataggio perso) un id non torna mai uguale a uno già eseguito, che l'app scarterebbe
        # come "già eseguito" senza farlo.
        command_id = max(self.next_command_id, int(time.time() * 1000))
        self.next_command_id = command_id + 1
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
            if c["inviato"]:
                self._remember(c)
            self._result(c, False, "l'app non ha risposto entro 10 minuti: comando annullato")

    def _remember(self, command: dict[str, Any]) -> None:
        self.awaiting[command["id"]] = {k: command[k] for k in ("id", "tipo", "chi")}
        while len(self.awaiting) > 50:
            del self.awaiting[next(iter(self.awaiting))]

    @callback
    def async_results(self, raw: Any) -> None:
        if not isinstance(raw, list):
            return
        changed = False
        for item in raw[:50]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), int):
                continue
            message = _text(item.get("messaggio"), 250)
            command = next((c for c in self.commands if c["id"] == item["id"]), None)
            if command is not None:
                self.commands.remove(command)
            elif (command := self.awaiting.pop(item["id"], None)) is not None:
                message = f"{message} (esito arrivato in ritardo)"
            else:
                continue  # esito ripetuto
            self._result(command, item.get("ok") is True, message)
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
        if self._key(passage) in self._history_keys:
            # Già ricevuto: la risposta all'app si era persa e l'app lo rimanda. Niente evento doppio.
            _LOGGER.debug("Passaggio %s già ricevuto, ignorato", passage["targa"])
            return True
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
            self.photo, self.photo_at = photo, dt_util.utcnow()
        # Elenco dal più recente; un arretrato va al suo posto, non in cima.
        self._add_history([passage])
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
        if status["versione"] and status["versione"] != (self.status or {}).get("versione"):
            device = dr.async_get(self.hass).async_get_device(identifiers={(DOMAIN, self.entry_id)})
            if device is not None:
                dr.async_get(self.hass).async_update_device(device.id, sw_version=status["versione"])
        self.status, self.status_at = status, dt_util.utcnow()
        # Il conteggio dell'app vale di più (registro del PC): corregge quello tenuto qui.
        # Solo se il conteggio è dello stesso giorno: uno stato delle 23:59 arrivato dopo mezzanotte
        # non porta il totale di ieri nel giorno nuovo (le app vecchie non mandano la data).
        today = dt_util.now().date().isoformat()
        if status["passaggi_oggi"] is not None and status["passaggi_data"] in (None, today):
            self.today_date, self.today = today, status["passaggi_oggi"]
        self._notify()
        self._store.async_delay_save(self._data, 30)
        return True

    @property
    def paused(self) -> bool:
        """In pausa adesso: una pausa scaduta non conta anche se lo stato nuovo non è ancora arrivato."""
        until = _time((self.status or {}).get("pausa_fino"))
        return until is not None and until > dt_util.utcnow()

    @property
    def passages_today(self) -> int:
        return self.today if self.today_date == dt_util.now().date().isoformat() else 0
