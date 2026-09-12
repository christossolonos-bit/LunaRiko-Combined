"""VR routes: HMD pose tracking.

  POST /vr/position — `client/vr/vrPositionTracker.js` POSTs the HMD pose here
                      every frame while a VR session is active.
  GET  /vr/position — returns the last-known pose. Useful for the avatar
                      world to "see" where the user is standing.
"""

from __future__ import annotations

from fastapi import APIRouter

from server.routes._common import _vr_position
from server.routes.schemas import VRPositionUpdate


router = APIRouter()


@router.post("/vr/position")
async def update_vr_position(pos: VRPositionUpdate) -> dict:
    """Store the most recent HMD pose."""
    _vr_position.update({
        "x": pos.x, "y": pos.y, "z": pos.z,
        "rx": pos.rx, "ry": pos.ry, "rz": pos.rz,
        "timestamp": pos.timestamp or 0.0,
    })
    return {"status": "ok"}


@router.get("/vr/position")
async def get_vr_position() -> dict:
    """Return the most recently-POSTed HMD pose."""
    return _vr_position


if __name__ == "__main__":
    print("[vr] this is a route module. To exercise the routes, run:")
    print("     python -m server.routes.vr_test")
