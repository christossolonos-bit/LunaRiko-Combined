/**
 * PlaybackController - Persistent audio element for mobile-compatible playback.
 * Handles AudioContext unlocking across browsers/platforms.
 */

/** Ensure a URL is absolute (relative paths get prefixed with origin). */
function ensureAbsoluteUrl(url) {
  try {
    new URL(url);
    return url;
  } catch {
    if (!url) return url;
    if (url.startsWith('/')) return `${location.origin}${url}`;
    return `${location.origin}/${url}`;
  }
}

export class PlaybackController {
  constructor(audioMgr) {
    this.audioMgr = audioMgr;
    this._inited = false;
    this._unlocked = false;
    this.el = null;
    this._analyserAttached = false;
  }

  initPersistent() {
    if (this._inited) return;
    this._inited = true;

    // Create persistent audio element
    if (!this.audioMgr.audioElement) {
      const a = document.createElement('audio');
      a.crossOrigin = 'anonymous';
      a.preload = 'auto';
      a.playsInline = true;
      a.setAttribute('playsinline', '');
      a.setAttribute('webkit-playsinline', '');
      a.style.display = 'none';
      document.body.appendChild(a);
      this.audioMgr.audioElement = a;
    }
    this.el = this.audioMgr.audioElement;

    // Create AudioContext if needed
    try {
      if (!this.audioMgr.audioContext) {
        const AC = window.AudioContext || window.webkitAudioContext;
        if (AC) {
          this.audioMgr.audioContext = new AC();
        }
      }

      if (this.audioMgr.audioContext && !this.audioMgr.analyser) {
        this._tryAttachAnalyser();
      }

      // Visibility resume helper
      document.addEventListener('visibilitychange', async () => {
        if (document.visibilityState === 'visible' &&
            this.audioMgr.audioContext &&
            this.audioMgr.audioContext.state === 'suspended') {
          try {
            await this.audioMgr.audioContext.resume();
          } catch (e) {}
        }
      });
    } catch (e) {
      console.warn('PlaybackController init error:', e);
    }
  }

  // Unlock audio on user gesture - tries multiple strategies
  async unlockOnce() {
    if (this._unlocked) return true;

    this.initPersistent();

    // 1) Try to resume/create AudioContext
    let ctx = null;
    try {
      if (!this.audioMgr.audioContext) {
        const AC = window.AudioContext || window.webkitAudioContext;
        if (AC) {
          this.audioMgr.audioContext = new AC();
        }
      }
      ctx = this.audioMgr.audioContext;
      if (ctx && ctx.state === 'suspended') {
        try { await ctx.resume(); } catch (e) { console.warn('resume() failed:', e); }
      }
    } catch (e) {
      console.warn('AudioContext creation/resume failed:', e);
    }

    // 2) Try silent buffer (works on many browsers)
    try {
      if (ctx && ctx.state === 'running') {
        const sampleRate = ctx.sampleRate || 44100;
        const length = Math.max(1, Math.floor(sampleRate * 0.01));
        const buffer = ctx.createBuffer(1, length, sampleRate);
        const src = ctx.createBufferSource();
        src.buffer = buffer;
        src.connect(ctx.destination);
        src.start(0);
        await new Promise(res => setTimeout(res, 40));
        try { src.stop(); } catch (e) {}
        this._unlocked = true;
        this._tryAttachAnalyser();
        return true;
      }
    } catch (e) {
      console.warn('Silent buffer unlock failed:', e);
    }

    // 3) Fallback: muted play/pause on persistent element
    try {
      const el = this.el;
      if (!el) throw new Error('No audio element');
      const hadSrc = !!el.src;
      if (!hadSrc) {
        el.src = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAESsAACJWAAACABAAZGF0YQAAAAA=';
      }
      el.muted = true;
      el.playsInline = true;
      el.setAttribute('playsinline', '');
      el.setAttribute('webkit-playsinline', '');

      const p = new Promise((resolve, reject) => {
        let done = false;
        const onPlaying = () => { if (!done) { done = true; cleanup(); resolve(true); } };
        const onError = () => { if (!done) { done = true; cleanup(); reject(new Error('audio error')); } };
        const timeoutId = setTimeout(() => { if (!done) { done = true; cleanup(); reject(new Error('timeout')); } }, 1200);
        function cleanup() {
          el.removeEventListener('playing', onPlaying);
          el.removeEventListener('error', onError);
          clearTimeout(timeoutId);
        }
        el.addEventListener('playing', onPlaying);
        el.addEventListener('error', onError);
        try {
          const prom = el.play();
          if (prom && prom.catch) prom.catch(() => {});
        } catch (err) { cleanup(); reject(err); }
      });

      await p;
      try { el.pause(); el.currentTime = 0; } catch (e) {}
      el.muted = false;
      this._unlocked = true;
      this._tryAttachAnalyser();
      return true;
    } catch (e) {
      console.warn('Muted element fallback failed:', e);
    }

    console.warn('unlockOnce: could not unlock audio on this gesture');
    return false;
  }

  _tryAttachAnalyser() {
    try {
      if (this.audioMgr.audioContext && this.el && !this.audioMgr.analyser && !this._analyserAttached) {
        try {
          const src = this.audioMgr.audioContext.createMediaElementSource(this.el);
          const analyser = this.audioMgr.audioContext.createAnalyser();
          analyser.fftSize = 2048;
          src.connect(analyser);
          analyser.connect(this.audioMgr.audioContext.destination);
          this.audioMgr.analyser = analyser;
          this.audioMgr.timeDomainData = new Uint8Array(analyser.fftSize);
          this.audioMgr.freqData = new Uint8Array(analyser.frequencyBinCount);
          this._analyserAttached = true;
        } catch (e) {
          console.warn('attachAnalyser failed:', e);
        }
      }
    } catch (e) {
      console.warn('Error in _tryAttachAnalyser:', e);
    }
  }

  // Play audio URL using persistent element
  async playAudioUrl(url) {
    if (!url) return false;
    this.initPersistent();

    if (!this._unlocked) {
      console.warn('playAudioUrl: audio not unlocked yet. Attempting auto-unlock...');
      try { await this.unlockOnce(); } catch (e) {}
    }

    const abs = ensureAbsoluteUrl(url);
    const el = this.el;

    // Stop current playback
    try { el.pause(); el.currentTime = 0; } catch (e) {}

    // Set src only if changed
    if (!el.src || el.src !== abs) {
      el.src = abs;
      try { el.load(); } catch (e) {}
    }

    // Ensure AudioContext running
    try {
      if (this.audioMgr.audioContext && this.audioMgr.audioContext.state === 'suspended') {
        await this.audioMgr.audioContext.resume();
      }
    } catch (e) {}

    // Try to play
    try {
      await el.play();
      this._tryAttachAnalyser();
      return true;
    } catch (err) {
      console.warn('play() blocked, attempting muted-first fallback:', err);
    }

    // Muted-first fallback
    try {
      el.muted = true;
      await el.play();
      await new Promise(r => setTimeout(r, 80));
      el.muted = false;
      this._tryAttachAnalyser();
      return true;
    } catch (err) {
      console.warn('Muted-first failed:', err);
    }

    // Transient fallback (rare)
    try {
      const tmp = new Audio(abs);
      tmp.playsInline = true;
      tmp.crossOrigin = 'anonymous';
      tmp.setAttribute('playsinline', '');
      tmp.setAttribute('webkit-playsinline', '');
      document.body.appendChild(tmp);
      await tmp.play();
      el.src = abs;
      try { el.load(); } catch (e) {}
      tmp.pause();
      tmp.remove();
      this._tryAttachAnalyser();
      return true;
    } catch (err) {
      console.warn('Transient fallback failed:', err);
    }

    console.error('All playback strategies failed for', abs);
    return false;
  }

  /**
   * Resolve when the clip currently playing actually finishes.
   *
   * This is what lets the CLIENT decide when a clip is done, instead of the
   * server guessing with a sleep that starts when it POSTs /talk. Those are
   * different clocks: the server's starts at send, the client's at "enough
   * bytes have arrived to play", and on a phone using cellular data the gap
   * between them is seconds. Without this the next clip resets the element on
   * top of one still sounding — the mid-word cut-off.
   *
   * `expectedSeconds` is the server's duration, used only for the timeout
   * backstop, and only when the element doesn't know its own. The backstop is
   * deliberately generous: cutting a clip short is the exact bug this prevents,
   * so it must never fire early on a slow or stalled stream.
   */
  waitUntilEnded(expectedSeconds = 0) {
    const el = this.el;
    if (!el || el.ended) return Promise.resolve();

    return new Promise((resolve) => {
      let done = false;
      const finish = (why) => {
        if (done) return;
        done = true;
        el.removeEventListener('ended', onEnded);
        el.removeEventListener('error', onError);
        clearTimeout(timer);
        if (why !== 'ended') console.warn(`[playback] clip finished via ${why}`);
        resolve();
      };
      const onEnded = () => finish('ended');
      const onError = () => finish('error');

      const known = Number.isFinite(el.duration) && el.duration > 0
        ? el.duration
        : expectedSeconds;
      const remaining = Math.max(0, known - (el.currentTime || 0));
      const timer = setTimeout(() => finish('timeout'), (remaining + 5) * 1000);

      el.addEventListener('ended', onEnded);
      el.addEventListener('error', onError);
    });
  }
}
