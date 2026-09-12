"""SessionLog — the raw, complete transcript of one session.

This is the source of truth for long-term extraction (NOT the windowed
chat_history, and NOT the ephemeral gist). One JSONL file per session,
one turn per line: {"role": "user"|"assistant", "text": "..."}.

`strip_tags` removes the inline `<expr:…>`/`<emote:…>` markup and `*`
emphasis so distilled memories aren't polluted by avatar-control syntax.
It is deliberately lighter than text_clean.clean_for_tts — we keep names,
caps and punctuation intact (this is for an LLM to read, not for TTS).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import List

from server.config import CHARACTER, MEMORY_DIR
from server.llm.tag_parser import _TAG_RE

# How the two speakers are named in the transcript the memory LLM reads.
# Follows the character so a renamed persona writes memories about herself.
ROLE_LABELS = {"user": "User", "assistant": CHARACTER.capitalize()}

# Reuse the well-formed-tag pattern; also catch any leftover tag-shaped span
# and markdown asterisks (mirrors text_clean.REPLACEMENTS minus the TTS bits).
_LOOSE_TAG_RE = re.compile(r"<[a-zA-Z][^<>]*>")
_ASTERISK_RE = re.compile(r"\*+")
_WS_RE = re.compile(r"[ \t]+")


def strip_tags(text: str) -> str:
    """Remove expr/emote tags + markdown asterisks; collapse spacing."""
    out = _TAG_RE.sub("", text)
    out = _LOOSE_TAG_RE.sub("", out)
    out = _ASTERISK_RE.sub("", out)
    out = _WS_RE.sub(" ", out)
    return out.strip()


class SessionLog:
    def __init__(self, path: str | Path | None = None):
        if path is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = MEMORY_DIR / "sessions" / f"session_{ts}.jsonl"
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        print(f"[mem] session log → {self.path}")

    def append(self, role: str, text: str) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"role": role, "text": text}, ensure_ascii=False) + "\n")

    def turns(self) -> List[dict]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return [json.loads(ln) for ln in lines if ln.strip()]

    def transcript(self) -> str:
        """Full session as a clean, labelled, tag-stripped transcript."""
        out = []
        for t in self.turns():
            label = ROLE_LABELS.get(t["role"], t["role"])
            text = strip_tags(t["text"])
            if text:
                out.append(f"{label}: {text}")
        return "\n".join(out)


if __name__ == "__main__":
    import tempfile

    # strip_tags behaviour
    assert strip_tags("<expr:happy>hey <emote:wave> there!") == "hey there!"
    assert strip_tags("**so** cool") == "so cool"
    assert strip_tags("The user LOVES AI") == "The user LOVES AI"  # caps preserved

    with tempfile.TemporaryDirectory() as tmp:
        log = SessionLog(Path(tmp) / "s.jsonl")
        log.append("user", "hey riko, I love spicy ramen")
        log.append("assistant", "<expr:happy>Spicy ramen? *noted* — good taste!")
        assert len(log.turns()) == 2
        tx = log.transcript()
        assert "User: hey riko" in tx
        assert "Spicy ramen?" in tx and "<expr" not in tx and "*" not in tx
        print(tx)
    print("[session_log.__main__] OK — append / turns / transcript / strip_tags")
