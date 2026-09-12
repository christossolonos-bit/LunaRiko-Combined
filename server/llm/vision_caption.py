"""Ollama vision caption (LLaVA / other VL models).

Used when a webcam/image is attached on the local LLM path — qwen chat stays
the spoken brain; the VL model only describes what it sees.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional


def caption_image(
    image_path: str,
    *,
    model: str,
    base_url: str = "http://127.0.0.1:11434",
    prompt: str = (
        "Describe what you see in one short paragraph for a voice companion. "
        "Focus on people, faces, expression, clothing, and notable objects. "
        "Be concrete and brief."
    ),
    timeout_s: float = 90.0,
) -> str:
    """Return a short caption, or empty string on failure."""
    path = Path(image_path)
    if not path.is_file():
        print(f"[vision] missing image: {path}")
        return ""

    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    url = base_url.rstrip("/") + "/api/chat"
    body = {
        "model": model,
        "stream": False,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [b64],
            }
        ],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        print(f"[vision] HTTP {e.code}: {detail}")
        return ""
    except Exception as e:
        print(f"[vision] caption failed: {type(e).__name__}: {e}")
        return ""

    msg = data.get("message") or {}
    text = (msg.get("content") or "").strip()
    if text:
        print(f"[vision] caption ({model}): {text[:160]}{'…' if len(text) > 160 else ''}")
    return text
