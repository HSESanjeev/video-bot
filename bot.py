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
TTS_RATE = os.environ.get("TTS_RATE", "-8%")
TTS_PROVIDER = os.environ.get("TTS_PROVIDER", "auto").strip().lower()  # auto | gemini | edge
GEMINI_VOICE = os.environ.get("GEMINI_VOICE", "Charon")
GEMINI_TTS_MODELS = [m for m in os.environ.get(
    "GEMINI_TTS_MODELS", "gemini-3.1-flash-tts-preview,gemini-2.5-flash-preview-tts").split(",") if m.strip()]
GEMINI_TTS_GAP = float(os.environ.get("GEMINI_TTS_GAP", "4"))
WPM = float(os.environ.get("WPM_ESTIMATE", "130"))  # spoken Hindi words per minute (default estimate)
MIN_SEC, MAX_SEC = 180, 300
UPLOAD_MODE = os.environ.get("UPLOAD_MODE", "telegram").strip().lower()
SCENE_GAP = 0.30
HOOK_GAP = 0.70
ALLOW_POLLI = os.environ.get("ALLOW_POLLINATIONS", "") == "1"  # watermarked + very slow, off by default

GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
PEXELS_KEY = os.environ.get("PEXELS_API_KEY", "")
POLLI_KEY = os.environ.get("POLLINATIONS_KEY", "")
CF_ACCOUNT = os.environ.get("CF_ACCOUNT_ID", "")
CF_TOKEN = os.environ.get("CF_API_TOKEN", "")
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")

STYLE_PHOTO = ("vivid rich colors, bright natural lighting, sharp detail, wide cinematic composition, "
               "main subject centered with breathing space, high quality nature documentary look, "
               "no text, no letters, no watermark, no logo, no real person's face")
STYLE_CARD = ("vibrant detailed digital illustration, one clear centered subject, clean simple soft-colored background, "
              "bright saturated colors, soft studio lighting, polished game-art quality, "
              "no text, no letters, no watermark, no logo")
STYLE_THUMB = ("ultra dramatic hero shot, main subject large on the right side, bright rim light, vivid saturated colors, "
               "shallow depth of field, empty space on the left, high contrast, professional youtube thumbnail background, "
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
EXPLAINERS = {"merge", "split", "steps", "compare", "counter", "bars"}


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


def gemini(prompt, json_mode=False, temperature=0.8, search=False):
    """Returns (text, grounding_titles)."""
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": temperature}}
    if json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"
    if search:
        body["tools"] = [{"google_search": {}}]
    last = None
    for model in gemini_models():
        for attempt in range(2):
            try:
                r = requests.post(f"{GBASE}/models/{model}:generateContent",
                                  headers={"x-goog-api-key": GEMINI_KEY}, json=body, timeout=240)
                if r.status_code in (429, 500, 503):
                    last = f"{model} HTTP {r.status_code}"
                    time.sleep(15 * (attempt + 1))
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
                    if t and t not in titles:
                        titles.append(t)
                return text, titles
            except requests.RequestException as e:
                last = f"{model} {type(e).__name__}"
                time.sleep(10)
    raise RuntimeError(f"Gemini failed: {last}")


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


def gemini_json(prompt, temperature=0.8):
    for _ in range(3):
        text, _t = gemini(prompt, json_mode=True, temperature=temperature)
        try:
            return parse_json(text)
        except Exception as e:
            log("JSON parse failed, retrying:", e)
    raise RuntimeError("Gemini did not return valid JSON")


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
def research(topic):
    prompt = (f"Topic: {topic}\nUsing web search, collect the key well-established scientific facts about this topic "
              "(origins, mechanisms, widely accepted figures with rounded numbers, and the most surprising true facts). "
              "Prefer NASA, Smithsonian, National Geographic, universities, scientific journals. "
              "Write 15-25 short bullet facts in English. Mark anything still debated as 'debated'.")
    try:
        text, titles = gemini(prompt, search=True, temperature=0.3)
        return text, titles[:6]
    except Exception as e:
        log("Research (search grounding) failed, continuing without it:", e)
        return "", []


SCRIPT_RULES = """RULES FOR THE SCRIPT
- Narration language: simple, spoken Hindi in Devanagari, like an engaging documentary narrator. Short sentences. One idea per scene.
- Write every number in Hindi words (e.g. "चार अरब", "तीन हज़ार"). No digits, no English letters, no emojis in narration.
- Scene 1 is a SUSPENSE HOOK: 25-30 words, built on a TRUE surprising fact or question, does NOT reveal the answer, ends by promising it ("आगे जानेंगे"). Scene 1 must be type "photo", tone "suspense", visually dramatic.
- Scene 2 says in one or two lines what the viewer will understand by the end.
- Around 55% of the video add a short re-hook line ("लेकिन असली राज़ अभी बाक़ी है...") that is true to what follows.
- Final scenes: recap the 3 key points in simple words, then one short line asking viewers to subscribe.
- The viewer must truly UNDERSTAND the topic by the end: define every new term when first used, use one simple everyday analogy, go in a logical order.
- Facts: only well-established science, rounded widely-accepted numbers. If something is debated say "वैज्ञानिकों का मानना है". Never invent names, dates, statistics or quotes. If unsure, leave it out.
- Neutral on religion and politics; never mention or mock any belief. Say "वैज्ञानिक मान्यता के अनुसार" for origin topics.
- Every scene has a "tone" for the narrator's voice: suspense, wonder, serious, excited or calm. Vary it with the content.

VISUAL PLAN: the picture must TEACH and must match the exact words being spoken at that moment. Every scene has a "type":
- "photo": a cinematic full-screen picture with camera movement. Give 2-3 "shots": split the scene narration into 2-3 consecutive parts ("text_hi", together EXACTLY the scene narration) and give each part its own "prompt_en" showing precisely what that part says (one clear subject, cinematic). The picture changes about every 3-5 seconds (roughly 8-12 words). Optional "headline_hi" (2-4 Hindi words) shown as a chapter label when a new section starts.
- "merge": two things come together and create a third (e.g. young Earth + a Mars-sized planet -> the Moon). items = [A, B, RESULT]. First half of the narration names A and B, the second half announces the result.
- "split": one thing breaks into two parts. items = [WHOLE, PART1, PART2].
- "steps": a sequence, life cycle, timeline or evolution (3 or 4 items in order, e.g. egg -> larva -> pupa -> butterfly).
- "compare": two things side by side. items = [A, B].
- "counter": one striking number counting up on screen (age, distance, size, speed, count). "counter": {"from": 0, "to": 4.5, "decimals": 1, "unit_hi": "अरब साल पहले"} plus "headline_hi" (what the number is). The narration says the number in words.
- "bars": compare 2-4 real sizes as animated bars. "bars": [{"label_hi": "पृथ्वी", "value": 12742, "unit_hi": "किमी"}, ...]. Same unit for all, rounded well-established values only.
- "kinetic": a punchy key sentence: "keywords_hi": 2-4 short Hindi words/phrases that slam onto the screen one by one, plus "image_prompt_en" for a dramatic background picture. Use for the re-hook, key insights and the recap.
Use an explainer/data type (merge, split, steps, compare, counter, bars) for AT LEAST 40% of the scenes wherever the topic has a cause->effect, combination, breakup, sequence, comparison or a striking number. Include at least 2 "kinetic" scenes. Use "photo" for the hook, atmosphere, scale and the closing scene.
Explainer, counter, bars and kinetic scenes need short narration (14-22 words). Explainer scenes also need "headline_hi" of 2-5 Hindi words shown on screen (e.g. "टक्कर से चाँद बना").
Each explainer item has "label_hi" (1-3 Hindi words under the picture) and "prompt_en": a concrete, simple description of ONE clear subject on a plain background (e.g. "a young glowing blue-green Earth", "a Mars-sized rocky planet", "the grey cratered Moon"). No text or letters in any picture, no real people.
"mood" of the whole video's background music: one of mystery, epic, wonder, calm, tense, life, space.
"""

MOOD_NAMES = ["mystery", "epic", "wonder", "calm", "tense", "life", "space"]
TONE_NAMES = ["suspense", "wonder", "serious", "excited", "calm"]


def build_script_prompt(topic, cat, style, target_sec, facts, words):
    n_scenes = max(14, round(words / 21))
    return f"""You are a script writer and film editor for a Hindi nature & science explainer YouTube channel.
TOPIC: {topic}
CATEGORY: {cat}
STRUCTURE STYLE: {style[1]}
TARGET: about {words} Hindi words of narration in total ({target_sec} seconds), around {n_scenes} scenes.

{SCRIPT_RULES}

VERIFIED FACTS TO PREFER (may be empty):
{facts[:6000]}

Return ONLY JSON in exactly this shape:
{{
 "title_hi": "Hindi title, max 60 characters, curious but TRUTHFUL, no clickbait lies",
 "mood": "wonder",
 "thumb_text_hi": "3-5 powerful Hindi words for the thumbnail",
 "thumb_sub_hi": "a short Hindi line under the headline, 2-5 words (e.g. 4 अरब साल पहले...)",
 "thumb_badge_hi": "one short Hindi word like रहस्य / सच / विज्ञान",
 "thumb_prompt_en": "one striking, dramatic hero subject for the thumbnail (a single subject, glowing, cinematic)",
 "thumb_prompt2_en": "the contrasting counterpart for a then/now thumbnail (e.g. molten young Earth vs today's blue Earth); empty string if the topic has no natural contrast",
 "thumb_left_hi": "1-2 Hindi words for the left image, e.g. तब", "thumb_right_hi": "1-2 Hindi words for the right image, e.g. अब",
 "description_hi": "3-4 short Hindi lines telling what the viewer will learn",
 "tags": ["8-12 tags mixing Hindi and English"],
 "scenes": [
  {{"narration_hi": "...", "type": "photo", "tone": "suspense", "headline_hi": "",
    "shots": [{{"text_hi": "first part of the narration", "prompt_en": "..."}}, {{"text_hi": "second part", "prompt_en": "..."}}]}},
  {{"narration_hi": "...", "type": "merge", "tone": "wonder", "headline_hi": "...",
    "items": [{{"label_hi": "...", "prompt_en": "..."}}, {{"label_hi": "...", "prompt_en": "..."}}, {{"label_hi": "...", "prompt_en": "..."}}]}},
  {{"narration_hi": "...", "type": "counter", "tone": "excited", "headline_hi": "...", "counter": {{"from": 0, "to": 4.5, "decimals": 1, "unit_hi": "..."}}}},
  {{"narration_hi": "...", "type": "kinetic", "tone": "serious", "keywords_hi": ["...", "..."], "image_prompt_en": "..."}}
 ]
}}"""


NEED_ITEMS = {"merge": (3, 3), "split": (3, 3), "steps": (3, 4), "compare": (2, 2)}


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


def validate_script(s):
    if not isinstance(s, dict) or not isinstance(s.get("scenes"), list) or len(s["scenes"]) < 8:
        raise ValueError("script has too few scenes")
    for idx, sc in enumerate(s["scenes"]):
        sc["narration_hi"] = clean_narration(sc.get("narration_hi", ""))
        if not sc["narration_hi"]:
            raise ValueError("empty narration")
        tone = str(sc.get("tone") or "calm").lower().strip()
        sc["tone"] = tone if tone in TONE_NAMES else "calm"
        typ = str(sc.get("type") or "photo").lower().strip()
        items = []
        for it in (sc.get("items") or []):
            if isinstance(it, dict) and str(it.get("prompt_en") or "").strip():
                items.append({"label_hi": clean_short(it.get("label_hi"), 26) or " ",
                              "prompt_en": clean_short(it.get("prompt_en"), 400)})
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
        elif typ == "kinetic":
            kw = [clean_short(k, 18) for k in (sc.get("keywords_hi") or []) if clean_short(k, 18)][:4]
            if len(kw) >= 2:
                sc["keywords_hi"] = kw
            else:
                typ = "photo"
        else:
            typ = "photo"
        if idx == 0:
            typ = "photo"
            sc["tone"] = "suspense"
        sc["type"] = typ
        sc["headline_hi"] = clean_short(sc.get("headline_hi"), 36) if typ != "kinetic" else ""
        base_prompt = clean_short(sc.get("image_prompt_en"), 400)
        shots = []
        for sh in (sc.get("shots") or []):
            if isinstance(sh, dict) and str(sh.get("prompt_en") or "").strip():
                shots.append({"text_hi": clean_narration(sh.get("text_hi", "")), "prompt_en": clean_short(sh["prompt_en"], 400)})
        if typ == "photo":
            if not shots:
                shots = [{"text_hi": sc["narration_hi"], "prompt_en": base_prompt or "a dramatic wide nature landscape"}]
            shots = shots[:4]
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
    return s


def word_count(s):
    return sum(len(sc["narration_hi"].split()) for sc in s["scenes"])


def write_script(topic, cat, style, target_sec, facts, wpm):
    words = int(target_sec / 60 * wpm)
    script = validate_script(gemini_json(build_script_prompt(topic, cat, style, target_sec, facts, words), 0.8))
    review = f"""You are (1) a careful science editor, (2) a Hindi school teacher and (3) a documentary film editor.
Improve this explainer-video script JSON:
- Remove or soften any claim that is not widely accepted science; fix any factual doubt.
- Replace difficult words with simple ones; make sure a 15-year-old understands everything on first listen.
- Keep scene 1 a truthful suspense hook (25-30 words, type "photo") that does not reveal the answer.
- Check that every picture matches the words spoken at that moment (each shot's prompt_en must show exactly what its text_hi says; a merge scene must really show A and B combining into the result named in the narration; counter/bars numbers must be correct and round). Fix types/items/labels/headlines/shots where they do not teach.
- Keep at least 40% of scenes as merge/split/steps/compare/counter/bars and at least 2 kinetic scenes.
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
TONES = {
    "suspense": ("-14%", "-8Hz", "slow, hushed and suspenseful, building curiosity, with dramatic pauses"),
    "wonder": ("-6%", "+2Hz", "full of awe and wonder, warm and expressive"),
    "serious": ("-8%", "-3Hz", "serious, deep and authoritative"),
    "excited": ("-2%", "+4Hz", "energetic and excited, revealing something amazing"),
    "calm": ("-10%", "-2Hz", "calm, gentle and friendly, explaining clearly"),
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
    prompt = (f"Say in a deep, warm, confident male voice, like a premium Hindi nature-documentary narrator, "
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


def make_voice(scenes, tdir, synth=None):
    synth = synth or tts_one
    tdir.mkdir(parents=True, exist_ok=True)
    wavs = []
    for i, sc in enumerate(scenes):
        src = tdir / f"s{i:03d}.src"
        wav = tdir / f"s{i:03d}.wav"
        synth(sc["narration_hi"], src, sc.get("tone"))
        pad = HOOK_GAP if i == 0 else SCENE_GAP
        run(["ffmpeg", "-y", "-i", src, "-ar", "44100", "-ac", "1", "-af",
             f"silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.05,apad=pad_dur={pad}", wav])
        total = ffprobe_dur(wav)
        sc["dur"] = total
        sc["speech_dur"] = max(0.5, total - pad)
        wavs.append(wav)
        if i % 8 == 0:
            log(f"voice {i + 1}/{len(scenes)}")
    lst = tdir / "voice_list.txt"
    lst.write_text("".join(f"file '{w.resolve()}'\n" for w in wavs))
    raw = tdir / "voice_raw.wav"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", raw])
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


def chunk_words(text, n=5):
    w = text.split()
    ch = [" ".join(w[i:i + n]) for i in range(0, len(w), n)]
    if len(ch) > 1 and len(ch[-1].split()) <= 1:
        ch[-2] += " " + ch.pop()
    return ch


def chunk_times(text, speech_dur, scene_dur):
    ch = chunk_words(text)
    wts = [max(1, len(c)) for c in ch]
    tot, t, out = sum(wts), 0.0, []
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


def fit_image(raw_bytes, out_png, size=(1920, 1080)):
    tmp = Path(str(out_png) + ".tmp")
    tmp.write_bytes(raw_bytes)
    im = Image.open(tmp)
    im.verify()
    im = Image.open(tmp).convert("RGB")
    tmp.unlink(missing_ok=True)
    if im.width < 400 or im.height < 400:
        raise ValueError("image too small")
    im = ImageOps.fit(im, size, Image.LANCZOS)
    im.save(out_png)


CF_STATE = {"dead": False, "used": 0}
MAX_IMAGES = int(os.environ.get("MAX_IMAGES", "16"))


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
                     params={"query": query, "per_page": 5, "orientation": "landscape"}, timeout=30)
    if not r.ok or not r.json().get("photos"):
        return False
    p = random.choice(r.json()["photos"][:3])
    fit_image(requests.get(p["src"]["large2x"], timeout=60).content, out, size)
    return True


def gen_image(prompt, out, kind="photo", fallback_query=""):
    suffix = {"photo": STYLE_PHOTO, "card": STYLE_CARD, "thumb": STYLE_THUMB}[kind]
    size = (1080, 1080) if kind == "card" else (1920, 1080)
    full = f"{prompt}, {suffix}"
    dims = {"photo": (1024, 576), "thumb": (1024, 576), "card": (512, 512)}[kind]
    chain = [("cloudflare", lambda: img_cloudflare(full, out, size, dims))]
    if ALLOW_POLLI:
        chain.append(("pollinations", lambda: img_pollinations(full, out, size)))
    if kind != "card":
        chain.append(("pexels-photo", lambda: pexels_photo(fallback_query or prompt[:60], out, size)))
    for name, fn in chain:
        try:
            if fn() and Path(out).exists():
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


def make_music(duration, out, mood="wonder"):
    """Original music: chord pads + bass + mood-specific pluck / drums. Different mood, key and progression every video."""
    np = _np()
    cfg = MOODS.get(mood) or MOODS["wonder"]
    rnd = random.Random()
    root = rnd.choice([40, 41, 43, 45, 46, 48])
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

    def chord_notes(d):
        def semi(k):
            return sc_[k % 7] + 12 * (k // 7)
        return [root + semi(d), root + semi(d + 2), root + semi(d + 4), root + 12 + semi(d)]

    pads = {}
    for d in set(prog):
        notes = chord_notes(d)
        seg = sum(_pad_note(midi_hz(m), chord_len + 1.8, cfg["bright"]) for m in notes)
        bass = _pad_note(midi_hz(notes[0] - 12), chord_len + 1.8, 2) * 1.7
        pads[d] = seg + bass
    t0, k = 0.0, 0
    while t0 < total:
        d = prog[k % len(prog)]
        a = int(t0 * SR)
        p = pads[d]
        m = min(len(p), n - a)
        buf[a:a + m] += p[:m]
        t0 += chord_len
        k += 1
    if cfg["arp"]:
        step = beat / 2 if cfg["arp"] in ("pluck",) else beat
        cache = {}
        t0, k = 0.0, 0
        while t0 < total:
            d = prog[int(t0 // chord_len) % len(prog)]
            tones = [x + 12 * rnd.choice([1, 2]) for x in chord_notes(d)[:3]]
            if rnd.random() < (0.62 if cfg["arp"] == "pluck" else 0.4):
                f = midi_hz(rnd.choice(tones))
                key = round(f, 1)
                if key not in cache:
                    cache[key] = _pluck(f, cfg["arp"])
                s = cache[key]
                a = int(t0 * SR)
                m = min(len(s), n - a)
                if m > 0:
                    buf[a:a + m] += s[:m]
            t0 += step
    if cfg["drum"]:
        hit = _drum_hit(cfg["drum"])
        step = {"boom": beat * 2, "heart": beat * 2, "shaker": beat / 2}[cfg["drum"]]
        t0 = chord_len
        while t0 < total:
            a = int(t0 * SR)
            m = min(len(hit), n - a)
            swell = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(t0 / (chord_len * 2)))
            if m > 0:
                buf[a:a + m] += hit[:m] * swell
            t0 += step
    peak = float(np.max(np.abs(buf))) or 1.0
    buf = buf / peak * 0.9
    raw = Path(str(out) + ".raw.wav")
    write_wav(raw, buf)
    run(["ffmpeg", "-y", "-i", raw, "-af",
         "lowpass=f=5200,aecho=0.8:0.85:600|1100:0.28|0.18,"
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
    g = ImageOps.invert(Image.radial_gradient("L")).resize((size, size)).point(lambda v: int(v * alpha))
    s = Image.new("RGBA", (size, size), tuple(color) + (255,))
    s.putalpha(g)
    return s


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
    cy = 348
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
        _C["grain"] = [(rng.standard_normal((H, W, 1)) * 3.0).astype(np.float32) for _ in range(6)]
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


def shot_frame(shot, tl):
    """One camera frame of a still picture: push / pull / pan / tilt / orbit / handheld, optional 2.5D parallax + rack focus."""
    src = shot["img"]
    p = cl(tl / max(0.1, shot["dur"]))
    z, cx, cy = cam_state(shot["cam"], p, tl)
    fr = src.resize((W, H), Image.BILINEAR, box=crop_box(z, cx, cy))
    if shot.get("par"):
        fg = src.resize((W, H), Image.BILINEAR, box=crop_box(z * (1 + 0.07 * p), cx, cy))
        fr = Image.composite(fg, fr, par_mask())
    if shot.get("focus") and tl < 0.8:
        r = (1 - tl / 0.8) * 9
        if r > 0.4:
            fr = fr.filter(ImageFilter.GaussianBlur(r))
    return fr


def blend_trans(prev, cur, p, kind):
    np = _np()
    p = cl(p)
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


def make_subtrack(text, speech_dur, dur, pal):
    items = []
    for c, a, b in chunk_times(text, speech_dur, dur):
        sprites = build_sub_sprites(c, pal)
        lens = [max(1, len(x)) for x in c.split()]
        tot, acc, cum = sum(lens), 0, []
        for l in lens:
            acc += l
            cum.append(acc / tot)
        items.append((a, b, sprites, cum))
    return items


def draw_sub(frame, items, t):
    for a, b, sprites, cum in items:
        if a <= t < b:
            frac = (t - a) / max(1e-6, b - a)
            wi = next((k for k, c in enumerate(cum) if frac <= c), len(cum) - 1)
            sp = sprites[min(wi, len(sprites) - 1)]
            sc = 0.88 + 0.12 * e_out((t - a) / 0.14) if t - a < 0.14 else 1.0
            put(frame, sp, W / 2, H - 30 - sp.height / 2 * sc, sc, 1.0)
            return


# ---- scene drawers: each returns draw(t) -> PIL frame (before subtitles / scene transition)
def graded_image(ctx, path):
    if path not in ctx["imgs"]:
        im = Image.open(path).convert("RGB")
        im = ImageOps.fit(im, (SW, SH), Image.LANCZOS)
        im = ImageEnhance.Color(im).enhance(1.12)
        im = ImageEnhance.Contrast(im).enhance(1.06)
        ctx["imgs"][path] = im
    return ctx["imgs"][path]


def pick_cam(ctx):
    rnd = ctx["rnd"]
    c = rnd.choice([x for x in CAMS if x != ctx.get("last_cam")])
    ctx["last_cam"] = c
    return c


def build_shots(sc, dur, speech_dur, ctx, tone):
    rnd = ctx["rnd"]
    raw = sc["shots"]
    tot = sum(max(1, len(s["text_hi"])) for s in raw)
    acc, spans = 0, []
    for k, s in enumerate(raw):
        a = 0.0 if k == 0 else speech_dur * acc / tot
        acc += max(1, len(s["text_hi"]))
        b = dur if k == len(raw) - 1 else speech_dur * acc / tot
        spans.append((a, b, s))
    shots = []
    for a, b, s in spans:
        n = max(1, math.ceil((b - a) / 6.5))
        sd = (b - a) / n
        for j in range(n):
            cam = "push" if (not shots) else pick_cam(ctx)
            first_of_img = (j == 0)
            trans = "none" if not shots else (rnd.choice(["fade", "zoom", "whip", "wipe", "circle"] +
                                                         (["glitch"] if tone == "suspense" else []))
                                              if first_of_img else "zoom")
            shots.append({"img": graded_image(ctx, s["image"]), "t0": a + j * sd, "t1": a + (j + 1) * sd, "dur": sd,
                          "cam": cam, "par": cam in ("push", "pull", "orbit") and rnd.random() < 0.45,
                          "focus": (not shots), "trans": trans, "tdur": 0.3 if trans == "fade" else 0.42,
                          "phase": rnd.uniform(0, 6)})
    shots[-1]["t1"] = dur
    return shots


def make_photo_draw(sc, dur, speech_dur, ctx, events, pal):
    shots = build_shots(sc, dur, speech_dur, ctx, sc.get("tone"))
    dust = Dust(ctx["rnd"].random())
    lt = lower_third(sc["headline_hi"], pal) if sc.get("headline_hi") else None
    for sh in shots[1:]:
        nm, g = TRANS_SFX.get(sh["trans"], (None, 0))
        if nm:
            events.append((sh["t0"], nm, g * 0.6))
    if lt is not None:
        events.append((0.25, "pop", 0.55))
    ends = {}

    def draw(t):
        idx = 0
        for k, sh in enumerate(shots):
            if sh["t0"] <= t:
                idx = k
        sh = shots[idx]
        tl = t - sh["t0"]
        fr = shot_frame(sh, tl)
        if idx > 0 and tl < sh["tdur"]:
            if idx - 1 not in ends:
                ends[idx - 1] = shot_frame(shots[idx - 1], shots[idx - 1]["dur"])
            fr = blend_trans(ends[idx - 1], fr, tl / sh["tdur"], sh["trans"])
        fr = fx_apply(fr, t, int(t * FPS), sh["phase"])
        dust.draw(fr, t)
        if lt is not None:
            q = e_back((t - 0.2) / 0.5)
            out = 1 - cl((t - (dur - 0.45)) / 0.35)
            put(fr, lt, 70 + lt.width / 2 - (1 - cl(q)) * 420, 78, 1.0, cl((t - 0.2) * 5) * out)
        return fr
    return draw


def make_kinetic_draw(sc, dur, speech_dur, ctx, events, pal):
    img = graded_image(ctx, sc["image"])
    shot = {"img": img, "dur": dur, "cam": "push", "par": False, "focus": False}
    words = sc["keywords_hi"]
    n = len(words)
    size = 150 if n <= 2 else 118
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
            put(fr, sp, W / 2, H * 0.43 + (i - (n - 1) / 2) * lh, lerp(2.3, 1.0, e_out(q / 0.2)), cl(q * 9))
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
    if i == 1:
        return "flash", 0.55
    if sc["type"] in EXPLAINERS or (prev_sc and prev_sc["type"] in EXPLAINERS):
        return rnd.choice(["wipe", "circle", "fade", "zoom"]), 0.5
    pool = ["whip", "fade", "zoom", "flash"] + (["glitch"] if sc.get("tone") == "suspense" else [])
    return rnd.choice(pool), 0.45


def render_scene(i, sc, out, pal, ctx, prev_sc, last):
    dur, sp = sc["dur"], sc["speech_dur"]
    events = []
    typ = sc["type"]
    if typ in ("merge", "split", "steps", "compare"):
        draw = make_explainer(sc, dur, pal, events)
    elif typ == "counter":
        draw = make_counter_draw(sc, dur, sp, pal, events)
    elif typ == "bars":
        draw = make_bars_draw(sc, dur, sp, pal, events)
    elif typ == "kinetic":
        draw = make_kinetic_draw(sc, dur, sp, ctx, events, pal)
    else:
        draw = make_photo_draw(sc, dur, sp, ctx, events, pal)
    subs = make_subtrack(sc["narration_hi"], sp, dur, pal)
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
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", str(out)]
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


def build_video(scenes, tdir, pal):
    tdir.mkdir(parents=True, exist_ok=True)
    ctx = {"prev": Image.new("RGB", (W, H), (0, 0, 0)), "t0": 0.0, "frames": 0, "sfx": [], "imgs": {},
           "rnd": random.Random(), "last_cam": None}
    parts = []
    for i, sc in enumerate(scenes):
        out = tdir / f"scene_{i:03d}.mp4"
        render_scene(i, sc, out, pal, ctx, scenes[i - 1] if i else None, i == len(scenes) - 1)
        parts.append(out)
        ctx["imgs"] = {k: v for k, v in ctx["imgs"].items() if k in {s.get("image") for s in sc.get("shots", [])} or k == sc.get("image")}
        if i % 4 == 0:
            log(f"video scene {i + 1}/{len(scenes)} ({sc['type']})")
    lst = tdir / "shots.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    silent = tdir / "silent.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", silent])
    return silent, ctx["sfx"]


def mux_final(silent, voice, music, sfx, out, max_mb=None):
    dur = ffprobe_dur(voice)
    fc = ("[1:a]highpass=f=70,aresample=44100,asplit=2[v][vsc];"
          "[2:a]volume=0.30[m];"
          "[m][vsc]sidechaincompress=threshold=0.04:ratio=9:attack=15:release=500[duck];"
          "[3:a]aresample=44100,aformat=channel_layouts=stereo,volume=0.55[fx];"
          "[v]aformat=channel_layouts=stereo[vst];"
          "[vst][duck][fx]amix=inputs=3:duration=first:dropout_transition=0:normalize=0[mix];"
          "[mix]loudnorm=I=-16:TP=-1.5:LRA=11,aresample=44100[a]")
    vopts = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21"]
    if max_mb:
        kbps = int(max(500, min(5000, (max_mb * 8 * 1024) / dur - 136)))
        vopts = ["-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k",
                 "-bufsize", f"{2 * kbps}k"]
    run(["ffmpeg", "-y", "-i", silent, "-i", voice, "-i", music, "-i", sfx, "-filter_complex", fc,
         "-map", "0:v", "-map", "[a]", *vopts, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
         "-movflags", "+faststart", "-shortest", out])
    if max_mb and Path(out).stat().st_size > max_mb * 1024 * 1024:
        log("file still large, re-encoding smaller")
        tmp = Path(str(out) + ".big.mp4")
        Path(out).rename(tmp)
        kb2 = int(max(400, (max_mb * 8 * 1024) / dur - 136) * 0.8)
        run(["ffmpeg", "-y", "-i", tmp, "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{kb2}k",
             "-maxrate", f"{kb2}k", "-bufsize", f"{2 * kb2}k", "-c:a", "copy", "-movflags", "+faststart", out])
        tmp.unlink(missing_ok=True)


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


def title_layers(lines, max_w, max_h, key_last=True, start=210):
    probe = ImageDraw.Draw(Image.new("L", (4, 4)))
    size = start
    while size > 60:
        f = load_font(size, "title")
        if max(probe.textlength(l, font=f) for l in lines) <= max_w and size * 1.2 * len(lines) <= max_h:
            break
        size -= 6
    f = load_font(size, "title")
    stroke = max(6, min(15, int(size * 0.075)))
    ext = max(4, int(size * 0.05))
    layers = []
    for k, l in enumerate(lines):
        key = key_last and k == len(lines) - 1
        if key:
            lay = text_layer(l, f, fill=(255, 245, 90), fill2=(255, 160, 0), stroke=stroke, stroke_fill=(10, 12, 32),
                             shadow=(6, 10, 8, 0.8), extrude=(ext, (150, 65, 0)))
        else:
            lay = text_layer(l, f, fill=(255, 255, 255), fill2=(205, 225, 255), stroke=stroke, stroke_fill=(10, 12, 32),
                             shadow=(6, 10, 8, 0.8), extrude=(ext, (30, 45, 110)))
        layers.append((lay, key))
    return layers, size


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


def make_thumb_hero(img_path, text, sub, badge, out):
    np = _np()
    im = grade_photo(fit_cover(img_path))
    im = bloom(im)
    xs = np.linspace(0, 1, W)
    a = (np.clip(1 - xs / 0.74, 0, 1) ** 1.25 * 0.82 * 255).astype(np.uint8)
    im = Image.composite(Image.new("RGB", (W, H), (6, 14, 52)), im, Image.fromarray(np.tile(a, (H, 1))))
    ys = np.linspace(0, 1, H)
    b = (np.clip((ys - 0.62) / 0.38, 0, 1) ** 1.4 * 0.55 * 255).astype(np.uint8)
    im = Image.composite(Image.new("RGB", (W, H), (4, 8, 30)), im, Image.fromarray(np.tile(b[:, None], (1, W))))
    rgba = im.convert("RGBA")
    layers, size = title_layers(split_lines(text), W * 0.60, H * (0.52 if sub else 0.62))
    total_h = sum(l.height for l, _ in layers) - int(size * 0.42) * (len(layers) - 1)
    y = max(60, (H - total_h) // 2 - (28 if sub else 0))
    for lay, key in layers:
        if key:
            g = lay.getchannel("A").filter(ImageFilter.GaussianBlur(max(8, size // 8))).point(lambda v: int(v * 0.6))
            gl = Image.new("RGBA", lay.size, (255, 190, 0, 255))
            gl.putalpha(g)
            rgba.alpha_composite(gl, (36, y))
        rgba.alpha_composite(lay, (36, y))
        y += lay.height - int(size * 0.42)
    if sub:
        sp = pill(sub, 42, (214, 30, 50))
        rgba.alpha_composite(sp, (52, min(H - sp.height - 34, y + 12)))
    if badge:
        bp = pill(badge, 44, (230, 36, 56))
        rgba.alpha_composite(bp, (46, 36))
    final = rgba.convert("RGB").filter(ImageFilter.UnsharpMask(radius=2, percent=80, threshold=3))
    final.save(out, "JPEG", quality=93)


def make_thumb_split(img_a, img_b, text, left, right, out):
    """Then / Now style: two worlds split by a glowing diagonal, big headline on top."""
    np = _np()
    A = ImageEnhance.Color(grade_photo(fit_cover(img_a))).enhance(1.1)
    B = ImageEnhance.Color(grade_photo(fit_cover(img_b))).enhance(1.1)
    A = Image.blend(A, Image.new("RGB", (W, H), (255, 80, 20)), 0.16)
    B = Image.blend(B, Image.new("RGB", (W, H), (20, 140, 255)), 0.16)
    k = 2
    m = Image.new("L", (W * k, H * k), 0)
    ImageDraw.Draw(m).polygon([(0, 0), (W * 0.57 * k, 0), (W * 0.43 * k, H * k), (0, H * k)], fill=255)
    m = m.resize((W, H), Image.LANCZOS)
    im = Image.composite(A, B, m)
    seam = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(seam)
    sd.line([(W * 0.57, 0), (W * 0.43, H)], fill=(255, 255, 255, 255), width=9)
    glow = seam.filter(ImageFilter.GaussianBlur(14))
    glow2 = seam.filter(ImageFilter.GaussianBlur(34))
    base = im.convert("RGBA")
    for g, col in ((glow2, (255, 200, 80)), (glow, (255, 255, 255))):
        c = Image.new("RGBA", (W, H), col + (255,))
        c.putalpha(g.getchannel("A").point(lambda v: min(255, int(v * 1.6))))
        base.alpha_composite(c)
    base.alpha_composite(seam)
    im = bloom(base.convert("RGB"), 0.3, 22)
    ys = np.linspace(0, 1, H)
    top = (np.clip(1 - ys / 0.42, 0, 1) ** 1.3 * 0.7 * 255).astype(np.uint8)
    im = Image.composite(Image.new("RGB", (W, H), (4, 8, 30)), im, Image.fromarray(np.tile(top[:, None], (1, W))))
    rgba = im.convert("RGBA")
    layers, size = title_layers(split_lines(text)[:2] if len(split_lines(text)) > 2 else split_lines(text), W * 0.86, H * 0.36, start=130)
    y = 14
    for lay, key in layers:
        rgba.alpha_composite(lay, ((W - lay.width) // 2, y))
        y += lay.height - int(size * 0.42)
    if left:
        lp = pill(left, 70, (214, 24, 44), role="title")
        rgba.alpha_composite(lp, (40, H - lp.height - 36))
    if right:
        rp = pill(right, 70, (22, 100, 230), role="title")
        rgba.alpha_composite(rp, (W - rp.width - 40, H - rp.height - 36))
    final = rgba.convert("RGB").filter(ImageFilter.UnsharpMask(radius=2, percent=80, threshold=3))
    final.save(out, "JPEG", quality=93)


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


def make_visuals(scenes, imgdir):
    """Generate every picture the plan asks for. Returns (failed, requested)."""
    failed = requested = 0
    prev_img = None
    for i, sc in enumerate(scenes):
        if sc["type"] == "photo":
            for k, sh in enumerate(sc["shots"]):
                p = imgdir / f"{i:03d}_s{k}.png"
                if prev_img and CF_STATE["used"] >= MAX_IMAGES:
                    sh["image"] = prev_img  # free-quota saver: reuse the previous picture with a new camera move
                    continue
                requested += 1
                CF_STATE["used"] += 1
                if gen_image(sh["prompt_en"], p, "photo"):
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
                if gen_image(it["prompt_en"], p, "card"):
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

    cat, topic_en, topic_hi = pick_topic(done)
    style = random.choice(STYLES)
    pal = random.choice(PALETTES)
    target = random.randint(MIN_SEC, MAX_SEC)
    use_g = TTS_PROVIDER in ("auto", "gemini") and bool(GEMINI_KEY)
    wpm = float(wpms.get("gemini" if use_g else "edge", WPM))
    log(f"TOPIC: {topic_en} | category={cat} | style={style[0]} | theme={pal['name']} | target={target}s | wpm={wpm:.0f}")

    facts, sources = research(topic_en)
    script = write_script(topic_en, cat, style, target, facts, wpm)
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
    if not (MIN_SEC - 30 <= total <= MAX_SEC + 45):
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
    sfx = WORK / "sfx.wav"
    build_sfx_track(sfx_events, total, sfx)
    outdir = WORK / "out"
    outdir.mkdir()
    final = outdir / "video.mp4"
    mux_final(silent, voice, music, sfx, final, max_mb=44 if UPLOAD_MODE == "telegram" else None)
    log(f"final video {fmt_time(ffprobe_dur(final))}, {final.stat().st_size / 1e6:.1f} MB")

    any_img = next((sh["image"] for s in scenes for sh in s["shots"] if sh.get("image")), None)
    hero = imgdir / "thumb.png"
    if not gen_image(script["thumb_prompt_en"], hero, "thumb"):
        hero = any_img
    thumb = outdir / "thumbnail.jpg"
    text = script["thumb_text_hi"] or script["title_hi"]
    make_thumb_hero(hero, text, script["thumb_sub_hi"], script["thumb_badge_hi"], thumb)
    thumb2 = None
    if script["thumb_prompt2_en"]:
        other = imgdir / "thumb2.png"
        if gen_image(script["thumb_prompt2_en"], other, "thumb"):
            try:
                thumb2 = outdir / "thumbnail_thenow.jpg"
                make_thumb_split(hero, other, text, script["thumb_left_hi"], script["thumb_right_hi"], thumb2)
            except Exception as e:  # noqa
                log("split thumbnail failed:", e)
                thumb2 = None

    tags3 = " ".join("#" + re.sub(r"\s+", "", t) for t in script["tags"][:3])
    lines = [script["description_hi"], ""]
    if sources:
        lines.append("📚 जानकारी के लिए खोज में देखे गए स्रोत: " + ", ".join(sources))
    lines += ["🤖 इस वीडियो में AI से बनी तस्वीरें और AI आवाज़ का उपयोग किया गया है।", "", tags3]
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
        ok = tg_send_file(final, "video", meta["title"])
        tg_send_file(thumb, "doc", "Thumbnail 1")
        if thumb2:
            tg_send_file(thumb2, "doc", "Thumbnail 2 (then/now)")
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
    n10 = "तो अब आप जान गए कि चाँद कैसे बना। वीडियो अच्छा लगा हो तो सब्सक्राइब ज़रूर करें"
    sc = [
        {"type": "photo", "tone": "suspense", "headline_hi": "", "narration_hi": n1,
         "shots": shots([("क्या आप जानते हैं कि चाँद कैसे बना था?", "planet", (20, 30, 90), (120, 60, 160)),
                         ("यह कहानी बहुत हैरान करने वाली है, आगे जानेंगे", "moon", (10, 10, 40), (60, 20, 90))])},
        {"type": "merge", "tone": "wonder", "headline_hi": "टक्कर से चाँद बना",
         "items": items([("युवा पृथ्वी",) + P, ("थिया ग्रह",) + R, ("हमारा चाँद",) + M]),
         "narration_hi": "लगभग साढ़े चार अरब साल पहले, पृथ्वी से एक मंगल जितना बड़ा ग्रह टकराया, और इसी टक्कर से हमारा चाँद बना"},
        {"type": "photo", "tone": "serious", "headline_hi": "आग का दौर", "narration_hi": n3,
         "shots": shots([("अब ज़रा सोचिए, उस समय आसमान कितना डरावना दिखता होगा।", "rock", (255, 120, 40), (120, 20, 10)),
                         ("हर तरफ आग और धुआँ ही धुआँ था", "planet", (90, 20, 10), (20, 5, 5))])},
        {"type": "counter", "tone": "excited", "headline_hi": "पृथ्वी की उम्र",
         "counter": {"from": 0.0, "to": 4.5, "decimals": 1, "unit_hi": "अरब साल पहले"},
         "narration_hi": "पृथ्वी की उम्र लगभग साढ़े चार अरब साल मानी जाती है"},
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
        {"type": "kinetic", "tone": "excited", "keywords_hi": ["टक्कर", "मलबा", "चाँद!"], "headline_hi": "",
         "image_prompt_en": "x", "image": art(t0 / "k.png", "rock", (255, 120, 40), (60, 10, 60), 1920),
         "narration_hi": "याद रखिए, टक्कर से मलबा बना, और उसी मलबे से हमारा चाँद"},
        {"type": "compare", "tone": "calm", "headline_hi": "पृथ्वी बनाम चाँद",
         "items": items([("पृथ्वी", "planet", (200, 255, 220), (100, 200, 160)), ("चाँद",) + M]),
         "narration_hi": "पृथ्वी पर हवा और पानी है, लेकिन चाँद पर न हवा है और न पानी"},
        {"type": "photo", "tone": "calm", "headline_hi": "", "narration_hi": n10,
         "shots": shots([("तो अब आप जान गए कि चाँद कैसे बना।", "moon", (10, 20, 60), (40, 40, 110)),
                         ("वीडियो अच्छा लगा हो तो सब्सक्राइब ज़रूर करें", "planet", (20, 30, 90), (60, 90, 160))])},
    ]

    def fake_tts(text, out, tone=None):
        d = max(3.0, len(text.split()) * 0.42)
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=190:duration={d}", "-af", "volume=0.4", "-f", "wav", out])

    voice = make_voice(sc, t0 / "voice", synth=fake_tts)
    total = ffprobe_dur(voice)
    silent, ev = build_video(sc, t0 / "build", pal)
    music = t0 / "music.wav"
    mood = os.environ.get("MOOD", random.choice(MOOD_NAMES))
    make_music(total, music, mood)
    sfx = t0 / "sfx.wav"
    build_sfx_track(ev, total, sfx)
    final = t0 / "final.mp4"
    mux_final(silent, voice, music, sfx, final)
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
