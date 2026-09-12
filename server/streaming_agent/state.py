"""What the agent knows about the world, gathered from every input.

The single source of truth the decision engine reads from. Deliberately
small: an input adds a field here, a rule in decision_engine.py reads it, and
a renderer in payload_formatter.py turns it into text for the prompt.
"""
import logging
import threading
from collections import deque
from time import time
from typing import Any, Dict, Optional

from server.streaming_agent.event import Event

log = logging.getLogger(__name__)


class AgentState:
    """Thread-safe container for the agent's awareness of the world."""

    def __init__(self, max_recent_events: int = 20):
        self._lock = threading.Lock()
        self.recent_events: deque = deque(maxlen=max_recent_events)
        self.last_response_time: float = 0.0
        # Seeded at construction so the "user has been silent for N seconds"
        # rule doesn't fire immediately on a fresh start.
        self.last_voice_event_time: float = time()

        self.flags: Dict[str, bool] = {
            "new_voice_input": False,
            "new_vision_input": False,
            "important_event_detected": False,
        }

        # True while the microphone is open. Set by VoiceInput, cleared in a
        # finally. No rule reads it today — it is here so a rule you add can
        # avoid firing in the middle of someone's sentence.
        self.voice_recording: bool = False

    # ---- mutation -------------------------------------------------------

    def add_event(self, event: Event) -> None:
        with self._lock:
            self.recent_events.append(event)
            if event.type == "voice":
                self.flags["new_voice_input"] = True
                self.last_voice_event_time = time()
            if event.type == "vision":
                self.flags["new_vision_input"] = True
            if event.importance >= 0.8:
                self.flags["important_event_detected"] = True

        log.info(
            "[State] +event type=%s importance=%.2f content=%r",
            event.type, event.importance, event.content,
        )

    def mark_responded(self) -> None:
        """Called when an AI response COMPLETES (LLM done, TTS finished).

        Resets the user-silence clock so any future silence-gated rule
        (none today; see decision_engine.py) gets a fresh window. An AI
        response counts as conversational activity, equivalent to a user
        voice event.
        """
        with self._lock:
            now = time()
            self.last_response_time = now
            self.last_voice_event_time = now
            self.flags["new_voice_input"] = False
            self.flags["new_vision_input"] = False
            self.flags["important_event_detected"] = False
        log.info("[State] response dispatched, flags cleared")

    # ---- read -----------------------------------------------------------

    def time_since_last_response(self) -> float:
        if not self.last_response_time:
            return float("inf")
        return time() - self.last_response_time

    def time_since_last_voice(self) -> float:
        return time() - self.last_voice_event_time

    def set_voice_recording(self, active: bool) -> None:
        """VoiceInput hook — flag True while record_on_speech is open."""
        with self._lock:
            self.voice_recording = bool(active)

    def latest_event(self) -> Optional[Event]:
        with self._lock:
            return self.recent_events[-1] if self.recent_events else None

    def snapshot(self) -> Dict[str, Any]:
        """Read-only copy of the state, safe to hand to the LLM context builder."""
        with self._lock:
            return {
                "recent_events": [e.to_dict() for e in self.recent_events],
                "flags": dict(self.flags),
                "last_response_time": self.last_response_time,
                "time_since_last_response": self.time_since_last_response(),
                "time_since_last_voice": self.time_since_last_voice(),
            }
