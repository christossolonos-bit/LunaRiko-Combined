// Live video streaming: grab webcam frames at ~1fps, downscale, JPEG-compress,
// and POST each as a RAW JPEG body to `${HTTP_URL}/vision/frame`.
//
// The server relays the frame on to the Mac mini VLM (server/routes/vision_relay.py),
// so every client — desktop browser and phone alike — knows exactly one host.
// Body stays raw JPEG, no multipart, per the Mac mini contract (vision_handoff.md).
import { HTTP_URL } from '../config.js';

// ── Tunables ──────────────────────────────────────────────────────────
const FRAME_INTERVAL_MS = 1000;   // ~1fps
const FRAME_W = 320;              // frame size sent to the VLM — adjust here
const FRAME_H = 240;
const JPEG_QUALITY = 0.7;

let videoStream = null;
let videoElement = null;
let timer = null;
let frameCount = 0;

export function isVideoStreaming() {
  return timer !== null;
}

export async function startVideoStream() {
  if (timer) return; // already streaming

  videoElement = document.createElement('video');
  videoElement.autoplay = true;
  videoElement.playsInline = true;
  videoElement.style.display = 'none';
  document.body.appendChild(videoElement);

  videoStream = await navigator.mediaDevices.getUserMedia({ video: true });
  videoElement.srcObject = videoStream;
  await new Promise(resolve => { videoElement.onloadedmetadata = resolve; });

  const canvas = document.createElement('canvas');
  canvas.width = FRAME_W;
  canvas.height = FRAME_H;
  const ctx = canvas.getContext('2d');

  frameCount = 0;
  console.log(`[video] streaming started -> ${HTTP_URL}/vision/frame (${FRAME_INTERVAL_MS}ms interval)`);

  timer = setInterval(() => {
    ctx.drawImage(videoElement, 0, 0, FRAME_W, FRAME_H);
    canvas.toBlob(async (blob) => {
      if (!blob || !timer) return; // stopped while encoding
      try {
        // Raw JPEG body — the Mac mini vision server does not parse multipart.
        const res = await fetch(`${HTTP_URL}/vision/frame`, {
          method: 'POST',
          headers: { 'Content-Type': 'image/jpeg' },
          body: blob,
        });
        frameCount++;
        if (frameCount % 10 === 1) {  // log every 10th to avoid spam
          const data = await res.json();
          console.log(`[video] frame #${frameCount} sent (${blob.size} bytes)`, data);
        }
      } catch (err) {
        console.error('[video] frame upload failed:', err);
      }
    }, 'image/jpeg', JPEG_QUALITY);
  }, FRAME_INTERVAL_MS);
}

export function stopVideoStream() {
  if (timer) {
    clearInterval(timer);
    timer = null;
  }
  if (videoStream) {
    videoStream.getTracks().forEach(track => track.stop());
    videoStream = null;
  }
  if (videoElement) {
    videoElement.remove();
    videoElement = null;
  }
  console.log(`[video] streaming stopped after ${frameCount} frame(s)`);
}
