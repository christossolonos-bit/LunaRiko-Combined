"""Movement routes: walking, teleporting, speed, base locomotion clips.

  POST /walk_to                       — walk to (x, y, z)
  POST /stop_movement                 — stop, return to idle
  POST /teleport_to                   — instant position change (no walk)
  POST /set_movement_speed            — adjust walk speed
  POST /load_movement_animation       — load a walk/idle base clip
  POST /set_movement_lock_duration    — head-motion lock after state transition
  POST /walk_to_position              — query-param convenience form of /walk_to
  POST /preset_positions/{name}       — walk to a named preset (center / left / ...)
  GET  /movement_status               — coarse status (clients connected, etc.)
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter

from server.routes._common import active_connections, notify_clients
from server.routes.schemas import (
    LoadAnimationRequest,
    SetMovementLockRequest,
    SetSpeedRequest,
    TeleportRequest,
    WalkToRequest,
)


router = APIRouter()


@router.post("/walk_to")
async def walk_to(req: WalkToRequest) -> dict:
    """Walk the character to a world-space (x, y, z) at `speed` m/s."""
    payload = {
        "type": "walk_to",
        "x": req.x, "y": req.y, "z": req.z,
        "speed": req.speed,
    }
    await notify_clients(payload)
    return {
        "status": "walking",
        "target": {"x": req.x, "y": req.y, "z": req.z},
        "speed": req.speed,
    }


@router.post("/stop_movement")
async def stop_movement() -> dict:
    """Cancel any walking and return to idle."""
    await notify_clients({"type": "stop_movement"})
    return {"status": "stopped"}


@router.post("/teleport_to")
async def teleport_to(req: TeleportRequest) -> dict:
    """Instantly place the character at (x, y, z) — no walk animation."""
    payload = {"type": "teleport_to", "x": req.x, "y": req.y, "z": req.z}
    await notify_clients(payload)
    return {
        "status": "teleported",
        "position": {"x": req.x, "y": req.y, "z": req.z},
    }


@router.post("/set_movement_speed")
async def set_movement_speed(req: SetSpeedRequest) -> dict:
    """Change walking speed. Animation tempo follows."""
    await notify_clients({"type": "set_speed", "speed": req.speed})
    return {"status": "speed_updated", "speed": req.speed}


@router.post("/load_movement_animation")
async def load_movement_animation(req: LoadAnimationRequest) -> dict:
    """Load a base walk or idle clip into the client's movement mixer."""
    if req.anim_type == "walk":
        payload = {"type": "load_walk_animation", "url": req.url}
    elif req.anim_type == "idle":
        payload = {"type": "load_idle_animation", "url": req.url}
    else:
        return {"status": "error", "message": "anim_type must be 'walk' or 'idle'"}
    await notify_clients(payload)
    return {"status": "loading", "anim_type": req.anim_type, "url": req.url}


@router.post("/set_movement_lock_duration")
async def set_movement_lock_duration(req: SetMovementLockRequest) -> dict:
    """Set how long head movement is suppressed after a state transition."""
    duration = max(0.0, req.duration)
    await notify_clients({"type": "set_movement_lock_duration", "duration": duration})
    return {"status": "lock_duration_set", "duration": duration}


@router.get("/movement_status")
async def get_movement_status() -> dict:
    """Coarse status snapshot. For live position, use the WS feed."""
    return {
        "status": "active",
        "connected_clients": len(active_connections),
        "message": "Use WebSocket for real-time position tracking",
    }


@router.post("/walk_to_position")
async def walk_to_position(
    x: float, y: float, z: float, speed: Optional[float] = 1.5,
) -> dict:
    """Query-param flavor of `/walk_to`. Example: `?x=2&y=0&z=3&speed=1.5`."""
    return await walk_to(WalkToRequest(x=x, y=y, z=z, speed=speed))


# Stage-direction presets — handy for OBS/streaming choreography.
_PRESETS = {
    "center":      (0.0, 0.0, 0.0),
    "left":        (-3.0, 0.0, 0.0),
    "right":       (3.0, 0.0, 0.0),
    "forward":     (0.0, 0.0, 3.0),
    "back":        (0.0, 0.0, -3.0),
    "stage_left":  (-5.0, 0.0, 0.0),
    "stage_right": (5.0, 0.0, 0.0),
    "downstage":   (0.0, 0.0, 5.0),
    "upstage":     (0.0, 0.0, -5.0),
}


@router.post("/preset_positions/{position_name}")
async def walk_to_preset(position_name: str, speed: Optional[float] = 1.5) -> dict:
    """Walk to a named stage position. See `_PRESETS` for the list."""
    if position_name not in _PRESETS:
        return {
            "status": "error",
            "message": f"Unknown preset: {position_name}",
            "available_presets": list(_PRESETS.keys()),
        }
    x, y, z = _PRESETS[position_name]
    return await walk_to(WalkToRequest(x=x, y=y, z=z, speed=speed))


if __name__ == "__main__":
    print("[movement] this is a route module. To exercise the routes, run:")
    print("           python -m server.routes.movement_test")
