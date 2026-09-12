"""do_task.py — the single tool Riko (the character LLM) is exposed to.

Design choice: Riko does NOT get a pile of tools. She gets exactly one —
`do_task` — whose only job is to forward a plain-language instruction to a
separate actions agent (see agent_backend.py). One tool keeps her voice
intact; the agent behind it can grow to any number of real functions.

This module owns the do_task tool's entire prompt surface, so the whole
feature stays modular and easy to find:

  - DO_TASK_TOOL        : the function-calling schema (OpenAI Responses API).
  - DO_TASK_SYSTEM_HINT : a short system-prompt snippet (optional).
  - handle_do_task()    : the handler — forwards to the actions agent.
  - dispatch()          : name->handler router for the tool loop.

Enable/disable the whole feature with server.config.ENABLE_FUNCTION_CALLING.
"""

from __future__ import annotations

from server.function_calling.agent_backend import run_instruction

TOOL_NAME = "do_task"

# OpenAI Responses-API function schema. Note the FLAT shape — name /
# description / parameters sit at the top level, NOT nested under a
# "function" key (that nested form is the Chat Completions shape).
# strict=True makes the model's arguments reliably match the schema; it
# requires additionalProperties=false and every property listed in required.
DO_TASK_TOOL: dict = {
    "type": "function",
    "name": TOOL_NAME,
    "description": (
        "Hand a real-world task to your assistant. Use it for things you "
        "can't do just by talking — e.g. checking the weather, starting a "
        "poll, looking something up, writing something to a file. "
        "Pass ONE clear, plain-language instruction. "
        "This tool is OPTIONAL: only call it if you actually feel like doing "
        "the task. You are free to tease, deflect, or simply ignore a "
        "request instead of calling it — staying in character comes first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "instruction": {
                "type": "string",
                "description": (
                    "The task to perform, in plain language. Examples: "
                    "'check the weather in Vancouver', 'start a poll "
                    "asking which game to play next'."
                ),
            },
        },
        "required": ["instruction"],
        "additionalProperties": False,
    },
    "strict": True,
}

# Optional, modular system-prompt snippet. Append it to Riko's system prompt
# when function calling is on. Kept short on purpose — the tool description
# above already carries the "when to use it" detail. It also steers her away
# from the older <task:...> text tag so the two mechanisms don't compete.
DO_TASK_SYSTEM_HINT: str = (
    "For real actions you can't do just by talking, use the do_task tool — "
    "do not write a <task:...> text tag."
)


# DO_TASK_SYSTEM_HINT: str = (
#     "For real actions you can't do just by talking, use the do_task tool — "
#     "do not write a <task:...> text tag. Call it only when you genuinely "
#     "want to; staying in character always comes first."
# )

def handle_do_task(instruction: str) -> str:
    """Run one do_task call: forward `instruction` to the actions agent and
    return its plain-language result for the character model to react to."""
    print(f"[do_task] instruction={instruction!r}")
    result = run_instruction(instruction)
    print(f"[do_task] result={result!r}")
    return result


def dispatch(name: str, args: dict) -> str:
    """Tool router for the streaming tool loop. do_task is the only tool."""
    if name == TOOL_NAME:
        return handle_do_task(instruction=args.get("instruction", ""))
    print(f"[do_task] WARN unknown tool requested: {name!r}")
    return f"(unknown tool: {name})"


if __name__ == "__main__":
    # Offline self-check — no network. Validates the schema is strict-safe.
    # Run with: python -m server.function_calling.do_task
    import json

    print("[do_task.__main__] DO_TASK_TOOL schema:")
    print(json.dumps(DO_TASK_TOOL, indent=2))

    params = DO_TASK_TOOL["parameters"]
    # Strict mode requires: additionalProperties false + every property
    # marked required.
    assert DO_TASK_TOOL["strict"] is True
    assert params["additionalProperties"] is False
    assert set(params["required"]) == set(params["properties"].keys()), (
        "strict mode: every property must be in `required`"
    )
    print("\n[do_task.__main__] schema is strict-mode safe OK")

    # dispatch routes do_task to the (stub) agent and ignores unknown tools.
    out = dispatch(TOOL_NAME, {"instruction": "check the weather"})
    print(f"[do_task.__main__] dispatch(do_task) -> {out!r}")
    assert isinstance(out, str) and out
    bad = dispatch("not_a_tool", {})
    assert bad.startswith("(unknown tool")
    print("[do_task.__main__] OK — schema + dispatch verified")
