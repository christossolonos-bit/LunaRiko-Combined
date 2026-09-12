#!/usr/bin/env python3
"""Checks every part of the system and tells you exactly what to fix.

Run it any time something is not working:

    check_setup.bat                 run every test
    check_setup.bat --quick         skip the tests that need the servers running
    check_setup.bat --from 5        start at test 5

Each test says PASS or FAIL. A failure prints what to do about it, then the
script stops — there is no point testing the voice if the API key is missing.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
import wave
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

# cmd.exe starts in codepage 437 and cannot encode the arrows and dashes this
# script prints — Python raises UnicodeEncodeError instead of dropping them.
# server/config.py does the same for the rest of the project; this file runs
# before that import. check_setup.bat also does `chcp 65001`.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# 127.0.0.1, not localhost — on Windows the name resolves to IPv6 first and
# costs ~2 seconds per request. See the check inside test 9.
PAGE_URL = "http://127.0.0.1:5173"


def server_url() -> str:
    """The server's address, straight from server/config.py.

    A function rather than a module constant because importing config runs the
    character-config loader, and test 1 must be able to fail with a friendly
    message before that happens.
    """
    from server.config import VRM_BASE_URL
    return VRM_BASE_URL


# ---------------------------------------------------------------------------
#  Pretty printing. Windows terminals do ANSI colour since Windows 10, but
#  a redirected pipe does not — so colour is dropped when we are not a tty.
# ---------------------------------------------------------------------------
_TTY = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _TTY else text


def header(n: int, title: str) -> None:
    print()
    print(_c("1;36", "=" * 66))
    print(_c("1;36", f"  TEST {n}: {title}"))
    print(_c("1;36", "=" * 66))


def ok(msg: str) -> None:
    print(_c("32", f"  PASS  {msg}"))


def warn(msg: str) -> None:
    print(_c("33", f"  NOTE  {msg}"))


def info(msg: str) -> None:
    print(f"        {msg}")


def fail(msg: str, *fix: str) -> None:
    """Print the failure plus what to do, then stop."""
    print(_c("1;31", f"  FAIL  {msg}"))
    if fix:
        print()
        print(_c("1;33", "  HOW TO FIX IT"))
        for line in fix:
            print(f"    {line}")
    print()
    print(_c("1;31", "  Stopping here. Fix the above, then run check_setup.bat again."))
    sys.exit(1)


def ask(prompt: str) -> bool:
    """Yes/no question. Anything but 'n' counts as yes, so Enter means yes."""
    try:
        answer = input(_c("1", f"  {prompt} [Y/n] ")).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)
    return not answer.startswith("n")


# ===========================================================================
#  1. Files and folders
# ===========================================================================
def test_files() -> None:
    header(1, "Files and folders")

    required = {
        "character_config.yaml": "Her personality and voice settings.",
        "requirements.txt": "The Python package list.",
        "server/config.py": "Every runtime switch.",
        "client/index.html": "The web page.",
        "models/all-MiniLM-L6-v2/model.safetensors":
            "The model that matches her <expr:> and <emote:> tags.",
    }
    for rel, what in required.items():
        if not (PROJECT_ROOT / rel).exists():
            fail(f"missing: {rel}  ({what})",
                 "The package did not unzip completely.",
                 "Unzip it again, and make sure the antivirus did not quarantine anything.")
        ok(f"{rel}")

    vrm_dir = PROJECT_ROOT / "client" / "characters" / "riko" / "models"
    vrms = sorted(vrm_dir.glob("*.vrm"))
    if not vrms:
        fail(f"no .vrm file in {vrm_dir}",
             "That folder holds the 3D model. Drop a VRM 1.0 file in it,",
             "then set `vrm_path` in client/config.js to its filename.")
    ok(f"3D model: {vrms[0].name}  ({vrms[0].stat().st_size // (1024*1024)} MB)")

    # Check the packages, not the .venv folder — that is what actually
    # matters, and it also catches a half-finished install.
    missing = []
    for mod, pkg in [("yaml", "PyYAML"), ("dotenv", "python-dotenv"),
                     ("openai", "openai"), ("groq", "groq"),
                     ("fastapi", "fastapi"), ("sounddevice", "sounddevice"),
                     ("soundfile", "soundfile"),
                     ("sentence_transformers", "sentence-transformers")]:
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        fail(f"Python packages not installed: {', '.join(missing)}",
             "Run setup.bat.",
             "If you already did, it did not finish — run it again and read",
             "the error it prints.")
    ok("Python packages installed")


# ===========================================================================
#  2. API keys
# ===========================================================================
def test_keys() -> None:
    header(2, "API keys")

    from dotenv import load_dotenv

    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        fail("no .env file",
             "Copy .env.example to .env, then paste your keys into it.",
             "In this folder:   copy .env.example .env")
    load_dotenv(env_file)
    ok(".env found")

    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    from server.config import (
        ASR_BACKEND, TTS_BACKEND, ENABLE_FUNCTION_CALLING, AGENT_BACKEND,
        LLM_BACKEND,
    )

    if LLM_BACKEND == "openai":
        if not openai_key:
            fail("OPENAI_API_KEY is empty",
                 "This one is required when llm_backend: openai.",
                 "1. Get a key at https://platform.openai.com/api-keys",
                 "2. Add about $5 of credit to the account.",
                 '3. Put it in .env as:   OPENAI_API_KEY="sk-..."',
                 "Or switch to a local model:",
                 "  set  llm_backend: local  in character_config.yaml")
        if not openai_key.startswith("sk-"):
            warn("OPENAI_API_KEY does not start with 'sk-' — double-check you copied all of it")
        else:
            ok(f"OPENAI_API_KEY  ({openai_key[:8]}...)")
    else:
        info("OPENAI_API_KEY not required — llm_backend: local")

    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    needs_groq = (
        ASR_BACKEND == "groq"
        or (ENABLE_FUNCTION_CALLING and AGENT_BACKEND == "real")
    )
    if needs_groq and not groq_key:
        why = []
        if ASR_BACKEND == "groq":
            why.append("ASR_BACKEND=groq (speech-to-text)")
        if ENABLE_FUNCTION_CALLING and AGENT_BACKEND == "real":
            why.append("tool-picking agent (AGENT_BACKEND=real)")
        fail("GROQ_API_KEY is empty, but something still needs it",
             "Needed for: " + "; ".join(why),
             "It is FREE.",
             "1. Get a key at https://console.groq.com/keys",
             '2. Put it in .env as:   GROQ_API_KEY="gsk_..."',
             "Or stay fully local:",
             "  asr_backend: local   in character_config.yaml",
             "  and set AGENT_BACKEND = \"dummy\" in server/config.py")
    if groq_key:
        ok(f"GROQ_API_KEY  ({groq_key[:8]}...)")
    else:
        info("GROQ_API_KEY not set — fine with local Whisper + dummy agent")

    fish_key = os.getenv("FISH_API_KEY", "").strip()
    if TTS_BACKEND == "fish" and not fish_key:
        fail("FISH_API_KEY is empty, but character_config.yaml says tts_backend: fish",
             "Either add the key:",
             "  1. Get one at https://fish.audio",
             '  2. Put it in .env as:   FISH_API_KEY="..."',
             "Or switch voice:",
             "  set  tts_backend: edge    (local edge-tts, no key)",
             "  or   tts_backend: sovits  in character_config.yaml")
    if fish_key:
        ok(f"FISH_API_KEY  ({fish_key[:8]}...)")
    elif TTS_BACKEND == "edge":
        info("FISH_API_KEY not set — fine, you are using Edge TTS")
    else:
        info("FISH_API_KEY not set — fine, you are using the local SoVITS voice")


# ===========================================================================
#  3. Character config
# ===========================================================================
def test_config() -> None:
    header(3, "Character config")

    from server import config

    ok(f"character         : {config.CHARACTER}")
    ok(f"model             : {config.MODEL}")
    ok(f"voice             : {config.TTS_BACKEND}")
    ok(f"microphone source : {config.VOICE_SOURCE}")
    ok(f"tools             : {'on' if config.ENABLE_FUNCTION_CALLING else 'off'}")
    ok(f"memory            : {'on' if config.ENABLE_MEMORY else 'off'}")

    prompt = config.SYSTEM_PROMPT.strip()
    if len(prompt) < 40:
        fail("the system prompt in character_config.yaml is nearly empty",
             "That is her whole personality. Write a few sentences under",
             "`system_prompt:` in character_config.yaml.")
    if "<expr:" not in prompt and "<emote:" not in prompt:
        warn("the prompt never mentions <expr:> or <emote:> tags")
        info("She will still talk, but her face and body will not move.")
        info("Copy the INLINE TAGS section back in from the shipped prompt.")
    else:
        ok("prompt explains the inline tags")

    if config.TTS_BACKEND == "sovits":
        ref = Path(config.SOVITS_PING_CONFIG["ref_audio_path"])
        if not ref.exists():
            fail(f"the SoVITS reference clip is missing: {ref}",
                 "That is the voice she is cloned from.",
                 "Put a 3-10 second WAV in client/characters/riko/voice/ and",
                 "point `ref_audio_path` in character_config.yaml at it.")
        with wave.open(str(ref)) as w:
            secs = w.getnframes() / w.getframerate()
        ok(f"reference clip: {ref.name}  ({secs:.1f}s)")
        if not 2.0 <= secs <= 12.0:
            warn(f"that clip is {secs:.1f}s — SoVITS wants 3 to 10 seconds")


# ===========================================================================
#  4. Tag matching (the model that makes her face and body move)
# ===========================================================================
def test_tag_matching() -> None:
    header(4, "Expression and animation matching")
    info("Loading the matching model — first run takes a few seconds...")

    from server.llm.pickers import build_emote_picker, build_expression_picker

    try:
        expr = build_expression_picker()
    except Exception as e:
        fail(f"could not load the matching model ({type(e).__name__}: {e})",
             "It lives in models/all-MiniLM-L6-v2/.",
             "If that folder is missing or half-copied, unzip the package again.")

    name, score = expr.pick("furious")
    if name != "angry":
        warn(f"<expr:furious> matched '{name}', expected 'angry' (score {score:.2f})")
    else:
        ok(f"<expr:furious> -> {name}  (score {score:.2f})")

    emote, name_to_url = build_emote_picker()
    if not name_to_url:
        fail("no animations found",
             "They should be in client/animations/mixamo/ and client/animations/vrma/.",
             "If those folders are empty, unzip the package again.")
    ok(f"{len(name_to_url)} animations available")
    picked, score = emote.pick("wave hello")
    ok(f"<emote:wave hello> -> {picked}  (score {score:.2f})")


# ===========================================================================
#  5. The language model
# ===========================================================================
def test_llm() -> None:
    from server.config import LLM_BACKEND, MODEL

    if LLM_BACKEND == "local":
        header(5, "The language model (local)")
        from server.config import LOCAL_LLM_BASE_URL, LOCAL_LLM_THINK
        from server.llm.llm_backend import LocalLLMBackend

        info(f"Asking local {MODEL} at {LOCAL_LLM_BASE_URL} "
             f"(think={LOCAL_LLM_THINK})...")
        messages = [
            {"role": "system", "content": "You are a test. Reply with exactly: hello world"},
            {"role": "user", "content": "say the words"},
        ]
        try:
            reply = "".join(LocalLLMBackend(model=MODEL).stream(messages))
        except Exception as e:
            fail(f"local LLM failed ({type(e).__name__}: {e})",
                 "Is Ollama running?  ollama serve",
                 f"Is the model pulled?  ollama pull {MODEL}",
                 "Check local_llm_base_url in character_config.yaml")
        if not reply.strip():
            fail("local LLM replied with nothing",
                 "If this is a Qwen3 thinking model, set local_llm_think: false",
                 "and local_llm_api_style: ollama")
        ok(f"replied: {reply.strip()[:60]!r}")
        return

    header(5, "The language model (OpenAI)")

    from server.llm.llm_backend import OpenAIBackend

    info(f"Asking {MODEL} for one short line...")
    messages = [
        {"role": "system", "content": "You are a test. Reply with exactly: hello world"},
        {"role": "user", "content": "say the words"},
    ]
    try:
        reply = "".join(OpenAIBackend(model=MODEL).stream(messages))
    except Exception as e:
        msg = str(e)
        if "authentication" in msg.lower() or "401" in msg or "invalid_api_key" in msg:
            fail("OpenAI rejected the key",
                 "The key in .env is wrong, or was revoked.",
                 "Make a new one at https://platform.openai.com/api-keys")
        if "quota" in msg.lower() or "insufficient" in msg.lower() or "429" in msg:
            fail("OpenAI says the account is out of credit",
                 "Add credit at https://platform.openai.com/settings/organization/billing")
        if "model" in msg.lower() and "not" in msg.lower():
            fail(f"the model '{MODEL}' is not available on this account",
                 "Change `model:` in character_config.yaml to gpt-4.1-mini")
        fail(f"could not reach OpenAI ({type(e).__name__}: {e})",
             "Check your internet connection and any firewall or VPN.")

    if not reply.strip():
        fail("OpenAI replied with nothing",
             "Try again — if it keeps happening, change `model:` in",
             "character_config.yaml to gpt-4.1-mini")
    ok(f"replied: {reply.strip()[:60]!r}")


# ===========================================================================
#  6. The microphone
# ===========================================================================
def test_microphone() -> None:
    header(6, "Microphone")

    from server.config import VOICE_SOURCE
    if VOICE_SOURCE != "mic":
        info(f"VOICE_SOURCE is '{VOICE_SOURCE}', not 'mic' — skipping.")
        info("You will talk to her using the box on the web page instead.")
        return

    import sounddevice as sd

    try:
        devices = sd.query_devices()
    except Exception as e:
        fail(f"could not list audio devices ({type(e).__name__}: {e})",
             "Windows may be blocking microphone access.",
             "Settings > Privacy & security > Microphone > allow desktop apps.")

    inputs = [(i, d) for i, d in enumerate(devices) if d["max_input_channels"] > 0]
    if not inputs:
        fail("no microphone found",
             "Plug one in, or check Settings > Privacy & security > Microphone.")
    default_in = sd.default.device[0]
    for i, d in inputs:
        mark = "  <- default" if i == default_in else ""
        info(f"[{i}] {d['name']}{mark}")
    ok(f"{len(inputs)} input device(s)")

    if not ask("Record 3 seconds from the default mic and play it back?"):
        info("Skipped.")
        return

    from server.asr.recorder import record_on_speech
    from server.asr.transcribe import transcribe_audio
    from server.config import ASR_BACKEND

    out = str(PROJECT_ROOT / "audio" / "mic_test.wav")
    (PROJECT_ROOT / "audio").mkdir(exist_ok=True)
    print()
    print(_c("1;33", "  SPEAK NOW — say a whole sentence, then stop."))
    record_on_speech(output_file=out, silence_duration=1.0)

    with wave.open(out) as w:
        secs = w.getnframes() / w.getframerate()
    if secs < 0.3:
        fail(f"only recorded {secs:.2f}s",
             "The microphone is not picking you up. Try:",
             "  - raise the input volume in Windows sound settings",
             "  - pick a different mic (set `device` in server/asr/recorder.py)",
             "  - lower VAD_SPEECH_THRESHOLD in server/config.py to 0.3")
    ok(f"recorded {secs:.1f}s -> {out}")

    info(f"Transcribing with ASR_BACKEND={ASR_BACKEND!r}...")
    try:
        text = transcribe_audio(aud_path=out)
    except Exception as e:
        if ASR_BACKEND == "groq" and ("401" in str(e) or "invalid" in str(e).lower()):
            fail("Groq rejected the key",
                 "Make a new free one at https://console.groq.com/keys")
        fail(f"transcription failed ({type(e).__name__}: {e})",
             "For local Whisper: first run downloads the model — needs internet once.",
             "For Groq: check your internet connection and GROQ_API_KEY.")

    print()
    print(_c("1;36", f'  It heard: "{text.strip()}"'))
    print()
    if not text.strip():
        fail("it heard nothing",
             "The clip was recorded but had no speech in it.",
             "Check that the right microphone is the Windows default.")
    if not ask("Is that roughly what you said?"):
        fail("transcription is wrong",
             "Usually the wrong microphone is selected, or it is too quiet.",
             "  - set the right mic as default in Windows sound settings",
             "  - move closer, or raise the input volume")
    ok("microphone and transcription both work")


# ===========================================================================
#  7. Voice generation
# ===========================================================================
def test_tts() -> None:
    header(7, "Her voice")

    from server.config import TTS_BACKEND

    if TTS_BACKEND == "sovits":
        import requests
        from server.config import SOVITS_URL
        info(f"Checking GPT-SoVITS at {SOVITS_URL} ...")
        try:
            requests.get(SOVITS_URL.replace("/tts", "/"), timeout=3)
        except requests.exceptions.ConnectionError:
            fail("GPT-SoVITS is not running",
                 "It is a separate program that makes her voice.",
                 "  1. Install it: https://github.com/RVC-Boss/GPT-SoVITS",
                 "  2. Put its folder path in SOVITS_PATH at the top of start_servers.bat",
                 "  3. Run start_servers.bat again — a GPT-SoVITS window should open",
                 "",
                 "Or skip it and use Edge TTS instead:",
                 "  set  tts_backend: edge  in character_config.yaml")
        except Exception:
            pass  # any HTTP answer at all means something is listening
        ok("GPT-SoVITS is listening")
        from server.tts.sovits import synthesize
    elif TTS_BACKEND == "edge":
        ok("using Edge TTS (edge-tts — no API key)")
        try:
            import edge_tts  # noqa: F401
        except ImportError:
            fail("edge-tts is not installed",
                 "Run:  pip install edge-tts",
                 "Or re-run setup.bat so requirements.txt is installed.")
        from server.tts.edge import synthesize
    elif TTS_BACKEND == "sapi":
        ok("using Windows SAPI (pyttsx3 — offline)")
        try:
            import pyttsx3  # noqa: F401
        except ImportError:
            fail("pyttsx3 is not installed",
                 "Run:  pip install pyttsx3",
                 "Or re-run setup.bat so requirements.txt is installed.")
        from server.tts.sapi import synthesize
    else:
        ok("using Fish Audio (online — no local install needed)")
        from server.tts.fish import synthesize

    info("Generating a test line...")
    try:
        rel, dur = synthesize("Hello. If you can hear this, my voice is working.")
    except Exception as e:
        if TTS_BACKEND == "fish":
            fail(f"Fish Audio failed ({type(e).__name__}: {e})",
                 "  - check FISH_API_KEY in .env",
                 "  - check `fish_reference_id` in character_config.yaml is a real voice id",
                 "  - check your Fish account has credit")
        if TTS_BACKEND == "edge":
            fail(f"Edge TTS failed ({type(e).__name__}: {e})",
                 "  - check your internet connection",
                 "  - check `edge_tts_voice` in character_config.yaml",
                 "  - pip show edge-tts")
        if TTS_BACKEND == "sapi":
            fail(f"Windows SAPI failed ({type(e).__name__}: {e})",
                 "  - install a Windows speech voice (Zira)",
                 "  - pip show pyttsx3")
        fail(f"GPT-SoVITS failed ({type(e).__name__}: {e})",
             "Look at the GPT-SoVITS window for the real error. Usually:",
             "  - the reference clip path is wrong (character_config.yaml)",
             "  - the clip is too long or too short (3-10 seconds is right)",
             "  - `text_lang` does not match the language you are writing in")

    wav = PROJECT_ROOT / "client" / rel
    if not wav.exists() or dur < 0.3:
        fail(f"the generated clip is empty or missing ({dur:.2f}s)",
             "The voice engine answered but produced no audio.",
             "Try a different reference clip.")
    ok(f"generated {dur:.1f}s of speech -> client/{rel}")
    info(f"Play it yourself to check it sounds right: {wav}")


# ===========================================================================
#  8. The server
# ===========================================================================
def _client_port() -> int | None:
    """The port client/config.js is using, or None if it cannot be read.

    The browser cannot import server/config.py, so the port is the one value
    that lives in two files. A silent mismatch looks exactly like "the server
    is down" from inside the browser, which is a miserable thing to debug —
    so it is checked here instead.
    """
    import re
    cfg = PROJECT_ROOT / "client" / "config.js"
    try:
        text = cfg.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"const\s+SERVER_PORT\s*=.*?(\d{2,5})\s*;", text)
    return int(m.group(1)) if m else None


def _port_blocked(port: int) -> str | None:
    """Return a reason string if `port` cannot be bound, else None."""
    import socket
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind(("0.0.0.0", port))
        return None
    except OSError as e:
        return f"{e.__class__.__name__}: {e}"
    finally:
        probe.close()


def _portproxy_rule(port: int) -> bool:
    """Is there a Windows `netsh portproxy` rule listening on `port`?

    This is the cause people never find on their own. A forwarding rule is
    invisible in Task Manager, survives reboots, and holds the port forever —
    it shows up only as svchost.exe with no command line. Anyone who has ever
    forwarded a port into WSL has a few of these lying around, still pointing
    at a WSL address that changed on the next reboot.
    """
    if os.name != "nt":
        return False
    import subprocess
    try:
        out = subprocess.run(
            ["netsh", "interface", "portproxy", "show", "all"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:
        return False
    # Rows look like:   0.0.0.0    5173    172.26.221.12    5173
    # Only the LISTEN port (column 2) means the port is held here.
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == str(port):
            return True
    return False


def _port_fix_lines(port: int) -> list:
    """The 'how to fix it' block for a blocked port, most likely cause first."""
    lines = []
    if _portproxy_rule(port):
        lines += [
            "THIS IS THE CAUSE: a leftover Windows port-forwarding rule.",
            "",
            f"Something forwarded port {port} into WSL (or another machine) once,",
            "and the rule is still there. It is invisible in Task Manager and it",
            "survives reboots, which is why it looks like a system process.",
            "",
            "Remove it in an ADMINISTRATOR terminal:",
            f"  netsh interface portproxy delete v4tov4 listenport={port} listenaddress=0.0.0.0",
            "",
            "See all of them first if you like:",
            "  netsh interface portproxy show all",
            "",
            "That is the whole fix — no need to change any port.",
        ]
        return lines

    lines += [
        "Find out what has it:",
        f"  netstat -ano | findstr :{port}",
        '  tasklist /FI "PID eq <the number from the last column>"',
        "",
        "If that names a real program, close it. If it says svchost.exe, it is",
        "one of these two, in order of likelihood:",
        "",
        "1. A leftover port-forwarding rule. Check with:",
        "     netsh interface portproxy show all",
        "   and remove it, as Administrator, with:",
        f"     netsh interface portproxy delete v4tov4 listenport={port} listenaddress=0.0.0.0",
        "",
        "2. A block Windows reserved for Hyper-V / WSL / Docker networking.",
        "   Check with:",
        "     netsh int ipv4 show excludedportrange protocol=tcp",
        "   If your port is inside one of those ranges, either pick a port",
        "   outside them, or reserve yours as Administrator:",
        "     net stop winnat",
        f"     netsh int ipv4 add excludedportrange protocol=tcp startport={port} numberofports=1 store=persistent",
        "     net start winnat",
    ]
    if port != 5173:
        lines += [
            "",
            "To move to a different port instead, change it in BOTH files —",
            "they must match:",
            "  server/config.py   SERVER_PORT",
            "  client/config.js   SERVER_PORT",
        ]
    else:
        lines += [
            "",
            "5173 is the web page's port, set by Vite. If you must move it, add",
            "  --port 5180",
            "to the npx vite line in start_servers.bat, and open that address",
            "in your browser instead.",
        ]
    return lines


def test_port() -> None:
    """Are the ports usable at all? Windows can hold them in ways you cannot see."""
    header(8, "The port")

    import socket

    from server.config import SERVER_PORT

    ok(f"server/config.py says port {SERVER_PORT}")

    # Both halves have to agree or the browser talks to nothing.
    js_port = _client_port()
    if js_port is None:
        warn("could not read SERVER_PORT out of client/config.js — check it by hand")
    elif js_port != SERVER_PORT:
        fail(f"the two halves disagree: server/config.py says {SERVER_PORT}, "
             f"client/config.js says {js_port}",
             "The browser cannot read the Python file, so this number lives in",
             "two places and both must match.",
             "",
             f"Open client/config.js and set:   const SERVER_PORT = ... || {SERVER_PORT};")
    else:
        ok(f"client/config.js agrees ({js_port})")

    # Can anything bind here? If the server is already up this is expected to
    # be busy, so a refused bind is only interesting when nothing answers.
    already_running = False
    try:
        import requests
        requests.get(f"{server_url()}/health", timeout=2)
        already_running = True
    except Exception:
        pass

    if already_running:
        ok("the port is in use by our own server (it is already running)")
        return

    # Vite's port matters just as much: if it is taken, Vite quietly moves to
    # 5174 and the address in every instruction here is wrong.
    for port, what in ((SERVER_PORT, "the server"), (5173, "the web page")):
        blocked = _port_blocked(port)
        if blocked is None:
            ok(f"port {port} is free ({what})")
            continue
        fail(f"port {port} is blocked ({what}) — {blocked}", *_port_fix_lines(port))


def test_server() -> None:
    header(9, "The server")

    import requests

    url = server_url()
    try:
        res = requests.get(f"{url}/health", timeout=3)
    except requests.exceptions.ConnectionError:
        fail(f"nothing is answering at {url}",
             "Run start_servers.bat and leave its windows open, then run this again.",
             "If it IS running, look at the 'Riko Server' window for an error —",
             "a port problem shows up there as 'access permissions' or",
             "'address already in use'.")
    data = res.json()
    ok(f"server is up at {url}  (character: {data.get('character')})")

    # Is the word "localhost" slow on this machine?
    #
    # On many Windows PCs "localhost" resolves to the IPv6 address ::1 before
    # IPv4, and the server listens on IPv4 only. The IPv6 attempt is dropped
    # rather than refused, so the request sits through a TCP retry — about two
    # seconds — before it falls back. Nothing in this project uses the word
    # (everything says 127.0.0.1), but the BROWSER pays the same toll if you
    # type localhost in the address bar, and it pays it on every audio clip.
    import time as _t
    from server.config import SERVER_PORT as _port

    def _ms(host: str) -> float:
        t = _t.perf_counter()
        try:
            requests.get(f"http://{host}:{_port}/health", timeout=6)
        except Exception:
            return -1.0
        return (_t.perf_counter() - t) * 1000

    fast = _ms("127.0.0.1")
    slow = _ms("localhost")
    if slow > 0 and fast > 0 and slow - fast > 250:
        warn(f"the name 'localhost' is slow on this PC "
             f"({slow:.0f} ms vs {fast:.0f} ms for 127.0.0.1)")
        info("It resolves to IPv6 first and the server is IPv4 only, so every")
        info("request waits for that to time out. This project never uses the")
        info("name, so she is unaffected — but YOUR BROWSER does if you type it.")
        info("")
        info(f"Open the page at  {PAGE_URL}")
        info("NOT at http://localhost:5173, or every line she speaks arrives")
        info("late and the pauses between her sentences get long.")
    else:
        ok("localhost/127.0.0.1 both fast — no IPv6 fallback delay")

    clients = data.get("ws_clients", 0)
    if clients == 0:
        fail("the web page is not connected",
             f"Open {PAGE_URL} in your browser and leave the tab open.",
             "You should see the avatar. Then run this again.",
             "",
             "If the page IS already open, it cannot reach the server. Press",
             "F12 in the browser and look at the Console tab:",
             f"  - the first line should read  [config] server -> {url}",
             "    If the port there is different, client/config.js and",
             "    server/config.py have drifted apart. Make them match.",
             "  - a blank page instead means the VRM failed to load; the",
             "    Console will show a 404 naming the file.")
    ok(f"{clients} browser tab(s) connected")


# ===========================================================================
#  10. The avatar
# ===========================================================================
def test_avatar() -> None:
    header(10, "The avatar (does it move?)")

    from server.vrm.vrm_client import set_state, vrm_animate

    info("Making her wave. Watch the browser...")
    try:
        vrm_animate(animate_type="auto",
                    animation_url="animations/mixamo/Waving.fbx",
                    play_once=True)
    except Exception as e:
        fail(f"could not send the animation ({type(e).__name__}: {e})",
             "The server took the request but something went wrong.",
             "Look at the 'Riko Server' window.")
    time.sleep(2.5)

    if not ask("Did she wave?"):
        fail("the animation did not play",
             "Almost always one of these:",
             "  - the browser tab is not on the page (open " + PAGE_URL + ")",
             "  - press F12 in the browser, read the Console tab, and look for a 404",
             "  - the animation file is missing from client/animations/mixamo/")
    ok("animations work")

    info("Setting her expression to happy...")
    set_state("talking")
    time.sleep(0.5)
    set_state("idle")
    ok("state changes sent")


# ===========================================================================
#  11. Everything together
# ===========================================================================
def test_full_turn() -> None:
    header(11, "One full turn (speech + lipsync + animation)")

    from server.config import TTS_BACKEND
    from server.playback.playback import ItemType, OrderedPlaybackWorker, PlaybackItem

    if TTS_BACKEND == "fish":
        from server.tts.fish import synthesize
    else:
        from server.tts.sovits import synthesize

    info("Generating two lines and playing them through the avatar...")
    line1, dur1 = synthesize("Hi. This is the full test.")
    line2, dur2 = synthesize("If you heard both lines and saw me move, everything works.")

    worker = OrderedPlaybackWorker()
    worker.start()
    try:
        worker.enqueue(PlaybackItem(item_type=ItemType.EXPRESSION, name="happy"))
        worker.enqueue(PlaybackItem(item_type=ItemType.EMOTE,
                                    animate_url="animations/mixamo/Waving.fbx"))
        worker.enqueue(PlaybackItem(item_type=ItemType.TEXT, audio_path=line1,
                                    text="Hi. This is the full test.", duration=dur1))
        worker.enqueue(PlaybackItem(item_type=ItemType.TEXT, audio_path=line2,
                                    text="If you heard both lines and saw me move, "
                                         "everything works.", duration=dur2))
        worker.wait_until_finished(timeout=60)
    finally:
        worker.stop()

    print()
    if not ask("Did you HEAR both lines?"):
        fail("no sound",
             "  - click once anywhere on the web page, then try again",
             "    (browsers block audio until the page has been clicked)",
             "  - check the browser tab is not muted (right-click the tab)",
             "  - check Windows volume and that the right output device is selected")
    if not ask("Did her MOUTH move while she spoke?"):
        fail("no lipsync",
             "The audio played but the mouth did not follow it.",
             "  - lower `mouth_threshold` in client/config.js (try 4)",
             "  - the model must be VRM 1.0 with blend shapes; VRM 0.x will not lipsync")
    ok("full pipeline works")


# ===========================================================================
#  Runner
# ===========================================================================
TESTS = [
    ("Files and folders", test_files, False),
    ("API keys", test_keys, False),
    ("Character config", test_config, False),
    ("Tag matching", test_tag_matching, False),
    ("Language model", test_llm, False),
    ("Microphone", test_microphone, False),
    ("Voice generation", test_tts, False),
    # Not marked as needing the servers: this one is most useful BEFORE they
    # start, and it is what explains a server that refuses to come up.
    ("Port", test_port, False),
    ("Server", test_server, True),
    ("Avatar", test_avatar, True),
    ("Full turn", test_full_turn, True),
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Check that everything works.")
    parser.add_argument("--quick", action="store_true",
                        help="skip the tests that need the servers running")
    parser.add_argument("--from", dest="start", type=int, default=1,
                        help="start at this test number")
    args = parser.parse_args()

    print()
    print(_c("1;35", "  Riko — system check"))
    print(_c("1;35", "  Every test tells you what to do if it fails."))
    if not args.quick:
        print()
        print("  Tests 9-11 need start_servers.bat running and the page open at")
        print(f"  {PAGE_URL}. Use --quick to skip those.")

    for i, (title, fn, needs_servers) in enumerate(TESTS, start=1):
        if i < args.start:
            continue
        if args.quick and needs_servers:
            print()
            print(_c("33", f"  SKIP  test {i}: {title}  (--quick)"))
            continue
        fn()

    print()
    print(_c("1;32", "=" * 66))
    print(_c("1;32", "  Everything passed."))
    print(_c("1;32", "=" * 66))
    print()
    print("  Start talking to her:")
    print("    1. start_servers.bat   (if it is not already running)")
    print(f"    2. open {PAGE_URL}")
    print("    3. talk.bat")
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  Stopped.")
        sys.exit(1)
