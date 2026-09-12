"""Fish Audio TTS. A drop-in swap for `server.tts.sovits`.

Chosen by `tts_backend: fish` in the character yaml. No GPU and no install —
it synthesizes over the internet — but it needs FISH_API_KEY in .env and a
voice id (`fish_reference_id`) from https://fish.audio.

Public surface is identical to the SoVITS wrapper, on purpose:

    synthesize(text: str, emotion: str = "relaxed") -> (Path, float)
    safe_synthesize(text, emotion) -> (Path, float) | None   (never raises)

- Returns a browser-relative path (e.g. `audio/<uid>.wav`) ready to drop
  into the FastAPI `/talk` endpoint, plus the wav duration in seconds.
- Writes the wav to BOTH `client/audio/<uid>.wav` (so the browser can
  fetch it) and `<project_root>/audio/<uid>.wav`.
- The `emotion` argument is accepted for signature compatibility with
  the SoVITS wrapper but ignored — Fish uses a single `reference_id`
  voice (the character's locked-in voice id).

Why batch and not streaming: the v2 `OrderedPlaybackWorker` and
`vrm_talk` both expect a complete file + a known duration. Streaming
PCM requires a separate WebSocket route on the FastAPI server AND a
new browser-side `AnalyserNode`-driven lipsync path — both deferred to
Phase 2 (see this folder's README).

Env var: `FISH_API_KEY`. Voice id: `fish_reference_id` in the character yaml.
"""

from __future__ import annotations

import os
import shutil
import struct
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Optional, Tuple

import soundfile as sf
from dotenv import load_dotenv
from fish_audio_sdk import Prosody, Session, TTSRequest

from server.config import CLIENT_AUDIO_DIR, FISH_REFERENCE_ID, PUBLIC_AUDIO_DIR


load_dotenv()


# The voice id is per-character: it comes from the character yaml through
# config, so adding a persona only means editing that persona's file.
BACKEND = "s2-pro"
LATENCY_MODE = "balanced"
REFERENCE_ID: str = FISH_REFERENCE_ID
PROSODY = Prosody(speed=1.1, volume=1.0)
FORMAT = "wav"


_session: Optional[Session] = None


def _get_session() -> Session:
    global _session
    if _session is None:
        key = os.getenv("FISH_API_KEY")
        if not key:
            raise EnvironmentError(
                "FISH_API_KEY not set — required for server.tts.fish"
            )
        _session = Session(key)
        print(
            f"[fish_tts] Session ready backend={BACKEND} "
            f"reference_id={REFERENCE_ID} latency={LATENCY_MODE}"
        )
    return _session


def _wav_duration(path: Path) -> float:
    """Read wav duration in seconds via soundfile."""
    with sf.SoundFile(path) as f:
        return len(f) / f.samplerate


def fix_wav_header(raw: bytes) -> bytes:
    """Replace Fish's placeholder RIFF/data sizes with the real byte counts.

    Fish streams the audio, so it writes the header BEFORE it knows how long
    the clip is and fills both size fields with a placeholder
    (0xFFFFFF00 — declaring ~48,695 seconds, i.e. 13.5 hours). Writing those
    bytes to disk unchanged produces a wav that LIES about its length.

    libsndfile (and so `soundfile`, and so every duration this project prints)
    is tolerant and works the true length out from the file size, which is why
    this went unnoticed for a long time. Stricter players are not tolerant:
    they believe the header, so they never report the clip as finished when the
    audio runs out — which quietly wedges anything that advances a playback
    queue on that signal.

    Returns the bytes unchanged if this isn't a RIFF/WAVE file or the chunk
    walk hits anything unexpected — never worth losing audio over a header.
    """
    if len(raw) < 44 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        print("[fish_tts] header fix skipped — not a RIFF/WAVE payload")
        return raw

    buf = bytearray(raw)
    struct.pack_into("<I", buf, 4, len(buf) - 8)      # RIFF size

    pos = 12
    while pos + 8 <= len(buf):
        chunk_id = bytes(buf[pos:pos + 4])
        declared = struct.unpack_from("<I", buf, pos + 4)[0]
        if chunk_id == b"data":
            real = len(buf) - pos - 8
            if declared != real:
                struct.pack_into("<I", buf, pos + 4, real)
                print(f"[fish_tts] header fixed: data {declared:,} -> {real:,} bytes")
            return bytes(buf)
        if declared > len(buf):
            print(f"[fish_tts] header fix bailed — chunk {chunk_id!r} size {declared:,}")
            return bytes(buf)
        pos += 8 + declared + (declared & 1)          # chunks are word-aligned

    print("[fish_tts] header fix bailed — no data chunk found")
    return bytes(buf)


def synthesize(text: str, emotion: str = "relaxed") -> Tuple[Path, float]:
    """Synthesize `text` via Fish batch TTS and return `(relative_path, duration)`.

    `emotion` is ignored — kept for signature parity with `sovits.synthesize`.
    The returned path is browser-relative (e.g. `audio/<uid>.wav`), exactly
    what `vrm_talk(audio_path=...)` expects.
    """
    session = _get_session()

    CLIENT_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    PUBLIC_AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    filename = f"output_{uuid.uuid4().hex}.wav"
    client_out = CLIENT_AUDIO_DIR / filename
    public_out = PUBLIC_AUDIO_DIR / filename

    req = TTSRequest(
        text=text,
        reference_id=REFERENCE_ID,
        prosody=PROSODY,
        format=FORMAT,
        latency=LATENCY_MODE,
        normalize=True,
    )

    print(
        f"[fish_tts] synthesize emotion={emotion!r} (ignored) "
        f"len(text)={len(text)}"
    )
    t0 = time.perf_counter()
    # `session.tts(req)` returns a generator of audio chunks (per the SDK).
    # For batch we just drain it into a single buffer and write once.
    buf = bytearray()
    for chunk in session.tts(req, backend=BACKEND):
        buf.extend(chunk)
    elapsed = time.perf_counter() - t0

    if not buf:
        raise RuntimeError(
            "[fish_tts] empty audio response — check FISH_API_KEY and the text"
        )

    # Repair the streaming placeholder sizes before anything reads the file.
    client_out.write_bytes(fix_wav_header(bytes(buf)))
    shutil.copy2(client_out, public_out)

    duration = _wav_duration(public_out)
    # PurePosixPath, not Path: this value is handed to the browser as a
    # URL. Path on Windows would make it "audio\\output_x.wav".
    rel_path = PurePosixPath("audio") / filename
    print(
        f"[fish_tts] wrote {client_out.name} bytes={len(buf):,} "
        f"api={elapsed*1000:.0f}ms duration={duration:.2f}s rel={rel_path}"
    )
    return rel_path, duration


# Anything shorter than this is treated as a failed generation (mirrors
# server/tts/sovits.py — the API can return a near-empty clip on odd input).
MIN_AUDIBLE_S = 0.1


def safe_synthesize(text: str, emotion: str = "relaxed") -> Optional[Tuple[Path, float]]:
    """`synthesize` that never raises: returns None on any failure.

    Byte-for-byte the same contract as `sovits.safe_synthesize`, so the shared
    orchestrator can swap one for the other. One bad Fish generation (HTTP
    error, expired key, near-silent clip) costs that fragment only — the turn
    keeps going. Failures are logged loudly with the offending text.
    """
    try:
        rel_path, duration = synthesize(text, emotion)
    except Exception as e:
        print(f"[fish_tts] SAFE-SKIP: synthesize raised {type(e).__name__}: {e} text={text!r}")
        return None
    if duration < MIN_AUDIBLE_S:
        print(f"[fish_tts] SAFE-SKIP: near-zero duration ({duration:.3f}s) text={text!r}")
        return None
    return rel_path, duration


if __name__ == "__main__":
    # Two-part smoke test.
    #
    # PART A (always runs, offline-safe-ish — needs FISH_API_KEY only):
    #   call synthesize(...) and assert the file exists + has duration > 0.
    #
    # PART B needs the server running with the page open at :5173 — it feeds
    # the clip through the real playback worker, so you hear her say it.
    #
    # Run with:  python -m server.tts.fish
    print(f"[fish_tts.__main__] PART A — synthesize + duration check "
          f"(voice={REFERENCE_ID})")
    rel, dur = synthesize("Hi, this is a test from Fish Audio.")
    on_disk = CLIENT_AUDIO_DIR / rel.name
    public_copy = PUBLIC_AUDIO_DIR / rel.name
    assert on_disk.exists(), f"missing client wav: {on_disk}"
    assert public_copy.exists(), f"missing public wav: {public_copy}"
    assert dur > 0.5, f"implausibly short duration: {dur:.3f}s"
    print(f"[fish_tts.__main__] PART A OK — rel={rel} duration={dur:.2f}s")

    # PART B
    print("\n[fish_tts.__main__] PART B — feed into OrderedPlaybackWorker")
    print("[fish_tts.__main__] (needs the server running and the page open at :5173)")
    from server.playback.playback import ItemType, OrderedPlaybackWorker, PlaybackItem

    worker = OrderedPlaybackWorker()
    worker.start()
    try:
        worker.enqueue(PlaybackItem(
            item_type=ItemType.EXPRESSION,
            name="happy",
        ))
        worker.enqueue(PlaybackItem(
            item_type=ItemType.TEXT,
            audio_path=rel,
            text="Hi, this is a test from Fish Audio.",
            duration=dur,
        ))
        print("[fish_tts.__main__] waiting for playback to drain...")
        ok = worker.wait_until_finished(timeout=max(dur + 5.0, 10.0))
        assert ok, "playback worker did not finish in time"
        print("[fish_tts.__main__] PART B OK — playback worker accepted Fish audio")
    finally:
        worker.stop()

    # PART C — the never-raises wrapper the orchestrator actually calls.
    # The failure case is forced by dropping the API key (no wasted API call):
    # _get_session then raises, and safe_synthesize must swallow it.
    print("\n[fish_tts.__main__] PART C — safe_synthesize")
    assert safe_synthesize("Safe wrapper check, one two three.") is not None
    _saved_key, _saved_session = os.environ.get("FISH_API_KEY", ""), _session
    try:
        os.environ["FISH_API_KEY"] = ""
        _session = None
        assert safe_synthesize("boom") is None, "missing key should return None"
    finally:
        os.environ["FISH_API_KEY"] = _saved_key
        _session = _saved_session
    print("[fish_tts.__main__] PART C OK — safe_synthesize returns None on failure")
