"""Mute + vision frame routes for the browser UI."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel

from server import config
from server.routes._common import (
    latest_vision_caption,
    mic_muted,
    set_mic_muted,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_last_caption_at = 0.0
_frame_count = 0


class MuteRequest(BaseModel):
    muted: bool


@router.get("/mute")
async def get_mute() -> dict:
    return {"muted": mic_muted["value"]}


@router.post("/mute")
async def post_mute(req: MuteRequest) -> dict:
    set_mic_muted(req.muted)
    logger.info("mic muted=%s", req.muted)
    return {"muted": mic_muted["value"]}


@router.get("/vision/latest")
async def vision_latest() -> dict:
    """Latest live-camera caption for chat_terminal to inject into a turn."""
    return {
        "caption": latest_vision_caption.get("text"),
        "updated_at": latest_vision_caption.get("updated_at") or 0.0,
    }


@router.post("/vision/frame")
async def vision_frame(request: Request) -> dict:
    """Accept a raw JPEG webcam frame; caption with LLaVA every few frames.

    Used by the camera (video) button in the UI — client/interaction/videoStream.js.
    """
    global _last_caption_at, _frame_count

    body = await request.body()
    if not body:
        return {"ok": False, "error": "empty body"}

    _frame_count += 1
    vision_model = getattr(config, "VISION_MODEL", "") or ""
    if not vision_model:
        return {"ok": True, "captioned": False, "reason": "VISION_MODEL unset"}

    # Caption at most every ~3s so we don't flood Ollama.
    now = time.time()
    if now - _last_caption_at < 3.0 and latest_vision_caption.get("text"):
        return {
            "ok": True,
            "captioned": False,
            "frame": _frame_count,
            "caption": latest_vision_caption.get("text"),
        }

    uploads = Path(config.UPLOADS_DIR)
    uploads.mkdir(parents=True, exist_ok=True)
    frame_path = uploads / "vision_frame.jpg"
    frame_path.write_bytes(body)

    from server.llm.vision_caption import caption_image

    caption = caption_image(
        str(frame_path),
        model=vision_model,
        base_url=getattr(config, "LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434"),
    )
    if caption:
        latest_vision_caption["text"] = caption
        latest_vision_caption["updated_at"] = now
        _last_caption_at = now
    return {
        "ok": True,
        "captioned": bool(caption),
        "frame": _frame_count,
        "caption": caption or latest_vision_caption.get("text"),
    }
