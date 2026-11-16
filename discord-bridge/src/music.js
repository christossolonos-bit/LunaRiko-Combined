/**
 * Music queue for Discord VC (shared AudioPlayer with TTS).
 *
 * Sources:
 *   - YouTube (yt-dlp download → ffmpeg PCM)
 *   - DistroKid / local library (ffmpeg from file)
 *   - Radio streams (ffmpeg from URL, until stop)
 */

import { spawn, spawnSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { join, basename, extname, dirname } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { mkdtemp } from "node:fs/promises";
import {
  createAudioResource,
  StreamType,
  AudioPlayerStatus,
} from "@discordjs/voice";

const __dirname = dirname(fileURLToPath(import.meta.url));
const BRIDGE_ROOT = join(__dirname, "..");

const YTDLP = process.env.YTDLP_BINARY || "yt-dlp";
const FFMPEG = process.env.FFMPEG_BINARY || resolveOnPath("ffmpeg") || "ffmpeg";
const DISTROKID_DIR = (process.env.LUNA_DISTROKID_DIR || "D:\\Music for distrokid").trim();
const RADIO_FILE = (
  process.env.LUNA_RADIO_STATIONS_FILE || join(BRIDGE_ROOT, "radio-stations.json")
).trim();

const AUDIO_EXT = new Set([".mp3", ".wav", ".flac", ".m4a", ".ogg", ".opus", ".aac", ".wma"]);
const SKIP_PATH_RE = /lyra-refs|voice_ref|node_modules|\.git/i;

function resolveOnPath(cmd) {
  try {
    const out = spawnSync(process.platform === "win32" ? "where" : "which", [cmd], {
      encoding: "utf8",
      windowsHide: true,
    });
    const line = (out.stdout || "").split(/\r?\n/).map((s) => s.trim()).find(Boolean);
    return line || null;
  } catch {
    return null;
  }
}

function run(cmd, args, timeoutMs = 120_000) {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, { stdio: ["ignore", "pipe", "pipe"], windowsHide: true });
    let stdout = "";
    let stderr = "";
    const t = setTimeout(() => {
      child.kill();
      reject(new Error(`${cmd} timed out`));
    }, timeoutMs);
    child.stdout.on("data", (c) => {
      stdout += c.toString("utf8");
    });
    child.stderr.on("data", (c) => {
      stderr += c.toString("utf8");
    });
    child.on("error", (e) => {
      clearTimeout(t);
      reject(e);
    });
    child.on("close", (code) => {
      clearTimeout(t);
      if (code) reject(new Error(stderr.trim().slice(-500) || `${cmd} exit ${code}`));
      else resolve(stdout);
    });
  });
}

function normalizeQuery(s) {
  return String(s || "")
    .toLowerCase()
    .replace(/[^\w\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function scoreMatch(query, filePath) {
  const q = normalizeQuery(query);
  if (!q) return 0;
  const name = normalizeQuery(basename(filePath, extname(filePath)));
  const parent = normalizeQuery(basename(dirname(filePath)));
  const hay = `${name} ${parent}`;
  if (name === q) return 100;
  if (name.includes(q)) return 80 + Math.min(15, q.length);
  if (hay.includes(q)) return 60;
  const tokens = q.split(" ").filter((t) => t.length > 1);
  if (!tokens.length) return 0;
  const hits = tokens.filter((t) => hay.includes(t)).length;
  return (hits / tokens.length) * 50;
}

let _libraryCache = null;
let _libraryCacheAt = 0;

function walkAudioFiles(root, out = [], depth = 0) {
  if (depth > 8 || out.length > 5000) return out;
  let entries = [];
  try {
    entries = readdirSync(root, { withFileTypes: true });
  } catch {
    return out;
  }
  for (const ent of entries) {
    const full = join(root, ent.name);
    if (SKIP_PATH_RE.test(full)) continue;
    if (ent.isDirectory()) walkAudioFiles(full, out, depth + 1);
    else if (AUDIO_EXT.has(extname(ent.name).toLowerCase())) out.push(full);
  }
  return out;
}

export function listLibraryTracks(force = false) {
  if (!DISTROKID_DIR || !existsSync(DISTROKID_DIR)) return [];
  const now = Date.now();
  if (!force && _libraryCache && now - _libraryCacheAt < 60_000) return _libraryCache;
  _libraryCache = walkAudioFiles(DISTROKID_DIR);
  _libraryCacheAt = now;
  console.log(`[music] library indexed ${ _libraryCache.length} files from ${DISTROKID_DIR}`);
  return _libraryCache;
}

export function searchLibrary(query, limit = 5) {
  const files = listLibraryTracks();
  const scored = files
    .map((file) => ({ file, score: scoreMatch(query, file), title: basename(file, extname(file)) }))
    .filter((x) => x.score >= 35)
    .sort((a, b) => b.score - a.score);
  return scored.slice(0, limit);
}

export function loadRadioStations() {
  try {
    if (!existsSync(RADIO_FILE)) return {};
    const raw = JSON.parse(readFileSync(RADIO_FILE, "utf8"));
    const stations = raw.stations || raw;
    const out = {};
    for (const [k, v] of Object.entries(stations)) {
      if (k.startsWith("_")) continue;
      if (typeof v === "string" && /^https?:\/\//i.test(v)) {
        out[normalizeQuery(k)] = { name: k, url: v };
      }
    }
    return out;
  } catch (e) {
    console.warn("[music] radio stations load failed:", e.message);
    return {};
  }
}

export function findRadioStation(query) {
  const stations = loadRadioStations();
  const q = normalizeQuery(query);
  if (!q) return null;
  if (stations[q]) return stations[q];
  // Partial / contains match
  let best = null;
  let bestScore = 0;
  for (const [key, st] of Object.entries(stations)) {
    if (key === q) return st;
    if (key.includes(q) || q.includes(key)) {
      const score = Math.min(key.length, q.length);
      if (score > bestScore) {
        bestScore = score;
        best = st;
      }
    }
  }
  return best;
}

export function listRadioStationNames() {
  return Object.values(loadRadioStations()).map((s) => s.name);
}

async function resolveYoutubeTrack(query) {
  const q = query.trim();
  if (/^https?:\/\//i.test(q)) {
    const out = await run(YTDLP, [
      "-f", "bestaudio/best",
      "--dump-json",
      "--no-playlist",
      "--no-warnings",
      q,
    ], 60_000);
    const info = JSON.parse(out.split("\n").find((l) => l.startsWith("{")) || out);
    return {
      title: info.title || q,
      url: info.webpage_url || info.original_url || q,
      id: info.id,
      source: "youtube",
      kind: "youtube",
    };
  }
  const out = await run(YTDLP, [
    "--flat-playlist",
    "--dump-single-json",
    "--no-warnings",
    `ytsearch1:${q}`,
  ], 60_000);
  const info = JSON.parse(out);
  const entry = info.entries?.[0] || info;
  const id = entry.id;
  const url = entry.url?.startsWith("http")
    ? entry.url
    : `https://www.youtube.com/watch?v=${id}`;
  return { title: entry.title || q, url, id, source: "youtube", kind: "youtube" };
}

async function downloadAudio(url) {
  const dir = await mkdtemp(join(tmpdir(), "luna-music-"));
  const outTpl = join(dir, "audio.%(ext)s");
  await run(YTDLP, [
    "-f", "bestaudio/best",
    "--extractor-args", "youtube:player_client=android,web",
    "-o", outTpl,
    "--no-playlist",
    "--no-warnings",
    "--no-mtime",
    url,
  ], 180_000);
  const files = readdirSync(dir).map((f) => join(dir, f));
  const audio = files.find((f) => /\.(m4a|mp3|mp4|webm|opus|ogg|wav|flac)$/i.test(f));
  if (!audio || !existsSync(audio)) {
    throw new Error(`yt-dlp produced no audio file in ${dir}: ${files.join(", ") || "(empty)"}`);
  }
  console.log(`[music] downloaded ${audio} (ffmpeg=${FFMPEG})`);
  return { dir, file: audio };
}

function createPcmResourceFromInput(inputArgs) {
  const ff = spawn(
    FFMPEG,
    [
      "-hide_banner",
      "-loglevel", "error",
      ...inputArgs,
      "-f", "s16le",
      "-ar", "48000",
      "-ac", "2",
      "pipe:1",
    ],
    { stdio: ["ignore", "pipe", "pipe"], windowsHide: true },
  );
  ff.stderr?.on("data", (c) => {
    const t = c.toString("utf8").trim();
    if (t) console.warn("[music/ffmpeg]", t.slice(0, 300));
  });
  ff.on("error", (e) => console.warn("[music/ffmpeg] spawn:", e.message));

  const resource = createAudioResource(ff.stdout, {
    inputType: StreamType.Raw,
    inlineVolume: true,
    metadata: { ffmpeg: ff },
  });
  resource.volume?.setVolume(1.0);
  return { resource, ffmpeg: ff };
}

/**
 * Resolve a play request into a queue track.
 * source: "auto" | "youtube" | "distrokid" | "radio"
 */
export async function resolvePlayRequest(query, source = "auto") {
  const q = String(query || "").trim();
  if (!q) throw new Error("Empty play query");
  const src = String(source || "auto").toLowerCase();

  if (src === "radio" || (src === "auto" && /^radio\b/i.test(q))) {
    const name = q.replace(/^radio\s+/i, "").trim() || q;
    const st = findRadioStation(name);
    if (!st) {
      const names = listRadioStationNames().slice(0, 12).join(", ") || "(none configured)";
      throw new Error(`Unknown radio station "${name}". Try: ${names}`);
    }
    return {
      title: `Radio: ${st.name}`,
      url: st.url,
      source: "radio",
      kind: "radio",
      file: null,
    };
  }

  if (src === "radio") {
    const st = findRadioStation(q);
    if (!st) throw new Error(`Unknown radio station "${q}"`);
    return { title: `Radio: ${st.name}`, url: st.url, source: "radio", kind: "radio", file: null };
  }

  if (src === "distrokid" || src === "library" || src === "local") {
    const hits = searchLibrary(q);
    if (!hits.length) throw new Error(`No DistroKid match for "${q}" in ${DISTROKID_DIR}`);
    const hit = hits[0];
    return {
      title: hit.title,
      url: hit.file,
      file: hit.file,
      source: "distrokid",
      kind: "local",
      score: hit.score,
    };
  }

  if (src === "youtube" || /^https?:\/\//i.test(q)) {
    return resolveYoutubeTrack(q);
  }

  // auto: radio name exact-ish → else strong local match → else YouTube
  const radio = findRadioStation(q);
  if (radio && normalizeQuery(radio.name) === normalizeQuery(q)) {
    return { title: `Radio: ${radio.name}`, url: radio.url, source: "radio", kind: "radio", file: null };
  }
  const local = searchLibrary(q);
  if (local[0] && local[0].score >= 70) {
    return {
      title: local[0].title,
      url: local[0].file,
      file: local[0].file,
      source: "distrokid",
      kind: "local",
      score: local[0].score,
    };
  }
  return resolveYoutubeTrack(q);
}

/**
 * Music queue on a shared AudioPlayer.
 */
export class MusicQueue {
  constructor(player) {
    this.player = player;
    this.queue = [];
    this.current = null;
    this.token = 0;
    this.busy = false;
    this.onPlaying = null; // (track|null) => void
    this._ffmpeg = null;

    player.on("error", (err) => {
      console.warn("[music] player error:", err.message);
    });
  }

  status() {
    return {
      current: this.current,
      queued: this.queue.map((t) => t.title),
      library: DISTROKID_DIR,
      radio: listRadioStationNames(),
    };
  }

  async enqueue(query, source = "auto") {
    const track = await resolvePlayRequest(query, source);
    this.queue.push(track);
    if (!this.busy) this._pump().catch((e) => console.warn("[music]", e.message));
    return track;
  }

  skip() {
    this.token += 1;
    this._killFfmpeg();
    try {
      this.player.stop(true);
    } catch {}
  }

  stop() {
    this.queue = [];
    this.current = null;
    this.token += 1;
    this._killFfmpeg();
    try {
      this.player.stop(true);
    } catch {}
    this.busy = false;
    this.onPlaying?.(null);
  }

  _killFfmpeg() {
    if (this._ffmpeg) {
      try {
        this._ffmpeg.kill("SIGKILL");
      } catch {}
      this._ffmpeg = null;
    }
  }

  async _pump() {
    if (this.busy) return;
    this.busy = true;
    while (this.queue.length) {
      const track = this.queue.shift();
      this.current = track;
      this.onPlaying?.(track);
      const myToken = ++this.token;
      let downloaded = null;
      try {
        let inputArgs;
        if (track.kind === "radio") {
          console.log(`[music] radio stream: ${track.title} → ${track.url}`);
          inputArgs = ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5", "-i", track.url];
        } else if (track.kind === "local" || track.file) {
          const file = track.file || track.url;
          if (!existsSync(file)) throw new Error(`Missing file: ${file}`);
          console.log(`[music] local file: ${track.title}`);
          inputArgs = ["-i", file];
        } else {
          console.log(`[music] downloading: ${track.title}`);
          downloaded = await downloadAudio(track.url);
          if (myToken !== this.token) continue;
          inputArgs = ["-i", downloaded.file];
        }

        if (myToken !== this.token) continue;

        const { resource, ffmpeg } = createPcmResourceFromInput(inputArgs);
        this._ffmpeg = ffmpeg;
        this.player.play(resource);

        await new Promise((resolve, reject) => {
          const t = setTimeout(() => reject(new Error("Did not start playing")), 20_000);
          const onPlaying = () => {
            clearTimeout(t);
            this.player.off(AudioPlayerStatus.Playing, onPlaying);
            this.player.off("error", onErr);
            resolve();
          };
          const onErr = (e) => {
            clearTimeout(t);
            this.player.off(AudioPlayerStatus.Playing, onPlaying);
            this.player.off("error", onErr);
            reject(e);
          };
          if (this.player.state.status === AudioPlayerStatus.Playing) {
            clearTimeout(t);
            resolve();
            return;
          }
          this.player.once(AudioPlayerStatus.Playing, onPlaying);
          this.player.once("error", onErr);
        });
        console.log(`[music] playing in VC: ${track.title} (${track.source || track.kind})`);

        // Wait until track ends, errors, or stop/skip bumps the token.
        await new Promise((resolve) => {
          let done = false;
          const finish = () => {
            if (done) return;
            done = true;
            clearInterval(iv);
            this.player.off(AudioPlayerStatus.Idle, onIdle);
            this.player.off("error", onErr);
            resolve();
          };
          const onIdle = () => finish();
          const onErr = () => finish();
          this.player.once(AudioPlayerStatus.Idle, onIdle);
          this.player.once("error", onErr);
          const iv = setInterval(() => {
            if (myToken !== this.token) finish();
          }, 250);
        });
      } catch (e) {
        console.warn(`[music] failed ${track.title}:`, e.message);
      } finally {
        this._killFfmpeg();
        if (downloaded?.dir) {
          try {
            rmSync(downloaded.dir, { recursive: true, force: true });
          } catch {}
        }
      }
      if (myToken !== this.token && !this.queue.length) break;
    }
    this.current = null;
    this.busy = false;
    this.onPlaying?.(null);
  }
}

export const musicPaths = {
  distrokid: DISTROKID_DIR,
  radioFile: RADIO_FILE,
  ffmpeg: FFMPEG,
  ytdlp: YTDLP,
};
