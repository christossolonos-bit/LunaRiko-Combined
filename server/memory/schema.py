"""Pydantic schemas for a single memory and a batch of memories.

These are the *stored* shape — rich enough to support future scoring
upgrades (VAD, type, tone) without a DB rewrite. The character LLM never
sees these fields; only `summary` is injected into the prompt (see
`store.retrieve_formatted`).

Fields are unchanged from the original multi-user `faiss_memory.py` so the
on-disk JSON format stays compatible.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Literal, Optional


class VAD(BaseModel):
    """Valence / Arousal / Dominance — stored, unused in v1 ranking.

    Future-proofing: the extractor fills these in one shot (free), and
    later pure-Python scoring (salience, mood-congruence) can use them
    without re-extracting the whole store.
    """
    valence: float = Field(..., description="0.0 (negative) to 1.0 (positive)")
    arousal: float = Field(..., description="0.0 (calm) to 1.0 (excited)")
    dominance: float = Field(..., description="0.0 (submissive) to 1.0 (in-control)")


class MemorySchema(BaseModel):
    summary: str = Field(..., description="High-quality, concise summary of the memory or fact.")
    memory_type: Literal["episodic", "semantic"] = Field(
        ...,
        description="Classify as 'episodic' (events/experiences) or 'semantic' (core facts/beliefs/preferences)."
    )
    emotional_tone: str = Field(..., description="The underlying emotion (e.g., 'joyful', 'frustrated', 'neutral', 'nostalgic').")
    vad: VAD
    timestamp: str = Field(..., description="The date of the event or realization in YYYY-MM-DD format.")
    # Discord (or local) identity — optional for older memories.json rows.
    subject_id: Optional[str] = Field(
        default=None,
        description="Discord snowflake or 'local'. Who this fact is ABOUT.",
    )
    subject_role: Optional[Literal["owner", "guest", "local"]] = Field(
        default=None,
        description="owner / guest / local",
    )
    subject_name: Optional[str] = Field(
        default=None,
        description="Display name for prompt wording",
    )


class MemoryList(BaseModel):
    """Wrapper schema allowing the LLM to output 0, 1, or multiple memories."""
    memories: list[MemorySchema] = Field(
        default_factory=list,
        description="A list of extracted memories. Extract multiple separate memories if different topics were discussed. Can be empty."
    )


if __name__ == "__main__":
    # Round-trip a memory through the schema + JSON to confirm fields hold.
    m = MemorySchema(
        summary="The user finds philosophy boring but talks about it anyway.",
        memory_type="semantic",
        emotional_tone="amused",
        vad=VAD(valence=0.6, arousal=0.3, dominance=0.5),
        timestamp="2026-06-27",
    )
    batch = MemoryList(memories=[m])
    dumped = batch.model_dump()
    assert dumped["memories"][0]["summary"].startswith("The user finds")
    assert dumped["memories"][0]["vad"]["valence"] == 0.6
    reloaded = MemoryList(**dumped)
    assert reloaded.memories[0].memory_type == "semantic"
    assert MemoryList().memories == []   # empty list is valid
    print("[schema.__main__] OK — VAD / MemorySchema / MemoryList round-trip")
