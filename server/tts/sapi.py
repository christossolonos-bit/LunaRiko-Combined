"""Offline Windows SAPI TTS via pyttsx3. Completely local — no network.

Chosen by `tts_backend: sapi` in the character yaml. Uses the voices already
installed on Windows (Zira / David / Haruka, etc.).

Public surface matches fish/sovits/edge:

    synthesize(text: str, emotion: str = "relaxed") -> (Path, float)
    safe_synthesize(text, emotion) -> (Path, float) | None
"""

from __future__ import annotations

import shutil
import tempfile
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Optional, Tuple

import soundfile as sf

from server.config import (
    CLIENT_AUDIO_DIR,
    PUBLIC_AUDIO_DIR,
    SAPI_TTS_RATE,
    SAPI_TTS_VOICE,
)

MIN_AUDIBLE_S = 0.1
_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        import pyttsx3

        _engine = pyttsx3.init()
        voices = _engine.getProperty("voices") or []
        chosen = None
        needle = (SAPI_TTS_VOICE or "").strip().lower()
        if needle:
            for v in voices:
                name = (getattr(v, "name", "") or "").lower()
                vid = (getattr(v, "id", "") or "").lower()
                if needle in name or needle in vid:
                    chosen = v.id
                    break
        if chosen is None:
            # Prefer a female English voice when nothing matched.
            for v in voices:
                name = (getattr(v, "name", "") or "").lower()
                if "zira" in name or "female" in name:
                    chosen = v.id
                    break
        if chosen is None and voices:
            chosen = voices[0].id
        if chosen:
            _engine.setProperty("voice", chosen)
        # pyttsx3 rate: ~200 is default. Our yaml uses a relative int offset.
        try:
            base = int(_engine.getProperty("rate") or 200)
            _engine.setProperty("rate", base + int(SAPI_TTS_RATE))
        except Exception:
            pass
        print(
            f"[sapi_tts] ready voice={chosen!r} "
            f"(wanted {SAPI_TTS_VOICE!r}) rate_offset={SAPI_TTS_RATE}"
        )
    return _engine


def _wav_duration(path: Path) -> float:
    with sf.SoundFile(path) as f:
        return len(f) / f.samplerate


def synthesize(text: str, emotion: str = "relaxed") -> Tuple[Path, float]:
    """Synthesize `text` offline via Windows SAPI → `(relative_path, duration)`.

    `emotion` is ignored — kept for signature parity with the other backends.
    """
    CLIENT_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    PUBLIC_AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    filename = f"output_{uuid.uuid4().hex}.wav"
    client_out = CLIENT_AUDIO_DIR / filename
    public_out = PUBLIC_AUDIO_DIR / filename

    engine = _get_engine()
    print(
        f"[sapi_tts] synthesize emotion={emotion!r} (ignored) "
        f"len(text)={len(text)}"
    )
    t0 = time.perf_counter()

    # save_to_file is more reliable to a temp path; then copy into place.
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        engine.save_to_file(text, str(tmp_path))
        engine.runAndWait()
        if not tmp_path.exists() or tmp_path.stat().st_size < 44:
            raise RuntimeError(
                "[sapi_tts] empty audio — is a Windows speech voice installed?"
            )
        shutil.copy2(tmp_path, client_out)
        shutil.copy2(tmp_path, public_out)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass

    elapsed = time.perf_counter() - t0
    duration = _wav_duration(public_out)
    rel_path = PurePosixPath("audio") / filename
    print(
        f"[sapi_tts] wrote {client_out.name} "
        f"api={elapsed*1000:.0f}ms duration={duration:.2f}s rel={rel_path}"
    )
    return rel_path, duration


def safe_synthesize(text: str, emotion: str = "relaxed") -> Optional[Tuple[Path, float]]:
    """`synthesize` that never raises: returns None on any failure."""
    try:
        rel_path, duration = synthesize(text, emotion)
    except Exception as e:
        print(
            f"[sapi_tts] SAFE-SKIP: synthesize raised {type(e).__name__}: {e} "
            f"text={text!r}"
        )
        return None
    if duration < MIN_AUDIBLE_S:
        print(
            f"[sapi_tts] SAFE-SKIP: near-zero duration ({duration:.3f}s) "
            f"text={text!r}"
        )
        return None
    return rel_path, duration


if __name__ == "__main__":
    rel, dur = synthesize("Hi, this is a fully offline voice test.")
    assert (CLIENT_AUDIO_DIR / rel.name).exists()
    assert dur > 0.3
    assert safe_synthesize("Safe wrapper check.") is not None
    print(f"[sapi_tts.__main__] OK — rel={rel} duration={dur:.2f}s")
