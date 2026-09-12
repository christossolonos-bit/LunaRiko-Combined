"""tool_loop.py — streaming OpenAI Responses-API tool-call loop.

Two callables:

  stream_tool_turn(...) -> Iterator[str]
      The primitive. Yields assistant text deltas as they arrive, running any
      tool the model calls in between. When the model emits function_call
      items it runs each via `dispatch`, feeds the outputs back, and keeps
      streaming — so the caller sees ONE flat stream of text deltas even when
      a tool ran in the middle. The step-2 OpenAIToolBackend is just a thin
      wrapper over this, which is why the chunker / parser / playback never
      had to change.

  run_tool_turn(...) -> str
      A thin wrapper that drains stream_tool_turn into the full text, with an
      optional per-delta callback. Used by the step-1 standalone tests.

Uses the OpenAI **Responses API** only (`client.responses.stream`), per
general_guidlines.md point 2. NOT chat completions.
"""

from __future__ import annotations

import json
from typing import Callable, Iterator, List, Optional

from openai import OpenAI

from server.llm.llm_backend import accumulate_usage, usage_dict

from server.config import vlog

# dispatch(tool_name, arguments_dict) -> plain-language result string.
ToolDispatch = Callable[[str, dict], str]


def stream_tool_turn(
    client: OpenAI,
    model: str,
    input_items: List,
    tools: List[dict],
    dispatch: ToolDispatch,
    *,
    max_output_tokens: int = 512,
    max_rounds: int = 5,
    usage_sink: Optional[dict] = None,
) -> Iterator[str]:
    """Stream one turn end-to-end, running any tools the model calls.

    Yields assistant text deltas in order. If the model emits function_call
    items, each is run via `dispatch`, the outputs are appended, and streaming
    continues — the caller just sees more deltas.

    Args:
        client: an initialised OpenAI client.
        model: the model id (e.g. Riko's fine-tuned model).
        input_items: the conversation so far. NOT mutated — a copy is used.
        tools: Responses-API tool schemas (flat shape — see do_task.py).
        dispatch: runs a tool by name, returns its result string.
        max_output_tokens: per-response token cap.
        max_rounds: safety cap on tool rounds (prevents an infinite loop).

    Yields:
        Text deltas. Inline `<expr:>` / `<emote:>` tags pass through untouched.
    """
    # Local working copy — the caller's list is never mutated.
    items: List = list(input_items)

    for round_idx in range(1, max_rounds + 1):
        # A single space separates this round's text from the previous
        # round's, so text either side of a tool call never word-merges
        # (e.g. "...weather incoming!" + "Reporting live:"). A leading space
        # before the very first text is harmless — the chunker strips it.
        if round_idx > 1:
            yield " "

        vlog(f"[tool_loop] round {round_idx}/{max_rounds}: streaming response...")
        with client.responses.stream(
            model=model,
            input=items,
            tools=tools,
            # One tool call per response keeps the loop predictable and
            # sidesteps the fine-tuned-model "strict mode off for parallel
            # calls" caveat in the OpenAI docs. Sequential calls still work —
            # they just land in successive rounds of this loop.
            parallel_tool_calls=False,
            store=False,  # stateless: we pass the full context every round.
            max_output_tokens=max_output_tokens,
        ) as stream:
            for event in stream:
                if event.type == "response.output_text.delta":
                    yield event.delta
            final = stream.get_final_response()
        # One API call per round, so the counts are summed across rounds.
        if usage_sink is not None:
            accumulate_usage(usage_sink, usage_dict(final))

        # The Responses API returns function calls as items in `final.output`
        # with type "function_call" (each has name, call_id, JSON arguments).
        calls = [
            it for it in final.output
            if getattr(it, "type", None) == "function_call"
        ]
        if not calls:
            print(f"[tool_loop] round {round_idx}: no tool calls — turn complete")
            return

        print(f"[tool_loop] round {round_idx}: {len(calls)} tool call(s)")
        # Append the model's own output (which includes the function_call
        # items) so the next request can match each call to its output.
        items += list(final.output)

        for call in calls:
            try:
                args = json.loads(call.arguments or "{}")
            except json.JSONDecodeError as e:
                print(f"[tool_loop] bad JSON args for {call.name}: {e}")
                args = {}
            print(f"[tool_loop] -> {call.name}({args})")
            output = dispatch(call.name, args)
            print(f"[tool_loop] <- {call.name} returned: {output!r}")
            # `call_id` is the only field that must match — it links this
            # output back to the function_call item appended just above.
            items.append({
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": str(output),
            })

    print(f"[tool_loop] hit max_rounds={max_rounds} — stopping")


def run_tool_turn(
    client: OpenAI,
    model: str,
    input_items: List,
    tools: List[dict],
    dispatch: ToolDispatch,
    *,
    max_output_tokens: int = 512,
    max_rounds: int = 5,
    on_text_delta: Optional[Callable[[str], None]] = None,
) -> str:
    """Drain `stream_tool_turn` into the full assistant text.

    `on_text_delta`, if given, is called with each delta as it arrives (handy
    for live printing in tests). Returns every sub-response concatenated.
    """
    parts: List[str] = []
    for delta in stream_tool_turn(
        client, model, input_items, tools, dispatch,
        max_output_tokens=max_output_tokens, max_rounds=max_rounds,
    ):
        parts.append(delta)
        if on_text_delta is not None:
            on_text_delta(delta)

    full_text = "".join(parts)
    print(f"[tool_loop] turn done — text_len={len(full_text)}")
    return full_text
