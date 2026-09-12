#!/usr/bin/env python3
"""Minimal STT worker for discord-bridge — faster-whisper, JSONL stdin/stdout."""
from __future__ import annotations

import json
import sys

_model = None


def get_model(name: str, device: str, compute: str):
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(name, device=device, compute_type=compute)
    return _model


def main() -> None:
    cfg = {}
    if len(sys.argv) > 1:
        try:
            cfg = json.loads(sys.argv[1])
        except json.JSONDecodeError:
            pass
    model_name = cfg.get("model", "base")
    device = cfg.get("device", "cpu")
    compute = cfg.get("compute_type", "int8")
    language = cfg.get("language") or None

    get_model(model_name, device, compute)
    print(json.dumps({"type": "ready", "model": model_name, "device": device}), flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            print(json.dumps({"type": "error", "error": str(e)}), flush=True)
            continue
        req_id = req.get("id")
        op = req.get("op")
        if op == "ping":
            print(json.dumps({"type": "pong", "id": req_id}), flush=True)
            continue
        if op != "stt":
            print(json.dumps({"type": "error", "id": req_id, "error": f"unknown op {op}"}), flush=True)
            continue
        wav = req.get("wav")
        try:
            model = get_model(model_name, device, compute)
            segments, _ = model.transcribe(
                wav,
                language=language,
                vad_filter=False,
                beam_size=2,
            )
            text = " ".join(s.text.strip() for s in segments if s.text.strip()).strip()
            print(json.dumps({"type": "stt", "id": req_id, "text": text}), flush=True)
        except Exception as e:
            print(json.dumps({"type": "error", "id": req_id, "error": str(e)}), flush=True)


if __name__ == "__main__":
    main()
