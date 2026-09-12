"""Memory package — live memory (primary), session gist, optional FAISS.

Flags in server/config.py:
  ENABLE_LIVE_MEMORY — profiles + working/durable + permanent turn log (default ON)
  ENABLE_SESSION_GIST — rolling summary of older chat turns
  ENABLE_MEMORY — FAISS RAG backup (default OFF)
All LLM calls are local Ollama only — never OpenAI.

FAISS (`MemoryStore`) is imported lazily so the default live-memory path
does not require the faiss package.
"""

from server.memory.schema import VAD, MemorySchema, MemoryList
from server.memory.session_log import SessionLog, strip_tags
from server.memory.window import RollingWindow
from server.memory.gist import update_gist

__all__ = [
    "get_embedder",
    "VAD",
    "MemorySchema",
    "MemoryList",
    "MemoryStore",
    "SessionLog",
    "strip_tags",
    "RollingWindow",
    "extract_session",
    "update_gist",
]


def __getattr__(name: str):
    if name == "get_embedder":
        from server.memory.embedder import get_embedder
        return get_embedder
    if name == "MemoryStore":
        from server.memory.store import MemoryStore
        return MemoryStore
    if name == "extract_session":
        from server.memory.extractor import extract_session
        return extract_session
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
