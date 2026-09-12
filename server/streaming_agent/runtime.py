"""AgentRuntime — the single object the conversation loop talks to.

Owns the world state, the event queue, every input thread, and the decision
engine. The whole API is four calls:

    agent = AgentRuntime()
    agent.start()

    while True:
        payload = agent.next_payload()       # blocks until a rule fires
        ...                                  # LLM + speech + avatar
        agent.end_response()                 # she is done; start listening again

    agent.stop()

While she is talking, `responding_event` is set and EVERY moving part pauses:
inputs stop producing, the engine stops draining and stops checking rules.
``end_response()`` throws away anything that snuck in, resets the timers, then
clears the event so listening resumes. Without that she hears her own voice
and answers herself.

ADDING AN INPUT
Write a class under `inputs/` that subclasses BaseInput and emits Events, then
add three lines here (a flag, a constructor call, an append). Nothing else in
the project needs to know it exists.
"""
from __future__ import annotations

import logging
import threading
from queue import Empty, Queue
from threading import Thread
from typing import Any, Callable, Dict, Optional

from server.config import BROWSER_INPUT_URL
from server.streaming_agent.decision_engine import DecisionEngine
from server.streaming_agent.inputs.browser_voice_input import BrowserVoiceInput
from server.streaming_agent.inputs.voice_input import VoiceInput
from server.streaming_agent.state import AgentState

log = logging.getLogger(__name__)


class AgentRuntime:
    def __init__(
        self,
        # The desktop microphone.
        enable_voice: bool = True,
        # The web page's mic button and text box, uploaded through the server.
        # Pass enable_voice=False alongside this or the mic is captured twice
        # — config.VOICE_SOURCE enforces that upstream.
        enable_browser_voice: bool = False,
        browser_input_url: str = BROWSER_INPUT_URL,
        # Seconds of silence that end your turn.
        silence_duration: float = 0.25,
        silence_threshold: float = 0.02,
        # Speech-to-text function. None means Groq Whisper. Same signature
        # either way: (aud_path, prompt) -> str.
        transcribe_fn: Optional[Callable] = None,
    ):
        self.event_queue: Queue = Queue()
        self.payload_queue: "Queue[Dict[str, Any]]" = Queue()
        self.responding_event = threading.Event()
        self.state = AgentState()

        self.engine = DecisionEngine(
            state=self.state,
            event_queue=self.event_queue,
            payload_sink=self.payload_queue.put,
            responding_event=self.responding_event,
        )

        self.inputs = []
        if enable_voice:
            self.inputs.append(VoiceInput(
                event_queue=self.event_queue,
                responding_event=self.responding_event,
                state=self.state,
                silence_duration=silence_duration,
                silence_threshold=silence_threshold,
                transcribe_fn=transcribe_fn,
            ))
        if enable_browser_voice:
            self.inputs.append(BrowserVoiceInput(
                event_queue=self.event_queue,
                base_url=browser_input_url,
                responding_event=self.responding_event,
                transcribe_fn=transcribe_fn,
            ))

        self._engine_thread: Optional[Thread] = None

    # ---- lifecycle -----------------------------------------------------
    def start(self) -> None:
        log.info("[AgentRuntime] starting %d input(s) + decision engine",
                 len(self.inputs))
        for inp in self.inputs:
            inp.start()
        self._engine_thread = Thread(
            target=self.engine.run, daemon=True, name="DecisionEngine",
        )
        self._engine_thread.start()

    def stop(self) -> None:
        log.info("[AgentRuntime] stopping")
        for inp in self.inputs:
            inp.stop()
        self.engine.stop()
        # Every thread is a daemon — the process exits cleanly.

    # ---- what the conversation loop uses -------------------------------
    def next_payload(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Block until a rule fires. Returns the payload for one turn.

        Once this returns, ``responding_event`` is already set: inputs and the
        engine are paused. The caller MUST call ``end_response()`` when she has
        finished talking.
        """
        return self.payload_queue.get(timeout=timeout)

    def end_response(self) -> None:
        """She is done talking. Drop stale events, reset timers, listen again."""
        # Anything that landed while she was speaking is almost certainly her
        # own voice bleeding into the mic. Inputs already refuse to emit while
        # responding; this is the belt to that pair of braces.
        dropped = 0
        while True:
            try:
                self.event_queue.get_nowait()
                dropped += 1
            except Empty:
                break
        if dropped:
            log.info("[AgentRuntime] dropped %d stale event(s)", dropped)

        self.state.mark_responded()
        self.responding_event.clear()
        log.info("[AgentRuntime] resumed — listening")
