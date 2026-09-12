"""The bridge between Python and the browser. Start this first.

It does two jobs:
  - takes commands from the Python side (speak this clip, play this
    animation, look sad) and pushes them to the web page over a WebSocket;
  - takes uploads from the web page (mic clip, typed message, image) and
    parks them until the conversation loop picks them up.

Run it from the PROJECT FOLDER, not from inside server/:

    python -m server.server

Every endpoint lives in `server/routes/`. To add one:
  1. Add the request model to `routes/schemas.py`.
  2. Add the handler to whichever module fits (or start a new one).
  3. If it is a new module, add an `app.include_router(...)` line below.

The full list of endpoints is in `server/routes/README.md`.
"""

from __future__ import annotations

import logging

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from server.config import CLIENT_AUDIO_DIR, SERVER_PORT
from server.routes import additive, browser_input, core, movement, vision, vr

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def build_app() -> FastAPI:
    """Factory, so a test can stand the app up without touching the global one."""
    app = FastAPI(title="Riko VRM Server")

    # The page is served by Vite on :5173 and talks to us on SERVER_PORT — a
    # different origin, so it needs CORS.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(core.router)          # talk, animate, set_state, /ws
    app.include_router(movement.router)      # walk, teleport, locomotion clips
    app.include_router(additive.router)      # gesture-layer animations
    app.include_router(vr.router)            # headset pose
    app.include_router(browser_input.router) # the web page's uploads
    app.include_router(vision.router)        # /mute + /vision/frame (LLaVA)

    # Serve the generated speech ourselves as well as Vite. A page opened from
    # another device resolves `audio/<file>.wav` against whatever host served
    # it, so this is what makes remote viewing work without a second config.
    if CLIENT_AUDIO_DIR.is_dir():
        app.mount("/audio", StaticFiles(directory=CLIENT_AUDIO_DIR), name="audio")
        logger.info(f"Serving /audio from {CLIENT_AUDIO_DIR}")
    else:
        logger.warning(f"No audio dir at {CLIENT_AUDIO_DIR} — /audio not mounted")

    return app


app = build_app()


if __name__ == "__main__":
    # host="0.0.0.0" so a phone or another computer on your network can reach
    # it. reload=False because the playback worker does not survive reloads.
    #
    # The port comes from server/config.py. If binding fails with "access
    # permissions" or "address already in use" and nothing of yours is running,
    # Windows has reserved the port — see the SERVER_PORT comment there.
    # 127.0.0.1 rather than localhost on purpose — see VRM_BASE_URL in
    # server/config.py for the 2-second reason.
    print(f"[server] listening on http://127.0.0.1:{SERVER_PORT}")
    uvicorn.run("server.server:app", host="0.0.0.0", port=SERVER_PORT, reload=False)
