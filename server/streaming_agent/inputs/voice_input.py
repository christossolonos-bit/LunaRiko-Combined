"""Voice input stream.

Listens to the microphone, records on detected speech, transcribes via
`server.asr.transcribe.transcribe_audio` (local faster-whisper or Groq),
and emits a 'voice' Event with the transcribed text.

Built on server/asr/recorder.py (the microphone + speech detector) and
server/asr/transcribe.py.
"""
import json
import logging
import os
import tempfile
import urllib.request
from queue import Queue
from typing import Callable, Optional

from server.asr.recorder import record_on_speech
from server.asr.transcribe import transcribe_audio
from server.config import ASR_PROMPT, BROWSER_INPUT_URL
from server.streaming_agent.event import Event
from server.streaming_agent.inputs.base_input import BaseInput
from server.streaming_agent.state import AgentState

log = logging.getLogger(__name__)


def _server_mic_muted() -> bool:
    """Respect the browser mic mute toggle (POST /mute on the FastAPI bridge)."""
    try:
        req = urllib.request.Request(
            f"{BROWSER_INPUT_URL.rstrip('/')}/mute", method="GET"
        )
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return bool(data.get("muted"))
    except Exception:
        return False


def _discord_vc_active() -> bool:
    """While Luna Bot is in Discord VC, desktop mic must not steal turns."""
    try:
        req = urllib.request.Request(
            f"{BROWSER_INPUT_URL.rstrip('/')}/discord_vc", method="GET"
        )
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return bool(data.get("active"))
    except Exception:
        return False


class VoiceInput(BaseInput):
    """Continuously listens for user speech and emits transcribed events."""

    def __init__(
        self,
        event_queue: Queue,
        # 16k: what Silero requires, and what Whisper downsamples to anyway —
        # recording higher only made the upload bigger.
        samplerate: int = 16000,
        channels: int = 1,
        silence_threshold: float = 0.02,
        silence_duration: float = 1.0,
        device: Optional[int] = None,
        audio_path: Optional[str] = None,
        prompt: str = ASR_PROMPT,   # character-aware (server/config.py)
        responding_event=None,
        state: Optional[AgentState] = None,
        transcribe_fn: Optional[Callable] = None,
    ):
        super().__init__(event_queue, name="VoiceInput", responding_event=responding_event)
        self.samplerate = samplerate
        self.channels = channels
        self.silence_threshold = silence_threshold
        self.silence_duration = silence_duration
        self.device = device
        self.audio_path = audio_path or os.path.join(
            tempfile.gettempdir(), f"streaming_agent_voice_{os.getpid()}.wav"
        )
        self.prompt = prompt
        # Optional state reference so we can flip voice_recording while the
        # mic is open. Without it, long utterances can let the silence-based
        # vision rule fire mid-speech (the user's voice ends up dropped).
        self.state = state
        # Speech-to-text, injected so a character can swap providers without
        # this file changing. Signature: (aud_path, prompt) -> str.
        # None means whatever ASR_BACKEND selects (local Whisper by default).
        self.transcribe_fn = transcribe_fn or transcribe_audio

    def run(self) -> None:
        log.info("[%s] entering listen loop (audio_path=%s)", self.name, self.audio_path)
        while not self._stop_event.is_set():
            # Pause before opening the mic when the AI is mid-response.
            self._wait_if_responding()
            if self._stop_event.is_set():
                break

            try:
                if _server_mic_muted() or _discord_vc_active():
                    # Mic mute OR Discord VC session — don't open the desktop recorder.
                    self._stop_event.wait(0.4)
                    continue

                # Blocks until speech is detected and ends with sustained silence.
                # record_on_speech deletes any prior file at this path.
                if self.state is not None:
                    self.state.set_voice_recording(True)
                try:
                    record_on_speech(
                        output_file=self.audio_path,
                        samplerate=self.samplerate,
                        channels=self.channels,
                        silence_threshold=self.silence_threshold,
                        silence_duration=self.silence_duration,
                        device=self.device,
                    )
                finally:
                    if self.state is not None:
                        self.state.set_voice_recording(False)
                if self._stop_event.is_set():
                    break

                if _server_mic_muted():
                    log.info("[%s] mic muted — discarding recording", self.name)
                    continue

                # If the AI started talking while we were recording, the audio
                # is most likely the AI's own TTS bleed-through — drop it.
                if self._is_responding():
                    log.info("[%s] AI is responding — discarding recording", self.name)
                    continue

                log.info("[%s] transcribing recorded audio...", self.name)
                text = self.transcribe_fn(aud_path=self.audio_path, prompt=self.prompt)
                if not text or not text.strip():
                    log.info("[%s] empty transcription, skipping emit", self.name)
                    continue

                if self._is_responding():
                    log.info("[%s] AI started responding mid-transcribe — dropping", self.name)
                    continue

                self.emit(Event(
                    type="voice",
                    content=text.strip(),
                    importance=1.0,
                    metadata={"audio_path": self.audio_path},
                ))
            except Exception:
                log.exception("[%s] error during listen/transcribe cycle", self.name)
