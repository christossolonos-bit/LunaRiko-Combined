/**
 * Luna Discord bridge — listen + talk + music against Riko VRM core.
 *
 *   /luna join|leave|say|status|play|radio|skip|stop
 *   VC speech → Whisper STT → Riko /send_message → TTS plays in VC + VRM
 *   Voice: "Luna play …" / "Luna play distrokid …" / "Luna play radio …" / "Luna stop"
 *   /luna play <query|url> → YouTube, DistroKid folder, or radio
 */

import "dotenv/config";
import {
  Client,
  GatewayIntentBits,
  Partials,
  REST,
  Routes,
  SlashCommandBuilder,
  ChannelType,
  AttachmentBuilder,
} from "discord.js";
import {
  joinVoiceChannel,
  createAudioPlayer,
  createAudioResource,
  AudioPlayerStatus,
  getVoiceConnection,
  entersState,
  VoiceConnectionStatus,
  generateDependencyReport,
} from "@discordjs/voice";
import { createWriteStream, existsSync, mkdirSync } from "node:fs";
import { unlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve, isAbsolute, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { pipeline } from "node:stream/promises";
import { Readable } from "node:stream";
import { spawn } from "node:child_process";
import { SttWorker } from "./sttWorker.js";
import { attachVoiceListen } from "./voiceListen.js";
import { MusicQueue, listRadioStationNames, musicPaths } from "./music.js";
import { parseMusicIntent } from "./musicIntents.js";

const TOKEN = process.env.DISCORD_BOT_TOKEN?.trim();
const CLIENT_ID = process.env.DISCORD_CLIENT_ID?.trim();
const RIKO_HTTP = (process.env.RIKO_HTTP_URL || "http://127.0.0.1:8100").replace(/\/$/, "");
const GUILD_ID = process.env.DISCORD_GUILD_ID?.trim() || "";
const TEXT_CHANNEL_ID = process.env.DISCORD_TEXT_CHANNEL_ID?.trim() || "";
const PROJECT_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const LISTEN_ENABLED = (process.env.LUNA_DISCORD_LISTEN || "1") !== "0";
// Permanent "this is you" Discord user ids (comma-separated). Join host is also tagged owner.
const OWNER_USER_IDS = new Set(
  String(process.env.DISCORD_OWNER_USER_IDS || process.env.DISCORD_OWNER_USER_ID || "")
    .split(/[,\s]+/)
    .map((s) => s.trim())
    .filter(Boolean),
);
// VC wake word — STT/transcribe everyone always; reply ONLY when her name is said.
// Follow-up window defaults to 0 (must say "Luna" each time). Set LUNA_WAKE_FOLLOWUP_MS
// if you want a short no-name continue window after a wake.
const WAKE_REQUIRED = (process.env.LUNA_WAKE_REQUIRED || "1") !== "0";
const WAKE_WORDS = String(process.env.LUNA_WAKE_WORDS || "luna,hey luna,okay luna,ok luna")
  .split(",")
  .map((s) => s.trim().toLowerCase())
  .filter(Boolean)
  .sort((a, b) => b.length - a.length);
const WAKE_FOLLOWUP_MS = Number(process.env.LUNA_WAKE_FOLLOWUP_MS || 0);
/** Keep ears closed this long after each TTS clip so inter-sentence gaps don't barge in. */
const TTS_SETTLE_MS = Number(process.env.LUNA_TTS_SETTLE_MS || 1500);
/** Discord VC TTS gain. 1.0 = normal; >1 boosts so other bots hear her better. */
const TTS_VOLUME = Math.min(
  4,
  Math.max(0.1, Number(process.env.LUNA_TTS_VOLUME || 2.0) || 2.0),
);

if (!TOKEN || !CLIENT_ID) {
  console.error("[luna-discord] Set DISCORD_BOT_TOKEN and DISCORD_CLIENT_ID in discord-bridge/.env");
  process.exit(1);
}

const commands = [
  new SlashCommandBuilder()
    .setName("luna")
    .setDescription("Luna Discord controls (Riko VRM avatar)")
    .addSubcommand((s) => s.setName("join").setDescription("Join your voice channel and listen"))
    .addSubcommand((s) => s.setName("leave").setDescription("Leave voice"))
    .addSubcommand((s) =>
      s
        .setName("say")
        .setDescription("Send text to Luna")
        .addStringOption((o) => o.setName("text").setDescription("What to tell her").setRequired(true)))
    .addSubcommand((s) =>
      s
        .setName("play")
        .setDescription("Play YouTube, DistroKid library, or auto")
        .addStringOption((o) =>
          o.setName("query").setDescription("Song name, YouTube URL, or library title").setRequired(true))
        .addStringOption((o) =>
          o
            .setName("source")
            .setDescription("Where to play from")
            .addChoices(
              { name: "auto", value: "auto" },
              { name: "youtube", value: "youtube" },
              { name: "distrokid", value: "distrokid" },
              { name: "radio", value: "radio" },
            )))
    .addSubcommand((s) =>
      s
        .setName("radio")
        .setDescription("Play a named radio station")
        .addStringOption((o) =>
          o.setName("station").setDescription("Station name (see radio-stations.json)").setRequired(true)))
    .addSubcommand((s) => s.setName("skip").setDescription("Skip current song"))
    .addSubcommand((s) => s.setName("stop").setDescription("Stop music and clear queue"))
    .addSubcommand((s) => s.setName("status").setDescription("Show bridge + Riko + music status")),
].map((c) => c.toJSON());

async function registerCommands() {
  const rest = new REST({ version: "10" }).setToken(TOKEN);
  if (GUILD_ID) {
    await rest.put(Routes.applicationGuildCommands(CLIENT_ID, GUILD_ID), { body: commands });
    console.log(`[luna-discord] registered guild commands for ${GUILD_ID}`);
  } else {
    await rest.put(Routes.applicationCommands(CLIENT_ID), { body: commands });
    console.log("[luna-discord] registered global commands (can take up to ~1h)");
  }
}

async function rikoHealth() {
  const res = await fetch(`${RIKO_HTTP}/health`, { signal: AbortSignal.timeout(3000) });
  if (!res.ok) throw new Error(`health ${res.status}`);
  return res.json();
}

async function sendToRiko(text, opts = {}) {
  const inVc = Boolean(voiceGuildId && getVoiceConnection(voiceGuildId));
  // In VC → speak in the call. Out of VC → text chat reply + mp3.
  const source = opts.source || (inVc ? "discord" : "discord_chat");
  const res = await fetch(`${RIKO_HTTP}/send_message`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message_text: text,
      source,
      reply_audio: source,
    }),
  });
  if (!res.ok) throw new Error(`send_message ${res.status}: ${await res.text()}`);
  return res.json();
}

function runFfmpeg(args) {
  return new Promise((resolvePromise, reject) => {
    const child = spawn("ffmpeg", args, { windowsHide: true, stdio: ["ignore", "ignore", "pipe"] });
    let err = "";
    child.stderr.on("data", (d) => {
      err += d.toString();
    });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) resolvePromise();
      else reject(new Error(`ffmpeg exit ${code}: ${err.slice(-400)}`));
    });
  });
}

async function wavsToMp3(wavPaths) {
  const paths = (wavPaths || []).map((p) => resolveAudioPath(p) || p).filter((p) => p && existsSync(p));
  if (!paths.length) return null;
  const dir = join(tmpdir(), "luna-discord-chat");
  mkdirSync(dir, { recursive: true });
  const outMp3 = join(dir, `luna-${Date.now()}.mp3`);
  if (paths.length === 1) {
    await runFfmpeg(["-y", "-i", paths[0], "-codec:a", "libmp3lame", "-q:a", "4", outMp3]);
    return outMp3;
  }
  const args = ["-y"];
  for (const p of paths) args.push("-i", p);
  const labels = paths.map((_, i) => `[${i}:a]`).join("");
  args.push(
    "-filter_complex",
    `${labels}concat=n=${paths.length}:v=0:a=1[a]`,
    "-map",
    "[a]",
    "-codec:a",
    "libmp3lame",
    "-q:a",
    "4",
    outMp3,
  );
  await runFfmpeg(args);
  return outMp3;
}

async function waitForDiscordChatReply({ afterVersion = 0, timeoutMs = 180_000 } = {}) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    try {
      const res = await fetch(
        `${RIKO_HTTP}/pending_discord_chat_reply?consume=0`,
        { signal: AbortSignal.timeout(3000) },
      );
      if (res.ok) {
        const snap = await res.json();
        if (snap.ready && Number(snap.version || 0) > Number(afterVersion || 0)) {
          const take = await fetch(
            `${RIKO_HTTP}/pending_discord_chat_reply?consume=1`,
            { signal: AbortSignal.timeout(3000) },
          );
          if (take.ok) return take.json();
          return snap;
        }
      }
    } catch {
      /* server restarting */
    }
    await new Promise((r) => setTimeout(r, 400));
  }
  throw new Error("timed out waiting for Luna's text reply");
}

async function postChatReply(messageOrInteraction, labeled) {
  const before = await fetch(`${RIKO_HTTP}/pending_discord_chat_reply?consume=0`)
    .then((r) => (r.ok ? r.json() : { version: 0 }))
    .catch(() => ({ version: 0 }));
  const sent = await sendToRiko(labeled, { source: "discord_chat" });
  console.log(`[luna-discord] text→riko v${sent.version}: ${labeled}`);
  const reply = await waitForDiscordChatReply({ afterVersion: before.version || 0 });
  if (reply.error && !reply.text) {
    throw new Error(reply.error);
  }
  const text = (reply.text || "").trim() || "…";
  const files = [];
  let mp3 = null;
  try {
    mp3 = await wavsToMp3(reply.audio_paths || []);
    if (mp3) {
      files.push(new AttachmentBuilder(mp3, { name: "luna.mp3" }));
    }
  } catch (e) {
    console.warn("[luna-discord] mp3 convert failed:", e.message);
  }
  const payload = { content: text.slice(0, 1900), files };
  if (messageOrInteraction.reply && messageOrInteraction.author) {
    await messageOrInteraction.reply(payload);
  } else if (messageOrInteraction.editReply) {
    await messageOrInteraction.editReply(payload);
  } else {
    await messageOrInteraction.channel.send(payload);
  }
  if (mp3) unlink(mp3).catch(() => {});
  return reply;
}

async function setDiscordVcActive(active) {
  try {
    const res = await fetch(`${RIKO_HTTP}/discord_vc`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ active: Boolean(active) }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    console.log(`[luna-discord] discord_vc active=${data.active} reply=${data.reply_audio}`);
  } catch (e) {
    console.warn("[luna-discord] discord_vc notify failed:", e.message);
  }
}

function resolveAudioPath(audioPath) {
  if (!audioPath) return null;
  if (isAbsolute(audioPath) && existsSync(audioPath)) return audioPath;
  const cleaned = String(audioPath).replace(/^\.?\//, "");
  for (const c of [
    resolve(PROJECT_ROOT, cleaned),
    resolve(PROJECT_ROOT, "client", cleaned),
    resolve(PROJECT_ROOT, "audio", cleaned.split(/[/\\]/).pop()),
    resolve(PROJECT_ROOT, "client", "audio", cleaned.split(/[/\\]/).pop()),
  ]) {
    if (existsSync(c)) return c;
  }
  return null;
}

async function downloadIfRemote(audioPath) {
  if (!audioPath) return null;
  if (/^https?:\/\//i.test(audioPath) || audioPath.startsWith("audio/") || audioPath.startsWith("/audio/")) {
    const url = /^https?:\/\//i.test(audioPath)
      ? audioPath
      : `${RIKO_HTTP}/${audioPath.replace(/^\//, "")}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`download ${res.status}`);
    const dir = join(tmpdir(), "luna-discord-audio");
    mkdirSync(dir, { recursive: true });
    const dest = join(dir, `clip-${Date.now()}.wav`);
    await pipeline(Readable.fromWeb(res.body), createWriteStream(dest));
    return dest;
  }
  return resolveAudioPath(audioPath);
}

const player = createAudioPlayer();
const music = new MusicQueue(player);
const stt = new SttWorker();

let lastAudioVersion = 0;
let voiceGuildId = null;
let vcHostUserId = null; // who ran /luna join — treated as owner for this session
let detachListen = null;
let lunaSpeaking = false;
let musicPlaying = false;
const ttsQueue = [];
let ttsPumpRunning = false;
/** Hold listen closed across gaps between streamed TTS sentences. */
let ttsHoldUntil = 0;
let ttsSettleTimer = null;
let lastReportedTtsBusy = null;
/** One deferred wake while she talks — fired after TTS goes idle. */
let deferredReply = null;
let lastWakeAt = 0;
let lastWakeUserId = null;

function normalizeForWake(text) {
  return String(text || "")
    .toLowerCase()
    .replace(/[^\w\s']/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function hasWakeWord(text) {
  const t = normalizeForWake(text);
  if (!t) return false;
  return WAKE_WORDS.some((w) => {
    // Word-boundary-ish: "luna" matches "luna hey" / "hey luna" not "lunar"
    const re = new RegExp(`(?:^|\\s)${w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?:\\s|$)`);
    return re.test(` ${t} `);
  });
}

function stripWakeWord(text) {
  let out = String(text || "").trim();
  for (const w of WAKE_WORDS) {
    const re = new RegExp(`^${w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}[,:;!?]?\\s+`, "i");
    if (re.test(out)) {
      out = out.replace(re, "").trim();
      break;
    }
  }
  return out || String(text || "").trim();
}

/** Still STT everything; only return true when she should answer. */
function shouldReplyToVoice(text, userId) {
  if (!WAKE_REQUIRED) return true;
  if (hasWakeWord(text)) {
    lastWakeAt = Date.now();
    lastWakeUserId = userId ? String(userId) : null;
    return true;
  }
  // Optional short follow-up (off by default): same speaker, no need to repeat "Luna".
  if (
    WAKE_FOLLOWUP_MS > 0 &&
    lastWakeUserId &&
    userId &&
    String(userId) === lastWakeUserId &&
    Date.now() - lastWakeAt < WAKE_FOLLOWUP_MS
  ) {
    lastWakeAt = Date.now();
    return true;
  }
  return false;
}

function isOwnerUserId(userId) {
  if (!userId) return false;
  const id = String(userId);
  return OWNER_USER_IDS.has(id) || (vcHostUserId && id === String(vcHostUserId));
}

/**
 * Stable speaker tag so Luna can tell people apart in a group call.
 * Example:
 *   [Discord VC <@123> Solonaras (@solonaras) role=owner YOU (owner) voice=low~105Hz warm]: hey
 *   [Discord VC <@456> Travis (@travis) role=guest other user voice=mid~175Hz bright]: what
 */
async function formatDiscordSpeaker(userId, guild = null, voiceLabel = null) {
  const id = String(userId || "");
  let username = id || "unknown";
  let display = "someone";
  try {
    const user = await client.users.fetch(id);
    username = user.username || username;
    display = user.globalName || user.displayName || username;
    if (guild) {
      try {
        const member = await guild.members.fetch(id);
        if (member?.displayName) display = member.displayName;
      } catch {
        /* DMs / missing member */
      }
    }
  } catch {
    /* keep fallbacks */
  }
  const role = isOwnerUserId(id) ? "owner" : "guest";
  const whoLabel = role === "owner" ? "YOU (owner)" : "other user";
  const voiceBit = voiceLabel ? ` voice=${String(voiceLabel).replace(/[\[\]]/g, "")}` : "";
  return {
    tag: `<@${id}>`,
    display,
    username,
    role,
    whoLabel,
    voiceLabel: voiceLabel || null,
    linePrefix: `[Discord VC ${`<@${id}>`} ${display} (@${username}) role=${role} ${whoLabel}${voiceBit}]`,
  };
}

async function learnVoiceprint(userId, display, voice) {
  if (!userId || !voice?.vector?.length) return null;
  try {
    const res = await fetch(`${RIKO_HTTP}/voiceprint/learn`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id: String(userId),
        display: display || undefined,
        vector: voice.vector,
        pitch_hz: voice.pitchHz,
        pitch_std: voice.pitchStd,
        centroid_hz: voice.centroidHz,
        label: voice.label,
      }),
      signal: AbortSignal.timeout(5000),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    console.log(
      `[voiceprint] learned ${display || userId} → ${data.label || voice.label} ` +
        `(samples=${data.samples || "?"})`,
    );
    return data;
  } catch (e) {
    console.warn("[voiceprint] learn failed:", e.message);
    return null;
  }
}

async function labelDiscordUtterance(userId, text, guild = null, voiceLabel = null) {
  const info = await formatDiscordSpeaker(userId, guild, voiceLabel);
  return `${info.linePrefix}: ${text}`;
}

music.onPlaying = (track) => {
  musicPlaying = Boolean(track);
  setDiscordMusic(musicPlaying, track?.title || null);
  if (track) {
    // Don't let queued Luna TTS talk over the song.
    ttsQueue.length = 0;
    deferredReply = null;
    console.log(
      `[luna-discord] music on (${track.source || track.kind}) — quiet: ${track.title}`,
    );
  } else {
    console.log("[luna-discord] music off — Luna can talk again");
  }
};

/** Hard interrupt: stop music + clear her TTS queue. */
function interruptPlayback(reason = "stop") {
  music.stop();
  ttsQueue.length = 0;
  deferredReply = null;
  lunaSpeaking = false;
  ttsHoldUntil = 0;
  lastReportedTtsBusy = true; // force sync below
  syncTtsBusy().catch(() => {});
  console.log(`[luna-discord] interrupted (${reason})`);
}

async function handleMusicIntent(intent, meta = {}) {
  const guild = voiceGuildId ? client.guilds.cache.get(voiceGuildId) : null;
  if (!voiceGuildId || !getVoiceConnection(voiceGuildId)) {
    return { ok: false, message: "Not in a voice channel." };
  }

  if (intent.type === "stop") {
    interruptPlayback("voice-stop");
    return { ok: true, message: "Stopped.", announce: false };
  }
  if (intent.type === "skip") {
    music.skip();
    ttsQueue.length = 0;
    deferredReply = null;
    return { ok: true, message: "Skipped.", announce: false };
  }
  if (intent.type === "play") {
    try {
      const track = await music.enqueue(intent.query, intent.source || "auto");
      const where =
        track.source === "distrokid"
          ? "DistroKid"
          : track.source === "radio"
            ? "radio"
            : "YouTube";
      // Brief in-character confirm (wake already said).
      const confirm = await labelDiscordUtterance(
        meta.userId,
        `I asked you to play music. Confirm in ONE short sentence that you started ` +
          `"${track.title}" from ${where}. Do not ask follow-ups.`,
        guild,
        meta.voiceLabel || null,
      );
      return {
        ok: true,
        message: `Playing ${track.title} (${where})`,
        announce: confirm,
        track,
      };
    } catch (e) {
      return { ok: false, message: e.message, announce: false };
    }
  }
  return { ok: false, message: "Unknown music intent" };
}

function isTtsBusy() {
  return (
    lunaSpeaking ||
    ttsPumpRunning ||
    ttsQueue.length > 0 ||
    Date.now() < ttsHoldUntil
  );
}

function shouldIgnoreListen() {
  // Mute capture while she speaks (echo) and across short gaps between clips.
  return isTtsBusy();
}

function bumpTtsHold(ms = TTS_SETTLE_MS) {
  const until = Date.now() + Math.max(0, ms);
  if (until > ttsHoldUntil) ttsHoldUntil = until;
  syncTtsBusy().catch(() => {});
  if (ttsSettleTimer) clearTimeout(ttsSettleTimer);
  const wait = Math.max(0, ttsHoldUntil - Date.now()) + 50;
  ttsSettleTimer = setTimeout(() => {
    ttsSettleTimer = null;
    syncTtsBusy()
      .then(() => flushDeferredReply())
      .catch(() => {});
  }, wait);
}

async function syncTtsBusy() {
  const busy = isTtsBusy();
  if (busy === lastReportedTtsBusy) return;
  lastReportedTtsBusy = busy;
  try {
    const res = await fetch(`${RIKO_HTTP}/discord_tts_busy`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ busy }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
  } catch (e) {
    console.warn("[luna-discord] discord_tts_busy notify failed:", e.message);
  }
}

async function flushDeferredReply() {
  if (!deferredReply || isTtsBusy() || musicPlaying) return;
  const labeled = deferredReply;
  deferredReply = null;
  try {
    const result = await sendToRiko(labeled);
    console.log(`[luna-discord] deferred voice→riko v${result.version}: ${labeled}`);
  } catch (e) {
    console.warn("[luna-discord] deferred voice→riko failed:", e.message);
  }
}

async function setDiscordMusic(playing, title = null) {
  try {
    const res = await fetch(`${RIKO_HTTP}/discord_music`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ playing: Boolean(playing), title: title || null }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
  } catch (e) {
    console.warn("[luna-discord] discord_music notify failed:", e.message);
  }
}

async function playClip(filePath, durationHintSec = null) {
  if (!filePath || !existsSync(filePath)) {
    console.warn("[luna-discord] audio file missing:", filePath);
    return;
  }
  // Song wins — don't interrupt /luna play with talk clips.
  if (musicPlaying) {
    console.log("[luna-discord] skip TTS clip — music is playing");
    return;
  }
  const holdMs =
    (Number.isFinite(durationHintSec) && durationHintSec > 0
      ? durationHintSec * 1000
      : 3000) + TTS_SETTLE_MS;
  bumpTtsHold(holdMs);
  lunaSpeaking = true;
  await syncTtsBusy();
  try {
    const resource = createAudioResource(filePath, { inlineVolume: true });
    resource.volume?.setVolume(TTS_VOLUME);
    player.play(resource);
    await entersState(player, AudioPlayerStatus.Playing, 5_000).catch(() => null);
    await entersState(player, AudioPlayerStatus.Idle, 180_000).catch(() => null);
  } finally {
    lunaSpeaking = false;
    bumpTtsHold(TTS_SETTLE_MS);
    await syncTtsBusy();
  }
}

async function pumpTtsQueue() {
  if (ttsPumpRunning) return;
  ttsPumpRunning = true;
  await syncTtsBusy();
  try {
    while (ttsQueue.length) {
      if (musicPlaying) {
        console.log(`[luna-discord] clearing ${ttsQueue.length} TTS clip(s) — music playing`);
        ttsQueue.length = 0;
        break;
      }
      const snap = ttsQueue.shift();
      const conn = voiceGuildId ? getVoiceConnection(voiceGuildId) : null;
      if (!conn) {
        console.log(`[luna-discord] TTS v${snap.version} (VRM only — not in VC)`);
        continue;
      }
      console.log(`[luna-discord] TTS v${snap.version}: ${snap.audio_path} (left=${ttsQueue.length})`);
      const file = await downloadIfRemote(snap.audio_path);
      const dur = Number(snap.duration);
      await playClip(file, Number.isFinite(dur) ? dur : null);
      if (file && file.includes("luna-discord-audio")) unlink(file).catch(() => {});
    }
  } finally {
    ttsPumpRunning = false;
    bumpTtsHold(TTS_SETTLE_MS);
    await syncTtsBusy();
  }
}

async function pollDiscordAudio() {
  for (;;) {
    try {
      const res = await fetch(`${RIKO_HTTP}/pending_discord_audio`, {
        signal: AbortSignal.timeout(3000),
      });
      if (res.ok) {
        const snap = await res.json();
        // Server pops FIFO — every non-null path is the next sentence, in order.
        if (snap.audio_path) {
          lastAudioVersion = snap.version;
          const dur = Number(snap.duration);
          // Hold ears closed as soon as a clip is known — before download/play.
          bumpTtsHold(
            (Number.isFinite(dur) && dur > 0 ? dur * 1000 : 4000) + TTS_SETTLE_MS,
          );
          ttsQueue.push(snap);
          pumpTtsQueue().catch((e) => console.warn("[luna-discord] tts pump:", e.message));
          // Drain any backlog immediately (don't wait 200ms between pops).
          continue;
        }
      }
    } catch {
      /* riko restarting */
    }
    await new Promise((r) => setTimeout(r, 200));
  }
}

async function sendOverhear(payload) {
  try {
    const res = await fetch(`${RIKO_HTTP}/discord_overhear`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
  } catch (e) {
    console.warn("[luna-discord] overhear failed:", e.message);
  }
}

async function attachListen(connection) {
  if (!LISTEN_ENABLED) {
    console.log("[luna-discord] listen disabled (LUNA_DISCORD_LISTEN=0)");
    return;
  }
  try {
    await stt.start();
  } catch (e) {
    console.warn("[luna-discord] STT worker failed — join works but no listen:", e.message);
    return;
  }
  detachListen?.();
  detachListen = attachVoiceListen(connection, stt, {
    botUserId: client.user?.id,
    shouldIgnore: shouldIgnoreListen,
    onTranscript: async (text, meta = {}) => {
      const raw = String(text || "").trim();
      if (!raw) return;

      const guild = voiceGuildId ? client.guilds.cache.get(voiceGuildId) : null;
      const voice = meta.voice || null;
      const voiceLabel = voice?.label || null;
      const info = await formatDiscordSpeaker(meta.userId, guild, voiceLabel);

      // Learn this caller's pitch/timbre so Luna can tell voices apart.
      if (meta.userId && voice?.vector?.length) {
        learnVoiceprint(meta.userId, info.display, voice).catch(() => {});
      }

      // Always remember the call — even without a wake word (and during music).
      await sendOverhear({
        text: raw,
        user_id: meta.userId ? String(meta.userId) : null,
        display: info.display,
        role: info.role,
      });

      // Music / interrupt intents (play needs her name; stop/skip work during music).
      const intent = parseMusicIntent(raw, {
        musicPlaying,
        wakeWords: WAKE_WORDS,
      });
      const woken = shouldReplyToVoice(raw, meta.userId);
      const stopOrSkip = intent && (intent.type === "stop" || intent.type === "skip");
      const playCmd = intent && intent.type === "play";
      if (stopOrSkip && (musicPlaying || isTtsBusy() || woken)) {
        const result = await handleMusicIntent(intent, {
          userId: meta.userId,
          voiceLabel,
        });
        console.log(`[luna-discord] music intent ${intent.type}: ${result.message}`);
        return;
      }
      if (playCmd && woken) {
        const result = await handleMusicIntent(intent, {
          userId: meta.userId,
          voiceLabel,
        });
        console.log(`[luna-discord] music intent play: ${result.message}`);
        if (result.ok && result.announce) {
          try {
            await sendToRiko(result.announce);
          } catch (e) {
            console.warn("[luna-discord] play announce failed:", e.message);
          }
        } else if (!result.ok) {
          try {
            const fail = await labelDiscordUtterance(
              meta.userId,
              `Music play failed: ${result.message}. Apologize in one short sentence.`,
              guild,
              voiceLabel,
            );
            await sendToRiko(fail);
          } catch {}
        }
        return;
      }

      if (musicPlaying) {
        console.log(
          `[luna-discord] overhear (music, no reply): ${info.display}` +
            `${voiceLabel ? ` [${voiceLabel}]` : ""}: ${raw}`,
        );
        return;
      }

      if (!woken) {
        console.log(
          `[luna-discord] overhear (no wake): ${info.display}` +
            `${voiceLabel ? ` [${voiceLabel}]` : ""}: ${raw}`,
        );
        return;
      }

      try {
        const spoken = stripWakeWord(raw);
        const labeled = await labelDiscordUtterance(
          meta.userId,
          spoken,
          guild,
          voiceLabel,
        );
        // Don't start a new reply while VC TTS is still playing — that cuts her off.
        if (isTtsBusy()) {
          deferredReply = labeled;
          console.log(`[luna-discord] defer wake until TTS idle: ${labeled}`);
          return;
        }
        const result = await sendToRiko(labeled);
        console.log(`[luna-discord] voice→riko v${result.version}: ${labeled}`);
      } catch (e) {
        console.warn("[luna-discord] voice→riko failed:", e.message);
      }
    },
  });
}

function joinMemberVoice(interaction) {
  const member = interaction.member;
  const channel = member?.voice?.channel;
  if (
    !channel ||
    (channel.type !== ChannelType.GuildVoice && channel.type !== ChannelType.GuildStageVoice)
  ) {
    return null;
  }
  const connection = joinVoiceChannel({
    channelId: channel.id,
    guildId: channel.guild.id,
    adapterCreator: channel.guild.voiceAdapterCreator,
    selfDeaf: false,
    selfMute: false,
    // Discord requires DAVE E2EE for VC (March 2026+). Needs @snazzah/davey.
    daveEncryption: true,
    decryptionFailureTolerance: 24,
  });
  connection.on("error", (e) => console.warn("[luna-discord] voice error:", e.message));
  connection.on("stateChange", async (_old, state) => {
    if (
      state.status === VoiceConnectionStatus.Destroyed ||
      state.status === VoiceConnectionStatus.Disconnected
    ) {
      if (voiceGuildId === channel.guild.id) {
        voiceGuildId = null;
        vcHostUserId = null;
        detachListen?.();
        detachListen = null;
        await setDiscordVcActive(false);
        console.log("[luna-discord] voice connection lost — website audio restored");
      }
    }
  });
  connection.subscribe(player);
  voiceGuildId = channel.guild.id;
  return { channel, connection };
}

const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildVoiceStates,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.MessageContent,
  ],
  partials: [Partials.Channel],
});

client.once("ready", () => {
  console.log(`[luna-discord] logged in as ${client.user.tag}`);
  console.log(`[luna-discord] Riko → ${RIKO_HTTP} · listen=${LISTEN_ENABLED ? "on" : "off"}`);
  const report = generateDependencyReport();
  const hasDave = /@snazzah\/davey:\s*(?!not found)\S+/i.test(report);
  console.log(`[luna-discord] DAVE: ${hasDave ? "ready (@snazzah/davey)" : "MISSING — cannot hear VC"}`);
  if (!hasDave) console.log(report);
  pollDiscordAudio();
});

client.on("interactionCreate", async (interaction) => {
  if (!interaction.isChatInputCommand() || interaction.commandName !== "luna") return;
  const sub = interaction.options.getSubcommand();

  try {
    if (sub === "join") {
      // ACK within 3s — Whisper warmup can be slow.
      await interaction.deferReply({ ephemeral: true });
      const joined = joinMemberVoice(interaction);
      if (!joined) {
        await interaction.editReply("Join a voice channel first (you must be in VC).");
        return;
      }
      vcHostUserId = interaction.user.id;
      await entersState(joined.connection, VoiceConnectionStatus.Ready, 20_000).catch(() => null);
      await setDiscordVcActive(true);
      await attachListen(joined.connection);
      const hostTag = await formatDiscordSpeaker(vcHostUserId, interaction.guild);
      await interaction.editReply(
        `Joined **${joined.channel.name}**. I only talk in this call now (website silent). ` +
          `You are tagged as **owner** (${hostTag.tag} ${hostTag.display}). ` +
          (WAKE_REQUIRED
            ? `I transcribe the call quietly; say **${WAKE_WORDS[0] || "luna"}** when you want me to talk. `
            : "") +
          `Talk, then pause ~1s. Music: \`/luna play\`.`,
      );
      return;
    }

    if (sub === "leave") {
      detachListen?.();
      detachListen = null;
      music.stop();
      const conn = getVoiceConnection(interaction.guildId);
      if (conn) conn.destroy();
      voiceGuildId = null;
      vcHostUserId = null;
      await setDiscordVcActive(false);
      await interaction.reply({ content: "Left voice. Website audio back on.", ephemeral: true });
      return;
    }

    if (sub === "say") {
      const text = interaction.options.getString("text", true).trim();
      const inVc = Boolean(voiceGuildId && getVoiceConnection(voiceGuildId));
      if (inVc) {
        await interaction.deferReply({ ephemeral: true });
        const labeled = await labelDiscordUtterance(interaction.user.id, text, interaction.guild);
        const result = await sendToRiko(labeled, { source: "discord" });
        await interaction.editReply(`Sent to Luna (v${result.version}).`);
        return;
      }
      await interaction.deferReply({ ephemeral: false });
      try {
        const labeled = await labelDiscordUtterance(interaction.user.id, text, interaction.guild);
        await postChatReply(interaction, labeled);
      } catch (e) {
        await interaction.editReply(`Couldn't get Luna's reply: ${e.message}`);
      }
      return;
    }

    if (sub === "play") {
      const query = interaction.options.getString("query", true).trim();
      const source = interaction.options.getString("source") || "auto";
      if (!voiceGuildId || !getVoiceConnection(interaction.guildId)) {
        await interaction.reply({
          content: "I'm not in a voice channel. Use `/luna join` first.",
          ephemeral: true,
        });
        return;
      }
      await interaction.deferReply();
      try {
        const track = await music.enqueue(query, source);
        await interaction.editReply(
          `Playing **${track.title}** (${track.source || source}). ` +
            `Say **Luna stop** (or \`/luna stop\`) to cut it.`,
        );
      } catch (e) {
        await interaction.editReply(`Couldn't play that: ${e.message}`);
      }
      return;
    }

    if (sub === "radio") {
      const station = interaction.options.getString("station", true).trim();
      if (!voiceGuildId || !getVoiceConnection(interaction.guildId)) {
        await interaction.reply({
          content: "I'm not in a voice channel. Use `/luna join` first.",
          ephemeral: true,
        });
        return;
      }
      await interaction.deferReply();
      try {
        const track = await music.enqueue(station, "radio");
        await interaction.editReply(
          `Radio **${track.title}**. Say **Luna stop** to cut it. ` +
            `Stations: ${listRadioStationNames().slice(0, 8).join(", ") || "(edit radio-stations.json)"}`,
        );
      } catch (e) {
        await interaction.editReply(`Couldn't start radio: ${e.message}`);
      }
      return;
    }

    if (sub === "skip") {
      music.skip();
      await interaction.reply({ content: "Skipped.", ephemeral: true });
      return;
    }

    if (sub === "stop") {
      interruptPlayback("slash-stop");
      await interaction.reply({ content: "Music stopped.", ephemeral: true });
      return;
    }

    if (sub === "status") {
      await interaction.deferReply({ ephemeral: true });
      const st = music.status();
      let riko = "unreachable";
      try {
        const h = await rikoHealth();
        riko = `character=\`${h.character}\` ws=${h.ws_clients ?? "?"}`;
      } catch (e) {
        riko = e.message;
      }
      await interaction.editReply(
        [
          `Riko: ${riko}`,
          `VC: ${voiceGuildId ? "joined" : "not in voice"}`,
          `Listen: ${LISTEN_ENABLED ? "on" : "off"} (hears call → answers in call)`,
          `Library: ${musicPaths.distrokid}`,
          `Radio stations: ${st.radio?.slice(0, 10).join(", ") || "(none)"}`,
          `Now: ${st.current ? `${st.current.title} [${st.current.source || "?"}]` : "(nothing)"}`,
          `Queue: ${st.queued.length ? st.queued.join(" · ") : "(empty)"}`,
        ].join("\n"),
      );
    }
  } catch (e) {
    console.error("[luna-discord] command error:", e);
    const msg = e?.message || String(e);
    if (interaction.deferred || interaction.replied) {
      await interaction.editReply(`Error: ${msg}`).catch(() => {});
    } else {
      await interaction.reply({ content: `Error: ${msg}`, ephemeral: true }).catch(() => {});
    }
  }
});

client.on("messageCreate", async (message) => {
  if (message.author.bot) return;
  if (TEXT_CHANNEL_ID && message.channelId !== TEXT_CHANNEL_ID) return;
  if (GUILD_ID && message.guildId !== GUILD_ID) return;
  if (!message.guild) return;

  const content = message.content.trim();
  const mentioned = message.mentions.has(client.user);
  const addressed = /^luna[,:]?\s+/i.test(content);
  if (!mentioned && !addressed) return;

  const text = content
    .replace(new RegExp(`<@!?${client.user.id}>`, "g"), "")
    .replace(/^luna[,:]?\s+/i, "")
    .trim();
  if (!text) return;

  const inVc = Boolean(voiceGuildId && getVoiceConnection(voiceGuildId));
  try {
    await message.channel.sendTyping();
    const labeled = await labelDiscordUtterance(message.author.id, text, message.guild);
    if (inVc) {
      const result = await sendToRiko(labeled, { source: "discord" });
      await message.react("💬").catch(() => {});
      console.log(`[luna-discord] text→riko (VC) v${result.version}: ${labeled}`);
      return;
    }
    await postChatReply(message, labeled);
  } catch (e) {
    await message.reply(`Couldn't reach Luna: ${e.message}`).catch(() => {});
  }
});

await registerCommands();
if (OWNER_USER_IDS.size) {
  console.log(`[luna-discord] owner id(s): ${[...OWNER_USER_IDS].join(", ")}`);
} else {
  console.log("[luna-discord] no DISCORD_OWNER_USER_IDS set — /luna join host is treated as owner");
}
console.log(
  `[luna-discord] wake: ${WAKE_REQUIRED ? "on" : "off"} words=[${WAKE_WORDS.join(" | ")}] ` +
    `followup=${WAKE_FOLLOWUP_MS}ms · tts_volume=${TTS_VOLUME}`,
);
await client.login(TOKEN);
