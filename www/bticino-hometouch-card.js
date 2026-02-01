/**
 * BTicino Hometouch Card for Home Assistant
 *
 * A custom Lovelace card that displays outdoor station buttons
 * and opens a popup with video stream when pressed.
 */

class BticinoHometouchCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._hass = null;
    this._config = null;
    this._popupOpen = false;
    this._callActive = false;
  }

  set hass(hass) {
    this._hass = hass;
    this._updateCard();
  }

  setConfig(config) {
    this._config = {
      title: config.title || 'Videocitofono',
      stations: config.stations || [
        { id: 1, name: 'Albani', button: 'button.bticino_hometouch_view_video_1', lock: 'button.bticino_hometouch_unlock_door_1', camera: 'camera.bticino_hometouch_outdoor_station_1' },
        { id: 2, name: 'Madruzzo', button: 'button.bticino_hometouch_view_video_2', lock: 'button.bticino_hometouch_unlock_door_2', camera: 'camera.bticino_hometouch_outdoor_station_2' },
        { id: 3, name: 'Scala B', button: 'button.bticino_hometouch_view_video_3', lock: 'button.bticino_hometouch_unlock_door_3', camera: 'camera.bticino_hometouch_outdoor_station_3' },
      ],
      go2rtc_url: config.go2rtc_url || 'http://a889bffc-go2rtc:1984',
      hangup_button: config.hangup_button || 'button.bticino_hometouch_hangup_call',
      audio_button: config.audio_button || 'button.bticino_hometouch_enable_audio',
      ...config
    };
    this._render();
  }

  _render() {
    const stationButtons = this._config.stations.map(station => `
      <div class="station-card" data-station-id="${station.id}">
        <div class="station-icon">
          <ha-icon icon="mdi:doorbell-video"></ha-icon>
        </div>
        <div class="station-name">${station.name}</div>
        <button class="view-btn" data-action="view" data-station='${JSON.stringify(station)}'>
          <ha-icon icon="mdi:video"></ha-icon>
          <span>Visualizza</span>
        </button>
      </div>
    `).join('');

    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
        }
        .card {
          background: var(--ha-card-background, var(--card-background-color, white));
          border-radius: var(--ha-card-border-radius, 12px);
          box-shadow: var(--ha-card-box-shadow, 0 2px 4px rgba(0,0,0,0.1));
          padding: 16px;
        }
        .header {
          display: flex;
          align-items: center;
          gap: 12px;
          margin-bottom: 16px;
        }
        .header ha-icon {
          color: var(--primary-color);
          --mdc-icon-size: 32px;
        }
        .header h2 {
          margin: 0;
          font-size: 20px;
          font-weight: 500;
        }
        .stations-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
          gap: 16px;
        }
        .station-card {
          background: var(--secondary-background-color);
          border-radius: 12px;
          padding: 16px;
          text-align: center;
          transition: transform 0.2s ease, box-shadow 0.2s ease;
        }
        .station-card:hover {
          transform: translateY(-2px);
          box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        }
        .station-icon {
          width: 60px;
          height: 60px;
          margin: 0 auto 12px;
          background: var(--primary-color);
          border-radius: 50%;
          display: flex;
          align-items: center;
          justify-content: center;
        }
        .station-icon ha-icon {
          color: white;
          --mdc-icon-size: 32px;
        }
        .station-name {
          font-weight: 500;
          margin-bottom: 12px;
          font-size: 16px;
        }
        .view-btn {
          width: 100%;
          padding: 10px 16px;
          border: none;
          border-radius: 8px;
          background: var(--primary-color);
          color: white;
          cursor: pointer;
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 8px;
          font-family: inherit;
          font-size: 14px;
          transition: background 0.2s ease;
        }
        .view-btn:hover {
          background: var(--primary-color);
          filter: brightness(1.1);
        }
        .view-btn:active {
          transform: scale(0.98);
        }
        .view-btn ha-icon {
          --mdc-icon-size: 20px;
        }

        /* Status indicator */
        .status-bar {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 8px 12px;
          background: var(--secondary-background-color);
          border-radius: 8px;
          margin-top: 16px;
          font-size: 14px;
        }
        .status-dot {
          width: 10px;
          height: 10px;
          border-radius: 50%;
          background: var(--success-color, #4CAF50);
        }
        .status-dot.disconnected {
          background: var(--error-color, #F44336);
        }

        /* Popup Overlay */
        .popup-overlay {
          position: fixed;
          top: 0;
          left: 0;
          right: 0;
          bottom: 0;
          background: rgba(0,0,0,0.9);
          z-index: 9999;
          display: none;
          flex-direction: column;
          padding: 16px;
        }
        .popup-overlay.open {
          display: flex;
        }
        .popup-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 8px 0;
          color: white;
        }
        .popup-header h3 {
          margin: 0;
          font-size: 18px;
        }
        .close-btn {
          background: none;
          border: none;
          color: white;
          cursor: pointer;
          padding: 8px;
        }
        .close-btn ha-icon {
          --mdc-icon-size: 28px;
        }
        .video-container {
          flex: 1;
          display: flex;
          align-items: center;
          justify-content: center;
          background: #000;
          border-radius: 8px;
          overflow: hidden;
          position: relative;
        }
        .video-container iframe,
        .video-container video {
          width: 100%;
          height: 100%;
          max-height: 70vh;
          object-fit: contain;
        }
        .video-loading {
          position: absolute;
          top: 50%;
          left: 50%;
          transform: translate(-50%, -50%);
          color: white;
          text-align: center;
        }
        .video-loading ha-icon {
          --mdc-icon-size: 48px;
          animation: spin 1s linear infinite;
        }
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
        .popup-controls {
          display: flex;
          justify-content: center;
          gap: 16px;
          padding: 16px 0;
        }
        .popup-btn {
          display: flex;
          flex-direction: column;
          align-items: center;
          padding: 12px 24px;
          border: none;
          border-radius: 12px;
          cursor: pointer;
          transition: all 0.2s ease;
          font-family: inherit;
          min-width: 80px;
        }
        .popup-btn:hover {
          transform: scale(1.05);
        }
        .popup-btn:active {
          transform: scale(0.95);
        }
        .popup-btn ha-icon {
          --mdc-icon-size: 28px;
          margin-bottom: 4px;
        }
        .popup-btn span {
          font-size: 12px;
        }
        .popup-btn.unlock {
          background: var(--primary-color, #03A9F4);
          color: white;
        }
        .popup-btn.audio {
          background: var(--secondary-background-color);
          color: var(--primary-text-color);
        }
        .popup-btn.audio.active {
          background: var(--success-color, #4CAF50);
          color: white;
        }
        .popup-btn.hangup {
          background: var(--error-color, #F44336);
          color: white;
        }
      </style>

      <div class="card">
        <div class="header">
          <ha-icon icon="mdi:doorbell-video"></ha-icon>
          <h2>${this._config.title}</h2>
        </div>

        <div class="stations-grid">
          ${stationButtons}
        </div>

        <div class="status-bar">
          <div class="status-dot" id="status-dot"></div>
          <span id="status-text">Connesso</span>
        </div>
      </div>

      <div class="popup-overlay" id="popup">
        <div class="popup-header">
          <h3 id="popup-title">Video</h3>
          <button class="close-btn" id="close-popup">
            <ha-icon icon="mdi:close"></ha-icon>
          </button>
        </div>

        <div class="video-container" id="video-container">
          <div class="video-loading" id="video-loading">
            <ha-icon icon="mdi:loading"></ha-icon>
            <div>Connessione...</div>
          </div>
        </div>

        <div class="popup-controls">
          <button class="popup-btn unlock" id="popup-unlock">
            <ha-icon icon="mdi:door-open"></ha-icon>
            <span>Apri</span>
          </button>
          <button class="popup-btn audio" id="popup-audio">
            <ha-icon icon="mdi:microphone"></ha-icon>
            <span>Audio</span>
          </button>
          <button class="popup-btn hangup" id="popup-hangup">
            <ha-icon icon="mdi:phone-hangup"></ha-icon>
            <span>Chiudi</span>
          </button>
        </div>
      </div>
    `;

    this._setupEventListeners();
  }

  _setupEventListeners() {
    // Station view buttons
    this.shadowRoot.querySelectorAll('.view-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const station = JSON.parse(e.currentTarget.dataset.station);
        this._openVideoPopup(station);
      });
    });

    // Close popup
    this.shadowRoot.getElementById('close-popup').addEventListener('click', () => {
      this._closeVideoPopup();
    });

    // Popup controls
    this.shadowRoot.getElementById('popup-unlock').addEventListener('click', () => {
      if (this._currentStation) {
        this._pressButton(this._currentStation.lock);
        // Visual feedback
        const btn = this.shadowRoot.getElementById('popup-unlock');
        btn.style.background = 'var(--success-color)';
        setTimeout(() => btn.style.background = '', 1000);
      }
    });

    this.shadowRoot.getElementById('popup-audio').addEventListener('click', () => {
      this._pressButton(this._config.audio_button);
      const btn = this.shadowRoot.getElementById('popup-audio');
      btn.classList.toggle('active');
    });

    this.shadowRoot.getElementById('popup-hangup').addEventListener('click', () => {
      this._closeVideoPopup();
    });

    // Close on escape key
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this._popupOpen) {
        this._closeVideoPopup();
      }
    });
  }

  _updateCard() {
    if (!this._hass || !this.shadowRoot) return;

    // Update status based on connection state
    const statusDot = this.shadowRoot.getElementById('status-dot');
    const statusText = this.shadowRoot.getElementById('status-text');

    // Check connection sensor
    const connectionState = this._hass.states['binary_sensor.bticino_hometouch_connection'];
    if (connectionState && connectionState.state === 'on') {
      statusDot?.classList.remove('disconnected');
      if (statusText) statusText.textContent = 'Pronto';
    } else {
      statusDot?.classList.add('disconnected');
      if (statusText) statusText.textContent = 'Disconnesso';
    }
  }

  async _openVideoPopup(station) {
    this._currentStation = station;
    this._popupOpen = true;

    const popup = this.shadowRoot.getElementById('popup');
    const popupTitle = this.shadowRoot.getElementById('popup-title');
    const videoContainer = this.shadowRoot.getElementById('video-container');
    const videoLoading = this.shadowRoot.getElementById('video-loading');

    popup.classList.add('open');
    popupTitle.textContent = `Video - ${station.name}`;
    videoLoading.style.display = 'block';

    // Press the video button to start the call
    await this._pressButton(station.button);

    // Wait a bit for the call to establish, then load the video
    setTimeout(() => {
      this._loadVideoStream(station);
    }, 3000);
  }

  _loadVideoStream(station) {
    const videoContainer = this.shadowRoot.getElementById('video-container');
    const videoLoading = this.shadowRoot.getElementById('video-loading');

    // Use go2rtc WebRTC player
    const streamName = `bticino_camera_${station.id}`;
    const go2rtcUrl = this._config.go2rtc_url;

    // Create iframe for go2rtc WebRTC player
    const iframe = document.createElement('iframe');
    iframe.src = `${go2rtcUrl}/stream.html?src=${streamName}`;
    iframe.style.border = 'none';
    iframe.allow = 'autoplay; fullscreen';

    iframe.onload = () => {
      videoLoading.style.display = 'none';
    };

    // Clear and add iframe
    const existingIframe = videoContainer.querySelector('iframe');
    if (existingIframe) {
      existingIframe.remove();
    }
    videoContainer.appendChild(iframe);

    // Fallback: hide loading after timeout
    setTimeout(() => {
      videoLoading.style.display = 'none';
    }, 5000);
  }

  async _closeVideoPopup() {
    // Hangup the call
    await this._pressButton(this._config.hangup_button);

    // Close popup
    this._popupOpen = false;
    this._currentStation = null;

    const popup = this.shadowRoot.getElementById('popup');
    const videoContainer = this.shadowRoot.getElementById('video-container');
    const audioBtn = this.shadowRoot.getElementById('popup-audio');

    popup.classList.remove('open');
    audioBtn.classList.remove('active');

    // Remove iframe
    const iframe = videoContainer.querySelector('iframe');
    if (iframe) {
      iframe.remove();
    }
  }

  async _pressButton(entityId) {
    if (!this._hass) return;

    try {
      await this._hass.callService('button', 'press', {
        entity_id: entityId
      });
    } catch (error) {
      console.error(`Failed to press button ${entityId}:`, error);
    }
  }

  getCardSize() {
    return 3;
  }

  static getConfigElement() {
    return document.createElement('bticino-hometouch-card-editor');
  }

  static getStubConfig() {
    return {
      title: 'Videocitofono',
      stations: [
        { id: 1, name: 'Albani', button: 'button.bticino_hometouch_view_video_1', lock: 'button.bticino_hometouch_unlock_door_1', camera: 'camera.bticino_hometouch_outdoor_station_1' },
        { id: 2, name: 'Madruzzo', button: 'button.bticino_hometouch_view_video_2', lock: 'button.bticino_hometouch_unlock_door_2', camera: 'camera.bticino_hometouch_outdoor_station_2' },
        { id: 3, name: 'Scala B', button: 'button.bticino_hometouch_view_video_3', lock: 'button.bticino_hometouch_unlock_door_3', camera: 'camera.bticino_hometouch_outdoor_station_3' },
      ],
      go2rtc_url: 'http://a889bffc-go2rtc:1984'
    };
  }
}

// Card Editor
class BticinoHometouchCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = config;
    this._render();
  }

  _render() {
    this.innerHTML = `
      <div style="padding: 16px;">
        <ha-textfield
          label="Titolo"
          value="${this._config.title || 'Videocitofono'}"
          @change="${this._valueChanged}"
          data-key="title"
        ></ha-textfield>
        <ha-textfield
          label="go2rtc URL"
          value="${this._config.go2rtc_url || 'http://a889bffc-go2rtc:1984'}"
          @change="${this._valueChanged}"
          data-key="go2rtc_url"
        ></ha-textfield>
        <p style="font-size: 12px; color: var(--secondary-text-color);">
          Le stazioni sono configurate automaticamente in base ai pulsanti disponibili.
        </p>
      </div>
    `;
  }

  _valueChanged(ev) {
    const key = ev.target.dataset.key;
    const value = ev.target.value;

    const newConfig = { ...this._config, [key]: value };

    const event = new CustomEvent('config-changed', {
      detail: { config: newConfig },
      bubbles: true,
      composed: true
    });
    this.dispatchEvent(event);
  }
}

customElements.define('bticino-hometouch-card', BticinoHometouchCard);
customElements.define('bticino-hometouch-card-editor', BticinoHometouchCardEditor);

window.customCards = window.customCards || [];
window.customCards.push({
  type: 'bticino-hometouch-card',
  name: 'BTicino Hometouch Card',
  description: 'Card per videocitofono BTicino con popup video',
  preview: true
});

console.info('%c BTICINO-HOMETOUCH-CARD %c v2.0.0 ',
  'color: white; background: #03A9F4; font-weight: bold;',
  'color: #03A9F4; background: white; font-weight: bold;'
);
