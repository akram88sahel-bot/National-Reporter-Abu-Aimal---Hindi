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
    # Indian Express feeds currently return 403 to automated requests.
    # Add a working feed URL here when available.
]
PREFERRED_OUTLETS = {"The Hindu", "Indian Express", "NDTV"}

DRY_RUN = os.getenv("DRY_RUN", "1") == "1"
MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
WRITE_MODEL = os.getenv("GEMINI_WRITE_MODEL", "gemini-flash-latest")
TEST_WRITE = os.getenv("TEST_WRITE", "0") == "1"
MAX_POSTS = int(os.getenv("MAX_POSTS_PER_RUN", "1"))
MAX_POSTS_PER_DAY = int(os.getenv("MAX_POSTS_PER_DAY", "8"))
MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS_PER_RUN", "4"))
MIN_SOURCES = int(os.getenv("MIN_SOURCES", "2"))
LOOKBACK_HOURS = 3

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


def gemini_json(system_prompt, user_content, temperature=0.0, model=None):
    """Call Gemini and return the parsed JSON object it produces."""
    if os.getenv("LLM_PROVIDER", "gemini") == "openai" and os.getenv("OPENAI_API_KEY"):
        return openai_json(system_prompt, user_content, temperature)
    api_key = os.environ["GEMINI_API_KEY"]
    body = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_content}]}],
        "generationConfig": {
            "temperature": temperature,
            "responseMimeType": "application/json",
        },
    }
    for attempt in range(3):
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
        if r.status_code in (429, 500, 503) and attempt < 2:
            log(f"Gemini returned {r.status_code}, retrying...")
            time.sleep(10 * (attempt + 1))
            continue
        if r.status_code in (403, 429, 500, 503) and os.getenv("OPENAI_API_KEY"):
            log(f"Gemini unavailable ({r.status_code}): {r.text[:400]}")
            log("Using OpenAI fallback")
            return openai_json(system_prompt, user_content, temperature)
        raise RuntimeError(f"Gemini call failed: {r.status_code} {r.text[:500]}")


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


def publish(data, card_path):
    """Publish the black-and-gold card. Text lives on the image; caption is only the source line."""
    page_id = os.environ["FB_PAGE_ID"]
    token = os.environ["FB_PAGE_TOKEN"]
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
    return 0


def card_check():
    """Connection check: build a plain test card and upload it to the Page UNPUBLISHED (not visible publicly)."""
    page_id = os.environ["FB_PAGE_ID"]
    token = os.environ["FB_PAGE_TOKEN"]
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


def main():
    if os.getenv("DIAG", "0") == "1":
        return diagnose()
    if os.getenv("CARD_CHECK", "0") == "1":
        return card_check()
    if os.getenv("REPOST", "0") == "1":
        return repost_previous()
    if TEST_WRITE:
        return test_write()
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
    candidates.sort(key=lambda e: e["class"])
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
