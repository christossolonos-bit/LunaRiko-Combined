# Setup Guide

Everything you need, in order. If you get stuck, jump to
[When something is wrong](#when-something-is-wrong) at the bottom — the
`check_setup.bat` script diagnoses almost everything on its own.

Tested on **Windows 10 and 11** with **Python 3.10**.

---

## 1. Install the things she needs

| What | Where | Notes |
|---|---|---|
| **Python 3.10** | [python.org](https://www.python.org/downloads/release/python-31011/) | During install, **tick "Add python.exe to PATH"**. This is the step everyone misses. |
| **Node.js** | [nodejs.org](https://nodejs.org/) | Take the LTS version. Nothing to configure. |
| **GPT-SoVITS** | [github.com/RVC-Boss/GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) | **Optional.** Only if you want the local voice. Use the one-click installer. Skip this if you would rather use Fish Audio. |

To check Python installed correctly, open a terminal and type `python --version`.
You want `3.10.x`. If you get "not recognised", PATH was not ticked — reinstall.

---

## 2. Run setup.bat

Unzip the package somewhere with no spaces or accents in the path. `C:\Riko` is
a good choice; your Desktop is fine too.

Double-click **`setup.bat`**.

It installs the Python packages (using `uv`, which is much faster than pip),
installs the browser packages, and creates your `.env` file. Takes about five
minutes the first time, mostly downloading.

If it fails, it prints why. The usual causes are Python not on PATH, or Node.js
not installed.

---

## 3. Get your API keys

Open **`.env`** in Notepad. There are three lines. Paste your keys between the
quotes.

### OpenAI — required

This is what writes everything she says.

1. Go to [platform.openai.com/api-keys](https://platform.openai.com/api-keys)
2. Create a key and copy it.
3. Add about **$5** of credit to the account. On `gpt-4.1-mini` that lasts a
   very long time — it is a small model and she says short things.
4. Paste it: `OPENAI_API_KEY="sk-proj-..."`

### Groq — required, free

This turns your speech into text, and powers the tool-picking agent.

1. Go to [console.groq.com/keys](https://console.groq.com/keys)
2. Create a key and copy it.
3. Paste it: `GROQ_API_KEY="gsk_..."`

### Fish Audio — optional

Only needed if you want the online voice instead of the local one.

1. Go to [fish.audio](https://fish.audio) and make an account.
2. Copy your API key.
3. Paste it: `FISH_API_KEY="..."`

**Keep this file to yourself.** Anyone with those keys can spend your money.

---

## 4. Pick a voice

Open **`character_config.yaml`** and find this line near the top:

```yaml
tts_backend: sovits
```

### Option A — GPT-SoVITS (local, free, needs a GPU)

Leave it as `sovits`. Then open **`start_servers.bat`** and edit one line near
the top to point at your GPT-SoVITS folder — the one containing `go-api.bat`:

```bat
set SOVITS_PATH=C:\GPT-SoVITS
```

A reference clip is already included, so she has a voice out of the box. To use
your own, see [Cloning a different voice](#cloning-a-different-voice).

**Note!!!** If `go-api.bat` is missing, you can create the file and paste these two lines in there.

```bat
runtime\python.exe api_v2.py
pause
```


### Option B — Fish Audio (online, no GPU)

Change it to:

```yaml
tts_backend: fish
```

and make sure `FISH_API_KEY` is in `.env`. A sample voice is already set. To use
your own, make a voice model at [fish.audio](https://fish.audio) and paste its
id into `fish_reference_id`.

---

## 5. Test everything

Double-click **`check_setup.bat`**.

It goes through the whole system one piece at a time — files, keys, config, tag
matching, the language model, your microphone, her voice, the server, the
avatar, and finally a complete turn. Each test says PASS or FAIL, and a failure
prints exactly what to do about it.

Some tests ask you a question ("did she wave?"). Answer honestly — a "no" is how
it finds the problem.

The last three tests need the servers running, so run `start_servers.bat` first
(next section) or use `check_setup.bat --quick` to skip them.

---

## 6. Start her up

1. Double-click **`start_servers.bat`**. Three windows open (two if you are on
   Fish Audio). **Leave them all open** — closing one stops her.
2. Open **http://127.0.0.1:5173** in your browser. You should see the avatar
   standing there.
3. Double-click **`talk.bat`**. Wait for `listening...`, then speak.

She replies when you stop talking. There is no button to hold.

**Click once on the web page before your first turn.** Browsers refuse to play
audio until the page has been clicked. This catches everybody once.

To stop: close the terminal windows, or press Ctrl-C in each.

---

## 7. Make her yours

### The personality

`character_config.yaml`, the `system_prompt` block. This matters more than
everything else combined — it is who she is. Rewrite it completely if you like.

**Keep the INLINE TAGS section.** That is what teaches her to write `<expr:...>`
and `<emote:...>`, which is what makes her face and body move. Delete it and she
will still talk, but stand there like a statue.

You do not need to give her a list of valid tag names. Whatever word she writes
gets matched to the closest expression or animation available, so `<expr:smug>`,
`<expr:pleased>` and `<expr:grinning>` all work.

### The 3D model

1. Export from VRoid Studio as **VRM 1.0**. VRM 0.x will not load.
2. Put the `.vrm` file in `client/characters/riko/models/`.
3. Open `client/config.js` and change `vrm_path` to your filename.
4. Refresh the browser.

If her mouth barely moves when she talks, lower `mouth_threshold` in the same
file. Try 4.

### Cloning a different voice

Only for GPT-SoVITS.

1. Record **3 to 10 seconds** of the voice, clean, no long silences. Save it as
   a WAV in `client/characters/riko/voice/`.
2. In `character_config.yaml`, set `ref_audio_path` to the new file and
   `prompt_text` to **the exact words spoken in it**. Getting that text wrong is
   the most common cause of a bad clone.
3. Do the same for each entry under `sovits_emotion` — or point them all at the
   same clip, which is what the shipped config does.

### A room around her

She floats in empty space by default. To put her somewhere:

1. Download a room as a **GLB** — [Sketchfab](https://sketchfab.com) has a large
   free library.
2. Drop the file in `client/backgrounds/glb/`.
3. Open `client/scene/roomConfig.js`, set `enabled: true`, and point `url` at
   your file.
4. Refresh. Expect to nudge `position` and `scale` — downloaded rooms come in
   wildly different sizes.

Props work the same way in `client/scene/objectsConfig.js`.

### Lighting

Open **http://127.0.0.1:5173/lightlab.html** — a separate sandbox page. Drag the
light balls around, tune the sliders, and press **copy setup** when you like it.
Paste the result into `client/scene/scenePresets.js` as a new named preset, then
load it on the normal page with `?scene=yourname`.

The sandbox needs no server — just the web page.

---

## 8. Extras

### VR (Quest)

1. Connect the Quest to your PC with Link or Air Link.
2. Open **http://127.0.0.1:5173** in the headset's browser (or press **Enter VR**
   on the desktop page while the headset is active).
3. Reach out and touch her — she reacts.

If she looks too small, too far away, or too low, edit `dollyPosition` in
`client/config.js`. `Y` is your eye height, `Z` is how far back you stand.

### The transparent desktop window

Just the avatar, floating over your desktop, no browser around it.

1. `start_servers.bat` first — this is only the window.
2. `start_window.bat`. It installs Electron the first time (~200 MB).

`ctrl+shift+Q` quits. Drag the top 22 pixels to move it.

Note: a `?scene=` preset that uses depth of field makes the window solid, so
scenes and the transparent window are mutually exclusive. Use one or the other.

### Talking to her from your phone or another computer

The web page works from any device on your network — the page figures out which
machine it came from and talks back to it, so there is nothing to configure.

1. Find your PC's local IP: `ipconfig` in a terminal, look for IPv4 Address.
2. On the other device, open `http://<that-ip>:5173`.

That gives you a **viewer** — you see and hear her, and the desktop mic still
drives the conversation. To talk from that device instead, set
`VOICE_SOURCE = "browser"` in `server/config.py` and use the mic button and text
box on the page. Note that browsers only allow the mic over `https://` or
`localhost`, so on a plain LAN address the text box will work and the mic button
will not.

**Over [Tailscale](https://tailscale.com)** it works the same way, from
anywhere: install Tailscale on both machines and use the PC's tailnet address
(`http://100.x.x.x:5173`) instead of the LAN one. Tailscale addresses are
treated as secure by browsers, so the mic button works too.

### Tools — letting her do things

`server/config.py`:

```python
ENABLE_FUNCTION_CALLING = True
REGISTRY = "sample"      # the example tools
```

The example registry has two canned tools (`get_weather`, `start_poll`) so you
can see the mechanism work. Ask her "what's the weather in Tokyo?" and watch the
terminal.

To write real ones, copy `server/agent_registries/sample_registry.py` and follow
the comments in `server/agent_registries/__init__.py`. Set `AGENT_BACKEND =
"dummy"` if you want to test the wiring with no API key at all.

### Memory

Off by default. In `server/config.py`:

```python
ENABLE_MEMORY = True         # remembers across sessions
ENABLE_SESSION_GIST = True   # keeps long conversations coherent
```

It uses the OpenAI key you already have — nothing new to sign up for. Both run
**between** turns, never during one, so she does not get slower. Memories are
stored on your machine in `memories/`.

Delete that folder to make her forget everything.

---

## When something is wrong

**First: run `check_setup.bat`.** It is built for exactly this and will usually
name the problem outright.

If it does not, here are the ones that come up most.

| What you see | What it means |
|---|---|
| The web page is blank / white | The server or Vite is not running, or the VRM failed to load. Press **F12** in the browser and read the Console tab — a red 404 tells you which file is missing. |
| The server window says the port is in use, or "access permissions" | Windows has reserved the port. See [Port problems](#port-problems) below — this is common on Windows and is not something you did wrong. |
| She never replies, and the page looks fine | Press F12 in the browser. The first console line reads `[config] server -> http://…:8100`. If that port is not the one in `server/config.py`, the two config files have drifted apart. |
| She talks but there is no sound | Click once on the web page. Browsers block audio until the page has been clicked. |
| Her mouth does not move | Lower `mouth_threshold` in `client/config.js`. If it still does not, the model is probably VRM 0.x — re-export as VRM 1.0. |
| She never responds when I speak | Check `talk.bat` prints `listening...`. Then check the right microphone is the Windows default. Then lower `VAD_SPEECH_THRESHOLD` in `server/config.py` to 0.3. |
| She responds to herself | Your speakers are feeding the mic. Use headphones, or turn the volume down. |
| Animations never play | Press F12 and look for a 404 in the Console. The animation file is missing or renamed. |
| `GPT-SoVITS` window shows an error | Almost always the reference clip: wrong path, wrong `prompt_text`, or a clip that is too long. |
| "insufficient_quota" from OpenAI | The account is out of credit. Add some at [the billing page](https://platform.openai.com/settings/organization/billing). |
| Long pause before she speaks, long gaps between her sentences | You opened the page at `localhost:5173`. Use **`127.0.0.1:5173`**. See [Why localhost is slow](#why-localhost-is-slow) below. |
| Everything is slow | Try `gpt-4.1-mini` if you are not already on it. Fish Audio is slower than a local GPU; GPT-SoVITS on CPU is much slower than on GPU. |

### Why localhost is slow

Use **`http://127.0.0.1:5173`**, not `http://localhost:5173`.

On many Windows PCs the word `localhost` resolves to the IPv6 address `::1`
before it tries IPv4, and the servers here listen on IPv4. Windows does not
refuse the IPv6 attempt, it drops it — so every request waits about **two
seconds** for that to give up before it falls back to the address that works.

    http://localhost:8100/health     2040 ms
    http://127.0.0.1:8100/health        3 ms

Two seconds does not sound like much until you count how often it happens: the
browser fetches one audio clip per sentence, so it lands before her first word
and again in every gap between sentences. It is the single biggest thing that
makes her feel slow on Windows, and it does not happen on Linux or WSL, where
`localhost` is 127.0.0.1.

Nothing inside the project uses the name any more — it all says `127.0.0.1`.
The only place it can still bite you is the address bar. `check_setup.bat`
(test 9) measures both and tells you if this PC is affected.

### Port problems

The most common Windows-only problem. It looks alarming because the process
holding the port is usually `svchost.exe`, which you cannot close — but it is
almost never actually a system process doing it.

**Run `check_setup.bat` first.** Test 8 checks both ports, works out which of
the causes below it is, and prints the exact command to fix it. The rest of
this section is what it is doing, in case you want to do it by hand.

#### Cause 1 — a leftover port-forwarding rule (most likely)

If you have ever forwarded a port into WSL, a virtual machine, or another
computer, Windows still has the rule. They are invisible in Task Manager, they
survive reboots, and they hold the port forever — pointing at an address that
probably stopped existing the next time WSL restarted.

List them:

```
netsh interface portproxy show all
```

If your port is in the **Listen Port** column, that rule is your problem.
Remove it in a terminal opened **as Administrator**:

```
netsh interface portproxy delete v4tov4 listenport=8100 listenaddress=0.0.0.0
```

Or clear every rule at once, if you are sure you want none of them:

```
netsh interface portproxy reset
```

No need to change any port after this — it just works again.

#### Cause 2 — a real program

```
netstat -ano | findstr :8100
tasklist /FI "PID eq <the number from the last column>"
```

If that names a real program, close it.

#### Cause 3 — a block Windows reserved for virtualisation

With Hyper-V, WSL, Docker Desktop, or Windows Sandbox installed, Windows hands
blocks of TCP ports to its own networking at boot. Anything inside a block is
held before your program starts, and the blocks move between reboots.

```
netsh int ipv4 show excludedportrange protocol=tcp
```

If your port is inside one of those ranges, either pick a port outside them, or
reserve yours so Windows skips it. **As Administrator**, once:

```
net stop winnat
netsh int ipv4 add excludedportrange protocol=tcp startport=8100 numberofports=1 store=persistent
net start winnat
```

`net stop winnat` briefly drops Docker / WSL networking; it comes back.

#### Moving to a different port

Always a valid escape hatch. Pick any free number from 1024 to 49151 and set it
in **both** files — the browser cannot read a Python file, so this one number
lives in two places:

```
server/config.py     SERVER_PORT = 8100
client/config.js     const SERVER_PORT = Number(_params.get('api')) || 8100;
```

`check_setup.bat` (test 8) compares them and tells you if you only changed one.

To try a port before committing, open the page with `?api=` — that overrides
the client for one page load with nothing to edit:

```
http://127.0.0.1:5173/?api=8250
```

#### If 5173 is the blocked one

That is the web page's port, not the server's. Vite will quietly start on 5174
instead, so the address in every instruction here would be wrong. Fix the port
using the causes above, or add `--port 5180` to the `npx vite` line in
`start_servers.bat` and open that address instead.

---

### Reading the logs

Every turn is written to `logs/turns_<date>.jsonl` — what she was told, what she
said, how long each step took, and any tools she used. That file is the honest
record when something feels wrong but nothing errors.

For more detail on screen, set `VERBOSE = True` in `server/config.py`.

---

## Quick reference

```
setup.bat            install everything (once)
check_setup.bat      test everything, get told what is wrong
start_servers.bat    start the server + web page (+ SoVITS)
talk.bat             start talking
start_window.bat     transparent desktop window
```

| Address | What |
|---|---|
| http://127.0.0.1:5173 | The avatar |
| http://127.0.0.1:5173/lightlab.html | Lighting sandbox |
| http://127.0.0.1:8100/health | Is the server alive? |

| File | What is in it |
|---|---|
| `.env` | Your API keys |
| `character_config.yaml` | Who she is, and her voice |
| `server/config.py` | Every runtime switch |
| `client/config.js` | Which 3D model, VR settings |
| `client/scene/roomConfig.js` | The room around her |
