import { VRM_PATH, WS_URL }       from '../config.js';
import { showSubtitleStreaming } from './subtitles.js';

// Setup WebSocket
const ws = new WebSocket(WS_URL);
ws.onmessage = ({ data }) => {
  let msg;
  try {
    msg = JSON.parse(data);
  } catch {
    return;
  }

  if (msg.type === 'start_animation') {
    const { audio_text, audio_duraction } = msg;
    showSubtitleStreaming(audio_text, audio_duraction, "letter");
  }
};
