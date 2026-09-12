"""Inline tag parser.

Converts a single streamed chunk of LLM text into an ordered list of events.
Tags look like `<expr:moneyeyes>` or `<emote:backflip>` (case-insensitive,
names lowercased on the way out). Any text outside of tags becomes a TEXT
event; whitespace at the boundaries of a TEXT event is stripped, and empty
TEXT events are dropped entirely.

Events preserve their original left-to-right order so the playback worker
can drive the avatar in the exact sequence the LLM emitted them.

Public surface:
    Event = tuple[str, str]          # ("EXPR"|"EMOTE"|"TEXT", value)
    parse_chunk(text) -> list[Event]
"""

from __future__ import annotations

import re
from typing import Tuple, List


Event = Tuple[str, str]

# `<expr:name>` or `<emote:name>`. Names allow letters/digits/underscores AND
# spaces or hyphens — the LLM sometimes emits multi-word names like
# `<expr:exaggerated think>` or `<emote:sad-idle>`, and those should still
# reach the semantic picker instead of leaking through to TTS. We normalize
# the captured name below (lowercase + whitespace collapsed) so the picker
# sees a clean single-line string.
_TAG_RE = re.compile(r"<(expr|emote):([a-zA-Z0-9_\- ]+)>", re.IGNORECASE)


def _normalize_name(raw: str) -> str:
    """Lowercase + collapse internal whitespace + strip ends."""
    return re.sub(r"\s+", " ", raw).strip().lower()


def parse_chunk(text: str) -> List[Event]:
    """Parse a chunk of mixed text + inline tags into ordered events.

    Tags are recognized as `<expr:name>` or `<emote:name>`. The name is
    lowercased. Text between (or outside of) tags becomes a TEXT event with
    surrounding whitespace stripped; empty TEXT events are dropped.
    """
    events: List[Event] = []
    cursor = 0

    for match in _TAG_RE.finditer(text):
        # Any plain text before this tag is a TEXT event.
        if match.start() > cursor:
            chunk_text = text[cursor:match.start()].strip()
            if chunk_text:
                events.append(("TEXT", chunk_text))

        kind = match.group(1).upper()      # "EXPR" or "EMOTE"
        name = _normalize_name(match.group(2))
        if not name:
            # Defensive — pattern requires at least one char, but if all chars
            # were whitespace we'd end up empty. Skip it.
            cursor = match.end()
            continue
        events.append((kind, name))
        cursor = match.end()

    # Trailing text after the final tag (or the whole string if there were
    # no tags at all).
    if cursor < len(text):
        tail = text[cursor:].strip()
        if tail:
            events.append(("TEXT", tail))

    print(f"[parse] {len(events)} event(s) from chunk len={len(text)}: {events}")
    return events


if __name__ == "__main__":
    # Standalone tests — each case prints its result and asserts the expected
    # event list. Run with: python -m server.llm.tag_parser
    print("[tag_parser.__main__] running tests...")

    # 1. A typical chunk: two tags, then the line.
    sample = (
        "<expr:moneyeyes>\n"
        "<emote:spin>\n"
        "Did you say 'pay'? Well in that case... Watch and learn!"
    )
    expected = [
        ("EXPR", "moneyeyes"),
        ("EMOTE", "spin"),
        ("TEXT", "Did you say 'pay'? Well in that case... Watch and learn!"),
    ]
    got = parse_chunk(sample)
    assert got == expected, f"sample mismatch:\n got={got}\n exp={expected}"

    # 2. Tag mid-sentence.
    got = parse_chunk("Hello there <emote:wave> nice to meet you.")
    assert got == [
        ("TEXT", "Hello there"),
        ("EMOTE", "wave"),
        ("TEXT", "nice to meet you."),
    ], got

    # 3. Pure text — no tags at all.
    got = parse_chunk("  just plain text, nothing special.  ")
    assert got == [("TEXT", "just plain text, nothing special.")], got

    # 4. Only tags, no surrounding text.
    got = parse_chunk("<expr:happy><emote:backflip>")
    assert got == [("EXPR", "happy"), ("EMOTE", "backflip")], got

    # 5. Empty whitespace between adjacent tags is dropped.
    got = parse_chunk("<expr:sad>   \n   <emote:cry>")
    assert got == [("EXPR", "sad"), ("EMOTE", "cry")], got

    # 6. Case-insensitive tag kind; name is lowercased.
    got = parse_chunk("<EXPR:MoneyEyes> hi")
    assert got == [("EXPR", "moneyeyes"), ("TEXT", "hi")], got

    # 7. Empty input.
    got = parse_chunk("")
    assert got == [], got

    # 8. Unknown tag prefix is left alone (treated as plain text).
    got = parse_chunk("<other:foo> stays as text")
    assert got == [("TEXT", "<other:foo> stays as text")], got

    # 9. Multi-word names (with internal spaces) are accepted + normalized.
    got = parse_chunk("<expr:exaggerated think> wow")
    assert got == [("EXPR", "exaggerated think"), ("TEXT", "wow")], got

    # 10. Hyphenated names work; surrounding whitespace inside the tag is
    #     collapsed so `<emote: sad  idle >` reads as 'sad idle'.
    got = parse_chunk("<emote: sad  idle >")
    assert got == [("EMOTE", "sad idle")], got

    print("[tag_parser.__main__] OK — all cases passed")
