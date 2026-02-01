# Changelog

Tutte le modifiche significative a questo progetto saranno documentate in questo file.

## [2.0.5] - 2025-02-01

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

### File modificati

- `sip_client.py` - Riscritta autenticazione MESSAGE, aggiunto proxy auth con qop=auth
- `const.py` - Aggiunta costante `CONF_APARTMENT_CODE`
- `config_flow.py` - Campo apartment_code nel form di configurazione
- `coordinator.py` - Passaggio apartment_code a SIPConfig
- `translations/en.json` - Traduzione campo apartment_code
- `translations/it.json` - Traduzione campo apartment_code

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

### File modificati

- `sip_client.py` - Riscritta gestione certificati SSL
- `media_proxy.py` - Nuova classe Go2RTCProxy per add-on
- `camera.py` - Entity ID espliciti
- `button.py` - Entity ID espliciti
- `binary_sensor.py` - Entity ID espliciti
- `coordinator.py` - Riferimento corretto all'entity camera
- `install_it.md` - Automazioni multi-stazione

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
