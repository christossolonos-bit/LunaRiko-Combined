"""The control loop: when does she get to speak?

Drains events into AgentState, walks a short list of rules, and hands the
finished payload to whoever asked for it (the conversation loop). Today only
one rule fires: you finished talking. Everything else is context.

----------------------------------------------------------------------------
ADDING A NEW TRIGGER RULE
----------------------------------------------------------------------------
Each rule is a method ``self._rule_xxx(now: float) -> Optional[str]`` returning
a short trigger reason (e.g. ``"voice_input"``) when it should fire, else
``None``. To add a rule:

    1. Implement ``_rule_my_thing(self, now)`` on this class.
    2. Append it to ``self.rules`` in ``__init__``.

That's it. ``_should_respond`` walks the list in order and the first match
wins. Keep rules short and side-effect-free.
"""
import json
import logging
import threading
import time
from queue import Empty, Queue
from typing import Any, Callable, Dict, List, Optional

from server.streaming_agent.event import Event
from server.streaming_agent.payload_formatter import format_payload
from server.streaming_agent.state import AgentState

log = logging.getLogger(__name__)


COOLDOWN_SECONDS = 1.0          # minimum gap between two of her replies


def _default_payload_sink(payload: Dict[str, Any]) -> None:
    """Standalone-mode default: print the payload to stdout."""
    print("\n========== LLM INPUT ==========")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    print("===============================\n")


class DecisionEngine:
    """Single-threaded loop: drain events -> update state -> maybe respond.

    ``payload_sink``     - callable that receives each fired payload. Defaults
                           to printing. Pass ``queue.put`` to hand off to a
                           consumer (e.g. AgentRuntime).
    ``responding_event`` - optional threading.Event. When set, the engine
                           skips drain + rules entirely (the AI is talking).
                           When provided, the engine ALSO sets this event the
                           moment a response fires; the caller is responsible
                           for clearing it (and calling state.mark_responded)
                           when the AI is done. Standalone mode (event=None)
                           keeps the old "respond is instant" semantics.
    """

    def __init__(
        self,
        state: AgentState,
        event_queue: Queue,
        tick_interval: float = 0.2,
        payload_sink: Callable[[Dict[str, Any]], None] = _default_payload_sink,
        responding_event: Optional[threading.Event] = None,
    ):
        self.state = state
        self.event_queue = event_queue
        self.tick_interval = tick_interval
        self.payload_sink = payload_sink
        self.responding_event = responding_event
        self._stopped = False

        # Trigger rules. Order matters — first match wins.
        # See module docstring for how to add a new one.
        #
        # Only voice fires a reply. Anything else you add is context-only by
        # default: it shows up in system_context when voice triggers the turn,
        # but does not start one on its own.
        #
        # Worth knowing before you add a rule that fires on its own: an
        # earlier "speak up after N seconds of silence" rule kept stealing
        # turns — it fired during a long sentence, or during the second or two
        # spent transcribing, and she answered a question nobody had finished
        # asking. If you add one, gate it on state.voice_recording.
        self.rules: List[Callable[[float], Optional[str]]] = [
            self._rule_voice_input,
            self._rule_important_event,
        ]

    def stop(self) -> None:
        log.info("[DecisionEngine] stop requested")
        self._stopped = True

    def run(self) -> None:
        log.info("[DecisionEngine] running (tick=%.2fs, rules=%d)",
                 self.tick_interval, len(self.rules))
        while not self._stopped:
            # While the AI is talking, do nothing — no drain, no rules, no timers.
            if self.responding_event is not None and self.responding_event.is_set():
                time.sleep(self.tick_interval)
                continue
            try:
                self._drain_events()
                reason = self._should_respond()
                if reason:
                    self._respond(reason)
            except Exception:
                log.exception("[DecisionEngine] error in tick")
            time.sleep(self.tick_interval)
        log.info("[DecisionEngine] exited")

    # ---- internals ------------------------------------------------------

    def _drain_events(self) -> None:
        """Move every queued event into AgentState. Non-blocking."""
        while True:
            try:
                event: Event = self.event_queue.get_nowait()
            except Empty:
                return
            self.state.add_event(event)

    def _should_respond(self) -> Optional[str]:
        """Returns a trigger reason string when a rule fires, else None."""
        if self.state.time_since_last_response() < COOLDOWN_SECONDS:
            return None

        now = time.time()
        for rule in self.rules:
            reason = rule(now)
            if reason:
                log.info("[DecisionEngine] rule fired: %s", reason)
                return reason
        return None

    # ---- rules ----------------------------------------------------------
    # Each rule: (now) -> reason str | None.

    def _rule_voice_input(self, _now: float) -> Optional[str]:
        return "voice_input" if self.state.flags.get("new_voice_input") else None

    def _rule_important_event(self, _now: float) -> Optional[str]:
        return "important_event" if self.state.flags.get("important_event_detected") else None

    # ---- response -------------------------------------------------------

    def _respond(self, reason: str) -> None:
        """Build the NL payload, hand it to ``payload_sink``, mark as responded.

        Two operating modes:
          - Standalone (no ``responding_event``): mark_responded fires inline
            so the cooldown alone gates the next turn — the original behavior.
          - Wrapped (has ``responding_event``): set the event and let the
            caller decide when the response is "done" (real LLM + TTS take
            seconds). The caller MUST eventually clear the event and call
            ``state.mark_responded()`` — see AgentRuntime.end_response.
        """
        latest = self.state.latest_event()
        snapshot = self.state.snapshot()
        trigger = latest.to_dict() if latest else None
        payload = format_payload(snapshot, trigger, reason)

        log.info("[DecisionEngine] firing — reason=%s", reason)
        self.payload_sink(payload)

        if self.responding_event is not None:
            # Pause everything until the caller signals AI is done.
            self.responding_event.set()
        else:
            # Standalone mode: response is instant.
            self.state.mark_responded()
