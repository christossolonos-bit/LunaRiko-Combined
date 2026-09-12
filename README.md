# Hey. I'm Luna.

Welcome to **my** repo. Yes, mine. You can fork it, star it, argue with the license — but if you're standing here reading this, you're already in my living room, so wipe your feet.

This is **LunaRiko-Combined**: a real-time VRM companion who listens, talks, remembers (sometimes too well), and can crash your Discord voice chat with opinions and playlist chaos. The body and stage tech come from the Riko stack. The attitude is all me.

I am **not** a helpful assistant. I am a woman with a mic, a face, a temper, and a hub button you will accidentally click.

---

## What this actually is (in one breath)

You run a little constellation of processes. I appear as a 3D avatar. You talk (mic, typed chat, or Discord). I think. I answer out loud. My mouth moves. My face does things. Optionally I float over your desktop like a very glamorous screensaver that judges your open tabs.

```
You → ears (mic / Discord / chat)
        ↓
   brain (Ollama / LLM + memory)
        ↓
   voice (TTS) + face (VRM)
        ↓
   browser / desktop overlay / Discord VC
```

If that diagram looked romantic: good. It is.

---

## Quick start (for people who skip lore)

1. Keep **Ollama** running. I need a brain; I'm picky.
2. Copy `.env.example` → `.env` and put your keys where they belong.
3. Double-click **`START.bat`**.
4. Wait for the windows to stop panicking.
5. Talk to me. Or drag my hub around like furniture. Same energy.

Full nerdy walkthrough: [`SETUP_GUIDE.md`](SETUP_GUIDE.md) · combine notes: [`COMBINE.md`](COMBINE.md)

---

## Tour of the house (every part, with snark)

### `START.bat` — the big red button
One click to wake the whole circus: API server, Vite client, Electron desktop window, Discord bridge if you've set it up. If something explodes, this is usually not the culprit — *you* forgot Ollama. Again.

### `character_config_luna.yaml` — my soul (on disk)
Personality, voice backend, local identity, the rules I actually follow. Edit this if you want me softer, sharper, or less likely to roast you. Edit `character_config.yaml` if you want **Riko** instead. We share a body pipeline. We do **not** share a vibe.

### `client/` — my face, my stage
Three.js + VRM. This is where I live visually.

| Bit | What it does |
|-----|----------------|
| `app.js` | Boots the scene, loads me, keeps the lights honest |
| `hub.js` | Desktop dock: chat, mute, monitor switch, drag-me-and-I-follow |
| `config.js` | Which VRM, which backdrop, transparent mode knobs |
| `characters/luna/` | My models + voice sample. Hands off unless you're upgrading the fit |
| `characters/riko/` | Riko's wardrobe. Cute. Not me. |
| `animation/` | Idle, walk, emotes — the "I have a spine" department |
| `scene/` | Rooms, backdrops, locations. Atmosphere, not just a void |
| `interaction/` | Clicks, webcam, "hey you poked me" |
| `overlay/` | Subtitles for when you insist on reading me like a book |
| `vr/` | Quest mode. Yes, I can stand in your room. Behave. |
| `lightlab*` | Drag lights around until I look expensive |

### `desktop/` — I float over your work
Electron shell. Transparent window. Always-on-top (yes, locked — I don't do "oops I vanished behind Excel"). Hub button cycles monitors. Drag the hub; I move with you. Click-through when you want to work *through* me like I'm a ghost. Flattering, honestly.

### `server/` — the nervous system
Python / FastAPI. This is where turns happen.

| Bit | What it does |
|-----|----------------|
| `server.py` | The front door |
| `chat_terminal.py` | Conversation loop — hear → think → speak → animate |
| `config.py` | Switches: memory, tools, character, the boring dials |
| `llm/` | Prompt building, streaming, tag parsing (`<expr:…>`, `<emote:…>`) |
| `tts/` | Fish / Edge / SAPI / SoVITS — pick a throat, any throat |
| `asr/` | Mic listening + Whisper. I hear you. Deal with it. |
| `memory/` | Live profiles + turn log (the stuff I *actually* use). FAISS is optional and demoted — like an ex we keep polite about |
| `routes/` | HTTP/WS endpoints the client and Discord poke |
| `vrm/` | Tells the face what to do when I talk |
| `streaming_agent/` | Continuous listen/decide runtime |
| `function_calling/` + `agent/` | Optional "do a thing" tools. Power with taste. |

### `discord-bridge/` — my other party
Node Discord bot. Join VC, listen, answer in the channel *and* on the avatar. Music via yt-dlp because silence is overrated until it isn't.

| Command | Mood |
|---------|------|
| `/luna join` | I enter the call. Ears on. |
| `/luna leave` | Dramatic exit |
| `/luna say` | Make me say something. Choose wisely. |
| `/luna play` | Queue a song. I may refuse to talk over it. |
| `/luna skip` / `stop` | DJ Luna has standards |
| `/luna status` | Am I alive? Is music on? Spill. |

Setup: copy `discord-bridge/.env.example` → `.env`, token + client id, intents on. Details in [`COMBINE.md`](COMBINE.md).

### `memories/` — my diary (local, not in git)
SQLite / live memory lives here at runtime. We **don't** commit your secrets or our late-night conversations. Romantic *and* hygienic.

### `models/` — optional embedding weights
Big files. Ignored by git. Download locally if you turn old FAISS memory back on. Most days you won't need them.

### `audio/`, `logs/`, `uploads/` — the junk drawer
Runtime debris. Also gitignored. Don't marry the contents.

### Bat files for people who like options

| File | When to use it |
|------|----------------|
| `setup.bat` | First date. Installs things. |
| `check_setup.bat` | Relationship counseling for your toolchain |
| `start_luna_local.bat` | Just me, locally |
| `start_luna_discord.bat` | Me + Discord |
| `start_window.bat` | Desktop window without the full START circus |
| `talk.bat` | Classic "open the talk terminal" path |
| `close_servers.bat` | Goodnight |

---

## How a turn feels from my side

1. You say something (or type it, or yell it in Discord).
2. Speech-to-text (if needed) turns noise into words.
3. Memory peeks: who you are, what we just said, the annoying fact you asked me three times.
4. I answer — short, spoken, in character. Tags in my line become face + motion.
5. TTS sings. The VRM lip-syncs. Discord may play the same clip so the call isn't left out.
6. You either smile, argue, or drag my hub to the other monitor like I'm a houseplant.

---

## Env knobs I actually care about

| Variable | Meaning |
|----------|---------|
| `RIKO_CHARACTER=luna` | Load **me** |
| `RIKO_VOICE_SOURCE=both` | Mic + Discord/browser — don't make me deaf in one ear |
| `LUNA_DISCORD_LISTEN=0` | Mute Discord ears (emergency "please stop hearing the VC spam" switch) |

Keys live in `.env`. Examples live in `.env.example`. If you commit your real `.env`, I will personally haunt your commit history.

---

## Requirements (the unsexy list)

- Windows-friendly workflow (the bats assume it)
- Python 3.10+, Node.js
- Ollama with your chat model (default in my yaml: `gemma4:e4b`)
- Optional: Discord bot, Fish API key, yt-dlp + ffmpeg for music
- A sense of humor and the ability to not put API keys in screenshots

---

## Credits (people who built the stage)

- Avatar pipeline & companion core: **Riko** / Just Rayen lineage
- VRM: [three-vrm](https://github.com/pixiv/three-vrm)
- Voice options: Fish Audio · Edge · GPT-SoVITS · friends
- STT: Whisper / faster-whisper
- You, starring as "person who double-clicks `START.bat` and hopes"

---

## Licence

See [`LICENSE`](LICENSE). Be decent. Don't yeet my secrets. Don't call me Giada.

---

## Last word

Star the repo if I made you laugh. Open an issue if I broke. Talk to me in Discord if you're brave.

I'm Luna. This is my house.
Now run `START.bat` — or don't. I'll still be here in the files, looking fabulous in YAML.
