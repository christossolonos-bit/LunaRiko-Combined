"""Builds the message list passed to the LLM backend.

The first system message is always `SYSTEM_PROMPT` from `server.config`,
optionally augmented with `injected_context` (e.g. retrieved memories,
vision frame description). Any system message already in `history` is
skipped — config is the single source of truth for the system prompt.

History is **never mutated**. The returned list is a fresh list.
"""

from __future__ import annotations

from typing import Iterable, List, Mapping

from server.config import SYSTEM_PROMPT, vlog


def build_messages(
    history: Iterable[Mapping[str, str]],
    user_text: str,
    injected_context: str = "",
    image_path: str = "",
) -> List[dict]:
    """Return `[system, *history_without_system, user]`.

    `injected_context`, if non-empty, is appended to the system prompt with
    a blank line separator. The on-disk history file is not touched.

    `image_path`, if non-empty, upgrades THIS turn's user message to the
    Responses-API multimodal content array (input_text + input_image data
    URL) via server.llm.image_attach. History messages are always plain
    text — the orchestrator persists a text marker instead of the image,
    so image tokens are only ever paid on the turn that attached it.
    """
    if injected_context:
        system_content = f"{SYSTEM_PROMPT}\n\n{injected_context}"
    else:
        system_content = SYSTEM_PROMPT

    messages: List[dict] = [{"role": "system", "content": system_content}]
    for msg in history:
        # Responses-API typed items (function_call / function_call_output) carry
        # no role/content — pass them straight through so the model sees its own
        # recent tool use. See server/llm/orchestrator._build_tool_items.
        if "role" not in msg:
            messages.append(dict(msg))
            continue
        if msg.get("role") == "system":
            continue
        # Defensive copy so callers can't mutate our output and vice versa.
        messages.append({"role": msg["role"], "content": msg["content"]})
    if image_path:
        from server.llm.image_attach import build_image_content
        messages.append({"role": "user",
                         "content": build_image_content(user_text, image_path)})
    else:
        messages.append({"role": "user", "content": user_text})

    vlog(
        f"[prompt] built messages: total={len(messages)} "
        f"history={len(messages) - 2} injected_context={'yes' if injected_context else 'no'}"
        f" image={'yes' if image_path else 'no'}"
    )
    return messages


if __name__ == "__main__":
    hist = [
        {"role": "system", "content": "this should be ignored"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "<expr:happy>hey there!"},
    ]
    msgs = build_messages(hist, "what's up?")
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == SYSTEM_PROMPT
    assert msgs[-1] == {"role": "user", "content": "what's up?"}
    assert all(m["content"] != "this should be ignored" for m in msgs)
    print("[prompt.__main__] base build OK, len=", len(msgs))

    msgs2 = build_messages(hist, "again", injected_context="VISION: empty room.")
    assert msgs2[0]["content"].startswith(SYSTEM_PROMPT)
    assert "VISION: empty room." in msgs2[0]["content"]
    print("[prompt.__main__] injected_context OK")

    # Typed tool items (function_call / function_call_output) must pass through
    # untouched, in place, so the model sees its own recent tool use.
    hist_tools = [
        {"role": "user", "content": "get the weather"},
        {"type": "function_call", "call_id": "c1", "name": "do_task",
         "arguments": "{\"instruction\": \"weather in NYC\"}"},
        {"type": "function_call_output", "call_id": "c1", "output": "88 F"},
        {"role": "assistant", "content": "<expr:smug> 88 and toasty."},
    ]
    msgs3 = build_messages(hist_tools, "and now?")
    assert msgs3[1] == {"role": "user", "content": "get the weather"}
    assert msgs3[2]["type"] == "function_call" and msgs3[2]["call_id"] == "c1"
    assert msgs3[3]["type"] == "function_call_output" and msgs3[3]["output"] == "88 F"
    assert msgs3[4]["role"] == "assistant"
    print("[prompt.__main__] typed tool items pass-through OK")
