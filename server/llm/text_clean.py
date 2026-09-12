"""Lightweight LLM-output cleaner for TTS + subtitle display.

The chunker can emit fragments that START with punctuation (e.g.
`;You're welcome!`) when the previous chunk ended on `.`/`?`/`!`. SoVITS
then either rejects the input outright (400) or reads punctuation
literally ("non-negotiable" → "non minus negotiable"). This module is
the single pre-clean step the orchestrator calls right before
`synthesize(...)`.

DESIGN — intentionally tiny and table-driven so new rules are one-line
additions:

    LEADING_JUNK            regex for chars to strip from the front
    INTRA_WORD_HYPHEN_FIX   regex for letter-hyphen-letter -> letter space letter
    REPLACEMENTS            ordered list of (pattern, replacement) — add here
                            for words SoVITS mispronounces

`clean_for_tts(text)` returns the cleaned string; the orchestrator uses
the result for BOTH the SoVITS request body and the subtitle text passed
to `vrm_talk`, so what the avatar speaks is exactly what shows on screen.
"""

from __future__ import annotations

import re

# Characters we trim off the start of a chunk. Quotes and parens are left
# alone — those usually carry meaning. Dash variants and semicolons here.
LEADING_JUNK = re.compile(r"^[\s;:,.\-—–…!?]+")

# "non-negotiable" / "state-of-the-art" → space-separated words so SoVITS
# treats them as two tokens instead of saying "minus". Only matches when
# letters sit on both sides of the hyphen so flag-style "-h" stays put.
INTRA_WORD_HYPHEN_FIX = re.compile(r"(?<=[A-Za-z])-(?=[A-Za-z])")

# Curly/unicode apostrophes → ASCII. Verified by ear (2026-06-10, TODO bug
# #8): SoVITS reads "he’s" (U+2019) as "h e s" but ASCII "he's" correctly.
UNICODE_APOSTROPHE_FIX = re.compile(r"[‘’ʼ´`]")

# ALL-CAPS words → lowercase. Verified by ear (2026-06-10, TODO bug #7):
# SoVITS spells caps words letter-by-letter ("PLEASE" → P-L-E-A-S-E).
# Words of ≤ 3 letters are left intact — those are usually acronyms (AI,
# GPU) that SHOULD be spelled out. Trade-off: short shouty words ("SO")
# also stay spelled out; length alone can't tell them apart.
ALL_CAPS_WORD = re.compile(r"\b[A-Z]+(?:'[A-Z]+)?\b")


def _lower_long_caps(match: re.Match) -> str:
    word = match.group(0)
    letters = sum(c.isalpha() for c in word)
    return word.lower() if letters >= 4 else word

# Ordered (pattern, replacement) pairs applied AFTER the structural cleanup
# above. Add entries here as you find words SoVITS mishandles.
# Tip: use `\b...\b` for whole-word matching, and `re.IGNORECASE` is on by
# default so don't worry about case.
REPLACEMENTS: list[tuple[str, str]] = [
    # Fish TTS reads literal asterisks ("**bold**" → "asterisk asterisk bold
    # asterisk asterisk"). SoVITS occasionally trips on them too. The LLM
    # sometimes emits markdown-style emphasis; strip it everywhere before TTS.
    (r"\*+", ""),
    # Safety net for malformed inline tags. The tag parser only matches
    # well-formed `<expr:...>` / `<emote:...>`; anything garbled
    # (`<pexpr:fake-thinking>`, `<expr:annoyed, playful>`) falls through as
    # TEXT and would be read aloud. Strip any remaining tag-shaped `<...>`
    # span (must start with a letter, so "a < b and b > c" survives) before
    # it reaches TTS or the subtitles.
    (r"<[a-zA-Z][^<>]*>", ""),
    # Example placeholder — uncomment / extend when needed:
    # (r"\bAI\b", "A I"),
]


def clean_for_tts(text: str) -> str:
    """Run the chunk through the cleanup chain and return the result."""
    out = text.strip()
    out = UNICODE_APOSTROPHE_FIX.sub("'", out)
    out = LEADING_JUNK.sub("", out)
    out = INTRA_WORD_HYPHEN_FIX.sub(" ", out)
    out = ALL_CAPS_WORD.sub(_lower_long_caps, out)
    for pattern, replacement in REPLACEMENTS:
        out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)
    return out.strip()


if __name__ == "__main__":
    cases = [
        (";You're welcome!", "You're welcome!"),
        ("...just a thought.", "just a thought."),
        ("That's non-negotiable.", "That's non negotiable."),
        ("Use the -h flag for help.", "Use the -h flag for help."),
        ("   leading whitespace   ", "leading whitespace"),
        ("…ellipsis start", "ellipsis start"),
        ("**bold** text", "bold text"),
        ("*emphasis* here", "emphasis here"),
        ("<pexpr:fake-thinking> Hmm, let me see.", "Hmm, let me see."),
        ("Oh wow <expr:annoyed, playful> very funny.", "Oh wow  very funny."),
        ("a < b and b > c stays put", "a < b and b > c stays put"),
        ("He said he’s going to the store.", "He said he's going to the store."),
        ("Will someone PLEASE pay me already?", "Will someone please pay me already?"),
        ("That is SO unfair, I am NEVER doing that again.", "That is SO unfair, I am never doing that again."),
        ("My GPU is dying and the AI keeps talking.", "My GPU is dying and the AI keeps talking."),
        ("I said DON'T touch that.", "I said don't touch that."),
    ]
    for raw, want in cases:
        got = clean_for_tts(raw)
        status = "OK " if got == want else "FAIL"
        print(f"{status}  {raw!r} -> {got!r}  (want {want!r})")
