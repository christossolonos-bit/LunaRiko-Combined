"""base_registry.py — the tools she ALWAYS has, whatever else is switched on.

Unlike the domain registries (sample_registry, and whatever you write), this
one is not an either/or choice: when `BASE_REGISTRY` is True in
server/config.py, these tools are MERGED into whichever registry `REGISTRY`
picked, so she can reach both in the same turn. The merge happens in
server/chat_terminal.py.

Put things here that are useful no matter who she is. Today there is one:

  play_sound_effect — play the alarm clip out of the browser's speakers.
                      No arguments: one hard-wired effect. `inline_only`
                      because a sound effect that arrives "in a minute" is
                      not a sound effect.

ADDING A TOOL
Write the handler under base_tools/ (copy sound_effects.py — it shows the
{"ok", "message"} return shape and the never-raise rule), then register it
below. That is the whole job; nothing else has to change.

Offline structure check, no network:
  python -m server.agent_registries.base_registry
"""

from __future__ import annotations

from server.agent import Tool, ToolRegistry
from server.agent_registries.base_tools.sound_effects import play_alarm


def build_base_registry() -> ToolRegistry:
    """Build the base registry. Tools: play_sound_effect."""
    registry = ToolRegistry()
    registry.register(Tool(
        name="play_sound_effect",
        description=(
            "Play the alarm sound effect out loud through the speakers. Use "
            "when the user asks to sound the alarm, play the alarm, set off a "
            "siren, or otherwise wants the alarm noise played. Takes no "
            "arguments — the alarm is currently the only effect available."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        handler=play_alarm,
        inline_only=True,
    ))
    return registry


if __name__ == "__main__":
    # Offline structure check — no network.
    # Run with: python -m server.agent_registries.base_registry
    registry = build_base_registry()
    names = [t.name for t in registry.all()]
    print(f"[base_registry.__main__] tools: {names}")
    assert names == ["play_sound_effect"]

    tool = registry.get("play_sound_effect")
    assert tool is not None and tool.inline_only is True

    # Schemas must validate as OpenAI/Groq function-call specs.
    specs = registry.schemas()
    assert [s["function"]["name"] for s in specs] == names
    for spec in specs:
        assert spec["type"] == "function"
        assert spec["function"]["parameters"]["required"] == []
    print("[base_registry.__main__] OK — tools + schemas verified")
