"""Top-level glue: one turn of the LLM pipeline.

Wires together the LLM stream → tag parser → semantic pickers → TTS →
playback queue. A "turn" is one user message in, one assistant response
fully played out.

Flow:
  1. Build messages from history + user_text (prompt_builder).
  2. Stream the LLM via OpenAIBackend, chunk it on punctuation/length
     without splitting `<...>` tags (stream_chunker — shared chunker,
     without splitting `<...>` tags (stream_chunker — sentence enders
     only).
  3. For each chunk, parse out events (tag_parser) and dispatch:
       EXPR  → expression_picker → enqueue PlaybackItem(EXPRESSION)
       EMOTE → emote_picker      → enqueue PlaybackItem(EMOTE)
       TEXT  → safe_synthesize → enqueue PlaybackItem(TEXT)
               (which TTS module is picked off config.TTS_BACKEND; None on
               failure → the fragment is skipped, the turn never crashes)
  4. Wait for the playback queue to drain (audio fully spoken).
  5. Append the raw assistant text (tags included) to history and return
     the updated history. Tags are preserved in storage so the model's
     own style carries into the next turn.

The OpenAI backend, both pickers, and the emote name→path map are loaded
lazily once and cached at module scope so repeated turns reuse the same
in-memory state.

Public surface:
    run_turn(user_text: str,
             history: list[dict],
             playback: OrderedPlaybackWorker,
             injected_context: str = "") -> list[dict]

`injected_context` is a free-form string (e.g. vision caption rendered as
natural language by the streaming_agent's payload_formatter) appended to
the system prompt for THIS turn only via `build_messages`. Never stored
in history.
"""

from __future__ import annotations

import json
import time
from typing import List, Mapping, Optional, Tuple
from uuid import uuid4

from server import config
from server.config import MODEL
from server.config import vlog
from server.llm.llm_backend import LLMBackend, LocalLLMBackend, OpenAIBackend
from server.llm.pickers import build_emote_picker, build_expression_picker
from server.llm.prompt_builder import build_messages
from server.llm.semantic_picker import SemanticPicker
from server.llm.stream_chunker import stream_text_chunks
from server.llm.tag_parser import parse_chunk
from server.llm.text_clean import clean_for_tts
from server.playback.playback import ItemType, OrderedPlaybackWorker, PlaybackItem
from server.turn_log import log_turn

# Which voice speaks — `tts_backend` in the character yaml, surfaced as
# config.TTS_BACKEND. Every module exposes the identical
# `safe_synthesize(text, emotion) -> (rel_path, duration) | None`, so nothing
# below this line knows or cares which one is loaded.
if config.TTS_BACKEND == "fish":
    from server.tts.fish import safe_synthesize
elif config.TTS_BACKEND == "edge":
    from server.tts.edge import safe_synthesize
elif config.TTS_BACKEND == "sapi":
    from server.tts.sapi import safe_synthesize
else:
    from server.tts.sovits import safe_synthesize
print(f"[orch] TTS backend: {config.TTS_BACKEND}")
print(f"[orch] LLM backend: {config.LLM_BACKEND} model={MODEL!r}")


# Lazy-initialized singletons. First call to `run_turn` pays the cost of
# loading MiniLM + creating the OpenAI client; subsequent calls reuse.
_components: Optional[Tuple[LLMBackend, SemanticPicker, SemanticPicker, dict]] = None


def _get_components() -> Tuple[LLMBackend, SemanticPicker, SemanticPicker, dict]:
    global _components
    if _components is None:
        vlog("[orch] initializing components (LLM backend + pickers)...")
        # Local models use chat completions / Ollama native — OpenAI tool
        # calling (Responses API) does not apply. Dialogue only.
        if config.LLM_BACKEND == "local":
            backend: LLMBackend = LocalLLMBackend(model=MODEL)
            print("[orch] local LLM — function calling skipped for this turn path")
        elif config.ENABLE_FUNCTION_CALLING:
            from server.function_calling.do_task import DO_TASK_TOOL, dispatch
            from server.function_calling.openai_tool_backend import (
                OpenAIToolBackend,
            )
            backend = OpenAIToolBackend(
                model=MODEL, tools=[DO_TASK_TOOL], dispatch=dispatch,
            )
            print("[orch] function calling ENABLED — do_task tool exposed")
        else:
            backend = OpenAIBackend(model=MODEL)
        expr_picker = build_expression_picker()
        emote_picker, emote_name_to_rel_url = build_emote_picker()
        _components = (backend, expr_picker, emote_picker, emote_name_to_rel_url)
        vlog("[orch] components ready")
    return _components


def reload_emote_picker() -> int:
    """Re-scan the animation folders and rebuild the emote picker in place.

    Call this after dropping new `.fbx` / `.vrma` files into any of the
    folders in `config.ANIMATION_SOURCES` — the new files become pickable
    without restarting the process. Returns the new pool size.

    Note: the MiniLM model itself stays cached (in `semantic_picker`), so
    only the candidate-name embeddings are recomputed. Typically takes
    well under a second.
    """
    global _components
    if _components is None:
        # No components built yet — initial build will pick up new files anyway.
        _get_components()
        return len(_components[3])  # type: ignore[index]

    backend, expr_picker, _old_emote_picker, _old_n2u = _components
    print("[orch] reloading emote picker (re-scanning animation folders)...")
    new_emote_picker, new_n2u = build_emote_picker()
    _components = (backend, expr_picker, new_emote_picker, new_n2u)
    print(f"[orch] reloaded — pool size now {len(new_n2u)}")
    return len(new_n2u)


def _build_tool_items(tool_calls: List[dict]) -> List[dict]:
    """Turn this turn's recorded tool calls into Responses-API history items.

    Each recorded call becomes a (function_call, function_call_output) PAIR that
    shares a short synthetic `call_id`. We keep `call_id` because the Responses
    API rejects a function_call_output whose call_id has no matching
    function_call in the same input — it is the one field that MUST survive. We
    drop the server-assigned `id` (never needed on input). Persisting these
    between the user and assistant messages shows the model its own recent tool
    use, which keeps it from "forgetting" it has tools after several turns.

    Empty in -> empty out, so a turn with no tool call is byte-identical to
    before (nothing is appended to history).
    """
    items: List[dict] = []
    for tc in tool_calls:
        cid = "call_" + uuid4().hex[:20]
        items.append({
            "type": "function_call",
            "call_id": cid,
            "name": tc["name"],
            "arguments": json.dumps(tc.get("arguments", {})),
        })
        items.append({
            "type": "function_call_output",
            "call_id": cid,
            "output": str(tc.get("output", "")),
        })
    return items


def run_turn(
    user_text: str,
    history: List[Mapping[str, str]],
    playback: OrderedPlaybackWorker,
    injected_context: str = "",
    image_path: str = "",
) -> List[dict]:
    """Drive one full turn end-to-end. Returns the updated history list.

    `history` is not mutated; a new list is returned (caller decides whether
    to persist it). The raw assistant text — TAGS INCLUDED — is appended so
    the model's own emote/expression style carries forward.

    `injected_context`, if non-empty, is appended to the base system prompt
    for this single turn (per-turn modality context like vision). Never
    written to history — see prompt_builder.build_messages for the wiring.

    `image_path`, if non-empty, attaches that image to THIS turn's user
    message as Responses-API input_image (the model sees it directly).
    History stores a plain-text marker instead — the image is never re-sent.
    """
    backend, expr_picker, emote_picker, emote_name_to_rel_url = _get_components()

    vlog(f"\n[orch] === run_turn ===  user_text={user_text!r}"
          f" injected_context={'yes' if injected_context else 'no'}"
          f" image={image_path or 'no'}")

    # Function calling on: prepend the do_task usage hint to the per-turn
    # system context. build_messages folds injected_context into the system
    # prompt; it is never written to history.
    if config.ENABLE_FUNCTION_CALLING:
        from server.function_calling.do_task import DO_TASK_SYSTEM_HINT
        injected_context = (
            f"{DO_TASK_SYSTEM_HINT}\n\n{injected_context}".strip()
            if injected_context else DO_TASK_SYSTEM_HINT
        )

    messages = build_messages(history, user_text,
                              injected_context=injected_context,
                              image_path=image_path)

    # Chunks come pre-stripped from the chunker, so a naive concat collapses
    # the sentence boundary spacing ("time?That's..."). Re-insert a single
    # space between chunks for the history record.
    chunk_parts: list[str] = []
    chunk_count = 0
    # Turn timing for logs/turns_<date>.jsonl. ttft is measured to the first
    # SPEAKABLE chunk, not the first token — that is the boundary that actually
    # gates TTS, and it is the number to look at when the stream feels stalled.
    t_start = time.perf_counter()
    t_first_chunk = 0.0

    for chunk in stream_text_chunks(backend, messages):
        chunk_count += 1
        if chunk_count == 1:
            t_first_chunk = time.perf_counter()
        chunk_parts.append(chunk)
        events = parse_chunk(chunk)

        for kind, value in events:
            if kind == "EXPR":
                name, _ = expr_picker.pick(value)
                playback.enqueue(PlaybackItem(
                    item_type=ItemType.EXPRESSION,
                    name=name,
                ))
            elif kind == "EMOTE":
                name, _ = emote_picker.pick(value)
                # `emote_name_to_rel_url[name]` is already the browser-relative
                # path string (e.g. "animations/vrma/Spin.vrma" or
                # "animations/mixamo/Backflip.fbx") — ready to drop into the
                # /animate payload. See server/vrm/vrm_client.py module
                # docstring for why this is relative not absolute.
                playback.enqueue(PlaybackItem(
                    item_type=ItemType.EMOTE,
                    animate_url=emote_name_to_rel_url[name],
                ))
            elif kind == "TEXT":
                # Single pre-clean step before TTS + subtitle. See
                # server/llm/text_clean.py for the rule list. SoVITS still
                # returns 400 on punctuation-only input, so we also skip
                # anything with no letters/digits AFTER cleaning.
                cleaned = clean_for_tts(value)
                if not any(c.isalnum() for c in cleaned):
                    print(f"[orch] skip TEXT (no speakable content): {value!r}")
                    continue
                # safe_synthesize returns None on a failed / silent
                # generation — skip the fragment, never crash the turn.
                result = safe_synthesize(cleaned)
                if result is None:
                    print(f"[orch] TTS failed — fragment skipped, turn continues: {cleaned!r}")
                    continue
                audio_path, dur = result
                playback.enqueue(PlaybackItem(
                    item_type=ItemType.TEXT,
                    audio_path=audio_path,
                    text=cleaned,
                    duration=dur,
                ))

    t_stream_end = time.perf_counter()
    ttft_ms  = round((t_first_chunk - t_start) * 1000) if t_first_chunk else None
    total_ms = round((t_stream_end - t_start) * 1000)
    usage = dict(getattr(backend, "last_turn_usage", {}) or {})

    full_assistant_text = " ".join(chunk_parts)
    print(
        f"[orch] stream complete chunks={chunk_count} "
        f"raw_len={len(full_assistant_text)} "
        f"ttft={ttft_ms}ms total={total_ms}ms "
        f"cached_tokens={usage.get('cached_tokens', '?')}"
    )

    vlog("[orch] waiting for playback queue to drain...")
    playback.wait_until_finished()
    vlog("[orch] playback finished")

    # Write the full structured turn to logs/turns_<date>.jsonl for later
    # fine-tuning / debugging. This is SEPARATE from chat_history.json (which
    # only stores her spoken words). Wrapped in try so a logging failure
    # never crashes the turn.
    try:
        log_turn({
            "function_calling": config.ENABLE_FUNCTION_CALLING,
            "system": (messages[0]["content"]
                       if messages and messages[0].get("role") == "system"
                       else ""),
            "history_in": [m for m in history if m.get("role") != "system"],
            "user": user_text,
            "image_path": image_path or "",
            "injected_context": injected_context or "",
            "tool_calls": list(getattr(backend, "last_turn_tool_calls", [])),
            "assistant": full_assistant_text,
            # Timings + token counts. `cached_tokens` is the one to read for
            # how much of the prompt was cached; `ttft_ms` / `total_ms`
            # are for diagnosing a stalling stream with data instead of vibes.
            "ttft_ms":     ttft_ms,
            "total_ms":    total_ms,
            "chunk_count": chunk_count,
            **usage,
        })
    except Exception as e:
        print(f"[orch] turn_log failed (non-fatal): {type(e).__name__}: {e}")

    # Append the raw assistant text (tags included) so the next turn sees the
    # model's own style. Defensive copy of history — we never mutate input.
    # Order matches the Responses API: user -> [tool call/output pairs] ->
    # assistant. The tool items are only present when a tool actually ran.
    updated: List[dict] = list(history)
    if image_path:
        # Persist text + marker, never the image itself (token saver).
        from server.llm.image_attach import history_text
        updated.append({"role": "user", "content": history_text(user_text)})
    else:
        updated.append({"role": "user", "content": user_text})
    tool_items = _build_tool_items(getattr(backend, "last_turn_tool_calls", []))
    if tool_items:
        updated.extend(tool_items)
        print(f"[orch] persisted {len(tool_items) // 2} tool call(s) to history")
    updated.append({"role": "assistant", "content": full_assistant_text})
    print(f"[orch] history now has {len(updated)} message(s)")
    return updated


if __name__ == "__main__":
    # Standalone test (needs the server running, and SoVITS on :9880).
    # Run with: python -m server.llm.orchestrator
    user_msg = "Do a backflip and say something cheeky to me."
    print(f"[orch.__main__] hardcoded user message: {user_msg!r}\n")

    worker = OrderedPlaybackWorker()
    worker.start()
    try:
        updated_history = run_turn(user_msg, history=[], playback=worker)
    finally:
        worker.stop()

    print("\n[orch.__main__] === FINAL HISTORY ===")
    for i, m in enumerate(updated_history):
        content = m["content"]
        if isinstance(content, str) and len(content) > 200:
            content = content[:200] + "..."
        print(f"  [{i}] {m['role']:>9}: {content!r}")

    assert updated_history[-1]["role"] == "assistant", "last msg should be assistant"
    assert updated_history[-2]["role"] == "user", "second-to-last should be user"
    print("\n[orch.__main__] OK — turn completed end-to-end")
