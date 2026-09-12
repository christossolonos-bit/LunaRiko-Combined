import { VRM_PATH, WS_URL, MOUTH_THRESHOLD }       from '../config.js';

export class AudioManager {
    /**
     * @param vrm - VRM model with expressionManager
     * @param options - optional config overrides:
     *   smoothFactor (0-1), ampThreshold, ampScale,
     *   centroidThresholds: { wide, ih, oh }
     */
    constructor(vrm, options = {}) {
      this.vrm = vrm;
      this.currentExpression = "neutral";
      this.audioElement = null;
      this.audioContext = null;
      this.analyser = null;
      this.timeDomainData = null;
      this.freqData = null;
      this.prevValues = { aa: 0, ee: 0, ih: 0, oh: 0, ou: 0 };
      // default parameters
      const defaults = {
        smoothFactor: 0.2,
        ampThreshold: 0.005,
        ampScale: MOUTH_THRESHOLD, 
        centroidThresholds: { wide: 0.75, ih: 0.5, oh: 0.25 }
      };
      // merge options
      this.config = { ...defaults, ...options };
      if (options.centroidThresholds) {
        this.config.centroidThresholds = { ...defaults.centroidThresholds, ...options.centroidThresholds };
      }
      this.allExpressions = ["happy", "angry", "sad", "relaxed", "surprised", "neutral"];
    }
  
    /** Update config at runtime */
    updateConfig(newConfig) {
      Object.assign(this.config, newConfig);
      if (newConfig.centroidThresholds) {
        Object.assign(this.config.centroidThresholds, newConfig.centroidThresholds);
      }
    }
  
    setExpression(expr) {
      for (const e of this.allExpressions) {
        if (this.vrm.expressionManager.getExpression(e)) {
          this.vrm.expressionManager.setValue(e, 0);
        }
      }
      if (this.vrm.expressionManager.getExpression(expr)) {
        this.vrm.expressionManager.setValue(expr, 1.0);
        this.currentExpression = expr;
      }
    }
  
    async setupAudio(url) {
      this.audioElement = new Audio(url);

      this.audioElement.crossOrigin = "anonymous"; // safe for analysis
      this.audioElement.preload = "auto";

      // ✅ Add the audio element to DOM for mobile
      this.audioElement.style.display = "none";
      document.body.appendChild(this.audioElement);

      await new Promise((res, rej) => {
        this.audioElement.oncanplaythrough = res;
        this.audioElement.onerror = rej;
        this.audioElement.load();
      });
      this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
      const source = this.audioContext.createMediaElementSource(this.audioElement);
      this.analyser = this.audioContext.createAnalyser();
      this.analyser.fftSize = 2048;
      source.connect(this.analyser);
      this.analyser.connect(this.audioContext.destination);
      this.timeDomainData = new Uint8Array(this.analyser.fftSize);
      this.freqData = new Uint8Array(this.analyser.frequencyBinCount);
    }
  
    async analyzeAudio() {
      if (!this.audioContext) {
        throw new Error("Call setupAudio() before analyzeAudio().");
      }
      if (this.audioContext.state === 'suspended') {
        await this.audioContext.resume();
      }
    }
  
    resetMouth() {
      Object.keys(this.prevValues).forEach(k => {
        if (this.vrm.expressionManager.getExpression(k)) {
          this.vrm.expressionManager.setValue(k, 0);
        }
        this.prevValues[k] = 0;
      });
    }
  
    /**
     * Call each frame (time arg ignored)
     */
    updateLipSync(time) {
      if (!this.analyser) return;
      this.analyser.getByteTimeDomainData(this.timeDomainData);
      this.analyser.getByteFrequencyData(this.freqData);
  
      // RMS amplitude
      let sumSq = 0;
      for (let i = 0; i < this.timeDomainData.length; i++) {
        const v = (this.timeDomainData[i] - 128) / 128;
        sumSq += v * v;
      }
      const rms = Math.sqrt(sumSq / this.timeDomainData.length);
  
      // spectral centroid (0–1)
      let weightedSum = 0, magSum = 0;
      for (let i = 0; i < this.freqData.length; i++) {
        const mag = this.freqData[i];
        weightedSum += i * mag;
        magSum += mag;
      }
      const centroid = magSum > 0 ? (weightedSum / magSum) / this.freqData.length : 0;
  
      // map to phoneme shapes with config
      const { ampThreshold, ampScale, centroidThresholds, smoothFactor } = this.config;
      const shapes = { aa: 0, ee: 0, ih: 0, oh: 0, ou: 0 };
      if (rms > ampThreshold) {
        const level = Math.min(1, (rms - ampThreshold) * ampScale);
        shapes.aa = level;
        if (centroid > centroidThresholds.wide) shapes.ee = level;
        else if (centroid > centroidThresholds.ih) shapes.ih = level;
        else if (centroid > centroidThresholds.oh) shapes.oh = level;
        else shapes.ou = level;
      }
  
      // smoothing + apply
      for (const k of Object.keys(shapes)) {
        const target = shapes[k];
        const smooth = this.prevValues[k] + smoothFactor * (target - this.prevValues[k]);
        this.prevValues[k] = smooth;
        if (this.vrm.expressionManager.getExpression(k)) {
          this.vrm.expressionManager.setValue(k, smooth);
        }
      }
    }
  }
  