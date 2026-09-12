"""Tag-aware streaming chunker.

Buffers LLM deltas and yields chunks for TTS. Chunks split ONLY on
sentence enders (`. ! ? …`) — comma splitting was tried (2026-06-10) and
reverted: it stranded short sentence tails as their own chunks
("...gratitude," | "boss!"), which sounds worse than waiting. The FIRST
chunk still ships early because its speakable minimum is small (a short
first sentence goes out alone); later chunks wait for more because
playback always outpaces generation after chunk 0.

Invariants (each has a case in tools/test_stream_chunker.py):
  * never splits an unclosed `<...>` tag
  * minimum AND maximum lengths are measured on SPEAKABLE text (tags
    stripped): a tag-heavy chunk can't smuggle a tiny TTS fragment like
    "Ah," through, and leading tags can't eat the length budget and
    force a mid-sentence cut before the first real boundary
  * boundary punctuation only counts when followed by whitespace/end —
    decimals ("3.5") and mid-token punctuation never split
  * a boundary is found anywhere in the buffer (not just at its end), so
    a delta like "on, y" doesn't make the chunker miss the comma
  * sentence enders count through closing quotes ("just saying.'")
  * forced max-length cuts back up to the last soft punctuation (a pause
    at a comma sounds natural), falling back to a word boundary — never
    mid-word (the old behavior produced "Tug" + "ging")
  * apostrophes are never split points

Yield rules (in order):
  1. If the buffer is inside an unclosed `<`, do NOT yield — accumulate.
  2. Cut after the last sentence ender whose left side has speakable
     length >= the chunk's min (FIRST_MIN_LEN for chunk 0, REST_MIN_LEN
     after).
  3. Force-yield when speakable length reaches the chunk's max — at the
     last soft punctuation if any, else the last whitespace outside a tag.
  4. Final flush on iterator exhaustion.
"""

from __future__ import annotations

import re
import time
from typing import Callable, Iterable, Iterator, Mapping, Optional

from server.llm.llm_backend import LLMBackend


# Chunk boundaries: sentence enders ONLY (see module docstring for why
# commas were demoted). Soft punctuation is used solely for emergency
# cuts when a stretch exceeds the length cap.
TERMS = (".", "!", "?", "…")
SOFT_CUT_TERMS = (",", ";", ":") + TERMS

# FIRST chunk: small minimum so a short COMPLETE opening sentence ships
# early (keeps first-audio latency low on Ino's streaming path). Its MAX now
# matches REST_MAX_LEN — the first chunk must never force-cut a long opening
# sentence at a comma. That mid-sentence break was the first thing you'd hear
# on Ino's streamed chunk 0 (and it desynced the follow-on chunk). A short
# sentence still ships early via the min; a long one waits for its sentence
# ender like every other chunk. (Was 100 — the low cap was the "split at the
# first comma" behavior; negligible latency win, real audio artifacts.)
FIRST_MIN_LEN = 8
FIRST_MAX_LEN = 200

# REST chunks: bigger minimum — no rush once audio is playing.
REST_MIN_LEN = 30
REST_MAX_LEN = 200

# Quote/bracket chars allowed between a terminator and its confirming
# whitespace, so sentence enders inside quotes count: "just saying.'"
_CLOSERS = "'\")]’”»"

_TAG_SPAN = re.compile(r"<[^<>]*>")


def _inside_open_tag(s: str) -> bool:
    """True iff `s` ends with an unclosed `<` — i.e. there is a `<` after
    the last `>` in the string (or no `>` at all but there is a `<`)."""
    last_open = s.rfind("<")
    if last_open == -1:
        return False
    last_close = s.rfind(">")
    return last_close < last_open


def _speakable_len(s: str) -> int:
    """Length of the text that would actually reach TTS: complete `<...>`
    tags removed, surrounding whitespace stripped."""
    return len(_TAG_SPAN.sub("", s).strip())


def _find_punct_cut(s: str, terms: tuple[str, ...], min_len: int) -> Optional[int]:
    """Index just AFTER the last terminator in `s` that (a) already has
    whitespace after it in the buffer (optionally through closing quotes,
    so "saying.'" counts), (b) is not inside an open tag, and (c) leaves
    >= min_len speakable chars on its left. None if no cut.

    A terminator at the very END of the buffer does NOT count: the stream
    may be mid-token there (the '.' of an incoming "3.5"). The confirming
    whitespace arrives with the next delta; true stream end is handled by
    the final flush."""
    for i in range(len(s) - 2, -1, -1):
        if s[i] not in terms:
            continue
        j = i + 1
        while j < len(s) and s[j] in _CLOSERS:
            j += 1
        if j >= len(s) or not s[j].isspace():
            continue  # "3.5", "?!" mid-run — not a real boundary
        head = s[:j]
        if _inside_open_tag(head):
            continue
        if _speakable_len(head) >= min_len:
            return j
    return None


def _cut_at_word_boundary(s: str) -> tuple[str, str]:
    """Split at the last whitespace that is not inside a `<...>` tag (tags
    may contain spaces: `<expr:exaggerated think>`). Falls back to no cut
    (whole string, empty tail) if there is no safe whitespace."""
    for i in range(len(s) - 1, 0, -1):
        if s[i].isspace() and not _inside_open_tag(s[:i]) and s[:i].strip():
            return s[:i], s[i:]
    return s, ""


def stream_text_chunks(
    backend: LLMBackend,
    messages: Iterable[Mapping[str, str]],
    on_first_token: Optional[Callable[[float], None]] = None,
) -> Iterator[str]:
    """Yield TTS-ready chunks from the LLM stream per the module rules.

    `on_first_token(t_perf)` is called exactly once, the first time the
    backend emits any text — used by the Ino orchestrator to stamp TTFT.
    """
    buffer = ""
    chunk_idx = 0
    first_token_signaled = False

    for delta in backend.stream(messages):
        if not first_token_signaled:
            first_token_signaled = True
            if on_first_token is not None:
                try:
                    on_first_token(time.perf_counter())
                except Exception as e:
                    print(f"[stream] on_first_token raised: {e!r}")

        buffer += delta

        # Rule 1: never yield while inside an open tag — wait for `>`.
        if _inside_open_tag(buffer):
            continue

        first = chunk_idx == 0
        min_len = FIRST_MIN_LEN if first else REST_MIN_LEN
        max_len = FIRST_MAX_LEN if first else REST_MAX_LEN

        # Rule 2: sentence-ender boundary with enough speakable text.
        cut = _find_punct_cut(buffer, TERMS, min_len)
        if cut is not None:
            out = buffer[:cut].strip()
            print(
                f"[stream] yield #{chunk_idx} (punct, first={first}, "
                f"len={len(out)}, speakable={_speakable_len(out)}): {out!r}"
            )
            chunk_idx += 1
            yield out
            buffer = buffer[cut:]
            continue

        # Rule 3: force flush on length, measured on SPEAKABLE text like
        # the minimums (tags must not eat the cap). Prefer cutting at the
        # last soft punctuation — a pause at a comma sounds natural; a
        # pause between "my" and "nose" does not. Bare word boundary only
        # when the buffer has no punctuation at all.
        if _speakable_len(buffer) >= max_len:
            cut = _find_punct_cut(buffer, SOFT_CUT_TERMS, min_len)
            if cut is not None:
                head, tail, how = buffer[:cut], buffer[cut:], "maxlen-punct"
            else:
                head, tail = _cut_at_word_boundary(buffer)
                how = "maxlen-word"
            out = head.strip()
            if out:
                print(
                    f"[stream] yield #{chunk_idx} ({how}, first={first}, "
                    f"len={len(out)}): {out!r}"
                )
                chunk_idx += 1
                yield out
            buffer = tail

    # Rule 4: final flush.
    if buffer.strip():
        out = buffer.strip()
        print(f"[stream] yield #{chunk_idx} (final, len={len(out)}): {out!r}")
        yield out


if __name__ == "__main__":
    # Live standalone test: stream a real LLM reply and verify the chunk
    # invariants hold. Offline cases live in
    # tools/test_stream_chunker.py (no servers needed).
    # Run with: python -m server.llm.stream_chunker
    from server.config import MODEL, SYSTEM_PROMPT
    from server.llm.llm_backend import OpenAIBackend

    backend = OpenAIBackend(model=MODEL)

    user_text = (
        "For this response, do exactly two things: "
        "(1) emit a single tag of the form <emote:backflip> on its own line "
        "in the middle of your reply, and "
        "(2) say something cheeky in 2-3 short sentences. "
        "Do not explain the tag, just include it inline."
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_text},
    ]

    print("[stream_chunker.__main__] streaming...")
    chunks: list[str] = []
    for chunk in stream_text_chunks(backend, messages):
        chunks.append(chunk)
        assert chunk.count("<") == chunk.count(">"), (
            f"chunk has unbalanced <...>: {chunk!r}"
        )
        assert not _inside_open_tag(chunk), (
            f"chunk ends with unclosed '<': {chunk!r}"
        )

    print(f"[stream_chunker.__main__] total chunks: {len(chunks)}")
    full = " ".join(chunks)
    has_tag = "<emote:" in full or "<expr:" in full
    print(f"[stream_chunker.__main__] reconstructed contains a tag: {has_tag}")
    print(f"[stream_chunker.__main__] reconstructed: {full!r}")
    print("[stream_chunker.__main__] OK — no chunk split an open tag")
