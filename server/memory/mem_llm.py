"""Backend seam for the memory subsystem — local Ollama only.

  complete_json(messages, schema, model) -> a `schema` instance
  complete_text(messages, model)         -> str

Uses Ollama native /api/chat with think:false (same as Luna chat). No OpenAI.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Type, TypeVar

from pydantic import BaseModel

from server.config import MEMORY_LOCAL_BASE_URL

T = TypeVar("T", bound=BaseModel)

_JSON_OBJECT_RE = re.compile(r"\{[\s\S]*\}")


def _ollama_root() -> str:
    base = MEMORY_LOCAL_BASE_URL.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3].rstrip("/")
    return base


def _parse_json_payload(raw: str) -> dict:
    text = (raw or "").strip()
    if not text:
        raise ValueError("[mem-llm] empty local LLM reply")
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_OBJECT_RE.search(text)
        if not match:
            raise
        return json.loads(match.group(0))


def _ollama_chat(messages: list[dict], model: str, *, format_json: bool = False) -> str:
    """Non-streaming Ollama /api/chat with thinking forced off."""
    payload: dict = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": False,
        "options": {
            "temperature": 0.3,
            "num_predict": 1024,
        },
    }
    if format_json:
        payload["format"] = "json"
    data = json.dumps(payload).encode("utf-8")
    url = f"{_ollama_root()}/api/chat"
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"[mem-llm] Ollama unreachable at {url} — is it running? ({e})"
        ) from e
    msg = body.get("message") or {}
    content = (msg.get("content") or "").strip()
    if content:
        return content
    for key in ("thinking", "reasoning"):
        val = msg.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    raise ValueError("[mem-llm] Ollama returned empty content (think still on?)")


def complete_json(
    messages: list[dict],
    schema: Type[T],
    backend: str,
    model: str,
) -> T:
    """Return a validated `schema` instance from local Ollama."""
    if backend != "local":
        raise ValueError(
            f"[mem-llm] backend={backend!r} refused — memory is local/Ollama only"
        )
    print(f"[mem-llm] complete_json backend='local' model={model!r} schema={schema.__name__}")
    schema_hint = json.dumps(schema.model_json_schema())
    msgs = list(messages) + [{
        "role": "user",
        "content": (
            f"Reply with ONLY a JSON object matching this schema "
            f"(no markdown):\n{schema_hint}"
        ),
    }]
    raw = _ollama_chat(msgs, model, format_json=True)
    return schema.model_validate(_parse_json_payload(raw))


def complete_text(messages: list[dict], backend: str, model: str) -> str:
    """Return a plain text completion from local Ollama."""
    if backend != "local":
        raise ValueError(
            f"[mem-llm] backend={backend!r} refused — memory is local/Ollama only"
        )
    print(f"[mem-llm] complete_text backend='local' model={model!r}")
    return _ollama_chat(messages, model, format_json=False)


if __name__ == "__main__":
    from server.memory.schema import MemoryList

    assert "memories" in MemoryList.model_json_schema()["properties"]
    try:
        complete_json([], MemoryList, "openai", "x")
        raise SystemExit("[mem-llm.__main__] FAIL — openai should be refused")
    except ValueError:
        pass
    print("[mem-llm.__main__] OK — schema serialises, openai refused (no network)")
