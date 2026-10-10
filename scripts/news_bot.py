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
import os
import sys
import time
import calendar
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests

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
WRITE_MODEL = os.getenv("GEMINI_WRITE_MODEL", "gemini-flash-latest")
TEST_WRITE = os.getenv("TEST_WRITE", "0") == "1"
MAX_POSTS = int(os.getenv("MAX_POSTS_PER_RUN", "2"))
MIN_SOURCES = int(os.getenv("MIN_SOURCES", "2"))
LOOKBACK_HOURS = 3

GRAPH = "https://graph.facebook.com/v19.0"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def gemini_json(system_prompt, user_content, temperature=0.0, model=None):
    """Call Gemini and return the parsed JSON object it produces."""
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
        r = requests.post(
            GEMINI_URL.format(model=model or MODEL),
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=body,
            timeout=90,
        )
        if r.status_code == 200:
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text)
        if r.status_code in (429, 500, 503) and attempt < 2:
            log(f"Gemini returned {r.status_code}, retrying...")
            time.sleep(10 * (attempt + 1))
            continue
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


WRITE_PROMPT = """Write a Facebook breaking-news post for a Hyderabad/Telangana news page.
Use ONLY the facts in the provided source headlines and summaries. Never add names, numbers, causes or quotes that are not there.
Use cautious wording for developing or police-related facts, such as "Reports suggest", "Police said", "More details are awaited".
Return JSON:
{"headline": "LATEST NEWS — HYDERABAD" (or TELANGANA / INDIA / WORLD),
 "hindi": ["bullet 1", "bullet 2", "bullet 3"],   // simple Hindi in Devanagari, exactly 3 short bullets
 "roman_urdu": ["bullet 1", "bullet 2", "bullet 3"], // simple natural Roman Urdu (Latin letters), exactly 3 short bullets, same 3 facts
 "sources": ["The Hindu", "NDTV"]}"""


def write_post(event, items):
    related = [i for i in items if i["id"] in event["item_ids"]]
    data = gemini_json(
        WRITE_PROMPT,
        json.dumps({"event": event["event_key"], "items": related}, ensure_ascii=False),
        temperature=0.2,
        model=WRITE_MODEL,
    )
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


def publish(message):
    page_id = os.environ["FB_PAGE_ID"]
    token = os.environ["FB_PAGE_TOKEN"]
    with open(LOGO_FILE, "rb") as f:
        r = requests.post(
            f"{GRAPH}/{page_id}/photos",
            data={"message": message, "access_token": token, "published": "true"},
            files={"source": f},
            timeout=60,
        )
    if r.status_code != 200:
        raise RuntimeError(f"Facebook publish failed: {r.status_code} {r.text}")
    return r.json()


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


def main():
    if TEST_WRITE:
        return test_write()
    log(f"Start. DRY_RUN={DRY_RUN} MODEL={MODEL} WRITE_MODEL={WRITE_MODEL} MIN_SOURCES={MIN_SOURCES}")
    posted = load_posted()
    items = fetch_items()
    if not items:
        log("No recent items. Nothing to do.")
        return 0

    events = classify(items, posted)
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
    for ev in candidates[:MAX_POSTS]:
        data = write_post(ev, items)
        message = format_message(data)
        if DRY_RUN:
            log("DRY RUN - would publish:\n" + message)
        else:
            result = publish(message)
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
