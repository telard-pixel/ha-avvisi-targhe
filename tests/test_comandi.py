"""Comandi da Home Assistant all'app: partono nella risposta al webhook, tornano gli esiti."""

from datetime import timedelta

import pytest
import voluptuous as vol
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_capture_events, async_fire_time_changed

from custom_components.avvisi_targhe.const import DOMAIN, EVENT_RESULT

from .test_integrazione import WEBHOOK, _setup

STATO = {"app": "accesa", "versione": "1.0.57", "nvr": "collegato", "dettaglio": "", "telecamera_muta": False,
         "ultima_targa": None, "passaggi_oggi": 3, "pausa_fino": None, "dentro": []}


async def _app_on(client, **changes):
    resp = await client.post(f"/api/webhook/{WEBHOOK}", json={"tipo": "stato", "stato": {**STATO, **changes}, "esiti": []})
    assert resp.status == 200
    return await resp.json()


async def _poll(client, esiti=()):
    resp = await client.post(f"/api/webhook/{WEBHOOK}", json={"tipo": "comandi", "esiti": list(esiti)})
    assert resp.status == 200
    return (await resp.json())["comandi"]


async def test_comandi_rifiutati_con_app_spenta(hass: HomeAssistant) -> None:
    await _setup(hass)
    assert hass.states.get("switch.avvisi_targhe_avvisi").state == "unavailable"
    assert hass.states.get("button.avvisi_targhe_riavvia_app").state == "unavailable"
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "riprendi", {}, blocking=True)


async def test_pausa_dallo_switch_fino_all_esito(hass: HomeAssistant, hass_client_no_auth) -> None:
    await _setup(hass)
    client = await hass_client_no_auth()
    assert (await _app_on(client))["comandi"] == []
    await hass.async_block_till_done()
    assert hass.states.get("switch.avvisi_targhe_avvisi").state == "on"

    await hass.services.async_call("number", "set_value", {"entity_id": "number.avvisi_targhe_durata_pausa", "value": 30}, blocking=True)
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.avvisi_targhe_avvisi"}, blocking=True)
    await hass.async_block_till_done()
    # Subito spento (in attesa dell'app), e il comando parte alla prossima chiamata dell'app.
    assert hass.states.get("switch.avvisi_targhe_avvisi").state == "off"
    assert hass.states.get("sensor.avvisi_targhe_ultimo_comando").state == "in attesa dell'app"
    commands = await _poll(client)
    assert len(commands) == 1
    assert commands[0]["tipo"] == "pausa" and commands[0]["dati"] == {"minuti": 30}
    # Già mandato: non si ripete subito.
    assert await _poll(client) == []

    events = async_capture_events(hass, EVENT_RESULT)
    changes = async_capture_events(hass, "state_changed")
    until = (dt_util.now() + timedelta(minutes=30)).isoformat()
    await client.post(f"/api/webhook/{WEBHOOK}", json={"tipo": "stato", "stato": {**STATO, "pausa_fino": until},
                      "esiti": [{"id": commands[0]["id"], "ok": True, "messaggio": "avvisi in pausa fino alle 18:30"}]})
    await hass.async_block_till_done()
    assert hass.states.get("switch.avvisi_targhe_avvisi").state == "off"
    # Esito e stato nuovo nella stessa richiesta: l'interruttore non torna acceso nemmeno per un attimo.
    assert not [e for e in changes if e.data["entity_id"] == "switch.avvisi_targhe_avvisi"
                and e.data["new_state"].state != e.data["old_state"].state]
    last = hass.states.get("sensor.avvisi_targhe_ultimo_comando")
    assert last.state == "avvisi in pausa fino alle 18:30"
    assert last.attributes["riuscito"] is True and last.attributes["comando"] == "pausa"
    assert len(events) == 1 and events[0].data["ok"] is True
    # Esito ripetuto (l'app non sapeva se era arrivato): ignorato.
    await _poll(client, [{"id": commands[0]["id"], "ok": True, "messaggio": "x"}])
    await hass.async_block_till_done()
    assert len(events) == 1


async def test_servizi_targhe_e_validazione(hass: HomeAssistant, hass_client_no_auth) -> None:
    await _setup(hass)
    client = await hass_client_no_auth()
    await _app_on(client)
    await hass.services.async_call(DOMAIN, "salva_targa", {"targa": "ab 123-cd", "nome": "Mario", "tipo": "segui"}, blocking=True)
    await hass.services.async_call(DOMAIN, "togli_targa", {"targa": "ZZ999ZZ"}, blocking=True)
    commands = await _poll(client)
    assert [c["tipo"] for c in commands] == ["targa-salva", "targa-togli"]
    assert commands[0]["dati"] == {"targa": "AB123CD", "nome": "Mario", "nota": "", "tipo": "Segui"}
    assert commands[0]["chi"] == "automazione"
    for bad in ({"targa": "AB"}, {"targa": "<script>"}, {"targa": "AB123CD", "tipo": "boh"}):
        with pytest.raises(vol.Invalid):
            await hass.services.async_call(DOMAIN, "salva_targa", bad, blocking=True)


async def test_pulsante_rimandato_e_poi_scaduto(hass: HomeAssistant, hass_client_no_auth, freezer: FrozenDateTimeFactory) -> None:
    await _setup(hass)
    client = await hass_client_no_auth()
    await _app_on(client)
    await hass.services.async_call("button", "press", {"entity_id": "button.avvisi_targhe_ricollega_nvr"}, blocking=True)
    first = await _poll(client)
    assert [c["tipo"] for c in first] == ["ricollega-nvr"]
    # Nessun esito per un minuto: si rimanda (l'app risponde "già eseguito" se l'aveva fatto).
    freezer.tick(timedelta(seconds=61))
    resent = (await _app_on(client))["comandi"]
    assert [c["id"] for c in resent] == [first[0]["id"]]
    assert await _poll(client) == []
    # Dopo 10 minuti senza esito si annulla.
    freezer.tick(timedelta(minutes=10))
    await _app_on(client)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert await _poll(client) == []
    last = hass.states.get("sensor.avvisi_targhe_ultimo_comando")
    assert last.attributes["riuscito"] is False
    assert "10 minuti" in last.state
    # L'app l'aveva eseguito e l'esito arriva tardi: si mostra lo stesso.
    await _poll(client, [{"id": first[0]["id"], "ok": True, "messaggio": "NVR ricollegato"}])
    await hass.async_block_till_done()
    last = hass.states.get("sensor.avvisi_targhe_ultimo_comando")
    assert last.attributes["riuscito"] is True
    assert last.state.startswith("NVR ricollegato")


async def test_id_dei_comandi_mai_ripetuti_e_pausa_scaduta(hass: HomeAssistant, hass_client_no_auth,
                                                           freezer: FrozenDateTimeFactory) -> None:
    await _setup(hass)
    client = await hass_client_no_auth()
    await _app_on(client, pausa_fino=(dt_util.now() + timedelta(minutes=2)).isoformat())
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.avvisi_targhe_pausa").state == "on"
    # Pausa finita: si vede anche prima che arrivi lo stato nuovo dell'app.
    freezer.tick(timedelta(minutes=2, seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.avvisi_targhe_pausa").state == "off"
    assert hass.states.get("switch.avvisi_targhe_avvisi").state == "on"

    await _app_on(client)
    await hass.services.async_call(DOMAIN, "riprendi", {}, blocking=True)
    await hass.services.async_call(DOMAIN, "riprendi", {}, blocking=True)
    ids = [c["id"] for c in await _poll(client)]
    # Dall'orologio: anche con un'integrazione rimessa da capo non tornano gli id già eseguiti dall'app.
    assert ids[0] > 1_700_000_000_000 and ids[1] > ids[0]
