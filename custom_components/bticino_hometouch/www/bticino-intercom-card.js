/**
 * BTicino Intercom Card v3.0
 *
 * A compact Lovelace card for BTicino Door Entry intercom.
 * Displays all outdoor stations in a horizontal layout with video popup.
 *
 * Features:
 * - Compact horizontal layout with all stations
 * - Video popup with HLS streaming
 * - Door unlock with visual feedback
 * - Incoming call banner notification
 * - Play/pause controls
 *
 * Limitations:
 * - Audio is NOT supported (BTicino cloud gateway limitation)
 * - Video has ~8 second latency
 * - Only on-demand calls from HA are supported
 */

import "https://cdn.jsdelivr.net/npm/hls.js@latest";

class BticinoIntercomCard extends HTMLElement {

  static getConfigElement() {
    return document.createElement("bticino-intercom-card-editor");
  }

  static getStubConfig() {
    return {
      title: "Citofono",
      show_title: true
    };
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._rendered) return;
    this._updateCard();
  }

  setConfig(config) {
    this._config = {
      title: "Citofono",
      show_title: true,
      ...config
    };
    this._rendered = false;
    this._hlsPlayer = null;
    this._activeStation = null;
    this._popupOpen = false;
    this._unlockSubscription = null;
    this._incomingCallSubscription = null;
  }

  connectedCallback() {
    if (!this._rendered) {
      this._render();
      this._rendered = true;
    }
    this._subscribeToEvents();
  }

  disconnectedCallback() {
    this._closePopup();
    this._unsubscribeFromEvents();
  }

  async _subscribeToEvents() {
    if (this._hass && this._hass.connection) {
      try {
        // Subscribe to door unlock events
        this._unlockSubscription = await this._hass.connection.subscribeEvents(
          (event) => this._onDoorUnlocked(event),
          'bticino_hometouch_door_unlocked'
        );
        // Subscribe to incoming call events
        this._incomingCallSubscription = await this._hass.connection.subscribeEvents(
          (event) => this._onIncomingCall(event),
          'bticino_hometouch_incoming_call'
        );
      } catch (e) {
        console.warn('Failed to subscribe to events:', e);
      }
    }
  }

  _unsubscribeFromEvents() {
    if (this._unlockSubscription && typeof this._unlockSubscription === 'function') {
      try { this._unlockSubscription(); } catch (e) {}
      this._unlockSubscription = null;
    }
    if (this._incomingCallSubscription && typeof this._incomingCallSubscription === 'function') {
      try { this._incomingCallSubscription(); } catch (e) {}
      this._incomingCallSubscription = null;
    }
  }

  _onDoorUnlocked(event) {
    const lockId = event.data.lock_id;
    const btn = this.querySelector(`.action-btn.unlock[data-station-id="${lockId}"]`);
    if (btn) {
      btn.classList.remove('waiting', 'error');
      btn.classList.add('success');
      setTimeout(() => btn.classList.remove('success'), 5000);
    }
  }

  _onIncomingCall(event) {
    // Show incoming call banner
    const stationId = event.data.station_id;
    const stationName = event.data.station_name || `Posto Esterno ${stationId}`;
    this._showIncomingCallBanner(stationId, stationName);
  }

  _showIncomingCallBanner(stationId, stationName) {
    const banner = this.querySelector('#incoming-call-banner');
    const bannerText = this.querySelector('#incoming-call-text');

    if (banner && bannerText) {
      bannerText.textContent = `📞 Chiamata in arrivo da ${stationName}`;
      banner.classList.add('visible');
      banner.dataset.stationId = stationId;

      // Auto-hide after 30 seconds (typical ring duration)
      if (this._bannerTimeout) clearTimeout(this._bannerTimeout);
      this._bannerTimeout = setTimeout(() => {
        this._hideIncomingCallBanner();
      }, 30000);
    }
  }

  _hideIncomingCallBanner() {
    const banner = this.querySelector('#incoming-call-banner');
    if (banner) {
      banner.classList.remove('visible');
    }
    if (this._bannerTimeout) {
      clearTimeout(this._bannerTimeout);
      this._bannerTimeout = null;
    }
  }

  _getStations() {
    if (!this._hass) return [];

    const stations = [];
    const cameraEntities = Object.keys(this._hass.states)
      .filter(e => e.startsWith("camera.bticino_"))
      .sort();

    for (const entityId of cameraEntities) {
      const state = this._hass.states[entityId];
      const stationId = state.attributes.station_id;
      const stationName = state.attributes.station_name ||
                          state.attributes.friendly_name ||
                          entityId.replace("camera.bticino_", "").replace(/_/g, " ");

      stations.push({
        entityId,
        stationId: stationId || stations.length + 1,
        name: stationName,
        state: state,
        isRinging: state.attributes.is_ringing,
        isConnected: state.attributes.is_connected,
        hlsUrl: state.attributes.hls_url,
        hlsAvailable: state.attributes.hls_available,
        lastError: state.attributes.last_error
      });
    }

    return stations;
  }

  _render() {
    const stations = this._getStations();

    this.innerHTML = `
      <ha-card>
        <style>
          :host {
            --card-padding: 12px;
          }
          .card-container {
            padding: var(--card-padding);
          }
          .card-title {
            font-size: 1.1em;
            font-weight: 500;
            margin-bottom: 12px;
            color: var(--primary-text-color);
          }

          /* Incoming call banner */
          .incoming-call-banner {
            display: none;
            background: linear-gradient(135deg, #ff9800 0%, #f57c00 100%);
            color: white;
            padding: 12px 16px;
            margin-bottom: 12px;
            border-radius: 8px;
            font-weight: 500;
            animation: pulse-banner 1.5s infinite;
            align-items: center;
            justify-content: space-between;
          }
          .incoming-call-banner.visible {
            display: flex;
          }
          .incoming-call-banner .dismiss-btn {
            background: rgba(255,255,255,0.2);
            border: none;
            color: white;
            padding: 4px 8px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 0.9em;
          }
          .incoming-call-banner .dismiss-btn:hover {
            background: rgba(255,255,255,0.3);
          }
          @keyframes pulse-banner {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.85; }
          }

          .stations-row {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
          }
          .station-block {
            flex: 1;
            min-width: 120px;
            background: var(--card-background-color, var(--ha-card-background));
            border: 1px solid var(--divider-color);
            border-radius: 8px;
            padding: 10px;
            transition: all 0.2s ease;
          }
          .station-block:hover {
            border-color: var(--primary-color);
          }
          .station-block.ringing {
            border-color: var(--warning-color, #ff9800);
            animation: ring-pulse 1s infinite;
          }
          .station-block.connected {
            border-color: var(--success-color, #4caf50);
          }
          @keyframes ring-pulse {
            0%, 100% { box-shadow: 0 0 0 0 rgba(255, 152, 0, 0.4); }
            50% { box-shadow: 0 0 0 8px rgba(255, 152, 0, 0); }
          }
          .station-name {
            font-size: 0.9em;
            font-weight: 500;
            margin-bottom: 8px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
          }
          .station-actions {
            display: flex;
            gap: 8px;
            justify-content: center;
          }
          .action-btn {
            display: flex;
            align-items: center;
            justify-content: center;
            width: 40px;
            height: 40px;
            border-radius: 50%;
            border: none;
            cursor: pointer;
            transition: all 0.2s ease;
            background: var(--primary-background-color);
          }
          .action-btn:hover {
            transform: scale(1.1);
          }
          .action-btn:active {
            transform: scale(0.95);
          }
          .action-btn.view {
            color: var(--primary-color);
          }
          .action-btn.view:hover {
            background: var(--primary-color);
            color: white;
          }
          .action-btn.unlock {
            color: var(--primary-color);
          }
          .action-btn.unlock:hover {
            background: var(--primary-color);
            color: white;
          }
          .action-btn.unlock.success {
            background: var(--success-color, #4caf50) !important;
            color: white !important;
          }
          .action-btn.unlock.error {
            background: var(--error-color, #f44336) !important;
            color: white !important;
          }
          .action-btn.unlock.waiting {
            opacity: 0.6;
            cursor: wait;
          }
          .action-btn ha-icon {
            --mdc-icon-size: 20px;
          }

          /* Popup styles */
          .popup-overlay {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0, 0, 0, 0.8);
            z-index: 9999;
            align-items: center;
            justify-content: center;
          }
          .popup-overlay.open {
            display: flex;
          }
          .popup-content {
            background: var(--card-background-color, #fff);
            border-radius: 12px;
            overflow: hidden;
            max-width: 90vw;
            max-height: 90vh;
            width: 640px;
          }
          .popup-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 12px 16px;
            border-bottom: 1px solid var(--divider-color);
          }
          .popup-title {
            font-size: 1.1em;
            font-weight: 500;
          }
          .popup-close {
            background: none;
            border: none;
            cursor: pointer;
            padding: 4px;
            color: var(--secondary-text-color);
          }
          .popup-close:hover {
            color: var(--primary-text-color);
          }
          .video-container {
            position: relative;
            width: 100%;
            padding-bottom: 56.25%; /* 16:9 */
            background: #000;
          }
          .video-container video {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            object-fit: contain;
          }
          .video-container .placeholder {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            color: #888;
          }
          .video-container .placeholder ha-icon {
            --mdc-icon-size: 48px;
            margin-bottom: 8px;
          }
          .video-container .loading {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            color: white;
            background: rgba(0, 0, 0, 0.85);
            z-index: 10;
          }
          .video-container .loading .loading-spinner {
            margin-bottom: 16px;
          }
          .video-container .loading .loading-text {
            font-size: 1.1em;
            font-weight: 500;
            margin-bottom: 8px;
          }
          .video-container .loading .loading-status {
            font-size: 0.85em;
            color: rgba(255, 255, 255, 0.7);
            animation: pulse 1.5s infinite;
          }
          @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
          }
          .video-container .buffering-overlay {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            display: none;
            align-items: center;
            justify-content: center;
            background: rgba(0, 0, 0, 0.4);
            z-index: 12;
            pointer-events: none;
          }
          .video-container .buffering-overlay.visible {
            display: flex;
          }
          .video-container .buffering-overlay .buffering-content {
            display: flex;
            flex-direction: column;
            align-items: center;
            color: white;
          }
          .video-container .buffering-overlay .buffering-text {
            margin-top: 12px;
            font-size: 0.9em;
          }
          .popup-controls {
            display: flex;
            gap: 12px;
            padding: 16px;
            justify-content: center;
          }
          .popup-btn {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 10px 20px;
            border-radius: 8px;
            border: none;
            cursor: pointer;
            font-size: 0.95em;
            font-weight: 500;
            transition: all 0.2s ease;
          }
          .popup-btn.hangup {
            background: var(--error-color, #f44336);
            color: white;
          }
          .popup-btn.hangup:hover {
            filter: brightness(1.1);
          }
          .popup-btn ha-icon {
            --mdc-icon-size: 18px;
          }

          /* Limitations notice */
          .limitations-notice {
            font-size: 0.75em;
            color: var(--secondary-text-color);
            text-align: center;
            padding: 8px;
            border-top: 1px solid var(--divider-color);
          }
        </style>

        <div class="card-container">
          ${this._config.show_title ? `<div class="card-title">${this._config.title}</div>` : ''}

          <!-- Incoming call banner -->
          <div class="incoming-call-banner" id="incoming-call-banner">
            <span id="incoming-call-text">📞 Chiamata in arrivo...</span>
            <button class="dismiss-btn" id="dismiss-banner">✕</button>
          </div>

          <div class="stations-row" id="stations-row">
            ${stations.map(s => this._renderStation(s)).join('')}
          </div>
        </div>

        <div class="popup-overlay" id="popup-overlay">
          <div class="popup-content">
            <div class="popup-header">
              <span class="popup-title" id="popup-title">Video</span>
              <button class="popup-close" id="popup-close">
                <ha-icon icon="mdi:close"></ha-icon>
              </button>
            </div>
            <div class="video-container" id="video-container">
              <div class="placeholder" id="placeholder">
                <ha-icon icon="mdi:video-off"></ha-icon>
                <span>Connessione in corso...</span>
              </div>
              <video id="video" playsinline muted></video>
              <div class="loading" id="loading" style="display: none;">
                <ha-circular-progress indeterminate class="loading-spinner"></ha-circular-progress>
                <div class="loading-text">Connessione in corso...</div>
                <div class="loading-status" id="loading-status">Avvio chiamata video</div>
              </div>
              <div class="buffering-overlay" id="buffering-overlay">
                <div class="buffering-content">
                  <ha-circular-progress indeterminate></ha-circular-progress>
                  <div class="buffering-text">Buffering...</div>
                </div>
              </div>
            </div>
            <div class="popup-controls">
              <button class="popup-btn hangup" id="btn-hangup">
                <ha-icon icon="mdi:phone-hangup"></ha-icon>
                Chiudi
              </button>
            </div>
            <div class="limitations-notice">
              ⚠️ Solo video (audio non disponibile) • Latenza ~8s
            </div>
          </div>
        </div>
      </ha-card>
    `;

    this._bindEvents();
  }

  _renderStation(station) {
    const classes = ['station-block'];
    if (station.isRinging) classes.push('ringing');
    if (station.isConnected) classes.push('connected');

    return `
      <div class="${classes.join(' ')}" data-station-id="${station.stationId}" data-entity-id="${station.entityId}">
        <div class="station-name">${station.name}</div>
        <div class="station-actions">
          <button class="action-btn view" data-action="view" data-station-id="${station.stationId}" title="Visualizza">
            <ha-icon icon="mdi:video"></ha-icon>
          </button>
          <button class="action-btn unlock" data-action="unlock" data-station-id="${station.stationId}" title="Apri">
            <ha-icon icon="mdi:door-open"></ha-icon>
          </button>
        </div>
      </div>
    `;
  }

  _bindEvents() {
    // Station action buttons
    this.querySelectorAll('.action-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const action = btn.dataset.action;
        const stationId = parseInt(btn.dataset.stationId);

        if (action === 'view') {
          this._openVideoPopup(stationId);
        } else if (action === 'unlock') {
          this._unlockDoor(stationId, btn);
        }
      });
    });

    // Popup controls
    const popupClose = this.querySelector('#popup-close');
    const btnHangup = this.querySelector('#btn-hangup');
    const overlay = this.querySelector('#popup-overlay');
    const dismissBanner = this.querySelector('#dismiss-banner');

    if (popupClose) popupClose.addEventListener('click', () => this._closePopup());
    if (btnHangup) btnHangup.addEventListener('click', () => this._hangupAndClose());
    if (overlay) overlay.addEventListener('click', (e) => {
      if (e.target === overlay) this._closePopup();
    });
    if (dismissBanner) dismissBanner.addEventListener('click', () => this._hideIncomingCallBanner());

    // Video events
    const video = this.querySelector('#video');
    const bufferingOverlay = this.querySelector('#buffering-overlay');

    if (video) {
      video.addEventListener('playing', () => this._onVideoPlaying());

      video.addEventListener('waiting', () => {
        if (this._popupOpen && bufferingOverlay) {
          bufferingOverlay.classList.add('visible');
        }
      });

      video.addEventListener('canplay', () => {
        if (bufferingOverlay) bufferingOverlay.classList.remove('visible');
      });

      video.addEventListener('timeupdate', () => {
        if (!video.paused && bufferingOverlay && bufferingOverlay.classList.contains('visible')) {
          bufferingOverlay.classList.remove('visible');
        }
      });
    }
  }

  _updateCard() {
    const stations = this._getStations();
    const stationsRow = this.querySelector('#stations-row');

    if (stationsRow) {
      stationsRow.innerHTML = stations.map(s => this._renderStation(s)).join('');
      this.querySelectorAll('.action-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
          const action = btn.dataset.action;
          const stationId = parseInt(btn.dataset.stationId);

          if (action === 'view') {
            this._openVideoPopup(stationId);
          } else if (action === 'unlock') {
            this._unlockDoor(stationId, btn);
          }
        });
      });
    }

    // Check for incoming calls based on station state
    for (const station of stations) {
      if (station.isRinging) {
        this._showIncomingCallBanner(station.stationId, station.name);
        break;
      }
    }

    if (this._popupOpen && this._activeStation) {
      this._updateVideoStream();
    }
  }

  async _openVideoPopup(stationId) {
    this._activeStation = stationId;
    this._popupOpen = true;
    this._hideIncomingCallBanner();

    const overlay = this.querySelector('#popup-overlay');
    const title = this.querySelector('#popup-title');
    const placeholder = this.querySelector('#placeholder');
    const loading = this.querySelector('#loading');
    const loadingStatus = this.querySelector('#loading-status');
    const video = this.querySelector('#video');

    const stations = this._getStations();
    const station = stations.find(s => s.stationId === stationId);

    if (title && station) title.textContent = station.name;
    if (overlay) overlay.classList.add('open');
    if (placeholder) placeholder.style.display = 'none';
    if (video) video.style.display = 'none';
    if (loading) loading.style.display = 'flex';
    if (loadingStatus) loadingStatus.textContent = 'Avvio chiamata video...';

    const buttonEntity = `button.citofono_view_video_station_${stationId}`;
    try {
      if (loadingStatus) loadingStatus.textContent = 'Connessione al posto esterno...';
      await this._hass.callService('button', 'press', { entity_id: buttonEntity });
      if (loadingStatus) loadingStatus.textContent = 'In attesa dello stream video...';
    } catch (e) {
      console.error('Failed to start video call:', e);
      if (loadingStatus) loadingStatus.textContent = 'Errore di connessione';
    }

    this._pollForHls(stationId);
  }

  async _pollForHls(stationId) {
    const maxAttempts = 40;
    let attempts = 0;
    const loadingStatus = this.querySelector('#loading-status');

    const errorMessages = {
      'busy_gateway': 'Posto esterno occupato',
      'busy_call_in_progress': 'Chiamata già in corso',
      'not_registered': 'Sistema non connesso',
      'timeout': 'Timeout connessione',
      'service_unavailable': 'Servizio non disponibile',
      'call_failed': 'Chiamata fallita'
    };

    const poll = async () => {
      if (!this._popupOpen || this._activeStation !== stationId) return;

      const stations = this._getStations();
      const station = stations.find(s => s.stationId === stationId);

      if (station && station.lastError) {
        const errorMsg = errorMessages[station.lastError] || station.lastError;
        this._showError(errorMsg, station.lastError === 'busy_gateway');
        return;
      }

      if (loadingStatus) {
        const seconds = Math.floor(attempts / 2);
        if (station && station.hlsAvailable) {
          loadingStatus.textContent = 'Stream pronto, caricamento...';
        } else if (attempts < 4) {
          loadingStatus.textContent = 'Connessione al posto esterno...';
        } else if (attempts < 10) {
          loadingStatus.textContent = 'Negoziazione SRTP...';
        } else {
          loadingStatus.textContent = `Preparazione video... (${seconds}s)`;
        }
      }

      if (station && station.hlsAvailable && station.hlsUrl) {
        if (loadingStatus) loadingStatus.textContent = 'Avvio riproduzione...';
        this._startStream(station.hlsUrl);
        return;
      }

      attempts++;
      if (attempts < maxAttempts) {
        setTimeout(poll, 500);
      } else {
        this._showError('Timeout connessione', false);
      }
    };

    poll();
  }

  _showError(message, canRetry = false) {
    const placeholder = this.querySelector('#placeholder');
    const loading = this.querySelector('#loading');

    if (placeholder) {
      placeholder.innerHTML = `
        <ha-icon icon="mdi:video-off" style="color: var(--error-color, #f44336);"></ha-icon>
        <span style="margin-top: 8px; font-weight: 500;">${message}</span>
        ${canRetry ? '<span style="font-size: 0.85em; margin-top: 8px; color: #888;">Riprova tra qualche secondo</span>' : ''}
      `;
      placeholder.style.display = 'flex';
    }
    if (loading) loading.style.display = 'none';
  }

  _updateVideoStream() {
    const stations = this._getStations();
    const station = stations.find(s => s.stationId === this._activeStation);

    if (station && station.hlsAvailable && station.hlsUrl && !this._hlsPlayer) {
      this._startStream(station.hlsUrl);
    }
  }

  _startStream(hlsUrl) {
    const video = this.querySelector('#video');
    const loading = this.querySelector('#loading');
    const placeholder = this.querySelector('#placeholder');

    if (!video) return;

    const fullUrl = hlsUrl.startsWith('http') ? hlsUrl : `${window.location.origin}${hlsUrl}`;

    this._stopStream();

    try {
      if (typeof Hls !== 'undefined' && Hls.isSupported()) {
        this._hlsPlayer = new Hls({
          enableWorker: true,
          lowLatencyMode: false,
          backBufferLength: 30,
          maxBufferLength: 60,
          maxMaxBufferLength: 120,
          maxBufferSize: 60 * 1000 * 1000,
          maxBufferHole: 2,
          liveSyncDurationCount: 5,
          liveMaxLatencyDurationCount: 10,
          liveDurationInfinity: true,
          manifestLoadingTimeOut: 15000,
          manifestLoadingMaxRetry: 5,
          fragLoadingTimeOut: 30000,
          fragLoadingMaxRetry: 6,
        });

        this._hlsPlayer.loadSource(fullUrl);
        this._hlsPlayer.attachMedia(video);

        this._hlsPlayer.on(Hls.Events.MANIFEST_PARSED, () => {
          video.play().catch(e => console.warn('Autoplay blocked:', e));
        });

        this._hlsPlayer.on(Hls.Events.FRAG_LOADED, () => {
          if (loading && loading.style.display !== 'none') {
            loading.style.display = 'none';
            video.style.display = 'block';
          }
        });

        this._hlsPlayer.on(Hls.Events.ERROR, (event, data) => {
          if (data.fatal) {
            switch (data.type) {
              case Hls.ErrorTypes.NETWORK_ERROR:
                this._hlsPlayer.startLoad();
                break;
              case Hls.ErrorTypes.MEDIA_ERROR:
                this._hlsPlayer.recoverMediaError();
                break;
            }
          }
        });

      } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
        video.src = fullUrl;
        video.addEventListener('loadedmetadata', () => {
          if (loading) loading.style.display = 'none';
        });
        video.play().catch(e => console.warn('Autoplay blocked:', e));
      }

      if (placeholder) placeholder.style.display = 'none';
      video.style.display = 'block';

    } catch (e) {
      console.error('Error starting stream:', e);
    }
  }

  _stopStream() {
    if (this._hlsPlayer) {
      this._hlsPlayer.destroy();
      this._hlsPlayer = null;
    }

    const video = this.querySelector('#video');
    if (video) {
      video.pause();
      video.src = '';
      video.load();
      video.style.display = 'none';
    }
  }

  _onVideoPlaying() {
    const loading = this.querySelector('#loading');
    if (loading) loading.style.display = 'none';
  }

  _closePopup() {
    this._stopStream();
    this._popupOpen = false;
    this._activeStation = null;

    const overlay = this.querySelector('#popup-overlay');
    const bufferingOverlay = this.querySelector('#buffering-overlay');

    if (overlay) overlay.classList.remove('open');
    if (bufferingOverlay) bufferingOverlay.classList.remove('visible');
  }

  async _hangupAndClose() {
    try {
      await this._hass.callService('button', 'press', {
        entity_id: 'button.citofono_hangup_call'
      });
    } catch (e) {
      console.error('Failed to hangup:', e);
    }
    this._closePopup();
  }

  async _unlockDoor(stationId, btn) {
    btn.classList.add('waiting');
    btn.classList.remove('success', 'error');

    try {
      await this._hass.callService('button', 'press', {
        entity_id: `button.citofono_unlock_door_${stationId}`
      });

      setTimeout(() => {
        if (btn.classList.contains('waiting')) {
          btn.classList.remove('waiting');
          btn.classList.add('error');
          setTimeout(() => btn.classList.remove('error'), 5000);
        }
      }, 10000);

    } catch (e) {
      console.error('Failed to unlock door:', e);
      btn.classList.remove('waiting');
      btn.classList.add('error');
      setTimeout(() => btn.classList.remove('error'), 5000);
    }
  }

  getCardSize() {
    return 2;
  }
}

// Card Editor
class BticinoIntercomCardEditor extends HTMLElement {
  set hass(hass) { this._hass = hass; }

  setConfig(config) {
    this._config = config;
    this._render();
  }

  _render() {
    this.innerHTML = `
      <style>
        .editor-row {
          margin-bottom: 16px;
        }
        .editor-row label {
          display: block;
          margin-bottom: 4px;
          font-weight: 500;
        }
        .editor-row input {
          width: 100%;
          padding: 8px;
          border: 1px solid var(--divider-color);
          border-radius: 4px;
          background: var(--card-background-color);
          color: var(--primary-text-color);
        }
        .editor-row input[type="checkbox"] {
          width: auto;
        }
      </style>

      <div class="editor-row">
        <label>Titolo</label>
        <input type="text" id="title" value="${this._config.title || 'Citofono'}">
      </div>

      <div class="editor-row">
        <label>
          <input type="checkbox" id="show_title" ${this._config.show_title !== false ? 'checked' : ''}>
          Mostra titolo
        </label>
      </div>
    `;

    this.querySelector('#title').addEventListener('input', (e) => this._valueChanged('title', e.target.value));
    this.querySelector('#show_title').addEventListener('change', (e) => this._valueChanged('show_title', e.target.checked));
  }

  _valueChanged(key, value) {
    const newConfig = { ...this._config, [key]: value };
    this.dispatchEvent(new CustomEvent('config-changed', {
      detail: { config: newConfig },
      bubbles: true,
      composed: true
    }));
  }
}

// Register components
customElements.define('bticino-intercom-card', BticinoIntercomCard);
customElements.define('bticino-intercom-card-editor', BticinoIntercomCardEditor);

// Register with Lovelace
window.customCards = window.customCards || [];
window.customCards.push({
  type: 'bticino-intercom-card',
  name: 'BTicino Intercom',
  description: 'Card compatta per citofono BTicino (solo video, audio non supportato)',
  preview: true
});

console.info(
  '%c BTICINO-INTERCOM-CARD %c v3.0.0 ',
  'color: white; background: #03a9f4; font-weight: bold;',
  'color: #03a9f4; background: white; font-weight: bold;'
);
