// subtitles.js

let _hideTimeout = null;
let _revealTimeouts = [];
let _enabled = true;

export function setSubtitlesEnabled(enabled) {
  _enabled = enabled;
  if (!enabled) {
    const container = document.getElementById('subtitle-container');
    if (container) container.classList.remove('visible');
  }
}

export function getSubtitlesEnabled() {
  return _enabled;
}

export function showSubtitleStreaming(text, totalDurationSeconds, mode = "word") {
  if (!_enabled) return;

  const container = document.getElementById('subtitle-container');

  // Clear any pending timeouts from the previous chunk
  if (_hideTimeout !== null) {
    clearTimeout(_hideTimeout);
    _hideTimeout = null;
  }
  for (const id of _revealTimeouts) clearTimeout(id);
  _revealTimeouts = [];

  container.innerHTML = '';
  container.classList.add('visible');

  let segments = mode === "letter" ? [...text] : text.split(' ');
  let totalSteps = segments.length;
  let delay = (totalDurationSeconds * 1000 * 0.7) / totalSteps;

  segments.forEach((segment, i) => {
    const span = document.createElement('span');
    span.style.opacity = '0';
    span.style.transition = 'opacity 0.3s ease';
    span.textContent = mode === "word" ? segment + ' ' : segment;

    container.appendChild(span);

    const tid = setTimeout(() => {
      span.style.opacity = '1';
    }, i * delay);
    _revealTimeouts.push(tid);
  });

  // Hide after total duration
  _hideTimeout = setTimeout(() => {
    container.classList.remove('visible');
    _hideTimeout = null;
  }, totalDurationSeconds * 1000 + 900);
}
