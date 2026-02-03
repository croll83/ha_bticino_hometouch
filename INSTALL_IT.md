# Guida Installazione BTicino Hometouch per Home Assistant

Questa guida ti mostrerà passo dopo passo come installare e configurare l'integrazione BTicino Door Entry Touch per Home Assistant.

## ⚠️ Limitazioni Importanti

**Leggi attentamente prima di installare:**

| Limitazione | Descrizione |
|-------------|-------------|
| **Niente Audio** | L'audio NON è disponibile. Il gateway cloud BTicino non accetta stream audio da client SIP di terze parti. |
| **Latenza Video** | Il video ha circa 1-3 secondi di latenza (variabile in base alle condizioni di rete). |
| **Solo On-Demand** | Sono supportate solo le chiamate video avviate da Home Assistant. |
| **Chiamate in Arrivo** | Le chiamate in arrivo mostrano un banner nella card, ma non possono essere risposte con video/audio. Usa l'app ufficiale BTicino per gestire le chiamate in arrivo. |
| **Sblocco Porte** | I comandi di sblocco porta funzionano perfettamente, anche durante le chiamate in arrivo. |

---

## Indice

1. [Prerequisiti](#prerequisiti)
2. [Installazione](#installazione)
3. [Configurazione go2rtc](#configurazione-go2rtc)
4. [Configurazione Integrazione](#configurazione-integrazione)
5. [Port Forwarding](#port-forwarding)
6. [Aggiungere la Card](#aggiungere-la-card)
7. [Automazioni Opzionali](#automazioni-opzionali)
8. [Risoluzione Problemi](#risoluzione-problemi)

---

## Prerequisiti

### Dati Necessari

| Dato | Esempio | Dove trovarlo |
|------|---------|---------------|
| Email BTicino | `tuonome@gmail.com` | La stessa dell'app Door Entry Touch |
| Password BTicino | `password123` | La stessa dell'app Door Entry Touch |
| MAC del Gateway | `00:03:50:B2:0E:1F` | Etichetta sul gateway o app Door Entry Touch |
| IP Pubblico | `1.2.3.4` | [whatismyip.com](https://whatismyip.com) |
| N. Posti Esterni | `2` | Conta i videocitofoni esterni |
| N. Serrature | `3` | Conta le serrature controllabili |

### Requisiti Sistema

- Home Assistant 2024.11+ (con go2rtc integrato) **OPPURE** add-on go2rtc installato
- FFmpeg con libx264 (incluso in Home Assistant)
- HACS installato (consigliato)
- Accesso al router per configurare il port forwarding

---

## Installazione

### Opzione A: HACS (Consigliato)

1. Apri **HACS** in Home Assistant
2. Vai su **Integrations → Menu (⋮) → Custom repositories**
3. Aggiungi l'URL del repository GitHub
4. Categoria: **Integration**
5. Cerca "**BTicino Hometouch**" e installa
6. **Riavvia Home Assistant**

### Opzione B: Installazione Manuale

1. Scarica il repository
2. Copia la cartella `custom_components/bticino_hometouch` in `/config/custom_components/`
3. **Riavvia Home Assistant**

---

## Configurazione go2rtc

L'integrazione usa go2rtc per lo streaming WebRTC a bassa latenza.

### Auto-configurazione (Automatica)

Al primo avvio, l'integrazione configura automaticamente gli stream in `go2rtc.yaml`:
- Vengono aggiunti gli stream `bticino_live_1` fino a `bticino_live_10`
- Questi stream accettano il push RTSP da FFmpeg

**Dopo la prima installazione**, riavvia l'add-on go2rtc (o Home Assistant) per registrare gli stream.

### Verifica Configurazione

Puoi verificare che gli stream siano configurati controllando `/config/go2rtc.yaml`:

```yaml
streams:
  bticino_live_1: []
  bticino_live_2: []
  bticino_live_3: []
  # ... fino a bticino_live_10
```

---

## Configurazione Integrazione

### Passo 1: Aggiungi l'Integrazione

1. Vai su **Settings → Devices & Services → Add Integration**
2. Cerca "**BTicino Hometouch**"
3. Clicca su di esso

### Passo 2: Inserisci i Dati

| Campo | Descrizione | Esempio |
|-------|-------------|---------|
| Email | Email account BTicino | `tuonome@gmail.com` |
| Password | Password account BTicino | `password123` |
| Indirizzo MAC Gateway | MAC del gateway | `00:03:50:B2:0E:1F` |
| IP Pubblico | Il tuo IP pubblico per lo streaming | `1.2.3.4` |
| Numero di Posti Esterni | Quante telecamere hai | `2` |
| Numero di Serrature | Quante serrature hai | `3` |

### Passo 3: Attendi il Provisioning

Clicca "**Submit**" e l'integrazione automaticamente:

1. ✅ Effettua il login ai server BTicino
2. ✅ Scopre il tuo impianto e gateway
3. ✅ Crea un nuovo dispositivo SIP chiamato "HomeAssistant"
4. ✅ Genera e ottiene i certificati TLS
5. ✅ Si registra al server SIP
6. ✅ Configura gli stream go2rtc

**Fatto!** Le entity sono state create.

---

## Port Forwarding

⚠️ **Questo passo è OBBLIGATORIO per il video!**

Configura il tuo router/firewall per inoltrare la porta UDP 9078:

| Protocollo | Porta Esterna | Porta Interna | IP Interno | Descrizione |
|------------|---------------|---------------|------------|-------------|
| **UDP** | 9078 | 9078 | IP di HA (es. 192.168.1.18) | Stream Video SRTP |

### Note Importanti

- Il gateway cloud BTicino invia il video SRTP al tuo IP pubblico sulla porta 9078
- **Senza questo port forwarding il video NON funzionerà** (la chiamata si connette ma niente video)
- Il signaling SIP (porta 5061) NON necessita di forwarding - è solo in uscita
- La porta audio (7076) non è usata dato che l'audio non è supportato

### Come Configurare (Esempio)

1. Accedi al pannello del router (di solito `192.168.1.1`)
2. Cerca "Port Forwarding", "NAT" o "Virtual Server"
3. Aggiungi una regola:
   - Protocollo: UDP
   - Porta esterna: 9078
   - Porta interna: 9078
   - IP destinazione: l'IP di Home Assistant

---

## Aggiungere la Card

### Passo 1: Aggiungi la Resource

La card viene copiata automaticamente in `/config/www/bticino-intercom-card.js` al primo avvio.

1. Vai su **Settings → Dashboards → Resources** (menu ⋮ in alto a destra)
2. Verifica che `/local/bticino-intercom-card.js` sia presente
3. Se non c'è, clicca **Add Resource**:
   - URL: `/local/bticino-intercom-card.js`
   - Tipo: **JavaScript Module**

### Passo 2: Aggiungi la Card alla Dashboard

Modifica la tua dashboard e aggiungi una card manuale con questo YAML:

```yaml
type: custom:bticino-intercom-card
title: Citofono
show_title: true
```

### Funzionalità della Card

La card custom include:

| Funzione | Descrizione |
|----------|-------------|
| **Pulsanti Stazioni** | Un pulsante per ogni posto esterno |
| **Video Popup** | Clicca per vedere il video WebRTC in fullscreen |
| **Sblocco Porta** | Pulsante per aprire la porta |
| **Banner Chiamata** | Banner arancione pulsante quando qualcuno suona |
| **Stream Terminato** | Messaggio chiaro quando il gateway chiude la chiamata |
| **Gestione Errori** | Messaggi specifici per ogni tipo di errore |
| **Feedback Visivo** | Animazioni per successo/errore sblocco porta |

### Messaggi di Stato

| Stato | Icona | Descrizione |
|-------|-------|-------------|
| Citofono occupato | mdi:phone-off | Il gateway è occupato (486) |
| Timeout connessione | mdi:timer-off | La connessione non è riuscita (408) |
| Stream terminato | mdi:video-off | Il gateway ha chiuso lo stream |
| Chiamata fallita | mdi:phone-cancel | Errore generico |

---

## Entity Create

L'integrazione crea automaticamente queste entity:

| Entity | Tipo | Descrizione |
|--------|------|-------------|
| `button.citofono_view_video_station_N` | Button | Avvia chiamata video alla stazione N |
| `button.citofono_unlock_door_N` | Button | Sblocca la porta N |
| `button.citofono_hangup_call` | Button | Termina la chiamata attiva |
| `camera.bticino_camera_N` | Camera | Entity camera per la stazione N |
| `binary_sensor.bticino_hometouch_doorbell` | Binary Sensor | ON quando qualcuno suona |
| `binary_sensor.bticino_hometouch_connection` | Binary Sensor | ON quando connesso al server SIP |

---

## Automazioni Opzionali

La card gestisce già internamente il banner per le chiamate in arrivo. Le automazioni sotto sono **opzionali** e servono solo se vuoi notifiche push sul telefono.

### Notifica Push sul Telefono

```yaml
automation:
  - alias: "Citofono - Notifica Mobile"
    trigger:
      - platform: event
        event_type: bticino_hometouch_incoming_call
    action:
      - service: notify.mobile_app_tuo_telefono  # Sostituisci con il tuo
        data:
          title: "Citofono"
          message: "Chiamata da {{ trigger.event.data.station_name | default('Posto Esterno') }}"
          data:
            tag: citofono
            actions:
              - action: "UNLOCK"
                title: "Apri Porta"
```

### Gestione Azione "Apri" dalla Notifica

```yaml
automation:
  - alias: "Citofono - Gestione Azione Notifica"
    trigger:
      - platform: event
        event_type: mobile_app_notification_action
        event_data:
          action: "UNLOCK"
    action:
      - service: button.press
        target:
          entity_id: button.citofono_unlock_door_1
```

---

## Risoluzione Problemi

### Video non funziona

1. ✅ Verifica che il port forwarding UDP 9078 sia configurato correttamente
2. ✅ Verifica che l'IP pubblico nella configurazione sia corretto
3. ✅ Verifica che go2rtc sia attivo e funzionante
4. ✅ Controlla i log di Home Assistant per errori

### "Email o password non validi"

- Verifica di usare le stesse credenziali dell'app Door Entry Touch
- Prova a fare logout/login nell'app per verificare che funzionino

### La chiamata si connette ma niente video

- Questo indica che il **port forwarding non è configurato** correttamente
- Il gateway riesce a comunicare via SIP ma non può inviare il video

### Video verde o artefatti

- Questo succede quando il player non riceve il keyframe iniziale
- Aspetta qualche secondo oppure riavvia lo stream
- Il re-encoding con FFmpeg minimizza questo problema

### "Citofono occupato" (errore 486)

- Il gateway è impegnato in un'altra chiamata (forse dall'app ufficiale)
- Aspetta e riprova

### Banner chiamata in arrivo non appare

- Verifica che la card sia la versione 4.5 o superiore
- Fai hard refresh del browser (Ctrl+Shift+R)
- Controlla la console del browser per errori

### Log di Debug

Abilita i log di debug in `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.bticino_hometouch: debug
```

---

## Eventi Disponibili

L'integrazione genera questi eventi Home Assistant:

| Evento | Descrizione | Dati |
|--------|-------------|------|
| `bticino_hometouch_incoming_call` | Qualcuno ha suonato | `station_id`, `station_name`, `call_id` |
| `bticino_hometouch_door_unlocked` | Porta sbloccata | `lock_id` |
| `bticino_hometouch_call_ended` | Chiamata terminata (stream chiuso dal gateway) | `station_id`, `call_id` |

---

## Architettura Video

```
BTicino Gateway → SRTP (criptato) → Home Assistant → FFmpeg (re-encode) → RTSP → go2rtc → WebRTC → Browser
```

Il flusso video:
1. Il gateway invia video SRTP-criptato H.264 al tuo IP pubblico (porta 9078)
2. FFmpeg decripta SRTP e ri-codifica con keyframe frequenti per evitare artefatti
3. Il video viene inviato a go2rtc via RTSP
4. go2rtc serve WebRTC al browser con bassa latenza

---

## Note Legali

Questa integrazione è fornita "così com'è" per uso personale. BTicino è un marchio registrato di Legrand. Questa integrazione non è affiliata con BTicino o Legrand.
