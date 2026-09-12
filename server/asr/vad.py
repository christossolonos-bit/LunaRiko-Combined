"""Silero VAD speech gate — asks "is this a voice?", not "is this loud?".

The mic loop in `recorder.py` ended a turn when the raw RMS of a block sat
below a fixed threshold. That is a loudness meter, not a voice detector: a
keyboard, a desk fan, or a bumped table all read as speech, while the quiet
tail of a real sentence ("...I guess.") reads as silence. To survive its own
false silences the hangover had to be padded to 0.5-0.6s, and that padding is
pure latency on every single turn.

Silero scores each 32ms frame for *speech* specifically, so noise reads near
zero while quiet speech still reads high. The hangover can then drop to
~0.25s without cutting the speaker off.

No new dependency and no download: `faster-whisper` is already installed and
ships `silero_vad_v6.onnx` in its assets, loaded here through its cached
`get_vad_model()`. We drive the onnxruntime session directly rather than
calling faster-whisper's own wrapper, because that wrapper zeroes the model's
LSTM state on every call — correct for scoring a finished file in one shot,
wrong for a live stream where each block must remember the one before it.

16 kHz mono only. The model's frame is 512 samples, which is 32ms at 16k and
nothing useful at any other rate.

Public surface:
    SileroGate(threshold=0.5).push(samples) -> float   # speech probability
    SileroGate(threshold=0.5).is_speech(samples) -> bool
    SileroGate(...).reset()

Run it standalone (no mic needed):
    .venv/bin/python -m server.asr.vad
    .venv/bin/python -m server.asr.vad some_recording.wav
"""

from __future__ import annotations

import numpy as np

SAMPLERATE = 16000
FRAME = 512               # samples the model consumes per step = 32ms at 16k
CONTEXT = 64              # lookback samples the v6 graph expects prepended
DEFAULT_THRESHOLD = 0.5


class SileroGate:
    """Streaming speech detector. Feed it mono 16k audio, ask if it's a voice.

    Holds the model's recurrent state across calls, so blocks may be any
    length — samples that don't fill a frame are carried to the next push.
    """

    def __init__(self, threshold: float = DEFAULT_THRESHOLD):
        from faster_whisper.vad import get_vad_model

        self.threshold = threshold
        self._session = get_vad_model().session
        self.reset()

    def reset(self) -> None:
        """Clear recurrent state and the partial-frame buffer. Call between
        recordings so the previous utterance can't leak into the next one."""
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._context = np.zeros(CONTEXT, dtype=np.float32)
        self._buffer = np.zeros(0, dtype=np.float32)
        self.prob = 0.0

    def push(self, samples) -> float:
        """Feed mono 16k samples; return the speech probability, 0..1.

        The value is the *highest* probability across the frames completed by
        this push, rather than the last one. A brief burst of speech inside a
        block therefore holds the silence timer open — erring toward waiting,
        never toward cutting the speaker off. If the push didn't complete a
        frame, the previous probability is returned unchanged.
        """
        audio = np.asarray(samples, dtype=np.float32).reshape(-1)
        self._buffer = np.concatenate([self._buffer, audio])

        probs = []
        while self._buffer.size >= FRAME:
            frame, self._buffer = self._buffer[:FRAME], self._buffer[FRAME:]
            batch = np.concatenate([self._context, frame])[None, :]
            out, self._h, self._c = self._session.run(
                None, {"input": batch, "h": self._h, "c": self._c}
            )
            self._context = frame[-CONTEXT:]
            probs.append(float(out.reshape(-1)[0]))

        if probs:
            self.prob = max(probs)
        return self.prob

    def is_speech(self, samples) -> bool:
        """Drop-in replacement for `rms > silence_threshold` in the mic loop."""
        return self.push(samples) >= self.threshold


if __name__ == "__main__":
    # Self-check with no mic and no recording: the whole point of this module
    # is that it separates *speech* from *loud*, so the test feeds it loud
    # things that are not speech and asserts it stays quiet.
    #
    #   .venv/bin/python -m server.asr.vad                  # synthetic check
    #   .venv/bin/python -m server.asr.vad recording.wav    # timeline of a clip
    import sys
    import time

    def score(signal: np.ndarray, gate: SileroGate) -> float:
        """Push a signal one 32ms block at a time, return the peak probability."""
        gate.reset()
        peak = 0.0
        for i in range(0, signal.size - FRAME + 1, FRAME):
            peak = max(peak, gate.push(signal[i:i + FRAME]))
        return peak

    if len(sys.argv) > 1:
        import soundfile as sf

        path = sys.argv[1]
        data, sr = sf.read(path, dtype="float32", always_2d=False)
        audio = data.mean(axis=1) if data.ndim > 1 else data
        if sr != SAMPLERATE:
            import librosa

            print(f"[vad.__main__] resampling {sr} -> {SAMPLERATE} for the test")
            audio = librosa.resample(audio, orig_sr=sr, target_sr=SAMPLERATE)

        gate = SileroGate()
        print(f"\n[vad.__main__] {path}  {audio.size / SAMPLERATE:.2f}s\n")
        print(f"  {'time':>7}  {'rms':>7}  {'speech':>7}  bar")
        t0 = time.perf_counter()
        speech_s = 0.0
        for i in range(0, audio.size - FRAME + 1, FRAME):
            block = audio[i:i + FRAME]
            p = gate.push(block)
            rms = float(np.sqrt(np.mean(block ** 2)))
            speech_s += FRAME / SAMPLERATE if p >= gate.threshold else 0.0
            # One line per 96ms so a long clip stays readable.
            if (i // FRAME) % 3 == 0:
                bar = "#" * int(p * 40)
                print(f"  {i / SAMPLERATE:>6.2f}s  {rms:>7.4f}  {p:>7.2f}  {bar}")
        n = audio.size // FRAME
        print(f"\n[vad.__main__] speech {speech_s:.2f}s of "
              f"{audio.size / SAMPLERATE:.2f}s   "
              f"{(time.perf_counter() - t0) / max(n, 1) * 1000:.2f}ms per frame")
        sys.exit(0)

    gate = SileroGate()
    t = np.linspace(0, 2.0, SAMPLERATE * 2, endpoint=False, dtype=np.float32)
    rng = np.random.default_rng(0)

    silence = np.zeros(SAMPLERATE * 2, dtype=np.float32)
    # A 180 Hz tone at amplitude 0.3 has an RMS of ~0.21 — ten times the old
    # 0.02 silence threshold, so the RMS detector calls this "speech".
    tone = (0.3 * np.sin(2 * np.pi * 180 * t)).astype(np.float32)
    # Broadband hiss at a similar level: a desk fan, or an air conditioner.
    hiss = (0.2 * rng.standard_normal(t.size)).astype(np.float32)

    results = {name: score(sig, gate) for name, sig in
               [("silence", silence), ("180Hz tone", tone), ("white noise", hiss)]}

    print(f"\n  {'signal':<14}{'rms':>8}{'peak speech prob':>20}")
    for name, sig in [("silence", silence), ("180Hz tone", tone),
                      ("white noise", hiss)]:
        rms = float(np.sqrt(np.mean(sig ** 2)))
        print(f"  {name:<14}{rms:>8.4f}{results[name]:>20.2f}")

    for name, peak in results.items():
        assert peak < DEFAULT_THRESHOLD, f"{name} scored {peak:.2f} as speech"
    assert float(np.sqrt(np.mean(tone ** 2))) > 0.02, "tone must beat old threshold"

    # Latency: this runs once per 32ms of audio inside the mic callback, so it
    # has to be far under 32ms or the input stream will overrun.
    gate.reset()
    block = tone[:FRAME]
    t0 = time.perf_counter()
    for _ in range(200):
        gate.push(block)
    per_frame_ms = (time.perf_counter() - t0) / 200 * 1000
    print(f"\n[vad.__main__] {per_frame_ms:.2f}ms per 32ms frame "
          f"({32 / per_frame_ms:.0f}x realtime)")
    assert per_frame_ms < 10, f"too slow for the mic callback: {per_frame_ms:.1f}ms"

    print("[vad.__main__] OK — loud non-speech stays below threshold, "
          "and it keeps up with the mic")
