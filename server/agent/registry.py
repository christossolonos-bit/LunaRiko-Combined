"""registry.py — the tool registry, the one deployment-specific piece.

A Tool bundles what the agent needs for one capability:
  * name / description / parameters — shown to the LLM so it can choose;
  * handler — the async function that does the work.

To reuse this agent elsewhere, copy the agent/ folder and build a new
registry; the core never changes.

Tool standard — every handler must:
  * be async,
  * accept keyword arguments matching its `parameters` JSON schema,
  * return a dict shaped {"ok": bool, "message": str, ...}.
The "message" is the natural-language result the agent reports back.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

log = logging.getLogger(__name__)

ToolHandler = Callable[..., Awaitable[dict]]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict           # JSON schema for LLM function-calling
    handler: ToolHandler
    inline_only: bool = False  # True -> never run as a background task

    def filter_args(self, args: dict) -> dict:
        """Drop arguments this tool does not declare.

        Models invent keys — for a tool with NO parameters the common one is an
        empty-string key (`{"": ""}`), which makes `handler(**args)` die with
        `unexpected keyword argument ''`. The JSON schema is the contract, so
        anything outside it is dropped instead of forwarded.
        """
        allowed = (self.parameters or {}).get("properties") or {}
        kept = {k: v for k, v in args.items() if k in allowed}
        if len(kept) != len(args):
            log.warning("tool %s: dropped undeclared args %s",
                        self.name, sorted(set(args) - set(kept)))
        return kept

    def to_schema(self) -> dict:
        """This tool as an OpenAI/Groq function-calling tool spec."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """An ordered collection of Tools."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name!r} is already registered")
        self._tools[tool.name] = tool
        log.info("registered tool: %s", tool.name)

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def schemas(self) -> list[dict]:
        """Function-calling specs for every tool — pass straight to the LLM."""
        return [t.to_schema() for t in self._tools.values()]
