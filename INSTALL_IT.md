# Guida Installazione BTicino Hometouch per Home Assistant

Questa guida ti mostrerà passo dopo passo come installare e configurare l'integrazione BTicino Door Entry Touch per Home Assistant.

## Indice

1. [Prerequisiti](#prerequisiti)
2. [Estrazione Certificati](#estrazione-certificati)
3. [Installazione go2rtc](#installazione-go2rtc)
4. [Installazione Integrazione](#installazione-integrazione)
5. [Configurazione](#configurazione)
6. [Dashboard](#dashboard)
7. [Automazioni](#automazioni)
8. [Risoluzione Problemi](#risoluzione-problemi)

---

## Prerequisiti

### Software Necessario

- Home Assistant 2024.1 o superiore
- HACS (Home Assistant Community Store) - opzionale ma consigliato
- File Manager o accesso SSH a Home Assistant
- Python 3.10+ (per lo script di decriptazione)

### Dati Necessari dal tuo Videocitofono

Avrai bisogno dei seguenti dati (estratti dall'app BTicino):

| Dato | Esempio | Dove trovarlo |
|------|---------|---------------|
| SIP Username | `user@email.com-MACADDRESS@123456.bs.iotleg.com` | shared_prefs |
| SIP Password | `password123` | Database (criptato) |
| SIP Domain | `123456.bs.iotleg.com` | shared_prefs |
| Gateway Address | `SERIALNUMBER.bs.iotleg.com` | Database |
| Certificato Client | PEM file | Criptato in /files/certs/ |
| Chiave Privata | PEM file | Criptato in /files/ |
| Certificato CA | PEM file | Criptato in /files/certs/ |

---

## Estrazione Certificati

### Passo 1: Dump dei dati dall'app

Se hai un dispositivo Android con root o un emulatore:

 - Installa l'app Door Entry for Hometouch (https://apkpure.com/door-entry-for-hometouch/com.bticino.doorentrytouch/download)
 - Effettua la Login con le tue credenziali valide. Se vedi che carica i posti esterni e le serrature, anche se crasha subito (per mancanza dei servizi Google), i dati sono stati già salvati sul device, continua.

```bash
# Con ADB e root access
adb root
adb pull /data/data/com.bticino.doorentrytouch/ ./bticino_dump/
```

La struttura sarà:
```
bticino_dump/
├── shared_prefs/
│   └── com.bticino.doorentrytouch_preferences.xml
├── databases/
│   └── plantSQLite
└── files/
    ├── config2.plant      # File chiave (128KB random)
    ├── certs/
    │   ├── *.cert.rsa     # Certificati criptati
    │   └── *.cert.chain.rsa
    └── private            # Chiave privata criptata
```

### Passo 2: Decriptazione

Esegui lo script di decriptazione:

```bash
python3 decrypt_bticino_certs.py -c bticino_dump/files/config2.plant -d bticino_dump/files/certs/
```

Output:
```
[*] Extracting key from bticino_dump/files/config2.plant
[*] Key offset calculated: XXXXX (65536 - YYY)
[+] Key: xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
[+] Mode: NEW

[*] Processing: username.cert.rsa
[+] Decrypted successfully!
[+] Saved to: client.cert.pem

[*] Processing: username.cert.chain.rsa
[+] Decrypted successfully!
[+] Saved to: client.chain.pem

[*] Processing private key...
[+] Private key decrypted!
[+] Saved to: private.pem
```

### Passo 3: Verifica Certificati

```bash
# Verifica che certificato e chiave corrispondano
openssl x509 -noout -modulus -in decrypted_certs/client.cert.pem | md5sum
openssl rsa -noout -modulus -in decrypted_certs/private.pem | md5sum
# I due hash devono essere identici
```

---

## Installazione go2rtc

go2rtc è necessario per convertire lo stream SRTP in RTSP/WebRTC.

### Opzione A: Add-on Home Assistant (Consigliato)

1. Vai su **Settings → Add-ons → Add-on Store**
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
2. Vai su **Integrations → Menu (⋮) → Custom repositories**
3. Aggiungi URL del repository GitHub
4. Categoria: Integration
5. Cerca "BTicino Hometouch" e installa
6. Riavvia Home Assistant

### Opzione B: Installazione Manuale

1. Copia la cartella `custom_components/bticino_hometouch` in `/config/custom_components/`:

```bash
# Via SCP
scp -r custom_components/bticino_hometouch root@homeassistant:/config/custom_components/

# Oppure via File Manager / Samba
```

2. Copia la custom card:

```bash
# Copia in /config/www/
scp www/bticino-hometouch-card.js root@homeassistant:/config/www/
```

3. Riavvia Home Assistant

---

## Configurazione

### Passo 1: Aggiungi l'Integrazione

1. Vai su **Settings → Devices & Services → Add Integration**
2. Cerca "BTicino Hometouch"
3. Clicca su di esso

### Passo 2: Inserisci le Credenziali

**Schermata 1 - Dati SIP:**

| Campo | Valore |
|-------|--------|
| SIP Server | `sipserver.bs.iotleg.com` |
| SIP Port | `5228` |
| SIP Username | Il tuo username SIP completo |
| SIP Password | Password decriptata dal database |
| SIP Domain | Il tuo dominio (es. `123456.bs.iotleg.com`) |
| Gateway Address | Indirizzo gateway (es. `SERIAL.bs.iotleg.com`) |
| Number of Cameras | Numero di posti esterni (1-10) |
| Number of Locks | Numero di serrature (1-10) |

**Schermata 2 - Certificati:**

Copia e incolla il contenuto dei file PEM:

| Campo | File |
|-------|------|
| Client Certificate | `client.cert.pem` |
| Client Private Key | `private.pem` |
| CA Certificate Chain | `client.chain.pem` |

### Passo 3: Completa la Configurazione

Clicca "Submit" e attendi che l'integrazione si connetta.

Verifica nella pagina dell'integrazione:
- ✅ Stato connessione: verde
- ✅ Registrazione SIP: completata

---

## Dashboard

### Passo 1: Registra la Custom Card

Aggiungi al file `/config/configuration.yaml`:

```yaml
lovelace:
  resources:
    - url: /local/bticino-hometouch-card.js
      type: module
```

Oppure via UI:
1. Vai su **Settings → Dashboards → Resources**
2. Aggiungi risorsa:
   - URL: `/local/bticino-hometouch-card.js`
   - Tipo: JavaScript Module

### Passo 2: Crea la Dashboard

**Configurazione base (1 posto esterno, 2 serrature):**

```yaml
type: vertical-stack
cards:
  # Titolo
  - type: markdown
    content: "# 🏠 Videocitofono"

  # Card interattiva
  - type: custom:bticino-hometouch-card
    camera_entity: camera.bticino_hometouch_outdoor_station_1
    lock_entity: button.bticino_hometouch_unlock_door_1

  # Pulsanti serrature aggiuntive
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

  # Stato connessione
  - type: entities
    entities:
      - entity: binary_sensor.bticino_hometouch_connection
        name: Connessione
      - entity: binary_sensor.bticino_hometouch_doorbell
        name: Campanello
```

**Configurazione multi-camera (3 posti esterni):**

```yaml
type: vertical-stack
cards:
  - type: markdown
    content: "# 🏠 Videocitofono"

  - type: horizontal-stack
    cards:
      - type: custom:bticino-hometouch-card
        camera_entity: camera.bticino_hometouch_outdoor_station_1
        lock_entity: button.bticino_hometouch_unlock_door_1

      - type: custom:bticino-hometouch-card
        camera_entity: camera.bticino_hometouch_outdoor_station_2
        lock_entity: button.bticino_hometouch_unlock_door_2

      - type: custom:bticino-hometouch-card
        camera_entity: camera.bticino_hometouch_outdoor_station_3
        lock_entity: button.bticino_hometouch_unlock_door_3
```

---

## Automazioni

### Popup Automatico su Chiamata

Questa automazione apre automaticamente un popup con il video quando qualcuno suona:

```yaml
automation:
  - alias: "Videocitofono - Popup su chiamata"
    trigger:
      - platform: event
        event_type: bticino_hometouch_incoming_call
    action:
      - service: browser_mod.popup
        data:
          title: "🔔 Chiamata in arrivo"
          content:
            type: custom:bticino-hometouch-card
            camera_entity: >
              camera.bticino_hometouch_outdoor_station_{{ trigger.event.data.station_id }}
            lock_entity: >
              button.bticino_hometouch_unlock_door_{{ trigger.event.data.station_id }}
          size: wide
          dismissable: false
          timeout: 60000
        target:
          device_id: all
```

### Notifica su Companion App

```yaml
automation:
  - alias: "Videocitofono - Notifica mobile"
    trigger:
      - platform: event
        event_type: bticino_hometouch_incoming_call
    action:
      - service: notify.mobile_app_your_phone
        data:
          title: "🔔 Videocitofono"
          message: "Chiamata dal posto esterno {{ trigger.event.data.station_id }}"
          data:
            tag: hometouch_call
            group: hometouch
            actions:
              - action: "ANSWER"
                title: "📞 Rispondi"
                activationMode: "foreground"
              - action: "UNLOCK"
                title: "🔓 Apri"
              - action: "REJECT"
                title: "❌ Rifiuta"
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

---

## Risoluzione Problemi

### Problema: "SIP client not registered"

**Causa:** La connessione al server SIP non è riuscita.

**Soluzioni:**
1. Verifica che i certificati siano corretti
2. Controlla che la porta 5228 sia raggiungibile
3. Verifica le credenziali SIP

```bash
# Test connessione
openssl s_client -connect sipserver.bs.iotleg.com:5228 \
  -cert client.cert.pem -key private.pem -CAfile client.chain.pem
```

### Problema: Video non funziona

**Causa:** go2rtc non è configurato correttamente.

**Soluzioni:**
1. Verifica che go2rtc sia in esecuzione
2. Controlla i log di go2rtc
3. Verifica che le porte 8554 e 8555 siano aperte

```bash
# Verifica go2rtc
curl http://localhost:1984/api/streams
```

### Problema: Audio non funziona

**Causa:** WebRTC non negoziato correttamente.

**Soluzioni:**
1. Verifica che il browser supporti WebRTC
2. Controlla i permessi del microfono
3. Usa HTTPS (WebRTC richiede connessione sicura)

### Problema: Notifiche non arrivano

**Soluzioni:**
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

## Note sulla Configurazione Multi-Impianto

Ogni impianto BTicino può avere configurazioni diverse:

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

Questa integrazione è fornita "così com'è" per uso personale. BTicino è un marchio registrato di Legrand. Questa integrazione non è affiliata con BTicino o Legrand.
