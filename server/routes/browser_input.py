"""Browser input routes — the web page's mic button, text box and image upload.

The browser (client/ui.js) and the streaming agent (chat_terminal.py) are
different processes that only share HTTP, so uploads land here on the
server's port and
sit in `_common.py` buffers until the agent's BrowserVoiceInput polls them.

  POST /upload-audio/   — save the mic-button clip to a shared temp path,
                          bump `pending_voice` {version, path, image_path}.
                          Optional `image` part rides along (voice + picture).
  GET  /pending_voice   — polled by BrowserVoiceInput (~0.5s)
  POST /send_message    — buffer text from the UI box, bump `pending_text`
  GET  /pending_text    — polled by BrowserVoiceInput (~0.5s)
  POST /upload-image/   — save to uploads/browser_image.<ext> + set
                          `latest_uploaded_image` (revives /take_picture).
                          Optional `text` field is routed via pending_text
                          so the message still reaches the agent.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, UploadFile

from server.config import UPLOADS_DIR
from server.routes._common import (
    discord_audio_queue,
    discord_music,
    discord_overhear,
    discord_tts_busy,
    discord_vc_active,
    latest_uploaded_image,
    pending_text,
    pending_voice,
    reply_audio_target,
)
from server.routes.schemas import (
    DiscordMusicRequest,
    DiscordOverhearRequest,
    DiscordTtsBusyRequest,
    DiscordVcRequest,
    LiveMemoryTurnRequest,
    ReplyAudioRequest,
    SendMessageRequest,
)


logger = logging.getLogger(__name__)
router = APIRouter()


def _resolve_reply_audio(req: SendMessageRequest) -> str:
    """Where TTS should play for this turn.

    Discord-labeled messages stay Discord-only. Website text/voice use browser
    (or both) even while the bot is in Discord VC — otherwise the VRM page
    goes silent whenever /luna join is active.
    """
    raw = (req.reply_audio or req.source or "").strip().lower()
    text = (req.message_text or "").lstrip()
    if text.startswith("[Discord voice") or text.startswith("[Discord VC"):
        return "discord"
    if raw in ("discord", "browser", "both"):
        return raw
    return "both"


async def _save_image(file: UploadFile) -> str:
    """Write an uploaded picture to the one stable path and return it.

    One filename (uploads/browser_image.<ext>) so the base registry's
    analyze_images tool — which scans ALL of uploads/ — never sees a backlog.
    Also fills `latest_uploaded_image`, which un-blocks /take_picture.
    """
    data = await file.read()
    ext = Path(file.filename or "image.jpg").suffix or ".jpg"
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    dest = UPLOADS_DIR / f"browser_image{ext}"
    dest.write_bytes(data)
    latest_uploaded_image["path"] = str(dest)
    logger.info(f"[browser-input] image saved: {file.filename} "
                f"({len(data)} bytes) -> {dest}")
    return str(dest)


@router.post("/upload-audio/")
async def upload_audio(file: UploadFile = File(...),
                       image: Optional[UploadFile] = File(None)) -> dict:
    """Receive a full voice clip from the browser mic button.

    Saved to one stable temp path (extension follows the upload — webm from
    Chrome, mp4 from Safari). The agent process reads this same path, so it
    must be a location both processes can see (/tmp is, on WSL).

    `image` is optional and mirrors the `text` field on /upload-image/: it lets
    a staged picture ride along with a VOICE memo, so "here, look at this" can
    be spoken instead of typed. Sending them in ONE request keeps the pairing
    atomic — no server-side tray that could attach a forgotten image to an
    unrelated turn later.
    """
    data = await file.read()
    ext = Path(file.filename or "recording.webm").suffix or ".webm"
    dest = Path(tempfile.gettempdir()) / f"riko_browser_voice{ext}"
    dest.write_bytes(data)

    image_path = await _save_image(image) if image is not None else None

    pending_voice["version"] += 1
    pending_voice["path"] = str(dest)
    pending_voice["image_path"] = image_path
    logger.info(
        f"[browser-input] audio received: {file.filename} ({len(data)} bytes) "
        f"-> {dest} (voice v{pending_voice['version']}, image={image_path})"
    )
    return {"status": "received", "bytes": len(data),
            "version": pending_voice["version"], "path": str(dest),
            "image_path": image_path}


@router.get("/pending_voice")
async def get_pending_voice() -> dict:
    """Polled by the agent's BrowserVoiceInput. Fires on version change."""
    return dict(pending_voice)


@router.post("/send_message")
async def send_message(req: SendMessageRequest) -> dict:
    """Buffer a typed message from the UI text box for the agent."""
    target = _resolve_reply_audio(req)
    pending_text["version"] += 1
    pending_text["text"] = req.message_text
    pending_text["image_path"] = None
    pending_text["reply_audio"] = target
    # So /talk during the upcoming turn routes correctly (same FastAPI process).
    reply_audio_target["value"] = target
    logger.info(
        f"[browser-input] text received: {req.message_text!r} "
        f"(text v{pending_text['version']}, reply_audio={target})"
    )
    return {
        "status": "received",
        "version": pending_text["version"],
        "reply_audio": target,
    }


@router.post("/set_reply_audio")
async def set_reply_audio(req: ReplyAudioRequest) -> dict:
    """chat_terminal sets this at turn start / clears after (cross-process)."""
    target = (req.target or "both").strip().lower()
    if target not in ("discord", "browser", "both"):
        target = "both"
    reply_audio_target["value"] = target
    return {"target": target, "discord_vc": bool(discord_vc_active.get("value"))}


@router.get("/discord_vc")
async def get_discord_vc() -> dict:
    return {"active": bool(discord_vc_active.get("value"))}


@router.post("/discord_vc")
async def post_discord_vc(req: DiscordVcRequest) -> dict:
    """Discord bridge calls this on /luna join and /luna leave."""
    active = bool(req.active)
    discord_vc_active["value"] = active
    if active:
        # Default Discord routing until a website turn sets browser/both.
        reply_audio_target["value"] = "discord"
    else:
        reply_audio_target["value"] = "both"
        discord_music["playing"] = False
        discord_music["title"] = None
        discord_tts_busy["value"] = False
        discord_overhear.clear()
    logger.info("discord_vc active=%s (reply_audio=%s)", active, reply_audio_target["value"])
    return {"active": active, "reply_audio": reply_audio_target["value"]}


@router.get("/discord_music")
async def get_discord_music() -> dict:
    return {
        "playing": bool(discord_music.get("playing")),
        "title": discord_music.get("title"),
    }


@router.post("/discord_music")
async def post_discord_music(req: DiscordMusicRequest) -> dict:
    """Discord bridge calls this when /luna play starts or the track ends."""
    playing = bool(req.playing)
    title = (req.title or "").strip() or None
    discord_music["playing"] = playing
    discord_music["title"] = title if playing else None
    logger.info("discord_music playing=%s title=%r", playing, discord_music["title"])
    return {"playing": playing, "title": discord_music["title"]}


@router.get("/discord_tts_busy")
async def get_discord_tts_busy() -> dict:
    return {
        "busy": bool(discord_tts_busy.get("value")),
        "updated_at": float(discord_tts_busy.get("updated_at") or 0),
        "queued": len(discord_audio_queue),
    }


@router.post("/discord_tts_busy")
async def post_discord_tts_busy(req: DiscordTtsBusyRequest) -> dict:
    """Bridge sets busy=true while VC TTS (or settle gap) is active."""
    import time as _time
    discord_tts_busy["value"] = bool(req.busy)
    discord_tts_busy["updated_at"] = _time.time()
    return {"busy": bool(discord_tts_busy["value"])}


@router.post("/discord_overhear")
async def post_discord_overhear(req: DiscordOverhearRequest) -> dict:
    """Append one overheard VC line (no reply) + feed live memory turn log."""
    text = (req.text or "").strip()
    if not text:
        return {"ok": False, "stored": len(discord_overhear)}
    import time as _time
    user_id = (req.user_id or "").strip() or None
    display = (req.display or "").strip() or None
    role = (req.role or "").strip() or None
    discord_overhear.append({
        "text": text,
        "user_id": user_id,
        "display": display,
        "role": role,
        "at": _time.time(),
    })
    live_seq = 0
    try:
        from server.config import ENABLE_LIVE_MEMORY
        if ENABLE_LIVE_MEMORY:
            from server.memory import live_memory
            live_seq = live_memory.record_turn(
                body=text,
                source="overhear",
                user_id=user_id,
                display=display,
                role=role,
                speaker=display,
            )
    except Exception as e:
        logger.warning("live_memory overhear failed: %s", e)
    return {"ok": True, "stored": len(discord_overhear), "live_seq": live_seq}


@router.get("/discord_overhear")
async def get_discord_overhear(limit: int = 40, clear: bool = False) -> dict:
    """Return recent overheard VC lines for injection into a wake-word turn."""
    items = list(discord_overhear)[-max(1, min(limit, 80)) :]
    if clear:
        discord_overhear.clear()
    return {"lines": items, "count": len(items)}


@router.get("/live_memory/context")
async def get_live_memory_context(
    user_id: Optional[str] = None,
    query: Optional[str] = None,
) -> dict:
    """Working + durable + speaker profile + turn log (+ keyword search if query)."""
    from server.config import ENABLE_LIVE_MEMORY
    if not ENABLE_LIVE_MEMORY:
        return {"enabled": False, "context": "", "stats": {}}
    from server.memory import live_memory
    ctx = live_memory.get_context(user_id, query=query)
    return {"enabled": True, "context": ctx, "stats": live_memory.stats()}


@router.get("/live_memory/recall")
async def get_live_memory_recall(
    q: str = "",
    user_id: Optional[str] = None,
    limit: int = 40,
) -> dict:
    """Exact turn-log search (no FAISS)."""
    from server.config import ENABLE_LIVE_MEMORY
    if not ENABLE_LIVE_MEMORY:
        return {"enabled": False, "text": "", "lines": []}
    from server.memory import live_memory
    from server.memory import live_store
    rows = live_store.search_turns(q, user_id=user_id, limit=limit)
    text = live_memory.recall(q, user_id=user_id, limit=limit)
    return {"enabled": True, "text": text, "lines": rows, "count": len(rows)}


@router.post("/live_memory/turn")
async def post_live_memory_turn(req: LiveMemoryTurnRequest) -> dict:
    """Record a wake user utterance or Luna's reply into the permanent log."""
    from server.config import ENABLE_LIVE_MEMORY
    if not ENABLE_LIVE_MEMORY:
        return {"ok": False, "enabled": False}
    text = (req.text or "").strip()
    if not text:
        return {"ok": False, "enabled": True, "seq": 0}
    from server.memory import live_memory
    source = (req.source or "wake").strip().lower()
    if source not in ("wake", "reply", "local", "overhear"):
        source = "wake"
    speaker = (req.speaker or "").strip() or None
    if source == "reply" and not speaker:
        speaker = "Luna"
    seq = live_memory.record_turn(
        body=text,
        source=source,
        user_id=(req.user_id or "").strip() or None,
        display=(req.display or "").strip() or None,
        role=(req.role or "").strip() or None,
        speaker=speaker,
    )
    return {"ok": True, "enabled": True, "seq": seq}


@router.get("/live_memory/stats")
async def get_live_memory_stats() -> dict:
    from server.config import ENABLE_LIVE_MEMORY
    if not ENABLE_LIVE_MEMORY:
        return {"enabled": False}
    from server.memory import live_memory
    return {"enabled": True, **live_memory.stats()}


@router.get("/pending_text")
async def get_pending_text() -> dict:
    """Polled by the agent's BrowserVoiceInput. Fires on version change."""
    return dict(pending_text)


@router.post("/upload-image/")
async def upload_image(file: UploadFile = File(...),
                       text: Optional[str] = Form(None)) -> dict:
    """Receive an image (+ optional text) from the browser picker.

    ALWAYS bumps pending_text (even with no text) so an image-only upload
    still triggers a turn: BrowserVoiceInput emits the message with
    `image_path` metadata, and run_turn attaches the file as Responses-API
    input_image — the model sees the image directly.

    For image + VOICE, the picture goes to /upload-audio/ instead — one
    request, one turn.
    """
    dest = await _save_image(file)

    pending_text["version"] += 1
    pending_text["text"] = text or ""
    pending_text["image_path"] = dest
    logger.info(
        f"[browser-input] image queued as a turn (text={text!r}, "
        f"text v{pending_text['version']})"
    )
    return {"status": "received", "saved_to": dest,
            "text": text, "version": pending_text["version"]}


if __name__ == "__main__":
    print("[browser-input] this is a route module — mount via server.server.")
    print("  verify with:")
    from server.config import SERVER_PORT
    print(f"    curl -F file=@sample.webm 127.0.0.1:{SERVER_PORT}/upload-audio/")
    print(f"    curl 127.0.0.1:{SERVER_PORT}/pending_voice")
