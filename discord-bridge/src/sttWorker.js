import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { existsSync } from "node:fs";

const HERE = dirname(fileURLToPath(import.meta.url));
const PROJECT = resolve(HERE, "..", "..");

function defaultPython() {
  const venvPy = resolve(PROJECT, ".venv", "Scripts", "python.exe");
  if (existsSync(venvPy)) return venvPy;
  return process.env.LOCAL_VOICE_PYTHON || "python";
}

/**
 * Long-lived faster-whisper worker (JSONL).
 */
export class SttWorker {
  constructor(opts = {}) {
    this.python = opts.python || defaultPython();
    this.script = opts.script || resolve(HERE, "..", "scripts", "transcribe_worker.py");
    this.model = opts.model || process.env.WHISPER_MODEL || "base";
    this.device = opts.device || "cpu";
    this.compute = opts.compute || "int8";
    this.language = opts.language || process.env.WHISPER_LANGUAGE || null;
    this.child = null;
    this.pending = new Map();
    this.ready = null;
    this.buf = "";
  }

  async start() {
    if (this.child) return;
    const cfg = JSON.stringify({
      model: this.model,
      device: this.device,
      compute_type: this.compute,
      language: this.language,
    });
    this.ready = new Promise((resolveReady, rejectReady) => {
      this._resolveReady = resolveReady;
      this._rejectReady = rejectReady;
      setTimeout(() => rejectReady(new Error("STT worker ready timeout")), 180_000).unref?.();
    });
    this.child = spawn(this.python, [this.script, cfg], {
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
    });
    this.child.stdout.setEncoding("utf8");
    this.child.stdout.on("data", (chunk) => this._onData(chunk));
    this.child.stderr.setEncoding("utf8");
    this.child.stderr.on("data", (chunk) => {
      const t = chunk.trim();
      if (t) console.warn("[stt]", t.slice(0, 400));
    });
    this.child.on("exit", (code) => {
      console.warn(`[stt] worker exited code=${code}`);
      for (const [, p] of this.pending) p.reject(new Error("STT worker exited"));
      this.pending.clear();
      this.child = null;
    });
    await this.ready;
    console.log(`[stt] ready model=${this.model} via ${this.python}`);
  }

  _onData(chunk) {
    this.buf += chunk;
    let idx;
    while ((idx = this.buf.indexOf("\n")) >= 0) {
      const line = this.buf.slice(0, idx).trim();
      this.buf = this.buf.slice(idx + 1);
      if (!line) continue;
      let msg;
      try {
        msg = JSON.parse(line);
      } catch {
        continue;
      }
      if (msg.type === "ready") {
        this._resolveReady?.();
        continue;
      }
      const id = msg.id;
      const pending = id ? this.pending.get(id) : null;
      if (!pending) continue;
      this.pending.delete(id);
      if (msg.type === "error") pending.reject(new Error(msg.error || "stt error"));
      else pending.resolve(msg.text || "");
    }
  }

  async transcribe(wavPath) {
    await this.start();
    const id = randomUUID();
    const result = new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error("STT timeout"));
        }
      }, 90_000).unref?.();
    });
    this.child.stdin.write(JSON.stringify({ id, op: "stt", wav: wavPath }) + "\n");
    return result;
  }

  stop() {
    try {
      this.child?.kill();
    } catch {}
    this.child = null;
  }
}
