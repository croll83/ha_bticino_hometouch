# Changelog

Tutte le modifiche significative a questo progetto saranno documentate in questo file.

## [4.1.0] - 2026-02-04

### Aggiunto

- **Configurazione indirizzi serrature**: Nuovo campo nelle opzioni dell'integrazione per specificare gli indirizzi OpenWebNet delle serrature. Permette di configurare serrature con indirizzi non sequenziali (es. 0, 1, 6).

### Risolto

- **Fix sblocco porte multiple**: Corretto il formato del comando OpenWebNet per le serrature. Il formato corretto è `*8*19*{gateway_id}{lock_addr}##` (es. `*8*19*20##` per gateway ID 2 e serratura all'indirizzo 0).

### Come trovare gli indirizzi delle serrature

1. Sul gateway BTicino, vai in **Impostazioni Avanzate → Videocitofonia**
2. Trova l'ID del gateway (solitamente "2")
3. Per ogni serratura, trova l'indirizzo configurato (es. 0, 1, 6)
4. L'indirizzo completo è: `{ID_Gateway}{Indirizzo_Serratura}`
   - Esempio: Gateway ID `2`, Serrature agli indirizzi `0`, `1`, `6` → inserisci `20, 21, 26`

---

## [4.0.0] - 2026-02-03

### ⚠️ Breaking Changes
- **WebRTC via go2rtc**: Il sistema video è stato completamente riscritto per usare WebRTC tramite go2rtc invece di HLS. Questo richiede il go2rtc add-on installato.
- **Configurazione go2rtc richiesta**: Gli stream BTicino vengono configurati automaticamente in `go2rtc.yaml` al primo avvio.

### Aggiunto
- **WebRTC streaming**: Nuovo sistema di streaming video tramite go2rtc con latenza significativamente ridotta rispetto a HLS
- **Auto-configurazione go2rtc**: L'integrazione configura automaticamente gli stream `bticino_live_1` attraverso `bticino_live_10` in `go2rtc.yaml`
- **HTTPS WebRTC proxy**: Nuovo endpoint `/api/bticino_hometouch/webrtc/{stream_name}` per supportare WebRTC su connessioni HTTPS
- **FFmpeg re-encoding**: Il video viene ri-encodato con keyframe frequenti per evitare artefatti verdi all'inizio dello stream
- **Rilevamento fine stream**: La card rileva automaticamente quando lo stream viene chiuso dal gateway e mostra "Stream terminato"
- **Gestione errori migliorata**: Messaggi di errore specifici per ogni codice SIP (486 → "Citofono occupato", 408 → "Timeout", ecc.)

### Modificato
- **Card v4.5.0**: Interfaccia aggiornata con:
  - Rilevamento fine stream tramite evento `bticino_hometouch_call_ended`
  - Icone dedicate per ogni tipo di errore (mdi:phone-off, mdi:timer-off, ecc.)
  - Rimosso messaggio latenza (variabile a seconda delle condizioni)
- **SIP client**: Aggiunto `last_error` per propagare errori specifici (486, 408, 480, 603) alla UI
- **Media proxy**: FFmpeg usa libx264 con CRF 23, preset veryfast, keyframe ogni 15 frame

### Rimosso
- **HLS streaming**: Il fallback HLS è stato rimosso in favore del solo WebRTC
- **Media proxy converter basato su RTP**: Sostituito con FFmpeg subprocess che fa push RTSP a go2rtc

### Requisiti
- Home Assistant 2024.11+ (go2rtc built-in) oppure go2rtc add-on installato
- FFmpeg con libx264 (incluso in HA)

---

## [3.0.0] - 2026-02-02

### ⚠️ Breaking Changes
- **Audio rimosso**: Dopo test approfonditi, la funzionalità audio è stata rimossa. Il gateway cloud BTicino (`sipserver.bs.iotleg.com`) rifiuta sistematicamente le offerte SDP audio da client non-Linphone.

### Modificato
- Le chiamate video ora usano solo `VIDEO_ONLY` invece di `AUDIO_VIDEO`
- Card custom semplificata - rimosso pulsante mic e controlli audio
- La card mostra "Solo video (audio non disponibile)" nel popup

### Aggiunto
- **Banner chiamata in arrivo**: Banner arancione pulsante quando qualcuno suona al citofono
- README aggiornato con sezione limitazioni in evidenza

### Rimosso
- Entity `BticinoEnableAudioButton`
- Funzionalità microfono dalla card custom
- Codice G.711 A-law encoder/decoder dalla card

---

## Investigazione Audio - Riassunto Completo

### Il Problema
Volevamo implementare audio bidirezionale (sentire il citofono e parlare) in Home Assistant, come fa l'app ufficiale BTicino Door Entry Touch.

### Cosa Abbiamo Provato

#### 1. Approccio Iniziale - SDP con Audio
Aggiunto `m=audio` all'SDP con varie configurazioni:
- Codec OPUS (payload type 98) - stesso dell'app ufficiale
- PCMA/PCMU (G.711 a-law/mu-law)
- Vari sample rate (8kHz, 48kHz)
- Direzione `sendrecv` per audio bidirezionale

**Risultato**: Il gateway ha sempre risposto con `m=audio 0` (porta 0 = rifiutato)

#### 2. Analisi PCAP
Catturato traffico di rete dall'app ufficiale con PCAPdroid:
- Scoperto che l'app usa porte 7076 (audio) e 9078 (video)
- Traffico audio bidirezionale (895 pacchetti inviati, 864 ricevuti)
- L'app usa codec OPUS con crittografia SRTP

#### 3. Decompilazione APK
Decompilato l'APK BTicino Door Entry Touch:
- Trovato config `linphonerc_factory` con assegnazione porte
- Scoperto `MediaEncryption = 1` (SRTP)
- L'app imposta `setAudioDirection(MediaDirection.SendRecv)` per chiamate VDE
- Le telecamere TVCC disabilitano esplicitamente l'audio con `MediaDirection.Inactive`

#### 4. Variazioni SDP Testate

| Configurazione | Risultato |
|---------------|-----------|
| OPUS + PCMA + PCMU | `m=audio 0` |
| Solo PCMA | `m=audio 0` |
| RTP plain (senza SRTP) | `m=audio 0` |
| Porte diverse (7076 vs dinamiche) | `m=audio 0` |
| Con/senza `a=rtcp:` | `m=audio 0` |
| Con/senza `b=AS:` bandwidth | `m=audio 0` |
| Audio prima del video in SDP | `m=audio 0` |
| Audio dopo il video in SDP | `m=audio 0` |

#### 5. Analisi User-Agent
Verificato che il nostro User-Agent corrisponde all'app ufficiale: `VctLinphoneService/3.0.0`

### Causa Principale
Il gateway cloud BTicino (server Flexisip) sembra avere restrizioni lato server che accettano audio solo da:
1. Client che usano l'SDK Linphone reale (libreria nativa)
2. Client che completano qualche handshake o autenticazione non documentata

Il signaling SIP funziona correttamente (otteniamo 200 OK per il video), ma l'audio viene sistematicamente rifiutato indipendentemente dal formato SDP. Questa è probabilmente una restrizione intenzionale di BTicino/Legrand.

### Conclusione
L'audio non può essere implementato senza:
1. Usare l'SDK Linphone reale (che richiederebbe compilare codice nativo)
2. Che BTicino/Legrand modifichi le policy lato server
3. Reverse-engineering di protocolli proprietari aggiuntivi

L'integrazione ora si concentra su ciò che funziona:
- Streaming video (con ~8s di latenza per transcoding SRTP→HLS)
- Sblocco porta (funziona perfettamente)
- Notifiche chiamata in arrivo (solo banner)

---

## [2.2.0] - 2026-02-01

### Aggiunto

- **Camera on-demand**: Le entity camera ora avviano automaticamente una chiamata SIP quando viene richiesto lo stream video. Non è più necessario premere un pulsante prima di visualizzare il video.

- **Nomi stazioni negli attributi**: Ogni entity ora include `station_name` (Albani, Madruzzo, Scala B) negli attributi per facilitare il routing con sistemi come Jarvis.

- **Custom card v2.0**: Nuova card Lovelace con:
  - Grid di 3 stazioni con icone
  - Popup fullscreen con video player (go2rtc iframe)
  - Pulsanti Apri/Audio/Chiudi nel popup
  - Indicatore stato connessione SIP

### Risolti

- **Fix 486 Busy Here**: Rimosso parametro `CAMERASLIDING` dall'INVITE iniziale. L'app ufficiale lo usa solo per cambiare camera durante una chiamata attiva, non nell'INVITE iniziale.

- **Fix DEVADDR mapping**: Corretto il mapping da 60/61/66 a 20/21/26. I dispositivi VDE usano deviceDev=2, non 6.

- **Fix race condition 407**: La riconnessione dopo 407 Proxy Authentication ora avviene nel loop principale invece che inline, evitando l'errore "read() called while another coroutine is already waiting".

### Modificato

- **Dashboard aggiornata**: Nuova configurazione Lovelace con card custom e entity ID corretti.

- **Cleanup repository**: Aggiunto `.gitignore`, rimossi file cache e test.

---

## [2.1.0] - 2025-02-01

### Risolti

- **Fix critico sblocco porta SIP MESSAGE**: Completamente riscritta l'autenticazione per i comandi di sblocco porta. Implementato il flusso completo di Digest Authentication con supporto per:
  - Gestione 407 Proxy Authentication Required
  - Supporto `qop=auth` con `nc` (nonce count) e `cnonce` (client nonce)
  - Parsing e inclusione parametro `opaque`
  - Destinazione MESSAGE corretta: `sip:MHT@{domain}` invece di indirizzo MAC

- **Fix loop infinito autenticazione REGISTER**: Aggiunto tracking dei tentativi di autenticazione (`_auth_attempts`, `_auth_in_progress`) per prevenire loop infiniti con risposte 401.

- **Fix username con dominio doppio**: Estratta automaticamente solo la parte utente se l'username contiene già il dominio (es. `user@domain` → `user`).

### Aggiunto

- **Apartment code configurabile**: Nuovo campo opzionale `apartment_code` nella configurazione per specificare il codice OpenWebNet dell'appartamento/unità nei comandi serratura. Utile per impianti multi-appartamento.

### Modificato

- **Logging ottimizzato**: Ridotto il livello di log per i messaggi SIP MESSAGE da INFO a DEBUG per evitare log eccessivamente verbosi in produzione.

---

## [2.0.3] - 2025-02-01

### Risolti

- **Fix critico SSL/TLS**: Risolto errore `certfile should be a valid filesystem path` nel client SIP. I certificati vengono ora scritti in file temporanei prima di essere caricati nel contesto SSL, utilizzando `run_in_executor()` per evitare operazioni bloccanti nel loop asyncio.

- **Integrazione go2rtc Add-on**: Completamente riscritta l'integrazione con go2rtc per utilizzare l'add-on esistente di Home Assistant invece di tentare di avviare un processo separato. Il proxy ora si connette automaticamente all'add-on tramite API HTTP, cercando tra gli hostname comuni (`a889bffc-go2rtc`, `homeassistant`, `localhost`).

- **Entity ID standardizzati**: Tutti gli entity ID sono ora esplicitamente definiti per garantire nomi consistenti e predicibili:
  - `binary_sensor.bticino_hometouch_doorbell`
  - `binary_sensor.bticino_hometouch_connection`
  - `button.bticino_hometouch_unlock_door_{n}`
  - `button.bticino_hometouch_answer_call`
  - `button.bticino_hometouch_hangup_call`
  - `camera.bticino_hometouch_outdoor_station_{n}`

- **Blocking call warnings**: Risolti i warning di chiamate bloccanti nel loop asyncio spostando le operazioni di I/O su file e SSL nell'executor.

### Aggiunto

- **Supporto multi-stazione nelle automazioni**: Gli eventi `bticino_hometouch_incoming_call` ora includono sempre `station_id` per identificare il posto esterno chiamante. Le automazioni di esempio in `install_it.md` sono state aggiornate per supportare scenari multi-stazione con template Jinja2 dinamici.

- **Rilevamento automatico go2rtc**: Il sistema ora rileva automaticamente l'add-on go2rtc su diversi hostname possibili, migliorando la compatibilità con diverse configurazioni di Home Assistant.

### Modificato

- **Documentazione aggiornata**: `install_it.md` ora include automazioni con supporto dinamico per `station_id`, notifiche con immagine dalla camera corretta, e script che gestiscono correttamente più posti esterni.

---

## [2.0.0] - 2025-01-XX (Release iniziale)

### Aggiunto

- Integrazione SIP/TLS con autenticazione certificato client
- Supporto videocitofono con stream video SRTP
- Ricezione chiamate in arrivo con notifiche push
- Controllo serrature (fino a 3 comandi configurabili)
- Entità camera con supporto WebRTC
- Sensori binari per stato campanello e connessione
- Pulsanti per rispondere, riagganciare e aprire porte
- Eventi Home Assistant per automazioni
- Documentazione installazione completa
