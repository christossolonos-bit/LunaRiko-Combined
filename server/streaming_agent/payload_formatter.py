"""Turn the agent's world state into the payload one turn is built from.

This is the CONTEXT INJECTION SYSTEM. Anything she should know about but was
not told directly — what a camera sees, what a chat feed says, the weather,
the time — arrives here as one short block of plain English, once per turn.
It goes into the system prompt for that turn only and is never written to the
conversation history, so it cannot pile up.

Output shape:

    {
        "trigger_reason": "voice_input" | "important_event" | ...,
        "system_context": "<plain English, or empty>",
        "user_voice":     "<what you said>" or "[the user did not say anything]",
        "image_path":     "<image attached to this turn>" or "",
        "audio_path":     "<the wav you were recorded into>" or "",
    }

---------------------------------------------------------------------------
ADDING SOMETHING SHE SHOULD KNOW ABOUT
---------------------------------------------------------------------------
Three steps:

    1. Write `_render_<name>(snapshot, now) -> Optional[str]` below. It gets
       the whole world state and returns a short block of text, or None.
    2. Add it to MODALITY_RENDERERS.
    3. If something has to be POLLED to fill that state, write an input under
       `inputs/` (copy voice_input.py for the shape) and add it to
       AgentRuntime.

`_render_vision` below is a complete, working example of step 1 — nothing
feeds it in this package, so it always returns None, but wire an input that
emits `type="vision"` events and it starts producing.

---------------------------------------------------------------------------
THE ONE RULE
---------------------------------------------------------------------------
A renderer MUST return None when it has nothing to say. This is not style.
An earlier version printed its header unconditionally, so on a quiet desk
session she was told "this is your live chat summary" with no chat running —
and she spent the whole conversation convinced she was streaming. An idle
session must inject NOTHING AT ALL.

Check it yourself:  python -m server.streaming_agent.payload_formatter
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional


VISION_HEADER = "This is your vision, you do not have to respond."
NO_VOICE_PLACEHOLDER = "[the user did not say anything]"


# ---- helpers ---------------------------------------------------------------
def _human_age(seconds: float) -> str:
    """An age in seconds as 'just now' / 'N seconds ago' / 'N minutes ago'."""
    if seconds < 1.0:
        return "just now"
    if seconds < 60:
        n = int(seconds)
        return f"{n} second{'s' if n != 1 else ''} ago"
    minutes = int(seconds // 60)
    return f"{minutes} minute{'s' if minutes != 1 else ''} ago"


# ---- renderers -------------------------------------------------------------
# Each one gets the full world-state snapshot plus the current time, picks out
# what it cares about, and returns a short block of text — or None.

def _render_vision(snapshot: Dict[str, Any], now: float) -> Optional[str]:
    """EXAMPLE RENDERER — what a camera sees.

    Nothing in this package produces vision events, so this returns None on
    every turn and injects nothing. It is here as the working template: write
    an input that emits `Event(type="vision", content="a person waving")` and
    this starts feeding her descriptions with no other change.
    """
    events = snapshot.get("recent_events", [])
    vision = [e for e in events if e.get("type") == "vision"]
    if not vision:
        return None                      # <- THE RULE. Nothing seen, nothing said.
    lines = [VISION_HEADER]
    for e in vision:
        age = _human_age(max(0.0, now - float(e.get("timestamp", now))))
        lines.append(f"- {age}, you see {e.get('content', '')}")
    return "\n".join(lines)


# Order matters — sections appear in system_context in this order.
MODALITY_RENDERERS: List[Callable[[Dict[str, Any], float], Optional[str]]] = [
    _render_vision,
    # _render_chat,     <- your next one goes here
]


# ---- main entry ------------------------------------------------------------
def format_payload(
    snapshot: Dict[str, Any],
    trigger: Optional[Dict[str, Any]],
    reason: str,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Build one turn's payload from the world state and the triggering event.

    Args:
        snapshot: AgentState.snapshot() output.
        trigger:  the Event that fired the rule, as a dict (or None).
        reason:   which rule fired.
        now:      override the clock (for tests). Defaults to time.time().
    """
    now = now if now is not None else time.time()

    sections: List[str] = []
    for renderer in MODALITY_RENDERERS:
        section = renderer(snapshot, now)
        if section:
            sections.append(section)
    system_context = "\n\n".join(sections)

    # user_voice is only the immediate trigger, and only when that trigger is
    # voice. image_path and audio_path ride along in the trigger's metadata:
    # image_path is a picture uploaded from the web page (attached to this one
    # turn), audio_path is the wav the microphone recorded into. Both are
    # optional for every consumer.
    image_path = ""
    audio_path = ""
    reply_audio = "both"
    if trigger and trigger.get("type") == "voice":
        spoken = str(trigger.get("content", "")).strip()
        user_voice = spoken or NO_VOICE_PLACEHOLDER
        metadata = trigger.get("metadata") or {}
        image_path = str(metadata.get("image_path", "") or "")
        audio_path = str(metadata.get("audio_path", "") or "")
        reply_audio = str(metadata.get("reply_audio") or "both")
        if spoken.lstrip().startswith("[Discord voice") or spoken.lstrip().startswith("[Discord VC"):
            reply_audio = "discord"
    else:
        user_voice = NO_VOICE_PLACEHOLDER

    return {
        "trigger_reason": reason,
        "system_context": system_context,
        "user_voice":     user_voice,
        "image_path":     image_path,
        "audio_path":     audio_path,
        "reply_audio":    reply_audio,
    }


if __name__ == "__main__":
    # The empty case is the one that matters — see THE ONE RULE above.
    quiet = format_payload({}, {"type": "voice", "content": "hey"}, "voice_input")
    assert quiet["system_context"] == "", repr(quiet["system_context"])
    assert quiet["user_voice"] == "hey"
    print("[payload.__main__] idle session injects nothing OK")

    now = time.time()
    live = format_payload(
        {"recent_events": [{"type": "vision", "content": "an empty room",
                            "timestamp": now}]},
        {"type": "voice", "content": "what do you see?"}, "voice_input", now=now,
    )
    assert VISION_HEADER in live["system_context"]
    assert "an empty room" in live["system_context"]
    print("[payload.__main__] example vision renderer produces a section OK")

    silent = format_payload({}, None, "idle")
    assert silent["user_voice"] == NO_VOICE_PLACEHOLDER
    print("[payload.__main__] all OK")
