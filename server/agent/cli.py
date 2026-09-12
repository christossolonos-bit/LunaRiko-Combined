"""cli.py — a generic interactive CLI for any Agent.

run_cli(agent) reads natural-language lines and prints the agent's replies.
input() runs in a thread so background tasks keep progressing while you type.

This file is generic — it knows nothing about any particular tool. A
deployment builds an Agent (core + its registry + an LLM client) and passes
it here.
"""
from __future__ import annotations

import asyncio
import logging

from .core import Agent

log = logging.getLogger(__name__)

_HELP = (
    "Type a request in plain language. Examples:\n"
    "  what's the weather in Tokyo\n"
    "  research the history of tea\n"
    "  is the research done?\n"
    "Commands: 'help', 'quit'."
)


async def run_cli(agent: Agent) -> None:
    loop = asyncio.get_running_loop()
    print(f"\n{agent.name} ready. {_HELP}\n")
    while True:
        try:
            # input() blocks — run it in a thread so background tasks keep going.
            line = await loop.run_in_executor(None, input, "> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        line = line.strip()
        if not line:
            continue
        if line.lower() in {"quit", "exit"}:
            break
        if line.lower() == "help":
            print(_HELP)
            continue
        reply = await agent.handle_prompt(line)
        print(reply)
    print("bye.")
