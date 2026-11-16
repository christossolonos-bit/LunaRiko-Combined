"""Acoustic voice fingerprints — pitch/timbre signatures per Discord user.

Learned from Discord VC PCM features (bridge). Used to:
  - remember each caller's voice character (low/mid/high Hz, warm/bright)
  - confirm / re-identify speakers by cosine similarity of feature vectors
  - inject a short voice label into live profiles + prompt context
"""

from __future__ import annotations

import json
import math
import threading
import time
from typing import Any, Optional

from server.memory import live_store

_lock = threading.Lock()

# Cosine similarity threshold to treat a print as "same voice".
MATCH_THRESHOLD = 0.82
# EMA blend when updating an existing print (higher = trust new sample more).
LEARN_ALPHA = 0.35


def _doc_key(user_id: str) -> str:
    return f"voiceprint:{user_id}"


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na <= 0 or nb <= 0:
        return 0.0
    return float(dot / (na * nb))


def _blend_vector(old: list[float], new: list[float], alpha: float) -> list[float]:
    if not old:
        return list(new)
    if len(old) != len(new):
        return list(new)
    blended = [(1.0 - alpha) * o + alpha * n for o, n in zip(old, new)]
    norm = math.sqrt(sum(x * x for x in blended)) or 1.0
    return [round(x / norm, 4) for x in blended]


def get_voiceprint(user_id: str) -> Optional[dict[str, Any]]:
    raw = live_store.get_doc(_doc_key(str(user_id)))
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def list_voiceprints() -> list[dict[str, Any]]:
    # Docs are keyed; scan known profile keys via turns is heavy — store index.
    raw = live_store.get_doc("voiceprint:_index")
    ids: list[str] = []
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                ids = [str(x) for x in parsed]
        except json.JSONDecodeError:
            ids = []
    out: list[dict[str, Any]] = []
    for uid in ids:
        vp = get_voiceprint(uid)
        if vp:
            out.append(vp)
    return out


def _index_add(user_id: str) -> None:
    uid = str(user_id)
    raw = live_store.get_doc("voiceprint:_index")
    ids: list[str] = []
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                ids = [str(x) for x in parsed]
        except json.JSONDecodeError:
            ids = []
    if uid not in ids:
        ids.append(uid)
        live_store.set_doc("voiceprint:_index", json.dumps(ids))


def learn(
    *,
    user_id: str,
    display: Optional[str] = None,
    vector: list[float],
    pitch_hz: Optional[float] = None,
    pitch_std: Optional[float] = None,
    centroid_hz: Optional[float] = None,
    label: Optional[str] = None,
) -> dict[str, Any]:
    """Merge a new sample into this user's voiceprint."""
    uid = str(user_id).strip()
    if not uid or not vector:
        return {"ok": False, "error": "missing user_id or vector"}

    with _lock:
        existing = get_voiceprint(uid) or {}
        old_vec = existing.get("vector") if isinstance(existing.get("vector"), list) else []
        old_vec_f = [float(x) for x in old_vec] if old_vec else []
        new_vec = [float(x) for x in vector]
        blended = _blend_vector(old_vec_f, new_vec, LEARN_ALPHA if old_vec_f else 1.0)

        samples = int(existing.get("samples") or 0) + 1
        pitch = float(pitch_hz) if pitch_hz is not None else float(existing.get("pitch_hz") or 0)
        if pitch_hz is not None and existing.get("pitch_hz"):
            pitch = (1.0 - LEARN_ALPHA) * float(existing["pitch_hz"]) + LEARN_ALPHA * float(pitch_hz)

        centroid = (
            float(centroid_hz)
            if centroid_hz is not None
            else float(existing.get("centroid_hz") or 0)
        )
        if centroid_hz is not None and existing.get("centroid_hz"):
            centroid = (
                (1.0 - LEARN_ALPHA) * float(existing["centroid_hz"])
                + LEARN_ALPHA * float(centroid_hz)
            )

        voice_label = (label or existing.get("label") or "").strip()
        if not voice_label and pitch:
            voice_label = _describe(pitch, centroid)

        record = {
            "user_id": uid,
            "display": (display or existing.get("display") or "").strip() or uid,
            "vector": blended,
            "pitch_hz": round(pitch, 1) if pitch else None,
            "pitch_std": float(pitch_std) if pitch_std is not None else existing.get("pitch_std"),
            "centroid_hz": round(centroid, 1) if centroid else None,
            "label": voice_label,
            "samples": samples,
            "updated_at": time.time(),
        }
        live_store.set_doc(_doc_key(uid), json.dumps(record, ensure_ascii=False))
        _index_add(uid)

        # Keep a human-readable line on the live profile card.
        try:
            live_store.save_profile(
                uid,
                {
                    "voice_signature": voice_label,
                    "name": record["display"] if display else None,
                },
            )
        except Exception:
            pass

        return {"ok": True, **record}


def identify(
    vector: list[float],
    *,
    exclude_user_id: Optional[str] = None,
) -> dict[str, Any]:
    """Find the best matching enrolled voice."""
    if not vector:
        return {"ok": False, "match": None, "score": 0.0}

    query = [float(x) for x in vector]
    best: Optional[dict[str, Any]] = None
    best_score = 0.0
    for vp in list_voiceprints():
        uid = str(vp.get("user_id") or "")
        if exclude_user_id and uid == str(exclude_user_id):
            continue
        stored = vp.get("vector")
        if not isinstance(stored, list):
            continue
        score = _cosine(query, [float(x) for x in stored])
        if score > best_score:
            best_score = score
            best = vp

    matched = bool(best and best_score >= MATCH_THRESHOLD)
    return {
        "ok": True,
        "matched": matched,
        "score": round(best_score, 4),
        "threshold": MATCH_THRESHOLD,
        "match": (
            {
                "user_id": best.get("user_id"),
                "display": best.get("display"),
                "label": best.get("label"),
                "pitch_hz": best.get("pitch_hz"),
            }
            if best
            else None
        ),
    }


def confirm(
    user_id: str,
    vector: list[float],
) -> dict[str, Any]:
    """Score this sample against the enrolled print for user_id."""
    vp = get_voiceprint(str(user_id))
    if not vp or not isinstance(vp.get("vector"), list):
        return {"ok": True, "enrolled": False, "score": 0.0, "confirmed": False}
    score = _cosine([float(x) for x in vector], [float(x) for x in vp["vector"]])
    return {
        "ok": True,
        "enrolled": True,
        "score": round(score, 4),
        "confirmed": score >= MATCH_THRESHOLD,
        "label": vp.get("label"),
        "display": vp.get("display"),
    }


def format_for_prompt(user_id: Optional[str]) -> str:
    if not user_id:
        return ""
    vp = get_voiceprint(str(user_id))
    if not vp:
        return ""
    label = vp.get("label") or "?"
    pitch = vp.get("pitch_hz")
    samples = vp.get("samples") or 0
    pitch_bit = f", ~{pitch} Hz fundamental" if pitch else ""
    return (
        f"[VOICE ID] You recognize this speaker's voice as {label}{pitch_bit} "
        f"(learned from {samples} sample{'s' if samples != 1 else ''}). "
        "Use this to tell callers apart by how they sound — not only by Discord name."
    )


def _describe(pitch_hz: float, centroid_hz: float) -> str:
    if pitch_hz < 120:
        band = "low"
    elif pitch_hz < 180:
        band = "mid-low"
    elif pitch_hz < 240:
        band = "mid-high"
    else:
        band = "high"
    if centroid_hz and centroid_hz < 1200:
        color = "warm"
    elif centroid_hz and centroid_hz > 2200:
        color = "bright"
    else:
        color = "neutral"
    return f"{band}~{int(round(pitch_hz))}Hz {color}"
