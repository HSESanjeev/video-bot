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
        for attempt in range(3):
            try:
                r = requests.post(f"{GBASE}/models/{model}:generateContent",
                                  headers={"x-goog-api-key": GEMINI_KEY}, json=body, timeout=240)
                if r.status_code in (429, 500, 503):
                    last = f"{model} HTTP {r.status_code}"
                    time.sleep(25 * (attempt + 1))
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
- Scene 1 is a SUSPENSE HOOK: 25-30 words, built on a TRUE surprising fact or question, does NOT reveal the answer, ends by promising it ("आगे जानेंगे"). Scene 1 must be type "photo" and visually dramatic.
- Scene 2 says in one or two lines what the viewer will understand by the end.
- Around 55% of the video add a short re-hook line ("लेकिन असली राज़ अभी बाक़ी है...") that is true to what follows.
- Final scenes: recap the 3 key points in simple words, then one short line asking viewers to subscribe.
- The viewer must truly UNDERSTAND the topic by the end: define every new term when first used, use one simple everyday analogy, go in a logical order.
- Facts: only well-established science, rounded widely-accepted numbers. If something is debated say "वैज्ञानिकों का मानना है". Never invent names, dates, statistics or quotes. If unsure, leave it out.
- Neutral on religion and politics; never mention or mock any belief. Say "वैज्ञानिक मान्यता के अनुसार" for origin topics.

VISUAL PLAN (this is what makes the video explain itself - the picture must TEACH, not just decorate)
Every scene has a "type":
- "merge":  two things come together and create a third (e.g. early Earth + a Mars-sized planet -> the Moon; sperm + egg -> embryo; hydrogen + oxygen -> water). items = [A, B, RESULT]. Write the narration so the first half names A and B and the second half announces the result.
- "split":  one thing breaks into two parts (e.g. a supercontinent -> two continents; a star -> remnant + dust). items = [WHOLE, PART1, PART2].
- "steps":  a sequence, life cycle, timeline or evolution stages (3 or 4 items in order, e.g. egg -> larva -> pupa -> butterfly).
- "compare": two things side by side (e.g. worker ant vs queen ant). items = [A, B].
- "photo":  one beautiful full-screen image for atmosphere, scale or emotion. Use "image_prompt_en".
Use an explainer type (merge/split/steps/compare) for AT LEAST HALF of the scenes, in every place where the topic has a cause->effect, a combination, a breakup, a sequence or a comparison. Use "photo" for the hook, for scale/atmosphere, and for the closing scene.
Explainer scenes need shorter narration (14-22 words) and a "headline_hi" of 2-5 Hindi words shown on screen (e.g. "टक्कर से चाँद बना").
Each item has "label_hi" (1-3 Hindi words shown under the picture) and "prompt_en": a concrete, simple description of ONE clear subject on a plain background (an illustration of that single thing, e.g. "a young glowing blue-green Earth", "a Mars-sized rocky planet", "the grey cratered Moon"). No text or letters in any picture, no real people.
"""


def build_script_prompt(topic, cat, style, target_sec, facts, words):
    n_scenes = max(14, round(words / 21))
    stock = ('- "stock_query_en": 2-4 plain English words to find REAL stock footage of living animals/plants (only for photo scenes of present-day nature), else "".'
             if PEXELS_KEY else '- "stock_query_en": always "".')
    return f"""You are a script writer for a Hindi nature & science explainer YouTube channel.
TOPIC: {topic}
CATEGORY: {cat}
STRUCTURE STYLE: {style[1]}
TARGET: about {words} Hindi words of narration in total ({target_sec} seconds), around {n_scenes} scenes.

{SCRIPT_RULES}
{stock}

VERIFIED FACTS TO PREFER (may be empty):
{facts[:6000]}

Return ONLY JSON in exactly this shape:
{{
 "title_hi": "Hindi title, max 60 characters, curious but TRUTHFUL, no clickbait lies",
 "thumb_text_hi": "3-5 powerful Hindi words for the thumbnail",
 "thumb_badge_hi": "one short Hindi word like रहस्य / सच / विज्ञान",
 "thumb_prompt_en": "one striking, dramatic hero subject for the thumbnail background (a single subject)",
 "description_hi": "3-4 short Hindi lines telling what the viewer will learn",
 "tags": ["8-12 tags mixing Hindi and English"],
 "scenes": [
  {{"narration_hi": "...", "type": "photo", "headline_hi": "", "image_prompt_en": "...", "stock_query_en": "", "items": []}},
  {{"narration_hi": "...", "type": "merge", "headline_hi": "...", "image_prompt_en": "",
    "items": [{{"label_hi": "...", "prompt_en": "..."}}, {{"label_hi": "...", "prompt_en": "..."}}, {{"label_hi": "...", "prompt_en": "..."}}]}}
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


def validate_script(s):
    if not isinstance(s, dict) or not isinstance(s.get("scenes"), list) or len(s["scenes"]) < 8:
        raise ValueError("script has too few scenes")
    for idx, sc in enumerate(s["scenes"]):
        sc["narration_hi"] = clean_narration(sc.get("narration_hi", ""))
        if not sc["narration_hi"]:
            raise ValueError("empty narration")
        typ = str(sc.get("type") or "photo").lower().strip()
        items = []
        for it in (sc.get("items") or []):
            if isinstance(it, dict) and str(it.get("prompt_en") or "").strip():
                items.append({"label_hi": clean_short(it.get("label_hi"), 26) or " ",
                              "prompt_en": clean_short(it.get("prompt_en"), 400)})
        if typ in NEED_ITEMS:
            lo, hi = NEED_ITEMS[typ]
            items = items[:hi]
            if len(items) < lo:
                typ = "photo"
        else:
            typ = "photo"
        if idx == 0:
            typ = "photo"
        sc["type"] = typ
        sc["items"] = items if typ != "photo" else []
        prompt = clean_short(sc.get("image_prompt_en"), 400)
        if typ == "photo" and not prompt:
            prompt = items[-1]["prompt_en"] if items else "a dramatic wide nature landscape"
        sc["image_prompt_en"] = prompt
        sc["headline_hi"] = clean_short(sc.get("headline_hi"), 36) if typ != "photo" else ""
        sc["stock_query_en"] = clean_short(sc.get("stock_query_en"), 60) if (typ == "photo" and PEXELS_KEY) else ""
    for k in ("title_hi", "thumb_text_hi", "description_hi"):
        s[k] = (s.get(k) or "").strip()
    if not s["title_hi"]:
        raise ValueError("no title")
    s["thumb_badge_hi"] = clean_short(s.get("thumb_badge_hi"), 14) or "जानिए"
    s["tags"] = [str(t).strip() for t in (s.get("tags") or []) if str(t).strip()][:12]
    s["thumb_prompt_en"] = (s.get("thumb_prompt_en") or s["scenes"][0]["image_prompt_en"]).strip()
    return s


def word_count(s):
    return sum(len(sc["narration_hi"].split()) for sc in s["scenes"])


def write_script(topic, cat, style, target_sec, facts, wpm):
    words = int(target_sec / 60 * wpm)
    script = validate_script(gemini_json(build_script_prompt(topic, cat, style, target_sec, facts, words), 0.8))
    review = f"""You are (1) a careful science editor and (2) a Hindi school teacher.
Improve this explainer-video script JSON:
- Remove or soften any claim that is not widely accepted science; fix any factual doubt.
- Replace difficult words with simple ones; make sure a 15-year-old understands everything on first listen.
- Keep scene 1 a truthful suspense hook (25-30 words, type "photo") that does not reveal the answer.
- Check that every visual matches its narration (a merge scene must really show A and B combining into the result named in the narration). Fix types/items/labels/headlines where they do not teach.
- Keep roughly the same number of scenes (+/-2) and total narration length (+/-10%, now about {word_count(script)} words).
- Keep numbers written in Hindi words, no English letters in narration, keep every field and the exact JSON shape.
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
              "Keep the hook, structure, rules, scene types/items and the exact JSON shape (add or remove whole scenes if needed). Return ONLY JSON.\n"
              + json.dumps(script, ensure_ascii=False))
    return validate_script(gemini_json(prompt, 0.4))


# --------------------------------------------------------------------------- voice
def tts_one(text, out_path):
    """Edge neural voice (free, always available)."""
    import edge_tts

    async def go():
        await edge_tts.Communicate(text, VOICE, rate=TTS_RATE).save(str(out_path))

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


def tts_gemini(text, out_path):
    """Gemini speech (more natural, free tier has a small quota). Raises on any problem."""
    if not GEMINI_KEY:
        raise RuntimeError("no GEMINI_API_KEY")
    gap = time.time() - _tts_last[0]
    if gap < GEMINI_TTS_GAP:
        time.sleep(GEMINI_TTS_GAP - gap)
    prompt = f"Say in a clear, warm, calm voice, like a Hindi science-documentary narrator, with natural pauses: {text}"
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


def make_voice(scenes, tdir, synth=None):
    synth = synth or tts_one
    tdir.mkdir(parents=True, exist_ok=True)
    wavs = []
    for i, sc in enumerate(scenes):
        src = tdir / f"s{i:03d}.src"
        wav = tdir / f"s{i:03d}.wav"
        synth(sc["narration_hi"], src)
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
    voice = tdir / "voice.wav"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", voice])
    return voice


def make_voice_auto(scenes, tdir):
    """Natural Gemini voice if the free quota allows, otherwise the Edge voice for the WHOLE video."""
    VOICE_USED[0] = "edge"
    if TTS_PROVIDER in ("auto", "gemini") and GEMINI_KEY:
        try:
            v = make_voice(scenes, tdir, synth=tts_gemini)
            VOICE_USED[0] = "gemini"
            return v
        except Exception as e:  # noqa
            VOICE_USED[0] = "edge (Gemini voice failed: " + str(e)[:140] + ")"
            log("Gemini voice failed, using Edge voice for the whole video:", e)
            shutil.rmtree(tdir, ignore_errors=True)
    return make_voice(scenes, tdir, synth=tts_one)


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
               shadow=None, max_w=None, spacing=6, align="center"):
    """Transparent RGBA picture of text: optional vertical gradient fill, outline and soft drop shadow."""
    probe = ImageDraw.Draw(Image.new("L", (4, 4)))
    if max_w:
        text = "\n".join(wrap_text(probe, text, font, max_w))
    bb = probe.multiline_textbbox((0, 0), text, font=font, stroke_width=stroke, spacing=spacing, align=align)
    pad = stroke + 6 + (int(shadow[2] * 2 + abs(shadow[0]) + abs(shadow[1])) if shadow else 0)
    w, h = bb[2] - bb[0] + 2 * pad, bb[3] - bb[1] + 2 * pad
    ox, oy = pad - bb[0], pad - bb[1]
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if shadow:
        dx, dy, blur, alpha = shadow
        sh = Image.new("L", (w, h), 0)
        ImageDraw.Draw(sh).multiline_text((ox + dx, oy + dy), text, font=font, fill=255, stroke_width=stroke,
                                          spacing=spacing, align=align)
        sh = sh.filter(ImageFilter.GaussianBlur(blur)).point(lambda v: int(v * alpha))
        s_rgba = Image.new("RGBA", (w, h), (0, 0, 0, 255))
        s_rgba.putalpha(sh)
        layer = Image.alpha_composite(layer, s_rgba)
    if stroke:
        st = Image.new("L", (w, h), 0)
        ImageDraw.Draw(st).multiline_text((ox, oy), text, font=font, fill=255, stroke_width=stroke,
                                          stroke_fill=255, spacing=spacing, align=align)
        c = Image.new("RGBA", (w, h), tuple(stroke_fill) + (255,))
        c.putalpha(st)
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


def make_sub_png(text, path, ink=(14, 40, 96)):
    """Subtitle: big white text with a deep-blue outline (no black box), readable on bright and dark pictures."""
    lay = text_layer(text, load_font(46, "sub"), fill=(255, 255, 255), stroke=6, stroke_fill=ink,
                     shadow=(0, 4, 4, 0.55), max_w=W - 240, spacing=8)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    img.alpha_composite(lay, ((W - lay.width) // 2, H - 34 - lay.height))
    img.save(path)


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


def img_cloudflare(prompt, out, size=(1920, 1080)):
    if not (CF_ACCOUNT and CF_TOKEN):
        return False
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}/ai/run/@cf/black-forest-labs/flux-1-schnell"
    for a in range(3):
        try:
            r = requests.post(url, headers={"Authorization": f"Bearer {CF_TOKEN}"},
                              json={"prompt": prompt[:2000], "steps": 6}, timeout=120)
        except requests.RequestException as e:
            note_fail("cloudflare", type(e).__name__)
            time.sleep(8)
            continue
        if r.status_code in (429, 500, 502, 503, 504):
            note_fail("cloudflare", f"HTTP {r.status_code}")
            time.sleep(15 * (a + 1))
            continue
        if not r.ok:
            note_fail("cloudflare", f"HTTP {r.status_code}")
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
    chain = [("cloudflare", lambda: img_cloudflare(full, out, size))]
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


# --------------------------------------------------------------------------- music
def make_music(duration, out):
    root = random.choice([98.0, 110.0, 123.47, 130.81])
    freqs = [root, root * 1.5, root * 2, root * 2.378, root * 3]
    d = duration + 2
    cmd = ["ffmpeg", "-y"]
    for f in freqs:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={f:.2f}:duration={d:.1f}:sample_rate=44100"]
    cmd += ["-f", "lavfi", "-i", f"anoisesrc=color=brown:amplitude=0.25:duration={d:.1f}:sample_rate=44100"]
    fc = ("[0][1][2][3][4]amix=inputs=5:normalize=0,volume=1.6,tremolo=f=0.11:d=0.45,lowpass=f=900[pad];"
          "[5]lowpass=f=450,volume=0.5[wind];"
          "[pad][wind]amix=inputs=2:normalize=0,aecho=0.8:0.85:700|1300:0.3|0.2,"
          f"afade=t=in:d=3,afade=t=out:st={max(1, d - 4):.1f}:d=4,aformat=channel_layouts=stereo[m]")
    cmd += ["-filter_complex", fc, "-map", "[m]", "-t", f"{d:.1f}", out]
    run(cmd)


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


def render_explainer(sc, dur, out, pal):
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
        vs = sym_vs(pal)
        xa, xb = W * 0.27, W * 0.73
        actors += [(A, lambda t: (lerp(-260, xa, e_out((t - 0.15) / 0.8)), cy + fl(t, 0),
                                  1.0 + 0.045 * max(0.0, math.sin(t * 1.4)) * cl(t - 1.5), 1.0)),
                   (B, lambda t: (lerp(W + 260, xb, e_out((t - 0.35) / 0.8)), cy + fl(t, 1.6),
                                  1.0 + 0.045 * max(0.0, -math.sin(t * 1.4)) * cl(t - 1.5), 1.0)),
                   (vs, lambda t: (W / 2, cy, e_back((t - 1.0) / 0.4) * (1 + 0.05 * math.sin(t * 3)), 1.0))]

    base = bg_base(tuple(pal["top"]), tuple(pal["bottom"]))
    n_frames = max(2, int(round(dur * FPS)))
    cmd = ["ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-pix_fmt", "yuv420p", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for i in range(n_frames):
            t = i / FPS
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
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
        err = proc.stderr.read().decode(errors="ignore")
        if proc.wait() != 0:
            raise RuntimeError("explainer encode failed: " + err[-800:])
    except BrokenPipeError:
        raise RuntimeError("explainer encode failed (pipe): " + proc.stderr.read().decode(errors="ignore")[-800:])
    finally:
        if proc.poll() is None:
            proc.kill()


# --------------------------------------------------------------------------- video build
GRADE = "eq=contrast=1.05:saturation=1.18:brightness=0.03:gamma=1.04"


def motion_expr(kind, n):
    return {
        "in": ("1+0.18*on/%d" % n, "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
        "out": ("1.18-0.18*on/%d" % n, "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
        "lr": ("1.16", "(iw-iw/zoom)*on/%d" % n, "ih/2-(ih/zoom/2)"),
        "rl": ("1.16", "(iw-iw/zoom)*(1-on/%d)" % n, "ih/2-(ih/zoom/2)"),
        "in_tl": ("1+0.22*on/%d" % n, "(iw-iw/zoom)*0.15", "(ih-ih/zoom)*0.2"),
        "in_br": ("1+0.22*on/%d" % n, "(iw-iw/zoom)*0.85", "(ih-ih/zoom)*0.8"),
    }[kind]


def render_subshot(src, out, dur, subs, kind=None, offset=0.0, fade_in=False, fade_out=False, grade=True):
    """src: ('image', path) or ('clip', path). subs: [(png, a, b)] relative to start of this sub-shot."""
    n = max(1, int(round(dur * FPS)))
    cmd = ["ffmpeg", "-y"]
    g = ("," + GRADE) if grade else ""
    if src[0] == "image":
        cmd += ["-i", src[1]]
        z, x, y = motion_expr(kind or "in", n)
        base = (f"[0:v]scale=2560:1440:force_original_aspect_ratio=increase,crop=2560:1440,"
                f"zoompan=z='{z}':x='{x}':y='{y}':d={n}:s={W}x{H}:fps={FPS}{g},format=yuv420p")
    else:
        cmd += ["-stream_loop", "-1", "-ss", f"{offset:.2f}", "-i", src[1]]
        base = f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS}{g},format=yuv420p"
    if fade_in:
        base += ",fade=t=in:st=0:d=0.5"
    if fade_out:
        base += f",fade=t=out:st={max(0, dur - 0.9):.2f}:d=0.9"
    chain, last = [base + "[v0]"], "v0"
    for k, (png, a, b) in enumerate(subs):
        cmd += ["-i", png]
        chain.append(f"[{last}][{k + 1}:v]overlay=0:0:enable='between(t,{a:.3f},{b:.3f})':format=auto[v{k + 1}]")
        last = f"v{k + 1}"
    cmd += ["-filter_complex", ";".join(chain), "-map", f"[{last}]", "-t", f"{dur:.3f}", "-r", str(FPS),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-an", out]
    run(cmd)


def build_video(scenes, tdir, pal):
    tdir.mkdir(parents=True, exist_ok=True)
    kinds = ["in", "lr", "out", "in_tl", "rl", "in_br"]
    random.shuffle(kinds)
    parts, ki = [], 0
    total = len(scenes)
    for i, sc in enumerate(scenes):
        times = chunk_times(sc["narration_hi"], sc["speech_dur"], sc["dur"])
        pngs = []
        for j, (c, a, b) in enumerate(times):
            p = tdir / f"sub_{i:03d}_{j:02d}.png"
            make_sub_png(c, p, pal["ink"])
            pngs.append((str(p), a, b))
        first, lastsc = (i == 0), (i == total - 1)
        if sc["type"] != "photo":
            clip = tdir / f"exp_{i:03d}.mp4"
            render_explainer(sc, sc["dur"], clip, pal)
            out = tdir / f"shot_{i:03d}_00.mp4"
            render_subshot(("clip", str(clip)), out, sc["dur"], pngs, fade_out=lastsc, grade=False)
            parts.append(out)
        else:
            nsub = max(1, math.ceil(sc["dur"] / 4.5))
            sd = sc["dur"] / nsub
            for k in range(nsub):
                s0, s1 = k * sd, (k + 1) * sd
                subs = [(p, max(a, s0) - s0, min(b, s1) - s0) for (p, a, b) in pngs if a < s1 and b > s0]
                out = tdir / f"shot_{i:03d}_{k:02d}.mp4"
                fi, fo = (first and k == 0), (lastsc and k == nsub - 1)
                clips = sc.get("clips") or []
                if clips:
                    cl_ = clips[(k + i) % len(clips)]
                    off = random.uniform(0, cl_["dur"] - sd - 0.5) if cl_["dur"] > sd + 1 else 0.0
                    render_subshot(("clip", str(cl_["path"])), out, sd, subs, offset=off, fade_in=fi, fade_out=fo)
                else:
                    render_subshot(("image", str(sc["image"])), out, sd, subs, kind=kinds[ki % len(kinds)],
                                   fade_in=fi, fade_out=fo)
                    ki += 1
                parts.append(out)
        if i % 5 == 0:
            log(f"video scene {i + 1}/{total}")
    lst = tdir / "shots.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    silent = tdir / "silent.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", silent])
    return silent


def mux_final(silent, voice, music, out, max_mb=None):
    dur = ffprobe_dur(voice)
    fc = ("[1:a]highpass=f=70,acompressor=threshold=0.12:ratio=3:attack=10:release=200,aresample=44100,asplit=2[v][vsc];"
          "[2:a]volume=0.30[m];"
          "[m][vsc]sidechaincompress=threshold=0.04:ratio=10:attack=15:release=500[duck];"
          "[v][duck]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix];"
          "[mix]loudnorm=I=-16:TP=-1.5:LRA=11,aresample=44100[a]")
    vopts = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21"]
    if max_mb:
        kbps = int(max(500, min(5000, (max_mb * 8 * 1024) / dur - 136)))
        vopts = ["-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k",
                 "-bufsize", f"{2 * kbps}k"]
    run(["ffmpeg", "-y", "-i", silent, "-i", voice, "-i", music, "-filter_complex", fc,
         "-map", "0:v", "-map", "[a]", *vopts, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
         "-movflags", "+faststart", "-shortest", out])
    if max_mb and Path(out).stat().st_size > max_mb * 1024 * 1024:
        log("file still large, re-encoding smaller")
        tmp = Path(str(out) + ".big.mp4")
        Path(out).rename(tmp)
        kb2 = int(max(400, (max_mb * 8 * 1024) / dur - 136) * 0.8)
        run(["ffmpeg", "-y", "-i", tmp, "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{kb2}k",
             "-maxrate", f"{kb2}k", "-bufsize", f"{2 * kb2}k", "-c:a", "copy", "-movflags", "+faststart", out])
        tmp.unlink(missing_ok=True)


# --------------------------------------------------------------------------- thumbnail
def grade_photo(im):
    """Punchy colour grade: contrast, saturation and teal shadows / warm highlights."""
    im = ImageOps.autocontrast(im, cutoff=1)
    im = ImageEnhance.Contrast(im).enhance(1.12)
    im = ImageEnhance.Color(im).enhance(1.32)
    im = ImageEnhance.Brightness(im).enhance(1.05)
    lum = im.convert("L")
    shadows = lum.point(lambda v: int(((255 - v) / 255) ** 2 * 80))
    highs = lum.point(lambda v: int((v / 255) ** 2 * 50))
    im = Image.composite(Image.new("RGB", im.size, (0, 110, 150)), im, shadows)
    im = Image.composite(Image.new("RGB", im.size, (255, 170, 70)), im, highs)
    return im


def make_thumbnail(img_path, text, badge, out):
    im = ImageOps.fit(Image.open(img_path).convert("RGB"), (W, H), Image.LANCZOS)
    im = grade_photo(im)
    # soft light bloom on the right (where the subject is)
    bloom = glow_sprite(900, (255, 236, 200), 0.38)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    layer.alpha_composite(bloom, (int(W * 0.74 - 450), int(H * 0.5 - 450)))
    im = Image.alpha_composite(im.convert("RGBA"), layer).convert("RGB")
    # deep-blue gradient panel on the left for the text (not black)
    panel = Image.new("L", (W, H), 0)
    pd = ImageDraw.Draw(panel)
    for x in range(W):
        pd.line([(x, 0), (x, H)], fill=int(215 * max(0.0, 1 - x / (W * 0.66)) ** 1.4))
    im = Image.composite(Image.new("RGB", (W, H), (8, 22, 78)), im, panel)

    words = text.split()
    if len(words) <= 1:
        lines = [text]
    else:
        cut = max(1, len(words) // 2)
        lines = [" ".join(words[:cut]), " ".join(words[cut:])]
    probe = ImageDraw.Draw(Image.new("L", (4, 4)))
    size = 190
    while size > 70:
        f = load_font(size, "title")
        ws = [probe.textlength(l, font=f) for l in lines]
        if max(ws) <= W * 0.56 and size * 1.25 * len(lines) <= H * 0.66:
            break
        size -= 6
    f = load_font(size, "title")
    navy = (8, 18, 60)
    layers = []
    for k, l in enumerate(lines):
        if k == len(lines) - 1 and len(lines) > 1 or len(lines) == 1:
            lay = text_layer(l, f, fill=(255, 238, 90), fill2=(255, 150, 0), stroke=11, stroke_fill=navy,
                             shadow=(5, 8, 7, 0.75))
        else:
            lay = text_layer(l, f, fill=(255, 255, 255), fill2=(205, 228, 255), stroke=11, stroke_fill=navy,
                             shadow=(5, 8, 7, 0.75))
        layers.append(lay)
    total_h = sum(l.height for l in layers) - 24 * (len(layers) - 1)
    y = (H - total_h) // 2 + 20
    rgba = im.convert("RGBA")
    for lay in layers:
        rgba.alpha_composite(lay, (40, y))
        y += lay.height - 24
    # accent underline
    ul_w = int(min(W * 0.5, max(l.width for l in layers) * 0.9))
    ul = Image.new("RGBA", (ul_w, 16), (0, 0, 0, 0))
    ul.paste(Image.new("RGBA", ul.size, (255, 70, 60, 255)), (0, 0), rounded_mask(ul.size, 8))
    rgba.alpha_composite(ul, (56, min(H - 60, y + 6)))
    # badge
    if badge:
        bl = text_layer(badge, load_font(46, "label"), fill=(255, 255, 255), stroke=0)
        bw, bh = bl.width + 56, bl.height + 18
        pill = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        wm = Image.new("RGBA", (bw, bh), (255, 255, 255, 255))
        wm.putalpha(rounded_mask((bw, bh), bh // 2))
        pill.alpha_composite(wm)
        rd = Image.new("RGBA", (bw - 8, bh - 8), (226, 36, 56, 255))
        rd.putalpha(rounded_mask((bw - 8, bh - 8), (bh - 8) // 2))
        pill.alpha_composite(rd, (4, 4))
        pill.alpha_composite(bl, ((bw - bl.width) // 2, (bh - bl.height) // 2))
        rgba.alpha_composite(pill, (48, 38))
    final = rgba.convert("RGB").filter(ImageFilter.UnsharpMask(radius=2, percent=70, threshold=3))
    final.save(out, "JPEG", quality=92)


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
    method, field = {"video": ("sendVideo", "video"), "doc": ("sendDocument", "document")}[kind]
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


def make_visuals(scenes, imgdir, clipdir):
    """Generate every picture the plan asks for. Returns (credits, licenses, failed, requested)."""
    credits, licenses = [], []
    failed = requested = 0
    prev_img = None
    for i, sc in enumerate(scenes):
        if sc["type"] == "photo":
            requested += 1
            got = False
            if sc.get("stock_query_en") and PEXELS_KEY:
                try:
                    clips = pexels_clips(sc["stock_query_en"], clipdir)
                except Exception as e:  # noqa
                    log("  pexels error:", type(e).__name__)
                    clips = []
                if clips:
                    sc["clips"] = clips
                    got = True
                    for c in clips:
                        if c["credit"] not in credits:
                            credits.append(c["credit"])
                        licenses.append({"source": "pexels", "id": c["id"], "url": c["url"], "credit": c["credit"],
                                         "used_on": datetime.now(timezone.utc).date().isoformat()})
            if not got:
                p = imgdir / f"{i:03d}.png"
                if gen_image(sc["image_prompt_en"], p, "photo", sc.get("stock_query_en", "")):
                    sc["image"] = p
                    prev_img = p
                elif prev_img:
                    sc["image"] = prev_img
                    failed += 1
                else:
                    failed += 1
                    raise RuntimeError("no picture could be made for the first scene. Image problems: " + fail_summary())
        else:
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
    return credits, licenses, failed, requested


def pipeline():
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise RuntimeError(f"{tool} not installed")
    if not GEMINI_KEY:
        raise RuntimeError("GEMINI_API_KEY missing")
    if not (CF_ACCOUNT and CF_TOKEN) and not ALLOW_POLLI:
        raise RuntimeError("CF_ACCOUNT_ID / CF_API_TOKEN secrets missing: watermark-free images need a free Cloudflare account")
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
    log(f"script ready: {len(scenes)} scenes, {word_count(script)} words, "
        f"{sum(1 for s in scenes if s['type'] != 'photo')} explainer scenes")

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

    imgdir, clipdir = WORK / "img", WORK / "clips"
    imgdir.mkdir()
    clipdir.mkdir()
    credits, licenses, failed, requested = make_visuals(scenes, imgdir, clipdir)
    if failed > requested * 0.35:
        raise RuntimeError(f"too many pictures missing ({failed}/{requested}); not publishing junk. "
                           f"Image problems: {fail_summary()}")

    silent = build_video(scenes, WORK / "build", pal)
    music = WORK / "music.wav"
    make_music(total, music)
    outdir = WORK / "out"
    outdir.mkdir()
    final = outdir / "video.mp4"
    mux_final(silent, voice, music, final, max_mb=44 if UPLOAD_MODE == "telegram" else None)
    log(f"final video {fmt_time(ffprobe_dur(final))}, {final.stat().st_size / 1e6:.1f} MB")

    thumb_src = imgdir / "thumb.png"
    if not gen_image(script["thumb_prompt_en"], thumb_src, "thumb"):
        thumb_src = next((s.get("image") for s in scenes if s.get("image")), None) or next(
            (it.get("image") for s in scenes for it in s["items"] if it.get("image")), None)
    thumb = outdir / "thumbnail.jpg"
    make_thumbnail(thumb_src, script["thumb_text_hi"] or script["title_hi"], script["thumb_badge_hi"], thumb)

    tags3 = " ".join("#" + re.sub(r"\s+", "", t) for t in script["tags"][:3])
    lines = [script["description_hi"], ""]
    if sources:
        lines.append("📚 जानकारी के लिए खोज में देखे गए स्रोत: " + ", ".join(sources))
    if credits:
        lines.append("🎬 फुटेज: Pexels (" + ", ".join(credits[:6]) + ")")
    lines += ["🤖 इस वीडियो में AI से बनी तस्वीरें और AI आवाज़ का उपयोग किया गया है।", "", tags3]
    meta = {"title": script["title_hi"], "description": "\n".join(lines), "tags": script["tags"]}
    (outdir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    note = []
    if not facts:
        note.append("⚠️ Is baar facts search se verify nahi hue, public karne se pehle facts khud zaroor check karein.")
    if failed:
        note.append(f"ℹ️ {failed}/{requested} pictures nahi ban saki (unki jagah simple card/purani image lagi).")
    yt_id = None
    if UPLOAD_MODE == "youtube":
        yt_id = youtube_upload(final, thumb, meta)
        if meta.get("warn"):
            note.append("⚠️ " + meta["warn"])
    fb_id = facebook_upload(final, meta)

    head = (f"✅ Nayi video ready: {meta['title']}\nLambai: {fmt_time(total)}\nTopic: {topic_en}\n"
            f"Awaaz: {VOICE_USED[0]}\n")
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
        tg_send_file(thumb, "doc", "Thumbnail")
        if not ok:
            notify("⚠️ Video Telegram par nahi ja saki (size/limit). GitHub Actions ke 'Artifacts' se download karein.")

    done["videos"].append({"date": datetime.now(timezone.utc).isoformat(timespec="seconds"), "topic_en": topic_en,
                           "title_hi": meta["title"], "category": cat, "style": style[0], "theme": pal["name"],
                           "seconds": round(total), "voice": VOICE_USED[0][:20], "youtube_id": yt_id,
                           "facebook_id": fb_id})
    save_json(DONE_FILE, done)
    lic = load_json(LICENSE_FILE, [])
    lic.extend(licenses)
    save_json(LICENSE_FILE, lic)
    log("DONE")


# --------------------------------------------------------------------------- check + selftest
def check():
    res = []

    def t(name, fn):
        try:
            res.append((name, True, str(fn() or "ok")[:90]))
        except Exception as e:  # noqa
            res.append((name, False, f"{type(e).__name__}: {str(e)[:110]}"))

    t("ffmpeg", lambda: run(["ffmpeg", "-version"]).stdout.splitlines()[0])
    t("hindi-shaping(raqm)", lambda: "ok" if features.check("raqm") else (_ for _ in ()).throw(RuntimeError("raqm missing: Hindi letters will break")))
    t("fonts", lambda: ", ".join(Path(font_path(r)).name for r in ("sub", "head", "title")))
    t("gemini-text", lambda: gemini("Reply with the single word: ready", temperature=0)[0])

    def voice_check():
        p = Path("check_voice.src")
        tts_gemini("नमस्ते दोस्तों, आज हम प्रकृति की एक अद्भुत बात जानेंगे।", p)
        d = ffprobe_dur(p)
        p.unlink(missing_ok=True)
        return f"natural Gemini voice works ({d:.1f}s)"

    def edge_check():
        p = Path("check.mp3")
        tts_one("नमस्ते दोस्तों, आज हम प्रकृति की एक अद्भुत बात जानेंगे", p)
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
    t("pexels(optional)", lambda: len(pexels_clips("ants macro", Path("."), 1)) if PEXELS_KEY else "no key (fine)")
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
    notify("Health check v2 (" + UPLOAD_MODE + " mode)\n" + "\n".join(lines))
    for f in ("check.mp3", "check.png", "check_voice.src"):
        Path(f).unlink(missing_ok=True)
    must = {"ffmpeg", "hindi-shaping(raqm)", "fonts", "gemini-text", "images(cloudflare)"}
    return all(ok for n, ok, _m in res if n in must)


def selftest():
    """Offline build test: fake pictures, fake voice. Builds one scene of every type + thumbnail."""
    t0 = WORK / "selftest"
    if t0.exists():
        shutil.rmtree(t0)
    t0.mkdir(parents=True)
    pal = random.choice(PALETTES) if os.environ.get("PAL") is None else next(
        p for p in PALETTES if p["name"] == os.environ["PAL"])

    def art(path, kind, c1, c2):
        im = Image.new("RGB", (900, 900))
        d = ImageDraw.Draw(im)
        for y in range(900):
            d.line([(0, y), (900, y)], fill=(int(c1[0] + (c2[0] - c1[0]) * y / 900), int(c1[1] + (c2[1] - c1[1]) * y / 900),
                                              int(c1[2] + (c2[2] - c1[2]) * y / 900)))
        if kind == "planet":
            d.ellipse([200, 200, 700, 700], fill=(40, 120, 200))
            d.ellipse([300, 280, 480, 420], fill=(60, 170, 90))
        elif kind == "rock":
            d.ellipse([260, 260, 640, 640], fill=(190, 110, 70))
            d.ellipse([360, 340, 430, 410], fill=(150, 80, 50))
        elif kind == "moon":
            d.ellipse([220, 220, 680, 680], fill=(200, 200, 205))
            for cx, cy, r in ((330, 330, 50), (500, 450, 70), (400, 560, 40)):
                d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(160, 160, 168))
        else:
            d.polygon([(450, 150), (750, 700), (150, 700)], fill=(240, 190, 40))
        im.save(path)
        return path

    def items(specs):
        out = []
        for k, (lab, kind, c1, c2) in enumerate(specs):
            out.append({"label_hi": lab, "prompt_en": "x", "image": art(t0 / f"c_{lab}_{k}.png", kind, c1, c2)})
        return out

    bg1, bg2 = (20, 30, 90), (120, 60, 160)
    photo = t0 / "photo.png"
    pim = Image.new("RGB", (1920, 1080))
    pd = ImageDraw.Draw(pim)
    for y in range(1080):
        pd.line([(0, y), (1920, y)], fill=(30 + y // 8, 60 + y // 10, 160 - y // 12))
    pd.ellipse([1100, 200, 1700, 800], fill=(250, 190, 90))
    pim.save(photo)
    sc = [
        {"type": "photo", "image": photo, "items": [], "headline_hi": "",
         "narration_hi": "क्या आप जानते हैं कि चाँद कैसे बना था? यह कहानी बहुत हैरान करने वाली है, आगे जानेंगे"},
        {"type": "merge", "headline_hi": "टक्कर से चाँद बना",
         "items": items([("युवा पृथ्वी", "planet", (255, 220, 160), (255, 160, 120)),
                         ("थिया ग्रह", "rock", (200, 230, 255), (120, 160, 230)),
                         ("हमारा चाँद", "moon", (40, 50, 110), (20, 20, 60))]),
         "narration_hi": "लगभग साढ़े चार अरब साल पहले, पृथ्वी से एक मंगल जितना बड़ा ग्रह टकराया, और इसी टक्कर से हमारा चाँद बना"},
        {"type": "split", "headline_hi": "एक से दो हिस्से",
         "items": items([("पुरानी पृथ्वी", "planet", (255, 220, 160), (255, 160, 120)),
                         ("पृथ्वी", "planet", (200, 255, 220), (100, 200, 160)),
                         ("मलबा", "rock", (230, 210, 255), (170, 130, 220))]),
         "narration_hi": "इस टक्कर के बाद एक बड़ा गोला दो हिस्सों में बँट गया, एक हिस्सा पृथ्वी बना और दूसरा मलबा बना"},
        {"type": "steps", "headline_hi": "चाँद बनने के चरण",
         "items": items([("टक्कर", "rock", (255, 220, 160), (255, 160, 120)), ("मलबा", "rock", (200, 230, 255), (120, 160, 230)),
                         ("जुड़ना", "moon", (230, 210, 255), (170, 130, 220)), ("चाँद", "moon", (40, 50, 110), (20, 20, 60))]),
         "narration_hi": "पहले टक्कर हुई, फिर मलबा बना, फिर वह धीरे धीरे जुड़ता गया और आख़िर में चाँद बन गया"},
        {"type": "compare", "headline_hi": "पृथ्वी बनाम चाँद",
         "items": items([("पृथ्वी", "planet", (200, 255, 220), (100, 200, 160)), ("चाँद", "moon", (40, 50, 110), (20, 20, 60))]),
         "narration_hi": "पृथ्वी पर हवा और पानी है, लेकिन चाँद पर न हवा है और न पानी"},
        {"type": "steps", "headline_hi": "तीन चरण", "items": items([("एक", "rock", bg1, bg2), ("दो", "moon", bg1, bg2), ("तीन", "planet", bg1, bg2)]),
         "narration_hi": "तो याद रखिए, पहले टक्कर, फिर मलबा, और फिर चाँद। वीडियो अच्छा लगे तो सब्सक्राइब ज़रूर करें"},
    ]
    for s in sc:
        s["speech_dur"] = None

    def fake_tts(text, out):
        d = max(3.0, len(text.split()) * 0.42)
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=190:duration={d}", "-af", "volume=0.4", "-f", "wav", out])

    voice = make_voice(sc, t0 / "voice", synth=fake_tts)
    total = ffprobe_dur(voice)
    silent = build_video(sc, t0 / "build", pal)
    music = t0 / "music.wav"
    make_music(total, music)
    final = t0 / "final.mp4"
    mux_final(silent, voice, music, final)
    make_thumbnail(photo, "चाँद कैसे बना था?", "रहस्य", t0 / "thumb.jpg")
    log(f"SELFTEST OK ({pal['name']}): {final} {ffprobe_dur(final):.1f}s (voice {total:.1f}s) {final.stat().st_size / 1e6:.1f}MB")


if __name__ == "__main__":
    try:
        if "--selftest" in sys.argv:
            selftest()
        elif "--check" in sys.argv:
            sys.exit(0 if check() else 1)
        else:
            pipeline()
    except Exception as exc:
        traceback.print_exc()
        if "--selftest" not in sys.argv:
            notify(f"❌ Video banane mein error aaya:\n{type(exc).__name__}: {str(exc)[:700]}\n\n"
                   "GitHub → Actions mein us run ka log dekh sakte hain. Agle schedule par bot dobara koshish karega.")
        sys.exit(1)
