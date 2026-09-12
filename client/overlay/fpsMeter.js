// Frame-rate readout. Answers "is the frame rate the problem?" with a number
// instead of a guess — worth checking before you start turning knobs.
//
// OFF unless the page is opened with `?fps=1`, so it can never show up in a
// recording by accident:
//
//   http://127.0.0.1:5173/?fps=1
//
// It also prints to the browser console every few seconds, so you get a trace
// over time rather than only whatever it happened to say when you looked.
//
// If the number is low, the two knobs are `pixelRatioCap` and `maxFps` in
// client/config.js.

const ENABLED = new URLSearchParams(location.search).get('fps') === '1';
const LOG_EVERY_MS = 5000;   // console cadence; the on-screen number is 1/sec

/**
 * Creates the overlay and returns a `tick()` to call once per rendered frame.
 * Returns a no-op when disabled, so the caller needs no branch.
 */
export function initFpsMeter() {
  if (!ENABLED) return () => {};

  const el = document.createElement('div');
  el.style.cssText =
    'position:fixed;top:8px;left:8px;z-index:9999;pointer-events:none;' +
    'font:12px monospace;color:#7CFC00;background:rgba(0,0,0,0.55);' +
    'padding:4px 7px;border-radius:4px';
  document.body.appendChild(el);
  console.log('[fps] meter on (?fps=1)');

  let frames = 0;
  let windowStart = performance.now();
  let lastLog = windowStart;
  let worst = Infinity;

  return function tick() {
    frames += 1;
    const now = performance.now();
    const elapsed = now - windowStart;
    if (elapsed < 1000) return;

    const fps = Math.round((frames * 1000) / elapsed);
    if (fps < worst) worst = fps;
    el.textContent = `${fps} fps  (worst ${worst})`;
    frames = 0;
    windowStart = now;

    if (now - lastLog >= LOG_EVERY_MS) {
      console.log(`[fps] ${fps} fps (worst ${worst})`);
      lastLog = now;
    }
  };
}
