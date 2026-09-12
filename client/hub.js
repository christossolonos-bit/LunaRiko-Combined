/**
 * Desktop hub — Fluffy-style dock for Electron transparent mode.
 * Chat / mute / pin / click-through / minimize / close.
 */
import { HTTP_URL, TRANSPARENT_MODE } from './config.js';

const desktop = typeof window !== 'undefined' ? window.lunaDesktop : null;

function $(id) {
  return document.getElementById(id);
}

async function sendChat(text) {
  const message = (text || '').trim();
  if (!message) return;
  await fetch(`${HTTP_URL}/send_message`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message_text: message, reply_audio: 'both' }),
  });
}

async function setMute(muted) {
  await fetch(`${HTTP_URL}/mute`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ muted }),
  });
  const mainMic = $('mic-button');
  if (mainMic) {
    mainMic.title = muted ? 'Unmute microphone' : 'Mute microphone';
  }
}

function isInteractiveTarget(el) {
  return Boolean(el?.closest?.('#hub-dock, #hub-chat-panel'));
}

function setupClickThroughHover() {
  if (!desktop?.setIgnoreMouse) return;
  const onMove = (e) => {
    const over = isInteractiveTarget(e.target);
    desktop.setIgnoreMouse(!over);
  };
  window.addEventListener('mousemove', onMove, { passive: true });
}

const HUB_PLACE_KEY = 'riko.overlay.hub';

function placeChatNearDock(dock, panel) {
  if (!panel || panel.classList.contains('hidden')) return;
  const rect = dock.getBoundingClientRect();
  panel.style.transform = 'translateX(-50%)';
  panel.style.left = `${rect.left + rect.width / 2}px`;
  panel.style.bottom = 'auto';
  panel.style.top = `${Math.max(12, rect.top - panel.offsetHeight - 12)}px`;
}

function restoreHubPosition(dock, panel) {
  try {
    const saved = JSON.parse(localStorage.getItem(HUB_PLACE_KEY) || 'null');
    if (!saved || !Number.isFinite(saved.left) || !Number.isFinite(saved.top)) return;
    dock.style.transform = 'translateX(-50%)';
    dock.style.bottom = 'auto';
    dock.style.left = `${saved.left}px`;
    dock.style.top = `${saved.top}px`;
    placeChatNearDock(dock, panel);
  } catch {
    /* ignore */
  }
}

function setupDockDrag(dock, handle, panel) {
  let dragging = false;
  let startX = 0;
  let startY = 0;
  let origLeft = 0;
  let origTop = 0;
  let origOffX = 0;
  let origOffY = 0;

  handle.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    dragging = true;
    dock.classList.add('dragging');
    handle.setPointerCapture(e.pointerId);
    const rect = dock.getBoundingClientRect();
    startX = e.clientX;
    startY = e.clientY;
    // left is the dock center (paired with translateX(-50%)).
    origLeft = rect.left + rect.width / 2;
    origTop = rect.top;
    const off = window.lunaAvatar?.getOffset?.() || { x: 0, y: 0 };
    origOffX = off.x;
    origOffY = off.y;
    dock.style.transform = 'translateX(-50%)';
    dock.style.left = `${origLeft}px`;
    dock.style.top = `${origTop}px`;
    dock.style.bottom = 'auto';
  });

  handle.addEventListener('pointermove', (e) => {
    if (!dragging) return;
    const dx = e.clientX - startX;
    const dy = e.clientY - startY;
    dock.style.left = `${origLeft + dx}px`;
    dock.style.top = `${origTop + dy}px`;
    // Keep the VRM locked to the hub — same pixel delta.
    window.lunaAvatar?.setOffset?.(origOffX + dx, origOffY + dy);
    placeChatNearDock(dock, panel);
  });

  const end = (e) => {
    if (!dragging) return;
    dragging = false;
    dock.classList.remove('dragging');
    try {
      handle.releasePointerCapture(e.pointerId);
    } catch {
      /* ignore */
    }
    const rect = dock.getBoundingClientRect();
    localStorage.setItem(HUB_PLACE_KEY, JSON.stringify({
      left: rect.left + rect.width / 2,
      top: rect.top,
    }));
    const off = window.lunaAvatar?.getOffset?.();
    if (off) window.lunaAvatar.setOffset(off.x, off.y, { persist: true });
  };
  handle.addEventListener('pointerup', end);
  handle.addEventListener('pointercancel', end);
}

async function initHub() {
  if (!TRANSPARENT_MODE) return;

  const dock = $('hub-dock');
  const panel = $('hub-chat-panel');
  if (!dock) return;

  dock.hidden = false;
  document.body.classList.add('has-hub');

  const btnChat = $('hub-chat');
  const btnMic = $('hub-mic');
  const btnMonitor = $('hub-monitor');
  const btnMonitorLabel = $('hub-monitor-label');
  const btnPin = $('hub-pin');
  const btnClick = $('hub-clickthrough');
  const btnMin = $('hub-minimize');
  const btnClose = $('hub-close');
  const chatInput = $('hub-chat-input');
  const chatSend = $('hub-chat-send');
  const chatClose = $('hub-chat-close');

  let chatOpen = false;
  let micMuted = false;
  let clickThrough = false;

  restoreHubPosition(dock, panel);
  setupDockDrag(dock, $('hub-handle'), panel);
  window.addEventListener('luna-avatar-ready', () => {
    // Avatar API ready — placement already restored in app.js; keep hub in sync.
    restoreHubPosition(dock, panel);
  });

  const updateMonitorLabel = (info) => {
    if (!btnMonitor || !btnMonitorLabel) return;
    const count = info?.displayCount ?? 1;
    const label = info?.label ?? 1;
    btnMonitorLabel.textContent = String(label);
    btnMonitor.title = count > 1
      ? `Monitor ${label}/${count} — click to switch`
      : 'Only one monitor';
    btnMonitor.classList.toggle('hidden', count < 2);
  };

  try {
    const info = await desktop?.getDisplay?.();
    updateMonitorLabel(info);
  } catch {
    updateMonitorLabel({ label: 1, displayCount: 1 });
  }
  desktop?.onDisplayChanged?.(updateMonitorLabel);

  btnMonitor?.addEventListener('click', async () => {
    try {
      const info = await desktop?.cycleDisplay?.();
      updateMonitorLabel(info);
      // Three.js canvas size follows the new monitor.
      window.dispatchEvent(new Event('resize'));
    } catch (err) {
      console.error('[hub] cycle display failed', err);
    }
  });

  const setChatOpen = (open) => {
    chatOpen = open;
    panel?.classList.toggle('hidden', !open);
    btnChat?.setAttribute('aria-pressed', String(open));
    if (open) {
      placeChatNearDock(dock, panel);
      chatInput?.focus();
    }
  };

  btnChat?.addEventListener('click', () => setChatOpen(!chatOpen));
  chatClose?.addEventListener('click', () => setChatOpen(false));

  const doSend = async () => {
    const text = chatInput?.value || '';
    chatInput.value = '';
    try {
      await sendChat(text);
    } catch (err) {
      console.error('[hub] send failed', err);
    }
  };
  chatSend?.addEventListener('click', doSend);
  chatInput?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      doSend();
    }
  });

  btnMic?.addEventListener('click', async () => {
    micMuted = !micMuted;
    btnMic.setAttribute('aria-pressed', String(micMuted));
    btnMic.textContent = micMuted ? '🔇' : '🎤';
    try {
      await setMute(micMuted);
    } catch (err) {
      console.error('[hub] mute failed', err);
    }
  });

  btnPin?.setAttribute('aria-pressed', 'true');
  btnPin?.classList.add('hub-btn-active');
  btnPin.title = 'Always on top (locked)';
  btnPin?.addEventListener('click', async () => {
    // Locked on — keep asserting rather than toggling off.
    btnPin.setAttribute('aria-pressed', 'true');
    btnPin.classList.add('hub-btn-active');
    try {
      await desktop?.setAlwaysOnTop?.(true);
    } catch (err) {
      console.error('[hub] pin failed', err);
    }
  });

  btnClick?.addEventListener('click', async () => {
    clickThrough = !clickThrough;
    btnClick.setAttribute('aria-pressed', String(clickThrough));
    btnClick.classList.toggle('hub-btn-active', clickThrough);
    try {
      await desktop?.setClickThrough?.(clickThrough);
      if (clickThrough) setupClickThroughHover();
    } catch (err) {
      console.error('[hub] click-through failed', err);
    }
  });

  btnMin?.addEventListener('click', () => desktop?.minimize?.());
  btnClose?.addEventListener('click', () => desktop?.close?.());

  console.log('[hub] desktop dock ready', { hasElectronApi: Boolean(desktop) });
}

initHub().catch((err) => console.error('[hub] init failed', err));
