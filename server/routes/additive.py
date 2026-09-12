"""Additive animation layer routes.

Additive animations sit *on top of* the base locomotion (idle / walk /
run). Use them for gestures like waving while walking, pointing while
standing, etc.

  POST /set_additive_weight       — adjust an existing layer's strength (0..1)
  POST /play_additive_once        — fire a one-shot gesture (fade in → out)
  POST /load_additive_animation   — register a new additive clip (no play)
  POST /load_and_play_additive    — load + immediately apply weight or play once
"""

from __future__ import annotations

from fastapi import APIRouter

from server.routes._common import notify_clients
from server.routes.schemas import (
    AdditiveWeightRequestWithDuration,
    LoadAdditiveAnimationRequest,
    LoadAndPlayAdditiveRequest,
    PlayAdditiveOnceRequest,
)


router = APIRouter()


@router.post("/set_additive_weight")
async def set_additive_weight(req: AdditiveWeightRequestWithDuration) -> dict:
    """Set the blend weight (0..1) of an already-loaded additive layer."""
    weight = max(0.0, min(1.0, req.weight))
    payload = {
        "type": "set_additive_weight",
        "anim_name": req.anim_name,
        "weight": weight,
        "duration": req.duration,
    }
    await notify_clients(payload)
    return {
        "status": "weight_updated",
        "anim_name": req.anim_name,
        "weight": weight,
        "duration": req.duration,
    }


@router.post("/play_additive_once")
async def play_additive_once(req: PlayAdditiveOnceRequest) -> dict:
    """Play an additive layer once, with auto fade-in and fade-out."""
    payload = {
        "type": "play_additive_once",
        "anim_name": req.anim_name,
        "fade_in": req.fade_in,
        "fade_out": req.fade_out,
    }
    await notify_clients(payload)
    return {"status": "playing", "anim_name": req.anim_name}


@router.post("/load_additive_animation")
async def load_additive_animation(req: LoadAdditiveAnimationRequest) -> dict:
    """Load a new additive clip into the client and register it under `name`."""
    payload = {
        "type": "load_additive_animation",
        "url": req.url,
        "name": req.name,
    }
    await notify_clients(payload)
    return {"status": "loading", "name": req.name, "url": req.url}


@router.post("/load_and_play_additive")
async def load_and_play_additive(req: LoadAndPlayAdditiveRequest) -> dict:
    """Load + immediately play / weight an additive in one round-trip."""
    weight = max(0.0, min(1.0, req.weight or 1.0))
    payload = {
        "type": "load_and_play_additive",
        "url": req.url,
        "name": req.name,
        "weight": weight,
        "play_once": req.play_once,
        "fade_in": req.fade_in,
        "fade_out": req.fade_out,
    }
    await notify_clients(payload)
    return {
        "status": "loading_and_playing",
        "name": req.name,
        "url": req.url,
        "weight": weight,
        "play_once": req.play_once,
    }


if __name__ == "__main__":
    print("[additive] this is a route module. To exercise the routes, run:")
    print("           python -m server.routes.additive_test")
