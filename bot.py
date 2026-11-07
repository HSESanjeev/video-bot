#!/usr/bin/env python3
"""
Hindi nature-documentary video bot.

Flow:  topic -> research -> script -> Hindi voice -> visuals -> video + subtitles
       -> thumbnail -> (Telegram delivery | YouTube private upload) -> notify

Modes:
  python bot.py              full run (one video)
  python bot.py --check      quick health check of all keys / services
  python bot.py --selftest   offline build test (no internet, fake inputs)

UPLOAD_MODE env:
  telegram (default)  video + thumbnail + title/description are sent to you on Telegram
  youtube             bot uploads to YouTube as PRIVATE (needs an audited API project,
                      otherwise YouTube locks the video as private for good)
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
from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps, features

ROOT = Path(__file__).resolve().parent
WORK = Path(os.environ.get("WORK_DIR", str(ROOT / "work")))
STATE = ROOT / "state"
DONE_FILE = STATE / "done.json"
LICENSE_FILE = STATE / "license_log.json"

W, H, FPS = 1280, 720, 30
VOICE = os.environ.get("TTS_VOICE", "hi-IN-MadhurNeural")
TTS_RATE = os.environ.get("TTS_RATE", "-8%")
WPM = float(os.environ.get("WPM_ESTIMATE", "130"))  # spoken Hindi words per minute (estimate)
MIN_SEC, MAX_SEC = 180, 300
UPLOAD_MODE = os.environ.get("UPLOAD_MODE", "telegram").strip().lower()
SCENE_GAP = 0.30  # silence after every scene (seconds)
HOOK_GAP = 0.70  # longer pause after the suspense hook

GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
PEXELS_KEY = os.environ.get("PEXELS_API_KEY", "")
POLLI_KEY = os.environ.get("POLLINATIONS_KEY", "")
CF_ACCOUNT = os.environ.get("CF_ACCOUNT_ID", "")
CF_TOKEN = os.environ.get("CF_API_TOKEN", "")
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")

STYLE_SUFFIX = (
    "cinematic nature documentary still, ultra detailed, natural dramatic lighting, "
    "realistic, National Geographic quality, wide 16:9 composition, "
    "no text, no letters, no watermark, no logo, no identifiable real person"
)

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


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def run(cmd, **kw):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)
    if r.returncode != 0:
        raise RuntimeError("command failed: " + " ".join(str(c) for c in cmd[:6]) + "\n" + r.stderr[-1500:])
    return r


def ffprobe_dur(path):
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", path])
    return float(r.stdout.strip())


def has_devanagari(s):
    return any("ऀ" <= ch <= "ॿ" for ch in s)


# --------------------------------------------------------------------------- state
def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


# --------------------------------------------------------------------------- Gemini
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
- Scene 1 is a SUSPENSE HOOK: 25-30 words, built on a TRUE surprising fact or question, does NOT reveal the answer, ends by promising it ("आगे जानेंगे").
- Scene 2 says in one or two lines what the viewer will understand by the end.
- Around 55% of the video add a short re-hook line ("लेकिन असली राज़ अभी बाक़ी है...") that is true to what follows.
- Final scenes: recap the 3 key points in simple words, then one short line asking viewers to subscribe.
- The viewer must truly UNDERSTAND the topic by the end: define every new term when first used, use one simple everyday analogy, go in a logical order.
- Facts: only well-established science, rounded widely-accepted numbers. If something is debated say "वैज्ञानिकों का मानना है". Never invent names, dates, statistics or quotes. If unsure, leave it out.
- Neutral on religion and politics; never mention or mock any belief. Say "वैज्ञानिक मान्यता के अनुसार" for origin topics.
- Each scene has its own visual. visual="stock" ONLY for real present-day nature footage (living animals, insects, plants, ocean, forest, sky). visual="ai" for prehistoric times, planet formation, microscopic/inside-body views, space, abstract ideas.
- image_prompt_en: a concrete, photographic description of one image (subject, setting, lighting, camera angle). No text/letters, no real people's faces. ALWAYS provide it, even for stock scenes.
- stock_query_en: 2-4 plain English words for stock footage search (only for stock scenes, else "").
"""


def build_script_prompt(topic, cat, style, target_sec, facts, words):
    n_scenes = max(14, round(words / 24))
    return f"""You are a script writer for a Hindi nature & science documentary YouTube channel.
TOPIC: {topic}
CATEGORY: {cat}
STRUCTURE STYLE: {style[1]}
TARGET: about {words} Hindi words of narration in total ({target_sec} seconds), around {n_scenes} scenes of 18-30 words each.

{SCRIPT_RULES}

VERIFIED FACTS TO PREFER (may be empty):
{facts[:6000]}

Return ONLY JSON in exactly this shape:
{{
 "title_hi": "Hindi title, max 60 characters, curious but TRUTHFUL, no clickbait lies",
 "thumb_text_hi": "3-5 powerful Hindi words for the thumbnail",
 "thumb_prompt_en": "one striking, dramatic image description for the thumbnail background",
 "description_hi": "3-4 short Hindi lines telling what the viewer will learn",
 "tags": ["8-12 tags mixing Hindi and English"],
 "scenes": [
  {{"narration_hi": "...", "visual": "ai or stock", "image_prompt_en": "...", "stock_query_en": "..."}}
 ]
}}"""


def validate_script(s):
    if not isinstance(s, dict) or not isinstance(s.get("scenes"), list) or len(s["scenes"]) < 8:
        raise ValueError("script has too few scenes")
    for sc in s["scenes"]:
        sc["narration_hi"] = clean_narration(sc.get("narration_hi", ""))
        if not sc["narration_hi"]:
            raise ValueError("empty narration")
        sc["visual"] = "stock" if sc.get("visual") == "stock" and sc.get("stock_query_en") else "ai"
        sc["image_prompt_en"] = (sc.get("image_prompt_en") or "dramatic nature landscape").strip()
        sc["stock_query_en"] = (sc.get("stock_query_en") or "").strip()
    for k in ("title_hi", "thumb_text_hi", "description_hi"):
        s[k] = (s.get(k) or "").strip()
    if not s["title_hi"]:
        raise ValueError("no title")
    s["tags"] = [str(t).strip() for t in (s.get("tags") or []) if str(t).strip()][:12]
    s["thumb_prompt_en"] = (s.get("thumb_prompt_en") or s["scenes"][0]["image_prompt_en"]).strip()
    return s


def clean_narration(t):
    t = (t or "").replace("—", ", ").replace("–", ", ").replace("…", "...").replace("​", "")
    t = re.sub(r"[\*\_#`]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def word_count(s):
    return sum(len(sc["narration_hi"].split()) for sc in s["scenes"])


def write_script(topic, cat, style, target_sec, facts):
    words = int(target_sec / 60 * WPM)
    script = validate_script(gemini_json(build_script_prompt(topic, cat, style, target_sec, facts, words), 0.8))
    review = f"""You are (1) a careful science editor and (2) a Hindi school teacher.
Improve this documentary script JSON:
- Remove or soften any claim that is not widely accepted science; fix any factual doubt.
- Replace difficult words with simple ones; make sure a 15-year-old understands everything on first listen.
- Keep scene 1 a truthful suspense hook (25-30 words) that does not reveal the answer.
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
    prompt = (f"Rewrite this Hindi documentary script so total narration is about {target_words} words "
              f"(now {cur}). {'Trim less important details' if target_words < cur else 'Add clear explanation and one more simple example'}. "
              "Keep the hook, structure, rules and the exact JSON shape. Return ONLY JSON.\n"
              + json.dumps(script, ensure_ascii=False))
    return validate_script(gemini_json(prompt, 0.4))


# --------------------------------------------------------------------------- voice
def tts_one(text, out_mp3):
    import edge_tts

    async def go():
        await edge_tts.Communicate(text, VOICE, rate=TTS_RATE).save(str(out_mp3))

    err = None
    for a in range(4):
        try:
            asyncio.run(go())
            if Path(out_mp3).exists() and Path(out_mp3).stat().st_size > 1500:
                return
        except Exception as e:  # noqa
            err = e
            log("TTS retry", a + 1, type(e).__name__)
        time.sleep(3 * (a + 1))
    raise RuntimeError(f"TTS failed: {err}")


def make_voice(scenes, tdir, synth=None):
    synth = synth or tts_one
    tdir.mkdir(parents=True, exist_ok=True)
    wavs = []
    for i, sc in enumerate(scenes):
        mp3 = tdir / f"s{i:03d}.mp3"
        wav = tdir / f"s{i:03d}.wav"
        synth(sc["narration_hi"], mp3)
        pad = HOOK_GAP if i == 0 else SCENE_GAP
        run(["ffmpeg", "-y", "-i", mp3, "-ar", "44100", "-ac", "1", "-af",
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


# --------------------------------------------------------------------------- fonts / text images
@lru_cache(maxsize=8)
def find_font(need_devanagari=True):
    pats = [os.environ.get("FONT_PATH", ""),
            str(ROOT / "fonts" / "*Bold*.ttf"),
            "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf",
            "/usr/share/fonts/**/NotoSansDevanagari*Bold*.ttf",
            "/usr/share/fonts/**/NotoSansDevanagari*.ttf",
            "/usr/share/fonts/**/Mukta*Bold*.ttf",
            "/usr/share/fonts/**/Lohit-Devanagari*.ttf"]
    if not need_devanagari:
        pats += ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
    for p in pats:
        if not p:
            continue
        hits = sorted(glob.glob(p, recursive=True))
        if hits:
            return hits[0]
    raise RuntimeError("Devanagari font not found (install fonts-noto-core)" if need_devanagari else "no font")


def load_font(size, need_devanagari=True):
    path = find_font(need_devanagari)
    eng = ImageFont.Layout.RAQM if features.check("raqm") else ImageFont.Layout.BASIC
    f = ImageFont.truetype(path, size, layout_engine=eng)
    try:  # variable fonts: ask for bold if possible
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


def make_sub_png(text, path):
    dev = has_devanagari(text)
    font = load_font(44, dev)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    block = "\n".join(wrap_text(d, text, font, W - 200))
    sw, sp = 3, 8
    bb = d.multiline_textbbox((0, 0), block, font=font, align="center", stroke_width=sw, spacing=sp)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    x = (W - tw) / 2 - bb[0]
    y = H - 62 - th - bb[1]
    pad = 16
    d.rounded_rectangle([x + bb[0] - pad, y + bb[1] - pad, x + bb[0] + tw + pad, y + bb[1] + th + pad],
                        radius=14, fill=(0, 0, 0, 130))
    d.multiline_text((x, y), block, font=font, fill=(255, 255, 255, 255), stroke_width=sw,
                     stroke_fill=(0, 0, 0, 255), align="center", spacing=sp)
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


# --------------------------------------------------------------------------- visuals
_last_polli = [0.0]
IMG_FAILS = {}


def note_fail(provider, why):
    key = f"{provider}: {why}"
    IMG_FAILS[key] = IMG_FAILS.get(key, 0) + 1


def fail_summary():
    return "; ".join(f"{k} x{v}" for k, v in IMG_FAILS.items()) or "no details"


def fit_image(raw_bytes, out_png):
    tmp = Path(str(out_png) + ".tmp")
    tmp.write_bytes(raw_bytes)
    im = Image.open(tmp)
    im.verify()
    im = Image.open(tmp).convert("RGB")
    tmp.unlink(missing_ok=True)
    if im.width < 400 or im.height < 400:
        raise ValueError("image too small")
    im = ImageOps.fit(im, (1920, 1080), Image.LANCZOS)
    im.save(out_png)


def img_pollinations(prompt, out):
    wait = 16 if not POLLI_KEY else 2
    gap = time.time() - _last_polli[0]
    if gap < wait:
        time.sleep(wait - gap)
    url = f"https://image.pollinations.ai/prompt/{quote(prompt[:450], safe='')}"
    headers = {"Authorization": f"Bearer {POLLI_KEY}"} if POLLI_KEY else {}
    for a in range(4):
        _last_polli[0] = time.time()
        try:
            r = requests.get(url, headers=headers, timeout=150,
                             params={"width": 1280, "height": 720, "model": "flux", "nologo": "true",
                                     "seed": random.randint(1, 10**6)})
        except requests.RequestException as e:
            note_fail("pollinations", type(e).__name__)
            time.sleep(15)
            continue
        if r.ok and r.headers.get("content-type", "").startswith("image"):
            try:
                fit_image(r.content, out)
                return True
            except Exception:  # noqa
                note_fail("pollinations", "unreadable image")
        else:
            note_fail("pollinations", f"HTTP {r.status_code}")
        time.sleep(min(60, 20 * (a + 1)))
    return False


def img_cloudflare(prompt, out):
    if not (CF_ACCOUNT and CF_TOKEN):
        return False
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT}/ai/run/@cf/black-forest-labs/flux-1-schnell"
    r = requests.post(url, headers={"Authorization": f"Bearer {CF_TOKEN}"},
                      json={"prompt": prompt[:2000], "steps": 6}, timeout=120)
    if not r.ok:
        note_fail("cloudflare", f"HTTP {r.status_code}")
        return False
    if r.headers.get("content-type", "").startswith("image"):
        fit_image(r.content, out)
        return True
    b64 = (r.json().get("result") or {}).get("image")
    if not b64:
        note_fail("cloudflare", "no image in reply")
        return False
    fit_image(base64.b64decode(b64), out)
    return True


def pexels_photo(query, out):
    if not PEXELS_KEY or not query:
        return False
    r = requests.get("https://api.pexels.com/v1/search", headers={"Authorization": PEXELS_KEY},
                     params={"query": query, "per_page": 5, "orientation": "landscape"}, timeout=30)
    if not r.ok or not r.json().get("photos"):
        return False
    p = random.choice(r.json()["photos"][:3])
    img = requests.get(p["src"]["large2x"], timeout=60)
    fit_image(img.content, out)
    return True


def gen_image(prompt, out, fallback_query=""):
    full = f"{prompt}, {STYLE_SUFFIX}"
    for name, fn in (("cloudflare", lambda: img_cloudflare(full, out)),
                     ("pollinations", lambda: img_pollinations(full, out)),
                     ("pexels-photo", lambda: pexels_photo(fallback_query or prompt[:60], out))):
        try:
            if fn() and Path(out).exists():
                if name == "pexels-photo":
                    log("  (image fallback: pexels photo)")
                return True
        except Exception as e:  # noqa
            note_fail(name, type(e).__name__)
            log(f"  image source {name} failed: {type(e).__name__}: {str(e)[:120]}")
    return False


def pexels_clips(query, tdir, n=3):
    """Download up to n short landscape clips. Returns list of dicts with path/credit."""
    if not PEXELS_KEY:
        return []
    r = requests.get("https://api.pexels.com/videos/search", headers={"Authorization": PEXELS_KEY},
                     params={"query": query, "per_page": 10, "orientation": "landscape", "size": "medium"},
                     timeout=30)
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


# --------------------------------------------------------------------------- video build
GRADE = "eq=contrast=1.06:saturation=1.12:gamma=0.98,vignette=PI/5"


def motion_expr(kind, n):
    return {
        "in": ("1+0.18*on/%d" % n, "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
        "out": ("1.18-0.18*on/%d" % n, "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
        "lr": ("1.16", "(iw-iw/zoom)*on/%d" % n, "ih/2-(ih/zoom/2)"),
        "rl": ("1.16", "(iw-iw/zoom)*(1-on/%d)" % n, "ih/2-(ih/zoom/2)"),
        "in_tl": ("1+0.22*on/%d" % n, "(iw-iw/zoom)*0.15", "(ih-ih/zoom)*0.2"),
        "in_br": ("1+0.22*on/%d" % n, "(iw-iw/zoom)*0.85", "(ih-ih/zoom)*0.8"),
    }[kind]


def render_subshot(src, out, dur, subs, kind=None, offset=0.0, fade_in=False, fade_out=False):
    """src: ('image', path) or ('clip', path). subs: [(png, a, b)] relative to start of this sub-shot."""
    n = max(1, int(round(dur * FPS)))
    cmd = ["ffmpeg", "-y"]
    if src[0] == "image":
        cmd += ["-i", src[1]]
        z, x, y = motion_expr(kind or "in", n)
        base = (f"[0:v]scale=2560:1440:force_original_aspect_ratio=increase,crop=2560:1440,"
                f"zoompan=z='{z}':x='{x}':y='{y}':d={n}:s={W}x{H}:fps={FPS},{GRADE},format=yuv420p")
    else:
        cmd += ["-stream_loop", "-1", "-ss", f"{offset:.2f}", "-i", src[1]]
        base = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},{GRADE},format=yuv420p")
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


def build_video(scenes, tdir):
    """scenes: dicts with dur, speech_dur, narration_hi, image(path)|clips(list). Returns silent video path."""
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
            make_sub_png(c, p)
            pngs.append((str(p), a, b))
        nsub = max(1, math.ceil(sc["dur"] / 4.5))
        sd = sc["dur"] / nsub
        for k in range(nsub):
            s0, s1 = k * sd, (k + 1) * sd
            subs = [(p, max(a, s0) - s0, min(b, s1) - s0) for (p, a, b) in pngs if a < s1 and b > s0]
            out = tdir / f"shot_{i:03d}_{k:02d}.mp4"
            fi, fo = (i == 0 and k == 0), (i == total - 1 and k == nsub - 1)
            clips = sc.get("clips") or []
            if clips:
                cl = clips[(k + i) % len(clips)]
                off = 0.0
                if cl["dur"] > sd + 1:
                    off = random.uniform(0, cl["dur"] - sd - 0.5)
                render_subshot(("clip", str(cl["path"])), out, sd, subs, offset=off, fade_in=fi, fade_out=fo)
            else:
                render_subshot(("image", str(sc["image"])), out, sd, subs, kind=kinds[ki % len(kinds)],
                               fade_in=fi, fade_out=fo)
                ki += 1
            parts.append(out)
        if i % 6 == 0:
            log(f"video scene {i + 1}/{total}")
    lst = tdir / "shots.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    silent = tdir / "silent.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", silent])
    return silent


def mux_final(silent, voice, music, out, max_mb=None):
    dur = ffprobe_dur(voice)
    fc = ("[1:a]aresample=44100,asplit=2[v][vsc];"
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
def make_thumbnail(img_path, text, out):
    dev = has_devanagari(text)
    im = Image.open(img_path).convert("RGB")
    im = ImageOps.fit(im, (W, H), Image.LANCZOS)
    im = ImageEnhance.Contrast(im).enhance(1.18)
    im = ImageEnhance.Color(im).enhance(1.25)
    mask = Image.new("L", (W, H))
    md = ImageDraw.Draw(mask)
    for x in range(W):
        md.line([(x, 0), (x, H)], fill=int(200 * max(0.0, 1 - x / (W * 0.8))))
    im = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), im, mask)
    d = ImageDraw.Draw(im)
    max_w, size = int(W * 0.58), 150
    while size > 60:
        font = load_font(size, dev)
        lines = wrap_text(d, text, font, max_w)
        bb = d.multiline_textbbox((0, 0), "\n".join(lines), font=font, stroke_width=8, spacing=10)
        if len(lines) <= 3 and bb[3] - bb[1] < H * 0.78 and bb[2] - bb[0] <= max_w + 20:
            break
        size -= 8
    y = (H - (bb[3] - bb[1])) / 2 - bb[1]
    for k, line in enumerate(lines):
        lb = d.textbbox((0, 0), line, font=font, stroke_width=8)
        lh = (bb[3] - bb[1]) / len(lines)
        yy = y + k * (lh + 4)
        d.text((50, yy), line, font=font, fill=(255, 224, 0) if k % 2 == 0 else (255, 255, 255),
               stroke_width=8, stroke_fill=(0, 0, 0))
    im.save(out, "JPEG", quality=90)


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


def pipeline():
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise RuntimeError(f"{tool} not installed")
    if not GEMINI_KEY:
        raise RuntimeError("GEMINI_API_KEY missing")
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    done = load_json(DONE_FILE, {"videos": []})
    random.seed()

    cat, topic_en, topic_hi = pick_topic(done)
    style = random.choice(STYLES)
    target = random.randint(MIN_SEC, MAX_SEC)
    log(f"TOPIC: {topic_en} | category={cat} | style={style[0]} | target={target}s")

    facts, sources = research(topic_en)
    script = write_script(topic_en, cat, style, target, facts)
    scenes = script["scenes"]
    log(f"script ready: {len(scenes)} scenes, {word_count(script)} words")

    # voice first (cheap) so the real length can be checked before images are made
    tdir = WORK / "voice"
    voice = make_voice(scenes, tdir)
    total = ffprobe_dur(voice)
    log(f"voice length {fmt_time(total)} (target {fmt_time(target)})")
    if not (MIN_SEC - 15 <= total <= MAX_SEC + 15):
        new_words = int(word_count(script) * target / total)
        log(f"length off-range, rescaling script to ~{new_words} words")
        try:
            script = rescale_script(script, new_words)
            scenes = script["scenes"]
            shutil.rmtree(tdir, ignore_errors=True)
            voice = make_voice(scenes, tdir)
            total = ffprobe_dur(voice)
            log(f"new voice length {fmt_time(total)}")
        except Exception as e:  # noqa
            log("rescale failed, continuing:", e)

    # visuals
    imgdir, clipdir = WORK / "img", WORK / "clips"
    imgdir.mkdir()
    clipdir.mkdir()
    credits, licenses, failed = [], [], 0
    prev_img = None
    for i, sc in enumerate(scenes):
        got = False
        if sc["visual"] == "stock":
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
            if gen_image(sc["image_prompt_en"], p, sc.get("stock_query_en", "")):
                sc["image"] = p
                prev_img = p
                got = True
            elif prev_img:
                sc["image"] = prev_img
                failed += 1
            else:
                failed += 1
        if not got and not sc.get("image"):
            raise RuntimeError("no visual could be made for the first scenes")
        if i % 5 == 0:
            log(f"visuals {i + 1}/{len(scenes)} (failed so far: {failed})")
    if failed > len(scenes) * 0.35:
        raise RuntimeError(f"too many scenes without fresh visuals ({failed}/{len(scenes)}); not publishing junk. "
                           f"Image problems: {fail_summary()}")

    silent = build_video(scenes, WORK / "build")
    music = WORK / "music.wav"
    make_music(total, music)
    outdir = WORK / "out"
    outdir.mkdir()
    final = outdir / "video.mp4"
    mux_final(silent, voice, music, final, max_mb=44 if UPLOAD_MODE == "telegram" else None)
    log(f"final video {fmt_time(ffprobe_dur(final))}, {final.stat().st_size / 1e6:.1f} MB")

    # thumbnail
    thumb_src = imgdir / "thumb.png"
    if not gen_image(script["thumb_prompt_en"], thumb_src):
        first = next((s.get("image") for s in scenes if s.get("image")), None)
        thumb_src = first
    thumb = outdir / "thumbnail.jpg"
    make_thumbnail(thumb_src, script["thumb_text_hi"] or script["title_hi"], thumb)

    # description
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
    yt_id = None
    if UPLOAD_MODE == "youtube":
        yt_id = youtube_upload(final, thumb, meta)
        if meta.get("warn"):
            note.append("⚠️ " + meta["warn"])
    fb_id = facebook_upload(final, meta)

    head = (f"✅ Nayi video ready: {meta['title']}\nLambai: {fmt_time(total)}\nTopic: {topic_en}\n")
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
                           "title_hi": meta["title"], "category": cat, "style": style[0],
                           "seconds": round(total), "youtube_id": yt_id, "facebook_id": fb_id})
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
            res.append((name, True, str(fn() or "ok")[:80]))
        except Exception as e:  # noqa
            res.append((name, False, f"{type(e).__name__}: {str(e)[:100]}"))

    t("ffmpeg", lambda: run(["ffmpeg", "-version"]).stdout.splitlines()[0])
    t("font", lambda: find_font(True))
    t("gemini", lambda: gemini("Reply with the single word: ready", temperature=0)[0])
    def tts_check():
        tts_one("नमस्ते दोस्तों, आज हम प्रकृति की एक अद्भुत बात जानेंगे", Path("check.mp3"))
        return f"{Path('check.mp3').stat().st_size} bytes"

    t("edge-tts", tts_check)
    t("pexels", lambda: len(pexels_clips("ants macro", Path("."), 1)) if PEXELS_KEY else "no key (stock footage off)")
    t("image", lambda: gen_image("a red apple on a table", Path("check.png")))
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
    msg = "Health check (" + UPLOAD_MODE + " mode)\n" + "\n".join(lines)
    notify(msg)
    for f in ("check.mp3", "check.png"):
        Path(f).unlink(missing_ok=True)
    return all(ok for _n, ok, _m in res if _n != "pexels")


def selftest():
    """Offline build test: fake images, fake clip, fake voice (tones)."""
    global find_font
    t0 = WORK / "selftest"
    if t0.exists():
        shutil.rmtree(t0)
    t0.mkdir(parents=True)
    _orig = find_font
    find_font = lambda need=False: _orig(False)  # noqa  (Latin test text, no Devanagari font needed)
    texts = ["The earth was a ball of molten rock four billion years ago",
             "Then something amazing happened to the young planet",
             "Ants build cities under the ground with many rooms",
             "Every ant has a job and they never get lost",
             "So remember these three simple points about it",
             "Thank you for watching and please subscribe"]
    scenes = []
    for i, tx in enumerate(texts):
        im = Image.new("RGB", (1920, 1080))
        px = ImageDraw.Draw(im)
        for y in range(1080):
            px.line([(0, y), (1920, y)], fill=(40 + i * 25, 60 + y // 12, 140 - y // 14))
        px.ellipse([700 + i * 40, 300, 1200 + i * 40, 800], fill=(220 - i * 20, 120, 60))
        p = t0 / f"img{i}.png"
        im.save(p)
        scenes.append({"narration_hi": tx, "image": p})
    clip = t0 / "clip.mp4"
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=7", "-pix_fmt", "yuv420p", clip])
    scenes[2]["clips"] = [{"path": clip, "dur": 7.0, "id": 0}]

    def fake_tts(text, out):
        d = max(3.0, len(text.split()) * 0.45)
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=190:duration={d}", "-af", "volume=0.4", out])

    voice = make_voice(scenes, t0 / "voice", synth=fake_tts)
    total = ffprobe_dur(voice)
    silent = build_video(scenes, t0 / "build")
    music = t0 / "music.wav"
    make_music(total, music)
    final = t0 / "final.mp4"
    mux_final(silent, voice, music, final)
    make_thumbnail(scenes[0]["image"], "Earth was born", t0 / "thumb.jpg")
    log(f"SELFTEST OK: {final} {ffprobe_dur(final):.1f}s (voice {total:.1f}s) {final.stat().st_size / 1e6:.1f}MB")


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
            notify(f"❌ Video banane mein error aaya:\n{type(exc).__name__}: {str(exc)[:600]}\n\n"
                   "GitHub → Actions mein us run ka log dekh sakte hain. Agle schedule par bot dobara koshish karega.")
        sys.exit(1)
