"""Session gist — the medium-term rolling summary.

When pairs scroll out of the verbatim window, they're folded into a single
running summary that stays in the system prompt. This is COMPRESSION, not
retrieval: a small block the model always sees, so older context never
"falls off a cliff" mid-session.

Built in the background between turns, so it never makes a reply slower.
Capped at `GIST_MAX_TOKENS`; the prompt asks the model to recompact when it
grows past that. Backend and model are set in server/config.py.
"""

from __future__ import annotations

from server.config import (
    CHARACTER,
    GIST_BACKEND,
    GIST_MAX_TOKENS,
    MEMORY_LOCAL_MODEL,
)
from server.memory import mem_llm


def _model_for(backend: str) -> str:
    if backend != "local":
        raise ValueError(
            f"[gist] backend={backend!r} refused — local/Ollama only"
        )
    return MEMORY_LOCAL_MODEL


def _prompt(old_gist: str, evicted_lines: str) -> str:
    # Second person, and named after whichever character is loaded — this text
    # ends up inside her own system prompt, which says "You are <name>".
    have_gist = old_gist.strip() or "(none yet)"
    name = CHARACTER.capitalize()
    return f"""\
You are {name}. Maintain a single ROLLING summary of your ongoing conversation with the user, written in the SECOND person from your perspective (you = {name}) — e.g. "You and the user talked about his Apex run; you teased him for streaming with it off."

Summary so far:
{have_gist}

Newer lines now scrolling out of the recent-history window:
{evicted_lines}

REWRITE the summary so far into one updated summary that absorbs the newer lines.
This is a REPLACEMENT, not an append — never just tack the new lines on the end; rewrite the whole thing so it does not grow each time.
Rules:
- Keep the second-person voice throughout ("you", not "{name}" or "I").
- When Discord tags or names appear (Solonaras, Travis, T4rik, etc.), keep facts ON that person — never merge people into one "the user".
- Website/local mic tagged as Solonaras is the SAME person as Discord Solonaras (owner).
- Weight RECENT events most. As older material ages, compress it harder — keep only durable facts (names, decisions, ongoing topics) and let stale detail fade or drop.
- Keep concrete facts, names, decisions, and the current topic. Drop filler, greetings, small talk, and recycled lecture topics nobody asked about.
- Be terse — plain prose or a few short bullets. No preamble, no meta commentary.
- Hard limit: about {GIST_MAX_TOKENS} tokens. If you would exceed it, recompact and keep only what still matters.
Output ONLY the updated summary text."""


def update_gist(old_gist: str, evicted_lines: str) -> str:
    """Return the new rolling gist after folding in `evicted_lines`."""
    backend = GIST_BACKEND
    model = _model_for(backend)
    print(f"[gist] update via backend={backend!r} model={model!r} "
          f"old_len={len(old_gist)} evicted_len={len(evicted_lines)}")
    messages = [{"role": "user", "content": _prompt(old_gist, evicted_lines)}]
    new_gist = mem_llm.complete_text(messages, backend, model)
    print(f"[gist] new gist ({len(new_gist)} chars): {new_gist[:120]}{'…' if len(new_gist) > 120 else ''}")
    return new_gist


if __name__ == "__main__":
    # Offline: check the prompt assembles correctly. No network.
    p = _prompt("", "User: I got a cat named Mochi.\nRiko: cute!")
    # second-person voice + replacement (not append) + the evicted lines present
    assert "Mochi" in p and "You are " in p and "REPLACEMENT" in p
    p2 = _prompt("The user adopted a cat named Mochi.", "User: I'm quitting my job to stream.")
    assert "Mochi" in p2 and "quitting" in p2 and "SECOND person" in p2
    print("[gist.__main__] OK — prompt assembles, second-person + replacement (no network)")
