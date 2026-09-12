"""HTTP wrappers for this project's server (config.VRM_BASE_URL).

Pure I/O — no logic. Same payload shapes as `vrm_ping.py`. Anything that
needs to drive the avatar imports from here; no other module touches the
VRM HTTP API directly.

────────────────────────────────────────────────────────────────────────
PATH FORMAT — IMPORTANT
────────────────────────────────────────────────────────────────────────
`audio_path` and `animation_url` are NOT filesystem paths. The FastAPI
server forwards them over WebSocket to the browser, which fetches them
over HTTP from its own origin (Vite serves the `client/` directory). So
they must be RELATIVE paths from `client/`, e.g.:

    "animations/mixamo/Backflip.fbx"   ✓ browser fetches this URL
    "audio/output_<uid>.wav"           ✓ browser fetches this URL
    "/home/.../Backflip.fbx"           ✗ silently fails (animation never plays)

Use `server.config.MIXAMO_REL_DIR` for animation URLs and the relative
path returned by `server.tts.sovits.synthesize()` for audio paths. Never
construct these from `MIXAMO_DIR` (the absolute path) or `Path.absolute()`.

Public surface:
    vrm_talk(audio_path, expression, audio_text, audio_duration)
    vrm_animate(animate_type, animation_url, play_once=False,
                crop_start=0.0, crop_end=0.0,
                lock_position=False, track_position=True)
    set_state(state)            # "idle" | "listening" | "thinking" | "talking"
    play_sfx(audio_path)        # one-off sound effect, off the speech channel
    vrm_walk_to(x, y, z, speed=1.5)
    vrm_stop_movement()
    vrm_teleport_to(x, y, z)

Known issue:
    `track_position=True` on Mixamo emotes (Backflip, for one) is
    unreliable — the model can slide back to where it started once the
    animation ends.
"""

from __future__ import annotations

from typing import Any, Dict

import requests

from server.config import VRM_BASE_URL


def _post(endpoint: str, payload: Dict[str, Any], timeout: float | None = None) -> Dict[str, Any]:
    url = f"{VRM_BASE_URL}{endpoint}"
    resp = requests.post(url, json=payload, timeout=timeout)
    print(f"[vrm] POST {endpoint} status={resp.status_code}")
    resp.raise_for_status()
    return resp.json()


def vrm_talk(
    audio_path: str,
    expression: str,
    audio_text: str,
    audio_duration: float,
) -> Dict[str, Any]:
    """Trigger TTS playback + lip-sync on the avatar.

    `audio_path` must be a browser-relative path, e.g. `audio/output.wav`.
    """
    # Note: server-side field is misspelled `audio_duraction` (legacy);
    # we keep that exact key on the wire to match `server_reference.py`.
    return _post(
        "/talk",
        {
            "audio_path": audio_path,
            "expression": expression,
            "audio_text": audio_text,
            "audio_duraction": int(audio_duration),
        },
    )


def vrm_animate(
    animate_type: str,
    animation_url: str,
    play_once: bool = False,
    crop_start: float = 0.0,
    crop_end: float = 0.0,
    lock_position: bool = False,
    track_position: bool = True,
) -> Dict[str, Any]:
    """Trigger an animation.

    `animate_type` is `"start_vrma"` / `"start_mixamo"` / `"auto"` (server
    auto-detects from extension).

    `animation_url` must be a browser-relative path, e.g.
    `animations/mixamo/Backflip.fbx`.
    """
    return _post(
        "/animate",
        {
            "animate_type": animate_type,
            "animation_url": animation_url,
            "play_once": play_once,
            "crop_start": crop_start,
            "crop_end": crop_end,
            "lock_position": lock_position,
            "track_position": track_position,
        },
    )


def set_state(state: str) -> Dict[str, Any]:
    """Set the avatar's high-level state: idle, listening, thinking, talking."""
    return _post("/set_state", {"state": state})


def play_sfx(audio_path: str) -> Dict[str, Any]:
    """Play a one-off sound effect in the browser. No lipsync, no queueing.

    `audio_path` must be browser-relative, e.g. `audio/sfx/alarm.wav`.
    """
    return _post("/play_sfx", {"audio_path": audio_path})


def vrm_walk_to(x: float, y: float, z: float, speed: float = 1.5) -> Dict[str, Any]:
    """Walk the character to a world-space position."""
    return _post("/walk_to", {"x": x, "y": y, "z": z, "speed": speed})


def vrm_stop_movement() -> Dict[str, Any]:
    """Stop any in-progress walking and return to idle."""
    return _post("/stop_movement", {})


def vrm_teleport_to(x: float, y: float, z: float) -> Dict[str, Any]:
    """Instantly teleport the character to a world-space position."""
    return _post("/teleport_to", {"x": x, "y": y, "z": z})


if __name__ == "__main__":
    # Standalone smoke test. Requires the server to be running.
    # Run with: python -m server.vrm.vrm_client
    import time
    from pathlib import Path

    from server.config import MIXAMO_REL_DIR, VRMA_REL_DIR

    # Cycle through avatar states.
    # for s in ("idle", "listening", "thinking", "talking", "idle"):
    #     print(f"[vrm_client.__main__] set_state({s!r})")
    #     set_state(s)
    #     time.sleep(20.5)


    set_state("talking")

    # Trigger a Mixamo emote using the BROWSER-RELATIVE path.
    # backflip = MIXAMO_REL_DIR / "Backflip.fbx"
    # print(f"[vrm_client.__main__] vrm_animate('start_mixamo', {str(backflip)!r})")
    # vrm_animate("start_mixamo", str(backflip), play_once=True, track_position=True)
    # time.sleep(0.8)


    # backflip = MIXAMO_REL_DIR / "Idle.fbx"
    # print(f"[vrm_client.__main__] vrm_animate('start_mixamo', {str(backflip)!r})")
    # vrm_animate("start_mixamo", str(backflip), play_once=True, track_position=True)
    # time.sleep(2)

    # backflip = MIXAMO_REL_DIR / "Flying Kick.fbx"
    # print(f"[vrm_client.__main__] vrm_animate('start_mixamo', {str(backflip)!r})")
    # vrm_animate("start_mixamo", str(backflip), play_once=True, track_position=True)
    # time.sleep(10)
    # print("[vrm_client.__main__] OK")




    # backflip = VRMA_REL_DIR / "shikanoko.vrma"
    # print(f"[vrm_client.__main__] vrm_animate('start_vrma', {str(backflip)!r})")
    # vrm_animate("start_vrma", str(backflip), play_once=True, track_position=True)
    # time.sleep(3)
    # print("[vrm_client.__main__] OK")


    ###

    # backflip = MIXAMO_REL_DIR / "Backflip.fbx"
    # print(f"[vrm_client.__main__] vrm_animate('start_mixamo', {str(backflip)!r})")
    # vrm_animate("start_mixamo", str(backflip), play_once=True, track_position=True)
    # time.sleep(10)

    # vrm_walk_to(-5.0,0.0,0.0)
    # #vrm_teleport_to(-5.0,0.0,0.0)
    # time.sleep(9)

    # backflip = MIXAMO_REL_DIR / "Backflip.fbx"
    # print(f"[vrm_client.__main__] vrm_animate('start_mixamo', {str(backflip)!r})")
    # vrm_animate("start_mixamo", str(backflip), play_once=True, track_position=True)
    # time.sleep(3)

    # backflip = MIXAMO_REL_DIR / "Flying Kick.fbx"
    # print(f"[vrm_client.__main__] vrm_animate('start_mixamo', {str(backflip)!r})")
    # vrm_animate("start_mixamo", str(backflip), play_once=True, track_position=True)
    # time.sleep(3)


    # backflip = VRMA_REL_DIR / "shikanoko.vrma"
    # print(f"[vrm_client.__main__] vrm_animate('start_vrma', {str(backflip)!r})")
    # vrm_animate("start_vrma", str(backflip), play_once=True, track_position=True)
    # time.sleep(3)
    # print("[vrm_client.__main__] OK")

