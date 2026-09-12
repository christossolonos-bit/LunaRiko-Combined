"""Edge TTS (Microsoft Edge online voices). Drop-in for fish/sovits.

Chosen by `tts_backend: edge` in the character yaml. Uses the local
`edge-tts` package — no API key, no GPU. Needs network to Microsoft's
speech endpoint.

Public surface matches the other TTS wrappers:

    synthesize(text: str, emotion: str = "relaxed") -> (Path, float)
    safe_synthesize(text, emotion) -> (Path, float) | None
"""

from __future__ import annotations

import asyncio
import io
import shutil
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Optional, Tuple

import av
import numpy as np
import soundfile as sf

from server.config import CLIENT_AUDIO_DIR, EDGE_TTS_RATE, EDGE_TTS_VOICE, PUBLIC_AUDIO_DIR

MIN_AUDIBLE_S = 0.1
_SAMPLE_RATE = 24000


def _wav_duration(path: Path) -> float:
    with sf.SoundFile(path) as f:
        return len(f) / f.samplerate


def _mp3_to_wav(mp3_bytes: bytes, wav_path: Path) -> None:
    """Decode Edge's MP3 payload to a mono WAV the browser + soundfile like."""
    container = av.open(io.BytesIO(mp3_bytes), format="mp3")
    stream = container.streams.audio[0]
    resampler = av.audio.resampler.AudioResampler(
        format="s16", layout="mono", rate=_SAMPLE_RATE
    )
    chunks: list[np.ndarray] = []
    for frame in container.decode(stream):
        for rf in resampler.resample(frame):
            chunks.append(rf.to_ndarray().reshape(-1))
    for rf in resampler.resample(None):
        chunks.append(rf.to_ndarray().reshape(-1))
    container.close()

    if not chunks:
        raise RuntimeError("[edge_tts] decoded empty audio")

    audio = np.concatenate(chunks).astype(np.float32) / 32768.0
    sf.write(str(wav_path), audio, _SAMPLE_RATE)


async def _synthesize_mp3(text: str) -> bytes:
    import edge_tts

    communicate = edge_tts.Communicate(text, EDGE_TTS_VOICE, rate=EDGE_TTS_RATE)
    buf = bytearray()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.extend(chunk["data"])
    if not buf:
        raise RuntimeError(
            "[edge_tts] empty audio response — check network / EDGE_TTS_VOICE"
        )
    return bytes(buf)


def _run_async(coro):
    """Run a coroutine from sync code even if a loop is already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Already inside a loop (rare here) — run on a fresh thread.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def synthesize(text: str, emotion: str = "relaxed") -> Tuple[Path, float]:
    """Synthesize `text` via Edge TTS and return `(relative_path, duration)`.

    `emotion` is ignored — kept for signature parity with sovits/fish.
    """
    CLIENT_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    PUBLIC_AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    filename = f"output_{uuid.uuid4().hex}.wav"
    client_out = CLIENT_AUDIO_DIR / filename
    public_out = PUBLIC_AUDIO_DIR / filename

    print(
        f"[edge_tts] synthesize voice={EDGE_TTS_VOICE!r} rate={EDGE_TTS_RATE!r} "
        f"emotion={emotion!r} (ignored) len(text)={len(text)}"
    )
    t0 = time.perf_counter()
    mp3 = _run_async(_synthesize_mp3(text))
    _mp3_to_wav(mp3, client_out)
    shutil.copy2(client_out, public_out)
    elapsed = time.perf_counter() - t0

    duration = _wav_duration(public_out)
    rel_path = PurePosixPath("audio") / filename
    print(
        f"[edge_tts] wrote {client_out.name} bytes={len(mp3):,} "
        f"api={elapsed*1000:.0f}ms duration={duration:.2f}s rel={rel_path}"
    )
    return rel_path, duration


def safe_synthesize(text: str, emotion: str = "relaxed") -> Optional[Tuple[Path, float]]:
    """`synthesize` that never raises: returns None on any failure."""
    try:
        rel_path, duration = synthesize(text, emotion)
    except Exception as e:
        print(
            f"[edge_tts] SAFE-SKIP: synthesize raised {type(e).__name__}: {e} "
            f"text={text!r}"
        )
        return None
    if duration < MIN_AUDIBLE_S:
        print(
            f"[edge_tts] SAFE-SKIP: near-zero duration ({duration:.3f}s) "
            f"text={text!r}"
        )
        return None
    return rel_path, duration


if __name__ == "__main__":
    print(f"[edge_tts.__main__] voice={EDGE_TTS_VOICE} rate={EDGE_TTS_RATE}")
    rel, dur = synthesize("Hi, this is a test from Edge TTS.")
    on_disk = CLIENT_AUDIO_DIR / rel.name
    assert on_disk.exists(), f"missing client wav: {on_disk}"
    assert dur > 0.3, f"implausibly short duration: {dur:.3f}s"
    assert safe_synthesize("Safe wrapper check.") is not None
    print(f"[edge_tts.__main__] OK — rel={rel} duration={dur:.2f}s")
