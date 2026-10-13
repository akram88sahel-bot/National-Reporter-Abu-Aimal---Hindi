# roman_urdu_translator
### Portable English → Roman Urdu translation agent — kisi bhi project mein copy-paste karein

**Zero dependencies.** Sirf Python standard library, Python 3.9+.
Poora system prompt + 5 few-shot examples **`translator.py` ke andar embedded** hain,
isliye **ek single file** bhi poori tarah kaam karti hai.

```
roman_urdu_translator/
├── translator.py          ★ engine — akela hi kaafi hai (prompt embedded)
├── __init__.py              package exports (translate, translate_card, configure…)
├── cli.py                   command-line front end
├── server.py                optional HTTP API + browser demo page
├── card.py                  ★ OPTIONAL Pillow renderer — EN + Roman Urdu ek hi image par
├── examples/
│   └── sample_card.png      1200×1500 demo card (Azad Daily branding)
├── ci/
│   └── github-actions-tests.yml   copy → .github/workflows/ (85 offline tests in CI)
├── prompts/
│   ├── system_prompt.txt    OPTIONAL override — tune per project, no code change
│   └── few_shot_examples.md OPTIONAL override — apne niche ke examples add karein
├── config.example.json      copy → config.json (gitignored)
├── tests/
│   └── test_translator.py   66 offline tests, no API key needed
├── RECIPES.md               copy-paste integrations (Flask/FastAPI/cron/Pillow/n8n/Telegram)
├── pyproject.toml           optional: pip install -e .
├── requirements.txt         "none"
└── .env.example
```

---

## 1. Teen tareeqe, project ke hisaab se

### A) Sirf ek file chahiye (sab se common)

`translator.py` ko apne project mein kahin bhi rakhein:

```python
from translator import translate

print(translate("Petrol price may increase by Rs 8 per litre this week"))
# -> Is haftay petrol ki qeemat mein 8 rupay prati litre izafa hone ka imkaan
```

### B) Poora folder as a package

```python
from roman_urdu_translator import translate, translate_card, configure

configure(provider="openai", model="gpt-4o-mini", api_key="sk-...")

card = translate_card(
    title_en="Earthquake of magnitude 6.2 hits eastern Afghanistan",
    bullets_en=[
        "At least 18 people were killed and 40 injured",
        "Rescue teams reached the affected area after six hours",
        "The UN has promised emergency aid",
    ],
)
card["title_ur"]      # Mashriqi Afghanistan mein 6.2 intensity ka zalzala
card["bullets_ur"]    # ['Kam az kam 18 afraad jaan-ba-haq…', …]
card["roman_urdu"]    # card par print karne ke liye ready block
```

### C) HTTP microservice (n8n / Make / Zapier / frontend)

```bash
python3 server.py --port 8080          # ya: python3 cli.py --serve --port 8080
```

```bash
curl -X POST http://localhost:8080/translate-card \
  -H 'Content-Type: application/json' \
  -d '{"title_en":"Google launches new AI model","bullets_en":["The update is free"]}'
```

Browser mein `http://localhost:8080/` kholiye — card preview demo page mil jayega.

### D) Ready-made image card (Pillow, optional dependency)

```bash
pip install Pillow          # sirf card renderer ke liye; translator ko nahi chahiye
python3 card.py --sample --out sample_card.png
```

```python
from roman_urdu_translator.card import render_news_card

out = render_news_card(
    title_en="IMD issues orange alert as heavy monsoon rains lash Hyderabad",
    bullets_en=["At least 18 people were killed and 40 injured",
                "Rescue teams reached the affected area after six hours"],
    logo_path="logo.png",
    hashtags=["#BreakingNews", "#Hyderabad"],
    out_path="card.png",
)
out["path"], out["translation_ok"], out["problems"]
```

Default branding aapke news agent ki tarah hai — 1200×1500, gold `#D4A017`/`#F2C94C`,
black `#0A0A0A`, logo strip, dashed divider, footer line — English upar, Roman
Urdu neeche, **ek hi image par**. Sab kuch `card.json` ya keyword args se override
ho sakta hai (`BRANDING` dict dekhein). Layout khud fit hota hai: fonts chhote hote
hain, phir bhi na samaye to canvas lamba hota hai — text kabhi clip nahi hota.

`examples/sample_card.png` dekhein (API key ke baghair render hua demo).

---

## 2. Configuration (precedence)

**explicit kwarg → environment variable → `config.json` → default**

| Key | Default | Matlab |
|---|---|---|
| `RU_PROVIDER` | `openai` | `openai` (koi bhi OpenAI-compatible), `gemini`, `anthropic`, `mock` |
| `RU_MODEL` | `gpt-4o-mini` | `gpt-4o`, `llama-3.3-70b-versatile`, `gemini-2.0-flash`, `qwen2.5:7b`… |
| `RU_API_KEY` | – | aapki key (ya `OPENAI_API_KEY` / `GEMINI_API_KEY` / `ANTHROPIC_API_KEY`) |
| `RU_BASE_URL` | `https://api.openai.com/v1` | Groq / OpenRouter / Ollama / vLLM ke liye badlein |
| `RU_TEMPERATURE` | `0.2` | consistency ke liye 0.3 se upar na jayein |
| `RU_MAX_RETRIES` | `2` | validation fail par auto-retry |
| `RU_TIMEOUT` | `60` | seconds |
| `RU_MAXLEN` | `118` | card renderer ka per-line character cap (`None` se disable) |
| `RU_PASTE_N` | `4` | kitne consecutive English words verbatim = reject |

```bash
cp config.example.json config.json     # phir values bharein (gitignored)
python3 cli.py --config                # effective config dekh lein
```

**Provider recipes:**

```bash
# Groq — free tier, sub-second
RU_PROVIDER=openai RU_BASE_URL=https://api.groq.com/openai/v1 \
RU_MODEL=llama-3.3-70b-versatile RU_API_KEY=gsk_...

# OpenAI
RU_PROVIDER=openai RU_MODEL=gpt-4o-mini RU_API_KEY=sk-...

# Gemini
RU_PROVIDER=gemini RU_MODEL=gemini-2.0-flash RU_API_KEY=AIza...

# Local Ollama — bilkul free, koi internet nahi
RU_PROVIDER=openai RU_BASE_URL=http://localhost:11434/v1 \
RU_MODEL=qwen2.5:7b RU_API_KEY=ollama

# Offline plumbing test (koi key nahi)
RU_PROVIDER=mock
```

---

## 3. API reference

| Function | Returns | Kab use karein |
|---|---|---|
| `translate(text, **kw)` | `str` | sab se aasan — text in, Roman Urdu out |
| `translate_card(title_en, bullets_en, **kw)` | `dict` | headline + bullets (card/post/notification) |
| `translate_lines(lines, **kw)` | `{ok, lines, method, problems, warning}` | list in, aligned list out; **kabhi raise nahi karta** |
| `translate_news(text=None, *, title_en=, bullets_en=, **kw)` | `dict` (superset) | dono styles: plain text ya card |
| `translate_payload(payload, **kw)` | `dict` | JSON in/out — n8n/Make/webhook |
| `configure(**kw)` | `dict` | process-wide defaults set karein |
| `effective_config()` | `dict` | abhi kya config chal rahi hai (keys masked) |
| `configured(provider, api_key)` | `bool` | "kya translator usable hai?" gate |
| `validate(src, out, max_line_len, paste_n)` | `(bool, [problems])` | apna output khud check karna ho |
| `paste_check(en, ur, n)` | `bool` | English verbatim-run detector |
| `has_urdu_script(text)` | `bool` | Urdu/Arabic/Devanagari character detector |
| `trim_to(text, limit)` | `str` | word-boundary par fit karna |
| `strip_wrappers(text)` | `str` | LLM noise (fences, "Translation:") hatana |
| `load_system_prompt(path)` | `str` | prompt file/embedded se |
| `load_few_shot(path)` | `[messages]` | examples ko messages mein parse |

**`translate_news()` ka full return:**

```python
{
  "ok": True,                    # validation pass
  "roman_urdu": "...",           # ← card/post ke liye ready
  "lines_ur": ["...", "..."],    # ← input lines ke sath aligned
  "lines_en": ["...", "..."],
  "title_ur": "...",             # card mode
  "bullets_ur": ["...", "..."],  # card mode
  "method": "llm",               # llm | retry | fallback | none | noop
  "attempts": 1,
  "problems": [],                # validation issues (retry par model ko bheje jate hain)
  "warning": None                # fallback use hua to yahan reason
}
```

---

## 4. Yeh module aapke liye kya guarantee karta hai

Har model output validate hota hai; fail hone par **line-specific error report** ke
sath dobara poochha jata hai; sab fail ho to structure-preserving fallback:

| Guarantee | Kaise |
|---|---|
| Kabhi Urdu/Arabic/Devanagari script output nahi | regex guard + retry + final strip |
| N lines in → N lines out | line-count check |
| Bullet markers (`-`, `*`, `1.`, `•`) identical | marker-by-marker check |
| Digits/dates/percentages unchanged | number-preservation check |
| URLs, hashtags, @mentions, emojis preserved | explicit checks |
| Har line ≤ `RU_MAXLEN` (118) chars | length check + `trim_to()` |
| 4+ consecutive English words verbatim nahi | `paste_check()` |
| Output mostly-untranslated nahi | word-overlap detector |
| `"Sure, here is…"` jaisa wrapper nahi | `strip_wrappers()` |
| Pipeline kabhi crash nahi | `translate_lines()` never raises; fallback path |

---

## 5. Per-project tuning (bina code chhere)

Naye project ka apna niche ho (sports, crypto, weather, entertainment) to:

1. `prompts/few_shot_examples.md` mein apne 3-5 real examples add karein
   (`USER:` / `ASSISTANT:` blocks, `---` separators — format wahi rakhein)
2. Apni spelling pasand fix karni ho (`hukumat` vs `sarkar`) to
   `prompts/system_prompt.txt` ka Section 5 edit karein
3. Card ka line cap alag ho to `RU_MAXLEN=140` set kar dein
4. `prompts/` folder **optional** hai — na ho to embedded prompt chalega

File-based override isliye acha hai ke ek hi `translator.py` kayi projects mein
copy ho sakta hai aur har project ka apna prompt alag rahe.

---

## 6. Test

```bash
python3 tests/test_translator.py        # 66 passed, 0 failed (no API key)
python3 cli.py --config                 # effective configuration
python3 cli.py --mock --text "Google launches new AI model for smartphones"
```

Real model ke sath spot-check:

```bash
export RU_API_KEY=sk-...
python3 cli.py --json --text "Google launches new AI model
- The update is free for all Android users
- Battery usage will be reduced by up to 20%"
```

---

## 7. Cost

| Model | 1 card (title + 5 bullets, ~1k tokens with few-shot) | 24 cards/day ≈ month |
|---|---|---|
| `gpt-4o-mini` | ~$0.0004 | **~$0.30** |
| `gpt-4o` | ~$0.005 | ~$3.60 |
| `gemini-2.0-flash` | ~$0.0003 | ~$0.22 |
| Groq `llama-3.3-70b` | free tier | **$0** |
| Ollama local | local compute | **$0** |

`--no-few-shot` se ~600 tokens/call bachte hain, lekin quality girti hai —
sirf tab use karein jab volume bohot zyada ho.

---

## 8. Security

- `config.json` aur `.env` **gitignore** hain — real key kabhi commit na karein
- Prefer environment variables / secret managers (GitHub Secrets, AWS SSM, Vault)
- `server.py` ko public internet par rakhein to `RU_SERVER_TOKEN` set karein →
  har request par `Authorization: Bearer <token>` lazmi ho jata hai
- Server default `0.0.0.0` par bind karta hai; production mein reverse proxy
  (nginx/Caddy) + TLS ke peeche rakhein

---

## 9. Versioning

`__version__ = "2.0.0"` — embedded-prompt portable release.

`RECIPES.md` mein ready-to-paste integrations hain: Flask, FastAPI, Django,
plain cron script, Pillow image card, Telegram bot, n8n/Make webhook, aur
ek "copy into new project" checklist.
