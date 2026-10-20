# Avvisi Targhe per Home Assistant

Integrazione per Home Assistant che riceve dall'app **Avvisi Targhe** (lettura targhe con NVR Dahua) ogni
passaggio, con la foto, e lo stato dell'app. L'app chiama un webhook sulla rete locale: Home Assistant non
interroga nessuno e non serve internet.

## Installazione

1. HACS → Integrazioni → ⋮ → Archivi digitali personalizzati → questo repository, categoria *Integrazione*.
2. Installa **Avvisi Targhe** e riavvia Home Assistant.
3. Impostazioni → Dispositivi e servizi → Aggiungi integrazione → **Avvisi Targhe**: copia il codice mostrato.
4. Nell'app, **Opzioni → Home Assistant**: indirizzo di Home Assistant (es. `http://192.168.1.50:8123`) e
   codice, poi **Prova**.

## Entità

| Entità | Cosa mostra |
|---|---|
| `sensor.avvisi_targhe_ultima_targa` | ultima targa, con nome, verso, tipo e ora negli attributi |
| `sensor.avvisi_targhe_ultimo_passaggio` | ora dell'ultimo passaggio |
| `sensor.avvisi_targhe_passaggi_oggi` | passaggi di oggi |
| `sensor.avvisi_targhe_veicoli_dentro` | veicoli entrati e non usciti (elenco negli attributi) |
| `sensor.avvisi_targhe_stato_nvr` | collegato / scollegato / avvio |
| `image.avvisi_targhe_ultima_foto` | foto dell'ultimo passaggio |
| `event.avvisi_targhe_passaggio` | un evento per passaggio: entrata, uscita, passaggio |
| `binary_sensor.avvisi_targhe_app_attiva` | spento se l'app tace da 3 minuti |
| `binary_sensor.avvisi_targhe_telecamera_muta` | la telecamera non legge targhe da ore |
| `binary_sensor.avvisi_targhe_pausa` | avvisi in pausa |

## Automazioni

A ogni passaggio parte anche l'evento `avvisi_targhe_passaggio` con `targa`, `nome`, `verso`
(`entrata`/`uscita`/`passaggio`), `tipo` (`sconosciuta`, `nota`, `segui`, `silenziosa`, `nera`), `quando`,
`targa_nota`, `ritardo_s`, `in_ritardo` e `ha_foto`.

```yaml
triggers:
  - trigger: event
    event_type: avvisi_targhe_passaggio
    event_data:
      targa: AB123CD
      verso: entrata
conditions:
  - condition: template
    value_template: "{{ not trigger.event.data.in_ritardo }}"
actions:
  - action: notify.mobile_app_iphone
    data:
      message: "{{ trigger.event.data.nome or trigger.event.data.targa }} è arrivato"
```

`in_ritardo` è vero per i passaggi arrivati più di 5 minuti dopo (Home Assistant spento o rete giù):
l'app li manda quando torna il collegamento, fino a 24 ore indietro.
