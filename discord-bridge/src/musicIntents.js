/**
 * Parse voice lines into music control intents.
 *
 * Examples:
 *   "luna play never gonna give you up"
 *   "luna play youtube lo-fi beats"
 *   "luna play distrokid velvet lobby"
 *   "luna play my song aura bloom"
 *   "luna play radio groove salad"
 *   "luna stop" / "stop the music" / "luna skip"
 */

import { findRadioStation, listRadioStationNames } from "./music.js";

function normalize(text) {
  return String(text || "")
    .toLowerCase()
    .replace(/[^\w\s']/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * @returns {{
 *   type: "stop"|"skip"|"play"|"none",
 *   source?: "auto"|"youtube"|"distrokid"|"radio",
 *   query?: string,
 *   spokenAck?: string,
 * } | null}
 */
export function parseMusicIntent(text, { musicPlaying = false, wakeWords = ["luna"] } = {}) {
  const raw = String(text || "").trim();
  if (!raw) return null;
  let t = normalize(raw);

  // Strip leading wake words so "luna play X" and "play X" (after strip) both work.
  for (const w of wakeWords) {
    const ww = normalize(w);
    if (ww && t.startsWith(ww + " ")) {
      t = t.slice(ww.length).trim();
      break;
    }
    if (ww && t === ww) {
      t = "";
      break;
    }
  }

  // Interrupt / stop — works while music is on even without much else.
  if (
    /^(stop|halt|enough|shut up|be quiet)(\s+(the\s+)?(music|song|radio|track))?$/.test(t) ||
    /^(stop|halt)\s+(playing|that|it)$/.test(t) ||
    /^stop\s+(the\s+)?(music|song|radio)$/.test(t) ||
    /^turn\s+off\s+(the\s+)?(music|radio|song)$/.test(t) ||
    /^kill\s+(the\s+)?(music|song)$/.test(t)
  ) {
    return { type: "stop", spokenAck: "Stopping." };
  }

  if (/^(skip|next)(\s+(song|track|one))?$/.test(t) || /^skip\s+(this|it)$/.test(t)) {
    return { type: "skip", spokenAck: "Skipping." };
  }

  // play …
  const playMatch = t.match(
    /^(?:please\s+)?(?:can\s+you\s+)?(?:play|put\s+on|queue|start)\s+(.+)$/,
  );
  if (!playMatch) {
    // While music plays, bare "stop music" variants already handled; otherwise no intent.
    if (musicPlaying && /\b(stop|skip)\b/.test(t) && t.length < 40) {
      if (/\bskip\b/.test(t)) return { type: "skip", spokenAck: "Skipping." };
      return { type: "stop", spokenAck: "Stopping." };
    }
    return null;
  }

  let rest = playMatch[1].trim();
  let source = "auto";

  if (/^(youtube|yt)\s+/.test(rest)) {
    source = "youtube";
    rest = rest.replace(/^(youtube|yt)\s+/, "").trim();
  } else if (/^(distrokid|distro|library|local|my\s+(song|track|music|library))\s+/.test(rest)) {
    source = "distrokid";
    rest = rest
      .replace(/^(distrokid|distro|library|local|my\s+(song|track|music|library))\s+/, "")
      .trim();
  } else if (/^from\s+(distrokid|distro|my\s+library|the\s+library)\s+/.test(rest)) {
    source = "distrokid";
    rest = rest.replace(/^from\s+(distrokid|distro|my\s+library|the\s+library)\s+/, "").trim();
  } else if (/^radio\s+/.test(rest)) {
    source = "radio";
    rest = rest.replace(/^radio\s+/, "").trim();
  } else if (/\s+on\s+youtube$/.test(rest)) {
    source = "youtube";
    rest = rest.replace(/\s+on\s+youtube$/, "").trim();
  } else if (/\s+from\s+(distrokid|my\s+library)$/.test(rest)) {
    source = "distrokid";
    rest = rest.replace(/\s+from\s+(distrokid|my\s+library)$/, "").trim();
  } else if (/^the\s+radio(\s+|$)/.test(rest) || rest === "radio") {
    source = "radio";
    rest = rest.replace(/^the\s+radio\s*/, "").trim() || listRadioStationNames()[0] || "";
  }

  // "play groove salad radio" / "play radio station X"
  if (source === "auto") {
    const radioSuffix = rest.match(/^(.+?)\s+radio$/);
    if (radioSuffix && findRadioStation(radioSuffix[1])) {
      source = "radio";
      rest = radioSuffix[1].trim();
    } else if (findRadioStation(rest) && normalize(findRadioStation(rest).name) === normalize(rest)) {
      source = "radio";
    }
  }

  if (!rest) return null;

  const where =
    source === "distrokid"
      ? "from your DistroKid folder"
      : source === "radio"
        ? "radio"
        : source === "youtube"
          ? "on YouTube"
          : "";
  return {
    type: "play",
    source,
    query: rest,
    spokenAck: where ? `Playing ${rest} ${where}.` : `Playing ${rest}.`,
  };
}
