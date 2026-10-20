"""Prove dell'integrazione con un Home Assistant vero (pytest-homeassistant-custom-component)."""

import base64
from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events, async_fire_time_changed

from custom_components.avvisi_targhe.const import CONF_WEBHOOK_ID, DOMAIN, EVENT_PASSAGE

WEBHOOK = "0123456789abcdef0123456789abcdef"
# Una JPEG minuscola ma vera (inizia con FF D8 FF).
JPEG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffd9")


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_WEBHOOK_ID: WEBHOOK}, unique_id=DOMAIN, title="Avvisi Targhe")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _passage(**kw):
    p = {"quando": dt_util.now().replace(microsecond=0).isoformat(), "targa": "AB123CD", "verso": "entrata",
         "nome": "Mario", "targa_nota": "AB123CD", "tipo": "nota", "ritardo_s": 2, "foto": base64.b64encode(JPEG).decode()}
    p.update(kw)
    return {"tipo": "passaggio", "passaggio": p}


async def test_config_flow_mostra_il_codice_e_crea_la_voce(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    code = result["description_placeholders"]["codice"]
    assert len(code) == 32
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_WEBHOOK_ID] == code
    # Una sola installazione.
    again = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert again["type"] is FlowResultType.ABORT


async def test_prova_risponde_ricevuto(hass: HomeAssistant, hass_client_no_auth) -> None:
    await _setup(hass)
    client = await hass_client_no_auth()
    resp = await client.post(f"/api/webhook/{WEBHOOK}", json={"tipo": "prova"})
    assert resp.status == 200
    assert await resp.json() == {"ricevuto": True}
    # Un codice sbagliato dà 200 vuoto (Home Assistant non dice se il webhook esiste): l'app lo riconosce.
    wrong = await client.post("/api/webhook/sbagliato0123456789", json={"tipo": "prova"})
    assert wrong.status == 200
    assert await wrong.text() == ""


async def test_passaggio_aggiorna_sensori_foto_evento(hass: HomeAssistant, hass_client_no_auth) -> None:
    await _setup(hass)
    events = async_capture_events(hass, EVENT_PASSAGE)
    client = await hass_client_no_auth()
    sent = _passage()
    resp = await client.post(f"/api/webhook/{WEBHOOK}", json=sent)
    assert resp.status == 200
    await hass.async_block_till_done()

    plate = hass.states.get("sensor.avvisi_targhe_ultima_targa")
    assert plate.state == "AB123CD"
    assert plate.attributes["nome"] == "Mario"
    assert plate.attributes["verso"] == "entrata"
    assert plate.attributes["in_ritardo"] is False
    assert hass.states.get("sensor.avvisi_targhe_passaggi_oggi").state == "1"

    ev = hass.states.get("event.avvisi_targhe_passaggio")
    assert ev.attributes["event_type"] == "entrata"
    assert ev.attributes["targa"] == "AB123CD"

    assert len(events) == 1
    assert events[0].data["targa"] == "AB123CD"
    # Lo stesso passaggio rimandato (la risposta si era persa): niente evento né conteggio doppio.
    resp = await client.post(f"/api/webhook/{WEBHOOK}", json=sent)
    assert (await resp.json())["ricevuto"] is True
    await hass.async_block_till_done()
    assert len(events) == 1
    assert hass.states.get("sensor.avvisi_targhe_passaggi_oggi").state == "1"
    assert events[0].data["ha_foto"] is True
    assert "foto" not in events[0].data

    img = hass.states.get("image.avvisi_targhe_ultima_foto")
    assert img is not None and img.state != "unavailable"
    authed = await hass_client_no_auth()
    photo = await authed.get(img.attributes["entity_picture"])
    assert photo.status == 200
    assert await photo.read() == JPEG


async def test_passaggio_senza_foto_toglie_la_vecchia(hass: HomeAssistant, hass_client_no_auth) -> None:
    await _setup(hass)
    client = await hass_client_no_auth()
    await client.post(f"/api/webhook/{WEBHOOK}", json=_passage())
    later = (dt_util.now() + timedelta(seconds=5)).replace(microsecond=0).isoformat()
    await client.post(f"/api/webhook/{WEBHOOK}", json=_passage(quando=later, targa="ZZ999ZZ", foto=None, tipo="sconosciuta"))
    await hass.async_block_till_done()
    assert hass.states.get("sensor.avvisi_targhe_ultima_targa").state == "ZZ999ZZ"
    assert hass.states.get("image.avvisi_targhe_ultima_foto").state == "unavailable"


async def test_arretrato_non_scavalca_l_ultimo(hass: HomeAssistant, hass_client_no_auth) -> None:
    await _setup(hass)
    client = await hass_client_no_auth()
    await client.post(f"/api/webhook/{WEBHOOK}", json=_passage())
    old = (dt_util.now() - timedelta(hours=2)).replace(microsecond=0).isoformat()
    await client.post(f"/api/webhook/{WEBHOOK}", json=_passage(quando=old, targa="OLD111", ritardo_s=7200))
    await hass.async_block_till_done()
    assert hass.states.get("sensor.avvisi_targhe_ultima_targa").state == "AB123CD"
    # L'evento parte comunque, segnato come in ritardo.
    assert hass.states.get("event.avvisi_targhe_passaggio").attributes["in_ritardo"] is True


async def test_dati_non_validi_scartati(hass: HomeAssistant, hass_client_no_auth) -> None:
    await _setup(hass)
    client = await hass_client_no_auth()
    for bad in ({"tipo": "boh"}, [1, 2], {"tipo": "stato", "stato": "x"}):
        resp = await client.post(f"/api/webhook/{WEBHOOK}", json=bad)
        assert resp.status == 400
    # Un passaggio scartato risponde 200 "non ricevuto": l'app va avanti invece di rimandarlo per sempre.
    for bad in (_passage(targa="ab-12/..<script>"), _passage(quando="ieri")):
        resp = await client.post(f"/api/webhook/{WEBHOOK}", json=bad)
        assert resp.status == 200
        assert (await resp.json())["ricevuto"] is False
    # Foto che non è una JPEG: il passaggio vale, la foto no.
    resp = await client.post(f"/api/webhook/{WEBHOOK}", json=_passage(foto=base64.b64encode(b"<svg>").decode()))
    assert resp.status == 200
    await hass.async_block_till_done()
    assert hass.states.get("image.avvisi_targhe_ultima_foto").state == "unavailable"


async def test_stato_e_app_ferma(hass: HomeAssistant, hass_client_no_auth, freezer: FrozenDateTimeFactory) -> None:
    await _setup(hass)
    client = await hass_client_no_auth()
    assert hass.states.get("binary_sensor.avvisi_targhe_app_attiva").state == "off"
    status = {"app": "accesa", "versione": "1.0.60", "nvr": "Collegato", "dettaglio": "", "telecamera_muta": True,
              "ultima_targa": None, "passaggi_oggi": 12, "pausa_fino": None,
              "dentro": [{"targa": "AB123CD", "nome": "Mario", "dal": dt_util.now().isoformat()}]}
    resp = await client.post(f"/api/webhook/{WEBHOOK}", json={"tipo": "stato", "stato": status})
    assert resp.status == 200
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.avvisi_targhe_app_attiva").state == "on"
    assert hass.states.get("binary_sensor.avvisi_targhe_telecamera_muta").state == "on"
    assert hass.states.get("binary_sensor.avvisi_targhe_pausa").state == "off"
    assert hass.states.get("sensor.avvisi_targhe_stato_nvr").state == "collegato"
    assert hass.states.get("sensor.avvisi_targhe_veicoli_dentro").state == "1"
    assert hass.states.get("sensor.avvisi_targhe_passaggi_oggi").state == "12"

    # Nessuno stato per più di 3 minuti: app ferma, e quello che dipende da lei non è più noto.
    freezer.tick(timedelta(minutes=4))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.avvisi_targhe_app_attiva").state == "off"
    assert hass.states.get("binary_sensor.avvisi_targhe_telecamera_muta").state == "unavailable"


async def test_chiusa_apposta_e_riavvio_di_home_assistant(hass: HomeAssistant, hass_client_no_auth) -> None:
    entry = await _setup(hass)
    client = await hass_client_no_auth()
    await client.post(f"/api/webhook/{WEBHOOK}", json=_passage())
    await client.post(f"/api/webhook/{WEBHOOK}", json={"tipo": "stato", "stato": {"app": "chiusa", "nvr": "collegato"}})
    await hass.async_block_till_done()
    running = hass.states.get("binary_sensor.avvisi_targhe_app_attiva")
    assert running.state == "off"
    assert running.attributes["chiusa_apposta"] is True

    # Ricaricata (come dopo un riavvio): ultima targa e foto restano.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.avvisi_targhe_ultima_targa").state == "AB123CD"
    assert hass.states.get("image.avvisi_targhe_ultima_foto").state != "unavailable"


async def test_ultimi_passaggi_in_ordine(hass: HomeAssistant, hass_client_no_auth) -> None:
    entry = await _setup(hass)
    client = await hass_client_no_auth()
    now = dt_util.now().replace(microsecond=0)
    for i, plate in enumerate(["AA111AA", "BB222BB", "CC333CC"]):
        await client.post(f"/api/webhook/{WEBHOOK}", json=_passage(quando=(now + timedelta(seconds=i)).isoformat(), targa=plate))
    # Arretrato: finisce in fondo, non in cima.
    await client.post(f"/api/webhook/{WEBHOOK}", json=_passage(quando=(now - timedelta(hours=1)).isoformat(), targa="OLD111"))
    await hass.async_block_till_done()
    recent = hass.states.get("sensor.avvisi_targhe_ultima_targa").attributes["ultimi"]
    assert [p["targa"] for p in recent] == ["CC333CC", "BB222BB", "AA111AA", "OLD111"]
    assert "foto" not in recent[0]
    for i in range(30):
        await client.post(f"/api/webhook/{WEBHOOK}", json=_passage(quando=(now + timedelta(minutes=1, seconds=i)).isoformat(), targa=f"X{i:03d}XX"))
    await hass.async_block_till_done()
    assert len(hass.states.get("sensor.avvisi_targhe_ultima_targa").attributes["ultimi"]) == 20
    # Dopo un riavvio l'elenco resta.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.get("sensor.avvisi_targhe_ultima_targa").attributes["ultimi"]) == 20


async def test_storico_per_giorno(hass: HomeAssistant, hass_client_no_auth, freezer: FrozenDateTimeFactory) -> None:
    await hass.config.async_set_time_zone("Europe/Rome")
    freezer.move_to("2026-10-20 12:00:00+02:00")
    entry = await _setup(hass)
    client = await hass_client_no_auth()
    now = dt_util.now().replace(microsecond=0)
    # Più di 20 passaggi oggi: lo storico li tiene tutti, "ultimi" resta a 20.
    for i in range(30):
        await client.post(f"/api/webhook/{WEBHOOK}", json=_passage(quando=(now - timedelta(minutes=i)).isoformat(),
                                                                      targa=f"A{i:03d}AA", verso="entrata" if i % 3 else "uscita"))
    # Riempimento dall'app: ieri, 3 giorni fa, un doppione e uno troppo vecchio (8 giorni).
    ieri = (now - timedelta(days=1)).isoformat()
    storico = [{"quando": ieri, "targa": "IE111RI", "verso": "uscita", "tipo": "sconosciuta"},
               {"quando": ieri, "targa": "IE111RI", "verso": "uscita", "tipo": "sconosciuta"},
               {"quando": (now - timedelta(days=3)).isoformat(), "targa": "TR333EE", "verso": "entrata"},
               {"quando": (now - timedelta(days=8)).isoformat(), "targa": "VE888CC", "verso": "entrata"},
               {"quando": "boh", "targa": "XX"}]
    events = async_capture_events(hass, EVENT_PASSAGE)
    resp = await client.post(f"/api/webhook/{WEBHOOK}", json={"tipo": "storico", "passaggi": storico})
    assert resp.status == 200
    assert (await resp.json())["aggiunti"] == 2
    await hass.async_block_till_done()
    assert events == []  # lo storico non fa partire automazioni
    assert hass.states.get("sensor.avvisi_targhe_passaggi_oggi").state == "30"
    assert len(hass.states.get("sensor.avvisi_targhe_ultima_targa").attributes["ultimi"]) == 20

    day = hass.states.get("sensor.avvisi_targhe_passaggi_giorno")
    assert day.state == "30"
    assert day.attributes["etichetta"] == "Oggi"
    assert day.attributes["entrate"] == 20 and day.attributes["uscite"] == 10
    assert day.attributes["passaggi"][0]["targa"] == "A000AA"
    assert day.attributes["passaggi"][0]["ora"] == "12:00"

    select = hass.states.get("select.avvisi_targhe_giorno")
    assert select.state == "Oggi"
    assert select.attributes["options"][:3] == ["Oggi", "Ieri", "Sabato 17/10"]
    assert len(select.attributes["options"]) == 7
    await hass.services.async_call("select", "select_option", {"entity_id": "select.avvisi_targhe_giorno", "option": "Ieri"},
                                   blocking=True)
    day = hass.states.get("sensor.avvisi_targhe_passaggi_giorno")
    assert day.state == "1"
    assert day.attributes["passaggi"][0]["targa"] == "IE111RI"

    # Dopo un riavvio storico e giorno scelto restano.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("select.avvisi_targhe_giorno").state == "Ieri"
    assert hass.states.get("sensor.avvisi_targhe_passaggi_giorno").state == "1"

    # A mezzanotte "Ieri" diventa il giorno di oggi: il giorno scelto è relativo.
    freezer.move_to("2026-10-21 00:00:30+02:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.avvisi_targhe_passaggi_giorno").state == "30"

    # Dopo 7 giorni lo storico si svuota da solo.
    freezer.move_to("2026-10-28 00:00:30+02:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert entry.runtime_data.hub.history == []
