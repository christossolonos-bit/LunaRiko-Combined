import * as THREE from 'three';
import { VRButton } from 'three/addons/webxr/VRButton.js';
import { HTTP_URL, VR_CONFIG } from '../config.js';
import { VRTouchDetector } from './vrTouchDetector.js';
import { pulseXRController } from './xrHaptics.js';

/**
 * VRManager - Handles all WebXR setup, controllers, push-to-talk, and session lifecycle.
 * Only instantiated when VR is detected. Keeps VR concerns out of the main app.
 *
 * Controllers are rendered as simple hand indicators (spheres) instead of
 * controller models. Beam pointers are removed in favor of proximity touch.
 */
export class VRManager {
  constructor(renderer, scene, camera, controls, { onAudioRecorded } = {}) {
    this.renderer = renderer;
    this.scene = scene;
    this.camera = camera;
    this.controls = controls;
    this.onAudioRecorded = onAudioRecorded || null;

    // Dolly group - moves the user in VR space
    this.dolly = new THREE.Group();
    this.dolly.position.set(...VR_CONFIG.dollyPosition);
    this.scene.add(this.dolly);

    // Reparent camera under dolly for VR
    this.dolly.add(this.camera);

    // Controllers
    this.controller0 = null;
    this.controller1 = null;
    this.controllerGrip0 = null;
    this.controllerGrip1 = null;

    // Hand indicators (visual spheres on grips)
    this.handIndicators = [null, null];

    // Touch detector (initialized after VRM is loaded)
    this.touchDetector = null;

    // Recording state
    this.mediaRecorder = null;
    this.audioChunks = [];
    this.isRecording = false;
    this.currentStream = null;
    this.recordingIndicator = null;

    this.isInVRSession = false;
  }

  _pulseController(controllerIndex, intensity = VR_CONFIG.hapticIntensity, duration = VR_CONFIG.hapticDuration) {
    pulseXRController(this.renderer, controllerIndex, intensity, duration);
  }

  /** Enable XR on the renderer and set up everything */
  init() {
    // Enable WebXR
    this.renderer.xr.enabled = true;
    this.renderer.xr.setReferenceSpaceType('local-floor');

    // Add VR button to DOM
    document.body.appendChild(VRButton.createButton(this.renderer));

    // Setup controllers
    this._setupControllers();

    // Session lifecycle
    this.renderer.xr.addEventListener('sessionstart', () => this._onSessionStart());
    this.renderer.xr.addEventListener('sessionend', () => this._onSessionEnd());

    console.log('VR Manager initialized');
  }

  /**
   * Initialize proximity touch detection after the VRM model is loaded.
   * Must be called separately since VRM loading is async.
   */
  initTouchDetection(vrm, { onTouch } = {}) {
    this.touchDetector = new VRTouchDetector(
      this.renderer,
      vrm,
      {
        controllerGrips: [this.controllerGrip0, this.controllerGrip1],
        handIndicators: this.handIndicators,
      },
      { onTouch },
    );

    // Auto-enable if already in a VR session
    if (this.isInVRSession) {
      this.touchDetector.enable();
    }
  }

  _setupControllers() {
    // --- Grip spaces (hand position / visuals) ---
    this.controllerGrip0 = this.renderer.xr.getControllerGrip(0);
    this.dolly.add(this.controllerGrip0);

    this.controllerGrip1 = this.renderer.xr.getControllerGrip(1);
    this.dolly.add(this.controllerGrip1);

    // Hand indicator spheres (replace controller models and beam pointers)
    this.handIndicators[0] = this._createHandIndicator();
    this.controllerGrip0.add(this.handIndicators[0]);

    this.handIndicators[1] = this._createHandIndicator();
    this.controllerGrip1.add(this.handIndicators[1]);

    // --- Target ray spaces (for input events) ---
    this.controller0 = this.renderer.xr.getController(0);
    this.dolly.add(this.controller0);

    this.controller1 = this.renderer.xr.getController(1);
    this.dolly.add(this.controller1);

    // Recording indicator on right grip
    this.recordingIndicator = this._createRecordingIndicator();
    this.recordingIndicator.position.set(0, 0.05, -0.05);
    this.controllerGrip1.add(this.recordingIndicator);

    // Push-to-talk: squeeze (grip/side button) on either controller
    this.controller0.addEventListener('squeezestart', () => {
      this._pulseController(0, 0.55, 100);
      this._startRecording();
    });
    this.controller0.addEventListener('squeezeend', () => this._stopRecording());
    this.controller1.addEventListener('squeezestart', () => {
      this._pulseController(1, 0.55, 100);
      this._startRecording();
    });
    this.controller1.addEventListener('squeezeend', () => this._stopRecording());

    // Trigger (select) - audio unlock + touch interaction
    this.controller0.addEventListener('select', () => {
      this._pulseController(0);
      this._dispatchUnlockAudio();
      if (this.touchDetector) this.touchDetector.handleTrigger(0);
    });
    this.controller1.addEventListener('select', () => {
      this._pulseController(1);
      this._dispatchUnlockAudio();
      if (this.touchDetector) this.touchDetector.handleTrigger(1);
    });
  }

  /** Create a hand indicator: small sphere + larger glow sphere */
  _createHandIndicator() {
    const group = new THREE.Group();

    // Fingertip sphere
    const sphere = new THREE.Mesh(
      new THREE.SphereGeometry(0.02, 16, 16),
      new THREE.MeshBasicMaterial({
        color: 0x88ccff,
        transparent: true,
        opacity: 0.85,
      }),
    );
    group.add(sphere);

    // Touch radius glow
    const glow = new THREE.Mesh(
      new THREE.SphereGeometry(0.045, 16, 16),
      new THREE.MeshBasicMaterial({
        color: 0x88ccff,
        transparent: true,
        opacity: 0.1,
        depthWrite: false,
      }),
    );
    group.add(glow);

    // Store refs for color updates by VRTouchDetector
    group.userData = { sphere, glow };
    return group;
  }

  _createRecordingIndicator() {
    const geometry = new THREE.SphereGeometry(0.02, 16, 16);
    const material = new THREE.MeshBasicMaterial({
      color: 0xff0000,
      transparent: true,
      opacity: 0,
    });
    return new THREE.Mesh(geometry, material);
  }

  _dispatchUnlockAudio() {
    window.dispatchEvent(new CustomEvent('vr-unlock-audio'));
  }

  // --- Session lifecycle ---

  _onSessionStart() {
    console.log('VR Session Started');
    this.isInVRSession = true;
    document.body.classList.add('vr-active');

    if (this.controls) this.controls.enabled = false;
    if (this.touchDetector) this.touchDetector.enable();

    this._dispatchUnlockAudio();
    window.dispatchEvent(new CustomEvent('vr-session-start'));
  }

  _onSessionEnd() {
    console.log('VR Session Ended');
    this.isInVRSession = false;
    document.body.classList.remove('vr-active');

    if (this.controls) this.controls.enabled = true;
    if (this.touchDetector) this.touchDetector.disable();

    if (this.isRecording) this._stopRecording();
    window.dispatchEvent(new CustomEvent('vr-session-end'));
  }

  // --- Push-to-talk recording ---

  async _startRecording() {
    if (this.isRecording) return;
    console.log('VR: Starting recording (grip pressed)');

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          sampleRate: 16000,
        },
      });

      this.currentStream = stream;

      let mimeType = 'audio/webm';
      if (MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) {
        mimeType = 'audio/webm;codecs=opus';
      } else if (MediaRecorder.isTypeSupported('audio/mp4')) {
        mimeType = 'audio/mp4';
      }

      this.mediaRecorder = new MediaRecorder(stream, { mimeType });
      this.audioChunks = [];

      this.mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) this.audioChunks.push(e.data);
      };

      this.mediaRecorder.onstop = async () => {
        const blob = new Blob(this.audioChunks, { type: mimeType });
        if (blob.size > 1000) {
          await this._uploadAudio(blob);
        } else {
          console.log('VR: Recording too short, skipping');
        }
        if (this.currentStream) {
          this.currentStream.getTracks().forEach((t) => t.stop());
          this.currentStream = null;
        }
      };

      this.mediaRecorder.start(100);
      this.isRecording = true;
      window.dispatchEvent(new CustomEvent('recording-start'));
    } catch (err) {
      console.error('VR: Microphone error:', err);
      this.isRecording = false;
    }
  }

  _stopRecording() {
    if (!this.isRecording || !this.mediaRecorder) return;
    console.log('VR: Stopping recording (grip released)');

    try {
      if (this.mediaRecorder.state !== 'inactive') {
        this.mediaRecorder.stop();
      }
    } catch (err) {
      console.warn('VR: Error stopping recorder:', err);
    }

    this.isRecording = false;
    window.dispatchEvent(new CustomEvent('recording-stop'));
  }

  async _uploadAudio(blob) {
    const formData = new FormData();
    let ext = '.webm';
    if (blob.type.includes('mp4')) ext = '.mp4';
    else if (blob.type.includes('ogg')) ext = '.ogg';

    formData.append('file', blob, `recording${ext}`);
    console.log('VR: Uploading audio...', blob.size, 'bytes');

    try {
      const res = await fetch(`${HTTP_URL}/upload-audio/`, {
        method: 'POST',
        body: formData,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const result = await res.json();
      console.log('VR: Audio uploaded:', result);
    } catch (err) {
      console.error('VR: Audio upload failed:', err);
    }
  }

  // --- Per-frame update (call from animate loop) ---

  update() {
    // Recording indicator pulse
    if (this.recordingIndicator) {
      if (this.isRecording) {
        const pulse = Math.sin(Date.now() * 0.01) * 0.3 + 0.7;
        this.recordingIndicator.material.opacity = pulse;
      } else {
        this.recordingIndicator.material.opacity = 0;
      }
    }

    // Proximity touch detection + hand visual updates
    if (this.touchDetector) {
      this.touchDetector.update();
    }
  }

  /** Switch renderer to use XR animation loop instead of requestAnimationFrame */
  startXRLoop(animateCallback) {
    this.renderer.setAnimationLoop(animateCallback);
  }

  /** Get the dolly (user rig) for position queries */
  getDolly() {
    return this.dolly;
  }
}

/** Check if WebXR immersive-vr is supported */
export async function isVRSupported() {
  if (!navigator.xr) return false;
  try {
    return await navigator.xr.isSessionSupported('immersive-vr');
  } catch {
    return false;
  }
}
