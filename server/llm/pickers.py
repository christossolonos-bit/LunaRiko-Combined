"""Concrete picker factories for VRM expressions and emotes.

Two factories built on `SemanticPicker`:

- `build_expression_picker()` — the 6 VRM 1.0 facial expressions, aliased
  per Riko's personality.
- `build_emote_picker()` — autoloads `*.fbx` from `mixamo/` AND `*.vrma`
  from `vrma/` + `vrma_xr/`, normalizes filenames to embed-friendly names,
  and returns `(picker, name_to_rel_url)` so callers can resolve a picked
  name straight to the browser-relative path that goes to /animate.

Both pickers share the underlying MiniLM model (cached in `semantic_picker`).

Public surface:
    build_expression_picker() -> SemanticPicker
    build_emote_picker() -> (SemanticPicker, dict[str, str])
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Tuple

from server.config import (
    ANIMATION_SOURCES,
    CLIENT_ROOT,
    DEFAULT_EXPRESSION,
    MATCH_THRESHOLD,
    MINILM_PATH,
)
from server.llm.semantic_picker import SemanticPicker


# VRM 1.0 standard facial expressions.
VRM_EXPRESSIONS = ["angry", "happy", "neutral", "relaxed", "sad", "surprised"]


# Per-expression alias text. The embedder sees this richer synonym string
# instead of the bare name, which dramatically widens the semantic catchment
# of each expression. `.pick()` still returns the bare name — only the
# embedding side changes. Tuned for Riko: `relaxed` covers her smug/playful
# vibe, `sad` covers acute distress (crying/tears), etc. Swap or override
# this dict for other characters with different personalities.
#
# ─── HOW TO TUNE THIS DICT ─────────────────────────────────────────────────
# If you notice the LLM frequently emits a word that maps to the wrong
# expression (or falls back when it shouldn't), just add that word to the
# correct row below. Comma-separated, lowercase, no special syntax.
#
# Example: if Riko keeps saying `<expr:wow>` and it falls back to "relaxed"
# when you'd rather it map to "surprised", add `wow` to the surprised row:
#
#     "surprised": "surprised, shocked, astonished, amazed, startled, wow, ...",
#
# Note on conflicts: "grin" is intentionally on `relaxed` (Riko's smug grin)
# rather than `happy` (open smile). "evilgrin" / "smirk" follow the same
# logic — they're mischievous, not joyful.
# ───────────────────────────────────────────────────────────────────────────
RIKO_EXPRESSION_ALIASES = {
    "angry":     "angry, furious, mad, enraged, rage, hostile, livid, irritated, scowl",
    "happy":     "happy, joyful, glad, cheerful, gleeful, delighted, beaming, laugh, laughing, smile, joy",
    "neutral":   "neutral, plain, default, blank, expressionless, deadpan",
    "relaxed":   "relaxed, smug, smirk, smirking, grin, grinning, evilgrin, playful, mischievous, cocky, teasing, sly, confident, cheeky, flirty, mocking",
    "sad":       "sad, crying, weeping, tears, sorrowful, melancholy, upset, hurt, depressed, sobbing",
    "surprised": "surprised, shocked, astonished, amazed, startled, wide-eyed, gasping, stunned",
}


# Per-emote alias text. Same structure and tuning rules as the expression
# aliases above — keys are the NORMALIZED emote names (post-`_normalize_emote_name`),
# values are comma-separated synonym strings the embedder will use.
#
# ─── HOW TO TUNE THIS DICT ─────────────────────────────────────────────────
# When the LLM emits a creative emote name that doesn't match any animation
# you have, OR matches the wrong one, add it to the relevant row. Example:
# if Riko says `<emote:shadowbox>` and that should fire the "Punch Combo"
# animation, ensure "shadowbox" appears in the `punch combo` alias row.
#
# Use ONLY normalized names as keys — see `_normalize_emote_name` for the
# rules (lowercase, underscores/hyphens → spaces, leading digits stripped).
# Unaliased emotes fall back to their bare name automatically.
# ───────────────────────────────────────────────────────────────────────────
RIKO_EMOTE_ALIASES = {
    # combat / movement
    # Note: keep alias lists SHORT for names that already match well bare
    # (e.g. "backflip", "spin", "kiss") — every extra synonym dilutes the
    # averaged embedding and can flip a direct match to a wrong neighbor.
    "punch combo":  "punch combo, shadowbox, boxing, combat, combo, fists",
    "cross punch":  "cross punch, punch, jab, hit, strike",
    "flying kick":  "flying kick, kick, jump kick, attack",
    "spin":         "spin, twirl, quickspin, whirl, pirouette, rotate",
    "backflip":     "backflip, back flip, somersault",
    "front flip":   "front flip, frontflip, forward flip, flip",
    "running forward flip": "running forward flip, sprint flip, running flip, dash flip",
    # gestures
    "waving":       "waving, wave, hello, hi, greeting, bye",
    "greeting":     "greeting, hello, hi, salutation",
    "peace sign":   "peace sign, peace, victory, v sign, two fingers",
    "headpat1":     "headpat, pet, pat, head pat",
    "headpat cover":"headpat cover, dodge headpat, avoid pat, block pat",
    "headpat swat": "headpat swat, swat headpat, slap away, deflect pat",
    "shoot":        "shoot, finger gun, bang, gun, pew",
    "stop it":      "stop it, stop, halt, no, dismiss, enough",
    # niche names that tokenize oddly — alias pulls their embedding
    # toward a meaningful concept so they don't randomly attract queries.
    "lookleft":     "look left, glance left, peek left, turn left",
    "lookright":    "look right, glance right, peek right, turn right",
    "dogeza":       "dogeza, kneel, apology, prostrate, bow",
    "touch ears":   "touch ears, fox ears, ear touch, perk ears",
    "shikanoko":    "shikanoko, deer dance, noko",
    # idles / poses
    "bashful":      "bashful, shy, embarrassed, flustered, blush",
    "thinking":     "thinking, ponder, consider, hmm, contemplate, idea",
    "talking":      "talking, speak, chat, conversation, blabbing",
    "look around":  "look around, glance, survey, scan, looking",
    "model pose":   "model pose, pose, model, fashion",
    "motion pose":  "motion pose, pose, stand, posing",
    "squat":        "squat, crouch, sit low",
    # affection
    "kiss":         "kiss, smooch, peck",
    "kiss kit":     "kiss kit, smooch, blow kiss",
    "kiss kitsune": "kiss kitsune, fox kiss",
    # reactions
    "loser":        "loser, l sign, lose, fail, taunt, mock",
    "woah":         "woah, whoa, wow, amazement, astonishment",
    "excited":      "excited, hyped, pumped, eager, enthusiastic",
    "sad idle":     "sad idle, sad standing, mope, dejected",
    "angry":        "angry, mad, furious, rage, scowl, frown",
    "happy":        "happy, joyful, cheerful, glad, beam",
}


def _normalize_emote_name(path: Path) -> str:
    """Filename stem → embed-friendly normalized name.

    - lowercases
    - replaces underscores and hyphens with spaces
    - strips leading numeric prefixes like "001 ", "08 "

    Examples:
        Backflip.fbx                 -> 'backflip'
        Sad Idle.fbx                 -> 'sad idle'
        idle_calm.fbx                -> 'idle calm'
        001_motion_pose.vrma         -> 'motion pose'
        Bling-Bang-Bang-Born.vrma    -> 'bling bang bang born'
        Spin.vrma                    -> 'spin'
    """
    s = path.stem.replace("_", " ").replace("-", " ")
    parts = [p for p in s.split() if p]
    # Strip leading numeric tokens like "001", "08".
    while parts and parts[0].isdigit():
        parts.pop(0)
    return " ".join(parts).lower().strip()


def build_expression_picker() -> SemanticPicker:
    """Picker for the 6 VRM 1.0 facial expressions. Fallback comes from
    `character_config.yaml` (`presets.default.default_expression`) via
    `config.DEFAULT_EXPRESSION` — Riko defaults to "relaxed"."""
    return SemanticPicker(
        names=VRM_EXPRESSIONS,
        model_path=MINILM_PATH,
        threshold=MATCH_THRESHOLD,
        fallback=DEFAULT_EXPRESSION,
        aliases=RIKO_EXPRESSION_ALIASES,
    )


def build_emote_picker() -> Tuple[SemanticPicker, Dict[str, str]]:
    """Build a picker over every `.fbx` / `.vrma` file in `ANIMATION_SOURCES`.

    Returns `(picker, name_to_rel_url)`. Callers do:
        name, _ = picker.pick("shadowbox")
        url = name_to_rel_url[name]   # e.g. "animations/mixamo/Punch Combo.fbx"
        playback.enqueue(PlaybackItem(EMOTE, animate_url=url))

    The `name_to_rel_url` values are BROWSER-RELATIVE strings already (paths
    relative to `client/`), ready to drop into `vrm_animate(...)`. We never
    expose absolute filesystem paths from this function — that mistake is
    what broke Milestone 5's first test run.

    Folder iteration order is `ANIMATION_SOURCES`. First-seen wins on name
    collisions (so VRMA versions override Mixamo when both exist).
    """
    name_to_rel_url: Dict[str, str] = {}
    dropped_dupes: list = []

    for folder, pattern in ANIMATION_SOURCES:
        if not folder.exists():
            print(f"[pickers] WARN animation folder missing: {folder}")
            continue
        files = sorted(folder.glob(pattern))
        for p in files:
            name = _normalize_emote_name(p)
            if not name:
                continue  # empty after stripping leading digits — skip
            # .as_posix(), never str(): this is a URL the BROWSER fetches,
            # and str() on Windows returns backslashes.
            rel_url = p.relative_to(CLIENT_ROOT).as_posix()
            if name in name_to_rel_url:
                dropped_dupes.append((name, rel_url))
                continue
            name_to_rel_url[name] = rel_url

    if not name_to_rel_url:
        raise FileNotFoundError(
            f"no .fbx/.vrma animations found in any of: "
            f"{[str(f) for f, _ in ANIMATION_SOURCES]}"
        )

    names = list(name_to_rel_url.keys())
    fallback = "idle" if "idle" in name_to_rel_url else names[0]
    print(
        f"[pickers] emote pool: {len(names)} animations from "
        f"{len(ANIMATION_SOURCES)} source(s), fallback={fallback!r}"
    )
    if dropped_dupes:
        print(
            f"[pickers] dropped {len(dropped_dupes)} duplicate name(s) "
            f"(first-seen wins): {dropped_dupes[:3]}{'...' if len(dropped_dupes) > 3 else ''}"
        )

    picker = SemanticPicker(
        names=names,
        model_path=MINILM_PATH,
        threshold=MATCH_THRESHOLD,
        fallback=fallback,
        aliases=RIKO_EMOTE_ALIASES,
    )
    return picker, name_to_rel_url


if __name__ == "__main__":
    # Run with: python -m server.llm.pickers
    # Builds two pairs of pickers (bare vs aliased) for A/B comparison.

    # --- expression A/B ---
    print("[pickers.__main__] building BARE expression picker...")
    bare_expr = SemanticPicker(
        names=VRM_EXPRESSIONS, model_path=MINILM_PATH,
        threshold=MATCH_THRESHOLD, fallback=DEFAULT_EXPRESSION,
    )
    print("[pickers.__main__] building ALIASED expression picker (production)...")
    expr_picker = build_expression_picker()

    # --- emote A/B ---
    print("\n[pickers.__main__] building BARE emote picker (no aliases)...")
    # Reproduce the build steps without aliases for fair A/B.
    _bare_n2u: dict = {}
    for folder, pattern in ANIMATION_SOURCES:
        if not folder.exists():
            continue
        for p in sorted(folder.glob(pattern)):
            n = _normalize_emote_name(p)
            if n and n not in _bare_n2u:
                _bare_n2u[n] = p.relative_to(CLIENT_ROOT).as_posix()
    bare_emote = SemanticPicker(
        names=list(_bare_n2u.keys()), model_path=MINILM_PATH,
        threshold=MATCH_THRESHOLD,
        fallback="idle" if "idle" in _bare_n2u else next(iter(_bare_n2u)),
    )
    print("[pickers.__main__] building ALIASED emote picker (production)...")
    emote_picker, emote_urls = build_emote_picker()

    # --- expression queries ---
    expr_queries: list = [
        ("moneyeyes",  "happy"),
        ("furious",    "angry"),
        ("crying",     "sad"),
        ("sobbing",    "sad"),
        ("glee",       "happy"),
        ("laugh",      "happy"),       # new
        ("smug",       "relaxed"),
        ("mocking",    "relaxed"),
        ("teasing",    "relaxed"),
        ("smirk",      "relaxed"),     # new
        ("grin",       "relaxed"),     # new — Riko's grin is smug, not joyful
        ("evilgrin",   "relaxed"),     # new — user's actual LLM output
        ("shocked",    "surprised"),
        ("wow",        "surprised"),
        ("zzz",        None),
        ("nonsense_word", None),
    ]
    # --- emote queries ---
    emote_queries: list = [
        # User-reported cases:
        ("shadowbox",  "punch combo"),     # user's example #1
        ("quickspin",  "spin"),            # user's example #2
        # Other plausible LLM creativity:
        ("flip",       None),
        ("somersault", None),
        ("wave",       "waving"),
        ("hello",      None),
        ("kiss",       "kiss"),
        ("smooch",     None),
        ("punch",      None),
        ("jab",        None),
        ("kick",       None),
        ("pet",        None),
        ("headpat",    None),
        ("pose",       None),
        ("peace",      "peace sign"),
        ("loser",      "loser"),
        ("dance",      None),
        ("woah",       "woah"),
        ("whoa",       None),
        ("backflip",   "backflip"),
        ("spin",       "spin"),
        ("idle",       "idle"),
        ("nonsense_word", "idle"),         # fallback
    ]

    # --- run + collect ---
    expr_rows = []
    for q, exp in expr_queries:
        b, bs = bare_expr.pick(q)
        a, as_ = expr_picker.pick(q)
        expr_rows.append((q, exp, b, bs, a, as_))

    emote_rows = []
    for q, exp in emote_queries:
        b, bs = bare_emote.pick(q)
        a, as_ = emote_picker.pick(q)
        emote_rows.append((q, exp, b, bs, a, as_))

    # --- expression table ---
    print("\n" + "=" * 110)
    print("EXPRESSION PICKER A/B  (bare names vs aliased)")
    print("=" * 110)
    print(f"{'query':>15}  |  {'expected':<10}  |  {'BARE':<18}  |  {'ALIASED':<18}  |  result")
    print("-" * 110)
    fixed = regressed = 0
    for q, exp, b, bs, a, as_ in expr_rows:
        mark = ""
        if exp is not None:
            b_ok = (b == exp); a_ok = (a == exp)
            if a_ok and not b_ok: mark = "FIXED"; fixed += 1
            elif b_ok and not a_ok: mark = "REGRESSED"; regressed += 1
            elif a_ok and b_ok: mark = "ok (both)"
            else: mark = "still off"
        print(f"{q!r:>15}  |  {str(exp):<10}  |  {b:<10} {bs:.2f}     |  {a:<10} {as_:.2f}     |  {mark}")
    print(f"\nEXPRESSION: aliases fixed {fixed}, regressed {regressed}")

    # --- emote table ---
    print("\n" + "=" * 110)
    print("EMOTE PICKER A/B  (bare names vs aliased)")
    print("=" * 110)
    print(f"{'query':>15}  |  {'expected':<14}  |  {'BARE':<22}  |  {'ALIASED':<22}  |  result")
    print("-" * 110)
    fixed = regressed = 0
    for q, exp, b, bs, a, as_ in emote_rows:
        mark = ""
        if exp is not None:
            b_ok = (b == exp); a_ok = (a == exp)
            if a_ok and not b_ok: mark = "FIXED"; fixed += 1
            elif b_ok and not a_ok: mark = "REGRESSED"; regressed += 1
            elif a_ok and b_ok: mark = "ok (both)"
            else: mark = "still off"
        print(f"{q!r:>15}  |  {str(exp):<14}  |  {b:<14} {bs:.2f}     |  {a:<14} {as_:.2f}     |  {mark}")
    print(f"\nEMOTE: aliases fixed {fixed}, regressed {regressed}")

    # --- emote URL resolution preview ---
    print("\n" + "=" * 110)
    print("EMOTE URL RESOLUTION  (production picker only)")
    print("=" * 110)
    for q, exp, _b, _bs, a, as_ in emote_rows:
        url = emote_urls.get(a, "(missing)")
        print(f"{q!r:>15}  ->  {a!r:<24}  ->  {url}")

    # --- hard checks ---
    nw_expr = next(r for r in expr_rows if r[0] == "nonsense_word")
    assert nw_expr[4] == DEFAULT_EXPRESSION, (
        f"expr fallback broken: got {nw_expr[4]!r}"
    )
    nw_emote = next(r for r in emote_rows if r[0] == "nonsense_word")
    assert nw_emote[4] == "idle", f"emote fallback broken: got {nw_emote[4]!r}"
    print("\n[pickers.__main__] OK — fallbacks intact")
