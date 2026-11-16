"""Every runtime switch in the project, in one file.

Reads `character_config.yaml` once at import and exposes it as constants. If
you want to change how she behaves, this file and the character yaml are the
only two places you need to look.

Keep this dependency-free apart from stdlib + PyYAML, so every other module
can import it cheaply.
"""

import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

# Make print() safe on a Windows console.
#
# cmd.exe starts in codepage 437, which cannot encode the em dashes and arrows
# this project prints. Python raises UnicodeEncodeError rather than dropping the
# character — and when that happens inside the playback worker THREAD, the
# thread dies and she stops speaking mid-turn with a traceback. errors="replace"
# turns a crash into a "?" on screen. The .bat launchers also switch the console
# to UTF-8 (chcp 65001) so it renders properly instead.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass   # already redirected, or not a real console

# Project root = the folder this package sits in. Everything is resolved from
# here, so the project runs from wherever you unzipped it.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load .env before any API-key resolution (NVIDIA_API_KEY, FISH_API_KEY, …).
load_dotenv(PROJECT_ROOT / ".env", override=False)


# ===========================================================================
#  WHICH CHARACTER
# ===========================================================================
# One line. Change it and the prompt, model, voice, history file and memory
# store all follow.
#
#   riko    -> character_config.yaml
#   <name>  -> character_config_<name>.yaml
#
# The RIKO_CHARACTER environment variable overrides this when set, which is
# how start_servers.bat passes a name in.
DEFAULT_CHARACTER = "riko"

CHARACTER = (os.getenv("RIKO_CHARACTER") or DEFAULT_CHARACTER).strip().lower() or "riko"
if CHARACTER == "riko":
    CONFIG_PATH = PROJECT_ROOT / "character_config.yaml"
else:
    CONFIG_PATH = PROJECT_ROOT / f"character_config_{CHARACTER}.yaml"

if not CONFIG_PATH.exists():
    raise FileNotFoundError(
        f"[config] character config not found: {CONFIG_PATH} "
        f"(RIKO_CHARACTER={CHARACTER!r})"
    )
print(f"[config] character={CHARACTER!r} loading {CONFIG_PATH.name}")

with open(CONFIG_PATH, "r", encoding="utf-8") as _f:
    _config = yaml.safe_load(_f)


def _abs(path: str) -> str:
    """Turn a path from the yaml into an absolute one.

    Paths in the character yaml are written relative to the project folder so
    the package works from any directory. GPT-SoVITS is a SEPARATE process
    with its own working directory, though, and it opens the reference clips
    itself — so those have to reach it absolute. Already-absolute paths are
    left alone, which keeps an old hand-written config working.
    """
    p = Path(path)
    return str(p if p.is_absolute() else (PROJECT_ROOT / p))


# ===========================================================================
#  LLM
# ===========================================================================
# Which brain writes her lines. Local/Ollama only in this project.
#   "local"  — Ollama (or other local server). Never OpenAI.
LLM_BACKEND: str = (_config.get("llm_backend") or "local").strip().lower()
if LLM_BACKEND not in ("openai", "local"):
    raise ValueError(f"LLM_BACKEND={LLM_BACKEND!r} must be 'openai' or 'local'")
if LLM_BACKEND == "openai":
    raise ValueError(
        "LLM_BACKEND='openai' is disabled here — use llm_backend: local (Ollama)."
    )

MODEL: str = _config["model"]
SYSTEM_PROMPT: str = _config["presets"]["default"]["system_prompt"]
HISTORY_FILE: Path = Path(_abs(_config["history_file"]))

# Local OpenAI-compatible / Ollama settings (LLM_BACKEND=local only).
# Ollama's OpenAI shim is flaky with thinking models — we talk to the native
# /api/chat endpoint when the host looks like Ollama (port 11434 or
# LOCAL_LLM_API_STYLE=ollama).
LOCAL_LLM_BASE_URL: str = str(
    _config.get("local_llm_base_url") or "http://127.0.0.1:11434"
).rstrip("/")


def _resolve_local_llm_api_key() -> str:
    """Yaml key, env:NAME sentinel, or NVIDIA_API_KEY when hitting NVIDIA NIM."""
    raw = str(_config.get("local_llm_api_key") or "").strip()
    if raw.lower().startswith("env:"):
        return (os.getenv(raw.split(":", 1)[1].strip()) or "").strip()
    if raw in ("nvidia", "NVIDIA_API_KEY"):
        return (os.getenv("NVIDIA_API_KEY") or "").strip()
    host = LOCAL_LLM_BASE_URL.lower()
    if "nvidia.com" in host or "integrate.api.nvidia" in host:
        return (os.getenv("NVIDIA_API_KEY") or raw or "").strip()
    return raw or "ollama"


LOCAL_LLM_API_KEY: str = _resolve_local_llm_api_key()
# "ollama" = native /api/chat (recommended). "openai" = /v1/chat/completions.
LOCAL_LLM_API_STYLE: str = str(
    _config.get("local_llm_api_style") or "ollama"
).strip().lower()
# Qwen3 thinking models waste the whole budget on reasoning unless this is off.
# Keep false. Only True if the yaml value is explicitly true/yes/1/on.
_raw_think = _config.get("local_llm_think", False)
if isinstance(_raw_think, str):
    LOCAL_LLM_THINK: bool = _raw_think.strip().lower() in ("1", "true", "yes", "on")
else:
    LOCAL_LLM_THINK = bool(_raw_think)
LOCAL_LLM_MAX_TOKENS: int = int(_config.get("local_llm_max_tokens") or 512)

# Website / desktop mic / typed chat map to this Discord person (same human).
_local_id = _config.get("local_identity") or {}
if not isinstance(_local_id, dict):
    _local_id = {}
LOCAL_OWNER_DISCORD_ID: str = (
    os.getenv("LUNA_LOCAL_DISCORD_USER_ID")
    or str(_local_id.get("discord_user_id") or "")
).strip()
LOCAL_OWNER_DISPLAY: str = str(_local_id.get("display") or "Solonaras").strip() or "Solonaras"
LOCAL_OWNER_USERNAME: str = str(_local_id.get("username") or "solonaras2").strip() or "solonaras2"
LOCAL_OWNER_ROLE: str = str(_local_id.get("role") or "owner").strip().lower() or "owner"
if LOCAL_OWNER_DISCORD_ID:
    print(
        f"[config] local voice/chat = Discord "
        f"<@{LOCAL_OWNER_DISCORD_ID}> {LOCAL_OWNER_DISPLAY} "
        f"(@{LOCAL_OWNER_USERNAME}) role={LOCAL_OWNER_ROLE}"
    )
else:
    print("[config] local_identity unset — website/mic turns stay unlabeled")

# Vision caption model (Ollama VL). Chat stays on MODEL; this only describes images.
# Empty string disables LLaVA captioning. Typical: llava, llava:7b, moondream.
VISION_MODEL: str = str(_config.get("vision_model") or "").strip()

print(
    f"[config] LLM_BACKEND={LLM_BACKEND!r} model={MODEL!r}"
    + (
        f" url={LOCAL_LLM_BASE_URL!r} style={LOCAL_LLM_API_STYLE!r} "
        f"think={LOCAL_LLM_THINK}"
        if LLM_BACKEND == "local"
        else ""
    )
    + (f" vision={VISION_MODEL!r}" if VISION_MODEL else " vision=off")
)

# Her resting face — also the fallback when an <expr:...> tag matches nothing.
DEFAULT_EXPRESSION: str = _config["presets"]["default"].get(
    "default_expression", "neutral"
)


# ===========================================================================
#  VOICE (text to speech)
# ===========================================================================
# Declared in the character yaml so adding a character stays a one-file job.
#   "sovits" — local GPT-SoVITS on :9880. Free, needs a GPU and the installer.
#   "fish"   — Fish Audio over HTTP. No GPU, needs FISH_API_KEY in .env.
#   "edge"   — Microsoft Edge TTS via `edge-tts`. Needs network.
#   "sapi"   — Windows SAPI via pyttsx3. Fully offline, no API key, no GPU.
# All expose the same synthesize()/safe_synthesize() contract, so swapping
# one for another changes nothing else.
TTS_BACKEND: str = _config.get("tts_backend", "sovits")
FISH_REFERENCE_ID: str = _config.get(
    "fish_reference_id", "1df12c4bb692423283fde2bdc7f84093"
)
EDGE_TTS_VOICE: str = _config.get("edge_tts_voice", "en-US-AriaNeural")
# Edge rate string, e.g. "+0%", "+10%", "-5%".
EDGE_TTS_RATE: str = str(_config.get("edge_tts_rate", "+0%"))
# Substring matched against installed Windows voice names (e.g. "Zira", "Haruka").
SAPI_TTS_VOICE: str = str(_config.get("sapi_tts_voice") or "Zira")
# Relative rate offset from the SAPI default (~200). Negative = slower.
SAPI_TTS_RATE: int = int(_config.get("sapi_tts_rate") or 0)

SOVITS_URL: str = "http://127.0.0.1:9880/tts"

# The reference clip + spoken text SoVITS clones from. Paths made absolute
# above, because SoVITS opens them from its own process.
SOVITS_PING_CONFIG: dict = dict(_config["sovits_ping_config"])
SOVITS_PING_CONFIG["ref_audio_path"] = _abs(SOVITS_PING_CONFIG["ref_audio_path"])
SOVITS_PING_CONFIG["additional_aud"] = [
    _abs(p) for p in (SOVITS_PING_CONFIG.get("additional_aud") or [])
]

SOVITS_EMOTION_CONFIG: dict = {
    name: {**cfg, "audio_path": _abs(cfg["audio_path"])}
    for name, cfg in _config["sovits_emotion"].items()
}


# ===========================================================================
#  EARS (speech to text)
# ===========================================================================
# Where her input comes from.
#
#   "mic"     — desktop microphone + local/cloud Whisper. Default for fully
#               local setups. Browser text box still works (see chat_terminal).
#   "browser" — only the mic button / text box on the web page (:5173).
#   "both"    — desktop mic AND browser mic/text.
# Overridable: set RIKO_VOICE_SOURCE=browser for Discord text→turn feeding.
VOICE_SOURCE = (os.getenv("RIKO_VOICE_SOURCE") or "mic").strip().lower() or "mic"

VOICE_SOURCES = ("mic", "browser", "both")
if VOICE_SOURCE not in VOICE_SOURCES:
    raise ValueError(f"VOICE_SOURCE={VOICE_SOURCE!r} must be one of {VOICE_SOURCES}")
print(f"[config] VOICE_SOURCE={VOICE_SOURCE!r} "
      f"(local mic {'on' if VOICE_SOURCE in ('mic', 'both') else 'OFF'})")

# A short vocabulary hint for Whisper. NEVER a full sentence — Whisper often
# echoes sentence-length prompts as if you spoke them.
ASR_PROMPT: str = str(_config.get("asr_prompt") or "Luna.").strip()

# Who turns your speech into text.
#   "local" — faster-whisper on this machine. No API key. Default.
#   "groq"  — Groq Whisper over HTTP. Needs GROQ_API_KEY in .env.
ASR_BACKEND: str = (_config.get("asr_backend") or "local").strip().lower()
if ASR_BACKEND not in ("local", "groq"):
    raise ValueError(f"ASR_BACKEND={ASR_BACKEND!r} must be 'local' or 'groq'")

# faster-whisper settings (ASR_BACKEND=local only). On CPU, "base" + "int8"
# is the sweet spot; bump to "small" if you want better accuracy and can wait.
WHISPER_MODEL: str = str(_config.get("whisper_model") or "base")
WHISPER_DEVICE: str = str(_config.get("whisper_device") or "cpu")
WHISPER_COMPUTE_TYPE: str = str(_config.get("whisper_compute_type") or "int8")
print(
    f"[config] ASR_BACKEND={ASR_BACKEND!r}"
    + (
        f" model={WHISPER_MODEL!r} device={WHISPER_DEVICE!r} "
        f"compute={WHISPER_COMPUTE_TYPE!r}"
        if ASR_BACKEND == "local"
        else ""
    )
)

# How the microphone decides you have stopped talking.
#   True  — Silero VAD. Scores each 32 ms of audio for speech, so a keyboard
#           or a fan does not hold the turn open and the quiet end of a
#           sentence does not cut you off. No extra download: the model ships
#           inside faster-whisper, which is already installed.
#   False — a plain loudness threshold. Simpler, noticeably worse.
ENABLE_SILERO_VAD = True

# 0..1. Higher is stricter about what counts as speech. Raise it if room noise
# keeps the mic open; lower it if soft speech gets clipped.
VAD_SPEECH_THRESHOLD = 0.5

# Seconds of silence that end your turn. Silero can afford a short one.
VAD_SILENCE_DURATION = 0.25 if ENABLE_SILERO_VAD else 0.6

print(
    f"[config] ENABLE_SILERO_VAD={ENABLE_SILERO_VAD} "
    f"(threshold={VAD_SPEECH_THRESHOLD}, silence={VAD_SILENCE_DURATION}s)"
)


# ===========================================================================
#  SERVERS
# ===========================================================================
# The port this project's own server listens on. CHANGE IT HERE and every
# Python path follows — but you must change it in ONE MORE PLACE, because the
# browser cannot read this file:
#
#     client/config.js  ->  SERVER_PORT
#
# Those two numbers have to match. check_setup.bat compares them and tells you
# if they have drifted apart.
#
# WHY THIS IS NOT 8000
# 8000 is the most contested port on a developer machine. On Windows it also
# tends to be held by svchost.exe with no command line, which looks like a
# system process you cannot argue with. It usually is not — see below.
#
# IF THIS PORT IS TAKEN TOO, work through these in order. check_setup.bat
# (test 8) does this for you and prints the exact command.
#
#   1. A leftover port-forwarding rule. THE COMMON ONE, and the one nobody
#      finds on their own: it is invisible in Task Manager, survives reboots,
#      and shows up only as svchost.exe. Anyone who has forwarded a port into
#      WSL has some.
#           netsh interface portproxy show all
#      Remove one, as Administrator:
#           netsh interface portproxy delete v4tov4 listenport=8100 listenaddress=0.0.0.0
#
#   2. A real program. Find it:
#           netstat -ano | findstr :8100
#           tasklist /FI "PID eq <the number from the last column>"
#
#   3. A block Windows reserved for Hyper-V / WSL / Docker networking:
#           netsh int ipv4 show excludedportrange protocol=tcp
#      If your port is inside one of those ranges, pick one outside them, or
#      reserve yours as Administrator:
#           net stop winnat
#           netsh int ipv4 add excludedportrange protocol=tcp startport=8100 numberofports=1 store=persistent
#           net start winnat
#
# Changing the number here is always a valid escape hatch — just remember
# client/config.js has to change with it.
SERVER_PORT: int = 8100

# 127.0.0.1, NOT "localhost". Do not change this back.
#
# On Windows, "localhost" resolves to the IPv6 address ::1 FIRST, and the
# server listens on IPv4 only. The IPv6 attempt is not refused, it is dropped,
# so every request sits through a TCP retry before falling back to IPv4:
#
#     http://localhost:8100/health    2040 ms
#     http://127.0.0.1:8100/health       3 ms
#
# The playback worker POSTs /talk once per sentence, so that 2 seconds landed
# before her first word AND in the gap between every sentence after it. On
# Linux and WSL "localhost" is 127.0.0.1 and the cost is zero, which is why
# this only ever showed up on Windows.
VRM_BASE_URL: str = f"http://127.0.0.1:{SERVER_PORT}"     # this project's own bridge
BROWSER_INPUT_URL: str = f"http://127.0.0.1:{SERVER_PORT}"
print(f"[config] SERVER_PORT={SERVER_PORT} (must match SERVER_PORT in client/config.js)")


# ===========================================================================
#  PLAYBACK TIMING
# ===========================================================================
# The playback worker sends a clip, then waits its length before sending the
# next one, so two lines never overlap in the browser. The pad covers the
# browser's fetch-before-play cost, which the clip length alone does not: the
# first clip of a turn pays more (cold connection, first <audio> load), so it
# gets its own larger value instead of taxing every sentence with the worst
# case. Raise them if lines get clipped short; lower for snappier delivery.
PLAYBACK_PAD_S = 0.20
PLAYBACK_FIRST_PAD_S = 0.35


# ===========================================================================
#  ANIMATIONS AND ASSETS
# ===========================================================================
# Animation paths come in TWO flavours that look alike but are NOT
# interchangeable:
#
#   MIXAMO_DIR      absolute path on disk. For globbing files and checking
#                   they exist.
#   MIXAMO_REL_DIR  relative path for the BROWSER. Anything sent to /talk or
#                   /animate must be this kind — the browser fetches it over
#                   HTTP, and an absolute path fails silently.
CLIENT_ROOT: Path = PROJECT_ROOT / "client"
MIXAMO_DIR: Path = CLIENT_ROOT / "animations" / "mixamo"
MIXAMO_REL_DIR: Path = Path("animations") / "mixamo"
VRMA_REL_DIR: Path = Path("animations") / "vrma"

# Where the emote picker looks for animations. Order is match precedence when
# two folders hold the same name — earlier wins. VRMA first because those are
# hand-made; the Mixamo pack is the bulk fallback.
ANIMATION_SOURCES: list = [
    (CLIENT_ROOT / "animations" / "vrma",    "*.vrma"),
    (CLIENT_ROOT / "animations" / "vrma_xr", "*.vrma"),
    (MIXAMO_DIR,                              "*.fbx"),
]

# The small language model that matches her <expr:...> / <emote:...> tags to
# real expression and animation names. Ships inside the package.
MINILM_PATH: str = str(PROJECT_ROOT / "models" / "all-MiniLM-L6-v2")
# How close a tag has to be before it counts as a match. Below this, the
# fallback is used (default_expression / idle).
MATCH_THRESHOLD: float = 0.35

# Everything belonging to one character lives in one folder:
#   client/characters/<name>/models/   her .vrm files  (the browser loads them)
#   client/characters/<name>/voice/    SoVITS reference clips
# Under client/ because the dev server only serves what is inside its own
# folder — a VRM moved to the project root gives a blank page.
CHARACTERS_DIR: Path = CLIENT_ROOT / "characters"
CHARACTER_DIR: Path = CHARACTERS_DIR / CHARACTER

# Generated speech. The browser fetches from client/audio/; the copy in
# audio/ is there so other tools can find it too.
CLIENT_AUDIO_DIR: Path = CLIENT_ROOT / "audio"
PUBLIC_AUDIO_DIR: Path = PROJECT_ROOT / "audio"

# Drop images in here for the sample image tool. Also where the web page's
# image uploads land.
UPLOADS_DIR: Path = PROJECT_ROOT / "uploads"

# One JSON line per turn, one file per day. Separate from the chat history:
# this records the whole exchange including timings and any tool calls.
LOGS_DIR: Path = PROJECT_ROOT / "logs"


# ===========================================================================
#  TOOLS (function calling)
# ===========================================================================
# Lets her actually DO things instead of only talking. She calls one tool,
# `do_task`, with a plain-English instruction; a small second agent reads the
# instruction and picks the real tool to run.
ENABLE_FUNCTION_CALLING: bool = False

# Which agent handles do_task.
#   "dummy" — canned replies. No API key, no network. Good for a first run.
#   "real"  — a real agent that picks tools with Groq. Needs GROQ_API_KEY.
# With local Whisper + SAPI TTS + local LLM you can leave this on "dummy"
# and need no cloud keys at all.
AGENT_BACKEND = "dummy"

# Which set of tools the real agent gets.
#   "none"   — only the base tools below.
#   "sample" — the example registry (get_weather, start_poll). Canned, no
#              credentials. Copy server/agent_registries/sample_registry.py to
#              write your own.
REGISTRY = "sample"

# Base tools, merged into whichever registry is chosen above. Today: a sound
# effect she can play through the browser.
BASE_REGISTRY: bool = True

print(
    f"[config] ENABLE_FUNCTION_CALLING={ENABLE_FUNCTION_CALLING}"
    + (f" agent={AGENT_BACKEND!r} registry={REGISTRY!r}"
       f"{' + base' if BASE_REGISTRY else ''}" if ENABLE_FUNCTION_CALLING else "")
)


# ===========================================================================
#  MEMORY
# ===========================================================================
# Primary recall for Luna: live profiles + permanent turn log + working/durable
# (ENABLE_LIVE_MEMORY). Exact per Discord user — not fuzzy vector search.
#
# FAISS RAG (ENABLE_MEMORY) is optional backup only — off by default. Turn it
# on if you want soft "something like this" narrative recall; never use it as
# the source of truth for who said what.
#
# Session gist compresses older chat window text (still speaker-aware in prompt).
ENABLE_MEMORY = False          # FAISS long-term RAG — optional backup
ENABLE_SESSION_GIST = True     # rolling summary of older turns

# Live Discord/local memory (working + durable + per-user profiles + turn log).
# Overhear + wake/reply feed SQLite; Ollama consolidates in background.
ENABLE_LIVE_MEMORY = True

# Per character, so two characters never share memories. Git-ignored.
MEMORY_DIR = PROJECT_ROOT / "memories" / CHARACTER
MEMORY_EMBED_MODEL = MINILM_PATH        # reuse the model already loaded above
MEMORY_TOP_K = 4                        # memories injected per turn
MEMORY_DEDUP_THRESHOLD = 0.88           # similarity above this on WRITE = duplicate
MEMORY_MIN_SIMILARITY = 0.20            # similarity below this on READ = off-topic

# Which model writes the memories and the summary. Local Ollama only —
# never OpenAI. Runs between turns so it does not slow spoken replies.
MEMORY_EXTRACTOR_BACKEND = "local"
GIST_BACKEND = "local"

# Ollama native host (same as chat). mem_llm uses /api/chat + think:false.
MEMORY_LOCAL_BASE_URL = "http://127.0.0.1:11434"
MEMORY_LOCAL_MODEL = "gemma4:e4b"

# Session gist tuning.
WINDOW_MAX_MSGS = 16       # summarise once stored history reaches this many messages
EVICT_CHUNK_MSGS = 6       # how many oldest messages get folded in each time (must be even)
GIST_MAX_TOKENS = 150      # hard cap on the summary's length

print(
    f"[config][mem] ENABLE_MEMORY={ENABLE_MEMORY} "
    f"ENABLE_SESSION_GIST={ENABLE_SESSION_GIST} "
    f"ENABLE_LIVE_MEMORY={ENABLE_LIVE_MEMORY} "
    f"backend={MEMORY_EXTRACTOR_BACKEND!r}"
)


# ===========================================================================
#  LOGGING
# ===========================================================================
# False keeps the per-turn plumbing chatter out of the way so the lines that
# say something — what she was told, the timings, every warning — stay
# readable. True brings it all back for debugging. Either way the full record
# of every turn is written to logs/turns_<date>.jsonl.
VERBOSE = False


def vlog(*args, **kwargs) -> None:
    """print() that only speaks when VERBOSE is on."""
    if VERBOSE:
        print(*args, **kwargs)


print(
    f"[config] tts_backend={TTS_BACKEND!r}"
    + (f" fish_voice={FISH_REFERENCE_ID}" if TTS_BACKEND == "fish" else "")
    + (
        f" edge_voice={EDGE_TTS_VOICE} rate={EDGE_TTS_RATE}"
        if TTS_BACKEND == "edge"
        else ""
    )
    + (
        f" sapi_voice={SAPI_TTS_VOICE!r} rate_offset={SAPI_TTS_RATE}"
        if TTS_BACKEND == "sapi"
        else ""
    )
    + f" playback_pad={PLAYBACK_PAD_S}s first={PLAYBACK_FIRST_PAD_S}s"
)


if __name__ == "__main__":
    # Sanity check: python -m server.config
    print(f"[config] PROJECT_ROOT   = {PROJECT_ROOT}")
    print(f"[config] CONFIG_PATH    = {CONFIG_PATH}")
    print(f"[config] MODEL          = {MODEL}")
    preview = SYSTEM_PROMPT.strip().replace("\n", " ")
    print(f"[config] SYSTEM_PROMPT  = {preview[:140]}{'...' if len(preview) > 140 else ''}")
    print(f"[config] HISTORY_FILE   = {HISTORY_FILE}")
    print(f"[config] TTS_BACKEND    = {TTS_BACKEND}")
    print(f"[config] ref_audio_path = {SOVITS_PING_CONFIG['ref_audio_path']}")
    print(f"[config] MINILM_PATH    = {MINILM_PATH}")
    print(f"[config] CHARACTER_DIR  = {CHARACTER_DIR}")

    assert CONFIG_PATH.exists(), f"missing CONFIG_PATH: {CONFIG_PATH}"
    assert MIXAMO_DIR.exists(), f"missing MIXAMO_DIR: {MIXAMO_DIR}"
    assert Path(MINILM_PATH).exists(), f"missing MINILM_PATH: {MINILM_PATH}"
    assert CHARACTER_DIR.exists(), f"missing CHARACTER_DIR: {CHARACTER_DIR}"
    # Relative paths in the yaml must have come out absolute, or SoVITS (a
    # separate process with its own working directory) cannot open them.
    assert Path(SOVITS_PING_CONFIG["ref_audio_path"]).is_absolute()
    for name, cfg in SOVITS_EMOTION_CONFIG.items():
        assert Path(cfg["audio_path"]).is_absolute(), name
    print("[config] all required paths exist OK")
