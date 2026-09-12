import * as THREE from 'three';
import { HTTP_URL } from '../config.js';

/**
 * VRPositionTracker - Tracks the user's HMD position in VR world space.
 * Reads camera world position each frame and periodically sends it to the server.
 */
export class VRPositionTracker {
  constructor(camera, dolly, { sendInterval = 500 } = {}) {
    this.camera = camera;
    this.dolly = dolly;
    this.sendInterval = sendInterval;

    // Current position (updated every frame)
    this.position = new THREE.Vector3();
    this.rotation = new THREE.Euler();

    // For throttled server updates
    this._lastSendTime = 0;
    this._enabled = false;
    this._worldPos = new THREE.Vector3();
  }

  /** Start tracking and sending updates */
  enable() {
    this._enabled = true;
    console.log('VR Position Tracker enabled');
  }

  /** Stop tracking */
  disable() {
    this._enabled = false;
  }

  /** Call every frame from the animate loop */
  update() {
    if (!this._enabled) return;

    // Get camera world position (accounts for dolly + XR offset)
    this.camera.getWorldPosition(this._worldPos);
    this.position.copy(this._worldPos);
    this.rotation.copy(this.camera.rotation);

    // Throttled send to server
    const now = performance.now();
    if (now - this._lastSendTime >= this.sendInterval) {
      this._lastSendTime = now;
      this._sendPosition();
    }
  }

  /** Get current position as a plain object */
  getPosition() {
    return {
      x: this.position.x,
      y: this.position.y,
      z: this.position.z,
    };
  }

  /** Get current rotation as a plain object */
  getRotation() {
    return {
      x: this.rotation.x,
      y: this.rotation.y,
      z: this.rotation.z,
    };
  }

  /** Send position to server (fire-and-forget) */
  _sendPosition() {
    const payload = {
      x: parseFloat(this.position.x.toFixed(3)),
      y: parseFloat(this.position.y.toFixed(3)),
      z: parseFloat(this.position.z.toFixed(3)),
      rx: parseFloat(this.rotation.x.toFixed(3)),
      ry: parseFloat(this.rotation.y.toFixed(3)),
      rz: parseFloat(this.rotation.z.toFixed(3)),
      timestamp: Date.now(),
    };

    fetch(`${HTTP_URL}/vr/position`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    }).catch(() => {
      // Silent fail - position updates are best-effort
    });
  }
}
