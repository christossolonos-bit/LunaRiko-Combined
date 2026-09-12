"""Speech capture + transcription helpers.

`recorder.record_on_speech` blocks until the user speaks then writes a
WAV. `transcribe.transcribe_audio` turns that WAV into text — local
faster-whisper by default, or Groq when `ASR_BACKEND=groq`.
"""
