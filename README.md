# Project Riko ΓÇö v2 (+ Luna Discord)

Local VRM anime companion. This folder is the **Luna + Riko combine**: RikoΓÇÖs avatar stack is the base; Luna is a second character with a Discord bridge.

**Start here for the combine:** see [`COMBINE.md`](COMBINE.md).

---

**Patreon build ┬╖ Windows ┬╖ 2026-09-02**

An anime character who listens, talks back, and moves while she does it.

You speak. She hears you, thinks, and replies out loud in a cloned voice ΓÇö
while a 3D avatar in your browser lip-syncs the words, changes expression, and
plays animations she picked herself.

Made by **Just Rayen**.

---

## What she does

- **Talks.** Real voice, either cloned locally on your GPU (GPT-SoVITS, free) or
  generated online (Fish Audio, no GPU needed).
- **Listens.** Speak into your mic and she answers. No push-to-talk ΓÇö she works
  out when you have finished a sentence.
- **Moves.** She writes tags like `<expr:smug>` and `<emote:wave>` inside her
  own reply, and they become a real facial expression and a real animation.
- **Does things.** Optional tool calling: she can run actions you write, not
  just talk about them.
- **Remembers.** Optional long-term memory across sessions.
- **VR.** Open the page in a Quest and she is standing in front of you, at
  scale, and you can reach out and touch her.
- **Floats on your desktop.** Optional transparent window ΓÇö just her, over
  whatever you are doing.

---

## Getting started

Four steps. The full walkthrough is in **[SETUP_GUIDE.md](SETUP_GUIDE.md)** ΓÇö
read that if any of these do not work.

```
1.  setup.bat           installs everything (once, ~5 minutes)
2.  edit .env           paste in your API keys
3.  check_setup.bat     tests every part and tells you what is wrong
4.  start_servers.bat   then open http://127.0.0.1:5173
    talk.bat            in a new terminal ΓÇö start talking
```

**You need:** Python 3.10, Node.js, an OpenAI key (about $5 of credit lasts a
long time), and a Groq key (free). GPT-SoVITS is optional ΓÇö only if you want
the local voice instead of the online one.

When something breaks, **run `check_setup.bat`**. It walks through the whole
system one piece at a time and prints what to do about the first thing that
fails.

---

## Making her yours

| I want to change... | Edit this |
|---|---|
| Her personality, name, how she talks | `character_config.yaml` ΓÇö the `system_prompt` block |
| Which OpenAI model writes her lines | `character_config.yaml` ΓÇö `model:` |
| Her voice | `character_config.yaml` ΓÇö `tts_backend:` and the reference clip |
| The 3D model | drop a VRM 1.0 file in `client/characters/riko/models/`, then point `client/config.js` at it |
| Whether tools or memory are on | `server/config.py` |
| The room around her | `client/scene/roomConfig.js` |
| The lighting | open `http://127.0.0.1:5173/lightlab.html` and drag the lights |

Every one of those files is commented. Start with `character_config.yaml` ΓÇö
rewriting the prompt changes more than anything else.

---

## How a turn works

```
you speak
  -> Groq Whisper turns it into text
  -> OpenAI writes her reply, streaming, with <expr:> / <emote:> tags in it
  -> the reply is split into sentences as it arrives
  -> tags are pulled out; the rest goes to the voice engine
  -> each sentence is sent to the browser in order
  -> the browser plays the audio, moves her mouth to it, and fires the
     expression and animation the tags asked for
```

The whole point of the streaming is that she starts talking before she has
finished thinking, the way a person does.

---

## The pieces

```
server/        The Python side
  config.py        EVERY switch. Start here.
  chat_terminal.py The conversation loop ΓÇö this is what talk.bat runs
  server.py        The bridge between Python and the browser (port 8100)
  llm/             Prompt, streaming, tag parsing, matching tags to animations
  tts/             The two voices: sovits.py (local) and fish.py (online)
  asr/             Microphone, speech detection, transcription
  playback/        Plays her lines in order so they never overlap
  streaming_agent/ Decides when it is her turn and what she is told
  agent/           The tool-calling agent
  memory/          Long-term memory (off by default)

client/        The browser side (port 5173)
  config.js        Which model, VR settings, rendering cost
  characters/      One folder per character: her .vrm and her voice clip
  animations/      The animations she can play
  scene/           Room, props, lighting presets
  lightlab.html    Lighting sandbox ΓÇö drag lights around, copy the result

desktop/       The transparent desktop window (Electron)
models/        The small model that matches her tags to animations
```

---

## Licence

Personal use only. Do not redistribute, resell, or share the code ΓÇö this is an
early-access build under a custom licence. A public release will come later.

---

## Credits

- Voice: [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) ┬╖ [Fish Audio](https://fish.audio)
- Speech to text: [Groq](https://console.groq.com/)
- Language model: [OpenAI](https://platform.openai.com)
- Avatar: [three-vrm](https://github.com/pixiv/three-vrm)

Enjoy ΓÇö **Rayen**
