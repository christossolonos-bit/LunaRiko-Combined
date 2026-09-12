"""Parse Discord VC speaker tags from bridge-labeled user text.

Expected prefix from discord-bridge:
  [Discord VC <@123> Display (@user) role=owner YOU (owner)]: hello
  [Discord VC <@456> Name (@name) role=guest other user]: hi
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

_DISCORD_VC_RE = re.compile(
    r"^\[Discord VC <@(?P<id>\d+)>\s+"
    r"(?P<display>.+?)\s+"
    r"\(@(?P<username>[^)]+)\)\s+"
    r"role=(?P<role>owner|guest)\s+"
    r"(?P<who>YOU \(owner\)|other user)\]:\s*",
    re.DOTALL,
)

# Older label format (still accept)
_DISCORD_VOICE_RE = re.compile(
    r"^\[Discord voice\s*[—\-]\s*(?P<display>[^\]]+)\]:\s*",
    re.DOTALL,
)


@dataclass(frozen=True)
class DiscordSpeaker:
    subject_id: str
    display: str
    username: str
    role: str  # owner | guest
    who_label: str


def parse_discord_speaker(user_text: str) -> Optional[DiscordSpeaker]:
    text = (user_text or "").lstrip()
    m = _DISCORD_VC_RE.match(text)
    if m:
        return DiscordSpeaker(
            subject_id=m.group("id"),
            display=m.group("display").strip(),
            username=m.group("username").strip(),
            role=m.group("role").strip().lower(),
            who_label=m.group("who").strip(),
        )
    m2 = _DISCORD_VOICE_RE.match(text)
    if m2:
        return DiscordSpeaker(
            subject_id="unknown",
            display=m2.group("display").strip(),
            username=m2.group("display").strip(),
            role="guest",
            who_label="other user",
        )
    return None


def strip_discord_prefix(user_text: str) -> str:
    text = user_text or ""
    m = _DISCORD_VC_RE.match(text.lstrip())
    if m:
        return text.lstrip()[m.end() :].strip()
    m2 = _DISCORD_VOICE_RE.match(text.lstrip())
    if m2:
        return text.lstrip()[m2.end() :].strip()
    return text.strip()


def speaker_context_block(speaker: DiscordSpeaker) -> str:
    """Injected every Discord turn so the LLM does not mix people."""
    same = ""
    if speaker.role == "owner" or "YOU (owner)" in speaker.who_label:
        same = (
            " This is Solonaras — same person on Discord AND the website/mic. "
            "Answer HIM in second person; never narrate Solonaras in third person."
        )
    return (
        f"CURRENT SPEAKER: {speaker.display} (@{speaker.username}) "
        f"<@{speaker.subject_id}> role={speaker.role} ({speaker.who_label})."
        f"{same}\n"
        "Speak TO this person only. Travis, T4rik, and Solonaras are different "
        "people — never move hobbies, trips, or preferences across them. "
        "Only mention another person's facts if THIS speaker asks about them."
    )


def recent_facts_about_speaker(history: List[dict], speaker: DiscordSpeaker, limit: int = 10) -> str:
    """Pull this speaker's own prior user lines from shared history (memory-off path)."""
    if not speaker.subject_id or speaker.subject_id == "unknown":
        return ""
    needle = f"<@{speaker.subject_id}>"
    collected: list[str] = []
    for m in history:
        if m.get("role") != "user":
            continue
        content = m.get("content") or ""
        if needle not in content:
            continue
        body = strip_discord_prefix(content)
        if body:
            collected.append(body)
    if not collected:
        return ""
    # Most recent last in history — take the tail, show newest last.
    tail = collected[-limit:]
    lines = "\n".join(f"- {t}" for t in tail)
    return (
        f"What you already know from {speaker.display} (<@{speaker.subject_id}>) "
        f"in this conversation (theirs only — not other callers):\n{lines}"
    )


def scope_history_for_speaker(
    history: List[dict],
    speaker: Optional[DiscordSpeaker],
    *,
    keep_other_user_turns: int = 2,
) -> List[dict]:
    """Keep this speaker's thread + a few other recent turns for group context.

    Prevents the model from treating every guest line as 'the user' facts.
    """
    if not speaker or not speaker.subject_id or speaker.subject_id == "unknown":
        return list(history)

    needle = f"<@{speaker.subject_id}>"
    out: list[dict] = []
    other_budget = keep_other_user_turns
    i = 0
    while i < len(history):
        msg = history[i]
        role = msg.get("role")
        if role == "user":
            content = msg.get("content") or ""
            is_self = needle in content
            # Consume following assistant (+ optional tool items) as a block.
            block = [msg]
            j = i + 1
            while j < len(history) and history[j].get("role") != "user":
                block.append(history[j])
                j += 1
            if is_self:
                out.extend(block)
            elif other_budget > 0:
                # Keep short awareness of others, but mark them clearly if needed.
                out.extend(block)
                other_budget -= 1
            i = j
            continue
        # Leading non-user (shouldn't happen often) — keep.
        out.append(msg)
        i += 1
    return out
