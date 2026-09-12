"""SoVITS TTS wrapper.

Single public function: `synthesize(text, emotion="relaxed") -> (Path, float)`.

Talks to the SoVITS HTTP API (started separately on :9880), writes the wav
to `client/audio/<uid>.wav` (where Vite can serve it to the browser), then
copies it to `<project_root>/audio/<uid>.wav` to match the v1 file layout.
Returns the *relative* path string that the FastAPI `/talk` endpoint expects
(e.g. `audio/<uid>.wav`) plus the audio duration in seconds.

Emotion is hardcoded to "relaxed" for now — the plan covers swapping that to
a picker or a static VRM→SoVITS map in a later milestone.

Public surface:
    synthesize(text: str, emotion: str = "relaxed") -> (Path, float)
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path, PurePosixPath
from typing import Optional, Tuple

import requests
import soundfile as sf

from server.config import (
    CLIENT_AUDIO_DIR,
    PUBLIC_AUDIO_DIR,
    SOVITS_EMOTION_CONFIG,
    SOVITS_PING_CONFIG,
    SOVITS_URL,
)


def _wav_duration(path: Path) -> float:
    """Read wav duration in seconds via soundfile."""
    with sf.SoundFile(path) as f:
        return len(f) / f.samplerate


def synthesize(text: str, emotion: str = "relaxed") -> Tuple[Path, float]:
    """Synthesize `text` to a wav file and return `(relative_path, duration)`.

    `relative_path` is what gets sent to FastAPI `/talk`'s `audio_path`. The
    browser resolves it against its origin (Vite serving `client/`), so the
    file is written to `client/audio/<uid>.wav` and the returned path is
    `audio/<uid>.wav`. A duplicate is also placed in `<project_root>/audio/`.
    """
    if emotion not in SOVITS_EMOTION_CONFIG:
        raise KeyError(
            f"unknown sovits emotion {emotion!r}; "
            f"valid: {list(SOVITS_EMOTION_CONFIG.keys())}"
        )

    CLIENT_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    PUBLIC_AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    filename = f"output_{uuid.uuid4().hex}.wav"
    client_out = CLIENT_AUDIO_DIR / filename
    public_out = PUBLIC_AUDIO_DIR / filename

    emo_cfg = SOVITS_EMOTION_CONFIG[emotion]
    payload = {
        "text": text,
        "text_lang": SOVITS_PING_CONFIG["text_lang"],
        "ref_audio_path": emo_cfg["audio_path"],
        "prompt_text": emo_cfg["audio_text"],
        "prompt_lang": SOVITS_PING_CONFIG["prompt_lang"],
        "aux_ref_audio_paths": SOVITS_PING_CONFIG.get("additional_aud", []),
        "speed_factor": 1.1,
    }

    print(f"[tts] POST {SOVITS_URL} emotion={emotion!r} len(text)={len(text)}")
    resp = requests.post(SOVITS_URL, json=payload)
    resp.raise_for_status()

    with open(client_out, "wb") as f:
        f.write(resp.content)
    shutil.copy2(client_out, public_out)

    duration = _wav_duration(public_out)
    # The path the FastAPI server / browser expects: relative `audio/<file>`.
    # PurePosixPath, not Path: this value is handed to the browser as a
    # URL. Path on Windows would make it "audio\\output_x.wav".
    rel_path = PurePosixPath("audio") / filename
    print(f"[tts] wrote {client_out.name} duration={duration:.2f}s rel={rel_path}")
    return rel_path, duration


# Anything shorter than this is treated as a failed generation — SoVITS
# occasionally returns a 0-second silent wav instead of erroring.
MIN_AUDIBLE_S = 0.1


def safe_synthesize(text: str, emotion: str = "relaxed") -> Optional[Tuple[Path, float]]:
    """`synthesize` that never raises: returns None on any failure.

    Wraps the unchanged `synthesize()` so one bad SoVITS generation (HTTP
    error, rejected input, or a zero-length silent wav) costs only that
    fragment instead of crashing the whole turn. Callers skip the fragment
    when this returns None. Failures are logged loudly with the input text.
    """
    try:
        rel_path, duration = synthesize(text, emotion)
    except Exception as e:
        print(f"[tts] SAFE-SKIP: synthesize raised {type(e).__name__}: {e} text={text!r}")
        return None
    if duration < MIN_AUDIBLE_S:
        print(f"[tts] SAFE-SKIP: near-zero duration ({duration:.3f}s) text={text!r}")
        return None
    return rel_path, duration


if __name__ == "__main__":
    # Standalone test: synthesize a short line and print the result.
    # Requires SoVITS server running on :9880.
    # Run with: python -m server.tts.sovits
    rel, dur = synthesize("Hi, this is a voice test. One, two, three.", "relaxed")
    print(f"[sovits.__main__] rel={rel}  duration={dur:.2f}s")
    on_disk = CLIENT_AUDIO_DIR / rel.name
    assert on_disk.exists(), f"missing client wav: {on_disk}"
    public_copy = PUBLIC_AUDIO_DIR / rel.name
    assert public_copy.exists(), f"missing public wav: {public_copy}"

    # safe_synthesize: success path returns a result, failure path returns
    # None instead of raising (exercised with an emotion key that the inner
    # synthesize() rejects).
    assert safe_synthesize("Safe wrapper check, one two three.") is not None
    assert safe_synthesize("boom", emotion="no-such-emotion") is None
    print("[sovits.__main__] OK")
