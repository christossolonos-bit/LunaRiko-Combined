"""turn_log.py — append-only per-turn log for fine-tuning / debugging.

Each completed turn the orchestrator writes one JSON line to
`<project>/logs/turns_<YYYY-MM-DD>.jsonl`. Schema per line:

    {
      "ts": ISO timestamp,
      "function_calling": bool,
      "system": str,                  the actual system prompt used this turn
      "history_in": list[{role, content}],   prior history (no system msg)
      "user": str,
      "injected_context": str,        what she was told this turn
      "tool_calls": list[{name, arguments, output}],
      "assistant": str                raw assistant text, tags included
    }

This is SEPARATE from chat_history.json:

  - chat_history.json — only Riko's spoken words, so the next turn's
    in-context history stays in-character (no JSON tool-call items leaking
    in and nudging her toward assistant-mode).
  - logs/turns_*.jsonl — the full structured exchange, suitable for later
    reshaping into fine-tuning examples or replaying for debugging.

Append-only, fire-and-forget. A logging failure must never crash a turn —
the orchestrator catches exceptions around the `log_turn` call.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from server.config import LOGS_DIR, vlog


def log_turn(record: dict) -> Optional[str]:
    """Append `record` as one JSON line to today's turn log. Returns the
    file path written to (for the caller's debug print) or None on failure.

    `record` is expected to follow the schema in the module docstring.
    Missing keys are tolerated — only `ts` is filled in by default.
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    record = dict(record)
    record.setdefault("ts", datetime.now().isoformat(timespec="seconds"))
    day = datetime.now().strftime("%Y-%m-%d")
    path = LOGS_DIR / f"turns_{day}.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False))
        f.write("\n")
    vlog(f"[turn_log] appended turn "
          f"({len(record.get('tool_calls', []))} tool call(s)) -> {path}")
    return str(path)


if __name__ == "__main__":
    # Offline self-check: writes one synthetic turn and reads it back.
    # Run with: python -m server.turn_log
    sample = {
        "function_calling": True,
        "system": "You are Riko. ...",
        "history_in": [],
        "user": "hey check the weather",
        "injected_context": "",
        "tool_calls": [{
            "name": "do_task",
            "arguments": {"instruction": "check the weather in Vancouver"},
            "output": "The weather in Vancouver is 23 degrees C and sunny today.",
        }],
        "assistant": "<expr:smug>Oh look, sunny again. <emote:flips_hair>",
    }
    path = log_turn(sample)
    assert path is not None

    # Sanity: last line of the file parses and is what we just wrote.
    with open(path, "r", encoding="utf-8") as f:
        last = f.readlines()[-1]
    parsed = json.loads(last)
    assert parsed["user"] == sample["user"]
    assert parsed["tool_calls"][0]["name"] == "do_task"
    print(f"[turn_log.__main__] OK — wrote and read back from {path}")
