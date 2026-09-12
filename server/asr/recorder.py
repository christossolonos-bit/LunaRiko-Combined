"""Mic capture: records once speech is detected, stops on sustained silence.

Sole public function: `record_on_speech`. Writes a 16-bit PCM WAV to
`output_file` and returns its path. No model loading — keep this module
import-light so `server.streaming_agent.inputs.voice_input` doesn't pay
for whisper/groq at process start.

Two ways to decide "is the user still talking":

  Silero VAD (default)  A trained speech detector — see `server/asr/vad.py`.
                        Ignores keyboards and fans, and still hears the quiet
                        tail of a sentence, so `silence_duration` can be short.
  fixed RMS threshold   The original: block loudness vs a constant. Kept as a
                        fallback via `ENABLE_SILERO_VAD = False`.

Run `python -m server.asr.recorder` to capture a one-off clip to
`/tmp/asr_test.wav` — handy for confirming the mic device works.
"""

from __future__ import annotations

import os
import queue
import sys

import numpy as np
import sounddevice as sd
import soundfile as sf

from server.asr.vad import FRAME as VAD_FRAME
from server.asr.vad import SAMPLERATE as VAD_SAMPLERATE
from server.config import ENABLE_SILERO_VAD, VAD_SPEECH_THRESHOLD


def record_on_speech(
    output_file: str = "conversation.wav",
    samplerate: int = VAD_SAMPLERATE,
    channels: int = 1,
    silence_threshold: float = 0.01,
    silence_duration: float = 1.0,
    device=None,
    use_vad: bool | None = None,
    speech_threshold: float | None = None,
) -> str:
    """Record from the mic, starting on first voice, stopping on silence.

    Args:
        output_file: Path to write the WAV. Removed first if it exists.
        samplerate: Hz. 16000 is what Silero needs and what Whisper uses
            internally anyway — recording higher just uploads more bytes.
        channels: 1 = mono.
        silence_threshold: RMS level below which audio counts as silence.
            Only used when the VAD is off.
        silence_duration: Seconds of continuous silence required to stop.
        device: sounddevice input device id/name, or None for system default.
        use_vad: None follows `config.ENABLE_SILERO_VAD`.
        speech_threshold: None follows `config.VAD_SPEECH_THRESHOLD`.

    Returns:
        The `output_file` path that was written.
    """
    if use_vad is None:
        use_vad = ENABLE_SILERO_VAD
    if speech_threshold is None:
        speech_threshold = VAD_SPEECH_THRESHOLD
    if os.path.exists(output_file):
        os.remove(output_file)
        print(f"[asr.recorder] removed existing file: {output_file}")

    audio_q: "queue.Queue" = queue.Queue()

    def callback(indata, frames, time_info, status):  # noqa: ARG001 — sd API
        if status:
            print(status, file=sys.stderr)
        audio_q.put(indata.copy())

    def rms_level(data) -> float:
        return float(np.sqrt(np.mean(np.square(data))))

    gate = None
    if use_vad and samplerate != VAD_SAMPLERATE:
        print(f"[asr.recorder] VAD needs {VAD_SAMPLERATE} Hz but samplerate is "
              f"{samplerate} — falling back to the RMS threshold")
    elif use_vad:
        from server.asr.vad import SileroGate

        gate = SileroGate(threshold=speech_threshold)
        print(f"[asr.recorder] Silero VAD on (threshold={speech_threshold}, "
              f"silence={silence_duration}s)")

    def is_speaking(data) -> bool:
        if gate is None:
            return rms_level(data) > silence_threshold
        return gate.is_speech(data[:, 0] if data.ndim > 1 else data)

    try:
        with sf.SoundFile(
            output_file,
            mode="x",
            samplerate=samplerate,
            channels=channels,
            subtype="PCM_16",
        ) as file:
            with sd.InputStream(
                samplerate=samplerate,
                device=device,
                channels=channels,
                callback=callback,
                # One Silero frame per callback: the silence timer then ticks
                # in 32ms steps instead of whatever the driver felt like.
                blocksize=VAD_FRAME if gate is not None else 0,
            ):
                print("[asr.recorder] listening for speech...")
                silent_time = 0.0
                recording_started = False

                while True:
                    data = audio_q.get()
                    speaking = is_speaking(data)

                    if not recording_started and speaking:
                        print("[asr.recorder] voice detected, recording...")
                        recording_started = True

                    if recording_started:
                        file.write(data)
                        if not speaking:
                            silent_time += len(data) / samplerate
                        else:
                            silent_time = 0.0
                        if silent_time >= silence_duration:
                            print("[asr.recorder] silence detected, stopping")
                            break
    except KeyboardInterrupt:
        print("\n[asr.recorder] interrupted")
    except Exception as e:
        print(f"[asr.recorder] error: {type(e).__name__}: {e}", file=sys.stderr)

    return output_file


if __name__ == "__main__":
    # Live mic check. Say a sentence, pause, and read the reported timings —
    # `stop lag` is the delay this whole change exists to shrink.
    #   .venv/bin/python -m server.asr.recorder            # VAD, 0.25s hangover
    #   .venv/bin/python -m server.asr.recorder 0.6 rms    # the old behaviour
    import sys
    import time

    hangover = float(sys.argv[1]) if len(sys.argv) > 1 else 0.25
    mode_rms = len(sys.argv) > 2 and sys.argv[2] == "rms"

    out = "/tmp/asr_test.wav"
    t0 = time.perf_counter()
    record_on_speech(
        output_file=out,
        samplerate=VAD_SAMPLERATE,
        channels=1,
        silence_threshold=0.02,
        silence_duration=hangover,
        device=None,
        use_vad=not mode_rms,
    )
    elapsed = time.perf_counter() - t0

    # Filler playback deliberately does NOT live here — it goes out through
    # /talk so the avatar lipsyncs it and the browser does the decoding.
    with sf.SoundFile(out) as f:
        duration = len(f) / f.samplerate
    print(f"[asr.recorder.__main__] wrote {out}  "
          f"mode={'RMS' if mode_rms else 'VAD'}  hangover={hangover}s")
    print(f"[asr.recorder.__main__] clip {duration:.2f}s, "
          f"stop lag {hangover:.2f}s of it was the wait after you stopped")
    assert duration > 0, "recorded nothing"
