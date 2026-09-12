"""Speech-to-text: local faster-whisper (default) or Groq Whisper.

Public surface:

    transcribe_audio(aud_path, prompt) -> str

Dispatches on `ASR_BACKEND` in server/config.py:
  - "local" — faster-whisper on this machine (no API key)
  - "groq"  — Groq's whisper-large-v3 over HTTP (needs GROQ_API_KEY)

Legacy alias `transcribe_audio_groq` still points at the Groq path so
older call sites and check_setup keep working when they ask for Groq
explicitly.
"""

from __future__ import annotations

import os
from typing import Optional

from dotenv import load_dotenv

from server.config import (
    ASR_BACKEND,
    ASR_PROMPT,
    CHARACTER,
    WHISPER_COMPUTE_TYPE,
    WHISPER_DEVICE,
    WHISPER_MODEL,
)

load_dotenv()

_groq_client = None
_local_model = None


def _get_groq_client():
    global _groq_client
    if _groq_client is None:
        from groq import Groq

        key = os.getenv("GROQ_API_KEY")
        if not key:
            raise EnvironmentError(
                "GROQ_API_KEY not set — required for ASR_BACKEND=groq"
            )
        _groq_client = Groq(api_key=key)
    return _groq_client


def _get_local_model():
    global _local_model
    if _local_model is None:
        from faster_whisper import WhisperModel

        print(
            f"[asr.transcribe] loading faster-whisper model={WHISPER_MODEL!r} "
            f"device={WHISPER_DEVICE!r} compute_type={WHISPER_COMPUTE_TYPE!r}"
        )
        _local_model = WhisperModel(
            WHISPER_MODEL,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE_TYPE,
        )
        print("[asr.transcribe] local Whisper ready")
    return _local_model


def _scrub_asr_text(text: str, prompt: str) -> str:
    """Drop Whisper prompt-echo hallucinations (empty → caller skips emit)."""
    out = (text or "").strip()
    if not out:
        return ""
    prompt_s = (prompt or "").strip()
    # Exact / near-exact echo of the initial_prompt or the old long prompt.
    bad = {
        prompt_s.lower(),
        f"the following is a conversation with {CHARACTER.lower()}.",
        f"the following is a conversation with {CHARACTER.capitalize()}.",
        "the following is a conversation with luna.",
        "the following is a conversation with riko.",
    }
    low = out.lower().rstrip(".!?")
    if out.lower() in bad or low in {b.rstrip(".!?") for b in bad if b}:
        print(f"[asr.transcribe] dropped prompt-echo hallucination: {out!r}")
        return ""
    # Prompt swallowed as prefix: "Luna. what time is it" → keep the rest.
    if prompt_s and out.lower().startswith(prompt_s.lower()):
        rest = out[len(prompt_s) :].lstrip(" .,;:-")
        if rest:
            return rest
        print(f"[asr.transcribe] dropped prompt-only hallucination: {out!r}")
        return ""
    return out


def transcribe_audio_local(
    aud_path: str = "conversation.wav",
    prompt: str = ASR_PROMPT,
) -> str:
    """Transcribe audio with local faster-whisper. Returns the spoken text.

    `vad_filter` is OFF on purpose: browser mic uploads are already button-
    segmented, and Silero VAD on Opus/webm often deletes the entire clip
    (empty transcription → she never answers voice). Desktop mic recordings
    are already VAD-trimmed in `recorder.record_on_speech`.
    """
    model = _get_local_model()
    # Normalize browser webm/ogg to 16 kHz mono wav — Whisper + av handle it,
    # but a plain wav is more reliable across machines.
    path = _ensure_wav_16k(aud_path)
    # Prefer a tiny prompt (or none) — long sentences get spoken back by Whisper.
    use_prompt = (prompt or "").strip()
    if len(use_prompt) > 40:
        use_prompt = "Luna."
    segments, _info = model.transcribe(
        path,
        initial_prompt=use_prompt or None,
        vad_filter=False,
        language="en",
    )
    text = " ".join(seg.text.strip() for seg in segments).strip()
    text = _scrub_asr_text(text, use_prompt)
    print(f"[asr.transcribe.local] {text!r}")
    return text


def _ensure_wav_16k(aud_path: str) -> str:
    """Return a 16 kHz mono wav path; convert if the upload is webm/ogg/mp3."""
    import tempfile
    from pathlib import Path

    src = Path(aud_path)
    if src.suffix.lower() == ".wav":
        return aud_path

    try:
        import av
        import numpy as np
        import soundfile as sf
    except ImportError:
        return aud_path

    try:
        container = av.open(str(src))
        stream = container.streams.audio[0]
        resampler = av.audio.resampler.AudioResampler(
            format="s16", layout="mono", rate=16000
        )
        chunks = []
        for frame in container.decode(stream):
            for rf in resampler.resample(frame):
                chunks.append(rf.to_ndarray().reshape(-1))
        for rf in resampler.resample(None):
            chunks.append(rf.to_ndarray().reshape(-1))
        container.close()
        if not chunks:
            print(f"[asr.transcribe] convert produced no audio from {src.name}")
            return aud_path
        audio = np.concatenate(chunks).astype(np.float32) / 32768.0
        out = Path(tempfile.gettempdir()) / "riko_browser_voice_16k.wav"
        sf.write(str(out), audio, 16000)
        print(f"[asr.transcribe] converted {src.suffix} -> {out.name} "
              f"({len(audio)/16000:.2f}s)")
        return str(out)
    except Exception as e:
        print(f"[asr.transcribe] convert failed ({type(e).__name__}: {e}); "
              f"using original {src.name}")
        return aud_path


def transcribe_audio_groq(
    aud_path: str = "conversation.wav",
    prompt: str = ASR_PROMPT,
) -> str:
    """Transcribe a WAV via Groq Whisper-large-v3. Returns the spoken text."""
    client = _get_groq_client()
    use_prompt = (prompt or "").strip()
    if len(use_prompt) > 40:
        use_prompt = "Luna."
    with open(aud_path, "rb") as file:
        transcription = client.audio.transcriptions.create(
            file=(aud_path, file.read()),
            model="whisper-large-v3",
            response_format="verbose_json",
            prompt=use_prompt or None,
        )
    text = _scrub_asr_text(str(transcription.text or "").strip(), use_prompt)
    print(f"[asr.transcribe.groq] {text!r}")
    return text


def transcribe_audio(
    aud_path: str = "conversation.wav",
    prompt: str = ASR_PROMPT,
) -> str:
    """Transcribe using whichever backend `ASR_BACKEND` selects."""
    if ASR_BACKEND == "groq":
        return transcribe_audio_groq(aud_path=aud_path, prompt=prompt)
    if ASR_BACKEND == "local":
        return transcribe_audio_local(aud_path=aud_path, prompt=prompt)
    raise ValueError(
        f"ASR_BACKEND={ASR_BACKEND!r} must be one of ('local', 'groq')"
    )


if __name__ == "__main__":
    from server.asr.recorder import record_on_speech

    out = "/tmp/asr_test.wav"
    record_on_speech(
        out,
        samplerate=16000,
        channels=1,
        silence_threshold=0.02,
        silence_duration=0.25,
    )
    print(f"[asr.transcribe.__main__] backend={ASR_BACKEND!r} transcribing...")
    print(transcribe_audio(out))
