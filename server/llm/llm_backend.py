"""LLM backend abstraction.

Implementations:
  - `OpenAIBackend` — OpenAI Responses API (cloud).
  - `LocalLLMBackend` — local Ollama native /api/chat, or OpenAI-compatible
    /v1/chat/completions (LM Studio, llama.cpp, etc.).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Iterable, Iterator, List, Mapping, Optional

from dotenv import load_dotenv
from openai import OpenAI

from server.config import vlog


def usage_dict(final) -> dict:
    """Pull the token counts off a completed Responses object.

    `cached_tokens` is the field that answers whether prompt caching is firing
    at all. OpenAI matches on the longest common PREFIX, so anything volatile
    at the FRONT of the message list drives it to zero — today that is
    `injected_context`, which prompt_builder folds into the system prompt at
    messages[0]. Log it before changing anything: see the caching test in
    the OpenAI docs.

    Defensive throughout — a missing usage block must never cost a turn.
    """
    u = getattr(final, "usage", None)
    if u is None:
        return {}
    details = getattr(u, "input_tokens_details", None)
    return {
        "input_tokens":  getattr(u, "input_tokens", None),
        "output_tokens": getattr(u, "output_tokens", None),
        "cached_tokens": getattr(details, "cached_tokens", 0) if details else 0,
    }


def accumulate_usage(sink: dict, new: dict) -> None:
    """Sum `new` into `sink` in place. The tool loop makes one API call per
    round, so a turn with a tool call has more than one usage block."""
    for k, v in new.items():
        if v is None:
            continue
        sink[k] = (sink.get(k) or 0) + v


class LLMBackend(ABC):
    """Abstract streaming LLM backend.

    `stream(messages)` must yield raw text deltas (str fragments) in order.
    The chunker upstream is responsible for buffering / boundary detection;
    backends do not return punctuation-aligned chunks.
    """

    @abstractmethod
    def stream(self, messages: Iterable[Mapping[str, str]]) -> Iterator[str]:
        raise NotImplementedError


def _plain_chat_messages(messages: Iterable[Mapping]) -> List[dict]:
    """Keep only role/content chat messages local servers understand."""
    out: List[dict] = []
    for msg in messages:
        role = msg.get("role")
        if role not in ("system", "user", "assistant"):
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") in (
                    "input_text", "text",
                ):
                    parts.append(str(part.get("text") or ""))
                elif isinstance(part, str):
                    parts.append(part)
            content = "\n".join(p for p in parts if p)
        out.append({"role": role, "content": str(content)})
    return out


class OpenAIBackend(LLMBackend):
    """OpenAI Responses-API backend.

    Uses `client.responses.stream(...)` and yields `event.delta` strings on
    `response.output_text.delta` events. All other event types are ignored —
    the iterator terminates naturally when the response is complete.
    """

    def __init__(
        self,
        model: str,
        temperature: float = 1.0,
        top_p: float = 1.0,
        max_output_tokens: int = 512,
    ):
        load_dotenv()
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY not set in environment or .env file."
            )
        self.client = OpenAI(api_key=api_key)
        self.model = model
        self.temperature = temperature
        self.top_p = top_p
        self.max_output_tokens = max_output_tokens
        print(f"[llm_backend] OpenAIBackend ready model={self.model}")

    def stream(self, messages: Iterable[Mapping[str, str]]) -> Iterator[str]:
        msgs: List[Mapping[str, str]] = list(messages)
        # Per-turn token counts, read by the orchestrator for the turn log.
        # Same pattern as OpenAIToolBackend.last_turn_tool_calls: reset here so
        # a failed turn can never report the previous turn's numbers.
        self.last_turn_usage: dict = {}
        vlog(f"[llm_backend] stream start messages={len(msgs)}")
        with self.client.responses.stream(
            model=self.model,
            input=msgs,
            temperature=self.temperature,
            top_p=self.top_p,
            max_output_tokens=self.max_output_tokens,
        ) as stream:
            for event in stream:
                if event.type == "response.output_text.delta":
                    yield event.delta
                elif event.type == "response.output_text.done":
                    # Logged for visibility; iterator terminates after stream exits.
                    vlog("[llm_backend] output_text.done")
            self.last_turn_usage = usage_dict(stream.get_final_response())
        vlog("[llm_backend] stream end")


class LocalLLMBackend(LLMBackend):
    """Local model via Ollama native API or OpenAI-compatible chat completions.

    Thinking is OFF (`think: false`). Qwen3 thinking models otherwise spend the
    whole token budget on reasoning and return empty spoken content.
    """

    def __init__(
        self,
        model: str,
        *,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        api_style: Optional[str] = None,
        think: Optional[bool] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.8,
    ):
        from server import config as cfg

        self.model = model
        self.base_url = (base_url or cfg.LOCAL_LLM_BASE_URL).rstrip("/")
        self.api_key = api_key if api_key is not None else cfg.LOCAL_LLM_API_KEY
        self.api_style = (api_style or cfg.LOCAL_LLM_API_STYLE).strip().lower()
        # Always false unless the caller/config explicitly enables thinking.
        if think is None:
            self.think = bool(cfg.LOCAL_LLM_THINK)
        else:
            self.think = bool(think)
        self.max_tokens = (
            cfg.LOCAL_LLM_MAX_TOKENS if max_tokens is None else max_tokens
        )
        self.temperature = temperature
        self.last_turn_usage: dict = {}

        # Ensure .env is loaded even if config was imported before dotenv ran.
        load_dotenv()
        if not (self.api_key or "").strip() or self.api_key.strip() in (
            "ollama", "local", "env:NVIDIA_API_KEY",
        ):
            host = self.base_url.lower()
            if "nvidia.com" in host or "integrate.api.nvidia" in host:
                self.api_key = (os.getenv("NVIDIA_API_KEY") or "").strip()

        if self.api_style == "openai":
            openai_base = self.base_url
            if openai_base.endswith("/v1"):
                openai_base = openai_base[:-3]
            self._openai = OpenAI(
                base_url=f"{openai_base}/v1",
                api_key=self.api_key or "local",
            )
        else:
            self._openai = None

        print(
            f"[llm_backend] LocalLLMBackend ready model={self.model!r} "
            f"url={self.base_url!r} style={self.api_style!r} think={self.think}"
        )

    def stream(self, messages: Iterable[Mapping[str, str]]) -> Iterator[str]:
        msgs = _plain_chat_messages(messages)
        self.last_turn_usage = {}
        vlog(f"[llm_backend.local] stream start messages={len(msgs)}")
        if self.api_style == "openai":
            yield from self._stream_openai(msgs)
        else:
            yield from self._stream_ollama(msgs)
        vlog("[llm_backend.local] stream end")

    def _stream_ollama(self, msgs: List[dict]) -> Iterator[str]:
        # Hard-pass false when disabled — never omit the field (Ollama defaults on).
        payload = {
            "model": self.model,
            "messages": msgs,
            "stream": True,
            "think": False if not self.think else True,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    msg = chunk.get("message") or {}
                    # Spoken content only — never yield reasoning.
                    piece = msg.get("content") or ""
                    if piece:
                        yield piece
                    if chunk.get("done"):
                        self.last_turn_usage = {
                            "input_tokens": chunk.get("prompt_eval_count"),
                            "output_tokens": chunk.get("eval_count"),
                            "cached_tokens": 0,
                        }
                        break
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"Local LLM unreachable at {self.base_url}/api/chat — "
                f"is Ollama running? ({e})"
            ) from e

    def _openai_extra_body(self) -> dict:
        """Provider-specific knobs. NVIDIA Nemotron uses chat_template_kwargs."""
        host = self.base_url.lower()
        if "nvidia.com" in host or "integrate.api.nvidia" in host:
            return {
                "chat_template_kwargs": {
                    "enable_thinking": bool(self.think),
                }
            }
        return {"think": bool(self.think)}

    def _stream_openai(self, msgs: List[dict]) -> Iterator[str]:
        assert self._openai is not None
        if not (self.api_key or "").strip() or self.api_key.strip() in (
            "ollama", "local", "env:NVIDIA_API_KEY",
        ):
            raise RuntimeError(
                "LLM API key missing — set NVIDIA_API_KEY (or local_llm_api_key) in .env"
            )
        stream = self._openai.chat.completions.create(
            model=self.model,
            messages=msgs,
            stream=True,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            top_p=0.95,
            extra_body=self._openai_extra_body(),
        )
        assembled: List[str] = []
        for event in stream:
            if not event.choices:
                continue
            delta = event.choices[0].delta
            # Spoken content only — ignore Nemotron reasoning_content if present.
            piece = getattr(delta, "content", None) or ""
            if piece:
                assembled.append(piece)
                yield piece
        if not assembled:
            raise RuntimeError(
                "LLM returned empty content (thinking may still be on, or the "
                "API key/model rejected the request). Check NVIDIA_API_KEY and "
                "local_llm_think: false."
            )
