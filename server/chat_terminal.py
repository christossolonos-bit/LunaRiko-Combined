"""The conversation loop. This is the thing you run to talk to her.

One turn, start to finish:

  1. Wait for the streaming agent to hand over a payload. Today that fires
     when you finish speaking; the agent is built so other inputs (a camera,
     a chat feed, a timer) can fire it too without this file changing.
  2. Run the turn: LLM -> inline tags -> speech -> the avatar in the browser.
  3. Save the history, tell the agent she is done, listen again.

Run it with:  python -m server.chat_terminal

Needs, in this order:
  - the server + web page          (run.bat starts both)
  - a local LLM if llm_backend is local (Ollama on :11434)
  - a microphone                   (only if VOICE_SOURCE is "mic")
  - no cloud API keys when using local LLM + local Whisper + edge/sapi TTS
"""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import List

from server.config import (
    AGENT_BACKEND,
    BASE_REGISTRY,
    BROWSER_INPUT_URL,
    CHARACTER,
    ENABLE_FUNCTION_CALLING,
    ENABLE_LIVE_MEMORY,
    ENABLE_MEMORY,
    ENABLE_SESSION_GIST,
    HISTORY_FILE,
    LOCAL_OWNER_DISCORD_ID,
    LOCAL_OWNER_DISPLAY,
    LOCAL_OWNER_ROLE,
    LOCAL_OWNER_USERNAME,
    REGISTRY,
    TTS_BACKEND,
    VAD_SILENCE_DURATION,
    VOICE_SOURCE,
)
from server.llm.orchestrator import reload_emote_picker, run_turn
from server.playback.playback import OrderedPlaybackWorker
from server.streaming_agent.runtime import AgentRuntime


def _set_reply_audio(target: str) -> None:
    """Tell the FastAPI /talk route where this turn's TTS should play."""
    body = json.dumps({"target": target or "both"}).encode("utf-8")
    req = urllib.request.Request(
        f"{BROWSER_INPUT_URL.rstrip('/')}/set_reply_audio",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            resp.read()
    except Exception as e:
        print(f"[term] set_reply_audio failed: {type(e).__name__}: {e}")


def _discord_vc_active() -> bool:
    try:
        req = urllib.request.Request(
            f"{BROWSER_INPUT_URL.rstrip('/')}/discord_vc", method="GET"
        )
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return bool(data.get("active"))
    except Exception:
        return False


def _discord_music() -> dict:
    try:
        req = urllib.request.Request(
            f"{BROWSER_INPUT_URL.rstrip('/')}/discord_music", method="GET"
        )
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {"playing": False, "title": None}


def _wait_discord_tts_idle(timeout_s: float = 90.0) -> None:
    """Don't resume listen until Discord finished speaking this reply."""
    import time

    deadline = time.time() + max(1.0, timeout_s)
    saw_busy = False
    stable = 0
    while time.time() < deadline:
        try:
            req = urllib.request.Request(
                f"{BROWSER_INPUT_URL.rstrip('/')}/discord_tts_busy",
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            busy = bool(data.get("busy"))
            queued = int(data.get("queued") or 0)
        except Exception:
            busy, queued = False, 0

        if busy or queued > 0:
            saw_busy = True
            stable = 0
            time.sleep(0.25)
            continue

        # Need a short quiet stretch so inter-clip gaps don't look "done".
        stable += 1
        if saw_busy and stable >= 3:
            print("[term] Discord TTS idle — resuming listen")
            return
        if not saw_busy and stable >= 6:
            # No Discord clips ever reported busy (bridge down / not in VC).
            return
        time.sleep(0.25)
    print("[term] Discord TTS wait timed out — resuming listen anyway")


def _fetch_discord_overhear(limit: int = 40) -> str:
    """Recent VC lines she overheard (wake-gated — context only, not a prompt to reply)."""
    try:
        url = (
            f"{BROWSER_INPUT_URL.rstrip('/')}/discord_overhear"
            f"?limit={int(limit)}&clear=false"
        )
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[term] overhear fetch failed: {type(e).__name__}: {e}")
        return ""

    lines = data.get("lines") or []
    if not lines:
        return ""
    bullets = []
    for item in lines:
        who = item.get("display") or item.get("user_id") or "someone"
        uid = item.get("user_id")
        role = item.get("role") or ""
        text = (item.get("text") or "").strip()
        if not text:
            continue
        tag = f"<@{uid}>" if uid else who
        role_bit = f" ({role})" if role else ""
        bullets.append(f"- {who}{role_bit} {tag}: {text}")
    if not bullets:
        return ""
    return (
        "Recent Discord call you overheard while silent "
        "(context only — people were NOT necessarily talking to you; "
        "use this when the current speaker asks what was said / going on):\n"
        + "\n".join(bullets)
    )


def _fetch_live_memory_context(
    user_id: str | None = None,
    query: str | None = None,
) -> str:
    """Working + durable + profile + recent turn log from the API process."""
    if not ENABLE_LIVE_MEMORY:
        return ""
    try:
        from urllib.parse import urlencode
        params = {}
        if user_id:
            params["user_id"] = user_id
        if query:
            params["query"] = query
        q = f"?{urlencode(params)}" if params else ""
        url = f"{BROWSER_INPUT_URL.rstrip('/')}/live_memory/context{q}"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not data.get("enabled"):
            return ""
        return (data.get("context") or "").strip()
    except Exception as e:
        print(f"[term] live_memory context failed: {type(e).__name__}: {e}")
        return ""


def _label_local_as_owner(user_text: str) -> str:
    """Tag website/mic/typed lines as Solonaras (same Discord owner identity)."""
    text = (user_text or "").strip()
    if not text or not LOCAL_OWNER_DISCORD_ID:
        return text
    if text.lstrip().startswith("[Discord VC") or text.lstrip().startswith("[Discord voice"):
        return text
    who = "YOU (owner)" if LOCAL_OWNER_ROLE == "owner" else "other user"
    return (
        f"[Discord VC <@{LOCAL_OWNER_DISCORD_ID}> {LOCAL_OWNER_DISPLAY} "
        f"(@{LOCAL_OWNER_USERNAME}) role={LOCAL_OWNER_ROLE} {who}]: {text}"
    )


def _post_live_memory_turn(
    text: str,
    *,
    source: str,
    user_id: str | None = None,
    display: str | None = None,
    role: str | None = None,
    speaker: str | None = None,
) -> None:
    if not ENABLE_LIVE_MEMORY:
        return
    body = (text or "").strip()
    if not body:
        return
    try:
        payload = json.dumps({
            "text": body[:2000],
            "source": source,
            "user_id": user_id,
            "display": display,
            "role": role,
            "speaker": speaker,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{BROWSER_INPUT_URL.rstrip('/')}/live_memory/turn",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            resp.read()
    except Exception as e:
        print(f"[term] live_memory turn failed: {type(e).__name__}: {e}")



def configure_logging() -> None:
    """One logging setup, so the agent's lines interleave cleanly with print()."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def load_history() -> List[dict]:
    if not HISTORY_FILE.exists():
        print(f"[term] no history at {HISTORY_FILE}, starting fresh")
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            history = json.load(f)
        print(f"[term] loaded {len(history)} message(s) from {HISTORY_FILE}")
        return history
    except (json.JSONDecodeError, OSError) as e:
        print(f"[term] WARN couldn't read history ({e}); starting fresh")
        return []


def save_history(history: List[dict]) -> None:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


def _handle_slash_command(text: str) -> bool:
    """Return True if `text` was a command we already handled."""
    if text == "/reload":
        pool_size = reload_emote_picker()
        print(f"[term] emote picker reloaded — pool size: {pool_size}\n")
        return True
    return False


def _build_actions_agent() -> None:
    """Wire whatever handles her `do_task` tool. No-op when tools are off.

    `do_task` is the ONE tool she sees. She calls it with a plain-English
    instruction and a second, smaller agent decides which real tool to run.
    That way adding tools never means rewriting her prompt.
    """
    from server.function_calling.agent_backend import set_actions_agent

    if AGENT_BACKEND != "real":
        from server.function_calling.dummy_agent import DummyActionsAgent
        set_actions_agent(DummyActionsAgent())
        print("[term] tools ON — do_task -> DummyActionsAgent (canned, no API key)")
        return

    from server.agent import Agent, ToolRegistry
    from server.agent.llm_groq import GroqLLMClient
    from server.function_calling.agent_backend import AgentRunnerBackend

    if REGISTRY == "sample":
        from server.agent_registries.sample_registry import build_sample_registry
        registry = build_sample_registry()
    else:
        registry = ToolRegistry()

    if BASE_REGISTRY:
        from server.agent_registries.base_registry import build_base_registry
        base_tools = build_base_registry().all()
        for tool in base_tools:
            registry.register(tool)
        print(f"[term] base tools merged: {[t.name for t in base_tools]}")

    if not registry.all():
        print(f"[term] WARN the actions agent has NO tools — REGISTRY={REGISTRY!r} "
              f"with BASE_REGISTRY={BASE_REGISTRY}. do_task has nothing to run.")

    set_actions_agent(AgentRunnerBackend(
        Agent(registry=registry, llm_client=GroqLLMClient(), name="riko-actions")
    ))
    print(f"[term] tools ON — do_task -> real agent + {REGISTRY!r} registry"
          f"{' + base' if BASE_REGISTRY else ''}")


def main() -> None:
    configure_logging()
    history = load_history()

    # Memory is off by default (server/config.py). With both switches off,
    # `sess` stays None and nothing below imports the memory package at all —
    # the loop is byte-identical to a build without it. When on, memory only
    # ever ADDS text to this turn's injected context; it never touches the
    # turn itself or the tool path.
    sess = None
    if ENABLE_MEMORY or ENABLE_SESSION_GIST:
        from server.memory.session import MemorySession
        sess = MemorySession(enable_memory=ENABLE_MEMORY, enable_gist=ENABLE_SESSION_GIST)
        sess.history = history          # seed the rolling window from disk
        print(f"[term] memory ON — long_term={ENABLE_MEMORY} gist={ENABLE_SESSION_GIST}")

    if ENABLE_FUNCTION_CALLING:
        _build_actions_agent()

    worker = OrderedPlaybackWorker()
    worker.start()

    agent = AgentRuntime(
        browser_input_url=BROWSER_INPUT_URL,
        # Desktop mic uses local Whisper (ASR_BACKEND). Browser stays on so the
        # page text box still works; browser mic uploads are optional.
        enable_voice=(VOICE_SOURCE in ("mic", "both")),
        enable_browser_voice=(VOICE_SOURCE in ("browser", "both"))
            or VOICE_SOURCE == "mic",  # mic mode: keep typed messages working
        silence_duration=VAD_SILENCE_DURATION,
    )
    agent.start()

    name = CHARACTER.capitalize()
    tools_label = (
        f"ON ({AGENT_BACKEND}/{REGISTRY}{'+base' if BASE_REGISTRY else ''})"
        if ENABLE_FUNCTION_CALLING else "OFF"
    )
    mem_label = (
        f"ON (long_term={ENABLE_MEMORY}, gist={ENABLE_SESSION_GIST}, "
        f"live={ENABLE_LIVE_MEMORY})"
        if (sess is not None or ENABLE_LIVE_MEMORY) else "OFF"
    )
    print("\n" + "=" * 60)
    print(f"  {name} — talk to her. Ctrl-C to quit.")
    print(f"  Voice in: {VOICE_SOURCE}   Voice out: {TTS_BACKEND}"
          f"   Silence: {VAD_SILENCE_DURATION}s")
    print(f"  Tools: {tools_label}   Memory: {mem_label}")
    print("=" * 60 + "\n")

    try:
        while True:
            # 1) Block until the agent decides it is her turn to speak.
            print("[term] listening...")
            payload = agent.next_payload()
            print(f"[term] trigger={payload['trigger_reason']!r}")
            print(f"[term] you said: {payload['user_voice']!r}")
            image_path = payload.get("image_path", "")
            if image_path:
                print(f"[term] image attached: {image_path}")
            if payload["system_context"]:
                print(f"[term] system_context:\n{payload['system_context']}")

            user_text = payload["user_voice"].strip()
            if not user_text:
                print("[term] empty speech — skipping")
                agent.end_response()
                continue
            # Real Discord-bridge turns already carry the prefix. Local mic/text
            # get an owner label afterward — that must NOT force Discord-only TTS.
            from_discord_bridge = (
                user_text.lstrip().startswith("[Discord voice")
                or user_text.lstrip().startswith("[Discord VC")
            )
            labeled = _label_local_as_owner(user_text)
            if labeled != user_text:
                print(f"[term] local→owner label: {LOCAL_OWNER_DISPLAY} <@{LOCAL_OWNER_DISCORD_ID}>")
                user_text = labeled

            music = _discord_music()
            if music.get("playing") and from_discord_bridge:
                title = music.get("title") or "a song"
                print(f"[term] music playing ({title!r}) — Discord stay quiet")
                agent.end_response()
                continue

            # Discord-bridge turns → Discord speaker only.
            # Browser / local mic (even after owner label) → webpage hears too.
            reply_audio = (payload.get("reply_audio") or "both").strip().lower()
            if from_discord_bridge:
                reply_audio = "discord"
            elif reply_audio not in ("discord", "browser", "both"):
                reply_audio = "both"
            _set_reply_audio(reply_audio)
            if reply_audio == "discord":
                print("[term] reply audio → Discord VC only (webpage silent)")
            else:
                print(f"[term] reply audio → {reply_audio}")

            if _handle_slash_command(user_text):
                _set_reply_audio("both")
                agent.end_response()
                continue

            if sess is not None and user_text == "/extract":
                print(f"[term] /extract -> {sess.extract()}")
                _set_reply_audio("both")
                agent.end_response()
                continue

            # 2) Build this turn's system-prompt injection. With memory off
            #    this is exactly what the agent handed over. With memory on,
            #    the relevant memories and the session summary are appended.
            # Discord: scope facts/history to the CURRENT speaker tag only.
            from server.memory.discord_speaker import (
                parse_discord_speaker,
                recent_facts_about_speaker,
                scope_history_for_speaker,
                speaker_context_block,
            )
            speaker = parse_discord_speaker(user_text)

            if sess is not None:
                mem_gist, hist_for_turn = sess.retrieve_context(user_text)
                injected = "\n\n".join(p for p in (payload["system_context"], mem_gist) if p)
            else:
                hist_for_turn = history
                injected = payload["system_context"]
                if speaker:
                    hist_for_turn = scope_history_for_speaker(hist_for_turn, speaker)
                    bits = [speaker_context_block(speaker)]
                    recent = recent_facts_about_speaker(history, speaker)
                    if recent:
                        bits.append(recent)
                    injected = "\n\n".join(p for p in (injected, *bits) if p)

            # Live memory is primary recall (profiles + turn log). FAISS is off.
            if ENABLE_LIVE_MEMORY:
                from server.memory.discord_speaker import strip_discord_prefix
                spoken_for_recall = (
                    strip_discord_prefix(user_text)
                    if (speaker or user_text.lstrip().startswith("[Discord VC"))
                    else user_text
                )
                live_ctx = _fetch_live_memory_context(
                    speaker.subject_id if speaker else None,
                    query=spoken_for_recall,
                )
                if live_ctx:
                    injected = "\n\n".join(p for p in (injected, live_ctx) if p)
                    print("[term] injected live memory (profile/turn-log)")
                elif speaker or user_text.lstrip().startswith("[Discord VC"):
                    overhear = _fetch_discord_overhear()
                    if overhear:
                        injected = "\n\n".join(p for p in (injected, overhear) if p)
                        print(
                            f"[term] injected Discord overhear "
                            f"({overhear.count(chr(10))+1} lines)"
                        )
                if speaker:
                    is_discord = user_text.lstrip().startswith("[Discord VC")
                    _post_live_memory_turn(
                        spoken_for_recall or user_text,
                        source="wake" if is_discord else "local",
                        user_id=speaker.subject_id,
                        display=speaker.display,
                        role=speaker.role,
                        speaker=speaker.display,
                    )

            # LLaVA: caption + photo and/or live camera before the chat model runs.
            from server.llm.vision_inject import resolve_vision_for_turn
            user_text, image_path_for_llm, injected = resolve_vision_for_turn(
                user_text, image_path, injected,
            )

            # 3) One full turn. Wrapped so a single bad chunk (say, SoVITS
            #    rejecting odd punctuation) costs that turn, not the session.
            try:
                updated = run_turn(
                    user_text,
                    hist_for_turn,
                    worker,
                    injected_context=injected,
                    image_path=image_path_for_llm,
                )
                if sess is not None:
                    # sess owns the canonical window. Hand it the turn plus any
                    # tool-call items run_turn slipped between the user and
                    # assistant messages, so those persist too.
                    turn_tail = updated[len(hist_for_turn):]
                    tool_items = [m for m in turn_tail if "type" in m]
                    recorded_user = user_text
                    if image_path:
                        from server.llm.image_attach import history_text
                        recorded_user = history_text(user_text)
                    sess.record(recorded_user, updated[-1]["content"], tool_items=tool_items)
                    history = sess.history_snapshot()
                else:
                    history = updated
                save_history(history)
                print(f"[term] history saved ({len(history)} messages)\n")
                # Luna's spoken reply → permanent live turn log.
                if ENABLE_LIVE_MEMORY:
                    try:
                        assistant_text = updated[-1].get("content") or ""
                    except Exception:
                        assistant_text = ""
                    if assistant_text:
                        _post_live_memory_turn(
                            assistant_text,
                            source="reply",
                            speaker="Luna",
                        )
                    # Local (non-Discord) user turns also feed the log.
                    if not speaker:
                        _post_live_memory_turn(
                            user_text,
                            source="local",
                            user_id="local",
                            display="local",
                            role="local",
                            speaker="local",
                        )
            except Exception as e:
                print(f"[term] ERROR during run_turn: {type(e).__name__}: {e}")
            finally:
                # Reset routing after each turn; next Discord wake sets discord again.
                _set_reply_audio("both")

            # Discord VC: wait until the bridge finished playing every clip.
            # Otherwise listen resumes mid-reply and the next wake cuts her off.
            if from_discord_bridge or reply_audio == "discord":
                _wait_discord_tts_idle()

            # 4) Resume listening. Always called, even if the turn failed.
            agent.end_response()

    except KeyboardInterrupt:
        print("\n[term] Ctrl-C — exiting")
    finally:
        agent.stop()
        worker.stop()
        if sess is not None:
            try:
                print(f"[term] session end — writing memories: {sess.extract()}")
            except Exception as e:
                print(f"[term] memory extraction failed (non-fatal): "
                      f"{type(e).__name__}: {e}")
            history = sess.history_snapshot()
        save_history(history)
        print(f"[term] final save: {len(history)} message(s) -> {HISTORY_FILE}")


if __name__ == "__main__":
    main()
