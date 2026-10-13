"""
National Reporter - breaking news bot.

Runs once per invocation (scheduled by GitHub Actions every 2 hours):
1. Reads recent items from preferred RSS feeds.
2. Asks an AI model to group items into events, classify them, and flag duplicates.
3. Posts an event only if:
   - it is classified A (breaking) or B (important) or C with meaningful new info,
   - it is Hyderabad / Telangana / India / World relevant,
   - it is reported by at least MIN_SOURCES distinct preferred outlets,
   - it has not been posted before (data/posted.json).
4. Writes Hindi + Roman Urdu 3-bullet posts and publishes them with the logo.

Secrets (GitHub Actions secrets / env vars):
  FB_PAGE_ID, FB_PAGE_TOKEN, GEMINI_API_KEY
Settings (env vars):
  DRY_RUN=1 (default) -> prints posts, does not publish
  GEMINI_MODEL (classification, default gemini-flash-latest)
  GEMINI_WRITE_MODEL (Hindi/Roman Urdu writing, default gemini-flash-latest)
  TEST_WRITE=1 -> writes one sample post to the log and exits (no publish)
  MAX_POSTS_PER_RUN (default 2)
  MIN_SOURCES (default 2)
"""

import json
import re
import os
import sys
import time
import calendar
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from card import render_card  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
POSTED_FILE = ROOT / "data" / "posted.json"
LOGO_FILE = ROOT / "assets" / "logo.jpg"

FEEDS = [
    ("The Hindu", "https://www.thehindu.com/news/cities/Hyderabad/feeder/default.rss"),
    ("The Hindu", "https://www.thehindu.com/news/national/telangana/feeder/default.rss"),
    ("NDTV", "https://feeds.feedburner.com/ndtvnews-top-stories"),
    ("NDTV", "https://feeds.feedburner.com/ndtvnews-india-news"),
    ("India Today", "https://www.indiatoday.in/rss/home"),
    ("Indian Express", "https://news.google.com/rss/search?q=site:indianexpress.com+(Hyderabad+OR+Telangana+OR+India+OR+world)&hl=en-IN&gl=IN&ceid=IN:en"),
    # Indian Express feeds currently return 403 to automated requests.
    # Add a working feed URL here when available.
]
PREFERRED_OUTLETS = {"The Hindu", "Indian Express", "NDTV", "India Today"}

DRY_RUN = os.getenv("DRY_RUN", "1") == "1"
MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
WRITE_MODEL = os.getenv("GEMINI_WRITE_MODEL", "gemini-flash-latest")
TEST_WRITE = os.getenv("TEST_WRITE", "0") == "1"
MAX_POSTS = int(os.getenv("MAX_POSTS_PER_RUN", "1"))
MAX_POSTS_PER_DAY = int(os.getenv("MAX_POSTS_PER_DAY", "8"))
MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS_PER_RUN", "4"))
MIN_SOURCES = int(os.getenv("MIN_SOURCES", "2"))
LOOKBACK_HOURS = 12

GRAPH = "https://graph.facebook.com/v19.0"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def openai_json(system_prompt, user_content, temperature=0.0):
    """Fallback model (OpenAI) with the same JSON contract as gemini_json."""
    body = {
        "model": OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
        "temperature": temperature,
    }
    for attempt in range(3):
        try:
            r = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}", "Content-Type": "application/json"},
                json=body,
                timeout=120,
            )
        except requests.exceptions.RequestException:
            if attempt < 2:
                time.sleep(10 * (attempt + 1))
                continue
            raise
        if r.status_code == 200:
            return json.loads(r.json()["choices"][0]["message"]["content"])
        if r.status_code in (429, 500, 503) and attempt < 2:
            time.sleep(10 * (attempt + 1))
            continue
        raise RuntimeError(f"OpenAI call failed: {r.status_code} {r.text[:300]}")


XAI_API_KEY = os.getenv("XAI_API_KEY", "")
XAI_BASE_URL = os.getenv("XAI_BASE_URL", "https://api.x.ai/v1")
XAI_MODEL = os.getenv("XAI_MODEL", "grok-4-1-fast-reasoning")


def xai_json(system_prompt, user_content, temperature=0.0):
    """Grok (xAI) fallback, OpenAI-compatible, same JSON contract."""
    body = {
        "model": XAI_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
        "temperature": temperature,
    }
    for attempt in range(3):
        try:
            r = requests.post(
                f"{XAI_BASE_URL.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {XAI_API_KEY}", "Content-Type": "application/json"},
                json=body,
                timeout=120,
            )
        except requests.exceptions.RequestException:
            if attempt < 2:
                time.sleep(10 * (attempt + 1))
                continue
            raise
        if r.status_code == 200:
            text = r.json()["choices"][0]["message"]["content"]
            return json.loads(text)
        if r.status_code in (429, 500, 503) and attempt < 2:
            time.sleep(10 * (attempt + 1))
            continue
        raise RuntimeError(f"Grok call failed: {r.status_code} {r.text[:300]}")


_last_gemini_call = 0.0


def _gemini_once(system_prompt, user_content, temperature=0.0, model=None):
    """Call one Gemini model. Raises GeminiUnavailable when that model is out of quota or down."""
    api_key = os.environ["GEMINI_API_KEY"]
    body = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_content}]}],
        "generationConfig": {
            "temperature": temperature,
            "responseMimeType": "application/json",
        },
    }
    global _last_gemini_call
    for attempt in range(3):
        # Space requests out so the free/low per-minute limit is not hit by a burst
        wait = 6 - (time.time() - _last_gemini_call)
        if wait > 0:
            time.sleep(wait)
        _last_gemini_call = time.time()
        try:
            r = requests.post(
                GEMINI_URL.format(model=model or MODEL),
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                json=body,
                timeout=120,
            )
        except requests.exceptions.RequestException as e:
            if attempt < 2:
                log(f"Gemini request error ({e.__class__.__name__}), retrying...")
                time.sleep(10 * (attempt + 1))
                continue
            raise
        if r.status_code == 200:
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text)
        if r.status_code in (500, 503) and attempt < 2:
            log(f"Gemini returned {r.status_code}, retrying...")
            time.sleep(15 * (attempt + 1))
            continue
        if r.status_code in (403, 404, 429, 500, 503):
            raise GeminiUnavailable(f"{model or MODEL} {r.status_code}: {r.text[:300]}")
        raise RuntimeError(f"Gemini call failed: {r.status_code} {r.text[:500]}")


class GeminiUnavailable(Exception):
    pass


GEMINI_FALLBACK_MODELS = [m for m in os.getenv("GEMINI_FALLBACK_MODELS", "gemini-flash-lite-latest,gemini-3.1-flash-lite,gemini-3.5-flash-lite").split(",") if m.strip()]


def gemini_json(system_prompt, user_content, temperature=0.0, model=None):
    """Try Gemini models in order (each has its own free quota), then OpenAI if configured."""
    if os.getenv("LLM_PROVIDER", "gemini") == "openai" and os.getenv("OPENAI_API_KEY"):
        return openai_json(system_prompt, user_content, temperature)
    chain = [model or MODEL] + [m.strip() for m in GEMINI_FALLBACK_MODELS if m.strip() != (model or MODEL)]
    last = ""
    for m in chain:
        try:
            return _gemini_once(system_prompt, user_content, temperature, model=m)
        except GeminiUnavailable as e:
            last = str(e)
            log(f"Gemini model unavailable, trying next: {last[:200]}")
    errors = [f"gemini: {last[:160]}"]
    if os.getenv("OPENAI_API_KEY"):
        try:
            log("Gemini unavailable; trying OpenAI")
            return openai_json(system_prompt, user_content, temperature)
        except Exception as e:
            errors.append(f"openai: {str(e)[:160]}")
    if XAI_API_KEY:
        try:
            log("OpenAI unavailable; trying Grok (xAI)")
            return xai_json(system_prompt, user_content, temperature)
        except Exception as e:
            errors.append(f"grok: {str(e)[:160]}")
    raise RuntimeError("All AI providers unavailable: " + " | ".join(errors))


def log(msg):
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}", flush=True)


def load_posted():
    return json.loads(POSTED_FILE.read_text(encoding="utf-8"))


def save_posted(records):
    POSTED_FILE.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_items():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    items = []
    for outlet, url in FEEDS:
        parsed = feedparser.parse(url, agent="Mozilla/5.0")
        for e in parsed.entries:
            ts = e.get("published_parsed") or e.get("updated_parsed")
            if ts:
                published = datetime.fromtimestamp(calendar.timegm(ts), tz=timezone.utc)
                if published < cutoff:
                    continue
            items.append({
                "id": len(items),
                "outlet": outlet,
                "title": e.get("title", "").strip(),
                "summary": (e.get("summary", "") or "")[:500],
                "link": e.get("link", ""),
                "published": e.get("published", ""),
            })
    log(f"Fetched {len(items)} recent items")
    return items


CLASSIFY_PROMPT = """You are a strict news editor for a Hyderabad/Telangana breaking-news Facebook page.
You receive news headlines from preferred outlets and a list of events already posted.

Group the items that describe the SAME event. For each event return:
- "event_key": short English description of the event (no speculation)
- "item_ids": ids of the items about this event
- "region": one of HYDERABAD, TELANGANA, INDIA, WORLD
- "category": short label, e.g. accident, fire, crime, government, weather, court, politics, traffic, other
- "class": A (breaking, publish now), B (important), C (developing with new info), D (routine), E (duplicate of an already-posted event)
- "sensitive": true for crime, communal, security or terror stories
- "duplicate_of": the posted event_key it duplicates, or null

Rules: never invent facts. Routine, opinion, feature, entertainment, sports, and celebrity stories are class D.
Coverage priority: politics, protests, public agitation, government decisions, elections, court rulings with public impact, and major national or international developments are important (class B or A). Crime is class D unless it is a MAJOR incident: mass casualties, a large-scale police operation, a high-profile case with wide public impact, or communal or terror violence. Single arrests, petty theft, fraud and ordinary accidents are class D.
Region priority: Hyderabad and Telangana stories come first, then India, then World. Give WORLD stories class B only if they are major.
Return JSON: {"events": [...]}"""


def classify(items, posted):
    payload = {
        "items": [{"id": i["id"], "outlet": i["outlet"], "title": i["title"], "summary": i["summary"]} for i in items],
        "already_posted": [p["event_key"] for p in posted[-50:]],
    }
    data = gemini_json(CLASSIFY_PROMPT, json.dumps(payload, ensure_ascii=False), temperature=0)
    return data.get("events", [])


WRITE_PROMPT = """You write a Facebook breaking-news post for a Hyderabad/Telangana news page.
STRICT RULES:
1. Use ONLY facts present in the provided headlines and summaries. Never add names, numbers, causes, places, quotes or outcomes that are not there.
2. Every story must answer WHO, WHAT, WHERE and WHY/CONTEXT. If the material does not clearly give all four, set "complete" to false. Do not guess.
3. Never write filler such as "more details are awaited", "information is awaited", "reports are coming". Each bullet must state a fact.
4. Never mention any news outlet, channel or source name (no NDTV, The Hindu, Indian Express, "reports by", "source").
5. Hindi must be simple Devanagari. Roman Urdu must be simple, natural Latin letters. Both must carry the same 3 facts, with no added or missing facts.
6. Exactly 3 bullets in Hindi and exactly 3 in Roman Urdu. Short, complete sentences.
Return JSON:
{"complete": true or false,
 "headline": "LATEST NEWS — HYDERABAD" (or TELANGANA / INDIA / WORLD),
 "hindi": ["bullet 1", "bullet 2", "bullet 3"],
 "roman_urdu": ["bullet 1", "bullet 2", "bullet 3"],
 "sources": ["The Hindu"]}"""


class SkipStory(Exception):
    pass


def fetch_article_text(url, limit=6000):
    """Pull readable body text from a story link. Returns '' on any failure."""
    if not url:
        return ""
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (NR news bot)"})
        if r.status_code != 200:
            return ""
        html = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", r.text)
        paras = re.findall(r"(?is)<p[^>]*>(.*?)</p>", html)
        text = " ".join(re.sub(r"(?s)<[^>]+>", " ", p) for p in paras)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:limit]
    except Exception:
        return ""


def write_post(event, items):
    related = [i for i in items if i["id"] in event["item_ids"]]
    payload = {"event": event["event_key"], "items": related}
    data = gemini_json(
        WRITE_PROMPT,
        json.dumps(payload, ensure_ascii=False),
        temperature=0.2,
        model=WRITE_MODEL,
    )
    if not data.get("complete"):
        # Do not drop the story yet: read the full article from its own link and try again.
        full = []
        for i in related:
            body = fetch_article_text(i.get("link", ""))
            if body:
                full.append({"id": i["id"], "outlet": i["outlet"], "title": i["title"], "full_text": body})
        if not full:
            raise SkipStory("incomplete and no full article text available")
        log(f"Story incomplete in headlines; retrying with full article text ({len(full)} source(s))")
        payload = {"event": event["event_key"], "items": full}
        data = gemini_json(
            WRITE_PROMPT,
            json.dumps(payload, ensure_ascii=False),
            temperature=0.2,
            model=WRITE_MODEL,
        )
    if not data.get("complete"):
        raise SkipStory("still incomplete after reading full article")
    assert len(data["hindi"]) == 3 and len(data["roman_urdu"]) == 3, "bullet count must be exactly 3"
    return data


def format_message(data):
    hindi = "\n".join(f"• {b}" for b in data["hindi"])
    urdu = "\n".join(f"• {b}" for b in data["roman_urdu"])
    sources = ", ".join(data.get("sources", []))
    return (
        f"🚨 {data['headline']}\n\n"
        f"HINDI\n{hindi}\n\n"
        f"ROMAN URDU\n{urdu}\n\n"
        f"Source: {sources}"
    )


def post_text(data):
    """Facebook post text: the same headline and bullets shown on the card (no source names)."""
    hindi = "\n".join(f"• {b}" for b in data["hindi"])
    urdu = "\n".join(f"• {b}" for b in data["roman_urdu"])
    return f"🚨 {data['headline']}\n\nHINDI\n{hindi}\n\nROMAN URDU\n{urdu}"


def page_token():
    """Return the Page's own access token (needed to post photos); falls back to the given token."""
    user_token = os.environ["FB_PAGE_TOKEN"]
    page_id = os.environ["FB_PAGE_ID"]
    r = requests.get(f"{GRAPH}/me/accounts", params={"fields": "id,access_token", "access_token": user_token}, timeout=30)
    if r.status_code == 200:
        for acc in r.json().get("data", []):
            if acc.get("id") == page_id and acc.get("access_token"):
                return acc["access_token"]
    print(f"--- page token lookup [{r.status_code}]: using token as given", flush=True)
    return user_token


def publish(data, card_path):
    """Publish the black-and-gold card. Text lives on the image; caption is only the source line."""
    page_id = os.environ["FB_PAGE_ID"]
    token = page_token()
    caption = post_text(data)  # same headline and bullets as the card; no source names
    with open(card_path, "rb") as f:
        r = requests.post(
            f"{GRAPH}/{page_id}/photos",
            data={"message": caption, "access_token": token, "published": "true"},
            files={"source": ("card.png", f, "image/png")},
            timeout=60,
        )
    if r.status_code != 200:
        raise RuntimeError(f"Facebook publish failed: {r.status_code} {r.text}")
    return r.json()


def posts_today(posted):
    """Count live posts made today (Asia/Kolkata date)."""
    ist = timezone(timedelta(hours=5, minutes=30))
    today = datetime.now(ist).date().isoformat()
    count = 0
    for rec in posted:
        if rec.get("dry_run"):
            continue
        try:
            when = datetime.fromisoformat(rec["posted_at"]).astimezone(ist).date().isoformat()
        except Exception:
            continue
        if when == today:
            count += 1
    return count


def qualifies(event):
    if event.get("class") not in ("A", "B", "C"):
        return False, "not important or no new development"
    if event.get("duplicate_of"):
        return False, "duplicate"
    outlets = {i["outlet"] for i in event["_items"] if i["outlet"] in PREFERRED_OUTLETS}
    if len(outlets) < MIN_SOURCES:
        return False, f"only {len(outlets)} preferred source(s)"
    return True, "ok"


SAMPLE_EVENT = {
    "event_key": "Test event: Hyderabad traffic disruption",
    "item_ids": [0, 1],
    "class": "A",
}
SAMPLE_ITEMS = [
    {"id": 0, "outlet": "The Hindu", "title": "Traffic disrupted on Outer Ring Road near Gachibowli after vehicle breakdown", "summary": "Police said traffic was diverted and clearing work is under way."},
    {"id": 1, "outlet": "NDTV", "title": "Vehicle breakdown causes traffic jam on Outer Ring Road, Hyderabad", "summary": "Commuters are advised to take alternate routes."},
]


def test_write():
    log(f"TEST_WRITE with model {WRITE_MODEL}")
    data = write_post(SAMPLE_EVENT, SAMPLE_ITEMS)
    print(format_message(data), flush=True)
    return 0


def repost_previous():
    """One-off: republish the stories in data/reposts.json (no AI call) using the new card design."""
    reposts = json.loads((ROOT / "data" / "reposts.json").read_text(encoding="utf-8"))
    for data in reposts:
        card_path = Path("/tmp/nr_card.png")
        render_card(data["headline"], data["hindi"], data["roman_urdu"], "", card_path)
        log("REPOST: " + data["headline"])
        result = publish(data, card_path)
        log(f"Republished: {result}")
        time.sleep(5)
    return 0


def _old_repost_previous_unused():
    posted = load_posted()
    for rec in posted:
        if rec.get("dry_run"):
            continue
        event = {"event_key": rec["event_key"], "item_ids": [0], "class": rec.get("class", "B")}
        items = [{"id": 0, "outlet": "", "title": rec["event_key"], "summary": ""}]
        data = write_post(event, items)
        card_path = Path("/tmp/nr_card.png")
        render_card(data["headline"], data["hindi"], data["roman_urdu"], "", card_path)
        log("REPOST: " + rec["event_key"])
        result = publish(data, card_path)
        log(f"Republished: {result}")
        time.sleep(5)
    return 0


def diagnose():
    """Read-only check of the Facebook token: which app, which type, which Page. Never posts."""
    token = os.environ["FB_PAGE_TOKEN"]
    page_id = os.environ["FB_PAGE_ID"]
    r = requests.get(f"{GRAPH}/debug_token", params={"input_token": token, "access_token": token}, timeout=30)
    d = r.json().get("data", {})
    print("--- debug_token:", {k: d.get(k) for k in ["type", "app_id", "application", "is_valid", "expires_at", "scopes"]}, flush=True)
    r = requests.get(f"{GRAPH}/{page_id}", params={"fields": "id,name", "access_token": token}, timeout=30)
    print(f"--- page [{r.status_code}]: {r.text[:200]}", flush=True)
    gk = os.getenv("GEMINI_API_KEY", "")
    if gk:
        rm = requests.get("https://generativelanguage.googleapis.com/v1beta/models", params={"pageSize": 200}, headers={"x-goog-api-key": gk}, timeout=30)
        names = [m["name"].replace("models/", "") for m in rm.json().get("models", []) if "generateContent" in m.get("supportedGenerationMethods", [])] if rm.status_code == 200 else []
        print(f"--- gemini models [{rm.status_code}]: {', '.join(names)[:1500] or rm.text[:300]}", flush=True)
    for path, params in [(f"{page_id}", {"fields": "tasks"}), ("me/accounts", {"fields": "id,name,tasks"}), ("me", {"fields": "id,name"})]:
        r2 = requests.get(f"{GRAPH}/{path}", params={**params, "access_token": token}, timeout=30)
        print(f"--- {path} [{r2.status_code}]: {r2.text[:300]}", flush=True)
    return 0


def card_check():
    """Connection check: build a plain test card and upload it to the Page UNPUBLISHED (not visible publicly)."""
    page_id = os.environ["FB_PAGE_ID"]
    token = page_token()
    card_path = Path("/tmp/nr_check_card.png")
    render_card("CONNECTION TEST", ["परीक्षण कार्ड", "यह एक तकनीकी जांच है", "कोई समाचार नहीं"],
                ["Test card", "Connection check only", "Not a news story"], "", card_path)
    with open(card_path, "rb") as f:
        r = requests.post(
            f"{GRAPH}/{page_id}/photos",
            data={"message": "Connection check", "access_token": token, "published": "false"},
            files={"source": ("card.png", f, "image/png")},
            timeout=60,
        )
    print(f"--- card check [{r.status_code}]: {r.text[:300]}", flush=True)
    return 0 if r.status_code == 200 else 1


def publish_test_draft():
    """Publish one connection-check card publicly, then remove the earlier unpublished draft."""
    page_id = os.environ["FB_PAGE_ID"]
    token = os.environ["FB_PAGE_TOKEN"]
    card_path = Path("/tmp/nr_check_card.png")
    render_card("CONNECTION TEST", ["परीक्षण कार्ड", "यह एक तकनीकी जांच है", "कोई समाचार नहीं"],
                ["Test card", "Connection check only", "Not a news story"], "", card_path)
    with open(card_path, "rb") as f:
        r = requests.post(
            f"{GRAPH}/{page_id}/photos",
            data={"message": "Connection check", "access_token": token, "published": "true"},
            files={"source": ("card.png", f, "image/png")},
            timeout=60,
        )
    print(f"--- publish card [{r.status_code}]: {r.text[:300]}", flush=True)
    d = requests.delete(f"{GRAPH}/1637569854397717", params={"access_token": token}, timeout=30)
    print(f"--- delete draft [{d.status_code}]: {d.text[:200]}", flush=True)
    return 0 if r.status_code == 200 else 1


# ---------------------------------------------------------------------------
# One story per AI call (free-tier friendly). Each run looks at the single
# best unseen headline, makes ONE Gemini call that both judges and writes it,
# and counts that call against a daily budget.
# ---------------------------------------------------------------------------
SEEN_FILE = ROOT / "data" / "seen.json"
USAGE_FILE = ROOT / "data" / "ai_usage.json"
AI_DAILY_LIMIT = int(os.getenv("AI_DAILY_LIMIT", "18"))
IST_TZ = timezone(timedelta(hours=5, minutes=30))

HYD_WORDS = re.compile(r"hyderabad|telangana|secunderabad|rangareddy|warangal|karimnagar|nizamabad|khammam|medak|nalgonda|ghmc|revanth|brs|kcr|telangana", re.I)
POLITICS_WORDS = re.compile(r"protest|march|agitation|rally|bandh|election|\bsir\b|vote|assembly|parliament|supreme court|high court|governor|minister|cabinet|congress|bjp|opposition|bloc|chief minister|\bcm\b|tribunal|policy|government|govt|petition|verdict|ruling", re.I)
CRIME_WORDS = re.compile(r"arrest|murder|killed|\bdies\b|\bdead\b|theft|robbery|fraud|rape|drug|mdma|ganja|cyber|accident|stabbed|burns|suicide|loot|cheat|molest|kidnap", re.I)

SINGLE_PROMPT = """You are the editor of a Hyderabad/Telangana breaking-news Facebook page. You get ONE news story (outlet, headline, summary, article text). Decide and write in ONE reply.
Important = politics, protests, government or court decisions with public impact, major national or world events, or MAJOR crime (mass casualties, large police operations, high-profile cases). Routine crime, single arrests, petty theft, fraud, sports, entertainment, celebrity, opinion, features and shopping are NOT important.
STRICT RULES:
1. Use ONLY facts in the provided material. Never invent names, numbers, causes, places or quotes.
2. The story must answer WHO, WHAT, WHERE and WHY/CONTEXT. If the material does not clearly give all four, set "complete" to false.
3. No filler such as "more details awaited". Each bullet states a fact.
4. Never name any news outlet or source.
5. Hindi in simple Devanagari, Roman Urdu in simple Latin letters. Both carry the same 3 facts.
6. Exactly 3 Hindi bullets and exactly 3 Roman Urdu bullets.
Return JSON: {"important": true or false, "complete": true or false, "class": "A" or "B" or "C" or "D", "region": "HYDERABAD" or "TELANGANA" or "INDIA" or "WORLD", "event_key": "short English description", "headline": "LATEST NEWS — HYDERABAD" (or TELANGANA / INDIA / WORLD), "hindi": ["...","...","..."], "roman_urdu": ["...","...","..."]}"""


def _load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def ai_calls_today():
    today = datetime.now(IST_TZ).strftime("%Y-%m-%d")
    usage = _load_json(USAGE_FILE, {})
    return usage.get(today, 0), usage, today


def ai_bump():
    count, usage, today = ai_calls_today()
    usage[today] = count + 1
    _save_json(USAGE_FILE, usage)


def pick_item(items, seen_links, posted):
    posted_links = {l for p in posted for l in p.get("links", [])}
    posted_keys = {p.get("event_key", "").lower() for p in posted}
    best, best_score = None, None
    for it in items:
        if not it["link"] or it["link"] in seen_links or it["link"] in posted_links:
            continue
        if it["title"].lower() in posted_keys:
            continue
        text = f'{it["title"]} {it["summary"]}'
        base = 0 if HYD_WORDS.search(text) else (1 if POLITICS_WORDS.search(text) else 2)
        crime_penalty = 1 if CRIME_WORDS.search(text) and not POLITICS_WORDS.search(text) else 0
        score = (crime_penalty, base, -len(it.get("published", "")))
        if best_score is None or score < best_score:
            best, best_score = it, score
    return best


def run_single():
    posted = load_posted()
    seen = _load_json(SEEN_FILE, [])
    if posts_today(posted) >= MAX_POSTS_PER_DAY:
        log(f"Daily post cap of {MAX_POSTS_PER_DAY} reached. Nothing more today.")
        return 0
    used, _, _ = ai_calls_today()
    if used >= AI_DAILY_LIMIT:
        log(f"AI daily budget used ({used}/{AI_DAILY_LIMIT}). Waiting for tomorrow.")
        return 0

    items = fetch_items()
    item = pick_item(items, set(seen), posted)
    if not item:
        log("No new unseen headlines. Nothing to do.")
        return 0
    log(f"Story picked: [{item['outlet']}] {item['title']}")
    seen.append(item["link"])
    _save_json(SEEN_FILE, seen[-500:])

    article = fetch_article_text(item["link"])
    payload = {"outlet": item["outlet"], "title": item["title"], "summary": item["summary"], "article": article}
    ai_bump()
    try:
        data = gemini_json(SINGLE_PROMPT, json.dumps(payload, ensure_ascii=False), temperature=0.2, model=WRITE_MODEL)
    except Exception as e:
        log(f"AI error, story skipped: {str(e)[:160]}")
        return 0

    if not data.get("important") or data.get("class") not in ("A", "B", "C"):
        log("Not important enough, skipped.")
        return 0
    if not data.get("complete"):
        log("Incomplete story (who/what/where/why missing), skipped.")
        return 0
    if len(data.get("hindi", [])) != 3 or len(data.get("roman_urdu", [])) != 3:
        log("Bullet count is not 3, skipped.")
        return 0

    message = format_message(data)
    card_path = Path("/tmp/nr_card.png")
    render_card(data["headline"], data["hindi"], data["roman_urdu"], "", card_path)
    if DRY_RUN:
        log("DRY RUN - would publish card:\n" + message)
    else:
        result = publish(data, card_path)
        log(f"Published: {result}")
    posted.append({
        "event_key": data.get("event_key", item["title"]),
        "class": data.get("class"),
        "region": data.get("region"),
        "links": [item["link"]],
        "posted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dry_run": DRY_RUN,
    })
    save_posted(posted)
    log("Done. 1 post published.")
    return 0


# ---------------------------------------------------------------------------
# Digest mode: one card per run with 3 to 5 separate complete stories,
# built from ONE AI call (free-tier friendly).
# ---------------------------------------------------------------------------
SOFT_WORDS = re.compile(r"cricket|t20|\bmatch\b|\bipl\b|football|tennis|movie|film|trailer|actor|actress|celebrity|box office|recipe|horoscope|festive|festival sale|discount|offer|smartphone|review|launch|\bpyre\b|sculpture|feature|opinion|explainer|how to|tips", re.I)
DIGEST_POOL = 12          # headlines sent to the AI in one call
DIGEST_MIN = 3           # a card needs at least 3 complete stories
DIGEST_MAX = 5           # and at most 5

DIGEST_PROMPT = """You are the editor of a Hyderabad/Telangana breaking-news Facebook page. You get a numbered list of news items (outlet, headline, summary, article text). Choose the 3 to 5 most important items that can each be written as a COMPLETE short story, and write each one in Hindi and Roman Urdu.
Importance order: politics, protests, government or court decisions with public impact, and MAJOR national or world incidents come first. Hyderabad and Telangana items are preferred when they are equally important. Routine crime, single arrests, petty theft, fraud, sports, entertainment, celebrity, opinion, features and shopping are NOT important.
STRICT RULES:
1. Use ONLY facts in the provided items. Never invent names, numbers, causes, places, or quotes.
2. Each story must clearly state WHO, WHAT, WHERE and WHY/CONTEXT in one short sentence. If an item does not give all four, mark it "complete": false.
3. No filler such as "more details awaited". Never name any news outlet or source.
4. Hindi in simple Devanagari; Roman Urdu in simple Latin letters. Both carry the same facts, one bullet per story.
5. Keep each bullet short (one sentence).
Return JSON: {"headline": "LATEST NEWS — HYDERABAD" (or TELANGANA / INDIA / WORLD, the region of the most important story), "stories": [{"id": item id, "important": true or false, "complete": true or false, "event_key": "short English description", "region": "HYDERABAD" or "TELANGANA" or "INDIA" or "WORLD", "english": "one short complete English sentence with who, what, where and why", "hindi": "one Hindi sentence", "roman_urdu": "one Roman Urdu sentence (backup if the translator is not set up)"}]}"""


def _item_for_prompt(it):
    article = fetch_article_text(it["link"], limit=1200)
    return {"id": it["id"], "outlet": it["outlet"], "title": it["title"], "summary": it["summary"], "article": article}


def run_digest():
    posted = load_posted()
    seen = _load_json(SEEN_FILE, {})
    if isinstance(seen, list):  # older format: plain list of links
        seen = {l: 1 for l in seen}
    if posts_today(posted) >= MAX_POSTS_PER_DAY:
        log(f"Daily post cap of {MAX_POSTS_PER_DAY} reached. Nothing more today.")
        return 0
    used, _, _ = ai_calls_today()
    if used >= AI_DAILY_LIMIT:
        log(f"AI daily budget used ({used}/{AI_DAILY_LIMIT}). Waiting for tomorrow.")
        return 0

    items = fetch_items()
    pool = []
    seen_set = {l for l, n in seen.items() if n >= 2}  # tried twice without success, or used
    posted_links = {l for p in posted for l in p.get("links", [])}
    remaining = [it for it in items if it["link"] and it["link"] not in seen_set and it["link"] not in posted_links]
    # Best-first ordering using the same keyword priority as before
    def rank(it):
        text = f'{it["title"]} {it["summary"]}'
        base = 0 if HYD_WORDS.search(text) else (1 if POLITICS_WORDS.search(text) else 2)
        crime = 1 if CRIME_WORDS.search(text) and not POLITICS_WORDS.search(text) else 0
        return (crime, base)
    remaining = [it for it in remaining if not SOFT_WORDS.search(f'{it["title"]} {it["summary"]}')]
    def rank2(it):
        text = f'{it["title"]} {it["summary"]}'
        pol = 0 if POLITICS_WORDS.search(text) else 1
        hyd = 0 if HYD_WORDS.search(text) else 1
        crime = 1 if CRIME_WORDS.search(text) and not POLITICS_WORDS.search(text) else 0
        return (crime, pol, hyd)
    remaining.sort(key=rank2)
    pool = remaining[:DIGEST_POOL]
    if len(pool) < DIGEST_MIN:
        log("Not enough new headlines for a card this run.")
        return 0

    log("Digest pool: " + " | ".join(it["title"][:70] for it in pool))
    for it in pool:
        seen[it["link"]] = seen.get(it["link"], 0) + 1
    _save_json(SEEN_FILE, dict(list(seen.items())[-1000:]))

    payload = [_item_for_prompt(it) for it in pool]
    ai_bump()
    try:
        data = gemini_json(DIGEST_PROMPT, json.dumps({"items": payload}, ensure_ascii=False), temperature=0.2, model=WRITE_MODEL)
    except Exception as e:
        log(f"AI error, no card this run: {str(e)[:160]}")
        return 0

    by_id = {it["id"]: it for it in pool}
    chosen = []
    for st in data.get("stories", []):
        it = by_id.get(st.get("id"))
        if not it or not st.get("important") or not st.get("complete"):
            continue
        if not st.get("hindi") or not st.get("roman_urdu"):
            continue
        chosen.append((st, it))
    chosen = chosen[:DIGEST_MAX]
    for _, it in chosen:
        seen[it["link"]] = 99  # used: never reconsider
    _save_json(SEEN_FILE, dict(list(seen.items())[-1000:]))
    if len(chosen) < DIGEST_MIN:
        log(f"Only {len(chosen)} complete important stories this run; need {DIGEST_MIN}. No card.")
        return 0

    roman = [st["roman_urdu"] for st, _ in chosen]
    try:
        import translator as ru_translator
        english = [st.get("english", "") for st, _ in chosen]
        attempts = [{}]  # first: the translator's own configured provider (RU_* env, e.g. Groq)
        if XAI_API_KEY:
            attempts.append({"provider": "openai", "base_url": XAI_BASE_URL, "model": XAI_MODEL, "api_key": XAI_API_KEY})
        done = False
        for kw in attempts:
            res = ru_translator.translate_lines(english, **kw)
            if res.get("ok") and len(res.get("lines", [])) == len(chosen):
                roman = res["lines"]
                log("Roman Urdu from translator" + (" (Grok)" if kw else ""))
                done = True
                break
            log(f"Translator attempt failed: {res.get('problems') or res.get('warning')}")
        if not done:
            log("Using AI Roman Urdu")
    except Exception as e:  # never let the translator stop the card
        log(f"Translator error, using AI Roman Urdu: {str(e)[:120]}")

    card_data = {
        "headline": data.get("headline") or "LATEST NEWS",
        "hindi": [st["hindi"] for st, _ in chosen],
        "roman_urdu": roman,
        "sources": [],
    }
    card_path = Path("/tmp/nr_card.png")
    render_card(card_data["headline"], card_data["hindi"], card_data["roman_urdu"], "", card_path)
    if DRY_RUN:
        log("DRY RUN - would publish card:\n" + post_text(card_data))
    else:
        result = publish(card_data, card_path)
        log(f"Published: {result}")
    posted.append({
        "event_key": "; ".join(st.get("event_key", it["title"]) for st, it in chosen),
        "class": "B",
        "region": chosen[0][0].get("region"),
        "links": [it["link"] for _, it in chosen],
        "posted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dry_run": DRY_RUN,
    })
    save_posted(posted)
    log(f"Done. 1 card with {len(chosen)} stories.")
    return 0


def main():
    if os.getenv("DIAG", "0") == "1":
        return diagnose()
    if os.getenv("CARD_PUBLISH", "0") == "1":
        return publish_test_draft()
    if os.getenv("CARD_CHECK", "0") == "1":
        return card_check()
    if os.getenv("REPOST", "0") == "1":
        return repost_previous()
    if TEST_WRITE:
        return test_write()
    if os.getenv("DIGEST", "1") == "1":
        log(f"Start (one card per run, 3-5 stories, one AI call). DRY_RUN={DRY_RUN} MODEL={WRITE_MODEL}")
        return run_digest()
    log(f"Start. DRY_RUN={DRY_RUN} MODEL={MODEL} WRITE_MODEL={WRITE_MODEL} MIN_SOURCES={MIN_SOURCES}")
    posted = load_posted()
    items = fetch_items()
    if not items:
        log("No recent items. Nothing to do.")
        return 0

    try:
        events = classify(items, posted)
    except Exception as e:  # AI unavailable: do not post, do not crash noisily
        log(f"AI classification unavailable, nothing posted this run: {str(e)[:160]}")
        return 0
    by_id = {i["id"]: i for i in items}
    candidates = []
    for ev in events:
        ev["_items"] = [by_id[i] for i in ev.get("item_ids", []) if i in by_id]
        ok, reason = qualifies(ev)
        log(f"[{ev.get('class')}] {ev.get('event_key')} -> {'POST CANDIDATE' if ok else 'skip: ' + reason}")
        if ok and not any(p["event_key"] == ev["event_key"] for p in posted):
            candidates.append(ev)

    # Most important first: A before B before C
    region_rank = {"HYDERABAD": 0, "TELANGANA": 1, "INDIA": 2, "WORLD": 3}
    candidates.sort(key=lambda e: (e["class"], region_rank.get(str(e.get("region", "")).upper(), 9)))
    published = 0
    remaining_today = MAX_POSTS_PER_DAY - posts_today(posted)
    if remaining_today <= 0:
        log(f"Daily cap of {MAX_POSTS_PER_DAY} posts reached. Nothing more today.")
        save_posted(posted)
        return 0
    limit = min(MAX_POSTS, remaining_today)
    attempts = 0
    for ev in candidates:
        if published >= limit or attempts >= MAX_ATTEMPTS:
            break
        attempts += 1
        try:
            data = write_post(ev, items)
        except SkipStory as e:
            log(f"SKIP (not complete news): {ev['event_key']} -> {e}")
            continue
        except Exception as e:  # provider error on this story: log it and move on
            log(f"SKIP (AI error): {ev['event_key']} -> {str(e)[:160]}")
            continue
        message = format_message(data)
        card_path = Path("/tmp/nr_card.png")
        render_card(data["headline"], data["hindi"], data["roman_urdu"], "", card_path)
        if DRY_RUN:
            log("DRY RUN - would publish card:\n" + message)
        else:
            result = publish(data, card_path)
            log(f"Published: {result}")
        posted.append({
            "event_key": ev["event_key"],
            "class": ev["class"],
            "region": ev.get("region"),
            "links": [i["link"] for i in ev["_items"]],
            "posted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "dry_run": DRY_RUN,
        })
        published += 1
        time.sleep(5)

    save_posted(posted)
    log(f"Done. {published} post(s) {'prepared' if DRY_RUN else 'published'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
