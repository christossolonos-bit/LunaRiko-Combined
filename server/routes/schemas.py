"""All Pydantic request/response models in one place.

Keeping the schemas centralized makes it easy to see the wire shape of
the whole API at a glance and prevents duplicate / drifting definitions
between route modules.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


# ─── core (/talk, /animate, /animate_and_talk, /set_state) ─────────────

class TalkRequest(BaseModel):
    audio_path: str
    expression: str = "neutral"
    audio_text: str
    # NOTE: field name is intentionally `audio_duraction` (misspelled). The
    # v1 client and the playback wrapper both rely on this exact key — fix
    # alongside a coordinated client update, not here.
    audio_duraction: int


class AnimationPayload(BaseModel):
    animate_type: str  # "start_vrma" / "start_mixamo" / "auto"
    animation_url: str
    play_once: Optional[bool] = False
    crop_start: Optional[float] = 0.0
    crop_end: Optional[float] = 0.0
    lock_position: Optional[bool] = False
    track_position: Optional[bool] = True


class CombinedPayload(BaseModel):
    animation_url: str
    audio_path: str
    expression: str = "neutral"
    delay: float = 0.0


class SetStateRequest(BaseModel):
    state: str  # "idle" | "listening" | "thinking" | "talking"


class PlaySfxRequest(BaseModel):
    # Browser-relative, e.g. "audio/sfx/alarm.wav" — same rule as audio_path.
    audio_path: str


# ─── browser_input (/send_message) ─────────────────────────────────────

class SendMessageRequest(BaseModel):
    message_text: str
    # "discord" → speak only through the Discord bot in VC (not the webpage speaker).
    # Omit / "both" → normal webpage + Discord queue.
    source: Optional[str] = None
    reply_audio: Optional[str] = None


class ReplyAudioRequest(BaseModel):
    target: str = "both"  # "discord" | "browser" | "both"


class DiscordVcRequest(BaseModel):
    active: bool = False


class DiscordMusicRequest(BaseModel):
    playing: bool = False
    title: Optional[str] = None


class DiscordOverhearRequest(BaseModel):
    text: str
    user_id: Optional[str] = None
    display: Optional[str] = None
    role: Optional[str] = None


class LiveMemoryTurnRequest(BaseModel):
    """Wake user line and/or Luna reply — written to the permanent turn log."""
    text: str
    source: str = "wake"  # wake | reply | local
    user_id: Optional[str] = None
    display: Optional[str] = None
    role: Optional[str] = None
    speaker: Optional[str] = None



# ─── movement (/walk_to, /teleport_to, /set_movement_speed, ...) ───────

class WalkToRequest(BaseModel):
    x: float
    y: float
    z: float
    speed: Optional[float] = 1.5


class TeleportRequest(BaseModel):
    x: float
    y: float
    z: float


class SetSpeedRequest(BaseModel):
    speed: float


class LoadAnimationRequest(BaseModel):
    """For `/load_movement_animation` — the base locomotion clips."""
    url: str
    anim_type: str  # "walk" | "idle"


class SetMovementLockRequest(BaseModel):
    duration: float  # seconds — head-motion lock after state transition


# ─── additive (gesture-layer endpoints) ────────────────────────────────

class AdditiveWeightRequestWithDuration(BaseModel):
    anim_name: str
    weight: float  # 0.0 .. 1.0
    duration: Optional[float] = 0.25


class PlayAdditiveOnceRequest(BaseModel):
    anim_name: str
    fade_in: Optional[float] = 0.25
    fade_out: Optional[float] = 0.25


class LoadAdditiveAnimationRequest(BaseModel):
    url: str
    name: str


class LoadAndPlayAdditiveRequest(BaseModel):
    """Load + immediately play in one call."""
    url: str
    name: str
    weight: Optional[float] = 1.0
    play_once: Optional[bool] = False
    fade_in: Optional[float] = 0.25
    fade_out: Optional[float] = 0.25


# ─── vr (/vr/position) ─────────────────────────────────────────────────

class VRPositionUpdate(BaseModel):
    x: float
    y: float
    z: float
    rx: Optional[float] = 0.0
    ry: Optional[float] = 0.0
    rz: Optional[float] = 0.0
    timestamp: Optional[float] = None
