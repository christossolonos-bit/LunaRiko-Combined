"""RollingWindow — pure trim logic for the verbatim short-term history.

When stored history reaches `max_msgs`, the oldest `evict_chunk` messages
are peeled off (to be folded into the session gist) and the rest is kept.

The one invariant that keeps the OpenAI Responses API happy:

  the kept remainder ALWAYS starts with a `user` message. OpenAI must never
  receive an assistant message first; the summary of the evicted turns lives
  in the *system* prompt (via injected_context), not as its own message.

An exchange is `user` -> (optional `function_call` / `function_call_output`
items) -> `assistant`. `evict_chunk` is the MINIMUM number of oldest messages
to peel off; `split` then extends that cut forward to the next `user` message
so it lands on an exchange boundary. This means (a) a function_call is never
separated from its function_call_output — the API rejects that — and (b) the
kept remainder always starts with a user message, whether or not tool items
are interleaved. For a plain [user, assistant, …] history this is identical to
peeling off exactly `evict_chunk` messages.

This module is pure — it never mutates its input; the caller replaces its
history with the returned `kept` list.
"""

from __future__ import annotations

from typing import List, Mapping, Tuple

from server.config import WINDOW_MAX_MSGS, EVICT_CHUNK_MSGS

Msg = Mapping[str, str]


def _is_exchange_start(msg: Msg) -> bool:
    """True for a `user` message — the only thing that begins an exchange.
    Typed tool items (function_call / function_call_output) and assistant
    messages return False, so an exchange is never cut in the middle."""
    return msg.get("role") == "user"


class RollingWindow:
    def __init__(self, max_msgs: int = WINDOW_MAX_MSGS, evict_chunk: int = EVICT_CHUNK_MSGS):
        if evict_chunk % 2 != 0:
            raise ValueError(f"evict_chunk must be even (pair-aligned), got {evict_chunk}")
        if evict_chunk >= max_msgs:
            raise ValueError(f"evict_chunk ({evict_chunk}) must be < max_msgs ({max_msgs})")
        self.max_msgs = max_msgs
        self.evict_chunk = evict_chunk

    def needs_compaction(self, history: List[Msg]) -> bool:
        return len(history) >= self.max_msgs

    def split(self, history: List[Msg]) -> Tuple[List[Msg], List[Msg]]:
        """Return (evict, kept). `evict` is the oldest whole exchanges (at least
        `evict_chunk` messages); `kept` is the verbatim remainder, which always
        starts with a user message."""
        # Extend the cut forward to the next exchange start so we never split an
        # exchange (a function_call from its function_call_output, or a user from
        # its assistant). For a pure [user, assistant] history history[cut] is
        # already a user message at cut == evict_chunk, so this is a no-op.
        cut = self.evict_chunk
        while cut < len(history) and not _is_exchange_start(history[cut]):
            cut += 1
        evict = list(history[:cut])
        kept = list(history[cut:])
        print(f"[window] split: evict={len(evict)} kept={len(kept)} (from {len(history)})")
        return evict, kept


if __name__ == "__main__":
    win = RollingWindow()  # 16 / 6 from config
    assert win.evict_chunk % 2 == 0

    history: List[dict] = []
    compactions = 0

    # Drive enough turns to overflow twice; assert invariants each compaction.
    for turn in range(12):
        history.append({"role": "user", "content": f"u{turn}"})
        history.append({"role": "assistant", "content": f"a{turn}"})

        if win.needs_compaction(history):
            evict, kept = win.split(history)
            # whole-pairs evicted
            assert len(evict) == win.evict_chunk
            assert evict[0]["role"] == "user" and evict[-1]["role"] == "assistant"
            # kept never starts with assistant — the rule that keeps OpenAI valid
            assert kept[0]["role"] == "user", f"kept starts with {kept[0]['role']}!"
            # window shrinks back below max (16 -> 10 with these sizes)
            assert len(kept) == win.max_msgs - win.evict_chunk == 10
            history = kept
            compactions += 1

    assert compactions >= 2, f"expected the window to oscillate twice, got {compactions}"
    # Pairs preserved → even length, alternating roles from the front.
    assert len(history) % 2 == 0
    for i, msg in enumerate(history):
        assert msg["role"] == ("user" if i % 2 == 0 else "assistant")
    print(f"[window.__main__] OK — {compactions} compactions, kept always starts with user")

    # Tool items interleaved: an exchange is user -> (function_call,
    # function_call_output) -> assistant. split() must never leave `kept`
    # starting on a tool item or an assistant, and must keep each
    # function_call glued to its function_call_output.
    win2 = RollingWindow(max_msgs=8, evict_chunk=4)
    hist2: List[dict] = [
        {"role": "user", "content": "u0"},
        {"type": "function_call", "call_id": "c0", "name": "do_task", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c0", "output": "ok"},
        {"role": "assistant", "content": "a0"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ]
    assert win2.needs_compaction(hist2)
    ev, kp = win2.split(hist2)
    # evict_chunk=4 lands mid-exchange-1 (index 4 is a user, so it actually
    # lands cleanly here) — the guarantee we check is the invariant, not the count.
    assert kp[0].get("role") == "user", f"kept starts with {kp[0]!r}"
    # No dangling function_call_output without its function_call in kept.
    call_ids = [m.get("call_id") for m in kp if m.get("type") == "function_call"]
    for m in kp:
        if m.get("type") == "function_call_output":
            assert m["call_id"] in call_ids, "orphaned function_call_output in kept!"
    # And the evicted exchange kept its pair together.
    ev_calls = [m for m in ev if m.get("type") == "function_call"]
    ev_outs = [m for m in ev if m.get("type") == "function_call_output"]
    assert len(ev_calls) == len(ev_outs), "split a function_call from its output!"
    print("[window.__main__] OK — tool-item exchanges never split")
