/**
 * Acoustic voice fingerprint from 16 kHz mono PCM (Int16 LE).
 * Uses pitch (F0), spectral centroid, and energy — enough to tell callers apart
 * without a heavy ML model.
 */

const SAMPLE_RATE = 16_000;

/**
 * @param {Buffer} pcm Int16 mono @ 16kHz
 * @returns {{
 *   pitchHz: number,
 *   pitchStd: number,
 *   centroidHz: number,
 *   zcr: number,
 *   rms: number,
 *   vector: number[],
 *   label: string,
 * } | null}
 */
export function extractVoiceFingerprint(pcm) {
  if (!pcm || pcm.length < SAMPLE_RATE * 0.25 * 2) return null;

  const samples = new Float32Array(pcm.length / 2);
  for (let i = 0; i < samples.length; i += 1) {
    samples[i] = pcm.readInt16LE(i * 2) / 32768;
  }

  // Light high-pass to reduce rumble before pitch.
  const filtered = highPass(samples, 70);

  const pitches = [];
  const frame = Math.floor(SAMPLE_RATE * 0.04); // 40ms
  const hop = Math.floor(SAMPLE_RATE * 0.02);
  for (let start = 0; start + frame < filtered.length; start += hop) {
    const slice = filtered.subarray(start, start + frame);
    const rms = frameRms(slice);
    if (rms < 0.02) continue; // silence / noise
    const f0 = estimatePitchHz(slice, SAMPLE_RATE);
    if (f0 >= 70 && f0 <= 400) pitches.push(f0);
  }

  if (pitches.length < 3) return null;

  const pitchHz = median(pitches);
  const pitchStd = stddev(pitches);
  const centroidHz = spectralCentroid(filtered, SAMPLE_RATE);
  const zcr = zeroCrossingRate(filtered);
  const rms = frameRms(filtered);

  // Fixed 12-dim signature for cosine matching (normalized components).
  const vector = normalizeVector([
    pitchHz / 400,
    pitchStd / 80,
    centroidHz / 4000,
    zcr,
    rms,
    Math.min(1, pitches.filter((p) => p < 120).length / pitches.length),
    Math.min(1, pitches.filter((p) => p >= 120 && p < 180).length / pitches.length),
    Math.min(1, pitches.filter((p) => p >= 180 && p < 240).length / pitches.length),
    Math.min(1, pitches.filter((p) => p >= 240).length / pitches.length),
    Math.tanh(pitchStd / 40),
    Math.tanh(centroidHz / 2000),
    Math.tanh(rms * 4),
  ]);

  return {
    pitchHz: round1(pitchHz),
    pitchStd: round1(pitchStd),
    centroidHz: round1(centroidHz),
    zcr: round3(zcr),
    rms: round3(rms),
    vector,
    label: describeVoice(pitchHz, centroidHz),
  };
}

function describeVoice(pitchHz, centroidHz) {
  let band = "mid";
  if (pitchHz < 120) band = "low";
  else if (pitchHz < 180) band = "mid-low";
  else if (pitchHz < 240) band = "mid-high";
  else band = "high";

  let color = "neutral";
  if (centroidHz < 1200) color = "warm";
  else if (centroidHz > 2200) color = "bright";

  return `${band}~${Math.round(pitchHz)}Hz ${color}`;
}

function highPass(samples, cutoffHz) {
  const out = new Float32Array(samples.length);
  const rc = 1 / (2 * Math.PI * cutoffHz);
  const dt = 1 / SAMPLE_RATE;
  const alpha = rc / (rc + dt);
  let prevY = 0;
  let prevX = samples[0] || 0;
  for (let i = 0; i < samples.length; i += 1) {
    const x = samples[i];
    prevY = alpha * (prevY + x - prevX);
    prevX = x;
    out[i] = prevY;
  }
  return out;
}

function frameRms(frame) {
  let sum = 0;
  for (let i = 0; i < frame.length; i += 1) sum += frame[i] * frame[i];
  return Math.sqrt(sum / Math.max(frame.length, 1));
}

function zeroCrossingRate(samples) {
  let crosses = 0;
  for (let i = 1; i < samples.length; i += 1) {
    if ((samples[i - 1] >= 0 && samples[i] < 0) || (samples[i - 1] < 0 && samples[i] >= 0)) {
      crosses += 1;
    }
  }
  return crosses / Math.max(samples.length - 1, 1);
}

/** Autocorrelation pitch estimate in Hz, or 0 if unclear. */
function estimatePitchHz(frame, sampleRate) {
  const minLag = Math.floor(sampleRate / 400);
  const maxLag = Math.floor(sampleRate / 70);
  if (maxLag >= frame.length) return 0;

  let bestLag = 0;
  let bestCorr = 0;
  for (let lag = minLag; lag <= maxLag; lag += 1) {
    let corr = 0;
    for (let i = 0; i < frame.length - lag; i += 1) {
      corr += frame[i] * frame[i + lag];
    }
    if (corr > bestCorr) {
      bestCorr = corr;
      bestLag = lag;
    }
  }
  if (bestLag <= 0 || bestCorr <= 0) return 0;
  return sampleRate / bestLag;
}

/** Crude magnitude spectrum centroid via short DFT buckets. */
function spectralCentroid(samples, sampleRate) {
  const n = 512;
  if (samples.length < n) return 0;
  // Average a few windows near the loudest stretch.
  let peakAt = 0;
  let peakRms = 0;
  for (let i = 0; i + n < samples.length; i += n) {
    const r = frameRms(samples.subarray(i, i + n));
    if (r > peakRms) {
      peakRms = r;
      peakAt = i;
    }
  }
  const frame = samples.subarray(peakAt, peakAt + n);
  let weighted = 0;
  let total = 0;
  const bins = 64;
  for (let k = 1; k < bins; k += 1) {
    let re = 0;
    let im = 0;
    const w = (2 * Math.PI * k) / n;
    for (let t = 0; t < n; t += 1) {
      re += frame[t] * Math.cos(w * t);
      im -= frame[t] * Math.sin(w * t);
    }
    const mag = Math.sqrt(re * re + im * im);
    const freq = (k * sampleRate) / n;
    weighted += freq * mag;
    total += mag;
  }
  return total > 0 ? weighted / total : 0;
}

function median(arr) {
  const s = [...arr].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

function stddev(arr) {
  if (arr.length < 2) return 0;
  const mean = arr.reduce((a, b) => a + b, 0) / arr.length;
  const v = arr.reduce((a, b) => a + (b - mean) ** 2, 0) / arr.length;
  return Math.sqrt(v);
}

function normalizeVector(v) {
  const norm = Math.sqrt(v.reduce((a, x) => a + x * x, 0)) || 1;
  return v.map((x) => round4(x / norm));
}

function round1(n) {
  return Math.round(n * 10) / 10;
}
function round3(n) {
  return Math.round(n * 1000) / 1000;
}
function round4(n) {
  return Math.round(n * 10000) / 10000;
}
