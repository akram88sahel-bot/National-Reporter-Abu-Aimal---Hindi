# RECIPES — copy-paste integrations for any future project

Har recipe self-contained hai. `translator.py` (ya poora folder) project mein
copy karein, phir neeche wala code paste karein.

---

## 0. Naye project mein copy karne ki checklist

```bash
# poora folder (recommended)
cp -r roman_urdu_translator /path/to/new-project/

# YA sirf ek file (prompt embedded hai, akeli kaam karti hai)
cp roman_urdu_translator/translator.py /path/to/new-project/

cd /path/to/new-project
export RU_API_KEY=sk-...                 # ya config.json banayein
python3 -c "from translator import translate; print(translate('Petrol price may rise'))"
```

- [ ] `RU_API_KEY` env ya secret manager mein set hai (code mein hardcode nahi)
- [ ] `config.json` / `.env` `.gitignore` mein hai
- [ ] Card/UI ka line cap pata hai → `RU_MAXLEN` set (default 118)
- [ ] `python3 tests/test_translator.py` → 66 passed
- [ ] Apne niche ke 2-3 examples `prompts/few_shot_examples.md` mein add kiye

---

## 1. Flask / any WSGI app

```python
from flask import Flask, request, jsonify
from roman_urdu_translator import translate_card, translate, configured

app = Flask(__name__)

@app.get("/api/health")
def health():
    return jsonify({"translator_ready": configured()})

@app.post("/api/translate")
def api_translate():
    data = request.get_json(force=True)
    if "bullets_en" in data:
        return jsonify(translate_card(data.get("title_en", ""), data["bullets_en"]))
    return jsonify({"roman_urdu": translate(data.get("text", ""))})
```

---

## 2. FastAPI (async, with timeout guard)

```python
import asyncio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from roman_urdu_translator import translate_card

app = FastAPI()

class NewsIn(BaseModel):
    title_en: str = ""
    bullets_en: list[str] = []

@app.post("/translate-card")
async def translate_card_endpoint(payload: NewsIn):
    try:
        # translator is blocking (urllib) -> run it off the event loop
        return await asyncio.wait_for(
            asyncio.to_thread(
                translate_card, payload.title_en, payload.bullets_en
            ),
            timeout=90,
        )
    except asyncio.TimeoutError:
        raise HTTPException(504, "translation timed out")
```

---

## 3. Django

```python
# myapp/translator_service.py
from roman_urdu_translator import translate_card, translate_lines

def news_card_context(title_en, bullets_en):
    card = translate_card(title_en, bullets_en)
    return {
        "title_en": title_en,
        "bullets_en": bullets_en,
        "title_ur": card["title_ur"],
        "bullets_ur": card["bullets_ur"],
        "translation_ok": card["ok"],
    }
```

```python
# views.py
from django.http import JsonResponse
from .translator_service import news_card_context

def card_view(request):
    return JsonResponse(news_card_context("Petrol price may rise", ["Rs 8 per litre more"]))
```

---

## 4. Plain cron script (hourly job, never crashes)

```python
#!/usr/bin/env python3
"""hourly_post.py — cron: 5 * * * *  /usr/bin/python3 /opt/app/hourly_post.py"""
import logging, sys
from roman_urdu_translator import translate_lines, configured

log = logging.basicConfig(level=logging.INFO)

def main():
    if not configured():
        print("ERROR: RU_API_KEY not configured — refusing to post", file=sys.stderr)
        return 1                                  # cron mail / alert fires

    bullets_en = fetch_news_bullets()             # aapka existing scraper
    res = translate_lines(bullets_en)             # never raises

    if not res["ok"] or len(res["lines"]) != len(bullets_en):
        print(f"WARN translation degraded ({res['method']}): {res['problems']}",
              file=sys.stderr)
        # policy choose karein: post anyway (fallback quality) ya skip this hour
        # return 1

    render_and_post(bullets_en, res["lines"])
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

**Important pattern:** `configured()` pehle check karein. Agar key missing hai to
module `ok=False, lines=[]` deta hai — chup-chaap kharab output nahi. Yeh wahi
gate hai jo ek news pipeline mein "cards aana band" hone ki wajah banta hai,
isliye is par loud alert lagayein.

---

## 5. Image card (English + Roman Urdu ek hi image par) — `card.py` use karein

Hand-rolled Pillow code likhne ki zaroorat nahi: **`card.py`** poora renderer hai
(Azad Daily branding by default — 1200×1500, gold `#D4A017`, black `#0A0A0A`,
logo strip, dashed divider, footer). `examples/sample_card.png` dekhein.

```bash
pip install Pillow
python3 card.py --sample --out sample_card.png
```

```python
from roman_urdu_translator.card import render_news_card

out = render_news_card(
    title_en, bullets_en,
    logo_path="logo.png",
    hashtags=["#BreakingNews", "#Hyderabad"],
    out_path="card.png",
)
if not out["translation_ok"]:
    log.warning("card rendered English-only: %s", out["problems"])
```

### Apni branding / apna size

```python
from roman_urdu_translator import card

card.BRANDING.update({
    "image_width": 1080, "image_height": 1350,      # Instagram portrait
    "accent_gold": "#FFB800", "background_black": "#111111",
    "text_ur_title": "#FFFFFF", "text_ur_body": "#CCCCCC",
    "page_name": "My Page",
    "default_footer": "Follow for hourly updates",
})
```

Ya file se: package folder mein `card.json` rakh dein (gitignored nahi, isliye
keys kabhi na rakhein — sirf colors/sizes/fonts):

```json
{ "image_width": 1080, "image_height": 1350, "accent_gold": "#FFB800" }
```

### Guarantees jo `card.py` deta hai

- Har line pehle se `MAXLEN` (118) ke andar hoti hai (`translate_card` + `trim_to`),
  isliye overflow nahi hota
- Text canvas mein na samaye to fonts chhote hote hain (8 steps × 2px), phir bhi
  na samaye to canvas lamba hota hai — **clip kabhi nahi hota**
- Ek bohot lamba single word bhi pixel-width par hard-split hota hai
- Translation fail ho to crash nahi: English-only card render hota hai aur
  `translation_ok=False` + `problems` return hote hain (job chalta rehta hai)

---

## 6. Telegram / WhatsApp / Discord bot

```python
from telegram import Update
from telegram.ext import ContextTypes
from roman_urdu_translator import translate

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    en = update.message.text
    ur = translate(en, max_line_len=None)     # chat mein 118 cap ki zaroorat nahi
    await update.message.reply_text(f"{en}\n\n{ur}")
```

Note: chat/free-form text ke liye `max_line_len=None` dein — 118 cap sirf
rendered cards ke liye hai.

---

## 7. n8n / Make / Zapier (HTTP)

```bash
python3 server.py --port 8080
RU_SERVER_TOKEN=my-secret python3 server.py --port 8080   # public host par
```

**n8n HTTP Request node**

```
POST http://YOUR-HOST:8080/translate-card
Headers: Content-Type: application/json
         Authorization: Bearer my-secret          (agar RU_SERVER_TOKEN set hai)
Body (JSON):
{
  "title_en": "={{ $json.title }}",
  "bullets_en": {{ JSON.stringify($json.bullets) }}
}
```

Response ke `card_text_en` aur `card_text_ur` seedha image/post node ko dein.

**Make.com:** HTTP → "Make a request" → same body. `bullets_ur[]` array ko
Iterator se card renderer tak bhejein.

**Zapier:** Webhooks by Zapier → POST, phir "Formatter → Text" se lines split.

Ready n8n nodes: `n8n_workflow_snippet.json` (agar aap news-agent repo se copy
kar rahe hain to wahan mojood hai).

---

## 8. Batch / bulk (CSV, JSONL, database)

```python
import csv, json
from roman_urdu_translator import translate_lines

BATCH = 20                                   # ek call mein 20 lines = sasta + consistent

def translate_file(inp, outp, field="headline"):
    rows = list(csv.DictReader(open(inp, encoding="utf-8")))
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        res = translate_lines([r[field] for r in chunk])
        for row, ur in zip(chunk, res["lines"]):
            row[field + "_ur"] = ur
        if not res["ok"]:
            print("batch", i, "degraded:", res["problems"][:1])

    w = csv.DictWriter(open(outp, "w", encoding="utf-8", newline=""),
                       fieldnames=list(rows[0].keys()))
    w.writeheader(); w.writerows(rows)
```

**Batching tip:** ek call mein zyada lines bhejne se (a) cost kam, (b) wording
consistent rehti hai. Lekin `max_tokens` provider limit ka khyaal rakhein —
20-40 lines per call safe hai.

---

## 9. Custom validation apne rules ke sath

```python
from roman_urdu_translator import validate, has_urdu_script, paste_check

def my_gate(en, ur):
    ok, problems = validate(en, ur, max_line_len=140, paste_n=5)
    if len(ur.split()) < 4:
        problems.append("too-short-urdu")
    if not any(ch.isdigit() for ch in ur) and any(ch.isdigit() for ch in en):
        problems.append("lost-numbers")
    return ok and not problems, problems
```

`validate()` ke saare checks aapke own rules ke sath combine ho sakte hain —
return `(bool, [problems])` hota hai.

---

## 10. Prompt ko project ke mutabiq badalna (bina code chhere)

```python
from roman_urdu_translator import translate_news, load_system_prompt

# apni file se prompt
prompt = load_system_prompt("/path/to/my_project_prompt.txt")

out = translate_news("Headline\n- bullet one", system_prompt=prompt)
```

Ya folder ke andar `prompts/system_prompt.txt` rakh dein — woh embedded prompt
par automatically override ho jata hai. Isse ek hi `translator.py` kai projects
mein chal sakta hai, har project ka apna tone/style.

**Style examples:**
- Kids' content → "Simple, chhote jumlay; technical words avoid karein"
- Sports blog → "Match-report tone; scores aur player names exactly rakhein"
- Finance → "Numbers/currency kabhi round na karein; 'arab'/'crore' source ke mutabiq"
- Formal govt → "Darbari Urdu theek hai: 'se mutaliq', 'baja tor par', 'aitarafi'"

---

## 11. Failure handling policy (choose one, be explicit)

| Policy | Kab | Code |
|---|---|---|
| **Post anyway** | Fallback Urdu acceptable hai | `translate_lines()` ka result use karein, `ok` ignore |
| **Retry then skip** | Quality bar high hai | `if not res["ok"]: skip_this_item()` |
| **Loud fail** | Public brand, galti afford nahi | `if not configured(): raise` + alert |
| **Human review** | Sensitive content | `if not res["ok"]: queue_for_review(res["problems"])` |

News automation ke liye recommended: `configured()` par loud fail (key missing =
alert), aur per-item par "retry then post fallback" — taaki ek kharab item poori
pipeline na roke.

---

## 12. Docker (kisi bhi project mein sidecar service)

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY roman_urdu_translator/ ./roman_urdu_translator/
ENV PYTHONUNBUFFERED=1 PORT=8080 RU_PROVIDER=openai RU_MODEL=gpt-4o-mini
EXPOSE 8080
RUN python3 roman_urdu_translator/tests/test_translator.py
HEALTHCHECK --interval=30s --timeout=5s CMD python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=4)"
CMD ["python3", "roman_urdu_translator/server.py", "--host", "0.0.0.0", "--port", "8080"]
```

```bash
docker build -t roman-urdu .
docker run -d -p 8080:8080 -e RU_API_KEY=sk-... roman-urdu
```

---

## 13. Testing apne project mein

```python
# tests/test_my_feature.py
import os
os.environ.setdefault("RU_PROVIDER", "mock")     # tests mein API call nahi

from roman_urdu_translator import translate_lines, has_urdu_script

def test_bullets_aligned():
    res = translate_lines(["Petrol price may rise", "The government reviews prices"])
    assert len(res["lines"]) == 2
    assert not any(has_urdu_script(l) for l in res["lines"])
    assert all(len(l) <= 118 for l in res["lines"])
```

`mock` provider se tests fast, free aur deterministic rehte hain — CI mein
API key ki zaroorat nahi. Production path test karna ho to alag `@pytest.mark.live`
banayein.
