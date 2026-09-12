"""llm_groq.py — Groq-backed LLMClient.

Uses the official `groq` SDK, model `openai/gpt-oss-20b`, the Chat Completions
API. We use AsyncGroq — the async variant of the same SDK — because the whole
agent loop is async.

Tool selection is done with Chat Completions function calling: the model is
given the registry's tool schemas and either returns a tool call (parsed into
a ToolChoice) or plain text (returned as a natural-language reply).

GROQ_API_KEY is read from the environment, loaded from .env via python-dotenv.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

from dotenv import load_dotenv
from groq import AsyncGroq, RateLimitError

from .llm import LLMResponse, ToolChoice

log = logging.getLogger(__name__)

# --- Request settings ---------------------------------------------------
# Tuned for tool routing, which is a small classification task — not an
# open-ended generation. These settings keep token usage (and so Groq
# rate-limit pressure) low.
# Was qwen/qwen3-32b — Groq decommissioned it (now 404 model_not_found), which
# silently broke every do_task tool routing. gpt-oss keeps the same Groq endpoint
# + Chat Completions function calling; only the model id + reasoning knob change.
MODEL = "openai/gpt-oss-20b"
TEMPERATURE = 0.6
TOP_P = 0.95
# A routed tool call is tiny — it does not need a large response budget.
MAX_COMPLETION_TOKENS = 1024
# Routing is a classification task, not open-ended generation — we want the LEAST
# reasoning. gpt-oss is a reasoning-native model: it takes "low"/"medium"/"high"
# (NOT "none", which was qwen's off-switch). "low" keeps hidden reasoning tokens
# (and so latency + rate-limit pressure) minimal while staying valid for gpt-oss.
REASONING_EFFORT = "low"
# Retries on a 429 / 5xx. Kept at 0 so a rate-limited call fails fast with a
# clear message instead of silently freezing the agent for the retry-after
# window (Groq can set that to 30s+). Raise it to let the SDK auto-wait.
MAX_RETRIES = 0

SYSTEM_PROMPT = """You are the action router for an AI assistant.

You receive one natural-language request. Your only job is to choose the single
tool that performs it and fill in its arguments from the request.

Rules:
- Call exactly one tool when the request maps to one. Always make the tool
  call through the function-calling mechanism, never as text in your reply.
- Fill in arguments that are stated or clearly implied by the request. Omit
  optional arguments the user did not mention — tools have sensible defaults.
- If the request asks about something the user started earlier (is it done,
  what's the status, cancel it), use the check_task or cancel_task tool.
- If no tool fits, do NOT call a tool. Reply with one short plain sentence
  saying you can't do that.
"""

# qwen reasoning models may wrap chain-of-thought in <think>...</think>.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _strip_reasoning(text: str) -> str:
    return _THINK_RE.sub("", text).strip()


def _recover_tool_call(text: str, tools: list[dict[str, Any]]) -> Optional[ToolChoice]:
    """Recover a tool call a model wrote as JSON in its reply text instead of
    via the function-calling mechanism. Returns None if `text` is not a tool
    call for a known tool."""
    text = text.strip()
    if not (text.startswith("{") and text.endswith("}")):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    name = data.get("name") or data.get("tool") or data.get("function")
    known = {t["function"]["name"] for t in tools}
    if name not in known:
        return None
    args = data.get("arguments") or data.get("parameters") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}
    return ToolChoice(tool_name=name, arguments=args if isinstance(args, dict) else {})


class GroqLLMClient:
    """LLMClient backed by Groq Chat Completions with function calling."""

    def __init__(self, model: str = MODEL,
                 reasoning_effort: Optional[str] = REASONING_EFFORT) -> None:
        load_dotenv()
        if not os.getenv("GROQ_API_KEY"):
            raise RuntimeError(
                "GROQ_API_KEY not found — add it to .env. Get a free key at "
                "https://console.groq.com/keys"
            )
        self.model = model
        # reasoning_effort is only valid for reasoning models (e.g. qwen3);
        # pass None for plain instruct models such as the llama family.
        self.reasoning_effort = reasoning_effort
        self._client = AsyncGroq(max_retries=MAX_RETRIES)  # GROQ_API_KEY from env
        log.info("GroqLLMClient ready (model=%s, reasoning_effort=%s)",
                 model, reasoning_effort)

    async def choose_tool(
        self,
        task: str,
        context: Optional[str],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        user_content = task if not context else f"{task}\n\nContext: {context}"
        log.info("asking Groq to route prompt: %r", task)
        try:
            params = dict(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                tools=tools,
                tool_choice="auto",
                temperature=TEMPERATURE,
                top_p=TOP_P,
                max_completion_tokens=MAX_COMPLETION_TOKENS,
                stream=False,
            )
            if self.reasoning_effort is not None:
                params["reasoning_effort"] = self.reasoning_effort
            completion = await self._client.chat.completions.create(**params)
        except RateLimitError as e:
            log.warning("Groq rate limit hit (429): %s", e)
            return LLMResponse(message=(
                "The language model is rate-limited right now — "
                "give it a few seconds and try again."
            ))
        except Exception as e:  # network / API / auth — agent must not crash
            log.error("Groq request failed: %s", e)
            return LLMResponse(message=f"I couldn't reach the language model ({e}).")

        message = completion.choices[0].message
        tool_calls = getattr(message, "tool_calls", None)

        if not tool_calls:
            text = _strip_reasoning(message.content or "")
            recovered = _recover_tool_call(text, tools)
            if recovered is not None:
                log.info("recovered a tool call from reply text: tool=%s args=%s",
                         recovered.tool_name, recovered.arguments)
                return LLMResponse(tool_choice=recovered)
            log.info("Groq returned no tool call; content=%r", text)
            return LLMResponse(message=text or "I'm not sure how to do that.")

        call = tool_calls[0]
        name = call.function.name
        raw_args = call.function.arguments or "{}"
        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError as e:
            log.error("Groq tool arguments were not valid JSON: %s (%r)", e, raw_args)
            return LLMResponse(
                message="I understood the request but couldn't read the details."
            )
        if not isinstance(args, dict):
            log.warning("Groq tool arguments were not an object: %r", args)
            args = {}

        log.info("Groq chose tool=%s args=%s", name, args)
        return LLMResponse(tool_choice=ToolChoice(tool_name=name, arguments=args))
