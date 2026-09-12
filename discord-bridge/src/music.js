/**
 * Music queue for Discord VC (shared AudioPlayer with TTS).
 *
 * yt-dlp downloads → ffmpeg remuxes to 48k stereo PCM → @discordjs/voice.
 * Passing .m4a paths straight into createAudioResource often goes Idle
 * immediately on Windows (no audible frames) even though the track "loaded".
 */

import { spawn, spawnSync } from "node:child_process";
import { existsSync, readdirSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { mkdtemp } from "node:fs/promises";
import {
  createAudioResource,
  StreamType,
  AudioPlayerStatus,
  entersState,
} from "@discordjs/voice";

const YTDLP = process.env.YTDLP_BINARY || "yt-dlp";
const FFMPEG = process.env.FFMPEG_BINARY || resolveOnPath("ffmpeg") || "ffmpeg";

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

async function resolveTrack(query) {
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
  return { title: entry.title || q, url, id };
}

async function downloadAudio(url) {
  const dir = await mkdtemp(join(tmpdir(), "luna-music-"));
  // Download raw bestaudio — do NOT ask yt-dlp to remux (-x). That needs
  // ffmpeg on yt-dlp's PATH and often fails on Windows even when ffmpeg exists.
  // We always re-encode ourselves below for Discord PCM.
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

/**
 * Discord wants 48 kHz stereo PCM (or opus). Remux anything yt-dlp gave us.
 */
function createPcmResource(filePath) {
  const ff = spawn(
    FFMPEG,
    [
      "-hide_banner",
      "-loglevel", "error",
      "-i", filePath,
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
    };
  }

  async enqueue(query) {
    const track = await resolveTrack(query);
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
        console.log(`[music] downloading: ${track.title}`);
        downloaded = await downloadAudio(track.url);
        if (myToken !== this.token) continue;

        const { resource, ffmpeg } = createPcmResource(downloaded.file);
        this._ffmpeg = ffmpeg;
        this.player.play(resource);

        await entersState(this.player, AudioPlayerStatus.Playing, 15_000);
        console.log(`[music] playing in VC: ${track.title}`);

        await new Promise((resolve) => {
          const onIdle = () => {
            this.player.off(AudioPlayerStatus.Idle, onIdle);
            this.player.off("error", onErr);
            resolve();
          };
          const onErr = () => {
            this.player.off(AudioPlayerStatus.Idle, onIdle);
            this.player.off("error", onErr);
            resolve();
          };
          this.player.once(AudioPlayerStatus.Idle, onIdle);
          this.player.once("error", onErr);
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
