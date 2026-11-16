"""Core routes: WebSockets + the bread-and-butter VRM-driving endpoints.

  GET  /                  — status HTML page (dev convenience)
  GET  /health            — liveness probe (used by the phone link harness)
  WS   /ws                — main client WS: VRM commands flow OUTBOUND through here
  WS   /ws_status         — status WS: receives `count_update` pings
  POST /talk              — speak: forward audio_path + expression + duration
  POST /animate           — play an animation (auto-detects mixamo vs vrma)
  POST /animate_and_talk  — one-shot combined animation + speech
  POST /set_state         — set avatar state: idle / listening / thinking / talking
  POST /play_sfx          — play a one-off sound effect (own audio element, no lipsync)
  POST /take_picture      — request a webcam frame from the client and wait for upload
                            (currently dormant — re-activates when /upload-image/ is added back)
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from server.config import CHARACTER
from server.routes._common import (
    active_connections,
    broadcast_status,
    discord_music,
    discord_vc_active,
    enqueue_discord_audio,
    latest_uploaded_image,
    notify_clients,
    pop_discord_audio,
    reply_audio_target,
    status_connections,
)
from server.routes.schemas import (
    AnimationPayload,
    CombinedPayload,
    PlaySfxRequest,
    SetStateRequest,
    TalkRequest,
)


logger = logging.getLogger(__name__)
router = APIRouter()


_STATUS_HTML = """
<!DOCTYPE html>
<html>
  <head><title>VRM Trigger Server</title></head>
  <body>
    <h1>VRM Trigger Server</h1>
    <p>WebSocket clients: <span id="count">0</span></p>
    <script>
      const ws = new WebSocket(`ws://${location.host}/ws_status`);
      ws.onmessage = e => {
        const msg = JSON.parse(e.data);
        if (msg.type === 'count_update') {
          document.getElementById('count').textContent = msg.count;
        }
      };
    </script>
  </body>
</html>
"""


@router.get("/")
async def root() -> HTMLResponse:
    """Tiny status page — shows live `/ws` client count."""
    return HTMLResponse(_STATUS_HTML)


@router.get("/health")
async def health() -> dict:
    """Is the server up? Curl this first when something is not working."""
    return {
        "status": "ok",
        "service": "riko-vrm-bridge",
        "ws_clients": len(active_connections),
        # Which persona this server launched as. The browser client reads it so
        # a page opened without `?character=` loads the matching VRM instead of
        # guessing — see client/config.js.
        "character": CHARACTER,
    }


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    """Main outbound WS — the browser subscribes here to receive VRM commands."""
    await ws.accept()
    active_connections.add(ws)
    logger.info(f"Client connected: {ws.client} (total {len(active_connections)})")
    await broadcast_status(len(active_connections))
    try:
        while True:
            # Drain inbound messages to keep the connection alive. Currently
            # we don't act on anything the client says here; future work
            # (animation-finished callback in Milestone 9) will read these.
            await ws.receive_text()
    except WebSocketDisconnect:
        active_connections.discard(ws)
        logger.info(
            f"Client disconnected: {ws.client} (total {len(active_connections)})"
        )
        await broadcast_status(len(active_connections))
    except Exception as e:
        logger.error(f"WS error: {e}")
        active_connections.discard(ws)
        await broadcast_status(len(active_connections))


@router.websocket("/ws_status")
async def ws_status(ws: WebSocket) -> None:
    """Status WS — broadcasts the live client count. Responds to `ping`."""
    await ws.accept()
    status_connections.add(ws)
    await ws.send_text(
        f'{{"type": "count_update", "count": {len(active_connections)}}}'
    )
    try:
        while True:
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_text("pong")
    except WebSocketDisconnect:
        status_connections.discard(ws)
    except Exception:
        status_connections.discard(ws)


@router.post("/talk")
async def talk(req: TalkRequest) -> dict:
    """Forward TTS to Discord and/or the browser depending on reply_audio_target."""
    target = (reply_audio_target.get("value") or "both").strip().lower()
    if target not in ("discord", "discord_chat", "browser", "both"):
        target = "both"

    music_on = bool(discord_music.get("playing"))

    # /luna play: don't queue Discord talk over the song. Browser can still play.
    if music_on and target in ("discord", "discord_chat"):
        logger.info(
            "talk dropped — discord music playing (%r)",
            discord_music.get("title"),
        )
        return {
            "status": "dropped",
            "reason": "music_playing",
            "title": discord_music.get("title"),
        }

    if target == "discord" and not music_on:
        enqueue_discord_audio(
            audio_path=req.audio_path,
            audio_text=req.audio_text,
            expression=req.expression,
            duration=req.audio_duraction,
        )

    if target == "discord_chat" and not music_on:
        from server.routes._common import append_discord_chat_clip
        append_discord_chat_clip(req.audio_path)
        # Still drive the VRM lips on the desktop overlay.
        payload = {
            "type": "start_animation",
            "audio_path": req.audio_path,
            "expression": req.expression,
            "audio_text": req.audio_text,
            "audio_duraction": req.audio_duraction,
            "skip_browser_audio": True,
        }
        await notify_clients(payload)
        logger.info(
            "talk → Discord chat reply clip (VRM lips, no webpage speaker) text=%r",
            (req.audio_text or "")[:80],
        )
        return {
            "status": "sent",
            "reply_audio": "discord_chat",
            "browser": False,
        }

    if target in ("both",) and not music_on:
        enqueue_discord_audio(
            audio_path=req.audio_path,
            audio_text=req.audio_text,
            expression=req.expression,
            duration=req.audio_duraction,
        )

    # Discord VC-only: do NOT push audio to the webpage.
    if target == "discord":
        logger.info(
            "talk → Discord only (no browser WS) text=%r",
            (req.audio_text or "")[:80],
        )
        return {
            "status": "sent",
            "reply_audio": "discord",
            "browser": False,
        }

    payload = {
        "type": "start_animation",
        "audio_path": req.audio_path,
        "expression": req.expression,
        "audio_text": req.audio_text,
        "audio_duraction": req.audio_duraction,
        "skip_browser_audio": False,
    }
    await notify_clients(payload)
    return {"status": "sent", "payload": payload, "reply_audio": target}


@router.get("/pending_discord_audio")
async def get_pending_discord_audio() -> dict:
    """Pop the next TTS clip for discord-bridge (FIFO). Empty audio_path = nothing waiting."""
    from server.routes._common import discord_audio_queue

    item = pop_discord_audio()
    item["queued"] = len(discord_audio_queue)
    return item


@router.post("/animate")
async def animate(payload: AnimationPayload) -> dict:
    """Play an animation. `animate_type="auto"` infers from the URL extension."""
    anim_type = payload.animate_type
    if anim_type == "auto":
        url_lower = payload.animation_url.lower()
        if url_lower.endswith(".vrma"):
            anim_type = "start_vrma"
        elif url_lower.endswith(".fbx"):
            anim_type = "start_mixamo"
        else:
            anim_type = "start_mixamo"  # default for unknown extensions
        logger.info(
            f"Auto-detected animation type: {anim_type} for {payload.animation_url}"
        )
    forwarded = {
        "type": anim_type,
        "animation_url": payload.animation_url,
        "play_once": payload.play_once,
        "crop_start": payload.crop_start,
        "crop_end": payload.crop_end,
        "lock_position": payload.lock_position,
        "track_position": payload.track_position,
    }
    await notify_clients(forwarded)
    return {"status": "sent", "payload": forwarded}


@router.post("/animate_and_talk")
async def animate_and_talk(payload: CombinedPayload) -> dict:
    """One-shot: kick off an animation and speech together."""
    target = (reply_audio_target.get("value") or "both").strip().lower()
    if target not in ("discord", "browser", "both"):
        target = "both"
    music_on = bool(discord_music.get("playing"))

    if target in ("discord", "both") and not music_on:
        enqueue_discord_audio(
            audio_path=payload.audio_path,
            audio_text=getattr(payload, "audio_text", "") or "",
            expression=payload.expression,
            duration=getattr(payload, "audio_duraction", None),
        )

    if target == "discord":
        return {"status": "sent", "reply_audio": "discord", "browser": False}

    forwarded = {
        "type": "start_vrma_and_talk",
        "animation_url": payload.animation_url,
        "audio_path": payload.audio_path,
        "expression": payload.expression,
        "delay": payload.delay,
    }
    await notify_clients(forwarded)
    return {"status": "sent", "payload": forwarded, "reply_audio": target}


@router.post("/set_state")
async def set_state(req: SetStateRequest) -> dict:
    """Set the high-level avatar state. Drives head/eye micro-animations."""
    valid_states = ["idle", "listening", "thinking", "talking"]
    if req.state not in valid_states:
        return {
            "status": "error",
            "message": f"Invalid state: {req.state}",
            "valid_states": valid_states,
        }
    payload = {"type": "set_state", "state": req.state}
    await notify_clients(payload)
    return {"status": "state_set", "state": req.state}


@router.post("/play_sfx")
async def play_sfx(req: PlaySfxRequest) -> dict:
    """Play a one-off sound effect in the browser, OFF the speech channel.

    Deliberately not `/talk`: that drives the persistent `<audio>` element,
    which is the lipsync source and a strict FIFO. The browser plays this on
    its own throwaway element, so an effect never moves the avatar's mouth and
    never queues behind (or ahead of) her speech.
    """
    payload = {"type": "play_sfx", "audio_path": req.audio_path}
    await notify_clients(payload)
    return {"status": "sent", "payload": payload}


@router.post("/take_picture")
async def trigger_picture() -> dict:
    """Ask the client to upload a webcam frame, then wait up to ~10s for it.

    The matching `/upload-image/` endpoint lives in routes/browser_input.py
    (re-added in the UI-wiring milestone) and fills `latest_uploaded_image`.
    """
    latest_uploaded_image["path"] = None
    await notify_clients({"type": "take_picture"})
    for _ in range(20):  # 20 × 0.5s = 10s
        await asyncio.sleep(0.5)
        if latest_uploaded_image["path"] is not None:
            return {"file_location": latest_uploaded_image["path"]}
    return {"error": "Timeout waiting for image upload"}


if __name__ == "__main__":
    # Run the test script with: python -m server.routes.core_test
    print("[core] this is a route module. To exercise the routes, run:")
    print("       python -m server.routes.core_test")
