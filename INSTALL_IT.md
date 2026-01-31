# Guida Installazione BTicino Hometouch per Home Assistant

Questa guida ti mostrerà passo dopo passo come installare e configurare l'integrazione BTicino Door Entry Touch per Home Assistant.

## Indice

1. [Prerequisiti](#prerequisiti)
2. [Installazione go2rtc](#installazione-go2rtc)
3. [Installazione Integrazione](#installazione-integrazione)
4. [Configurazione](#configurazione)
5. [Dashboard](#dashboard)
6. [Automazioni](#automazioni)
7. [Risoluzione Problemi](#risoluzione-problemi)

---

## Prerequisiti

### Software Necessario

- Home Assistant 2024.1 o superiore
- HACS (Home Assistant Community Store) - opzionale ma consigliato
- go2rtc (per lo streaming video)

### Dati Necessari

Ti serviranno solo queste informazioni:

| Dato | Esempio | Dove trovarlo |
|------|---------|---------------|
| Email BTicino | `tuonome@gmail.com` | La stessa email che usi nell'app Door Entry Touch |
| Password BTicino | `password123` | La stessa password che usi nell'app Door Entry Touch |
| MAC del Gateway | `00:03:50:B2:0E:1F` | Etichetta sul gateway o nell'app Door Entry Touch |
| N. Posti Esterni | `1` | Conta i videocitofoni esterni |
| N. Serrature | `2` | Conta le serrature controllabili |

---

## Installazione go2rtc

go2rtc e' necessario per convertire lo stream SRTP in WebRTC (video con audio bidirezionale).

### Opzione A: Add-on Home Assistant (Consigliato)

1. Vai su **Settings -> Add-ons -> Add-on Store**
2. Aggiungi repo https://github.com/AlexxIT/hassio-addons
3. Cerca "go2rtc" e installalo
4. Configura `/config/go2rtc.yaml`:

```yaml
api:
  listen: ":1984"

rtsp:
  listen: ":8554"

webrtc:
  listen: ":8555"
  candidates:
    - stun:stun.l.google.com:19302

streams: {}
# Gli stream saranno aggiunti dinamicamente dall'integrazione
```

5. Avvia l'add-on

### Opzione B: Installazione Manuale

```bash
# Scarica go2rtc
wget https://github.com/AlexxIT/go2rtc/releases/latest/download/go2rtc_linux_amd64
chmod +x go2rtc_linux_amd64
mv go2rtc_linux_amd64 /config/go2rtc/go2rtc
```

---

## Installazione Integrazione

### Opzione A: HACS (Consigliato)

1. Apri HACS in Home Assistant
2. Vai su **Integrations -> Menu (tre puntini) -> Custom repositories**
3. Aggiungi l'URL del repository GitHub
4. Categoria: Integration
5. Cerca "BTicino Hometouch" e installa
6. Riavvia Home Assistant

### Opzione B: Installazione Manuale

1. Copia la cartella `custom_components/bticino_hometouch` in `/config/custom_components/`:

```bash
# Via SCP
scp -r custom_components/bticino_hometouch root@homeassistant:/config/custom_components/
```

2. Riavvia Home Assistant

---

## Configurazione

### Passo 1: Aggiungi l'Integrazione

1. Vai su **Settings -> Devices & Services -> Add Integration**
2. Cerca "BTicino Hometouch"
3. Clicca su di esso

### Passo 2: Inserisci i Dati

Inserisci le seguenti informazioni:

| Campo | Descrizione | Esempio |
|-------|-------------|---------|
| Email | La tua email dell'account BTicino | `tuonome@gmail.com` |
| Password | La tua password dell'account BTicino | `password123` |
| Indirizzo MAC Gateway | MAC address del tuo gateway | `00:03:50:B2:0E:1F` |
| Numero di Posti Esterni | Quante telecamere/posti esterni hai | `1` |
| Numero di Serrature | Quante serrature hai | `2` |

### Passo 3: Attendi il Provisioning

Clicca "Submit" e l'integrazione automaticamente:

1. Effettua il login ai server BTicino
2. Scopre il tuo impianto e gateway
3. Crea un nuovo dispositivo SIP chiamato "HomeAssistant"
4. Genera e ottiene i certificati TLS
5. Configura tutto automaticamente

**Fatto!** Il tuo videocitofono e' pronto all'uso.

### Rinnovo Automatico Certificati

L'integrazione monitora automaticamente la scadenza dei certificati e li rinnova 30 giorni prima della scadenza. Non devi fare nulla!

---

## Dashboard

### Dashboard Semplice

```yaml
type: vertical-stack
cards:
  # Titolo
  - type: markdown
    content: "# Videocitofono"

  # Telecamera
  - type: picture-entity
    entity: camera.bticino_hometouch_outdoor_station_1
    camera_view: live

  # Pulsanti
  - type: horizontal-stack
    cards:
      - type: button
        entity: button.bticino_hometouch_unlock_door_1
        name: Apri Porta
        icon: mdi:door-open
        show_state: false

      - type: button
        entity: button.bticino_hometouch_answer_call
        name: Rispondi
        icon: mdi:phone
        show_state: false

      - type: button
        entity: button.bticino_hometouch_hangup_call
        name: Chiudi
        icon: mdi:phone-hangup
        show_state: false

  # Stato
  - type: entities
    entities:
      - entity: binary_sensor.bticino_hometouch_connection
        name: Connessione
      - entity: binary_sensor.bticino_hometouch_doorbell
        name: Campanello
```

### Dashboard con 2 Serrature

```yaml
type: vertical-stack
cards:
  - type: markdown
    content: "# Videocitofono"

  - type: picture-entity
    entity: camera.bticino_hometouch_outdoor_station_1
    camera_view: live

  # Pulsanti serrature
  - type: horizontal-stack
    cards:
      - type: button
        entity: button.bticino_hometouch_unlock_door_1
        name: Cancelletto
        icon: mdi:gate
        show_state: false

      - type: button
        entity: button.bticino_hometouch_unlock_door_2
        name: Carrabile
        icon: mdi:garage
        show_state: false

  # Pulsanti chiamata
  - type: horizontal-stack
    cards:
      - type: button
        entity: button.bticino_hometouch_answer_call
        name: Rispondi
        icon: mdi:phone
        show_state: false

      - type: button
        entity: button.bticino_hometouch_hangup_call
        name: Chiudi
        icon: mdi:phone-hangup
        show_state: false

  # Stato
  - type: entities
    entities:
      - entity: binary_sensor.bticino_hometouch_connection
        name: Connessione
      - entity: binary_sensor.bticino_hometouch_doorbell
        name: Campanello
```

### Dashboard Multi-Camera

```yaml
type: vertical-stack
cards:
  - type: markdown
    content: "# Videocitofono"

  - type: horizontal-stack
    cards:
      - type: picture-entity
        entity: camera.bticino_hometouch_outdoor_station_1
        camera_view: live
        name: Posto Esterno 1

      - type: picture-entity
        entity: camera.bticino_hometouch_outdoor_station_2
        camera_view: live
        name: Posto Esterno 2

  - type: horizontal-stack
    cards:
      - type: button
        entity: button.bticino_hometouch_unlock_door_1
        name: Porta 1
        icon: mdi:door-open
        show_state: false

      - type: button
        entity: button.bticino_hometouch_unlock_door_2
        name: Porta 2
        icon: mdi:door-open
        show_state: false
```

---

## Automazioni

### Notifica su Campanello

```yaml
automation:
  - alias: "Videocitofono - Notifica campanello"
    trigger:
      - platform: event
        event_type: bticino_hometouch_incoming_call
    action:
      - service: notify.mobile_app_your_phone
        data:
          title: "Campanello"
          message: "Qualcuno ha suonato al videocitofono"
          data:
            actions:
              - action: "UNLOCK_DOOR"
                title: "Apri"
              - action: "DISMISS"
                title: "Ignora"
```

### Notifica con Azioni Complete

```yaml
automation:
  - alias: "Videocitofono - Notifica mobile"
    trigger:
      - platform: event
        event_type: bticino_hometouch_incoming_call
    action:
      - service: notify.mobile_app_your_phone
        data:
          title: "Videocitofono"
          message: "Chiamata in arrivo"
          data:
            tag: hometouch_call
            group: hometouch
            actions:
              - action: "ANSWER"
                title: "Rispondi"
                activationMode: "foreground"
              - action: "UNLOCK"
                title: "Apri"
              - action: "REJECT"
                title: "Rifiuta"
                destructive: true
            push:
              sound:
                name: "default"
                critical: 1
                volume: 1.0
            entity_id: camera.bticino_hometouch_outdoor_station_1
```

### Gestione Azioni Notifica

```yaml
automation:
  - alias: "Videocitofono - Gestione azioni notifica"
    trigger:
      - platform: event
        event_type: mobile_app_notification_action
    condition:
      - condition: template
        value_template: "{{ trigger.event.data.action in ['ANSWER', 'UNLOCK', 'REJECT'] }}"
    action:
      - choose:
          - conditions:
              - condition: template
                value_template: "{{ trigger.event.data.action == 'ANSWER' }}"
            sequence:
              - service: button.press
                target:
                  entity_id: button.bticino_hometouch_answer_call

          - conditions:
              - condition: template
                value_template: "{{ trigger.event.data.action == 'UNLOCK' }}"
            sequence:
              - service: button.press
                target:
                  entity_id: button.bticino_hometouch_unlock_door_1

          - conditions:
              - condition: template
                value_template: "{{ trigger.event.data.action == 'REJECT' }}"
            sequence:
              - service: button.press
                target:
                  entity_id: button.bticino_hometouch_hangup_call
```

### Popup su Browser (richiede browser_mod)

```yaml
automation:
  - alias: "Videocitofono - Popup su chiamata"
    trigger:
      - platform: event
        event_type: bticino_hometouch_incoming_call
    action:
      - service: browser_mod.popup
        data:
          title: "Chiamata in arrivo"
          content:
            type: picture-entity
            entity: camera.bticino_hometouch_outdoor_station_1
            camera_view: live
          size: wide
          dismissable: false
          timeout: 60000
        target:
          device_id: all
```

---

## Risoluzione Problemi

### "Email o password non validi"

- Verifica di usare le stesse credenziali dell'app Door Entry Touch
- Prova a fare logout/login nell'app per verificare che funzionino

### "Impossibile connettersi ai server BTicino"

- Verifica la connessione internet
- I server BTicino potrebbero essere temporaneamente non disponibili
- Riprova piu' tardi

### Video non funziona

1. Verifica che go2rtc sia installato e in esecuzione
2. Controlla i log di go2rtc
3. Verifica che le porte 8554 e 8555 siano aperte

```bash
# Verifica go2rtc
curl http://localhost:1984/api/streams
```

### Audio non funziona

1. Verifica che il browser supporti WebRTC
2. Controlla i permessi del microfono
3. Usa HTTPS (WebRTC richiede connessione sicura)

### Notifiche non arrivano

1. Verifica la configurazione della Companion App
2. Controlla che il servizio notify sia disponibile
3. Verifica i permessi delle notifiche sul dispositivo

### Log di Debug

Abilita i log di debug in `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.bticino_hometouch: debug
```

---

## Note sulla Configurazione

Ogni impianto BTicino puo' avere configurazioni diverse:

| Scenario | Posti Esterni | Serrature |
|----------|---------------|-----------|
| Appartamento singolo | 1 | 1-2 |
| Villa con cancello | 1 | 2 (pedonale + carrabile) |
| Condominio | 1-3 | 1-3 |
| Ufficio | 2-4 | 2-4 |

Imposta correttamente il numero di posti esterni e serrature durante la configurazione iniziale.

---

## Supporto

- **Issues:** Apri un issue sul repository GitHub
- **Discussioni:** Community Home Assistant

---

## Note Legali

Questa integrazione e' fornita "cosi' com'e'" per uso personale. BTicino e' un marchio registrato di Legrand. Questa integrazione non e' affiliata con BTicino o Legrand.
