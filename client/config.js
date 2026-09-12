// Browser-side settings. This is the file to edit when you swap the model.

const _params = new URLSearchParams(location.search);

// ---------------------------------------------------------------------------
// Where the Python server is.
//
// THE PORT MUST MATCH `SERVER_PORT` IN server/config.py.
// The browser cannot read a Python file, so this is the one number that lives
// in two places. check_setup.bat compares them and tells you if they drift.
//
// Not 8000, which is the most contested port on a developer machine and is
// often squatted on Windows by something that looks like a system process.
// What to do if this one is taken too: the SERVER_PORT comment in
// server/config.py, or just run check_setup.bat (test 8).
//
// `?api=9999` on the URL overrides it for one page load, without editing
// anything — handy for testing a port before you commit to it.
// ---------------------------------------------------------------------------
const SERVER_PORT = Number(_params.get('api')) || 8100;

// The HOST is derived from whatever served this page, never hardcoded. That is
// what lets you open it from a phone, a tablet, or another PC (over your LAN or
// over Tailscale) with nothing to edit: the page loads from that machine's
// address, so it talks back to that same address. A hardcoded `localhost` would
// mean the phone itself.
// The one exception is the word "localhost" itself. On Windows it resolves to
// IPv6 (::1) before IPv4, and the server only listens on IPv4 — so the browser
// stalls on every request before falling back. 127.0.0.1 means the same machine
// and skips it. Anything else (a LAN address, a Tailscale name) is left alone.
const _host = location.hostname === 'localhost' ? '127.0.0.1' : location.hostname;
export const WS_URL   = `ws://${_host}:${SERVER_PORT}/ws`;
export const HTTP_URL = `http://${_host}:${SERVER_PORT}`;
console.log(`[config] server -> ${HTTP_URL}`);


// ---------------------------------------------------------------------------
// Which character.
//
// `?character=name` on the URL picks one. With no parameter the page asks the
// server which character IT launched as, so the two can never disagree and
// load different models.
//
// NOTE this is a top-level await, so every module that imports this file waits
// for it. Do not put DOM setup behind `DOMContentLoaded` in those modules —
// that event can fire while this fetch is still in flight, and the listener
// would never run. Register at module scope instead (see client/ui.js).
// ---------------------------------------------------------------------------
async function askServerCharacter() {
  try {
    const res = await fetch(`${HTTP_URL}/health`, { signal: AbortSignal.timeout(2000) });
    const { character } = await res.json();
    if (character) {
      console.log(`[config] server says character=${character}`);
      return String(character).toLowerCase();
    }
    console.warn('[config] /health returned no character — falling back to riko');
  } catch (e) {
    console.warn(`[config] could not reach the server (${e}) — falling back to riko`);
  }
  return 'riko';
}

const _urlChar = _params.get('character');
export const CHARACTER = _urlChar ? _urlChar.toLowerCase() : await askServerCharacter();


// ---------------------------------------------------------------------------
// One entry per character. Everything a character owns lives in
// client/characters/<name>/ — her .vrm goes in models/.
//
// TO USE YOUR OWN MODEL:
//   1. Export it from VRoid Studio as VRM 1.0 (VRM 0.x will not load).
//   2. Drop the .vrm in client/characters/riko/models/.
//   3. Change vrm_path below to its filename.
//
// mouth_threshold is how loud the audio has to be before the mouth opens.
// Lower = mouth moves more. 5-8 is the usual range; tune it by ear.
// ---------------------------------------------------------------------------
export const CHARACTER_DEFAULTS = {
  riko: {
    vrm_path: './characters/riko/models/Riko1.vrm',
    mouth_threshold: 7,
    // From T-pose rest → arms at sides (VRM 1.0).
    standingArms: { leftUpper: [0, 0, -1.2], rightUpper: [0, 0, 1.2] },
    backdrop: {
      url: './backgrounds/riko_bedroom.png',
      height: 4.4,
      position: [0, 1.4, -2.5],
    },
  },
  luna: {
    vrm_path: './characters/luna/models/luna_1.0.vrm',
    mouth_threshold: 7,
    // Luna 1.0 T-pose — same arms-down as Riko.
    standingArms: { leftUpper: [0, 0, -1.2], rightUpper: [0, 0, 1.2] },
    // Lobby sunlight still (from Lounge music folder).
    backdrop: {
      url: './backgrounds/lobby_sunlight.jpeg',
      height: 4.6,
      position: [0, 1.4, -2.5],
    },
    camera: {
      position: [0, 1.08, 1.62],
      target: [0, 0.98, 0],
      fov: 30,
    },
  },
};

const _char = CHARACTER_DEFAULTS[CHARACTER] || CHARACTER_DEFAULTS.riko;
console.log(`[config] character=${CHARACTER} vrm=${_char.vrm_path} mouth=${_char.mouth_threshold}`);

export const VRM_PATH = _char.vrm_path;
export const MOUTH_THRESHOLD = _char.mouth_threshold;
export const BACKDROP = _char.backdrop || null;
export const CAMERA_DEFAULT = _char.camera || null;

// Optional live tune: ?arms=-2.55,2.55  (leftUpper.z, rightUpper.z)
const _armsParam = _params.get('arms');
const _baseArms = _char.standingArms || { leftUpper: [0, 0, -1.2], rightUpper: [0, 0, 1.2] };
if (_armsParam) {
  const [lz, rz] = _armsParam.split(',').map(Number);
  if (Number.isFinite(lz) && Number.isFinite(rz)) {
    _baseArms.leftUpper = [0, 0, lz];
    _baseArms.rightUpper = [0, 0, rz];
    console.log(`[config] standingArms override from ?arms=`, _baseArms);
  }
}
export const STANDING_ARMS = _baseArms;


// ---------------------------------------------------------------------------
// Transparent mode — the page running inside the Electron desktop window
// (desktop/main.js), which has a real alpha channel. No browser can do that,
// which is the only reason that shell exists.
//
// Set by `?transparent=1`. The 3D canvas is already transparent; this just
// clears the page's white background and hides the on-screen buttons. Opening
// http://127.0.0.1:5173/ normally is completely unaffected.
// ---------------------------------------------------------------------------
export const TRANSPARENT_MODE = _params.get('transparent') === '1';


// ---------------------------------------------------------------------------
// Rendering cost. Both 0 means "off": full resolution, as many frames as the
// display allows. Turn them down if your GPU is struggling, or if you are
// viewing on a phone and it runs hot.
//
//   pixelRatioCap  ceiling on the display's pixel ratio. A phone is ~2.6-3.0,
//                  so 2 is a mild trim and 1.5 is the big saving. Costs
//                  sharpness and nothing else.
//   maxFps         frame budget. 30 roughly halves GPU work — hard to notice
//                  on a talking avatar, easy to notice while orbiting the
//                  camera. Ignored in VR (the headset sets the rate).
// ---------------------------------------------------------------------------
export const RENDER = { pixelRatioCap: 0, maxFps: 0 };
console.log(`[config] render=${JSON.stringify(RENDER)}`);


// ---------------------------------------------------------------------------
// VR (WebXR). Works out of the box with a Quest over Link or Air Link — open
// this page in the headset's browser and press Enter VR.
// ---------------------------------------------------------------------------
export const VR_CONFIG = {
  // Where you stand, [x, y, z] in metres. If she looks too small, too far, or
  // too low when you put the headset on, THIS is the knob. Y is your eye
  // height, Z is how far away you are.
  dollyPosition: [0, 0.43, 0.8],

  // How close your hand has to get to count as a touch (metres).
  touchRadius: 0.09,

  // The same, while the trigger is held — a longer reach.
  triggerTouchRadius: 0.25,

  // Minimum gap between two touches from the same hand (ms).
  touchCooldown: 2500,

  // Controller rumble on a touch.
  hapticIntensity: 0.8,
  hapticDuration: 150,
};
