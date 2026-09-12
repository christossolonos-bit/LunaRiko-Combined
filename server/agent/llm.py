"""llm.py — the LLM client interface plus a mock implementation.

The agent depends only on the LLMClient protocol below: given a task and the
available tool schemas, return either a chosen tool (name + arguments) or a
natural-language reply when nothing fits.

MockLLMClient is a deterministic, keyword-based stand-in. It lets the whole
agent loop be tested offline — no API key, no network. The real Groq-backed
client (llm_groq.py) implements the same protocol and drops in unchanged.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

log = logging.getLogger(__name__)


@dataclass
class ToolChoice:
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    """The LLM's decision for one prompt.

    - tool_choice set  -> run that tool.
    - tool_choice None -> `message` is a natural-language reply to return
      directly (e.g. the model could not map the prompt to any tool).
    """
    tool_choice: Optional[ToolChoice] = None
    message: Optional[str] = None


@runtime_checkable
class LLMClient(Protocol):
    async def choose_tool(
        self,
        task: str,
        context: Optional[str],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        """Pick a tool for `task`, or return a no-tool LLMResponse."""
        ...


# --------------------------------------------------------------------------
# Mock client — offline, deterministic, keyword-based. Testing only.
# --------------------------------------------------------------------------
def _after(original: str, markers: tuple[str, ...]) -> str:
    """Text following the first marker found (case-insensitive search)."""
    low = original.lower()
    for m in markers:
        idx = low.find(m)
        if idx != -1:
            return original[idx + len(m):].strip(" ?.!,")
    return ""


def _topic(original: str) -> str:
    low = original.lower()
    for lead in ("research ", "look into ", "investigate ", "find out about ",
                 "look up "):
        if low.startswith(lead):
            return original[len(lead):].strip(" ?.!,")
    return _after(original, ("about ", " on "))


class MockLLMClient:
    """Keyword-based fake LLM for offline testing. Not for production use."""

    async def choose_tool(
        self,
        task: str,
        context: Optional[str],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        names = {t["function"]["name"] for t in tools}
        text = task.lower()
        log.info("[mock-llm] routing prompt: %r", task)

        def has(*words: str) -> bool:
            return any(w in text for w in words)

        # Status / cancel checks first — they may also mention a tool's topic.
        if has("cancel", "abort", "never mind", "call off") and "cancel_task" in names:
            return LLMResponse(ToolChoice("cancel_task", {"task_reference": task}))
        if has("done", "finished", "ready", "status", "is it", "how is",
               "progress", "complete") and "check_task" in names:
            return LLMResponse(ToolChoice("check_task", {"task_reference": task}))
        if has("weather", "temperature", "forecast") and "get_weather" in names:
            location = _after(task, ("in ", "for ", "at ")) or "your area"
            return LLMResponse(ToolChoice("get_weather", {"location": location}))
        if has("research", "look into", "investigate", "find out", "look up") \
                and "do_research" in names:
            return LLMResponse(ToolChoice("do_research", {"topic": _topic(task) or "that topic"}))

        return LLMResponse(message="I'm not sure how to help with that one.")
