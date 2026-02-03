/**
 * BTicino Intercom Card v4.0.2
 *
 * WebRTC-only card for BTicino Door Entry intercom via go2rtc.
 * Low latency (~1-2s) video streaming.
 */

class BticinoIntercomCard extends HTMLElement {

  static getConfigElement() {
    return document.createElement("bticino-intercom-card-editor");
  }

  static getStubConfig() {
    return { title: "Citofono", show_title: true };
  }

  set hass(hass) {
    this._hass = hass;
    if (this._rendered) this._updateCard();
  }

  setConfig(config) {
    this._config = { title: "Citofono", show_title: true, ...config };
    this._rendered = false;
    this._activeStation = null;
    this._popupOpen = false;
    this._peerConnection = null;
  }

  static get ENTITY_PREFIX() { return 'bticino_hometouch_'; }

  _getViewVideoButton(stationId) {
    return `button.${BticinoIntercomCard.ENTITY_PREFIX}view_video_station_${stationId}`;
  }

  _getUnlockDoorButton(stationId) {
    return `button.${BticinoIntercomCard.ENTITY_PREFIX}unlock_door_${stationId}`;
  }

  _getHangupButton() {
    return `button.${BticinoIntercomCard.ENTITY_PREFIX}hangup_call`;
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
    if (this._hass?.connection) {
      try {
        this._unlockSubscription = await this._hass.connection.subscribeEvents(
          (event) => this._onDoorUnlocked(event),
          'bticino_hometouch_door_unlocked'
        );
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
    if (typeof this._unlockSubscription === 'function') {
      try { this._unlockSubscription(); } catch (e) {}
    }
    if (typeof this._incomingCallSubscription === 'function') {
      try { this._incomingCallSubscription(); } catch (e) {}
    }
    this._unlockSubscription = null;
    this._incomingCallSubscription = null;
  }

  _onDoorUnlocked(event) {
    const btn = this.querySelector(`.action-btn.unlock[data-station-id="${event.data.lock_id}"]`);
    if (btn) {
      btn.classList.remove('waiting', 'error');
      btn.classList.add('success');
      setTimeout(() => btn.classList.remove('success'), 5000);
    }
  }

  _onIncomingCall(event) {
    const stationId = event.data.station_id;
    const stationName = event.data.station_name || `Posto Esterno ${stationId}`;
    this._showBanner(stationId, stationName);
  }

  _showBanner(stationId, stationName) {
    const banner = this.querySelector('#incoming-call-banner');
    const bannerText = this.querySelector('#incoming-call-text');
    if (banner && bannerText) {
      bannerText.textContent = `📞 Chiamata da ${stationName}`;
      banner.classList.add('visible');
      banner.dataset.stationId = stationId;
      clearTimeout(this._bannerTimeout);
      this._bannerTimeout = setTimeout(() => this._hideBanner(), 30000);
    }
  }

  _hideBanner() {
    const banner = this.querySelector('#incoming-call-banner');
    if (banner) banner.classList.remove('visible');
    clearTimeout(this._bannerTimeout);
  }

  _getStations() {
    if (!this._hass) return [];
    const prefix = `camera.${BticinoIntercomCard.ENTITY_PREFIX}camera_`;
    return Object.keys(this._hass.states)
      .filter(e => e.startsWith(prefix))
      .sort()
      .map(entityId => {
        const state = this._hass.states[entityId];
        const stationId = parseInt(entityId.replace(prefix, '')) || 1;
        return {
          entityId,
          stationId,
          name: state.attributes.friendly_name || `Station ${stationId}`,
          isRinging: state.attributes.is_ringing,
          isConnected: state.attributes.is_connected,
          webrtcUrl: state.attributes.webrtc_url,
          streamReady: state.attributes.is_connected && state.attributes.webrtc_available,
          lastError: state.attributes.last_error
        };
      });
  }

  _render() {
    const stations = this._getStations();
    this.innerHTML = `
      <ha-card>
        <style>
          .card-container { padding: 12px; }
          .card-title { font-size: 1.1em; font-weight: 500; margin-bottom: 12px; }
          .incoming-call-banner {
            display: none; background: linear-gradient(135deg, #ff9800, #f57c00);
            color: white; padding: 12px 16px; margin-bottom: 12px; border-radius: 8px;
            font-weight: 500; animation: pulse-banner 1.5s infinite;
            justify-content: space-between; align-items: center;
          }
          .incoming-call-banner.visible { display: flex; }
          .incoming-call-banner .dismiss-btn {
            background: rgba(255,255,255,0.2); border: none; color: white;
            padding: 4px 8px; border-radius: 4px; cursor: pointer;
          }
          @keyframes pulse-banner { 0%, 100% { opacity: 1; } 50% { opacity: 0.85; } }
          .stations-row { display: flex; gap: 8px; flex-wrap: wrap; }
          .station-block {
            flex: 1; min-width: 120px; background: var(--card-background-color);
            border: 1px solid var(--divider-color); border-radius: 8px; padding: 10px;
          }
          .station-block:hover { border-color: var(--primary-color); }
          .station-block.ringing { border-color: var(--warning-color, #ff9800); animation: ring-pulse 1s infinite; }
          .station-block.connected { border-color: var(--success-color, #4caf50); }
          @keyframes ring-pulse { 0%, 100% { box-shadow: 0 0 0 0 rgba(255,152,0,0.4); } 50% { box-shadow: 0 0 0 8px rgba(255,152,0,0); } }
          .station-name { font-size: 0.9em; font-weight: 500; margin-bottom: 8px; overflow: hidden; text-overflow: ellipsis; }
          .station-actions { display: flex; gap: 8px; justify-content: center; }
          .action-btn {
            display: flex; align-items: center; justify-content: center;
            width: 40px; height: 40px; border-radius: 50%; border: none;
            cursor: pointer; background: var(--primary-background-color);
          }
          .action-btn:hover { transform: scale(1.1); }
          .action-btn.view { color: var(--primary-color); }
          .action-btn.view:hover { background: var(--primary-color); color: white; }
          .action-btn.unlock { color: var(--primary-color); }
          .action-btn.unlock:hover { background: var(--primary-color); color: white; }
          .action-btn.unlock.success { background: var(--success-color, #4caf50) !important; color: white !important; }
          .action-btn.unlock.error { background: var(--error-color, #f44336) !important; color: white !important; }
          .action-btn.unlock.waiting { opacity: 0.6; cursor: wait; }
          .action-btn ha-icon { --mdc-icon-size: 20px; }

          .popup-overlay {
            display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0,0,0,0.8); z-index: 9999; align-items: center; justify-content: center;
          }
          .popup-overlay.open { display: flex; }
          .popup-content {
            background: var(--card-background-color, #fff); border-radius: 12px;
            overflow: hidden; max-width: 90vw; max-height: 90vh; width: 640px;
          }
          .popup-header {
            display: flex; justify-content: space-between; align-items: center;
            padding: 12px 16px; border-bottom: 1px solid var(--divider-color);
          }
          .popup-title { font-size: 1.1em; font-weight: 500; }
          .popup-close { background: none; border: none; cursor: pointer; padding: 4px; color: var(--secondary-text-color); }
          .video-container { position: relative; width: 100%; padding-bottom: 56.25%; background: #000; }
          .video-container video {
            position: absolute; top: 0; left: 0; width: 100%; height: 100%; object-fit: contain;
          }
          .video-container .status-overlay {
            position: absolute; top: 0; left: 0; width: 100%; height: 100%;
            display: flex; flex-direction: column; align-items: center; justify-content: center;
            color: white; background: rgba(0,0,0,0.85); z-index: 10;
          }
          .video-container .status-overlay.hidden { display: none; }
          .video-container .status-overlay .status-text { font-size: 1.1em; font-weight: 500; margin-top: 16px; }
          .video-container .status-overlay .status-sub { font-size: 0.85em; color: rgba(255,255,255,0.7); margin-top: 8px; }
          .popup-controls { display: flex; gap: 12px; padding: 16px; justify-content: center; }
          .popup-btn {
            display: flex; align-items: center; gap: 8px; padding: 10px 20px;
            border-radius: 8px; border: none; cursor: pointer; font-size: 0.95em; font-weight: 500;
          }
          .popup-btn.hangup { background: var(--error-color, #f44336); color: white; }
          .popup-btn.hangup:hover { filter: brightness(1.1); }
          .popup-btn ha-icon { --mdc-icon-size: 18px; }
          .latency-notice { font-size: 0.75em; color: var(--secondary-text-color); text-align: center; padding: 8px; border-top: 1px solid var(--divider-color); }
        </style>

        <div class="card-container">
          ${this._config.show_title ? `<div class="card-title">${this._config.title}</div>` : ''}
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
              <button class="popup-close" id="popup-close"><ha-icon icon="mdi:close"></ha-icon></button>
            </div>
            <div class="video-container" id="video-container">
              <video id="video" playsinline muted autoplay></video>
              <div class="status-overlay" id="status-overlay">
                <ha-circular-progress indeterminate></ha-circular-progress>
                <div class="status-text" id="status-text">Connessione...</div>
                <div class="status-sub" id="status-sub"></div>
              </div>
            </div>
            <div class="popup-controls">
              <button class="popup-btn hangup" id="btn-hangup">
                <ha-icon icon="mdi:phone-hangup"></ha-icon> Chiudi
              </button>
            </div>
            <div class="latency-notice">WebRTC • Latenza ~1-2s • Solo video</div>
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
      <div class="${classes.join(' ')}" data-station-id="${station.stationId}">
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
    this.querySelectorAll('.action-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const action = btn.dataset.action;
        const stationId = parseInt(btn.dataset.stationId);
        if (action === 'view') this._openVideoPopup(stationId);
        else if (action === 'unlock') this._unlockDoor(stationId, btn);
      });
    });

    this.querySelector('#popup-close')?.addEventListener('click', () => this._closePopup());
    this.querySelector('#btn-hangup')?.addEventListener('click', () => this._hangupAndClose());
    this.querySelector('#popup-overlay')?.addEventListener('click', (e) => {
      if (e.target.id === 'popup-overlay') this._closePopup();
    });
    this.querySelector('#dismiss-banner')?.addEventListener('click', () => this._hideBanner());
  }

  _updateCard() {
    const stations = this._getStations();
    const row = this.querySelector('#stations-row');
    if (row) {
      row.innerHTML = stations.map(s => this._renderStation(s)).join('');
      this.querySelectorAll('.action-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          const action = btn.dataset.action;
          const stationId = parseInt(btn.dataset.stationId);
          if (action === 'view') this._openVideoPopup(stationId);
          else if (action === 'unlock') this._unlockDoor(stationId, btn);
        });
      });
    }
    for (const station of stations) {
      if (station.isRinging) {
        this._showBanner(station.stationId, station.name);
        break;
      }
    }
  }

  async _openVideoPopup(stationId) {
    this._activeStation = stationId;
    this._popupOpen = true;
    this._hideBanner();

    const overlay = this.querySelector('#popup-overlay');
    const title = this.querySelector('#popup-title');
    const statusOverlay = this.querySelector('#status-overlay');
    const statusText = this.querySelector('#status-text');
    const statusSub = this.querySelector('#status-sub');
    const video = this.querySelector('#video');

    const stations = this._getStations();
    const station = stations.find(s => s.stationId === stationId);

    if (title && station) title.textContent = station.name;
    overlay?.classList.add('open');
    statusOverlay?.classList.remove('hidden');
    if (statusText) statusText.textContent = 'Avvio chiamata...';
    if (statusSub) statusSub.textContent = '';
    if (video) video.style.display = 'none';

    // Start video call
    const buttonEntity = this._getViewVideoButton(stationId);
    try {
      if (statusText) statusText.textContent = 'Connessione al citofono...';
      await this._hass.callService('button', 'press', { entity_id: buttonEntity });
      if (statusSub) statusSub.textContent = 'Attesa risposta...';
    } catch (e) {
      console.error('Failed to start video call:', e);
      if (statusText) statusText.textContent = 'Errore connessione';
      return;
    }

    // Poll for WebRTC stream
    this._pollForWebRTC(stationId);
  }

  async _pollForWebRTC(stationId) {
    const maxAttempts = 30;
    let attempts = 0;
    const statusText = this.querySelector('#status-text');
    const statusSub = this.querySelector('#status-sub');

    const poll = async () => {
      if (!this._popupOpen || this._activeStation !== stationId) return;

      const stations = this._getStations();
      const station = stations.find(s => s.stationId === stationId);

      if (station?.lastError) {
        const errors = {
          'busy_gateway': 'Citofono occupato',
          'busy_call_in_progress': 'Chiamata in corso',
          'not_registered': 'Non connesso',
          'timeout': 'Timeout',
        };
        if (statusText) statusText.textContent = errors[station.lastError] || station.lastError;
        if (statusSub) statusSub.textContent = 'Riprova più tardi';
        return;
      }

      if (station?.streamReady) {
        if (statusText) statusText.textContent = 'Connessione WebRTC...';
        await this._startWebRTC(stationId);
        return;
      }

      attempts++;
      if (statusSub) statusSub.textContent = `Preparazione stream... (${attempts}s)`;

      if (attempts < maxAttempts) {
        setTimeout(poll, 1000);
      } else {
        if (statusText) statusText.textContent = 'Timeout';
        if (statusSub) statusSub.textContent = 'Stream non disponibile';
      }
    };

    setTimeout(poll, 1000);
  }

  async _startWebRTC(stationId) {
    const video = this.querySelector('#video');
    const statusOverlay = this.querySelector('#status-overlay');
    const statusText = this.querySelector('#status-text');
    const statusSub = this.querySelector('#status-sub');

    if (!video) return;

    // Use HA proxy endpoint for WebRTC (works with both HTTP and HTTPS)
    const go2rtcApi = `/api/bticino_hometouch/webrtc/bticino_live_${stationId}`;

    try {
      // Create peer connection
      this._peerConnection = new RTCPeerConnection({
        iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
      });

      // Handle incoming tracks
      this._peerConnection.ontrack = (event) => {
        console.log('Received track:', event.track.kind);
        if (event.streams[0]) {
          video.srcObject = event.streams[0];
          video.style.display = 'block';
          statusOverlay?.classList.add('hidden');
        }
      };

      // Handle connection state
      this._peerConnection.onconnectionstatechange = () => {
        console.log('WebRTC state:', this._peerConnection.connectionState);
        if (this._peerConnection.connectionState === 'failed') {
          if (statusText) statusText.textContent = 'Connessione fallita';
          statusOverlay?.classList.remove('hidden');
        } else if (this._peerConnection.connectionState === 'disconnected') {
          if (statusText) statusText.textContent = 'Disconnesso';
          if (statusSub) statusSub.textContent = 'Stream terminato';
          statusOverlay?.classList.remove('hidden');
        }
      };

      // Add transceiver for receiving video
      this._peerConnection.addTransceiver('video', { direction: 'recvonly' });

      // Create offer
      const offer = await this._peerConnection.createOffer();
      await this._peerConnection.setLocalDescription(offer);

      if (statusSub) statusSub.textContent = 'Negoziazione SDP...';

      // Send offer to go2rtc and get answer
      const response = await fetch(go2rtcApi, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: 'offer', sdp: offer.sdp })
      });

      if (!response.ok) {
        throw new Error(`go2rtc error: ${response.status}`);
      }

      const answer = await response.json();
      await this._peerConnection.setRemoteDescription(new RTCSessionDescription(answer));

      if (statusSub) statusSub.textContent = 'In attesa del video...';

    } catch (e) {
      console.error('WebRTC error:', e);
      if (statusText) statusText.textContent = 'Errore WebRTC';
      if (statusSub) statusSub.textContent = e.message;
      statusOverlay?.classList.remove('hidden');
    }
  }

  _stopWebRTC() {
    if (this._peerConnection) {
      this._peerConnection.close();
      this._peerConnection = null;
    }
    const video = this.querySelector('#video');
    if (video) {
      video.srcObject = null;
      video.style.display = 'none';
    }
  }

  _closePopup() {
    this._stopWebRTC();
    this._popupOpen = false;
    this._activeStation = null;
    this.querySelector('#popup-overlay')?.classList.remove('open');
  }

  async _hangupAndClose() {
    const hangupButton = this._getHangupButton();
    if (hangupButton) {
      try {
        await this._hass.callService('button', 'press', { entity_id: hangupButton });
      } catch (e) {
        console.error('Failed to hangup:', e);
      }
    }
    this._closePopup();
  }

  async _unlockDoor(stationId, btn) {
    btn.classList.add('waiting');
    btn.classList.remove('success', 'error');

    const unlockButton = this._getUnlockDoorButton(stationId);
    try {
      await this._hass.callService('button', 'press', { entity_id: unlockButton });
      setTimeout(() => {
        if (btn.classList.contains('waiting')) {
          btn.classList.remove('waiting');
          btn.classList.add('error');
          setTimeout(() => btn.classList.remove('error'), 5000);
        }
      }, 10000);
    } catch (e) {
      console.error('Failed to unlock:', e);
      btn.classList.remove('waiting');
      btn.classList.add('error');
      setTimeout(() => btn.classList.remove('error'), 5000);
    }
  }

  getCardSize() { return 2; }
}

// Editor
class BticinoIntercomCardEditor extends HTMLElement {
  set hass(hass) { this._hass = hass; }
  setConfig(config) { this._config = config; this._render(); }

  _render() {
    this.innerHTML = `
      <style>
        .editor-row { margin-bottom: 16px; }
        .editor-row label { display: block; margin-bottom: 4px; font-weight: 500; }
        .editor-row input { width: 100%; padding: 8px; border: 1px solid var(--divider-color); border-radius: 4px; }
        .editor-row input[type="checkbox"] { width: auto; }
      </style>
      <div class="editor-row">
        <label>Titolo</label>
        <input type="text" id="title" value="${this._config.title || 'Citofono'}">
      </div>
      <div class="editor-row">
        <label><input type="checkbox" id="show_title" ${this._config.show_title !== false ? 'checked' : ''}> Mostra titolo</label>
      </div>
    `;
    this.querySelector('#title').addEventListener('input', (e) => this._valueChanged('title', e.target.value));
    this.querySelector('#show_title').addEventListener('change', (e) => this._valueChanged('show_title', e.target.checked));
  }

  _valueChanged(key, value) {
    this.dispatchEvent(new CustomEvent('config-changed', {
      detail: { config: { ...this._config, [key]: value } },
      bubbles: true, composed: true
    }));
  }
}

customElements.define('bticino-intercom-card', BticinoIntercomCard);
customElements.define('bticino-intercom-card-editor', BticinoIntercomCardEditor);

window.customCards = window.customCards || [];
window.customCards.push({
  type: 'bticino-intercom-card',
  name: 'BTicino Intercom',
  description: 'WebRTC video intercom card for BTicino',
  preview: true
});

console.info('%c BTICINO-INTERCOM-CARD %c v4.0.2 WebRTC ', 'color: white; background: #03a9f4; font-weight: bold;', 'color: #03a9f4; background: white; font-weight: bold;');
