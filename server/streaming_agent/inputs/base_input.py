"""Abstract base class for all input streams.

Subclass this, implement `run()`, and emit `Event`s via `self.emit(...)`.
The base class handles thread lifecycle and queue plumbing so input
implementations can stay focused on their own producer logic.
"""
import logging
import threading
from abc import ABC, abstractmethod
from queue import Queue
from typing import Optional

from server.streaming_agent.event import Event

log = logging.getLogger(__name__)


class BaseInput(ABC):
    """Producer that pushes Events onto a shared queue from its own thread.

    Optional ``responding_event`` lets the orchestrator pause this input while
    the AI is talking. When the event is set, ``_wait_if_responding()`` blocks
    until it's cleared (or until stop is requested). Subclasses should call
    that helper at the top of each iteration AND drop any work-in-progress
    output if the event becomes set mid-cycle.
    """

    def __init__(
        self,
        event_queue: Queue,
        name: Optional[str] = None,
        responding_event: Optional[threading.Event] = None,
    ):
        self.event_queue = event_queue
        self.name = name or self.__class__.__name__
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.responding_event = responding_event

    @abstractmethod
    def run(self) -> None:
        """Main producer loop.

        Implementations should:
            * call ``self.emit(event)`` for each observation
            * periodically check ``self._stop_event.is_set()`` so ``stop()`` works
        """

    def emit(self, event: Event) -> None:
        log.info("[%s] emit -> type=%s importance=%.2f content=%r",
                 self.name, event.type, event.importance, event.content)
        self.event_queue.put(event)

    def _wait_if_responding(self, poll_interval: float = 0.2) -> None:
        """Block while the responding_event is set. Returns early on stop."""
        if not self.responding_event:
            return
        while self.responding_event.is_set() and not self._stop_event.is_set():
            # Sleep in small chunks so stop() is responsive.
            self._stop_event.wait(poll_interval)

    def _is_responding(self) -> bool:
        return bool(self.responding_event and self.responding_event.is_set())

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            log.warning("[%s] already running", self.name)
            return
        log.info("[%s] starting input thread", self.name)
        self._thread = threading.Thread(target=self._safe_run, daemon=True, name=self.name)
        self._thread.start()

    def stop(self) -> None:
        log.info("[%s] stop requested", self.name)
        self._stop_event.set()

    def _safe_run(self) -> None:
        try:
            self.run()
        except Exception:
            log.exception("[%s] input thread crashed", self.name)
        finally:
            log.info("[%s] input thread exited", self.name)
