"""Inject LLaVA captions into a chat turn.

Local chat models (qwen) cannot see pixels — when VISION_MODEL is set we
caption attached photos and fresh webcam frames, then fold that text into
injected_context for the spoken brain.
"""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Tuple

from server import config


def _fetch_live_caption(max_age_s: float = 25.0) -> str:
    """Pull the latest /vision/frame caption from the FastAPI process."""
    url = f"{config.BROWSER_INPUT_URL.rstrip('/')}/vision/latest"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[vision] live fetch failed: {type(e).__name__}: {e}")
        return ""

    caption = (data.get("caption") or "").strip()
    updated_at = float(data.get("updated_at") or 0.0)
    if not caption:
        return ""
    if updated_at and (time.time() - updated_at) > max_age_s:
        return ""
    return caption


def resolve_vision_for_turn(
    user_text: str,
    image_path: str,
    injected_context: str,
) -> Tuple[str, str, str]:
    """Return (user_text, image_path_for_llm, injected_context).

    For local LLM + VISION_MODEL: caption the attached file and/or live
    camera, clear image_path (pixels unused), keep captions in context.
    For OpenAI vision: keep image_path; still add live-camera text if any.
    """
    vision_model = getattr(config, "VISION_MODEL", "") or ""
    bits: list[str] = []
    path_for_llm = (image_path or "").strip()

    if path_for_llm and vision_model:
        from server.llm.vision_caption import caption_image

        print(f"[vision] captioning attached image with {vision_model!r}...")
        attached = caption_image(
            path_for_llm,
            model=vision_model,
            base_url=config.LOCAL_LLM_BASE_URL,
        )
        if attached:
            bits.append(f"[What you see in the attached photo]\n{attached}")

        # Local chat models strip images — don't bother attaching pixels.
        if config.LLM_BACKEND == "local":
            path_for_llm = ""

    live = _fetch_live_caption()
    if live:
        # Avoid duplicating if attach caption already covers a still from cam.
        if not bits or live not in bits[0]:
            bits.append(f"[What you see on the live camera right now]\n{live}")

    if not bits:
        return user_text, (image_path or "").strip() if config.LLM_BACKEND != "local" else path_for_llm, injected_context

    vision_block = (
        "You can see through vision. Use these observations naturally; "
        "do not say you cannot see.\n\n" + "\n\n".join(bits)
    )
    injected = (
        f"{injected_context}\n\n{vision_block}".strip()
        if injected_context
        else vision_block
    )
    print(f"[vision] injected {len(bits)} observation(s) into turn context")
    return user_text, path_for_llm, injected
