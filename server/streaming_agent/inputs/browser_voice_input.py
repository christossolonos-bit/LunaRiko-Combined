"""Browser input stream — the web page's mic button and text box.

Polls the FastAPI bridge's `/pending_voice` + `/pending_text` buffers
(filled by the browser UI via routes/browser_input.py) and emits a `voice`
Event when either version counter advances:

  - pending_voice: the mic-button clip. The buffer carries a file PATH
    (both processes share the filesystem), which we transcribe with the
    injected `transcribe_fn` — same contract as VoiceInput, so Riko gets
    Groq Whisper by default, and a character can swap it with zero
    changes here.
  - pending_text: the typed message. Emitted directly as a voice event —
    a typed turn and a spoken turn are the same thing to the decision
    engine (`_rule_voice_input` fires either way).

Mirrors VisionInput's poll-on-version-change pattern: stdlib urllib only,
first successful poll initializes without firing so stale uploads from
before launch are never replayed.
"""
import json
import logging
import urllib.error
import urllib.request
from queue import Queue
from typing import Callable, Optional

from server.asr.transcribe import transcribe_audio
from server.config import ASR_PROMPT, BROWSER_INPUT_URL
from server.streaming_agent.event import Event
from server.streaming_agent.inputs.base_input import BaseInput

log = logging.getLogger(__name__)


def _server_mic_muted(base_url: str) -> bool:
    try:
        req = urllib.request.Request(f"{base_url.rstrip('/')}/mute", method="GET")
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return bool(data.get("muted"))
    except Exception:
        return False


class BrowserVoiceInput(BaseInput):
    """Polls the server's browser-input buffers and emits voice events."""

    def __init__(
        self,
        event_queue: Queue,
        base_url: str = BROWSER_INPUT_URL,
        poll_interval: float = 0.5,
        request_timeout: float = 2.0,
        prompt: str = ASR_PROMPT,   # character-aware (server/config.py)
        responding_event=None,
        transcribe_fn: Optional[Callable] = None,
    ):
        super().__init__(event_queue, name="BrowserVoiceInput",
                         responding_event=responding_event)
        self.base_url = base_url.rstrip("/")
        self.poll_interval = poll_interval
        self.request_timeout = request_timeout
        self.prompt = prompt
        # Same injected-provider contract as VoiceInput: (aud_path, prompt) -> str.
        self.transcribe_fn = transcribe_fn or transcribe_audio
        self._voice_version: Optional[int] = None
        self._text_version: Optional[int] = None

    def run(self) -> None:
        log.info("[%s] polling %s/pending_voice + /pending_text every %.1fs",
                 self.name, self.base_url, self.poll_interval)
        while not self._stop_event.is_set():
            # Pause polling while the AI is responding.
            self._wait_if_responding()
            if self._stop_event.is_set():
                break

            try:
                self._check_voice()
                self._check_text()
            except urllib.error.URLError as e:
                log.warning("[%s] fetch failed (%s) — is the server down? "
                            "will retry", self.name, e)
            except Exception:
                log.exception("[%s] unexpected error during poll", self.name)

            self._sleep()

    # ---- pollers --------------------------------------------------------

    def _check_voice(self) -> None:
        snap = self._fetch("/pending_voice")
        version = int(snap.get("version", 0))
        if self._voice_version is None:
            self._voice_version = version
            log.info("[%s] connected (initial voice version=%d)",
                     self.name, version)
            return
        if version == self._voice_version:
            return

        # Advance first so a failed transcription isn't retried forever.
        self._voice_version = version
        path = snap.get("path")
        # A picture staged in the client's tray and sent with the clip — same
        # `image_path` contract as the typed path below.
        image_path = (snap.get("image_path") or "").strip()
        if not path or self._is_responding():
            return
        if _server_mic_muted(self.base_url):
            log.info("[%s] mic muted — ignoring browser voice clip", self.name)
            return
        # Browser mic always works — even while Discord VC is joined.
        # (Desktop mic stays quiet in VC so it does not steal Discord turns.)

        log.info("[%s] new voice clip (v%d) — transcribing %s%s",
                 self.name, version, path,
                 f" (+image {image_path})" if image_path else "")
        text = self.transcribe_fn(aud_path=path, prompt=self.prompt)
        if not text or not text.strip():
            log.info("[%s] empty transcription, skipping emit", self.name)
            return
        if self._is_responding():
            log.info("[%s] AI started responding mid-transcribe — dropping",
                     self.name)
            return
        self.emit(Event(
            type="voice",
            content=text.strip(),
            importance=1.0,
            metadata={
                "source": "browser_voice",
                "audio_path": path,
                "image_path": image_path,
                # Prefer webpage speaker for site mic turns (also OK in Discord VC).
                "reply_audio": "both",
            },
        ))

    def _check_text(self) -> None:
        snap = self._fetch("/pending_text")
        version = int(snap.get("version", 0))
        if self._text_version is None:
            self._text_version = version
            log.info("[%s] connected (initial text version=%d)",
                     self.name, version)
            return
        if version == self._text_version:
            return

        self._text_version = version
        text = (snap.get("text") or "").strip()
        image_path = (snap.get("image_path") or "").strip()
        reply_audio = (snap.get("reply_audio") or "both")
        if (not text and not image_path) or self._is_responding():
            return
        # Website typed turns always run — Discord VC must not swallow them.
        # Discord-bridge turns keep reply_audio=discord / [Discord VC] prefix.
        # Image with no message still makes a turn — the model sees the
        # image itself (run_turn attaches it as input_image), so the text
        # is just the marker image_attach also uses for history.
        self.emit(Event(
            type="voice",
            content=text or "[shared an image]",
            importance=1.0,
            metadata={
                "source": "browser_text",
                "image_path": image_path,
                "reply_audio": reply_audio,
            },
        ))

    # ---- helpers --------------------------------------------------------

    def _fetch(self, path: str) -> dict:
        req = urllib.request.Request(self.base_url + path, method="GET")
        with urllib.request.urlopen(req, timeout=self.request_timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _sleep(self) -> None:
        # Wakes early if stop() is called.
        self._stop_event.wait(self.poll_interval)


if __name__ == "__main__":
    # Standalone check: poll the real server buffers and print emitted events.
    # Start the FastAPI server first, then POST via the browser or curl:
    #   curl -X POST 127.0.0.1:<SERVER_PORT>/send_message \
    #        -H "Content-Type: application/json" -d '{"message_text":"hi"}'
    logging.basicConfig(level=logging.INFO)
    q: Queue = Queue()
    inp = BrowserVoiceInput(q)
    inp.start()
    print(f"[browser-voice-test] polling {BROWSER_INPUT_URL} — Ctrl-C to quit")
    try:
        while True:
            event = q.get()
            print(f"[browser-voice-test] EVENT: {event}")
    except KeyboardInterrupt:
        inp.stop()
