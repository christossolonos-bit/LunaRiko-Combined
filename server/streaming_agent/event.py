"""Unified event abstraction shared between input streams, state, and decision engine."""
from dataclasses import dataclass, field
from time import time
from typing import Any, Dict


@dataclass
class Event:
    """A single observation from any input stream.

    Attributes:
        type: source category, e.g. 'voice', 'chat', 'vision'. Add new types freely.
        content: short text representation suitable for an LLM prompt.
        importance: 0.0 - 1.0. Inputs decide their own importance; the decision
            engine uses it to gate responses.
        timestamp: unix seconds, captured at construction time.
        metadata: free-form dict for input-specific extras (speaker id, audio path, etc.).
    """

    type: str
    content: str
    importance: float = 0.5
    timestamp: float = field(default_factory=time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "content": self.content,
            "importance": self.importance,
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }
