"""sound_effects.py — the wrapper behind the base registry's `play_sound_effect`.

Plays a one-off sound effect out of the BROWSER's speakers. Hard-wired to a
single effect for now (the alarm), so the tool takes no arguments at all.

It does NOT go through `/talk`. That endpoint owns the persistent `<audio>`
element, which is both the lipsync source and a strict FIFO — routing a sound
effect through it would flap the avatar's mouth to a siren and push her spoken
reply behind the whole 4-second clip. `/play_sfx` broadcasts its own WS message
and the browser plays it on a throwaway `<audio>` element instead, so the effect
sounds *over* whatever is happening and costs the turn nothing.

Returns the agent-standard {"ok": bool, "message": str} shape and never raises.

Run standalone (needs the server running + a browser open at :5173):
    .venv/bin/python -m server.agent_registries.base_tools.sound_effects
"""

from __future__ import annotations

import asyncio

from server.config import CLIENT_ROOT
from server.vrm.vrm_client import play_sfx

# Browser-relative, NOT a filesystem path — Vite serves `client/`, so this is
# what the browser fetches. An absolute path here silently plays nothing.
ALARM_REL_PATH = "audio/sfx/alarm.wav"


async def play_alarm() -> dict:
    """Tool handler — sound the alarm in the browser. Takes no arguments."""
    clip = CLIENT_ROOT / ALARM_REL_PATH
    print(f"[sfx] play_alarm() -> {ALARM_REL_PATH}")
    if not clip.is_file():
        print(f"[sfx] MISSING clip on disk: {clip}")
        return {"ok": False, "message": f"The alarm sound file is missing ({clip})."}
    try:
        # The POST is blocking (requests); keep it off the event loop.
        await asyncio.to_thread(play_sfx, ALARM_REL_PATH)
        return {"ok": True, "message": "Alarm sound effect played."}
    except Exception as e:  # tool standard: report, don't raise
        print(f"[sfx] ERROR: {type(e).__name__}: {e}")
        return {"ok": False, "message": f"Couldn't play the alarm ({type(e).__name__}: {e})."}


if __name__ == "__main__":
    # The clip must exist and the path must stay browser-relative — those are
    # the two ways this silently does nothing.
    assert not ALARM_REL_PATH.startswith("/"), "path must be browser-relative"
    assert (CLIENT_ROOT / ALARM_REL_PATH).is_file(), \
        f"missing clip: {CLIENT_ROOT / ALARM_REL_PATH}"

    result = asyncio.run(play_alarm())
    print(f"[sound_effects.__main__] ok={result['ok']} — {result['message']}")
    assert result["ok"], "is the server up, with a browser open at :5173?"
