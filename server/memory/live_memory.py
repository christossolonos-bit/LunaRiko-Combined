"""Live Discord/local memory — working + durable + per-user profiles + turn log.

Adapted from the discord-agent idea (not a port): every overheard VC line and
every wake/reply turn is written to SQLite immediately; a background Ollama
pass rewrites working/durable/profile docs. Retrieval injects those docs on
wake turns. Never OpenAI.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from server.config import CHARACTER, MEMORY_LOCAL_MODEL
from server.memory import live_store, mem_llm

WORKING_MAX = 2500
DURABLE_MAX = 5000
PROFILE_FIELD_MAX = 800
TURN_FEED_MAX = 60
RECENT_INJECT = 25

_consolidating = False
_pending = False
_bg_lock = threading.Lock()


class ProfileCard(BaseModel):
    goals: str = ""
    active_projects: str = ""
    constraints: str = ""
    vibe_notes: str = ""
    notes: str = ""
    name: str = ""


class LiveConsolidation(BaseModel):
    durable: str = Field(default="", description="Guild/group long-term facts, dated when possible.")
    working: str = Field(
        default="",
        description="Live conversation context: topic, speakers, open questions.",
    )
    profiles: dict[str, ProfileCard] = Field(
        default_factory=dict,
        description="Keyed by Discord user_id when known, else display name.",
    )


def record_turn(
    *,
    body: str,
    source: str = "overhear",
    user_id: Optional[str] = None,
    display: Optional[str] = None,
    role: Optional[str] = None,
    speaker: Optional[str] = None,
    trigger_consolidate: bool = True,
) -> int:
    """Append a permanent turn. Optionally kick background consolidation."""
    seq = live_store.append_turn(
        source=source,
        body=body,
        speaker=speaker or display or user_id or ("Luna" if source == "reply" else "someone"),
        user_id=user_id,
        display=display,
        role=role,
    )
    if seq and trigger_consolidate:
        schedule_consolidate()
    return seq


def schedule_consolidate() -> None:
    """Coalesce: one in-flight job; if turns arrive mid-run, loop once more."""
    global _consolidating, _pending
    with _bg_lock:
        if _consolidating:
            _pending = True
            return
        _consolidating = True

    def _run() -> None:
        global _consolidating, _pending
        try:
            while True:
                try:
                    consolidate_once()
                except Exception as e:
                    print(f"[live-mem] consolidate failed: {type(e).__name__}: {e}")
                with _bg_lock:
                    if _pending:
                        _pending = False
                        continue
                    _consolidating = False
                    return
        finally:
            with _bg_lock:
                _consolidating = False

    threading.Thread(target=_run, name="luna-live-mem", daemon=True).start()


def _format_turns(turns: list[dict[str, Any]]) -> str:
    lines = []
    for t in turns:
        who = t.get("speaker") or t.get("display") or t.get("user_id") or "?"
        uid = t.get("user_id")
        role = t.get("role") or ""
        src = t.get("source") or ""
        tag = f" <@{uid}>" if uid else ""
        role_bit = f" role={role}" if role else ""
        lines.append(f"[{src}] {who}{tag}{role_bit}: {t.get('body', '')}")
    return "\n".join(lines)


def _profiles_block(turns: list[dict[str, Any]]) -> str:
    seen: dict[str, str] = {}
    for t in turns:
        uid = t.get("user_id")
        if not uid:
            continue
        seen[str(uid)] = t.get("display") or t.get("speaker") or str(uid)
    if not seen:
        return "(no member profiles this round)"
    lines = []
    for uid, name in seen.items():
        card = live_store.get_profile(uid) or {}
        lines.append(f"{name} id={uid}: {card if card else '(no card yet)'}")
    return "\n".join(lines)


def consolidate_once() -> bool:
    turns = live_store.unconsolidated_turns(limit=TURN_FEED_MAX)
    if not turns:
        return False

    today = datetime.now().strftime("%Y-%m-%d")
    name = CHARACTER.capitalize()
    durable = live_store.get_doc("durable") or "(empty)"
    working = live_store.get_doc("working") or "(empty)"
    turns_block = _format_turns(turns)
    profiles_block = _profiles_block(turns)

    system = (
        f"You update memory for {name}. Reply with JSON only.\n"
        "Fields: durable (string), working (string), profiles (object).\n"
        "profiles keys = Discord user_id digits from the turns (e.g. \"111\").\n"
        "Each profile value: goals, active_projects, constraints, vibe_notes, notes, name.\n"
        "Person facts → that profile. Shared call facts → durable. "
        "Current topic / open questions → working.\n"
        "Be specific. Do not invent. Empty string if nothing new for a field.\n"
        f"Keep durable <{DURABLE_MAX} chars, working <{WORKING_MAX}. Today={today}."
    )
    user = (
        f"DURABLE:\n{durable}\n\n"
        f"WORKING:\n{working}\n\n"
        f"PROFILES:\n{profiles_block}\n\n"
        f"NEW TURNS:\n{turns_block}\n\n"
        "Merge NEW TURNS into durable/working/profiles. "
        "Always fill working with the live topic from these turns."
    )
    parsed = mem_llm.complete_json(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        LiveConsolidation,
        "local",
        MEMORY_LOCAL_MODEL,
    )

    new_durable = (parsed.durable or "").strip()
    new_working = (parsed.working or "").strip()
    if not new_working:
        new_working = "Recent call:\n" + "\n".join(
            f"- {t.get('speaker')}: {t.get('body')}" for t in turns[-12:]
        )
    if new_durable:
        live_store.set_doc("durable", new_durable[:DURABLE_MAX])
    live_store.set_doc("working", new_working[:WORKING_MAX])

    # Map profile keys → user_id. ONLY accept keys that are real ids from
    # this batch (or exact display→id within this batch). Never invent ids.
    id_by_name: dict[str, str] = {}
    batch_ids: set[str] = set()
    for t in turns:
        raw_uid = t.get("user_id")
        if not raw_uid:
            continue
        uid = str(raw_uid)
        batch_ids.add(uid)
        for label in (t.get("display"), t.get("speaker")):
            if label:
                id_by_name[str(label).strip().lower()] = uid

    for key, card in (parsed.profiles or {}).items():
        key_s = str(key).strip()
        if key_s in batch_ids:
            uid = key_s
        elif key_s.isdigit() and key_s in batch_ids:
            uid = key_s
        else:
            uid = id_by_name.get(key_s.lower())
        if not uid or uid not in batch_ids:
            print(f"[live-mem] skip profile key={key_s!r} (not in this batch)")
            continue
        fields = card.model_dump() if hasattr(card, "model_dump") else dict(card)
        if not fields.get("name"):
            fields["name"] = next(
                (t.get("display") for t in turns if str(t.get("user_id")) == uid),
                key_s,
            )
        trimmed = {
            k: str(v)[:PROFILE_FIELD_MAX]
            for k, v in fields.items()
            if v is not None and str(v).strip()
        }
        if trimmed:
            live_store.save_profile(uid, trimmed)

    live_store.mark_consolidated(int(turns[-1]["seq"]))
    print(
        f"[live-mem] consolidated {len(turns)} turn(s) "
        f"up_to=seq{turns[-1]['seq']} profiles={len(parsed.profiles or {})}"
    )
    return True


def _format_profile(user_id: Optional[str]) -> str:
    if not user_id:
        return ""
    card = live_store.get_profile(str(user_id))
    if not card:
        return ""
    lines = [f"Name: {card.get('name') or '?'}"]
    for label, key in (
        ("Goals", "goals"),
        ("Active projects", "active_projects"),
        ("Constraints", "constraints"),
        ("Vibe notes", "vibe_notes"),
        ("Notes", "notes"),
    ):
        if card.get(key):
            lines.append(f"{label}: {card[key]}")
    return "[SPEAKER PROFILE — who you're talking to right now]\n" + "\n".join(lines)


def get_context(
    user_id: Optional[str] = None,
    *,
    recent_limit: int = RECENT_INJECT,
    query: Optional[str] = None,
) -> str:
    """Prompt injection — profile + working/durable + turn log (+ optional search)."""
    uid = str(user_id).strip() if user_id else None
    parts: list[str] = [
        "MEMORY RULES (HARD):\n"
        "- CURRENT SPEAKER facts come only from their SPEAKER PROFILE and "
        "their own lines below.\n"
        "- Other people's lines are overheard context — never treat them as "
        "facts ABOUT the current speaker.\n"
        "- Do not invent hobbies, trips, instruments, or preferences for "
        "someone unless their own profile/lines say so.\n"
        "- If unsure who a fact belongs to, stay quiet about it.\n"
        "- Prefer the turn log and profile over any fuzzy guess.\n"
        "- If they ask their own name / who they are / what you remember "
        "about them: answer with the known fact in the first sentence "
        "(e.g. say Solonaras). Do not refuse, stall, or tease-withhold."
    ]

    profile = _format_profile(uid)
    if profile:
        parts.append(profile)

    durable = live_store.get_doc("durable").strip()
    if durable:
        parts.append(
            "[DURABLE — shared group facts only, not one person's biography]\n"
            + durable
        )

    working = live_store.get_doc("working").strip()
    if working:
        parts.append(
            "[WORKING — live call topic; keep people distinct]\n" + working
        )

    # Exact keyword search when they ask what was said / remembered.
    q = (query or "").strip()
    if q and _looks_like_recall(q):
        # Search this speaker first, then whole log if thin.
        hit = recall(_recall_keywords(q), user_id=uid, limit=20)
        if hit == "(no matching turns)" and uid:
            hit = recall(_recall_keywords(q), user_id=None, limit=20)
        if hit != "(no matching turns)":
            parts.append(
                "[TURN LOG SEARCH — exact matches for this question; "
                "attribute each line to the named speaker only]\n" + hit
            )

    recent = live_store.recent_turns(limit=max(recent_limit * 2, 40))
    mine: list[str] = []
    others: list[str] = []
    for t in recent:
        src = t.get("source") or ""
        # Skip her own long replies — they often invent and then re-poison memory.
        if src == "reply":
            continue
        who = t.get("speaker") or t.get("display") or "?"
        body = (t.get("body") or "").strip()
        if not body:
            continue
        line = f"- [{src}] {who}: {body}"
        t_uid = str(t.get("user_id") or "")
        if uid and t_uid == uid:
            mine.append(line)
        else:
            others.append(line)

    if mine:
        parts.append(
            "THIS SPEAKER's own recent lines (trusted for their facts):\n"
            + "\n".join(mine[-recent_limit:])
        )
    if others:
        parts.append(
            "OTHER people in the call (overheard only — do NOT attribute "
            "these facts to the current speaker unless they ask about them):\n"
            + "\n".join(others[-recent_limit:])
        )

    return "\n\n".join(parts)


_RECALL_HINTS = (
    "remember", "remind", "what did", "what was", "what were", "earlier",
    "before", "talking about", "said about", "told you", "you know about",
    "recall", "last time", "we talked", "did i say", "did he say", "did she say",
)


def _looks_like_recall(text: str) -> bool:
    low = (text or "").lower()
    return any(h in low for h in _RECALL_HINTS)


def _recall_keywords(text: str) -> str:
    """Strip wake fluff so LIKE search hits content words."""
    import re
    t = (text or "").strip()
    t = re.sub(
        r"\b(luna|hey luna|okay luna|ok luna|please|remind me|do you remember)\b",
        " ",
        t,
        flags=re.I,
    )
    t = re.sub(r"[^\w\s]", " ", t)
    words = [w for w in t.split() if len(w) > 2]
    # Prefer content words; fall back to original if nothing left.
    return " ".join(words[:8]) if words else text.strip()[:80]


def recall(query: str = "", *, user_id: Optional[str] = None, limit: int = 40) -> str:
    rows = live_store.search_turns(query, user_id=user_id, limit=limit)
    if not rows:
        return "(no matching turns)"
    return "\n".join(
        f"[{t.get('source')}] {t.get('speaker')}: {t.get('body')}" for t in rows
    )


def stats() -> dict[str, Any]:
    return {
        "turns": live_store.turn_count(),
        "working_chars": len(live_store.get_doc("working")),
        "durable_chars": len(live_store.get_doc("durable")),
        "consolidating": _consolidating,
        "db": str(live_store._db_path()),
    }
