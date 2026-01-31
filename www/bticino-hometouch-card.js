/**
 * BTicino Hometouch Card for Home Assistant
 *
 * A custom Lovelace card that displays the intercom video stream
 * with bidirectional audio and action buttons.
 */

class BticinoHometouchCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._hass = null;
    this._config = null;
    this._webrtcConnection = null;
    this._localStream = null;
    this._isConnected = false;
  }

  set hass(hass) {
    this._hass = hass;
    this._updateCard();
  }

  setConfig(config) {
    if (!config.camera_entity) {
      throw new Error('Please define a camera_entity');
    }
    this._config = config;
    this._render();
  }

  _render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
        }
        .card {
          background: var(--ha-card-background, var(--card-background-color, white));
          border-radius: var(--ha-card-border-radius, 12px);
          box-shadow: var(--ha-card-box-shadow, 0 2px 4px rgba(0,0,0,0.1));
          overflow: hidden;
          position: relative;
        }
        .video-container {
          position: relative;
          width: 100%;
          padding-top: 56.25%; /* 16:9 aspect ratio */
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
          top: 50%;
          left: 50%;
          transform: translate(-50%, -50%);
          color: #fff;
          text-align: center;
        }
        .video-container .placeholder mwc-icon {
          font-size: 64px;
          margin-bottom: 16px;
        }
        .controls {
          display: flex;
          justify-content: space-around;
          padding: 16px;
          background: var(--primary-background-color);
        }
        .control-btn {
          display: flex;
          flex-direction: column;
          align-items: center;
          padding: 12px 24px;
          border: none;
          border-radius: 8px;
          cursor: pointer;
          transition: all 0.2s ease;
          font-family: var(--paper-font-body1_-_font-family);
        }
        .control-btn:hover {
          transform: scale(1.05);
        }
        .control-btn:active {
          transform: scale(0.95);
        }
        .control-btn.answer {
          background: var(--success-color, #4CAF50);
          color: white;
        }
        .control-btn.unlock {
          background: var(--primary-color, #03A9F4);
          color: white;
        }
        .control-btn.hangup {
          background: var(--error-color, #F44336);
          color: white;
        }
        .control-btn.mute {
          background: var(--secondary-background-color);
          color: var(--primary-text-color);
        }
        .control-btn.muted {
          background: var(--warning-color, #FF9800);
          color: white;
        }
        .control-btn mwc-icon {
          font-size: 32px;
          margin-bottom: 4px;
        }
        .control-btn span {
          font-size: 12px;
        }
        .status-bar {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 8px 16px;
          background: rgba(0,0,0,0.8);
          color: white;
          font-size: 14px;
        }
        .status-indicator {
          display: flex;
          align-items: center;
          gap: 8px;
        }
        .status-dot {
          width: 10px;
          height: 10px;
          border-radius: 50%;
          background: var(--error-color);
        }
        .status-dot.connected {
          background: var(--success-color);
        }
        .status-dot.ringing {
          background: var(--warning-color);
          animation: blink 0.5s ease-in-out infinite;
        }
        @keyframes blink {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.3; }
        }
        .incoming-overlay {
          position: absolute;
          top: 0;
          left: 0;
          right: 0;
          bottom: 0;
          background: rgba(0,0,0,0.7);
          display: flex;
          flex-direction: column;
          justify-content: center;
          align-items: center;
          color: white;
          z-index: 10;
        }
        .incoming-overlay h2 {
          font-size: 24px;
          margin-bottom: 24px;
          animation: pulse 1s ease-in-out infinite;
        }
        .incoming-overlay .actions {
          display: flex;
          gap: 24px;
        }
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.6; }
        }
      </style>

      <div class="card">
        <div class="video-container">
          <video id="video" autoplay playsinline muted></video>
          <div class="placeholder" id="placeholder">
            <ha-icon icon="mdi:doorbell-video"></ha-icon>
            <div>Premi per visualizzare</div>
          </div>
          <div class="incoming-overlay" id="incoming-overlay" style="display: none;">
            <h2>🔔 Chiamata in arrivo</h2>
            <div class="actions">
              <button class="control-btn answer" id="answer-btn">
                <ha-icon icon="mdi:phone"></ha-icon>
                <span>Rispondi</span>
              </button>
              <button class="control-btn unlock" id="quick-unlock-btn">
                <ha-icon icon="mdi:door-open"></ha-icon>
                <span>Apri</span>
              </button>
              <button class="control-btn hangup" id="reject-btn">
                <ha-icon icon="mdi:phone-hangup"></ha-icon>
                <span>Rifiuta</span>
              </button>
            </div>
          </div>
        </div>

        <div class="status-bar">
          <div class="status-indicator">
            <div class="status-dot" id="status-dot"></div>
            <span id="status-text">Disconnesso</span>
          </div>
          <span id="duration"></span>
        </div>

        <div class="controls">
          <button class="control-btn answer" id="call-btn">
            <ha-icon icon="mdi:phone"></ha-icon>
            <span>Chiama</span>
          </button>
          <button class="control-btn mute" id="mute-btn">
            <ha-icon icon="mdi:microphone"></ha-icon>
            <span>Mute</span>
          </button>
          <button class="control-btn unlock" id="unlock-btn">
            <ha-icon icon="mdi:door-open"></ha-icon>
            <span>Apri</span>
          </button>
          <button class="control-btn hangup" id="hangup-btn">
            <ha-icon icon="mdi:phone-hangup"></ha-icon>
            <span>Chiudi</span>
          </button>
        </div>
      </div>
    `;

    this._setupEventListeners();
  }

  _setupEventListeners() {
    // Call/View button
    this.shadowRoot.getElementById('call-btn').addEventListener('click', () => {
      this._startCall();
    });

    // Mute button
    this.shadowRoot.getElementById('mute-btn').addEventListener('click', () => {
      this._toggleMute();
    });

    // Unlock button
    this.shadowRoot.getElementById('unlock-btn').addEventListener('click', () => {
      this._unlockDoor();
    });

    // Hangup button
    this.shadowRoot.getElementById('hangup-btn').addEventListener('click', () => {
      this._hangupCall();
    });

    // Incoming call buttons
    this.shadowRoot.getElementById('answer-btn').addEventListener('click', () => {
      this._answerCall();
    });

    this.shadowRoot.getElementById('quick-unlock-btn').addEventListener('click', () => {
      this._unlockDoor();
      this._answerCall();
    });

    this.shadowRoot.getElementById('reject-btn').addEventListener('click', () => {
      this._hangupCall();
    });

    // Click on video to view
    this.shadowRoot.getElementById('placeholder').addEventListener('click', () => {
      this._startCall();
    });
  }

  _updateCard() {
    if (!this._hass || !this._config) return;

    const cameraEntity = this._hass.states[this._config.camera_entity];
    if (!cameraEntity) return;

    const isRinging = cameraEntity.attributes.is_ringing;
    const isConnected = cameraEntity.attributes.is_connected;
    const stationState = cameraEntity.attributes.station_state;

    // Update status indicator
    const statusDot = this.shadowRoot.getElementById('status-dot');
    const statusText = this.shadowRoot.getElementById('status-text');

    statusDot.classList.remove('connected', 'ringing');

    if (isConnected) {
      statusDot.classList.add('connected');
      statusText.textContent = 'In chiamata';
    } else if (isRinging) {
      statusDot.classList.add('ringing');
      statusText.textContent = 'Chiamata in arrivo';
    } else {
      statusText.textContent = 'Pronto';
    }

    // Show/hide incoming overlay
    const incomingOverlay = this.shadowRoot.getElementById('incoming-overlay');
    incomingOverlay.style.display = isRinging && !this._isConnected ? 'flex' : 'none';

    // Show/hide placeholder
    const placeholder = this.shadowRoot.getElementById('placeholder');
    placeholder.style.display = this._isConnected ? 'none' : 'flex';

    // Update button states
    const callBtn = this.shadowRoot.getElementById('call-btn');
    const hangupBtn = this.shadowRoot.getElementById('hangup-btn');

    if (this._isConnected) {
      callBtn.style.display = 'none';
      hangupBtn.style.display = 'flex';
    } else {
      callBtn.style.display = 'flex';
      hangupBtn.style.display = isConnected ? 'flex' : 'none';
    }
  }

  async _startCall() {
    if (this._isConnected) return;

    try {
      // Get user media for microphone
      this._localStream = await navigator.mediaDevices.getUserMedia({
        audio: true,
        video: false
      });

      // Create WebRTC connection
      this._webrtcConnection = new RTCPeerConnection({
        iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
      });

      // Add local audio track
      this._localStream.getTracks().forEach(track => {
        this._webrtcConnection.addTrack(track, this._localStream);
      });

      // Handle incoming tracks (video from intercom)
      this._webrtcConnection.ontrack = (event) => {
        const video = this.shadowRoot.getElementById('video');
        video.srcObject = event.streams[0];
        video.muted = false; // Enable audio from intercom
      };

      // Create offer
      const offer = await this._webrtcConnection.createOffer({
        offerToReceiveAudio: true,
        offerToReceiveVideo: true
      });
      await this._webrtcConnection.setLocalDescription(offer);

      // Wait for ICE gathering to complete
      await new Promise(resolve => {
        if (this._webrtcConnection.iceGatheringState === 'complete') {
          resolve();
        } else {
          this._webrtcConnection.onicegatheringstatechange = () => {
            if (this._webrtcConnection.iceGatheringState === 'complete') {
              resolve();
            }
          };
        }
      });

      // Send offer to camera entity via Home Assistant
      const response = await this._hass.callService('camera', 'handle_web_rtc_offer', {
        entity_id: this._config.camera_entity,
        offer: this._webrtcConnection.localDescription.sdp
      });

      // Handle answer
      if (response && response.answer) {
        await this._webrtcConnection.setRemoteDescription({
          type: 'answer',
          sdp: response.answer
        });
      }

      this._isConnected = true;
      this._updateCard();

    } catch (error) {
      console.error('Failed to start call:', error);
      this._cleanup();
    }
  }

  async _answerCall() {
    // Press the answer button
    await this._hass.callService('button', 'press', {
      entity_id: 'button.bticino_hometouch_answer_call'
    });

    // Start the video/audio connection
    await this._startCall();
  }

  async _hangupCall() {
    // Press the hangup button
    await this._hass.callService('button', 'press', {
      entity_id: 'button.bticino_hometouch_hangup_call'
    });

    this._cleanup();
  }

  async _unlockDoor() {
    const lockEntity = this._config.lock_entity || 'button.bticino_hometouch_unlock_door_1';

    await this._hass.callService('button', 'press', {
      entity_id: lockEntity
    });

    // Visual feedback
    const unlockBtn = this.shadowRoot.getElementById('unlock-btn');
    unlockBtn.style.background = 'var(--success-color)';
    setTimeout(() => {
      unlockBtn.style.background = '';
    }, 1000);
  }

  _toggleMute() {
    if (!this._localStream) return;

    const audioTrack = this._localStream.getAudioTracks()[0];
    if (audioTrack) {
      audioTrack.enabled = !audioTrack.enabled;

      const muteBtn = this.shadowRoot.getElementById('mute-btn');
      const icon = muteBtn.querySelector('ha-icon');

      if (audioTrack.enabled) {
        muteBtn.classList.remove('muted');
        icon.setAttribute('icon', 'mdi:microphone');
      } else {
        muteBtn.classList.add('muted');
        icon.setAttribute('icon', 'mdi:microphone-off');
      }
    }
  }

  _cleanup() {
    if (this._webrtcConnection) {
      this._webrtcConnection.close();
      this._webrtcConnection = null;
    }

    if (this._localStream) {
      this._localStream.getTracks().forEach(track => track.stop());
      this._localStream = null;
    }

    const video = this.shadowRoot.getElementById('video');
    video.srcObject = null;

    this._isConnected = false;
    this._updateCard();
  }

  disconnectedCallback() {
    this._cleanup();
  }

  getCardSize() {
    return 5;
  }

  static getConfigElement() {
    return document.createElement('bticino-hometouch-card-editor');
  }

  static getStubConfig() {
    return {
      camera_entity: 'camera.bticino_hometouch_outdoor_station_1',
      lock_entity: 'button.bticino_hometouch_unlock_door_1'
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
        <paper-input
          label="Camera Entity"
          value="${this._config.camera_entity || ''}"
          @change="${this._valueChanged}"
          data-key="camera_entity"
        ></paper-input>
        <paper-input
          label="Lock Entity"
          value="${this._config.lock_entity || ''}"
          @change="${this._valueChanged}"
          data-key="lock_entity"
        ></paper-input>
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
  description: 'Interactive intercom card with video and audio',
  preview: true
});

console.info('%c BTICINO-INTERCOM-CARD %c v1.0.0 ',
  'color: white; background: #03A9F4; font-weight: bold;',
  'color: #03A9F4; background: white; font-weight: bold;'
);
