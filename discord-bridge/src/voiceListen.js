/**
 * Discord VC receive → Whisper STT.
 *
 * Requires @discordjs/voice >= 0.19 + @snazzah/davey (Discord DAVE E2EE,
 * mandatory since March 2026). Without DAVE, the bot joins but never hears.
 */

import { mkdirSync, writeFileSync } from "node:fs";
import { unlink } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { EndBehaviorType } from "@discordjs/voice";
import OpusScript from "opusscript";

const DISCORD_RATE = 48_000;
const DISCORD_CHANNELS = 2;
const STT_RATE = 16_000;
const AFTER_SILENCE_MS = Number(process.env.LUNA_SPEECH_END_SILENCE_MS || 1200);
const MIN_MS = Number(process.env.LUNA_SPEECH_MIN_MS || 400);

function downsample48kStereoTo16kMono(input) {
  const completeFrames = Math.floor(input.length / (DISCORD_CHANNELS * 2));
  const outputFrames = Math.floor((completeFrames * STT_RATE) / DISCORD_RATE);
  const output = Buffer.allocUnsafe(outputFrames * 2);
  for (let frame = 0; frame < outputFrames; frame += 1) {
    const startFrame = Math.floor((frame * DISCORD_RATE) / STT_RATE);
    const endFrame = Math.min(
      completeFrames,
      Math.floor(((frame + 1) * DISCORD_RATE) / STT_RATE),
    );
    let sum = 0;
    let count = 0;
    for (let i = startFrame; i < endFrame; i += 1) {
      const offset = i * DISCORD_CHANNELS * 2;
      sum += input.readInt16LE(offset);
      sum += input.readInt16LE(offset + 2);
      count += 2;
    }
    const sample = Math.max(-32768, Math.min(32767, Math.round(sum / Math.max(count, 1))));
    output.writeInt16LE(sample, frame * 2);
  }
  return output;
}

function writeWav16kMono(pcm, outPath) {
  const dataSize = pcm.length;
  const header = Buffer.alloc(44);
  header.write("RIFF", 0);
  header.writeUInt32LE(36 + dataSize, 4);
  header.write("WAVE", 8);
  header.write("fmt ", 12);
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);
  header.writeUInt16LE(1, 22);
  header.writeUInt32LE(STT_RATE, 24);
  header.writeUInt32LE(STT_RATE * 2, 28);
  header.writeUInt16LE(2, 32);
  header.writeUInt16LE(16, 34);
  header.write("data", 36);
  header.writeUInt32LE(dataSize, 40);
  writeFileSync(outPath, Buffer.concat([header, pcm]));
}

/**
 * Attach Discord voice receive → STT → onTranscript(text, meta).
 */
export function attachVoiceListen(connection, sttWorker, {
  onTranscript,
  shouldIgnore = () => false,
  botUserId = null,
} = {}) {
  const active = new Set();
  const dir = join(tmpdir(), "luna-discord-stt");
  mkdirSync(dir, { recursive: true });

  console.log(
    `[listen] attached (afterSilence=${AFTER_SILENCE_MS}ms). Speak, then pause.`,
  );

  const speaking = connection.receiver.speaking;
  const onStart = (userId) => {
    if (botUserId && userId === botUserId) return;
    if (active.has(userId)) return;
    if (shouldIgnore()) {
      console.log(`[listen] speaking ignored (Luna TTS playing) user=${userId}`);
      return;
    }
    console.log(`[listen] speaking start user=${userId}`);
    captureUtterance(userId).catch((e) => {
      console.warn(`[listen] capture failed user=${userId}:`, e.message);
    });
  };
  speaking.on("start", onStart);

  async function captureUtterance(userId) {
    active.add(userId);
    const opus = new OpusScript(DISCORD_RATE, DISCORD_CHANNELS);
    const stream = connection.receiver.subscribe(userId, {
      end: {
        behavior: EndBehaviorType.AfterSilence,
        duration: AFTER_SILENCE_MS,
      },
    });

    const pcmParts = [];
    let packets = 0;
    let decodeFail = 0;

    try {
      await new Promise((resolve, reject) => {
        stream.on("data", (packet) => {
          packets += 1;
          try {
            const decoded = Buffer.from(opus.decode(packet));
            pcmParts.push(downsample48kStereoTo16kMono(decoded));
          } catch {
            decodeFail += 1;
          }
        });
        stream.once("end", resolve);
        stream.once("close", resolve);
        stream.once("error", reject);
      });

      console.log(
        `[listen] utterance end user=${userId} packets=${packets} decodeFail=${decodeFail}`,
      );

      // Still transcribe if capture was already running when TTS started —
      // the bridge will defer the reply until she finishes speaking.
      if (!pcmParts.length) {
        if (packets > 0 && decodeFail === packets) {
          console.warn(
            "[listen] got packets but opus decode failed — check DAVE / opusscript",
          );
        } else if (packets === 0) {
          console.warn(
            "[listen] zero packets — DAVE decrypt may have failed; rejoin with /luna leave then /luna join",
          );
        }
        return;
      }

      const pcm = Buffer.concat(pcmParts);
      const durationMs = (pcm.length / 2 / STT_RATE) * 1000;
      if (durationMs < MIN_MS) {
        console.log(`[listen] too short (${durationMs.toFixed(0)}ms), skip`);
        return;
      }

      const wav = join(dir, `u-${userId}-${Date.now()}.wav`);
      try {
        writeWav16kMono(pcm, wav);
        const text = await sttWorker.transcribe(wav);
        if (text && text.trim()) {
          console.log(`[listen] ${userId}: ${text}`);
          await onTranscript(text.trim(), { userId });
        } else {
          console.log(`[listen] empty transcript user=${userId}`);
        }
      } finally {
        unlink(wav).catch(() => {});
      }
    } finally {
      try {
        opus.delete();
      } catch {}
      try {
        stream.destroy();
      } catch {}
      active.delete(userId);
    }
  }

  return () => {
    speaking.off("start", onStart);
    active.clear();
  };
}
