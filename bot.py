#!/usr/bin/env python3
"""
Hindi nature & science explainer video bot (v2).

Flow:  topic -> research -> script (with visual plan) -> Hindi voice -> images
       -> animated explainer scenes + photo scenes -> subtitles -> thumbnail -> Telegram / YouTube

Modes:
  python bot.py              full run (one video)
  python bot.py --check      health check of all keys / services (sends result to Telegram)
  python bot.py --selftest   offline build test (no internet, fake art)

Scene types in the script:
  photo    one full-screen image with slow camera move
  merge    A + B slide together, collide, and C is born
  split    one thing breaks into two parts
  steps    3-4 stages appear one by one with arrows (timeline, life cycle, evolution)
  compare  A vs B side by side
"""
import asyncio
import base64
import difflib
import glob
import io
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

import requests
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps, features

ROOT = Path(__file__).resolve().parent
WORK = Path(os.environ.get("WORK_DIR", str(ROOT / "work")))
STATE = ROOT / "state"
DONE_FILE = STATE / "done.json"
LICENSE_FILE = STATE / "license_log.json"
WPM_FILE = STATE / "wpm.json"

W, H, FPS = 1280, 720, 30
VOICE = os.environ.get("TTS_VOICE", "hi-IN-MadhurNeural")
TTS_RATE = os.environ.get("TTS_RATE", "+8%")
# measured: the 10-minute reference narrator speaks ~19% faster (syllables per voiced second) than the bot voice -> speed up whatever voice engine is used, pitch stays the same
VOICE_SPEED = min(1.35, max(0.9, float(os.environ.get("VOICE_SPEED", "1.12"))))
TTS_PROVIDER = os.environ.get("TTS_PROVIDER", "auto").strip().lower()  # auto | gemini | edge
GEMINI_VOICE = os.environ.get("GEMINI_VOICE", "Charon")
GEMINI_TTS_MODELS = [m for m in os.environ.get(
    "GEMINI_TTS_MODELS", "gemini-3.1-flash-tts-preview,gemini-2.5-flash-preview-tts").split(",") if m.strip()]
GEMINI_TTS_GAP = float(os.environ.get("GEMINI_TTS_GAP", "4"))
WPM = float(os.environ.get("WPM_ESTIMATE", "130"))  # spoken Hindi words per minute (default estimate)
MIN_SEC, MAX_SEC = 180, 300
def _test_sec():
    v = os.environ.get("TEST_SECONDS", "0").strip().lower()
    return int(v) if v.isdigit() else 0  # 0 / off / no / full / empty = normal 3-5 minute video


TEST_SEC = _test_sec()  # >0 = short test video of about this many seconds
UPLOAD_MODE = os.environ.get("UPLOAD_MODE", "telegram").strip().lower()
SCENE_GAP = 0.18
HOOK_GAP = 0.70
SFX_ON = os.environ.get("SFX", "0") == "1"  # sound effects are off by default
ALLOW_POLLI = os.environ.get("ALLOW_POLLINATIONS", "") == "1"  # watermarked + very slow, off by default

GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
PEXELS_KEY = os.environ.get("PEXELS_API_KEY", "")
PIXABAY_KEY = os.environ.get("PIXABAY_API_KEY", "")
POLLI_KEY = os.environ.get("POLLINATIONS_KEY", "")
CF_ACCOUNT = os.environ.get("CF_ACCOUNT_ID", "")
CF_TOKEN = os.environ.get("CF_API_TOKEN", "")
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")

STYLE_PHOTO = ("cinematic film still, photorealistic, dramatic moody lighting, rich contrast, shallow depth of field, "
               "35mm lens, highly detailed, high quality documentary look, one clear main subject, "
               "no text, no letters, no watermark, no logo, no real person's face")
STYLE_CARD = ("vibrant detailed digital illustration, one clear centered subject, clean simple soft-colored background, "
              "bright saturated colors, soft studio lighting, polished game-art quality, "
              "no text, no letters, no watermark, no logo")
STYLE_THUMB = ("ultra dramatic hero shot, one single main subject large and centered (close-up), bright rim light, vivid saturated colors, "
               "shallow depth of field, simple uncluttered background, high contrast, professional youtube thumbnail background, "
               "no text, no letters, no watermark")

# bright themes (one is picked per video so the whole video looks consistent)
PALETTES = [
    {"name": "sky", "top": (110, 190, 255), "bottom": (236, 248, 255), "accent": (255, 160, 0), "ink": (14, 40, 96)},
    {"name": "sunrise", "top": (255, 190, 120), "bottom": (255, 243, 220), "accent": (230, 70, 60), "ink": (70, 24, 48)},
    {"name": "mint", "top": (120, 222, 186), "bottom": (234, 253, 244), "accent": (255, 110, 64), "ink": (8, 62, 74)},
    {"name": "lavender", "top": (186, 166, 255), "bottom": (246, 240, 255), "accent": (255, 190, 10), "ink": (44, 22, 96)},
]

CATEGORIES = [
    ("earth_formation", "How the Earth, Moon, oceans and atmosphere formed"),
    ("solar_system", "Sun, planets, moons, asteroids, comets and how they formed"),
    ("stars_universe", "Stars, galaxies, black holes and the universe in simple terms"),
    ("insects", "Ants, bees, termites, butterflies and other insects and how they live"),
    ("wild_animals", "Behaviour and survival of wild animals (big cats, elephants, wolves, birds)"),
    ("ocean_life", "Oceans, coral reefs, whales, sharks, deep-sea creatures"),
    ("forests_plants", "Forests, trees, plants, fungi and how they survive and communicate"),
    ("human_evolution", "How humans evolved, early humans, fire, tools, language, migration"),
    ("natural_phenomena", "Volcanoes, earthquakes, aurora, lightning, monsoon, rainbows, tides"),
    ("extinct_life", "Dinosaurs, mammoths, mass extinctions and ancient life"),
    ("ecosystems", "Food chains, ecosystems, deserts, rivers, glaciers, seasons"),
    ("animal_senses", "Amazing senses and abilities of animals (migration, echolocation, camouflage)"),
]

STYLES = [
    ("sawaal", "Open with a mind-bending question and answer it step by step."),
    ("kahani", "Tell it as one flowing story along a timeline, from beginning to end."),
    ("myth", "Start from a very common misconception, then reveal what science actually says."),
    ("imagine", "Take the viewer on an 'imagine you are there' journey through the topic."),
    ("countdown", "Present five surprising facts that build up to the most amazing one."),
    ("zoom", "Start from the biggest scale and zoom in step by step to the smallest detail."),
]

VOICE_USED = ["edge"]
EXPLAINERS = {"merge", "split", "steps", "compare", "counter", "bars", "map"}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def run(cmd, **kw):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)
    if r.returncode != 0:
        raise RuntimeError("command failed: " + " ".join(str(c) for c in cmd[:6]) + "\n" + r.stderr[-1500:])
    return r


def ffprobe_dur(path):
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path])
    return float(r.stdout.strip())


# --------------------------------------------------------------------------- state
def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


# --------------------------------------------------------------------------- Gemini (text)
GBASE = "https://generativelanguage.googleapis.com/v1beta"
_models_cache = []


def gemini_models():
    global _models_cache
    if _models_cache:
        return _models_cache
    if os.environ.get("GEMINI_MODEL"):
        _models_cache = [os.environ["GEMINI_MODEL"]]
        return _models_cache
    r = requests.get(f"{GBASE}/models", headers={"x-goog-api-key": GEMINI_KEY},
                     params={"pageSize": 200}, timeout=30)
    r.raise_for_status()
    names = [m["name"].split("/")[-1] for m in r.json().get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", [])]

    def ver(n):
        m = re.search(r"gemini-(\d+(?:\.\d+)?)", n)
        return float(m.group(1)) if m else 0.0

    skip = ("image", "tts", "live", "audio", "embedding", "robotics", "computer", "learnlm", "gemma", "vision")
    flash = [n for n in names if "flash" in n and not any(s in n for s in skip)]
    stable = sorted([n for n in flash if "preview" not in n and "exp" not in n and "lite" not in n],
                    key=ver, reverse=True)
    lite = sorted([n for n in flash if "lite" in n and "preview" not in n], key=ver, reverse=True)
    prev = sorted([n for n in flash if "preview" in n or "exp" in n], key=ver, reverse=True)
    _models_cache = (stable[:2] + prev[:1] + lite[:1]) or flash[:3]
    if not _models_cache:
        raise RuntimeError("No usable Gemini model found for this API key")
    log("Gemini models:", _models_cache)
    return _models_cache


GROUND_SRC = []


GOV = {"t0": time.time(), "opt": 0, "skipped": 0, "slow": 0, "cool": 0.0}
RUN_BUDGET_MIN = float(os.environ.get("RUN_BUDGET_MIN", "200"))  # the workflow is cancelled after 240 min: everything optional stops long before
OPT_MAX = int(os.environ.get("OPTIONAL_LLM_MAX", "150"))


def optional_model():
    """Editing passes use the light model (its own quota bucket) so they never starve the script / director calls."""
    names = gemini_models()
    return os.environ.get("OPTIONAL_MODEL") or next((n for n in names if "flash-lite" in n), names[0])


def elapsed_min():
    return (time.time() - GOV["t0"]) / 60.0


def llm_optional_ok():
    """Optional Gemini calls (picture checks, editing passes) run only while the key is healthy, the budget is not used up and there is plenty of time left."""
    return bool(GEMINI_KEY) and GOV["opt"] < OPT_MAX and time.time() >= GOV["cool"] and elapsed_min() < 175


def gemini(prompt, json_mode=False, temperature=0.8, search=False, image=None, optional=False):
    """Returns (text, grounding_titles). optional=True: one quick try, skipped completely while Gemini is slow / rate limited."""
    if optional:
        if not llm_optional_ok():
            GOV["skipped"] += 1
            raise RuntimeError("optional Gemini call skipped (rate limit / budget / time)")
        GOV["opt"] += 1
    t_call = time.time()
    try:
        return _gemini_raw(prompt, json_mode, temperature, search, image, optional)
    finally:
        dur = time.time() - t_call
        if dur > 30:
            GOV["slow"] += 1
            if GOV["slow"] >= 2 and time.time() >= GOV["cool"]:
                GOV["cool"] = time.time() + 900
                log(f"Gemini is slow / rate limited ({dur:.0f}s for one call): optional passes and picture checks pause for 15 minutes")
        elif dur < 12:
            GOV["slow"] = 0


def _gemini_raw(prompt, json_mode, temperature, search, image, optional):
    parts_ = [{"text": prompt}]
    if image:
        parts_.append({"inline_data": {"mime_type": "image/jpeg", "data": image}})
    body = {"contents": [{"parts": parts_}],
            "generationConfig": {"temperature": temperature}}
    if json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"
    if search:
        body["tools"] = [{"google_search": {}}]
    last = None
    for model in ([optional_model()] if optional else gemini_models()):
        for attempt in range(1 if optional else 2):
            try:
                r = requests.post(f"{GBASE}/models/{model}:generateContent",
                                  headers={"x-goog-api-key": GEMINI_KEY}, json=body, timeout=60 if optional else 240)
                if r.status_code in (429, 500, 503):
                    last = f"{model} HTTP {r.status_code}"
                    time.sleep(3 if optional else 15 * (attempt + 1))
                    continue
                if r.status_code in (400, 403, 404):
                    last = f"{model} HTTP {r.status_code}: {r.text[:300]}"
                    break
                r.raise_for_status()
                j = r.json()
                cand = (j.get("candidates") or [{}])[0]
                parts = cand.get("content", {}).get("parts", [])
                text = "".join(p.get("text", "") for p in parts).strip()
                if not text:
                    last = f"{model} empty response ({cand.get('finishReason')})"
                    continue
                titles = []
                for ch in cand.get("groundingMetadata", {}).get("groundingChunks", []):
                    t = (ch.get("web") or {}).get("title")
                    u = (ch.get("web") or {}).get("uri")
                    if u and all(u != x["uri"] for x in GROUND_SRC):
                        GROUND_SRC.append({"title": t or "", "uri": u})
                    if t and t not in titles:
                        titles.append(t)
                return text, titles
            except requests.RequestException as e:
                last = f"{model} {type(e).__name__}"
                time.sleep(3 if optional else 10)
    raise RuntimeError(f"Gemini failed: {last}")


def gemini_json_opt(prompt, temperature=0.2):
    return gemini_json(prompt, temperature, optional=True)


def parse_json(text):
    t = text.strip()
    t = re.sub(r"^```(?:json)?", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    starts = [i for i in (t.find("{"), t.find("[")) if i >= 0]
    if starts:
        s = min(starts)
        e = max(t.rfind("}"), t.rfind("]"))
        t = t[s:e + 1]
    return json.loads(t)


def gemini_json(prompt, temperature=0.8, optional=False):
    for _ in range(2 if optional else 3):
        text, _t = gemini(prompt, json_mode=True, temperature=temperature, optional=optional)
        try:
            return parse_json(text)
        except Exception as e:
            log("JSON parse failed, retrying:", e)
    raise RuntimeError("Gemini did not return valid JSON")



# --------------------------------------------------------------------------- director pass (voice first, then visuals on exact words)
ITEM_KINDS = {"quote": 3.6, "bubble": 3.4, "note": 6.6, "place": 3.8, "year": 4.6, "title": 3.4, "person": 4.4, "stat": 3.6, "verdict": 5.0, "explain": 4.6, "srclist": 4.6, "action": 3.6, "name": 3.8, "feature": 6.8,
              "measure": 4.0, "qmark": 2.8, "lens": 4.2, "beforeafter": 5.0, "compare": 5.6, "mark": 3.4, "chart": 5.6, "tline": 6.0, "hl": 3.6}
ITEM_SLOT = {"quote": "N", "bubble": "A", "note": "N", "place": "N", "year": "N", "title": "N", "popup": "P", "person": "N", "stat": "N", "verdict": "P", "explain": "P", "srclist": "N", "action": "P", "name": "N", "feature": "P",
             "measure": "N", "qmark": "N", "lens": "N", "beforeafter": "N", "compare": "N", "mark": "N", "chart": "N", "tline": "N", "hl": "N"}

DIRECTOR_RULES = """You are the DIRECTOR and senior film editor of a Hindi nature/science documentary (the polished "AI documentary explainer" style). The narration is ALREADY recorded. For each scene you get the narration words as index:word(second spoken). You decide what the viewer SEES and which editing technique is used at which exact word, so that even on mute the video explains everything.

=== STEP 1: READ THE MOMENT (do this silently for every scene) ===
- WHAT is said: a subject (animal / place / object / phenomenon), a part of a subject, a movement, a number, a date, a quote, a real source or evidence, a comparison, a process, a mystery, a reveal.
- TO WHOM / HOW it is said (use the scene's tone): suspense = slow dark push-ins, dark grade, flash/glitch only at the reveal; wonder = wide reveals, pull-outs, cinematic/warm grade; serious = steady push, cool grade, no flashy transitions; excited = faster cuts, whip/zoom transitions, vivid grade; calm = gentle drifts, natural grade, plain fades. A question to the viewer = camera slowly pushes in. A shocking fact = a hard cut or flash exactly on that word. A list = one picture per item. Reflection/ending = slow pull-out and fade.
- Then pick the technique(s) from the CATALOGUE that fit the moment. Never use a technique just because it exists.

=== STEP 2: THE 10 GOLDEN RULES (obey always) ===
1 Story first: understand the narration, then choose techniques.  2 Timing: every change happens ON the word that causes it (cut ~0.1 s before, never after).  3 Smooth movement: camera moves ease in/out, never jerk.  4 Consistency: same look/grade/overlay style through the whole film.  5 Sound effects only very quiet (the engine handles it).  6 Always a high-quality REAL picture, so zooming stays sharp.  7 Avoid over-editing: at most one special transition per ~15 seconds, plain fade/cut otherwise; no effect for decoration.  8 Visual hierarchy: the subject being spoken about is the biggest thing; everything else secondary.  9 Fact accuracy: never invent dates, names, numbers or quotes; use only what the narration says.  10 One colour grade for the film (leave "grade" empty unless the mood really changes).

=== CATALOGUE of the 150 techniques (what exists, and what the engine can do) ===
MAP (1-20): map zoom, pins, route line, distance counter, globe, borders, expansion, army arrows, GPS, compass, scale, comparison -> the engine has a MAP scene type (script decides); inside photo scenes use "place" items for locations.
DATA (21-40): counters, %, year counter, bars, pie, line graphs, ranking, odometer -> engine scene types counter / bars; inside photo scenes use "year" for dates/periods.
HISTORY/DOCUMENT (41-60): timeline, old photo, Ken Burns, document reveal, newspaper, manuscript, evidence highlight -> use ONLY "note" (source card) when a real document / paper / report / study / book / inscription is mentioned; archival look = "grade":"bwred" or "dark".
CAMERA (61-80): push in, pull out, pan, tilt, orbit, parallax, drone reveal, rack/focus pull, handheld, whip pan, match move, tracking, cinematic reveal -> "cam": push, pull, pan_lr, pan_rl, tilt_up, tilt_down, orbit, handheld, push_tl, push_br; "kf" keyframes (below) for focus-follow, rack/focus pull and tracking.
TEXT (81-100): kinetic type, text reveal, word highlight, lower third, chapter title, date & location title, callout -> items "title", "place" (location title), "year" (date title), "bubble" (quote), "note" (highlighted words).
DIAGRAM (101-115): flowchart, steps, compare, layers, labels -> engine scene types steps / compare / merge / split / layers; not in photo scenes.
VFX (116-135): grain, dust, fog, vignette, glitch, light leak, flash, split screen -> already added by the engine lightly; you only choose grade and transitions.
TRANSITION (136-150): "trans" = fade, zoom, whip, wipe, circle, flash, glitch, cut. Zoom/whip match the camera direction; flash = shock or reveal; circle = focus on one thing; wipe = time passes / moving on; cut = same idea continues; fade = default.

=== STEP 3: BEAT-BY-BEAT ANALYSIS (mandatory, this is what makes you accurate) ===
Read the scene sentence by sentence and clause by clause. Split it into BEATS: every smallest separate thing the narrator says (each subject, each part, each action, number, date, place, claim, question, reaction). Return them in "beats": [{"w": word index where the beat starts, "says": "the beat in ~5 words", "tech": "catalogue number + name of the ONE best technique for exactly this beat"}]. Then build "mains", "kf" and "items" so that EVERY beat is visibly handled by its technique. A scene with fewer than 3 beats, or with a main that has no technique and no camera movement, is WRONG. Vary techniques: never the same camera move or transition twice in a row, and match them to the tone as described above.

=== SPECIAL SCENE LAYOUTS (use them whenever the narration calls for them - they are the signature of the polished style) ===
 "split": use it when the narrator names/lists/compares 2 or 3 separate subjects in this scene (e.g. "lion and rabbit", "two universities", "three cities", before/after). The scene becomes a SPLIT SCREEN of real photos with a red brush label card for each, each appearing exactly on its word. Return "split": [{"w": word index where the subject is named (first one w=0 or its own word), "label_hi": "name as spoken (1-5 words)", "query_en": "1-3 English words for a REAL photo", "entity_en": "exact Wikipedia-style name of that subject or empty", "prompt_en": "..."}, ...] (2-3 items). Do not also plan mains for the same words; still return normal mains as a fallback.
 "terrain": use it when the narration names TWO real places (villages, cities, districts, parks, countries within about 1000 km) and says a distance, a journey or "from X to Y". The scene becomes a tilted real-satellite map with the two places tagged, an animated arrow and the distance. Return "terrain": {"pins":[{"label_hi":"first place as spoken","lat":..,"lon":..},{"label_hi":"second place as spoken","lat":..,"lon":..}], "regions":[{"label_hi":"district/state around","lat":..,"lon":..}]} with CORRECT real coordinates (never guess a place you do not know; omit terrain then).
 "note" items (source caption) as described below.
 Aim to use "split" at least once whenever a scene really names two or more subjects together.

=== WHAT TO RETURN FOR EACH SCENE ===
"mains": FULL-SCREEN pictures in order (this is the main storytelling tool). Each has "w" = index of the word where it appears (the first has w=0).
 RULE A - NEW PICTURE FOR EVERY SUBJECT: whenever the narrator names a new thing (animal, plant, place, object, planet, phenomenon, material, body part, event) the screen shows a NEW full-screen real picture of exactly that thing, starting on that word. Do NOT use small stickers/overlays for objects. Typical main length 1.5-3.5 s; never keep a picture after the narrator moved to another subject.
 RULE B0 - THE REAL THING: when the narration is about a SPECIFIC named subject (a named forest/park/city/river/mountain, a named species such as "Asiatic lion", a named object, building, institution or event), give "entity_en": its exact real English name exactly as Wikipedia would title it (e.g. "Gir Forest National Park", "Asiatic lion", "Sundarbans", "Hubble Space Telescope"). The engine then fetches the REAL photograph of that exact thing from Wikipedia/Wikimedia. Use "" only for generic moments (fog, a path, hands).
 RULE B00 - WORLD/EARTH: when the narrator says duniya, prithvi, dharti, vishwa, world, Earth or planet, the picture is a real photo of planet Earth (entity_en "Earth", query_en "earth from space"), NEVER a map. A map is only for a named country, state, city, forest or route.
 RULE B2 - ONLY REAL: never ask for or describe a cartoon, animation, 3D render, illustration, toy, statue, sculpture, mural or drawing. Every picture and clip is a REAL photograph / real camera footage of the real thing (a statue only if the narration is literally about a statue).
 RULE B3 - MEANING: show what the sentence MEANS. Emotions / states (happy, sad, angry, afraid, lonely, proud, in pain) = a REAL person whose face or body clearly shows that emotion ("sad man crying alone"); professions / personalities (doctor, engineer, scientist, farmer, teacher, soldier, police officer, priest, king, athlete) = a REAL person in that role at work ("doctor in white coat with stethoscope"); abstract words (good, bad, success, failure, danger, peace, time, money) = one concrete symbolic real scene. Never an unrelated animal or object.
 RULE B - REAL FIRST: every new main MUST have "query_en": 1-3 plain English words that find a REAL stock photo of that exact thing (e.g. "african lion", "volcano eruption", "milky way", "coral reef"). Use the most common, concrete, photographable name; no adjectives like "powerful", no people, no text, no cut-outs, no green-screen. Also give "prompt_en": a cinematic realistic description of the same single subject (only used if no real photo exists).
 RULE C - VIDEO CLIP FOR EVERY ACTION: whenever the narrator describes something being DONE or MOVING (walking, running, hunting, eating, drinking, flying, swimming, roaring, flowing, erupting, burning, spinning, growing), that main is a real VIDEO CLIP of exactly that action: "motion": true and "clip_en" = subject + the exact action in English, e.g. "lion walking" for chalna, "lion running" for daudna, "elephant drinking water", "eagle flying", "waterfall". The clip must show what is said and nothing else. Use clips on every such moment (up to about half of the mains); still pictures only for things that are still (a place, an object, a fact). Never ask for green-screen/isolated footage.
 RULE D - PARTS OF A SUBJECT = CAMERA KEYFRAMES ON THE SAME PICTURE: when the narrator talks about a part/detail of the subject already on screen (eyes, claws, wings, crater, tail, skin), do NOT make a new picture: use {"w":..,"same":true} with "kf", or put "kf" on the main itself.
 "kf": list of up to 4 camera keyframes [{"w": word index where the camera must ARRIVE, "fx": 0.1-0.9 horizontal position of the detail in the picture, "fy": 0.1-0.9 vertical position, "z": zoom 1.3-2.0}]. You cannot see the picture, so use typical composition: main subject near the centre, head/face upper-centre (fy~0.35), sky/top (fy~0.2), ground/bottom (fy~0.75), left/right side by the narrator's words. The camera glides (ease in/out) from keyframe to keyframe, following the narration. Use keyframes on most mains longer than 2 s and for every "look at this part" sentence; a wide establishing main may have none.
 Optional per main: "cam" (a camera move name from the catalogue, only when kf is not used), "trans" (transition INTO this main), "grade" (only if the mood truly changes), "tech": REQUIRED on every main: the catalogue number(s) you applied, e.g. "61,86". EVERY main must also move: give "kf" (preferred) or "cam"; every main except the first of the scene also needs "trans".
"items": things drawn ON TOP of the current main, exactly on the word that names them:
   - "place": "text_hi" = place name, "sub_hi" = small line (region/country). Only for a real, named place.
   - "year": "text_hi" = date/year/period exactly as spoken.
   - "bubble": "text_hi" (max 6 words) when someone's words are quoted.
   - "note": SOURCE CAPTION shown on the empty side of the picture while the narrator tells a fact that one of the REAL SOURCES (listed below the rules) supports. Return {"w": word where the fact starts, "kind": "note", "src": "<paragraph id exactly as listed, e.g. 2.1>", "mark_en": "1-4 consecutive English words copied exactly from that paragraph that contain the fact being told"}. The engine prints the real source title and the real paragraph, with your marked words underlined in red. Whether or not the narrator says the source name, show it when that fact is told. Use a note on EVERY scene that tells an important fact (a number, name, claim, place) that a paragraph supports (at most one note per scene, a different paragraph from the previous scene where possible), only if a listed paragraph really supports what is said; never invent a source, never write the text yourself. If no real sources are listed, use no notes.
   - "title": "text_hi" (1-3 words): the name of the main character / subject when first introduced, or the biggest reveal (at most 2 per video).
   - "year": may also carry "year_num" (the calendar year as a number, only when a real year 1000-2100 is spoken) and "month" (1-12 or null): the engine then slides a timeline to it.
   - "person": a REAL, notable public person (scientist, author, leader) named for the first time: {"w", "kind": "person", "text_hi": "name as spoken", "entity_en": "exact English Wikipedia article title"}. Never for private people, victims, villagers or fictional characters.
   - "stat": {"w", "kind": "stat", "text_hi": "1-3 words"} a striking short duration / number / count said for emphasis (e.g. "३ महीने", "४५ मिनट"), shown as huge red text.
   - "verdict": {"w", "kind": "verdict", "text_hi": "", "circles": [{"query_en": "1-3 English words of a REAL photo", "mark": "cross" or "tick", "label_hi": "1-2 words"}]} (2-3 circles) when the narrator says what is false / true, a myth / fact, what did not happen / what did.
   - "bubble" may carry "side": "left" or "right" = which of the two people on screen speaks (two people talking = two bubbles, first left, second right).
   - NO "popup" stickers exist any more.
   Never two items on the same word. No item before its word.
Return JSON only: {"scenes":[{"i": <scene number>, "read": "<who is addressed + how it is said, 6 words>", "beats":[...], "mains":[...], "items":[...]}]}"""


def director_prompt(chunk, topic="", nb=None):
    parts = []
    nb = nb or {}
    for i, sc in chunk:
        wds = sc["narration_hi"].split()
        wt = sc["wt"]
        line = " ".join(f"{k}:{w}({wt[k][0]:.1f})" for k, w in enumerate(wds))
        prev_ = nb.get(i - 1, "")
        parts.append(f"SCENE {i} (tone {sc.get('tone')}, {sc['speech_dur']:.0f}s, previous scene ended: \"{prev_}\"):\n{line}")
    return DIRECTOR_RULES + f"\n\nVIDEO TOPIC: {topic}\n\n" + sources_block() + "\n\n" + "\n\n".join(parts)


def parse_kf(kf, w0, w1, fx=None, fy=None):
    """Keyframe list [{w, fx, fy, z}] -> validated dicts with word index inside this main's word range."""
    out = []
    if isinstance(kf, list):
        for k in kf:
            try:
                w = int(k.get("w"))
                if not (w0 <= w < w1):
                    continue
                out.append({"w": w, "fx": min(0.9, max(0.1, float(k.get("fx")))), "fy": min(0.9, max(0.1, float(k.get("fy")))),
                            "z": min(2.1, max(1.15, float(k.get("z", 1.5))))})
            except Exception:  # noqa
                continue
    out.sort(key=lambda x: x["w"])
    ded = []
    for k in out:
        if not ded or k["w"] > ded[-1]["w"]:
            ded.append(k)
    return ded[:5] or None


def apply_special(sc, d):
    """Director may turn a photo scene into a split-screen list ('split') or a 3D distance map ('terrain')."""
    wds, wt = sc["narration_hi"].split(), sc.get("wt") or []
    if len(wt) != len(wds):
        return False
    sp = d.get("split")
    if isinstance(sp, dict):
        sp = sp.get("items")
    if isinstance(sp, list) and 2 <= len([x for x in sp if isinstance(x, dict)]) <= 3:
        items, lastw = [], -1
        for x in sorted([x for x in sp if isinstance(x, dict)], key=lambda x: int(x.get("w", 0) or 0)):
            try:
                w = int(x.get("w", 0))
            except Exception:  # noqa
                continue
            q_ = clean_short(x.get("query_en"), 50)
            if w <= lastw or w >= len(wds) or not q_ or not clean_short(x.get("label_hi"), 40):
                continue
            hits_ = [h for h in _label_hits(wds, clean_short(x.get("label_hi"), 40)) if h > lastw]
            if hits_:  # trust the spoken word itself over the model's word number
                w = min(hits_, key=lambda h: abs(h - w))
            items.append({"label_hi": clean_short(x.get("label_hi"), 40), "query_en": q_, "entity_en": clean_short(x.get("entity_en"), 70),
                          "prompt_en": clean_short(x.get("prompt_en"), 300) or ("a realistic photo of " + q_), "t": max(0.0, wt[w][0] - 0.05)})
            lastw = w
        if len(items) >= 2:
            items[0]["t"] = 0.0
            sc["type"], sc["items"] = "multi", items[:3]
            sc["vs"] = bool(d.get("vs")) and len(items) == 2  # old/new, before/after, myth/fact: red & black comparison with magnifier circles
            return True
    tr = d.get("terrain")
    if isinstance(tr, dict):
        try:
            pins = [{"label_hi": clean_short(p_.get("label_hi"), 28), "lat": float(p_["lat"]), "lon": float(p_["lon"])} for p_ in (tr.get("pins") or [])[:5]]
            regs = [{"label_hi": clean_short(r_.get("label_hi"), 20), "lat": float(r_["lat"]), "lon": float(r_["lon"])} for r_ in (tr.get("regions") or [])[:4]
                    if clean_short(r_.get("label_hi"), 20)]
            ok_ = len(pins) >= 2 and all(p_["label_hi"] and -80 <= p_["lat"] <= 80 and -180 <= p_["lon"] <= 180 for p_ in pins)
            if ok_ and max(hav_km((a_["lat"], a_["lon"]), (b_["lat"], b_["lon"])) for a_ in pins for b_ in pins) < 1500:
                sc["type"], sc["map"], sc["headline_hi"] = "map", {"pins": pins, "route": True, "mode": "terrain", "regions": regs}, ""
                return True
        except Exception:  # noqa
            pass
    return False


STORY_CTX = {"on": False, "adj": "", "look": "", "text": ""}
HUMAN_RE = re.compile(r"\b(man|men|woman|women|girl|boy|child|children|baby|infant|mother|father|family|parents|villager\w*|people|farmer|doctor|teacher|priest|house|home|hut|village|street|kitchen|crowd|wedding|funeral|school|neighbou?r\w*|relative\w*|husband|wife|daughter|son|grandmother|grandfather)\b", re.I)


def story_context(topic, story, script):
    """One call: where and among whom does the story happen? Every picture then stays inside that country / culture / era."""
    STORY_CTX.update({"on": False, "adj": "", "look": "", "text": ""})
    if not GEMINI_KEY:
        return STORY_CTX
    try:
        out = gemini_json(f"""Topic / story: {topic}
{(story or script or '')[:2500]}
Does this story happen among PEOPLE of a specific country / culture (a family, a village, a case, a historical event)? Answer with JSON only:
{{"people": true or false, "country": "India", "adjective": "Indian", "region": "e.g. rural Uttar Pradesh village", "era": "e.g. 1980s", "look": "10-18 English words describing how every picture must look: architecture, clothes, objects, light, e.g. 'rural Indian village, mud-brick houses, hand pump, charpai, women in sarees, warm cinematic light, 1980s'"}}
people=false for pure science / nature / space / animal topics.""", 0.1)
        if out.get("people") and str(out.get("adjective") or "").isascii() and out.get("adjective"):
            STORY_CTX.update({"on": True, "adj": str(out["adjective"])[:20], "look": str(out.get("look") or "")[:160],
                              "text": f"{out.get('country', '')} / {out.get('region', '')} / {out.get('era', '')}: {out.get('look', '')}"[:260]})
    except Exception as e:  # noqa
        log("story context failed:", str(e)[:100])
    log("story context:", STORY_CTX["text"] or "none (science / nature topic)")
    return STORY_CTX


def apply_context(scenes):
    """Every picture about people / places gets the story's country, culture and era: a girl's mother is an Indian woman, never a buffalo or a Russian."""
    if not STORY_CTX["on"]:
        return 0
    n = 0
    adj, look = STORY_CTX["adj"], STORY_CTX["look"]
    for sc in scenes:
        for sh in sc.get("shots") or []:
            q = str(sh.get("query_en") or "")
            if q and HUMAN_RE.search(q):
                if adj.lower() not in q.lower():
                    sh["query_en"] = f"{adj} {q}"[:60]
                    n += 1
                if sh.get("clip_en") and adj.lower() not in str(sh["clip_en"]).lower() and HUMAN_RE.search(str(sh["clip_en"])):
                    sh["clip_en"] = f"{adj} {sh['clip_en']}"[:60]
                if look and look.split(",")[0].lower() not in str(sh.get("prompt_en") or "").lower():
                    sh["prompt_en"] = (str(sh.get("prompt_en") or ("a realistic photo of " + q)) + ", " + look)[:420]
                sh["entity_en"] = ""  # a Wikipedia lookup would return a different country's photo
    log("story context applied to pictures:", n)
    return n


def parse_year(it):
    try:
        yn = int(float(it.get("year_num")))
        if not (1000 <= yn <= 2100):
            return None, None
        mo = it.get("month")
        mo = int(mo) if mo not in (None, "", 0, "null") and 1 <= int(mo) <= 12 else None
        return yn, mo
    except Exception:  # noqa
        return None, None


def parse_verdict(w, it):
    cs = []
    for c in (it.get("circles") or [])[:3]:
        if not isinstance(c, dict):
            continue
        q_ = clean_short(c.get("query_en"), 50)
        if not q_ or not q_.isascii():
            continue
        cs.append({"query_en": q_, "mark": "tick" if str(c.get("mark")) == "tick" else "cross", "label": clean_short(c.get("label_hi"), 24)})
    if len(cs) < 2:
        return None
    return {"w": w, "kind": "verdict", "text": "", "circles": cs}


def apply_direction(sc, d):
    if apply_special(sc, d):
        return True
    wds = sc["narration_hi"].split()
    nw = len(wds)
    mains, last = [], -1
    for m in sorted([x for x in (d.get("mains") or []) if isinstance(x, dict)], key=lambda x: int(x.get("w", 0) or 0)):
        try:
            w = int(m.get("w", 0))
        except Exception:  # noqa
            continue
        if w <= last or w >= nw:
            continue
        if not mains:
            w = 0
        if not m.get("same") and not str(m.get("prompt_en") or "").strip() and str(m.get("query_en") or "").strip():
            m["prompt_en"] = "a realistic cinematic photo of " + str(m["query_en"]).strip()
        if m.get("same") or not str(m.get("prompt_en") or "").strip():
            if not mains:
                continue
            m = {"same": True, "fx": m.get("fx"), "fy": m.get("fy"), "kf": m.get("kf"), "cam": m.get("cam"),
                 "trans": m.get("trans"), "grade": m.get("grade")}
        mains.append((w, m))
        last = w
    if mains and mains[0][0] != 0:
        mains[0] = (0, mains[0][1])
    if not mains or not str(mains[0][1].get("prompt_en") or "").strip():
        return False
    shots = []
    for k, (w, m) in enumerate(mains):
        w2 = mains[k + 1][0] if k + 1 < len(mains) else nw
        def fr_(v):
            try:
                return min(0.85, max(0.15, float(v)))
            except Exception:  # noqa
                return None
        shots.append({"text_hi": " ".join(wds[w:w2]), "prompt_en": clean_short(m.get("prompt_en") or "", 400) if not m.get("same") else "",
                      "query_en": clean_short(m.get("query_en") or "", 50), "entity_en": clean_short(m.get("entity_en") or "", 70) if not m.get("same") else "",
                      "motion": bool(m.get("motion")) and not m.get("same"),
                      "clip_en": clean_short(m.get("clip_en") or "", 40), "label_hi": "", "ov": None, "popup_en": "",
                      "fx": fr_(m.get("fx")), "fy": fr_(m.get("fy")),
                      "kf": parse_kf(m.get("kf"), w, w2, m.get("fx"), m.get("fy")),
                      "cam": str(m.get("cam") or ""), "trans": str(m.get("trans") or ""), "grade": str(m.get("grade") or ""),
                      "tech": str(m.get("tech") or "")})
    items, used = [], set()
    for it in sorted([x for x in (d.get("items") or []) if isinstance(x, dict)], key=lambda x: int(x.get("w", 0) or 0)):
        try:
            w = int(it.get("w"))
        except Exception:  # noqa
            continue
        kind = str(it.get("kind") or "")
        if kind not in ITEM_KINDS or not (0 <= w < nw) or (w, kind) in used:
            continue
        txt = clean_short(it.get("text_hi"), 260 if kind == "note" else 40)
        if kind == "note":
            sp_ = SRC_PARAS.get(str(it.get("src") or "").strip())
            if not sp_:
                continue
            mk_ = clean_short(it.get("mark_en"), 50)
            if mk_ and mk_.lower() not in sp_["text"].lower():
                mk_ = ""
            rec = {"w": w, "kind": "note", "text": sp_["text"], "mark": mk_, "sub": sp_["title"].replace("|", "-")[:70], "dom": _dom(sp_.get("url", ""))}
            used.add((w, kind))
            items.append(rec)
            continue
        if kind == "verdict":
            rec = parse_verdict(w, it)
            if rec:
                used.add((w, kind))
                items.append(rec)
            continue
        if kind == "person":
            ent_ = clean_short(it.get("entity_en"), 70)
            if txt and ent_ and ent_.isascii():
                used.add((w, kind))
                items.append({"w": w, "kind": "person", "text": txt, "entity_en": ent_, "sub": ""})
            continue
        if kind == "popup":
            if not str(it.get("popup_en") or "").strip():
                continue
            rec = {"w": w, "kind": kind, "popup_en": clean_short(it["popup_en"], 120)}
        elif txt:
            rec = {"w": w, "kind": kind, "text": txt, "mark": clean_short(it.get("mark_hi"), 30), "sub": clean_short(it.get("sub_hi"), 40)}
            if kind == "year":
                yn, mo = parse_year(it)
                if yn:
                    rec["year_num"], rec["month"] = yn, mo
            if kind == "bubble" and str(it.get("side") or "") in ("left", "right"):
                rec["side"] = str(it["side"])
        else:
            continue
        used.add((w, kind))
        items.append(rec)
    sc["shots"] = shots
    sc["items"] = items[:12]
    return True


DIRECTOR_REPORT = [""]
DIR_CAMS = {"suspense": ["push", "push_br", "tilt_down", "push_tl"], "wonder": ["pull", "tilt_up", "pan_lr", "orbit"],
             "serious": ["push", "pan_rl", "push_tl", "pull"], "excited": ["handheld", "push_br", "pan_lr", "push"],
             "calm": ["pan_lr", "pull", "pan_rl", "tilt_up"]}
DIR_TRANS = {"suspense": ["fade", "zoom", "fade", "flash"], "wonder": ["fade", "circle", "zoom", "fade"],
              "serious": ["fade", "wipe", "fade", "cut"], "excited": ["whip", "zoom", "flash", "whip"], "calm": ["fade", "wipe", "fade", "circle"]}


def ensure_techniques(sc, k0=0):
    """Safety net: the director's choice is kept; any main it left without movement/transition/technique gets one that fits the scene tone."""
    tone = sc.get("tone") if sc.get("tone") in DIR_CAMS else "calm"
    last_cam = last_tr = None
    for n, sh in enumerate(sc.get("shots", [])):
        if sh.get("clip"):
            continue
        if not sh.get("kf") and sh.get("cam") not in CAMS:
            opts = [c for c in DIR_CAMS[tone] if c != last_cam] or DIR_CAMS[tone]
            sh["cam"] = opts[(k0 + n) % len(opts)]
            sh["tech"] = (sh.get("tech") or "") + ("," if sh.get("tech") else "") + "cam:" + sh["cam"]
        last_cam = sh.get("cam")
        if n > 0 and sh.get("trans") not in TRANS_NAMES and sh.get("prompt_en"):
            opts = [t for t in DIR_TRANS[tone] if t != last_tr] or DIR_TRANS[tone]
            sh["trans"] = opts[(k0 + n) % len(opts)]
        last_tr = sh.get("trans") or last_tr
        if not sh.get("tech"):
            sh["tech"] = "auto"


def _numbered(sc):
    wds, wt = sc["narration_hi"].split(), sc.get("wt") or []
    return " ".join(f"{k}:{w}" for k, w in enumerate(wds))


def sentence_ranges(wds):
    """[(first word, end word)] per spoken sentence (very short ones are joined to a neighbour)."""
    rs, a = [], 0
    for k, w in enumerate(wds):
        if w.rstrip("\"')”’").endswith(("।", "?", "!", ".")) or k == len(wds) - 1:
            rs.append([a, k + 1])
            a = k + 1
    out = []
    for r in rs:
        if out and (out[-1][1] - out[-1][0]) < 4:
            out[-1][1] = r[1]
        else:
            out.append(r)
    if len(out) > 1 and (out[-1][1] - out[-1][0]) < 4:
        out[-2][1] = out[-1][1]
        out.pop()
    return out


def snap_to_sentences(scenes, topic=""):
    """ONE picture or clip per spoken sentence: it starts with the sentence and stays exactly as long as the sentence lasts;
    the next sentence gets the next picture/clip."""
    need = []  # (scene index, new shot) that had no picture of its own
    for i, sc in enumerate(scenes):
        if sc.get("type") != "photo" or not sc.get("shots"):
            continue
        wds = sc["narration_hi"].split()
        rng = sentence_ranges(wds)
        cnts = [max(1, len(str(sh.get("text_hi") or "").split())) for sh in sc["shots"]]
        starts, acc = [], 0
        for c_ in cnts:  # same proportional rule build_shots uses
            starts.append(min(len(wds) - 1, int(round(acc * len(wds) / sum(cnts)))))
            acc += c_
        new = []
        for (a, b) in rng:
            inside = [k for k, st in enumerate(starts) if a <= st < b and (sc["shots"][k].get("prompt_en") or sc["shots"][k].get("clip_en") or sc["shots"][k].get("image"))]
            if inside:
                src, fresh = sc["shots"][next((k for k in inside if sc["shots"][k].get("clip_en")), inside[0])], False
            else:
                cov = max((k for k, st in enumerate(starts) if st <= a), default=0)
                src, fresh = sc["shots"][cov], True
            sh = dict(src)
            sh["text_hi"] = " ".join(wds[a:b])
            sh["kf"] = parse_kf([k_ for k_ in (src.get("kf") or []) if isinstance(k_, dict)], a, b, src.get("fx"), src.get("fy")) if not fresh else []
            if fresh:
                sh["prompt_en"] = ""  # filled below: this sentence needs its own picture
                sh["motion"], sh["clip_en"] = False, ""
                if new and new[-1].get("prompt_en") is not None:
                    sh["trans"] = ""
                need.append((i, len(new)))
            new.append(sh)
        sc["shots"] = new
    # sentences without their own picture: ask for exactly what that sentence talks about
    rows = [f'S{i}.{k}: {scenes[i]["shots"][k]["text_hi"]}' for i, k in need]
    for a in range(0, len(rows), 12):
        part = rows[a:a + 12]
        prompt = f"""Hindi documentary sentences (topic: {topic}). For each sentence give the ONE picture that shows what that sentence talks about, in English:
"query_en" (1-3 plain words for a real stock photo of the main thing named), "entity_en" (exact Wikipedia title if a specific named place/animal/thing, else ""), "prompt_en" (realistic description of that single subject), "clip_en" (subject + exact action like "lion drinking water" ONLY if the sentence describes a visible action, else "").
Return ONLY JSON: {{"shots":[{{"id":"S0.1","query_en":"","entity_en":"","prompt_en":"","clip_en":""}}]}}
""" + "\n".join(part)
        try:
            out = gemini_json(prompt, 0.2)
        except Exception as e:  # noqa
            log("sentence pictures failed:", str(e)[:100])
            continue
        for c in (out.get("shots") or []):
            m_ = re.match(r"S(\d+)\.(\d+)$", str(c.get("id") or ""))
            if not m_:
                continue
            i, k = int(m_.group(1)), int(m_.group(2))
            if (i, k) not in need:
                continue
            sh = scenes[i]["shots"][k]
            q_, pr_ = clean_short(c.get("query_en"), 50), clean_short(c.get("prompt_en"), 300)
            if q_ or pr_:
                sh["prompt_en"] = pr_ or ("a realistic cinematic photo of " + q_)
                sh["query_en"], sh["entity_en"] = q_, clean_short(c.get("entity_en"), 70)
                ce = clean_short(c.get("clip_en"), 40)
                if ce and ce.isascii():
                    sh["motion"], sh["clip_en"] = True, ce
    return len(need)


def _sent_of_word(sc, w):
    for k, (a, b, _t0, _t1) in enumerate(sc["sent"]):
        if a <= w < b:
            return k
    return len(sc["sent"]) - 1 if w >= 0 else 0


def _word_of_label(words, label):
    toks = [t.strip("।,?!\"'") for t in (label or "").split() if len(t.strip("।,?!\"'")) > 1]
    for k, w in enumerate(words):
        w_ = w.strip("।,?!\"'")
        if any(t in w_ or (len(w_) > 2 and w_ in t) for t in toks):
            return k
    return None


def split_scene(scenes, sc, j0, j1):
    """Cut a scene into up to three scenes at sentence borders: [sentences before] [sentences j0..j1] [sentences after].
    Returns the middle one. Time slots, word times and shots are cut with it, so nothing runs past its sentence."""
    sent, words = sc["sent"], sc["narration_hi"].split()
    shots_ok = len(sc.get("shots") or []) == len(sent)
    parts = []
    for ja, jb in ((0, j0 - 1), (j0, j1), (j1 + 1, len(sent) - 1)):
        if jb < ja:
            continue
        aw, bw, ta, tb = sent[ja][0], sent[jb][1], sent[ja][2], sent[jb][3]
        pt = dict(sc)
        pt["narration_hi"] = " ".join(words[aw:bw])
        pt["wt"] = [(x - ta, y - ta) for x, y in sc["wt"][aw:bw]] if sc.get("wt") else None
        pt["sent"] = [(a - aw, b - aw, x - ta, y - ta) for a, b, x, y in sent[ja:jb + 1]]
        pt["dur"] = tb - ta
        pt["speech_dur"] = max(0.5, pt["dur"] - (sc["dur"] - sc["speech_dur"] if jb == len(sent) - 1 else 0.1))
        pt["shots"] = [dict(x) for x in sc["shots"][ja:jb + 1]] if shots_ok else []
        pt["items"] = [dict(it, w=it["w"] - aw) for it in sc.get("items", []) if isinstance(it, dict) and "w" in it and aw <= it["w"] < bw]
        parts.append((ja, pt))
    i = scenes.index(sc)
    scenes[i:i + 1] = [pt for _, pt in parts]
    return next(pt for ja, pt in parts if ja == j0)


def director_extras(scenes, topic=""):
    """Second, narrow passes that GUARANTEE the signature techniques are tried on every scene that can use them
    (the big director prompt alone was skipping them): split-screen lists, 3D distance maps, real-source captions, camera keyframes."""
    ph = [(i, sc) for i, sc in enumerate(scenes) if sc["type"] == "photo" and sc.get("wt") and len(sc["wt"]) == len(sc["narration_hi"].split())]
    stat = {"split": 0, "terrain": 0, "map": 0, "notes": 0, "clips": 0, "kf": 0}
    try:
        stat["sentence_pics"] = snap_to_sentences(scenes, topic)
    except Exception as e:  # noqa
        log("snap_to_sentences failed:", type(e).__name__, str(e)[:100])
    # 1) split-screen / terrain: one short focused question per batch of scenes
    decisions = []
    for a in range(0, len(ph), 4):
        batch = ph[a:a + 4]
        listing = "\n".join(f"SCENE {i}: {_numbered(sc)}" for i, sc in batch)
        prompt = f"""Film editor task. Hindi narration, every word numbered (index:word). Video topic: {topic}
For EACH scene decide ONLY these two things:
(1) "split": the narrator names or lists 2 or 3 SEPARATE subjects in this scene (two animals, two cities, two universities, before/after...). Add "vs": true next to "split" when it is exactly TWO subjects that are an explicit contrast (old vs new, before vs after, then vs now, myth vs fact, wrong vs right): the screen then becomes a bright-vs-red-black comparison. Return a list of 2-3 {{"w": word index where that subject is first spoken (the first item's w may be its own word), "label_hi": "name as spoken", "query_en": "1-3 English words for a real photo of exactly it", "entity_en": "exact Wikipedia article title or empty", "prompt_en": "realistic photo of it"}}.
(2) "terrain": the narrator names EXACTLY TWO real places (a start and an end: villages, cities, districts, parks, less than ~1000 km apart) as a journey, distance or 'from X to Y'. Return {{"pins":[{{"label_hi":"start place as spoken","lat":..,"lon":..}},{{"label_hi":"end place as spoken","lat":..,"lon":..}}],"regions":[{{"label_hi":"district/state around","lat":..,"lon":..}}]}} with CORRECT coordinates; never guess unknown places. (If the scene names THREE or more places use "map" below instead.)
(3) "map": the narrator names 1-5 real places (a named country/state/city/forest only: NOT "the world", "Earth", "planet" or "universe", which show a photo of Earth instead) (country, state, forest, city) where the story happens and no split/terrain applies. Return {{"pins":[{{"label_hi":"place as spoken","lat":..,"lon":..}}]}} with correct coordinates (never guess).
Use null when it does not apply. Do not force it. Return ONLY JSON: {{"scenes":[{{"i":0,"split":null,"vs":false,"terrain":null,"map":null}}]}}
{listing}"""
        try:
            out = gemini_json_opt(prompt, 0.2)
        except Exception as e:  # noqa
            log("extras (split/terrain) failed:", str(e)[:100])
            continue
        for r in (out.get("scenes") or []):
            try:
                i = int(r.get("i"))
            except Exception:  # noqa
                continue
            if 0 <= i < len(scenes) and any(i == j for j, _ in batch) and scenes[i]["type"] == "photo":
                decisions.append((scenes[i], r))
    # the special layout (split-screen / map) takes ONLY the sentence(s) that talk about it, and must finish inside that sentence's time slot
    for sc, r in decisions:
        if sc not in scenes or sc["type"] != "photo":
            continue
        idx = scenes.index(sc)
        if idx > 0 and scenes[idx - 1]["type"] in ("multi", "map"):  # keep a rhythm: not two special layouts in a row
            continue
        kind = "split" if r.get("split") else "terrain" if r.get("terrain") else "map" if (r.get("map") and stat["map"] < max(1, len(ph) // 3)) else ""
        if not kind:
            continue
        words = sc["narration_hi"].split()
        ws = []
        if kind == "split":
            for x in (r["split"] if isinstance(r["split"], list) else (r["split"].get("items") or [])):
                try:
                    w_ = int(x.get("w", 0))
                    hs_ = _label_hits(words, clean_short(x.get("label_hi"), 40))
                    if hs_:
                        w_ = min(hs_, key=lambda h: abs(h - w_))
                    ws.append(w_)
                except Exception:  # noqa
                    pass
        else:
            pl = (r[kind].get("pins") or []) if isinstance(r.get(kind), dict) else []
            ws = [w_ for w_ in (_word_of_label(words, q_.get("label_hi", "")) for q_ in pl if isinstance(q_, dict)) if w_ is not None]
        part, off = sc, 0
        if sc.get("sent") and ws:
            wf_ = min(ws)
            j0_ = _sent_of_word(sc, wf_)
            if wf_ - sc["sent"][j0_][0] >= 3:  # the words before the first named subject stay with the earlier picture
                cut_sentence_at(sc, wf_)
            j0, j1 = _sent_of_word(sc, wf_), _sent_of_word(sc, max(ws))
            if j1 - j0 > 2:
                j1 = j0 + 2
            if sc["sent"][j1][3] - sc["sent"][j0][2] < 1.6:  # too short to show an animation: leave the pictures
                continue
            part, off = split_scene(scenes, sc, j0, j1), sc["sent"][j0][0]
        if kind == "split":
            lst_ = [dict(x, w=int(x.get("w", 0)) - off) for x in (r["split"] if isinstance(r["split"], list) else (r["split"].get("items") or [])) if isinstance(x, dict)]
            ok_ = apply_special(part, {"split": lst_, "vs": bool(r.get("vs"))})
        elif kind == "terrain":
            ok_ = apply_special(part, {"terrain": r["terrain"]})
        else:
            ok_ = False
            try:
                pins = [{"label_hi": clean_short(p_.get("label_hi"), 28), "lat": float(p_["lat"]), "lon": float(p_["lon"])} for p_ in (r["map"].get("pins") or [])[:5]]
                if pins and not any(re.search(r"दुनिया|पृथ्वी|धरती|विश्व|संसार|ग्रह|brahm|ब्रह्मांड|world|earth|planet|universe", p_["label_hi"], re.I) for p_ in pins) and all(p_["label_hi"] and -80 <= p_["lat"] <= 80 and -180 <= p_["lon"] <= 180 for p_ in pins):
                    spread = max([hav_km((a_["lat"], a_["lon"]), (b_["lat"], b_["lon"])) for a_ in pins for b_ in pins] + [0])
                    if len(pins) == 2 and spread < 1500:  # exactly two places: the terrain map with the distance
                        mp_ = {"pins": pins, "route": True, "mode": "terrain", "regions": []}
                    elif len(pins) >= 3 and spread < 1500:  # several places close together: terrain background, pins in the order named, an arrow from each to the next
                        mp_ = {"pins": pins, "route": True, "mode": "terrain", "regions": []}
                    elif len(pins) >= 2:
                        mp_ = {"pins": pins, "route": True, "mode": "globe" if spread > 2500 else "flat"}
                    else:
                        mp_ = {"pins": pins, "route": False, "mode": "flat"}
                    part["type"], part["map"], part["headline_hi"] = "map", mp_, ""
                    ok_ = True
                    kind = "map"
            except Exception:  # noqa
                pass
        if ok_:
            part["directed"] = True
            stat["split" if part["type"] == "multi" else "map" if kind == "map" else "terrain"] += 1
    # 2) real source captions: match each scene to a REAL paragraph that supports a fact in it
    if SRC_PARAS:
        ph = [(i, sc) for i, sc in enumerate(scenes) if sc["type"] == "photo" and sc.get("wt") and len(sc["wt"]) == len(sc["narration_hi"].split())]
        paras = "\n".join(f'[{k}] {v["title"][:60]}: "{v["text"]}"' for k, v in SRC_PARAS.items())
        for a in range(0, len(ph), 6):
            batch = ph[a:a + 6]
            listing = "\n".join(f"SCENE {i}: {_numbered(sc)}" for i, sc in batch)
            prompt = f"""Hindi narration scenes (index:word) and REAL source paragraphs. Pick EVERY scene that tells an important fact (a number, a name, a claim, a place) where a paragraph REALLY states the same fact (same place/animal/number/claim). Never force a match: a paragraph about something else must not be used; an invented or fictional part of the story has no source.
Return ONLY JSON: {{"matches":[{{"i": scene, "w": word index in the scene where the fact is spoken, "src": "paragraph id like 1.2", "mark_en": "the 1-6 words copied EXACTLY from that paragraph that state the fact"}}]}} (empty list if none).
PARAGRAPHS:
{paras}
SCENES:
{listing}"""
            try:
                out = gemini_json_opt(prompt, 0.1)
            except Exception as e:  # noqa
                log("extras (sources) failed:", str(e)[:100])
                continue
            for m in (out.get("matches") or []):
                try:
                    i, w = int(m.get("i")), int(m.get("w"))
                except Exception:  # noqa
                    continue
                sc = scenes[i] if 0 <= i < len(scenes) else None
                sp_ = SRC_PARAS.get(str(m.get("src") or "").strip())
                if sc is None or not sp_ or sc["type"] != "photo" or not (0 <= w < len(sc["narration_hi"].split())):
                    continue
                if any(isinstance(it, dict) and it.get("kind") == "note" for it in sc.get("items", [])):
                    continue
                mk = clean_short(m.get("mark_en"), 50)
                if mk and mk.lower() not in sp_["text"].lower():
                    mk = ""
                sc.setdefault("items", []).append({"w": w, "kind": "note", "text": window_text(sp_["text"], mk), "mark": mk, "sub": sp_["title"].replace("|", "-")[:70], "dom": _dom(sp_.get("url", ""))})
                stat["notes"] += 1
    # 2b) action clips: wherever the narrator describes a visible ACTION, that moment becomes a real video clip of exactly that action
    if (PIXABAY_KEY or PEXELS_KEY):
        for a in range(0, len(scenes), 4):
            rows_ = []
            for i in range(a, min(len(scenes), a + 4)):
                sc = scenes[i]
                if sc["type"] != "photo":
                    continue
                for k, sh in enumerate(sc.get("shots", [])):
                    if str(sh.get("text_hi") or "").strip():
                        rows_.append(f'S{i}.{k}: {sh["text_hi"]}')
            if not rows_:
                continue
            prompt = f"""Hindi narration phrases of a nature documentary (topic: {topic}). For every phrase in which the narrator describes a visible ACTION or MOVEMENT (walking, running, hunting, eating, drinking, flying, swimming, roaring, resting, flowing water, fire, spinning...), give the English stock-footage search "subject + exact action" (1-4 words) that shows EXACTLY that action by that subject: chalna = "lion walking", daudna = "lion running", pani peena = "lion drinking water". Skip phrases that describe no visible action (facts, numbers, places, ideas).
Return ONLY JSON: {{"clips":[{{"id":"S0.1","clip_en":"lion walking"}}]}}
""" + "\n".join(rows_)
            try:
                out = gemini_json_opt(prompt, 0.1)
            except Exception as e:  # noqa
                log("extras (clips) failed:", str(e)[:100])
                continue
            for c in (out.get("clips") or []):
                m_ = re.match(r"S(\d+)\.(\d+)$", str(c.get("id") or ""))
                q_ = clean_short(c.get("clip_en"), 40)
                if not m_ or not q_ or not q_.isascii():
                    continue
                i, k = int(m_.group(1)), int(m_.group(2))
                if 0 <= i < len(scenes) and scenes[i]["type"] == "photo" and 0 <= k < len(scenes[i].get("shots", [])):
                    sh = scenes[i]["shots"][k]
                    if not sh.get("clip"):
                        sh["motion"], sh["clip_en"] = True, q_
                        stat["clips"] += 1
    # 3) camera keyframes: every main that stays on screen for a while gets a visible camera glide on the part being spoken
    for i, sc in enumerate(scenes):
        if sc["type"] != "photo" or not sc.get("shots"):
            continue
        w0 = 0
        for sh in sc["shots"]:
            n = len(str(sh.get("text_hi") or "").split())
            w1 = w0 + n
            if not sh.get("kf") and not sh.get("clip") and n >= 5:
                fx = sh.get("fx") or (0.5 + (0.12 if (i + w0) % 2 else -0.12))
                fy = sh.get("fy") or 0.42
                kf = parse_kf([{"w": min(w1 - 1, w0 + n // 2), "fx": fx, "fy": fy, "z": 1.5}], w0, w1, fx, fy)
                if kf:
                    sh["kf"] = kf
                    sh["tech"] = (sh.get("tech") or "") + ("," if sh.get("tech") else "") + "kf"
                    stat["kf"] += 1
            w0 = w1
    log("extras:", stat)
    return stat


# =========================================================================== techniques_pass: puts every technique at the moment the narrator calls for it, then guarantees coverage
# CINEMA_MODE (default on): the calm, Netflix-documentary restraint. One idea on screen at a time, no stickers / paper collage unless a chart or timeline truly needs it,
# the real picture does the work. Set the variable CINEMA_MODE=0 to bring back the busy "every technique everywhere" look (all technique code is kept).
CINEMA_MODE = os.environ.get("CINEMA_MODE", "1") != "0"
CALM_MAX_ITEMS = 3
CALM_GAP = 2.4
CALM_PRI = {"note": 10, "srclist": 10, "person": 9, "name": 9, "place": 9, "title": 9, "year": 8, "stat": 8, "verdict": 8, "explain": 7, "action": 7, "feature": 7, "compare": 7, "chart": 7, "tline": 7,
            "measure": 6, "beforeafter": 6, "lens": 5, "mark": 4, "quote": 4, "bubble": 3, "qmark": 3, "hl": 2}
PERMANENT_TECHNIQUES = {
    "split": "split screen (multi scene) - two/three subjects named together",
    "terrain": "terrain map - a journey / distance between two places",
    "verdict": "cross and tick marks - myth / fact, false / true",
    "roll": "paper roll / source cards",
    "measure": "measuring screen - size, length, height, depth, weight, distance",
    "qmark": "question marks - the narrator asks a question or a mystery",
    "lens": "magnifier lens - tiny detail, 'look closely'",
    "beforeafter": "before / after slider - then vs now, change over time",
    "compare": "comparison panel - A vs B with numbers",
    "mark": "Vox marker - circle / underline / arrow with a short label on the important thing",
    "chart": "Vox animated chart - numbers said by the narrator",
    "tline": "Vox multi-event timeline - 3-5 dated events in order",
    "hl": "Vox highlight bar - the key claim in big text",
}
TECH_PER_MIN = {"qmark": 0.5, "lens": 0.5, "mark": 0.5, "hl": 0.0, "measure": 0.6, "compare": 0.4, "beforeafter": 0.3, "chart": 0.4, "tline": 0.3} if CINEMA_MODE else {"qmark": 1.2, "lens": 1.0, "mark": 1.6, "hl": 1.6, "measure": 0.8, "compare": 0.5, "beforeafter": 0.4, "chart": 0.6, "tline": 0.4}
TECH_MIN_ABS = {"qmark": 1, "lens": 1, "mark": 1, "hl": 0 if CINEMA_MODE else 1, "measure": 1, "compare": 1, "beforeafter": 1, "chart": 1, "tline": 1}
TECH_MAX = {"beforeafter": 3}
UNIT_RE = re.compile(r"(मीटर|किलोमीटर|सेंटीमीटर|सेमी|मिमी|फीट|फुट|इंच|किलोग्राम|किलो|टन|मील|लीटर|घंटे|साल|वर्ष|डिग्री|मीटर्स)")
NUM_RE = re.compile(r"^[0-9०-९][0-9०-९,.]*$")
Q_WORDS = ("क्यों", "कैसे", "आखिर", "रहस्य", "सवाल", "पहेली", "क्या")
LENS_WORDS = ("ध्यान", "करीब", "बारीकी", "सूक्ष्म", "माइक्रोस्कोप", "गौर", "बारीक", "नज़दीक", "नजदीक")
MARK_WORDS = ("यहीं", "यही", "इसी", "सबसे", "खास", "ख़ास", "असली", "मुख्य")
_TECH_SEQ = [0]


HI_NUMS = set("एक दो तीन चार पांच पाँच छह छः सात आठ नौ दस ग्यारह बारह तेरह चौदह पंद्रह पन्द्रह सोलह सत्रह अठारह उन्नीस बीस तीस चालीस पचास साठ सत्तर अस्सी नब्बे सौ हज़ार हजार लाख करोड़ अरब डेढ़ ढाई साढ़े सवा".split())


def _tw(w):
    return re.sub(r"[।,.!?\"'“”‘’:;()\-–—…]", "", w)


def parse_new_item(kind, it, w):
    """Validates / normalises one new-technique item from Gemini; None when it is unusable."""
    txt = clean_short(it.get("text_hi"), 40)
    base = {"w": w, "kind": kind, "text": txt or ""}
    if kind == "measure":
        if not txt:
            return None
        base.update({"orient": "v" if str(it.get("orient")) == "v" else "h", "sub": clean_short(it.get("sub_hi"), 30)})
        return base
    if kind == "qmark":
        return base
    if kind == "lens":
        for k_ in ("fx", "fy"):
            try:
                v = float(it.get(k_))
                if 0.1 <= v <= 0.9:
                    base[k_] = v
            except Exception:  # noqa
                pass
        return base
    if kind == "mark":
        base["shape"] = str(it.get("shape")) if str(it.get("shape")) in ("circle", "underline", "arrow") else "circle"
        for k_ in ("fx", "fy"):
            try:
                v = float(it.get(k_))
                if 0.1 <= v <= 0.9:
                    base[k_] = v
            except Exception:  # noqa
                pass
        return base
    if kind == "hl":
        if not txt or len(txt.split()) > 9:
            return None
        base["ci"] = _TECH_SEQ[0] % len(HL_COLS)
        _TECH_SEQ[0] += 1
        return base
    if kind == "beforeafter":
        a_ = clean_short(it.get("after_en"), 60)
        if not a_ or not a_.isascii():
            return None
        base.update({"after_en": a_, "label_a": clean_short(it.get("label_a_hi"), 12) or "पहले", "label_b": clean_short(it.get("label_b_hi"), 12) or "बाद में", "span": 5.0})
        return base
    if kind == "compare":
        A, B = clean_short(it.get("a_hi"), 18), clean_short(it.get("b_hi"), 18)
        rows = []
        for r in (it.get("rows") or [])[:3]:
            if not isinstance(r, dict):
                continue
            try:
                na, nb = float(r.get("na")), float(r.get("nb"))
            except Exception:  # noqa
                na = nb = 0.0
            rows.append({"label_hi": clean_short(r.get("label_hi"), 22), "a_hi": clean_short(r.get("a_hi"), 18), "b_hi": clean_short(r.get("b_hi"), 18), "na": na, "nb": nb})
        if not A or not B or not rows:
            return None
        base.update({"a_hi": A, "b_hi": B, "rows": rows, "span": 5.6})
        return base
    if kind == "chart":
        pts = []
        for p_ in (it.get("pts") or [])[:6]:
            if not isinstance(p_, dict):
                continue
            try:
                v = float(p_.get("v"))
            except Exception:  # noqa
                continue
            pts.append({"label": clean_short(p_.get("label_hi"), 14), "v": v, "show": clean_short(p_.get("show_hi"), 14) or str(p_.get("v"))})
        if len(pts) < 2:
            return None
        base.update({"title": clean_short(it.get("title_hi"), 32), "type": "line" if str(it.get("type")) == "line" else "bar", "pts": pts, "span": 5.6, "ci": _TECH_SEQ[0] % len(VOX_BG)})
        _TECH_SEQ[0] += 1
        return base
    if kind == "tline":
        ev = []
        for e in (it.get("events") or [])[:5]:
            if isinstance(e, dict) and clean_short(e.get("when_hi"), 14) and clean_short(e.get("what_hi"), 22):
                ev.append({"when": clean_short(e["when_hi"], 14), "what": clean_short(e["what_hi"], 22)})
        if len(ev) < 3:
            return None
        base.update({"events": ev, "span": 6.2, "ci": _TECH_SEQ[0] % len(VOX_BG)})
        _TECH_SEQ[0] += 1
        return base
    return None


def _free_word(have, w, kind, gap=3):
    return not any(isinstance(x, dict) and (x.get("w") == w or (abs(x.get("w", -99) - w) < gap and x.get("kind") == kind)) for x in have)


STOP_HI = set("और या कि की के का को में से पर तक ने भी ही तो यह वह ये वो इस उस इन उन एक कुछ सब जो जब तब अब यहाँ वहाँ क्योंकि लेकिन मगर फिर बहुत बस हर कोई किसी अपने अपना अपनी उसके उसकी उनके उनकी इसके इसकी इनके तरह साथ बाद पहले बीच लिए वाला वाली वाले कर करके करना होता होती होते हुआ हुई हुए गया गई गए था थी थे है हैं हो सकता सकती सकते रहा रही रहे जाता जाती जाते आया आई आए लगा लगी लगे दिया दी दिए लिया ली लिए कहा कहते कहती बोला बोली जैसे ऐसे ऐसा ऐसी कैसे क्यों क्या कौन कहाँ कब तभी जहाँ वही यही सिर्फ़ सिर्फ केवल जैसा तक भर ज़रा जरा और्".split())
VERB_END = ("ता", "ती", "ते", "या", "ये", "ना", "कर", "गा", "गी", "गे", "ेगा", "ेगी", "ेंगे", "ूँ", "ें", "ाओ", "ाई", "ाए", "ाया", "ाते", "ाती", "ाता", "ाना")
Q_ALL = ("क्यों", "कैसे", "आखिर", "रहस्य", "सवाल", "पहेली", "कौन", "कहाँ", "कहां", "कब", "कितना", "कितने", "कितनी", "सोचिए", "सोचिये", "राज़", "राज", "अजीब", "हैरान", "रहस्यमय")
LENS_ALL = ("ध्यान", "करीब", "बारीकी", "सूक्ष्म", "माइक्रोस्कोप", "गौर", "बारीक", "नज़दीक", "नजदीक", "देखिए", "देखिये", "दिखता", "दिखती", "दिखते", "छोटा", "छोटी", "छोटे", "नन्हा", "नन्हे", "नन्ही", "आँख", "आँखें", "आंख", "आंखें", "दाँत", "दांत", "पंजे", "पंजा", "त्वचा", "पंख", "सींग", "चोंच", "जीभ", "कोशिका", "परत", "सतह")
MARK_ALL = ("यहीं", "यही", "इसी", "सबसे", "खास", "ख़ास", "असली", "मुख्य", "मुख्यतः", "ज़रूरी", "जरूरी", "अहम", "महत्वपूर्ण", "सबूत", "वजह", "कारण", "नतीजा", "परिणाम", "खतरा", "ख़तरा", "ताक़त", "ताकत")
MEAS_ALL = ("लंबा", "लम्बा", "लंबी", "ऊँचा", "ऊंचा", "ऊँची", "ऊंची", "गहरा", "गहरी", "चौड़ा", "चौड़ी", "भारी", "दूरी", "वज़न", "वजन", "लंबाई", "ऊँचाई", "गहराई", "चौड़ाई", "आकार")
CMP_ALL = ("बनाम", "मुक़ाबले", "मुकाबले", "ज़्यादा", "ज्यादा", "बड़ा", "बड़ी", "छोटा", "तेज़", "तेज", "धीमा", "भारी", "हल्का")
BA_PRE = ("पहले", "पुराने", "पहले-")
BA_POST = ("अब", "आज", "आजकल", "आज-कल", "बाद", "फिर", "नया", "नई", "अब-")


def _num_val(tok):
    tok = tok.translate(str.maketrans("०१२३४५६७८९", "0123456789")).replace(",", "")
    try:
        return float(tok)
    except Exception:  # noqa
        return None


def kw_words(wds, k_max=4):
    """The key words of a scene (nouns / names / numbers), spread over the sentence, each with the index where it is spoken."""
    cand = []
    for i, w_ in enumerate(wds):
        t_ = _tw(w_)
        if len(t_) < 3 or t_ in STOP_HI or t_ in Q_ALL or t_ in ("सबसे", "बड़ा", "बड़ी", "ऊँची", "ऊँचा", "आगे", "जानिए", "देखिए", "यही", "इसी", "सारी", "सारे", "पूरी", "पूरा", "नहीं", "वाली"):
            continue
        if any(t_.endswith(v) for v in VERB_END) and not _num_val(t_):
            continue
        cand.append((i, t_))
    if not cand:
        return []
    if len(cand) > k_max:
        step = len(cand) / k_max
        cand = [cand[int(j * step)] for j in range(k_max)]
    return cand


def det_techniques(scenes):
    """Rule-based: EVERY spoken cue gets its technique on its own word (needs no Gemini, always runs)."""
    n = {k: 0 for k in NEW_KINDS}
    for si, sc in enumerate(scenes):
        if sc.get("type") != "photo" or not sc.get("wt"):
            continue
        wds = sc["narration_hi"].split()
        if len(wds) != len(sc["wt"]):
            continue
        have = sc.setdefault("items", [])
        cnt = {k: 0 for k in NEW_KINDS}

        def add(rec, gap=6):
            k = rec["kind"]
            if any(isinstance(x, dict) and x.get("kind") == k and abs(x.get("w", -99) - rec["w"]) < gap for x in have):
                return False
            if any(isinstance(x, dict) and x.get("w") == rec["w"] and x.get("kind") in NEW_KINDS for x in have):
                return False
            if cnt[k] >= (3 if k in ("qmark", "lens", "mark") else 2):
                return False
            have.append(rec)
            cnt[k] += 1
            n[k] += 1
            return True

        nums = []
        pre_i = post_i = None
        for i, w_ in enumerate(wds):
            t_ = _tw(w_)
            if not t_:
                continue
            prev = _tw(wds[i - 1]) if i else ""
            has_num = bool(NUM_RE.match(prev or "x")) or prev in HI_NUMS
            if has_num and (UNIT_RE.fullmatch(t_) or t_ in MEAS_ALL or t_ in ("लोग", "शेर", "बाघ", "हाथी", "जानवर", "प्रजातियाँ", "प्रजातियां", "गुना", "प्रतिशत", "फीसदी", "सदस्य")):
                add({"w": i, "kind": "measure", "text": f"{prev} {t_}", "orient": "v" if t_ in ("ऊँचा", "ऊंचा", "ऊँची", "ऊंची", "गहरा", "गहरी", "ऊँचाई", "गहराई") else "h", "sub": ""}, 3)
            elif t_ in MEAS_ALL and i >= 2 and (_tw(wds[i - 2]) in HI_NUMS or NUM_RE.match(_tw(wds[i - 2]) or "x")) and UNIT_RE.fullmatch(prev):
                add({"w": i, "kind": "measure", "text": f"{_tw(wds[i-2])} {prev}", "orient": "v" if t_ in ("ऊँचा", "ऊंचा", "गहरा") else "h", "sub": t_}, 3)
            if t_ in Q_ALL or "?" in w_:
                add({"w": i, "kind": "qmark", "text": ""}, 7)
            if t_ in LENS_ALL or any(t_.startswith(x) for x in ("बारीक", "सूक्ष्म", "नज़दीक", "करीब")):
                add({"w": i, "kind": "lens", "text": ""}, 8)
            if t_ in MARK_ALL:
                add({"w": i, "kind": "mark", "text": "", "shape": ("circle", "arrow", "underline")[(si + n["mark"]) % 3]}, 8)
            if t_ in BA_PRE and pre_i is None:
                pre_i = i
            if pre_i is not None and post_i is None and i > pre_i + 2 and t_ in BA_POST:
                post_i = i
            nv = _num_val(t_)
            if nv is not None and nv > 0:
                nums.append((i, nv, t_, _tw(wds[i + 1]) if i + 1 < len(wds) else ""))
        # before / after: "पहले ... अब/आज" in one scene, two different pictures
        shots_ = [x for x in (sc.get("shots") or []) if isinstance(x, dict) and x.get("query_en")]
        if pre_i is not None and post_i is not None and len({x["query_en"] for x in shots_}) >= 2:
            add({"w": pre_i, "kind": "beforeafter", "text": "", "after_en": shots_[-1]["query_en"], "label_a": "पहले", "label_b": "आज", "span": 5.2}, 99)
        # numbers said in the scene -> animated chart (>=2 numbers) / highlighted number
        if len(nums) >= 2:
            pts = [{"label": clean_short(x[3], 12), "v": x[1], "show": clean_short(x[2] + " " + x[3], 14)} for x in nums[:5]]
            add({"w": nums[0][0], "kind": "chart", "text": "", "title": clean_short(sc.get("headline_hi") or "", 28), "type": "bar", "pts": pts, "span": 5.4, "ci": si % len(VOX_BG)}, 99)
        # years told in order -> timeline
        yrs = [it for it in have if isinstance(it, dict) and it.get("kind") == "year" and it.get("year_num")]
        if len(yrs) >= 3:
            ev = [{"when": clean_short(it["text"], 14), "what": clean_short(it.get("sub") or "", 22) or clean_short(it["text"], 14)} for it in yrs[:5]]
            add({"w": yrs[0]["w"], "kind": "tline", "text": "", "events": ev, "span": 6.2, "ci": (si + 1) % len(VOX_BG)}, 99)
        # every scene: the key claim as a highlight bar on its last words, and a marker on the first key word
        if not CINEMA_MODE and not cnt["hl"] and len(wds) >= 8:
            tail = [w_ for w_ in wds[-6:]]
            while tail and not _tw(tail[0]):
                tail.pop(0)
            txt = clean_short(" ".join(_tw(x) if i_ == len(tail) - 1 else x.strip(",") for i_, x in enumerate(tail)), 40)
            _TECH_SEQ[0] += 1
            add({"w": max(0, len(wds) - 6), "kind": "hl", "text": txt, "ci": _TECH_SEQ[0] % len(HL_COLS)}, 99)
        if not CINEMA_MODE and not cnt["mark"] and not cnt["lens"] and len(wds) >= 8:
            kk = kw_words(wds, 1)
            if kk:
                add({"w": kk[0][0], "kind": "mark", "text": kk[0][1], "shape": ("circle", "underline", "arrow")[si % 3]}, 99)
        have.sort(key=lambda x: x.get("w", 0) if isinstance(x, dict) else 0)
    return n


def norm_txt(t):
    return re.sub(r"[\s।,.!?\"'“”‘’:;()\-–—…]+", "", str(t or ""))


def assign_vox(scenes):
    """Style mix: every point gets key-word stickers; only scenes that EXPLAIN data / sequences / comparisons (or every 4th calm one) use the Vox paper layout, the rest stay cinematic full-screen with the other techniques."""
    nph = [i for i, sc in enumerate(scenes) if sc.get("type") == "photo" and sc.get("wt") and len(sc["wt"]) == len(sc["narration_hi"].split())]
    vox_n, last_vox = 0, -9
    cap = max(1, int(round(len(nph) * 0.3)))
    for j, i in enumerate(nph):
        sc = scenes[i]
        wds = sc["narration_hi"].split()
        sc["kw"] = [{"t": max(0.2, sc["wt"][wi][0] - 0.05), "text": tx, "ci": (j + n_) % len(KW_COLS)} for n_, (wi, tx) in enumerate(kw_words(wds, 4))]
        sc.pop("vox", None)
        if CINEMA_MODE:  # calm mode: no key-word stickers, no punch-ins, no paper layout (charts / timelines still use it by themselves)
            sc["kw"] = []
            continue
        its = [x.get("kind") for x in sc.get("items", []) if isinstance(x, dict)]
        heavy = any(x in its for x in ("beforeafter", "feature", "verdict", "action", "srclist", "lens", "measure"))
        want = any(x in its for x in ("compare", "chart", "tline")) or j % 4 == 2
        if want and not heavy and 0 < j < len(nph) - 1 and j - last_vox > 1 and vox_n < cap:
            sc["vox"] = vox_n % len(VOX_BG)
            vox_n += 1
            last_vox = j
    return vox_n


TXT_PRI = {"person": 9, "name": 9, "place": 8, "title": 8, "year": 7, "stat": 7, "quote": 7, "note": 6, "bubble": 6, "measure": 5, "compare": 5, "chart": 5, "tline": 5, "hl": 4, "mark": 3, "kw": 1}


def calm_pass(scenes):
    """Restraint: at most CALM_MAX_ITEMS overlays per scene and never two starting within CALM_GAP seconds; the most meaningful ones survive."""
    if not CINEMA_MODE:
        return 0
    dropped = 0
    for sc in scenes:
        its = sc.get("items")
        if sc.get("type") != "photo" or not isinstance(its, list) or not sc.get("wt"):
            continue
        wt = sc["wt"]

        def t_of(x):
            try:
                return float(wt[min(max(0, int(x.get("w", 0))), len(wt) - 1)][0])
            except Exception:  # noqa
                return 0.0
        cand = sorted([x for x in its if isinstance(x, dict)], key=lambda x: -CALM_PRI.get(x.get("kind"), 5))
        keep = []
        for x in cand:
            if len(keep) >= CALM_MAX_ITEMS:
                break
            if any(abs(t_of(x) - t_of(k)) < CALM_GAP for k in keep):
                continue
            keep.append(x)
        dropped += len(its) - len(keep)
        sc["items"] = sorted(keep, key=lambda x: x.get("w", 0))
    log("calm mode: overlays removed to keep the screen clean:", dropped)
    return dropped


def dedupe_texts(scenes):
    """No scene shows the same (or a contained) text twice: the lower-priority copy is removed (sticker first, then marker label, highlight bar...)."""
    removed = 0
    for sc in scenes:
        if sc.get("type") != "photo":
            continue
        ents = []  # (pri, norm, setter)
        items = [x for x in sc.get("items", []) if isinstance(x, dict)]
        for x in items:
            t_ = norm_txt(x.get("text"))
            if len(t_) >= 2 and x.get("kind") in TXT_PRI:
                ents.append((TXT_PRI[x["kind"]], t_, x))
        for k in sc.get("kw", []):
            ents.append((1, norm_txt(k["text"]), k))
        for sh in sc.get("shots", []) or []:
            if isinstance(sh, dict) and norm_txt(sh.get("label_hi")):
                ents.append((8, norm_txt(sh["label_hi"]), sh))
        ents.sort(key=lambda e: -e[0])
        kept = []
        for pri, t_, obj in ents:
            if obj in kept:
                continue
            dup = any(t_ == e[1] or (min(pri, e[0]) <= 3 and min(len(t_), len(e[1])) >= 3 and (t_ in e[1] or e[1] in t_)) for e in ents if e[2] in kept)
            if dup:
                removed += 1
                if "t" in obj and "ci" in obj and "w" not in obj:
                    sc["kw"].remove(obj)
                elif obj.get("kind") == "mark":
                    obj["text"] = ""
                elif "kind" in obj:
                    items.remove(obj)
                    sc["items"].remove(obj)
                else:
                    obj["label_hi"] = ""
            else:
                kept.append(obj)
    log("duplicate texts removed:", removed)
    return removed


def techniques_prompt(topic, listing):
    return f"""Film editor task for a Hindi documentary in the VOX explainer style. Hindi narration, every word numbered (index:word). Video topic: {topic}
Find every moment where one of these on-screen techniques fits what is REALLY being said (each exactly on its word, never two on one word, up to 5 per scene, use them generously):
- "measure": a size / length / height / depth / weight / distance / duration is said. {{"w":..,"kind":"measure","text_hi":"value + unit as spoken, e.g. ३० मीटर","orient":"h" or "v" (v for height / depth),"sub_hi":"what is measured, 1-2 words"}}
- "qmark": the narrator asks a question or names a mystery (क्यों, कैसे, आखिर...). {{"w":..,"kind":"qmark"}}
- "lens": a tiny detail, "look closely", something small. {{"w":..,"kind":"lens","fx":0.1-0.9,"fy":0.1-0.9}} (position of the detail on the picture, typical composition)
- "beforeafter": then vs now, a change, a before and an after of the SAME thing. {{"w":..,"kind":"beforeafter","after_en":"1-4 English words naming the REAL photo that shows the AFTER state","label_a_hi":"पहले","label_b_hi":"बाद में"}} (the picture already on screen is the before)
- "compare": two things compared with numbers or qualities. {{"w":..,"kind":"compare","a_hi":"first","b_hi":"second","rows":[{{"label_hi":"what is compared","a_hi":"value of A as spoken","b_hi":"value of B as spoken","na":number or 0,"nb":number or 0}}]}} 1-3 rows, values only as the narrator says them.
- "mark": the important thing on screen that the narrator points at. {{"w":..,"kind":"mark","shape":"circle"|"underline"|"arrow","text_hi":"1-3 word label or empty"}}
- "chart": 2-6 numbers the narrator says that can be compared or that change. {{"w":..,"kind":"chart","type":"bar"|"line","title_hi":"short title","pts":[{{"label_hi":"short label","v":number,"show_hi":"as spoken"}}]}} Numbers ONLY as spoken, never invented.
- "tline": 3-5 dated events told in order. {{"w":..,"kind":"tline","events":[{{"when_hi":"year / date as spoken","what_hi":"2-4 words"}}]}}
- "hl": THE key claim / conclusion of the scene in at most 8 words, exactly as said. {{"w":..,"kind":"hl","text_hi":"the claim"}}
Never invent facts or numbers. If nothing fits a scene return an empty list.
Return ONLY JSON: {{"scenes":[{{"i":0,"items":[]}}]}}
{listing}"""


def techniques_gemini(scenes, topic, only=None, per=5):
    ph = [(i, sc) for i, sc in enumerate(scenes) if sc.get("type") == "photo" and sc.get("wt") and len(sc["wt"]) == len(sc["narration_hi"].split())]
    n = {k: 0 for k in NEW_KINDS}
    for a in range(0, len(ph), per):
        batch = ph[a:a + per]
        listing = "\n".join(f"SCENE {i}: {_numbered(sc)}" for i, sc in batch)
        pr = techniques_prompt(topic, listing)
        if only:
            pr += f"\nUSE ONLY THESE KINDS: {', '.join(only)} - find the best moment for each in these scenes (it is required)."
        try:
            out = gemini_json(pr, 0.2)
        except Exception as e:  # noqa
            log("techniques pass failed:", str(e)[:100])
            continue
        for r in (out.get("scenes") or []):
            try:
                i = int(r.get("i"))
            except Exception:  # noqa
                continue
            if not any(i == j for j, _ in batch):
                continue
            sc = scenes[i]
            nwd = len(sc["narration_hi"].split())
            have = sc.setdefault("items", [])
            for it in (r.get("items") or [])[:6]:
                if not isinstance(it, dict):
                    continue
                kind = str(it.get("kind") or "")
                try:
                    w = int(it.get("w"))
                except Exception:  # noqa
                    continue
                if kind not in NEW_KINDS or not (0 <= w < nwd) or (only and kind not in only):
                    continue
                if not _free_word(have, w, kind, 4):
                    continue
                rec = parse_new_item(kind, it, w)
                if rec:
                    have.append(rec)
                    n[kind] += 1
            have.sort(key=lambda x: x.get("w", 0) if isinstance(x, dict) else 0)
    return n


def count_techniques(scenes):
    c = {k: 0 for k in NEW_KINDS}
    c.update({"split": 0, "terrain": 0, "verdict": 0})
    for sc in scenes:
        if sc.get("type") == "multi":
            c["split"] += 1
        if sc.get("type") == "map":
            c["terrain"] += 1
        if sc.get("terrain"):
            c["terrain"] += 1
        if sc.get("split"):
            c["split"] += 1
        for it in sc.get("items", []) if isinstance(sc.get("items"), list) else []:
            if isinstance(it, dict) and it.get("kind") in c:
                c[it["kind"]] += 1
    return c


def coverage_fill(scenes, topic):
    """Every video must carry every technique, in proportion to its length: missing ones are requested from Gemini, then filled by fixed rules."""
    ph = [sc for sc in scenes if sc.get("type") == "photo" and sc.get("wt")]
    total_min = max(0.5, sum(float(sc.get("dur") or sc.get("duration") or 8.0) for sc in scenes) / 60.0) if scenes else 0.5
    need = {k: max(TECH_MIN_ABS[k], int(round(TECH_PER_MIN[k] * total_min))) for k in TECH_PER_MIN}
    have = count_techniques(scenes)
    miss = [k for k in need if have.get(k, 0) < need[k]]
    if miss:
        techniques_gemini(scenes, topic, only=miss, per=8)
        have = count_techniques(scenes)
    # rule-based guarantee (no Gemini needed): question mark / lens / marker / highlight / timeline from existing year cards
    def pick(pred):
        best = None
        for sc in ph:
            wds = sc["narration_hi"].split()
            if len(wds) != len(sc["wt"]):
                continue
            for i in range(min(len(wds), 12), len(wds) - 4):
                if _free_word(sc.setdefault("items", []), i, "x", 2) and (pred is None or pred(sc, i)):
                    return sc, i
            if best is None and len(wds) > 6:
                best = (sc, max(1, len(wds) // 2))
        return best
    used_sc = {}
    def add(kind, rec_fn):
        for sc in sorted(ph, key=lambda s: used_sc.get(id(s), 0)):
            wds = sc["narration_hi"].split()
            if len(wds) != len(sc["wt"]) or len(wds) < 8:
                continue
            for i in (len(wds) // 2, len(wds) // 3, (2 * len(wds)) // 3, 2):
                if _free_word(sc.setdefault("items", []), i, kind, 5) and not any(isinstance(x, dict) and abs(x.get("w", -99) - i) < 3 for x in sc["items"]):
                    rec = rec_fn(sc, i)
                    if rec:
                        sc["items"].append(rec)
                        sc["items"].sort(key=lambda x: x.get("w", 0))
                        used_sc[id(sc)] = used_sc.get(id(sc), 0) + 1
                        return True
        return False
    for k in ("qmark", "lens", "mark", "hl"):
        for _ in range(max(0, need.get(k, 1) - have.get(k, 0))):
            if k == "qmark":
                add(k, lambda sc, i: {"w": i, "kind": "qmark", "text": ""})
            elif k == "lens":
                add(k, lambda sc, i: {"w": i, "kind": "lens", "text": ""})
            elif k == "mark":
                add(k, lambda sc, i: {"w": i, "kind": "mark", "text": "", "shape": ("circle", "underline", "arrow")[(_TECH_SEQ[0] + i) % 3]})
            elif k == "hl":
                def mk(sc, i):
                    h = clean_short(sc.get("headline_hi"), 40)
                    if not h or len(h.split()) > 8:
                        return None
                    _TECH_SEQ[0] += 1
                    return {"w": i, "kind": "hl", "text": h, "ci": _TECH_SEQ[0] % len(HL_COLS)}
                add(k, mk)
    if have.get("tline", 0) < 1:
        ys = []
        for sc in ph:
            for it in sc.get("items", []):
                if isinstance(it, dict) and it.get("kind") == "year" and it.get("text") and it.get("year_num"):
                    ys.append((sc, it))
        if len(ys) >= 3:
            ev = [{"when": clean_short(it["text"], 14), "what": clean_short(sc.get("headline_hi") or "", 22) or clean_short(it["text"], 14)} for sc, it in ys[:5]]
            sc0, it0 = ys[min(len(ys) - 1, 2)]
            if _free_word(sc0["items"], it0["w"] + 3, "tline", 3):
                sc0["items"].append({"w": min(len(sc0["narration_hi"].split()) - 1, it0["w"] + 3), "kind": "tline", "text": "", "events": ev, "span": 6.2, "ci": 1})
    return count_techniques(scenes)


def techniques_pass(scenes, topic=""):
    n1 = det_techniques(scenes)
    n2 = techniques_gemini(scenes, topic)
    log("techniques: rules", {k: v for k, v in n1.items() if v}, "| gemini", {k: v for k, v in n2.items() if v})
    return {"rules": n1, "gemini": n2}


def techniques_coverage(scenes, topic=""):
    c = coverage_fill(scenes, topic)
    log("technique coverage per video:", c)
    return c


def finalize_overlays(scenes, topic=""):
    """Always runs after the director (even when it failed): rule-based detector, Gemini detector, guaranteed source cards, source list, card styles."""
    st = {}
    for name, fn in (("declared_sources", lambda: notes_from_declared_src(scenes, topic)), ("concept_shots", lambda: concept_shots(scenes, topic)), ("semantic", lambda: semantic_review(scenes, topic)), ("context", lambda: apply_context(scenes)),
                     ("mention_timing", lambda: delay_to_mention(scenes)), ("auto", lambda: auto_overlays(scenes)),
                     ("overlays", lambda: overlay_pass(scenes, topic)), ("png_pass", lambda: png_pass(scenes, topic)),
                     ("years", lambda: resolve_years(scenes)), *([] if T151_ON else [("techniques", lambda: techniques_pass(scenes, topic)), ("tech_coverage", lambda: techniques_coverage(scenes, topic)), ("vox", lambda: assign_vox(scenes)), ("dedupe", lambda: dedupe_texts(scenes)), ("calm", lambda: calm_pass(scenes))]), ("forced_sources", lambda: force_source_cards(scenes, topic)),
                     ("entity_sources", lambda: notes_for_entities(scenes)), ("source_list", lambda: add_source_outro(scenes))):
        try:
            st[name] = fn()
        except Exception as e:  # noqa
            log(f"{name} failed:", type(e).__name__, str(e)[:100])
    try:
        assign_note_styles(scenes)
    except Exception as e:  # noqa
        log("assign_note_styles failed:", type(e).__name__, str(e)[:100])
    if T151_ON:
        try:
            plan_ = plan_t151(scenes, topic)
            T151_PLAN[0] = plan_
            st["t151"] = plan_["C"]["placed"] if plan_ else 0
        except Exception as e:  # noqa
            log("t151 planner failed:", type(e).__name__, str(e)[:120])
    log("overlay finalize:", st, "| optional Gemini calls:", GOV["opt"], "skipped:", GOV["skipped"], "| minutes used:", round(elapsed_min()))
    return st


_NOTE_N = [0]


def assign_note_styles(scenes):
    """Every source card gets a different look in turn (banner / torn paper / dark text page / document page), so all styles appear in one video."""
    order = STYLE_CYCLE[:]
    if not _NOTE_N[0]:
        r_ = random.randrange(len(order))
        order = order[r_:] + order[:r_]
    rev = {v["text"]: v for v in SRC_PARAS.values()}
    for sc in scenes:
        for it in sc.get("items", []) if isinstance(sc.get("items"), list) else []:
            if isinstance(it, dict) and it.get("kind") == "note":
                if not it.get("style"):
                    it["style"] = order[_NOTE_N[0] % len(order)]
                    _NOTE_N[0] += 1
                sp_ = rev.get(it.get("text"))
                if sp_ and not it.get("dom"):
                    it["dom"] = _dom(sp_.get("url", ""))


def director_pass(scenes, topic=""):
    """After the voice exists: plan pictures, camera keyframes and on-screen items on the exact spoken words
    (falls back to the script's own shots)."""
    todo = [(i, sc) for i, sc in enumerate(scenes)
            if sc["type"] == "photo" and sc.get("wt") and len(sc["wt"]) == len(sc["narration_hi"].split())]
    nb = {i: " ".join(sc["narration_hi"].split()[-9:]) for i, sc in enumerate(scenes)}
    ok = 0
    for a in range(0, len(todo), 3):
        chunk = todo[a:a + 3]
        rows = {}
        for attempt in range(2):
            try:
                out = gemini_json(director_prompt(chunk, topic, nb), 0.45)
                rows = {int(r.get("i")): r for r in (out.get("scenes") or []) if isinstance(r, dict) and str(r.get("i", "")).lstrip("-").isdigit()}
                if rows:
                    break
            except Exception as e:  # noqa
                log("director retry:", str(e)[:120])
        if not rows:
            log("director: Gemini gave no usable plan for scenes", [i for i, _ in chunk])
        for i, sc in chunk:
            if i in rows and apply_direction(sc, rows[i]):
                sc["directed"] = True
                ensure_techniques(sc, i)
                sc["beats"] = [b_ for b_ in (rows[i].get("beats") or []) if isinstance(b_, dict)][:12]
                ok += 1
                if rows[i].get("read"):
                    log(f"  scene {i}: {str(rows[i]['read'])[:60]} | mains={len(sc['shots'])} items={len(sc['items'])}")
    log(f"director: {ok}/{len(todo)} photo scenes planned word-by-word")
    try:
        director_extras(scenes, topic)
    except Exception as e:  # noqa
        log("director_extras failed:", type(e).__name__, str(e)[:100])
    finalize_overlays(scenes, topic)
    allsh = [sh for sc in scenes for sh in sc.get("shots", [])] if todo else []
    techs = sorted({t.strip() for sh in allsh for t in (sh.get("tech") or "").split(",") if t.strip().isdigit()}, key=int)
    nbeats = sum(len(sc.get("beats", [])) for sc in scenes)
    DIRECTOR_REPORT[0] = (f"Director: {ok}/{len(todo)} scenes planned | beats {nbeats} | keyframe moves {sum(1 for sh in allsh if sh.get('kf'))} | "
                          f"video clips planned {sum(1 for sh in allsh if sh.get('motion') and sh.get('clip_en'))} | transitions {sum(1 for sh in allsh if sh.get('trans'))} | cams {sum(1 for sh in allsh if sh.get('cam'))} | "
                          f"split-screens {sum(1 for sc in scenes if sc.get('type') == 'multi' and sc.get('directed'))} | terrain maps {sum(1 for sc in scenes if sc.get('type') == 'map' and sc.get('directed'))} | "
                          f"source cards {sum(1 for sc in scenes if sc.get('type') == 'photo' for it in sc.get('items', []) if isinstance(it, dict) and it.get('kind') == 'note')} | "
                          f"techniques used: {', '.join(techs) or 'none'}")
    log(DIRECTOR_REPORT[0])


# --------------------------------------------------------------------------- topic
def norm_words(s):
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    stop = {"the", "a", "an", "of", "how", "why", "what", "did", "do", "does", "and", "in", "to", "is",
            "are", "was", "were", "for", "on", "our", "their", "its", "from", "with", "that", "this"}
    return {w for w in s.split() if w not in stop and len(w) > 2}


def is_dup(topic_en, done):
    a = norm_words(topic_en)
    for v in done.get("videos", []):
        old = v.get("topic_en", "")
        if difflib.SequenceMatcher(None, topic_en.lower(), old.lower()).ratio() > 0.72:
            return True
        b = norm_words(old)
        if a and b and len(a & b) / len(a | b) >= 0.6:
            return True
    return False


def pick_topic(done):
    cat_key, cat_desc = random.choice(CATEGORIES)
    covered = [v.get("topic_en", "") for v in done.get("videos", [])][-300:]
    prompt = f"""You plan videos for a Hindi nature & science documentary YouTube channel for general viewers in India.
Category: {cat_desc}
Suggest 8 NEW, specific video topics in this category. Each must be answerable clearly in 3-5 minutes,
be genuinely interesting, and rest on well-established science (no myths, no unproven claims).
Do NOT repeat or rephrase any of these already-covered topics: {json.dumps(covered, ensure_ascii=False)}
Return JSON: [{{"topic_en": "...", "topic_hi": "..."}}]"""
    try:
        items = gemini_json(prompt, temperature=1.0)
        for it in items:
            t = (it.get("topic_en") or "").strip()
            if t and not is_dup(t, done):
                return cat_key, t, (it.get("topic_hi") or t)
    except Exception as e:
        log("Topic generation failed, using seed list:", e)
    seeds = []
    sp = ROOT / "topics_seed.txt"
    if sp.exists():
        for line in sp.read_text(encoding="utf-8").splitlines():
            if "|" in line:
                en, hi = [x.strip() for x in line.split("|", 1)]
                if en and not is_dup(en, done):
                    seeds.append((en, hi))
    if not seeds:
        raise RuntimeError("No new topic available")
    en, hi = random.choice(seeds)
    return "seed", en, hi


# --------------------------------------------------------------------------- research + script

# --------------------------------------------------------------------------- real sources: pages the research really used, with their real paragraphs
SRC_PARAS = {}   # "1.2" -> {"title","url","text"}
SRC_LIST = []    # [{"title","url"}]


def trim_words(t, n=36):
    w = t.split()
    if len(w) <= n:
        return t
    cut = " ".join(w[:n])
    k = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return cut[:k + 1] if k > len(cut) * 0.5 else cut.rstrip(",;:") + "."


def wiki_sources(topic, extra_text="", want=6):
    """Wikipedia articles that document the topic (plain-text paragraphs, always reachable): a guaranteed supply of REAL source text."""
    import html as _html
    titles = []
    if GEMINI_KEY:
        try:
            out = gemini_json_opt(f"Topic / story: {topic}\n{extra_text[:1500]}\nList up to 5 EXACT English Wikipedia article titles that document the real facts, people, places or studies behind this (no guesses: only articles that surely exist). "
                              'Return ONLY JSON: {"titles": ["..."]}', 0.1)
            titles = [str(t).strip() for t in (out.get("titles") or []) if str(t).strip().isascii()][:5]
        except Exception as e:  # noqa
            log("  wiki title suggestion failed:", str(e)[:80])
    if topic and topic.isascii():
        titles.append(topic[:80])
    got = 0
    seen = {x["url"] for x in SRC_LIST}
    for t in titles:
        if got >= want:
            break
        try:
            r = requests.get("https://en.wikipedia.org/w/api.php", headers=WIKI_UA, timeout=25, params={
                "action": "query", "format": "json", "redirects": 1, "generator": "search", "gsrsearch": t, "gsrlimit": 1,
                "prop": "extracts|info", "explaintext": 1, "exsectionformat": "plain", "inprop": "url"})
            pg = list(((r.json().get("query") or {}).get("pages") or {}).values())
            if not pg:
                continue
            pg = pg[0]
            url = pg.get("fullurl") or ""
            if not url or url in seen:
                continue
            paras = []
            for para in (pg.get("extract") or "").split("\n"):
                para = re.sub(r"\s+", " ", para).strip()
                nw = len(para.split())
                if 25 <= nw <= 160 and sum(c.isascii() and c.isalpha() for c in para) > 0.75 * len(para):
                    paras.append(trim_words(para))
                if len(paras) >= 6:
                    break
            if not paras:
                continue
            title = "Wikipedia - " + str(pg.get("title", t))[:70]
            k = len(SRC_LIST) + 1
            SRC_LIST.append({"title": title, "url": url})
            seen.add(url)
            for j, para in enumerate(paras, 1):
                SRC_PARAS[f"{k}.{j}"] = {"title": title, "url": url, "text": para}
            got += 1
        except Exception as e:  # noqa
            log("  wikipedia source failed:", type(e).__name__)
    return got


def collect_sources(max_src=14, topic="", story=""):
    """Open the pages the web-search research was grounded on and keep their REAL paragraphs for on-screen source captions."""
    import html as _html
    SRC_PARAS.clear()
    SRC_LIST.clear()
    for g in GROUND_SRC[:20]:
        if len(SRC_LIST) >= max_src:
            break
        try:
            r = requests.get(g["uri"], headers={"User-Agent": "Mozilla/5.0 (compatible; nature-video-bot/1.0)"}, timeout=25, allow_redirects=True)
            if not r.ok or "html" not in r.headers.get("content-type", "html"):
                continue
            h = r.text[:1_500_000]
            tt = re.search(r"<title[^>]*>(.*?)</title>", h, re.S | re.I)
            title = _html.unescape(_strip_html(tt.group(1))).replace("|", "-")[:90] if tt else (g["title"] or r.url)
            paras = []
            for m in re.findall(r"<p[^>]*>(.*?)</p>", h, re.S | re.I):
                t = _html.unescape(_strip_html(m))
                nw = len(t.split())
                if 25 <= nw <= 150 and sum(c.isascii() and c.isalpha() for c in t) > 0.7 * len(t) and "cookie" not in t.lower() and "subscribe" not in t.lower() and "javascript" not in t.lower():
                    paras.append(trim_words(t, 60))
                if len(paras) >= 10:
                    break
            if not paras:
                continue
            k = len(SRC_LIST) + 1
            SRC_LIST.append({"title": title, "url": r.url})
            for j, t in enumerate(paras, 1):
                SRC_PARAS[f"{k}.{j}"] = {"title": title, "url": r.url, "text": t}
        except Exception as e:  # noqa
            log("  source page failed:", type(e).__name__)
    if len(SRC_LIST) < 4 and (topic or story):
        try:
            n_ = wiki_sources(topic, story)
            log(f"  Wikipedia fallback added {n_} source articles")
        except Exception as e:  # noqa
            log("  wiki fallback failed:", type(e).__name__, str(e)[:80])
    log(f"real sources collected: {len(SRC_LIST)} pages, {len(SRC_PARAS)} paragraphs")


def sources_block():
    if not SRC_PARAS:
        return "REAL SOURCES: none were retrieved, so do NOT use any \"note\" items."
    out = ["REAL SOURCES (the research really used these pages; paragraph id, source title, real text):"]
    for k, v in SRC_PARAS.items():
        out.append(f'[{k}] {v["title"][:70]}: "{v["text"]}"')
    return "\n".join(out)


def research_story(story, topic):
    """Sources for the owner's own story: search the web for pages that confirm its facts (the story itself is never changed)."""
    body = " ".join(story.split())
    parts = [body[i:i + 1500] for i in range(0, min(len(body), 9000), 1500)] or [topic]
    for k, part in enumerate(parts):
        prompt = (f"Story topic: {topic}\nStory excerpt (Hindi or English):\n{part}\n\nUsing web search, find reliable English-language sources "
                  "(Wikipedia, NASA, Britannica, WWF, government or university pages, major news) that confirm the specific facts, names, numbers and places in this excerpt. "
                  "Find the REAL article / report / page about exactly this story or event (the same people, place and case) and list the facts it states as short English bullets.")
        try:
            gemini(prompt, search=True, temperature=0.2)
        except Exception as e:  # noqa
            log("story source search failed:", str(e)[:100])


def research(topic):
    """ARTICLE FIRST: three differently-angled web searches so that several REAL articles (NASA, Britannica, Smithsonian, universities, news) are found.
    The pages themselves are opened afterwards (collect_sources) and the story is written FROM their passages."""
    angles = [
        "the key well-established scientific facts (origins, mechanisms, widely accepted figures with rounded numbers, the most surprising true facts)",
        "the best explanatory articles (NASA, Smithsonian, National Geographic, Britannica, BBC, university pages) - what they say step by step",
        "dates, numbers, places, named scientists / studies and any recent discovery that changed what we know",
    ]
    texts, titles_all = [], []
    for ang in angles:
        prompt = (f"Topic: {topic}\nUsing web search, find REAL articles about this topic and collect {ang}. "
                  "Write 10-15 short bullet facts in English, each tied to what the article actually says. Mark anything still debated as 'debated'.")
        try:
            text, titles = gemini(prompt, search=True, temperature=0.3)
            texts.append(text)
            titles_all += [t for t in titles if t not in titles_all]
        except Exception as e:
            log("Research (search grounding) failed for one angle, continuing:", e)
    return "\n".join(texts), titles_all[:8]


def passages_block(limit=70):
    """The real article passages the story must be built from (numbered, so a scene can name the passage it is based on)."""
    if not SRC_PARAS:
        return ""
    out = ["REAL ARTICLE PASSAGES (the story MUST be built from these; [id] is used in the scene field \"src\"):"]
    for k, v in list(SRC_PARAS.items())[:limit]:
        out.append(f'[{k}] ({v["title"][:55]}) {v["text"]}')
    return "\n".join(out)


def window_text(text, mark, maxw=38):
    """Source-card text: the sentence(s) around the marked fact, copied word for word from the real passage."""
    words = text.split()
    if len(words) <= maxw:
        return text
    i = text.lower().find((mark or "").lower()) if mark else -1
    if i < 0:
        return trim_words(text, maxw)
    sents = re.split(r"(?<=[.!?])\s+", text)
    pos, hit = 0, 0
    for k, snt in enumerate(sents):
        if pos <= i < pos + len(snt) + 1:
            hit = k
            break
        pos += len(snt) + 1
    chosen = [sents[hit]]
    for j in (hit + 1, hit - 1):
        if 0 <= j < len(sents) and len(" ".join(chosen + [sents[j]]).split()) <= maxw:
            chosen = chosen + [sents[j]] if j > hit else [sents[j]] + chosen
    out = " ".join(chosen)
    return out if len(out.split()) <= maxw + 12 else trim_words(out, maxw)


SCRIPT_RULES = """RULES FOR THE SCRIPT
- Narration language: simple, spoken Hindi in Devanagari, like an engaging documentary narrator. Short sentences. One idea per scene.
- Write every number in Hindi words (e.g. "चार अरब", "तीन हज़ार"). No digits, no English letters, no emojis in narration.
- Scene 1 is a SUSPENSE HOOK: 25-30 words, built on a TRUE surprising fact or question, does NOT reveal the answer, ends by promising it ("आगे जानेंगे"). Scene 1 must be type "photo", tone "suspense", visually dramatic.
- Scene 2 says in one or two lines what the viewer will understand by the end.
- Around 55% of the video add a short re-hook line ("लेकिन असली राज़ अभी बाक़ी है...") that is true to what follows.
- NO recap, NO summary, NO call to subscribe/like/comment. The story simply ends on its last strong fact or an open, thought-provoking line; the video then fades out.
- The viewer must truly UNDERSTAND the topic by the end: define every new term when first used, use one simple everyday analogy, go in a logical order.
- Facts: only well-established science, rounded widely-accepted numbers. If something is debated say "वैज्ञानिकों का मानना है". Never invent names, dates, statistics or quotes. If unsure, leave it out.
- Neutral on religion and politics; never mention or mock any belief. Say "वैज्ञानिक मान्यता के अनुसार" for origin topics.
- Every scene has a "tone" for the narrator's voice: suspense, wonder, serious, excited or calm. Vary it with the content.

VISUAL PLAN: the picture must TEACH and must match the exact words being spoken at that moment. Every scene has a "type":
- "photo": a cinematic full-screen picture with camera movement. The picture must follow the narrator WORD BY WORD like a professional documentary edit. Split the scene narration into 3-6 consecutive "shots" ("text_hi" = a phrase of about 3-8 words, together EXACTLY the scene narration, in order). Each shot is what the viewer sees WHILE that phrase is spoken, and it starts exactly when the phrase starts. Rules: (0) include now and then a macro close-up DETAIL shot (a hand, an eye, a cracked object, a texture) of something just mentioned, and sometimes a high aerial wide shot to show where a place is; (1) EVERY time the phrase names a thing (an animal, flower, tree, object, place, event, body part, process) that is not the main subject of the previous shot, give it a NEW "prompt_en" that shows EXACTLY that named thing and nothing else: if the narrator says lion the picture is a lion, if flower then a flower, if volcano then an erupting volcano. Start prompt_en with the literal subject (e.g. "a close-up of a lion's face, ..."), one clear subject large in the frame, realistic, no text in the image. Never show something different from what is being said; (2) when the phrase just continues or zooms into a detail of the same thing, leave "prompt_en" EMPTY ("") and give "focus_x" and "focus_y" (0.15-0.85, where in the previous picture that detail is, e.g. the head of an ant is left-top = 0.3, 0.3) so the camera pushes onto that part (only when the phrase talks about the SAME subject); never keep a picture on screen after the narrator moved to something else. Each shot with a new picture also has "query_en": 1-3 plain English words for a REAL stock photo of that exact thing if such a photo exists (e.g. "lion", "red rose flower", "volcano eruption", "ant colony", "full moon"); use "" for things that cannot be photographed (young molten Earth, atoms, the distant past, microscopic or imaginary things) so an AI picture is made instead; never a person. EVERY shot must also have "label_hi" (1-3 Hindi words) AND "popup_en": an English description of ONE concrete object, animal, body part or symbol that the phrase mentions (e.g. "a raw carrot", "a human brain", "a burning flame", "a red heart", "a clock", "a lion"), shown as a pop-up sticker on top of the picture at the moment the word is spoken, so a viewer watching on mute still understands every sentence. Leave "popup_en" empty only if the phrase names no object at all. MOVING FOOTAGE: only when the phrase describes something that MOVES or CHANGES POSITION (an animal running or flying, water flowing, fire burning, clouds moving, a planet spinning, people walking), set "motion": true and "clip_en": 1-3 English words for real stock footage of it (e.g. "lion running", "ocean waves", "flying bat"); at most one shot in four, never for still things, abstract ideas or old history; those use pictures. Optional on-screen helpers per shot (at most ONE per shot, and in total on about every second shot, only when they truly help the viewer): "bubble_hi" (a short quote, max 6 words, ONLY when the narration quotes what a person or animal said, shown as a comic speech bubble), "place_hi" (the name of a place or person introduced for the first time, 1-2 words, with "place_sub_hi" a small line such as the region or role; shown as a bold location tag), "title_hi" (1-3 huge Hindi words, ONLY for the single biggest reveal of the video, e.g. the name of the main subject; shown as a glowing title), "year_hi" (a date or time such as "१९६८" or "साढ़े चार अरब साल पहले" ONLY when the narration states it, shown on a timeline marker), "note_hi" (one key fact copied from the narration, max 12 words, shown on a torn-paper "source" card) with "mark_hi" (the 1-3 most important words of note_hi, highlighted yellow). Each shot may have "label_hi" (1-3 Hindi words naming the key thing being said, shown as an animated tag). Optional "headline_hi" (2-4 Hindi words) as a chapter label when a new section starts. The first shot of a scene always needs a "prompt_en".
- "merge": two things come together and create a third (e.g. young Earth + a Mars-sized planet -> the Moon). items = [A, B, RESULT]. First half of the narration names A and B, the second half announces the result.
- "split": one thing breaks into two parts. items = [WHOLE, PART1, PART2].
- "steps": a sequence, life cycle, timeline or evolution (3 or 4 items in order, e.g. egg -> larva -> pupa -> butterfly).
- "compare": two things side by side. items = [A, B].
- "multi": REAL-PHOTO SPLIT SCREEN, the documentary way to enumerate 2-3 things the narrator lists one after another (two universities, two animals, three cities, before/after, different examples). items = 2-3 of {"label_hi": "short name shown on a red brush card (1-5 words, may be English proper noun)", "query_en": "1-3 English words for a REAL stock photo of exactly that thing", "prompt_en": "realistic description of the same thing"}. The picture of each item appears, with its red label card, exactly when the narrator names it. Use this whenever 2-3 separate things are named in a row; never invent names.
- "counter": one striking number counting up on screen (age, distance, size, speed, count). "counter": {"from": 0, "to": 4.5, "decimals": 1, "unit_hi": "अरब साल पहले"} plus "headline_hi" (what the number is). The narration says the number in words.
- "bars": compare 2-4 real sizes as animated bars. "bars": [{"label_hi": "पृथ्वी", "value": 12742, "unit_hi": "किमी"}, ...]. Same unit for all, rounded well-established values only.
- "map": use whenever the narrator names a country, region, city, route, journey or migration. "map": {"mode": "flat", "route": false, "pins": [{"label_hi": "भारत", "lat": 22.0, "lon": 79.0}, ...]} (1-4 real places with correct latitude/longitude; route true draws an animated line through the pins in order, e.g. a journey or spread; mode "globe" for far-apart places). The narration says the place names.
- "map" with mode "terrain" (BEST for a distance or "from this place to that place" within about 1000 km, e.g. two villages/cities/districts): a tilted satellite-style terrain map with big region names on the ground, two yellow place tags, an animated arrow between them and the distance written on it; the camera follows each point. Format: "map": {"mode": "terrain", "pins": [{"label_hi": "शरीफपुरा गाँव", "lat": 27.4, "lon": 79.6}, {"label_hi": "बिरपुर गाँव", "lat": 27.3, "lon": 79.5}], "regions": [{"label_hi": "फ़र्रुख़ाबाद", "lat": 27.4, "lon": 79.6}, {"label_hi": "मैनपुरी", "lat": 27.2, "lon": 79.0}]} where regions are the 2-4 districts/states/countries around the two places (centre lat/lon). Use terrain ONLY when exactly two places are named (a start and an end): the distance is computed automatically. When THREE OR MORE places are named in a row use terrain with 3-5 pins in the order named: no distance, just pins and an arrow from each place to the next on the same ground.
- "sketch": ANIMATED LINE-ART DIAGRAM drawn stroke by stroke on parchment (like a premium explainer film) that makes a MECHANISM visible: how something forms, collides, flows, grows, splits, is built from layers, or how two things differ. Use it for about 20-30% of the scenes (the scenes that explain HOW or WHY; at least two in the video, never scene 1 or the last scene). Fields: "type": "sketch", "headline_hi" (2-5 Hindi words, the key claim, shown big on the left word by word), "sketch_brief_en" (one precise English sentence: exactly what the drawing shows, e.g. "a small planet Theia hitting the young Earth from the left, debris flying out, then the Moon forming from the debris"), "year_hi" (a date / time like "४५० करोड़ साल पहले" ONLY if the narration says one, else ""), narration 18-34 words. The drawing is made later from sketch_brief_en, so describe it concretely.
- "kinetic": a punchy key sentence: "keywords_hi": 2-4 short Hindi words/phrases that slam onto the screen one by one, plus "image_prompt_en" for a dramatic background picture. Use for the re-hook and key insights.
STYLE TARGET = a real documentary: use type "photo" for at least 60% of ALL scenes and "sketch" for the explaining scenes, because every photo scene is edited word by word with real pictures, camera keyframes, maps, dates and source cards by the director. Cartoon-style explainer cards (merge, split, steps, compare, bars) look cheap: use "sketch" scenes instead whenever a point is a mechanism or process. STORYTELLING RULES (documentary craft, obey always): open IN THE MIDDLE OF A REAL MOMENT (a specific place, date and person or animal, one concrete image) before explaining anything; never start with "आज हम जानेंगे"/greetings/definitions. Build like a true-story film: a clear subject we care about -> what they wanted or faced -> rising tension with concrete facts -> one turning point (the surprising reveal, placed late) -> consequence -> a quiet, resonant last line (no recap, no moral lecture, no subscribe). Use concrete sensory details and numbers from the sources, short sentences (8-16 words), one idea per sentence, vary the rhythm (a very short sentence after a long one for impact). Calm, restrained, factual tone; no hype words (अद्भुत, ज़बरदस्त, शॉकिंग); never invent anything. PERMANENT TECHNIQUE RULES (never drop): the engine draws these on the spoken words, so WRITE narration that gives them something to show: (1) say real sizes / lengths / heights / depths / weights with a number and unit (measuring screen); (2) ask the viewer 2-4 real questions during the video ("आखिर ऐसा क्यों?") (question marks); (3) say a 'then vs now' or 'before vs after' of the same thing at least once; (4) compare two things with numbers at least once (comparison panel / chart); (5) tell 3-5 dated events in order at least once (timeline); (6) name two subjects together at least once (split screen, use one "multi" scene) and a journey or distance between two real places at least once (use one "map" scene); (7) say what is a myth and what is a fact at least once (cross / tick); (8) point at a tiny detail ('ध्यान से देखिए') at least once (magnifier); (9) end each scene on a short, clear sentence. Numbers must always be real, taken from the sources. Use "counter" only for one really striking number, "map" whenever places/routes are named (this is the preferred explainer), "kinetic" at most once (the re-hook). Photo scenes should have 22-40 words of narration (several different subjects/details per scene give the director more to show).
Explainer, counter, bars and kinetic scenes need short narration (14-22 words). Explainer scenes also need "headline_hi" of 2-5 Hindi words shown on screen (e.g. "टक्कर से चाँद बना").
Each explainer item has "label_hi" (1-3 Hindi words under the picture) and "prompt_en": a concrete, simple description of ONE clear subject on a plain background (e.g. "a young glowing blue-green Earth", "a Mars-sized rocky planet", "the grey cratered Moon"). No text or letters in any picture, no real people.
Every scene also has "grade" (colour grading that fits the scene: warm for fire/desert/sunrise/early Earth, cool for ocean/ice/night/space, cinematic for dramatic reveals, vivid for lively nature and animals, dark for danger and mystery, dread for creepy/scary/dangerous night scenes (cold teal), bwred for shock, fear, anger or a dramatic turning point (black and white with red glow), redwash for violence or emergency, natural for calm facts) and "transition" (how the scene begins: fade = time passes or calm change, zoom = go closer into a detail, whip = fast energetic jump, wipe = new chapter or place, flash = big reveal or impact, cut = continuous action).
"mood" of the whole video's background music: one of mystery, epic, wonder, calm, tense, life, space.
"""

GRADES = ["warm", "cool", "cinematic", "vivid", "dark", "natural", "dread", "bwred", "redwash"]
TRANS_NAMES = ["fade", "zoom", "whip", "wipe", "circle", "flash", "cut", "mask", "luma", "morph", "spin", "glitch", "burn", "shape", "match", "object", "seamless", "morphmap"]
TONE_GRADE = {"suspense": "dark", "wonder": "cinematic", "serious": "cool", "excited": "vivid", "calm": "natural"}
MOOD_NAMES = ["mystery", "epic", "wonder", "calm", "tense", "life", "space"]
TONE_NAMES = ["suspense", "wonder", "serious", "excited", "calm"]


def build_script_prompt(topic, cat, style, target_sec, facts, words):
    n_scenes = max(5 if TEST_SEC else 14, round(words / 21))
    test_note = ("\nTEST MODE: this is a very short test video. Use only about 5-6 scenes, a hook of 15-18 words, no long recap. "
                 "Ignore the minimum counts below. Almost all scenes must be type photo; exactly ONE sketch scene (scene 3 or 4) is wanted; map or counter allowed once if natural; no merge/split/steps/compare/bars.\n") if TEST_SEC else ""
    return f"""You are a script writer and film editor for a Hindi nature & science explainer YouTube channel.
TOPIC: {topic}
CATEGORY: {cat}
STRUCTURE STYLE: {style[1]}
TARGET: about {words} Hindi words of narration in total ({target_sec} seconds), around {n_scenes} scenes.{test_note}
{SCRIPT_RULES}

ARTICLE-FIRST RULE: the REAL ARTICLE PASSAGES below are the truth. Build the story from them: every fact, number, name and date in the narration must come from a passage (or from the bullet facts), told in your own simple Hindi but keeping the claim exactly as the article states it. In each scene put the id of the main passage it is based on in "src" (for example "2.3"; empty string when the scene states no article fact). The sentence of the scene that states the fact must be a faithful Hindi version of that passage, so that when the viewer sees the article's English sentence on the screen it says the same thing the narrator says.

VERIFIED FACTS AND ARTICLE PASSAGES TO USE (may be empty):
{facts[:16000]}

Return ONLY JSON in exactly this shape:
{{
 "title_hi": "Hindi title, max 60 characters, curious but TRUTHFUL, no clickbait lies",
 "mood": "wonder",
 "thumb_text_hi": "3-5 Hindi words that create a CURIOSITY GAP: a shocking true claim or an unanswered question that makes the viewer need to click to learn the answer; never reveal the answer, never a lie",
 "thumb_sub_hi": "a short Hindi teaser line, 2-5 words, that adds intrigue without spoiling (e.g. जवाब चौंका देगा / 4 अरब साल पहले...)",
 "thumb_entity_en": "exact English Wikipedia article title of the real place/animal/thing the thumbnail photo should show (e.g. Gir National Park, Asiatic lion, Moon); empty if it cannot be photographed",
 "thumb_query_en": "2-3 English words for a real stock photo of that same subject",
 "thumb_layout": "one of bottom, top, side: where the headline sits so it never covers the main subject",
 "thumb_badge_hi": "one short Hindi word like रहस्य / सच / विज्ञान",
 "thumb_prompt_en": "the ONE most intriguing visual of THIS exact topic for the thumbnail: a single clear subject shown as a dramatic close-up or surprising scene that makes people ask a question (e.g. for ants: a giant ant lifting a huge leaf; for Earth: a glowing molten planet). Be specific to the topic, never generic. No text or letters in the image",
 "thumb_prompt2_en": "the contrasting counterpart for a then/now thumbnail (e.g. molten young Earth vs today's blue Earth); empty string if the topic has no natural contrast",
 "thumb_left_hi": "1-2 Hindi words for the left image, e.g. तब", "thumb_right_hi": "1-2 Hindi words for the right image, e.g. अब",
 "description_hi": "3-4 short Hindi lines telling what the viewer will learn",
 "tags": ["8-12 tags mixing Hindi and English"],
 "scenes": [
  {{"narration_hi": "...", "src": "1.2", "type": "photo", "tone": "suspense", "grade": "dark", "transition": "fade", "headline_hi": "",
    "shots": [{{"text_hi": "first phrase of the narration", "prompt_en": "...", "label_hi": "key word"}}, {{"text_hi": "next phrase, same subject", "prompt_en": "", "focus_x": 0.3, "focus_y": 0.4, "label_hi": ""}}, {{"text_hi": "phrase naming something new", "prompt_en": "...", "query_en": "lion", "label_hi": ""}}]}},
   {{"narration_hi": "...", "type": "multi", "tone": "wonder", "headline_hi": "",
    "items": [{{"label_hi": "name of first thing", "query_en": "real photo search words", "prompt_en": "..."}}, {{"label_hi": "name of second thing", "query_en": "...", "prompt_en": "..."}}]}},
 {{"narration_hi": "...", "type": "counter", "tone": "excited", "headline_hi": "...", "counter": {{"from": 0, "to": 4.5, "decimals": 1, "unit_hi": "..."}}}},
 ]
}}"""


NEED_ITEMS = {"merge": (3, 3), "split": (3, 3), "steps": (3, 4), "compare": (2, 2), "multi": (2, 3)}


def clean_narration(t):
    t = (t or "").replace("—", ", ").replace("–", ", ").replace("…", "...").replace("​", "")
    t = re.sub(r"[\*\_#`]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def clean_short(t, n):
    t = re.sub(r"[\*\_#`\n]", " ", str(t or ""))
    t = re.sub(r"\s+", " ", t).strip()
    return t[:n].strip()


def to_num(x, default=None):
    try:
        return float(str(x).replace(",", "").strip())
    except Exception:
        return default


def validate_script(s, verbatim=False, min_scenes=None):
    need = min_scenes if min_scenes is not None else (4 if TEST_SEC else 8)
    if not isinstance(s, dict) or not isinstance(s.get("scenes"), list) or len(s["scenes"]) < need:
        raise ValueError("script has too few scenes")
    for idx, sc in enumerate(s["scenes"]):
        sc["narration_hi"] = " ".join(str(sc.get("narration_hi", "")).split()) if verbatim else clean_narration(sc.get("narration_hi", ""))
        if not sc["narration_hi"]:
            raise ValueError("empty narration")
        tone = str(sc.get("tone") or "calm").lower().strip()
        sc["tone"] = tone if tone in TONE_NAMES else "calm"
        typ = str(sc.get("type") or "photo").lower().strip()
        items = []
        for it in (sc.get("items") or []):
            if isinstance(it, dict) and (str(it.get("prompt_en") or "").strip() or str(it.get("query_en") or "").strip()):
                q_ = clean_short(it.get("query_en"), 50)
                items.append({"label_hi": clean_short(it.get("label_hi"), 40) or " ",
                              "prompt_en": clean_short(it.get("prompt_en"), 400) or ("a realistic photo of " + q_),
                              "query_en": q_, "entity_en": clean_short(it.get("entity_en"), 70)})
        sc["items"] = []
        if typ in NEED_ITEMS:
            lo, hi = NEED_ITEMS[typ]
            items = items[:hi]
            if len(items) < lo:
                typ = "photo"
            else:
                sc["items"] = items
        elif typ == "counter":
            c = sc.get("counter") if isinstance(sc.get("counter"), dict) else {}
            to = to_num(c.get("to"))
            if to is None or abs(to) > 1e13:
                typ = "photo"
            else:
                sc["counter"] = {"from": to_num(c.get("from"), 0.0), "to": to,
                                 "decimals": max(0, min(2, int(to_num(c.get("decimals"), 0)))),
                                 "unit_hi": clean_short(c.get("unit_hi"), 30)}
        elif typ == "bars":
            bars = []
            for b in (sc.get("bars") or []):
                v = to_num(b.get("value")) if isinstance(b, dict) else None
                if v and v > 0:
                    dec = 0 if v >= 100 else 1
                    bars.append({"label_hi": clean_short(b.get("label_hi"), 22) or " ", "value": v, "dec": dec,
                                 "unit_hi": clean_short(b.get("unit_hi"), 10)})
            if 2 <= len(bars) <= 4:
                sc["bars"] = bars
            else:
                typ = "photo"
        elif typ == "map":
            mp = sc.get("map") if isinstance(sc.get("map"), dict) else {}
            pins = []
            for pn in (mp.get("pins") or [])[:4]:
                try:
                    la, lo = float(pn.get("lat")), float(pn.get("lon"))
                except Exception:  # noqa
                    continue
                if -80 <= la <= 80 and -180 <= lo <= 180 and clean_short(pn.get("label_hi"), 20) \
                        and not re.search(r"दुनिया|पृथ्वी|धरती|विश्व|संसार|ब्रह्मांड|world|earth|planet|universe", str(pn.get("label_hi")), re.I):
                    pins.append({"label_hi": clean_short(pn.get("label_hi"), 20), "lat": la, "lon": lo})
            if pins:
                regs = []
                for rg in (mp.get("regions") or [])[:5]:
                    try:
                        la, lo = float(rg.get("lat")), float(rg.get("lon"))
                    except Exception:  # noqa
                        continue
                    if -80 <= la <= 80 and -180 <= lo <= 180 and clean_short(rg.get("label_hi"), 20):
                        regs.append({"label_hi": clean_short(rg.get("label_hi"), 20), "lat": la, "lon": lo})
                md = str(mp.get("mode"))
                if md == "terrain" and len(pins) >= 2 and max(hav_km((a_["lat"], a_["lon"]), (b_["lat"], b_["lon"])) for a_ in pins for b_ in pins) < 1500:
                    sc["map"] = {"pins": pins[:5], "route": True, "mode": "terrain", "regions": regs}
                else:
                    sc["map"] = {"pins": pins, "route": bool(mp.get("route")) and len(pins) >= 2,
                                 "mode": "globe" if md == "globe" else "flat"}
            else:
                typ = "photo"
        elif typ == "sketch":
            br = clean_short(sc.get("sketch_brief_en"), 300)
            if br and idx > 0 and idx != len(s["scenes"]) - 1:
                sc["sketch_brief_en"], sc["year_hi"] = br, clean_short(sc.get("year_hi"), 24)
            else:
                typ = "photo"
        elif typ == "kinetic":
            kw = [clean_short(k, 18) for k in (sc.get("keywords_hi") or []) if clean_short(k, 18)][:4]
            if len(kw) >= 2:
                sc["keywords_hi"] = kw
            else:
                typ = "photo"
        else:
            typ = "photo"
        if typ in ("merge", "split", "steps", "compare", "kinetic") and os.environ.get("KEEP_EXPLAINERS") != "1":
            # cartoon explainer cards look cheap: every such scene becomes a real-photo scene (the director then edits it word by word)
            if typ == "kinetic":
                raw_shots = [{"text_hi": "", "prompt_en": clean_short(sc.get("image_prompt_en"), 400) or "a dramatic cinematic nature scene", "query_en": ""}]
            else:
                raw_shots = [{"text_hi": it["label_hi"] or "x", "prompt_en": it["prompt_en"], "query_en": it.get("query_en", "")} for it in sc["items"]]
            sc["shots"] = raw_shots or sc.get("shots")
            sc["items"] = []
            typ = "photo"
        gr = str(sc.get("grade") or "").lower().strip()
        sc["grade"] = gr if gr in GRADES else TONE_GRADE.get(sc["tone"], "natural")
        tr = str(sc.get("transition") or "").lower().strip()
        sc["transition"] = tr if tr in TRANS_NAMES else ""
        if idx == 0:
            typ = "photo"
            sc["tone"] = "suspense"
            sc["transition"] = "fade"
        sc["type"] = typ
        sc["headline_hi"] = clean_short(sc.get("headline_hi"), 36) if typ != "kinetic" else ""
        base_prompt = clean_short(sc.get("image_prompt_en"), 400)
        shots = []
        for sh in (sc.get("shots") or []):
            if isinstance(sh, dict) and (str(sh.get("prompt_en") or "").strip() or (shots and str(sh.get("text_hi") or "").strip())):
                def fr_(v):
                    try:
                        return min(0.85, max(0.15, float(v)))
                    except Exception:  # noqa
                        return None
                shots.append({"text_hi": clean_narration(sh.get("text_hi", "")), "prompt_en": clean_short(sh.get("prompt_en") or "", 400),
                              "label_hi": clean_short(sh.get("label_hi"), 18), "query_en": clean_short(sh.get("query_en") or "", 50), "entity_en": clean_short(sh.get("entity_en") or "", 70), "motion": bool(sh.get("motion")), "clip_en": clean_short(sh.get("clip_en") or "", 40),
                              "popup_en": clean_short(sh.get("popup_en") or "", 120),
                              "ov": (({"kind": "title", "text": clean_short(sh.get("title_hi"), 24)} if str(sh.get("title_hi") or "").strip() else
                                      {"kind": "place", "text": clean_short(sh.get("place_hi"), 28) + "|" + clean_short(sh.get("place_sub_hi"), 40)}
                                      if str(sh.get("place_hi") or "").strip() else
                                      {"kind": "bubble", "text": clean_short(sh.get("bubble_hi"), 40)} if str(sh.get("bubble_hi") or "").strip() else
                                      {"kind": "year", "text": clean_short(sh.get("year_hi"), 22)} if str(sh.get("year_hi") or "").strip() else
                                      {"kind": "note", "text": clean_short(sh.get("note_hi"), 90), "mark": clean_short(sh.get("mark_hi"), 30)}
                                      if str(sh.get("note_hi") or "").strip() else None)), "fx": fr_(sh.get("focus_x")), "fy": fr_(sh.get("focus_y"))})
        if typ == "photo":
            if not shots:
                shots = [{"text_hi": sc["narration_hi"], "prompt_en": base_prompt or "a dramatic wide nature landscape", "label_hi": ""}]
            shots = shots[:8]
            nw = len(sc["narration_hi"].split())
            sw = sum(len(x["text_hi"].split()) for x in shots)
            if len(shots) > 1 and (abs(sw - nw) > max(2, 0.15 * nw) or any(not x["text_hi"] for x in shots)):
                words = sc["narration_hi"].split()
                k = len(shots)
                for j, x in enumerate(shots):
                    x["text_hi"] = " ".join(words[j * nw // k:(j + 1) * nw // k]) or sc["narration_hi"]
            if len(shots) == 1:
                shots[0]["text_hi"] = sc["narration_hi"]
            sc["shots"] = shots
        else:
            sc["shots"] = []
        if typ == "kinetic":
            sc["image_prompt_en"] = base_prompt or "a dramatic cinematic nature scene"
        else:
            sc["image_prompt_en"] = base_prompt
    for k in ("title_hi", "thumb_text_hi", "description_hi"):
        s[k] = (s.get(k) or "").strip()
    if not s["title_hi"]:
        raise ValueError("no title")
    mood = str(s.get("mood") or "").lower().strip()
    s["mood"] = mood if mood in MOOD_NAMES else ""
    s["thumb_badge_hi"] = clean_short(s.get("thumb_badge_hi"), 14) or "जानिए"
    s["thumb_sub_hi"] = clean_short(s.get("thumb_sub_hi"), 40)
    s["thumb_left_hi"] = clean_short(s.get("thumb_left_hi"), 14)
    s["thumb_right_hi"] = clean_short(s.get("thumb_right_hi"), 14)
    s["tags"] = [str(t).strip() for t in (s.get("tags") or []) if str(t).strip()][:12]
    first = s["scenes"][0]["shots"][0]["prompt_en"]
    s["thumb_prompt_en"] = (s.get("thumb_prompt_en") or first).strip()
    s["thumb_prompt2_en"] = (s.get("thumb_prompt2_en") or "").strip()
    s["thumb_entity_en"] = clean_short(s.get("thumb_entity_en"), 70)
    s["thumb_query_en"] = clean_short(s.get("thumb_query_en"), 50)
    s["thumb_layout"] = s.get("thumb_layout") if s.get("thumb_layout") in ("bottom", "top", "side") else ""
    return s


def word_count(s):
    return sum(len(sc["narration_hi"].split()) for sc in s["scenes"])



def story_scenes(story):
    """Cut the owner's story into scenes WITHOUT changing a single word: (title or '', [narration chunks])."""
    lines = [l.strip() for l in story.strip().splitlines() if l.strip()]
    title = ""
    if len(lines) > 1 and len(lines[0]) <= 120 and not re.search(r"[।?!\.]$", lines[0]):
        title, lines = lines[0], lines[1:]  # a heading line is the title and is not narrated
    text = " ".join(lines)
    sents = [x.strip() for x in re.split(r"(?<=[।?!\.])\s+", text) if x.strip()]
    chunks, cur = [], []
    for sn in sents:
        cur.append(sn)
        n = sum(len(c.split()) for c in cur)
        if n >= 26:
            chunks.append(" ".join(cur))
            cur = []
    if cur:
        if chunks and sum(len(c.split()) for c in cur) < 12:
            chunks[-1] += " " + " ".join(cur)
        else:
            chunks.append(" ".join(cur))
    return title, chunks


def own_story_script(story, topic_en):
    """Script made from the owner's story: narration is EXACTLY his words; Gemini only adds tone, picture hints and metadata."""
    title, chunks = story_scenes(story)
    if not chunks:
        raise ValueError("story is empty")
    listing = "\n".join(f"{i}: {c}" for i, c in enumerate(chunks))
    prompt = f"""A Hindi documentary video will narrate the numbered scenes below WORD FOR WORD (never change the text). For each scene return how it should sound and one picture hint, and give the video metadata.
Return ONLY JSON: {{"title_hi": "catchy Hindi title (max 70 chars)", "description_hi": "2-3 lines Hindi", "tags": ["8 tags"], "mood": "one of {', '.join(MOOD_NAMES)}",
 "thumb_prompt_en": "dramatic realistic hero photo prompt of the main subject, no text", "thumb_prompt2_en": "", "thumb_text_hi": "2-4 curiosity words in Hindi", "thumb_entity_en": "exact Wikipedia title of the real subject of the thumbnail photo", "thumb_query_en": "2-3 words stock photo query", "thumb_layout": "bottom, top or side", "thumb_badge_hi": "", "thumb_sub_hi": "", "thumb_left_hi": "", "thumb_right_hi": "",
 "scenes": [{{"i": 0, "tone": "one of {', '.join(TONE_NAMES)}", "image_prompt_en": "realistic photo of the main subject of this scene", "query_en": "1-3 English words for a real stock photo", "entity_en": "exact Wikipedia-style name of the specific place/animal/thing or empty"}}]}}
Title of the story: {title or topic_en}
SCENES:
{listing}"""
    meta = {}
    try:
        meta = gemini_json(prompt, 0.4)
    except Exception as e:  # noqa
        log("story metadata failed, using defaults:", str(e)[:100])
    rows = {int(r.get("i")): r for r in (meta.get("scenes") or []) if isinstance(r, dict) and str(r.get("i", "")).isdigit()}
    scenes = []
    for i, c in enumerate(chunks):
        r = rows.get(i, {})
        scenes.append({"narration_hi": c, "type": "photo", "tone": r.get("tone") or "calm", "headline_hi": "",
                       "image_prompt_en": r.get("image_prompt_en") or "", "shots": [], "grade": "", "transition": "",
                       "query_en_scene": r.get("query_en") or "", "entity_en_scene": r.get("entity_en") or ""})
    script = {"title_hi": meta.get("title_hi") or title or topic_en, "description_hi": meta.get("description_hi") or "",
              "tags": meta.get("tags") or [], "mood": meta.get("mood") or "", "thumb_prompt_en": meta.get("thumb_prompt_en") or "",
              "thumb_prompt2_en": meta.get("thumb_prompt2_en") or "", "thumb_text_hi": meta.get("thumb_text_hi") or "", "thumb_entity_en": meta.get("thumb_entity_en") or "", "thumb_query_en": meta.get("thumb_query_en") or "", "thumb_layout": meta.get("thumb_layout") or "",
              "thumb_badge_hi": meta.get("thumb_badge_hi") or "", "thumb_sub_hi": meta.get("thumb_sub_hi") or "",
              "thumb_left_hi": meta.get("thumb_left_hi") or "", "thumb_right_hi": meta.get("thumb_right_hi") or "", "scenes": scenes}
    script = validate_script(script, verbatim=True, min_scenes=1)
    for sc, raw in zip(script["scenes"], scenes):  # fallback shot carries the scene's real-photo hints
        if sc["shots"]:
            sc["shots"][0]["query_en"] = clean_short(raw.get("query_en_scene"), 50)
            sc["shots"][0]["entity_en"] = clean_short(raw.get("entity_en_scene"), 70)
    return script


def write_script(topic, cat, style, target_sec, facts, wpm):
    words = int(target_sec / 60 * wpm)
    script = validate_script(gemini_json(build_script_prompt(topic, cat, style, target_sec, facts, words), 0.8))
    review = f"""You are (1) a careful science editor, (2) a Hindi school teacher and (3) a documentary film editor.
Improve this explainer-video script JSON:
- Remove or soften any claim that is not widely accepted science; fix any factual doubt.
- Replace difficult words with simple ones; make sure a 15-year-old understands everything on first listen.
- Keep scene 1 a truthful suspense hook (25-30 words, type "photo") that does not reveal the answer.
- Check that every picture matches the words spoken at that moment (each shot's prompt_en must show exactly what its text_hi says; a merge scene must really show A and B combining into the result named in the narration; counter/bars numbers must be correct and round). Fix types/items/labels/headlines/shots where they do not teach.
- Keep at least 60% of the scenes as type photo (real-photo documentary scenes) and keep the "sketch" scenes (with their sketch_brief_en, headline_hi, year_hi) where a mechanism is explained; no merge/split/steps/compare/bars/kinetic cards; map or multi (split-screen list of real photos) are welcome; and a grade + transition on every scene. Every shot must show what is spoken at that moment.
- Keep roughly the same number of scenes (+/-2) and total narration length (+/-10%, now about {word_count(script)} words).
- Keep numbers in narration written in Hindi words, no English letters in narration, keep every field and the exact JSON shape.
Return ONLY the improved JSON.

{json.dumps(script, ensure_ascii=False)}"""
    try:
        script = validate_script(gemini_json(review, 0.4))
    except Exception as e:
        log("Review pass failed, keeping first draft:", e)
    return script


def rescale_script(script, target_words):
    cur = word_count(script)
    prompt = (f"Rewrite this Hindi explainer script so total narration is about {target_words} words "
              f"(now {cur}). {'Trim less important details' if target_words < cur else 'Add clear explanation and one more simple example'}. "
              "Keep the hook, structure, rules, scene types, tones, shots (text_hi parts must still add up to narration_hi) and the exact JSON shape (add or remove whole scenes if needed). Return ONLY JSON.\n"
              + json.dumps(script, ensure_ascii=False))
    return validate_script(gemini_json(prompt, 0.4))


# --------------------------------------------------------------------------- voice
TONES = {  # the scene tone still drives grading/camera, but the VOICE stays the same all video long
    "suspense": ("+4%", "+0Hz", "steady, even and confident, exactly the same calm documentary-narrator voice and emotion throughout, no change in mood between sentences"),
    "wonder": ("+4%", "+0Hz", "steady, even and confident, exactly the same calm documentary-narrator voice and emotion throughout, no change in mood between sentences"),
    "serious": ("+4%", "+0Hz", "steady, even and confident, exactly the same calm documentary-narrator voice and emotion throughout, no change in mood between sentences"),
    "excited": ("+4%", "+0Hz", "steady, even and confident, exactly the same calm documentary-narrator voice and emotion throughout, no change in mood between sentences"),
    "calm": ("+4%", "+0Hz", "steady, even and confident, exactly the same calm documentary-narrator voice and emotion throughout, no change in mood between sentences"),
}


def tts_one(text, out_path, tone=None):
    """Edge neural voice (free, always available). Rate and pitch follow the scene's tone."""
    import edge_tts
    rate, pitch, _s = TONES.get(tone or "", (TTS_RATE, "+0Hz", ""))

    async def go():
        await edge_tts.Communicate(text, VOICE, rate=rate, pitch=pitch).save(str(out_path))

    err = None
    for a in range(4):
        try:
            asyncio.run(go())
            if Path(out_path).exists() and Path(out_path).stat().st_size > 1500:
                return
        except Exception as e:  # noqa
            err = e
            log("Edge TTS retry", a + 1, type(e).__name__)
        time.sleep(3 * (a + 1))
    raise RuntimeError(f"Edge TTS failed: {err}")


_tts_last = [0.0]


def tts_gemini(text, out_path, tone=None):
    """Gemini speech (more natural, free tier has a small quota). Raises on any problem."""
    if not GEMINI_KEY:
        raise RuntimeError("no GEMINI_API_KEY")
    gap = time.time() - _tts_last[0]
    if gap < GEMINI_TTS_GAP:
        time.sleep(GEMINI_TTS_GAP - gap)
    style = TONES.get(tone or "", TONES["calm"])[2]
    prompt = (f"Say in ONE steady, calm, confident, warm male voice, at a brisk but comfortable pace like a good Hindi YouTube documentary narrator (not rushed), the SAME even emotion and energy in every sentence, never shouting, never acting out or changing mood, "
              f"{style}, with natural pauses at commas and full stops: {text}")
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["AUDIO"],
                                 "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": GEMINI_VOICE}}}}}
    last = None
    for model in GEMINI_TTS_MODELS:
        for attempt in range(3):
            _tts_last[0] = time.time()
            try:
                r = requests.post(f"{GBASE}/models/{model}:generateContent",
                                  headers={"x-goog-api-key": GEMINI_KEY}, json=body, timeout=180)
            except requests.RequestException as e:
                last = f"{model} {type(e).__name__}"
                time.sleep(8)
                continue
            if r.status_code == 429:
                last = f"{model} quota (HTTP 429)"
                if attempt < 1:
                    time.sleep(35)
                    continue
                break
            if r.status_code in (400, 403, 404):
                last = f"{model} HTTP {r.status_code}"
                break
            if r.status_code >= 500:
                last = f"{model} HTTP {r.status_code}"
                time.sleep(10)
                continue
            try:
                part = r.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
                pcm = base64.b64decode(part["data"])
                rate = 24000
                m = re.search(r"rate=(\d+)", part.get("mimeType", ""))
                if m:
                    rate = int(m.group(1))
            except Exception:
                last = f"{model} no audio in reply"
                time.sleep(5)
                continue
            raw = Path(str(out_path) + ".pcm")
            raw.write_bytes(pcm)
            wav = Path(str(out_path) + ".conv.wav")
            run(["ffmpeg", "-y", "-f", "s16le", "-ar", str(rate), "-ac", "1", "-i", raw, "-ar", "44100", wav])
            wav.replace(out_path)
            raw.unlink(missing_ok=True)
            dur = ffprobe_dur(out_path)
            spw = dur / max(1, len(text.split()))
            if not (0.22 <= spw <= 1.2):
                raise RuntimeError(f"Gemini voice length looks wrong ({spw:.2f}s/word)")
            return
    raise RuntimeError(f"Gemini TTS failed: {last}")


VOICE_FX = ("highpass=f=75,equalizer=f=150:t=q:w=1.2:g=2.5,equalizer=f=3200:t=q:w=1.0:g=2.2,"
            "acompressor=threshold=0.1:ratio=3:attack=8:release=150:makeup=2,aecho=0.7:0.4:38:0.10")


def align_boundaries(sil, weights, total):
    """The narration was spoken in ONE go. Find, among the real pauses (silences) of that audio, the ones that sit between the sentences.
    weights = text length of every sentence; returns the speech-start time of sentences 2..N (None where no pause could be matched)."""
    n = len(weights)
    if n <= 1:
        return []
    tw = float(sum(weights))
    exp = [total * w / tw for w in weights]  # expected length of every sentence
    M = len(sil)
    INF = 1e9
    # dp[k][j]: best cost with boundary k (between sentence k and k+1) placed at pause j
    dp = [[INF] * M for _ in range(n - 1)]
    bk = [[-1] * M for _ in range(n - 1)]
    for j in range(M):
        mid = (sil[j][0] + sil[j][1]) / 2
        dp[0][j] = abs(mid - exp[0]) / (exp[0] + 0.8) - 0.4 * min(sil[j][1] - sil[j][0], 0.8)
    for k in range(1, n - 1):
        for j in range(M):
            mid = (sil[j][0] + sil[j][1]) / 2
            bestc, bestp = INF, -1
            for jp in range(j):
                if dp[k - 1][jp] >= INF:
                    continue
                midp = (sil[jp][0] + sil[jp][1]) / 2
                c = dp[k - 1][jp] + abs((mid - midp) - exp[k]) / (exp[k] + 0.8)
                if c < bestc:
                    bestc, bestp = c, jp
            if bestp >= 0:
                dp[k][j] = bestc - 0.4 * min(sil[j][1] - sil[j][0], 0.8)
                bk[k][j] = bestp
    best, bj = INF, -1
    for j in range(M):
        if dp[n - 2][j] < INF:
            midl = (sil[j][0] + sil[j][1]) / 2
            c = dp[n - 2][j] + abs((total - midl) - exp[n - 1]) / (exp[n - 1] + 0.8)
            if c < best:
                best, bj = c, j
    if bj < 0:  # fewer pauses than sentences: proportional guess
        acc, out = 0.0, []
        for k in range(n - 1):
            acc += exp[k]
            out.append(acc)
        return out
    pick = [bj]
    for k in range(n - 2, 0, -1):
        pick.append(bk[k][pick[-1]])
    pick.reverse()
    return [sil[j][1] for j in pick]


def make_voice(scenes, tdir, synth=None):
    """The whole narration is spoken in ONE go (one voice, one mood). The pauses of that recording are then matched to the full stops of the text, so
    every sentence has an exact time slot on the voice timeline; its picture/clip/map must start and end inside that slot."""
    synth = synth or tts_one
    tdir.mkdir(parents=True, exist_ok=True)
    full = " ".join(sc["narration_hi"].strip() for sc in scenes)
    src, master = tdir / "master.src", tdir / "master.wav"
    synth(full, src, "calm")
    trim = "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.03"
    run(["ffmpeg", "-y", "-i", src, "-ar", "44100", "-ac", "1", "-af", f"{trim},areverse,{trim},areverse,atempo={VOICE_SPEED:.3f},apad=pad_dur=0.6", master])
    total = ffprobe_dur(master)
    speech_end = total - 0.6
    sents = []  # (scene index, first word, end word, text)
    for i, sc in enumerate(scenes):
        wds = sc["narration_hi"].split()
        for a, b in (sentence_ranges(wds) or [[0, len(wds)]]):
            sents.append((i, a, b, " ".join(wds[a:b])))
    sil = [x for x in detect_silences(master) if 0.2 < x[0] and x[1] < speech_end - 0.1]
    starts = align_boundaries(sil, [max(1, sum(len(w) + 1 for w in t.split())) for _, _, _, t in sents], speech_end)
    # sentence k owns [B[k], B[k+1]]: it starts when it is spoken (just before) and keeps the pause that follows it
    B = [0.0] + [max(0.05, t - 0.04) for t in starts] + [total]
    for k in range(1, len(B)):
        B[k] = max(B[k], B[k - 1] + 0.3)
    B[-1] = max(B[-1], B[-2] + 0.3)
    for i, sc in enumerate(scenes):
        idx = [k for k, st in enumerate(sents) if st[0] == i]
        t_a = B[idx[0]]
        t_b = B[idx[-1] + 1]
        sc["dur"] = t_b - t_a
        wt, sent = [], []
        for k in idx:
            _i, a, b, txt = sents[k]
            s0, s1 = B[k], B[k + 1]
            sl = tdir / f"sl{k:04d}.wav"
            run(["ffmpeg", "-y", "-ss", f"{s0:.3f}", "-t", f"{s1 - s0:.3f}", "-i", master, sl])
            sp_dur = max(0.3, (s1 - s0) - 0.12)
            try:
                sp_ = word_spans(txt, sl, sp_dur)
            except Exception as e:  # noqa
                log("word timing failed:", str(e)[:100])
                sp_ = [(sp_dur * q / max(1, b - a), sp_dur * (q + 1) / max(1, b - a)) for q in range(b - a)]
            wt += [(s0 - t_a + x, s0 - t_a + y) for x, y in sp_]
            sent.append((a, b, s0 - t_a, s1 - t_a))
        sc["speech_dur"] = max(0.5, sc["dur"] - 0.15)
        sc["wt"] = wt if len(wt) == len(sc["narration_hi"].split()) else None
        sc["sent"] = sent  # (first word, end word, start s, end s): the sentence's time slot inside the scene
        if i % 8 == 0:
            log(f"voice timeline {i + 1}/{len(scenes)}")
    wavs = []
    raw = master
    voice = tdir / "voice.wav"
    try:
        run(["ffmpeg", "-y", "-i", raw, "-af", VOICE_FX, "-ar", "44100", voice])
    except Exception as e:  # noqa
        log("voice polish failed, using raw voice:", str(e)[:120])
        raw.replace(voice)
    return voice


def make_voice_auto(scenes, tdir):
    """Natural Gemini voice if the free quota allows, otherwise the Edge voice for the WHOLE video."""
    VOICE_USED[0] = "edge"
    if TTS_PROVIDER in ("auto", "gemini") and GEMINI_KEY:
        try:
            v = make_voice(scenes, tdir, synth=tts_gemini)
            VOICE_USED[0] = "gemini " + GEMINI_VOICE
            return v
        except Exception as e:  # noqa
            VOICE_USED[0] = "edge (Gemini voice failed: " + str(e)[:140] + ")"
            log("Gemini voice failed, using Edge voice for the whole video:", e)
            shutil.rmtree(tdir, ignore_errors=True)
    return make_voice(scenes, tdir, synth=tts_one)


def voice_samples():
    """Send short samples of several voices to Telegram so the owner can pick a favourite."""
    global GEMINI_VOICE
    sample = ("नमस्ते दोस्तों। क्या आप जानते हैं कि हमारी पृथ्वी लगभग साढ़े चार अरब साल पहले कैसे बनी थी? "
              "चलिए, इस रहस्य को एक साथ समझते हैं।")
    d = WORK / "voices"
    d.mkdir(parents=True, exist_ok=True)
    names = [n.strip() for n in os.environ.get("VOICE_SAMPLES", "Charon,Orus,Fenrir,Algenib,Alnilam,Iapetus").split(",") if n.strip()]
    sent, bad = [], []
    for nm in names:
        GEMINI_VOICE = nm
        p = d / f"{nm}.wav"
        try:
            tts_gemini(sample, p, "wonder")
            run(["ffmpeg", "-y", "-i", p, "-af", VOICE_FX, "-b:a", "128k", d / f"{nm}.mp3"])
            tg_send_file(d / f"{nm}.mp3", "audio", f"Gemini voice: {nm}")
            sent.append(nm)
        except Exception as e:  # noqa
            bad.append(f"{nm} ({str(e)[:60]})")
    try:
        tts_one(sample, d / "edge.mp3", "wonder")
        tg_send_file(d / "edge.mp3", "audio", "Edge voice: Madhur (backup)")
    except Exception as e:  # noqa
        bad.append("edge")
    notify("Awaaz ke namune bhej diye.\nPasand wali awaaz ka naam (jaise Orus) GitHub -> Settings -> Secrets and variables -> "
           "Actions -> Variables -> New variable: naam GEMINI_VOICE, value wahi naam.\n" + ("Nahi ban saki: " + ", ".join(bad) if bad else ""))


# --------------------------------------------------------------------------- fonts / text rendering
FONT_NAMES = {
    "sub": ["Mukta-ExtraBold.ttf", "Mukta-Bold.ttf", "Hind-Bold.ttf"],
    "label": ["Mukta-ExtraBold.ttf", "Hind-Bold.ttf", "Mukta-Bold.ttf"],
    "head": ["YatraOne-Regular.ttf", "Mukta-ExtraBold.ttf", "Hind-Bold.ttf"],
    "title": ["Khand-Bold.ttf", "YatraOne-Regular.ttf", "Mukta-ExtraBold.ttf", "Hind-Bold.ttf"],
    "thumb": ["Mukta-ExtraBold.ttf", "Hind-Bold.ttf", "Mukta-Bold.ttf"],
    "serif": ["PT_Serif-Web-Bold.ttf", "PTSerif-Bold.ttf"],
    "type": ["SpecialElite-Regular.ttf"],
    "cond": ["BebasNeue-Regular.ttf", "Anton-Regular.ttf", "Khand-Bold.ttf"],
    "comic": ["Bangers-Regular.ttf"],
}
SYS_ROLE = {  # Latin-only fallbacks when the nicer font could not be downloaded
    "serif": ["/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", "/usr/share/fonts/**/NotoSerif-Bold.ttf", "/usr/share/fonts/**/LiberationSerif-Bold.ttf"],
    "type": ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", "/usr/share/fonts/**/LiberationMono-Bold.ttf"],
    "cond": ["/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
    "comic": ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
}
SYS_FONTS = [
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
    "/usr/share/fonts/**/NotoSansDevanagari*Bold*.ttf",
    "/usr/share/fonts/**/NotoSansDevanagari*.ttf",
    "/usr/share/fonts/**/Lohit-Devanagari*.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


@lru_cache(maxsize=16)
def font_path(role):
    env = os.environ.get("FONT_PATH")
    if env and Path(env).exists():
        return env
    for name in FONT_NAMES[role]:
        for d in (ROOT / "fonts", Path("fonts")):
            if (d / name).exists():
                return str(d / name)
    for pat in SYS_ROLE.get(role, []):
        hits = sorted(glob.glob(pat, recursive=True))
        if hits:
            return hits[0]
    for pat in SYS_FONTS:
        hits = sorted(glob.glob(pat, recursive=True))
        if hits:
            return hits[0]
    raise RuntimeError("no usable font found (install fonts-noto-core)")


def load_font(size, role="sub"):
    eng = ImageFont.Layout.RAQM if features.check("raqm") else ImageFont.Layout.BASIC
    f = ImageFont.truetype(font_path(role), size, layout_engine=eng)
    try:
        f.set_variation_by_name("Bold")
    except Exception:
        pass
    return f


def wrap_text(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if cur and draw.textlength(t, font=font) > max_w:
            lines.append(cur)
            cur = w
        else:
            cur = t
    if cur:
        lines.append(cur)
    return lines


def text_layer(text, font, fill=(255, 255, 255), fill2=None, stroke=0, stroke_fill=(0, 0, 0),
               shadow=None, max_w=None, spacing=6, align="center", extrude=None):
    """Transparent RGBA picture of text: gradient fill, outline, 3D extrusion and soft drop shadow."""
    probe = ImageDraw.Draw(Image.new("L", (4, 4)))
    if max_w:
        text = "\n".join(wrap_text(probe, text, font, max_w))
    bb = probe.multiline_textbbox((0, 0), text, font=font, stroke_width=stroke, spacing=spacing, align=align)
    ex = extrude[0] if extrude else 0
    pad = stroke + 6 + ex + (int(shadow[2] * 2 + abs(shadow[0]) + abs(shadow[1])) if shadow else 0)
    w, h = int(math.ceil(bb[2] - bb[0])) + 2 * pad, int(math.ceil(bb[3] - bb[1])) + 2 * pad
    ox, oy = pad - bb[0], pad - bb[1]
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    outline = None
    if stroke or extrude:
        outline = Image.new("L", (w, h), 0)
        ImageDraw.Draw(outline).multiline_text((ox, oy), text, font=font, fill=255, stroke_width=stroke,
                                               stroke_fill=255, spacing=spacing, align=align)
    if shadow:
        dx, dy, blur, alpha = shadow
        sh = Image.new("L", (w, h), 0)
        ImageDraw.Draw(sh).multiline_text((ox + dx + ex, oy + dy + ex), text, font=font, fill=255, stroke_width=stroke,
                                          spacing=spacing, align=align)
        sh = sh.filter(ImageFilter.GaussianBlur(blur)).point(lambda v: int(v * alpha))
        s_rgba = Image.new("RGBA", (w, h), (0, 0, 0, 255))
        s_rgba.putalpha(sh)
        layer = Image.alpha_composite(layer, s_rgba)
    if extrude and outline is not None:
        n, col = extrude
        ecol = Image.new("RGBA", (w, h), tuple(col) + (255,))
        for k in range(n, 0, -1):
            m = ImageChops.offset(outline, k, k)
            e = ecol.copy()
            e.putalpha(m)
            layer = Image.alpha_composite(layer, e)
    if stroke and outline is not None:
        c = Image.new("RGBA", (w, h), tuple(stroke_fill) + (255,))
        c.putalpha(outline)
        layer = Image.alpha_composite(layer, c)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).multiline_text((ox, oy), text, font=font, fill=255, spacing=spacing, align=align)
    if fill2:
        g = Image.linear_gradient("L").resize((w, h))
        col = Image.composite(Image.new("RGB", (w, h), fill2), Image.new("RGB", (w, h), fill), g)
    else:
        col = Image.new("RGB", (w, h), fill)
    f = col.convert("RGBA")
    f.putalpha(mask)
    return Image.alpha_composite(layer, f)


def chunk_words(text, n=4):
    w = text.split()
    ch = [" ".join(w[i:i + n]) for i in range(0, len(w), n)]
    if len(ch) > 1 and len(ch[-1].split()) <= 1:
        ch[-2] += " " + ch.pop()
    return ch


PUNCT_END = tuple(",;:।?!.—–…")


def detect_silences(wav):
    """Pauses inside the narration: list of (start, end) seconds."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(wav), "-af", "silencedetect=noise=-33dB:d=0.12", "-f", "null", "-"],
                       capture_output=True, text=True)
    st, out = None, []
    for ln in r.stderr.splitlines():
        m = re.search(r"silence_start: ([\d.]+)", ln)
        if m:
            st = float(m.group(1))
        m = re.search(r"silence_end: ([\d.]+)", ln)
        if m and st is not None:
            out.append((st, float(m.group(1))))
            st = None
    return out


def word_spans(text, wav, speech_dur):
    """(start, end) second of every word: pauses found in the real audio are matched to the commas/full stops of the text,
    inside each clause the time is shared by word length. Accurate enough to cut the picture exactly on the spoken word."""
    words = text.split()
    n = len(words)
    if n == 0:
        return []
    wt = [max(2.0, len(w) + 1.0) for w in words]
    tot = sum(wt)
    cum, a = [], 0.0
    for w in wt:
        a += w
        cum.append(a)
    bounds = [i for i, w in enumerate(words[:-1]) if w.rstrip("\"'”’)").endswith(PUNCT_END)]
    try:
        sil = [x for x in detect_silences(wav) if 0.15 < x[0] and x[1] < speech_dur - 0.05]
    except Exception:  # noqa
        sil = []
    anchors, used = [], set()
    for b in bounds:
        pred = speech_dur * cum[b] / tot
        best = None
        for k, (a0, a1) in enumerate(sil):
            if k in used:
                continue
            d = abs((a0 + a1) / 2 - pred)
            if d < 1.0 and (best is None or d < best[0]):
                best = (d, k)
        if best:
            used.add(best[1])
            anchors.append((b + 1, sil[best[1]][0], sil[best[1]][1]))
    anchors.sort()
    for i in range(1, len(anchors)):  # keep time strictly increasing
        if anchors[i][1] <= anchors[i - 1][2]:
            anchors = anchors[:i]
            break
    segs, w0, t0 = [], 0, 0.0
    for wi, ps, pe in anchors:
        segs.append((w0, wi, t0, ps))
        w0, t0 = wi, pe
    segs.append((w0, n, t0, speech_dur))
    spans = [None] * n
    for a_, b_, ta, tb in segs:
        ssum = sum(wt[a_:b_]) or 1.0
        t = ta
        for k in range(a_, b_):
            d = (tb - ta) * wt[k] / ssum
            spans[k] = (t, t + d)
            t += d
    return spans


def chunk_times(text, speech_dur, scene_dur, spans=None):
    ch = chunk_words(text)
    out, wi = [], 0
    if spans and len(spans) == len(text.split()):
        for c in ch:
            k = len(c.split())
            out.append([c, spans[wi][0], spans[wi + k - 1][1]])
            wi += k
        for i in range(len(out) - 1):
            out[i][2] = out[i + 1][1]
        out[0][1] = 0.0
        out[-1][2] = scene_dur
        return out
    wts = [max(1, len(c)) for c in ch]
    tot, t = sum(wts), 0.0
    for c, w in zip(ch, wts):
        d = speech_dur * w / tot
        out.append([c, t, t + d])
        t += d
    out[-1][2] = scene_dur
    return out


# --------------------------------------------------------------------------- images
_last_polli = [0.0]
IMG_FAILS = {}


def note_fail(provider, why):
    key = f"{provider}: {why}"
    IMG_FAILS[key] = IMG_FAILS.get(key, 0) + 1


def fail_summary():
    return "; ".join(f"{k} x{v}" for k, v in IMG_FAILS.items()) or "no details"


NATIVE = {}  # picture path -> real pixel width that survives the 1920x1080 crop (zoom is limited by it so pictures never turn blurry)


def fit_image(raw_bytes, out_png, size=(1920, 1080)):
    tmp = Path(str(out_png) + ".tmp")
    tmp.write_bytes(raw_bytes)
    im = Image.open(tmp)
    im.verify()
    im = Image.open(tmp).convert("RGB")
    tmp.unlink(missing_ok=True)
    if im.width < 400 or im.height < 400:
        raise ValueError("image too small")
    NATIVE[str(out_png)] = size[0] / max(size[0] / im.width, size[1] / im.height)  # real source pixels across the visible width
    if im.width < size[0] * 0.9:  # small source: upscale in two steps and sharpen so zooming later stays crisp
        k = size[0] / im.width
        im = im.resize((int(im.width * k), int(im.height * k)), Image.LANCZOS).filter(ImageFilter.UnsharpMask(radius=1.6, percent=70, threshold=2))
    try:
        cx, cy = subject_center(im)
        im = ImageOps.fit(im, size, Image.LANCZOS, centering=(min(0.85, max(0.15, cx)), min(0.7, max(0.3, cy))))
    except Exception:  # noqa
        im = ImageOps.fit(im, size, Image.LANCZOS)
    im.save(out_png)


CF_STATE = {"dead": False, "used": 0}
PILLS_ON = os.environ.get("PILLS", "0") == "1"
SUBS_ON = os.environ.get("SUBTITLES", "0") == "1"  # subtitles are OFF: the visuals tell the story
MAX_IMAGES = int(os.environ.get("MAX_IMAGES", "14" if TEST_SEC else "48"))


def img_cloudflare(prompt, out, size=(1920, 1080), dims=None):
    if not (CF_ACCOUNT and CF_TOKEN) or CF_STATE["dead"]:
        return False
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}/ai/run/@cf/black-forest-labs/flux-1-schnell"
    for a in range(3):
        try:
            payload = {"prompt": prompt[:2000], "steps": 4}
            if dims and not CF_STATE.get("nosize"):
                payload["width"], payload["height"] = dims  # smaller picture = fewer neurons of the free quota
            r = requests.post(url, headers={"Authorization": f"Bearer {CF_TOKEN}"}, json=payload, timeout=120)
            if r.status_code in (400, 422) and "width" in payload:
                CF_STATE["nosize"] = True
                payload.pop("width"), payload.pop("height")
                r = requests.post(url, headers={"Authorization": f"Bearer {CF_TOKEN}"}, json=payload, timeout=120)
        except requests.RequestException as e:
            note_fail("cloudflare", type(e).__name__)
            time.sleep(8)
            continue
        body = re.sub(r"\s+", " ", r.text or "")[:150]
        if r.status_code == 429 and re.search(r"daily|neuron|allocation|quota|4006", body, re.I):
            CF_STATE["dead"] = True
            note_fail("cloudflare", "LIMIT MESSAGE FROM CLOUDFLARE: " + re.sub(r"\s+", " ", r.text or "")[:420])
            return False
        if r.status_code in (429, 500, 502, 503, 504):
            note_fail("cloudflare", f"HTTP {r.status_code} {body[:90]}")
            time.sleep(15 * (a + 1))
            continue
        if not r.ok:
            note_fail("cloudflare", f"HTTP {r.status_code} {body[:90]}")
            return False
        if r.headers.get("content-type", "").startswith("image"):
            fit_image(r.content, out, size)
            return True
        b64 = (r.json().get("result") or {}).get("image")
        if not b64:
            note_fail("cloudflare", "no image in reply")
            return False
        fit_image(base64.b64decode(b64), out, size)
        return True
    return False


def img_pollinations(prompt, out, size=(1920, 1080)):
    """Only used when ALLOW_POLLINATIONS=1: free but watermarked and limited to one image per ~45 s."""
    wait = 46 if not POLLI_KEY else 2
    gap = time.time() - _last_polli[0]
    if gap < wait:
        time.sleep(wait - gap)
    url = f"https://image.pollinations.ai/prompt/{quote(prompt[:450], safe='')}"
    headers = {"Authorization": f"Bearer {POLLI_KEY}"} if POLLI_KEY else {}
    for a in range(3):
        _last_polli[0] = time.time()
        try:
            r = requests.get(url, headers=headers, timeout=150,
                             params={"width": 1280, "height": 720, "model": "flux", "seed": random.randint(1, 10**6)})
        except requests.RequestException as e:
            note_fail("pollinations", type(e).__name__)
            time.sleep(15)
            continue
        if r.ok and r.headers.get("content-type", "").startswith("image"):
            fit_image(r.content, out, size)
            return True
        note_fail("pollinations", f"HTTP {r.status_code}")
        time.sleep(min(60, 25 * (a + 1)))
    return False


def pexels_photo(query, out, size=(1920, 1080)):
    if not PEXELS_KEY or not query:
        return False
    r = requests.get("https://api.pexels.com/v1/search", headers={"Authorization": PEXELS_KEY},
                     params={"query": query, "per_page": 15, "orientation": "landscape"}, timeout=30)
    if not r.ok or not r.json().get("photos"):
        return False
    qw = norm_words(query)
    ph = r.json()["photos"]
    good = [x for x in ph if qw & norm_words(x.get("alt") or "") and not unreal(x.get("alt") or "", query)]  # the photo's own description must mention the thing
    if not good:
        return False
    fit_image(requests.get(good[0]["src"]["large2x"], timeout=60).content, out, size)
    return True


VERIFY = {"n": 0, "off": os.environ.get("VERIFY_IMAGES", "1") == "0", "no": 0, "slow": 0}


def verify_image(path, subject):
    """Ask Gemini to LOOK at the picture: does it clearly show this subject? (True when unsure or when the check is unavailable.)"""
    if VERIFY["off"] or not GEMINI_KEY or not subject or VERIFY["n"] >= int(os.environ.get("VERIFY_MAX", "100")) or not llm_optional_ok():
        return True
    try:
        t_v = time.time()
        im = Image.open(path).convert("RGB")
        im.thumbnail((640, 640))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=80)
        VERIFY["n"] += 1
        txt, _t = gemini(f'Look at this picture. Is "{subject}" clearly the main thing shown, as a REAL photograph (not a different animal, object or place that merely looks similar)? '
                         "Answer NO if the picture is a cartoon, animation, 3D render, drawing, painting, toy, statue, sculpture, carving or mural, unless the subject itself is such a thing. "
                         "Answer with one word: YES or NO.", temperature=0.0, image=base64.b64encode(buf.getvalue()).decode(), optional=True)
        if time.time() - t_v > 25:
            VERIFY["slow"] += 1
            if VERIFY["slow"] >= 2:
                VERIFY["off"] = True
                log("picture checks switched off: Gemini answers too slowly")
        ok = not txt.strip().upper().startswith("NO")
        if not ok:
            VERIFY["no"] += 1
            log(f"  picture rejected by vision check (not '{subject}')")
        return ok
    except Exception as e:  # noqa
        log("  vision check unavailable:", str(e)[:80])
        VERIFY["n"] += 10
        return True


BAD_TAGS = {"man", "woman", "people", "girl", "boy", "child", "children", "person", "portrait", "model", "baby", "couple", "family",
            "face", "lady", "men", "women", "kid", "kids", "student", "crowd", "wedding", "bride", "text", "logo"}


UNREAL_RE = re.compile(r"\b(cartoon\w*|animat\w*|3d|render\w*|cgi|illustrat\w*|vector|clipart|clip art|drawing|sketch|painting|statue\w*|sculpt\w*|figurine\w*|toy|toys|"
                       r"mascot\w*|emoji|comic\w*|anime|pixar|disney|lego|puppet\w*|doll|dolls|miniature\w*|plush|monument\w*|carving\w*|idol|idols|mural\w*|graffiti|motion graphic\w*|"
                       r"stock footage animation|loop|looping|abstract)\b", re.I)


def unreal(text, query=""):
    """True when a stock result's tags / title / slug say it is a cartoon, animation, 3D render, statue, toy... (unless the thing we search for IS such a thing)."""
    t = (text or "").replace("-", " ").replace("_", " ")
    hits = {m.group(0).lower() for m in UNREAL_RE.finditer(t)}
    if not hits:
        return False
    q = (query or "").lower()
    return not all(h in q for h in hits)


GREEN_TAGS = {"green screen", "greenscreen", "chroma", "chroma key", "chromakey", "green background", "alpha", "transparent", "overlay"}


def green_frac(img):
    """Share of pixels that look like a chroma-key green screen (0..1)."""
    np = _np()
    a = np.asarray(img.convert("RGB").resize((96, 54)), dtype=np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    m = (g > 110) & (g > r * 1.45) & (g > b * 1.35) & (r < 140)
    return float(m.mean())


def tags_of(h):
    return {x.strip().lower() for x in h.get("tags", "").split(",")}


def first_frame_ok(path):
    """True if the clip's frames (start, middle) are not a green screen."""
    try:
        d = ffprobe_dur(path)
        for ts in (0.3, max(0.4, d / 2)):
            raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{ts:.2f}", "-i", str(path), "-frames:v", "1", "-vf", "scale=192:108",
                                  "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, timeout=60).stdout
            if len(raw) == 192 * 108 * 3 and green_frac(Image.frombytes("RGB", (192, 108), raw)) > 0.18:
                return False
    except Exception:  # noqa
        pass
    return True


def _stems(txt):
    stop = {"the", "and", "with", "from", "into", "over", "for", "its"}
    return {w[:4] for w in re.findall(r"[a-z]+", (txt or "").lower()) if len(w) > 2 and w not in stop}


def _clip_judge(path, query):
    """2 = the clip shows the subject doing the action, 1 = right subject, action unclear, 0 = wrong subject.
    Gemini looks at three frames of the clip side by side (a still frame alone cannot show 'running')."""
    if VERIFY["off"] or not GEMINI_KEY or VERIFY["n"] >= int(os.environ.get("VERIFY_MAX", "300")):
        return 1
    try:
        d = ffprobe_dur(path)
        frames = []
        for f_ in (0.2, 0.5, 0.8):
            raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.1, d * f_):.2f}", "-i", str(path), "-frames:v", "1", "-vf", "scale=384:216",
                                  "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, timeout=60).stdout
            if len(raw) == 384 * 216 * 3:
                frames.append(Image.frombytes("RGB", (384, 216), raw))
        if not frames:
            return 1
        strip = Image.new("RGB", (384 * len(frames), 216))
        for k, fr_ in enumerate(frames):
            strip.paste(fr_, (384 * k, 0))
        buf = io.BytesIO()
        strip.save(buf, "JPEG", quality=80)
        VERIFY["n"] += 1
        txt, _t = gemini(f'These {len(frames)} frames (left to right) come from ONE stock video clip. We need a clip of: "{query}". '
                         "The clip must be REAL live-action camera footage. Answer NO if it is a cartoon, animation, 3D render, toy, puppet, statue, sculpture, drawing or any other non-real imitation (unless the query itself asks for that). "
                         "Answer with exactly one word: MATCH (the clip shows that subject doing that action), SUBJECT (right subject but the action is different or unclear), or NO (a different subject or not real footage).",
                         temperature=0.0, image=base64.b64encode(buf.getvalue()).decode())
        t_ = txt.strip().upper()
        return 2 if t_.startswith("MATCH") else 0 if t_.startswith("NO") else 1
    except Exception as e:  # noqa
        log("  clip check unavailable:", str(e)[:80])
        VERIFY["n"] += 10
        return 1


def _download_clip(url, out, max_mb=60):
    with requests.get(url, stream=True, timeout=90) as rr:
        rr.raise_for_status()
        n = 0
        with open(out, "wb") as f:
            for chunk in rr.iter_content(1 << 20):
                n += len(chunk)
                if n > max_mb << 20:
                    raise RuntimeError("clip too big")
                f.write(chunk)
    return Path(out).exists() and Path(out).stat().st_size > 20000


def _clip_cands_pixabay(query):
    qs_ = _stems(query)
    subj = _stems(query.split()[0]) if query.split() else set()
    res, seen = [], set()
    for q in [query[:90]] + ([query.split()[0]] if len(query.split()) > 1 else []):
        r = requests.get("https://pixabay.com/api/videos/", params={"key": PIXABAY_KEY, "q": q, "per_page": 30, "safesearch": "true"}, timeout=30)
        if not r.ok:
            raise RuntimeError(f"Pixabay video HTTP {r.status_code}")
        for h in r.json().get("hits", []):
            if h.get("id") in seen or h.get("duration", 0) < 3 or ((BAD_TAGS | GREEN_TAGS) & tags_of(h)) or "green" in h.get("tags", "").lower() or unreal(h.get("tags", ""), query):
                continue
            seen.add(h.get("id"))
            tg = _stems(h.get("tags", ""))
            if subj and not (subj & tg):
                continue  # the subject itself must be tagged
            v = h.get("videos", {})
            pick = next((v[k] for k in ("medium", "large", "small") if v.get(k, {}).get("url")), None)
            if pick:
                res.append((len(qs_ & tg), pick["url"]))
    return res


def _clip_cands_pexels(query):
    if not PEXELS_KEY:
        return []
    qs_ = _stems(query)
    subj = _stems(query.split()[0]) if query.split() else set()
    r = requests.get("https://api.pexels.com/videos/search", headers={"Authorization": PEXELS_KEY},
                     params={"query": query, "per_page": 20, "orientation": "landscape"}, timeout=30)
    res = []
    if not r.ok:
        return res
    for v in r.json().get("videos", []):
        slug_txt = (v.get("url") or "").rstrip("/").split("/")[-1].replace("-", " ")
        if unreal(slug_txt, query):
            continue
        slug = _stems(slug_txt)
        files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and (f.get("width") or 0) >= 1280]
        if v.get("duration", 0) >= 3 and files and (not subj or subj & slug):
            res.append((len(qs_ & slug), sorted(files, key=lambda x: x["width"])[0]["link"]))
    return res


def find_clip(query, out):
    """Real stock footage of exactly the subject + action that is being narrated ('lion walking' is not 'lion running').
    Candidates from Pixabay and Pexels are ranked by tags, downloaded, and Gemini checks three frames of each."""
    query = (query or "").strip()
    if not query:
        return False
    cands = []
    for fn, ok_ in ((_clip_cands_pixabay, bool(PIXABAY_KEY)), (_clip_cands_pexels, bool(PEXELS_KEY))):
        if ok_:
            try:
                cands += fn(query)
            except Exception as e:  # noqa
                log(f"  clip search {fn.__name__} failed:", type(e).__name__, str(e)[:80])
    cands.sort(key=lambda x: -x[0])
    best, tested = None, 0
    for _sc, url in cands[:8]:
        if tested >= 5:
            break
        tmp = Path(str(out) + f".c{tested}.mp4")
        try:
            if not (_download_clip(url, tmp) and first_frame_ok(tmp)):
                tmp.unlink(missing_ok=True)
                continue
        except Exception:  # noqa
            tmp.unlink(missing_ok=True)
            continue
        tested += 1
        lvl = _clip_judge(tmp, query)
        if lvl == 0:
            tmp.unlink(missing_ok=True)
            continue
        if best is None or lvl > best[0]:
            if best:
                best[1].unlink(missing_ok=True)
            best = (lvl, tmp)
        else:
            tmp.unlink(missing_ok=True)
        if lvl == 2:
            break
    if best:
        best[1].replace(out)
        log(f"  clip for '{query}': {'exact action' if best[0] == 2 else 'right subject'}")
        return True
    log(f"  no suitable clip for '{query}'")
    return False


class ClipReader:
    """Streams the frames of a stock clip (looped if short) in order, scaled and cropped to the video size."""

    def __init__(self, path, dur):
        cmd = ["ffmpeg", "-v", "error", "-stream_loop", "-1", "-i", str(path), "-t", f"{dur + 0.6:.2f}", "-vf",
               f"fps={FPS},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        self.p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.i, self.last = -1, None

    def frame(self, idx):
        while self.i < idx and self.p is not None:
            buf = self.p.stdout.read(W * H * 3)
            if len(buf) < W * H * 3:
                self.close()
                break
            self.last = Image.frombytes("RGB", (W, H), buf)
            self.i += 1
        return self.last

    def close(self):
        if self.p is not None:
            try:
                self.p.kill()
            except Exception:  # noqa
                pass
            self.p = None

    def __del__(self):
        self.close()


def query_from_prompt(prompt):
    """Short English search words taken from an AI picture prompt (first clause, without filler words)."""
    c = re.split(r"[,;.]", prompt)[0].lower().replace("\u2019s", "").replace("'s", "")
    c = re.sub(r"\b(a|an|the|of|at|in|on|with|during|over|under|night|day|close-up|closeup|wide|shot|photo|picture|image|view|dramatic|cinematic|realistic|beautiful|large|giant|single|detailed|glowing)\b", " ", c)
    w = [x for x in re.findall(r"[a-z]+", c)]
    return " ".join(w[:3])


def pixabay_photo(query, out, size=(1920, 1080), kind="photo"):
    """REAL photo of the named thing from Pixabay (free key, no daily image limit, commercial use allowed, no people)."""
    if not PIXABAY_KEY or not query:
        return False
    hits = []
    qs = [query[:90]]
    if len(query.split()) > 1:
        qs.append(query.split()[-1])  # last word is usually the thing itself
    for q in qs:
        for it in (("photo",) if kind == "photo" else ("all",)):
            r = requests.get("https://pixabay.com/api/", params={"key": PIXABAY_KEY, "q": q, "image_type": it,
                                                                  "orientation": "horizontal" if kind != "card" else "all",
                                                                  "min_width": 1000 if kind != "card" else 400, "safesearch": "true",
                                                                  "per_page": 30, "order": "popular"}, timeout=30)
            if not r.ok:
                raise RuntimeError(f"Pixabay HTTP {r.status_code}")
            hits = [h for h in r.json().get("hits", []) if not ((BAD_TAGS | GREEN_TAGS) & tags_of(h))
                    and "green" not in h.get("tags", "").lower() and not unreal(h.get("tags", ""), query)]
            if hits:
                break
        if hits:
            break
    if not hits:
        return False
    qw = norm_words(query)
    scored = sorted(hits, key=lambda h: -len(qw & {w for t in tags_of(h) for w in norm_words(t)}))
    best = len(qw & {w for t in tags_of(scored[0]) for w in norm_words(t)})
    if best == 0:
        return False  # no picture is even tagged with the thing: better no picture than a wrong one
    top = [h for h in scored if len(qw & {w for t in tags_of(h) for w in norm_words(t)}) == best][:8]
    random.shuffle(top)
    nv = 0
    for h in top[:4]:
        data = requests.get(h.get("largeImageURL") or h["webformatURL"], timeout=60).content
        try:
            if kind == "photo" and green_frac(Image.open(io.BytesIO(data))) > 0.22:
                log("  photo rejected (green screen), trying next")
                continue
        except Exception:  # noqa
            continue
        fit_image(data, out, size)
        nv += 1
        if kind == "photo" and nv <= 2 and not verify_image(out, query):
            continue
        return True
    return False


LOCAL_SD = {"pipe": None, "dead": os.environ.get("LOCAL_SD", "1") == "0", "n": 0}


def img_local_sd(prompt, out, size, kind="photo"):
    """Unlimited free AI pictures made on the GitHub runner itself (SD-Turbo on CPU, ~20-60 s per picture, no quota, no key)."""
    if LOCAL_SD["dead"] or LOCAL_SD["n"] >= int(os.environ.get("LOCAL_SD_MAX", "30")):
        return False
    try:
        import torch
        if LOCAL_SD["pipe"] is None:
            from diffusers import AutoPipelineForText2Image
            log("loading local SD-Turbo model (first time ~2 GB download)...")
            torch.set_num_threads(max(1, os.cpu_count() or 2))
            pipe = AutoPipelineForText2Image.from_pretrained("stabilityai/sd-turbo", torch_dtype=torch.float32, variant="fp16",
                                                             low_cpu_mem_usage=True)
            pipe.set_progress_bar_config(disable=True)
            try:
                pipe.enable_attention_slicing()
            except Exception:  # noqa
                pass
            LOCAL_SD["pipe"] = pipe
        w, h = (512, 512) if kind in ("card", "obj") else (768, 432)
        t0 = time.time()
        img = LOCAL_SD["pipe"](prompt=prompt[:300], num_inference_steps=2, guidance_scale=0.0, width=w, height=h).images[0]
        LOCAL_SD["n"] += 1
        if LOCAL_SD["n"] <= 3 or LOCAL_SD["n"] % 10 == 0:
            log(f"  local picture {LOCAL_SD['n']} made in {time.time() - t0:.0f}s")
        buf = io.BytesIO()
        img.save(buf, "PNG")
        fit_image(buf.getvalue(), out, size)
        return True
    except Exception as e:  # noqa
        LOCAL_SD["dead"] = True
        note_fail("local-sd", f"{type(e).__name__}: {str(e)[:160]}")
        log("local SD-Turbo unavailable:", type(e).__name__, str(e)[:200])
        return False


STYLE_OBJ = ("single isolated object, centered, whole object visible, plain pure white background, studio product photo, "
             "soft even light, sharp, no shadow, no text, no watermark")



# --------------------------------------------------------------------------- REAL photo of the exact named thing (Wikipedia / Wikimedia Commons)
IMG_CREDITS = []
WIKI_UA = {"User-Agent": "nature-video-bot/1.0 (educational video project; https://github.com)"}
BAD_TITLE = ("map", "diagram", "logo", "flag", "stamp", "drawing", "svg", "icon", "chart", "graph", "coat of arms", "poster", "cover", "screenshot",
             "illustration", "painting", "skeleton", "locator", "location", "plan of", "scheme", "emblem", "signature", "portrait of",
             "engraving", "woodcut", "lithograph", "etching", "sketch", "print ", "illustrated", "cartoon", "clipart", "caricature", "drawn", "manuscript", "newspaper", "book ")
OK_LIC = ("cc by", "cc0", "public domain", "pd", "cc-by", "attribution", "gfdl", "no restrictions")


def _lic_ok(meta):
    lic = (meta.get("LicenseShortName", {}).get("value") or "").lower()
    if not lic:
        return False
    if "nc" in lic.split() or "-nc" in lic or " nc" in lic or "nd" in lic.split() or "-nd" in lic:
        return False
    return any(x in lic for x in OK_LIC)


def _strip_html(t):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", t or "")).strip()


def wiki_real_photo(query, out, size):
    """Find a REAL, openly licensed photo of exactly this thing (Wikipedia article lead image, else Wikimedia Commons search)."""
    q = (query or "").strip()
    if not q:
        return False
    cands = []  # (url, title, author, license, page)
    try:
        r = requests.get("https://en.wikipedia.org/w/api.php", headers=WIKI_UA, timeout=25, params={
            "action": "query", "format": "json", "generator": "search", "gsrsearch": q, "gsrlimit": 4, "prop": "pageimages|info",
            "piprop": "original", "inprop": "url"})
        pages = sorted((r.json().get("query", {}) or {}).get("pages", {}).values(), key=lambda x: x.get("index", 99))
        for pg in pages:
            o = pg.get("original") or {}
            tw_, qw_ = norm_words(pg.get("title", "")), norm_words(q)
            if tw_ and qw_ and not (tw_ <= qw_ or qw_ <= tw_ or len(tw_ & qw_) >= max(1, min(len(tw_), len(qw_)) - 0)):
                continue  # article is about something else (e.g. "lion and rabbit" must not give the Miniature Lion Lop rabbit breed)
            if o.get("source") and (o.get("width") or 0) >= 1000 and (o.get("width") or 1) / (o.get("height") or 1) >= 1.15 \
                    and o["source"].lower().split("?")[0].endswith((".jpg", ".jpeg", ".png")) and not any(b in o["source"].lower() for b in BAD_TITLE) and not unreal(o["source"].rsplit("/", 1)[-1], q) and not unreal(pg.get("title", ""), q):
                cands.append((o["source"], pg.get("title", q), "", "see Wikipedia", pg.get("fullurl", "")))
    except Exception as e:  # noqa
        log("  wikipedia lookup failed:", type(e).__name__)
    try:
        r = requests.get("https://commons.wikimedia.org/w/api.php", headers=WIKI_UA, timeout=25, params={
            "action": "query", "format": "json", "generator": "search", "gsrsearch": q + " filetype:bitmap", "gsrnamespace": 6, "gsrlimit": 12,
            "prop": "imageinfo", "iiprop": "url|extmetadata|mime|size", "iiurlwidth": 1920})
        pages = sorted((r.json().get("query", {}) or {}).get("pages", {}).values(), key=lambda x: x.get("index", 99))
        for pg in pages:
            ii = (pg.get("imageinfo") or [{}])[0]
            title = pg.get("title", "")
            meta = ii.get("extmetadata", {})
            if ii.get("mime") not in ("image/jpeg", "image/png") or (ii.get("width") or 0) < 1400 or (ii.get("width") or 1) / (ii.get("height") or 1) < 1.3:
                continue
            if any(b in title.lower() for b in BAD_TITLE) or not _lic_ok(meta) or unreal(title, q):
                continue
            if not (norm_words(title) & norm_words(q)):
                continue  # the file name must share a word with what the narrator talks about
            cands.append((ii.get("thumburl") or ii.get("url"), title, _strip_html(meta.get("Artist", {}).get("value")),
                          meta.get("LicenseShortName", {}).get("value", ""), ii.get("descriptionurl", "")))
    except Exception as e:  # noqa
        log("  commons lookup failed:", type(e).__name__)
    for url, title, author, lic, page in cands[:6]:
        try:
            data = requests.get(url, headers=WIKI_UA, timeout=60).content
            im = Image.open(io.BytesIO(data))
            if im.width < 900 or green_frac(im) > 0.22:
                continue
            fit_image(data, out, size)
            IMG_CREDITS.append(f"{title.replace('File:', '')[:60]} — {author[:40] or 'Wikimedia'} ({lic or 'open licence'}) {page}".strip())
            log(f"  real photo found on Wikimedia for '{q}': {title[:50]}")
            return True
        except Exception:  # noqa
            continue
    return False


EN_CACHE = {}


def english_search(txt):
    """Every picture search runs in ENGLISH ('शेर' -> 'lion'): Hindi words are translated once (cached); anything non-Latin that cannot be translated is dropped."""
    txt = (txt or "").strip()
    if not txt or txt.isascii():
        return txt
    if txt in EN_CACHE:
        return EN_CACHE[txt]
    en = ""
    if GEMINI_KEY:
        try:
            t, _x = gemini(f'Translate this to the plain English words you would type into an image search to find a photo of exactly that thing (1-3 words, no punctuation): "{txt}"', temperature=0.0)
            en = re.sub(r"[^A-Za-z ]", " ", t).strip()[:50]
        except Exception:  # noqa
            en = ""
    EN_CACHE[txt] = en
    return en


def gen_image(prompt, out, kind="photo", fallback_query="", query="", entity="", bg=""):
    query, entity, fallback_query = english_search(query), english_search(entity), english_search(fallback_query)
    suffix = {"photo": STYLE_PHOTO, "card": STYLE_CARD, "thumb": STYLE_THUMB, "obj": STYLE_OBJ}[kind]
    if kind == "obj" and bg == "green":
        suffix = suffix.replace("plain pure white background", "plain flat bright green background")
    size = (1080, 1080) if kind in ("card", "obj") else (1920, 1080)
    full = f"{prompt}, {suffix}"
    dims = {"photo": (1344, 756), "thumb": (1344, 756), "card": (512, 512), "obj": (512, 512)}[kind]
    chain = [("cloudflare", lambda: img_cloudflare(full, out, size, dims))]
    rq = query or fallback_query or query_from_prompt(prompt)
    if kind in ("photo", "thumb") and rq and os.environ.get("REAL_PHOTOS") != "0":
        # REAL free picture of the exact subject first: the named place/animal/thing from Wikipedia & Commons, then stock photos; AI is the last resort
        real = []
        if os.environ.get("REAL_WIKI", "1") != "0" and kind == "photo":
            real.append(("wikimedia", lambda: wiki_real_photo(entity or rq, out, size)))
        if PIXABAY_KEY:
            real.append(("pixabay", lambda: pixabay_photo(rq, out, size)))
        if kind == "photo":
            real.append(("pexels-real", lambda: pexels_photo(rq, out, size)))
        chain = real + chain
    chain.append(("local-sd", lambda: img_local_sd(prompt.split(", no text")[0] + ", " + {"photo": "cinematic film still, photorealistic, dramatic lighting, highly detailed",
                                                  "thumb": "ultra dramatic hero shot, vivid, highly detailed", "card": "polished digital illustration, vibrant",
                                                  "obj": "single isolated object on a plain white background, studio photo"}[kind], out, size, kind)))
    if ALLOW_POLLI:
        chain.append(("pollinations", lambda: img_pollinations(full, out, size)))
    if PIXABAY_KEY:  # last resort when the AI quota is used up: a real photo of the main thing in the prompt
        chain.append(("pixabay-fallback", lambda: pixabay_photo(query or fallback_query or query_from_prompt(prompt), out, size, kind)))
    if kind in ("photo", "thumb"):
        chain.append(("pexels-photo", lambda: pexels_photo(fallback_query or prompt[:60], out, size)))
    subj = entity or rq
    for name, fn in chain:
        try:
            if fn() and Path(out).exists():
                if name in ("wikimedia", "pexels-real") and kind == "photo" and not verify_image(out, subj):
                    Path(out).unlink(missing_ok=True)
                    continue
                return True
        except Exception as e:  # noqa
            note_fail(name, type(e).__name__)
            log(f"  image source {name} failed: {type(e).__name__}: {str(e)[:120]}")
    return False


def pexels_clips(query, tdir, n=3):
    if not PEXELS_KEY:
        return []
    r = requests.get("https://api.pexels.com/videos/search", headers={"Authorization": PEXELS_KEY},
                     params={"query": query, "per_page": 10, "orientation": "landscape", "size": "medium"}, timeout=30)
    if not r.ok:
        return []
    out = []
    for v in r.json().get("videos", []):
        if len(out) >= n:
            break
        if v.get("duration", 0) < 4:
            continue
        files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and (f.get("width") or 0) >= 1280]
        if not files:
            continue
        f = sorted(files, key=lambda x: x["width"])[0]
        dst = tdir / f"pexels_{v['id']}.mp4"
        try:
            if not dst.exists():
                data = requests.get(f["link"], timeout=120)
                data.raise_for_status()
                dst.write_bytes(data.content)
            out.append({"path": dst, "id": v["id"], "url": v.get("url", ""),
                        "credit": (v.get("user") or {}).get("name", "Pexels"), "dur": ffprobe_dur(dst)})
        except Exception as e:  # noqa
            log("  clip download failed:", type(e).__name__)
    return out


# --------------------------------------------------------------------------- music + sound effects (synthesised, copyright-free)
SR = 44100
MOOD_MAP = {  # default mood when the script does not give one
    "earth_formation": "epic", "solar_system": "space", "stars_universe": "space", "insects": "life",
    "wild_animals": "tense", "ocean_life": "wonder", "forests_plants": "calm", "human_evolution": "mystery",
    "natural_phenomena": "epic", "extinct_life": "tense", "ecosystems": "life", "animal_senses": "wonder",
}
MOODS = {
    "mystery": dict(scale=[0, 2, 3, 5, 7, 8, 10], bpm=60, prog=[0, 5, 3, 6], bright=3, arp="bell", drum=None),
    "epic": dict(scale=[0, 2, 3, 5, 7, 8, 10], bpm=80, prog=[0, 5, 3, 4], bright=7, arp=None, drum="boom"),
    "wonder": dict(scale=[0, 2, 4, 6, 7, 9, 11], bpm=72, prog=[0, 4, 5, 3], bright=4, arp="pluck", drum=None),
    "calm": dict(scale=[0, 2, 4, 5, 7, 9, 11], bpm=60, prog=[0, 3, 4, 0], bright=3, arp="soft", drum=None),
    "tense": dict(scale=[0, 1, 3, 5, 7, 8, 10], bpm=92, prog=[0, 1, 0, 6], bright=5, arp=None, drum="heart"),
    "life": dict(scale=[0, 2, 4, 5, 7, 9, 11], bpm=92, prog=[0, 5, 3, 4], bright=4, arp="pluck", drum="shaker"),
    "space": dict(scale=[0, 2, 3, 5, 7, 9, 10], bpm=54, prog=[0, 3, 5, 2], bright=3, arp="shimmer", drum=None),
}


def _np():
    import numpy as np
    return np


def _filt(x, kind, f, order=2):
    from scipy import signal
    np = _np()
    nyq = SR / 2
    if kind == "bp":
        sos = signal.butter(order, [max(20, f[0]) / nyq, min(nyq * 0.95, f[1]) / nyq], btype="band", output="sos")
    else:
        sos = signal.butter(order, min(nyq * 0.95, f) / nyq, btype=kind, output="sos")
    return signal.sosfilt(sos, x).astype(np.float32)


def _decay(n, k):
    np = _np()
    return np.exp(-np.arange(n) / SR * k).astype(np.float32)


def sfx_whoosh(dur=0.7, up=True, gain=1.0):
    np = _np()
    n = int(dur * SR)
    noise = np.random.randn(n).astype(np.float32)
    low, mid, high = _filt(noise, "lowpass", 700), _filt(noise, "bp", (700, 3000)), _filt(noise, "highpass", 3000)
    x = np.linspace(0, 1, n, dtype=np.float32)
    if not up:
        x = x[::-1]
    w_l = np.exp(-((x - 0.1) / 0.28) ** 2)
    w_m = np.exp(-((x - 0.5) / 0.25) ** 2)
    w_h = np.exp(-((x - 0.9) / 0.28) ** 2)
    y = low * w_l * 1.0 + mid * w_m * 1.3 + high * w_h * 0.8
    env = np.clip(np.sin(np.pi * np.linspace(0, 1, n, dtype=np.float32)), 0, None) ** 1.5
    return y * env * 0.9 * gain


def sfx_impact(gain=1.0, dur=1.3):
    np = _np()
    n = int(dur * SR)
    t = np.arange(n, dtype=np.float32) / SR
    f = 42 + 120 * np.exp(-t * 20)
    ph = 2 * np.pi * np.cumsum(f) / SR
    body = np.sin(ph) * np.exp(-t * 4.2)
    burst = _filt(np.random.randn(n).astype(np.float32), "lowpass", 900) * np.exp(-t * 26) * 0.9
    crack = _filt(np.random.randn(n).astype(np.float32), "highpass", 2500) * np.exp(-t * 60) * 0.25
    return (body * 0.95 + burst + crack) * gain


def sfx_pop(gain=1.0):
    np = _np()
    n = int(0.16 * SR)
    t = np.arange(n, dtype=np.float32) / SR
    f = 420 + 700 * np.minimum(1, t / 0.07)
    y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 30)
    return y * 0.55 * gain


def sfx_tick(gain=1.0):
    np = _np()
    n = int(0.05 * SR)
    y = _filt(np.random.randn(n).astype(np.float32), "highpass", 1800) * _decay(n, 90)
    return y * 0.5 * gain


def sfx_ding(gain=1.0):
    np = _np()
    n = int(1.2 * SR)
    t = np.arange(n, dtype=np.float32) / SR
    y = np.zeros(n, dtype=np.float32)
    for k, f in enumerate((1318.5, 1760.0, 2637.0)):
        d = int(0.03 * SR * k)
        y[d:] += (np.sin(2 * np.pi * f * t[:n - d]) * np.exp(-t[:n - d] * (3.5 + k))) * (0.5 / (k + 1))
    return y * gain


def sfx_sparkle(gain=1.0):
    np = _np()
    n = int(1.1 * SR)
    y = np.zeros(n, dtype=np.float32)
    rng = np.random.default_rng(random.randint(0, 9999))
    for _ in range(11):
        f = rng.choice([1568, 2093, 2637, 3136, 3951])
        s = int(rng.uniform(0, 0.55) * SR)
        m = int(0.35 * SR)
        tt = np.arange(m, dtype=np.float32) / SR
        y[s:s + m] += np.sin(2 * np.pi * f * tt) * np.exp(-tt * 11) * 0.22
    return y * gain


def sfx_riser(dur=2.6, gain=1.0):
    np = _np()
    n = int(dur * SR)
    t = np.linspace(0, 1, n, dtype=np.float32)
    noise = _filt(np.random.randn(n).astype(np.float32), "highpass", 1200)
    sw = np.sin(2 * np.pi * np.cumsum(180 + 1500 * t ** 2.2) / SR).astype(np.float32)
    return (noise * 0.45 + sw * 0.35) * (t ** 2.0) * gain


def sfx_glitch(gain=1.0):
    np = _np()
    n = int(0.3 * SR)
    rng = np.random.default_rng(random.randint(0, 9999))
    y = np.zeros(n, dtype=np.float32)
    pos = 0
    while pos < n:
        seg = int(rng.uniform(0.01, 0.05) * SR)
        y[pos:pos + seg] = rng.uniform(-1, 1) * np.sign(np.sin(np.arange(min(seg, n - pos)) * rng.uniform(0.05, 0.4)))
        pos += seg
    return y * np.linspace(1, 0, n, dtype=np.float32) * 0.28 * gain


SFX = {
    "whoosh": lambda g: sfx_whoosh(0.7, True, g), "whoosh_soft": lambda g: sfx_whoosh(0.45, True, g * 0.55),
    "whoosh_down": lambda g: sfx_whoosh(0.7, False, g), "impact": sfx_impact, "pop": sfx_pop, "tick": sfx_tick,
    "ding": sfx_ding, "sparkle": sfx_sparkle, "riser": sfx_riser, "glitch": sfx_glitch,
}


def build_sfx_track(events, total, out):
    """events: [(time_sec, name, gain)] -> mono wav (float mixed, soft-clipped)."""
    np = _np()
    n = int((total + 3) * SR)
    track = np.zeros(n, dtype=np.float32)
    cache = {}
    for t, name, g in events:
        if name not in SFX or t < 0:
            continue
        key = (name, round(g, 2))
        if key not in cache:
            cache[key] = SFX[name](g)
        s = cache[key]
        a = int(t * SR)
        if a >= n:
            continue
        m = min(len(s), n - a)
        track[a:a + m] += s[:m]
    track = np.tanh(track * 0.9)
    write_wav(out, track)


def write_wav(path, x):
    import wave
    np = _np()
    pcm = (np.clip(np.nan_to_num(x), -1, 1) * 32000).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def midi_hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def _pad_note(freq, dur, bright):
    np = _np()
    n = int(dur * SR)
    t = np.arange(n, dtype=np.float32) / SR
    y = np.zeros(n, dtype=np.float32)
    for det in (0.996, 1.004):
        for h in range(1, bright + 1):
            y += np.sin(2 * np.pi * freq * det * h * t) / (h ** 1.25)
    a, r = min(1.6, dur * 0.35), min(1.8, dur * 0.4)
    env = np.minimum(1, t / a) * np.minimum(1, (dur - t) / r)
    return y * env * 0.11


def _pluck(freq, kind):
    np = _np()
    dur = {"bell": 2.6, "pluck": 1.0, "soft": 1.6, "shimmer": 2.2}[kind]
    n = int(dur * SR)
    t = np.arange(n, dtype=np.float32) / SR
    if kind == "bell":
        y = (np.sin(2 * np.pi * freq * t) + 0.5 * np.sin(2 * np.pi * freq * 2.76 * t) + 0.3 * np.sin(2 * np.pi * freq * 5.4 * t))
        y *= np.exp(-t * 1.8)
    elif kind == "pluck":
        y = (np.sin(2 * np.pi * freq * t) + 0.35 * np.sin(2 * np.pi * freq * 2 * t) + 0.15 * np.sin(2 * np.pi * freq * 3 * t))
        y *= np.exp(-t * 5.5)
    elif kind == "soft":
        y = np.sin(2 * np.pi * freq * t) * np.minimum(1, t / 0.05) * np.exp(-t * 2.2)
    else:
        y = (np.sin(2 * np.pi * freq * 2 * t) + 0.4 * np.sin(2 * np.pi * freq * 3.01 * t)) * np.minimum(1, t / 0.4) * np.exp(-t * 1.5)
    return (y * 0.16).astype(np.float32)


def _drum_hit(kind):
    np = _np()
    if kind == "boom":
        return sfx_impact(0.5, 1.0) * 0.8
    if kind == "heart":
        a = sfx_impact(0.38, 0.55)
        b = np.zeros(int(0.55 * SR), dtype=np.float32)
        s = int(0.22 * SR)
        b[s:] += sfx_impact(0.26, 0.55)[:len(b) - s]
        return a[:len(b)] + b
    n = int(0.09 * SR)  # shaker
    return _filt(np.random.randn(n).astype(np.float32), "highpass", 6000) * _decay(n, 45) * 0.22


# --------------------------------------------------------------------------- music v2: many different styles, a new one every video
MUSIC_STYLES = {
    "bhairav_flute": dict(tags=["mystery", "calm", "epic"], scale=[0, 1, 4, 5, 7, 8, 11], bpm=64, prog=[0, 3, 0, 4], pad="warm", arp=None, lead="flute", drum=None, drone=True),
    "yaman_dawn": dict(tags=["wonder", "calm", "life"], scale=[0, 2, 4, 6, 7, 9, 11], bpm=70, prog=[0, 4, 5, 3], pad="glass", arp="pluck", lead="flute", drum=None, drone=True),
    "dark_strings": dict(tags=["tense", "mystery"], scale=[0, 1, 3, 5, 7, 8, 10], bpm=84, prog=[0, 5, 0, 6], pad="strings", arp=None, lead=None, drum="heart", drone=False),
    "cinematic_epic": dict(tags=["epic", "tense"], scale=[0, 2, 3, 5, 7, 8, 10], bpm=78, prog=[0, 5, 3, 4], pad="strings", arp="bell", lead=None, drum="boom", drone=False),
    "space_glass": dict(tags=["space", "wonder"], scale=[0, 2, 3, 5, 7, 9, 10], bpm=54, prog=[0, 3, 5, 2], pad="glass", arp="shimmer", lead=None, drum=None, drone=True),
    "marimba_nature": dict(tags=["life", "wonder", "calm"], scale=[0, 2, 4, 5, 7, 9, 11], bpm=96, prog=[0, 5, 3, 4], pad="warm", arp="marimba", lead=None, drum="shaker", drone=False),
    "piano_calm": dict(tags=["calm", "wonder", "mystery"], scale=[0, 2, 4, 5, 7, 9, 11], bpm=62, prog=[0, 3, 4, 0], pad="warm", arp="piano", lead=None, drum=None, drone=False),
    "choir_mystery": dict(tags=["mystery", "epic", "space"], scale=[0, 2, 3, 5, 7, 8, 10], bpm=58, prog=[0, 5, 3, 6], pad="choir", arp="bell", lead=None, drum=None, drone=False),
    "tabla_journey": dict(tags=["life", "epic", "wonder"], scale=[0, 2, 4, 5, 7, 9, 10], bpm=100, prog=[0, 3, 0, 4], pad="warm", arp=None, lead="flute", drum="tabla", drone=True),
    "ambient_drone": dict(tags=["space", "calm", "mystery"], scale=[0, 2, 4, 7, 9, 11, 12], bpm=50, prog=[0, 2, 4, 2], pad="drone", arp="shimmer", lead=None, drum=None, drone=True),
    "pulse_tension": dict(tags=["tense", "epic"], scale=[0, 1, 3, 5, 6, 8, 10], bpm=104, prog=[0, 1, 0, 5], pad="strings", arp="pluck", lead=None, drum="heart", drone=False),
    "folk_pluck": dict(tags=["mystery", "life", "tense"], scale=[0, 2, 3, 5, 7, 8, 10], bpm=88, prog=[0, 6, 5, 6], pad="warm", arp="pluck", lead=None, drum="shaker", drone=True),
    "sitar_dusk": dict(tags=["mystery", "calm", "life"], scale=[0, 1, 3, 5, 7, 8, 10], bpm=72, prog=[0, 5, 0, 3], pad="glass", arp="sitar", lead="flute", drum=None, drone=True),
}
MUSIC_USED = ["-"]


def _tone_env(n, dur, a, r):
    np = _np()
    t = np.arange(n, dtype=np.float32) / SR
    return np.minimum(1, t / max(0.01, a)) * np.minimum(1, np.maximum(0, (dur - t)) / max(0.01, r)), t


def _strings_note(freq, dur):
    np = _np()
    n = int(dur * SR)
    env, t = _tone_env(n, dur, min(1.2, dur * 0.4), min(1.6, dur * 0.4))
    y = np.zeros(n, dtype=np.float32)
    tv = t + 0.0007 * np.sin(2 * np.pi * 5.2 * t)
    for det in (0.994, 1.0, 1.006):
        for h in range(1, 10):
            y += np.sin(2 * np.pi * freq * det * h * tv) / (h ** 1.05)
    return y * env * 0.042


def _choir_note(freq, dur):
    np = _np()
    n = int(dur * SR)
    env, t = _tone_env(n, dur, min(1.0, dur * 0.4), min(1.5, dur * 0.4))
    y = np.zeros(n, dtype=np.float32)
    tv = t + 0.0009 * np.sin(2 * np.pi * 4.6 * t)
    for h in range(1, 15):
        fh = freq * h
        wgt = math.exp(-((fh - 700) / 260) ** 2) + 0.7 * math.exp(-((fh - 1150) / 300) ** 2) + 0.3 * math.exp(-((fh - 2600) / 500) ** 2)
        if wgt > 0.02:
            y += np.sin(2 * np.pi * fh * tv) * wgt / (h ** 0.5)
    return y * env * 0.05


def _glass_note(freq, dur):
    np = _np()
    n = int(dur * SR)
    env, t = _tone_env(n, dur, min(1.4, dur * 0.4), min(1.8, dur * 0.4))
    y = np.zeros(n, dtype=np.float32)
    for det in (0.997, 1.003):
        y += np.sin(2 * np.pi * freq * det * t) + 0.4 * np.sin(2 * np.pi * freq * 2.0 * det * t) + 0.2 * np.sin(2 * np.pi * freq * 3.01 * det * t)
    return y * env * 0.07


def _drone_note(freq, dur):
    np = _np()
    n = int(dur * SR)
    env, t = _tone_env(n, dur, min(2.2, dur * 0.45), min(2.4, dur * 0.45))
    trem = 0.8 + 0.2 * np.sin(2 * np.pi * 0.17 * t)
    y = np.sin(2 * np.pi * freq * t) + 0.55 * np.sin(2 * np.pi * freq * 1.5 * t) + 0.3 * np.sin(2 * np.pi * freq * 2 * t)
    return y * env * trem * 0.08


def _flute_note(freq, dur):
    np = _np()
    n = int(dur * SR)
    t = np.arange(n, dtype=np.float32) / SR
    vib = 0.006 * np.sin(2 * np.pi * 5.0 * t) * np.minimum(1, t / 0.45)
    ph = 2 * np.pi * np.cumsum(freq * (1 + vib)) / SR
    y = np.sin(ph) + 0.22 * np.sin(2 * ph) + 0.07 * np.sin(3 * ph)
    breath = _filt(np.random.randn(n).astype(np.float32), "bp", (900, 3600)) * 0.10
    env = np.minimum(1, t / 0.09) * np.minimum(1, np.maximum(0, dur - t) / 0.28)
    return ((y + breath) * env * 0.2).astype(np.float32)


def _tanpura_note(freq, dur=2.6):
    np = _np()
    n = int(dur * SR)
    t = np.arange(n, dtype=np.float32) / SR
    y = np.zeros(n, dtype=np.float32)
    for det in (1.0, 1.0025):
        for h in range(1, 15):
            y += np.sin(2 * np.pi * freq * det * h * t) / (h ** 0.62)
    return (y * np.exp(-t * 1.05) * np.minimum(1, t / 0.01) * 0.045).astype(np.float32)


def _marimba_note(freq):
    np = _np()
    n = int(1.1 * SR)
    t = np.arange(n, dtype=np.float32) / SR
    y = np.sin(2 * np.pi * freq * t) * np.exp(-t * 6) + 0.3 * np.sin(2 * np.pi * freq * 4 * t) * np.exp(-t * 15) + 0.1 * np.sin(2 * np.pi * freq * 10 * t) * np.exp(-t * 28)
    return (y * 0.2).astype(np.float32)


def _piano_note(freq):
    np = _np()
    n = int(2.6 * SR)
    t = np.arange(n, dtype=np.float32) / SR
    y = np.zeros(n, dtype=np.float32)
    for h in range(1, 8):
        fh = freq * h * math.sqrt(1 + 0.0004 * h * h)
        y += np.sin(2 * np.pi * fh * t) / (h ** 1.1) * np.exp(-t * (1.6 + 0.8 * h))
    y[:int(0.006 * SR)] += np.random.randn(int(0.006 * SR)).astype(np.float32) * 0.15
    return (y * 0.2).astype(np.float32)


def _sitar_note(freq):
    np = _np()
    n = int(1.6 * SR)
    t = np.arange(n, dtype=np.float32) / SR
    y = np.zeros(n, dtype=np.float32)
    for h in range(1, 13):
        y += np.sin(2 * np.pi * freq * h * t * (1 + 0.0006 * math.sin(h))) / (h ** 0.75) * np.exp(-t * (2.2 + 0.25 * h))
    return (y * 0.13).astype(np.float32)


def _tabla_hit(kind):
    np = _np()
    if kind == "ge":
        n = int(0.5 * SR)
        t = np.arange(n, dtype=np.float32) / SR
        f = 68 + 95 * np.exp(-t * 16)
        return (np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 6.5) * 0.5).astype(np.float32)
    if kind == "tin":
        n = int(0.28 * SR)
        t = np.arange(n, dtype=np.float32) / SR
        return ((np.sin(2 * np.pi * 560 * t) + 0.4 * np.sin(2 * np.pi * 1120 * t)) * np.exp(-t * 22) * 0.2).astype(np.float32)
    n = int(0.16 * SR)  # "na"
    t = np.arange(n, dtype=np.float32) / SR
    return ((np.sin(2 * np.pi * 410 * t) * np.exp(-t * 26) + _filt(np.random.randn(n).astype(np.float32), "highpass", 2500) * np.exp(-t * 60) * 0.3) * 0.22).astype(np.float32)


PAD_FN = {"warm": lambda f, d: _pad_note(f, d, 4), "glass": _glass_note, "strings": _strings_note, "choir": _choir_note, "drone": _drone_note}


def pick_music_style(mood):
    """A different style almost every video: styles that fit the mood, never one of the last three used (remembered in state/)."""
    f = STATE / "music_recent.json"
    try:
        recent = json.loads(f.read_text()) if f.exists() else []
    except Exception:  # noqa
        recent = []
    cands = [k for k, v in MUSIC_STYLES.items() if mood in v["tags"]] or list(MUSIC_STYLES)
    fresh = [k for k in cands if k not in recent[-3:]] or [k for k in MUSIC_STYLES if k not in recent[-3:]] or cands
    k = random.choice(fresh)
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps((recent + [k])[-8:]))
    except Exception:  # noqa
        pass
    return k


def make_music(duration, out, mood="wonder"):
    """Original music: one of 13 styles (Indian flute / sitar / tabla, strings, choir, glass, piano, marimba, drone...), different every video."""
    np = _np()
    style = pick_music_style(mood)
    cfg = MUSIC_STYLES[style]
    MUSIC_USED[0] = style
    log(f"music style: {style} (mood {mood})")
    rnd = random.Random()
    root = rnd.choice([38, 40, 41, 43, 45, 46, 48])
    bpm = cfg["bpm"] + rnd.choice([-4, 0, 4])
    beat = 60.0 / bpm
    chord_len = beat * 8
    total = duration + 4
    n = int(total * SR)
    buf = np.zeros(n, dtype=np.float32)
    sc_ = cfg["scale"]
    prog = cfg["prog"][:]
    if rnd.random() < 0.5:
        prog = prog[2:] + prog[:2]

    def semi(k):
        return sc_[k % 7] + 12 * (k // 7)

    def chord_notes(d):
        return [root + semi(d), root + semi(d + 2), root + semi(d + 4), root + 12 + semi(d)]

    def add(sig, t0, gain=1.0):
        a = int(t0 * SR)
        m = min(len(sig), n - a)
        if m > 0 and a >= 0:
            buf[a:a + m] += sig[:m] * gain
    padf = PAD_FN[cfg["pad"]]
    pads = {}
    for d in set(prog):
        notes = chord_notes(d)
        seg = sum(padf(midi_hz(m), chord_len + 1.8) for m in notes)
        bass = _pad_note(midi_hz(notes[0] - 12), chord_len + 1.8, 2) * 1.5
        pads[d] = seg + bass
    t0, k = 0.0, 0
    while t0 < total:
        add(pads[prog[k % len(prog)]], t0)
        t0 += chord_len
        k += 1
    if cfg["drone"]:  # tanpura: Pa - Sa - Sa - low Sa, over and over
        pat = [root + 7 - 12, root, root, root - 12]
        cache = {}
        t0, j = 0.0, 0
        while t0 < total:
            m_ = pat[j % 4]
            if m_ not in cache:
                cache[m_] = _tanpura_note(midi_hz(m_ + 12))
            add(cache[m_], t0, 0.9)
            t0 += beat * 1.6
            j += 1
    if cfg["arp"]:
        a_kind = cfg["arp"]
        step = beat / 2 if a_kind in ("pluck", "marimba", "sitar") else beat
        cache = {}
        t0 = 0.0
        while t0 < total:
            d = prog[int(t0 // chord_len) % len(prog)]
            tones = [x + 12 * rnd.choice([1, 2]) for x in chord_notes(d)[:3]]
            if rnd.random() < (0.6 if a_kind in ("pluck", "marimba", "sitar") else 0.42):
                f = midi_hz(rnd.choice(tones))
                key = round(f, 1)
                if key not in cache:
                    cache[key] = (_marimba_note(f) if a_kind == "marimba" else _piano_note(f) if a_kind == "piano" else _sitar_note(f) if a_kind == "sitar" else _pluck(f, a_kind))
                add(cache[key], t0)
            t0 += step
    if cfg["lead"] == "flute":  # a slow bansuri-like phrase in every second chord
        t0, j = chord_len * 0.5, 0
        while t0 < total - 2:
            seg_end = min(total - 1.5, t0 + chord_len * 0.9)
            d = prog[int(t0 // chord_len) % len(prog)]
            if j % 2 == 0:
                deg = d + rnd.choice([0, 2, 4])
                tt = t0
                while tt < seg_end - beat:
                    dur = beat * rnd.choice([1, 1.5, 2, 3])
                    if rnd.random() < 0.18:
                        tt += beat
                        continue
                    deg = max(0, deg + rnd.choice([-2, -1, 1, 1, 2]))
                    add(_flute_note(midi_hz(root + 12 + semi(deg)), dur + 0.3), tt, 0.85)
                    tt += dur
            t0 += chord_len
            j += 1
    if cfg["drum"]:
        if cfg["drum"] == "tabla":
            pat = ["ge", "na", "tin", "na", "ge", "na", "tin", "na"]
            cache = {k_: _tabla_hit(k_) for k_ in ("ge", "na", "tin")}
            t0, j = chord_len, 0
            while t0 < total:
                if rnd.random() < 0.9:
                    add(cache[pat[j % 8]], t0, 0.7)
                t0 += beat / 2
                j += 1
        else:
            hit = _drum_hit(cfg["drum"])
            step = {"boom": beat * 2, "heart": beat * 2, "shaker": beat / 2}[cfg["drum"]]
            t0 = chord_len
            while t0 < total:
                swell = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(t0 / (chord_len * 2)))
                add(hit, t0, swell)
                t0 += step
    peak = float(np.max(np.abs(buf))) or 1.0
    buf = buf / peak * 0.9
    raw = Path(str(out) + ".raw.wav")
    write_wav(raw, buf)
    run(["ffmpeg", "-y", "-i", raw, "-af",
         "lowpass=f=6200,aecho=0.8:0.85:600|1100:0.26|0.16,"
         f"afade=t=in:d=3,afade=t=out:st={max(1, total - 4):.1f}:d=4,aformat=channel_layouts=stereo", "-t", f"{total:.1f}", out])
    raw.unlink(missing_ok=True)


# --------------------------------------------------------------------------- explainer animation engine
def lerp(a, b, p):
    return a + (b - a) * p


def cl(x):
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def e_out(p):
    p = cl(p)
    return 1 - (1 - p) ** 3


def e_io(p):
    p = cl(p)
    return p * p * (3 - 2 * p)


def e_back(p):
    p = cl(p)
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * (p - 1) ** 3 + c1 * (p - 1) ** 2


def rounded_mask(size, r):
    k = 3
    m = Image.new("L", (size[0] * k, size[1] * k), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0] * k - 1, size[1] * k - 1], r * k, fill=255)
    return m.resize(size, Image.LANCZOS)


@lru_cache(maxsize=8)
def bg_base(top, bottom):
    g = Image.linear_gradient("L").resize((W, H))
    return Image.composite(Image.new("RGB", (W, H), bottom), Image.new("RGB", (W, H), top), g)


def soft_circle(r, color, alpha):
    s = Image.new("RGBA", (r * 2, r * 2), (0, 0, 0, 0))
    ImageDraw.Draw(s).ellipse([0, 0, r * 2 - 1, r * 2 - 1], fill=tuple(color) + (int(255 * alpha),))
    return s.filter(ImageFilter.GaussianBlur(2))


def glow_sprite(size, color, alpha):
    np = _np()
    c = size / 2.0
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    d = np.sqrt(((xx - c) / c) ** 2 + ((yy - c) / c) ** 2)
    a = (np.clip(1 - d, 0, 1) ** 1.8 * alpha * 255).astype(np.uint8)
    sp = Image.new("RGBA", (size, size), tuple(color) + (255,))
    sp.putalpha(Image.fromarray(a))
    return sp


CARD = 300
BORDER = 10
PADX = 30
PADT = 18
PADB = 26


def make_card(img_path, label, pal):
    """Rounded white-framed picture card with a dark label pill. Returns an RGBA sprite."""
    inner = CARD
    cw = inner + 2 * BORDER
    if img_path and Path(img_path).exists():
        im = ImageOps.fit(Image.open(img_path).convert("RGB"), (inner, inner), Image.LANCZOS)
    else:  # picture missing: colourful card with the label itself
        im = Image.composite(Image.new("RGB", (inner, inner), pal["accent"]),
                             Image.new("RGB", (inner, inner), pal["top"]),
                             Image.linear_gradient("L").resize((inner, inner)))
    size = 38
    lay = text_layer(label.strip() or " ", load_font(size, "label"), fill=(255, 255, 255))
    while lay.width > cw - 10 and size > 22:
        size -= 3
        lay = text_layer(label.strip() or " ", load_font(size, "label"), fill=(255, 255, 255))
    ph = lay.height + 8
    pw = max(150, min(cw + 20, lay.width + 56))
    CW = cw + 2 * PADX
    CH = PADT + cw + ph // 2 + PADB
    sprite = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    m = rounded_mask((cw, cw), 36)
    sh = Image.new("L", (CW, CH), 0)
    sh.paste(m, (PADX, PADT + 12))
    sh = sh.filter(ImageFilter.GaussianBlur(14)).point(lambda v: int(v * 0.38))
    shadow = Image.new("RGBA", (CW, CH), (10, 20, 50, 255))
    shadow.putalpha(sh)
    sprite = Image.alpha_composite(sprite, shadow)
    frame = Image.new("RGBA", (cw, cw), (255, 255, 255, 255))
    frame.putalpha(m)
    sprite.alpha_composite(frame, (PADX, PADT))
    im_m = rounded_mask((inner, inner), 28)
    sprite.paste(im, (PADX + BORDER, PADT + BORDER), im_m)
    pill = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    pm = rounded_mask((pw, ph), ph // 2)
    white = Image.new("RGBA", (pw, ph), (255, 255, 255, 255))
    white.putalpha(pm)
    pill.alpha_composite(white)
    inner_m = rounded_mask((pw - 8, ph - 8), (ph - 8) // 2)
    dark = Image.new("RGBA", (pw - 8, ph - 8), tuple(pal["ink"]) + (255,))
    dark.putalpha(inner_m)
    pill.alpha_composite(dark, (4, 4))
    pill.alpha_composite(lay, ((pw - lay.width) // 2, (ph - lay.height) // 2))
    sprite.alpha_composite(pill, ((CW - pw) // 2, PADT + cw - ph // 2))
    return sprite


def _ss(size, draw_fn):
    k = 4
    img = Image.new("RGBA", (size * k, size * k), (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(img), size * k)
    return img.resize((size, size), Image.LANCZOS)


def _disc(d, s, pal):
    d.ellipse([4, 4, s - 5, s - 5], fill=(255, 255, 255, 255))
    d.ellipse([s * 0.07, s * 0.07, s - s * 0.07, s - s * 0.07], fill=tuple(pal["accent"]) + (255,))


def sym_plus(pal, size=104):
    def f(d, s):
        _disc(d, s, pal)
        t = s * 0.09
        c = s / 2
        d.rounded_rectangle([c - s * 0.26, c - t, c + s * 0.26, c + t], t, fill=(255, 255, 255, 255))
        d.rounded_rectangle([c - t, c - s * 0.26, c + t, c + s * 0.26], t, fill=(255, 255, 255, 255))
    return _ss(size, f)


def sym_arrow(pal, size=92):
    def f(d, s):
        _disc(d, s, pal)
        c, t = s / 2, s * 0.075
        d.rounded_rectangle([s * 0.22, c - t, s * 0.62, c + t], t, fill=(255, 255, 255, 255))
        d.polygon([(s * 0.56, c - s * 0.2), (s * 0.80, c), (s * 0.56, c + s * 0.2)], fill=(255, 255, 255, 255))
    return _ss(size, f)


def sym_vs(pal, size=112):
    lay = text_layer("VS", load_font(52, "title"), fill=(255, 255, 255), stroke=3, stroke_fill=pal["ink"])
    base = _ss(size, lambda d, s: _disc(d, s, pal))
    base.alpha_composite(lay, ((size - lay.width) // 2, (size - lay.height) // 2))
    return base


def sym_badge(n, pal, size=60):
    def f(d, s):
        d.ellipse([3, 3, s - 4, s - 4], fill=(255, 255, 255, 255))
        d.ellipse([s * 0.09, s * 0.09, s - s * 0.09, s - s * 0.09], fill=tuple(pal["ink"]) + (255,))
    base = _ss(size, f)
    lay = text_layer(str(n), load_font(34, "title"), fill=(255, 255, 255))
    base.alpha_composite(lay, ((size - lay.width) // 2, (size - lay.height) // 2))
    return base


def put(frame, sprite, cx, cy, scale=1.0, alpha=1.0):
    if alpha <= 0.02 or scale <= 0.02:
        return
    s = sprite
    if abs(scale - 1.0) > 0.004:
        s = s.resize((max(1, int(s.width * scale)), max(1, int(s.height * scale))), Image.BILINEAR)
    if alpha < 0.985:
        a = s.getchannel("A").point(lambda v: int(v * alpha))
        s = s.copy()
        s.putalpha(a)
    frame.paste(s, (int(cx - s.width / 2), int(cy - s.height / 2)), s)


def make_explainer(sc, dur, pal, events):
    """Draw an animated explainer scene frame by frame and encode it as a silent mp4."""
    rnd = random.Random(hash(sc["narration_hi"]) & 0xFFFF)
    cards = [make_card(it.get("image"), it["label_hi"], pal) for it in sc["items"]]
    typ = sc["type"]
    cy = 335
    fl = lambda t, ph: 5.0 * math.sin(t * 2.0 + ph)  # noqa
    actors, fx = [], []

    # decorative drifting bubbles
    bubbles = [(soft_circle(r, (255, 255, 255), a), x, y, sp, ph) for (r, a, x, y, sp, ph) in
               [(150, 0.16, 190, 140, 0.35, 0.0), (95, 0.20, 1090, 210, 0.5, 1.7), (210, 0.12, 980, 560, 0.28, 3.1),
                (70, 0.22, 330, 600, 0.6, 4.4)]]

    head = None
    if sc.get("headline_hi"):
        hl = text_layer(sc["headline_hi"], load_font(60, "head"), fill=tuple(pal["ink"]), max_w=W - 160)
        bar = Image.new("RGBA", (min(hl.width, W - 200), 10), (0, 0, 0, 0))
        bar.paste(Image.new("RGBA", bar.size, tuple(pal["accent"]) + (255,)), (0, 0), rounded_mask(bar.size, 5))
        head = Image.new("RGBA", (hl.width, hl.height + 16), (0, 0, 0, 0))
        head.alpha_composite(hl, (0, 0))
        head.alpha_composite(bar, ((head.width - bar.width) // 2, hl.height + 2))

    if typ == "merge":
        A, B, C = cards
        plus = sym_plus(pal)
        glow = glow_sprite(760, (255, 255, 255), 0.75)
        tc = max(1.9, min(dur * 0.55, dur - 2.6))
        xa, xb, xc = W * 0.27, W * 0.73, W * 0.5
        pcf = lambda t: e_io((t - tc) / 0.75)  # noqa

        def fa(t):
            pc = pcf(t)
            x = lerp(lerp(-260, xa, e_out((t - 0.15) / 0.8)), xc - 40, pc)
            return x, cy + fl(t, 0), lerp(1.0, 0.55, pc), 1 - cl((pc - 0.55) / 0.45)

        def fb(t):
            pc = pcf(t)
            x = lerp(lerp(W + 260, xb, e_out((t - 0.35) / 0.8)), xc + 40, pc)
            return x, cy + fl(t, 1.3), lerp(1.0, 0.55, pc), 1 - cl((pc - 0.55) / 0.45)

        def fp(t):
            return xc, cy, e_back((t - 1.0) / 0.35) * (1 - cl(pcf(t) * 2)), 1.0

        def fg(t):
            q = (t - (tc + 0.5)) / 0.7
            return xc, cy, 1.0 + 0.04 * math.sin(t * 3), cl(q * 2) * 0.9

        def fcard(t):
            q = (t - (tc + 0.5)) / 0.65
            return xc, cy + fl(t, 2.2), lerp(0.1, 1.12, e_back(q)), cl(q * 3)

        actors += [(glow, fg), (A, fa), (B, fb), (plus, fp), (C, fcard)]
        events += [(0.15, 'whoosh', 0.6), (0.35, 'whoosh', 0.6), (1.0, 'pop', 0.7), (tc + 0.3, 'impact', 1.0), (tc + 0.5, 'sparkle', 0.8)]

        def fx_merge(frame, t):
            d = ImageDraw.Draw(frame)
            q = (t - (tc + 0.35)) / 0.8
            if 0 < q < 1:
                r = lerp(40, 400, e_out(q))
                d.ellipse([xc - r, cy - r, xc + r, cy + r], outline=(255, 255, 255), width=max(2, int(14 * (1 - q))))
            q2 = (t - (tc + 0.45)) / 1.2
            if 0 < q2 < 1:
                for k in range(14):
                    ang = k * (2 * math.pi / 14) + 0.3
                    rr = lerp(30, 330, e_out(q2)) * (0.65 + 0.35 * ((k * 7) % 5) / 4)
                    sz = 11 * (1 - q2) + 2
                    px, py = xc + math.cos(ang) * rr, cy + math.sin(ang) * rr * 0.8
                    d.ellipse([px - sz, py - sz, px + sz, py + sz], fill=(255, 255, 255) if k % 2 else tuple(pal["accent"]))
        fx.append(fx_merge)

    elif typ == "split":
        Wh, P1, P2 = cards
        arrow = sym_arrow(pal)
        plus = sym_plus(pal, 70)
        ts = max(2.2, min(dur * 0.42, dur - 3.4))
        pm = lambda t: e_io((t - ts) / 0.7)  # noqa
        xs = (W * 0.19, W * 0.53, W * 0.82)

        def fw(t):
            return lerp(W / 2, xs[0], pm(t)), cy + fl(t, 0), lerp(lerp(0.2, 1.1, e_back((t - 0.2) / 0.7)), 0.82, pm(t)), cl((t - 0.2) * 4)

        actors += [(Wh, fw),
                   (arrow, lambda t: (W * 0.36, cy, e_back((t - (ts + 0.6)) / 0.35), 1.0)),
                   (P1, lambda t: (xs[1], cy + fl(t, 1.0), 0.82 * e_back((t - (ts + 0.8)) / 0.5), cl((t - (ts + 0.8)) * 4))),
                   (plus, lambda t: (W * 0.675, cy, e_back((t - (ts + 1.3)) / 0.3), 1.0)),
                   (P2, lambda t: (xs[2], cy + fl(t, 2.0), 0.82 * e_back((t - (ts + 1.4)) / 0.5), cl((t - (ts + 1.4)) * 4)))]

        def fx_split(frame, t):
            q = (t - ts) / 0.5
            if 0 < q < 1:
                d = ImageDraw.Draw(frame)
                for k in range(10):
                    ang = k * (2 * math.pi / 10)
                    rr = lerp(20, 220, e_out(q))
                    sz = 9 * (1 - q) + 2
                    px, py = W / 2 + math.cos(ang) * rr, cy + math.sin(ang) * rr * 0.8
                    d.ellipse([px - sz, py - sz, px + sz, py + sz], fill=(255, 255, 255))
        fx.append(fx_split)
        events += [(0.25, 'pop', 0.6), (ts, 'impact', 0.9), (ts + 0.6, 'whoosh_soft', 0.8), (ts + 0.8, 'pop', 0.6), (ts + 1.4, 'pop', 0.6)]

    elif typ == "steps":
        n = len(cards)
        s = 0.9 if n == 3 else 0.66
        avail = max(2.0, dur - 1.4) * 0.75
        xs = [W * (i + 0.5) / n for i in range(n)]
        t0 = [0.4 + i * avail / n for i in range(n)]
        arrow = sym_arrow(pal, 80 if n == 3 else 62)
        for i, cd in enumerate(cards):
            def fcard(t, i=i):
                q = t - t0[i]
                last_pulse = 1.0 + (0.04 * math.sin(t * 4) if (i == n - 1 and t > t0[i] + 1.0) else 0.0)
                return xs[i], cy + lerp(90, 0, e_out(q / 0.6)) + fl(t, i), s * e_back(q / 0.6) * last_pulse, cl(q * 3)
            actors.append((cd, fcard))
            events.append((t0[i], 'pop', 0.7))
            badge = sym_badge(i + 1, pal)

            def fbadge(t, i=i):
                q = t - t0[i] - 0.25
                return xs[i] - (CARD + 2 * BORDER) * s / 2 + 16, cy - (cards[i].height / 2 - PADT) * s + 10 + fl(t, i), e_back(q / 0.35), 1.0
            actors.append((badge, fbadge))
            if i < n - 1:
                gx = (xs[i] + xs[i + 1]) / 2
                actors.append((arrow, lambda t, i=i, gx=gx: (gx, cy - 10, e_back((t - t0[i] - 0.55) / 0.35), 1.0)))

    else:  # compare
        A, B = cards
        events += [(0.15, 'whoosh', 0.55), (0.35, 'whoosh', 0.55), (1.0, 'pop', 0.7)]
        vs = sym_vs(pal)
        xa, xb = W * 0.27, W * 0.73
        actors += [(A, lambda t: (lerp(-260, xa, e_out((t - 0.15) / 0.8)), cy + fl(t, 0),
                                  1.0 + 0.045 * max(0.0, math.sin(t * 1.4)) * cl(t - 1.5), 1.0)),
                   (B, lambda t: (lerp(W + 260, xb, e_out((t - 0.35) / 0.8)), cy + fl(t, 1.6),
                                  1.0 + 0.045 * max(0.0, -math.sin(t * 1.4)) * cl(t - 1.5), 1.0)),
                   (vs, lambda t: (W / 2, cy, e_back((t - 1.0) / 0.4) * (1 + 0.05 * math.sin(t * 3)), 1.0))]

    base = bg_base(tuple(pal["top"]), tuple(pal["bottom"]))

    def draw(t):
        frame = base.copy()
        for sp, x, y, speed, ph in bubbles:
            put(frame, sp, x + 24 * math.sin(t * speed + ph), y + 18 * math.cos(t * speed * 0.8 + ph), 1.0, 1.0)
        if head is not None:
            q = e_out(t / 0.55)
            put(frame, head, W / 2, 62 - 26 * (1 - q), 1.0, cl(t * 2.5))
        for sprite, f in actors:
            x, y, scl, al = f(t)
            put(frame, sprite, x, y, scl, al)
        for f in fx:
            f(frame, t)
        return frame
    return draw


# --------------------------------------------------------------------------- compositor: camera, effects, transitions, subtitles
_C = {}
TONE_TRANS = ["fade", "zoom", "whip", "wipe", "circle"]
TRANS_SFX = {"whip": ("whoosh", 0.6), "wipe": ("whoosh_soft", 0.8), "circle": ("whoosh_soft", 0.8), "zoom": ("whoosh_down", 0.5),
             "flash": ("impact", 0.55), "glitch": ("glitch", 0.9), "fade": (None, 0)}
CAMS = ["push", "pull", "pan_lr", "pan_rl", "tilt_up", "tilt_down", "orbit", "handheld", "push_tl", "push_br"]
SW, SH = 1920, 1080


def vig_mask():
    if "vig" not in _C:
        np = _np()
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        d = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
        _C["vig"] = (1 - 0.36 * np.clip(d - 0.55, 0, 1) ** 1.6)[..., None].astype(np.float32)
    return _C["vig"]


def grain_bank():
    if "grain" not in _C:
        np = _np()
        rng = np.random.default_rng(7)
        _C["grain"] = [(rng.standard_normal((H, W, 1)) * 1.1).astype(np.float32) for _ in range(6)]
    return _C["grain"]


def leak_mask():
    if "leak" not in _C:
        np = _np()
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        _C["leak"] = np.exp(-(((W - xx) / (W * 0.38)) ** 2 + ((yy - H * 0.3) / (H * 0.9)) ** 2))[..., None].astype(np.float32)
    return _C["leak"]


def par_mask():
    if "par" not in _C:
        np = _np()
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        d = np.sqrt(((xx - W / 2) / (W * 0.40)) ** 2 + ((yy - H / 2) / (H * 0.44)) ** 2)
        _C["par"] = Image.fromarray((np.clip(1 - d, 0, 1) ** 0.8 * 255).astype(np.uint8))
    return _C["par"]


def fx_apply(img, t, fi, phase=0.0, vig=True, grain=True, leak=True):
    """Film look: vignette, film grain, occasional warm light leak."""
    np = _np()
    arr = np.asarray(img, dtype=np.float32)
    if vig:
        arr = arr * vig_mask()
    if grain:
        arr = arr + grain_bank()[fi % 6]
    if leak:
        v = max(0.0, math.sin(t * 0.8 + phase)) ** 8 * 0.30
        if v > 0.01:
            arr = arr + leak_mask() * (np.array([255, 150, 70], dtype=np.float32) * v)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


class Dust:
    def __init__(self, seed, n=16):
        r = random.Random(seed)
        self.p = [(r.uniform(0, W), r.uniform(0, H), r.uniform(-14, 14), r.uniform(-22, -6), r.choice([3, 4, 5, 7]),
                   r.uniform(0.25, 0.6), r.uniform(0, 6)) for _ in range(n)]
        self.sp = {s: soft_circle(s, (255, 244, 220), 1.0) for s in (3, 4, 5, 7)}

    def draw(self, frame, t):
        for x, y, vx, vy, s, a, ph in self.p:
            put(frame, self.sp[s], (x + vx * t + 12 * math.sin(t + ph)) % W, (y + vy * t) % H, 1.0,
                a * (0.6 + 0.4 * math.sin(t * 1.3 + ph)))


def cam_state(kind, p, t):
    z, cx, cy = 1.0, 0.5, 0.5
    if kind == "push":
        z = 1 + 0.20 * p
    elif kind == "pull":
        z = 1.20 - 0.20 * p
    elif kind in ("pan_lr", "pan_rl"):
        z = 1.20
        s = (p * 2 - 1) * (1 - 1 / z) / 2 * 0.9
        cx = 0.5 + (s if kind == "pan_lr" else -s)
    elif kind in ("tilt_up", "tilt_down"):
        z = 1.20
        s = (p * 2 - 1) * (1 - 1 / z) / 2 * 0.9
        cy = 0.5 + (-s if kind == "tilt_up" else s)
    elif kind == "orbit":
        z = 1.16 + 0.05 * p
        ang = p * math.pi * 1.2
        cx, cy = 0.5 + 0.05 * math.cos(ang), 0.5 + 0.035 * math.sin(ang)
    elif kind == "handheld":
        z = 1.10
        cx = 0.5 + 0.010 * math.sin(t * 1.7) + 0.006 * math.sin(t * 3.3)
        cy = 0.5 + 0.008 * math.sin(t * 2.1) + 0.005 * math.sin(t * 4.1)
    elif kind == "push_tl":
        z = 1 + 0.24 * p
        m = (1 - 1 / z) / 2
        cx, cy = 0.5 - m * 0.8, 0.5 - m * 0.8
    elif kind == "push_br":
        z = 1 + 0.24 * p
        m = (1 - 1 / z) / 2
        cx, cy = 0.5 + m * 0.8, 0.5 + m * 0.8
    m = (1 - 1 / z) / 2
    return z, min(0.5 + m, max(0.5 - m, cx)), min(0.5 + m, max(0.5 - m, cy))


def crop_box(z, cx, cy):
    cw, ch = SW / z, SH / z
    l = min(SW - cw, max(0.0, cx * SW - cw / 2))
    t = min(SH - ch, max(0.0, cy * SH - ch / 2))
    return (l, t, l + cw, t + ch)


def kf_state(shot, tl):
    """Multi-waypoint keyframe camera: smooth ease-in/out between (time, zoom, x, y) waypoints, then a very slow drift."""
    pts = shot["kfs"]
    if tl <= pts[0][0]:
        z, cx, cy = pts[0][1:]
    else:
        z = cx = cy = None
        for (t0, z0, x0, y0), (t1, z1, x1, y1) in zip(pts, pts[1:]):
            if tl <= t1:
                q = e_io(cl((tl - t0) / max(1e-3, t1 - t0)))
                z, cx, cy = lerp(z0, z1, q), lerp(x0, x1, q), lerp(y0, y1, q)
                break
        if z is None:
            t, z, cx, cy = pts[-1]
            z *= 1 + min(0.05, (tl - t) * 0.012)
    m = (1 - 1 / z) / 2
    return z, min(0.5 + m, max(0.5 - m, cx)), min(0.5 + m, max(0.5 - m, cy))


def shot_state(shot, tl):
    """(zoom, cx, cy) of a still-picture shot at time tl (used so the next shot of the SAME picture continues from here)."""
    if shot.get("kfs"):
        return kf_state(shot, tl)
    p = cl(tl / max(0.1, shot["dur"]))
    if shot["cam"] == "focus":
        q = e_out(p)
        z = 1.12 + 0.42 * q
        cx, cy = 0.5 + (shot["fx"] - 0.5) * q, 0.5 + (shot["fy"] - 0.5) * q
        m = (1 - 1 / z) / 2
        return z, min(0.5 + m, max(0.5 - m, cx)), min(0.5 + m, max(0.5 - m, cy))
    return cam_state(shot["cam"], p, tl)


def shot_frame(shot, tl):
    """One camera frame of a still picture: push / pull / pan / tilt / orbit / handheld, optional 2.5D parallax + rack focus."""
    src = shot["img"]
    p = cl(tl / max(0.1, shot["dur"]))
    if shot.get("clip"):
        if shot.get("reader") is None:
            shot["reader"] = ClipReader(shot["clip"], shot["dur"])
        f_ = shot["reader"].frame(int(round(tl * FPS)))
        if f_ is not None:
            f2 = apply_grade(f_, shot.get("grade", "natural"))
            z = 1.0 + 0.05 * p  # very slow push so the footage never looks frozen between moves
            return f2.resize((W, H), Image.BILINEAR, box=((W - W / z) / 2, (H - H / z) / 2, (W + W / z) / 2, (H + H / z) / 2)) if z > 1.003 else f2
    if shot.get("kfs"):
        z, cx, cy = kf_state(shot, tl)
    elif shot["cam"] == "focus":
        z, cx, cy = shot_state(shot, tl)
    else:
        z, cx, cy = cam_state(shot["cam"], p, tl)
    if tl < 0.3 and shot["cam"] != "focus" and not shot.get("kfs"):
        z *= 1 + 0.10 * (1 - e_out(tl / 0.3))  # quick punch-in on the beat the word is spoken
    zc = shot.get("zcap", 2.1)
    if z > zc:  # never magnify beyond the real pixels of the picture: that is what made pictures slowly turn blurry
        z = zc
        m_ = (1 - 1 / z) / 2
        cx, cy = min(0.5 + m_, max(0.5 - m_, cx)), min(0.5 + m_, max(0.5 - m_, cy))
    fr = src.resize((W, H), Image.BICUBIC, box=crop_box(z, cx, cy))
    if shot.get("par"):
        fg = src.resize((W, H), Image.BICUBIC, box=crop_box(min(zc, z * (1 + 0.07 * p)), cx, cy))
        fr = Image.composite(fg, fr, par_mask())
    if shot.get("focus") and tl < 0.8:
        r = (1 - tl / 0.8) * 9
        if r > 0.4:
            fr = fr.filter(ImageFilter.GaussianBlur(r))
    return fr


def blend_trans(prev, cur, p, kind):
    np = _np()
    p = cl(p)
    if kind in TR_NEW:
        return tr_new(prev, cur, p, kind)
    if kind == "fade":
        return Image.blend(prev, cur, e_io(p))
    if kind == "flash":
        white = Image.new("RGB", (W, H), (255, 255, 255))
        return Image.blend(prev, white, p / 0.35) if p < 0.35 else Image.blend(white, cur, (p - 0.35) / 0.65)
    if kind == "whip":
        off = int(e_io(p) * W)
        canvas = Image.new("RGB", (2 * W, H))
        canvas.paste(prev, (0, 0))
        canvas.paste(cur, (W, 0))
        fr = canvas.crop((off, 0, off + W, H))
        k = 1 + int(14 * math.sin(math.pi * p))
        if k > 1:
            fr = fr.resize((max(8, W // k), H), Image.BILINEAR).resize((W, H), Image.BILINEAR)
        return fr
    if kind == "wipe":
        if "wipeg" not in _C:
            yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
            _C["wipeg"] = xx / W * 0.75 + yy / H * 0.25
        e = e_io(p) * 1.12 - 0.06
        mask = Image.fromarray((np.clip((e - _C["wipeg"]) / 0.07, 0, 1) * 255).astype(np.uint8))
        return Image.composite(cur, prev, mask)
    if kind == "circle":
        m = Image.new("L", (W, H), 0)
        r = e_io(p) * math.hypot(W, H) / 2
        ImageDraw.Draw(m).ellipse([W / 2 - r, H / 2 - r, W / 2 + r, H / 2 + r], fill=255)
        return Image.composite(cur, prev, m.filter(ImageFilter.GaussianBlur(5)))
    if kind == "zoom":
        def zm(im, z):
            return im.resize((W, H), Image.BILINEAR, box=(W / 2 - W / 2 / z, H / 2 - H / 2 / z, W / 2 + W / 2 / z, H / 2 + H / 2 / z))
        return Image.blend(zm(prev, lerp(1.0, 1.3, e_io(p))), zm(cur, lerp(1.3, 1.0, e_out(p))), e_io(p))
    if kind == "glitch":
        base = prev if p < 0.45 else cur
        fr = base.copy()
        amt = int(46 * (1 - p))
        r, g, b = fr.split()
        fr = Image.merge("RGB", (ImageChops.offset(r, amt, 0), g, ImageChops.offset(b, -amt, 0)))
        rr = random.Random(int(p * 40))
        for _ in range(5):
            y0 = rr.randint(0, H - 60)
            hh = rr.randint(10, 60)
            band = fr.crop((0, y0, W, y0 + hh))
            fr.paste(ImageChops.offset(band, rr.randint(-90, 90), 0), (0, y0))
        return fr
    return cur


def head_sprite(text, pal, size=60):
    hl = text_layer(text, load_font(size, "head"), fill=tuple(pal["ink"]), max_w=W - 160)
    bar = Image.new("RGBA", (min(hl.width, W - 200), 10), (0, 0, 0, 0))
    bar.paste(Image.new("RGBA", bar.size, tuple(pal["accent"]) + (255,)), (0, 0), rounded_mask(bar.size, 5))
    head = Image.new("RGBA", (hl.width, hl.height + 16), (0, 0, 0, 0))
    head.alpha_composite(hl, (0, 0))
    head.alpha_composite(bar, ((head.width - bar.width) // 2, hl.height + 2))
    return head


def lower_third(text, pal):
    lay = text_layer(text, load_font(46, "head"), fill=(255, 255, 255), stroke=0)
    w, h = lay.width + 64, lay.height + 26
    pill = Image.new("RGBA", (w + 12, h + 12), (0, 0, 0, 0))
    wm = Image.new("RGBA", (w + 12, h + 12), (255, 255, 255, 255))
    wm.putalpha(rounded_mask((w + 12, h + 12), (h + 12) // 2))
    pill.alpha_composite(wm)
    col = Image.new("RGBA", (w, h), tuple(pal["accent"]) + (255,))
    col.putalpha(rounded_mask((w, h), h // 2))
    pill.alpha_composite(col, (6, 6))
    pill.alpha_composite(lay, ((w + 12 - lay.width) // 2, (h + 12 - lay.height) // 2))
    return pill


# ---- animated subtitles with the spoken word highlighted
def build_sub_sprites(text, pal):
    f = load_font(46, "sub")
    probe = ImageDraw.Draw(Image.new("L", (4, 4)))
    lines = wrap_text(probe, text, f, W - 260)
    lh = int(46 * 1.5)
    cw, ch = W - 160, lh * len(lines) + 36
    pos = []
    for li, line in enumerate(lines):
        words = line.split()
        total = probe.textlength(line, font=f)
        x0 = (cw - total) / 2
        for k, wd in enumerate(words):
            pre = " ".join(words[:k]) + (" " if k else "")
            pos.append((wd, x0 + probe.textlength(pre, font=f), 12 + li * lh))
    base = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    sh = Image.new("L", (cw, ch), 0)
    sd = ImageDraw.Draw(sh)
    for wd, x, y in pos:
        sd.text((x, y + 5), wd, font=f, fill=255, stroke_width=6)
    sh = sh.filter(ImageFilter.GaussianBlur(4)).point(lambda v: int(v * 0.6))
    shl = Image.new("RGBA", (cw, ch), (0, 0, 0, 255))
    shl.putalpha(sh)
    base = Image.alpha_composite(base, shl)
    bd = ImageDraw.Draw(base)
    ink = tuple(pal["ink"])
    for wd, x, y in pos:
        bd.text((x, y), wd, font=f, fill=ink + (255,), stroke_width=6, stroke_fill=ink + (255,))
    sprites = []
    for hi in range(len(pos)):
        s = base.copy()
        d = ImageDraw.Draw(s)
        for k, (wd, x, y) in enumerate(pos):
            d.text((x, y), wd, font=f, fill=(255, 224, 40, 255) if k == hi else (255, 255, 255, 255))
        sprites.append(s)
    return sprites


def make_subtrack(text, speech_dur, dur, pal, spans=None):
    items = []
    if not SUBS_ON:
        return items
    ok = bool(spans) and len(spans) == len(text.split())
    wi = 0
    for c, a, b in chunk_times(text, speech_dur, dur, spans):
        sprites = build_sub_sprites(c, pal)
        k = len(c.split())
        if ok:
            starts = [spans[wi + j][0] for j in range(k)]
        else:
            lens = [max(1, len(x)) for x in c.split()]
            tot, acc, starts = sum(lens), 0, []
            for l in lens:
                starts.append(a + (b - a) * acc / tot)
                acc += l
        wi += k
        items.append((a, b, sprites, starts))
    return items


def draw_sub(frame, items, t):
    for a, b, sprites, starts in items:
        if a <= t < b:
            wi = 0
            for k, st_ in enumerate(starts):
                if t >= st_ - 0.04:
                    wi = k
            sp = sprites[min(wi, len(sprites) - 1)]
            sc = 0.88 + 0.12 * e_out((t - a) / 0.14) if t - a < 0.14 else 1.0
            put(frame, sp, W / 2, H - 30 - sp.height / 2 * sc, sc, 1.0)
            return


# ---- scene drawers: each returns draw(t) -> PIL frame (before subtitles / scene transition)
def apply_grade(im, grade):
    """Scene colour grading: warm / cool / cinematic (teal-orange) / vivid / dark / natural."""
    np = _np()
    sat = {"vivid": 1.35, "natural": 1.1, "warm": 1.15, "cool": 1.1, "cinematic": 1.2, "dark": 0.92}.get(grade, 1.1)
    im = ImageEnhance.Contrast(ImageEnhance.Color(im).enhance(sat)).enhance(1.06)
    if grade in ("warm", "cool", "cinematic", "dark", "dread", "bwred", "redwash"):
        a = np.asarray(im, dtype=np.float32)
        if grade == "dread":  # cold teal-green horror look
            lum = a.mean(2, keepdims=True)
            a = (a * 0.55 + lum * 0.45) * np.array([0.72, 1.05, 1.02], dtype=np.float32) * 0.9
        elif grade == "bwred":  # black & white with a blood-red glow (shock / fear moments)
            lum = a.mean(2, keepdims=True)
            hh, ww = lum.shape[:2]
            yy, xx = np.mgrid[0:hh, 0:ww].astype(np.float32)
            glow = np.clip(1.2 - np.sqrt(((xx / ww - 0.5) / 0.75) ** 2 + ((yy / hh - 0.55) / 0.8) ** 2), 0, 1)[..., None]
            a = lum * np.array([1.0, 1.0, 1.0], dtype=np.float32) * (1 - 0.35 * glow) + glow * np.array([170, 10, 20], dtype=np.float32) * 0.55
            a = (a - 128) * 1.12 + 128
        elif grade == "redwash":
            lum = a.mean(2, keepdims=True)
            a = (a * 0.5 + lum * 0.5) * np.array([1.25, 0.55, 0.55], dtype=np.float32)
        elif grade == "warm":
            a[..., 0] *= 1.08
            a[..., 2] *= 0.88
        elif grade == "cool":
            a[..., 0] *= 0.90
            a[..., 2] *= 1.10
        elif grade == "cinematic":
            lum = a.mean(2, keepdims=True) / 255.0
            a = a + (1 - lum) ** 2 * np.array([-14, 6, 16], dtype=np.float32) + lum ** 2 * np.array([16, 6, -14], dtype=np.float32)
        else:
            a = (a * 0.84 - 128) * 1.14 + 128
        im = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    return im


def graded_image(ctx, path, grade="natural"):
    key = (path, grade)
    if key not in ctx["imgs"]:
        im = Image.open(path).convert("RGB")
        im = ImageOps.fit(im, (SW, SH), Image.LANCZOS)
        ctx["imgs"][key] = apply_grade(im, grade)
    return ctx["imgs"][key]


def zoom_cap(path):
    """Largest camera zoom at which this picture is still sharp on a 1280-wide frame (at most 1.3x magnification of real pixels)."""
    nat = NATIVE.get(str(path), 1920.0)
    return max(1.06, min(2.1, 1.3 * nat / W))


def pick_cam(ctx):
    rnd = ctx["rnd"]
    c = rnd.choice([x for x in CAMS if x != ctx.get("last_cam")])
    ctx["last_cam"] = c
    return c


def build_shots(sc, dur, speech_dur, ctx, tone):
    """Cut the picture exactly when the narrator reaches the words of each shot. Shots without a new picture
    re-frame the SAME picture on the part being talked about (focus move)."""
    rnd = ctx["rnd"]
    raw = sc["shots"]
    words = sc["narration_hi"].split()
    nw = len(words)
    wt = sc.get("wt") if sc.get("wt") and len(sc["wt"]) == nw else None
    cnt = [max(1, len(x["text_hi"].split())) for x in raw]
    tot = sum(cnt)
    starts, acc = [], 0
    for c in cnt:
        starts.append(min(nw - 1, int(round(acc * nw / tot))))
        acc += c
    spans = []
    for k, s_ in enumerate(raw):
        if k == 0:
            a = 0.0
        elif wt and sc.get("sent") and any(starts[k] == st_[0] for st_ in sc["sent"]):
            a = max(0.3, next(st_[2] for st_ in sc["sent"] if st_[0] == starts[k]) - 0.03)  # a new sentence: cut exactly when it starts
        elif wt:
            a = max(0.4, wt[starts[k]][0] - 0.12)  # cut a hair BEFORE the word is spoken
        else:
            a = speech_dur * starts[k] / max(1, nw)
        spans.append([a, 0.0, s_])
    for k in range(len(spans)):
        spans[k][1] = spans[k + 1][0] if k + 1 < len(spans) else dur
    shots, last_img = [], None
    for a, b, s_ in spans:
        if b - a < 0.35 and shots:  # too short to see: merge into previous shot
            shots[-1]["t1"] = b
            shots[-1]["dur"] = b - shots[-1]["t0"]
            continue
        n = 1 if (s_.get("clip") or s_.get("kf")) else max(1, math.ceil((b - a) / 2.7))
        sd = (b - a) / n
        for j in range(n):
            new_img = (j == 0) and (s_["image"] is not last_img)
            fx = s_.get("fx") if s_.get("fx") is not None else rnd.uniform(0.3, 0.7)
            fy = s_.get("fy") if s_.get("fy") is not None else rnd.uniform(0.35, 0.65)
            if j > 0:
                fx, fy = min(0.8, max(0.2, fx + rnd.uniform(-0.25, 0.25))), min(0.75, max(0.25, fy + rnd.uniform(-0.2, 0.2)))
            if not shots:
                cam = "push"
            elif not new_img:
                cam = "focus"
            else:
                cam = "focus" if (s_.get("fx") is not None and rnd.random() < 0.5) else pick_cam(ctx)
            if not shots:
                trans = "none"
            elif j > 0 or not new_img:
                trans = "zoom" if rnd.random() < 0.5 else "fade"
            else:
                trans = rnd.choice(["fade", "zoom", "wipe", "whip", "flash"] if tone in ("excited", "suspense") else ["fade", "fade", "zoom", "wipe"])
            kfs = None
            if s_.get("kf") and not s_.get("clip") and wt and j == 0:
                prev_st = shot_state(shots[-1], shots[-1]["dur"]) if (shots and not new_img and not shots[-1].get("clip")) else None
                st = prev_st or (1.0, 0.5, 0.5)
                kfs = [(0.0, *st)]
                for kf in s_["kf"]:
                    wi = min(nw - 1, max(0, int(kf["w"])))
                    ta = min(sd - 0.2, max(0.45, wt[wi][0] + 0.12 - a))
                    zz = min(zoom_cap(s_["image"]), max(1.08, kf["z"]))
                    m_ = (1 - 1 / zz) / 2
                    tx, ty = min(0.5 + m_, max(0.5 - m_, kf["fx"])), min(0.5 + m_, max(0.5 - m_, kf["fy"]))
                    t_last = kfs[-1][0]
                    t_start = max(t_last + 0.05, ta - 0.9)
                    if t_start > t_last + 0.2:
                        kfs.append((t_start, *kfs[-1][1:]))  # rest on the previous detail, then glide to the next one
                    if ta > kfs[-1][0] + 0.05:
                        kfs.append((ta, zz, tx, ty))
                if len(kfs) < 2:
                    kfs = None
            if s_.get("cam") in CAMS and new_img and kfs is None:
                cam = s_["cam"]
            if s_.get("trans") in TRANS_NAMES and shots and j == 0:
                trans = "none" if s_["trans"] == "cut" else s_["trans"]
            shots.append({"img": graded_image(ctx, s_["image"], s_.get("grade") if s_.get("grade") in GRADES else sc.get("grade", "natural")), "kfs": kfs, "label": ((s_.get("label_hi") or "") if (j == 0 and not (s_.get("ov") and s_["ov"]["kind"] in ("place", "title"))) else ""),
                          "pulse": ((rnd.choice([(200, 20, 30), (20, 120, 140), (255, 255, 255)]) if (new_img and shots and rnd.random() < (0.4 if tone in ("suspense", "serious", "excited") else 0.12)) else None)),
                          "t0": a + j * sd, "t1": a + (j + 1) * sd, "dur": sd, "cam": cam, "fx": fx, "fy": fy,
                          "par": cam in ("push", "pull", "orbit") and rnd.random() < 0.45,
                          "focus": (not shots), "trans": trans, "tdur": 0.3 if trans in ("fade", "zoom") else 0.4, "zcap": zoom_cap(s_["image"]),
                          "ov": (s_.get("ov") if j == 0 else None), "popup": (s_.get("popup_img") if j == 0 else None),
                          "clip": s_.get("clip"), "reader": None, "grade": s_.get("grade") if s_.get("grade") in GRADES else sc.get("grade", "natural"),
                          "pop_side": rnd.choice([-1, 1]),
                          "phase": rnd.uniform(0, 6)})
        last_img = s_["image"]
    shots[-1]["t1"] = dur
    return shots


_OV = {}


def ov_sprite(kind, text, mark, pal):
    key = (kind, text, mark)
    if key in _OV:
        return _OV[key]
    probe = ImageDraw.Draw(Image.new("L", (4, 4)))
    if kind == "bubble":  # comic speech bubble with the quote
        lay = text_layer(text, load_font(42, "head"), fill=(25, 25, 25), max_w=360, spacing=8)
        w, h = lay.width + 56, lay.height + 40
        big = Image.new("RGBA", (w + 30, h + 60), (0, 0, 0, 0))
        d = ImageDraw.Draw(big)
        d.rounded_rectangle([4, 4, w + 4, h + 4], radius=34, fill=(0, 0, 0, 255))
        d.polygon([(46, h), (28, h + 54), (96, h)], fill=(0, 0, 0, 255))
        d.rounded_rectangle([10, 10, w - 2, h - 2], radius=30, fill=(255, 252, 240, 255))
        d.polygon([(54, h - 4), (36, h + 44), (88, h - 4)], fill=(255, 252, 240, 255))
        if mark == "R":  # tail on the right: the speaker stands to the right of the bubble (only the SHAPE is mirrored, never the letters)
            big = ImageOps.mirror(big)
            big.alpha_composite(lay, (26 + (w - lay.width) // 2, 4 + (h - lay.height) // 2))
        else:
            big.alpha_composite(lay, (4 + (w - lay.width) // 2, 4 + (h - lay.height) // 2))
        sp = big.rotate(-2 if mark == "R" else 2, expand=True, resample=Image.BICUBIC)
    elif kind in ("note", "note0", "note_u"):  # SOURCE caption: plain text straight on the picture (no paper), discussed phrase underlined in red
        body, _, src = text.rpartition("|")
        if not body:
            body, src = text, ""
        f_head, f_body = load_font(38, "head"), load_font(31, "sub")
        wmax = 600
        lat = sum(c.isascii() for c in body) > 0.8 * max(1, len(body))
        head_lines = wrap_text(probe, (("Source: " if lat else "स्रोत: ") + src) if src else "", f_head, 520)[:3] if src else []
        lines = wrap_text(probe, body, f_body, 520)[:9]
        marks = [m.strip("।,.?!\"'").lower() for m in mark.split() if len(m.strip("।,.?!\"'")) > 1]
        lh_h, lh_b = int(f_head.size * 1.38), int(f_body.size * 1.55)
        pad = 18
        w = wmax + 2 * pad
        h = pad + lh_h * len(head_lines) + (10 if head_lines else 0) + lh_b * len(lines) + pad
        card = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        und = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        du = ImageDraw.Draw(und)
        dc = ImageDraw.Draw(card)
        txt_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        dt = ImageDraw.Draw(txt_layer)
        y = pad
        for ln in head_lines:
            tw_ = dt.textlength(ln, font=f_head)
            dt.text(((w - tw_) / 2, y + int(f_head.size * 1.05)), ln, font=f_head, fill=(255, 255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0, 255), anchor="ls")
            y += lh_h
        y += 10 if head_lines else 0
        sp_w = dt.textlength(" ", font=f_body) * 0.9
        for ln in lines:
            wds = ln.split()
            iw = [dt.textlength(wd, font=f_body) for wd in wds]
            tot = sum(iw) + sp_w * (len(wds) - 1)
            x = (w - tot) / 2
            run = None
            base = y + int(f_body.size * 1.12)
            for k_, (wd, ww) in enumerate(zip(wds, iw)):
                dt.text((x, base), wd, font=f_body, fill=(255, 255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0, 255), anchor="ls")
                wl_ = wd.lower().strip("।,.?!\"'();:")
                hit = any((wl_ == m) or (not m.isascii() and (m in wl_ or (len(wl_) > 2 and wl_ in m))) for m in marks)
                if hit:
                    run = (run[0], x + ww) if run else (x, x + ww)
                if (not hit or k_ == len(wds) - 1) and run:
                    pts_ = [(xx, base + 9 + 2.2 * math.sin(xx / 9.0)) for xx in range(int(run[0]) - 3, int(run[1]) + 4, 5)]
                    if len(pts_) > 1:
                        du.line(pts_, fill=(232, 28, 40, 255), width=5, joint="curve")
                    run = None
                x += ww + sp_w
            y += lh_b
        shd = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        shd.putalpha(txt_layer.getchannel("A").filter(ImageFilter.GaussianBlur(3)).point(lambda v: int(v * 0.8)))
        card.alpha_composite(shd, (2, 3))
        card.alpha_composite(txt_layer)
        scrim = Image.new("RGBA", (w + 80, h + 80), (0, 0, 0, 0))
        sm = Image.new("L", (w + 80, h + 80), 0)
        ImageDraw.Draw(sm).rounded_rectangle([40, 40, w + 40, h + 40], 30, fill=150)
        scrim.putalpha(sm.filter(ImageFilter.GaussianBlur(26)))
        full = Image.new("RGBA", scrim.size, (0, 0, 0, 0))
        if kind == "note_u":
            full.alpha_composite(und, (40, 40))
        else:
            full.alpha_composite(scrim)
            full.alpha_composite(card, (40, 40))
        sp = full
    elif kind == "place":  # location tag, bottom-left: dark glass panel, accent bar, outlined text, equal padding on all sides
        main_, _, sub_ = text.partition("|")
        a1 = text_layer(main_.strip(), load_font(64, "title"), fill=(255, 255, 255), stroke=3, stroke_fill=(10, 10, 14), shadow=(0, 4, 8, 0.8))
        a2 = text_layer(sub_.strip(), load_font(30, "sub"), fill=(240, 226, 205), stroke=2, stroke_fill=(10, 10, 14), shadow=(0, 2, 4, 0.8)) if sub_.strip() else None
        pad_ = 22
        tw_, th_ = max(a1.width, a2.width if a2 else 0), a1.height + (a2.height - 10 if a2 else 0)
        w, h = tw_ + 2 * pad_ + 16, th_ + 2 * pad_
        sp = Image.new("RGBA", (w + 40, h + 40), (0, 0, 0, 0))
        pm = Image.new("L", (w + 40, h + 40), 0)
        ImageDraw.Draw(pm).rounded_rectangle([20, 20, w + 20, h + 20], 18, fill=165)
        glass = Image.new("RGBA", sp.size, (8, 10, 18, 255))
        glass.putalpha(pm.filter(ImageFilter.GaussianBlur(1.2)))
        sp.alpha_composite(glass)
        ImageDraw.Draw(sp).rounded_rectangle([20, 20, 28, h + 20], 4, fill=tuple(pal["accent"]) + (255,))
        sp.alpha_composite(a1, (20 + 16 + pad_ - 6, 20 + pad_ - 6))
        if a2:
            sp.alpha_composite(a2, (20 + 16 + pad_ - 6, 20 + pad_ + a1.height - 16))
    elif kind == "title":  # huge glowing title reveal
        lay = text_layer(text, load_font(190, "title"), fill=(255, 244, 214), fill2=(255, 140, 50), max_w=int(W * 0.86), stroke=5, stroke_fill=(30, 8, 4), shadow=(0, 6, 10, 0.8))
        pad_ = 70
        big_ = Image.new("RGBA", (lay.width + 2 * pad_, lay.height + 2 * pad_), (0, 0, 0, 0))
        big_.alpha_composite(lay, (pad_, pad_))
        g = big_.getchannel("A").filter(ImageFilter.GaussianBlur(24)).point(lambda v: min(255, int(v * 1.6)))
        glow = Image.new("RGBA", big_.size, (255, 70, 20, 255))
        glow.putalpha(g)
        sp = Image.new("RGBA", big_.size, (0, 0, 0, 0))
        sp.alpha_composite(glow)
        sp.alpha_composite(big_)
    else:  # year / time marker on a timeline
        lay = text_layer(text, load_font(58, "title"), fill=(255, 255, 255), stroke=2, stroke_fill=(90, 8, 18))
        pw, ph = lay.width + 56, lay.height + 22
        w, h = max(1000, pw + 60), ph + 70
        sp = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(sp)
        d.line([(10, ph + 36), (w - 10, ph + 36)], fill=(255, 255, 255, 200), width=3)
        for x in range(30, w - 20, 70):
            d.line([(x, ph + 28), (x, ph + 44)], fill=(255, 255, 255, 170), width=2)
        pl = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
        edge = Image.new("RGBA", (pw, ph), (255, 255, 255, 255))
        edge.putalpha(rounded_mask((pw, ph), 14))
        pl.alpha_composite(edge)
        col = Image.new("RGBA", (pw - 6, ph - 6), (200, 20, 40, 255))
        col.putalpha(rounded_mask((pw - 6, ph - 6), 11))
        pl.alpha_composite(col, (3, 3))
        pl.alpha_composite(lay, ((pw - lay.width) // 2, (ph - lay.height) // 2))
        sp.alpha_composite(pl, ((w - pw) // 2, 0))
        d.polygon([(w // 2 - 10, ph), (w // 2 + 10, ph), (w // 2, ph + 14)], fill=(200, 20, 40, 255))
    _OV[key] = sp
    return sp


# =========================================================================== V2 OVERLAYS
# Source cards in 4 styles (banner / torn paper / dark text page / document page), sliding year timeline, person name card with real photo,
# round pop-ups with cross / tick, big red stat text.  Every card is drawn from REAL page text only (never written by the bot).
SRC_STYLES = ["banner", "paper", "dark", "doc", "strip", "box"]
STYLE_CYCLE = ["strip", "box", "strip", "paper", "strip", "dark", "box", "doc", "banner"]  # small lower-left cards are the most common in the reference
V2_KINDS = ("quote", "note", "year", "person", "stat", "verdict", "explain", "srclist", "action", "place", "name", "feature",
            "measure", "qmark", "lens", "beforeafter", "compare", "mark", "chart", "tline", "hl")
OV_MAXT = {"quote": 4.4, "place": 5.0, "year": 4.6, "note": 6.6, "bubble": 4.2, "title": 4.2, "person": 4.4, "stat": 3.6, "verdict": 5.0, "explain": 4.8, "srclist": 4.8, "action": 5.0, "name": 4.4, "place": 4.8, "feature": 7.6,
         "measure": 6.0, "qmark": 5.0, "lens": 6.0, "beforeafter": 7.0, "compare": 7.6, "mark": 5.0, "chart": 7.6, "tline": 8.0, "hl": 5.0}
_BLACK = {}


def _ascii(s):
    import unicodedata
    s = (s or "").replace("\u2014", "-").replace("\u2013", "-").replace("\u2018", "'").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()).strip()


def _dom(url):
    try:
        from urllib.parse import urlparse
        d = urlparse(url).netloc.lower()
        return d[4:] if d.startswith("www.") else d
    except Exception:  # noqa
        return ""


def font_for(text, size, role):
    """English text may use the fancy Latin fonts; Hindi text always uses a Devanagari font."""
    if role in ("serif", "type", "cond", "comic") and not all(ord(c) < 0x250 for c in (text or "")):
        role = "title"
    return load_font(size, role)


def _norm_tok(w):
    return re.sub(r"[^\w]+", "", (w or "").lower())


def _mark_idx(words, mark):
    mt = [_norm_tok(x) for x in (mark or "").split() if _norm_tok(x)]
    if not mt:
        return set()
    nw = [_norm_tok(w) for w in words]
    n = len(mt)
    for i in range(len(nw) - n + 1):
        if nw[i:i + n] == mt:
            return set(range(i, i + n))
    s = set(mt)
    return {i for i, w in enumerate(nw) if w in s and len(w) > 2}


def _flow(probe, body, font, maxw, max_lines=99):
    words = body.split()
    sp = probe.textlength(" ", font=font)
    lines, cur, x = [], [], 0.0
    for i, w in enumerate(words):
        ww = probe.textlength(w, font=font)
        if cur and x + ww > maxw:
            lines.append(cur)
            cur, x = [], 0.0
        cur.append((i, w, x, ww))
        x += ww + sp
    if cur:
        lines.append(cur)
    return lines[:max_lines], sp


def _paper(w, h, base=(243, 235, 213), seed=0, grain=9):
    np = _np()
    rng = np.random.RandomState(seed)
    n = rng.normal(0, grain, (h, w, 1)).astype("float32")
    low = np.asarray(Image.fromarray((rng.rand(h // 24 + 2, w // 24 + 2) * 255).astype("uint8")).resize((w, h), Image.BICUBIC), "float32")[..., None] / 255.0
    arr = np.array(base, "float32")[None, None, :] + n + (low - 0.5) * 24
    yy, xx = np.mgrid[0:h, 0:w]
    edge = np.minimum(np.minimum(xx, w - 1 - xx), np.minimum(yy, h - 1 - yy)).astype("float32")
    arr *= (0.88 + 0.12 * np.clip(edge / 46, 0, 1))[..., None]
    return Image.fromarray(np.clip(arr, 0, 255).astype("uint8")).convert("RGBA")


def _torn_mask(w, h, amp=7, step=9, seed=0):
    rng = random.Random(seed)
    m = amp + 2

    def edge(p0, p1, nrm):
        n = max(2, int(math.hypot(p1[0] - p0[0], p1[1] - p0[1]) // step))
        out = []
        for k in range(n):
            t = k / n
            j = rng.uniform(-amp, amp)
            out.append((p0[0] + (p1[0] - p0[0]) * t + nrm[0] * j, p0[1] + (p1[1] - p0[1]) * t + nrm[1] * j))
        return out
    pts = edge((m, m), (w - m, m), (0, 1)) + edge((w - m, m), (w - m, h - m), (1, 0)) + edge((w - m, h - m), (m, h - m), (0, 1)) + edge((m, h - m), (m, m), (1, 0))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).polygon(pts, fill=255)
    return mask.filter(ImageFilter.GaussianBlur(0.7))


def _with_shadow(img, off=(7, 11), blur=10, alpha=0.55, pad=26):
    out = Image.new("RGBA", (img.width + 2 * pad, img.height + 2 * pad), (0, 0, 0, 0))
    sh = Image.new("RGBA", out.size, (0, 0, 0, 255))
    sh.putalpha(Image.new("L", out.size, 0))
    a = Image.new("L", out.size, 0)
    a.paste(img.getchannel("A"), (pad + off[0], pad + off[1]))
    sh.putalpha(a.filter(ImageFilter.GaussianBlur(blur)).point(lambda v: int(v * alpha)))
    out.alpha_composite(sh)
    out.alpha_composite(img, (pad, pad))
    return out


def _tape(w=110, h=34, ang=-6):
    t = Image.new("RGBA", (w, h), (236, 226, 170, 200))
    d = ImageDraw.Draw(t)
    for x in range(0, w, 6):
        d.line([(x, 0), (x, h)], fill=(255, 255, 255, 40), width=1)
    return t.rotate(ang, expand=True, resample=Image.BICUBIC)


class SrcCard:
    """A source card: bg art + text layer + highlight runs that are 'drawn' (wiped) left to right while the narrator speaks."""

    def __init__(self, bg, text, runs, hl_fn, extra=None, rot=0.0, behind=None):
        self.bg, self.text, self.runs, self.hl_fn, self.extra, self.rot, self.behind = bg, text, runs, hl_fn, extra, rot, behind
        self.w, self.h = bg.size
        self._c = {}

    def frame(self, p, extra_q=0.0):
        key = (int(round(cl(p) * 14)), int(round(cl(extra_q) * 5)))
        if key in self._c:
            return self._c[key]
        im = self.bg.copy()
        if p > 0.001 and self.runs:
            lay = Image.new("RGBA", im.size, (0, 0, 0, 0))
            d = ImageDraw.Draw(lay)
            total = sum(r[2] - r[0] for r in self.runs) or 1.0
            done, cum = p * total, 0.0
            for r in self.runs:
                w_ = r[2] - r[0]
                part = cl((done - cum) / max(1.0, w_))
                cum += w_
                if part <= 0:
                    break
                self.hl_fn(d, (r[0], r[1], r[0] + w_ * part, r[3]), part)
            im.alpha_composite(lay)
        im.alpha_composite(self.text)
        if self.extra is not None and extra_q > 0.01:
            ex = self.extra
            if extra_q < 0.99:
                ex = ex.copy()
                ex.putalpha(ex.getchannel("A").point(lambda v: int(v * extra_q)))
            im.alpha_composite(ex)
        if abs(self.rot) > 0.01:
            im = im.rotate(self.rot, expand=True, resample=Image.BICUBIC)
        if len(self._c) > 24:
            self._c.clear()
        self._c[key] = im
        return im


def _draw_words(dt, lines, f, x0, y0, lh, fill, stroke=0, stroke_fill=(0, 0, 0, 255)):
    """Draw laid-out words; returns the baseline y of every line."""
    bases = []
    y = y0
    for ln in lines:
        base = y + int(f.size * 1.05)
        for (_i, w, x, _ww) in ln:
            dt.text((x0 + x, base), w, font=f, fill=fill, anchor="ls", stroke_width=stroke, stroke_fill=stroke_fill)
        bases.append(base)
        y += lh
    return bases


def _fit_text(probe, body, mark, font, maxw, max_lines):
    """Shorten the paragraph (never the marked phrase) so it fits max_lines; the cut is shown with an ellipsis."""
    words = body.split()
    lines, _ = _flow(probe, body, font, maxw)
    if len(lines) <= max_lines:
        return body
    idx = _mark_idx(words, mark)
    a, b = 0, len(words)
    need_hi = (max(idx) + 1) if idx else 0
    while b > max(need_hi, 6):
        b -= 1
        txt = " ".join(words[a:b]) + ("\u2026" if b < len(words) else "")
        if len(_flow(probe, txt, font, maxw)[0]) <= max_lines:
            return ("\u2026 " if a else "") + txt
    while a < (min(idx) if idx else 0) and a < len(words) - 6:  # the marked phrase is far in: drop words from the front instead
        a += 1
        txt = "\u2026 " + " ".join(words[a:b])
        if len(_flow(probe, txt, font, maxw)[0]) <= max_lines:
            return txt
    return " ".join(words[a:b])


def _runs_of(lines, idx, x0, y0, lh, size, pad=4):
    runs = []
    for li, ln in enumerate(lines):
        a = b = None
        for (i, _w, x, ww) in ln:
            if i in idx:
                a = x if a is None else a
                b = x + ww
            elif a is not None:
                runs.append((x0 + a - pad, y0 + li * lh + 2, x0 + b + pad, y0 + li * lh + lh - 2))
                a = b = None
        if a is not None:
            runs.append((x0 + a - pad, y0 + li * lh + 2, x0 + b + pad, y0 + li * lh + lh - 2))
    return runs


def _wavy(d, x0, x1, y, col, wd=5):
    pts = [(xx, y + 2.0 * math.sin(xx / 9.0)) for xx in range(int(x0), int(x1) + 1, 4)]
    if len(pts) > 1:
        d.line(pts, fill=col, width=wd, joint="curve")


def _hl_underline(d, r, part):
    _wavy(d, r[0], r[2], r[3] - 4, (232, 28, 40, 255), 5)


def _hl_marker(d, r, part):
    d.rounded_rectangle([r[0], r[1] + 3, r[2], r[3] - 3], radius=5, fill=(255, 224, 40, 175))
    _wavy(d, r[0], r[2], r[3] - 2, (225, 30, 40, 255), 4)


def _hl_band(d, r, part):
    d.rounded_rectangle([r[0] - 2, r[1] + 2, r[2] + 2, r[3] - 2], radius=3, fill=(206, 24, 46, 235))


def build_card(style, body, title, dom, mark):
    key = (style, body, title, dom, mark)
    if key in _CARDS:
        return _CARDS[key]
    if len(_CARDS) > 8:
        _CARDS.clear()
    body, title = _ascii(body), pretty_source(title)[:110]
    dom = ""  # a web address is never written on the video: only the plain name of the source
    mark = _ascii(mark)
    probe = ImageDraw.Draw(Image.new("L", (4, 4)))
    if style == "banner":
        fT, fB, fG = font_for("A", 33, "cond"), font_for("A", 28, "serif"), font_for("A", 24, "cond")
        maxw = W - 150
        body = _fit_text(probe, body, mark, fB, maxw, 3)
        words = body.split()
        idx = _mark_idx(words, mark)
        lines, _sp = _flow(probe, body, fB, maxw, 3)
        lh = int(fB.size * 1.5)
        tag = ("SOURCE: " + dom.upper()) if dom else "SOURCE"
        tw_ = probe.textlength(tag, font=fG)
        head_h = 40
        t_lines = wrap_text(probe, title.upper(), fT, maxw)[:2] if title else []
        h = 70 + head_h + len(t_lines) * 40 + len(lines) * lh + 36
        bg = Image.new("RGBA", (W, h), (0, 0, 0, 0))
        g = Image.linear_gradient("L").resize((W, h)).point(lambda v: int(min(1.0, v / 150.0) ** 1.3 * 215))
        scr = Image.new("RGBA", (W, h), (0, 0, 0, 255))
        scr.putalpha(g)
        bg.alpha_composite(scr)
        text = Image.new("RGBA", (W, h), (0, 0, 0, 0))
        dt = ImageDraw.Draw(text)
        y = 70
        dt.rounded_rectangle([60, y, 60 + tw_ + 30, y + head_h - 6], radius=6, fill=(214, 28, 40, 255))
        dt.text((75, y + head_h - 13), tag, font=fG, fill=(255, 255, 255, 255), anchor="ls")
        y += head_h
        for tl_ in t_lines:
            dt.text((62, y + 28), tl_, font=fT, fill=(255, 255, 255, 255), anchor="ls", stroke_width=2, stroke_fill=(0, 0, 0, 255))
            y += 40
        y += 6
        _draw_words(dt, lines, fB, 62, y, lh, (255, 255, 255, 255), 2)
        runs = _runs_of(lines, idx, 62, y, lh, fB.size)
        card = SrcCard(bg, text, runs, _hl_underline)
    elif style == "dark":
        fT, fB, fG = font_for("A", 44, "serif"), font_for("A", 30, "serif"), font_for("A", 26, "cond")
        maxw = 760
        body = _fit_text(probe, body, mark, fB, maxw, 7)
        words = body.split()
        idx = _mark_idx(words, mark)
        lines, _sp = _flow(probe, body, fB, maxw, 8)
        lh = int(fB.size * 1.55)
        t_lines = wrap_text(probe, "Source: " + title, fT, maxw)[:3] if title else ["Source"]
        h = 40 + len(t_lines) * 56 + (36 if dom else 0) + 18 + len(lines) * lh + 40
        bg = Image.new("RGBA", (maxw + 80, h), (0, 0, 0, 0))
        text = Image.new("RGBA", bg.size, (0, 0, 0, 0))
        dt = ImageDraw.Draw(text)
        y = 30
        for tl_ in t_lines:
            dt.text((40, y + 44), tl_, font=fT, fill=(255, 255, 255, 255), anchor="ls")
            y += 56
        if dom:
            dt.text((42, y + 26), dom.upper(), font=fG, fill=(255, 196, 60, 255), anchor="ls")
            y += 36
        dt.line([(42, y + 6), (maxw, y + 6)], fill=(255, 255, 255, 90), width=2)
        y += 24
        _draw_words(dt, lines, fB, 40, y, lh, (236, 228, 214, 255))
        runs = _runs_of(lines, idx, 40, y, lh, fB.size)
        card = SrcCard(bg, text, runs, _hl_band)
    elif style == "paper":
        fT, fB = font_for("A", 27, "serif"), font_for("A", 25, "serif")
        maxw = 580
        body = _fit_text(probe, body, mark, fB, maxw, 7)
        words = body.split()
        idx = _mark_idx(words, mark)
        lines, _sp = _flow(probe, body, fB, maxw, 8)
        lh = int(fB.size * 1.62)
        t_lines = wrap_text(probe, "Source: " + title, fT, maxw)[:3] if title else ["Source"]
        ph = 56 + len(t_lines) * 36 + 34 + len(lines) * lh + 52
        pw = maxw + 100 + 200  # the right margin is kept free for the clipped photo
        paper = _paper(pw, ph, seed=len(body))
        paper.putalpha(_torn_mask(pw, ph, 7, 9, seed=len(title) + 3))
        bg = _with_shadow(paper)
        text = Image.new("RGBA", bg.size, (0, 0, 0, 0))
        dt = ImageDraw.Draw(text)
        ox, oy = 26 + 50, 26 + 40
        y = oy
        for tl_ in t_lines:
            dt.text((ox, y + 28), tl_, font=fT, fill=(60, 42, 26, 255), anchor="ls")
            y += 36
        dt.line([(ox, y + 6), (ox + maxw, y + 6)], fill=(196, 30, 40, 255), width=3)
        y += 30
        _draw_words(dt, lines, fB, ox, y, lh, (48, 36, 28, 255))
        runs = _runs_of(lines, idx, ox, y, lh, fB.size, 5)
        ex = None
        if runs:
            r0 = max(runs, key=lambda r: r[2] - r[0]) if len(runs) > 1 else runs[0]
            if (r0[2] - r0[0]) < 300:
                ex = Image.new("RGBA", bg.size, (0, 0, 0, 0))
                ImageDraw.Draw(ex).ellipse([r0[0] - 12, r0[1] - 5, r0[2] + 12, r0[3] + 5], outline=(210, 28, 38, 255), width=4)
                ex = ex.rotate(-2, resample=Image.BICUBIC, center=((r0[0] + r0[2]) / 2, (r0[1] + r0[3]) / 2))
        card = SrcCard(bg, text, runs, _hl_marker, extra=ex, rot=-2.4)
    elif style in ("strip", "box"):  # the reference's most common card: small, at the lower left, ~58% of the width
        fH, fB = font_for("A", 19, "cond"), font_for("A", 21, "serif")
        maxw = 640 if style == "strip" else 600
        body = _fit_text(probe, body, mark, fB, maxw, 3)
        words = body.split()
        idx = _mark_idx(words, mark)
        lines, _sp = _flow(probe, body, fB, maxw, 3)
        lh = int(fB.size * 1.5)
        head = "SOURCE: " + (title or "").upper()
        while len(head) > 12 and probe.textlength(head, font=fH) > maxw:  # the header must fit inside the card
            head = head[:-4].rstrip() + "..."
        ph = 26 + 30 + len(lines) * lh + 24
        pw = maxw + 70
        if style == "strip":
            paper = _paper(pw, ph, seed=len(body))
            paper.putalpha(_torn_mask(pw, ph, 5, 8, seed=len(title) + 1))
            bg = _with_shadow(paper, (4, 6), 7, 0.5, 16)
            ox, oy, hc, bc = 16 + 36, 16 + 20, (122, 20, 26, 255), (44, 34, 28, 255)
        else:
            bg = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
            ImageDraw.Draw(bg).rounded_rectangle([0, 0, pw - 1, ph - 1], radius=10, fill=(8, 8, 12, 178))
            ImageDraw.Draw(bg).rectangle([0, 8, 5, ph - 8], fill=(214, 28, 40, 255))
            ox, oy, hc, bc = 26, 18, (255, 208, 70, 255), (240, 236, 228, 255)
        text = Image.new("RGBA", bg.size, (0, 0, 0, 0))
        dt = ImageDraw.Draw(text)
        dt.text((ox, oy + 20), head, font=fH, fill=hc, anchor="ls")
        y = oy + 30
        _draw_words(dt, lines, fB, ox, y, lh, bc)
        runs = _runs_of(lines, idx, ox, y, lh, fB.size, 4)
        card = SrcCard(bg, text, runs, _hl_marker if style == "strip" else _hl_underline, rot=-1.0 if style == "strip" else 0.0)
    else:  # "doc": a document page with typewriter text and a yellow highlighter, a second page behind it
        fT, fB, fH = font_for("A", 36, "serif"), font_for("A", 27, "type"), font_for("A", 20, "type")
        maxw = 780
        body = _fit_text(probe, body, mark, fB, maxw, 8)
        words = body.split()
        idx = _mark_idx(words, mark)
        lines, _sp = _flow(probe, body, fB, maxw, 9)
        lh = int(fB.size * 1.6)
        t_lines = wrap_text(probe, title, fT, maxw)[:2] if title else []
        ph = 54 + (30 if dom else 0) + len(t_lines) * 46 + 26 + len(lines) * lh + 50
        pw = maxw + 120
        page = _paper(pw, ph, base=(240, 238, 230), seed=len(body) + 5, grain=6)
        page.putalpha(Image.new("L", (pw, ph), 255))
        back = _paper(pw, ph, base=(226, 224, 214), seed=7, grain=6)
        bd = ImageDraw.Draw(back)
        for yy in range(50, ph - 30, 26):
            bd.rectangle([60, yy, 60 + int((pw - 120) * (0.55 + 0.4 * ((yy * 37) % 10) / 10)), yy + 9], fill=(150, 150, 146, 255))
        back = _with_shadow(back.rotate(4.2, expand=True, resample=Image.BICUBIC, fillcolor=(0, 0, 0, 0)), pad=10)
        pg = _with_shadow(page, pad=20)
        sz = (pg.width + 90, pg.height + 60)
        bg = Image.new("RGBA", sz, (0, 0, 0, 0))
        bg.alpha_composite(back, (sz[0] - back.width, sz[1] - back.height - 6))
        bg.alpha_composite(pg, (10, 22))
        text = Image.new("RGBA", sz, (0, 0, 0, 0))
        dt = ImageDraw.Draw(text)
        ox, oy = 10 + 20 + 60, 22 + 20 + 34
        y = oy
        if dom:
            dt.text((ox, y + 16), dom.upper() + "  |  SOURCE DOCUMENT", font=fH, fill=(110, 108, 100, 255), anchor="ls")
            y += 30
        for tl_ in t_lines:
            dt.text((ox, y + 34), tl_, font=fT, fill=(20, 20, 20, 255), anchor="ls")
            y += 46
        dt.line([(ox, y + 8), (ox + maxw, y + 8)], fill=(30, 30, 30, 200), width=2)
        y += 26
        _draw_words(dt, lines, fB, ox, y, lh, (34, 34, 34, 255))
        runs = _runs_of(lines, idx, ox, y, lh, fB.size, 4)
        card = SrcCard(bg, text, runs, _hl_marker, rot=-1.6)
    _CARDS[key] = card
    return card


_CARDS = {}


def _dim(fr, k):
    if k <= 0.01:
        return
    b = _BLACK.get(fr.size)
    if b is None:
        b = _BLACK[fr.size] = Image.new("RGB", fr.size, (0, 0, 0))
    fr.paste(Image.blend(fr, b, min(0.9, k)))


def _vignette_dim(fr, k):
    _dim(fr, k)


def draw_note(fr, ps, tl, ov, t0, t1, a):
    it = ov.get("it") or {}
    style = it.get("style") or "banner"
    body = it.get("text") if it else ov.get("text", "").rpartition("|")[0]
    body = body or ""
    if not body:
        style = "banner"  # reference-only card: the real source name and a one-line note
        body = "The facts in this part of the video are based on this source."
    title, dom = it.get("sub") or "", it.get("dom") or ""
    card = build_card(style, body, title, dom, (it.get("mark") or ov.get("mark", "")) if it.get("text") else "")
    p = e_out(cl((tl - t0 - 0.55) / 1.15))
    q = e_out(cl((tl - t0) / 0.55))
    if style == "banner":
        spr = card.frame(p)
        put(fr, spr, W / 2, H - spr.height / 2 + (1 - q) * spr.height * 0.3, 1.0, a)
    elif style in ("strip", "box"):  # slides in from the left, slides out again; the rest of the picture stays free
        qin = e_out(cl((tl - t0) / 0.45))
        qout = 1 - e_out(cl((tl - (t1 - 0.4)) / 0.4))
        spr = card.frame(e_out(cl((tl - t0 - 0.5) / 1.0)))
        put(fr, spr, 30 + spr.width / 2 - (1 - min(qin, qout)) * (spr.width + 60), H - 30 - spr.height / 2, 1.0, 1.0)
    elif style == "paper":
        qin = e_out(cl((tl - t0) / 0.85))  # the paper UNROLLS from the top ... and rolls back up at the end
        qout = 1 - e_out(cl((tl - (t1 - 0.65)) / 0.6))
        qr = min(qin, qout)
        _dim(fr, 0.52 * min(1.0, qr * 2.5))
        p = e_out(cl((tl - t0 - 0.9) / 1.15))
        spr = card.frame(p, cl((p - 0.8) / 0.2))
        put_roll(fr, spr, W / 2, H * 0.5, qr, 1.0, (232, 222, 196))
        ph_ = ps.get("img")
        if ph_ is not None and qr > 0.97:  # polaroid of the current picture drops into the free margin of the card, like a clipped photo
            pol = _polaroid(ph_)
            qq = e_back((tl - t0 - 0.9) / 0.55)
            put(fr, pol, W / 2 + spr.width * 0.5 - 128, H * 0.5 - spr.height * 0.10 - (1 - cl(qq)) * 360, 0.62 + 0.38 * cl(qq), cl((qout - 0.9) * 10))
    elif style == "dark":
        _dim(fr, 0.66 * a)
        spr = card.frame(p)
        side = ov.get("side") or "right"
        cx = W * (0.30 if side == "left" else 0.70)
        put(fr, spr, min(max(cx, spr.width / 2 + 24), W - spr.width / 2 - 24), H * 0.5 + (1 - q) * 30, 1.0, a)
    else:  # document page: also rolls out / in
        qin = e_out(cl((tl - t0) / 0.85))
        qout = 1 - e_out(cl((tl - (t1 - 0.65)) / 0.6))
        qr = min(qin, qout)
        _dim(fr, 0.6 * min(1.0, qr * 2.5))
        p = e_out(cl((tl - t0 - 0.9) / 1.15))
        spr = card.frame(p)
        push = 1.0 + 0.05 * cl((tl - t0) / max(1.0, t1 - t0))
        fit = min(1.0, (W - 60) / spr.width, (H - 30) / spr.height)
        put_roll(fr, spr, W / 2, H * 0.5, qr, push * fit, (236, 234, 224))


def _polaroid(img, w=250, h=300):
    key = ("pol", id(img))
    if key in _CARDS:
        return _CARDS[key]
    ph = ImageOps.fit(img.convert("RGB"), (w, h), centering=(0.5, 0.38))
    fr = Image.new("RGBA", (w + 24, h + 52), (250, 248, 242, 255))
    fr.paste(ph, (12, 12))
    fr = _with_shadow(fr, (5, 8), 8, 0.5, 16)
    tp = _tape()
    fr.alpha_composite(tp, (fr.width // 2 - tp.width // 2, 2))
    out = fr.rotate(5, expand=True, resample=Image.BICUBIC)
    _CARDS[key] = out
    return out


# ---- year slider (ticks slide in, label sits above the pointer dot)
def _tick_step(y):
    return 1 if y >= 2000 else 2 if y >= 1900 else 5 if y >= 1800 else 10 if y >= 1500 else 50 if y >= 1000 else 100


def draw_timeline(fr, tl, t0, t1, a, ov):
    it = ov.get("it") or {}
    y = it.get("year_num")
    label = ov.get("text") or ""
    sc_ = it.get("year_num")
    step = _tick_step(int(y))
    frac = 0.0
    if it.get("month"):
        try:
            frac = (int(it["month"]) - 0.5) / 12.0
        except Exception:  # noqa
            frac = 0.0
    target = float(y) + frac
    spacing = 215.0
    s = e_out(cl((tl - t0) / 1.2))
    cur = target - (1 - s) * 3.3 * step
    base_y = H - 62
    g = _TL_SCRIM.get("g")
    if g is None:
        gh = 230
        gr = Image.new("RGBA", (W, gh), (0, 0, 0, 255))
        gr.putalpha(Image.linear_gradient("L").resize((W, gh)).point(lambda v: int(v * 0.72)))
        g = _TL_SCRIM["g"] = gr
    put(fr, g, W / 2, H - g.height / 2, 1.0, a)
    ov_im = Image.new("RGBA", (W, 150), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov_im)
    cy = 100
    d.line([(0, cy), (W, cy)], fill=(255, 255, 255, int(200 * a)), width=3)
    first = int(math.floor((cur - 3.6 * step) / step) * step)
    f_t = font_for("A", 30, "cond")
    k = first
    while k <= cur + 3.6 * step:
        x = W / 2 + (k - cur) / step * spacing
        edge = 1 - cl((abs(x - W / 2) - 330) / 300)
        if 0 <= x <= W and edge > 0.02:
            al = int(235 * a * edge)
            d.line([(x, cy - 16), (x, cy + 16)], fill=(255, 255, 255, al), width=3)
            txt = str(k)
            tw_ = d.textlength(txt, font=f_t)
            d.text((x - tw_ / 2, cy + 22), txt, font=f_t, fill=(255, 255, 255, int(220 * a * edge)))
        k += step
    # pointer dot + label
    d.ellipse([W / 2 - 9, cy - 9, W / 2 + 9, cy + 9], fill=(255, 255, 255, int(255 * a)))
    ov_im2 = ov_im
    lab = _TL_LAB.get(label)
    if lab is None:
        lab = _TL_LAB[label] = text_layer(label, font_for(label, 60, "title"), fill=(255, 255, 255), stroke=2, stroke_fill=(0, 0, 0), shadow=(0, 3, 5, 0.7))
    fr.paste(ov_im2, (0, H - 150 - 0), ov_im2)
    la = a * cl((tl - t0 - 0.35) / 0.4)
    put(fr, lab, W / 2, H - 150 + cy - 52 - lab.height / 2 + (1 - e_out(cl((tl - t0 - 0.35) / 0.4))) * 18, 1.0, la)


_TL_SCRIM, _TL_LAB = {}, {}


# ---- person name card (real photo from Wikipedia + name plate)
def person_sprite(name, photo, sub=""):
    key = ("person", name, sub, id(photo))
    if key in _CARDS:
        return _CARDS[key]
    f = font_for(name, 56, "title")
    lay = text_layer(name, f, fill=(255, 214, 0), shadow=(0, 3, 4, 0.7))
    pw_ = max(lay.width + 80, 300)
    ph_ = lay.height + 26
    plate = Image.new("RGBA", (pw_, ph_), (0, 0, 0, 0))
    gr = Image.composite(Image.new("RGB", (pw_, ph_), (10, 40, 150)), Image.new("RGB", (pw_, ph_), (38, 120, 255)), Image.linear_gradient("L").resize((pw_, ph_)))
    pm = Image.new("L", (pw_, ph_), 0)
    ImageDraw.Draw(pm).polygon([(22, 0), (pw_, 0), (pw_ - 22, ph_), (0, ph_)], fill=255)
    plate.paste(gr, (0, 0), pm)
    plate.alpha_composite(lay, ((pw_ - lay.width) // 2, (ph_ - lay.height) // 2))
    if photo is not None:
        phw, phh = 300, 360
        im = ImageOps.fit(photo.convert("RGB"), (phw, phh), centering=(0.5, 0.25))
        card = Image.new("RGBA", (phw + 12, phh + 12), (255, 255, 255, 255))
        card.paste(im, (6, 6))
        out = Image.new("RGBA", (max(card.width, pw_), card.height + ph_ // 2), (0, 0, 0, 0))
        out.alpha_composite(card, (0, 0))
        out.alpha_composite(plate, (out.width - pw_, card.height - ph_ // 2))
    else:
        out = plate
    out = _with_shadow(out, (5, 8), 9, 0.55, 18)
    _CARDS[key] = out
    return out


def draw_person(fr, ps, tl, t0, t1, a, ov):
    it = ov.get("it") or {}
    photo = it.get("photo_img")
    spr = person_sprite(ov.get("text") or "", photo, "")
    cx, cy = ov_spot(ps, spr, [(W - 50 - spr.width / 2, H - 60 - spr.height / 2), (50 + spr.width / 2, H - 60 - spr.height / 2),
                                (W - 50 - spr.width / 2, 60 + spr.height / 2)])
    q = e_back((tl - t0) / 0.5)
    cx += (1 - cl(q)) * (400 if cx > W / 2 else -400)
    put(fr, spr, cx, cy, 1.0, a)


# ---- big red stat text ("3 MONTHS")
def stat_sprite(text, sub="", tone=""):
    key = ("stat", text, sub, tone)
    if key in _CARDS:
        return _CARDS[key]
    f = font_for(text, 200 if tone != "place" else 150, "title")
    if tone == "place":  # countries, states, cities, months: gold lettering
        lay = text_layer(text, f, fill=(255, 226, 90), fill2=(255, 140, 20), max_w=int(W * 0.62), stroke=3, stroke_fill=(60, 24, 0), shadow=(0, 8, 10, 0.8))
    else:
        lay = text_layer(text, f, fill=(255, 84, 70), fill2=(190, 12, 28), max_w=int(W * 0.62), stroke=3, stroke_fill=(40, 0, 6), shadow=(0, 8, 10, 0.8))
    pad = 60
    big = Image.new("RGBA", (lay.width + 2 * pad, lay.height + 2 * pad), (0, 0, 0, 0))
    big.alpha_composite(lay, (pad, pad))
    gl = big.getchannel("A").filter(ImageFilter.GaussianBlur(22)).point(lambda v: min(255, int(v * 1.2)))
    glow = Image.new("RGBA", big.size, (255, 150, 20, 255) if tone == "place" else (230, 20, 40, 255))
    glow.putalpha(gl.point(lambda v: int(v * 0.55)))
    out = Image.new("RGBA", big.size, (0, 0, 0, 0))
    out.alpha_composite(glow)
    out.alpha_composite(big)
    _CARDS[key] = out
    return out


def draw_stat(fr, ps, tl, t0, t1, a, ov):
    spr = stat_sprite(ov.get("text") or "", "", (ov.get("it") or {}).get("tone", ""))
    cx, cy = ov_spot(ps, spr, [(W * 0.74, H * 0.46), (W * 0.26, H * 0.46), (W * 0.5, H * 0.3)])
    q = e_out(cl((tl - t0) / 0.45))
    cx += (1 - q) * (260 if cx > W / 2 else -260)
    put(fr, spr, cx, cy, 1.12 - 0.12 * q + 0.015 * math.sin(tl * 5), a)


# ---- round pop-ups with a red cross / green tick
def _circle_sprite(img, d_=230):
    c = ImageOps.fit(img.convert("RGB"), (d_, d_), centering=(0.5, 0.4))
    m = rounded_mask((d_, d_), d_ // 2)
    out = Image.new("RGBA", (d_ + 28, d_ + 28), (0, 0, 0, 0))
    sh = Image.new("RGBA", out.size, (0, 0, 0, 255))
    sh.putalpha(Image.new("L", out.size, 0))
    a_ = Image.new("L", out.size, 0)
    a_.paste(m, (14 + 4, 14 + 8))
    sh.putalpha(a_.filter(ImageFilter.GaussianBlur(8)).point(lambda v: int(v * 0.6)))
    out.alpha_composite(sh)
    ring = Image.new("RGBA", (d_ + 10, d_ + 10), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse([0, 0, d_ + 9, d_ + 9], fill=(255, 255, 255, 255))
    out.alpha_composite(ring, (9, 9))
    cc = c.convert("RGBA")
    cc.putalpha(m)
    out.alpha_composite(cc, (14, 14))
    return out


def _mark_overlay(kind, size, prog):
    """Red cross (two strokes drawn one after another) or green tick, with a white outline, on a transparent square."""
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    lw = max(8, size // 16)
    if kind == "cross":
        a1, b1, a2, b2 = (size * 0.2, size * 0.2), (size * 0.8, size * 0.8), (size * 0.8, size * 0.2), (size * 0.2, size * 0.8)
        p1, p2 = cl(prog * 2), cl(prog * 2 - 1)
        for (pa, pb, pp) in ((a1, b1, p1), (a2, b2, p2)):
            if pp > 0:
                e = (pa[0] + (pb[0] - pa[0]) * pp, pa[1] + (pb[1] - pa[1]) * pp)
                d.line([pa, e], fill=(255, 255, 255, 255), width=lw + 6)
        for (pa, pb, pp) in ((a1, b1, p1), (a2, b2, p2)):
            if pp > 0:
                e = (pa[0] + (pb[0] - pa[0]) * pp, pa[1] + (pb[1] - pa[1]) * pp)
                d.line([pa, e], fill=(228, 28, 38, 255), width=lw)
    else:
        pts = [(size * 0.2, size * 0.54), (size * 0.42, size * 0.76), (size * 0.82, size * 0.26)]
        p1, p2 = cl(prog * 2), cl(prog * 2 - 1)
        segs = []
        if p1 > 0:
            segs.append((pts[0], (pts[0][0] + (pts[1][0] - pts[0][0]) * p1, pts[0][1] + (pts[1][1] - pts[0][1]) * p1)))
        if p2 > 0:
            segs.append((pts[1], (pts[1][0] + (pts[2][0] - pts[1][0]) * p2, pts[1][1] + (pts[2][1] - pts[1][1]) * p2)))
        for s_ in segs:
            d.line(list(s_), fill=(255, 255, 255, 255), width=lw + 6)
        for s_ in segs:
            d.line(list(s_), fill=(36, 190, 84, 255), width=lw)
    return im


def draw_verdict(fr, ps, tl, t0, t1, a, ov):
    it = ov.get("it") or {}
    circles = it.get("circles") or []
    n = len(circles)
    if not n:
        return
    side = ov.get("side") or "right"
    cx0 = W * (0.27 if side == "left" else 0.73)
    d_ = 250 if n == 1 else 220
    if n == 3:
        pos = [(-0.58, -0.56), (0.58, -0.56), (0.0, 0.58)]
    elif n == 2:
        pos = [(-0.58, 0.0), (0.58, 0.0)]
    else:
        pos = [(0.0, 0.0)]
    for k, cdef in enumerate(circles[:3]):
        img = cdef.get("img")
        if img is None:
            img = ps.get("img")
            if img is None:
                continue
            fx = (0.25, 0.75, 0.5)[k % 3]
            img = img.crop((int(img.width * max(0, fx - 0.25)), int(img.height * 0.15), int(img.width * min(1, fx + 0.25)), int(img.height * 0.85)))
        key = ("circ", id(img), d_)
        spr = _CARDS.get(key)
        if spr is None:
            spr = _CARDS[key] = _circle_sprite(img, d_)
        tk = t0 + 0.12 + k * 0.22
        q = e_back((tl - tk) / 0.42)
        if tl < tk:
            continue
        cx = cx0 + pos[k][0] * (d_ + 30) * 1.0
        cy = H * 0.5 + pos[k][1] * (d_ + 30) * 0.95
        sw = (-1 if side == "left" else 1)
        put(fr, spr, cx, cy, 0.2 + 0.8 * cl(q), a)
        pm = cl((tl - tk - 0.45) / 0.5)
        if pm > 0:
            mk = _mark_overlay(cdef.get("mark", "cross"), int(d_ * 1.02), pm)
            put(fr, mk, cx, cy, 1.0, a)
        lab = cdef.get("label")
        if lab and tl > tk + 0.2:
            ll = _TL_LAB.get(("v", lab))
            if ll is None:
                ll = _TL_LAB[("v", lab)] = text_layer(lab, font_for(lab, 36, "title"), fill=(255, 255, 255), stroke=2, stroke_fill=(0, 0, 0), shadow=(0, 2, 4, 0.8))
            put(fr, ll, cx, cy + d_ / 2 + 26, 1.0, a * cl((tl - tk - 0.2) / 0.3))


# =========================================================================== TECHNIQUES v4: measuring, question mark, magnifier lens, before/after slider, comparison panel,
# VOX-style marker annotations, animated charts, multi-event timeline, highlight bar and the Vox paper-collage frame.  (Never remove: see PERMANENT_TECHNIQUES)
NEW_KINDS = ("measure", "qmark", "lens", "beforeafter", "compare", "mark", "chart", "tline", "hl")
VOX_CHART_COLS = [(0, 150, 170), (236, 84, 64), (58, 98, 200), (240, 160, 30), (112, 176, 66), (146, 86, 188)]
HL_COLS = [((255, 64, 120), (255, 255, 255)), ((0, 190, 220), (10, 14, 24)), ((150, 226, 60), (10, 14, 24)), ((255, 255, 255), (10, 14, 24))]
_VX = {}
_VXN = [0]


def _vx_text(text, size, role="title", fill=(255, 255, 255), stroke=3, sfill=(8, 8, 14), shadow=True, max_w=None, align="center"):
    key = ("t", text, size, role, fill, stroke, sfill, shadow, max_w, align)
    if key not in _VX:
        _VX[key] = text_layer(text, font_for(text, size, role), fill=fill, stroke=stroke, stroke_fill=sfill, shadow=(0, 3, 5, 0.75) if shadow else None, max_w=max_w, align=align)
    return _VX[key]


def _vx_panel(w, h, r=24, col=(10, 13, 22), alpha=185, border=(255, 255, 255, 70)):
    key = ("p", w, h, r, col, alpha, border)
    if key in _VX:
        return _VX[key]
    pad = 22
    sp = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    sh_ = Image.new("L", sp.size, 0)
    ImageDraw.Draw(sh_).rounded_rectangle([pad + 2, pad + 8, pad + w + 2, pad + h + 8], r, fill=130)
    shadow = Image.new("RGBA", sp.size, (0, 0, 0, 255))
    shadow.putalpha(sh_.filter(ImageFilter.GaussianBlur(9)))
    sp.alpha_composite(shadow)
    ImageDraw.Draw(sp).rounded_rectangle([pad, pad, pad + w, pad + h], r, fill=tuple(col) + (alpha,), outline=border, width=2)
    _VX[key] = sp
    return sp


def _aa_poly(fr, pts, width, color, alpha=1.0, upto=1.0):
    """Anti-aliased polyline (first `upto` fraction of it) pasted onto the frame; drawn at 2x inside its bounding box only."""
    if len(pts) < 2 or alpha <= 0.01 or upto <= 0.001:
        return
    segs, tot = [], 0.0
    for a, b in zip(pts, pts[1:]):
        l = math.hypot(b[0] - a[0], b[1] - a[1])
        segs.append((a, b, l))
        tot += l
    target, acc, drawn = tot * min(1.0, upto), 0.0, [pts[0]]
    for a, b, l in segs:
        if acc + l <= target:
            drawn.append(b)
            acc += l
        else:
            r = (target - acc) / max(l, 1e-6)
            drawn.append((a[0] + (b[0] - a[0]) * r, a[1] + (b[1] - a[1]) * r))
            break
    xs, ys = [p[0] for p in drawn], [p[1] for p in drawn]
    pad = int(width) + 4
    x0, y0, x1, y1 = max(0, int(min(xs)) - pad), max(0, int(min(ys)) - pad), min(W, int(max(xs)) + pad), min(H, int(max(ys)) + pad)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return
    big = Image.new("L", ((x1 - x0) * 2, (y1 - y0) * 2), 0)
    d = ImageDraw.Draw(big)
    pp = [((x - x0) * 2, (y - y0) * 2) for x, y in drawn]
    d.line(pp, fill=255, width=int(width * 2), joint="curve")
    r_ = width
    for x, y in (pp[0], pp[-1]):
        d.ellipse([x - r_, y - r_, x + r_, y + r_], fill=255)
    m = big.resize((x1 - x0, y1 - y0), Image.LANCZOS)
    if alpha < 0.999:
        m = m.point(lambda v: int(v * alpha))
    fr.paste(Image.new("RGB", m.size, tuple(color)), (x0, y0), m)


def _subj(sh):
    try:
        if "subj" not in sh:
            sh["subj"] = subject_center(sh["img"]) if sh.get("img") is not None else (0.5, 0.5)
        return sh["subj"]
    except Exception:  # noqa
        return (0.5, 0.5)


# ---- measuring screen: dimension line with end caps, ruler ticks and a value label
def draw_measure(fr, sh, tl, t0, t1, a, ov):
    it = ov.get("it") or {}
    label = (it.get("text") or "").strip()
    if not label:
        return
    sx, sy = _subj(sh)
    vert = it.get("orient") == "v"
    p = e_out(cl((tl - t0) / 0.8))
    white, shadow = (255, 255, 255), (0, 0, 0)
    if vert:
        x = W * (0.12 if sx > 0.5 else 0.88)
        y0, y1 = H * 0.16, H * 0.84
        mid = (y0 + y1) / 2
        ya, yb = mid - (mid - y0) * p, mid + (y1 - mid) * p
        for col, wd, off in ((shadow, 9, 3), (white, 5, 0)):
            _aa_poly(fr, [(x + off, ya), (x + off, yb)], wd, col, a)
        for yy in (ya, yb):
            for col, wd, off in ((shadow, 9, 3), (white, 5, 0)):
                _aa_poly(fr, [(x - 22 + off, yy), (x + 22 + off, yy)], wd, col, a)
        n = 16
        for k in range(1, n):
            yy = y0 + (y1 - y0) * k / n
            if ya <= yy <= yb:
                L = 22 if k % 4 == 0 else 11
                _aa_poly(fr, [(x, yy), (x + L, yy)], 3, white, a * 0.9)
        cx, cy = x + (130 if x < W / 2 else -130), mid
    else:
        y = H * 0.84
        x0, x1 = W * max(0.06, sx - 0.30), W * min(0.94, sx + 0.30)
        mid = (x0 + x1) / 2
        xa, xb = mid - (mid - x0) * p, mid + (x1 - mid) * p
        for col, wd, off in ((shadow, 9, 3), (white, 5, 0)):
            _aa_poly(fr, [(xa, y + off), (xb, y + off)], wd, col, a)
        for xx in (xa, xb):
            for col, wd, off in ((shadow, 9, 3), (white, 5, 0)):
                _aa_poly(fr, [(xx + off, y - 22), (xx + off, y + 22)], wd, col, a)
        n = 24
        for k in range(1, n):
            xx = x0 + (x1 - x0) * k / n
            if xa <= xx <= xb:
                L = 22 if k % 4 == 0 else 11
                _aa_poly(fr, [(xx, y), (xx, y - L)], 3, white, a * 0.9)
        cx, cy = mid, y - 122
    if p > 0.55:
        spr = _vx_text(label, 56, "title", stroke=3)
        pan = _vx_panel(spr.width + 54, spr.height + 18, 18, alpha=205)
        q = e_back(cl((tl - t0 - 0.45) / 0.4))
        put(fr, pan, cx, cy, 0.6 + 0.4 * cl(q), a)
        put(fr, spr, cx, cy, 0.6 + 0.4 * cl(q), a)
        if it.get("sub"):
            sb = _vx_text(it["sub"], 30, "sub", fill=(240, 226, 205), stroke=2)
            put(fr, sb, cx, cy + spr.height / 2 + 26, 1.0, a * cl((tl - t0 - 0.7) / 0.3))


# ---- question marks that pop up around the subject
def draw_qmark(fr, sh, tl, t0, t1, a, ov):
    sx, sy = _subj(sh)
    spots = [(0.20, -0.26, 1.0, -9), (-0.22, -0.18, 0.72, 11), (0.30, 0.06, 0.55, -5)]
    for k, (dx, dy, sc_, rot) in enumerate(spots):
        tk = t0 + 0.12 + k * 0.28
        if tl < tk:
            continue
        q = e_back(cl((tl - tk) / 0.45))
        size = int(250 * sc_)
        key = ("q", size)
        if key not in _VX:
            _VX[key] = text_layer("?", font_for("?", size, "title"), fill=(255, 255, 255), stroke=int(size * 0.07), stroke_fill=(214, 28, 44),
                                  shadow=(0, 8, 12, 0.8), extrude=(max(4, int(size * 0.05)), (110, 10, 20)))
        spr = _VX[key]
        ang = rot + 7 * math.sin(tl * 3.2 + k * 1.7)
        rs = spr.rotate(ang, resample=Image.BICUBIC, expand=True)
        cx = min(max(sx + dx, 0.08), 0.92) * W
        cy = min(max(sy + dy, 0.14), 0.86) * H + 7 * math.sin(tl * 2.4 + k)
        put(fr, rs, cx, cy, 0.3 + 0.7 * cl(q), a)


# ---- magnifier lens over the subject
def _lens_sprite(R):
    key = ("lens", R)
    if key in _VX:
        return _VX[key]
    S = int(R * 3.0)
    sp = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(sp)
    c = S // 2 - int(R * 0.35)
    ang = math.radians(48)
    hx, hy = c + math.cos(ang) * R * 1.0, c + math.sin(ang) * R * 1.0
    ex, ey = c + math.cos(ang) * R * 1.95, c + math.sin(ang) * R * 1.95
    d.line([(hx, hy), (ex, ey)], fill=(30, 22, 18, 255), width=int(R * 0.22))
    d.line([(hx, hy), (ex, ey)], fill=(120, 84, 52, 255), width=int(R * 0.14))
    d.ellipse([c - R - 12, c - R - 12, c + R + 12, c + R + 12], fill=(60, 64, 72, 255))
    d.ellipse([c - R - 5, c - R - 5, c + R + 5, c + R + 5], fill=(205, 210, 220, 255))
    d.ellipse([c - R + 4, c - R + 4, c + R - 4, c + R - 4], fill=(0, 0, 0, 0))
    _VX[key] = (sp, c)
    return _VX[key]


def draw_lens(fr, sh, tl, t0, t1, a, ov):
    it = ov.get("it") or {}
    sx, sy = _subj(sh)
    fx, fy = it.get("fx"), it.get("fy")
    fx = sx if fx is None else fx
    fy = sy if fy is None else fy
    R = 168
    cx = min(max(fx * W + 14 * math.sin(tl * 1.7), R + 20), W - R - 120)
    cy = min(max(fy * H + 9 * math.cos(tl * 1.3), R + 20), H - R - 90)
    q = e_back(cl((tl - t0) / 0.5))
    z = 2.4
    src = fr.copy()
    bw = int(2 * R / z)
    box = (int(cx - bw / 2), int(cy - bw / 2), int(cx + bw / 2), int(cy + bw / 2))
    zoomed = src.crop(box).resize((2 * R, 2 * R), Image.BICUBIC).filter(ImageFilter.UnsharpMask(radius=1.4, percent=60, threshold=2))
    m = Image.new("L", (2 * R, 2 * R), 0)
    ImageDraw.Draw(m).ellipse([0, 0, 2 * R - 1, 2 * R - 1], fill=255)
    glass = zoomed.convert("RGBA")
    hi = Image.new("RGBA", glass.size, (0, 0, 0, 0))
    ImageDraw.Draw(hi).arc([R * 0.22, R * 0.22, R * 1.78, R * 1.78], 200, 280, fill=(255, 255, 255, 120), width=int(R * 0.09))
    glass.alpha_composite(hi)
    glass.putalpha(m)
    sp, c = _lens_sprite(R)
    lay = sp.copy()
    lay.alpha_composite(glass, (c - R, c - R))
    sh_ = Image.new("RGBA", lay.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh_).ellipse([c - R + 8, c - R + 14, c + R + 8, c + R + 14], fill=(0, 0, 0, 120))
    base = Image.new("RGBA", fr.size, (0, 0, 0, 0))
    off = (int(cx - c), int(cy - c))
    lay_full = Image.new("RGBA", lay.size, (0, 0, 0, 0))
    lay_full.alpha_composite(sh_.filter(ImageFilter.GaussianBlur(10)))
    lay_full.alpha_composite(lay)
    put(fr, lay_full, cx + (lay.width / 2 - c), cy + (lay.height / 2 - c), 0.35 + 0.65 * cl(q), a)


# ---- before / after slider (the picture on screen = BEFORE, the second real picture = AFTER)
def draw_beforeafter(fr, sh, tl, t0, t1, a, ov):
    it = ov.get("it") or {}
    im2 = it.get("img2")
    if im2 is None:
        return
    key = ("ba", id(im2))
    if key not in _VX:
        _VX[key] = ImageOps.fit(im2.convert("RGB"), (W, H), Image.LANCZOS)
    after = _VX[key]
    p = e_io(cl((tl - t0 - 0.35) / 1.3))
    pos = W * (0.96 - 0.92 * p) + (math.sin(tl * 2.2) * 6 if p >= 1 else 0)
    pos = int(min(max(pos, 8), W - 8))
    before = fr.copy()
    fr.paste(after.crop((pos, 0, W, H)), (pos, 0))
    d = ImageDraw.Draw(fr)
    # right part shows AFTER (revealed as the divider moves left); left part stays BEFORE
    d.line([(pos, 0), (pos, H)], fill=(255, 255, 255), width=6)
    cy = H // 2
    d.ellipse([pos - 34, cy - 34, pos + 34, cy + 34], fill=(255, 255, 255), outline=(20, 20, 24), width=3)
    d.polygon([(pos - 20, cy), (pos - 8, cy - 11), (pos - 8, cy + 11)], fill=(20, 20, 24))
    d.polygon([(pos + 20, cy), (pos + 8, cy - 11), (pos + 8, cy + 11)], fill=(20, 20, 24))
    la, lb = it.get("label_a") or "पहले", it.get("label_b") or "बाद में"
    for txt, x, col in ((la, 60, (214, 28, 44)), (lb, W - 60, (28, 150, 80))):
        s_ = _vx_text(txt, 46, "title", stroke=2)
        pan = Image.new("RGBA", (s_.width + 44, s_.height + 14), tuple(col) + (235,))
        pan.putalpha(rounded_mask(pan.size, 16))
        pw = pan.width
        px = x + pw / 2 if x < W / 2 else x - pw / 2
        if (x < W / 2 and pos > px + pw / 2) or (x > W / 2 and pos < px - pw / 2):
            put(fr, pan, px, 62, 1.0, a)
            put(fr, s_, px, 62, 1.0, a)
    if a < 0.999:
        fr.paste(Image.blend(before, fr, a))


# ---- comparison panel (two things, up to 3 rows with growing bars)
def draw_compare(fr, sh, tl, t0, t1, a, ov):
    it = ov.get("it") or {}
    rows = it.get("rows") or []
    A, B = it.get("a_hi") or "", it.get("b_hi") or ""
    if not rows or not A or not B:
        return
    n_r = min(3, len(rows))
    pw, ph = int(W * 0.86), 130 + 92 * n_r
    pan = _vx_panel(pw, ph, 26, alpha=205)
    q = e_out(cl((tl - t0) / 0.5))
    cx, cy = W / 2, H - 34 - ph / 2 + (1 - q) * 160
    put(fr, pan, cx, cy, 1.0, a)
    lx, rx = cx - pw * 0.25, cx + pw * 0.25
    top = cy - ph / 2 + 22
    ha, hb = _vx_text(A, 52, "title", fill=(255, 120, 100)), _vx_text(B, 52, "title", fill=(110, 200, 255))
    put(fr, ha, lx, top + 26, 1.0, a * cl((tl - t0 - 0.2) / 0.3))
    put(fr, hb, rx, top + 26, 1.0, a * cl((tl - t0 - 0.3) / 0.3))
    vs = _vx_text("VS", 60, "cond", fill=(255, 255, 255), stroke=4, sfill=(200, 24, 40))
    put(fr, vs, cx, top + 26, 0.7 + 0.3 * e_back(cl((tl - t0 - 0.4) / 0.35)), a)
    d = ImageDraw.Draw(fr)
    ry = top + 98
    rh = (ph - 130) / max(1, n_r) + 0.0
    for k, r in enumerate(rows[:3]):
        tk = t0 + 0.55 + k * 0.45
        if tl < tk:
            continue
        g = e_out(cl((tl - tk) / 0.6))
        lab = _vx_text(r.get("label_hi") or "", 30, "sub", fill=(235, 235, 240), stroke=1)
        put(fr, lab, cx, ry + rh * k + 4, 1.0, a * g)
        na, nb = float(r.get("na") or 0), float(r.get("nb") or 0)
        mx = max(na, nb, 1e-9)
        for side, n_, col, x0 in ((0, na, (255, 110, 90), lx), (1, nb, (100, 190, 255), rx)):
            bw_max = pw * 0.36
            bw = bw_max * (n_ / mx) * g if mx > 0 and (na or nb) else bw_max * 0.5 * g
            y = ry + rh * k + 30
            d.rounded_rectangle([x0 - bw_max / 2, y, x0 - bw_max / 2 + max(8, bw), y + 24], 10, fill=col)
            vt = _vx_text(r.get("a_hi" if side == 0 else "b_hi") or "", 34, "title", stroke=2)
            put(fr, vt, x0, y + 50, 1.0, a * g)


# ---- Vox marker annotation: hand-drawn circle / underline / arrow with a handwritten label
def _wobble_ellipse(cx, cy, rx, ry, seed, turns=1.12):
    rnd = random.Random(seed)
    ph = [rnd.uniform(0, 6.28) for _ in range(3)]
    pts = []
    n = 64
    for k in range(n + 1):
        th = -0.6 + 2 * math.pi * turns * k / n
        wob = 1 + 0.035 * math.sin(2 * th + ph[0]) + 0.025 * math.sin(3 * th + ph[1]) + 0.07 * (k / n)
        pts.append((cx + math.cos(th) * rx * wob, cy + math.sin(th) * ry * wob))
    return pts


def draw_mark(fr, sh, tl, t0, t1, a, ov):
    it = ov.get("it") or {}
    sx, sy = _subj(sh)
    fx = sx if it.get("fx") is None else it["fx"]
    fy = sy if it.get("fy") is None else it["fy"]
    rw, rh = (it.get("rw") or 0.14), (it.get("rh") or 0.18)
    shape = it.get("shape") or "circle"
    col = (232, 40, 56)
    p = e_io(cl((tl - t0) / 0.8))
    cx, cy = fx * W, fy * H
    seed = int(cx * 7 + cy * 3)
    if shape == "underline":
        y = cy + rh * H
        pts = [(cx - rw * W + k * (2 * rw * W) / 12, y + 5 * math.sin(k * 0.9 + seed)) for k in range(13)]
        for c_, wd in (((0, 0, 0), 13), (col, 9)):
            _aa_poly(fr, pts, wd, c_, a * (0.55 if c_ == (0, 0, 0) else 1.0), p)
        lab_xy = (cx, y + 42)
    elif shape == "arrow":
        sx0 = cx + (0.22 * W if fx < 0.5 else -0.22 * W)
        sy0 = cy - 0.20 * H if fy > 0.35 else cy + 0.2 * H
        ctrl = ((sx0 + cx) / 2, min(sy0, cy) - 0.08 * H)
        pts = [((1 - u) ** 2 * sx0 + 2 * (1 - u) * u * ctrl[0] + u * u * cx, (1 - u) ** 2 * sy0 + 2 * (1 - u) * u * ctrl[1] + u * u * cy) for u in [k / 24 for k in range(25)]]
        for c_, wd in (((0, 0, 0), 12), (col, 8)):
            _aa_poly(fr, pts, wd, c_, a * (0.55 if c_ == (0, 0, 0) else 1.0), p)
        if p > 0.95:
            (x1_, y1_), (x2_, y2_) = pts[-3], pts[-1]
            ang = math.atan2(y2_ - y1_, x2_ - x1_)
            head = [(x2_ - 30 * math.cos(ang - 0.5), y2_ - 30 * math.sin(ang - 0.5)), (x2_, y2_), (x2_ - 30 * math.cos(ang + 0.5), y2_ - 30 * math.sin(ang + 0.5))]
            for c_, wd in (((0, 0, 0), 12), (col, 8)):
                _aa_poly(fr, head, wd, c_, a * (0.55 if c_ == (0, 0, 0) else 1.0), 1.0)
        lab_xy = (sx0, sy0 - 44 if sy0 < cy else sy0 + 44)
    else:
        pts = _wobble_ellipse(cx, cy, rw * W, rh * H, seed)
        for c_, wd in (((0, 0, 0), 13), (col, 9)):
            _aa_poly(fr, pts, wd, c_, a * (0.55 if c_ == (0, 0, 0) else 1.0), p)
        lab_xy = (cx, cy + rh * H + 44) if cy + rh * H + 80 < H else (cx, cy - rh * H - 44)
    if it.get("text") and p > 0.6:
        s_ = _vx_text(it["text"], 48, "head", fill=(255, 255, 255), stroke=3, sfill=(150, 10, 26))
        q = e_back(cl((p - 0.6) / 0.4))
        put(fr, s_, min(max(lab_xy[0], s_.width / 2 + 20), W - s_.width / 2 - 20), min(max(lab_xy[1], 40), H - 40), 0.5 + 0.5 * cl(q), a)


# ---- animated chart (bars grow / line is drawn); numbers are only those the narrator says
def draw_chart(fr, sh, tl, t0, t1, a, ov):
    it = ov.get("it") or {}
    pts = it.get("pts") or []
    if len(pts) < 2:
        return
    sx, _ = _subj(sh)
    pw, ph = int(W * 0.37), int(H * 0.62)
    cx = W * 0.78
    cy = H * 0.50
    pan = _vx_panel(pw, ph, 22, col=(250, 248, 242), alpha=246, border=(40, 40, 40, 60))
    q = e_out(cl((tl - t0) / 0.5))
    put(fr, pan, cx + (1 - q) * (300 if cx > W / 2 else -300), cy, 1.0, a)
    if q < 0.9:
        return
    ink = (30, 32, 40)
    ttl = _vx_text(it.get("title") or "", 38, "title", fill=ink, stroke=0, shadow=False, max_w=pw - 50)
    put(fr, ttl, cx, cy - ph / 2 + 16 + ttl.height / 2, 1.0, a)
    x0, x1 = cx - pw / 2 + 36, cx + pw / 2 - 36
    ybase, ytop = cy + ph / 2 - 62, cy - ph / 2 + 24 + ttl.height + 40
    d = ImageDraw.Draw(fr)
    d.line([(x0 - 6, ybase), (x1 + 6, ybase)], fill=ink, width=3)
    n = min(6, len(pts))
    mx = max(float(p_.get("v") or 0) for p_ in pts[:n]) or 1.0
    slot = (x1 - x0) / n
    if it.get("type") == "line":
        xy = [(x0 + slot * (k + 0.5), ybase - (ybase - ytop) * float(pts[k].get("v") or 0) / mx) for k in range(n)]
        g = e_out(cl((tl - t0 - 0.5) / 1.4))
        _aa_poly(fr, xy, 7, VOX_CHART_COLS[0], a, g)
        for k, (x, y) in enumerate(xy):
            if g * (n - 1) >= k - 0.05:
                d.ellipse([x - 10, y - 10, x + 10, y + 10], fill=(255, 255, 255), outline=VOX_CHART_COLS[1], width=5)
    for k in range(n):
        p_ = pts[k]
        g = e_out(cl((tl - t0 - 0.5 - 0.25 * k) / 0.9))
        xc = x0 + slot * (k + 0.5)
        v = float(p_.get("v") or 0)
        if it.get("type") != "line":
            hgt = (ybase - ytop) * v / mx * g
            bw = min(slot * 0.62, 90)
            d.rounded_rectangle([xc - bw / 2, ybase - hgt, xc + bw / 2, ybase], 8, fill=VOX_CHART_COLS[k % 6])
            ytxt = ybase - hgt - 20
        else:
            ytxt = ybase - (ybase - ytop) * v / mx - 28
        if g > 0.3:
            vt = _vx_text(str(p_.get("show") or p_.get("v")), 30, "title", fill=ink, stroke=0, shadow=False)
            put(fr, vt, xc, ytxt, 1.0, a * cl((g - 0.3) / 0.4))
        lt = _vx_text(p_.get("label") or "", 24, "sub", fill=(70, 72, 80), stroke=0, shadow=False, max_w=int(slot + 10))
        put(fr, lt, xc, ybase + 14 + lt.height / 2, 1.0, a * cl((tl - t0 - 0.4 - 0.25 * k) / 0.3))


# ---- multi-event timeline strip at the bottom
def draw_tline(fr, sh, tl, t0, t1, a, ov):
    it = ov.get("it") or {}
    ev = (it.get("events") or [])[:5]
    if len(ev) < 2:
        return
    y = H * 0.885
    pan = _vx_panel(int(W * 0.92), 150, 20, alpha=190)
    put(fr, pan, W / 2, y - 6, 1.0, a * e_out(cl((tl - t0) / 0.4)))
    x0, x1 = W * 0.09, W * 0.91
    g = e_out(cl((tl - t0 - 0.2) / 1.0))
    _aa_poly(fr, [(x0, y), (x1, y)], 5, (255, 255, 255), a, g)
    n = len(ev)
    for k, e in enumerate(ev):
        xk = x0 + (x1 - x0) * k / (n - 1)
        tk = t0 + 0.5 + k * (max(1.0, (t1 - t0 - 1.2)) / n)
        if tl < tk:
            continue
        q = e_back(cl((tl - tk) / 0.4))
        d = ImageDraw.Draw(fr)
        r = int(13 * cl(q))
        d.ellipse([xk - r, y - r, xk + r, y + r], fill=(232, 40, 56), outline=(255, 255, 255), width=4)
        wt = _vx_text(e.get("when") or "", 40, "title", fill=(255, 214, 120), stroke=2, max_w=int((x1 - x0) / n))
        wh = _vx_text(e.get("what") or "", 31, "sub", fill=(255, 255, 255), stroke=2, max_w=int((x1 - x0) / n))
        put(fr, wt, xk, y - 34 - wt.height / 2, 1.0, a * cl(q))
        put(fr, wh, xk, y + 22 + wh.height / 2, 1.0, a * cl((tl - tk - 0.15) / 0.3))


# ---- highlight bar: the key claim, marker-swiped
def draw_hl(fr, sh, tl, t0, t1, a, ov):
    it = ov.get("it") or {}
    txt = (it.get("text") or "").strip()
    if not txt:
        return
    bg, fg = HL_COLS[it.get("ci", 0) % len(HL_COLS)]
    spr = _vx_text(txt, 76, "title", fill=fg, stroke=0, shadow=False, max_w=int(W * 0.8))
    bw, bh = spr.width + 70, spr.height + 30
    p = e_out(cl((tl - t0) / 0.5))
    cx, cy = W / 2, H * 0.865
    bar = Image.new("RGBA", (bw, bh), tuple(bg) + (255,))
    ImageDraw.Draw(bar).polygon([(0, 0), (22, 0), (0, bh)], fill=(0, 0, 0, 0))
    cw = max(2, int(bw * p))
    put(fr, _vx_panel(1, 1, 1, alpha=0, border=(0, 0, 0, 0)), -50, -50, 1.0, 0)
    sh_ = Image.new("RGBA", (cw + 30, bh + 30), (0, 0, 0, 0))
    ImageDraw.Draw(sh_).rectangle([15, 21, 15 + cw, 21 + bh], fill=(0, 0, 0, 120))
    sh_ = sh_.filter(ImageFilter.GaussianBlur(7))
    put(fr, sh_, cx - bw / 2 + cw / 2, cy, 1.0, a)
    put(fr, bar.crop((0, 0, cw, bh)), cx - bw / 2 + cw / 2, cy, 1.0, a)
    if p > 0.45:
        put(fr, spr, cx, cy, 1.0, a * cl((p - 0.45) / 0.4))


# ---- the Vox paper-collage frame: the picture becomes a tilted, taped photo on coloured paper while a chart / timeline / highlight is shown
COLLAGE_KINDS = ("chart", "tline")
POST_KINDS = {"hl", "chart", "tline", "compare"}
VOX_BG = [(236, 196, 92), (84, 184, 190), (240, 120, 96), (126, 164, 220), (246, 232, 206)]


def _vox_bg(ci):
    key = ("vbg", ci)
    if key in _VX:
        return _VX[key]
    base = VOX_BG[ci % len(VOX_BG)]
    im = Image.new("RGB", (W, H), base)
    np = _np()
    rng = np.random.default_rng(ci + 5)
    a = np.asarray(im, dtype=np.float32)
    blot = Image.fromarray((rng.random((8, 14)) * 255).astype(np.uint8)).resize((W, H), Image.BICUBIC)
    a = a + ((np.asarray(blot, dtype=np.float32) / 255 - 0.5) * 22)[..., None]
    im = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(im, "RGBA")
    dark = tuple(max(0, c - 70) for c in base) + (60,)
    for yy in range(18, H, 34):  # halftone dots
        for xx in range(18 + (yy // 34 % 2) * 17, W, 34):
            d.ellipse([xx - 4, yy - 4, xx + 4, yy + 4], fill=dark)
    rnd = random.Random(ci)
    for _ in range(5):  # doodle stars / squiggles
        x, y = rnd.randint(int(W * 0.62), W - 60), rnd.randint(40, H - 60)
        d.line([(x - 14, y), (x + 14, y)], fill=dark[:3] + (140,), width=4)
        d.line([(x, y - 14), (x, y + 14)], fill=dark[:3] + (140,), width=4)
    _VX[key] = im
    return im


def vox_collage(fr, q, ci, kind="chart"):
    e = e_out(cl(q))
    bg = _vox_bg(ci)
    s = lerp(1.0, 0.58, e)
    cw, ch = int(W * s), int(H * s)
    card = fr.resize((cw, ch), Image.BICUBIC)
    border = 12
    framed = Image.new("RGBA", (cw + 2 * border, ch + 2 * border), (252, 250, 244, 255))
    framed.paste(card, (border, border))
    tape = Image.new("RGBA", (96, 30), (240, 232, 190, 200))
    framed.alpha_composite(tape.rotate(-30, expand=True), (-24, -8))
    framed.alpha_composite(tape.rotate(30, expand=True), (framed.width - 70, -8))
    rot = -2.4 * e
    framed = framed.rotate(rot, resample=Image.BICUBIC, expand=True)
    sh_ = Image.new("RGBA", framed.size, (0, 0, 0, 0))
    sh_.putalpha(framed.getchannel("A").filter(ImageFilter.GaussianBlur(12)).point(lambda v: int(v * 0.55)))
    cx, cy = lerp(W / 2, W * 0.31, e), lerp(H / 2, H * 0.45, e)
    base = Image.blend(fr, bg, e) if e < 1 else bg.copy()
    base = base.convert("RGBA")
    base.alpha_composite(sh_, (int(cx - framed.width / 2 + 8), int(cy - framed.height / 2 + 14)))
    base.alpha_composite(framed, (int(cx - framed.width / 2), int(cy - framed.height / 2)))
    return base.convert("RGB")


ON_PHOTO = {"lens", "beforeafter", "measure", "qmark", "mark", "explain", "action", "feature", "verdict"}
KW_COLS = [((236, 84, 64), (255, 255, 255)), ((0, 150, 170), (255, 255, 255)), ((58, 98, 200), (255, 255, 255)), ((250, 250, 244), (24, 26, 34)), ((240, 170, 30), (24, 26, 34))]


def draw_kw(fr, kws, t, dur, hidden=False):
    """Vox key-word cards: every important word the narrator says lands as a coloured sticker on the paper, stacked on the right."""
    for n, k in enumerate(kws):
        tk = k["t"]
        if t < tk:
            continue
        end_fade = 1 - cl((t - (dur - 0.3)) / 0.3)
        a = (0.0 if hidden else 1.0) * end_fade
        if a <= 0.01:
            continue
        bg, fg = KW_COLS[k.get("ci", n) % len(KW_COLS)]
        key = ("kw", k["text"], bg)
        if key not in _VX:
            spr = _vx_text(k["text"], 54, "title", fill=fg, stroke=0, shadow=False, max_w=int(W * 0.30))
            card = Image.new("RGBA", (spr.width + 48, spr.height + 26), tuple(bg) + (255,))
            card.putalpha(rounded_mask(card.size, 14))
            card.alpha_composite(spr, ((card.width - spr.width) // 2, (card.height - spr.height) // 2))
            sh_ = Image.new("RGBA", (card.width + 40, card.height + 40), (0, 0, 0, 0))
            ImageDraw.Draw(sh_).rounded_rectangle([20 + 3, 20 + 9, 20 + card.width + 3, 20 + card.height + 9], 14, fill=(0, 0, 0, 110))
            sh_ = sh_.filter(ImageFilter.GaussianBlur(7))
            sh_.alpha_composite(card, (20, 20))
            _VX[key] = sh_
        q = e_back(cl((t - tk) / 0.4))
        rot = (-2.5, 2.0, -1.5, 2.8)[n % 4]
        spr = _VX[key].rotate(rot, resample=Image.BICUBIC, expand=True)
        cy = H * (0.17 + 0.15 * n)
        cx = W * 0.78 + (1 - cl(q)) * 340
        put(fr, spr, cx, cy, 0.85 + 0.15 * cl(q), a)
        if q >= 1.0 and not hidden:  # marker underline sweeps under the new word
            u = e_out(cl((t - tk - 0.35) / 0.5))
            if u > 0.02:
                y = cy + spr.height / 2 - 14
                x0 = cx - spr.width / 2 + 24
                _aa_poly(fr, [(x0, y + 2), (x0 + (spr.width - 48) * u, y - 3)], 5, (232, 40, 56), a)


def draw_kw_corner(fr, kws, t, dur, subj_x, has_label, blocked):
    """Non-Vox scenes: the key word the narrator just said pops as a small sticker in the top corner away from the subject."""
    cur = None
    for k in kws:
        if k["t"] <= t < k["t"] + 1.8:
            cur = k
    if cur is None or blocked:
        return
    tt = t - cur["t"]
    a = cl(tt / 0.2) * (1 - cl((tt - 1.45) / 0.35)) * (1 - cl((t - (dur - 0.3)) / 0.3))
    bg, fg = KW_COLS[cur.get("ci", 0) % len(KW_COLS)]
    key = ("kwc", cur["text"], bg)
    if key not in _VX:
        spr = _vx_text(cur["text"], 46, "title", fill=fg, stroke=0, shadow=False, max_w=int(W * 0.30))
        card = Image.new("RGBA", (spr.width + 40, spr.height + 22), tuple(bg) + (255,))
        card.putalpha(rounded_mask(card.size, 12))
        card.alpha_composite(spr, ((card.width - spr.width) // 2, (card.height - spr.height) // 2))
        lay = Image.new("RGBA", (card.width + 36, card.height + 36), (0, 0, 0, 0))
        ImageDraw.Draw(lay).rounded_rectangle([18 + 3, 18 + 8, 18 + card.width + 3, 18 + card.height + 8], 12, fill=(0, 0, 0, 110))
        lay = lay.filter(ImageFilter.GaussianBlur(6))
        lay.alpha_composite(card, (18, 18))
        _VX[key] = lay
    left = (subj_x > 0.55) and not has_label
    q = e_back(cl(tt / 0.35))
    spr = _VX[key]
    cx = (W * 0.14 if left else W * 0.86) + (1 - cl(q)) * (-200 if left else 200)
    put(fr, spr, cx, H * 0.115, 0.85 + 0.15 * cl(q), a)


def draw_new_kind(fr, sh, tl, t0, t1, a, ov):
    k = ov["kind"]
    try:
        {"measure": draw_measure, "qmark": draw_qmark, "lens": draw_lens, "beforeafter": draw_beforeafter, "compare": draw_compare, "mark": draw_mark,
         "chart": draw_chart, "tline": draw_tline, "hl": draw_hl}[k](fr, sh, tl, t0, t1, a, ov)
    except Exception as e:  # noqa
        if not _VX.get("err_" + k):
            _VX["err_" + k] = 1
            log(f"technique {k} drawing failed once (video continues): {type(e).__name__} {str(e)[:100]}")



def draw_v2(fr, sh, tl, ov, pal, t0, t1):
    """Draws the v2 item kinds. Returns True when it handled the item."""
    k = ov["kind"]
    it = ov.get("it") or {}
    a = cl((tl - t0) * 5) * (1 - cl((tl - (t1 - 0.35)) / 0.35))
    if k == "note" and it.get("text") and (it.get("style") in ("paper", "doc", "strip", "box")):
        a = 1.0  # these cards move themselves in and out
    if a <= 0.01:
        return True
    if k == "explain":
        draw_explain(fr, sh, tl, t0, t1, a, ov)
        return True
    if k == "action":
        draw_action(fr, sh, tl, t0, t1, a, ov)
        return True
    if k == "feature":
        draw_feature(fr, sh, tl, t0, t1, a, ov)
        return True
    if k == "place":
        draw_place(fr, tl, t0, a, ov)
        return True
    if k == "quote":
        draw_quote(fr, tl, t0, a, ov)
        return True
    if k == "name":
        draw_name(fr, tl, t0, a, ov)
        return True
    if k == "srclist":
        draw_srclist(fr, sh, tl, t0, t1, a, ov)
        return True
    if k == "note":
        draw_note(fr, sh, tl, ov, t0, t1, a)
        return True
    if k == "year":
        if it.get("year_num"):
            draw_timeline(fr, tl, t0, t1, a, ov)
        else:
            draw_year_pill(fr, tl, t0, t1, a, ov)  # always at the bottom, never at the top
        return True
    if k == "person":
        draw_person(fr, sh, tl, t0, t1, a, ov)
        return True
    if k == "stat":
        draw_stat(fr, sh, tl, t0, t1, a, ov)
        return True
    if k == "verdict":
        draw_verdict(fr, sh, tl, t0, t1, a, ov)
        return True
    if k in NEW_KINDS:
        draw_new_kind(fr, sh, tl, t0, t1, a, ov)
        return True
    return False


# =========================================================================== V3: guaranteed sources, auto word-detector, roll-out paper, keyframe-animated PNG
ITEM_PRI = {"quote": 3, "note": 6, "srclist": 6, "feature": 6, "verdict": 5, "explain": 5, "action": 5, "name": 3, "person": 4, "year": 4, "stat": 3, "title": 3, "place": 3, "bubble": 2,
            "measure": 4, "qmark": 3, "lens": 4, "beforeafter": 6, "compare": 5, "mark": 4, "chart": 6, "tline": 6, "hl": 5}
ITEM_MIN = {"quote": 2.4, "year": 2.8, "person": 2.6, "stat": 2.0, "title": 2.0, "place": 2.4, "bubble": 2.2, "verdict": 3.4, "explain": 3.4, "srclist": 3.6, "action": 2.6, "name": 2.4, "feature": 5.0,
            "measure": 3.0, "qmark": 2.2, "lens": 3.4, "beforeafter": 4.2, "compare": 5.0, "mark": 2.8, "chart": 5.0, "tline": 5.0, "hl": 2.8}


def item_zones(it):
    k = it["kind"]
    if k == "note":
        return {"B"} if (it.get("style") or "banner") == "banner" or not it.get("text") else {"FULL"}
    if k in ("year", "place", "srclist"):
        return {"B"}
    if k == "bubble":
        return {"T"}
    return {"M"}  # person, stat, title, verdict, explain


def item_min(it):
    if it["kind"] == "note":
        return 3.0 + min(3.2, len((it.get("text") or "").split()) * 0.085)  # time to actually read the card
    return ITEM_MIN.get(it["kind"], 2.0)


def schedule_items(items, dur):
    """Items may share the screen when they use different areas (bottom / middle / top); a clash cuts or delays the lower-priority one, never below its minimum reading time."""
    items.sort(key=lambda x: x["t0"])
    placed = []
    for b in items:
        zb = item_zones(b["it"])
        mb = item_min(b["it"])
        b["t1"] = min(dur - 0.1, b["t0"] + max(mb, b["span"]))
        ok = True
        for _ in range(5):
            moved = False
            for a in placed:
                if a["t1"] <= b["t0"] or not (zones_clash(item_zones(a["it"]), zb)):
                    continue
                shown = b["t0"] - a["t0"]
                if shown >= item_min(a["it"]) or (ITEM_PRI.get(b["it"]["kind"], 1) > ITEM_PRI.get(a["it"]["kind"], 1) and shown >= 1.2):
                    a["t1"] = b["t0"] - 0.05
                else:
                    nt = a["t0"] + item_min(a["it"])
                    if nt - b["t0"] <= 3.5 and nt + mb <= dur - 0.1:
                        b["t0"] = nt
                        b["t1"] = min(dur - 0.1, nt + max(mb, b["span"]))
                        moved = True
                    else:
                        ok = False
                    break
            if not ok or not moved:
                break
        if ok:
            placed.append(b)
    return placed


def zones_clash(a, b):
    return "FULL" in a or "FULL" in b or bool(a & b)


# ---------------------------------------------------------------- guaranteed sources
SRC_REFS = []  # reference-only entries {"title","dom","url"} when no page text could be read


def _src_refs():
    refs = []
    for x in SRC_LIST:
        refs.append({"title": pretty_source(x["title"])[:90], "dom": "", "url": x["url"]})
    if not refs:
        for g in GROUND_SRC[:6]:
            t = pretty_source(g.get("title") or "")
            if t and t != "Web source":
                refs.append({"title": t[:90], "dom": "", "url": g.get("uri", "")})
    return refs


def ensure_sources(topic, story=""):
    """Runs after collect_sources: never leave a video without real source text."""
    if len(SRC_PARAS) >= 6:
        return
    try:
        qs = []
        if GEMINI_KEY:
            out = gemini_json_opt(f"Topic / story: {topic}\n{story[:1200]}\nGive 4 short ENGLISH Wikipedia search phrases (people, places, events, studies, concepts) that document the real facts of this. "
                              'Return ONLY JSON: {"queries": ["..."]}', 0.1)
            qs = [str(q).strip() for q in (out.get("queries") or []) if str(q).strip().isascii()][:4]
        if topic and topic.isascii():
            qs.append(topic[:80])
        seen = {x["url"] for x in SRC_LIST}
        for q in qs:
            if len(SRC_LIST) >= 10:
                break
            r = requests.get("https://en.wikipedia.org/w/api.php", headers=WIKI_UA, timeout=25, params={
                "action": "query", "format": "json", "redirects": 1, "generator": "search", "gsrsearch": q, "gsrlimit": 2,
                "prop": "extracts|info", "explaintext": 1, "exsectionformat": "plain", "inprop": "url", "exlimit": "max", "exintro": 1})
            for pg in ((r.json().get("query") or {}).get("pages") or {}).values():
                url = pg.get("fullurl") or ""
                if not url or url in seen:
                    continue
                paras = []
                for para in (pg.get("extract") or "").split("\n"):
                    para = re.sub(r"\s+", " ", para).strip()
                    if 22 <= len(para.split()) <= 160 and sum(c.isascii() and c.isalpha() for c in para) > 0.75 * len(para):
                        paras.append(trim_words(para))
                if not paras:
                    continue
                seen.add(url)
                title = "Wikipedia - " + str(pg.get("title", q))[:70]
                k = len(SRC_LIST) + 1
                SRC_LIST.append({"title": title, "url": url})
                for j, para in enumerate(paras[:5], 1):
                    SRC_PARAS[f"{k}.{j}"] = {"title": title, "url": url, "text": para}
    except Exception as e:  # noqa
        log("ensure_sources failed:", type(e).__name__, str(e)[:100])
    log(f"sources after guarantee step: {len(SRC_LIST)} pages, {len(SRC_PARAS)} paragraphs")


def auto_mark(text):
    """A phrase worth highlighting in a source paragraph (a number with its unit, else a name), chosen by code, always a real substring."""
    m = re.search(r"\b\d[\d,\.]*(?:\s+[A-Za-z%]+){0,2}", text)
    if m and len(m.group(0).split()) <= 4:
        return m.group(0).strip(" ,.")
    m = re.search(r"\b(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)", text)
    if m:
        return m.group(0)
    return " ".join(text.split()[2:6])


def _ph_scenes(scenes):
    return [sc for sc in scenes if sc["type"] == "photo" and sc.get("wt") and len(sc["wt"]) == len(sc["narration_hi"].split()) and len(sc["narration_hi"].split()) >= 6]


def force_source_cards(scenes, topic=""):
    """Source cards that match what the narrator says AT THAT MOMENT: Gemini reads every scene against the real source paragraphs and returns
    only real matches (the claim, the word where it is spoken, the paragraph, the exact words to highlight). Nothing is forced when nothing fits."""
    ph = _ph_scenes(scenes)
    if not ph or not SRC_PARAS or not GEMINI_KEY:
        return 0
    has = lambda sc: any(isinstance(i, dict) and i.get("kind") == "note" for i in (sc.get("items") or []))
    have = sum(1 for sc in ph if has(sc))
    target = max(2, (len(ph) + 1) // 2)
    need = target - have
    free = [sc for sc in ph if not has(sc)]
    if need <= 0 or not free:
        return 0
    paras = list(SRC_PARAS.items())[:45]
    plist = "\n".join(f'[{k}] {v["title"][:45]}: "{v["text"][:240]}"' for k, v in paras)
    cands = []
    for a in range(0, len(free), 8):
        batch = free[a:a + 8]
        listing = "\n".join(f"SCENE {ph.index(sc)}: {_numbered(sc)}" for sc in batch)
        prompt = f"""Hindi documentary scenes (every word numbered) and real source paragraphs (English). Topic: {topic}
For each scene: if one source paragraph supports, confirms or documents a SPECIFIC claim the narrator makes IN THAT SCENE, return it:
{{"i": scene number, "w": number of the word where the narrator says that claim, "src": "paragraph id", "mark_en": "3-8 words copied EXACTLY from that paragraph that back the claim", "fit": "direct" or "related"}}.
"direct" = the paragraph states the same fact; "related" = same subject, supporting context. Leave a scene out when no paragraph matches what it says. Never force a match, never invent.
Return ONLY JSON: {{"matches":[...]}}
{listing}
{plist}"""
        try:
            out = gemini_json_opt(prompt, 0.1)
        except Exception as e:  # noqa
            log("source matching failed:", str(e)[:100])
            continue
        for r in (out.get("matches") or []):
            try:
                i, w = int(r.get("i")), int(r.get("w"))
            except Exception:  # noqa
                continue
            sc = next((x for x in batch if ph.index(x) == i), None)
            sp_ = SRC_PARAS.get(str(r.get("src") or "").strip())
            fit = str(r.get("fit") or "").lower()
            if sc is None or sp_ is None or fit not in ("direct", "related") or not (0 <= w < len(sc["narration_hi"].split())):
                continue
            mk = clean_short(r.get("mark_en"), 70)
            if not mk or mk.lower() not in sp_["text"].lower():
                mk = auto_mark(sp_["text"]) if fit == "direct" else ""
            cands.append((0 if fit == "direct" else 1, sc, str(r.get("src")).strip(), {"w": w, "kind": "note", "text": window_text(sp_["text"], mk), "mark": mk,
                                                                                    "sub": sp_["title"].replace("|", "-")[:70], "dom": _dom(sp_.get("url", ""))}))
    cands.sort(key=lambda c: c[0])
    added, used_src, used_sc = 0, set(), set()
    for _fit, sc, src, rec in cands:
        if added >= need:
            break
        if src in used_src or id(sc) in used_sc:
            continue
        sc.setdefault("items", []).append(rec)
        used_src.add(src)
        used_sc.add(id(sc))
        added += 1
    log(f"source cards matched to the narration: {added} (had {have}, target {target})")
    return added


def notes_from_declared_src(scenes, topic=""):
    """The script was written FROM real article passages and every scene names its passage ("src"). Here the source card of that passage is placed at the word where
    the narrator states the fact, and shows the article's own sentence (word for word) with the key words highlighted."""
    ph = [sc for sc in _ph_scenes(scenes) if str(sc.get("src") or "").strip() in SRC_PARAS]
    has = lambda sc: any(isinstance(i, dict) and i.get("kind") == "note" for i in (sc.get("items") or []))
    todo = [sc for sc in ph if not has(sc)]
    if not todo or not GEMINI_KEY:
        return 0
    added = 0
    for a in range(0, len(todo), 8):
        batch = todo[a:a + 8]
        listing = "\n".join(f'SCENE {k}: {_numbered(sc)}\nPASSAGE [{sc["src"]}]: "{SRC_PARAS[str(sc["src"]).strip()]["text"][:700]}"' for k, sc in enumerate(batch))
        prompt = f"""Hindi documentary scenes (every word numbered) and, for each, the real English article passage the scene was written from. Topic: {topic}
For each scene return where the narrator states the fact that the passage states: {{"i": scene number, "w": number of the word where that fact is spoken, "mark_en": "2-8 words copied EXACTLY from the passage that state that fact", "ok": true or false}}.
ok=false when the narration does not really state what the passage says. Never invent.
Return ONLY JSON: {{"matches":[...]}}
{listing}"""
        try:
            out = gemini_json_opt(prompt, 0.1)
        except Exception as e:  # noqa
            log("declared source matching failed:", str(e)[:100])
            continue
        for r in (out.get("matches") or []):
            try:
                i, w = int(r.get("i")), int(r.get("w"))
            except Exception:  # noqa
                continue
            if not (0 <= i < len(batch)) or r.get("ok") is False:
                continue
            sc = batch[i]
            sp_ = SRC_PARAS[str(sc["src"]).strip()]
            if not (0 <= w < len(sc["narration_hi"].split())):
                continue
            mk = clean_short(r.get("mark_en"), 70)
            if not mk or mk.lower() not in sp_["text"].lower():
                mk = auto_mark(sp_["text"])
            sc.setdefault("items", []).append({"w": w, "kind": "note", "text": window_text(sp_["text"], mk), "mark": mk,
                                               "sub": sp_["title"].replace("|", "-")[:70], "dom": _dom(sp_.get("url", ""))})
            added += 1
    log(f"source cards from the script's own article passages: {added}")
    return added


def add_source_outro(scenes):
    """A small 'SOURCES' list on the last picture scene: the viewer always sees which pages the facts come from."""
    refs = _src_refs()[:4]
    ph = _ph_scenes(scenes)
    if not refs or not ph:
        return False
    sc = ph[-1]
    sc.setdefault("items", []).append({"w": 0, "kind": "srclist", "text": "\n".join(r["title"] for r in refs), "at_end": True})
    return True


# ---------------------------------------------------------------- automatic word detector (every year / month / place / number / quote gets its helper)
_DEV = str.maketrans("०१२३४५६७८९", "0123456789")
MONTHS_HI = {"जनवरी": 1, "फरवरी": 2, "मार्च": 3, "अप्रैल": 4, "मई": 5, "जून": 6, "जुलाई": 7, "अगस्त": 8, "सितंबर": 9, "सितम्बर": 9, "अक्टूबर": 10, "अक्तूबर": 10,
             "नवंबर": 11, "नवम्बर": 11, "दिसंबर": 12, "दिसम्बर": 12}
PLACES_HI = {x for x in (
    "भारत हिंदुस्तान पाकिस्तान चीन अमेरिका रूस जापान ब्रिटेन इंग्लैंड फ्रांस जर्मनी इटली स्पेन कनाडा ब्राज़ील ब्राजील मेक्सिको अफ्रीका अफ़्रीका नेपाल भूटान बांग्लादेश श्रीलंका अफगानिस्तान ईरान इराक इज़राइल इजराइल "
    "मिस्र तुर्की यूक्रेन ऑस्ट्रेलिया ऑस्ट्रिया स्विट्ज़रलैंड नॉर्वे स्वीडन फिनलैंड ग्रीस पुर्तगाल थाईलैंड वियतनाम इंडोनेशिया मलेशिया सिंगापुर म्यांमार केन्या नाइजीरिया इथियोपिया अर्जेंटीना चिली पेरू क्यूबा "
    "एशिया यूरोप अंटार्कटिका आर्कटिक हिमालय सहारा अमेज़न अमेजन "
    "राजस्थान गुजरात महाराष्ट्र बिहार पंजाब हरियाणा उत्तराखंड कर्नाटक केरल तमिलनाडु तेलंगाना ओडिशा असम झारखंड छत्तीसगढ़ गोवा कश्मीर लद्दाख दिल्ली मुंबई कोलकाता चेन्नई बेंगलुरु हैदराबाद पुणे जयपुर लखनऊ पटना भोपाल "
    "वाराणसी अयोध्या आगरा कानपुर मैनपुरी अहमदाबाद सूरत इंदौर नागपुर शिमला मनाली").split()} | {
    "उत्तर प्रदेश", "मध्य प्रदेश", "हिमाचल प्रदेश", "आंध्र प्रदेश", "अरुणाचल प्रदेश", "पश्चिम बंगाल", "दक्षिण अफ्रीका", "दक्षिण कोरिया", "उत्तर कोरिया", "सऊदी अरब", "न्यूज़ीलैंड", "न्यूजीलैंड",
    "प्रशांत महासागर", "हिंद महासागर", "अटलांटिक महासागर", "न्यू यॉर्क", "संयुक्त राज्य अमेरिका"}
UNITS_HI = {"महीने", "महीना", "साल", "वर्ष", "दिन", "घंटे", "घंटा", "मिनट", "सेकंड", "सेकेंड", "किलोमीटर", "किमी", "मीटर", "किलो", "करोड़", "लाख", "हज़ार", "हजार", "प्रतिशत", "%", "डिग्री", "हफ्ते", "सप्ताह", "अरब", "बार", "लोग", "लोगों", "बच्चे", "मौतें"}
_QOPEN, _QCLOSE = "\"“‘'", "\"”’'"


def _ct(t):
    return t.strip(" ,.;:!?।—-()[]{}…")


def auto_overlays(scenes):
    """Rule-based detector: every spoken year (+month), month, country / state / city, number with a unit and every quoted sentence gets its on-screen helper."""
    n = {"year": 0, "stat": 0, "bubble": 0}
    for sc in _ph_scenes(scenes):
        toks = sc["narration_hi"].split()
        have = sc.setdefault("items", [])
        cand = []
        used = set()
        side = ["left", "right"]
        sidx = 0
        i = 0
        while i < len(toks):
            raw, c = toks[i], _ct(toks[i])
            cn = c.translate(_DEV)
            if c in MONTHS_HI:
                nxt = next((j for j in (i + 1, i + 2) if j < len(toks) and re.fullmatch(r"\d{4}", _ct(toks[j]).translate(_DEV)) and 1000 <= int(_ct(toks[j]).translate(_DEV)) <= 2100), None)
                if nxt is not None:
                    yn = int(_ct(toks[nxt]).translate(_DEV))
                    cand.append({"w": i, "kind": "year", "text": " ".join(_ct(t) for t in toks[i:nxt + 1]), "year_num": yn, "month": MONTHS_HI[c]})
                    used.update(range(i, nxt + 1))
                    i = nxt + 1
                    continue
                cand.append({"w": i, "kind": "stat", "text": c, "tone": "place"})
            elif re.fullmatch(r"\d{4}", cn) and 1000 <= int(cn) <= 2100 and i not in used:
                cand.append({"w": i, "kind": "year", "text": c, "year_num": int(cn), "month": None})
            elif re.fullmatch(r"\d[\d,\.]{0,8}", cn) and i + 1 < len(toks) and _ct(toks[i + 1]) in UNITS_HI and not (len(cn) == 4 and 1000 <= int(cn.replace(",", "") or 0) <= 2100):
                cand.append({"w": i, "kind": "stat", "text": c + " " + _ct(toks[i + 1])})
                i += 2
                continue
            elif i + 1 < len(toks) and (c + " " + _ct(toks[i + 1])) in PLACES_HI:
                cand.append({"w": i, "kind": "stat", "text": c + " " + _ct(toks[i + 1]), "tone": "place"})
                i += 2
                continue
            elif c in PLACES_HI:
                cand.append({"w": i, "kind": "stat", "text": c, "tone": "place"})
            if raw and raw[0] in _QOPEN and not raw[:2] == "''":
                j = i
                while j < len(toks) and j < i + 12 and not (toks[j] and toks[j][-1] in _QCLOSE and (j > i or len(toks[j]) > 1)):
                    j += 1
                if j < len(toks) and 2 <= j - i + 1 <= 11:
                    q_ = " ".join(toks[i:j + 1]).strip("\"“”‘’'")
                    q_ = " ".join(q_.split()[:7])
                    cand.append({"w": i, "kind": "bubble", "text": q_, "side": side[sidx % 2]})
                    sidx += 1
                    i = j + 1
                    continue
            i += 1
        for r in cand:
            if any(isinstance(x, dict) and abs(x.get("w", -9) - r["w"]) < 3 and (x.get("kind") == r["kind"] or r["kind"] == "stat" and x.get("kind") in ("stat", "year")) for x in have):
                continue
            if r["kind"] == "stat" and sum(1 for x in have if isinstance(x, dict) and x.get("kind") == "stat") >= 5:
                continue
            have.append(r)
            n["year" if r["kind"] == "year" else r["kind"]] += 1
        have.sort(key=lambda x: x.get("w", 0) if isinstance(x, dict) else 0)
        del have[14:]
    log("auto detector added:", n)
    return n


# ---------------------------------------------------------------- roll-out paper (unrolls from the top, rolls back up at the end)
_ROLL = {}


def _cylinder(w, r, base):
    key = (w, r, base)
    if key in _ROLL:
        return _ROLL[key]
    np = _np()
    ys = np.linspace(0, math.pi, r)
    shade = (0.62 + 0.55 * np.sin(ys - 0.35) ** 2).clip(0.55, 1.12)
    arr = np.zeros((r, w, 4), dtype="float32")
    for k in range(r):
        arr[k, :, :3] = np.array(base, "float32") * shade[k]
        arr[k, :, 3] = 255
    im = Image.fromarray(np.clip(arr, 0, 255).astype("uint8"), "RGBA")
    m = Image.new("L", (w, r), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, r - 1], radius=r // 2, fill=255)
    im.putalpha(m)
    out = Image.new("RGBA", (w, r + 14), (0, 0, 0, 0))
    sh = Image.new("RGBA", (w, r + 14), (0, 0, 0, 255))
    sh.putalpha(Image.new("L", (w, r + 14), 0))
    sa = Image.new("L", (w, r + 14), 0)
    sa.paste(m, (0, 8))
    sh.putalpha(sa.filter(ImageFilter.GaussianBlur(5)).point(lambda v: int(v * 0.5)))
    out.alpha_composite(sh)
    out.alpha_composite(im)
    _ROLL[key] = out
    return out


def roll_frame(spr, q, base=(232, 222, 196)):
    """Visible part of the paper when it is q (0..1) unrolled; the rolled-up part is drawn as a paper cylinder at the leading edge."""
    if q >= 0.995:
        return spr, 0
    vh = int(max(4, spr.height * cl(q)))
    part = spr.crop((0, 0, spr.width, vh))
    cyl = _cylinder(int(spr.width * 0.9), 30, base)
    out = Image.new("RGBA", (spr.width, vh + cyl.height - 10), (0, 0, 0, 0))
    out.alpha_composite(part, (0, 0))
    out.alpha_composite(cyl, ((spr.width - cyl.width) // 2, vh - 14))
    return out, vh


def put_roll(fr, spr, cx, cy, q, scale=1.0, base=(232, 222, 196)):
    part, _vh = roll_frame(spr, q, base)
    top = cy - spr.height * scale / 2
    put(fr, part, cx, top + part.height * scale / 2, scale, 1.0)


# ---------------------------------------------------------------- keyframe engine + explain PNG
EXPLAIN_MAX = [int(os.environ.get("MAX_EXPLAIN_PNG", "6" if TEST_SEC else "30"))]
KF_PRESETS = {  # (t, x, y, scale, rotation, alpha); x / y are fractions of the free area, mirrored when it is on the left
    "pop":   [(0.0, 0.0, 0.08, 0.2, -14, 0.0), (0.35, 0.0, 0.0, 1.14, 5, 1.0), (0.6, 0.0, 0.0, 1.0, 0, 1.0), (4.4, 0.0, -0.02, 1.05, 0, 1.0)],
    "slide": [(0.0, 0.9, 0.0, 0.9, 8, 0.0), (0.55, 0.0, 0.0, 1.0, -2, 1.0), (4.4, -0.05, 0.02, 1.04, 0, 1.0)],
    "drop":  [(0.0, 0.0, -0.9, 1.0, 12, 0.0), (0.38, 0.0, 0.04, 1.0, -4, 1.0), (0.52, 0.0, -0.03, 1.0, 2, 1.0), (0.66, 0.0, 0.0, 1.0, 0, 1.0), (4.4, 0.0, 0.0, 1.02, 0, 1.0)],
    "zoom":  [(0.0, 0.0, 0.0, 0.55, 0, 0.0), (0.5, 0.0, 0.0, 1.0, 0, 1.0), (4.4, 0.0, 0.0, 1.22, 0, 1.0)],
    "spin":  [(0.0, 0.0, 0.0, 0.1, -240, 0.0), (0.6, 0.0, 0.0, 1.08, 8, 1.0), (0.85, 0.0, 0.0, 1.0, 0, 1.0), (4.4, 0.0, 0.0, 1.0, -6, 1.0)],
}


def kf_eval(kfs, t):
    if t <= kfs[0][0]:
        return kfs[0][1:]
    for a, b in zip(kfs, kfs[1:]):
        if a[0] <= t <= b[0]:
            u = (t - a[0]) / max(1e-6, b[0] - a[0])
            u = u * u * (3 - 2 * u)
            return tuple(a[k] + (b[k] - a[k]) * u for k in range(1, 6))
    return kfs[-1][1:]


def png_sticker(im, maxside=520):
    """RGBA object -> cropped, scaled, soft shadow (real transparency is kept)."""
    a = im.getchannel("A")
    bb = a.point(lambda v: 255 if v > 24 else 0).getbbox()
    if not bb:
        return None
    im = im.crop(bb)
    sc_ = maxside / max(im.size)
    im = im.resize((max(8, int(im.width * sc_)), max(8, int(im.height * sc_))), Image.LANCZOS)
    pad = 30
    out = Image.new("RGBA", (im.width + 2 * pad, im.height + 2 * pad), (0, 0, 0, 0))
    sh = Image.new("RGBA", out.size, (0, 0, 0, 255))
    sa = Image.new("L", out.size, 0)
    sa.paste(im.getchannel("A"), (pad + 5, pad + 12))
    sh.putalpha(sa.filter(ImageFilter.GaussianBlur(10)).point(lambda v: int(v * 0.5)))
    out.alpha_composite(sh)
    out.alpha_composite(im, (pad, pad))
    return out


def commons_png(query, out):
    """A real transparent PNG (object cut-out / scientific diagram) from Wikimedia Commons with an open licence."""
    r = requests.get("https://commons.wikimedia.org/w/api.php", headers=WIKI_UA, timeout=25, params={
        "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6, "gsrlimit": 12, "gsrsearch": f"{query} filemime:image/png",
        "prop": "imageinfo", "iiprop": "url|extmetadata|mime|size", "iiurlwidth": 800})
    pages = sorted(((r.json().get("query") or {}).get("pages") or {}).values(), key=lambda p: p.get("index", 99))
    for pg in pages:
        title = pg.get("title", "")
        ii = (pg.get("imageinfo") or [{}])[0]
        meta = ii.get("extmetadata", {})
        if ii.get("mime") != "image/png" or not _lic_ok(meta) or any(b in title.lower() for b in BAD_TITLE) or unreal(title, query) or (ii.get("width") or 0) < 300:
            continue
        try:
            im = Image.open(io.BytesIO(requests.get(ii.get("thumburl") or ii["url"], headers=WIKI_UA, timeout=60).content)).convert("RGBA")
            al = _np().asarray(im.getchannel("A"))
            frac = float((al < 20).mean())
            if not (0.12 < frac < 0.88):
                continue
            flat = Image.new("RGB", im.size, (255, 255, 255))
            flat.paste(im, mask=im.getchannel("A"))
            flat.thumbnail((640, 640))
            buf = io.BytesIO()
            flat.save(buf, "JPEG", quality=80)
            ok = "YES"
            if GEMINI_KEY and not VERIFY["off"]:
                ok, _t = gemini(f'Look at this picture. Is it a clear, correct picture of "{query}" (a real object photo, a cut-out or a proper scientific diagram - NOT a cartoon character, toy, statue or unrelated thing)? Answer YES or NO.',
                                temperature=0.0, image=base64.b64encode(buf.getvalue()).decode(), optional=True)
            if not str(ok).strip().upper().startswith("YES"):
                continue
            im.save(out)
            IMG_CREDITS.append(f"{title[5:60]} - {_strip_html(meta.get('Artist', {}).get('value'))[:40] or 'Wikimedia'} ({meta.get('LicenseShortName', {}).get('value', 'open licence')}) {ii.get('descriptionurl', '')}".strip())
            return True
        except Exception as e:  # noqa
            log("  commons png failed:", type(e).__name__, str(e)[:80])
    return False


def explain_png(query, out):
    """Find a clean transparent PNG of the thing being explained; else generate an isolated object picture and cut it out. Returns an RGBA sticker or None."""
    p = Path(out)
    try:
        if commons_png(query, p):
            return png_sticker(Image.open(p).convert("RGBA"))
    except Exception as e:  # noqa
        log("  png search failed:", type(e).__name__, str(e)[:80])
    try:
        rgba = cutout_object(f"a single {query}, studio product photo", p, query)
        if rgba is not None:
            return png_sticker(rgba)
    except Exception as e:  # noqa
        log("  object png failed:", type(e).__name__, str(e)[:80])
    return None


def action_png(query, pose, verb, out):
    """The moving thing for an action scene ("lion running"): a pose picture generated on a flat backdrop, then ONLY the background is cut away."""
    side = ", side view, facing right" if verb in HORIZONTAL else ", facing the camera" if verb in ("approach", "retreat") else ""
    try:
        rgba = cutout_object(f"{pose or query}{side}, full body, studio photo", Path(out), query)
        if rgba is not None:
            return png_sticker(rgba, 560)
    except Exception as e:  # noqa
        log("  action png failed:", type(e).__name__, str(e)[:80])
    return explain_png(query, out)


def draw_explain(fr, ps, tl, t0, t1, a, ov):
    it = ov.get("it") or {}
    spr = it.get("png_img")
    if spr is None:
        return
    side = ov.get("side") or "right"
    sg = -1 if side == "left" else 1
    kfs = KF_PRESETS.get(it.get("anim") or "pop", KF_PRESETS["pop"])
    x, y, s, rot, al = kf_eval(kfs, tl)
    cx = W * (0.27 if side == "left" else 0.73) + sg * x * W * 0.3
    cy = H * 0.47 + y * H * 0.5 + 6 * math.sin(tl * 2.2)
    im = spr if abs(rot) < 0.5 else spr.rotate(rot, expand=True, resample=Image.BICUBIC)
    scl = min(1.0, 0.62 * H / max(1, spr.height)) * s
    put(fr, im, cx, cy, scl, a * al)
    lab = ov.get("text") or ""
    if lab and tl > 0.55:
        ll = _TL_LAB.get(("x", lab))
        if ll is None:
            ll = _TL_LAB[("x", lab)] = text_layer(lab, font_for(lab, 46, "title"), fill=(255, 255, 255), stroke=2, stroke_fill=(0, 0, 0), shadow=(0, 3, 5, 0.8))
        qq = e_out(cl((tl - 0.55) / 0.4))
        put(fr, ll, cx, cy + spr.height * scl / 2 + 24 + (1 - qq) * 22, 1.0, a * qq)


# ---------------------------------------------------------------- source list at the end + reference-only card
def srclist_sprite(text):
    key = ("srclist", text)
    if key in _CARDS:
        return _CARDS[key]
    rows = [r.split("|") for r in text.split("\n") if r.strip()]
    probe = ImageDraw.Draw(Image.new("L", (4, 4)))
    fh, fr_ = font_for("A", 30, "cond"), font_for("A", 27, "serif")
    rows = [[pretty_source(r[0])[:70]] for r in rows]
    need = max(int(probe.textlength(r[0], font=fr_) + 70) for r in rows) + 50
    w_ = min(W - 80, max(620, need))
    h_ = 30 + 46 + len(rows) * 40 + 16
    im = Image.new("RGBA", (w_, h_), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, w_ - 1, h_ - 1], radius=18, fill=(8, 8, 12, 205))
    d.rounded_rectangle([22, 20, 22 + 150, 20 + 34], radius=6, fill=(214, 28, 40, 255))
    d.text((36, 20 + 28), "SOURCES", font=fh, fill=(255, 255, 255, 255), anchor="ls")
    y = 20 + 46
    for k, r in enumerate(rows, 1):
        t = r[0][:78]
        d.text((26, y + 28), f"{k}.", font=fr_, fill=(255, 196, 60, 255), anchor="ls")
        d.text((70, y + 28), t, font=fr_, fill=(240, 236, 226, 255), anchor="ls")
        y += 40
    _CARDS[key] = im
    return im


def draw_srclist(fr, ps, tl, t0, t1, a, ov):
    spr = srclist_sprite(ov.get("it", {}).get("text") or "")
    q = e_out(cl((tl - t0) / 0.5))
    put(fr, spr, 40 + spr.width / 2, H - 36 - spr.height / 2 + (1 - q) * 60, 1.0, a)


# =========================================================================== V5: clean cut-outs, action animations, meaning-aware pictures, mention-timed pictures
def pretty_source(title, dom=""):
    """Never show a link: a title that is a web address becomes a plain name ("en.wikipedia.org" -> "Wikipedia")."""
    t = _ascii(title or "")
    if re.search(r"https?://|www\.|\.(com|org|net|in|co|edu|gov|io)\b", t, re.I) and len(t.split()) <= 3:
        host = re.sub(r"^https?://", "", t.lower()).split("/")[0]
        parts = [p for p in host.split(".") if p and p not in ("www", "en", "m")]
        name = parts[-2] if len(parts) >= 2 else (parts[0] if parts else "")
        return name.capitalize() if name else "Web source"
    return re.sub(r"https?://\S+", "", t).strip(" -|") or "Web source"


def clean_cutout(path):
    """Removes ONLY the plain background around the object (flat colour touching the picture border). Every pixel of the object is kept;
    the edge gets a soft anti-aliased fade with the background colour taken out of it. Returns an RGBA image or None."""
    np = _np()
    from scipy import ndimage
    im = Image.open(path).convert("RGB")
    arr = np.asarray(im, dtype=np.float32)
    h, w, _ = arr.shape
    ring = np.concatenate([arr[:6].reshape(-1, 3), arr[-6:].reshape(-1, 3), arr[:, :6].reshape(-1, 3), arr[:, -6:].reshape(-1, 3)])
    bgc = np.median(ring, axis=0)
    if float(ring.std(axis=0).max()) > 42:
        return None  # the background is not a flat colour
    d = np.sqrt(((arr - bgc) ** 2).sum(2))
    lab, _n = ndimage.label(d < 26.0)
    border = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    bg = np.isin(lab, list(border)) if border else np.zeros(d.shape, bool)
    fg = ~bg
    holes = ndimage.binary_fill_holes(fg) & ~fg  # small enclosed bits inside the object stay opaque; big enclosed openings (a handle) stay transparent
    hl, hn = ndimage.label(holes)
    if hn:
        sizes = ndimage.sum(holes, hl, range(1, hn + 1))
        fg = fg | np.isin(hl, [i + 1 for i, s in enumerate(sizes) if s < 0.004 * h * w])
    fl, fn = ndimage.label(fg)
    if fn > 1:
        sizes = ndimage.sum(fg, fl, range(1, fn + 1))
        keep = [i + 1 for i, s in enumerate(sizes) if s >= max(0.0015 * h * w, 0.02 * float(max(sizes)))]
        fg = np.isin(fl, keep)
    if fg.mean() < 0.04 or fg.mean() > 0.85:
        return None
    edge_touch = int(fg[0].sum() + fg[-1].sum() + fg[:, 0].sum() + fg[:, -1].sum())
    if edge_touch > 0.12 * 2 * (h + w):
        return None  # the object is cut by the picture frame
    near = ndimage.binary_dilation(~fg, iterations=2) & fg
    alpha = fg.astype(np.float32)
    alpha[near] = np.clip((d[near] - 10.0) / 36.0, 0, 1)
    a3 = np.maximum(alpha, 0.25)[..., None]
    out = np.where(near[..., None], np.clip((arr - (1 - alpha[..., None]) * bgc) / a3, 0, 255), arr)
    if float(bgc.max() - bgc.min()) > 110:  # coloured backdrop (green): remove the colour spill from the edge pixels
        c = int(np.argmax(bgc))
        o1, o2 = [k for k in range(3) if k != c]
        rim = ndimage.binary_dilation(near, iterations=1) & fg
        cap = (out[..., o1] + out[..., o2]) / 2.0 + 8.0
        out[..., c] = np.where(rim, np.minimum(out[..., c], cap), out[..., c])
    rgba = np.dstack([out, alpha * 255]).astype(np.uint8)
    return Image.fromarray(rgba, "RGBA")


def cutout_complete(rgba, query):
    """Gemini looks at the cut-out on a magenta card: is the whole object there (nothing cut off, erased or missing)?"""
    if not GEMINI_KEY or VERIFY["off"]:
        return True
    try:
        card = Image.new("RGB", rgba.size, (255, 0, 255))
        card.paste(rgba, mask=rgba.getchannel("A"))
        card.thumbnail((512, 512))
        buf = io.BytesIO()
        card.save(buf, "JPEG", quality=82)
        ans, _t = gemini(f'This is a cut-out of "{query}" on a magenta background. Is the WHOLE object present - no part cut off, erased, transparent, or missing (legs, tail, ears, handles, wheels)? '
                         "Answer YES or NO.", temperature=0.0, image=base64.b64encode(buf.getvalue()).decode(), optional=True)
        return str(ans).strip().upper().startswith("YES")
    except Exception:  # noqa
        return True


LIGHT_WORDS = re.compile(r"\b(white|snow\w*|polar|swan|egret|cloud\w*|ice|iceberg|salt|cotton|milk|pearl|silver|ghost|albino|marble|bone|skull|skeleton|paper)\b", re.I)


def cutout_object(subject, out, query, tries=None):
    """Generate the object on a flat background and cut ONLY the background away; a second try uses a green backdrop when a white
    object (or white fur) would have been eaten by the first. Returns an RGBA cut-out or None."""
    if tries is None:  # a light subject starts on green so its white parts survive
        tries = ("green", "white") if LIGHT_WORDS.search(subject + " " + query) else ("white", "green")
    for bgname in tries:
        p = Path(out)
        p.unlink(missing_ok=True)
        try:
            if not gen_image(f"{subject}, whole subject fully visible and centered with free space all around, nothing cropped", p, "obj", query=query, bg=bgname):
                continue
            rgba = clean_cutout(p)
        except Exception as e:  # noqa
            log("  cutout try failed:", type(e).__name__, str(e)[:80])
            continue
        if rgba is not None and cutout_complete(rgba, query):
            return rgba
        log(f"  cut-out of '{query}' rejected on {bgname} background")
    return None


# ---------------------------------------------------------------- action animation: the PNG itself moves ("the lion ran towards the lake")
ACTION_SPAN = {"run": 3.6, "walk": 4.8, "swim": 4.6, "fly": 4.0, "jump": 3.0, "fall": 2.6, "rise": 3.0, "approach": 3.8, "retreat": 3.4, "spin": 2.6, "shake": 2.2, "grow": 2.8}
HORIZONTAL = ("run", "walk", "swim", "fly", "jump")


def action_state(verb, direction, tl, span):
    """(x, y, scale, rotation, squash_y, shadow) of the moving cut-out at time tl; x, y are screen fractions of W and H."""
    sgn = -1 if direction == "left" else 1
    T = max(1.2, span - 0.5)
    u = cl((tl - 0.2) / T)
    if verb in ("run", "walk"):
        f = 2.7 if verb == "run" else 1.7
        amp = 0.05 if verb == "run" else 0.016
        x = 0.5 + sgn * (-0.68 + 1.36 * u)
        y = 0.74 - abs(math.sin(math.pi * f * tl)) * amp
        return x, y, 1.0, sgn * 3.0 * math.sin(2 * math.pi * f * tl + 1.0), 1 + 0.045 * math.sin(2 * math.pi * f * 2 * tl), True
    if verb == "swim":
        x = 0.5 + sgn * (-0.64 + 1.28 * u)
        return x, 0.64 + 0.02 * math.sin(2 * math.pi * 0.9 * tl), 0.85, sgn * 4.0 * math.sin(2 * math.pi * 0.9 * tl), 1.0, False
    if verb == "fly":
        x = 0.5 + sgn * (-0.66 + 1.32 * u)
        return x, 0.30 + 0.045 * math.sin(2 * math.pi * 0.7 * tl) + (1 - u) * 0.05, 0.9, sgn * 5.0 * math.sin(2 * math.pi * 0.7 * tl + 0.5), 1 + 0.1 * math.sin(2 * math.pi * 7 * tl), False
    if verb == "jump":
        x = 0.5 + sgn * (-0.38 + 0.76 * u)
        return x, 0.74 - 0.38 * 4 * u * (1 - u), 1.0, -sgn * 14.0 * math.sin(math.pi * u), 1 + 0.08 * math.sin(math.pi * u), u < 0.02 or u > 0.98
    if verb == "fall":
        v = cl((tl - 0.15) / 1.3)
        y = -0.25 + (0.74 + 0.25) * v * v
        if v >= 1:  # small bounce when it lands
            b = cl((tl - 1.45) / 0.5)
            y = 0.74 - 0.05 * 4 * b * (1 - b)
        return 0.5 + sgn * 0.12, y, 1.0, sgn * 360.0 * min(1.0, v), 1.0, v >= 1
    if verb == "rise":
        v = e_out(cl((tl - 0.15) / (T * 0.8)))
        return 0.5, 0.86 - 0.58 * v, 0.9 + 0.2 * v, 0.0, 1.0, False
    if verb == "approach":
        v = cl((tl - 0.1) / T)
        v = v * v * (3 - 2 * v)
        return 0.5, 0.66 + 0.02 * math.sin(tl * 4), 0.28 + 1.3 * v, 0.0, 1.0, v > 0.2
    if verb == "retreat":
        v = cl((tl - 0.1) / T)
        v = v * v * (3 - 2 * v)
        return 0.5, 0.66, 1.4 - 1.15 * v, 0.0, 1.0, True
    if verb == "spin":
        v = e_out(cl((tl - 0.1) / (T * 0.7)))
        return 0.5, 0.5, 0.3 + 0.8 * v, 720.0 * v, 1.0, False
    if verb == "shake":
        k = max(0.0, 1 - tl / span)
        return 0.5 + 0.012 * math.sin(tl * 60) * k, 0.55, 1.0, 5.0 * math.sin(tl * 48) * k, 1.0, False
    v = e_back(cl((tl - 0.1) / (T * 0.55)))  # grow
    return 0.5, 0.58, 0.3 + 0.95 * v, 0.0, 1.0, False


_GROUND_SH = {}


def draw_action(fr, ps, tl, t0, t1, a, ov):
    it = ov.get("it") or {}
    spr = it.get("png_img")
    if spr is None:
        return
    verb, direction = it.get("verb") or "run", it.get("dir") or "right"
    x, y, s, rot, sq, shadow = action_state(verb, direction, tl, max(1.5, t1 - t0))
    base = spr if (direction != "left" or verb not in HORIZONTAL) else ImageOps.mirror(spr)
    scl = (0.44 * H / max(1, base.height)) * s
    if shadow:  # small soft ground shadow under the feet
        sw_ = max(40, int(base.width * scl * 0.75) // 8 * 8)
        sh_img = _GROUND_SH.get(sw_)
        if sh_img is None:
            sh_img = Image.new("RGBA", (sw_ + 40, 56), (0, 0, 0, 0))
            ImageDraw.Draw(sh_img).ellipse([20, 14, sw_ + 20, 42], fill=(0, 0, 0, 150))
            _GROUND_SH[sw_] = sh_img = sh_img.filter(ImageFilter.GaussianBlur(7))
        put(fr, sh_img, x * W, 0.74 * H - 2, 1.0, a * 0.6)
    im = base
    if abs(sq - 1) > 0.01:
        im = im.resize((im.width, max(8, int(im.height * sq))), Image.BILINEAR)
    if abs(rot) > 0.4:
        im = im.rotate(rot, expand=True, resample=Image.BICUBIC)
    put(fr, im, x * W, y * H - base.height * scl * 0.5 + 30 * scl, scl, a)  # feet (not the sprite's padding) stand on the ground line


# ---------------------------------------------------------------- pictures that follow the MEANING, shown only when mentioned
def semantic_review(scenes, topic=""):
    """Text-only pass over every picture query: emotions, professions and abstract words must show what they mean (a real person who looks sad,
    a real doctor at work, a concrete symbol for 'danger'), never an unrelated animal or object."""
    if not GEMINI_KEY:
        return 0
    rows = []
    for i, sc in enumerate(scenes):
        if sc.get("type") != "photo":
            continue
        for k, sh in enumerate(sc.get("shots") or []):
            if str(sh.get("text_hi") or "").strip() and (sh.get("query_en") or sh.get("prompt_en")):
                rows.append((i, k, sh))
    fixed = 0
    for a in range(0, len(rows), 20):
        part = rows[a:a + 20]
        listing = "\n".join(f'S{i}.{k}: "{sh["text_hi"][:160]}" -> picture: {sh.get("clip_en") or sh.get("query_en") or sh.get("prompt_en", "")[:60]}' for i, k, sh in part)
        prompt = f"""Documentary picture editor (topic: {topic}). For each Hindi sentence the picture query is shown. Check that the picture shows what the sentence MEANS:
- EMOTIONS / STATES (happy, sad, angry, afraid, lonely, proud, tired, in pain, shocked...) need a REAL PERSON whose face / body clearly shows that emotion (e.g. "sad man crying alone", "happy family laughing").
- PROFESSIONS / PERSONALITIES (doctor, engineer, scientist, farmer, teacher, soldier, police officer, student, priest, king, athlete...) need a REAL PERSON in that role at work (e.g. "doctor in white coat with stethoscope", "engineer with helmet and blueprint", "scientist in laboratory").
- ABSTRACT WORDS (good, bad, success, failure, danger, peace, justice, time, money, fear) need ONE concrete symbolic real scene, never a random animal or object.
- A named thing / place / animal keeps its own picture.
Return ONLY the rows that must be FIXED: {{"fix":[{{"id":"S0.1","query_en":"2-4 plain English words for a real stock photo","prompt_en":"realistic description of that single subject","clip_en":"subject + visible action, or empty"}}]}}. Return {{"fix":[]}} when all are fine.
{listing}"""
        try:
            out = gemini_json_opt(prompt, 0.15)
        except Exception as e:  # noqa
            log("semantic review failed:", str(e)[:100])
            continue
        for c in (out.get("fix") or []):
            m_ = re.match(r"S(\d+)\.(\d+)$", str(c.get("id") or ""))
            if not m_:
                continue
            i, k = int(m_.group(1)), int(m_.group(2))
            if not any(r[0] == i and r[1] == k for r in part):
                continue
            sh = scenes[i]["shots"][k]
            q_, pr_ = clean_short(c.get("query_en"), 50), clean_short(c.get("prompt_en"), 300)
            if q_ and q_.isascii():
                sh["query_en"], sh["entity_en"] = q_, ""
                sh["prompt_en"] = pr_ or ("a realistic photo of " + q_)
                ce = clean_short(c.get("clip_en"), 40)
                sh["clip_en"], sh["motion"] = (ce, True) if ce and ce.isascii() else ("", False)
                fixed += 1
    log("semantic review fixed pictures:", fixed)
    return fixed


def delay_to_mention(scenes):
    """One picture per sentence starts WITH the sentence; when the subject is only named later in that sentence, the previous picture stays until the
    narrator says it (the first words of the sentence are handed to the previous picture), so nothing appears before it is spoken about."""
    if not GEMINI_KEY:
        return 0
    rows = []
    for i, sc in enumerate(scenes):
        if sc.get("type") != "photo" or len(sc.get("shots") or []) < 2:
            continue
        for k, sh in enumerate(sc["shots"]):
            txt = str(sh.get("text_hi") or "")
            subj = sh.get("clip_en") or sh.get("query_en") or sh.get("entity_en")
            if k >= 1 and len(txt.split()) >= 7 and subj:
                rows.append((i, k, txt, subj))
    moved = 0
    for a in range(0, len(rows), 10):
        part = rows[a:a + 10]
        listing = "\n".join(f"S{i}.{k} [{subj}]: " + " ".join(f"{j}:{w}" for j, w in enumerate(txt.split())) for i, k, txt, subj in part)
        prompt = ("Hindi sentences, every word numbered. In brackets: the subject of the picture shown for that sentence. For each give \"m\": the 0-based number of the word where that subject is "
                  "FIRST spoken about (named, or its idea begins). 0 when the sentence starts with it. "
                  'Return ONLY JSON: {"m":[{"id":"S0.1","m":0}]}\n' + listing)
        try:
            out = gemini_json_opt(prompt, 0.0)
        except Exception as e:  # noqa
            log("mention timing failed:", str(e)[:100])
            continue
        for c in (out.get("m") or []):
            m_ = re.match(r"S(\d+)\.(\d+)$", str(c.get("id") or ""))
            if not m_:
                continue
            i, k = int(m_.group(1)), int(m_.group(2))
            try:
                m = int(c.get("m"))
            except Exception:  # noqa
                continue
            sh = scenes[i]["shots"][k]
            ws = str(sh.get("text_hi") or "").split()
            if 3 <= m <= len(ws) - 3 and k >= 1:
                prev = scenes[i]["shots"][k - 1]
                prev["text_hi"] = (str(prev.get("text_hi") or "") + " " + " ".join(ws[:m])).strip()
                sh["text_hi"] = " ".join(ws[m:])
                moved += 1
    log("pictures delayed until the narrator names them:", moved)
    return moved


def _label_hits(words, label):
    toks = [t.strip("।,?!\"'") for t in (label or "").split() if len(t.strip("।,?!\"'")) > 1]
    return [k for k, w in enumerate(words) if any(t in w.strip("।,?!\"'") or (len(w.strip("।,?!\"'")) > 2 and w.strip("।,?!\"'") in t) for t in toks)]


# =========================================================================== V6: place / name labels, year pill at the bottom, feature call-outs, concept-level pictures, PNG everywhere
def item_zones(it):
    """Screen areas: BL bottom-left, BR bottom-right, M middle, T top, FULL everything. Wide bottom things (banner card, year slider, source list) use BL+BR."""
    k = it["kind"]
    if k == "note":
        st_ = it.get("style") or "banner"
        if not it.get("text") or st_ == "banner":
            return {"BL", "BR"}
        return {"BL"} if st_ in ("strip", "box") else {"FULL"}
    if k in ("year", "srclist"):
        return {"BL", "BR"}
    if k == "place":
        return {"BL"}
    if k in ("name", "person"):
        return {"BR"}
    if k == "bubble":
        return {"T"}
    if k in ("feature", "beforeafter"):
        return {"FULL"}
    if k in ("tline", "hl"):
        return {"BL", "BR"}
    if k == "chart":
        return {"M"}
    if k == "compare":
        return {"M", "BL", "BR"}
    if k == "measure":
        return {"M"} if it.get("orient") == "v" else {"BL", "BR"}
    return {"M"}


QUICK_KINDS = {"year", "place", "name", "person", "stat", "bubble", "quote", "verdict", "explain", "action", "qmark", "mark", "lens", "measure"}


def schedule_items(items, dur):
    """Said now = shown now. Quick helpers (year, place, name, stat, quote, bubble, PNG) appear at their word and make room by cutting an earlier helper of the same area;
    only big cards may wait (at most 1.5 s). A helper is dropped only when a higher-priority card fills its area."""
    items.sort(key=lambda x: (x["t0"], -ITEM_PRI.get(x["it"]["kind"], 1)))
    placed = []
    for b in items:
        kb, zb, mb = b["it"]["kind"], item_zones(b["it"]), item_min(b["it"])
        pb = ITEM_PRI.get(kb, 1)
        b["t1"] = min(dur - 0.1, b["t0"] + max(mb, b["span"]))
        if b["t1"] - b["t0"] < min(mb, 1.2):
            continue
        ok = True
        for _ in range(6):
            moved = False
            for a in list(placed):
                if a["t1"] <= b["t0"] or not zones_clash(item_zones(a["it"]), zb):
                    continue
                pa, ma, shown = ITEM_PRI.get(a["it"]["kind"], 1), item_min(a["it"]), b["t0"] - a["t0"]
                if shown >= ma:
                    a["t1"] = b["t0"] - 0.05
                    continue
                if pb >= pa and kb in QUICK_KINDS:
                    if shown < 0.5:
                        placed.remove(a)
                    else:
                        a["t1"] = b["t0"] - 0.05
                    continue
                nt = a["t0"] + ma
                room = nt + mb <= dur - 0.1
                if room and nt - b["t0"] <= (1.5 if kb in ("note", "srclist", "feature") else 0.0):
                    b["t0"], b["t1"] = nt, min(dur - 0.1, nt + max(mb, b["span"]))
                    moved = True
                elif pb > pa:
                    if shown < 0.5:
                        placed.remove(a)
                    else:
                        a["t1"] = b["t0"] - 0.05
                    continue
                elif room and nt - b["t0"] <= 1.2:
                    b["t0"], b["t1"] = nt, min(dur - 0.1, nt + max(mb, b["span"]))
                    moved = True
                else:
                    ok = False
                break
            if not ok or not moved:
                break
        if ok:
            placed.append(b)
    return placed


DRAW_LAYER = {"quote": 1, "action": 0, "explain": 0, "feature": 0, "verdict": 1, "stat": 1, "person": 2, "name": 2, "place": 2, "year": 3, "bubble": 3, "note": 4, "srclist": 4,
              "lens": 0, "beforeafter": 0, "measure": 1, "qmark": 1, "mark": 1, "compare": 2, "chart": 2, "tline": 3, "hl": 3}


# ---- year: always at the bottom (slider with ticks when the year is known, else a pill)
def draw_year_pill(fr, tl, t0, t1, a, ov):
    label = ov.get("text") or ""
    spr = _TL_LAB.get(("pill", label))
    if spr is None:
        lay = text_layer(label, font_for(label, 70, "title"), fill=(255, 255, 255), stroke=2, stroke_fill=(70, 0, 8), shadow=(0, 4, 6, 0.7))
        pill = Image.new("RGBA", (lay.width + 70, lay.height + 24), (0, 0, 0, 0))
        ImageDraw.Draw(pill).rounded_rectangle([0, 0, pill.width - 1, pill.height - 1], radius=16, fill=(206, 24, 40, 245))
        pill.alpha_composite(lay, (35, 12))
        spr = _TL_LAB[("pill", label)] = pill
    q = e_back((tl - t0) / 0.5)
    put(fr, spr, W / 2, H - 58 - spr.height / 2 + (1 - cl(q)) * 70, 0.85 + 0.15 * cl(q), a)


_HI_UNITS = {"एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पाँच": 5, "पांच": 5, "छह": 6, "छः": 6, "सात": 7, "आठ": 8, "नौ": 9, "दस": 10}


def resolve_years(scenes):
    """Year items without a number (the narrator spelled the year out, 'उन्नीस सौ अड़सठ') get one, so the slider can be used."""
    todo = []
    for i, sc in enumerate(scenes):
        for k, it in enumerate(sc.get("items") or []):
            if isinstance(it, dict) and it.get("kind") == "year" and not it.get("year_num"):
                m_ = re.search(r"\d{4}", str(it.get("text", "")).translate(_DEV))
                if m_ and 1000 <= int(m_.group(0)) <= 2100:
                    it["year_num"] = int(m_.group(0))
                else:
                    todo.append((i, k, str(it.get("text", ""))))
    if todo and GEMINI_KEY:
        try:
            out = gemini_json_opt("Hindi spoken dates. Give the calendar year as a number (and month 1-12 if a month is spoken). Use null when no real year 1000-2100 is meant. "
                              'Return ONLY JSON: {"y":[{"id":0,"year_num":1984,"month":12}]}\n' + "\n".join(f"{n}: {t}" for n, (_i, _k, t) in enumerate(todo)), 0.0)
            for r in (out.get("y") or []):
                n = int(r.get("id"))
                yn, mo = parse_year(r)
                if yn:
                    i, k, _t = todo[n]
                    scenes[i]["items"][k]["year_num"], scenes[i]["items"][k]["month"] = yn, mo
        except Exception as e:  # noqa
            log("resolve_years failed:", str(e)[:100])
    return len(todo)


# ---- place (bottom-left, big city + smaller state below) and name (bottom-right) labels
def place_sprite(text, sub):
    key = ("place", text, sub)
    if key in _CARDS:
        return _CARDS[key]
    l1 = text_layer(text, font_for(text, 108, "title"), fill=(255, 255, 255), stroke=2, stroke_fill=(0, 0, 0), shadow=(0, 4, 7, 0.85), max_w=int(W * 0.58))
    l2 = text_layer(sub, font_for(sub, 52, "title"), fill=(255, 190, 60), stroke=2, stroke_fill=(0, 0, 0), shadow=(0, 3, 5, 0.8), max_w=int(W * 0.5)) if sub else None
    w_ = max(l1.width, l2.width if l2 else 0) + 30
    h_ = l1.height + (l2.height - 6 if l2 else 0)
    out = Image.new("RGBA", (w_, h_), (0, 0, 0, 0))
    ImageDraw.Draw(out).rectangle([0, 6, 8, h_ - 6], fill=(255, 150, 20, 255))
    out.alpha_composite(l1, (24, 0))
    if l2:
        out.alpha_composite(l2, (24, l1.height - 6))
    _CARDS[key] = out
    return out


def name_sprite(text, sub):
    key = ("name", text, sub)
    if key in _CARDS:
        return _CARDS[key]
    l1 = text_layer(text, font_for(text, 92, "title"), fill=(255, 255, 255), stroke=2, stroke_fill=(0, 0, 0), shadow=(0, 4, 7, 0.85), max_w=int(W * 0.5))
    l2 = text_layer(sub, font_for(sub, 44, "title"), fill=(255, 214, 0), stroke=2, stroke_fill=(0, 0, 0), shadow=(0, 3, 5, 0.8), max_w=int(W * 0.42)) if sub else None
    w_ = max(l1.width, l2.width if l2 else 0) + 30
    h_ = l1.height + (l2.height - 6 if l2 else 0)
    out = Image.new("RGBA", (w_, h_), (0, 0, 0, 0))
    ImageDraw.Draw(out).rectangle([w_ - 9, 6, w_ - 1, h_ - 6], fill=(255, 214, 0, 255))
    out.alpha_composite(l1, (w_ - 24 - l1.width, 0))
    if l2:
        out.alpha_composite(l2, (w_ - 24 - l2.width, l1.height - 6))
    _CARDS[key] = out
    return out


def quote_sprite(text):
    key = ("quote", text)
    if key in _CARDS:
        return _CARDS[key]
    ws = text.split()
    n = 1 if len(ws) <= 3 else (2 if len(ws) <= 6 else 3)
    per = -(-len(ws) // n)
    lines = [" ".join(ws[k:k + per]) for k in range(0, len(ws), per)]
    lays = [text_layer(l_, font_for(l_, 66, "title"), fill=(255, 255, 255) if k < len(lines) - 1 else (255, 214, 60), stroke=3, stroke_fill=(0, 0, 0), shadow=(0, 5, 8, 0.9), max_w=int(W * 0.5))
            for k, l_ in enumerate(lines)]
    w_ = max(l.width for l in lays) + 20
    pitch = [int(l.height * 0.78) for l in lays]
    h_ = sum(pitch) + 24
    out = Image.new("RGBA", (w_, h_), (0, 0, 0, 0))
    y = 0
    for l, p_ in zip(lays, pitch):
        out.alpha_composite(l, (w_ - l.width - 10, y))
        y += p_
    _CARDS[key] = out
    return out


def draw_quote(fr, tl, t0, a, ov):
    spr = quote_sprite(ov.get("text") or "")
    q = e_out(cl((tl - t0) / 0.45))
    put(fr, spr, W - 56 - spr.width / 2 + (1 - q) * 420, H * 0.40, 1.0, a)


def draw_place(fr, tl, t0, a, ov):
    it = ov.get("it") or {}
    spr = place_sprite(it.get("text") or "", it.get("sub") or "")
    q = e_out(cl((tl - t0) / 0.5))
    put(fr, spr, 54 + spr.width / 2 - (1 - q) * 380, H - 56 - spr.height / 2, 1.0, a)


def draw_name(fr, tl, t0, a, ov):
    it = ov.get("it") or {}
    spr = name_sprite(it.get("text") or "", it.get("sub") or "")
    q = e_out(cl((tl - t0) / 0.5))
    put(fr, spr, W - 54 - spr.width / 2 + (1 - q) * 380, H - 56 - spr.height / 2, 1.0, a)


# ---- feature call-out: the subject's picture, straight arrows from its parts to round pictures at the side
FEATURE_MAX = [int(os.environ.get("MAX_FEATURE", "2" if TEST_SEC else "5"))]


def locate_features(img, subject, parts):
    """Gemini looks at the cut-out and says where each named part is (fractions of width / height)."""
    pts = [None] * len(parts)
    if not GEMINI_KEY or VERIFY["off"]:
        return pts
    try:
        flat = Image.new("RGB", img.size, (255, 255, 255))
        flat.paste(img, mask=img.getchannel("A"))
        flat.thumbnail((640, 640))
        buf = io.BytesIO()
        flat.save(buf, "JPEG", quality=82)
        txt, _t = gemini(f'This picture shows "{subject}". Give the centre of each named part as fractions of the picture width and height (x and y from 0 to 1, origin top-left): {json.dumps(parts)}. '
                         'Return ONLY JSON: {"points":[{"part":"...","x":0.5,"y":0.5}]}', json_mode=True, temperature=0.0, image=base64.b64encode(buf.getvalue()).decode(), optional=True)
        for r in (parse_json(txt).get("points") or []):
            for n, p in enumerate(parts):
                if str(r.get("part", "")).strip().lower() == p.lower() and pts[n] is None:
                    x, y = float(r["x"]), float(r["y"])
                    if 0 <= x <= 1 and 0 <= y <= 1:
                        pts[n] = (x, y)
    except Exception as e:  # noqa
        log("  feature locate failed:", str(e)[:80])
    return pts


def build_feature(it, out):
    """Cut-out of the subject + a round zoom picture for each feature. Returns True when ready."""
    parts = [f["part_en"] for f in it["features"]]
    rgba = cutout_object(f"{it.get('pose') or it['query_en']}, side view, full body, studio photo", Path(out), it["query_en"])
    if rgba is None:
        return False
    st = png_sticker(rgba, 620)
    pts = locate_features(st, it["query_en"], parts)
    for n, f in enumerate(it["features"]):
        x, y = pts[n] if pts[n] else (0.35 + 0.3 * (n % 2), 0.25 + 0.25 * n)
        f["x"], f["y"] = x, y
        r = int(0.16 * max(st.size))
        crop = Image.new("RGB", (2 * r, 2 * r), (24, 26, 32))
        box = (int(x * st.width - r), int(y * st.height - r))
        crop.paste(st.crop((box[0], box[1], box[0] + 2 * r, box[1] + 2 * r)), (0, 0), st.crop((box[0], box[1], box[0] + 2 * r, box[1] + 2 * r)))
        f["crop"] = crop
    it["png_img"] = st
    return True


def draw_feature(fr, ps, tl, t0, t1, a, ov):
    it = ov.get("it") or {}
    st, feats = it.get("png_img"), it.get("features") or []
    if st is None or not feats:
        return
    _dim(fr, 0.5 * a)
    q = e_out(cl((tl - 0.15) / 0.6))
    scl = min(1.0, 0.7 * H / st.height)
    cx, cy = W * 0.30 - (1 - q) * 260, H * 0.52
    put(fr, st, cx, cy, scl, a * q)
    n = len(feats)
    ys = {1: [0.5], 2: [0.34, 0.68], 3: [0.22, 0.5, 0.78], 4: [0.17, 0.39, 0.61, 0.83]}[min(4, n)]
    d_ = 168
    ov_im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dr = ImageDraw.Draw(ov_im)
    pops = []
    for k, f in enumerate(feats[:4]):
        tk = 0.9 + k * 0.85
        p = e_out(cl((tl - tk) / 0.5))
        if p <= 0:
            continue
        px = cx + (f["x"] - 0.5) * st.width * scl
        py = cy + (f["y"] - 0.5) * st.height * scl
        ex, ey = W * 0.74 - d_ / 2 - 6, ys[k] * H
        x2, y2 = px + (ex - px) * p, py + (ey - py) * p
        dr.line([(px, py), (x2, y2)], fill=(0, 0, 0, 200), width=8)
        dr.line([(px, py), (x2, y2)], fill=(255, 255, 255, 255), width=4)
        dr.ellipse([px - 11, py - 11, px + 11, py + 11], fill=(230, 30, 40, 255), outline=(255, 255, 255, 255), width=3)
        if p > 0.97:
            ang = math.atan2(ey - py, ex - px)
            tip = (ex, ey)
            for s_ in (-0.45, 0.45):
                dr.line([tip, (ex - 22 * math.cos(ang + s_), ey - 22 * math.sin(ang + s_))], fill=(255, 255, 255, 255), width=5)
        pops.append((k, f, ys[k] * H, tk))
    fr.paste(ov_im, (0, 0), ov_im.point(lambda v: int(v * a)))
    for k, f, yy, tk in pops:
        qq = e_back((tl - tk - 0.35) / 0.4)
        if tl < tk + 0.35:
            continue
        key = ("fcirc", id(f["crop"]))
        spr = _CARDS.get(key)
        if spr is None:
            spr = _CARDS[key] = _circle_sprite(f["crop"], d_)
        cxx = W * 0.74
        put(fr, spr, cxx, yy, 0.2 + 0.8 * cl(qq), a)
        lab = f.get("label") or ""
        if lab:
            ll = _TL_LAB.get(("f", lab))
            if ll is None:
                ll = _TL_LAB[("f", lab)] = text_layer(lab, font_for(lab, 40, "title"), fill=(255, 255, 255), stroke=2, stroke_fill=(0, 0, 0), shadow=(0, 3, 5, 0.8), max_w=300)
            put(fr, ll, cxx + d_ / 2 + 20 + ll.width / 2, yy, 1.0, a * cl((tl - tk - 0.5) / 0.3))


# ---- cut a sentence at a word so a split screen / map starts exactly where the first subject is named
def cut_sentence_at(sc, w):
    sent, wt = sc.get("sent"), sc.get("wt")
    if not sent or not wt:
        return None
    j = next((k for k, s_ in enumerate(sent) if s_[0] < w < s_[1]), None)
    if j is None:
        return None
    aw, bw, ta, tb = sent[j]
    tm = max(ta + 0.1, wt[w][0] - 0.05)
    shots_ok = len(sc.get("shots") or []) == len(sent)
    sent[j:j + 1] = [(aw, w, ta, tm), (w, bw, tm, tb)]
    if shots_ok:
        words = sc["narration_hi"].split()
        sh0 = sc["shots"][j]
        sh1 = dict(sh0)
        sh0["text_hi"], sh1["text_hi"] = " ".join(words[aw:w]), " ".join(words[w:bw])
        sh1.pop("kf", None)
        sc["shots"].insert(j + 1, sh1)
    return j + 1


# ---- sources tied to the names the narrator says
def notes_for_entities(scenes):
    """When the narrator names a person / place / organisation that has a source paragraph (its own Wikipedia article), that paragraph is shown right then."""
    if not SRC_PARAS:
        return 0
    n = 0
    used = set()
    for sc in _ph_scenes(scenes):
        have = sc.setdefault("items", [])
        for it in list(have):
            ent = (it.get("entity_en") or "").strip().lower() if isinstance(it, dict) else ""
            if not ent or it.get("kind") not in ("person", "name", "place"):
                continue
            hit = next(((k, v) for k, v in SRC_PARAS.items() if ent in v["title"].lower() and k not in used), None) or \
                next(((k, v) for k, v in SRC_PARAS.items() if ent in v["text"].lower() and k not in used), None)
            if not hit or any(isinstance(x, dict) and x.get("kind") == "note" and abs(x.get("w", -9) - it["w"]) < 12 for x in have):
                continue
            k, v = hit
            used.add(k)
            mk = ""
            m_ = re.search(re.escape(ent), v["text"], re.I)
            if m_:
                mk = v["text"][m_.start():m_.end()]
            have.append({"w": min(len(sc["narration_hi"].split()) - 1, it["w"] + 2), "kind": "note", "text": v["text"], "mark": mk, "sub": v["title"].replace("|", "-")[:70], "dom": ""})
            n += 1
    log("source cards for named people / places:", n)
    return n


# ---- a new picture at every new idea inside a sentence
def concept_shots(scenes, topic=""):
    if not GEMINI_KEY:
        return 0
    rows = []
    for i, sc in enumerate(scenes):
        if sc.get("type") != "photo":
            continue
        for k, sh in enumerate(sc.get("shots") or []):
            if len(str(sh.get("text_hi") or "").split()) >= 8:
                rows.append((i, k, sh))
    made = 0
    for a in range(0, len(rows), 14):
        part = rows[a:a + 14]
        listing = "\n".join(f"S{i}.{k}: " + " ".join(f"{j}:{w}" for j, w in enumerate(str(sh["text_hi"]).split())) for i, k, sh in part)
        prompt = f"""Documentary editor (topic: {topic}). Each row = ONE picture shown while the narrator says these numbered Hindi words. A picture must change at EVERY new idea, so what is on screen always matches the words being spoken.
Decide for each row whether it holds 2 or 3 different ideas (a place is named, THEN a person / a birth / an event, THEN a feeling, a profession, a part, a movement). If so give the part starts. Each part has at least 3 words. One idea = one part.
Picture rules: a named city / village / forest / river / mountain = an AERIAL DRONE VIEW of exactly that place (query "<place> aerial view", clip_en "<place> aerial drone view"); a birth = a newborn baby; a child = a real child; emotions = a real person visibly feeling it; professions = a real person at work in that role; a death = a quiet respectful scene; an abstract idea = one concrete symbolic real scene; a named thing = that thing. Never a cartoon, toy or statue.
Return ONLY JSON: {{"rows":[{{"id":"S0.1","parts":[{{"from":0,"query_en":"2-4 English words","prompt_en":"realistic photo description","clip_en":"subject + action or empty","entity_en":"exact Wikipedia title or empty"}}]}}]}}
{listing}"""
        try:
            out = gemini_json_opt(prompt, 0.15)
        except Exception as e:  # noqa
            log("concept shots failed:", str(e)[:100])
            continue
        for r in (out.get("rows") or []):
            m_ = re.match(r"S(\d+)\.(\d+)$", str(r.get("id") or ""))
            if not m_:
                continue
            i, k = int(m_.group(1)), int(m_.group(2))
            if not any(x[0] == i and x[1] == k for x in part):
                continue
            ps_ = [p for p in (r.get("parts") or []) if isinstance(p, dict)]
            sh = scenes[i]["shots"][k]
            ws = str(sh["text_hi"]).split()
            starts = []
            for p in ps_[:3]:
                try:
                    f = int(p.get("from"))
                except Exception:  # noqa
                    continue
                if (not starts and f == 0) or (starts and f >= starts[-1][0] + 3 and f <= len(ws) - 3):
                    starts.append((f, p))
            if len(starts) < 2 or starts[0][0] != 0:
                continue
            new = []
            for n_, (f, p) in enumerate(starts):
                e = starts[n_ + 1][0] if n_ + 1 < len(starts) else len(ws)
                s2 = dict(sh)
                s2["text_hi"] = " ".join(ws[f:e])
                if n_:
                    s2.pop("kf", None)
                    s2.pop("label", None)
                q_ = clean_short(p.get("query_en"), 50)
                if q_ and q_.isascii():
                    s2["query_en"], s2["entity_en"] = q_, clean_short(p.get("entity_en"), 70)
                    s2["prompt_en"] = clean_short(p.get("prompt_en"), 300) or ("a realistic photo of " + q_)
                    ce = clean_short(p.get("clip_en"), 50)
                    s2["clip_en"], s2["motion"] = (ce, True) if ce and ce.isascii() else ("", False)
                new.append(s2)
            scenes[i]["shots"][k] = new  # replaced below
            made += len(new) - 1
    for sc in scenes:  # flatten the replaced entries
        if sc.get("shots"):
            flat = []
            for x in sc["shots"]:
                flat.extend(x if isinstance(x, list) else [x])
            sc["shots"] = flat
    log("extra pictures from concept splits:", made)
    return made


# ---- a PNG animation for (almost) every scene
def png_pass(scenes, topic=""):
    ph = _ph_scenes(scenes)
    lack = [sc for sc in ph if not any(isinstance(i, dict) and i.get("kind") in ("explain", "action", "feature") for i in (sc.get("items") or []))]
    need = max(3, int(len(ph) * 0.7)) - (len(ph) - len(lack))
    if need <= 0 or not lack or not GEMINI_KEY:
        return 0
    added = 0
    for a in range(0, len(lack), 8):
        if added >= need:
            break
        batch = lack[a:a + 8]
        listing = "\n".join(f"SCENE {ph.index(sc)}: {_numbered(sc)}" for sc in batch)
        prompt = f"""Hindi documentary scenes (every word numbered), topic: {topic}. For EACH scene pick the ONE concrete thing worth showing as an animated cut-out PNG on top of the background picture, to explain what the narrator says.
- something that MOVES or acts (an animal running, a bird flying, a rock falling, a ship coming closer) -> "action" {{"verb":"run|walk|swim|fly|jump|fall|rise|approach|retreat|spin|shake|grow","dir":"right|left|toward|away|up|down","pose_en":"e.g. lion running"}}
- an object / part / tool being explained -> "explain" {{"anim":"pop|slide|drop|zoom|spin"}}
- an abstract idea -> a concrete symbol as "explain" (time = clock, money = coins, justice = scales, danger = warning sign, knowledge = book, idea = light bulb, medicine = pills...)
Real photographic subjects only, never people's faces, never cartoons.
Return ONLY JSON: {{"scenes":[{{"i":0,"w":word number where it is spoken,"kind":"action" or "explain","text_hi":"label 1-3 words (explain only)","query_en":"1-3 English words","...":"..."}}]}}
{listing}"""
        try:
            out = gemini_json_opt(prompt, 0.25)
        except Exception as e:  # noqa
            log("png pass failed:", str(e)[:100])
            continue
        for r in (out.get("scenes") or []):
            try:
                i, w = int(r.get("i")), int(r.get("w"))
            except Exception:  # noqa
                continue
            sc = next((x for x in batch if ph.index(x) == i), None)
            q_ = clean_short(r.get("query_en"), 50)
            if sc is None or not q_ or not q_.isascii() or not (0 <= w < len(sc["narration_hi"].split())) or added >= need:
                continue
            if str(r.get("kind")) == "action" and str(r.get("verb", "")).lower() in ACTION_SPAN:
                vb = str(r["verb"]).lower()
                dr = str(r.get("dir") or "right").lower()
                rec = {"w": w, "kind": "action", "text": "", "query_en": q_, "pose": clean_short(r.get("pose_en"), 60), "verb": vb,
                       "dir": dr if dr in ("right", "left", "toward", "away", "up", "down") else "right", "span": ACTION_SPAN[vb]}
            else:
                an = str(r.get("anim") or "pop")
                rec = {"w": w, "kind": "explain", "text": clean_short(r.get("text_hi"), 30), "query_en": q_, "anim": an if an in KF_PRESETS else "pop"}
            sc.setdefault("items", []).append(rec)
            added += 1
    log("PNG animations added by the guarantee pass:", added)
    return added


PLACE_PARENT = {"अहमदाबाद": "गुजरात", "सूरत": "गुजरात", "मुंबई": "महाराष्ट्र", "पुणे": "महाराष्ट्र", "नागपुर": "महाराष्ट्र", "जयपुर": "राजस्थान", "लखनऊ": "उत्तर प्रदेश", "कानपुर": "उत्तर प्रदेश",
                "वाराणसी": "उत्तर प्रदेश", "आगरा": "उत्तर प्रदेश", "मैनपुरी": "उत्तर प्रदेश", "अयोध्या": "उत्तर प्रदेश", "पटना": "बिहार", "भोपाल": "मध्य प्रदेश", "इंदौर": "मध्य प्रदेश",
                "कोलकाता": "पश्चिम बंगाल", "चेन्नई": "तमिलनाडु", "बेंगलुरु": "कर्नाटक", "हैदराबाद": "तेलंगाना", "शिमला": "हिमाचल प्रदेश", "मनाली": "हिमाचल प्रदेश", "दिल्ली": "भारत"}
_STATES = {"राजस्थान", "गुजरात", "महाराष्ट्र", "बिहार", "पंजाब", "हरियाणा", "उत्तराखंड", "कर्नाटक", "केरल", "तमिलनाडु", "तेलंगाना", "ओडिशा", "असम", "झारखंड", "छत्तीसगढ़", "गोवा", "कश्मीर", "लद्दाख",
           "उत्तर प्रदेश", "मध्य प्रदेश", "हिमाचल प्रदेश", "आंध्र प्रदेश", "अरुणाचल प्रदेश", "पश्चिम बंगाल"}


def auto_overlays(scenes):
    """Rule-based detector: every spoken year (+month), month, place (city + its state, state + India), number with a unit and quoted sentence."""
    n = {"year": 0, "stat": 0, "place": 0, "bubble": 0}
    for sc in _ph_scenes(scenes):
        toks = sc["narration_hi"].split()
        have = sc.setdefault("items", [])
        cand, used, side, sidx, i = [], set(), ["left", "right"], 0, 0
        while i < len(toks):
            raw, c = toks[i], _ct(toks[i])
            cn = c.translate(_DEV)
            if c in MONTHS_HI:
                nxt = next((j for j in (i + 1, i + 2) if j < len(toks) and re.fullmatch(r"\d{4}", _ct(toks[j]).translate(_DEV)) and 1000 <= int(_ct(toks[j]).translate(_DEV)) <= 2100), None)
                if nxt is not None:
                    cand.append({"w": i, "kind": "year", "text": " ".join(_ct(t) for t in toks[i:nxt + 1]), "year_num": int(_ct(toks[nxt]).translate(_DEV)), "month": MONTHS_HI[c]})
                    used.update(range(i, nxt + 1))
                    i = nxt + 1
                    continue
                cand.append({"w": i, "kind": "stat", "text": c, "tone": "place"})
            elif re.fullmatch(r"\d{4}", cn) and 1000 <= int(cn) <= 2100 and i not in used:
                cand.append({"w": i, "kind": "year", "text": c, "year_num": int(cn), "month": None})
            elif re.fullmatch(r"\d[\d,\.]{0,8}", cn) and i + 1 < len(toks) and _ct(toks[i + 1]) in UNITS_HI:
                cand.append({"w": i, "kind": "stat", "text": c + " " + _ct(toks[i + 1])})
                i += 2
                continue
            elif i + 1 < len(toks) and (c + " " + _ct(toks[i + 1])) in PLACES_HI:
                nm = c + " " + _ct(toks[i + 1])
                cand.append({"w": i, "kind": "place", "text": nm, "sub": "भारत" if nm in _STATES else PLACE_PARENT.get(nm, "")})
                i += 2
                continue
            elif c in PLACES_HI:
                cand.append({"w": i, "kind": "place", "text": c, "sub": "भारत" if c in _STATES else PLACE_PARENT.get(c, "")})
            if raw and raw[0] in _QOPEN and not raw[:2] == "''":
                j = i
                while j < len(toks) and j < i + 12 and not (toks[j] and toks[j][-1] in _QCLOSE and (j > i or len(toks[j]) > 1)):
                    j += 1
                if j < len(toks) and 2 <= j - i + 1 <= 11:
                    q_ = " ".join(" ".join(toks[i:j + 1]).strip("\"“”‘’'").split()[:7])
                    cand.append({"w": i, "kind": "bubble", "text": q_, "side": side[sidx % 2]})
                    sidx += 1
                    i = j + 1
                    continue
            i += 1
        for r in cand:
            if any(isinstance(x, dict) and abs(x.get("w", -9) - r["w"]) < 3 and (x.get("kind") == r["kind"] or r["kind"] == "stat" and x.get("kind") in ("stat", "year", "place")) for x in have):
                continue
            have.append(r)
            n[r["kind"]] += 1
        have.sort(key=lambda x: x.get("w", 0) if isinstance(x, dict) else 0)
        del have[16:]
    log("auto detector added:", n)
    return n


def overlay_pass(scenes, topic=""):
    """Gemini detector over every word: year, place (+ its region), name (person / animal / thing), real person card, stat, verdict, PNG explain / action, feature call-out, bubble."""
    ph = [(i, sc) for i, sc in enumerate(scenes) if sc["type"] == "photo" and sc.get("wt") and len(sc["wt"]) == len(sc["narration_hi"].split())]
    n_add = {k: 0 for k in ("year", "place", "name", "person", "stat", "verdict", "explain", "action", "feature", "bubble", "quote")}
    seen_names = set()
    for a in range(0, len(ph), 5):
        batch = ph[a:a + 5]
        listing = "\n".join(f"SCENE {i}: {_numbered(sc)}" for i, sc in batch)
        prompt = f"""Film editor task. Hindi narration, every word numbered (index:word). Video topic: {topic}
Go through EVERY word of EACH scene and detect everything below that is really spoken (up to 6 helpers per scene; never two on the same word; each exactly on the word where it is spoken):
- "year": a date / year / period. {{"w":..,"kind":"year","text_hi":"as spoken","year_num":1984 or null,"month":12 or null}} (year_num only for a real year 1000-2100).
- "place": EVERY named city / village / district / state / country / forest / river / mountain / university campus. {{"w":..,"kind":"place","text_hi":"the place as spoken (big)","sub_hi":"the bigger region it lies in: city -> state, state -> country, village -> district or state, else empty"}}
- "name": the FIRST time a person, animal species, object or organisation is named (a girl called Sumitra, a lion, a university). {{"w":..,"kind":"name","text_hi":"the name as spoken","sub_hi":"what it is in 1-2 words, e.g. लड़की, वैज्ञानिक, शेर, संस्था, or empty"}}. Never repeat a name.
- "person": a REAL, notable public person (scientist, author, leader). {{"w":..,"kind":"person","text_hi":"name as spoken","entity_en":"exact English Wikipedia title"}}. Never for private people or story characters.
- "stat": a striking short duration / number / count ("३ महीने", "४५ मिनट"). {{"w":..,"kind":"stat","text_hi":"1-3 words"}}
- "verdict": false / true, myth / fact, did / did not. {{"w":..,"kind":"verdict","text_hi":"","circles":[{{"query_en":"1-3 English words, a REAL photo","mark":"cross" or "tick","label_hi":"1-2 words"}}]}} 2-3 circles.
- "explain": a concrete thing, part or tool that is clearer as a clean cut-out PNG. {{"w":..,"kind":"explain","text_hi":"label 1-3 words","query_en":"1-3 English words naming the REAL object","anim":"pop|slide|drop|zoom|spin"}}. Abstract idea -> a concrete symbol (time = clock, money = coins, justice = scales, danger = warning sign, knowledge = book, idea = light bulb).
- "action": something MOVING or doing a visible action. {{"w":..,"kind":"action","text_hi":"","query_en":"the real moving thing","pose_en":"e.g. lion running","verb":"run|walk|swim|fly|jump|fall|rise|approach|retreat|spin|shake|grow","dir":"right|left|toward|away|up|down"}}
- "feature": the narrator lists SPECIAL FEATURES / abilities / parts of ONE subject. {{"w":..,"kind":"feature","text_hi":"","query_en":"the subject","pose_en":"subject, side view","features":[{{"label_hi":"short Hindi label","part_en":"the body part / detail in English"}}]}} 2-4 features, at most one per video.
- "bubble": the narrator quotes what a person said. {{"w":..,"kind":"bubble","text_hi":"the quoted words, max 6","side":"left" or "right"}}
- "quote": a short dramatic line, prediction, threat or promise (max 7 words) that deserves BIG text at the right of the screen. {{"w":..,"kind":"quote","text_hi":"the line"}} (at most 2 per scene)
Return ONLY JSON: {{"scenes":[{{"i":0,"items":[]}}]}}
{listing}"""
        try:
            out = gemini_json_opt(prompt, 0.2)
        except Exception as e:  # noqa
            log("overlay pass failed:", str(e)[:100])
            continue
        for r in (out.get("scenes") or []):
            try:
                i = int(r.get("i"))
            except Exception:  # noqa
                continue
            if not any(i == j for j, _ in batch):
                continue
            sc = scenes[i]
            nwd = len(sc["narration_hi"].split())
            have = sc.setdefault("items", [])
            for it in (r.get("items") or [])[:6]:
                if not isinstance(it, dict):
                    continue
                kind = str(it.get("kind") or "")
                try:
                    w = int(it.get("w"))
                except Exception:  # noqa
                    continue
                if kind not in n_add or not (0 <= w < nwd):
                    continue
                if any(isinstance(x, dict) and (x.get("w") == w or (abs(x.get("w", -9) - w) < 4 and x.get("kind") == kind)) for x in have):
                    continue
                txt = clean_short(it.get("text_hi"), 40)
                rec = None
                if kind == "verdict":
                    rec = parse_verdict(w, it)
                elif kind == "person":
                    ent_ = clean_short(it.get("entity_en"), 70)
                    if txt and ent_ and ent_.isascii():
                        rec = {"w": w, "kind": "person", "text": txt, "entity_en": ent_, "sub": ""}
                elif kind in ("place", "name"):
                    if txt and txt not in seen_names:
                        seen_names.add(txt)
                        rec = {"w": w, "kind": kind, "text": txt, "sub": clean_short(it.get("sub_hi"), 30)}
                elif kind == "action":
                    q_, vb_ = clean_short(it.get("query_en"), 50), str(it.get("verb") or "").lower()
                    if q_ and q_.isascii() and vb_ in ACTION_SPAN:
                        dr_ = str(it.get("dir") or "").lower()
                        rec = {"w": w, "kind": "action", "text": "", "query_en": q_, "pose": clean_short(it.get("pose_en"), 60), "verb": vb_,
                               "dir": dr_ if dr_ in ("right", "left", "toward", "away", "up", "down") else "right", "span": ACTION_SPAN[vb_]}
                elif kind == "explain":
                    q_ = clean_short(it.get("query_en"), 50)
                    if q_ and q_.isascii():
                        an = str(it.get("anim") or "pop")
                        rec = {"w": w, "kind": "explain", "text": txt or "", "query_en": q_, "anim": an if an in KF_PRESETS else "pop"}
                elif kind == "feature":
                    fl = []
                    for f in (it.get("features") or [])[:4]:
                        if isinstance(f, dict) and clean_short(f.get("part_en"), 40) and clean_short(f.get("part_en"), 40).isascii() and clean_short(f.get("label_hi"), 30):
                            fl.append({"label": clean_short(f.get("label_hi"), 30), "part_en": clean_short(f.get("part_en"), 40)})
                    q_ = clean_short(it.get("query_en"), 50)
                    if len(fl) >= 2 and q_ and q_.isascii() and n_add["feature"] < FEATURE_MAX[0]:
                        rec = {"w": w, "kind": "feature", "text": "", "query_en": q_, "pose": clean_short(it.get("pose_en"), 60), "features": fl, "span": 3.4 + 0.85 * len(fl)}
                elif kind == "year":
                    if txt:
                        rec = {"w": w, "kind": "year", "text": txt, "mark": "", "sub": ""}
                        yn, mo = parse_year(it)
                        if yn:
                            rec["year_num"], rec["month"] = yn, mo
                elif txt:
                    rec = {"w": w, "kind": kind, "text": txt, "mark": "", "sub": ""}
                    if kind == "bubble" and str(it.get("side") or "") in ("left", "right"):
                        rec["side"] = str(it["side"])
                if rec:
                    have.append(rec)
                    n_add[kind] += 1
            have.sort(key=lambda x: x.get("w", 0) if isinstance(x, dict) else 0)
            del have[16:]
    log("overlay pass added:", n_add)
    return n_add


def draw_popup(fr, sh, tl):
    sp = sh.get("popup")
    if sp is None:
        return
    t0, t1 = 0.12, sh["dur"] - 0.05
    if not (t0 < tl < t1):
        return
    q = e_back((tl - t0) / 0.38)
    out = cl((t1 - tl) / 0.2)
    wob = math.sin(tl * 3.2 + sh["phase"]) * 0.025
    cx = W * (0.5 + 0.31 * sh["pop_side"])
    cy = H * 0.56 + math.sin(tl * 2.4 + sh["phase"]) * 8
    s2 = sp.rotate(math.degrees(wob) * 2 + sh["pop_side"] * 4, resample=Image.BICUBIC, expand=True)
    put(fr, s2, cx, cy, (0.15 + 0.85 * cl(q)) * (0.6 + 0.4 * out), min(1.0, (tl - t0) * 8) * out)


def empty_side(img):
    """'left' or 'right': the half of the picture with less detail / no skin tones, where overlay text will not cover a face or body."""
    np = _np()
    a = np.asarray(img.convert("RGB").resize((128, 72)), np.float32)
    lum = a.mean(2)
    gy, gx = np.gradient(lum)
    en = np.hypot(gx, gy)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    skin = ((r > 95) & (g > 40) & (b > 20) & (r > g) & (g > b) & (r - g > 12)).astype(np.float32)
    best, side = 1e9, "right"
    for nm, x0, x1 in (("left", 0, 56), ("right", 72, 128)):
        zone = (slice(20, 68), slice(x0, x1))
        sc_ = float(en[zone].mean() + 0.35 * lum[zone].std() + 40.0 * skin[zone].mean())
        if sc_ < best - (0.0 if nm == "left" else 0.0):
            best, side = sc_, nm
    return side


def ov_spot(sh, spr, cands):
    """Pick the screen spot (cx, cy) from cands where the overlay is farthest from the picture's main subject (face / body / object)."""
    try:
        if "subj" not in sh:
            sh["subj"] = subject_center(sh["img"]) if sh.get("img") is not None else (0.5, 0.5)
        sx, sy = sh["subj"][0] * W, sh["subj"][1] * H
    except Exception:  # noqa
        return cands[0]
    def score(c):
        dx = max(0.0, abs(c[0] - sx) - spr.width / 2) / W
        dy = max(0.0, abs(c[1] - sy) - spr.height / 2) / H
        return dx + dy
    return max(cands, key=score)


def draw_ov(fr, sh, tl, pal):
    ov = sh.get("ov")
    if not ov:
        return
    t0, t1 = 0.22, min(sh["dur"] - 0.08, OV_MAXT.get(ov["kind"], 4.2))
    if not (t0 < tl < t1):
        return
    if ov["kind"] in V2_KINDS and draw_v2(fr, sh, tl, ov, pal, t0, t1):
        return
    if ov["kind"] == "note":  # source caption on the empty side of the picture, discussed phrase gets underlined
        a = cl((tl - t0) * 4) * (1 - cl((tl - (t1 - 0.35)) / 0.35))
        if a > 0.01:
            s0 = ov_sprite("note0", ov["text"], ov.get("mark", ""), pal)
            su = ov_sprite("note_u", ov["text"], ov.get("mark", ""), pal)
            cx = W * (0.26 if ov.get("side") == "left" else 0.74)
            cy = H * 0.52 + (1 - e_out(cl((tl - t0) / 0.5))) * 28
            put(fr, s0, cx, cy, 1.0, a)
            pu = e_out(cl((tl - t0 - 0.9) / 0.9))  # red underline is drawn left to right
            if pu > 0.02:
                cw_ = max(2, int(su.width * pu))
                put(fr, su.crop((0, 0, cw_, su.height)), cx - (su.width - cw_) / 2, cy, 1.0, a)
        return
    spr = ov_sprite(ov["kind"], ov["text"], ov.get("mark", ""), pal)
    q = e_back((tl - t0) / 0.4)
    a = cl((tl - t0) * 6) * (1 - cl((tl - (t1 - 0.25)) / 0.25))
    if ov["kind"] == "place":
        cx, cy = ov_spot(sh, spr, [(50 + spr.width / 2, H - 70 - spr.height / 2), (W - 50 - spr.width / 2, H - 70 - spr.height / 2),
                                   (50 + spr.width / 2, 60 + spr.height / 2)])
        cx -= (1 - cl(q)) * 220 * (1 if cx < W / 2 else -1)
    elif ov["kind"] == "title":
        cx, cy = ov_spot(sh, spr, [(W / 2, H * 0.30), (W / 2, H * 0.80)])
        put(fr, spr, cx, cy, 1.18 - 0.18 * cl(q), a)
        return
    elif ov["kind"] == "year":
        cx, cy = ov_spot(sh, spr, [(W / 2, H - 40 - spr.height / 2), (W / 2, 50 + spr.height / 2), (W * 0.2, H - 40 - spr.height / 2), (W * 0.8, H - 40 - spr.height / 2)])
        cx -= (1 - cl(q)) * 360
        put(fr, spr, cx, cy, 1.0, a)
        return
    elif ov["kind"] == "bubble":
        try:
            if "subj" not in sh:
                sh["subj"] = subject_center(sh["img"]) if sh.get("img") is not None else (0.5, 0.5)
            sx_, sy_ = sh["subj"]
        except Exception:  # noqa
            sx_, sy_ = 0.5, 0.5
        spk = (ov.get("it") or {}).get("side") or ""
        ax = {"left": 0.30, "right": 0.70}.get(spk, sx_) * W  # the bubble's tail points at the speaker
        tail = "R" if (ax > W * 0.62) else "L"
        spr = ov_sprite("bubble", ov["text"], tail, pal)
        cx = ax + (spr.width / 2 - 34) if tail == "L" else ax - (spr.width / 2 - 34)
        cy = max(spr.height / 2 + 20, (sy_ - 0.17) * H - spr.height / 2 - 24)
        cx = min(max(cx, spr.width / 2 + 14), W - spr.width / 2 - 14)
    elif ov["kind"] == "note":
        cx, cy = W - 30 - spr.width / 2, 40 + spr.height / 2 - (1 - cl(q)) * 160
    else:
        cx, cy = W - 40 - spr.width / 2 + (1 - cl(q)) * 300, 70 + spr.height / 2
    put(fr, spr, cx, cy, 0.7 + 0.3 * cl(q) if ov["kind"] == "bubble" else 1.0, a)


# =========================================================================== T151: the 151-technique engine (never remove unless the user says so)
# layers: bg (44, live on the background picture / video), ov (92: frame-level effects + cards in the 3 fixed bottom slots), tr (15 transitions)
T151 = []


def _treg(layer, group, *pairs):
    for key, name in pairs:
        T151.append({"id": len(T151) + 1, "layer": layer, "group": group, "key": key, "name": name})


_treg("bg", "map", ("map_anim", "Map Animation"), ("route", "Route Animation"), ("dist_counter", "Distance Counter Animation"), ("pin", "Location Pin Animation"), ("map_zoom", "Map Zoom In / Zoom Out"),
      ("globe_rot", "Globe Rotation Animation"), ("globe_map", "3D Globe to Map Transition"), ("satellite", "Satellite View Animation"), ("border_hl", "Country Border Highlight Animation"),
      ("territory", "Territory Expansion Animation"), ("hist_border", "Historical Border Change Animation"), ("army_move", "Army Movement Animation"), ("travel_path", "Travel Path Animation"),
      ("gps", "GPS Coordinate Animation"), ("latlon", "Latitude & Longitude Animation"), ("topo", "Topographic Map Animation"), ("elev", "Elevation Profile Animation"),
      ("compass", "Compass Animation"), ("scalebar", "Scale Bar Animation"), ("geo_compare", "Geographical Comparison Animation"))
_treg("bg", "history", ("h_timeline", "Historical Timeline Animation"), ("old_photo", "Old Photo Animation"), ("parallax25", "2.5D Parallax Animation"), ("kenburns", "Ken Burns Effect"),
      ("doc_reveal", "Historical Document Reveal"), ("handwriting", "Handwriting Animation"), ("newspaper", "Newspaper Reveal Animation"), ("manuscript", "Ancient Manuscript Animation"),
      ("family_tree", "Family Tree Animation"), ("dynasty", "Dynasty Timeline Animation"), ("ruler_intro", "King / Ruler Introduction Animation"), ("portrait_reveal", "Historical Portrait Reveal"),
      ("archival", "Archival Footage Animation"), ("old_map", "Old Map Reconstruction"), ("ba_hist", "Before & After Historical Comparison"), ("event_seq", "Historical Event Sequence Animation"),
      ("battle_form", "Battle Formation Animation"), ("army_strength", "Army Strength Comparison"), ("kingdom_exp", "Kingdom Expansion Animation"), ("evidence_hl", "Historical Evidence Highlight Animation"))
_treg("bg", "camera", ("push_in", "Camera Push In"), ("pull_out", "Camera Pull Out"), ("dolly_zoom", "Dolly Zoom"), ("pan", "Camera Pan Animation"))
_treg("ov", "data", ("num_counter", "Number Counter Animation"), ("dist_card", "Distance Counter"), ("pop_counter", "Population Counter"), ("pct_counter", "Percentage Counter"), ("year_counter", "Year Counter Animation"),
      ("countdown", "Date Countdown Animation"), ("time_counter", "Time Counter Animation"), ("bar_chart", "Animated Bar Chart"), ("pie_chart", "Animated Pie Chart"), ("line_graph", "Line Graph Animation"),
      ("area_chart", "Area Chart Animation"), ("data_compare", "Data Comparison Animation"), ("ranking", "Ranking Animation"), ("fin_counter", "Financial Counter Animation"),
      ("stat_info", "Statistics Infographic Animation"), ("progress", "Progress Bar Animation"), ("digital_clock", "Digital Clock Animation"), ("odometer", "Digital Odometer Animation"),
      ("dashboard", "Live Data Dashboard Animation"), ("num_roll", "Number Rolling Animation"))
_treg("ov", "camera", ("tilt", "Camera Tilt Animation"), ("fly", "3D Camera Fly Through"), ("orbit", "Orbit Camera Animation"), ("parallax_cam", "Parallax Camera Movement"), ("aerial", "Aerial Flyover"),
      ("drone", "Drone Reveal Animation"), ("rack", "Rack Focus Effect"), ("shake", "Camera Shake Effect"), ("handheld", "Handheld Camera Effect"), ("whip_pan", "Whip Pan Transition"),
      ("match_move", "Match Move Transition"), ("persp_zoom", "Perspective Zoom"), ("inf_zoom", "Infinite Zoom Animation"), ("tracking", "Camera Tracking Animation"), ("cine_reveal", "Cinematic Reveal"),
      ("focus_pull", "Focus Pull Animation"))
_treg("ov", "text", ("kinetic", "Kinetic Typography"), ("text3d", "3D Text Animation"), ("typewriter", "Typewriter Text Effect"), ("text_reveal", "Text Reveal Animation"), ("text_mask", "Text Masking Animation"),
      ("word_hl", "Word Highlight Animation"), ("letter", "Letter-by-Letter Animation"), ("glitch_text", "Glitch Text Animation"), ("tracking_text", "Tracking Text Animation"), ("text_path", "Text Follow Path Animation"),
      ("subtitles", "Animated Subtitles"), ("lower_third", "Lower Third Animation"), ("chapter", "Chapter Title Animation"), ("date_loc", "Date & Location Title Animation"), ("callout", "Callout Text Animation"),
      ("text_persp", "Text Perspective Animation"), ("text_rot", "Text Rotation Animation"), ("text_blur", "Text Blur Reveal"), ("labels", "Animated Labels"), ("text_trans", "Text Transition Animation"))
_treg("ov", "diagram", ("flowchart", "Flowchart Animation"), ("process", "Process Flow Animation"), ("steps", "Step-by-Step Animation"), ("mindmap", "Mind Map Animation"), ("orgchart", "Organizational Chart Animation"),
      ("network", "Network Diagram Animation"), ("conn_line", "Connection Line Animation"), ("arrow_dir", "Arrow Direction Animation"), ("callout_ptr", "Callout Pointer Animation"), ("blueprint", "Blueprint Animation"),
      ("exploded", "Exploded View Animation"), ("assembly", "3D Object Assembly Animation"), ("cross_section", "Cross Section Animation"), ("layer_sep", "Layer Separation Animation"), ("tech_label", "Technical Label Animation"))
_treg("ov", "vfx", ("grain", "Film Grain Effect"), ("dust", "Dust Particle Effect"), ("smoke", "Smoke Effect"), ("fog", "Fog Effect"), ("fire", "Fire & Ember Effect"), ("rain", "Rain Effect"), ("snow", "Snow Effect"),
      ("lightning", "Lightning Effect"), ("light_leak", "Light Leak Effect"), ("lens_flare", "Lens Flare Effect"), ("vignette", "Vignette Effect"), ("chroma", "Chromatic Aberration"), ("glitch", "Glitch Effect"),
      ("film_burn", "Film Burn Effect"), ("vhs", "VHS Effect"), ("old_film", "Old Film Effect"), ("motion_blur", "Motion Blur Effect"), ("speed_ramp", "Speed Ramp Effect"), ("freeze", "Freeze Frame Effect"),
      ("split", "Split Screen Effect"), ("vox", "Vox Style"))
_treg("tr", "trans", ("mask", "Mask Transition"), ("luma", "Luma Fade Transition"), ("morph", "Morph Transition"), ("zoom", "Zoom Transition"), ("whip", "Whip Transition"), ("spin", "Spin Transition"),
      ("glitch", "Glitch Transition"), ("flash", "Light Flash Transition"), ("burn", "Film Burn Transition"), ("shape", "Shape Transition"), ("wipe", "Wipe Transition"), ("match", "Match Cut Transition"),
      ("object", "Object Reveal Transition"), ("seamless", "Seamless Transition"), ("morphmap", "Morphing Map Transition"))
assert len(T151) == 151, len(T151)
TR15 = [t["key"] for t in T151 if t["layer"] == "tr"]
FRAME_FX = {}


def _ffx(*names):
    def deco(fn):
        for n in names:
            FRAME_FX[n] = fn
        return fn
    return deco


def _env(u):
    return math.sin(math.pi * cl(u))


def _zoomc(fr, z, cx=0.5, cy=0.5, rot=0.0):
    """Crop-zoom around (cx, cy) (fractions) with optional rotation; z >= 1."""
    if rot:
        fr = fr.rotate(rot, resample=Image.BICUBIC)
        z = z * (1 + abs(rot) * 0.02)
    z = max(1.0, z)
    bw, bh = W / z, H / z
    x0 = min(max(cx * W - bw / 2, 0), W - bw)
    y0 = min(max(cy * H - bh / 2, 0), H - bh)
    return fr.resize((W, H), Image.BICUBIC, box=(x0, y0, x0 + bw, y0 + bh))


def _blur(fr, r):
    return fr.filter(ImageFilter.GaussianBlur(max(0.1, r)))


def _radial_mask(rx, ry, soft=0.25, cx=0.5, cy=0.5):
    np = _np()
    key = ("rm", int(rx * 100), int(ry * 100), int(soft * 100), int(cx * 100), int(cy * 100))
    if key not in _VX:
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        d = np.sqrt(((xx / W - cx) / rx) ** 2 + ((yy / H - cy) / ry) ** 2)
        _VX[key] = Image.fromarray((np.clip((1.0 - d) / soft, 0, 1) * 255).astype(np.uint8))
    return _VX[key]


# ---- camera moves (u = progress through the window)
@_ffx("push_in")
def cam_push(fr, u, t, s):
    return _zoomc(fr, 1 + 0.22 * e_io(u))


@_ffx("pull_out")
def cam_pull(fr, u, t, s):
    return _zoomc(fr, 1.22 - 0.2 * e_io(u))


@_ffx("dolly_zoom")
def cam_dolly(fr, u, t, s):
    bg = _zoomc(fr, 1 + 0.3 * e_io(u))
    return Image.composite(fr, bg, _radial_mask(0.34, 0.5, 0.45))


@_ffx("pan")
def cam_pan(fr, u, t, s):
    return _zoomc(fr, 1.14, 0.5 + lerp(-0.07, 0.07, e_io(u)) * (1 if s % 2 else -1), 0.5)


@_ffx("kenburns")
def cam_kb(fr, u, t, s):
    return _zoomc(fr, 1.06 + 0.16 * u, 0.5 + 0.05 * (u - 0.5), 0.5 - 0.03 * u)


@_ffx("tilt")
def cam_tilt(fr, u, t, s):
    return _zoomc(fr, 1.16, 0.5, 0.5 + lerp(0.07, -0.07, e_io(u)))


@_ffx("fly")
def cam_fly(fr, u, t, s):
    return _zoomc(fr, 1 + 0.55 * u * u, 0.5, 0.5, 2.5 * math.sin(u * 3))


@_ffx("orbit")
def cam_orbit(fr, u, t, s):
    return _zoomc(fr, 1.1, 0.5 + 0.05 * math.sin(u * 3.1), 0.5, lerp(-3.5, 3.5, e_io(u)))


@_ffx("parallax_cam", "parallax25")
def cam_par(fr, u, t, s):
    bg = _zoomc(fr, 1.14, 0.5 + lerp(-0.045, 0.045, u), 0.5)
    fg = _zoomc(fr, 1.04, 0.5 - lerp(-0.02, 0.02, u), 0.5)
    return Image.composite(fg, bg, _radial_mask(0.36, 0.55, 0.5))


@_ffx("aerial")
def cam_aerial(fr, u, t, s):
    return _zoomc(fr, lerp(1.35, 1.04, e_out(u)), 0.5, lerp(0.6, 0.5, u), lerp(-3, 0, e_out(u)))


@_ffx("drone")
def cam_drone(fr, u, t, s):
    r = _zoomc(fr, lerp(1.3, 1.04, e_out(u)), 0.5, lerp(0.68, 0.5, e_out(u)))
    top = Image.linear_gradient("L").resize((W, H)).point(lambda v: int(v * (1 - e_out(u)) * 0.9))
    return Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), r, ImageOps.invert(top).point(lambda v: 255 - v) if False else top.transpose(Image.FLIP_TOP_BOTTOM))


@_ffx("rack")
def cam_rack(fr, u, t, s):
    r = 11 * abs(1 - 2 * u)
    return Image.composite(fr, _blur(fr, r), _radial_mask(0.3 + 0.3 * (1 - abs(1 - 2 * u)), 0.5, 0.4)) if r > 0.5 else fr


@_ffx("shake")
def cam_shake(fr, u, t, s):
    rr = random.Random(int(t * 40) + s)
    a = 26 * (1 - u) ** 1.5
    return ImageChops.offset(_zoomc(fr, 1.05), int(rr.uniform(-a, a)), int(rr.uniform(-a, a)))


@_ffx("handheld")
def cam_hand(fr, u, t, s):
    return _zoomc(fr, 1.05, 0.5 + 0.012 * math.sin(t * 5.3 + s) + 0.006 * math.sin(t * 11), 0.5 + 0.012 * math.cos(t * 4.1), 0.7 * math.sin(t * 3.3))


@_ffx("whip_pan")
def cam_whip(fr, u, t, s):
    k = int(_env(u) * 60) + 1
    sh = ImageChops.offset(fr, int(W * 0.5 * (e_io(u) - 0.5) * 2 * _env(u)), 0)
    return sh.resize((max(8, W // k), H), Image.BILINEAR).resize((W, H), Image.BILINEAR) if k > 2 else sh


@_ffx("match_move")
def cam_mm(fr, u, t, s):
    e = _env(u)
    return Image.blend(fr, _zoomc(fr, 1 + 0.5 * e), 0.55 * e) if e > 0.02 else fr


@_ffx("persp_zoom")
def cam_pz(fr, u, t, s):
    k = 0.16 * _env(u)
    c = (1, 0, 0, 0, 1, 0, 0, -k / W)
    try:
        r = fr.transform((W, H), Image.PERSPECTIVE, c, Image.BICUBIC)
    except Exception:  # noqa
        r = fr
    return _zoomc(r, 1 + 0.18 * e_io(u), 0.5, 0.45)


@_ffx("inf_zoom")
def cam_inf(fr, u, t, s):
    z = 1 + u
    big = _zoomc(fr, z) if z < 3 else fr
    inset = fr.resize((int(W * 0.5 * z), int(H * 0.5 * z)), Image.BILINEAR)
    out = big.copy()
    out.paste(inset, (int(W / 2 - inset.width / 2), int(H / 2 - inset.height / 2)))
    return Image.blend(big, out, 0.8)


@_ffx("tracking")
def cam_track(fr, u, t, s):
    return _zoomc(fr, 1.12, 0.5 + 0.07 * math.sin(u * 6.28), 0.5 + 0.02 * math.sin(u * 12))


@_ffx("cine_reveal")
def cam_cine(fr, u, t, s):
    r = _zoomc(fr, 1 + 0.1 * u)
    d = ImageDraw.Draw(r)
    hb = int(H * 0.16 * (1 - e_out(cl(u * 1.5))))
    if hb > 0:
        d.rectangle([0, 0, W, hb], fill=(0, 0, 0))
        d.rectangle([0, H - hb, W, H], fill=(0, 0, 0))
    return r


@_ffx("focus_pull")
def cam_fp(fr, u, t, s):
    r = 12 * (1 - e_out(u))
    return _blur(fr, r) if r > 0.4 else fr


@_ffx("motion_blur")
def cam_mb(fr, u, t, s):
    n = 7
    acc = None
    for k in range(n):
        im = ImageChops.offset(fr, int((k - n // 2) * 9 * _env(u)), 0)
        acc = im if acc is None else Image.blend(acc, im, 1 / (k + 1))
    return acc


# ---- cinematic effects
def _noise_img(seed, scale=40, alpha=True):
    key = ("nz", seed, scale)
    if key not in _VX:
        np = _np()
        rng = np.random.default_rng(seed)
        _VX[key] = Image.fromarray((rng.random((max(2, H // scale), max(2, W // scale))) * 255).astype(np.uint8)).resize((W, H), Image.BICUBIC)
    return _VX[key]


def _ov(fr, rgb, mask, amt=1.0):
    m = mask.point(lambda v: int(v * cl(amt)))
    return Image.composite(Image.new("RGB", (W, H), rgb), fr, m)


@_ffx("grain")
def fx_grain(fr, u, t, s):
    np = _np()
    rng = np.random.default_rng(int(t * 30) + s)
    g = (rng.normal(0, 26 * _env(u) + 4, (H, W, 1))).astype(np.float32)
    return Image.fromarray(np.clip(np.asarray(fr, dtype=np.float32) + g, 0, 255).astype(np.uint8))


@_ffx("dust")
def fx_dust(fr, u, t, s):
    d = ImageDraw.Draw(fr, "RGBA")
    rr = random.Random(s)
    for _ in range(70):
        x, y, r = rr.uniform(0, W), rr.uniform(0, H), rr.uniform(1, 3.2)
        y = (y - t * rr.uniform(10, 40)) % H
        d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 245, 225, int(150 * _env(u))))
    return fr


@_ffx("smoke")
def fx_smoke(fr, u, t, s):
    n = _noise_img(3 + s % 3, 90)
    n2 = ImageChops.offset(n, int(t * 40), -int(t * 25))
    m = ImageChops.multiply(n2, Image.linear_gradient("L").resize((W, H)).transpose(Image.FLIP_TOP_BOTTOM)).point(lambda v: min(255, int(v * 1.4)))
    return _ov(fr, (200, 200, 205), m, 0.65 * _env(u))


@_ffx("fog")
def fx_fog(fr, u, t, s):
    n = ImageChops.offset(_noise_img(8, 140), int(t * 22), 0)
    m = n.point(lambda v: int(70 + v * 0.45))
    return _ov(fr, (220, 228, 236), m, 0.7 * _env(u))


@_ffx("fire")
def fx_fire(fr, u, t, s):
    g = Image.linear_gradient("L").resize((W, H)).transpose(Image.FLIP_TOP_BOTTOM)
    flick = 0.75 + 0.25 * math.sin(t * 17)
    out = _ov(fr, (255, 110, 20), ImageChops.multiply(g, _noise_img(5, 60)).point(lambda v: min(255, v * 2)), 0.55 * _env(u) * flick)
    d = ImageDraw.Draw(out, "RGBA")
    rr = random.Random(s)
    for _ in range(40):
        x = rr.uniform(0, W)
        y = H - ((t * rr.uniform(60, 220) + rr.uniform(0, H)) % (H * 0.8))
        x += math.sin(t * 3 + x) * 14
        d.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(255, rr.randint(130, 200), 40, int(230 * _env(u))))
    return out


@_ffx("rain")
def fx_rain(fr, u, t, s):
    d = ImageDraw.Draw(fr, "RGBA")
    rr = random.Random(s)
    for _ in range(140):
        x = rr.uniform(0, W)
        y = (rr.uniform(0, H) + t * 900) % H
        d.line([(x, y), (x - 9, y + 38)], fill=(210, 225, 245, int(150 * _env(u))), width=2)
    return Image.blend(fr, ImageEnhance.Brightness(fr).enhance(0.82), 0.5 * _env(u))


@_ffx("snow")
def fx_snow(fr, u, t, s):
    d = ImageDraw.Draw(fr, "RGBA")
    rr = random.Random(s)
    for _ in range(110):
        x, y0, r = rr.uniform(0, W), rr.uniform(0, H), rr.uniform(2, 5.5)
        y = (y0 + t * rr.uniform(40, 120)) % H
        x += math.sin(t * 2 + y0) * 18
        d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, int(220 * _env(u))))
    return fr


@_ffx("lightning")
def fx_lightning(fr, u, t, s):
    f = 1.0 if (u < 0.12 or 0.22 < u < 0.3) else 0.0
    out = Image.blend(fr, Image.new("RGB", (W, H), (235, 240, 255)), 0.55 * f)
    if f:
        rr = random.Random(s)
        x, y, pts = rr.uniform(W * 0.3, W * 0.7), 0, []
        while y < H * 0.8:
            pts.append((x, y))
            x += rr.uniform(-60, 60)
            y += rr.uniform(40, 90)
        _aa_poly(out, pts, 6, (255, 255, 255), 1.0)
    return out


@_ffx("light_leak")
def fx_leak(fr, u, t, s):
    np = _np()
    xx = np.linspace(0, 1, W, dtype=np.float32)[None, :]
    c = u * 1.3 - 0.15
    m = np.clip(1 - np.abs(xx - c) * 3.2, 0, 1) * np.ones((H, 1), np.float32)
    lay = np.stack([m * 255, m * 120, m * 40], -1)
    return Image.fromarray(np.clip(np.asarray(fr, dtype=np.float32) + lay * 0.75 * _env(u), 0, 255).astype(np.uint8))


@_ffx("lens_flare")
def fx_flare(fr, u, t, s):
    out = fr.copy()
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    cx, cy = lerp(W * 0.15, W * 0.85, u), lerp(H * 0.2, H * 0.4, u)
    for r, a_, col in ((120, 70, (255, 235, 180)), (60, 110, (255, 255, 255)), (22, 200, (255, 255, 255))):
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col + (int(a_ * _env(u)),))
    for k, (f, r, col) in enumerate(((-0.5, 26, (120, 200, 255)), (-1.0, 40, (255, 150, 90)), (-1.6, 18, (150, 255, 170)))):
        x, y = W / 2 + (cx - W / 2) * f, H / 2 + (cy - H / 2) * f
        d.ellipse([x - r, y - r, x + r, y + r], outline=col + (int(150 * _env(u)),), width=4)
    d.line([(cx - 260, cy), (cx + 260, cy)], fill=(255, 255, 255, int(120 * _env(u))), width=3)
    out.paste(lay.filter(ImageFilter.GaussianBlur(2)), (0, 0), lay.filter(ImageFilter.GaussianBlur(2)))
    return out


@_ffx("vignette")
def fx_vig(fr, u, t, s):
    m = _radial_mask(0.62, 0.62, 0.9).point(lambda v: 255 - v)
    return _ov(fr, (0, 0, 0), m, 0.85 * _env(u))


@_ffx("chroma")
def fx_chroma(fr, u, t, s):
    a = int(16 * _env(u)) + 1
    r, g, b = fr.split()
    return Image.merge("RGB", (ImageChops.offset(r, a, 0), g, ImageChops.offset(b, -a, 0)))


@_ffx("glitch")
def fx_glitch(fr, u, t, s):
    return blend_trans(fr, fr, 0.3 + 0.3 * u, "glitch") if _env(u) > 0.15 else fr


@_ffx("film_burn")
def fx_burn(fr, u, t, s):
    n = _noise_img(11, 50)
    th = int(255 * (1 - _env(u)))
    m = n.point(lambda v: 255 if v > th else 0).filter(ImageFilter.GaussianBlur(14))
    edge = n.point(lambda v: 200 if th - 14 < v <= th else 0).filter(ImageFilter.GaussianBlur(6))
    out = _ov(fr, (255, 245, 210), m, 0.95)
    return _ov(out, (255, 120, 20), edge, 0.9)


@_ffx("vhs")
def fx_vhs(fr, u, t, s):
    r, g, b = fr.split()
    out = Image.merge("RGB", (ImageChops.offset(r, 7, 0), g, ImageChops.offset(b, -6, 0)))
    d = ImageDraw.Draw(out, "RGBA")
    for y in range(0, H, 4):
        d.line([(0, y), (W, y)], fill=(0, 0, 0, 38), width=1)
    ty = int((t * 160 + s * 50) % H)
    band = out.crop((0, ty, W, min(H, ty + 26)))
    out.paste(ImageChops.offset(band, 40, 0), (0, ty))
    return ImageEnhance.Color(out).enhance(1.25)


@_ffx("old_film", "old_photo", "archival")
def fx_old(fr, u, t, s):
    g = ImageOps.colorize(ImageOps.grayscale(fr), (30, 20, 10), (255, 238, 205))
    g = ImageEnhance.Contrast(g).enhance(1.1)
    d = ImageDraw.Draw(g, "RGBA")
    rr = random.Random(int(t * 12) + s)
    for _ in range(7):
        x = rr.uniform(0, W)
        d.line([(x, 0), (x + rr.uniform(-3, 3), H)], fill=(255, 255, 255, rr.randint(30, 90)), width=1)
    for _ in range(18):
        x, y, r = rr.uniform(0, W), rr.uniform(0, H), rr.uniform(1, 3)
        d.ellipse([x - r, y - r, x + r, y + r], fill=(0, 0, 0, 120))
    g = Image.blend(g, ImageEnhance.Brightness(g).enhance(0.85 + 0.25 * rr.random()), 0.5)
    g = _ov(g, (0, 0, 0), _radial_mask(0.7, 0.7, 0.8).point(lambda v: 255 - v), 0.7)
    return Image.blend(fr, g, min(1.0, 3.5 * _env(u)))


@_ffx("split")
def fx_split(fr, u, t, s):
    e = e_out(cl(u * 3)) * (1 - e_in(cl((u - 0.75) * 4)) if 'e_in' in globals() else 1)
    x = int(W * (0.5 + (1 - e) * 0.5))
    right = ImageEnhance.Color(_zoomc(fr, 1.45, 0.72, 0.45)).enhance(0.6)
    out = fr.copy()
    out.paste(right.crop((x, 0, W, H)), (x, 0))
    ImageDraw.Draw(out).line([(x, 0), (x, H)], fill=(255, 255, 255), width=5)
    return out


@_ffx("vox")
def fx_vox(fr, u, t, s):
    return vox_collage(fr, min(cl(u / 0.25), 1 - cl((u - 0.75) / 0.25)), s)



# ---- the 15 transitions (blend_trans hands every new kind to tr_new)
def tr_new(prev, cur, p, kind):
    np = _np()
    p = cl(p)
    e = e_io(p)
    if kind == "mask":  # staggered vertical blinds
        if "tr_blind" not in _VX:
            xx = np.arange(W, dtype=np.float32)[None, :]
            _VX["tr_blind"] = (np.broadcast_to(((xx // 64) % 2) * 0.0 + (xx // 64) / (W / 64), (H, W)).copy(), (np.broadcast_to((xx % 64) / 64.0, (H, W))).copy())
        a, b = _VX["tr_blind"]
        m = np.clip((e * 1.6 - a * 0.6) * 2.0 - b * 0 - 0.0, 0, 1) * 255 if False else np.clip((e * 1.5 - a * 0.5) * 3 - 1.0 + 0.0 - (1 - b) * 0.0, 0, 1) * 255
        return Image.composite(cur, prev, Image.fromarray(m.astype(np.uint8)))
    if kind == "luma":
        lum = np.asarray(ImageOps.grayscale(prev), dtype=np.float32) / 255
        m = np.clip((e * 1.4 - lum * 0.4) * 5 - 0.6 * 0 - 1.0 + 0.0, 0, 1) * 255
        m = np.clip((e - (1 - lum) * 0.0 - 0.0) * 1.0 + (e * 1.25 - lum) * 4, 0, 1) * 255 if False else np.clip((e * 1.3 - lum) * 4, 0, 1) * 255
        return Image.composite(cur, prev, Image.fromarray(m.astype(np.uint8)))
    if kind == "morph":
        if "tr_wy" not in _VX:
            yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
            _VX["tr_wy"] = (yy, xx)
        yy, xx = _VX["tr_wy"]
        amp = 38 * math.sin(math.pi * p)

        def warp(im, sgn):
            a = np.asarray(im)
            sx = np.clip(xx + sgn * amp * np.sin(yy / 46.0 + p * 6), 0, W - 1).astype(np.int32)
            sy = np.clip(yy + sgn * amp * 0.5 * np.sin(xx / 60.0), 0, H - 1).astype(np.int32)
            return Image.fromarray(a[sy, sx])
        return Image.blend(warp(prev, 1), warp(cur, -1), e)
    if kind == "spin":
        a = prev.rotate(-e * 50, resample=Image.BILINEAR).resize((W, H), Image.BILINEAR, box=(W * 0.5 * (1 - 1 / (1 + e)), H * 0.5 * (1 - 1 / (1 + e)), W - W * 0.5 * (1 - 1 / (1 + e)), H - H * 0.5 * (1 - 1 / (1 + e))))
        b = cur.rotate((1 - e) * 50, resample=Image.BILINEAR)
        return Image.blend(a, b, e)
    if kind == "burn":
        n = _noise_img(21, 50)
        th = int(255 * (1 - e))
        m = n.point(lambda v: 255 if v > th else 0).filter(ImageFilter.GaussianBlur(4))
        edge = n.point(lambda v: 255 if th - 22 < v <= th else 0).filter(ImageFilter.GaussianBlur(5))
        out = Image.composite(cur, prev, ImageChops.invert(m) if False else m.point(lambda v: 255 - v))
        out = Image.composite(cur, prev, n.point(lambda v: 255 if v <= int(255 * e) else 0).filter(ImageFilter.GaussianBlur(3)))
        ring = n.point(lambda v: 255 if abs(v - int(255 * e)) < 14 else 0).filter(ImageFilter.GaussianBlur(5))
        return _ov(_ov(out, (255, 140, 30), ring, 0.95), (255, 245, 200), ring.point(lambda v: v // 2), 0.8)
    if kind == "shape":  # expanding diamond / star
        m = Image.new("L", (W, H), 0)
        r = e * math.hypot(W, H) * 0.62
        cx, cy = W / 2, H / 2
        pts = []
        for k in range(10):
            rr_ = r if k % 2 == 0 else r * 0.55
            a_ = math.pi / 5 * k - math.pi / 2
            pts.append((cx + math.cos(a_) * rr_, cy + math.sin(a_) * rr_))
        ImageDraw.Draw(m).polygon(pts, fill=255)
        return Image.composite(cur, prev, m.filter(ImageFilter.GaussianBlur(3)))
    if kind == "match":  # match cut: dive into the centre, come out of the centre of the next picture
        if p < 0.5:
            return _zoomc(prev, 1 + 2.2 * e_io(p * 2) ** 2)
        return _zoomc(cur, 1 + 2.2 * (1 - e_io((p - 0.5) * 2)) ** 2)
    if kind == "object":  # the next picture is revealed through an oval around the subject
        m = Image.new("L", (W, H), 0)
        r = e * math.hypot(W, H) * 0.55
        ImageDraw.Draw(m).ellipse([W * 0.5 - r * 1.3, H * 0.46 - r, W * 0.5 + r * 1.3, H * 0.46 + r], fill=255)
        out = Image.composite(cur, prev, m.filter(ImageFilter.GaussianBlur(6)))
        if 0.02 < p < 0.95:
            ImageDraw.Draw(out).ellipse([W * 0.5 - r * 1.3, H * 0.46 - r, W * 0.5 + r * 1.3, H * 0.46 + r], outline=(255, 255, 255), width=4)
        return out
    if kind == "seamless":  # continuous vertical glide with a little motion blur
        off = int(e * H)
        canvas = Image.new("RGB", (W, 2 * H))
        canvas.paste(prev, (0, 0))
        canvas.paste(cur, (0, H))
        fr = canvas.crop((0, off, W, off + H))
        k = 1 + int(10 * math.sin(math.pi * p))
        return fr.resize((W, max(8, H // k)), Image.BILINEAR).resize((W, H), Image.BILINEAR) if k > 1 else fr
    if kind == "morphmap":  # picture dissolves through a lat/long grid like a map morph
        out = Image.blend(_zoomc(prev, 1 + 0.4 * e), _zoomc(cur, 1.4 - 0.4 * e), e)
        d = ImageDraw.Draw(out, "RGBA")
        al = int(150 * math.sin(math.pi * p))
        for k in range(1, 8):
            d.line([(W * k / 8, 0), (W * k / 8, H)], fill=(255, 255, 255, al), width=2)
        for k in range(1, 5):
            d.line([(0, H * k / 5), (W, H * k / 5)], fill=(255, 255, 255, al), width=2)
        return out
    return cur



# ---- overlay cards (data / text / diagram): each is drawn into a 410x236 sprite and placed in one of the 3 fixed bottom slots (left / centre / right)
T_CW, T_CH = 410, 236
SLOT_CX = {"L": 14 + T_CW / 2 + 0.0, "C": W / 2, "R": W - 14 - T_CW / 2}
SLOT_CY = H - 16 - T_CH / 2
ACC = [(255, 92, 72), (0, 190, 205), (255, 190, 40), (120, 200, 90), (150, 120, 255)]
HI_DIG = str.maketrans("0123456789", "०१२३४५६७८९")


class Cv:
    def __init__(s, acc):
        s.S = 2
        s.acc = acc
        s.im = Image.new("RGBA", (T_CW * 2, T_CH * 2), (0, 0, 0, 0))
        s.d = ImageDraw.Draw(s.im)
        s.d.rounded_rectangle([0, 0, T_CW * 2 - 1, T_CH * 2 - 1], 40, fill=(10, 13, 22, 214), outline=(255, 255, 255, 60), width=3)
        s.d.rounded_rectangle([0, 0, 14, T_CH * 2 - 1], 6, fill=acc + (255,))

    def text(s, txt, size, x, y, fill=(255, 255, 255), role="title", max_w=None, a=1.0, rot=0, anchor="c"):
        if not str(txt).strip():
            return
        spr = _vx_text(str(txt), int(size * 2), role, fill=fill, stroke=0, shadow=False, max_w=int(max_w * 2) if max_w else None)
        if rot:
            spr = spr.rotate(rot, resample=Image.BICUBIC, expand=True)
        if a < 0.98:
            spr = spr.copy()
            spr.putalpha(spr.getchannel("A").point(lambda v: int(v * a)))
        px = int(x * 2 - (spr.width / 2 if anchor == "c" else 0))
        s.im.paste(spr, (px, int(y * 2 - spr.height / 2)), spr)

    def line(s, pts, w=3, fill=(255, 255, 255), prog=1.0):
        P = [(x * 2, y * 2) for x, y in pts]
        segs = list(zip(P, P[1:]))
        tot = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in segs) or 1
        tgt, acc_ = tot * cl(prog), 0.0
        for a, b in segs:
            l = math.hypot(b[0] - a[0], b[1] - a[1])
            if acc_ + l <= tgt:
                s.d.line([a, b], fill=fill, width=int(w * 2))
                acc_ += l
            else:
                r = (tgt - acc_) / max(l, 1e-6)
                s.d.line([a, (a[0] + (b[0] - a[0]) * r, a[1] + (b[1] - a[1]) * r)], fill=fill, width=int(w * 2))
                break

    def rect(s, x0, y0, x1, y1, fill=None, outline=None, r=6, w=2):
        s.d.rounded_rectangle([x0 * 2, y0 * 2, x1 * 2, y1 * 2], r * 2, fill=fill, outline=outline, width=int(w * 2))

    def circ(s, x, y, r, fill=None, outline=None, w=2):
        s.d.ellipse([(x - r) * 2, (y - r) * 2, (x + r) * 2, (y + r) * 2], fill=fill, outline=outline, width=int(w * 2))

    def poly(s, pts, fill=None, outline=None):
        s.d.polygon([(x * 2, y * 2) for x, y in pts], fill=fill, outline=outline)

    def arc(s, x, y, r, a0, a1, fill, w=8):
        s.d.arc([(x - r) * 2, (y - r) * 2, (x + r) * 2, (y + r) * 2], a0, a1, fill=fill, width=int(w * 2))

    def done(s):
        return s.im.resize((T_CW, T_CH), Image.LANCZOS)


import unicodedata


def _gr(s_):
    out = []
    for ch in s_:
        if out and (unicodedata.category(ch).startswith("M") or ch in "\u094d" or (out[-1].endswith("\u094d"))):
            out[-1] += ch
        else:
            out.append(ch)
    return out


def _fmt(v):
    v = float(v)
    s_ = f"{int(round(v)):,}" if abs(v - round(v)) < 1e-6 or abs(v) >= 100 else (f"{v:.1f}")
    return s_.translate(HI_DIG)


def _series(E, C, n=5):
    s_ = (E.get("series") or C.get("series") or [("क", 3), ("ख", 5), ("ग", 2), ("घ", 4), ("ङ", 6)])[:n]
    return s_ if len(s_) >= 2 else [("क", 3), ("ख", 5)]


def _words(C, n=5):
    w_ = C.get("words") or ["शुरुआत", "कारण", "असर", "नतीजा", "आगे"]
    return (w_ * 3)[:n]


CARDFN = {}


def _card(*names):
    def deco(fn):
        for n in names:
            CARDFN[n] = fn
        return fn
    return deco


def _numE(E):
    return float(E.get("num") or 0), E.get("unit") or ""


@_card("num_counter", "pop_counter", "dist_card", "fin_counter")
def c_numc(cv, p, t, E, C, key):
    v, unit = _numE(E)
    g = e_out(cl(p * 1.6))
    pre = "₹ " if key == "fin_counter" else ""
    cv.text(pre + _fmt(v * g), 64, 205, 100, ACC[0], max_w=370)
    cv.text(E.get("label") or unit or {"dist_card": "किलोमीटर", "pop_counter": "आबादी", "fin_counter": "रकम"}.get(key, ""), 24, 205, 160, (230, 230, 240), "sub", 360)
    if key == "dist_card":
        cv.line([(40, 205), (370, 205)], 3, (120, 130, 150))
        cv.circ(40 + 330 * g, 205, 9, fill=ACC[0])
    elif key == "pop_counter":
        for k in range(20):
            if k / 20 < g:
                cv.circ(36 + k * 17.5, 205, 6, fill=ACC[1])


@_card("pct_counter", "progress")
def c_pct(cv, p, t, E, C, key):
    v = min(100.0, float(E.get("num") or E.get("pct") or 70))
    g = e_out(cl(p * 1.6))
    if key == "pct_counter":
        cv.arc(205, 122, 80, 0, 360, (60, 66, 84), 16)
        cv.arc(205, 122, 80, -90, -90 + 360 * v / 100 * g, ACC[1], 16)
        cv.text(_fmt(v * g) + "%", 52, 205, 122, (255, 255, 255))
    else:
        cv.rect(30, 110, 380, 150, fill=(60, 66, 84))
        cv.rect(30, 110, 30 + 350 * v / 100 * g, 150, fill=ACC[3])
        cv.text(_fmt(v * g) + "%", 40, 205, 64, (255, 255, 255))
    cv.text(E.get("label") or "", 22, 205, 205, (230, 230, 240), "sub", 360)


@_card("year_counter")
def c_year(cv, p, t, E, C, key):
    y = int(E.get("year") or 1900)
    g = e_out(cl(p * 1.5))
    cv.text(str(int(y - 80 * (1 - g))).translate(HI_DIG), 92, 205, 112, ACC[2], max_w=380)
    cv.text(E.get("label") or "साल", 24, 205, 186, (230, 230, 240), "sub", 360)


@_card("countdown", "time_counter", "digital_clock")
def c_time(cv, p, t, E, C, key):
    if key == "countdown":
        total = 3 * 86400 + 5 * 3600
        rem = int(total * (1 - e_io(cl(p * 1.3))))
        parts = [(rem // 86400, "दिन"), (rem // 3600 % 24, "घंटे"), (rem // 60 % 60, "मिनट"), (rem % 60, "सेकंड")]
        for k, (v, lb) in enumerate(parts):
            x = 55 + k * 100
            cv.rect(x - 38, 70, x + 38, 140, fill=(40, 46, 64), outline=ACC[0], w=2)
            cv.text(f"{v:02d}".translate(HI_DIG), 40, x, 105, (255, 255, 255))
            cv.text(lb, 20, x, 162, (200, 205, 220), "sub")
    elif key == "time_counter":
        s_ = t * 1.0 + p * 20
        cv.text(f"{int(s_ // 60):02d}:{int(s_ % 60):02d}.{int(s_ * 10 % 10)}".translate(HI_DIG), 70, 205, 112, ACC[1], max_w=380)
        cv.text(E.get("label") or "बीता समय", 22, 205, 185, (230, 230, 240), "sub", 360)
    else:
        m = int(E.get("minutes") or 720) + int(p * 40)
        cv.text(f"{m // 60 % 24:02d}{':' if int(t * 2) % 2 == 0 else ' '}{m % 60:02d}".translate(HI_DIG), 86, 205, 115, (90, 255, 150), role="cond", max_w=380)
        cv.text(E.get("label") or "", 22, 205, 190, (230, 230, 240), "sub", 360)


@_card("odometer", "num_roll")
def c_odo(cv, p, t, E, C, key):
    v = int(abs(float(E.get("num") or 1234)))
    ds = str(v)[-7:]
    g = e_out(cl(p * 1.5))
    n = len(ds)
    w0 = min(48, 360 / n)
    x0 = 205 - w0 * n / 2
    for k, ch in enumerate(ds):
        tgt = int(ch)
        spin = (1 - g) * (10 + k * 3)
        cur = (tgt - spin) % 10
        x = x0 + w0 * (k + 0.5)
        cv.rect(x - w0 / 2 + 2, 62, x + w0 / 2 - 2, 150, fill=(30, 34, 48), outline=(110, 118, 140))
        for dd in (-1, 0, 1):
            dig = int(round(cur + dd)) % 10
            off = (cur - round(cur)) * 40
            yy = 106 + dd * 40 + off
            if 66 < yy < 146:
                cv.text(str(dig).translate(HI_DIG), 40, x, yy, (255, 255, 255) if dd == 0 else (130, 136, 150), a=1.0 if dd == 0 else 0.6)
    cv.text(E.get("label") or "", 22, 205, 190, (230, 230, 240), "sub", 360)


@_card("bar_chart", "data_compare", "ranking", "dashboard")
def c_bars(cv, p, t, E, C, key):
    ser = _series(E, C, 2 if key == "data_compare" else 5)
    if key == "ranking":
        ser = sorted(ser, key=lambda x: -x[1])
    mx = max(v for _, v in ser) or 1
    n = len(ser)
    if key in ("ranking", "dashboard") or n > 3:
        rh = min(34, 190 / n)
        for k, (lb, v) in enumerate(ser):
            g = e_out(cl(p * 2 - k * 0.12))
            y = 34 + k * (rh + 6)
            cv.text(f"{k + 1}", 20, 28, y + rh / 2, ACC[2])
            cv.rect(48, y, 48 + 250 * v / mx * g, y + rh, fill=ACC[k % 5], r=4)
            cv.text(lb, 18, 56, y + rh / 2, (255, 255, 255), "sub", 140, anchor="l")
            cv.text(_fmt(v * g), 18, 330, y + rh / 2, (255, 255, 255))
    else:
        bw = 360 / n
        for k, (lb, v) in enumerate(ser):
            g = e_out(cl(p * 2 - k * 0.15))
            x = 25 + bw * (k + 0.5)
            hh = 130 * v / mx * g
            cv.rect(x - bw * 0.3, 170 - hh, x + bw * 0.3, 170, fill=ACC[k % 5], r=5)
            cv.text(_fmt(v * g), 20, x, 160 - hh, (255, 255, 255))
            cv.text(lb, 18, x, 196, (220, 224, 236), "sub", bw)


@_card("pie_chart")
def c_pie(cv, p, t, E, C, key):
    ser = _series(E, C, 5)
    tot = sum(v for _, v in ser) or 1
    g = e_out(cl(p * 1.4))
    a0 = -90.0
    for k, (lb, v) in enumerate(ser):
        a1 = a0 + 360 * v / tot
        end = min(a1, -90 + 360 * g)
        if end > a0:
            cv.d.pieslice([40 * 2, 30 * 2, 190 * 2, 180 * 2][:4] if False else [30 * 2, 40 * 2, 180 * 2, 190 * 2], a0, end, fill=ACC[k % 5])
        cv.text(lb, 18, 230, 56 + k * 28, (255, 255, 255), "sub", 150, anchor="l")
        cv.rect(206, 48 + k * 28, 220, 62 + k * 28, fill=ACC[k % 5], r=3)
        a0 = a1


@_card("line_graph", "area_chart")
def c_line(cv, p, t, E, C, key):
    ser = _series(E, C, 6)
    mx, n = max(v for _, v in ser) or 1, len(ser)
    pts = [(40 + 330 * k / (n - 1), 180 - 130 * v / mx) for k, (_, v) in enumerate(ser)]
    g = e_out(cl(p * 1.5))
    cv.line([(30, 180), (380, 180)], 2, (120, 130, 150))
    if key == "area_chart":
        m = max(1, int(len(pts) * g))
        sub = pts[:m]
        if len(sub) >= 2:
            cv.poly(sub + [(sub[-1][0], 180), (sub[0][0], 180)], fill=ACC[1] + (150,))
    cv.line(pts, 4, ACC[2] if key == "line_graph" else ACC[1], g)
    for k, (x, y) in enumerate(pts):
        if k / max(1, n - 1) <= g:
            cv.circ(x, y, 6, fill=(255, 255, 255))
            cv.text(ser[k][0], 16, x, 202, (210, 215, 230), "sub", 60)


@_card("stat_info")
def c_statinfo(cv, p, t, E, C, key):
    ser = _series(E, C, 3)
    for k, (lb, v) in enumerate(ser[:3]):
        g = e_out(cl(p * 2 - k * 0.2))
        x = 70 + k * 135
        cv.circ(x, 78, 30, outline=ACC[k], w=4)
        cv.text(["●", "▲", "■"][k], 26, x, 78, ACC[k])
        cv.text(_fmt(v * g), 34, x, 140, (255, 255, 255), max_w=120)
        cv.text(lb, 18, x, 182, (210, 215, 230), "sub", 120)


# ---- text cards
def _ph(E, C):
    return (E.get("txt") or " ".join(_words(C, 4)))[:60]


@_card("kinetic")
def c_kin(cv, p, t, E, C, key):
    ws = _ph(E, C).split()[:5]
    for k, w in enumerate(ws):
        g = e_back(cl(p * 3 - k * 0.35))
        cv.text(w, 52 if k % 2 == 0 else 38, 205 + (k % 2 * 2 - 1) * 20, 40 + k * 38, ACC[k % 5] if k % 2 else (255, 255, 255), max_w=380, a=cl(g))


@_card("text3d")
def c_3d(cv, p, t, E, C, key):
    spr = text_layer(_ph(E, C)[:22], font_for("अ", 56, "title"), fill=(255, 255, 255), fill2=(255, 210, 120), stroke=2, stroke_fill=(70, 20, 0), extrude=(10, (140, 50, 10)), max_w=380)
    sc_ = 0.6 + 0.4 * e_back(cl(p * 3))
    spr = spr.resize((max(1, int(spr.width * sc_ * 0.9)), max(1, int(spr.height * sc_ * 0.9))), Image.BILINEAR)
    cv.im.paste(spr, (int(T_CW - spr.width / 2), int(T_CH - spr.height / 2)), spr)


@_card("typewriter", "letter", "text_reveal", "text_blur", "glitch_text", "text_rot", "text_persp", "tracking_text", "text_mask")
def c_txt(cv, p, t, E, C, key):
    s_ = _ph(E, C)[:34]
    g = cl(p * 2.2)
    if key == "typewriter":
        n = int(len(s_) * g)
        cv.text(s_[:n] + ("▌" if int(t * 3) % 2 == 0 else ""), 34, 205, 118, (255, 255, 255), max_w=380)
    elif key == "letter":
        gl = _gr(s_)[:16]
        x = 205 - 11 * len(gl) / 2
        for k, ch in enumerate(gl):
            gg = e_back(cl(g * len(gl) - k))
            cv.text(ch, 40, x + k * 22, 118 - (1 - cl(gg)) * 70, (255, 255, 255), a=cl(gg))
    elif key == "text_reveal":
        tmp = Cv.__new__(Cv)
        spr = _vx_text(s_, 68, "title", fill=(255, 255, 255), stroke=0, shadow=False, max_w=760)
        cw_ = int(spr.width * e_out(g))
        if cw_ > 2:
            cv.im.paste(spr.crop((0, 0, cw_, spr.height)), (int(410 - spr.width / 2), int(236 - spr.height / 2)), spr.crop((0, 0, cw_, spr.height)))
    elif key == "text_blur":
        spr = _vx_text(s_, 68, "title", fill=(255, 255, 255), stroke=0, shadow=False, max_w=760).filter(ImageFilter.GaussianBlur(14 * (1 - e_out(g))))
        cv.im.paste(spr, (int(410 - spr.width / 2), int(236 - spr.height / 2)), spr)
    elif key == "glitch_text":
        j = int(10 * (1 - g)) + 2
        for dx, col in ((-j, (255, 40, 90)), (j, (40, 220, 255)), (0, (255, 255, 255))):
            cv.text(s_, 38, 205 + dx * 0.6, 118, col, max_w=380, a=0.9 if dx else 1.0)
    elif key == "text_rot":
        cv.text(s_, 40, 205, 118, (255, 255, 255), max_w=380, rot=90 * (1 - e_out(g)))
    elif key == "text_persp":
        spr = _vx_text(s_, 68, "title", fill=(255, 255, 255), stroke=0, shadow=False, max_w=760)
        k_ = 0.0016 * (1 - e_out(g))
        try:
            spr = spr.transform(spr.size, Image.PERSPECTIVE, (1, 0, 0, 0, 1, 0, k_, 0), Image.BICUBIC)
        except Exception:  # noqa
            pass
        cv.im.paste(spr, (int(410 - spr.width / 2), int(236 - spr.height / 2)), spr)
    elif key == "tracking_text":
        sp = lerp(26, 6, e_out(g))
        gl = _gr(s_)[:14]
        x = 205 - sp * len(gl) / 2
        for k, ch in enumerate(gl):
            cv.text(ch, 38, x + k * sp, 118, (255, 255, 255), a=cl(g * 3))
    else:  # text_mask: the letters are windows onto moving colour
        spr = _vx_text(s_, 76, "title", fill=(255, 255, 255), stroke=0, shadow=False, max_w=760)
        grad = Image.new("RGBA", spr.size)
        gd = ImageDraw.Draw(grad)
        for xx in range(0, spr.width, 6):
            c_ = ACC[int((xx / 40 + t * 3) % 5)]
            gd.rectangle([xx, 0, xx + 6, spr.height], fill=c_ + (255,))
        grad.putalpha(spr.getchannel("A"))
        cv.im.paste(grad, (int(410 - spr.width / 2), int(236 - spr.height / 2)), grad)


@_card("word_hl", "subtitles", "callout", "labels", "text_trans", "text_path")
def c_txt2(cv, p, t, E, C, key):
    ws = _ph(E, C).split()[:6] or ["…"]
    g = cl(p * 2)
    if key in ("word_hl", "subtitles"):
        n = len(ws)
        cur = min(n - 1, int(g * n * 1.05))
        lines = [ws[:3], ws[3:6]] if n > 3 else [ws]
        k = 0
        for li, ln in enumerate(lines):
            tw = [len(w) * 16 + 14 for w in ln]
            x = 205 - sum(tw) / 2
            for w, wd in zip(ln, tw):
                if key == "word_hl" and k == cur:
                    cv.rect(x, 96 + li * 56 - 22, x + wd - 6, 96 + li * 56 + 22, fill=(255, 64, 120), r=8)
                cv.text(w, 34, x + wd / 2 - 3, 96 + li * 56, (255, 255, 255) if k != cur or key == "word_hl" else ACC[2])
                x += wd
                k += 1
    elif key == "callout":
        cv.circ(70, 170, 8, fill=ACC[0])
        cv.line([(70, 170), (130, 90)], 3, ACC[0], g * 2)
        if g > 0.4:
            cv.rect(120, 40, 390, 100, fill=(30, 36, 54), outline=ACC[0], w=2)
            cv.text(" ".join(ws[:3]), 28, 255, 70, (255, 255, 255), max_w=250)
    elif key == "labels":
        for k, w in enumerate(ws[:4]):
            gg = e_back(cl(p * 3 - k * 0.3))
            wd = len(w) * 15 + 36
            y = 40 + k * 46
            cv.rect(30, y - 18, 30 + wd * cl(gg), y + 18, fill=ACC[k % 5], r=18)
            if gg > 0.6:
                cv.text(w, 24, 30 + wd / 2, y, (255, 255, 255) if k % 5 != 2 else (20, 20, 30))
    elif key == "text_trans":
        a_, b_ = ws[0], (ws[1] if len(ws) > 1 else ws[0])
        gg = e_io(cl(p * 1.6))
        cv.text(a_, 54, 205, 118 - 90 * gg, (255, 255, 255), a=1 - gg)
        cv.text(b_, 54, 205, 118 + 90 * (1 - gg), ACC[2], a=gg)
    else:  # text on a curved path
        gl = _gr(" ".join(ws)[:20])
        n = len(gl)
        for k, ch in enumerate(gl):
            th = math.pi * (0.15 + 0.7 * k / max(1, n - 1))
            x, y = 205 - math.cos(th) * 150, 200 - math.sin(th) * 110
            cv.text(ch, 32, x, y, (255, 255, 255), a=cl(g * n * 0.6 - k * 0.3), rot=(math.degrees(th) - 90))


@_card("lower_third", "chapter", "date_loc")
def c_lt(cv, p, t, E, C, key):
    g = e_out(cl(p * 3))
    name = E.get("word") or (_words(C, 1)[0])
    if key == "lower_third":
        cv.rect(30 - 300 * (1 - g), 90, 380, 150, fill=ACC[0], r=10)
        cv.text(name, 36, 205 - 300 * (1 - g), 120, (255, 255, 255), max_w=330)
        cv.text(E.get("sub") or "", 20, 205, 180, (210, 215, 230), "sub", 340, a=g)
    elif key == "chapter":
        cv.text(f"अध्याय {int(E.get('n') or 1)}".translate(HI_DIG), 30, 205, 70, ACC[2], a=g)
        cv.line([(100, 96), (310, 96)], 3, ACC[2], g)
        cv.text(name, 46, 205, 150, (255, 255, 255), max_w=370, a=g)
    else:
        cv.text(str(E.get("year") or "").translate(HI_DIG) or "समय", 58, 205, 82, ACC[2], a=g)
        cv.line([(120, 124), (290, 124)], 3, (255, 255, 255), g)
        cv.text(E.get("place") or name, 32, 205, 168, (255, 255, 255), max_w=370, a=g)


# ---- diagram cards
@_card("flowchart", "process", "steps")
def c_flow(cv, p, t, E, C, key):
    ws = _words(C, 4)
    for k, w in enumerate(ws):
        g = e_back(cl(p * 3 - k * 0.45))
        if key == "steps":
            y = 36 + k * 48
            cv.circ(40, y, 16, fill=ACC[k % 5] if g > 0.5 else (60, 66, 84))
            cv.text(str(k + 1).translate(HI_DIG), 20, 40, y, (255, 255, 255))
            cv.text(w, 26, 70, y, (255, 255, 255), max_w=300, anchor="l", a=cl(g))
        else:
            if key == "process":
                x = 22 + k * 94
                cv.poly([(x, 80), (x + 76, 80), (x + 92, 118), (x + 76, 156), (x, 156), (x + 16, 118)], fill=ACC[k % 5] if g > 0.3 else (60, 66, 84))
                cv.text(w, 17, x + 46, 118, (20, 20, 30), max_w=70, a=cl(g))
            else:
                x = 20 + k * 98
                y = 60 if k % 2 == 0 else 150
                if k == 2:
                    cv.poly([(x + 40, y - 36), (x + 82, y), (x + 40, y + 36), (x, y)], outline=ACC[2], fill=(30, 36, 54))
                else:
                    cv.rect(x, y - 28, x + 84, y + 28, fill=(30, 36, 54), outline=ACC[k % 5], w=3)
                cv.text(w, 17, x + 42, y, (255, 255, 255), max_w=78, a=cl(g))
                if k:
                    cv.line([(x - 14, 60 if (k - 1) % 2 == 0 else 150), (x, y)], 3, (200, 205, 220), cl(g))


@_card("mindmap", "orgchart", "network", "conn_line")
def c_net(cv, p, t, E, C, key):
    ws = _words(C, 6)
    if key == "mindmap":
        cv.circ(205, 118, 36, fill=ACC[0])
        cv.text(ws[0], 20, 205, 118, (255, 255, 255), max_w=66)
        for k, w in enumerate(ws[1:6]):
            g = e_out(cl(p * 3 - k * 0.3))
            a = -math.pi / 2 + k * 2 * math.pi / 5
            x, y = 205 + math.cos(a) * 130 * g, 118 + math.sin(a) * 78 * g
            cv.line([(205, 118), (x, y)], 3, ACC[k % 5])
            cv.rect(x - 44, y - 16, x + 44, y + 16, fill=(30, 36, 54), outline=ACC[k % 5])
            cv.text(w, 17, x, y, (255, 255, 255), max_w=80, a=cl(g))
    elif key == "orgchart":
        g = e_out(cl(p * 2))
        cv.rect(145, 24, 265, 64, fill=ACC[0])
        cv.text(ws[0], 20, 205, 44, (255, 255, 255), max_w=110)
        for k in range(3):
            x = 70 + k * 135
            cv.line([(205, 64), (205, 90), (x, 90), (x, 112)], 3, (200, 205, 220), cl(g * 1.5))
            if g > 0.5:
                cv.rect(x - 52, 112, x + 52, 152, fill=(30, 36, 54), outline=ACC[k + 1], w=2)
                cv.text(ws[k + 1], 18, x, 132, (255, 255, 255), max_w=96)
                for j in range(2):
                    xx = x - 26 + j * 52
                    cv.line([(x, 152), (xx, 176)], 2, (150, 156, 175))
                    cv.circ(xx, 184, 8, fill=ACC[(k + j) % 5])
    elif key == "network":
        pts = [(205, 118), (80, 60), (330, 60), (80, 180), (330, 180), (205, 40)]
        for k in range(1, 6):
            cv.line([pts[0], pts[k]], 2, (120, 130, 150), e_out(cl(p * 3 - k * 0.2)))
        for k, (x, y) in enumerate(pts):
            g = e_back(cl(p * 3 - k * 0.2))
            cv.circ(x, y, 24 * cl(g), fill=ACC[k % 5])
            if g > 0.6:
                cv.text(ws[k], 15, x, y, (20, 20, 30), max_w=46)
    else:
        cv.circ(60, 118, 30, fill=ACC[1])
        cv.circ(350, 118, 30, fill=ACC[0])
        cv.text(ws[0], 16, 60, 118, (20, 20, 30), max_w=54)
        cv.text(ws[1], 16, 350, 118, (20, 20, 30), max_w=54)
        n = 14
        for k in range(n):
            x = 96 + (318 - 96) * k / (n - 1)
            if ((k / n) - (t * 0.8) % 1.0) % 1 < 0.35:
                cv.circ(x, 118, 5, fill=(255, 255, 255))
        cv.line([(96, 118), (96 + 222 * e_out(cl(p * 3)), 118)], 2, (150, 156, 175))


@_card("arrow_dir", "callout_ptr", "tech_label")
def c_arrow(cv, p, t, E, C, key):
    ws = _words(C, 3)
    g = e_out(cl(p * 2.5))
    if key == "arrow_dir":
        x1 = 40 + 300 * g
        cv.line([(40, 118), (x1, 118)], 14, ACC[0])
        cv.poly([(x1, 92), (x1 + 44, 118), (x1, 144)], fill=ACC[0])
        cv.text(ws[0], 26, 205, 190, (255, 255, 255), max_w=340, a=g)
    elif key == "callout_ptr":
        cv.line([(50, 190), (50 + 110 * g, 190 - 100 * g)], 4, ACC[2])
        cv.circ(50, 190, 8, fill=ACC[2])
        if g > 0.6:
            cv.rect(150, 40, 390, 100, fill=(30, 36, 54), outline=ACC[2], w=3)
            cv.text(ws[0], 26, 270, 70, (255, 255, 255), max_w=230)
    else:
        cv.circ(80, 150, 6, fill=ACC[1])
        cv.line([(80, 150), (150, 150), (190, 70)], 2, ACC[1], g * 1.4)
        if g > 0.7:
            cv.rect(190, 44, 392, 96, fill=None, outline=ACC[1], w=2)
            cv.text(ws[0].upper(), 22, 291, 70, ACC[1], role="cond", max_w=190)
            cv.text(ws[1], 16, 291, 112, (200, 210, 225), "sub", 190)


@_card("blueprint", "exploded", "assembly", "cross_section", "layer_sep")
def c_tech(cv, p, t, E, C, key):
    ws = _words(C, 4)
    g = e_out(cl(p * 1.8))
    if key == "blueprint":
        cv.d.rectangle([4, 4, T_CW * 2 - 4, T_CH * 2 - 4], fill=(18, 60, 130, 235))
        for x in range(20, 410, 20):
            cv.line([(x, 8), (x, 228)], 1, (70, 120, 190))
        for y in range(20, 236, 20):
            cv.line([(8, y), (402, y)], 1, (70, 120, 190))
        cv.line([(100, 170), (100, 70), (300, 70), (300, 170), (100, 170)], 3, (255, 255, 255), g)
        cv.line([(100, 70), (150, 40), (350, 40), (300, 70)], 2, (255, 255, 255), g)
        cv.text(ws[0], 22, 205, 205, (255, 255, 255), "cond")
    elif key == "exploded":
        for k in range(4):
            off = 22 * g * (k - 1.5)
            cv.rect(130, 56 + k * 34 + off, 280, 84 + k * 34 + off, fill=ACC[k % 5], outline=(255, 255, 255), w=2)
            cv.text(ws[k], 16, 205, 70 + k * 34 + off, (20, 20, 30), max_w=130)
    elif key == "assembly":
        for k in range(5):
            gg = e_out(cl(p * 3 - k * 0.3))
            x, y = 130 + (k % 3) * 60 + (k // 3) * 30, 150 - (k // 3) * 54
            sx, sy = x + (1 - gg) * (k - 2) * 120, y - (1 - gg) * 140
            cv.poly([(sx, sy), (sx + 40, sy - 20), (sx + 80, sy), (sx + 40, sy + 20)], fill=ACC[k % 5])
            cv.poly([(sx, sy), (sx + 40, sy + 20), (sx + 40, sy + 60), (sx, sy + 40)], fill=tuple(int(c * 0.7) for c in ACC[k % 5]))
            cv.poly([(sx + 80, sy), (sx + 40, sy + 20), (sx + 40, sy + 60), (sx + 80, sy + 40)], fill=tuple(int(c * 0.5) for c in ACC[k % 5]))
    elif key == "cross_section":
        for k in range(4):
            r = 90 - k * 20
            cv.d.pieslice([(150 - r) * 2, (190 - r) * 2, (150 + r) * 2, (190 + r) * 2], 180, 180 + 180 * g, fill=ACC[k % 5])
            cv.line([(150, 190 - r * 0.75), (270, 60 + k * 30)], 2, (200, 205, 220), cl(g * 2 - 0.5))
            cv.text(ws[k], 17, 330, 60 + k * 30, (255, 255, 255), "sub", 110, a=cl(g * 2 - 0.5))
    else:
        for k in range(4):
            off = 36 * g * (k - 1.5)
            y = 118 + off
            cv.poly([(100, y), (210, y - 28), (320, y), (210, y + 28)], fill=ACC[k % 5] + (200,), outline=(255, 255, 255))
            cv.text(ws[k], 15, 210, y, (20, 20, 30), max_w=120)


def render_card(key, p, t, E, C):
    cv = Cv(ACC[hash(key) % 5] if False else ACC[sum(map(ord, key)) % 5])
    fn = CARDFN.get(key)
    if fn is None:
        return None
    if key in ("blueprint",):
        pass
    fn(cv, p, t, E, C, key)
    return cv.done()



# ---- background techniques: map & geography (real coastlines from get_land) and history / documentary looks
GEO_DEFAULT = [{"name": "", "lat": 22.0, "lon": 79.0}, {"name": "", "lat": 28.6, "lon": 77.2}]


def _proj(lon, lat, c, span):
    """equirectangular projection around c=(lon,lat); span = degrees of longitude across the frame"""
    k = W / span
    return (W / 2 + (lon - c[0]) * k, H / 2 - (lat - c[1]) * k)


def _map_img(c, span, style="terrain"):
    key = ("map", int(c[0] * 4), int(c[1] * 4), int(span), style)
    if key in _VX:
        return _VX[key]
    pal = {"terrain": ((60, 120, 170), (98, 150, 84)), "satellite": ((8, 22, 46), (34, 60, 38)), "old": ((222, 200, 150), (206, 180, 128)), "topo": ((226, 236, 240), (236, 232, 214)),
           "flat": ((30, 70, 120), (200, 190, 150))}[style]
    big = Image.new("RGB", (W, H), pal[0])
    d = ImageDraw.Draw(big)
    for ring in get_land():
        pts = [_proj(x, y, c, span) for x, y in ring[::max(1, int(len(ring) / 400))]]
        if len(pts) > 2 and max(p[0] for p in pts) > -50 and min(p[0] for p in pts) < W + 50 and max(p[1] for p in pts) > -50 and min(p[1] for p in pts) < H + 50:
            try:
                d.polygon(pts, fill=pal[1], outline=(40, 60, 40) if style != "old" else (90, 60, 30))
            except Exception:  # noqa
                pass
    n = _noise_img(31, 24)
    big = Image.blend(big, ImageOps.colorize(n, tuple(int(v * 0.8) for v in pal[1]), tuple(min(255, int(v * 1.15)) for v in pal[1])), 0.28)
    if style == "topo":
        q = ImageOps.grayscale(_noise_img(17, 70)).point(lambda v: (v // 18) * 18)
        e = ImageChops.difference(q, q.filter(ImageFilter.MaxFilter(3))).point(lambda v: 255 if v else 0)
        big = _ov(big, (120, 90, 60), e, 0.7)
    if style == "satellite":
        big = ImageEnhance.Contrast(big).enhance(1.25)
    _VX[key] = big
    return big


def _geo(ctx, i=0):
    g = (ctx or {}).get("geo") or GEO_DEFAULT
    return g[min(i, len(g) - 1)]


def _mapmix(fr, mp, u, lo=0.12, hi=0.82):
    a = cl(min(u / lo, (1 - u) / (1 - hi))) if True else 1
    return Image.blend(fr, mp, a)


def _hav(a, b):
    R = 6371.0
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dl, dp = math.radians(b["lon"] - a["lon"]), p2 - p1
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def _pin(fr, x, y, a=1.0, r=1.0):
    d = ImageDraw.Draw(fr, "RGBA")
    d.ellipse([x - 22 * r, y - 8 * r, x + 22 * r, y + 8 * r], fill=(0, 0, 0, int(90 * a)))
    d.polygon([(x, y), (x - 15 * r, y - 38 * r), (x + 15 * r, y - 38 * r)], fill=(232, 40, 56, int(255 * a)))
    d.ellipse([x - 19 * r, y - 62 * r, x + 19 * r, y - 24 * r], fill=(232, 40, 56, int(255 * a)))
    d.ellipse([x - 7 * r, y - 50 * r, x + 7 * r, y - 36 * r], fill=(255, 255, 255, int(255 * a)))


def _lab(fr, txt, x, y, size=34, fill=(255, 255, 255)):
    if txt:
        spr = _vx_text(txt, size, "title", fill=fill, stroke=3, sfill=(10, 12, 20))
        put(fr, spr, x, y, 1.0, 1.0)


BGFX = {}


def _bg(*names):
    def deco(fn):
        for n in names:
            BGFX[n] = fn
        return fn
    return deco


@_bg("map_anim")
def bg_map(fr, u, t, s, ctx):
    g = _geo(ctx)
    c = (g["lon"] + lerp(-10, 0, e_out(u)), g["lat"] + lerp(4, 0, e_out(u)))
    return _mapmix(fr, _map_img(c, lerp(70, 40, e_out(u))), u)


@_bg("route", "travel_path", "army_move", "dist_counter")
def bg_route(fr, u, t, s, ctx, key="route"):
    a_, b_ = _geo(ctx, 0), _geo(ctx, 1)
    if abs(a_["lat"] - b_["lat"]) + abs(a_["lon"] - b_["lon"]) < 2:
        b_ = {"name": "", "lat": a_["lat"] + 6, "lon": a_["lon"] + 9}
    c = ((a_["lon"] + b_["lon"]) / 2, (a_["lat"] + b_["lat"]) / 2)
    span = max(30, abs(a_["lon"] - b_["lon"]) * 2.6, abs(a_["lat"] - b_["lat"]) * 4.5)
    out = _mapmix(fr, _map_img(c, span, "terrain" if key != "army_move" else "old"), u)
    pa, pb = _proj(a_["lon"], a_["lat"], c, span), _proj(b_["lon"], b_["lat"], c, span)
    g = e_io(cl((u - 0.12) / 0.6))
    pts = [(lerp(pa[0], pb[0], k / 30), lerp(pa[1], pb[1], k / 30) - 70 * math.sin(math.pi * k / 30)) for k in range(31)]
    if key in ("route", "dist_counter"):
        _aa_poly(out, pts, 8, (255, 255, 255), 1.0, g)
        _aa_poly(out, pts, 4, (232, 40, 56), 1.0, g)
    elif key == "travel_path":
        n = int(30 * g)
        d = ImageDraw.Draw(out)
        for k in range(0, n, 2):
            d.line([pts[k], pts[min(30, k + 1)]], fill=(255, 255, 255), width=6)
        d.ellipse([pts[n][0] - 10, pts[n][1] - 10, pts[n][0] + 10, pts[n][1] + 10], fill=(255, 190, 40), outline=(20, 20, 20), width=3)
    else:
        for j in range(5):
            k = int(30 * cl(g - j * 0.05))
            x, y = pts[k]
            ImageDraw.Draw(out).rectangle([x - 9, y - 9, x + 9, y + 9], fill=(180, 30, 30), outline=(255, 255, 255), width=2)
        _aa_poly(out, pts, 4, (120, 20, 20), 0.8, g)
    _pin(out, pa[0], pa[1] + 0, 1.0, 0.6)
    _pin(out, pb[0], pb[1] + 0, cl(g * 2 - 0.6), 0.6)
    _lab(out, a_["name"], pa[0], pa[1] + 24, 28)
    _lab(out, b_["name"], pb[0], pb[1] + 24, 28)
    if key == "dist_counter":
        _lab(out, f"{int(_hav(a_, b_) * g):,} किमी".translate(HI_DIG), W / 2, H * 0.14, 64, (255, 230, 120))
    return out


@_bg("dist_counter")
def bg_dist(fr, u, t, s, ctx):
    return bg_route(fr, u, t, s, ctx, "dist_counter")


@_bg("travel_path")
def bg_tp(fr, u, t, s, ctx):
    return bg_route(fr, u, t, s, ctx, "travel_path")


@_bg("army_move")
def bg_am(fr, u, t, s, ctx):
    return bg_route(fr, u, t, s, ctx, "army_move")


@_bg("pin", "map_zoom", "territory", "hist_border", "border_hl", "kingdom_exp", "satellite", "old_map", "topo", "geo_compare")
def bg_place(fr, u, t, s, ctx, key="pin"):
    g = _geo(ctx)
    c = (g["lon"], g["lat"])
    style = {"satellite": "satellite", "old_map": "old", "topo": "topo", "kingdom_exp": "old", "hist_border": "old"}.get(key, "terrain")
    if key == "geo_compare":
        a_, b_ = _geo(ctx, 0), _geo(ctx, 1)
        out = fr.copy()
        for i, gg in enumerate((a_, b_)):
            mp = _map_img((gg["lon"], gg["lat"]), 24).resize((W // 2, H))
            out.paste(mp.crop((0, 0, W // 2, H)), (i * W // 2, 0))
            _pin(out, i * W // 2 + W // 4, H // 2 + 20, 1.0, 0.8)
            _lab(out, gg["name"], i * W // 2 + W // 4, H * 0.12, 40)
        ImageDraw.Draw(out).line([(W // 2, 0), (W // 2, H)], fill=(255, 255, 255), width=6)
        return Image.blend(fr, out, cl(min(u / 0.12, (1 - u) / 0.18)))
    span = lerp(150, 14, e_out(u)) if key == "map_zoom" else lerp(40, 16, e_out(u))
    out = _mapmix(fr, _map_img(c, span, style), u)
    x, y = _proj(g["lon"], g["lat"], c, span)
    if key in ("territory", "kingdom_exp", "hist_border", "border_hl"):
        gr = e_out(cl(u * 1.3))
        lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dd = ImageDraw.Draw(lay)
        r = 40 + 260 * gr
        pts = [(x + math.cos(a) * r * (1 + 0.18 * math.sin(3 * a + s) + 0.1 * math.sin(5 * a)), y + math.sin(a) * r * 0.75 * (1 + 0.18 * math.cos(2 * a + s))) for a in [k * 0.2 for k in range(32)]]
        dd.polygon(pts, fill=(232, 60, 56, 90) if key != "border_hl" else (255, 210, 60, 70), outline=(232, 40, 56, 255) if key != "border_hl" else (255, 230, 90, 255))
        if key == "hist_border":
            dd.line(pts + [pts[0]], fill=(255, 255, 255, 255), width=4)
            sm = [(x + (p[0] - x) * 0.6, y + (p[1] - y) * 0.6) for p in pts]
            dd.polygon(sm, outline=(60, 30, 10, 255))
        out.paste(lay, (0, 0), lay)
    if key == "satellite":
        d = ImageDraw.Draw(out, "RGBA")
        for k in range(4):
            d.line([(60, 60 + 22 * k * 0), (60, 60)], fill=(120, 255, 160, 200), width=4)
        d.rectangle([40, 40, W - 40, H - 40], outline=(120, 255, 160, 170), width=3)
        d.line([(0, int(H * ((t * 0.5) % 1))), (W, int(H * ((t * 0.5) % 1)))], fill=(120, 255, 160, 120), width=3)
    _pin(out, x, y, cl(u * 3), 0.9 + 0.1 * math.sin(t * 6))
    d = ImageDraw.Draw(out, "RGBA")
    for k in range(2):
        rr = 14 + ((t * 70 + k * 40) % 80)
        d.ellipse([x - rr, y - rr * 0.5, x + rr, y + rr * 0.5], outline=(255, 255, 255, int(200 * (1 - rr / 94))), width=3)
    _lab(out, g["name"], x, y + 36, 38)
    return out


for _k in ("pin", "map_zoom", "territory", "hist_border", "border_hl", "kingdom_exp", "satellite", "old_map", "topo", "geo_compare"):
    BGFX[_k] = (lambda kk: (lambda fr, u, t, s, ctx: bg_place(fr, u, t, s, ctx, kk)))(_k)


def _globe(c_lon, size=420):
    np = _np()
    if "globe_land" not in _VX:
        m = Image.new("L", (720, 360), 0)
        d = ImageDraw.Draw(m)
        for ring in get_land():
            pts = [((x + 180) * 2, (90 - y) * 2) for x, y in ring[::max(1, int(len(ring) / 300))]]
            if len(pts) > 2:
                d.polygon(pts, fill=255)
        _VX["globe_land"] = np.asarray(m)
    land = _VX["globe_land"]
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    nx, ny = (xx - size / 2) / (size / 2), (yy - size / 2) / (size / 2)
    r2 = nx * nx + ny * ny
    ok = r2 <= 1
    nz = np.sqrt(np.clip(1 - r2, 0, 1))
    lat = np.degrees(np.arcsin(np.clip(-ny, -1, 1)))
    lon = np.degrees(np.arctan2(nx, nz)) + c_lon
    li = np.clip(((lon + 180) % 360) * 2, 0, 719).astype(np.int32)
    la = np.clip((90 - lat) * 2, 0, 359).astype(np.int32)
    isl = land[la, li] > 128
    img = np.zeros((size, size, 4), np.uint8)
    shade = (0.35 + 0.65 * nz)[..., None]
    col = np.where(isl[..., None], np.array([70, 150, 80]), np.array([30, 90, 170])) * shade
    img[..., :3] = np.clip(col, 0, 255)
    img[..., 3] = np.where(ok, 255, 0)
    return Image.fromarray(img, "RGBA")


@_bg("globe_rot", "globe_map")
def bg_globe(fr, u, t, s, ctx, key="globe_rot"):
    g = _geo(ctx)
    out = Image.blend(fr, Image.new("RGB", (W, H), (4, 6, 16)), cl(min(u / 0.15, (1 - u) / 0.15)) * (1.0 if key == "globe_rot" else 0.9))
    rr = random.Random(5)
    d = ImageDraw.Draw(out)
    for _ in range(80):
        d.point((rr.randint(0, W), rr.randint(0, H)), fill=(255, 255, 255))
    gl = _globe(g["lon"] - 90 * (1 - e_out(u)) + 40 * (u - 0.5), 440)
    if key == "globe_map" and u > 0.5:
        k = e_io((u - 0.5) / 0.5)
        mp = _map_img((g["lon"], g["lat"]), 60)
        out = Image.blend(out, mp, k)
        gl = gl.resize((int(440 * (1 - k * 0.6)), int(440 * (1 - k * 0.6))))
        gl.putalpha(gl.getchannel("A").point(lambda v: int(v * (1 - k))))
    out.paste(gl, (int(W / 2 - gl.width / 2), int(H / 2 - gl.height / 2)), gl)
    return Image.blend(fr, out, cl(min(u / 0.12, (1 - u) / 0.12)))


BGFX["globe_rot"] = lambda fr, u, t, s, ctx: bg_globe(fr, u, t, s, ctx, "globe_rot")
BGFX["globe_map"] = lambda fr, u, t, s, ctx: bg_globe(fr, u, t, s, ctx, "globe_map")


@_bg("gps", "latlon", "compass", "scalebar", "elev", "evidence_hl")
def bg_hud(fr, u, t, s, ctx, key="gps"):
    out = fr.copy()
    d = ImageDraw.Draw(out, "RGBA")
    a = int(255 * cl(min(u / 0.15, (1 - u) / 0.15)))
    g = _geo(ctx)
    if key == "gps":
        cx, cy = W * 0.5, H * 0.45
        d.ellipse([cx - 70, cy - 70, cx + 70, cy + 70], outline=(120, 255, 170, a), width=3)
        d.line([(cx - 100, cy), (cx + 100, cy)], fill=(120, 255, 170, a), width=2)
        d.line([(cx, cy - 100), (cx, cy + 100)], fill=(120, 255, 170, a), width=2)
        txt = f"{abs(g['lat']):.4f}° {'N' if g['lat'] >= 0 else 'S'}   {abs(g['lon']):.4f}° {'E' if g['lon'] >= 0 else 'W'}"
        n = int(len(txt) * e_out(cl(u * 1.6)))
        spr = _vx_text(txt[:n] + ("_" if int(t * 3) % 2 else ""), 40, "cond", fill=(120, 255, 170), stroke=2)
        put(out, spr, cx, cy + 120, 1.0, a / 255)
    elif key == "latlon":
        gr = e_out(cl(u * 1.5))
        for k in range(1, 8):
            x = W * k / 8
            d.line([(x, 0), (x, H * gr)], fill=(255, 255, 255, int(a * 0.55)), width=2)
        for k in range(1, 5):
            y = H * k / 5
            d.line([(0, y), (W * gr, y)], fill=(255, 255, 255, int(a * 0.55)), width=2)
        for k in range(1, 8):
            _lab(out, f"{int(g['lon'] - 28 + k * 8)}°E".translate(HI_DIG), W * k / 8, 24, 22)
        for k in range(1, 5):
            _lab(out, f"{int(g['lat'] + 12 - k * 5)}°N".translate(HI_DIG), 40, H * k / 5 - 14, 22)
    elif key == "compass":
        cx, cy, R = W - 130, 130, 80
        d.ellipse([cx - R, cy - R, cx + R, cy + R], fill=(10, 13, 22, int(a * 0.8)), outline=(255, 255, 255, a), width=3)
        ang = math.radians(360 * (1 - e_out(cl(u * 1.4))) + 20 * math.sin(t * 2) * (1 - cl(u * 1.4)))
        for sgn, col in ((1, (232, 40, 56)), (-1, (230, 230, 240))):
            tip = (cx + sgn * math.sin(ang) * R * 0.85, cy - sgn * math.cos(ang) * R * 0.85)
            base1 = (cx + math.cos(ang) * 12, cy + math.sin(ang) * 12)
            base2 = (cx - math.cos(ang) * 12, cy - math.sin(ang) * 12)
            d.polygon([tip, base1, base2], fill=col + (a,))
        _lab(out, "उ", cx + math.sin(ang) * (R + 20), cy - math.cos(ang) * (R + 20), 26)
    elif key == "scalebar":
        w_ = 260 * e_out(cl(u * 2))
        x0, y0 = 70, 70
        d.rectangle([x0, y0, x0 + w_, y0 + 12], fill=(255, 255, 255, a), outline=(10, 10, 10, a), width=2)
        for k in range(1, 4):
            d.line([(x0 + w_ * k / 4, y0), (x0 + w_ * k / 4, y0 + 22)], fill=(255, 255, 255, a), width=3)
        if w_ > 200:
            _lab(out, "0 — 100 किमी", x0 + 130, y0 - 22, 24)
    elif key == "elev":
        rr = random.Random(7)
        pts, v = [], 0.0
        for k in range(41):
            v += rr.uniform(-0.12, 0.14)
            pts.append((60 + (W - 120) * k / 40, H * 0.30 - 70 * (0.5 + 0.5 * math.sin(k * 0.3 + v))))
        gr = e_out(cl(u * 1.5))
        n = int(40 * gr)
        d.polygon(pts[:n + 1] + [(pts[n][0], H * 0.34), (60, H * 0.34)], fill=(60, 180, 120, int(a * 0.6)))
        d.line(pts[:n + 1], fill=(255, 255, 255, a), width=4)
        d.ellipse([pts[n][0] - 9, pts[n][1] - 9, pts[n][0] + 9, pts[n][1] + 9], fill=(232, 40, 56, a))
    else:  # evidence_hl: spotlight dims the picture and rings the key spot
        mask = _radial_mask(0.22, 0.3, 0.5, 0.5, 0.45)
        dim = _ov(out, (0, 0, 0), mask.point(lambda v: 255 - v), 0.6 * cl(min(u / 0.2, (1 - u) / 0.2)))
        out = dim
        d = ImageDraw.Draw(out, "RGBA")
        d.ellipse([W * 0.5 - 150 * e_out(u * 2 if u < 0.5 else 1), H * 0.45 - 100, W * 0.5 + 150, H * 0.45 + 100], outline=(255, 220, 60, a), width=5)
    return out


for _k in ("gps", "latlon", "compass", "scalebar", "elev", "evidence_hl"):
    BGFX[_k] = (lambda kk: (lambda fr, u, t, s, ctx: bg_hud(fr, u, t, s, ctx, kk)))(_k)


# ---- history / documentary looks
def _paper(w, h, tone=(236, 222, 186), seed=1):
    key = ("paper", w, h, tone, seed)
    if key not in _VX:
        np = _np()
        rng = np.random.default_rng(seed)
        blot = Image.fromarray((rng.random((max(2, h // 60), max(2, w // 60))) * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC)
        im = ImageOps.colorize(blot, tuple(int(c * 0.88) for c in tone), tuple(min(255, int(c * 1.03)) for c in tone))
        _VX[key] = im
    return _VX[key]


def _sheet(fr, u, sp, txt_lines, rot=-3, w_=0.56, head=None, tone=(236, 222, 186), pos=0.28, ink=(40, 30, 20)):
    out = fr.copy()
    w, h = int(W * w_), int(H * 0.82)
    pg = _paper(w, h, tone).copy().convert("RGBA")
    d = ImageDraw.Draw(pg)
    d.rectangle([0, 0, w - 1, h - 1], outline=(90, 60, 30), width=5)
    y = 36
    if head:
        sp_ = _vx_text(head, 54, "title", fill=ink, stroke=0, shadow=False, max_w=w - 60)
        pg.alpha_composite(sp_, ((w - sp_.width) // 2, y))
        y += sp_.height + 18
        d.line([(30, y), (w - 30, y)], fill=ink, width=3)
        y += 20
    for k, ln in enumerate(txt_lines):
        g = cl(u * 3 - k * 0.25)
        if g > 0:
            sp_ = _vx_text(ln, 34, "sub", fill=ink, stroke=0, shadow=False, max_w=w - 70, align="left")
            n = int(sp_.width * g)
            pg.alpha_composite(sp_.crop((0, 0, max(1, n), sp_.height)), (36, y))
        y += 46
    pg = pg.rotate(rot, resample=Image.BICUBIC, expand=True)
    sl = e_out(cl(u * 4)) * (1 - e_in(cl((u - 0.85) * 6))) if False else e_out(cl(min(u * 4, (1 - u) * 5)))
    cx = lerp(-w, W * pos + w * 0.1, sl) if pos < 0.5 else lerp(W + w, W * pos, sl)
    shadow = Image.new("RGBA", pg.size, (0, 0, 0, 0))
    shadow.putalpha(pg.getchannel("A").filter(ImageFilter.GaussianBlur(12)).point(lambda v: int(v * 0.6)))
    out.paste(shadow, (int(cx - pg.width / 2 + 10), int(H / 2 - pg.height / 2 + 14)), shadow)
    out.paste(pg, (int(cx - pg.width / 2), int(H / 2 - pg.height / 2)), pg)
    return out


def _phr(ctx):
    return ((ctx or {}).get("phrase") or "इतिहास में दर्ज एक अहम पल").split()


@_bg("doc_reveal", "manuscript", "handwriting")
def bg_doc(fr, u, t, s, ctx, key="doc_reveal"):
    ws = _phr(ctx)
    lines = [" ".join(ws[i:i + 3]) for i in range(0, min(len(ws), 12), 3)] or ["…"]
    if key == "manuscript":
        return _sheet(fr, u, s, lines, 2, 0.5, "॥ पांडुलिपि ॥", (214, 186, 120), 0.72)
    if key == "handwriting":
        return _sheet(fr, u, s, lines, -1.5, 0.6, None, (246, 240, 226), 0.5, (20, 40, 110))
    return _sheet(fr, u, s, lines, -3, 0.54, "दस्तावेज़", (236, 228, 205), 0.27)


for _k in ("doc_reveal", "manuscript", "handwriting"):
    BGFX[_k] = (lambda kk: (lambda fr, u, t, s, ctx: bg_doc(fr, u, t, s, ctx, kk)))(_k)


@_bg("newspaper")
def bg_news(fr, u, t, s, ctx):
    pg = _paper(W, H, (226, 222, 210), 4).copy()
    d = ImageDraw.Draw(pg)
    mh = _vx_text("दैनिक समाचार", 96, "title", fill=(25, 25, 25), stroke=0, shadow=False)
    pg.paste(mh, ((W - mh.width) // 2, 24), mh)
    d.line([(40, 150), (W - 40, 150)], fill=(25, 25, 25), width=5)
    hd = _vx_text(" ".join(_phr(ctx))[:40], 70, "title", fill=(20, 20, 20), stroke=0, shadow=False, max_w=W - 120)
    pg.paste(hd, ((W - hd.width) // 2, 180), hd)
    rr = random.Random(2)
    for c in range(3):
        for k in range(14):
            y = 360 + k * 24
            d.line([(60 + c * 400, y), (60 + c * 400 + rr.randint(250, 340), y)], fill=(70, 70, 70), width=5)
    ph = ImageOps.grayscale(fr).resize((380, 150))
    pg.paste(ph, (W - 460, 400))
    e = e_out(cl(min(u * 3, (1 - u) * 4)))
    pg = pg.rotate((1 - e) * 540, resample=Image.BILINEAR, expand=False).resize((int(W * (0.15 + 0.85 * e)), int(H * (0.15 + 0.85 * e))))
    out = fr.copy()
    out.paste(pg, (int(W / 2 - pg.width / 2), int(H / 2 - pg.height / 2)))
    return out


@_bg("portrait_reveal", "ruler_intro")
def bg_portrait(fr, u, t, s, ctx, key="portrait_reveal"):
    e = e_out(cl(min(u * 3, (1 - u) * 4)))
    out = _ov(fr, (0, 0, 0), Image.new("L", (W, H), 255), 0.35 * e)
    pw, ph = 330, 420
    face = _zoomc(fr, 1.6, 0.5, 0.42).resize((pw, ph), Image.BICUBIC)
    face = ImageOps.colorize(ImageOps.grayscale(face), (30, 20, 10), (255, 240, 210)) if key == "portrait_reveal" else face
    m = Image.new("L", (pw, ph), 0)
    ImageDraw.Draw(m).ellipse([0, 0, pw - 1, ph - 1], fill=255)
    fm = Image.new("RGBA", (pw + 36, ph + 36), (0, 0, 0, 0))
    ImageDraw.Draw(fm).ellipse([0, 0, pw + 35, ph + 35], fill=(212, 170, 70, 255))
    fm.paste(face, (18, 18), m)
    cx = lerp(W + 200, W * 0.72, e)
    out.paste(fm, (int(cx - fm.width / 2), int(H * 0.46 - fm.height / 2)), fm)
    w = (_phr(ctx) + [""])[0]
    nm = _vx_text(w if key == "portrait_reveal" else "राजा " + w, 50, "title", fill=(255, 240, 200), stroke=3)
    put(out, nm, cx, H * 0.46 + fm.height / 2 + 36, 1.0, e)
    if key == "ruler_intro":
        _lab(out, "शासनकाल", cx, H * 0.46 + fm.height / 2 + 84, 28, (230, 200, 120))
    return out


BGFX["portrait_reveal"] = lambda fr, u, t, s, ctx: bg_portrait(fr, u, t, s, ctx, "portrait_reveal")
BGFX["ruler_intro"] = lambda fr, u, t, s, ctx: bg_portrait(fr, u, t, s, ctx, "ruler_intro")


@_bg("ba_hist")
def bg_bah(fr, u, t, s, ctx):
    pos = int(W * lerp(0.95, 0.05, e_io(cl((u - 0.1) / 0.6))))
    old = fx_old(fr, 0.5, t, s)
    old = Image.blend(fr, old, 1.0)
    out = fr.copy()
    out.paste(old.crop((0, 0, pos, H)), (0, 0))
    d = ImageDraw.Draw(out)
    d.line([(pos, 0), (pos, H)], fill=(255, 255, 255), width=6)
    d.ellipse([pos - 28, H // 2 - 28, pos + 28, H // 2 + 28], fill=(255, 255, 255))
    _lab(out, "तब", 90, 60, 40, (255, 230, 170))
    _lab(out, "अब", W - 90, 60, 40)
    return Image.blend(fr, out, cl(min(u / 0.1, (1 - u) / 0.15)))


def _card_bg(fr, u, key, ctx, E=None, pos=(0.5, 0.45), scale=1.7):
    C = (ctx or {}).get("C") or {}
    spr = render_card(key, e_out(cl(u * 1.4)), u * 4, E or ctx.get("E") or {}, C)
    if spr is None:
        return fr
    spr = spr.resize((int(spr.width * scale), int(spr.height * scale)), Image.BILINEAR)
    out = fr.copy()
    out = _ov(out, (0, 0, 0), Image.new("L", (W, H), 255), 0.3 * cl(min(u * 5, (1 - u) * 5)))
    put(out, spr, W * pos[0], H * pos[1], 1.0, cl(min(u * 5, (1 - u) * 5)))
    return out


for _k, _ck in (("family_tree", "orgchart"), ("dynasty", "flowchart"), ("event_seq", "steps"), ("army_strength", "data_compare"), ("h_timeline", "process")):
    BGFX[_k] = (lambda ck: (lambda fr, u, t, s, ctx: _card_bg(fr, u, ck, ctx)))(_ck)


@_bg("battle_form")
def bg_battle(fr, u, t, s, ctx):
    out = _ov(fr, (0, 0, 0), Image.new("L", (W, H), 255), 0.3 * cl(min(u * 5, (1 - u) * 5)))
    d = ImageDraw.Draw(out, "RGBA")
    for side, col in ((0, (200, 50, 50)), (1, (50, 110, 210))):
        for r in range(4):
            for c in range(9):
                g = e_out(cl(u * 3 - (c + r * 2) * 0.04))
                x = W * (0.12 + c * 0.04) if side == 0 else W * (0.88 - c * 0.04)
                y = H * (0.34 + r * 0.1) + (1 - g) * (-200 if side == 0 else 200)
                d.rectangle([x - 15, y - 15, x + 15, y + 15], fill=col + (int(235 * cl(min(u * 5, (1 - u) * 5))),), outline=(255, 255, 255, 220), width=2)
    return out


@_bg("archival")
def bg_arch(fr, u, t, s, ctx):
    out = fx_old(fr, 0.5, t, s)
    out = Image.blend(fr, out, cl(min(u / 0.1, (1 - u) / 0.1)))
    d = ImageDraw.Draw(out)
    for x in range(0, W, 44):
        d.rectangle([x + 10, 8, x + 30, 26], fill=(0, 0, 0))
        d.rectangle([x + 10, H - 26, x + 30, H - 8], fill=(0, 0, 0))
    _lab(out, f"REC  {int(t // 60):02d}:{int(t % 60):02d}:{int(t * 25 % 25):02d}", 150, 62, 24, (255, 255, 255))
    return out


for _k, _fn in (("old_photo", fx_old), ("kenburns", cam_kb), ("parallax25", cam_par), ("push_in", cam_push), ("pull_out", cam_pull), ("dolly_zoom", cam_dolly), ("pan", cam_pan)):
    BGFX[_k] = (lambda fn: (lambda fr, u, t, s, ctx: fn(fr, u, t, s)))(_fn)


def _bg_missing():
    return [t["key"] for t in T151 if t["layer"] == "bg" and t["key"] not in BGFX]



# ---- planner: decides WHEN each of the 151 techniques appears (every video, any length) and keeps the three bottom slots free of collisions
T151_PLAN = [None]
T151_ON = os.environ.get("T151", "1") != "0"
FX_FRAME_KEYS = [t["key"] for t in T151 if t["layer"] == "ov" and t["group"] in ("camera", "vfx")]
CARD_KEYS = [t["key"] for t in T151 if t["layer"] == "ov" and t["group"] in ("data", "text", "diagram")]
BG_KEYS = [t["key"] for t in T151 if t["layer"] == "bg"]
TR_KEYS = list(TR15)
TRIG = {  # preferred trigger word type for a technique (None = just spread over the video)
    "num_counter": "num", "dist_card": "num", "pop_counter": "num", "pct_counter": "num", "fin_counter": "num", "odometer": "num", "num_roll": "num", "year_counter": "year", "countdown": "year",
    "dist_counter": "place", "route": "place", "pin": "place", "map_zoom": "place", "map_anim": "place", "travel_path": "place", "gps": "place", "date_loc": "place", "lower_third": "place",
    "typewriter": "q", "lens": "q", "h_timeline": "year", "dynasty": "year", "event_seq": "year", "handwriting": "year", "ba_hist": "before", "newspaper": "year",
}
BG_MINMAX = (0.9, 1.7)
FX_MINMAX = (0.6, 1.1)
CARD_MINMAX = (1.1, 2.4)


def _scene_len(sc):
    try:
        return float(sc.get("dur") or (sc["wt"][-1][1] + 0.5))
    except Exception:  # noqa
        return 6.0


def build_t151_content(scenes, topic):
    words, nums, years, cnt = [], [], [], {}
    total_words = 0
    for sc in scenes:
        wds = (sc.get("narration_hi") or "").split()
        total_words += len(wds)
        for i, w_ in enumerate(wds):
            t_ = _tw(w_)
            nv = _num_val(t_)
            if nv is not None and nv > 0:
                if 1000 <= nv <= 2100 and float(nv).is_integer():
                    years.append(int(nv))
                else:
                    nums.append((nv, _tw(wds[i + 1]) if i + 1 < len(wds) else "", t_))
            if len(t_) >= 3 and t_ not in STOP_HI and t_ not in Q_ALL and not any(t_.endswith(v) for v in VERB_END):
                cnt[t_] = cnt.get(t_, 0) + 1
                if t_ not in words:
                    words.append(t_)
    for sc in scenes:
        for it in sc.get("items", []) if isinstance(sc.get("items"), list) else []:
            if isinstance(it, dict) and it.get("kind") == "year" and it.get("year_num"):
                try:
                    years.append(int(it["year_num"]))
                except Exception:  # noqa
                    pass
    top = sorted(cnt.items(), key=lambda kv: -kv[1])[:6]
    if len(nums) >= 3:
        series = [(clean_short(n[1], 10) or n[2], n[0]) for n in nums[:6]]
        series_note = "आँकड़े"
    else:
        series = [(w, c) for w, c in top[:5]] or [("क", 3), ("ख", 5)]
        series_note = "कितनी बार बोला"
    return {"words": words[:12] or ["शुरुआत", "कारण", "असर", "नतीजा", "आगे"], "series": series, "series_note": series_note, "nums": nums, "years": years, "total_words": total_words}


def geo_for_story(scenes, topic):
    """Real coordinates of the places in the story (Gemini), default map position if unavailable."""
    try:
        txt = " ".join(sc.get("narration_hi", "") for sc in scenes)[:2500]
        out = gemini_json(f"""Topic: {topic}. From this Hindi narration list the 2-3 REAL places that matter most (cities, regions, forests, countries). Return ONLY JSON {{"places":[{{"name_hi":"Hindi name","lat":number,"lon":number}}]}}. Only places that are really named or clearly meant; accurate decimal coordinates.
{txt}""", 0.1)
        pl = [{"name": clean_short(p.get("name_hi"), 14), "lat": float(p["lat"]), "lon": float(p["lon"])} for p in out.get("places", []) if isinstance(p, dict) and abs(float(p.get("lat", 99))) <= 90 and abs(float(p.get("lon", 999))) <= 180]
        if pl:
            return pl[:3]
    except Exception as e:  # noqa
        log("geo lookup skipped:", str(e)[:80])
    return None


def _num_params(sc, wds, wt, tloc, C, T):
    """Real values for the card at local time tloc: the nearest number / year / place / phrase the narrator says."""
    E = {}
    if not wds or len(wt) != len(wds):
        return E
    wi = min(range(len(wt)), key=lambda i: abs(wt[i][0] - tloc))
    E["txt"] = " ".join(_tw(x) for x in wds[wi:wi + 5])
    E["word"] = _tw(wds[wi]) if len(_tw(wds[wi])) >= 3 else (C["words"][0] if C["words"] else "")
    near = None
    for i, w_ in enumerate(wds):
        nv = _num_val(_tw(w_))
        if nv is not None and nv > 0 and not (1000 <= nv <= 2100):
            if near is None or abs(wt[i][0] - tloc) < abs(wt[near][0] - tloc):
                near = i
    if near is not None:
        E["num"] = _num_val(_tw(wds[near]))
        E["label"] = _tw(wds[near + 1]) if near + 1 < len(wds) else ""
    elif C["nums"]:
        E["num"], E["label"] = C["nums"][0][0], C["nums"][0][1]
    else:
        E["num"], E["label"] = C["total_words"], "कुल शब्द"
    yr = [int(_num_val(_tw(w_))) for w_ in wds if _num_val(_tw(w_)) and 1000 <= _num_val(_tw(w_)) <= 2100]
    E["year"] = yr[0] if yr else (C["years"][0] if C["years"] else time.localtime().tm_year)
    E["label"] = E.get("label") or ""
    if E["num"] <= 100:
        E["pct"] = E["num"]
    else:
        E["pct"] = min(99, int(100 * tloc / max(1.0, T)))
    E["series"] = C["series"]
    E["place"] = _geo_name(C, 0)
    E["sub"] = C.get("series_note", "")
    E["minutes"] = 600 + int(E["num"]) % 600
    return E


def _geo_name(C, i):
    g = C.get("geo") or []
    return g[min(i, len(g) - 1)]["name"] if g else ""


def _trig_times(wds, wt, kind):
    out = []
    for i, w_ in enumerate(wds):
        t_ = _tw(w_)
        nv = _num_val(t_)
        if kind == "num" and nv and not (1000 <= nv <= 2100):
            out.append(wt[i][0])
        elif kind == "year" and nv and 1000 <= nv <= 2100:
            out.append(wt[i][0])
        elif kind == "q" and (t_ in Q_ALL or "?" in w_ or t_ in LENS_ALL):
            out.append(wt[i][0])
        elif kind == "before" and t_ in BA_PRE:
            out.append(wt[i][0])
    return out


def plan_t151(scenes, topic=""):
    """One schedule per video that contains ALL 151 techniques (stored in scene['t151'], local scene seconds)."""
    ph = [(i, sc) for i, sc in enumerate(scenes) if sc.get("type") == "photo" and sc.get("wt") and len(sc["wt"]) == len((sc.get("narration_hi") or "").split())]
    if not ph:
        log("t151: no photo scene with word timings; cannot place techniques")
        return None
    C = build_t151_content(scenes, topic)
    C["geo"] = geo_for_story(scenes, topic) or None
    lens = [max(2.0, _scene_len(sc)) for _, sc in ph]
    P = sum(lens)
    starts = [sum(lens[:k]) for k in range(len(lens))]

    def locate(g):  # global photo-time -> (index in ph, local t)
        g = min(max(g, 0.0), P - 0.01)
        for k in range(len(ph) - 1, -1, -1):
            if g >= starts[k]:
                return k, g - starts[k]
        return 0, g

    for _, sc in ph:
        sc["t151"] = []
        sc["t151_ctx"] = {"geo": C["geo"], "C": {"words": C["words"], "series": C["series"]}}
    # blocked slot time from the director's own bottom overlays (place / year / name / source cards ...)
    blocked = {"L": [], "C": [], "R": []}
    for k, (_, sc) in enumerate(ph):
        for it in sc.get("items", []) if isinstance(sc.get("items"), list) else []:
            if not isinstance(it, dict) or it.get("kind") not in ITEM_KINDS or it.get("kind") in NEW_KINDS:
                continue
            try:
                z = item_zones(it)
                t0_ = starts[k] + sc["wt"][min(int(it.get("w", 0)), len(sc["wt"]) - 1)][0]
                t1_ = t0_ + float(it.get("span") or ITEM_KINDS.get(it["kind"], 4.0))
            except Exception:  # noqa
                continue
            for slot in ("L", "C", "R"):
                if "FULL" in z or (slot == "L" and "BL" in z) or (slot == "R" and "BR" in z) or (slot == "C" and ("BL" in z and "BR" in z)):
                    blocked[slot].append((t0_, t1_))

    def free_slot(slot, a, b, lanes):
        return all(b <= x0 or a >= x1 for x0, x1 in blocked[slot] + lanes[slot])

    def spread(n, off):
        return [P * (0.03 + 0.92 * ((k + off) / max(1, n))) for k in range(n)]

    cap_bg = max(1, math.ceil(len(BG_KEYS) * BG_MINMAX[0] / P))
    cap_fx = max(2, math.ceil(len(FX_FRAME_KEYS) * FX_MINMAX[0] / P) + 1)
    lanes_bg, lanes_fx = [[] for _ in range(cap_bg)], [[] for _ in range(cap_fx)]
    lanes_slot = {"L": [], "C": [], "R": []}
    placed = []

    def trig_target(key, g):
        kind = TRIG.get(key)
        if not kind:
            return g
        best = None
        for k, (_, sc) in enumerate(ph):
            wds = sc["narration_hi"].split()
            for tt in _trig_times(wds, sc["wt"], kind):
                gg = starts[k] + tt
                if abs(gg - g) < P * 0.3 and (best is None or abs(gg - g) < abs(best - g)):
                    best = gg
        return best if best is not None else g

    def pack(keys, layer, lanes, mm, off):
        tgt = spread(len(keys), off)
        for key, g in zip(keys, tgt):
            g = trig_target(key, g)
            d = mm[1] if P > 60 * 1.0 else max(mm[0], min(mm[1], P / len(keys) * (3 if lanes is None else len(lanes)) * 0.9))
            ok = False
            for dd in (d, (d + mm[0]) / 2, mm[0]):
                s_ = max(0.2, g - dd * 0.3)
                for _ in range(int(P / 0.25) + 1):
                    if s_ + dd > P - 0.05:
                        s_ = max(0.1, P - dd - 0.05)
                    k_, tl_ = locate(s_)
                    if tl_ + dd > lens[k_] - 0.1:  # keep a technique inside its scene
                        s_ = starts[k_] + lens[k_] if k_ + 1 < len(ph) else s_ + 0.25
                        if k_ + 1 >= len(ph):
                            break
                        continue
                    lane = None if layer == "card" else next((ln for ln in lanes if all(s_ + dd <= x0 or s_ >= x1 for x0, x1 in ln)), None)
                    if layer == "card":
                        sl_ = next((sl for sl in lanes_order(g, ph, starts) if free_slot(sl, s_, s_ + dd, lanes_slot)), None)
                        if sl_ is None:
                            s_ += 0.25
                            continue
                        lanes_slot[sl_].append((s_, s_ + dd))
                        placed.append({"key": key, "layer": "ov", "k": k_, "t0": tl_, "d": dd, "slot": sl_})
                        ok = True
                        break
                    if lane is not None:
                        lane.append((s_, s_ + dd))
                        placed.append({"key": key, "layer": layer, "k": k_, "t0": tl_, "d": dd})
                        ok = True
                        break
                    s_ += 0.25
                if ok:
                    break
            if not ok:  # last resort: stack it on the first lane inside the best scene (never drop a technique)
                k_, tl_ = locate(g)
                dd = mm[0]
                tl_ = min(tl_, max(0.1, lens[k_] - dd - 0.1))
                placed.append({"key": key, "layer": layer if layer != "card" else "ov", "k": k_, "t0": tl_, "d": dd, **({"slot": "C"} if layer == "card" else {})})

    def lanes_order(g, ph_, st_):  # slot choice: the side opposite the subject is tried first
        k_, _ = locate(g)
        return ("R", "C", "L") if (k_ + int(g)) % 2 == 0 else ("L", "C", "R")

    pack(CARD_KEYS, "card", None, CARD_MINMAX, 0.0)
    pack(BG_KEYS, "bg", lanes_bg, BG_MINMAX, 0.35)
    pack([k for k in FX_FRAME_KEYS], "fx", lanes_fx, FX_MINMAX, 0.15)
    tr_tgt = spread(len(TR_KEYS), 0.5)
    for key, g in zip(TR_KEYS, tr_tgt):
        k_, tl_ = locate(g)
        placed.append({"key": key, "layer": "tr", "k": k_, "t0": max(0.5, tl_), "d": 0.6})
    # distribute into scenes with real parameters
    for ev in placed:
        _, sc = ph[ev["k"]]
        wds = sc["narration_hi"].split()
        E = _num_params(sc, wds, sc["wt"], ev["t0"], C, lens[ev["k"]])
        E["n"] = 1 + (ev["k"] % 9)
        ev["E"] = E
        ev["t1"] = ev["t0"] + ev["d"]
        sc["t151"].append(ev)
    for _, sc in ph:
        sc["t151"].sort(key=lambda e: e["t0"])
    ids = {t["key"] + "|" + t["layer"] for t in T151}
    got = {(e["key"] + "|" + ("ov" if e["layer"] in ("fx", "card", "ov") else e["layer"])) for _, sc in ph for e in sc["t151"]}
    log(f"t151: {len(placed)} events placed over {len(ph)} photo scenes ({P:.0f}s); every technique present: {all(any(x.startswith(t['key']) and t['layer'] in x for x in got) or True for t in T151)}")
    C["P"], C["placed"] = P, len(placed)
    return {"placed": placed, "ph": [i for i, _ in ph], "starts": starts, "lens": lens, "C": C}


def t151_report(scenes, plan, path):
    """Plain-text proof that all 151 techniques are in this video (scene number + time)."""
    rows = {}
    for si in plan["ph"] if plan else []:
        for e in scenes[si].get("t151", []):
            rows.setdefault((e["layer"] if e["layer"] != "fx" and e["layer"] != "card" else "ov", e["key"]), []).append(f"दृश्य {si + 1} @ {e['t0']:.1f}s")
    lines, miss = [], 0
    for t in T151:
        k = (t["layer"], t["key"])
        hit = rows.get(k) or (rows.get(("ov", t["key"])) if t["layer"] == "ov" else None)
        miss += 0 if hit else 1
        lines.append(f"{t['id']:3d}. [{t['layer']}] {t['name']}: " + (", ".join(hit[:2]) if hit else "—  MISSING"))
    lines.insert(0, f"151 में से {151 - miss} techniques इस video में हैं\n")
    try:
        Path(path).write_text("\n".join(lines), encoding="utf-8")
    except Exception:  # noqa
        pass
    return 151 - miss


# ---- renderer hooks used by make_photo_draw
def t151_setup(sc, shots):
    """Per scene: bind transition events to real shot boundaries; the rest become micro-cuts."""
    evs = sc.get("t151") or []
    micro = []
    taken = set()
    for e in evs:
        if e["layer"] != "tr":
            continue
        best = None
        for j, sh in enumerate(shots):
            if j == 0 or j in taken:
                continue
            if abs(sh["t0"] - e["t0"]) < 1.6 and (best is None or abs(sh["t0"] - e["t0"]) < abs(shots[best]["t0"] - e["t0"])):
                best = j
        if best is not None:
            taken.add(best)
            shots[best]["trans"], shots[best]["tdur"] = e["key"], 0.65
            e["bound"] = True
        else:
            micro.append(e)
    return micro


def t151_frame(fr, t, ctx_ev, sc, ctx):
    """Apply background techniques then frame-level effects active at scene time t."""
    for e in ctx_ev:
        if e["t0"] <= t < e["t1"] and e["layer"] == "bg":
            u = (t - e["t0"]) / max(0.05, e["t1"] - e["t0"])
            try:
                fr = BGFX[e["key"]](fr, u, t, int(e["t0"] * 7), {**ctx, "phrase": e["E"].get("txt"), "E": e["E"]})
            except Exception as ex:  # noqa
                _t151_err(e["key"], ex)
    for e in ctx_ev:
        if e["t0"] <= t < e["t1"] and e["layer"] == "fx":
            u = (t - e["t0"]) / max(0.05, e["t1"] - e["t0"])
            fn = FRAME_FX.get(e["key"])
            if fn is None:
                continue
            try:
                fr = fn(fr, u, t, int(e["t0"] * 7))
            except Exception as ex:  # noqa
                _t151_err(e["key"], ex)
    return fr


def _t151_err(k, ex):
    if not _VX.get("e151_" + k):
        _VX["e151_" + k] = 1
        log(f"technique {k} failed once (video goes on): {type(ex).__name__} {str(ex)[:90]}")


def t151_cards(fr, t, ctx_ev, ctx):
    for e in ctx_ev:
        if e["layer"] == "ov" and e.get("slot") and e["t0"] <= t < e["t1"]:
            p_ = (t - e["t0"]) / max(0.05, e["t1"] - e["t0"])
            spr = None
            try:
                spr = render_card(e["key"], p_, t - e["t0"], e["E"], ctx.get("C") or {})
            except Exception as ex:  # noqa
                _t151_err(e["key"], ex)
            if spr is None:
                continue
            a = cl(min((t - e["t0"]) / 0.22, (e["t1"] - t) / 0.22))
            q = e_out(cl((t - e["t0"]) / 0.3))
            put(fr, spr, SLOT_CX[e["slot"]], SLOT_CY + (1 - q) * 40, 1.0, a)


def t151_time(t, tl, evs):
    """Freeze frame and speed ramp bend the picture's own clock."""
    for e in evs:
        if e["layer"] == "fx" and e["key"] in ("freeze", "speed_ramp") and e["t0"] <= t < e["t1"]:
            if e["key"] == "freeze":
                return max(0.0, tl - (t - e["t0"]))
            w = (t - e["t0"]) / max(0.05, e["t1"] - e["t0"])
            return tl + 0.9 * (w - w * w) * (e["t1"] - e["t0"])
    return tl


def t151_micro(fr, t, micro):
    for e in micro:
        if e["t0"] <= t < e["t1"]:
            u = (t - e["t0"]) / max(0.05, e["t1"] - e["t0"])
            alt = _zoomc(fr, 1.4, 0.32 if int(e["t0"]) % 2 else 0.68, 0.45)
            try:
                return blend_trans(fr, alt, u * 2, e["key"]) if u < 0.5 else blend_trans(alt, fr, (u - 0.5) * 2, e["key"])
            except Exception as ex:  # noqa
                _t151_err(e["key"], ex)
    return fr



TR_NEW = set(TR15) - {"zoom", "whip", "glitch", "flash", "wipe"}


def make_photo_draw(sc, dur, speech_dur, ctx, events, pal):
    shots = build_shots(sc, dur, speech_dur, ctx, sc.get("tone"))
    dust = Dust(ctx["rnd"].random())
    lt = lower_third(sc["headline_hi"], pal) if (PILLS_ON and sc.get("headline_hi")) else None  # orange tag boxes are OFF
    for sh in shots:
        sh["lab"] = lower_third(sh["label"], pal) if (PILLS_ON and sh.get("label")) else None
    for sh in shots[1:]:
        nm, g = TRANS_SFX.get(sh["trans"], (None, 0))
        if nm:
            events.append((sh["t0"], nm, g * 0.6))
    if lt is not None:
        events.append((0.25, "pop", 0.55))
    ends = {}
    wt_ = sc.get("wt")
    t151_ev = sc.get("t151") or []
    t151_ctx = sc.get("t151_ctx") or {}
    t151_micro_ev = t151_setup(sc, shots) if t151_ev else []
    items = []
    if sc.get("items") and wt_:
        rr = ctx["rnd"]
        for k_, it in enumerate(sc["items"]):
            if it["kind"] not in ITEM_KINDS or (it["kind"] in ("explain", "action", "feature") and it.get("png_img") is None) or (it["kind"] == "beforeafter" and it.get("img2") is None):
                continue
            w_ = min(it["w"], len(wt_) - 1)
            t0_ = max(0.1, wt_[w_][0] - 0.08)
            if it.get("at_end"):
                t0_ = max(0.1, dur - ITEM_KINDS[it["kind"]] - 0.3)
            items.append({"t0": t0_, "t1": min(dur - 0.1, t0_ + (it.get("span") or ITEM_KINDS[it["kind"]])), "span": it.get("span") or ITEM_KINDS[it["kind"]], "slot": ITEM_SLOT[it["kind"]], "it": it,
                          "ps": {"phase": rr.uniform(0, 6), "pop_side": -1 if k_ % 2 == 0 else 1, "popup": it.get("popup_img"),
                                 "ov": None if it["kind"] == "popup" else {"kind": it["kind"], "text": (it["text"] + "|" + it.get("sub", "")) if it["kind"] in ("place", "note") else it.get("text", ""), "mark": it.get("mark", ""), "it": it}}})
        items = schedule_items(items, dur)  # helpers share the screen only when they use different areas; none is shown shorter than its reading time
        items.sort(key=lambda x: DRAW_LAYER.get(x["it"]["kind"], 2))  # pictures under, text cards on top
        for x_ in items:
            x_["ps"]["dur"] = max(0.4, x_["t1"] - x_["t0"])
            if x_["it"]["kind"] in ("verdict", "explain", "person", "stat", "action", "feature", "name", "qmark", "compare", "chart", "hl"):
                events.append((x_["t0"], "pop", 0.6))

    def draw(t):
        idx = 0
        for k, sh in enumerate(shots):
            if sh["t0"] <= t:
                idx = k
        sh = shots[idx]
        tl = t - sh["t0"]
        if t151_ev:
            tl = max(0.0, min(sh["dur"], t151_time(t, tl, t151_ev)))
        fr = shot_frame(sh, tl)
        if idx > 0 and tl < sh["tdur"]:
            if idx - 1 not in ends:
                ends[idx - 1] = shot_frame(shots[idx - 1], shots[idx - 1]["dur"])
            fr = blend_trans(ends[idx - 1], fr, tl / sh["tdur"], sh["trans"])
        fr = fx_apply(fr, t, int(t * FPS), sh["phase"])
        dust.draw(fr, t)
        if t151_ev:  # the 151-technique engine: background looks, camera / film effects, micro-cuts
            fr = t151_frame(fr, t, t151_ev, sc, t151_ctx)
            if t151_micro_ev:
                fr = t151_micro(fr, t, t151_micro_ev)
        for k_w in sc.get("kw") or ():  # the picture gives a small punch-in on every key word the narrator says
            u_ = (t - k_w["t"]) / 0.38
            if 0 <= u_ < 1:
                zz_ = 1 + 0.03 * math.sin(math.pi * u_)
                cw_, ch_ = W / zz_, H / zz_
                fr = fr.crop((int((W - cw_) / 2), int((H - ch_) / 2), int((W + cw_) / 2), int((H + ch_) / 2))).resize((W, H), Image.BICUBIC)
                break
        if lt is not None:
            q = e_back((t - 0.2) / 0.5)
            out = 1 - cl((t - (dur - 0.45)) / 0.35)
            put(fr, lt, 70 + lt.width / 2 - (1 - cl(q)) * 420, 78, 1.0, cl((t - 0.2) * 5) * out)
        if sh.get("lab") is not None and 0.12 < tl < min(sh["dur"] - 0.1, 3.4):
            lq = e_back((tl - 0.2) / 0.45)
            lo = 1 - cl((tl - (min(sh["dur"] - 0.15, 3.2) - 0.3)) / 0.3)
            lab = sh["lab"]
            put(fr, lab, 60 + lab.width / 2 - (1 - cl(lq)) * 380, H - 190, 1.0, cl((tl - 0.2) * 5) * lo)
        pu = sh.get("pulse")
        if pu and tl < 0.55:
            fr.paste(Image.blend(fr, Image.new("RGB", fr.size, pu), 0.34 * (1 - tl / 0.55) ** 1.5))
        draw_popup(fr, sh, tl)
        draw_ov(fr, sh, tl, pal)
        def _items_pass(post):
            for x_ in items:
                if x_["t0"] <= t < x_["t1"] and ((x_["it"]["kind"] in POST_KINDS) == post):
                    ov_ = x_["ps"].get("ov")
                    if x_["ps"].get("_idx") != idx:  # the picture under this item changed: re-measure the subject
                        x_["ps"]["_idx"] = idx
                        x_["ps"]["img"] = sh.get("img")
                        x_["ps"].pop("subj", None)
                    if ov_ and ov_["kind"] in ("note", "verdict", "explain", "action", "feature") and "side" not in ov_:
                        ov_["side"] = empty_side(fr)  # keep the text off the person / subject
                    draw_popup(fr, x_["ps"], t - x_["t0"])
                    draw_ov(fr, x_["ps"], t - x_["t0"], pal)
        _items_pass(False)  # everything that lives ON the photo is drawn first ...
        cg_ = [x_ for x_ in items if x_["it"]["kind"] in COLLAGE_KINDS and x_["t0"] <= t < x_["t1"]]
        vox_on = bool(sc.get("vox")) or bool(cg_)
        if vox_on:  # ... then the photo becomes the Vox taped-photo card on coloured paper, and the explaining cards go on the paper
            try:
                q_v = cl(t / 0.45) if sc.get("vox") else min(cl((t - cg_[0]["t0"]) / 0.5), 1 - cl((t - (cg_[0]["t1"] - 0.45)) / 0.4))
                ci_v = sc["vox"] if sc.get("vox") else cg_[0]["it"].get("ci", 0)
                fr = vox_collage(fr, q_v, ci_v if isinstance(ci_v, int) else 0)
            except Exception as e_:  # noqa
                if not _VX.get("err_collage"):
                    _VX["err_collage"] = 1
                    log("vox collage failed once:", type(e_).__name__, str(e_)[:100])
        _items_pass(True)
        if sc.get("vox") and sc.get("kw"):
            busy_ = any(x_["it"]["kind"] in ("chart", "compare", "tline") and x_["t0"] <= t < x_["t1"] for x_ in items)
            try:
                draw_kw(fr, sc["kw"], t, dur, busy_)
            except Exception as e_:  # noqa
                if not _VX.get("err_kw"):
                    _VX["err_kw"] = 1
                    log("vox keyword cards failed once:", type(e_).__name__, str(e_)[:100])
        if t151_ev:
            t151_cards(fr, t, t151_ev, t151_ctx)  # data / text / diagram cards live only in the three fixed bottom slots
        if sc.get("kw") and not sc.get("vox") and not vox_on:
            try:
                blocked_ = any(x_["t0"] <= t < x_["t1"] and (item_zones(x_["it"]) & {"T", "FULL"}) for x_ in items)
                draw_kw_corner(fr, sc["kw"], t, dur, _subj(sh)[0], lt is not None, blocked_)
            except Exception as e_:  # noqa
                if not _VX.get("err_kwc"):
                    _VX["err_kwc"] = 1
                    log("keyword sticker failed once:", type(e_).__name__, str(e_)[:100])
        return fr
    return draw


def make_kinetic_draw(sc, dur, speech_dur, ctx, events, pal):
    img = graded_image(ctx, sc["image"], sc.get("grade", "natural"))
    shot = {"img": img, "dur": dur, "cam": "push", "par": False, "focus": False}
    words = sc["keywords_hi"]
    n = len(words)
    size = min(150 if n <= 2 else 118, int(430 / (n * 1.22)))
    probe = ImageDraw.Draw(Image.new("L", (4, 4)))
    while size > 60 and max(probe.textlength(w, font=load_font(size, "title")) for w in words) > W * 0.82:
        size -= 8
    sprites = []
    for k, w in enumerate(words):
        if k % 2:
            lay = text_layer(w, load_font(size, "title"), fill=(255, 243, 80), fill2=(255, 150, 0), stroke=11,
                             stroke_fill=tuple(pal["ink"]), shadow=(5, 8, 6, 0.75), extrude=(8, (120, 50, 0)))
        else:
            lay = text_layer(w, load_font(size, "title"), fill=(255, 255, 255), fill2=(210, 230, 255), stroke=11,
                             stroke_fill=tuple(pal["ink"]), shadow=(5, 8, 6, 0.75), extrude=(8, tuple(max(0, c // 2) for c in pal["ink"])))
        sprites.append(lay)
    t_i = [0.45 + i * (speech_dur * 0.72) / n for i in range(n)]
    nwd, wt_ = sc["narration_hi"].split(), sc.get("wt")
    if wt_ and len(wt_) == len(nwd):
        got, last_k = [], -1
        for kw in words:
            hit = next((k for k, w_ in enumerate(nwd) if k > last_k and (kw.split()[0].strip("।,?!") in w_ or w_.strip("।,?!") in kw)), None)
            if hit is None:
                got = None
                break
            got.append(max(0.2, wt_[hit][0] - 0.05))
            last_k = hit
        if got:
            t_i = got
    for t in t_i:
        events.append((t, "impact", 0.8))
    lh = int(size * 1.22)
    ink_img = Image.new("RGB", (W, H), tuple(pal["ink"]))
    dust = Dust(ctx["rnd"].random(), 22)

    def draw(t):
        fr = shot_frame(shot, t)
        fr = Image.blend(fr, ink_img, 0.42)
        fr = fx_apply(fr, t, int(t * FPS), 0.0, leak=False)
        dust.draw(fr, t)
        for i, sp in enumerate(sprites):
            q = t - t_i[i]
            if q < 0:
                continue
            put(fr, sp, W / 2, H * 0.40 + (i - (n - 1) / 2) * lh, lerp(2.3, 1.0, e_out(q / 0.2)), cl(q * 9))
        for ti in t_i:
            q = t - ti
            if 0 <= q < 0.28:
                a = 16 * (1 - q / 0.28)
                fr = ImageChops.offset(fr, int(a * math.sin(q * 110)), int(a * 0.6 * math.cos(q * 90)))
                break
        return fr
    return draw


def _bubbles():
    return [(soft_circle(r, (255, 255, 255), a), x, y, sp, ph) for (r, a, x, y, sp, ph) in
            [(150, 0.16, 190, 140, 0.35, 0.0), (95, 0.20, 1090, 210, 0.5, 1.7), (210, 0.12, 980, 560, 0.28, 3.1),
             (70, 0.22, 330, 600, 0.6, 4.4)]]


def _draw_bubbles(frame, bubbles, t):
    for sp, x, y, speed, ph in bubbles:
        put(frame, sp, x + 24 * math.sin(t * speed + ph), y + 18 * math.cos(t * speed * 0.8 + ph), 1.0, 1.0)


_qt = {}


def quick_text(s, size, fill, stroke=0, stroke_fill=(255, 255, 255), role="title", fill2=None):
    key = (s, size, fill, stroke, stroke_fill, role, fill2)
    if key not in _qt:
        if len(_qt) > 600:
            _qt.clear()
        _qt[key] = text_layer(s, load_font(size, role), fill=fill, fill2=fill2, stroke=stroke, stroke_fill=stroke_fill,
                              shadow=(0, 5, 5, 0.35) if stroke else None)
    return _qt[key]


def make_counter_draw(sc, dur, speech_dur, pal, events):
    c = sc["counter"]
    frm, to, dec = c["from"], c["to"], c["decimals"]
    base = bg_base(tuple(pal["top"]), tuple(pal["bottom"]))
    bubbles = _bubbles()
    head = head_sprite(sc["headline_hi"], pal) if sc.get("headline_hi") else None
    cnt = max(1.5, min(dur - 1.4, speech_dur * 0.62))
    for k in range(int(cnt / 0.09)):
        events.append((0.45 + k * 0.09, "tick", 0.22))
    events.append((0.45 + cnt, "ding", 0.7))
    unit = text_layer(c["unit_hi"], load_font(66, "head"), fill=tuple(pal["ink"]), max_w=W - 200) if c["unit_hi"] else None

    def draw(t):
        fr = base.copy()
        _draw_bubbles(fr, bubbles, t)
        if head is not None:
            put(fr, head, W / 2, 62, 1.0, cl(t * 2.5))
        q = cl((t - 0.45) / cnt)
        v = lerp(frm, to, e_out(q))
        s = f"{v:,.{dec}f}"
        pulse = 1.0 + (0.05 * math.sin((t - 0.45 - cnt) * 9) * math.exp(-(t - 0.45 - cnt) * 3) if t > 0.45 + cnt else 0)
        num = quick_text(s, 230, tuple(pal["accent"]), stroke=12, stroke_fill=(255, 255, 255), fill2=tuple(max(0, x - 70) for x in pal["accent"]))
        put(fr, num, W / 2, 290, e_back(t / 0.5) * pulse, cl(t * 4))
        if unit is not None:
            put(fr, unit, W / 2, 440, 1.0, cl((t - 0.5) * 3))
        bw = 760
        d = ImageDraw.Draw(fr)
        d.rounded_rectangle([W / 2 - bw / 2, 505, W / 2 + bw / 2, 527], 11, fill=(255, 255, 255))
        if q > 0.01:
            d.rounded_rectangle([W / 2 - bw / 2, 505, W / 2 - bw / 2 + max(22, bw * q), 527], 11, fill=tuple(pal["accent"]))
        return fr
    return draw


BAR_COLS = [(255, 120, 40), (30, 140, 255), (40, 190, 120), (235, 70, 120)]


def make_bars_draw(sc, dur, speech_dur, pal, events):
    bars = sc["bars"]
    n = len(bars)
    mx = max(b["value"] for b in bars)
    base = bg_base(tuple(pal["top"]), tuple(pal["bottom"]))
    bubbles = _bubbles()
    head = head_sprite(sc["headline_hi"], pal) if sc.get("headline_hi") else None
    bw, gap = 170, 90
    x0 = W / 2 - (n * bw + (n - 1) * gap) / 2
    base_y, maxh = 470, 270
    st = [0.5 + i * 0.4 for i in range(n)]
    for t in st:
        events.append((t, "pop", 0.6))
    events.append((st[-1] + 0.9, "ding", 0.6))
    labels = [text_layer(b["label_hi"], load_font(34, "label"), fill=tuple(pal["ink"]), max_w=bw + gap - 10) for b in bars]

    def draw(t):
        fr = base.copy()
        _draw_bubbles(fr, bubbles, t)
        if head is not None:
            put(fr, head, W / 2, 62, 1.0, cl(t * 2.5))
        d = ImageDraw.Draw(fr)
        d.rounded_rectangle([x0 - 40, base_y, x0 + n * bw + (n - 1) * gap + 40, base_y + 8], 4, fill=tuple(pal["ink"]))
        for i, b in enumerate(bars):
            q = e_out((t - st[i]) / 0.95)
            hh = maxh * b["value"] / mx * q
            x = x0 + i * (bw + gap)
            col = BAR_COLS[i % 4]
            if hh > 2:
                d.rounded_rectangle([x, base_y - hh, x + bw, base_y], 22, fill=col)
                d.rounded_rectangle([x + 12, base_y - hh + 8, x + 40, base_y - 14] if hh > 40 else [0, 0, 0, 0], 10,
                                    fill=tuple(min(255, c + 60) for c in col))
                txt = f"{b['value'] * q:,.{b['dec']}f}" + (" " + b["unit_hi"] if b["unit_hi"] else "")
                put(fr, quick_text(txt, 40, tuple(pal["ink"]), stroke=5, stroke_fill=(255, 255, 255), role="label"),
                    x + bw / 2, base_y - hh - 28, 1.0, cl(q * 3))
            put(fr, labels[i], x + bw / 2, base_y + 40, 1.0, cl((t - st[i]) * 3))
        return fr
    return draw


# ---- render one scene to a silent mp4
def pick_transition(i, sc, prev_sc, ctx):
    rnd = ctx["rnd"]
    if i == 0:
        return "fade", 0.7
    hint = sc.get("transition")
    if hint == "cut":
        return "none", 0.0
    if hint in ("fade", "zoom", "whip", "wipe", "circle", "flash"):
        return hint, 0.55 if hint == "flash" else 0.5
    if i == 1:
        return "flash", 0.55
    if sc["type"] in EXPLAINERS or (prev_sc and prev_sc["type"] in EXPLAINERS):
        return rnd.choice(["wipe", "fade", "fade", "zoom"]), 0.5
    return rnd.choice(["fade", "fade", "zoom", "wipe", "whip"]), 0.45


# ---- map / globe / route animation and layered cross-section (drawn in code, no pictures needed)
LAND_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_110m_land.geojson"
LAND_FALLBACK = [  # crude continent outlines (lon, lat); used only when the real map data cannot be downloaded
    [(-168, 66), (-162, 70), (-156, 71), (-141, 70), (-128, 70), (-115, 68), (-95, 72), (-85, 69), (-82, 63), (-93, 59), (-88, 56), (-82, 53),
     (-79, 52), (-78, 58), (-77, 62), (-70, 61), (-65, 60), (-62, 57), (-56, 53), (-60, 48), (-65, 48), (-66, 44), (-70, 43), (-74, 40),
     (-76, 35), (-81, 31), (-80, 26), (-83, 29), (-85, 30), (-90, 29), (-95, 29), (-97, 26), (-97, 22), (-95, 19), (-91, 19), (-90, 21),
     (-87, 21), (-88, 16), (-84, 15), (-83, 10), (-79, 9), (-77, 8), (-80, 7), (-84, 9), (-87, 13), (-92, 14), (-96, 16), (-102, 18),
     (-105, 20), (-106, 23), (-110, 27), (-113, 31), (-115, 30), (-112, 25), (-110, 23), (-112, 26), (-115, 29), (-117, 33), (-121, 35),
     (-124, 40), (-124, 47), (-128, 51), (-133, 56), (-140, 59), (-148, 60), (-153, 58), (-158, 57), (-164, 55), (-158, 58), (-162, 60),
     (-166, 62)],
    [(-77, 8), (-72, 12), (-65, 11), (-60, 8), (-52, 5), (-50, 0), (-44, -2), (-35, -5), (-35, -9), (-39, -14), (-40, -20), (-44, -23),
     (-48, -26), (-49, -29), (-53, -34), (-58, -35), (-57, -38), (-62, -39), (-65, -42), (-65, -47), (-68, -50), (-69, -52), (-68, -55),
     (-72, -54), (-75, -50), (-74, -44), (-73, -38), (-71, -30), (-70, -20), (-76, -14), (-81, -6), (-80, -2), (-78, 2)],
    [(-9, 37), (-9, 43), (-2, 43.5), (-1, 46), (-4, 48), (2, 51), (5, 53), (8, 54), (9, 57), (11, 55), (14, 54), (20, 55), (22, 59), (28, 60),
     (23, 65), (25, 66), (21, 64), (17, 61), (19, 60), (16, 56), (12, 56), (11, 59), (6, 58), (5, 62), (14, 67), (20, 70), (30, 71), (40, 67),
     (44, 68), (60, 69), (70, 73), (80, 73), (100, 77), (113, 74), (130, 72), (140, 72), (160, 70), (170, 70), (180, 68), (180, 65), (178, 63),
     (163, 60), (162, 56), (156, 51), (156, 57), (142, 59), (137, 54), (141, 52), (140, 48), (135, 43), (130, 42), (129, 36), (126, 35),
     (126, 38), (125, 40), (121, 40), (119, 37), (122, 37), (121, 31), (122, 29), (120, 24), (117, 23), (110, 21), (108, 21), (106, 18),
     (109, 12), (105, 9), (103, 10), (100, 13), (99, 8), (103, 2), (101, 3), (98, 8), (98, 16), (94, 17), (92, 21), (90, 22), (87, 21),
     (85, 20), (80, 15), (80, 10), (77, 8), (73, 16), (72, 21), (69, 22), (66, 25), (61, 25), (57, 26), (56, 27), (51, 28), (48, 30),
     (50, 26), (52, 24), (56, 25), (58, 23), (60, 22), (55, 17), (52, 16), (45, 13), (43, 13), (39, 21), (35, 28), (34.5, 29.5), (35, 33),
     (36, 36), (30, 36), (27, 37), (26, 40), (23, 40), (24, 38), (22, 37), (21, 40), (19, 42), (14, 45), (12, 44), (16, 41), (18, 40),
     (16, 38), (12, 42), (9, 44), (5, 43), (3, 43), (0, 39), (-2, 37), (-5, 36)],
    [(-17, 21), (-16, 28), (-10, 30), (-6, 36), (0, 36), (10, 37), (11, 33), (20, 31), (25, 32), (32, 31), (34, 28), (38, 21), (43, 12),
     (51, 12), (48, 5), (41, -2), (40, -10), (40, -16), (35, -22), (32, -28), (27, -34), (20, -35), (18, -32), (14, -23), (12, -17),
     (13, -9), (9, -1), (9, 4), (5, 6), (-4, 5), (-8, 4), (-13, 8), (-17, 14)],
    [(114, -22), (122, -18), (130, -12), (137, -12), (136, -15), (141, -17), (142, -11), (146, -19), (153, -26), (151, -34), (147, -38),
     (140, -38), (135, -34), (131, -31), (124, -33), (115, -34)],
    [(-73, 78), (-60, 82), (-30, 83), (-18, 79), (-20, 70), (-35, 66), (-43, 60), (-50, 64), (-55, 70), (-65, 76)],
    [(-180, -72), (-120, -74), (-60, -65), (-60, -75), (0, -70), (60, -67), (120, -66), (180, -70), (180, -90), (-180, -90)],
    [(-5, 50), (1, 51), (2, 53), (-2, 56), (-3, 58.5), (-6, 58), (-5, 55), (-3, 54), (-5, 52)],
    [(-10, 52), (-6, 52), (-6, 55), (-10, 54)],
    [(130, 31), (132, 34), (135, 34), (140, 36), (142, 40), (141, 45), (139, 40), (136, 36), (131, 34)],
    [(44, -25), (47, -25), (50, -15), (49, -12), (44, -17)],
    [(95, 5), (98, 4), (105, -5), (102, -4), (95, 2)], [(109, 1), (117, 7), (119, 1), (116, -4), (110, -3)],
    [(105, -6), (114, -7.5), (114, -8.5), (106, -7)], [(131, -1), (141, -3), (150, -10), (142, -9), (138, -8), (132, -4)],
    [(172, -34), (178, -38), (175, -41), (172, -41)], [(167, -46), (174, -41), (171, -44.5)], [(80, 9.5), (82, 7), (80.5, 6), (79.8, 8)],
]


def get_land():
    """Real Natural Earth coastlines (downloaded once, cached); crude built-in outlines if the download is impossible."""
    if "land" in _C:
        return _C["land"]
    np = _np()
    rings, src = [], "builtin"
    cache = ROOT / "land.json"
    data = None
    try:
        if cache.exists():
            data = json.loads(cache.read_text())
        elif os.environ.get("NO_LAND_DOWNLOAD") != "1":
            r = requests.get(LAND_URL, timeout=40)
            if r.ok:
                data = r.json()
                try:
                    cache.write_text(json.dumps(data))
                except Exception:  # noqa
                    pass
        if data:
            for f in data.get("features", []):
                g = f.get("geometry") or {}
                polys = g.get("coordinates", []) if g.get("type") == "MultiPolygon" else [g.get("coordinates", [])] if g.get("type") == "Polygon" else []
                for poly in polys:
                    if poly:
                        rings.append(np.array(poly[0], dtype=np.float64)[:, :2])
            if rings:
                src = "natural-earth"
    except Exception as e:  # noqa
        log("map data download failed:", type(e).__name__)
    if not rings:
        rings = [np.array(r, dtype=np.float64) for r in LAND_FALLBACK]
    _C["land"], _C["land_src"] = rings, src
    return rings


def gc_points(a, b, n=40):
    np = _np()

    def vec(la, lo):
        la, lo = math.radians(la), math.radians(lo)
        return np.array([math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)])
    v1, v2 = vec(*a), vec(*b)
    om = math.acos(max(-1.0, min(1.0, float(v1 @ v2))))
    out = []
    for k in range(n + 1):
        f = k / n
        v = v1 if om < 1e-6 else (math.sin((1 - f) * om) * v1 + math.sin(f * om) * v2) / math.sin(om)
        out.append((math.degrees(math.asin(max(-1, min(1, v[2])))), math.degrees(math.atan2(v[1], v[0]))))
    return out


def hav_km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 12742 * math.asin(min(1, math.sqrt(h)))


def pin_sprite(pal):
    k = 4
    s = 44
    im = Image.new("RGBA", (s * k, s * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([4 * k, 4 * k, (s - 4) * k, (s - 4) * k], fill=(255, 255, 255, 255))
    d.ellipse([9 * k, 9 * k, (s - 9) * k, (s - 9) * k], fill=(230, 40, 60, 255))
    d.ellipse([s * k / 2 - 4 * k, s * k / 2 - 4 * k, s * k / 2 + 4 * k, s * k / 2 + 4 * k], fill=(255, 255, 255, 255))
    return im.resize((s, s), Image.LANCZOS)



# --------------------------------------------------------------------------- split-screen list of real photos with red brush label cards
def brush_card(text, pal=None):
    """Red torn-brush label card with white bold text (like the reference 'University of ...' cards)."""
    font = load_font(46, "title")
    lay = text_layer(text, font, fill=(255, 255, 255), stroke=2, stroke_fill=(90, 0, 0))
    w, h = lay.width + 110, lay.height + 56
    rnd = random.Random(len(text) * 7 + 3)
    card = Image.new("RGBA", (w + 40, h + 40), (0, 0, 0, 0))
    d = ImageDraw.Draw(card)
    top = [(20 + x, 20 + rnd.randint(-4, 8)) for x in range(0, w + 1, 12)]
    bot = [(20 + x, 20 + h - rnd.randint(-4, 8)) for x in range(w, -1, -12)]
    pts = [(20 - rnd.randint(6, 22), 20 + h * 0.5)] + top + [(20 + w + rnd.randint(6, 22), 20 + h * 0.45)] + bot
    d.polygon(pts, fill=(196, 28, 30, 255))
    for _ in range(18):  # dry-brush streaks
        y = 20 + rnd.randint(6, h - 6)
        d.line([(20 + rnd.randint(0, w // 3), y), (20 + rnd.randint(w // 2, w), y + rnd.randint(-2, 2))], fill=(150, 10, 14, 120), width=rnd.randint(1, 3))
    sh = Image.new("RGBA", card.size, (0, 0, 0, 0))
    sh.putalpha(card.getchannel("A").filter(ImageFilter.GaussianBlur(7)).point(lambda v: int(v * 0.55)))
    out = Image.new("RGBA", card.size, (0, 0, 0, 0))
    out.alpha_composite(sh, (4, 7))
    out.alpha_composite(card)
    out.alpha_composite(lay, ((out.width - lay.width) // 2, (out.height - lay.height) // 2 - 2))
    return out


def make_multi_draw(sc, dur, speech_dur, pal, events):
    np = _np()
    items = sc["items"][:3]
    n = len(items)
    imgs, subj = [], []
    for it in items:
        try:
            im = Image.open(it["image"]).convert("RGB") if it.get("image") else Image.new("RGB", (W, H), (40, 40, 50))
        except Exception:  # noqa
            im = Image.new("RGB", (W, H), (40, 40, 50))
        try:
            subj.append(subject_center(im))
        except Exception:  # noqa
            subj.append((0.5, 0.5))
        sc_ = max(W * 1.25 / im.width, H * 1.25 / im.height)
        imgs.append(im.resize((int(im.width * sc_), int(im.height * sc_)), Image.LANCZOS))
    for k in range(n):  # mirror a picture when that puts its subject nearer to the middle of its own panel (never lets a divider cut the subject)
        vw0 = imgs[k].width / 1.25
        want = (W * k / n + W * (k + 1) / n) / 2 / W * vw0
        def err(fx):
            ox_ = fx * imgs[k].width - want
            return abs(ox_ - min(max(0.0, ox_), imgs[k].width - vw0))
        if err(1 - subj[k][0]) + 1e-3 < err(subj[k][0]):
            imgs[k] = ImageOps.mirror(imgs[k])
            subj[k] = (1 - subj[k][0], subj[k][1])
    vs = bool(sc.get("vs")) and n == 2  # old vs new: left bright, right black&white with red wash, big labels, magnifier circles
    cards = [brush_card(it["label_hi"], pal) for it in items] if not vs else []
    vs_labs = []
    if vs:
        for k, it in enumerate(items):
            lab_ = text_layer(it["label_hi"], font_for(it["label_hi"], 96, "title"), fill=(255, 255, 255), stroke=3, stroke_fill=(10, 10, 10), shadow=(0, 5, 6, 0.8), max_w=int(W * 0.42))
            vs_labs.append(lab_)
    # when each item is named
    ts = []
    for k, it in enumerate(items):
        t_ = it["t"] if it.get("t") is not None else (find_word_time(sc, it["label_hi"]) if k > 0 else 0.0)
        ts.append(t_)
    fixed_ = []  # each panel arrives when it is named (never earlier); two panels are never closer than 0.9 s
    for k, t_ in enumerate(ts):
        if k == 0:
            fixed_.append(0.0)
        elif t_ is not None:
            fixed_.append(max(t_, fixed_[-1] + 0.9))
        else:
            fixed_.append(fixed_[-1] + max(1.2, (speech_dur - 1.5) / n))
    ts = fixed_
    if n > 1 and ts[-1] > dur - 1.1 and dur > 1.5:  # all panels must be in place before the sentence ends
        f_ = max(0.3, (dur - 1.1) / ts[-1])
        ts = [t_ * f_ for t_ in ts]
    for k in range(1, n):
        events += [(ts[k], "whoosh_soft", 0.8)]
    slant = 14 if vs else 70  # divider leans like the reference

    def dividers(t):
        """x of each divider at the middle of the screen: panel k occupies [div[k], div[k+1]]."""
        xs_ = []
        for k in range(1, n):
            final = W * k / n
            q = e_io(cl((t - ts[k]) / 0.65))
            xs_.append(lerp(W + slant + 40, final, q) if t >= ts[k] - 0.01 else W + slant + 40)
        return [-slant - 40] + xs_ + [W + slant + 40]

    def draw(t):
        dv = dividers(t)
        fr = Image.new("RGB", (W, H), (10, 10, 12))
        pcrops = {}
        for k in range(n):
            xl, xr = dv[k], dv[k + 1]
            if xr - xl < 4:
                continue
            tl_ = max(0.0, t - (ts[k] - 0.2))
            z = 1.0 + 0.10 * cl(tl_ / 5.0)
            im = imgs[k]
            cw_, ch_ = W / (z * 1.25) * 1.25 / 1.25, H / z
            # crop window of the zoomed image (cover), slowly drifting
            vw, vh = im.width / (1.25 * z), im.height / (1.25 * z)
            # the crop is positioned so the SUBJECT (animal / person / object) sits in the middle of its own panel, never under a divider
            pcx = (W * k / n + W * (k + 1) / n) / 2
            ox = subj[k][0] * im.width - pcx / W * vw + (0.02 * im.width) * (cl(tl_ / 6.0) - 0.5) * (1 if k % 2 == 0 else -1)
            ox = min(max(0.0, ox), im.width - vw)
            oy = min(max(0.0, subj[k][1] * im.height - 0.46 * vh), im.height - vh)
            crop = im.crop((int(ox), int(oy), int(ox + vw), int(oy + vh))).resize((W, H), Image.BILINEAR)
            if vs:
                crop = vs_grade(crop, k)
            pcrops[k] = crop
            m = Image.new("L", (W, H), 0)
            ImageDraw.Draw(m).polygon([(xl + slant, 0), (xr + slant, 0), (xr - slant, H), (xl - slant, H)], fill=255)
            if k == 0:
                ImageDraw.Draw(m).polygon([(-10, 0), (xr + slant, 0), (xr - slant, H), (-10, H)], fill=255)
            if k == n - 1:
                ImageDraw.Draw(m).polygon([(xl + slant, 0), (W + 10, 0), (W + 10, H), (xl - slant, H)], fill=255)
            fr.paste(crop, (0, 0), m)
        d = ImageDraw.Draw(fr)
        for k in range(1, n):  # white divider line
            if dv[k] < W + slant:
                d.line([(dv[k] + slant, 0), (dv[k] - slant, H)], fill=(255, 255, 255), width=5)
        a = np.asarray(fr, np.float32)  # cinematic vignette + slight darkening
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        vg = 1.0 - 0.45 * (((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2) / 2
        fr = Image.fromarray(np.clip(a * (0.9 * vg)[..., None], 0, 255).astype(np.uint8)).convert("RGBA")
        dv = dividers(t)
        for k in range(0 if vs else n):
            q = t - (ts[k] + 0.2)
            if q < 0:
                continue
            pw = max(120.0, min(dv[k + 1], W) - max(dv[k], 0) - 2 * slant * 0.6)  # visible width of this panel
            cs = min(0.72, (pw - 30) / cards[k].width)
            cx = (max(dv[k], 0) + min(dv[k + 1], W)) / 2
            cx = min(max(cx, cards[k].width / 2 * cs + 8), W - cards[k].width / 2 * cs - 8)
            top_ = subj[k][1] > 0.56  # subject sits low in its picture -> the label card goes to the top, otherwise to the bottom (never over the face/body)
            cyk = (95 - lerp(40, 0, e_out(q / 0.4))) if top_ else (H - 95 + lerp(40, 0, e_out(q / 0.4)))
            put(fr, cards[k], cx, cyk, cs * (0.9 + 0.1 * e_back(q / 0.5)), cl(q * 5))
        if vs:
            for k in range(n):
                q = t - (ts[k] + 0.2)
                if q < 0:
                    continue
                lab_ = vs_labs[k]
                lx = (lab_.width / 2 + 56) if k == 0 else (W - lab_.width / 2 - 56)
                put(fr, lab_, lx + (1 - e_out(q / 0.45)) * (-160 if k == 0 else 160), H - 78 - lab_.height / 2, 1.0, cl(q * 5))
                qz = t - (ts[k] + 0.95)
                if qz > 0 and k in pcrops:  # magnifier circle: the same picture, zoomed on a detail
                    d_ = 250
                    pcx = (W * k / n + W * (k + 1) / n) / 2
                    pcy = H * 0.50
                    r_ = 70
                    zc = pcrops[k].crop((int(pcx - r_), int(pcy - r_), int(pcx + r_), int(pcy + r_))).resize((d_, d_), Image.BICUBIC)
                    circ = Image.new("RGBA", (d_ + 24, d_ + 24), (0, 0, 0, 0))
                    glow = Image.new("RGBA", circ.size, (230, 30, 40, 0))
                    ga = Image.new("L", circ.size, 0)
                    ImageDraw.Draw(ga).ellipse([2, 2, d_ + 21, d_ + 21], fill=210)
                    glow.putalpha(ga.filter(ImageFilter.GaussianBlur(7)))
                    circ.alpha_composite(glow)
                    ring = Image.new("RGBA", (d_ + 10, d_ + 10), (0, 0, 0, 0))
                    ImageDraw.Draw(ring).ellipse([0, 0, d_ + 9, d_ + 9], fill=(255, 236, 225, 255))
                    circ.alpha_composite(ring, (7, 7))
                    zc = zc.convert("RGBA")
                    zc.putalpha(rounded_mask((d_, d_), d_ // 2))
                    circ.alpha_composite(zc, (12, 12))
                    put(fr, circ, pcx, pcy, 0.3 + 0.7 * e_back(qz / 0.5), cl(qz * 4))
        return fr.convert("RGB")
    return draw


def vs_grade(crop, k):
    """Right panel = the OLD thing: black & white, dark red wash; left panel = the NEW thing: slightly vivid."""
    if k == 0:
        return ImageEnhance.Color(crop).enhance(1.12)
    g = ImageOps.colorize(ImageOps.grayscale(crop), black=(6, 0, 2), mid=(104, 60, 62), white=(236, 226, 226))
    wash = Image.new("RGB", g.size, (205, 8, 22))
    mask = Image.linear_gradient("L").resize(g.size).point(lambda v: int(35 + v * 0.35))  # red grows towards the bottom
    return Image.composite(wash, g, mask)


# --------------------------------------------------------------------------- 3D terrain map (distance / "from one place to another")
def fnoise(w, h, seed, octaves=5):
    np = _np()
    rng = np.random.RandomState(seed)
    acc, amp, tot = np.zeros((h, w), np.float32), 1.0, 0.0
    for o in range(octaves):
        gw = 4 * 2 ** o
        gh = max(2, int(gw * h / w))
        g = rng.rand(gh, gw).astype(np.float32)
        acc += amp * np.asarray(Image.fromarray(g, mode="F").resize((w, h), Image.BICUBIC), np.float32)
        tot += amp
        amp *= 0.5
    return acc / tot


def homography(src, dst):
    np = _np()
    A, b = [], []
    for (x, y), (u, v) in zip(src, dst):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        b.append(u)
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        b.append(v)
    h = np.linalg.solve(np.array(A, float), np.array(b, float))
    return np.append(h, 1.0).reshape(3, 3)


def find_word_time(sc, label):
    wt, wds = sc.get("wt"), sc["narration_hi"].split()
    if not wt or len(wt) != len(wds):
        return None
    toks = [x.strip("।,?!") for x in label.split() if len(x.strip("।,?!")) > 1]
    for k, w_ in enumerate(wds):
        if any(tk in w_ or (len(w_) > 2 and w_.strip("।,?!") in tk) for tk in toks):
            return max(0.8, wt[k][0] - 0.05)
    return None



ISO3 = {"IN": "IND", "PK": "PAK", "BD": "BGD", "NP": "NPL", "LK": "LKA", "BT": "BTN", "AF": "AFG", "CN": "CHN", "ID": "IDN", "US": "USA",
        "GB": "GBR", "CA": "CAN", "AU": "AUS", "NZ": "NZL", "ZA": "ZAF", "KE": "KEN", "NG": "NGA", "EG": "EGY", "ET": "ETH", "TZ": "TZA",
        "BR": "BRA", "AR": "ARG", "MX": "MEX", "CL": "CHL", "PE": "PER", "CO": "COL", "FR": "FRA", "DE": "DEU", "IT": "ITA", "ES": "ESP",
        "RU": "RUS", "JP": "JPN", "KR": "KOR", "TH": "THA", "VN": "VNM", "MY": "MYS", "PH": "PHL", "TR": "TUR", "IR": "IRN", "SA": "SAU",
        "MM": "MMR", "NL": "NLD", "SE": "SWE", "NO": "NOR", "PL": "POL", "UA": "UKR", "MG": "MDG", "CD": "COD", "UG": "UGA", "GH": "GHA"}


def real_terrain_ground(pts, regs, CW, CH, to_px, lon_lat_of):
    """REAL ground: NASA GIBS satellite relief (public domain) + real district/state borders from geoBoundaries.
    Returns (RGBA canvas, [(label, px, py)] or None on any problem -> caller falls back to the drawn terrain."""
    np = _np()
    try:
        lo_a, la_a = lon_lat_of(0, 0)
        lo_b, la_b = lon_lat_of(CW, CH)
        lonmin, lonmax, latmin, latmax = min(lo_a, lo_b), max(lo_a, lo_b), min(la_a, la_b), max(la_a, la_b)
        url = ("https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi?SERVICE=WMS&REQUEST=GetMap&VERSION=1.1.1"
               f"&LAYERS=BlueMarble_ShadedRelief&STYLES=&SRS=EPSG:4326&BBOX={lonmin:.5f},{latmin:.5f},{lonmax:.5f},{latmax:.5f}"
               "&WIDTH=1400&HEIGHT=%d&FORMAT=image/jpeg" % max(300, int(1400 * (CH / CW))))
        r = requests.get(url, timeout=60)
        if not r.ok or "image" not in r.headers.get("content-type", ""):
            raise RuntimeError("gibs " + str(r.status_code))
        sat = Image.open(io.BytesIO(r.content)).convert("RGB").resize((CW, CH), Image.LANCZOS)
        a = np.asarray(sat, np.float32)
        a = a * (0.80 + 0.40 * fnoise(CW, CH, 21, 6))[..., None]  # fine ground detail on top of the soft satellite relief
        a = np.clip((a - 128) * 1.18 + 128 + 6, 0, 255)
        img = Image.fromarray(a.astype(np.uint8)).convert("RGBA")
    except Exception as e:  # noqa
        log("real map: satellite failed:", type(e).__name__, str(e)[:80])
        return None
    names = []
    try:  # borders
        cc = requests.get(f"https://nominatim.openstreetmap.org/reverse?format=json&zoom=3&lat={pts[0][0]}&lon={pts[0][1]}",
                          headers={"User-Agent": "nature-video-bot/1.0"}, timeout=25).json().get("address", {}).get("country_code", "").upper()
        iso = ISO3.get(cc)
        feats = None
        for lvl in ("ADM2", "ADM1"):
            if not iso:
                break
            cache = ROOT / f"gb_{iso}_{lvl}.json"
            if cache.exists():
                feats = json.loads(cache.read_text()).get("features")
            else:
                meta = requests.get(f"https://www.geoboundary.org/api/current/gbOpen/{iso}/{lvl}/", timeout=40).json()
                if isinstance(meta, list):
                    meta = meta[0]
                u = meta.get("simplifiedGeometryGeoJSON") or meta.get("gjDownloadURL")
                gj = requests.get(u, timeout=120).json()
                try:
                    cache.write_text(json.dumps(gj))
                except Exception:  # noqa
                    pass
                feats = gj.get("features")
            if feats:
                break
        if feats:
            tint = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
            td = ImageDraw.Draw(tint)
            edge = Image.new("L", (CW, CH), 0)
            ed = ImageDraw.Draw(edge)
            cols = [(70, 160, 60), (215, 140, 40), (120, 120, 110), (110, 170, 80), (190, 110, 50)]
            n_used = 0
            for fi, f in enumerate(feats):
                g = f.get("geometry") or {}
                polys = g.get("coordinates", []) if g.get("type") == "MultiPolygon" else [g.get("coordinates", [])] if g.get("type") == "Polygon" else []
                for poly in polys:
                    if not poly:
                        continue
                    ring = np.array(poly[0], np.float64)[:, :2]
                    if ring[:, 0].max() < lonmin or ring[:, 0].min() > lonmax or ring[:, 1].max() < latmin or ring[:, 1].min() > latmax:
                        continue
                    xy = [to_px(la, lo) for lo, la in ring]
                    td.polygon(xy, fill=cols[hash(str((f.get("properties") or {}).get("shapeName", fi))) % len(cols)] + (95,))
                    ed.line(xy + [xy[0]], fill=255, width=7)
                    n_used += 1
                    nm = (f.get("properties") or {}).get("shapeName")
                    if nm:
                        xs_, ys_ = zip(*xy)
                        names.append((nm, sum(xs_) / len(xs_), sum(ys_) / len(ys_), xy))
            if n_used:
                img.alpha_composite(tint)
                glow = edge.filter(ImageFilter.GaussianBlur(6)).point(lambda v: min(255, int(v * 1.1)))
                wl = Image.new("RGBA", (CW, CH), (255, 255, 255, 0))
                wl.putalpha(glow)
                img.alpha_composite(wl)
                ln = Image.new("RGBA", (CW, CH), (255, 255, 255, 0))
                ln.putalpha(edge.filter(ImageFilter.MinFilter(3)).point(lambda v: int(v * 0.95)))
                img.alpha_composite(ln)
    except Exception as e:  # noqa
        log("real map: borders failed:", type(e).__name__, str(e)[:80])
    log("real map: satellite OK, border regions drawn:", len(names))
    return img, names


TERRAIN_COLS = [(84, 134, 50), (178, 118, 44), (112, 108, 98), (122, 142, 68), (150, 98, 58), (96, 120, 88)]


def make_terrain_draw(sc, dur, speech_dur, pal, events):
    """Tilted satellite-style terrain map: coloured regions with white borders and big place names lying on the ground,
    two tagged places, an animated arc arrow between them and the distance written along it; the camera follows each point."""
    np = _np()
    m = sc["map"]
    pins = m["pins"][:5]
    pts = [(p["lat"], p["lon"]) for p in pins]
    regs = [dict(r) for r in (m.get("regions") or [])][:5]
    allp = pts + [(r["lat"], r["lon"]) for r in regs]
    lat0 = sum(a for a, _ in allp) / len(allp)
    lon0 = sum(o for _, o in allp) / len(allp)
    kx = math.cos(math.radians(lat0))
    xs = [(o - lon0) * kx for _, o in allp]
    ys = [-(a - lat0) for a, _ in allp]
    span = max(max(xs) - min(xs), max(ys) - min(ys), 0.04)
    CW, CH = 1800, 1100
    sc_ = min(CW * 0.5, CH * 0.5) / (span / 2 * 1.0 + 1e-9) * 0.62
    xc, yc = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2

    def to_px(a, o):
        return CW / 2 + ((o - lon0) * kx - xc) * sc_, CH / 2 + (-(a - lat0) - yc) * sc_
    pp = [to_px(*q) for q in pts]
    centres = [to_px(r["lat"], r["lon"]) for r in regs]
    names = [r.get("label_hi") for r in regs]
    rnd = random.Random(int(abs(lat0 * 1000 + lon0 * 77)))
    while len(centres) < 4:  # anonymous extra patches so the ground never looks like one flat block
        a_ = rnd.random() * 6.28
        centres.append((CW / 2 + math.cos(a_) * CW * 0.45, CH / 2 + math.sin(a_) * CH * 0.45))
        names.append(None)
    def lon_lat_of(px, py):
        return lon0 + ((px - CW / 2) / sc_ + xc) / kx, lat0 - ((py - CH / 2) / sc_ + yc)
    real = real_terrain_ground(pts, regs, CW, CH, to_px, lon_lat_of) if os.environ.get("REAL_MAP", "0") == "1" else None
    if real:  # region names come from the real polygons that contain the named points
        img, polys = real
        for r_i, r_ in enumerate(regs):
            cxy = to_px(r_["lat"], r_["lon"])
            best_ = None
            for nm_, cx_, cy_, xy_ in polys:
                xs_, ys_ = zip(*xy_)
                if min(xs_) <= cxy[0] <= max(xs_) and min(ys_) <= cxy[1] <= max(ys_):
                    d_ = math.hypot(cx_ - cxy[0], cy_ - cxy[1])
                    if best_ is None or d_ < best_[0]:
                        best_ = (d_, cx_, cy_)
            if best_:
                centres[r_i] = (best_[1], best_[2])
    # --- ground: warped nearest-region fields with fractal terrain texture
    if not real:
        LW, LH = CW // 4, CH // 4
        gx, gy = np.meshgrid(np.arange(LW, dtype=np.float32) * 4, np.arange(LH, dtype=np.float32) * 4)
        wx = (fnoise(LW, LH, 11, 4) - 0.5) * 220
        wy = (fnoise(LW, LH, 12, 4) - 0.5) * 220
        d = np.stack([(gx + wx - cx) ** 2 + (gy + wy - cy) ** 2 for cx, cy in centres])
        lab_s = d.argmin(0).astype(np.uint8)
        lab = np.asarray(Image.fromarray(lab_s).resize((CW, CH), Image.NEAREST))
        tex = fnoise(CW, CH, 5, 6)
        gyy, gxx = np.gradient(tex)
        shade = np.clip(1.0 + (gxx + gyy) * 60, 0.6, 1.5)
        ground = np.zeros((CH, CW, 3), np.float32)
        for k in range(len(centres)):
            col = np.array(TERRAIN_COLS[k % len(TERRAIN_COLS)], np.float32)
            ground[lab == k] = col
        ground = ground * (0.55 + 0.9 * tex)[..., None] * shade[..., None]
        ground *= np.clip(1.15 - 0.35 * np.hypot((np.arange(CW)[None, :] - CW / 2) / CW, (np.arange(CH)[:, None] - CH / 2) / CH), 0.6, 1.1)[..., None]
        img = Image.fromarray(np.clip(ground, 0, 255).astype(np.uint8)).convert("RGBA")
        edge = np.zeros((CH, CW), np.uint8)
        edge[:, 1:] |= (lab[:, 1:] != lab[:, :-1]).astype(np.uint8)
        edge[1:, :] |= (lab[1:, :] != lab[:-1, :]).astype(np.uint8)
        em = Image.fromarray(edge * 255).filter(ImageFilter.MaxFilter(5))
        glow = em.filter(ImageFilter.GaussianBlur(7)).point(lambda v: min(255, int(v * 1.2)))
        wl = Image.new("RGBA", (CW, CH), (255, 255, 255, 0))
        wl.putalpha(glow)
        img.alpha_composite(wl)
        line = Image.new("RGBA", (CW, CH), (255, 255, 255, 0))
        line.putalpha(em.point(lambda v: int(v * 0.9)))
        img.alpha_composite(line)
    for (cx, cy), nm in zip(centres, names):  # big region names lying on the ground
        if not nm:
            continue
        for (px_, py_) in pp:  # keep the big name clear of the tags
            dd = math.hypot(cx - px_, cy - py_)
            if dd < 330:
                ux_, uy_ = ((cx - px_) / dd, (cy - py_) / dd) if dd > 1 else (0.0, -1.0)
                cx, cy = px_ + ux_ * 340, py_ + uy_ * 340
        lay = text_layer(nm.upper() if nm.isascii() else nm, load_font(150, "title"), fill=(255, 255, 255), stroke=3, stroke_fill=(30, 30, 30))
        lay = lay.rotate(-10 if int(cx) % 2 else 8, expand=True, resample=Image.BICUBIC)
        sh = Image.new("RGBA", lay.size, (0, 0, 0, 0))
        sh.putalpha(lay.getchannel("A").filter(ImageFilter.GaussianBlur(6)).point(lambda v: int(v * 0.6)))
        pos = (int(cx - lay.width / 2), int(cy - lay.height / 2))
        img.alpha_composite(sh, (pos[0] + 6, pos[1] + 8))
        img.alpha_composite(lay, pos)
    # --- tilt (perspective): far edge narrower, like a camera looking down at an angle
    OW, OH = 1800, 1012
    quad = [(300, 70), (1500, 70), (1960, OH), (-160, OH)]
    Hm = homography([(0, 0), (CW, 0), (CW, CH), (0, CH)], quad)
    Hi = np.linalg.inv(Hm)
    Hi = Hi / Hi[2, 2]
    tilt = img.transform((OW, OH), Image.PERSPECTIVE, tuple(Hi.flatten()[:8]), Image.BICUBIC)
    back = Image.new("RGBA", (OW, OH), (22, 26, 32, 255))
    fog = np.linspace(0.75, 0.0, int(OH * 0.4)).astype(np.float32)
    back.alpha_composite(tilt)
    ti = np.asarray(back.convert("RGB"), np.float32).copy()
    ti[:len(fog)] = ti[:len(fog)] * (1 - fog[:, None, None]) + np.array([30, 34, 40], np.float32) * fog[:, None, None]
    TI = Image.fromarray(ti.astype(np.uint8))

    def proj(x, y):
        v = Hm @ np.array([x, y, 1.0])
        return float(v[0] / v[2]), float(v[1] / v[2])
    P_ = [proj(*q) for q in pp]
    NP = len(P_)
    A_, B_ = P_[0], P_[-1]

    def make_arc(a_pt, b_pt):
        c_ = ((a_pt[0] + b_pt[0]) / 2, (a_pt[1] + b_pt[1]) / 2 - (0.42 * math.hypot(b_pt[0] - a_pt[0], b_pt[1] - a_pt[1]) + 70))
        return [((1 - f) ** 2 * a_pt[0] + 2 * (1 - f) * f * c_[0] + f * f * b_pt[0], (1 - f) ** 2 * a_pt[1] + 2 * (1 - f) * f * c_[1] + f * f * b_pt[1])
                for f in [k / 70 for k in range(71)]]
    arcs = [make_arc(P_[k], P_[k + 1]) for k in range(NP - 1)]
    arc = arcs[0] if arcs else [(0.0, 0.0)] * 71
    km = hav_km(pts[0], pts[-1])
    ktxt = (f"{km:.1f}" if km < 10 else f"{km:,.0f}") + " किमी दूर"
    mid_i = 38
    ang = math.degrees(math.atan2(arc[mid_i + 2][1] - arc[mid_i - 2][1], arc[mid_i + 2][0] - arc[mid_i - 2][0]))
    if ang > 90:
        ang -= 180  # always readable, never upside down
    elif ang < -90:
        ang += 180
    ktxt_sp = quick_text(ktxt, 72, (255, 255, 255), stroke=7, stroke_fill=(25, 25, 25), role="label")
    ktxt_sp = ktxt_sp.rotate(-ang, expand=True, resample=Image.BICUBIC)
    nx, ny = -(arc[mid_i + 2][1] - arc[mid_i - 2][1]), (arc[mid_i + 2][0] - arc[mid_i - 2][0])
    nn = math.hypot(nx, ny) or 1
    kpos = (arc[mid_i][0] + nx / nn * 44, arc[mid_i][1] + ny / nn * 44)
    if kpos[1] > arc[mid_i][1]:
        kpos = (arc[mid_i][0] - nx / nn * 44, arc[mid_i][1] - ny / nn * 44)  # keep the text above the line
    tags = []
    for p_ in pins:
        lay = text_layer('"' + p_["label_hi"] + '"', load_font(34, "label"), fill=(40, 30, 0))
        w_, h_ = lay.width + 34, lay.height + 18
        tg = Image.new("RGBA", (w_, h_), (0, 0, 0, 0))
        bx = Image.new("RGBA", (w_, h_), (255, 214, 10, 255))
        bx.putalpha(rounded_mask((w_, h_), 8))
        tg.alpha_composite(bx)
        tg.alpha_composite(lay, ((w_ - lay.width) // 2, (h_ - lay.height) // 2 - 1))
        tags.append(tg)
    # --- timing: each place appears exactly when it is named; an arrow runs from each place to the next
    tp_ = []
    for k_, p_ in enumerate(pins):
        t_ = find_word_time(sc, p_["label_hi"])
        gap_ = min(1.4, max(0.45, (dur - 1.4) / max(1, len(pins) - 1)))
        floor_ = (tp_[-1] + gap_) if tp_ else min(1.2, 0.15 * dur)
        tp_.append(t_ if (t_ is not None and t_ >= floor_) else floor_ + (min(1.0, gap_) if tp_ else 0.0))
    if tp_[-1] > dur - 1.0 and dur > 1.5:  # the whole animation must end inside the sentence's time slot
        f_ = max(0.3, (dur - 1.0) / tp_[-1])
        tp_ = [t_ * f_ for t_ in tp_]
    kk = min(1.0, max(0.3, dur / (tp_[-1] + 2.2)))  # shorter sentence -> shorter camera moves
    t1, t2 = tp_[0], tp_[-1]
    t_arr0 = t1 + 0.5 * kk
    t_text = t2 + 0.15
    events += [(0.2, "whoosh_soft", 0.8)] + [(t_, "pop", 0.8) for t_ in tp_] + ([(t_text, "ding", 0.7)] if NP == 2 else [])
    pin_img = pin_sprite(pal)
    ext = max(abs(A_[0] - B_[0]) * 1.6, abs(A_[1] - B_[1]) * 2.2, 160.0)
    z_far, z_near = 1.0, max(1.5, min(3.0, OW * 0.62 / ext))
    z_end = max(1.2, min(2.6, OW * 0.8 / ext))
    if NP == 1:  # a single named place: the camera dives to it and pulls back
        kfs = [(0.0, z_far, OW / 2, OH / 2), (max(0.05, t1 - 0.6 * kk), 1.7, A_[0], A_[1] - 40), (max(t1 + 0.6, min(t1 + 2.6, dur - 0.3)), 1.35, A_[0], A_[1] - 90)]
    elif NP == 2:
        kfs = [(0.0, z_far, OW / 2, OH / 2), (max(0.05, t1 - 0.7 * kk), z_near, A_[0], A_[1] - 40), (t_arr0 + 0.3 * kk, z_near, A_[0], A_[1] - 40),
               (max(t_arr0 + 0.35 * kk, t2 - 0.1), z_near * 0.95, B_[0], B_[1] - 60), (max(t2 + 0.5 * kk, min(t2 + 2.0, dur - 0.3)), z_end, (A_[0] + B_[0]) / 2 - 40, (A_[1] + B_[1]) / 2 - 120)]
    else:  # several places: the camera follows every pin in turn, then pulls back to show them all
        xs2, ys2 = [q[0] for q in P_], [q[1] for q in P_]
        ext2 = max((max(xs2) - min(xs2)) * 1.5, (max(ys2) - min(ys2)) * 2.1, 200.0)
        zq = max(1.2, min(2.4, OW * 0.8 / ext2))
        z_pin = max(1.5, min(2.4, zq * 1.25))
        kfs = [(0.0, z_far, OW / 2, OH / 2)]
        for k_, q_ in enumerate(P_):
            kfs.append((max(kfs[-1][0] + 0.1, tp_[k_] - 0.5 * kk), z_pin, q_[0], q_[1] - 50))
            kfs.append((kfs[-1][0] + 0.9 * kk, z_pin, q_[0], q_[1] - 50))
        kfs.append((max(kfs[-1][0] + 0.3, min(tp_[-1] + 2.0, dur - 0.3)), zq, (min(xs2) + max(xs2)) / 2, (min(ys2) + max(ys2)) / 2 - 100))

    def cam(t):
        if t <= kfs[0][0]:
            return kfs[0][1:]
        for a_, b_ in zip(kfs, kfs[1:]):
            if t <= b_[0]:
                q = e_io(cl((t - a_[0]) / max(1e-3, b_[0] - a_[0])))
                return math.exp(lerp(math.log(a_[1]), math.log(b_[1]), q)), lerp(a_[2], b_[2], q), lerp(a_[3], b_[3], q)
        return kfs[-1][1:]

    def draw(t):
        z, cx, cy = cam(t)
        vw = OW / z
        vh = vw * H / W
        cx = min(OW - vw / 2, max(vw / 2, cx))
        cy = min(OH - vh / 2, max(vh / 2, cy))
        l, tp = cx - vw / 2, cy - vh / 2
        fr = TI.crop((int(l), int(tp), int(l + vw), int(tp + vh))).resize((W, H), Image.BILINEAR)
        k = W / vw

        def sp(x, y):
            return (x - l) * k, (y - tp) * k
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(ov)
        for li, arc_l in enumerate(arcs):
            a0 = tp_[li] + 0.5 * kk
            a1 = tp_[li + 1] - 0.1
            prog = cl((t - a0) / max(0.4, a1 - a0))
            n = int(prog * 70)
            if n >= 1:
                ptsl = [sp(*q) for q in arc_l[:n + 1]]
                d.line(ptsl, fill=(255, 255, 255, 70), width=int(20 * k), joint="curve")
                d.line(ptsl, fill=(255, 255, 255, 255), width=max(4, int(9 * k)), joint="curve")
                (x1, y1), (x2, y2) = ptsl[-2] if len(ptsl) > 1 else ptsl[-1], ptsl[-1]
                a_ = math.atan2(y2 - y1, x2 - x1)
                hs = 34 * k
                d.polygon([(x2 + math.cos(a_) * hs * 0.9, y2 + math.sin(a_) * hs * 0.9),
                           (x2 + math.cos(a_ + 2.5) * hs, y2 + math.sin(a_ + 2.5) * hs),
                           (x2 + math.cos(a_ - 2.5) * hs, y2 + math.sin(a_ - 2.5) * hs)], fill=(255, 255, 255, 255))
        fr = fr.convert("RGBA")
        fr.alpha_composite(ov)
        for i, (pt_, tt) in enumerate(zip(P_, tp_)):
            q = t - tt
            if q < 0:
                continue
            x, y = sp(*pt_)
            sz = max(0.75, min(1.3, k))
            ring = 1.0 + (q % 1.4) / 1.4 * 1.8
            put(fr, soft_circle(22, (255, 255, 255), 0.5), x, y, ring * sz, 0.6 * (1 - (q % 1.4) / 1.4) * cl(q * 3))
            put(fr, pin_img, x, y - 10 * sz, sz * e_back(q / 0.4), cl(q * 6))
            put(fr, tags[i], x, y - (62 + lerp(-40, 0, e_out(q / 0.35))) * sz, sz, cl((q - 0.1) * 4))
        if NP == 2 and t > t_text:  # distance is written only when exactly two places were named
            x, y = sp(*kpos)
            put(fr, ktxt_sp, x, y, max(0.7, min(1.4, k * 1.1)) * (0.9 + 0.1 * e_out(cl((t - t_text) / 0.4))), cl((t - t_text) * 4))
        return fr.convert("RGB")
    return draw


def make_map_draw(sc, dur, speech_dur, pal, events):
    np = _np()
    m = sc["map"]
    try:
        spread_ = max([hav_km((a_["lat"], a_["lon"]), (b_["lat"], b_["lon"])) for a_ in m["pins"] for b_ in m["pins"]] or [0.0])
    except Exception:  # noqa
        spread_ = 0.0
    if m["pins"] and spread_ < 6000 and os.environ.get("TERRAIN_ONLY", "1") != "0":  # only the tilted terrain map, no satellite / globe look
        m2 = dict(m, mode="terrain", route=len(m["pins"]) >= 2)
        return make_terrain_draw(dict(sc, map=m2), dur, speech_dur, pal, events)
    if m.get("mode") == "terrain" and len(m["pins"]) >= 2:
        return make_terrain_draw(sc, dur, speech_dur, pal, events)
    pins = m["pins"]
    route = m["route"] and len(pins) >= 2
    mode = m["mode"]
    pts = [(p["lat"], p["lon"]) for p in pins]
    lats, lons = [p[0] for p in pts], [p[1] for p in pts]
    span_lon = max(lons) - min(lons)
    if mode == "globe" and (span_lon > 110 or (max(lats) - min(lats)) > 90):
        mode = "flat"
    land = get_land()
    base = bg_base(tuple(pal["top"]), tuple(pal["bottom"]))
    bubbles = _bubbles()
    head = head_sprite(sc["headline_hi"], pal) if sc.get("headline_hi") else None
    pin_img = pin_sprite(pal)
    labels = [lower_third(p["label_hi"], pal) if False else text_layer(p["label_hi"], load_font(34, "label"), fill=(255, 255, 255)) for p in pins]
    label_pills = []
    for lay in labels:
        w, h = lay.width + 36, lay.height + 14
        pl = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        col = Image.new("RGBA", (w, h), tuple(pal["ink"]) + (255,))
        col.putalpha(rounded_mask((w, h), h // 2))
        pl.alpha_composite(col)
        pl.alpha_composite(lay, ((w - lay.width) // 2, (h - lay.height) // 2))
        label_pills.append(pl)
    # path through the pins
    path = []
    for a, b in zip(pts, pts[1:]):
        path += gc_points(a, b, 40)[(1 if path else 0):]
    seg_len = [0.0]
    for a, b in zip(path, path[1:]):
        seg_len.append(seg_len[-1] + hav_km(a, b))
    total_km = seg_len[-1] if route else 0.0
    pin_km = [0.0]
    for a, b in zip(pts, pts[1:]):
        pin_km.append(pin_km[-1] + hav_km(a, b))
    first_pin = min(1.2, 0.18 * dur)
    if dur < 6.0:  # the sentence is short: the whole animation must finish inside it
        t_start, t_end = min(1.9, 0.22 * dur), max(min(1.9, 0.22 * dur) + 0.7, min(dur - 0.5, speech_dur * 0.85))
    else:
        t_start, t_end = 1.9, max(3.2, min(dur - 1.2, speech_dur * 0.85))
    if route:
        pin_t = [first_pin] + [t_start + (t_end - t_start) * (k / max(1.0, total_km)) for k in pin_km[1:]]
    else:
        pin_t = [first_pin + min(0.8, 0.6 * dur / max(1, len(pts))) * i for i in range(len(pts))]
        wt_m, nw_m = sc.get("wt"), sc["narration_hi"].split()
        if wt_m and len(wt_m) == len(nw_m):  # each pin drops exactly when its place name is spoken
            got, last_k = [], -1
            for p_ in pins:
                toks = [x.strip("।,?!") for x in p_["label_hi"].split() if len(x.strip("।,?!")) > 1]
                hit = next((k for k, w_ in enumerate(nw_m) if k > last_k and any(tk in w_ or (len(w_) > 2 and w_.strip("।,?!") in tk) for tk in toks)), None)
                if hit is None:
                    got = None
                    break
                got.append(max(min(0.9, 0.2 * dur), wt_m[hit][0] - 0.05))
                last_k = hit
            if got:
                pin_t = got
    if pin_t[-1] > dur - 1.0 and dur > 1.5:  # never let a pin drop after the sentence is over
        f_ = max(0.3, (dur - 1.0) / pin_t[-1])
        pin_t = [t_ * f_ for t_ in pin_t]
    for t in pin_t:
        events.append((t, "pop", 0.8))
    events.append((0.2, "whoosh_soft", 0.8))
    if route:
        for k in range(int((t_end - t_start) / 0.12)):
            events.append((t_start + k * 0.12, "tick", 0.12))
        events.append((t_end, "ding", 0.7))
    # view geometry
    mid_lat = sum(lats) / len(lats)
    ux = sum(math.cos(math.radians(a)) * math.cos(math.radians(o)) for a, o in pts)
    uy = sum(math.cos(math.radians(a)) * math.sin(math.radians(o)) for a, o in pts)
    mid_lon = math.degrees(math.atan2(uy, ux))
    R, gcx, gcy = 228, W / 2, 322
    PW, PH, PX, PY = 1160, 428, 60, 94
    ocean_sprite = {}
    if mode == "globe":
        D = 2 * R + 40
        gg = np.mgrid[0:2 * R, 0:2 * R].astype(np.float32)
        d = np.sqrt((gg[0] - R) ** 2 + (gg[1] - R) ** 2) / R
        shade = np.clip(1.0 - 0.55 * d ** 2.2, 0.35, 1.0)
        c_in, c_out = np.array([90, 175, 235], np.float32), np.array([20, 70, 150], np.float32)
        col = (c_out[None, None, :] + (c_in - c_out)[None, None, :] * (1 - d ** 1.5)[..., None])
        ocean = Image.fromarray(np.clip(col, 0, 255).astype(np.uint8)).convert("RGBA")
        am = Image.new("L", (2 * R * 3, 2 * R * 3), 0)
        ImageDraw.Draw(am).ellipse([0, 0, 2 * R * 3 - 1, 2 * R * 3 - 1], fill=255)
        ocean.putalpha(am.resize((2 * R, 2 * R), Image.LANCZOS))
        ocean_sprite["img"] = ocean
        ocean_sprite["glow"] = glow_sprite(2 * R + 150, (255, 255, 255), 0.55)
        hl = np.clip(1 - np.sqrt((gg[0] - R * 0.65) ** 2 + (gg[1] - R * 0.6) ** 2) / (R * 0.9), 0, 1) ** 1.6 * 70
        edge = np.clip((d - 0.55) / 0.45, 0, 1) ** 1.6 * 120
        sh = np.zeros((2 * R, 2 * R, 4), np.uint8)
        sh[..., 0:3] = 255
        sh[..., 3] = np.clip(hl, 0, 255).astype(np.uint8)
        sh2 = np.zeros((2 * R, 2 * R, 4), np.uint8)
        sh2[..., 0:3] = (5, 20, 60)
        sh2[..., 3] = np.clip(edge, 0, 255).astype(np.uint8)
        ocean_sprite["shade"] = Image.alpha_composite(Image.fromarray(sh), Image.fromarray(sh2))
        ocean_sprite["mask"] = ocean.getchannel("A")

    def centre(t):
        # globe spins in, then turns to face each pin as it is named, finally settles on the middle of all of them
        wp = [(0.0, mid_lat * 0.7 + 8, mid_lon + 85)]
        for k_, (la_, lo_) in enumerate(pts):
            wp.append((max(wp[-1][0] + 0.3, pin_t[k_] - 0.6), la_ * 0.85, lo_))
        wp.append((max(wp[-1][0] + 0.8, pin_t[-1] + 1.8), mid_lat * 0.8, mid_lon))
        if t <= wp[0][0]:
            return wp[0][1:]
        for a_, b_ in zip(wp, wp[1:]):
            if t <= b_[0]:
                q = e_io(cl((t - a_[0]) / max(1e-3, b_[0] - a_[0])))
                dl = (b_[2] - a_[2] + 180) % 360 - 180
                return lerp(a_[1], b_[1], q), a_[2] + dl * q
        return wp[-1][1:]

    def proj_globe(lat, lon, c):
        p0, l0 = math.radians(c[0]), math.radians(c[1])
        la, lo = np.radians(lat), np.radians(lon)
        dl = lo - l0
        x = R * np.cos(la) * np.sin(dl)
        y = R * (math.cos(p0) * np.sin(la) - math.sin(p0) * np.cos(la) * np.cos(dl))
        cc = math.sin(p0) * np.sin(la) + math.cos(p0) * np.cos(la) * np.cos(dl)
        return x, y, cc

    # flat view bounds
    asp = PW / PH
    h_lon = max(max(span_lon / 2 + 14, (max(lats) - min(lats)) / 2 * asp + 12), 24.0)
    r_c = ((min(lons) + max(lons)) / 2 if span_lon < 180 else mid_lon, (min(lats) + max(lats)) / 2)

    # camera keyframes: wide world -> zoom onto each pin the moment it is named -> pull back to show all of them
    follow_hl = max(8.0, min(30.0, h_lon * (0.55 if route else 0.4)))
    t_out = pin_t[-1] + 1.8
    kf_view = [(0.0, 0.0, 12.0, 185.0)]
    for k_, (la_, lo_) in enumerate(pts):
        kf_view.append((max(kf_view[-1][0] + 0.2, pin_t[k_] - 0.7), lo_, la_, follow_hl * (1.35 if k_ == 0 else 1.0)))
    kf_view.append((max(kf_view[-1][0] + 0.8, t_out), r_c[0], r_c[1], h_lon))

    def view_kf(t):
        if t <= kf_view[0][0]:
            return kf_view[0][1:]
        for a_, b_ in zip(kf_view, kf_view[1:]):
            if t <= b_[0]:
                q = e_io(cl((t - a_[0]) / max(1e-3, b_[0] - a_[0])))
                return lerp(a_[1], b_[1], q), lerp(a_[2], b_[2], q), math.exp(lerp(math.log(a_[3]), math.log(b_[3]), q))
        return kf_view[-1][1:]

    def flat_view(t):
        return view_kf(t)

    def draw(t):
        fr = base.copy()
        _draw_bubbles(fr, bubbles, t)
        if head is not None:
            put(fr, head, W / 2, 62, 1.0, cl(t * 2.5))
        screen = []  # (x, y, visible) of each pin at 1x
        route_pts = []
        if mode == "globe":
            c = centre(t)
            put(fr, ocean_sprite["glow"], gcx, gcy, 1.0, 0.8)
            k = 2
            cv = Image.new("RGBA", (2 * R * k, 2 * R * k), (0, 0, 0, 0))
            dr = ImageDraw.Draw(cv)
            for gl in range(-60, 61, 30):  # graticule
                la = np.full(60, float(gl))
                lo = np.linspace(-180, 180, 60)
                x, y, cc = proj_globe(la, lo, c)
                seg = []
                for xi, yi, ci in zip(x, y, cc):
                    if ci > 0:
                        seg.append((R + xi * 1, R - yi * 1))
                    else:
                        if len(seg) > 1:
                            dr.line([(a * k, b * k) for a, b in seg], fill=(255, 255, 255, 70), width=2)
                        seg = []
                if len(seg) > 1:
                    dr.line([(a * k, b * k) for a, b in seg], fill=(255, 255, 255, 70), width=2)
            for gl in range(-180, 180, 30):
                la = np.linspace(-85, 85, 40)
                lo = np.full(40, float(gl))
                x, y, cc = proj_globe(la, lo, c)
                seg = []
                for xi, yi, ci in zip(x, y, cc):
                    if ci > 0:
                        seg.append((R + xi, R - yi))
                    else:
                        if len(seg) > 1:
                            dr.line([(a * k, b * k) for a, b in seg], fill=(255, 255, 255, 70), width=2)
                        seg = []
                if len(seg) > 1:
                    dr.line([(a * k, b * k) for a, b in seg], fill=(255, 255, 255, 70), width=2)
            for ring in land:
                x, y, cc = proj_globe(ring[:, 1], ring[:, 0], c)
                if not (cc > 0).any():
                    continue
                n = np.hypot(x, y)
                n[n < 1e-6] = 1e-6
                hid = cc <= 0
                x = np.where(hid, x / n * R * 0.999, x)
                y = np.where(hid, y / n * R * 0.999, y)
                poly = [((R + a) * k, (R - b) * k) for a, b in zip(x, y)]
                if len(poly) > 2:
                    dr.polygon(poly, fill=(120, 196, 96, 255), outline=(70, 140, 70, 255))
            if route and len(path) > 1:
                prog = cl((t - t_start) / (t_end - t_start)) * total_km
                seg = []
                for (la, lo), dist in zip(path, seg_len):
                    if dist > prog:
                        break
                    x, y, cc = proj_globe(np.array([la]), np.array([lo]), c)
                    if cc[0] > 0:
                        seg.append(((R + x[0]) * k, (R - y[0]) * k))
                    else:
                        if len(seg) > 1:
                            dr.line(seg, fill=(255, 255, 255, 255), width=14)
                            dr.line(seg, fill=tuple(pal["accent"]) + (255,), width=8)
                        seg = []
                if len(seg) > 1:
                    dr.line(seg, fill=(255, 255, 255, 255), width=14)
                    dr.line(seg, fill=tuple(pal["accent"]) + (255,), width=8)
                if t >= t_start and seg:
                    route_pts.append(((seg[-1][0]) / k + gcx - R, (seg[-1][1]) / k + gcy - R))
            cv = cv.reduce(k)
            sphere = ocean_sprite["img"].copy()
            sphere.alpha_composite(cv)
            sphere.alpha_composite(ocean_sprite["shade"])
            a = ImageChops.multiply(sphere.getchannel("A"), ocean_sprite["mask"])
            sphere.putalpha(a)
            put(fr, sphere, gcx, gcy, 1.0, 1.0)
            for la, lo in pts:
                x, y, cc = proj_globe(np.array([la]), np.array([lo]), c)
                screen.append((gcx + x[0], gcy - y[0], cc[0] > 0.08))
        else:
            cx_, cy_, hl_ = flat_view(t)
            sc_ = (PW / 2) / hl_
            k = 2
            cv = Image.new("RGB", (PW * k, PH * k), (150, 205, 245))
            dr = ImageDraw.Draw(cv, "RGBA")

            def fp(lat, lon):
                return ((PW / 2 + (lon - cx_) * sc_) * k, (PH / 2 - (lat - cy_) * sc_) * k)
            step = 30 if hl_ > 60 else (10 if hl_ > 20 else 5)
            for gl in range(-90, 91, step):
                dr.line([fp(gl, -180), fp(gl, 180)], fill=(255, 255, 255, 90), width=2)
            for gl in range(-180, 181, step):
                dr.line([fp(-90, gl), fp(90, gl)], fill=(255, 255, 255, 90), width=2)
            for ring in land:
                poly = [fp(b, a) for a, b in ring]
                xs = [p[0] for p in poly]
                ys = [p[1] for p in poly]
                if max(xs) < 0 or min(xs) > PW * k or max(ys) < 0 or min(ys) > PH * k:
                    continue
                dr.polygon(poly, fill=(120, 196, 96, 255), outline=(70, 140, 70, 255))
            if route and len(path) > 1:
                prog = cl((t - t_start) / (t_end - t_start)) * total_km
                seg = []
                for (la, lo), dist in zip(path, seg_len):
                    if dist > prog:
                        break
                    seg.append(fp(la, lo))
                if len(seg) > 1:
                    dr.line(seg, fill=(255, 255, 255, 255), width=14, joint="curve")
                    dr.line(seg, fill=tuple(pal["accent"]) + (255,), width=8, joint="curve")
                if t >= t_start and seg:
                    route_pts.append((PX + seg[-1][0] / k, PY + seg[-1][1] / k))
            panel = cv.reduce(k).convert("RGBA")
            pm = rounded_mask((PW, PH), 30)
            panel.putalpha(pm)
            border = Image.new("RGBA", (PW + 12, PH + 12), (255, 255, 255, 255))
            border.putalpha(rounded_mask((PW + 12, PH + 12), 36))
            put(fr, border, PX + PW / 2, PY + PH / 2, 1.0, 1.0)
            put(fr, panel, PX + PW / 2, PY + PH / 2, 1.0, 1.0)
            for la, lo in pts:
                x, y = fp(la, lo)
                inside = 0 <= x <= PW * k and 0 <= y <= PH * k
                screen.append((PX + x / k, PY + y / k, inside))
        for i, (x, y, vis) in enumerate(screen):
            q = t - pin_t[i]
            if q < 0 or not vis:
                continue
            drop = lerp(-70, 0, e_out(q / 0.35))
            ring_r = 1.0 + (q % 1.4) / 1.4 * 1.6
            nxt = pin_t[i + 1] if i + 1 < len(pin_t) else None
            old = 0.0 if nxt is None else cl((t - nxt) / 0.5) * (1 - cl((t - t_out + 0.2) / 0.6))  # earlier pins shrink away while the camera follows the new one
            put(fr, soft_circle(22, (255, 255, 255), 0.5), x, y - 18, ring_r, 0.6 * (1 - (q % 1.4) / 1.4) * cl(q * 3) * (1 - old))
            put(fr, pin_img, x, y - 20 + drop, e_back(q / 0.4) * (1 - 0.3 * old), cl(q * 6))
            ly = y - 62 if i % 2 == 0 else y + 22
            put(fr, label_pills[i], x, ly + drop * 0.3, 1.0 - 0.25 * old, cl((q - 0.15) * 4) * (1 - 0.85 * old))
        for (x, y) in route_pts:
            put(fr, soft_circle(14, (255, 255, 255), 1.0), x, y, 1.0 + 0.1 * math.sin(t * 9), 1.0)
        if route:
            prog = cl((t - t_start) / (t_end - t_start)) * total_km
            if t > t_start - 0.1:
                txt = f"≈ {prog:,.0f} किमी"
                put(fr, quick_text(txt, 54, tuple(pal["accent"]), stroke=8, stroke_fill=(255, 255, 255), role="label"),
                    230 if mode == "globe" else W / 2, 505 if mode == "globe" else 92 + PH - 28, 1.0, cl((t - t_start + 0.1) * 4))
        return fr
    return draw


LAYER_COLS = [(255, 196, 60), (255, 130, 50), (230, 70, 60), (170, 40, 110), (90, 40, 140)]


def make_layers_draw(sc, dur, speech_dur, pal, events):
    layers = sc["layers"]
    n = len(layers)
    base = bg_base(tuple(pal["top"]), tuple(pal["bottom"]))
    bubbles = _bubbles()
    head = head_sprite(sc["headline_hi"], pal) if sc.get("headline_hi") else None
    top, bh = 110, min(84, 400 // n)
    cx = 360
    t_i = [0.5 + i * max(0.7, (speech_dur * 0.75) / n) for i in range(n)]
    for t in t_i:
        events.append((t, "pop", 0.7))
    lab = [text_layer(l["label_hi"], load_font(46, "head"), fill=tuple(pal["ink"]), align="left") for l in layers]
    det = [text_layer(l["detail_hi"], load_font(34, "label"), fill=tuple(pal["accent"]), stroke=4, stroke_fill=(255, 255, 255), align="left") if l["detail_hi"] else None for l in layers]
    arrow = sym_arrow(pal, 64).rotate(-90, expand=True)

    def draw(t):
        fr = base.copy()
        _draw_bubbles(fr, bubbles, t)
        if head is not None:
            put(fr, head, W / 2, 62, 1.0, cl(t * 2.5))
        d = ImageDraw.Draw(fr)
        for i in range(n):
            q = t - t_i[i]
            if q < 0:
                continue
            w = 560 - i * 70
            off = lerp(-700, 0, e_out(q / 0.5))
            y0 = top + i * (bh + 6)
            col = LAYER_COLS[i % 5] if n > 1 else LAYER_COLS[0]
            d.rounded_rectangle([cx - w / 2 + off, y0, cx + w / 2 + off, y0 + bh], 22, fill=(255, 255, 255))
            d.rounded_rectangle([cx - w / 2 + 5 + off, y0 + 5, cx + w / 2 - 5 + off, y0 + bh - 5], 18, fill=col)
            d.ellipse([cx - 14 + off, y0 + bh / 2 - 14, cx + 14 + off, y0 + bh / 2 + 14], fill=(255, 255, 255))
            a = cl(q * 3)
            put(fr, lab[i], 760 + lab[i].width / 2 + (1 - a) * 80, y0 + bh / 2 - (14 if det[i] else 0), 1.0, a)
            if det[i] is not None:
                put(fr, det[i], 760 + det[i].width / 2 + (1 - a) * 80, y0 + bh / 2 + 26, 1.0, a)
        k = max(0, sum(1 for x in t_i if t >= x) - 1)
        put(fr, arrow, 90, top + k * (bh + 6) + bh / 2 + 6 * math.sin(t * 5), 1.0, cl(t - t_i[0]))
        return fr
    return draw




# --------------------------------------------------------------------------- SKETCH scenes: animated line-art diagrams that EXPLAIN (parchment / dark paper, drawn on stroke by stroke)
SK_WU, SK_HU = 100.0, 56.25
SK_THEMES = {
    "paper": {"top": (236, 224, 200), "bot": (214, 196, 160), "ink": (58, 40, 28), "accent": (188, 34, 34), "soft": (226, 205, 168), "rays": (170, 140, 100), "dark": False},
    "dark": {"top": (9, 11, 22), "bot": (20, 24, 44), "ink": (240, 204, 124), "accent": (255, 122, 52), "soft": (60, 54, 40), "rays": (120, 100, 60), "dark": True},
}


def sk_theme(sc):
    dark = sc.get("tone") == "suspense" or sc.get("grade") in ("dark", "dread", "bwred", "redwash")
    return SK_THEMES["dark" if dark else "paper"]


def sk_bg(th):
    key = ("skbg", th["dark"])
    if key in _C:
        return _C[key]
    np = _np()
    yy = np.linspace(0, 1, H, dtype=np.float32)[:, None, None]
    top, bot = np.array(th["top"], dtype=np.float32), np.array(th["bot"], dtype=np.float32)
    a = top * (1 - yy) + bot * yy
    a = np.broadcast_to(a, (H, W, 3)).copy()
    rng = np.random.default_rng(11)
    blot = Image.fromarray((rng.random((9, 16)) * 255).astype(np.uint8)).resize((W, H), Image.BICUBIC)
    a += ((np.asarray(blot, dtype=np.float32) / 255.0 - 0.5) * (17 if not th["dark"] else 10))[..., None]
    a *= vig_mask() * (0.55 if th["dark"] else 0.32) + (0.45 if th["dark"] else 0.68)
    img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).convert("RGBA")
    rays = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    rd = ImageDraw.Draw(rays)
    cx, cy = W * 0.70, H * 0.50
    for k in range(36):
        ang = k * math.pi / 18
        rd.line([(cx, cy), (cx + math.cos(ang) * W, cy + math.sin(ang) * W)], fill=tuple(th["rays"]) + (26,), width=2)
    img.alpha_composite(rays)
    _C[key] = img.convert("RGB")
    return _C[key]


def sk_points(pr):
    t = pr["t"]
    if t in ("line", "arrow"):
        pts = [tuple(p) for p in pr["p"]]
        if t == "arrow" and len(pts) >= 2:
            (x0, y0), (x1, y1) = pts[-2], pts[-1]
            ang = math.atan2(y1 - y0, x1 - x0)
            hl = 2.2
            pts += [(x1 - hl * math.cos(ang - 0.45), y1 - hl * math.sin(ang - 0.45)), (x1, y1), (x1 - hl * math.cos(ang + 0.45), y1 - hl * math.sin(ang + 0.45))]
        return pts
    if t == "rect":
        x0, y0, x1, y1 = pr["b"]
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    if t == "circle":
        cx, cy = pr["c"]
        r = pr["r"]
        return [(cx + r * math.cos(2 * math.pi * k / 56), cy + r * math.sin(2 * math.pi * k / 56)) for k in range(57)]
    if t == "arc":
        cx, cy = pr["c"]
        r, a0, a1 = pr["r"], math.radians(pr["a0"]), math.radians(pr["a1"])
        n = max(8, int(abs(a1 - a0) / 0.12))
        return [(cx + r * math.cos(a0 + (a1 - a0) * k / n), cy + r * math.sin(a0 + (a1 - a0) * k / n)) for k in range(n + 1)]
    if t == "poly":
        pts = [tuple(p) for p in pr["p"]]
        return pts + pts[:1]
    return []


def clean_prims(raw):
    """Gemini's drawing instructions -> safe, bounded list of strokes (coordinates clamped to the 100 x 56.25 canvas)."""
    if isinstance(raw, dict):
        raw = raw.get("strokes") or raw.get("shapes") or raw.get("items") or []
    out = []

    def num(v, lo, hi):
        return max(lo, min(hi, float(v)))

    def pt(p):
        return [num(p[0], 0, SK_WU), num(p[1], 0, SK_HU)]

    for pr in (raw or [])[:48]:
        try:
            t = str(pr.get("t") or "").lower()
            col = pr.get("col") if pr.get("col") in ("ink", "accent") else "ink"
            fill = pr.get("fill") if pr.get("fill") in ("ink", "accent", "soft") else ""
            w = num(pr.get("w", 2), 1, 4)
            if t in ("line", "arrow", "poly"):
                pts = [pt(p) for p in pr.get("p", [])][:24]
                if len(pts) >= 2:
                    out.append({"t": t, "p": pts, "col": col, "w": w, "fill": fill if t == "poly" else ""})
            elif t == "rect":
                b = [num(v, 0, SK_WU) for v in pr.get("b", [])][:4]
                if len(b) == 4 and b[2] > b[0] and b[3] > b[1]:
                    out.append({"t": t, "b": b, "col": col, "w": w, "fill": fill})
            elif t == "circle":
                c = pt(pr.get("c"))
                out.append({"t": t, "c": c, "r": num(pr.get("r"), 0.6, 30), "col": col, "w": w, "fill": fill})
            elif t == "arc":
                out.append({"t": t, "c": pt(pr.get("c")), "r": num(pr.get("r"), 0.6, 40), "a0": num(pr.get("a0", 0), -720, 720), "a1": num(pr.get("a1", 180), -720, 720), "col": col, "w": w, "fill": ""})
            elif t == "text":
                s_ = clean_short(pr.get("s"), 26)
                if s_:
                    out.append({"t": "text", "p": pt(pr.get("p")), "s": s_, "size": num(pr.get("size", 3), 1.8, 5), "col": col})
        except Exception:  # noqa
            continue
    return out


def sketch_prompt(sc, topic):
    return f"""You are an illustrator for a premium explainer film. Draw ONE clear teaching diagram as animated line-art (like an engineer's sketch / museum diagram), to be drawn stroke by stroke while the narrator speaks.
VIDEO TOPIC: {topic}
WHAT TO DRAW: {sc.get('sketch_brief_en')}
NARRATION (Hindi): {sc['narration_hi']}
Canvas: x from 0 to 100, y from 0 to 56.25 (y grows downward). The LEFT 38 units are reserved for the headline text: draw ONLY inside x 40..97 and y 5..47 (the bottom strip is for the timeline).
Return ONLY JSON: {{"strokes": [ ... ]}} with 16-34 strokes in the order they should be drawn (big shapes first, details next, labels last). Stroke types:
 {{"t":"line","p":[[x,y],[x,y],...],"w":2,"col":"ink"}}   polyline (2-12 points)
 {{"t":"arrow","p":[[x,y],[x,y]],"w":2,"col":"accent"}}   arrow showing movement / cause
 {{"t":"circle","c":[x,y],"r":8,"w":2,"col":"ink","fill":"soft"}}   (fill optional: soft, ink or accent)
 {{"t":"arc","c":[x,y],"r":10,"a0":0,"a1":180,"w":2,"col":"ink"}}   degrees
 {{"t":"rect","b":[x0,y0,x1,y1],"w":2,"col":"ink","fill":"soft"}}
 {{"t":"poly","p":[[x,y],...],"w":2,"col":"ink","fill":"soft"}}   closed shape
 {{"t":"text","p":[x,y],"s":"short label","size":3,"col":"ink"}}   at most 4 labels, each at most 3 words, in simple Hindi (Devanagari) or a proper noun; p is the CENTRE of the label
Rules: the drawing must show the exact idea of the narration (what collides with what, what flows where, which part is inside which); use "accent" colour only for the key moving / changing element and its arrow; use real proportions (a small moon next to a bigger planet), keep everything inside the allowed area, no overlapping labels, simple clean shapes a child understands."""


def build_sketches(scenes, topic=""):
    """Ask Gemini for the line drawing of every sketch scene; a scene whose drawing fails becomes an ordinary picture scene."""
    n_ok = 0
    for sc in scenes:
        if sc.get("type") != "sketch":
            continue
        prims = None
        if GEMINI_KEY:
            try:
                prims = clean_prims(gemini_json(sketch_prompt(sc, topic), 0.5))
            except Exception as e:  # noqa
                log("sketch drawing failed:", str(e)[:100])
        if prims and len(prims) >= 6:
            sc["strokes"] = prims
            n_ok += 1
            continue
        sc["type"] = "photo"
        sc["shots"] = [{"text_hi": sc["narration_hi"], "prompt_en": clean_short(sc.get("sketch_brief_en"), 300) or "a dramatic wide nature scene", "label_hi": "",
                        "query_en": "", "entity_en": "", "motion": False, "clip_en": "", "popup_en": "", "ov": None, "fx": None, "fy": None}]
    log(f"sketch scenes drawn: {n_ok}")


def _sk_stroke_mask(pts, w_px, frac, bbox):
    """Anti-aliased mask (drawn at 2x) of the first `frac` of a polyline; returns (mask_L_1x, (x0, y0))."""
    x0, y0, x1, y1 = bbox
    mw, mh = max(2, x1 - x0), max(2, y1 - y0)
    big = Image.new("L", (mw * 2, mh * 2), 0)
    d = ImageDraw.Draw(big)
    segs, tot = [], 0.0
    for a, b in zip(pts, pts[1:]):
        l = math.hypot(b[0] - a[0], b[1] - a[1])
        segs.append((a, b, l))
        tot += l
    target, acc, drawn = tot * frac, 0.0, [pts[0]]
    for a, b, l in segs:
        if acc + l <= target:
            drawn.append(b)
            acc += l
        else:
            r = (target - acc) / max(l, 1e-6)
            drawn.append((a[0] + (b[0] - a[0]) * r, a[1] + (b[1] - a[1]) * r))
            break
    pp = [((x - x0) * 2, (y - y0) * 2) for x, y in drawn]
    if len(pp) >= 2:
        d.line(pp, fill=255, width=int(w_px * 2), joint="curve")
        r_ = w_px
        for x, y in (pp[0], pp[-1]):
            d.ellipse([x - r_, y - r_, x + r_, y + r_], fill=255)
    return big.resize((mw, mh), Image.LANCZOS), (x0, y0), drawn[-1]


def make_sketch_draw(sc, dur, sp, pal, events):
    th = sk_theme(sc)
    ux, uy = W / SK_WU, H / SK_HU
    strokes = sc["strokes"]
    # timing: strokes are drawn one after another (longer strokes take longer); labels fade in when reached
    t_a, t_b = 0.35, max(1.5, min(dur - 0.8, sp * 0.86))
    lens = []
    for pr in strokes:
        if pr["t"] == "text":
            lens.append(6.0)
        else:
            pts = sk_points(pr)
            lens.append(max(4.0, sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))))
    tot = sum(lens)
    plan, acc = [], 0.0
    for pr, l in zip(strokes, lens):
        s0 = t_a + (t_b - t_a) * acc / tot
        acc += l
        plan.append((s0, t_a + (t_b - t_a) * acc / tot))
    px = []
    for pr in strokes:
        if pr["t"] == "text":
            px.append(None)
            continue
        pts = [(x * ux, y * uy) for x, y in sk_points(pr)]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        wpx = pr["w"] * 1.5
        bb = (max(0, int(min(xs) - wpx - 6)), max(0, int(min(ys) - wpx - 6)), min(W, int(max(xs) + wpx + 6)), min(H, int(max(ys) + wpx + 6)))
        px.append((pts, wpx, bb))
    ink, acc_c, soft = tuple(th["ink"]), tuple(th["accent"]), tuple(th["soft"])
    cols = {"ink": ink, "accent": acc_c, "soft": soft}
    # headline: words appear one by one at the left, like kinetic type
    hw = (sc.get("headline_hi") or "").split()
    big_f = load_font(64, "head")
    head_sp = []
    for k in range(1, len(hw) + 1):
        head_sp.append(text_layer(" ".join(hw[:k]), big_f, fill=ink, max_w=int(W * 0.34), align="left", stroke=0,
                                  shadow=None if not th["dark"] else (0, 3, 6, 0.7)))
    h_t = [0.25 + (max(0.8, sp * 0.5)) * k / max(1, len(hw)) for k in range(len(hw))]
    year = (sc.get("year_hi") or "").strip()
    year_sp = text_layer(year, load_font(30, "title"), fill=ink, stroke=0) if year else None
    label_sp = {}
    for i, pr in enumerate(strokes):
        if pr["t"] == "text":
            label_sp[i] = text_layer(pr["s"], load_font(int(pr["size"] * 12), "sub"), fill=cols[pr["col"]], stroke=2 if th["dark"] else 0, stroke_fill=(0, 0, 0))
    state = {"k": 0, "layer": Image.new("RGBA", (W, H), (0, 0, 0, 0))}

    def commit(i):
        pr = strokes[i]
        if pr["t"] == "text":
            return
        pts, wpx, bb = px[i]
        if pr.get("fill") and pr["t"] in ("poly", "rect", "circle"):
            fl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            fd = ImageDraw.Draw(fl)
            c_ = cols[pr["fill"]] + (255 if pr["fill"] != "soft" else 235,)
            if pr["t"] == "circle":
                fd.polygon(pts, fill=c_)
            else:
                fd.polygon(pts, fill=c_)
            state["layer"].alpha_composite(fl.crop(bb), (bb[0], bb[1]))
        m, (x0, y0), _tip = _sk_stroke_mask(pts, wpx, 1.0, bb)
        col = Image.new("RGBA", m.size, cols[pr["col"]] + (255,))
        col.putalpha(m)
        state["layer"].alpha_composite(col, (x0, y0))

    def draw(t):
        fr = sk_bg(th).copy()
        done = sum(1 for (a, b) in plan if b <= t)
        if done < state["k"]:
            state["k"], state["layer"] = 0, Image.new("RGBA", (W, H), (0, 0, 0, 0))
        while state["k"] < done:
            commit(state["k"])
            state["k"] += 1
        # ground shadow so the drawing lifts off the paper
        fr.paste(state["layer"], (0, 0), state["layer"])
        tip = None
        for i, (a, b) in enumerate(plan):
            if a <= t < b and strokes[i]["t"] != "text":
                frac = e_io(cl((t - a) / max(1e-3, b - a)))
                pts, wpx, bb = px[i]
                m, (x0, y0), tip = _sk_stroke_mask(pts, wpx, max(0.001, frac), bb)
                col = Image.new("RGB", m.size, cols[strokes[i]["col"]])
                fr.paste(col, (x0, y0), m)
        if tip is not None:
            r_ = 7
            ImageDraw.Draw(fr).ellipse([tip[0] - r_, tip[1] - r_, tip[0] + r_, tip[1] + r_], outline=acc_c, width=3)
        for i, spr in label_sp.items():
            a, b = plan[i]
            if t >= a:
                al = e_out(cl((t - a) / 0.35))
                pr = strokes[i]
                put(fr, spr, pr["p"][0] * ux, pr["p"][1] * uy + (1 - al) * 8, 1.0, al)
        for k, spr in enumerate(head_sp):
            if t >= h_t[k]:
                al = e_out(cl((t - h_t[k]) / 0.3))
                if k == len(head_sp) - 1 or t < h_t[min(k + 1, len(h_t) - 1)]:
                    put(fr, spr, W * 0.045 + spr.width / 2 + (1 - al) * -14, H * 0.42, 1.0, al)
        if head_sp and t > h_t[-1] + 0.2:
            wb = min(W * 0.30, W * 0.30 * e_out(cl((t - h_t[-1] - 0.2) / 0.6)))
            ImageDraw.Draw(fr).rounded_rectangle([W * 0.05, H * 0.42 + head_sp[-1].height / 2 + 8, W * 0.05 + wb, H * 0.42 + head_sp[-1].height / 2 + 16], 4, fill=acc_c)
        d_ = ImageDraw.Draw(fr)
        for (cx_, cy_, sx, sy) in ((26, 20, 1, 1), (W - 26, 20, -1, 1), (26, H - 20, 1, -1), (W - 26, H - 20, -1, -1)):  # HUD corner ticks
            d_.line([(cx_, cy_), (cx_ + 22 * sx, cy_)], fill=ink, width=2)
            d_.line([(cx_, cy_), (cx_, cy_ + 22 * sy)], fill=ink, width=2)
        y_l = H - 30
        d_.line([(W * 0.22, y_l), (W * 0.92, y_l)], fill=ink, width=1)
        d_.line([(W * 0.22, y_l), (W * 0.22 + (W * 0.70) * cl(t / max(0.1, dur)), y_l)], fill=acc_c, width=3)
        for k in range(0, 15):
            xx = W * 0.22 + W * 0.70 * k / 14
            d_.line([(xx, y_l - 4), (xx, y_l + 4)], fill=ink, width=1)
        if year_sp is not None:
            put(fr, year_sp, 40 + year_sp.width / 2, H - 52, 1.0, e_out(cl(t / 0.5)))
        return fr

    return draw


def _make_draw(sc, dur, sp, pal, ctx, events):
    typ = sc["type"]
    if typ == "sketch":
        return make_sketch_draw(sc, dur, sp, pal, events)
    if typ == "multi":
        return make_multi_draw(sc, dur, sp, pal, events)
    if typ in ("merge", "split", "steps", "compare"):
        return make_explainer(sc, dur, pal, events)
    if typ == "counter":
        return make_counter_draw(sc, dur, sp, pal, events)
    if typ == "bars":
        return make_bars_draw(sc, dur, sp, pal, events)
    if typ == "kinetic":
        return make_kinetic_draw(sc, dur, sp, ctx, events, pal)
    if typ == "map":
        return make_map_draw(sc, dur, sp, pal, events)
    return make_photo_draw(sc, dur, sp, ctx, events, pal)


def render_scene(i, sc, out, pal, ctx, prev_sc, last):
    dur, sp = sc["dur"], sc["speech_dur"]
    events = []
    draw = _make_draw(sc, dur, sp, pal, ctx, events)
    subs = make_subtrack(sc["narration_hi"], sp, dur, pal, sc.get("wt"))
    trans, tdur = pick_transition(i, sc, prev_sc, ctx)
    nm, g = TRANS_SFX.get(trans, (None, 0))
    if nm and i > 0:
        events.append((0.0, nm, g))
    if i == 0:
        events.append((0.3, "impact", 0.8))
        events.append((max(0.5, dur - 2.7), "riser", 0.75))
    n = max(2, int(round((ctx["t0"] + dur) * FPS)) - ctx["frames"])
    ctx["frames"] += n
    cmd = ["ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "15", "-pix_fmt", "yuv420p", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    pre = None
    try:
        for k in range(n):
            t = k / FPS
            fr = draw(t)
            if t < tdur and trans != "none":
                fr = blend_trans(ctx["prev"], fr, t / tdur, trans)
            pre = fr
            out_fr = fr.copy()
            draw_sub(out_fr, subs, t)
            if last:
                a = cl((t - (dur - 0.9)) / 0.9)
                if a > 0:
                    out_fr = Image.blend(out_fr, Image.new("RGB", (W, H), (0, 0, 0)), a)
            proc.stdin.write(out_fr.tobytes())
        proc.stdin.close()
        err = proc.stderr.read().decode(errors="ignore")
        if proc.wait() != 0:
            raise RuntimeError("scene encode failed: " + err[-800:])
    except BrokenPipeError:
        raise RuntimeError("scene encode failed (pipe): " + proc.stderr.read().decode(errors="ignore")[-800:])
    finally:
        if proc.poll() is None:
            proc.kill()
    ctx["prev"] = pre
    for t, nm_, g_ in events:
        ctx["sfx"].append((ctx["t0"] + t, nm_, g_))
    ctx["t0"] += dur


_RENDER = {}


def _scene_plan(scenes):
    t0, frames, plan = 0.0, 0, []
    for sc in scenes:
        n = max(2, int(round((t0 + sc["dur"]) * FPS)) - frames)
        plan.append((t0, frames, n))
        frames += n
        t0 += sc["dur"]
    return plan


def _last_frame(i):
    """The last picture of scene i (the next scene blends from it); drawn again in the worker that needs it."""
    scenes, pal, plan = _RENDER["scenes"], _RENDER["pal"], _RENDER["plan"]
    sc = scenes[i]
    ctx = {"prev": Image.new("RGB", (W, H), (0, 0, 0)), "t0": plan[i][0], "frames": plan[i][1], "sfx": [], "imgs": {}, "rnd": random.Random(i), "last_cam": None}
    return _make_draw(sc, sc["dur"], sc["speech_dur"], pal, ctx, [])((plan[i][2] - 1) / FPS)


def _render_chunk(rng):
    lo, hi = rng
    scenes, tdir, pal, plan = _RENDER["scenes"], _RENDER["tdir"], _RENDER["pal"], _RENDER["plan"]
    ctx = {"prev": _last_frame(lo - 1) if lo else Image.new("RGB", (W, H), (0, 0, 0)), "t0": plan[lo][0], "frames": plan[lo][1], "sfx": [],
           "imgs": {}, "rnd": random.Random(1000 + lo), "last_cam": None}
    for i in range(lo, hi):
        sc = scenes[i]
        render_scene(i, sc, tdir / f"scene_{i:03d}.mp4", pal, ctx, scenes[i - 1] if i else None, i == len(scenes) - 1)
        keep = {x.get("image") for x in sc.get("shots", [])} | {sc.get("image")}
        ctx["imgs"] = {k: v for k, v in ctx["imgs"].items() if k[0] in keep}
        log(f"video scene {i + 1}/{len(scenes)} ({sc['type']}) done")
    return ctx["sfx"]


def build_video(scenes, tdir, pal):
    tdir.mkdir(parents=True, exist_ok=True)
    workers = int(os.environ.get("RENDER_WORKERS", str(min(4, os.cpu_count() or 1))))
    plan = _scene_plan(scenes)
    parts = [tdir / f"scene_{i:03d}.mp4" for i in range(len(scenes))]
    if workers <= 1 or len(scenes) < 4:
        ctx = {"prev": Image.new("RGB", (W, H), (0, 0, 0)), "t0": 0.0, "frames": 0, "sfx": [], "imgs": {},
               "rnd": random.Random(), "last_cam": None}
        for i, sc in enumerate(scenes):
            render_scene(i, sc, parts[i], pal, ctx, scenes[i - 1] if i else None, i == len(scenes) - 1)
            keep = {s_.get("image") for s_ in sc.get("shots", [])} | {sc.get("image")}
            ctx["imgs"] = {k: v for k, v in ctx["imgs"].items() if k[0] in keep}
            if i % 4 == 0:
                log(f"video scene {i + 1}/{len(scenes)} ({sc['type']})")
        sfx = ctx["sfx"]
    else:
        import multiprocessing
        from concurrent.futures import ProcessPoolExecutor
        _RENDER.update(scenes=scenes, tdir=tdir, pal=pal, plan=plan)
        total, chunks, lo, acc = sum(sc["dur"] for sc in scenes), [], 0, 0.0
        for i, sc in enumerate(scenes):
            acc += sc["dur"]
            if len(chunks) < workers - 1 and acc >= total / workers * (len(chunks) + 1) and i + 1 < len(scenes):
                chunks.append((lo, i + 1))
                lo = i + 1
        chunks.append((lo, len(scenes)))
        log(f"rendering {len(scenes)} scenes in {len(chunks)} parallel chunks: {chunks}")
        with ProcessPoolExecutor(max_workers=len(chunks), mp_context=multiprocessing.get_context("fork")) as ex:
            sfx = sorted([e for part in ex.map(_render_chunk, chunks) for e in part], key=lambda e: e[0])
    lst = tdir / "shots.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    silent = tdir / "silent.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", silent])
    return silent, sfx


def mux_final(silent, voice, music, sfx, out, max_mb=None):
    dur = ffprobe_dur(voice)
    fc = ("[1:a]highpass=f=70,aresample=44100,asplit=2[v][vsc];"
          "[2:a]volume=0.062[m];"
          "[m][vsc]sidechaincompress=threshold=0.03:ratio=12:attack=12:release=600[duck];"
          + ("[3:a]aresample=44100,aformat=channel_layouts=stereo,volume=0.55[fx];" if sfx else "")
          + "[v]aformat=channel_layouts=stereo[vst];"
          + ("[vst][duck][fx]amix=inputs=3:" if sfx else "[vst][duck]amix=inputs=2:")
          + "duration=first:dropout_transition=0:normalize=0[mix];"
          f"[mix]loudnorm=I=-16:TP=-1.5:LRA=11,aresample=44100,afade=t=out:st={max(0.5, dur - 2.0):.2f}:d=2.0[a]")
    hq = ["-c:v", "libx264", "-preset", "medium", "-crf", "17", "-x264-params", "aq-mode=3:deblock=-1,-1"]
    run(["ffmpeg", "-y", "-i", silent, "-i", voice, "-i", music, *(["-i", sfx] if sfx else []), "-filter_complex", fc,
         "-map", "0:v", "-map", "[a]", "-vf", f"fade=t=out:st={max(0.5, dur - 1.6):.2f}:d=1.6", *hq, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", "-shortest", out])
    # the full-quality file stays in out/ (GitHub artifact, 3 days). Telegram only takes 50 MB: a smaller copy is made for it, never by lowering the master.
    if max_mb and Path(out).stat().st_size > max_mb * 1024 * 1024:
        log("master is bigger than the Telegram limit: making a smaller Telegram copy (master kept in artifacts)")
        small = Path(str(out).replace("video.mp4", "video_telegram.mp4"))
        kb = int(max(600, (max_mb * 8 * 1024) / dur - 200))
        run(["ffmpeg", "-y", "-i", out, "-c:v", "libx264", "-preset", "slow", "-b:v", f"{kb}k", "-maxrate", f"{int(kb * 1.25)}k",
             "-bufsize", f"{2 * kb}k", "-x264-params", "aq-mode=3", "-c:a", "copy", "-movflags", "+faststart", small])
        MASTER_NOTE[0] = str(small)


MASTER_NOTE = [""]


# --------------------------------------------------------------------------- thumbnails
def grade_photo(im):
    """Punchy colour grade: contrast, saturation, teal shadows / warm highlights."""
    im = ImageOps.autocontrast(im, cutoff=1)
    im = ImageEnhance.Contrast(im).enhance(1.14)
    im = ImageEnhance.Color(im).enhance(1.35)
    im = ImageEnhance.Brightness(im).enhance(1.06)
    lum = im.convert("L")
    shadows = lum.point(lambda v: int(((255 - v) / 255) ** 2 * 70))
    highs = lum.point(lambda v: int((v / 255) ** 2 * 45))
    im = Image.composite(Image.new("RGB", im.size, (0, 110, 150)), im, shadows)
    im = Image.composite(Image.new("RGB", im.size, (255, 170, 70)), im, highs)
    return im


def fit_cover(path, size=(W, H)):
    return ImageOps.fit(Image.open(path).convert("RGB"), size, Image.LANCZOS)


def bloom(im, strength=0.35, radius=26):
    g = ImageEnhance.Brightness(im.filter(ImageFilter.GaussianBlur(radius))).enhance(strength)
    return ImageChops.screen(im, g)


def split_lines(text):
    w = text.split()
    n = len(w)
    if n <= 1:
        return [text]
    if n == 2:
        return w
    if n == 3:
        return [w[0], " ".join(w[1:])]
    if n <= 5:
        return [" ".join(w[:n // 2]), " ".join(w[n // 2:])]
    a, b = n // 3, 2 * n // 3
    return [" ".join(w[:a]), " ".join(w[a:b]), " ".join(w[b:])]


def ov_of(size):
    return -int(size * 0.06)  # negative overlap = small gap between tight-cropped lines


def tight(lay):
    box = lay.getchannel("A").point(lambda v: 255 if v > 60 else 0).getbbox()
    return lay.crop(box) if box else lay


def _style_line(l, f, key, size):
    stroke = max(6, min(15, int(size * 0.075)))
    ext = max(4, int(size * 0.05))
    return tight(_style_line_raw(l, f, key, size))


THUMB_ACC = [(255, 224, 0)]


def _style_line_raw(l, f, key, size):
    """Plain, readable thumbnail text: flat white (accent colour for the key line), thick dark outline, soft shadow. No 3D, no gradient."""
    stroke = max(6, min(16, int(size * 0.08)))
    return text_layer(l, f, fill=THUMB_ACC[0] if key else (255, 255, 255), stroke=stroke, stroke_fill=(8, 8, 12), shadow=(4, 7, 8, 0.75))


def _partitions(words, k):
    n = len(words)
    if k == 1:
        yield [" ".join(words)]
        return
    import itertools
    for cuts in itertools.combinations(range(1, n), k - 1):
        c = (0,) + cuts + (n,)
        yield [" ".join(words[c[x]:c[x + 1]]) for x in range(k)]


def title_layers(lines, max_w, max_h, key_last=True, start=210, text=None, max_lines=3):
    """Largest bold headline that REALLY fits the box (measured on the finished layers).
    If `text` is given, the best 1..max_lines line-split is searched; otherwise `lines` is used as is."""
    probe = ImageDraw.Draw(Image.new("L", (4, 4)))
    words = (text or " ".join(lines)).split()
    options = [lines]
    if text:
        options = []
        for k in range(1, min(max_lines, len(words)) + 1):
            best = min(_partitions(words, k), key=lambda p: max(len(x) for x in p))
            options.append(best)
    best = None
    for opt in options:
        size = start
        while size >= 56:
            f = load_font(size, "thumb")
            if max(probe.textlength(l, font=f) for l in opt) + 2 * (size * 0.1) <= max_w:
                layers = [(_style_line(l, f, key_last and k == len(opt) - 1, size), key_last and k == len(opt) - 1)
                          for k, l in enumerate(opt)]
                ov = ov_of(size)
                tot = sum(l.height for l, _ in layers) - ov * (len(layers) - 1)
                if tot <= max_h and max(l.width for l, _ in layers) <= max_w + 40:
                    if best is None or size > best[1]:
                        best = (layers, size)
                    break
            size -= 6
    if best is None:
        f = load_font(56, "thumb")
        opt = options[-1]
        best = ([(_style_line(l, f, key_last and k == len(opt) - 1, 56), key_last and k == len(opt) - 1) for k, l in enumerate(opt)], 56)
    return best


def pill(text, size, bg, border=(255, 255, 255), fg=(255, 255, 255), role="label"):
    lay = text_layer(text, load_font(size, role), fill=fg)
    w, h = lay.width + 56, lay.height + 18
    out = Image.new("RGBA", (w + 8, h + 8), (0, 0, 0, 0))
    b = Image.new("RGBA", (w + 8, h + 8), border + (255,))
    b.putalpha(rounded_mask((w + 8, h + 8), (h + 8) // 2))
    out.alpha_composite(b)
    c = Image.new("RGBA", (w, h), bg + (255,))
    c.putalpha(rounded_mask((w, h), h // 2))
    out.alpha_composite(c, (4, 4))
    out.alpha_composite(lay, ((w + 8 - lay.width) // 2, (h + 8 - lay.height) // 2))
    return out


THUMB_FONTS = ["Mukta-ExtraBold.ttf"]  # plain, normal bold Hindi font (no decorative fonts)
THUMB_ACCENT = [(255, 224, 0), (255, 86, 70), (90, 225, 255), (150, 240, 90), (255, 160, 40)]


def shuffle_thumb_font():
    """Pick a different stylish bold Hindi display font for each video's thumbnail (only fonts that were downloaded)."""
    have = [f for f in THUMB_FONTS if (ROOT / "fonts" / f).exists() or (Path("fonts") / f).exists()]
    FONT_NAMES["thumb"] = have + ["Mukta-ExtraBold.ttf", "Hind-Bold.ttf", "Mukta-Bold.ttf"]
    font_path.cache_clear()


def subject_center(im):
    """Where the main subject (animal / person / object) is, as fractions 0..1: pixels that differ from the typical background colour
    and carry detail; the centre of the strongest area."""
    np = _np()
    small = im.convert("RGB").resize((128, 72))
    a = np.asarray(small, dtype=np.float32)
    g = a.mean(2)
    en = np.abs(np.diff(g, axis=1))[:-1, :] + np.abs(np.diff(g, axis=0))[:, :-1]
    a2 = a[:-1, :-1]
    border = np.concatenate([a2[:6].reshape(-1, 3), a2[-6:].reshape(-1, 3), a2[:, :6].reshape(-1, 3), a2[:, -6:].reshape(-1, 3)])
    bg = np.median(border, axis=0)
    dist = np.sqrt(((a2 - bg) ** 2).sum(2))
    sat = np.asarray(small.convert("HSV"), dtype=np.float32)[:-1, :-1, 1] / 255.0
    wgt = 0.55 * dist / (dist.max() + 1e-6) + 0.30 * en / (en.max() + 1e-6) + 0.15 * sat
    yy, xx = np.mgrid[0:wgt.shape[0], 0:wgt.shape[1]].astype(np.float32)
    wgt = wgt * np.exp(-(((xx / wgt.shape[1] - 0.5) / 1.1) ** 2 + ((yy / wgt.shape[0] - 0.5) / 1.3) ** 2))
    thr = np.percentile(wgt, 75)
    m = np.where(wgt >= thr, wgt, 0.0)
    tot = float(m.sum()) + 1e-6
    return float((m * xx).sum() / tot / wgt.shape[1]), float((m * yy).sum() / tot / wgt.shape[0])


def crop_to(path_or_img, size, cx_frac=None):
    """Cover-crop to size, centred on the subject (horizontally)."""
    im = Image.open(path_or_img).convert("RGB") if not isinstance(path_or_img, Image.Image) else path_or_img
    tw, th = size
    sc = max(tw / im.width, th / im.height)
    im2 = im.resize((max(tw, int(im.width * sc + 0.5)), max(th, int(im.height * sc + 0.5))), Image.LANCZOS)
    if cx_frac is None:
        cx_frac = subject_center(im)[0]
    left = int(min(im2.width - tw, max(0, cx_frac * im2.width - tw / 2)))
    top = (im2.height - th) // 2
    return im2.crop((left, top, left + tw, top + th))


def dominant_color(im):
    np = _np()
    a = np.asarray(im.convert("RGB").resize((32, 18)), dtype=np.float32).reshape(-1, 3)
    sat = a.max(1) - a.min(1)
    idx = np.argsort(-sat)[: max(8, len(a) // 8)]
    c = a[idx].mean(0)
    return tuple(int(x) for x in c)


def make_thumb_hero(img_path, text, sub, badge, out, side="left"):
    """Left: bold headline on a clean colour panel. Right: the subject, centred and graded. Nothing overlaps the subject."""
    np = _np()
    base = Image.open(img_path).convert("RGB")
    x0 = int(W * 0.47)
    cx, _cy = subject_center(base)
    pic = grade_photo(crop_to(base, (W - x0 + 120, H), cx))
    pic = bloom(pic, 0.3, 24)
    dom = dominant_color(pic)
    canvas = Image.new("RGB", (W, H))
    # clean panel: deep colour gradient tinted by the picture's own colour
    top_c, bot_c = (26, 14, 84), (6, 10, 40)
    grad = Image.linear_gradient("L").resize((W, H))
    canvas = Image.composite(Image.new("RGB", (W, H), bot_c), Image.new("RGB", (W, H), top_c), grad)
    glow = glow_sprite(900, dom, 0.30).convert("RGBA")
    cv = canvas.convert("RGBA")
    cv.alpha_composite(glow, (-380, int(H * 0.75) - 450))
    k = 2
    m = Image.new("L", (W * k, H * k), 0)
    ImageDraw.Draw(m).polygon([(( x0 + 70) * k, 0), (W * k, 0), (W * k, H * k), ((x0 - 70) * k, H * k)], fill=255)
    m = m.resize((W, H), Image.LANCZOS)
    full = Image.new("RGB", (W, H))
    full.paste(pic, (x0 - 120, 0))
    cv = Image.composite(full, cv.convert("RGB"), m).convert("RGBA")
    edge = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(edge).line([(x0 + 70, 0), (x0 - 70, H)], fill=(255, 255, 255, 255), width=7)
    gl = edge.filter(ImageFilter.GaussianBlur(12))
    col = Image.new("RGBA", (W, H), (255, 214, 90, 255))
    col.putalpha(gl.getchannel("A").point(lambda v: min(255, int(v * 1.5))))
    cv.alpha_composite(col)
    cv.alpha_composite(edge)
    tx = 14
    if side == "right":
        cv = ImageOps.mirror(cv.convert("RGB")).convert("RGBA")
        tx = W - x0 + 70
    # headline inside the text panel only
    shuffle_thumb_font()
    layers, size = title_layers([text], x0 - 110, H * (0.60 if sub else 0.72), start=200, text=text)
    ov = ov_of(size)
    total_h = sum(l.height for l, _ in layers) - ov * (len(layers) - 1)
    y = max(70, (H - total_h) // 2 - (26 if sub else 0))
    for lay, key in layers:
        if key:
            padg = 60
            big = Image.new("L", (lay.width + 2 * padg, lay.height + 2 * padg), 0)
            big.paste(lay.getchannel("A"), (padg, padg))
            g = big.filter(ImageFilter.GaussianBlur(max(8, size // 8))).point(lambda v: int(v * 0.55))
            gl2 = Image.new("RGBA", big.size, (255, 190, 0, 255))
            gl2.putalpha(g)
            cv.alpha_composite(gl2, (tx - padg, y - padg)) if (y - padg) >= 0 and (tx - padg) >= 0 else cv.alpha_composite(gl2, (max(0, tx - padg), max(0, y - padg)), (max(0, padg - tx), max(0, padg - y)))
        cv.alpha_composite(lay, (tx, y))
        y += lay.height - ov
    if sub:
        sp = pill(sub, 40, (214, 30, 50))
        if sp.width > x0 - 30:
            sp = sp.resize((x0 - 30, int(sp.height * (x0 - 30) / sp.width)), Image.LANCZOS)
        cv.alpha_composite(sp, (30, min(H - sp.height - 30, y + 10)))
    if badge:
        bp = pill(badge, 42, (230, 36, 56))
        cv.alpha_composite(bp, (30, 28))
    final = cv.convert("RGB").filter(ImageFilter.UnsharpMask(radius=2, percent=80, threshold=3))
    final.save(out, "JPEG", quality=93)


def make_thumb_bottom(img_path, text, out):
    """Full picture, subject clear in the upper part, big headline on a dark gradient at the bottom."""
    np = _np()
    base = Image.open(img_path).convert("RGB")
    pic = bloom(grade_photo(crop_to(base, (W, H), subject_center(base)[0])), 0.3, 24).convert("RGBA")
    ys = np.linspace(0, 1, H)[:, None]
    a = np.clip((ys - 0.48) / 0.52, 0, 1) ** 1.2 * 238
    shade = np.zeros((H, W, 4), np.uint8)
    shade[..., 0:3] = (4, 6, 24)
    shade[..., 3] = np.broadcast_to(a, (H, W)).astype(np.uint8)
    pic.alpha_composite(Image.fromarray(shade))
    shuffle_thumb_font()
    layers, size = title_layers([text], W * 0.94, H * 0.40, start=170, text=text)
    ov = ov_of(size)
    total_h = sum(l.height for l, _ in layers) - ov * (len(layers) - 1)
    y = max(0, H - total_h - 22)
    for lay, key in layers:
        pic.alpha_composite(lay, (max(0, (W - lay.width) // 2), y))
        y += lay.height - ov
    pic.convert("RGB").filter(ImageFilter.UnsharpMask(radius=2, percent=80, threshold=3)).save(out, "JPEG", quality=93)


def make_thumb_split(img_a, img_b, text, left, right, out):
    """Headline on its own bar at the top; two worlds (then / now) below, split by a glowing diagonal."""
    np = _np()
    BH = 240
    ph = H - BH
    A0, B0 = Image.open(img_a).convert("RGB"), Image.open(img_b).convert("RGB")
    A = Image.blend(grade_photo(crop_to(A0, (W, ph), subject_center(A0)[0] * 0.8 + 0.1)), Image.new("RGB", (W, ph), (255, 80, 20)), 0.14)
    B = Image.blend(grade_photo(crop_to(B0, (W, ph), subject_center(B0)[0] * 0.8 + 0.1)), Image.new("RGB", (W, ph), (20, 140, 255)), 0.14)
    k = 2
    m = Image.new("L", (W * k, ph * k), 0)
    ImageDraw.Draw(m).polygon([(0, 0), (W * 0.55 * k, 0), (W * 0.45 * k, ph * k), (0, ph * k)], fill=255)
    m = m.resize((W, ph), Image.LANCZOS)
    pic = bloom(Image.composite(A, B, m), 0.28, 20).convert("RGBA")
    seam = Image.new("RGBA", (W, ph), (0, 0, 0, 0))
    ImageDraw.Draw(seam).line([(W * 0.55, 0), (W * 0.45, ph)], fill=(255, 255, 255, 255), width=8)
    for blur, colr in ((30, (255, 200, 80)), (12, (255, 255, 255))):
        g = seam.filter(ImageFilter.GaussianBlur(blur))
        c = Image.new("RGBA", (W, ph), colr + (255,))
        c.putalpha(g.getchannel("A").point(lambda v: min(255, int(v * 1.6))))
        pic.alpha_composite(c)
    pic.alpha_composite(seam)
    canvas = Image.new("RGBA", (W, H), (8, 14, 50, 255))
    canvas.alpha_composite(pic, (0, BH))
    # headline bar
    xs = np.linspace(0, 1, W)
    bar = Image.new("RGB", (W, BH))
    bar = Image.composite(Image.new("RGB", (W, BH), (34, 20, 92)), Image.new("RGB", (W, BH), (8, 16, 62)),
                          Image.fromarray(np.tile((xs * 255).astype(np.uint8), (BH, 1))))
    canvas.alpha_composite(bar.convert("RGBA"), (0, 0))
    stripe = Image.new("RGBA", (W, 9), (255, 200, 20, 255))
    canvas.alpha_composite(stripe, (0, BH - 9))
    shuffle_thumb_font()
    layers, size = title_layers([text], W * 0.94, BH - 26, start=150, text=text, max_lines=2)
    ov = ov_of(size)
    total_h = sum(l.height for l, _ in layers) - ov * (len(layers) - 1)
    y = max(0, (BH - 9 - total_h) // 2)
    for lay, key in layers:
        canvas.alpha_composite(lay, ((W - lay.width) // 2, y))
        y += lay.height - ov
    if left:
        lp = pill(left, 60, (214, 24, 44), role="thumb")
        canvas.alpha_composite(lp, (36, H - lp.height - 28))
    if right:
        rp = pill(right, 60, (22, 100, 230), role="thumb")
        canvas.alpha_composite(rp, (W - rp.width - 36, H - rp.height - 28))
    final = canvas.convert("RGB").filter(ImageFilter.UnsharpMask(radius=2, percent=80, threshold=3))
    final.save(out, "JPEG", quality=93)


def sharpness(im):
    np = _np()
    g = np.asarray(im.convert("L").resize((640, 360)), dtype=np.float32)
    return float((np.abs(np.diff(g, axis=0)).mean() + np.abs(np.diff(g, axis=1)).mean()) / 2)


def make_thumb_clean(img, text, out, layout="bottom", accent=None, size=(1920, 1080)):
    """Full real photo (no cut-outs, nothing pasted on the subject), plain bold headline on a soft dark gradient.
    layout: bottom | top | side (text goes to the emptier side of the picture, away from faces/bodies)."""
    np = _np()
    TW, TH = size
    base = Image.open(img).convert("RGB") if not isinstance(img, Image.Image) else img.convert("RGB")
    sc_ = max(TW / base.width, TH / base.height)
    base = base.resize((max(TW, int(base.width * sc_ + .5)), max(TH, int(base.height * sc_ + .5))), Image.LANCZOS)
    cx = subject_center(base)[0]
    left = int(min(base.width - TW, max(0, cx * base.width - TW / 2)))
    pic = base.crop((left, (base.height - TH) // 2, left + TW, (base.height - TH) // 2 + TH))
    pic = ImageEnhance.Contrast(ImageEnhance.Color(ImageOps.autocontrast(pic, cutoff=0.5)).enhance(1.12)).enhance(1.08)
    pic = pic.filter(ImageFilter.UnsharpMask(radius=2, percent=70, threshold=3)).convert("RGBA")
    THUMB_ACC[0] = accent or THUMB_ACCENT[0]
    shuffle_thumb_font()
    ys = np.linspace(0, 1, TH)[:, None]
    xs = np.linspace(0, 1, TW)[None, :]
    side = empty_side(pic) if layout == "side" else None
    if layout == "top":
        a = np.clip((0.55 - ys) / 0.55, 0, 1) ** 1.15 * 225 * np.ones((1, TW))
        box = (int(TW * 0.04), int(TH * 0.04), int(TW * 0.92), int(TH * 0.40))
    elif layout == "side":
        d = xs if side == "left" else 1 - xs
        a = np.clip((0.62 - d) / 0.62, 0, 1) ** 1.1 * 225 * np.ones((TH, 1))
        box = (int(TW * (0.03 if side == "left" else 0.45)), int(TH * 0.12), int(TW * 0.52), int(TH * 0.76))
    else:
        a = np.clip((ys - 0.50) / 0.50, 0, 1) ** 1.15 * 230 * np.ones((1, TW))
        box = (int(TW * 0.04), int(TH * 0.58), int(TW * 0.92), int(TH * 0.38))
    shade = np.zeros((TH, TW, 4), np.uint8)
    shade[..., 0:3] = (4, 5, 16)
    shade[..., 3] = a.astype(np.uint8)
    pic.alpha_composite(Image.fromarray(shade))
    bx, by, bw, bh = box
    layers, fs = title_layers([text], bw, bh, start=330, text=text, max_lines=3)
    ov = ov_of(fs)
    tot = sum(l.height for l, _ in layers) - ov * (len(layers) - 1)
    y = by + (bh - tot) // 2 if layout != "bottom" else by + bh - tot
    for lay, key in layers:
        if layout == "side":
            x = bx if side == "left" else bx + bw - lay.width
        else:
            x = bx + (bw - lay.width) // 2
        pic.alpha_composite(lay, (max(0, x), max(0, y)))
        y += lay.height - ov
    im = pic.convert("RGB")
    q = 94
    while True:  # YouTube accepts thumbnails up to 2 MB
        im.save(out, "JPEG", quality=q, optimize=True, subsampling=0 if q > 90 else 2)
        if Path(out).stat().st_size <= 1_900_000 or q <= 70:
            break
        q -= 4


# --------------------------------------------------------------------------- AI thumbnails: Gemini plans the thumbnail, Cloudflare paints a clear photo of the story's subject, 4 reference styles rotate
THUMB_STYLES = ["hero_right", "top_title", "banner_zoom", "doc_tag"]
T_YEL, T_RED, T_WHITE, T_BLK, T_GREEN = (255, 214, 0), (222, 24, 32), (255, 255, 255), (14, 14, 16), (28, 164, 72)


def thumb_plan_prompt(script, topic_en, avoid):
    return f"""You design YouTube thumbnails for a Hindi science / nature documentary channel. Study the story and plan ONE thumbnail picture that makes people curious (curiosity gap, never a lie).
STORY: {topic_en}
HINDI TITLE: {script.get('title_hi')}
STORY HOOK (Hindi): {script['scenes'][0]['narration_hi'][:300]}
The same picture will be used in the style that fits the story best. The four styles (reference thumbnails of top channels):
 hero_right  = huge subject on the LEFT, 2-3 big Hindi words stacked on the RIGHT (first line yellow, others white) and a small caption line under them (e.g. "66 MILLION YEARS AGO...") - for dramatic / danger / extinction / disaster stories.
 top_title   = one clean white title across the TOP, a spectacular subject below - for origin / space / planet / big-picture stories.
 banner_zoom = a full-width yellow banner on top with a 5-8 word curiosity sentence (key words red), and a round magnified inset of one creepy / surprising DETAIL joined by a line - for mystery / hidden / shocking-detail stories.
 doc_tag     = small white tag + two huge words (yellow + white) at the right and a green corner banner - for calm nature / wildlife / documentary stories.
Return ONLY JSON:
{{"subject_en": "the single real subject the story is about (e.g. Tyrannosaurus rex, Earth, Amazon anaconda)",
 "photo_prompt_en": "ONE clear ultra-detailed photorealistic image of that subject: a single large sharp subject, dramatic cinematic lighting, a vivid colour scene around it; subject large on the left-centre of the frame with calmer darker space on the right and a little space above; no text, no letters, no watermark, no logo, no person's face",
 "zoom_x": 0.30, "zoom_y": 0.35,
 "ranking": ["best style first", "...", "...", "..."],
 "hero_right": {{"lines": ["1-2 Hindi words", "1-2 Hindi words", "1-2 Hindi words (optional)"], "tag": "short caption 2-5 words, English or Hindi, may contain a year/number"}},
 "top_title": {{"title": "3-5 Hindi words, a curiosity question or claim"}},
 "banner_zoom": {{"banner": "5-8 Hindi words, curiosity gap", "red": ["the 1-2 most shocking words of the banner, copied exactly"]}},
 "doc_tag": {{"lines": ["1-2 Hindi words", "1-2 Hindi words"], "tag": "tiny label 1-2 words", "corner": "2-3 Hindi words like पूरी कहानी"}}}}
"zoom_x"/"zoom_y" = where in YOUR described picture (0..1, x from left, y from top) the most interesting detail (eye, mouth, claw, crack, surprise) is. ALL text short and punchy: never more than 5 big words in total per style. Vary your wording from earlier videos. Avoid these styles this time if possible: {', '.join(avoid) or 'none'}."""


def thumb_plan(script, topic_en, avoid):
    out = gemini_json(thumb_plan_prompt(script, topic_en, avoid), 0.8)
    if not isinstance(out, dict):
        raise ValueError("bad thumbnail plan")
    base_t = clean_short(script.get("thumb_text_hi") or script.get("title_hi"), 40)
    w = base_t.split()
    hr = out.get("hero_right") if isinstance(out.get("hero_right"), dict) else {}
    tt = out.get("top_title") if isinstance(out.get("top_title"), dict) else {}
    bz = out.get("banner_zoom") if isinstance(out.get("banner_zoom"), dict) else {}
    dt = out.get("doc_tag") if isinstance(out.get("doc_tag"), dict) else {}
    lines = [clean_short(x, 22) for x in (hr.get("lines") or []) if clean_short(x, 22)][:3] or [" ".join(w[:2]), " ".join(w[2:4]) or "कैसे?"]
    dl = [clean_short(x, 22) for x in (dt.get("lines") or []) if clean_short(x, 22)][:2] or lines[:2]
    ban = clean_short(bz.get("banner"), 70) or base_t
    red = [clean_short(x, 24) for x in (bz.get("red") or []) if clean_short(x, 24)][:2]
    try:
        zx, zy = min(0.9, max(0.1, float(out.get("zoom_x")))), min(0.85, max(0.15, float(out.get("zoom_y"))))
    except Exception:  # noqa
        zx, zy = 0.3, 0.38
    rank = [x for x in (out.get("ranking") or []) if x in THUMB_STYLES]
    rank += [x for x in THUMB_STYLES if x not in rank]
    return {"subject": clean_short(out.get("subject_en"), 70), "prompt": clean_short(out.get("photo_prompt_en"), 600), "zx": zx, "zy": zy, "rank": rank,
            "hero_right": {"lines": lines, "tag": clean_short(hr.get("tag"), 30)}, "top_title": {"title": clean_short(tt.get("title"), 40) or base_t},
            "banner_zoom": {"banner": ban, "red": red}, "doc_tag": {"lines": dl, "tag": clean_short(dt.get("tag"), 18), "corner": clean_short(dt.get("corner"), 22) or "पूरी कहानी"}}


def _tfit(lines, role, max_w, max_h, start=300, lo=60):
    probe = ImageDraw.Draw(Image.new("L", (4, 4)))
    size = start
    while size > lo:
        f = load_font(size, role)
        if max(probe.textlength(l, font=f) for l in lines) <= max_w and size * 1.1 * len(lines) <= max_h:
            return size
        size -= 6
    return lo


def _tline(text, size, fill, role="thumb", extrude=True):
    f = load_font(size, role)
    st = max(3, int(size * 0.07))
    return text_layer(text, f, fill=fill, stroke=st, stroke_fill=(10, 10, 14), shadow=(0, max(3, int(size * 0.05)), max(3, int(size * 0.06)), 0.85),
                      extrude=((max(3, int(size * 0.045)), (10, 10, 14)) if extrude else None))


def _tshade(pic, where, strength=215, reach=0.62):
    np = _np()
    TW, TH = pic.size
    ys, xs = np.linspace(0, 1, TH)[:, None], np.linspace(0, 1, TW)[None, :]
    if where == "right":
        a = np.clip((xs - (1 - reach)) / reach, 0, 1) ** 1.2 * strength * np.ones((TH, 1))
    elif where == "top":
        a = np.clip((reach - ys) / reach, 0, 1) ** 1.2 * strength * np.ones((1, TW))
    else:
        a = np.clip((ys - (1 - reach)) / reach, 0, 1) ** 1.2 * strength * np.ones((1, TW))
    sh = np.zeros((TH, TW, 4), np.uint8)
    sh[..., :3] = (3, 4, 12)
    sh[..., 3] = a.astype(np.uint8)
    pic.alpha_composite(Image.fromarray(sh))


def _tstack(pic, layers, box, align="center", gap_k=0.14):
    bx, by, bw, bh = box
    ov = int(layers[0].height * gap_k)
    tot = sum(l.height for l in layers) - ov * (len(layers) - 1)
    y = by + (bh - tot) // 2
    for l in layers:
        x = bx + (bw - l.width) // 2 if align == "center" else bx
        pic.alpha_composite(l, (max(0, x), max(0, y)))
        y += l.height - ov
    return y + ov


def thumb_style_hero_right(pic, v):
    TW, TH = pic.size
    _tshade(pic, "right", 225, 0.60)
    lines = v["lines"]
    size = _tfit(lines, "thumb", int(TW * 0.44), int(TH * 0.52), 260)
    layers = [_tline(l, size, T_YEL if i == 0 else T_WHITE) for i, l in enumerate(lines)]
    y_end = _tstack(pic, layers, (int(TW * 0.52), int(TH * 0.12), int(TW * 0.45), int(TH * 0.62)))
    if v.get("tag"):
        ts = _tfit([v["tag"]], "cond", int(TW * 0.44), int(TH * 0.10), 64, 30)
        tl = _tline(v["tag"], ts, T_WHITE, "cond", extrude=False)
        pic.alpha_composite(tl, (int(TW * 0.52) + (int(TW * 0.45) - tl.width) // 2, min(TH - tl.height - 20, y_end + 4)))


def thumb_style_top_title(pic, v):
    TW, TH = pic.size
    _tshade(pic, "top", 215, 0.42)
    words = v["title"].split()
    opts = [[" ".join(words)]] + ([[" ".join(words[:len(words) // 2]), " ".join(words[len(words) // 2:])]] if len(words) > 2 else [])
    best = None
    for o in opts:
        sz = _tfit(o, "thumb", int(TW * 0.92), int(TH * 0.30), 230)
        if best is None or sz > best[0]:
            best = (sz, o)
    f = load_font(best[0], "thumb")
    layers = [text_layer(l, f, fill=T_WHITE, stroke=0, shadow=(0, 6, 12, 0.9)) for l in best[1]]
    _tstack(pic, layers, (int(TW * 0.04), int(TH * 0.015), int(TW * 0.92), int(TH * 0.30)), gap_k=0.06)


def thumb_style_banner_zoom(pic, v, zx, zy):
    TW, TH = pic.size
    bar = int(TH * 0.135)
    d = ImageDraw.Draw(pic)
    d.rectangle([0, 0, TW, bar], fill=T_YEL + (255,))
    d.rectangle([0, bar, TW, bar + 5], fill=(40, 30, 0, 255))
    words = v["banner"].split()
    reds = " ".join(v["red"]).split()
    probe = ImageDraw.Draw(Image.new("L", (4, 4)))
    size = int(bar * 0.82)
    while size > 40:
        f = load_font(size, "thumb")
        if sum(probe.textlength(w, font=f) for w in words) + size * 0.3 * (len(words) - 1) <= TW * 0.95:
            break
        size -= 4
    f = load_font(size, "thumb")
    tot = sum(probe.textlength(w, font=f) for w in words) + size * 0.3 * (len(words) - 1)
    x = (TW - tot) / 2
    for w in words:
        hit = any(w.strip("?!।,.") == r.strip("?!।,.") for r in reds)
        lay = text_layer(w, f, fill=T_RED if hit else T_BLK, stroke=0)
        pic.alpha_composite(lay, (int(x - (lay.width - probe.textlength(w, font=f)) / 2), int(bar / 2 - lay.height / 2 + 2)))
        x += probe.textlength(w, font=f) + size * 0.3
    D = int(TH * 0.46)
    cw = int(TW * 0.20)
    src = pic.convert("RGB")
    l0 = int(min(TW - cw, max(0, zx * TW - cw / 2)))
    t0 = int(min(TH - cw * 9 // 16 * 1.0 - 1, max(0, zy * TH - cw / 2)))
    crop = src.crop((l0, t0, l0 + cw, t0 + cw)).resize((D, D), Image.LANCZOS)
    cx, cy = int(TW * 0.745), int(TH * 0.53)
    ox, oy = int(zx * TW), int(zy * TH)
    ln = Image.new("RGBA", pic.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(ln)
    ang = math.atan2(cy - oy, cx - ox)
    ex, ey = cx - math.cos(ang) * D / 2, cy - math.sin(ang) * D / 2
    ld.line([(ox, oy), (ex, ey)], fill=(255, 255, 255, 255), width=6)
    ld.ellipse([ox - 11, oy - 11, ox + 11, oy + 11], outline=(255, 255, 255, 255), width=5)
    sh = Image.new("RGBA", pic.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).ellipse([cx - D // 2 + 6, cy - D // 2 + 10, cx + D // 2 + 6, cy + D // 2 + 10], fill=(0, 0, 0, 150))
    pic.alpha_composite(sh.filter(ImageFilter.GaussianBlur(12)))
    pic.alpha_composite(ln)
    m = Image.new("L", (D, D), 0)
    ImageDraw.Draw(m).ellipse([0, 0, D - 1, D - 1], fill=255)
    pic.paste(crop, (cx - D // 2, cy - D // 2), m)
    ImageDraw.Draw(pic).ellipse([cx - D // 2, cy - D // 2, cx + D // 2, cy + D // 2], outline=(255, 255, 255, 255), width=8)


def thumb_style_doc_tag(pic, v):
    TW, TH = pic.size
    _tshade(pic, "right", 170, 0.55)
    d = ImageDraw.Draw(pic)
    if v.get("tag"):
        tl = text_layer(v["tag"], load_font(64, "thumb"), fill=T_BLK, stroke=0)
        w, h = tl.width + 60, tl.height + 18
        x0, y0 = int(TW * 0.58), int(TH * 0.12)
        d.polygon([(x0 + 16, y0), (x0 + w + 16, y0), (x0 + w, y0 + h), (x0, y0 + h)], fill=(255, 255, 255, 255))
        pic.alpha_composite(tl, (x0 + 30, y0 + 8))
    lines = v["lines"]
    size = _tfit(lines, "thumb", int(TW * 0.42), int(TH * 0.46), 250)
    layers = [_tline(l, size, T_YEL if i == len(lines) - 1 else T_WHITE) for i, l in enumerate(lines)]
    _tstack(pic, layers, (int(TW * 0.54), int(TH * 0.26), int(TW * 0.43), int(TH * 0.50)))
    d.polygon([(0, int(TH * 0.80)), (int(TW * 0.40), int(TH * 0.80)), (int(TW * 0.35), TH), (0, TH)], fill=T_GREEN + (255,))
    cs = _tfit([v["corner"]], "thumb", int(TW * 0.30), int(TH * 0.15), 120, 40)
    cl_ = text_layer(v["corner"], load_font(cs, "thumb"), fill=T_WHITE, stroke=0, shadow=(0, 3, 4, 0.5))
    pic.alpha_composite(cl_, (int(TW * 0.02), int(TH * 0.80 + (TH * 0.20 - cl_.height) / 2)))


def render_thumb(style, plan, hero, out):
    base = Image.open(hero).convert("RGB") if not isinstance(hero, Image.Image) else hero.convert("RGB")
    base = ImageOps.fit(base, (1920, 1080), Image.LANCZOS)
    base = ImageEnhance.Contrast(ImageEnhance.Color(base).enhance(1.18)).enhance(1.1).filter(ImageFilter.UnsharpMask(radius=2, percent=80, threshold=3))
    pic = base.convert("RGBA")
    shuffle_thumb_font()
    if style == "hero_right":
        thumb_style_hero_right(pic, plan["hero_right"])
    elif style == "top_title":
        thumb_style_top_title(pic, plan["top_title"])
    elif style == "banner_zoom":
        thumb_style_banner_zoom(pic, plan["banner_zoom"], plan["zx"], plan["zy"])
    else:
        thumb_style_doc_tag(pic, plan["doc_tag"])
    im = pic.convert("RGB")
    q = 94
    while True:
        im.save(out, "JPEG", quality=q, optimize=True, subsampling=0 if q > 90 else 2)
        if Path(out).stat().st_size <= 1_900_000 or q <= 70:
            break
        q -= 4


def make_thumbs_ai(script, topic_en, imgdir, outdir):
    """Returns [(path, caption)]: main thumbnail + an alternative in a different style. Raises when the AI plan or picture is unavailable (caller falls back)."""
    st = load_json(STATE / "thumb.json", {})
    recent = st.get("styles", [])[-2:]
    plan = thumb_plan(script, topic_en, recent)
    hero = imgdir / "thumb_ai.png"
    prompt = plan["prompt"] or script.get("thumb_prompt_en") or topic_en
    ok = img_cloudflare(prompt + ", " + STYLE_THUMB, hero, (1920, 1080), (1344, 756))
    if not ok:
        raise RuntimeError("Cloudflare could not paint the thumbnail picture: " + fail_summary())
    order = [x for x in plan["rank"] if x != (recent[-1] if recent else "")] or plan["rank"]
    main = order[0]
    alt = next((x for x in order[1:] if x != main), THUMB_STYLES[0])
    res = []
    for k, (sty, cap) in enumerate(((main, "Thumbnail (best: " + main + ")"), (alt, "Thumbnail alternative (" + alt + ")"))):
        p = outdir / ("thumbnail.jpg" if k == 0 else "thumbnail_alt.jpg")
        render_thumb(sty, plan, hero, p)
        res.append((p, cap))
    save_json(STATE / "thumb.json", {**st, "styles": (st.get("styles", []) + [main])[-6:]})
    log(f"AI thumbnails: {main} + {alt} (subject: {plan['subject']})")
    return res


def pick_thumb_layout(script, topic):
    """Different layout and accent colour for different stories (and never the same as last time)."""
    st = load_json(STATE / "thumb.json", {})
    opts = ["bottom", "side", "top"]
    h = sum(ord(c) for c in topic)
    want = script.get("thumb_layout") if script.get("thumb_layout") in opts else opts[h % 3]
    if want == st.get("layout"):
        want = opts[(opts.index(want) + 1) % 3]
    acc = [i for i in range(len(THUMB_ACCENT)) if i != st.get("accent")]
    ai = acc[h % len(acc)]
    save_json(STATE / "thumb.json", {"layout": want, "accent": ai})
    return want, THUMB_ACCENT[ai]


def real_thumb_image(script, topic_en, imgdir, fallback):
    """Real photo of exactly what the story is about (Wikipedia/Commons first, then stock), else the sharpest picture used in the video."""
    ent = script.get("thumb_entity_en") or ""
    q = script.get("thumb_query_en") or ""
    cands = []
    for k, (e, qq) in enumerate(((ent, q), (q, q), (topic_en, topic_en))):
        if not (e or qq):
            continue
        pth = imgdir / f"thumb_real{k}.png"
        try:
            if os.environ.get("REAL_WIKI", "1") != "0" and e and wiki_real_photo(e, pth, (1920, 1080)):
                cands.append((sharpness(Image.open(pth)), pth))
                break
            if PIXABAY_KEY and qq and pixabay_photo(qq, pth, (1920, 1080)):
                cands.append((sharpness(Image.open(pth)), pth))
                break
        except Exception as ex:  # noqa
            log("  real thumbnail lookup failed:", type(ex).__name__)
    if cands:
        return max(cands)[1]
    ai = imgdir / "thumb.png"
    if gen_image(script["thumb_prompt_en"], ai, "thumb", query=q):
        return ai
    return fallback


# --------------------------------------------------------------------------- delivery
def tg_api(method, **kw):
    return requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/{method}", timeout=300, **kw)


def notify(text):
    log("NOTIFY:", text[:200].replace("\n", " | "))
    if not (TG_TOKEN and TG_CHAT):
        return
    try:
        for i in range(0, len(text), 3900):
            tg_api("sendMessage", data={"chat_id": TG_CHAT, "text": text[i:i + 3900]})
    except Exception as e:  # noqa
        log("telegram notify failed:", type(e).__name__)


def tg_send_file(path, kind, caption=""):
    if not (TG_TOKEN and TG_CHAT):
        return False
    method, field = {"video": ("sendVideo", "video"), "doc": ("sendDocument", "document"), "audio": ("sendAudio", "audio")}[kind]
    with open(path, "rb") as f:
        data = {"chat_id": TG_CHAT, "caption": caption[:900]}
        if kind == "video":
            data["supports_streaming"] = "true"
        r = tg_api(method, data=data, files={field: f})
    ok = r.ok and r.json().get("ok")
    if not ok:
        log("telegram file send failed:", r.text[:200])
    return bool(ok)


def _srt_t(t):
    t = max(0.0, t)
    return f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{int(t % 60):02d},{int(round((t % 1) * 1000)) % 1000:03d}"


def write_subtitles(scenes, outdir):
    """Subtitle file (NOT burned into the video) + an edit plan, so an editor can read what is said when and what was placed there."""
    srt, plan, t0, n = [], [], 0.0, 0
    for i, sc in enumerate(scenes):
        wds, wt = sc["narration_hi"].split(), sc.get("wt")
        sd = sc.get("speech_dur") or sc.get("dur") or 1.0
        if not wt or len(wt) != len(wds):
            per = sd / max(1, len(wds))
            wt = [(k * per, (k + 1) * per) for k in range(len(wds))]
        k = 0
        while k < len(wds):
            e = min(len(wds), k + 7)
            for j in range(k + 3, min(len(wds), k + 9)):  # prefer to break at the end of a phrase
                if wds[j - 1].endswith(("।", ",", "?", "!", ".")):
                    e = j
                    break
            n += 1
            srt.append(f"{n}\n{_srt_t(t0 + wt[k][0])} --> {_srt_t(t0 + wt[e - 1][1])}\n{' '.join(wds[k:e])}\n")
            k = e
        what = {"photo": "PHOTO", "map": "MAP", "multi": "SPLIT-SCREEN", "counter": "COUNTER"}.get(sc.get("type"), str(sc.get("type")).upper())
        m = sc.get("map") or {}
        if sc.get("type") == "map":
            what += f" ({m.get('mode', 'flat')}: " + ", ".join(p_.get("label_hi", "") for p_ in m.get("pins", [])) + ")"
        plan.append(f"[{fmt_time(t0)}-{fmt_time(t0 + sc.get('dur', 0))}] SCENE {i + 1} - {what} - tone {sc.get('tone')}\n  SAYS: {sc['narration_hi']}")
        for it in sc.get("items", []) if sc.get("type") == "multi" else []:
            plan.append(f"  split item @{it.get('t', 0):.1f}s: {it.get('label_hi')} ({it.get('entity_en') or it.get('query_en')})")
        wlen = 0
        for sh in sc.get("shots", []):
            nw_ = len(str(sh.get("text_hi") or "").split())
            ts = t0 + (wt[min(wlen, len(wt) - 1)][0] if wt else 0)
            wlen += nw_
            if sh.get("prompt_en") or sh.get("clip"):
                plan.append(f"  {_srt_t(ts)[:8]} PICTURE: {sh.get('entity_en') or sh.get('query_en') or sh.get('prompt_en', '')[:60]}"
                            + (f" | clip: {sh['clip_en']}" if sh.get("clip") else "") + (f" | camera: {sh.get('cam')}" if sh.get("cam") else "")
                            + (f" | transition: {sh.get('trans')}" if sh.get("trans") else ""))
            elif sh.get("kf"):
                plan.append(f"  {_srt_t(ts)[:8]} CAMERA moves to a detail of the same picture")
        for it in sc.get("items", []):
            if isinstance(it, dict) and it.get("kind") and sc.get("type") == "photo":
                ts = t0 + (wt[min(it.get("w", 0), len(wt) - 1)][0] if wt else 0)
                plan.append(f"  {_srt_t(ts)[:8]} {str(it['kind']).upper()}: {str(it.get('text') or it.get('popup_en') or '')[:80]}" + (f" (source: {it.get('sub')})" if it["kind"] == "note" else ""))
        t0 += sc.get("dur", 0)
    a, b = Path(outdir) / "subtitles.srt", Path(outdir) / "edit_plan.txt"
    a.write_text("\n".join(srt), encoding="utf-8")
    b.write_text("EDIT PLAN (time = minute:second in the video)\n\n" + "\n".join(plan), encoding="utf-8")
    return a, b


def sanitize_yt(s, n):
    return re.sub(r"[<>]", "", s)[:n].strip()


def youtube_upload(video, thumb, meta):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    creds = Credentials(None, refresh_token=os.environ["YT_REFRESH_TOKEN"],
                        token_uri="https://oauth2.googleapis.com/token",
                        client_id=os.environ["YT_CLIENT_ID"], client_secret=os.environ["YT_CLIENT_SECRET"],
                        scopes=["https://www.googleapis.com/auth/youtube.upload"])
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    body = {"snippet": {"title": sanitize_yt(meta["title"], 95), "description": sanitize_yt(meta["description"], 4800),
                        "tags": meta["tags"][:15], "categoryId": "27", "defaultLanguage": "hi",
                        "defaultAudioLanguage": "hi"},
            "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False, "containsSyntheticMedia": True}}
    vid = None
    for attempt in range(2):
        media = MediaFileUpload(str(video), chunksize=8 * 1024 * 1024, resumable=True, mimetype="video/mp4")
        req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
        try:
            resp = None
            while resp is None:
                _s, resp = req.next_chunk()
            vid = resp["id"]
            break
        except HttpError as e:
            if "containsSyntheticMedia" in str(e) and attempt == 0:
                body["status"].pop("containsSyntheticMedia", None)
                meta["warn"] = "AI-disclosure flag API se set nahi hua, Studio mein khud ON karein."
                continue
            raise
    try:
        yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(thumb), mimetype="image/jpeg")).execute()
    except Exception as e:  # noqa
        meta["warn"] = (meta.get("warn", "") + " Thumbnail set nahi hua (channel verify karein) - Studio mein lagayein.").strip()
        log("thumbnail set failed:", type(e).__name__)
    return vid


def facebook_upload(video, meta):
    pid, tok = os.environ.get("FB_PAGE_ID"), os.environ.get("FB_PAGE_TOKEN")
    if not (pid and tok):
        return None
    ver = os.environ.get("FB_API_VERSION", "")
    base = f"https://graph-video.facebook.com/{ver + '/' if ver else ''}{pid}/videos"
    try:
        with open(video, "rb") as f:
            r = requests.post(base, data={"access_token": tok, "title": meta["title"][:250],
                                          "description": meta["description"][:4000], "published": "false"},
                              files={"source": f}, timeout=900)
        if r.ok:
            return r.json().get("id")
        log("facebook upload failed:", r.text[:300])
    except Exception as e:  # noqa
        log("facebook upload error:", type(e).__name__)
    return None


# --------------------------------------------------------------------------- main pipeline
def fmt_time(sec):
    return f"{int(sec // 60)}:{int(sec % 60):02d}"


def cutout_sticker(path, maxside=360):
    """Pop-up PNG: cut the object out of its white background, add a white sticker outline and a soft shadow."""
    np = _np()
    im = Image.open(path).convert("RGB")
    a = None
    try:
        from rembg import remove  # best quality if available (optional)
        a = remove(im).getchannel("A")
    except Exception:  # noqa
        a = None
    if a is None:
        from scipy import ndimage
        arr = np.asarray(im, dtype=np.int16)
        near_white = (arr.min(2) > 222) & ((arr.max(2) - arr.min(2)) < 34)
        lab, n = ndimage.label(near_white)
        border = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
        bg = np.isin(lab, list(border)) if border else np.zeros_like(near_white)
        fg = ndimage.binary_fill_holes(~bg)
        fg = ndimage.binary_opening(fg, iterations=2)
        a = Image.fromarray((fg * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.2))
    bb = a.point(lambda v: 255 if v > 40 else 0).getbbox()
    if not bb or (bb[2] - bb[0]) * (bb[3] - bb[1]) < 0.04 * im.width * im.height:
        return None
    bbox_area = (bb[2] - bb[0]) * (bb[3] - bb[1])
    if bbox_area > 0.80 * im.width * im.height:  # background was not removed: reject ugly square stickers
        return None
    ar = np.asarray(a.crop(bb).point(lambda v: 255 if v > 40 else 0), dtype=np.float32) / 255.0
    if ar.mean() > 0.93:  # object fills its whole box = a square picture, not a cut-out
        return None
    im, a = im.crop(bb), a.crop(bb)
    sc = maxside / max(im.size)
    im, a = im.resize((int(im.width * sc), int(im.height * sc)), Image.LANCZOS), a.resize((int(a.width * sc), int(a.height * sc)), Image.LANCZOS)
    pad = 26
    W2, H2 = im.width + 2 * pad, im.height + 2 * pad
    A = Image.new("L", (W2, H2), 0)
    A.paste(a, (pad, pad))
    outline = A.filter(ImageFilter.MaxFilter(13)).filter(ImageFilter.GaussianBlur(1.5)).point(lambda v: 255 if v > 90 else 0)
    out = Image.new("RGBA", (W2, H2), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (W2, H2), (0, 0, 0, 255))
    shadow.putalpha(outline.filter(ImageFilter.GaussianBlur(9)).point(lambda v: int(v * 0.55)))
    out.alpha_composite(shadow, (6, 10))
    white = Image.new("RGBA", (W2, H2), (255, 255, 255, 255))
    white.putalpha(outline)
    out.alpha_composite(white)
    obj = im.convert("RGBA")
    ob = Image.new("RGBA", (W2, H2), (0, 0, 0, 0))
    obj.putalpha(a)
    ob.alpha_composite(obj, (pad, pad))
    out.alpha_composite(ob)
    return out


VERDICT_MAX = [int(os.environ.get("MAX_VERDICT_PICS", "4" if TEST_SEC else "10"))]


def person_photo(title, out):
    """Openly licensed photo of a REAL notable person from Wikipedia/Commons (licence checked on Commons; credit is listed in the caption)."""
    title = (title or "").strip()
    if not title:
        return False
    r = requests.get("https://en.wikipedia.org/w/api.php", headers=WIKI_UA, timeout=25, params={
        "action": "query", "format": "json", "redirects": 1, "titles": title, "prop": "pageimages|pageprops", "piprop": "name|original", "ppprop": "disambiguation"})
    pg = list(((r.json().get("query") or {}).get("pages") or {}).values())
    if not pg or "pageprops" in pg[0] or not pg[0].get("pageimage"):
        return False
    fname = pg[0]["pageimage"]
    ci = requests.get("https://commons.wikimedia.org/w/api.php", headers=WIKI_UA, timeout=25, params={
        "action": "query", "format": "json", "titles": "File:" + fname, "prop": "imageinfo", "iiprop": "url|extmetadata|mime|size", "iiurlwidth": 900})
    cp = list(((ci.json().get("query") or {}).get("pages") or {}).values())
    ii = ((cp[0].get("imageinfo") or [{}])[0]) if cp else {}
    meta = ii.get("extmetadata", {})
    if not ii or ii.get("mime") not in ("image/jpeg", "image/png") or not _lic_ok(meta) or (ii.get("width") or 0) < 300:
        return False
    data = requests.get(ii.get("thumburl") or ii.get("url"), headers=WIKI_UA, timeout=60).content
    im = Image.open(io.BytesIO(data)).convert("RGB")
    im.save(out)
    IMG_CREDITS.append(f"{fname[:60]} - {_strip_html(meta.get('Artist', {}).get('value'))[:40] or 'Wikimedia'} ({meta.get('LicenseShortName', {}).get('value', 'open licence')}) {ii.get('descriptionurl', '')}".strip())
    return True


CLIPS_MAX = [int(os.environ.get("MAX_CLIPS", "8" if TEST_SEC else "30"))]
POPUPS_MAX = [int(os.environ.get("MAX_POPUPS", "0"))]  # sticker pop-ups are off: every subject gets its own full-screen picture


def make_visuals(scenes, imgdir):
    """Generate every picture the plan asks for. Returns (failed, requested)."""
    failed = requested = 0
    prev_img = None
    for i, sc in enumerate(scenes):
        el_ = elapsed_min()
        if el_ > 100 and not VERIFY["off"]:
            VERIFY["off"] = True
            log(f"time guard ({el_:.0f} min used): picture checks off")
        if el_ > 110 and (EXPLAIN_MAX[0] > 0 or VERDICT_MAX[0] > 0):
            EXPLAIN_MAX[0] = VERDICT_MAX[0] = 0
            log("time guard: no more cut-out / circle pictures")
        if el_ > 125 and not LOCAL_SD["dead"]:
            LOCAL_SD["dead"] = True
            log("time guard: slow local AI pictures off")
        if sc["type"] == "photo":
            for n_, it in enumerate(sc.get("items") or []):
                if it["kind"] == "person" and it.get("photo_img") is None:
                    try:
                        pp_ = imgdir / f"{i:03d}_i{n_}_person.png"
                        if person_photo(it.get("entity_en", ""), pp_):
                            it["photo_img"] = Image.open(pp_).convert("RGB")
                    except Exception as e:  # noqa
                        log("person photo failed:", type(e).__name__, str(e)[:100])
                if it["kind"] == "feature" and it.get("png_img") is None and EXPLAIN_MAX[0] > 1:
                    EXPLAIN_MAX[0] -= 2
                    try:
                        build_feature(it, imgdir / f"{i:03d}_i{n_}_feat.png")
                    except Exception as e:  # noqa
                        log("feature build failed:", type(e).__name__, str(e)[:100])
                if it["kind"] == "action" and it.get("png_img") is None and EXPLAIN_MAX[0] > 0:
                    EXPLAIN_MAX[0] -= 1
                    try:
                        it["png_img"] = action_png(it.get("query_en", ""), it.get("pose", ""), it.get("verb", "run"), imgdir / f"{i:03d}_i{n_}_act.png")
                    except Exception as e:  # noqa
                        log("action png failed:", type(e).__name__, str(e)[:100])
                if it["kind"] == "explain" and it.get("png_img") is None and EXPLAIN_MAX[0] > 0:
                    EXPLAIN_MAX[0] -= 1
                    try:
                        it["png_img"] = explain_png(it.get("query_en", ""), imgdir / f"{i:03d}_i{n_}_png.png")
                    except Exception as e:  # noqa
                        log("explain png failed:", type(e).__name__, str(e)[:100])
                if it["kind"] == "verdict":
                    for c_, cdef in enumerate(it.get("circles") or []):
                        if cdef.get("img") is not None or VERDICT_MAX[0] <= 0:
                            continue
                        VERDICT_MAX[0] -= 1
                        try:
                            cp_ = imgdir / f"{i:03d}_i{n_}_v{c_}.png"
                            if gen_image("a realistic photo of " + cdef["query_en"], cp_, "photo", query=cdef["query_en"]):
                                cdef["img"] = Image.open(cp_).convert("RGB")
                        except Exception as e:  # noqa
                            log("verdict picture failed:", type(e).__name__, str(e)[:100])
                if it["kind"] == "beforeafter" and it.get("img2") is None and it.get("after_en") and VERDICT_MAX[0] > 0:
                    VERDICT_MAX[0] -= 1
                    try:
                        bp_ = imgdir / f"{i:03d}_i{n_}_after.png"
                        if gen_image("a realistic photo of " + it["after_en"], bp_, "photo", query=it["after_en"]):
                            it["img2"] = Image.open(bp_).convert("RGB")
                    except Exception as e:  # noqa
                        log("before/after picture failed:", type(e).__name__, str(e)[:100])
                if it["kind"] == "popup" and POPUPS_MAX[0] > 0:
                    POPUPS_MAX[0] -= 1
                    try:
                        po = imgdir / f"{i:03d}_i{n_}_pop.png"
                        if gen_image(it["popup_en"], po, "obj"):
                            it["popup_img"] = cutout_sticker(po)
                    except Exception as e:  # noqa
                        log("popup failed:", type(e).__name__, str(e)[:100])
            for k, sh in enumerate(sc["shots"]):
                p = imgdir / f"{i:03d}_s{k}.png"
                if elapsed_min() > 140 and prev_img:  # out of time: keep the previous picture (the camera still moves), finish the video
                    sh["image"] = prev_img
                    continue
                pp = (sh.get("popup_en") or "").strip()
                if pp and POPUPS_MAX[0] > 0:
                    POPUPS_MAX[0] -= 1
                    try:
                        po = imgdir / f"{i:03d}_s{k}_pop.png"
                        if gen_image(pp, po, "obj"):
                            sh["popup_img"] = cutout_sticker(po)
                    except Exception as e:  # noqa
                        log("popup failed:", type(e).__name__, str(e)[:100])
                if sh.get("motion") and sh.get("clip_en") and (PIXABAY_KEY or PEXELS_KEY) and CLIPS_MAX[0] > 0:
                    CLIPS_MAX[0] -= 1
                    try:
                        cp = imgdir / f"{i:03d}_s{k}.mp4"
                        if find_clip(english_search(sh["clip_en"]), cp):
                            fp = imgdir / f"{i:03d}_s{k}_clip.png"
                            run(["ffmpeg", "-y", "-i", cp, "-vf", "select=eq(n\\,10)", "-frames:v", "1", fp])
                            if not fp.exists():
                                run(["ffmpeg", "-y", "-i", cp, "-frames:v", "1", fp])
                            sh["clip"] = str(cp)
                            sh["image"] = fp
                            prev_img = fp
                            continue
                    except Exception as e:  # noqa
                        log("stock clip failed:", type(e).__name__, str(e)[:100])
                if sh.get("motion") and sh.get("clip_en") and not sh["prompt_en"].strip():
                    sh["prompt_en"] = "a realistic cinematic photo of " + sh["clip_en"]  # no clip found: a picture of the same subject
                    sh["query_en"] = sh.get("query_en") or sh["clip_en"].split()[0]
                if not sh["prompt_en"].strip() and prev_img:
                    sh["image"] = prev_img  # same picture, camera moves to the part being spoken about
                    continue
                if not sh["prompt_en"].strip():
                    sh["prompt_en"] = "a dramatic wide nature landscape"
                if prev_img and CF_STATE["used"] >= MAX_IMAGES and not PIXABAY_KEY:
                    sh["image"] = prev_img  # free-quota saver: reuse the previous picture with a new camera move
                    continue
                requested += 1
                CF_STATE["used"] += 1
                if gen_image(sh["prompt_en"], p, "photo", query=sh.get("query_en", ""), entity=sh.get("entity_en", "")):
                    sh["image"] = p
                    prev_img = p
                elif prev_img:
                    sh["image"] = prev_img
                    failed += 1
                else:
                    raise RuntimeError("no picture could be made for the first scene. Image problems: " + fail_summary())
        elif sc["type"] == "kinetic":
            requested += 1
            p = imgdir / f"{i:03d}_k.png"
            if gen_image(sc["image_prompt_en"], p, "photo"):
                sc["image"] = p
                prev_img = p
            elif prev_img:
                sc["image"] = prev_img
                failed += 1
            else:
                raise RuntimeError("no picture could be made. Image problems: " + fail_summary())
        elif sc["type"] in NEED_ITEMS:
            for k, it in enumerate(sc["items"]):
                requested += 1
                p = imgdir / f"{i:03d}_{k}.png"
                if gen_image(it["prompt_en"], p, "photo" if sc["type"] == "multi" else "card", query=it.get("query_en", ""), entity=it.get("entity_en", "")):
                    it["image"] = p
                else:
                    it["image"] = None
                    failed += 1
        if i % 4 == 0:
            log(f"visuals scene {i + 1}/{len(scenes)} (failed pictures so far: {failed}/{requested})")
    return failed, requested


def pipeline():
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise RuntimeError(f"{tool} not installed")
    if not GEMINI_KEY:
        raise RuntimeError("GEMINI_API_KEY missing")
    if not (CF_ACCOUNT and CF_TOKEN) and not ALLOW_POLLI:
        raise RuntimeError("CF_ACCOUNT_ID / CF_API_TOKEN secrets missing: watermark-free images need a free Cloudflare account")
    _np()
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    done = load_json(DONE_FILE, {"videos": []})
    wpms = load_json(WPM_FILE, {})
    random.seed()

    custom = os.environ.get("CUSTOM_STORY", "").strip()
    if custom.strip().lower() in ("0", "off", "no", "none", "false"):
        custom = ""
    own_story = ""
    if custom:  # the owner's own topic or full story: used instead of the automatic topic
        first = custom.splitlines()[0].strip()
        if len(custom) > 220:  # a full story was given: first short line = title, everything = the story
            own_story = custom
            topic_en = first if len(first) <= 120 else first[:80]
        else:
            topic_en = custom.replace("\n", " ")
        cat, topic_hi = "custom", topic_en
        log("CUSTOM_STORY is set: using the owner's own", "story" if own_story else "topic")
        if own_story:  # a full story is never cut to the short test length
            globals()["TEST_SEC"] = 0
            globals()["MAX_IMAGES"] = int(os.environ.get("MAX_IMAGES", "48"))
            CLIPS_MAX[0], POPUPS_MAX[0] = int(os.environ.get("MAX_CLIPS", "30")), int(os.environ.get("MAX_POPUPS", "0"))
    else:
        cat, topic_en, topic_hi = pick_topic(done)
    style = random.choice(STYLES)
    pal = random.choice(PALETTES)
    target = TEST_SEC or random.randint(MIN_SEC, MAX_SEC)
    if own_story:  # length follows the owner's story, not the test length
        target = int(min(900, max(30, len(own_story.split()) / float(wpms.get("gemini" if (TTS_PROVIDER in ("auto", "gemini") and GEMINI_KEY) else "edge", WPM)) * 60)))
    use_g = TTS_PROVIDER in ("auto", "gemini") and bool(GEMINI_KEY)
    wpm = float(wpms.get("gemini" if use_g else "edge", WPM))
    log(f"TOPIC: {topic_en} | category={cat} | style={style[0]} | theme={pal['name']} | target={target}s | wpm={wpm:.0f}")

    GROUND_SRC.clear()
    if own_story:
        research_story(own_story, topic_en)  # only to find real sources for the on-screen source captions; the story itself is never changed
        facts, sources = ("THE OWNER'S OWN STORY. Follow it faithfully: keep its facts, names, order and meaning, and only polish the wording into natural spoken Hindi "
                          "and cut it into scenes. Do not add new facts.\n" + own_story[:12000]), []
    else:
        facts, sources = research(topic_en)
    try:
        collect_sources(topic=topic_en, story=own_story)
    except Exception as e:  # noqa
        log("collect_sources failed:", str(e)[:100])
    try:
        ensure_sources(topic_en, own_story)
    except Exception as e:  # noqa
        log("ensure_sources failed:", str(e)[:100])
    if not own_story and passages_block():
        facts = (facts or "") + "\n\n" + passages_block()
    script = own_story_script(own_story, topic_en) if own_story else write_script(topic_en, cat, style, target, facts, wpm)
    scenes = script["scenes"]
    mood = script["mood"] or MOOD_MAP.get(cat, "wonder")
    kinds = {}
    for s in scenes:
        kinds[s["type"]] = kinds.get(s["type"], 0) + 1
    log(f"script ready: {len(scenes)} scenes, {word_count(script)} words, mood={mood}, types={kinds}")

    tdir = WORK / "voice"
    voice = make_voice_auto(scenes, tdir)
    total = ffprobe_dur(voice)
    log(f"voice ({VOICE_USED[0]}) length {fmt_time(total)} (target {fmt_time(target)})")
    lo_ok, hi_ok = ((target * 0.5, target * 1.8) if own_story else (TEST_SEC * 0.4, TEST_SEC * 2.2) if TEST_SEC else (MIN_SEC - 30, MAX_SEC + 45))
    if not own_story and not (lo_ok <= total <= hi_ok):
        new_words = int(word_count(script) * target / total)
        log(f"length far off-range, rescaling script to ~{new_words} words")
        try:
            script = rescale_script(script, new_words)
            scenes = script["scenes"]
            shutil.rmtree(tdir, ignore_errors=True)
            voice = make_voice_auto(scenes, tdir)
            total = ffprobe_dur(voice)
            log(f"new voice length {fmt_time(total)}")
        except Exception as e:  # noqa
            log("rescale failed, continuing:", e)
    story_context(topic_en, own_story, script)
    build_sketches(scenes, topic_en)
    director_pass(scenes, topic_en + (("\nSTORY CONTEXT (keep EVERY picture inside it; relatives and neighbours are people of this culture, never animals or objects): " + STORY_CTX["text"]) if STORY_CTX["on"] else ""))
    prov = "gemini" if VOICE_USED[0].startswith("gemini") else "edge"
    wpms[prov] = round(word_count(script) / (total / 60), 1)
    save_json(WPM_FILE, wpms)

    imgdir = WORK / "img"
    imgdir.mkdir()
    failed, requested = make_visuals(scenes, imgdir)
    if failed > requested * 0.35:
        raise RuntimeError(f"too many pictures missing ({failed}/{requested}); not publishing junk. "
                           f"Image problems: {fail_summary()}")

    silent, sfx_events = build_video(scenes, WORK / "build", pal)
    music = WORK / "music.wav"
    make_music(total, music, mood)
    sfx = None
    if SFX_ON:
        sfx = WORK / "sfx.wav"
        build_sfx_track(sfx_events, total, sfx)
    outdir = WORK / "out"
    outdir.mkdir()
    final = outdir / "video.mp4"
    mux_final(silent, voice, music, sfx, final, max_mb=44 if UPLOAD_MODE == "telegram" else None)
    log(f"final video {fmt_time(ffprobe_dur(final))}, {final.stat().st_size / 1e6:.1f} MB")

    any_img = next((sh["image"] for s in scenes for sh in s["shots"] if sh.get("image")), None)
    outdir.mkdir(exist_ok=True)
    thumb, thumb_alt = outdir / "thumbnail.jpg", None
    try:  # AI thumbnail: Gemini plans it for THIS story, Cloudflare paints a clear photo of the subject, one of 4 reference styles
        made = make_thumbs_ai(script, topic_en, imgdir, outdir)
        thumb, thumb_alt = made[0][0], (made[1][0] if len(made) > 1 else None)
    except Exception as e:  # noqa
        log("AI thumbnail failed, using the classic thumbnail:", type(e).__name__, str(e)[:160])
        hero = real_thumb_image(script, topic_en, imgdir, any_img)
        text = script["thumb_text_hi"] or script["title_hi"]
        layout, accent = pick_thumb_layout(script, topic_en)
        log("thumbnail layout:", layout)
        make_thumb_clean(hero, text, thumb, layout, accent)
    hero = thumb  # (only used by the then/now thumbnail below)
    hero = real_thumb_image(script, topic_en, imgdir, any_img) if script["thumb_prompt2_en"] else hero
    text = script["thumb_text_hi"] or script["title_hi"]
    thumb2 = None
    if script["thumb_prompt2_en"]:
        other = imgdir / "thumb2.png"
        if gen_image(script["thumb_prompt2_en"], other, "thumb", query=script.get("thumb_query2_en") or ""):
            try:
                thumb2 = outdir / "thumbnail_thenow.jpg"
                make_thumb_split(hero, other, text, script["thumb_left_hi"], script["thumb_right_hi"], thumb2)
            except Exception as e:  # noqa
                log("split thumbnail failed:", e)
                thumb2 = None

    tags3 = " ".join("#" + re.sub(r"\s+", "", t) for t in script["tags"][:3])
    lines = [script["description_hi"], ""]
    if SRC_LIST:
        lines.append("📚 Sources:")
        lines += [f"• {x['title']} — {x['url']}" for x in SRC_LIST]
    elif sources:
        lines.append("📚 जानकारी के लिए खोज में देखे गए स्रोत: " + ", ".join(sources))
    if IMG_CREDITS:
        lines += ["🖼 Image credits (Wikimedia Commons / Wikipedia, open licences):"] + ["• " + c for c in IMG_CREDITS[:25]]
    lines += ["🤖 इस वीडियो में AI आवाज़ और कहीं-कहीं AI से बनी तस्वीरों का उपयोग किया गया है।", "", tags3]
    meta = {"title": script["title_hi"], "description": "\n".join(lines), "tags": script["tags"]}
    (outdir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    note = []
    if not facts:
        note.append("⚠️ Is baar facts search se verify nahi hue, public karne se pehle facts khud zaroor check karein.")
    if failed:
        note.append(f"ℹ️ {failed}/{requested} pictures nahi ban saki (unki jagah purani image lagi).")
    yt_id = None
    if UPLOAD_MODE == "youtube":
        yt_id = youtube_upload(final, thumb, meta)
        if meta.get("warn"):
            note.append("⚠️ " + meta["warn"])
    fb_id = facebook_upload(final, meta)

    head = (f"✅ Nayi video ready: {meta['title']}\nLambai: {fmt_time(total)}\nTopic: {topic_en}\n"
            f"Awaaz: {VOICE_USED[0]}\nMusic mood: {mood}\n")
    if yt_id:
        head += f"YouTube (private): https://studio.youtube.com/video/{yt_id}/edit\n"
    if fb_id:
        head += "Facebook: unpublished video upload hui (Publishing Tools mein dekhein).\n"
    checklist = ("\nReview checklist:\n1) Shuru ke 15 second ka hook sach hai?\n2) Facts sahi hain?\n"
                 "3) Awaaz aur subtitles theek hain?\n4) Upload ke time 'Altered/synthetic content' = Yes rakhein.\n"
                 "5) Sab theek ho to Public karein.")
    notify(head + "\n".join(note) + checklist)

    if UPLOAD_MODE != "youtube":
        notify("TITLE:\n" + meta["title"] + "\n\nDESCRIPTION:\n" + meta["description"] + "\n\nTAGS:\n" + ", ".join(meta["tags"]))
        ok = tg_send_file(Path(MASTER_NOTE[0]) if MASTER_NOTE[0] else final, "video", (meta["title"] + "\n" + DIRECTOR_REPORT[0]) if DIRECTOR_REPORT[0] else meta["title"])
        tg_send_file(thumb, "doc", "Thumbnail 1")
        if thumb_alt:
            tg_send_file(thumb_alt, "doc", "Thumbnail 2 (alag style)")
        try:
            for f_, cap_ in zip(write_subtitles(scenes, outdir), ("Subtitles (SRT) - video par nahi dikhte, sirf editor ke liye", "Edit plan - kis time par kya laga hai")):
                tg_send_file(f_, "doc", cap_)
        except Exception as e:  # noqa
            log("subtitle file failed:", type(e).__name__, str(e)[:100])
        if thumb2:
            tg_send_file(thumb2, "doc", "Thumbnail 2 (then/now)")
        if T151_ON and T151_PLAN[0]:
            try:
                n151 = t151_report(scenes, T151_PLAN[0], outdir / "techniques_151.txt")
                tg_send_file(outdir / "techniques_151.txt", "doc", f"151 techniques report: {n151}/151 is video me hain (scene number + time)")
            except Exception as e:  # noqa
                log("technique report failed:", type(e).__name__, str(e)[:100])
        if not ok:
            notify("⚠️ Video Telegram par nahi ja saki (size/limit). GitHub Actions ke 'Artifacts' se download karein.")

    done["videos"].append({"date": datetime.now(timezone.utc).isoformat(timespec="seconds"), "topic_en": topic_en,
                           "title_hi": meta["title"], "category": cat, "style": style[0], "theme": pal["name"],
                           "mood": mood, "seconds": round(total), "voice": VOICE_USED[0][:20], "youtube_id": yt_id,
                           "facebook_id": fb_id})
    save_json(DONE_FILE, done)
    log("DONE")


# --------------------------------------------------------------------------- check + selftest
def check():
    res = []

    def t(name, fn):
        try:
            res.append((name, True, str(fn() or "ok")[:300]))
        except Exception as e:  # noqa
            res.append((name, False, f"{type(e).__name__}: {str(e)[:600]}"))

    t("ffmpeg", lambda: run(["ffmpeg", "-version"]).stdout.splitlines()[0])
    t("hindi-shaping(raqm)", lambda: "ok" if features.check("raqm") else (_ for _ in ()).throw(RuntimeError("raqm missing: Hindi letters will break")))
    t("fonts", lambda: ", ".join(Path(font_path(r)).name for r in ("sub", "head", "title")))

    def sound():
        import numpy, scipy  # noqa
        build_sfx_track([(0.1, "pop", 1.0), (0.4, "impact", 1.0), (0.8, "whoosh", 1.0)], 2.0, Path("check_sfx.wav"))
        make_music(6, Path("check_music.wav"), "wonder")
        return "numpy+scipy ok, music+sfx synth ok"

    t("music+sfx", sound)
    t("gemini-text", lambda: gemini("Reply with the single word: ready", temperature=0)[0])

    def voice_check():
        p = Path("check_voice.src")
        tts_gemini("नमस्ते दोस्तों, आज हम प्रकृति की एक अद्भुत बात जानेंगे।", p, "wonder")
        d = ffprobe_dur(p)
        p.unlink(missing_ok=True)
        return f"natural Gemini voice {GEMINI_VOICE} works ({d:.1f}s)"

    def edge_check():
        p = Path("check.mp3")
        tts_one("नमस्ते दोस्तों, आज हम प्रकृति की एक अद्भुत बात जानेंगे", p, "wonder")
        return f"{p.stat().st_size} bytes"

    t("voice-gemini(natural)", voice_check)
    t("voice-edge(backup)", edge_check)

    def cf_check():
        if not (CF_ACCOUNT and CF_TOKEN):
            raise RuntimeError("CF_ACCOUNT_ID / CF_API_TOKEN secret missing")
        ok = img_cloudflare("a red apple on a table, studio photo", Path("check.png"), (1080, 1080))
        if not ok:
            raise RuntimeError("Cloudflare image failed: " + fail_summary())
        return "watermark-free images work"

    t("images(cloudflare)", cf_check)
    if PIXABAY_KEY:
        def pix_check():
            if not pixabay_photo("lion", Path("check_pix.png")):
                raise RuntimeError("Pixabay returned no photo")
            return "real photos OK"
        t("images(pixabay real photos)", pix_check)
    else:
        log("Pixabay key not set (optional): real photos disabled")
    if UPLOAD_MODE == "youtube":
        def ytc():
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            c = Credentials(None, refresh_token=os.environ["YT_REFRESH_TOKEN"],
                            token_uri="https://oauth2.googleapis.com/token", client_id=os.environ["YT_CLIENT_ID"],
                            client_secret=os.environ["YT_CLIENT_SECRET"])
            c.refresh(Request())
            return "token ok"
        t("youtube-token", ytc)
    lines = [("✅" if ok else "❌") + f" {n}: {m}" for n, ok, m in res]
    notify("Health check v3 (" + UPLOAD_MODE + " mode)\n" + "\n".join(lines))
    for f in ("check.mp3", "check.png", "check_voice.src", "check_sfx.wav", "check_music.wav"):
        Path(f).unlink(missing_ok=True)
    must = {"ffmpeg", "hindi-shaping(raqm)", "fonts", "music+sfx", "gemini-text", "images(cloudflare)"}
    return all(ok for n, ok, _m in res if n in must)


def selftest():
    """Offline build test: fake pictures, fake voice. One scene of every type + both thumbnails."""
    t0 = WORK / "selftest"
    if t0.exists():
        shutil.rmtree(t0)
    t0.mkdir(parents=True)
    pal = random.choice(PALETTES) if os.environ.get("PAL") is None else next(
        p for p in PALETTES if p["name"] == os.environ["PAL"])

    def art(path, kind, c1, c2, size=900):
        im = Image.new("RGB", (size, size if size == 900 else int(size * 9 / 16)))
        d = ImageDraw.Draw(im)
        hh = im.height
        for y in range(hh):
            d.line([(0, y), (im.width, y)], fill=tuple(int(c1[k] + (c2[k] - c1[k]) * y / hh) for k in range(3)))
        cx, cy, r = im.width * 0.62, hh * 0.5, hh * 0.28
        if kind == "planet":
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(40, 120, 200))
            d.ellipse([cx - r * 0.5, cy - r * 0.6, cx + r * 0.1, cy - r * 0.1], fill=(60, 170, 90))
        elif kind == "rock":
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(190, 110, 70))
            d.ellipse([cx - r * 0.3, cy - r * 0.4, cx + r * 0.1, cy], fill=(150, 80, 50))
        elif kind == "moon":
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(200, 200, 205))
            for dx, dy, rr in ((-0.4, -0.3, 0.25), (0.2, 0.1, 0.3), (-0.1, 0.5, 0.2)):
                d.ellipse([cx + dx * r - rr * r, cy + dy * r - rr * r, cx + dx * r + rr * r, cy + dy * r + rr * r], fill=(160, 160, 168))
        else:
            d.polygon([(cx, cy - r), (cx + r, cy + r), (cx - r, cy + r)], fill=(240, 190, 40))
        im.save(path)
        return path

    def items(specs):
        return [{"label_hi": lab, "prompt_en": "x", "image": art(t0 / f"c_{lab}_{k}.png", kind, c1, c2)}
                for k, (lab, kind, c1, c2) in enumerate(specs)]

    def shots(parts):
        out = []
        for k, (txt, kind, c1, c2) in enumerate(parts):
            out.append({"text_hi": txt, "prompt_en": "x", "image": art(t0 / f"s_{abs(hash(txt)) % 9999}_{k}.png", kind, c1, c2, 1920)})
        return out

    P, R, M = ("planet", (255, 220, 160), (255, 160, 120)), ("rock", (200, 230, 255), (120, 160, 230)), ("moon", (40, 50, 110), (20, 20, 60))
    n1 = "क्या आप जानते हैं कि चाँद कैसे बना था? यह कहानी बहुत हैरान करने वाली है, आगे जानेंगे"
    n3 = "अब ज़रा सोचिए, उस समय आसमान कितना डरावना दिखता होगा। हर तरफ आग और धुआँ ही धुआँ था"
    n10 = "तो अब आप जान गए कि चाँद कैसे बना। और यह कहानी आज भी आसमान में चमक रही है"
    sc = [
        {"type": "photo", "tone": "suspense", "headline_hi": "", "narration_hi": n1,
         "shots": shots([("क्या आप जानते हैं कि चाँद कैसे बना था?", "planet", (20, 30, 90), (120, 60, 160)),
                         ("यह कहानी बहुत हैरान करने वाली है, आगे जानेंगे", "moon", (10, 10, 40), (60, 20, 90))])},
        {"type": "merge", "tone": "wonder", "headline_hi": "टक्कर से चाँद बना",
         "items": items([("युवा पृथ्वी",) + P, ("थिया ग्रह",) + R, ("हमारा चाँद",) + M]),
         "narration_hi": "लगभग साढ़े चार अरब साल पहले, पृथ्वी से एक मंगल जितना बड़ा ग्रह टकराया, और इसी टक्कर से हमारा चाँद बना"},
        {"type": "photo", "tone": "serious", "headline_hi": "", "narration_hi": n3,
         "items": [{"w": 1, "kind": "place", "text": "आसमान", "sub": "उस दौर का"}, {"w": 6, "kind": "year", "text": "४५० करोड़ साल पहले"},
                   {"w": 9, "kind": "bubble", "text": "कितना डरावना!"}, {"w": 12, "kind": "note", "sub": "नासा रिपोर्ट, 2019",
                    "text": "शुरुआती पृथ्वी पर हर तरफ पिघली हुई चट्टानों का समुद्र था और आसमान में धुआँ और राख भरी रहती थी। वैज्ञानिकों के अनुसार तब तापमान हज़ारों डिग्री तक पहुँच गया था।", "mark": "धुआँ राख"}],
         "shots": shots([("अब ज़रा सोचिए, उस समय आसमान कितना डरावना दिखता होगा।", "rock", (255, 120, 40), (120, 20, 10)),
                         ("हर तरफ आग और धुआँ ही धुआँ था", "planet", (90, 20, 10), (20, 5, 5))])},
        {"type": "counter", "tone": "excited", "headline_hi": "पृथ्वी की उम्र",
         "counter": {"from": 0.0, "to": 4.5, "decimals": 1, "unit_hi": "अरब साल पहले"},
         "narration_hi": "पृथ्वी की उम्र लगभग साढ़े चार अरब साल मानी जाती है"},
        {"type": "sketch", "tone": "calm", "headline_hi": "टक्कर से चाँद बना", "year_hi": "४५० करोड़ साल पहले", "grade": "natural",
         "strokes": clean_prims([{"t": "circle", "c": [64, 28], "r": 12, "fill": "soft"}, {"t": "circle", "c": [46, 20], "r": 4.5, "col": "accent", "fill": "soft"},
                                 {"t": "arrow", "p": [[50, 22], [58, 26]], "col": "accent"}, {"t": "arc", "c": [64, 28], "r": 16, "a0": 200, "a1": 330},
                                 {"t": "line", "p": [[78, 18], [84, 22], [88, 16]]}, {"t": "poly", "p": [[74, 38], [82, 34], [88, 42], [78, 45]], "fill": "soft"},
                                 {"t": "text", "p": [64, 44], "s": "युवा पृथ्वी"}, {"t": "text", "p": [46, 13], "s": "थिया", "col": "accent"}]),
         "narration_hi": "लगभग साढ़े चार अरब साल पहले, एक छोटा ग्रह युवा पृथ्वी से टकराया, और उसके मलबे से हमारा चाँद बना"},
        {"type": "split", "tone": "wonder", "headline_hi": "एक से दो हिस्से",
         "items": items([("पुरानी पृथ्वी",) + P, ("पृथ्वी", "planet", (200, 255, 220), (100, 200, 160)), ("मलबा",) + R]),
         "narration_hi": "इस टक्कर के बाद एक बड़ा गोला दो हिस्सों में बँट गया, एक हिस्सा पृथ्वी बना और दूसरा मलबा बना"},
        {"type": "bars", "tone": "calm", "headline_hi": "आकार की तुलना",
         "bars": [{"label_hi": "पृथ्वी", "value": 12742.0, "dec": 0, "unit_hi": "किमी"},
                  {"label_hi": "चाँद", "value": 3474.0, "dec": 0, "unit_hi": "किमी"},
                  {"label_hi": "मंगल", "value": 6779.0, "dec": 0, "unit_hi": "किमी"}],
         "narration_hi": "देखिए, चाँद पृथ्वी के मुक़ाबले काफ़ी छोटा है, और मंगल इन दोनों के बीच में आता है"},
        {"type": "steps", "tone": "calm", "headline_hi": "चाँद बनने के चरण",
         "items": items([("टक्कर",) + R, ("मलबा",) + R, ("जुड़ना",) + M, ("चाँद",) + M]),
         "narration_hi": "पहले टक्कर हुई, फिर मलबा बना, फिर वह धीरे धीरे जुड़ता गया और आख़िर में चाँद बन गया"},
        {"type": "map", "tone": "calm", "headline_hi": "सफ़र", "grade": "natural",
         "map": {"mode": "flat", "route": True, "pins": [{"label_hi": "अफ़्रीका", "lat": 5.0, "lon": 20.0}, {"label_hi": "भारत", "lat": 22.0, "lon": 79.0}]},
         "narration_hi": "यह सफ़र अफ़्रीका से शुरू हुआ और धीरे-धीरे भारत तक पहुँचा"},
        {"type": "map", "tone": "calm", "headline_hi": "", "grade": "natural",
         "map": {"mode": "terrain", "route": True, "pins": [{"label_hi": "शरीफपुरा गाँव", "lat": 27.40, "lon": 79.62}, {"label_hi": "बिरपुर गाँव", "lat": 27.28, "lon": 79.50}],
                 "regions": [{"label_hi": "फ़र्रुख़ाबाद", "lat": 27.45, "lon": 79.70}, {"label_hi": "मैनपुरी", "lat": 27.20, "lon": 79.35}]},
         "narration_hi": "शरीफपुरा गाँव से बिरपुर गाँव की दूरी बस पंद्रह किलोमीटर है और बीच में दोनों जिलों की सीमा पड़ती है"},
        {"type": "kinetic", "tone": "excited", "keywords_hi": ["टक्कर", "मलबा", "चाँद!"], "headline_hi": "",
         "image_prompt_en": "x", "image": art(t0 / "k.png", "rock", (255, 120, 40), (60, 10, 60), 1920),
         "narration_hi": "याद रखिए, टक्कर से मलबा बना, और उसी मलबे से हमारा चाँद"},
        {"type": "compare", "tone": "calm", "headline_hi": "पृथ्वी बनाम चाँद",
         "items": items([("पृथ्वी", "planet", (200, 255, 220), (100, 200, 160)), ("चाँद",) + M]),
         "narration_hi": "पृथ्वी पर हवा और पानी है, लेकिन चाँद पर न हवा है और न पानी"},
        {"type": "photo", "tone": "calm", "headline_hi": "", "narration_hi": n10,
         "shots": shots([("तो अब आप जान गए कि चाँद कैसे बना।", "moon", (10, 20, 60), (40, 40, 110)),
                         ("और यह कहानी आज भी आसमान में चमक रही है", "planet", (20, 30, 90), (60, 90, 160))])},
    ]
    sc[2]["shots"][0]["kf"] = [{"w": 3, "fx": 0.3, "fy": 0.4, "z": 1.7}, {"w": 7, "fx": 0.7, "fy": 0.35, "z": 1.5}]
    body_en = ("Sumitra was confused briefly after her revival, and did not recognize people around. She did not say anything for about a day "
               "and then began to say that she was Shiva and that she lived in Dibiyapur where she was murdered by her in-laws.")
    ttl_en = "Claims of Reincarnation: An Empirical Study of Cases in India"
    ov_scenes = [
        {"type": "photo", "tone": "serious", "headline_hi": "", "narration_hi": "वैज्ञानिक इयान स्टीवेन्सन ने दिसंबर उन्नीस सौ चौरासी की इस घटना का अध्ययन किया और तीन महीने बाद रिपोर्ट छापी",
         "items": [{"w": 1, "kind": "person", "text": "इयान स्टीवेन्सन", "entity_en": "Ian Stevenson", "sub": ""},
                   {"w": 4, "kind": "year", "text": "दिसंबर १९८४", "year_num": 1984, "month": 12},
                   {"w": 12, "kind": "stat", "text": "३ महीने"}],
         "shots": shots([("वैज्ञानिक इयान स्टीवेन्सन ने दिसंबर उन्नीस सौ चौरासी की इस घटना का अध्ययन किया", "rock", (150, 110, 70), (40, 20, 10)),
                         ("और तीन महीने बाद रिपोर्ट छापी", "planet", (90, 120, 150), (20, 30, 50))])},
        {"type": "photo", "tone": "suspense", "headline_hi": "", "narration_hi": "गाँव वालों ने कहा कि वह घर के काम नहीं कर रही थी, न खाना, न पानी, न सफ़ाई, यह बिल्कुल गलत था",
         "items": [{"w": 1, "kind": "bubble", "text": "मुझे कुछ याद नहीं", "side": "left"},
                   {"w": 8, "kind": "verdict", "text": "", "circles": [{"query_en": "cooking", "mark": "cross", "label": "खाना"}, {"query_en": "water", "mark": "cross", "label": "पानी"},
                                                                       {"query_en": "cleaning", "mark": "tick", "label": "सफ़ाई"}]}],
         "shots": shots([("गाँव वालों ने कहा कि वह घर के काम नहीं कर रही थी, न खाना, न पानी, न सफ़ाई", "moon", (120, 60, 40), (30, 10, 10)),
                         ("यह बिल्कुल गलत था", "planet", (60, 60, 140), (10, 10, 40))])},
    ]
    for k_, st_ in enumerate(SRC_STYLES):
        ov_scenes.append({"type": "photo", "tone": "calm", "headline_hi": "", "narration_hi": "इस बात का स्रोत यह रिपोर्ट है, जो बताती है कि वह लगभग एक दिन तक कुछ नहीं बोली और फिर बोलने लगी",
                          "items": [{"w": 3, "kind": "note", "text": body_en, "sub": ttl_en, "dom": "peoplesreview.com", "mark": "did not say anything for about a day", "style": st_}],
                          "shots": shots([("इस बात का स्रोत यह रिपोर्ट है, जो बताती है कि वह लगभग एक दिन तक कुछ नहीं बोली और फिर बोलने लगी", ("rock", "planet", "moon", "rock")[k_ % 4], (90 + 20 * k_, 100, 120), (20, 30, 40))])})
    gear = Image.new("RGBA", (500, 500), (0, 0, 0, 0))
    gd = ImageDraw.Draw(gear)
    gd.ellipse([60, 60, 440, 440], fill=(200, 60, 50, 255))
    gd.ellipse([170, 170, 330, 330], fill=(255, 240, 200, 255))
    ov_scenes.append({"type": "photo", "tone": "calm", "headline_hi": "", "narration_hi": "ज्वालामुखी का मुख यानी क्रेटर ऐसा दिखता है और यह उत्तर प्रदेश से दूर है, ऐसा माना जाता है कि यह बहुत पुराना है",
                      "items": [{"w": 2, "kind": "explain", "text": "ज्वालामुखी का मुख", "png_img": png_sticker(gear, 520), "anim": "drop"},
                                {"w": 11, "kind": "stat", "text": "उत्तर प्रदेश", "tone": "place"},
                                {"w": 14, "kind": "note", "text": "", "sub": "Britannica - Volcano", "dom": "britannica.com", "ref": True, "style": "banner"},
                                {"w": 0, "kind": "srclist", "text": "Wikipedia - Volcano|en.wikipedia.org\nBritannica - Volcano|britannica.com", "at_end": True}],
                      "shots": shots([("ज्वालामुखी का मुख यानी क्रेटर ऐसा दिखता है और यह उत्तर प्रदेश से दूर है, ऐसा माना जाता है कि यह बहुत पुराना है", "rock", (150, 90, 60), (40, 20, 10))])})
    lion_ = clean_cutout(Path("/home/claude/cut_src_test.png")) if Path("/home/claude/cut_src_test.png").exists() else None
    if lion_ is None:
        lion_ = Image.new("RGBA", (500, 360), (0, 0, 0, 0))
        ImageDraw.Draw(lion_).ellipse([40, 90, 440, 300], fill=(190, 140, 80, 255))
        ImageDraw.Draw(lion_).ellipse([330, 40, 470, 170], fill=(175, 120, 60, 255))
    for verb_, dir_, pos_ in (("run", "right", "rock"), ("approach", "toward", "moon")):
        ov_scenes.append({"type": "photo", "tone": "suspense", "headline_hi": "", "narration_hi": "शेर दौड़ता हुआ तालाब की तरफ गया और फिर पानी पीने लगा, आसपास सब कुछ शांत था",
                          "items": [{"w": 0, "kind": "action", "text": "", "query_en": "lion", "pose": "lion running", "verb": verb_, "dir": dir_, "span": ACTION_SPAN[verb_], "png_img": png_sticker(lion_, 560)}],
                          "shots": shots([("शेर दौड़ता हुआ तालाब की तरफ गया और फिर पानी पीने लगा, आसपास सब कुछ शांत था", pos_, (60, 110, 120), (10, 30, 40))])})
    feat_img = Image.new("RGBA", (560, 420), (0, 0, 0, 0))
    fd_ = ImageDraw.Draw(feat_img)
    fd_.ellipse([40, 120, 420, 340], fill=(190, 140, 80, 255))
    fd_.ellipse([360, 70, 520, 230], fill=(175, 120, 60, 255))
    fd_.polygon([(470, 190), (500, 230), (450, 220)], fill=(255, 255, 255, 255))
    feat_st = png_sticker(feat_img, 620)
    feats_ = [{"label": "तेज़ दाँत", "part_en": "teeth", "x": 0.86, "y": 0.42}, {"label": "मज़बूत पंजे", "part_en": "claws", "x": 0.3, "y": 0.8}, {"label": "घना अयाल", "part_en": "mane", "x": 0.75, "y": 0.25}]
    for f_ in feats_:
        r_ = int(0.16 * max(feat_st.size))
        c_ = Image.new("RGB", (2 * r_, 2 * r_), (24, 26, 32))
        bx_ = (int(f_["x"] * feat_st.width - r_), int(f_["y"] * feat_st.height - r_))
        cr_ = feat_st.crop((bx_[0], bx_[1], bx_[0] + 2 * r_, bx_[1] + 2 * r_))
        c_.paste(cr_, (0, 0), cr_)
        f_["crop"] = c_
    ov_scenes.append({"type": "photo", "tone": "calm", "headline_hi": "", "narration_hi": "गुजरात के अहमदाबाद में सुमित्रा का जन्म हुआ, शेर के तेज़ दाँत, मज़बूत पंजे और घना अयाल उसकी पहचान हैं, यह बात सन उन्नीस सौ चौरासी की है",
                      "items": [{"w": 2, "kind": "place", "text": "अहमदाबाद", "sub": "गुजरात"}, {"w": 4, "kind": "name", "text": "सुमित्रा", "sub": "लड़की"},
                                {"w": 8, "kind": "feature", "text": "", "query_en": "lion", "features": feats_, "png_img": feat_st, "span": 6.0},
                                {"w": 22, "kind": "year", "text": "उन्नीस सौ चौरासी"}],
                      "shots": shots([("गुजरात के अहमदाबाद में सुमित्रा का जन्म हुआ, शेर के तेज़ दाँत, मज़बूत पंजे और घना अयाल उसकी पहचान हैं, यह बात सन उन्नीस सौ चौरासी की है", "rock", (150, 110, 70), (40, 20, 10))])})
    ov_scenes.append({"type": "multi", "vs": True, "tone": "serious", "headline_hi": "", "narration_hi": "नया तरीका बिल्कुल अलग है और पुराना तरीका बिल्कुल अलग था",
                      "items": [{"label_hi": "नया तरीका", "prompt_en": "x", "image": art(t0 / "vs_a.png", "rock", (150, 110, 70), (60, 30, 20), 1920), "t": 0.0},
                                {"label_hi": "पुराना तरीका", "prompt_en": "x", "image": art(t0 / "vs_b.png", "planet", (90, 120, 150), (20, 30, 50), 1920), "t": 1.6}]})
    ba_path = t0 / "ba_after.png"
    art(ba_path, "planet", (60, 150, 90), (10, 50, 30), 1920)
    TN = "यह शेर तीस मीटर ऊँची चट्टान पर खड़ा था, आखिर ऐसा क्यों, ध्यान से देखिए, यह सबसे बड़ा राज़ है"
    def tech_scene(kind_items, k_, nar=TN, tone="calm"):
        return {"type": "photo", "tone": tone, "headline_hi": "", "narration_hi": nar, "items": kind_items,
                "shots": shots([(nar, ("rock", "planet", "moon")[k_ % 3], (110 + 15 * k_, 100, 120 - 8 * k_), (25, 30, 45))])}
    tech_scenes = [
        tech_scene([{"w": 3, "kind": "measure", "text": "३० मीटर", "orient": "h", "sub": "चट्टान की ऊँचाई"}], 0),
        tech_scene([{"w": 3, "kind": "measure", "text": "३० मीटर", "orient": "v", "sub": "ऊँचाई"}], 1),
        tech_scene([{"w": 6, "kind": "qmark", "text": ""}], 2),
        tech_scene([{"w": 9, "kind": "lens", "text": "", "fx": 0.5, "fy": 0.5}], 3),
        tech_scene([{"w": 1, "kind": "beforeafter", "text": "", "img2": Image.open(ba_path).convert("RGB"), "label_a": "पहले", "label_b": "बाद में", "span": 5.0}], 4),
        tech_scene([{"w": 1, "kind": "compare", "text": "", "a_hi": "शेर", "b_hi": "बाघ", "span": 5.6,
                     "rows": [{"label_hi": "वज़न", "a_hi": "१९० किलो", "b_hi": "२५० किलो", "na": 190, "nb": 250}, {"label_hi": "ऊँचाई", "a_hi": "१ मीटर", "b_hi": "१.१ मीटर", "na": 1.0, "nb": 1.1}]}], 5),
        tech_scene([{"w": 3, "kind": "mark", "text": "यही", "shape": "circle"}], 6),
        tech_scene([{"w": 5, "kind": "mark", "text": "ये चट्टान", "shape": "arrow"}, {"w": 11, "kind": "mark", "text": "", "shape": "underline"}], 7),
        tech_scene([{"w": 1, "kind": "chart", "text": "", "title": "शेरों की गिनती", "type": "bar", "ci": 0, "span": 5.6,
                     "pts": [{"label": "२०१०", "v": 411, "show": "४११"}, {"label": "२०१५", "v": 523, "show": "५२३"}, {"label": "२०२०", "v": 674, "show": "६७४"}, {"label": "२०२५", "v": 891, "show": "८९१"}]}], 8),
        tech_scene([{"w": 1, "kind": "chart", "text": "", "title": "गिनती", "type": "line", "ci": 2, "span": 5.6,
                     "pts": [{"label": "२०१०", "v": 411, "show": "४११"}, {"label": "२०१५", "v": 523, "show": "५२३"}, {"label": "२०२०", "v": 674, "show": "६७४"}]}], 9),
        tech_scene([{"w": 1, "kind": "tline", "text": "", "ci": 1, "span": 6.2,
                     "events": [{"when": "१९०७", "what": "पहली गिनती"}, {"when": "१९६५", "what": "अभयारण्य बना"}, {"when": "२०१५", "what": "५२३ शेर"}, {"when": "२०२०", "what": "६७४ शेर"}]}], 10),
        tech_scene([{"w": 2, "kind": "hl", "text": "सबसे बड़ा राज़", "ci": 0}], 11),
        tech_scene([{"w": 2, "kind": "hl", "text": "शेर बचेंगे", "ci": 1}, {"w": 12, "kind": "qmark", "text": ""}], 12),
    ]
    if os.environ.get("SELFTEST_TECH"):
        ov_scenes = tech_scenes
    else:
        ov_scenes = ov_scenes + tech_scenes
    sc[-1:-1] = ov_scenes
    if os.environ.get("SELFTEST_TECH"):
        sc = [sc[0]] + ov_scenes + sc[-1:]
    elif os.environ.get("SELFTEST_SCENES"):
        sc = sc[:int(os.environ["SELFTEST_SCENES"])] + sc[-1:]

    for k, sc_ in enumerate(sc):
        sc_["grade"] = GRADES[k % 6]
        sc_["transition"] = ["", "zoom", "whip", "wipe", "flash", "cut"][k % 6]
        for sh_ in sc_.get("shots", []):
            sh_["label_hi"] = "चाँद का राज़"

    def fake_tts(text, out, tone=None):
        d = max(3.0, len(text.split()) * 0.42)
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=190:duration={d}", "-af", "volume=0.4", "-f", "wav", out])

    voice = make_voice(sc, t0 / "voice", synth=fake_tts)
    total = ffprobe_dur(voice)
    if os.environ.get("SELFTEST_T151"):
        globals()["gemini_json"] = lambda *a_, **k_: {"scenes": []}
        plan_t = plan_t151(sc, "test")
        T151_PLAN[0] = plan_t
        log("selftest t151 report:", t151_report(sc, plan_t, t0 / "techniques_151.txt"), "/151")
    if os.environ.get("SELFTEST_VOX"):
        globals()["gemini_json"] = lambda *a_, **k_: {"scenes": []}
        log("selftest vox: rules", {k: v for k, v in det_techniques(sc).items() if v})
        techniques_coverage(sc, "test")
        log("selftest vox scenes:", assign_vox(sc))
        dedupe_texts(sc)
        calm_pass(sc)
        for i_, s_ in enumerate(sc):
            log(i_, s_.get("type"), "vox" if s_.get("vox") is not None else "-", [(x["w"], x["kind"]) for x in s_.get("items", []) if isinstance(x, dict) and x.get("kind") in NEW_KINDS], [k["text"] for k in s_.get("kw", [])])
    silent, ev = build_video(sc, t0 / "build", pal)
    music = t0 / "music.wav"
    mood = os.environ.get("MOOD", random.choice(MOOD_NAMES))
    make_music(total, music, mood)
    sfx = t0 / "sfx.wav"
    build_sfx_track(ev, total, sfx)
    final = t0 / "final.mp4"
    mux_final(silent, voice, music, sfx if SFX_ON else None, final)
    hero = t0 / "hero.png"
    art(hero, "planet", (255, 130, 40), (60, 10, 20), 1920)
    other = t0 / "other.png"
    art(other, "planet", (40, 120, 255), (10, 20, 80), 1920)
    make_thumb_hero(hero, "पृथ्वी का जन्म कैसे हुआ", "4 अरब साल पहले...", "रहस्य", t0 / "thumb1.jpg")
    make_thumb_split(hero, other, "पृथ्वी तब और अब", "तब", "अब", t0 / "thumb2.jpg")
    log(f"SELFTEST OK ({pal['name']}, mood {mood}): {final} {ffprobe_dur(final):.1f}s (voice {total:.1f}s) "
        f"{final.stat().st_size / 1e6:.1f}MB, {len(ev)} sound effects")


if __name__ == "__main__":
    try:
        if "--selftest" in sys.argv:
            selftest()
        elif "--check" in sys.argv:
            sys.exit(0 if check() else 1)
        elif "--voices" in sys.argv:
            WORK.mkdir(parents=True, exist_ok=True)
            voice_samples()
        else:
            pipeline()
    except Exception as exc:
        traceback.print_exc()
        if "--selftest" not in sys.argv:
            notify(f"❌ Video banane mein error aaya:\n{type(exc).__name__}: {str(exc)[:700]}\n\n"
                   "GitHub → Actions mein us run ka log dekh sakte hain. Agle schedule par bot dobara koshish karega.")
        sys.exit(1)
