# `server/routes/` — every endpoint the server exposes

The Python side and the browser are two separate programs. They only talk over
HTTP and one WebSocket, and this is the list of everything they can say.

You can hit any of these yourself with `curl` or Postman to drive the avatar by
hand — handy for testing, and for scripting her from your own code.

## Running the server

From the **project folder**, not from inside `server/`:

```
python -m server.server
```

(`start_servers.bat` does this for you, along with the web page.)

It has to be run as a module from the project folder, because uvicorn is given
the app as a string path (`"server.server:app"`) and the `server.*` package has
to be importable.

## The files

| File | What is in it |
|---|---|
| `_common.py` | Shared state (`active_connections`, the upload buffers) and the broadcast helpers every module uses. |
| `schemas.py` | All the request models, in one place. |
| `core.py` | The everyday ones: talk, animate, set_state, and the WebSocket. |
| `browser_input.py` | The web page's uploads: mic clip, typed message, image. |
| `movement.py` | Walking, teleporting, base locomotion clips. |
| `additive.py` | Gesture-layer animations that play on top of walking or idling. |
| `vr.py` | Headset pose. |

---

## Core (`core.py`)

| Method | Path | What it does |
|---|---|---|
| GET | `/` | A tiny status page showing how many browser tabs are connected. |
| GET | `/health` | Is the server alive? Returns `{status, service, ws_clients, character}`. **Check this first when something is not working.** |
| WS | `/ws` | The main channel. The browser subscribes here and receives everything below. |
| WS | `/ws_status` | Broadcasts the connection count. Answers `ping`. |
| POST | `/talk` | Play an audio clip on the avatar with lip sync, an expression, and a subtitle. Called by `vrm_client.vrm_talk()`. Also bumps `pending_discord_audio`. |
| GET | `/pending_discord_audio` | `{version, audio_path, audio_text, expression, duration}` — polled by `discord-bridge` so Discord VC plays the same clip as the VRM. |
| POST | `/animate` | Play an animation. `animate_type="auto"` works out `.fbx` vs `.vrma` from the file extension. |
| POST | `/animate_and_talk` | Both at once, with an optional delay between them. |
| POST | `/set_state` | `idle` / `listening` / `thinking` / `talking`. Drives her head and eye idle movement. |
| POST | `/play_sfx` | Play a one-off sound effect. Deliberately NOT through `/talk`: that channel owns the lip sync and runs strictly in order, so an effect sent through it would flap her mouth to a siren and delay her actual reply. |
| POST | `/take_picture` | Ask the browser to upload a webcam frame, and wait up to 10 s for it. |

## Browser input (`browser_input.py`)

The web page and the conversation loop are different processes, so uploads land
here and wait in memory until the loop's `BrowserVoiceInput` polls for them.
Only used when `VOICE_SOURCE = "browser"` in `server/config.py`.

| Method | Path | What it does |
|---|---|---|
| POST | `/upload-audio/` | Save the mic-button clip, bump the `pending_voice` version. An optional `image` part can ride along. |
| GET | `/pending_voice` | `{version, path, image_path}` — polled about twice a second; a version change means "there is a new clip". |
| POST | `/send_message` | Buffer a message typed into the box. |
| GET | `/pending_text` | `{version, text}` — polled the same way. |
| POST | `/upload-image/` | Save an image to `uploads/` and attach it to the next turn, so she can actually see it. Optional `text` rides along. |

## Movement (`movement.py`)

| Method | Path | What it does |
|---|---|---|
| POST | `/walk_to` | Walk to `(x, y, z)` at `speed`. |
| POST | `/stop_movement` | Stop walking, return to idle. |
| POST | `/teleport_to` | Snap to `(x, y, z)` with no walk animation. |
| POST | `/set_movement_speed` | Change walking speed. The animation tempo follows. |
| POST | `/load_movement_animation` | Load a `walk` or `idle` base clip. |
| POST | `/set_movement_lock_duration` | How long head movement is suppressed after a state change. |
| GET | `/movement_status` | Where she is and what she is doing. |
| POST | `/walk_to_position` | Same as `/walk_to` but with query parameters: `?x=2&y=0&z=3&speed=1.5`. |
| POST | `/preset_positions/{name}` | Walk to a named spot: `center`, `left`, `right`, `forward`, `back`, `stage_left`, `stage_right`, `downstage`, `upstage`. |

## Additive animations (`additive.py`)

These layer **on top of** whatever she is already doing — a wave while walking,
rather than instead of walking.

| Method | Path | What it does |
|---|---|---|
| POST | `/load_additive_animation` | Register a clip under a name. Does not play it. |
| POST | `/set_additive_weight` | Blend a registered layer in or out, `0` to `1`. |
| POST | `/play_additive_once` | One-shot gesture with automatic fade in and out. |
| POST | `/load_and_play_additive` | Both of the above in one call. |

## VR (`vr.py`)

| Method | Path | What it does |
|---|---|---|
| POST | `/vr/position` | The headset's position, sent every frame during a VR session. |
| GET | `/vr/position` | The last position received. |

## Static files (`server.py`)

| Path | What it does |
|---|---|
| `/audio/<file>` | Serves `client/audio/` — the same wavs the web page server does. This is what lets a browser on another device resolve the audio paths `/talk` sends out. |

---

## Two rules that will bite you

**Paths sent to `/talk` and `/animate` must be browser-relative.** The server
just forwards them to the browser, which fetches them over HTTP from its own
address. So:

```
"animations/mixamo/Backflip.fbx"   works
"audio/output_abc.wav"             works
"C:\...\Backflip.fbx"              silently does nothing
```

An absolute path produces no error anywhere on the Python side. The animation
simply never plays.

**Folder names with spaces must be percent-encoded.** The gesture pack lives in
`client/animations/mixamo/Gestures Pack Basic/`, and an un-encoded space breaks
the browser's fetch with no message. Same symptom as above: nothing happens.

---

## Adding an endpoint

1. Add the request model to `schemas.py`.
2. Add the handler to whichever module fits, with a one-line docstring saying
   what it does and who calls it.
3. If you made a new module, add `app.include_router(your_module.router)` in
   `server/server.py`.
4. Add a row to this file.
