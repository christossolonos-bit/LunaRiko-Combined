"""sample_registry.py — a tiny ToolRegistry to prove the step-3 real-agent flow.

Two canned tools — `get_weather` and `start_poll` — that mirror the behaviour
of `DummyActionsAgent` from step 2, but registered to the REAL `Agent` so the
full chain is exercised:

  Riko -> do_task("check the weather in Vancouver")
              -> AgentRunnerBackend.run
                   -> Agent.handle_prompt (Groq function-calling)
                        -> get_weather(location="Vancouver")
                             -> "The weather in Vancouver is 23 C and sunny."
              <- result fed back so Riko reacts in voice

The handlers are deliberately fast (`asyncio.sleep(0.1)`) so they finish
inline within the agent's `INLINE_TIMEOUT_SECONDS`. Slow / background tasks
will land here naturally when real domain registries are wired in later.

Run offline (no network, no Groq) self-check:
  python -m server.agent_registries.sample_registry
"""

from __future__ import annotations

import asyncio
import random

from server.agent import Tool, ToolRegistry


async def get_weather(location: str) -> dict:
    """Canned weather. Returns the agent-standard {ok, message} shape."""
    print(f"[sample_registry] get_weather(location={location!r})")
    await asyncio.sleep(0.1)
    temp = random.randint(15, 28)
    sky = random.choice(["sunny", "cloudy", "lightly raining", "breezy"])
    return {
        "ok": True,
        "message": f"The weather in {location} is {temp} degrees C and {sky} today.",
    }


async def start_poll(question: str) -> dict:
    """Canned poll. Shows the shape a real handler should return."""
    print(f"[sample_registry] start_poll(question={question!r})")
    await asyncio.sleep(0.1)
    return {
        "ok": True,
        "message": f"A poll has been started: '{question}'. Viewers are voting now.",
    }


def build_sample_registry() -> ToolRegistry:
    """Build the sample registry. Two tools: get_weather, start_poll."""
    registry = ToolRegistry()
    registry.register(Tool(
        name="get_weather",
        description=(
            "Get the current weather for a place. Use this when the user "
            "asks about weather, temperature, or the forecast for a city."
        ),
        parameters={
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "City or place, e.g. 'Vancouver, BC'.",
                },
            },
            "required": ["location"],
        },
        handler=get_weather,
    ))
    registry.register(Tool(
        name="start_poll",
        description=(
            "Start a poll for viewers to vote on. Use when the user asks to "
            "create / launch / start a poll about something."
        ),
        parameters={
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The poll question (a short sentence).",
                },
            },
            "required": ["question"],
        },
        handler=start_poll,
    ))
    return registry


if __name__ == "__main__":
    # Offline self-check — no Groq, no network. Run the handlers directly.
    # Run with: python -m server.agent_registries.sample_registry
    registry = build_sample_registry()
    print(f"[sample_registry.__main__] tools: {[t.name for t in registry.all()]}")
    assert {t.name for t in registry.all()} == {"get_weather", "start_poll"}

    out = asyncio.run(get_weather(location="Vancouver"))
    print(f"[sample_registry.__main__] get_weather -> {out}")
    assert out["ok"] is True and "Vancouver" in out["message"]

    out = asyncio.run(start_poll(question="What should we play next?"))
    print(f"[sample_registry.__main__] start_poll -> {out}")
    assert out["ok"] is True and "poll" in out["message"].lower()

    # Each tool's JSON schema also validates as a Groq/OpenAI function-call spec.
    for spec in registry.schemas():
        assert spec["type"] == "function"
        assert "name" in spec["function"]
        assert "parameters" in spec["function"]
    print("[sample_registry.__main__] OK — handlers + schemas verified")
