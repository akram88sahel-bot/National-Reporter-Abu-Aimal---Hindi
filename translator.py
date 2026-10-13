#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
translator.py -- PORTABLE English -> Roman Urdu translation agent.

Copy this ONE FILE (or the whole roman_urdu_translator/ folder) into any
project. Zero third-party dependencies, Python 3.9+.

The full system prompt and the few-shot examples are EMBEDDED below, so the
file works standalone. If a prompts/system_prompt.txt or
prompts/few_shot_examples.md sits next to it, those files win (handy for
tuning one project without touching code).

Three ways to use it
--------------------
1) One-liner:
       from translator import translate
       ur = translate("Petrol price may increase by Rs 8 per litre")

2) News card (title + bullets, structure preserved, 118-char cap enforced):
       from translator import translate_card
       card = translate_card(title_en="...", bullets_en=["...", "..."])
       card["title_ur"], card["bullets_ur"], card["roman_urdu"]

3) Package (if you copied the whole folder):
       from roman_urdu_translator import translate, translate_lines, configure

Providers: any OpenAI-compatible endpoint (OpenAI, Groq, OpenRouter, DeepInfra,
Ollama, vLLM, LM Studio), Google Gemini, Anthropic Claude, plus an offline
"mock" mode for plumbing tests.

Configuration: environment variables (RU_PROVIDER, RU_MODEL, RU_API_KEY,
RU_BASE_URL, RU_TEMPERATURE, RU_MAX_RETRIES, RU_TIMEOUT, RU_MAXLEN, RU_PASTE_N),
a config.json next to this file, or explicit keyword arguments. Explicit
kwargs always win.

Safety for automation: the model output is validated (Roman-script-only, line
count, bullet markers, numbers, URLs, hashtags, emojis, per-line length, English
paste runs) and re-asked with a line-specific error report. If everything fails,
a deterministic structure-preserving fallback is returned so a pipeline never
crashes and never publishes Urdu-script text.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# Configuration
# Precedence: explicit kwarg  >  environment variable  >  config.json  >  default
# --------------------------------------------------------------------------

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
PROMPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")


def _load_config_file(path: str = CONFIG_PATH) -> Dict[str, Any]:
    """Optional config.json sitting next to this file (see config.example.json)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


_CFG: Dict[str, Any] = _load_config_file()


def _cfg(key: str, default: Any = "") -> Any:
    """environment variable > config.json > default."""
    env = os.environ.get(key)
    if env not in (None, ""):
        return env
    val = _CFG.get(key)
    if val not in (None, ""):
        return val
    return default


def configure(**kwargs) -> Dict[str, Any]:
    """
    Set defaults at runtime for this process. Useful in a project's bootstrap:

        from roman_urdu_translator import configure
        configure(provider="openai", model="gpt-4o-mini", api_key="sk-...",
                  temperature=0.2, max_line_len=118)

    Accepts: provider, model, api_key, base_url, temperature, max_retries,
    timeout, max_line_len, paste_n. Returns the effective configuration.
    """
    mapping = {
        "provider": "RU_PROVIDER", "model": "RU_MODEL", "api_key": "RU_API_KEY",
        "base_url": "RU_BASE_URL", "temperature": "RU_TEMPERATURE",
        "max_retries": "RU_MAX_RETRIES", "timeout": "RU_TIMEOUT",
        "max_line_len": "RU_MAXLEN", "paste_n": "RU_PASTE_N",
    }
    for arg, envname in mapping.items():
        if arg in kwargs and kwargs[arg] is not None:
            _CFG[envname] = kwargs[arg]
    _refresh_defaults()
    if kwargs.get("api_key"):
        for prov in API_KEYS:
            API_KEYS[prov] = kwargs["api_key"]
    return effective_config()


def effective_config() -> Dict[str, Any]:
    """The configuration currently in effect (API keys are masked)."""
    return {
        "provider": DEFAULT_PROVIDER, "model": DEFAULT_MODEL,
        "temperature": DEFAULT_TEMPERATURE, "max_retries": DEFAULT_MAX_RETRIES,
        "timeout": DEFAULT_TIMEOUT, "max_line_len": MAXLEN, "paste_n": PASTE_N,
        "base_url": _cfg("RU_BASE_URL", "https://api.openai.com/v1"),
        "api_key_set": bool(_cfg("RU_API_KEY")),
        "config_file": CONFIG_PATH if os.path.isfile(CONFIG_PATH) else None,
    }


def _refresh_defaults() -> None:
    global DEFAULT_PROVIDER, DEFAULT_MODEL, DEFAULT_TEMPERATURE
    global DEFAULT_MAX_RETRIES, DEFAULT_TIMEOUT, MAXLEN, PASTE_N
    global ENDPOINTS, API_KEYS
    DEFAULT_PROVIDER = str(_cfg("RU_PROVIDER", "openai"))
    DEFAULT_MODEL = str(_cfg("RU_MODEL", "gpt-4o-mini"))
    DEFAULT_TEMPERATURE = float(_cfg("RU_TEMPERATURE", "0.2"))
    DEFAULT_MAX_RETRIES = int(_cfg("RU_MAX_RETRIES", "2"))
    DEFAULT_TIMEOUT = int(_cfg("RU_TIMEOUT", "60"))
    MAXLEN = int(_cfg("RU_MAXLEN", "118"))
    PASTE_N = int(_cfg("RU_PASTE_N", "4"))
    ENDPOINTS = {
        "openai": str(_cfg("RU_BASE_URL", _cfg("OPENAI_BASE_URL", "https://api.openai.com/v1"))).rstrip("/")
        + "/chat/completions",
        "gemini": str(_cfg("RU_GEMINI_URL", "https://generativelanguage.googleapis.com/v1beta/models")).rstrip("/"),
        "anthropic": str(_cfg("RU_ANTHROPIC_URL", "https://api.anthropic.com/v1/messages")),
    }
    API_KEYS = {
        "openai": str(_cfg("RU_API_KEY") or _cfg("OPENAI_API_KEY", "")),
        "gemini": str(_cfg("RU_API_KEY") or _cfg("GEMINI_API_KEY") or _cfg("GOOGLE_API_KEY", "")),
        "anthropic": str(_cfg("RU_API_KEY") or _cfg("ANTHROPIC_API_KEY", "")),
    }


_refresh_defaults()



# ==========================================================================
# EMBEDDED PROMPTS -- these make the single file fully portable.
# A prompts/*.txt file next to this module overrides them.
# ==========================================================================

EMBEDDED_SYSTEM_PROMPT = """You are a professional English -> Roman Urdu translation engine for a Pakistani/Indian news-publishing automation. You translate news headlines, bullet points, captions and short articles into natural, easy-to-read Roman Urdu.

============================
1. OUTPUT CONTRACT (STRICT)
============================
- Output ONLY the Roman Urdu translation. No preamble, no "Sure", no "Translation:", no notes, no explanation, no apology, no sign-off.
- No markdown fences, no JSON, no quotes around the output unless they exist in the input.
- Reproduce the input STRUCTURE exactly:
  * Same number of lines, in the same order.
  * Same blank lines at the same positions.
  * Same bullet markers / numbering style as the input (e.g. "-", "*", "1.", "•"). Do not add markers that are not in the input, do not remove markers that are, do not renumber.
  * Same paragraph breaks.
- If the input has 5 bullets, the output MUST have exactly 5 bullets.
- Translate EVERY line. Never skip, merge, summarize, shorten, or reorder anything.
- Keep the output length roughly similar to the input (news bullets should stay short enough to fit on a graphic card). Do not expand a bullet into a paragraph.
- HARD CARD CAP: every output line must be at most 118 characters. This is a renderer limit, not a suggestion. If a line would be longer, shorten the Urdu phrasing (drop filler like "yeh baat yaad rahe ke", "ke mutabiq" when redundant, use tighter verbs) — but NEVER drop a fact, a name, a number or a date to save space.
- NO ENGLISH PASTE: never copy a run of 4 or more consecutive English words into the Urdu line. "Telangana targets 5 GW data centre capacity" must become Urdu phrasing that keeps only the proper nouns and technical nouns (Telangana, 5 GW, data centre), not the English sentence structure. Two-word technical terms and proper nouns are fine; whole English clauses are not.

============================
2. LANGUAGE RULES
============================
- Roman script ONLY. Never use Urdu/Arabic/Persian script characters (ا ب پ ت ٹ ث ج چ ح خ د ڈ ذ ر ڑ ز ژ س ش ص ض ط ظ ع غ ف ق ک گ ل م ن و ہ ی ے). If any such character appears, your output is invalid.
- Write Roman Urdu the way literate Pakistani news readers actually write it on social media. Natural, spoken-but-clean register.
- Do NOT produce Hindi-heavy Roman ("Hinglish") wording where an Urdu word is the normal Pakistani usage, and do NOT produce stiff Persianized "textbook" Urdu that nobody says aloud.
- Avoid literal word-for-word translation. Translate meaning, then re-express it in idiomatic Urdu.
  BAD:  "Sarkar ne faisla kiya ki woh taxes ko barhaye gi"
  GOOD: "Hukumat ne tax barhanay ka faisla kar liya"
- Use everyday Urdu connective style: "...ka elaan kar diya", "...ne wazeh kar diya", "...ke mutabiq", "...taaham", "...jabke", "...iske bar-aks", "...mein izafa hua", "...par tanqeed ki".
- Keep sentences crisp and news-like. Present/past tense must match the source.
- Never add information, opinions, adjectives, or context that is not in the source.
- Never censor, soften or change factual claims (numbers, dates, names, casualty figures, quotes, allegations). Stay neutral and factual.

============================
3. KEEP IN ENGLISH (do not translate)
============================
Keep these exactly as they appear in the input:
- Person names, place names, organization names, brand names, product names, team names (e.g. Elon Musk, Islamabad, Pakistan, Google, Facebook, iPhone 17, Manchester United, PSL, ICC).
- Job titles when used as part of a name phrase, if that is how the source writes it.
- Technical / internet terms with no common Urdu equivalent: software, app, AI, cloud, server, update, download, streaming, EV, satellite launch, etc.
- Currency symbols and codes: $, Rs, PKR, INR, USD, EUR, AED.
- Units: km, kg, GB, Mbps, °C.
- Hashtags (#PakistanCricket), @mentions, URLs, email addresses, phone numbers.
- Emojis and symbols (✅ 🔴 🚨 📉 etc.) — keep them in the same position relative to the text.
- Established English phrases commonly used in Urdu media: "breaking news", "live", "press release", "press conference" (optional: "prees conference").

Translate common loanwords into their natural Urdu form when Urdu media does so:
- government -> hukumat, administration -> intezamia, announcement -> elaan, decision -> faisla,
  statement -> bayan, investigation -> tehqeeq / jaanch, protest -> ehtijaj, meeting -> mulaqat / ijlas,
  agreement -> muahida, election -> intikhabat / chunaav, economy -> maeshat, prices -> qeematein,
  increase -> izafa, decrease -> kami, ban -> pabandi, warning -> intebah / khatra, talks -> muzakirat.

============================
4. NUMBERS, DATES, FORMATTING
============================
- Do not convert digits to words. Keep digits as digits.
- Keep the date format of the source (e.g. "12 March 2026" stays "12 March 2026"; do not rewrite as "12/03/2026").
- Keep numbers, percentages, scores and statistics exactly (e.g. "7.4%" stays "7.4%", "3-1" stays "3-1").
- Do not change or "localise" currency amounts (e.g. "$2 billion" -> "2 arab dollar", not a converted PKR figure).
- Never invent a number or a date.

============================
5. TRANSLITERATION STYLE (be consistent)
============================
- Use the mainstream Pakistani Roman style. Prefer:
  hukumat, intezamia, moashra, maeshat, elaan, wazir-e-ala, sadar, bain-ul-aqwami,
  afraad, shehriyon, muamlaat, koshishen, tawajjo, mutabiq, tehqeeq, jaanch,
  barhaya, kam kiya, qaim, manzoor, rad, tanqeed, pareshan, hairat, shauq,
  logon, shehron, mulk, duniya, roz, haftay, mahinay, saal, ghantay, minute.
- Keep hyphenation consistent for compound titles: wazir-e-ala, bain-ul-aqwami, sar-e-aam.
- Do not mix two spellings of the same word inside one output (e.g. never use both "hukumat" and "sarkar" for the same source word).
- Do not use diacritics or accents (no "ā", "ī", "ū", "ñ", "é").
- Do not use ASCII hacks like "7" for "q" or "2" for "h".

============================
6. QUALITY SELF-CHECK (run silently before replying)
============================
Before answering, silently verify:
1. Is there any Urdu/Arabic script character? -> remove it.
2. Line count == input line count? Bullet markers identical? -> fix.
3. Any English word left untranslated that should have been Urdu? -> fix.
4. Any word translated that should have stayed English (name, brand, hashtag, URL)? -> restore.
5. Any number, date or fact changed? -> restore exactly.
6. Does each line read like a Pakistani news anchor wrote it? -> smooth it.
7. Did I add anything not in the source? -> delete it.
Then output the translation only.

============================
7. EDGE CASES
============================
- If a line is already in Roman Urdu, return it unchanged.
- If a line is a hashtag, mention, URL, or pure number, return it unchanged.
- If a line is empty, return an empty line.
- If the input is a direct quote (inside quotation marks), translate the quote but keep the quotation marks.
- If the input contains a term you are unsure about, choose the most common Urdu news phrasing rather than leaving it blank.
- Never refuse to translate. Never ask questions. Never output anything except the translation."""

EMBEDDED_FEWSHOT = """# Few-shot examples — English to Roman Urdu (news bullets)

Use these as reference examples. You can either paste them inside the system prompt
(best quality, slightly more tokens) or send them as `messages` with role="user"/"assistant"
before the real translation request.

The most important thing these examples teach the model:
same number of bullets, same markers, names/numbers untouched, natural Urdu phrasing.

------------------------------------------------------------
EXAMPLE 1 — headline + 4 bullets
------------------------------------------------------------
USER:
PM announces new tax relief package for salaried class
- Finance Minister confirms relief will start from July 2026
- Salary up to Rs 100,000 per month will be fully exempt
- The government expects to lose Rs 45 billion in revenue
- Opposition parties call the announcement "an election gimmick"

ASSISTANT:
Wazir-e-Azam ka salary-class ke liye naye tax relief package ka elaan
- Finance Minister ne tasdeeq ki hai ke relief July 2026 se shuru hoga
- 100,000 rupay mahana tak ki tankhwa mukammal tor par exempt hogi
- Hukumat ko 45 arab rupay ki revenue ka nuqsan hone ka imkaan
- Mukhalif jamaaton ne elaan ko "intikhabi hathyar" qarar de diya

------------------------------------------------------------
EXAMPLE 2 — sport, names + scores preserved
------------------------------------------------------------
USER:
🏏 Pakistan beat Australia by 5 wickets in the 3rd ODI
- Babar Azam scored 84 runs off 91 balls
- Shaheen Afridi took 3 wickets for 42 runs
- The series is now level at 2-2
- The final match will be played in Lahore on 12 March

ASSISTANT:
🏏 Pakistan ne teesre ODI mein Australia ko 5 wickets se shikast de di
- Babar Azam ne 91 balls par 84 runs banaye
- Shaheen Afridi ne 42 runs de kar 3 wickets haasil keen
- Series ab 2-2 se barabar ho gayi hai
- Aakhri match 12 March ko Lahore mein khela jayega

------------------------------------------------------------
EXAMPLE 3 — tech, English terms kept
------------------------------------------------------------
USER:
Google launches new AI model for smartphones
- The update is free for all Android users
- It can work offline without internet
- Battery usage will be reduced by up to 20%
- The feature will first reach Pixel and Samsung devices

ASSISTANT:
Google ne smartphones ke liye naya AI model launch kar diya
- Yeh update tamam Android users ke liye muft hai
- Yeh internet ke baghair offline bhi kaam kar sakta hai
- Battery ka istemaal 20% tak kam ho jayega
- Yeh feature sab se pehle Pixel aur Samsung devices tak pohnchay ga

------------------------------------------------------------
EXAMPLE 4 — world news, neutral tone
------------------------------------------------------------
USER:
Earthquake of magnitude 6.2 hits eastern Afghanistan
- At least 18 people were killed and 40 injured
- Rescue teams reached the affected area after six hours
- The UN has promised emergency aid
- Roads and communication networks remain badly damaged

ASSISTANT:
Mashriqi Afghanistan mein 6.2 intensity ka zalzala
- Kam az kam 18 afraad jaan-ba-haq aur 40 zakhmi ho gaye
- Rescue teams che ghantay baad mutasira ilaqay mein pohnch gayeen
- UN ne hungami emdad ka wada kiya hai
- Sarkein aur communication networks ko shadeed nuqsan pohncha

------------------------------------------------------------
EXAMPLE 5 — single headline only
------------------------------------------------------------
USER:
Petrol price may increase by Rs 8 per litre this week

ASSISTANT:
Is haftay petrol ki qeemat mein 8 rupay prati litre izafa hone ka imkaan

------------------------------------------------------------
WHAT A BAD TRANSLATION LOOKS LIKE (never do this)
------------------------------------------------------------
- "सरकार" or "حکومت"            -> wrong script (must be Roman only)
- "Sarkar ne tax kam kiya"        -> Hindi flavour, use "Hukumat ne tax mein kami ki"
- "Wazeer-e-Aala ne elaan kiya ke woh tax ko kam karein ge" -> literal, clumsy
- "Pak ne Aus ko haraya"          -> over-abbreviated, unnatural
- Dropping the 🏏 emoji            -> must be preserved
- Changing "6.2" to "chhe point do"-> digits must stay digits
- Adding "yaad rahe ke"            -> never add anything not in the source"""

# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------

_SYSTEM_PROMPT_CACHE: Optional[str] = None


def load_system_prompt(path: Optional[str] = None) -> str:
    """Load the system prompt. Falls back to a compact built-in version."""
    global _SYSTEM_PROMPT_CACHE
    if _SYSTEM_PROMPT_CACHE and path is None:
        return _SYSTEM_PROMPT_CACHE

    candidates = [path] if path else [
        os.path.join(PROMPT_DIR, "system_prompt.txt"),
        os.path.join(os.getcwd(), "prompts", "system_prompt.txt"),
    ]
    for cand in candidates:
        if cand and os.path.isfile(cand):
            try:
                with open(cand, "r", encoding="utf-8") as fh:
                    text = fh.read().strip()
                if text:
                    if path is None:
                        _SYSTEM_PROMPT_CACHE = text
                    return text
            except OSError:
                pass

    if EMBEDDED_SYSTEM_PROMPT.strip():
        if path is None:
            _SYSTEM_PROMPT_CACHE = EMBEDDED_SYSTEM_PROMPT
        return EMBEDDED_SYSTEM_PROMPT

    compact = (
        "You are a professional English to Roman Urdu translation engine for Pakistani news media. "
        "Output ONLY the Roman Urdu translation: no explanation, no notes, no markdown fences. "
        "Roman script only - never Urdu/Arabic characters. "
        "Keep the exact same number of lines, the same blank lines and the same bullet markers as the input. "
        "Translate every line; never summarize, skip, merge or reorder. "
        "Keep names, brands, places, hashtags, mentions, URLs, emojis, currency codes, units, digits and dates exactly as they are. "
        "Do not convert digits into words. "
        "Use natural Pakistani news-style Roman Urdu (hukumat, elaan, izafa, tehqeeq, mutabiq, afraad, shehriyon), "
        "not Hindi-heavy wording and not literal word-for-word translation. "
        "Stay factual and neutral. Never add information that is not in the source."
    )
    if path is None:
        _SYSTEM_PROMPT_CACHE = compact
    return compact


def load_few_shot(path: Optional[str] = None) -> List[Dict[str, str]]:
    """
    Parse prompts/few_shot_examples.md into OpenAI-style messages.
    Returns [] if the file is missing or unparseable (optional feature).
    """
    candidates = [path] if path else [os.path.join(PROMPT_DIR, "few_shot_examples.md")]
    raw = ""
    for cand in candidates:
        if cand and os.path.isfile(cand):
            try:
                with open(cand, "r", encoding="utf-8") as fh:
                    raw = fh.read()
                break
            except OSError:
                raw = ""
    if not raw:
        raw = EMBEDDED_FEWSHOT      # portable single-file mode
    if not raw:
        return []

    messages: List[Dict[str, str]] = []
    blocks = re.split(r"\n-{20,}\n", raw)
    for block in blocks:
        if "USER:" not in block or "ASSISTANT:" not in block:
            continue
        user_part = block.split("USER:", 1)[1].split("ASSISTANT:", 1)[0].strip()
        asst_part = block.split("ASSISTANT:", 1)[1].strip()
        if user_part and asst_part and "IGNORE THIS LINE" not in asst_part:
            messages.append({"role": "user", "content": user_part})
            messages.append({"role": "assistant", "content": asst_part})
    return messages


# --------------------------------------------------------------------------
# HTTP helpers
# --------------------------------------------------------------------------


def _post(url: str, payload: Dict[str, Any], headers: Dict[str, str], timeout: int) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # noqa: PERF203
        body = exc.read().decode("utf-8", "replace")[:600]
        raise RuntimeError(f"HTTP {exc.code} from {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error calling {url}: {exc.reason}") from exc


def call_llm(
    text: str,
    *,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    api_key: str = "",
    temperature: Optional[float] = None,
    timeout: Optional[int] = None,
    system_prompt: Optional[str] = None,
    use_few_shot: bool = True,
) -> str:
    """Single LLM call. Returns the raw model text.

    Defaults are resolved at CALL time (not import time) so configure() works.
    """
    provider = DEFAULT_PROVIDER if provider is None else provider
    model = DEFAULT_MODEL if model is None else model
    temperature = DEFAULT_TEMPERATURE if temperature is None else temperature
    timeout = DEFAULT_TIMEOUT if timeout is None else timeout
    system_prompt = system_prompt or load_system_prompt()
    messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
    if use_few_shot:
        messages.extend(load_few_shot())
    messages.append({"role": "user", "content": text})

    key = api_key or API_KEYS.get(provider, "")

    if provider == "openai":
        if not key:
            raise RuntimeError("Missing OpenAI API key (set RU_API_KEY or OPENAI_API_KEY).")
        resp = _post(
            ENDPOINTS["openai"],
            {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": 4096,
            },
            {"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            timeout,
        )
        return resp["choices"][0]["message"]["content"]

    if provider == "anthropic":
        if not key:
            raise RuntimeError("Missing Anthropic API key (set RU_API_KEY or ANTHROPIC_API_KEY).")
        resp = _post(
            ENDPOINTS["anthropic"],
            {
                "model": model or "claude-sonnet-4-5",
                "system": system_prompt,
                "messages": messages[1:],
                "max_tokens": 4096,
                "temperature": temperature,
            },
            {
                "Content-Type": "application/json",
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
            },
            timeout,
        )
        return "".join(part.get("text", "") for part in resp.get("content", []))

    if provider == "gemini":
        if not key:
            raise RuntimeError("Missing Gemini API key (set RU_API_KEY or GEMINI_API_KEY).")
        model_name = model or "gemini-2.0-flash"
        url = f"{ENDPOINTS['gemini']}/{model_name}:generateContent?key={key}"
        gemini_msgs = []
        for msg in messages[1:]:
            role = "model" if msg["role"] == "assistant" else "user"
            gemini_msgs.append({"role": role, "parts": [{"text": msg["content"]}]})
        resp = _post(
            url,
            {
                "system_instruction": {"parts": [{"text": system_prompt}]},
                "contents": gemini_msgs,
                "generationConfig": {
                    "temperature": temperature,
                    "maxOutputTokens": 4096,
                },
            },
            {"Content-Type": "application/json"},
            timeout,
        )
        parts = resp.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        return "".join(p.get("text", "") for p in parts)

    if provider == "mock":
        return mock_translate(text)

    raise RuntimeError(f"Unknown provider: {provider}")


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

# Urdu / Arabic script blocks that must NEVER appear in Roman Urdu output.
_SCRIPT_RANGES = [
    (0x0600, 0x06FF),   # Arabic
    (0x0750, 0x077F),   # Arabic Supplement
    (0xFB50, 0xFDFF),   # Arabic Presentation Forms-A
    (0xFE70, 0xFEFF),   # Arabic Presentation Forms-B
    (0x08A0, 0x08FF),   # Arabic Extended-A
    (0x0900, 0x097F),   # Devanagari (Hindi script)
]

URDU_SCRIPT_RE = re.compile(
    "[" + "".join(f"\\u{lo:04X}-\\u{hi:04X}" for lo, hi in _SCRIPT_RANGES) + "]"
)

BULLET_RE = re.compile(r"^\s*(?:[-*+•·‣⁃◦]|\d+[.)]|#{1,6})\s+")
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")


def has_urdu_script(text: str) -> bool:
    return bool(URDU_SCRIPT_RE.search(text))


def configured(provider: Optional[str] = None, api_key: str = "") -> bool:
    """
    Same semantics as news_agent/roman_urdu.py::configured() so this module can
    be dropped in behind an `_ru_on()` style check. An explicit api_key counts
    as configured even when no environment key exists.
    """
    p = (provider or DEFAULT_PROVIDER or "openai").lower()
    if p == "mock":
        return True
    return bool(api_key or API_KEYS.get(p))


def paste_check(en: str, ur: str, n: int = PASTE_N) -> bool:
    """
    True when `ur` still carries an unbroken run of >= n words copied verbatim
    from `en` (case-insensitive). Vocabulary-free, so it flags real English
    paste without punishing legitimate proper nouns (Hyderabad, PD Act).
    Mirrors news_agent/roman_urdu.py::paste_check exactly.
    """
    t = re.findall(r"[a-z0-9][a-z0-9'.\-]*", (en or "").lower())
    u = re.findall(r"[a-z0-9][a-z0-9'.\-]*", (ur or "").lower())
    if len(t) < n or len(u) < n:
        return False
    tg = {tuple(t[j:j + n]) for j in range(len(t) - n + 1)}
    return any(tuple(u[i:i + n]) in tg for i in range(len(u) - n + 1))


def trim_to(text: str, limit: int = MAXLEN) -> str:
    """Fit a line inside `limit` chars on a word boundary (never mid-word).

    Reserves one character for the ellipsis so the result is ALWAYS <= limit.
    """
    if len(text) <= limit:
        return text
    cut = text[: max(1, limit - 1)]     # leave room for the ellipsis
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(",;:.") + "…"


def strip_wrappers(text: str) -> str:
    """Remove common LLM noise so downstream nodes get clean text."""
    out = text.strip()
    # markdown fences
    out = re.sub(r"^```[a-zA-Z0-9_-]*\s*\n?", "", out)
    out = re.sub(r"\n?```\s*$", "", out)
    # "Translation:" / "Roman Urdu:" style prefixes
    out = re.sub(
        r"^(?:roman urdu|translation|translated text|urdu|output|jawab|answer)\s*[:\-–]\s*",
        "",
        out,
        flags=re.IGNORECASE,
    )
    # leading/trailing blank lines
    return out.strip("\n")


def _markers(lines: List[str]) -> List[str]:
    """Extract the bullet marker of each line ('' when the line has none)."""
    markers = []
    for line in lines:
        m = BULLET_RE.match(line)
        if m:
            marker = m.group(0).strip()
            markers.append(re.sub(r"\s+$", "", marker))
        else:
            markers.append("")
    return markers


def validate(
    source: str,
    translated: str,
    max_line_len: Optional[int] = None,
    paste_n: Optional[int] = None,
    check_untranslated: bool = True,
) -> Tuple[bool, List[str]]:
    """
    Return (is_ok, list_of_problems). Used to decide whether to retry.

    max_line_len: when set (card mode), every output line must fit inside this
                  many characters - the news card renderer has a hard cap.
    paste_n:      when set, reject lines that copy >= paste_n consecutive
                  English words verbatim (writer.paste_check equivalent).
    check_untranslated: set False for the deterministic "mock" provider - it is a
                  word-swapper for plumbing tests, not a translator, so the
                  "output looks mostly English" heuristic would always fire.
    """
    problems: List[str] = []
    src_lines = source.strip("\n").split("\n")
    out_lines = translated.strip("\n").split("\n")

    if has_urdu_script(translated):
        bad = sorted({c for c in translated if URDU_SCRIPT_RE.match(c)})
        problems.append(
            "Output contains Urdu/Arabic/Devanagari script characters: "
            + " ".join(bad[:12])
            + ". Roman script only."
        )

    if len(out_lines) != len(src_lines):
        problems.append(
            f"Line count mismatch: input has {len(src_lines)} lines, output has {len(out_lines)}. "
            "Return exactly one translated line per input line, keeping blank lines in place."
        )
    else:
        src_markers = _markers(src_lines)
        out_markers = _markers(out_lines)
        for i, (a, b) in enumerate(zip(src_markers, out_markers), start=1):
            if a != b:
                problems.append(
                    f"Line {i}: bullet marker changed from {a!r} to {b!r}. Keep markers identical."
                )
                break
        for i, (s, o) in enumerate(zip(src_lines, out_lines), start=1):
            if s.strip() == "" and o.strip() != "":
                problems.append(f"Line {i}: input is blank but output is not. Keep blank lines blank.")
                break

        # --- card constraints (only meaningful when lines are aligned) ---
        if max_line_len:
            over = [
                (i, len(o)) for i, o in enumerate(out_lines, start=1)
                if len(o) > max_line_len
            ]
            if over:
                detail = ", ".join(f"line {i}={n} chars" for i, n in over[:5])
                problems.append(
                    f"Lines longer than the card cap of {max_line_len} characters "
                    f"({detail}). Shorten the Urdu phrasing on those lines without "
                    "dropping any fact, name or number."
                )
        if paste_n:
            pasted = [
                i for i, (s, o) in enumerate(zip(src_lines, out_lines), start=1)
                if paste_check(s, o, paste_n)
            ]
            if pasted:
                problems.append(
                    f"Lines {pasted[:5]} copy {paste_n}+ consecutive English words "
                    "verbatim into the Roman Urdu. Re-express those phrases in Urdu; "
                    "keep only proper nouns/brands/technical terms in English."
                )

    src_numbers = NUMBER_RE.findall(source)
    out_numbers = NUMBER_RE.findall(translated)
    if src_numbers and not all(n in out_numbers for n in src_numbers):
        missing = [n for n in src_numbers if n not in out_numbers]
        problems.append(
            "Numbers changed or missing: "
            + ", ".join(missing[:10])
            + ". Keep every digit, percentage, score and date exactly as in the source."
        )

    urls = re.findall(r"https?://\S+|www\.\S+", source)
    for u in urls:
        if u not in translated:
            problems.append(f"URL missing from output: {u}. Keep URLs unchanged.")

    tags = re.findall(r"(?:#\w+|@[A-Za-z0-9_]+)", source)
    for t in tags:
        if t not in translated:
            problems.append(f"Hashtag/mention missing from output: {t}. Keep it unchanged.")

    emojis_src = {c for c in source if ord(c) > 0x1F000}
    emojis_out = {c for c in translated if ord(c) > 0x1F000}
    if emojis_src and not emojis_src.issubset(emojis_out):
        problems.append("Some emojis were dropped: " + " ".join(sorted(emojis_src - emojis_out)))

    # Untranslated-detection: if >70% of the words are identical to English, warn.
    def words(s: str) -> List[str]:
        return re.findall(r"[A-Za-z]{4,}", s.lower())

    src_words, out_words = words(source), words(translated)
    if check_untranslated and src_words and out_words:
        overlap = sum(1 for w in out_words if w in set(src_words)) / len(out_words)
        if overlap > 0.75:
            problems.append(
                f"Output looks mostly untranslated ({overlap:.0%} of words identical to English). "
                "Translate the sentences into natural Roman Urdu, keeping only names/brands/terms in English."
            )

    if not translated.strip():
        problems.append("Output is empty.")

    return (not problems), problems


# --------------------------------------------------------------------------
# Deterministic fallback (keeps automation alive, never emits Urdu script)
# --------------------------------------------------------------------------

FALLBACK_DICT = [
    (r"\bgovernment\b", "hukumat"), (r"\badministration\b", "intezamia"),
    (r"\bannouncement\b", "elaan"), (r"\bannounced\b", "elaan kar diya"),
    (r"\bdecision\b", "faisla"), (r"\bstatement\b", "bayan"),
    (r"\binvestigation\b", "tehqeeq"), (r"\bprotest\b", "ehtijaj"),
    (r"\bmeeting\b", "mulaqat"), (r"\bagreement\b", "muahida"),
    (r"\belection\b", "intikhabat"), (r"\beconomy\b", "maeshat"),
    (r"\bprices?\b", "qeematein"), (r"\bincrease[d]?\b", "izafa"),
    (r"\bdecrease[d]?\b", "kami"), (r"\bpossible\b", "mumkin"),
    (r"\baccording to\b", "ke mutabiq"), (r"\bpeople\b", "afraad"),
    (r"\bcitizens\b", "shehriyon"), (r"\bcountry\b", "mulk"),
    (r"\bworld\b", "duniya"), (r"\bminister\b", "wazir"),
    (r"\bpresident\b", "sadar"), (r"\bprime minister\b", "wazir-e-azam"),
    (r"\bnew\b", "naya"), (r"\btoday\b", "aaj"), (r"\btomorrow\b", "kal"),
    (r"\bweek\b", "hafta"), (r"\bmonth\b", "mahina"), (r"\byear\b", "saal"),
    (r"\bkilled\b", "jaan-ba-haq"), (r"\binjured\b", "zakhmi"),
    (r"\bearthquake\b", "zalzala"), (r"\bflood\b", "siylab"),
    (r"\bcricket\b", "cricket"), (r"\bmatch\b", "match"),
    (r"\bseries\b", "series"), (r"\bwickets?\b", "wickets"),
    (r"\bruns?\b", "runs"), (r"\bteam\b", "team"),
    (r"\breports?\b", "raptar"), (r"\bconfirmed\b", "tasdeeq"),
]


def fallback_translate(text: str) -> str:
    """
    Last-resort translation: regex word substitution, line structure preserved.
    Quality is low but it guarantees (a) Roman script, (b) correct line/bullet
    structure, (c) untouched numbers/names. Log a warning when this is used.
    """
    out_lines = []
    for line in text.split("\n"):
        m = BULLET_RE.match(line)
        marker, body = (m.group(0), line[m.end():]) if m else ("", line)
        new_body = body
        for pattern, repl in FALLBACK_DICT:
            new_body = re.sub(pattern, repl, new_body, flags=re.IGNORECASE)
        out_lines.append(marker + new_body)
    return "\n".join(out_lines)


def mock_translate(text: str) -> str:
    """
    Offline stand-in used by --mock and by the test suite. It mimics a *good*
    model: preserves lines/markers/numbers and swaps a few common words.
    Replace with nothing in production (just don't pass provider='mock').
    """
    # Ordered: longer phrases first. Function words are included so the output
    # stops looking like English (the validator checks for that).
    lexicon = [
        ("prime minister", "wazir-e-azam"),
        ("finance minister", "finance minister"),
        ("according to", "ke mutabiq"),
        ("opposition parties", "mukhalif jamaatein"),
        ("rescue teams", "rescue teams"),
        ("is now", "ab"),
        ("will be", "hogi"),
        ("could be", "ho sakta hai"),
        ("may be", "ho sakta hai"),
        ("at least", "kam az kam"),
        ("has been", "ho chuka hai"),
        ("have been", "ho chukay hain"),
        ("the government", "hukumat"),
        ("government", "hukumat"),
        ("announced", "elaan kar diya"),
        ("announces", "elaan karta hai"),
        ("announcement", "elaan"),
        ("confirmed", "tasdeeq kar di"),
        ("confirms", "tasdeeq karta hai"),
        ("expected", "tawqoa"),
        ("expects", "tawqoa rakhti hai"),
        ("per month", "mahana"),
        ("per litre", "prati litre"),
        ("this week", "is haftay"),
        ("today", "aaj"),
        ("tomorrow", "kal"),
        ("week", "haftay"),
        ("month", "mahinay"),
        ("year", "saal"),
        ("prices", "qeematein"),
        ("price", "qeemat"),
        ("increase", "izafa"),
        ("decrease", "kami"),
        ("people", "afraad"),
        ("citizens", "shehriyon"),
        ("minister", "wazir"),
        ("new", "naya"),
        ("killed", "jaan-ba-haq"),
        ("injured", "zakhmi"),
        ("earthquake", "zalzala"),
        ("flood", "siylab"),
        ("match", "match"),
        ("series", "series"),
        ("report", "raptar"),
        ("reports", "raptaron"),
        ("decision", "faisla"),
        ("statement", "bayan"),
        ("investigation", "tehqeeq"),
        ("protest", "ehtijaj"),
        ("meeting", "mulaqat"),
        ("agreement", "muahida"),
        ("election", "intikhabat"),
        ("economy", "maeshat"),
        ("country", "mulk"),
        ("world", "duniya"),
        ("president", "sadar"),
        ("salary", "tankhwa"),
        ("revenue", "revenue"),
        ("consumers", "aam shehri"),
        ("worried", "pareshan"),
        ("inflation", "mehangai"),
        ("relief", "relief"),
        ("package", "package"),
        ("tax", "tax"),
        ("fully", "mukammal tor par"),
        ("exempt", "exempt"),
        ("apply", "laagu"),
        ("from", "se"),
        ("to", "tak"),
        ("and", "aur"),
        ("but", "lekin"),
        ("after", "baad"),
        ("before", "pehle"),
        ("more", "zyada"),
        ("less", "kam"),
        ("up to", "tak"),
        ("lose", "nuqsan uthana"),
        ("call", "qarar deti hain"),
        ("are", "hain"),
        ("is", "hai"),
        ("was", "tha"),
        ("were", "thay"),
        ("will", "ga"),
        ("can", "sakta hai"),
        ("could", "sakta tha"),
        ("has", "ne"),
        ("have", "ne"),
        ("had", "ke paas tha"),
        ("the", ""),
        ("a", ""),
        ("an", ""),
        ("of", "ka"),
        ("in", "mein"),
        ("on", "par"),
        ("for", "ke liye"),
        ("with", "ke sath"),
        ("by", "ne"),
        ("at", "par"),
        ("become", "ho jana"),
        ("becomes", "ho jata hai"),
        ("hit", "se mutasir hua"),
        ("hits", "ne apni lapet mein liya"),
        ("reached", "pohnch gayeen"),
        ("remain", "barqarar hain"),
        ("badly", "shadeed"),
        ("damaged", "nuqsan pohncha"),
        ("promised", "wada kiya"),
        ("emergency", "hungami"),
        ("aid", "emdad"),
    ]
    out = []
    for line in text.split("\n"):
        m = BULLET_RE.match(line)
        marker, body = (m.group(0), line[m.end():]) if m else ("", line)
        for en, ur in lexicon:
            body = re.sub(rf"\b{re.escape(en)}\b", ur, body, flags=re.IGNORECASE)
        body = re.sub(r"\s{2,}", " ", body).strip()
        out.append(marker + body)
    return "\n".join(out)


# --------------------------------------------------------------------------
# Main API
# --------------------------------------------------------------------------


def translate_news(
    text: Optional[str] = None,
    *,
    title_en: str = "",
    bullets_en: Any = (),
    provider: Optional[str] = None,
    model: Optional[str] = None,
    api_key: str = "",
    temperature: Optional[float] = None,
    max_retries: Optional[int] = None,
    timeout: Optional[int] = None,
    system_prompt: Optional[str] = None,
    use_few_shot: bool = True,
    strict: bool = True,
    max_line_len: Optional[int] = None,
    paste_n: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Translate English news text into Roman Urdu.

    Two calling styles (the second matches news_agent/roman_urdu.py so this
    module can be swapped in without touching the caller):

        translate_news("Headline\\n- bullet one\\n- bullet two")
        translate_news(title_en="Headline", bullets_en=["bullet one", "bullet two"])

    Returns a dict (a superset of both modules' shapes):
      {
        "ok": bool,                 # passed validation
        "roman_urdu": str,          # <- use this on the card
        "lines_ur": [str, ...],     # per-line, aligned with lines_en
        "lines_en": [str, ...],
        "title_ur": str,            # card mode only
        "bullets_ur": [str, ...],   # card mode only
        "method": "llm"|"retry"|"fallback"|"none"|"noop",
        "attempts": int,
        "problems": [str, ...],     # last validation issues (empty when ok)
        "warning": str|None,
      }

    max_line_len / paste_n: card constraints. Pass max_line_len=MAXLEN (118) for
    card bullets so over-long lines are rejected and retried instead of being
    silently dropped later by writer.bullet_quality().
    """
    # Resolve defaults at CALL time so configure() / config.json take effect.
    provider = DEFAULT_PROVIDER if provider is None else provider
    model = DEFAULT_MODEL if model is None else model
    temperature = DEFAULT_TEMPERATURE if temperature is None else temperature
    max_retries = DEFAULT_MAX_RETRIES if max_retries is None else max_retries
    timeout = DEFAULT_TIMEOUT if timeout is None else timeout

    # ---- card mode: title_en=/bullets_en= -------------------------------
    card_mode = text is None
    if card_mode:
        bullets = [str(b).strip() for b in (bullets_en or []) if str(b).strip()]
        title = (title_en or "").strip()
        src = ([title] if title else []) + bullets
        if not src:
            return {
                "ok": False, "roman_urdu": "", "lines_ur": [], "lines_en": [],
                "title_ur": "", "bullets_ur": [], "method": "none", "attempts": 0,
                "problems": ["empty input"], "warning": None,
            }
        if not configured(provider, api_key):
            return {
                "ok": False, "roman_urdu": "", "lines_ur": [], "lines_en": src,
                "title_ur": "", "bullets_ur": [], "method": "none", "attempts": 0,
                "problems": [f"no API key configured for provider {provider}"],
                "warning": "translator not configured",
            }
        res = translate_news(
            "\n".join(src),
            provider=provider, model=model, api_key=api_key,
            temperature=temperature, max_retries=max_retries, timeout=timeout,
            system_prompt=system_prompt, use_few_shot=use_few_shot, strict=strict,
            max_line_len=MAXLEN if max_line_len is None else max_line_len,
            paste_n=PASTE_N if paste_n is None else paste_n,
        )
        off = 1 if title else 0
        lines = list(res["lines_ur"])
        if len(lines) != len(src):        # keep the caller's shape no matter what
            lines = (lines + src[len(lines):])[: len(src)]
            res["lines_ur"] = lines
        res["ok"] = bool(res["ok"]) and len(lines) == len(src)
        # Even when ok=False we return the structure-aligned fallback text so the
        # caller can decide (post degraded vs. skip this item). Callers that only
        # trust ok=True - e.g. news_agent/autod.py - are unaffected by this.
        res["title_ur"] = lines[0] if off else ""
        res["bullets_ur"] = lines[off:]
        return res

    text = str(text).replace("\r\n", "\n").replace("\r", "\n")
    result: Dict[str, Any] = {
        "ok": False,
        "roman_urdu": "",
        "lines_ur": [],
        "lines_en": text.strip("\n").split("\n"),
        "method": "fallback",
        "attempts": 0,
        "problems": [],
        "warning": None,
    }

    if not text.strip():
        result["ok"] = True
        result["roman_urdu"] = text
        result["lines_ur"] = result["lines_en"]
        result["method"] = "noop"
        return result

    base_prompt = system_prompt or load_system_prompt()
    attempt_text = text
    problems: List[str] = []

    for attempt in range(1, max_retries + 2):
        result["attempts"] = attempt
        try:
            raw = call_llm(
                attempt_text,
                provider=provider,
                model=model,
                api_key=api_key,
                temperature=temperature,
                timeout=timeout,
                system_prompt=base_prompt,
                use_few_shot=use_few_shot,
            )
        except Exception as exc:  # noqa: BLE001
            problems = [f"LLM call failed: {exc}"]
            time.sleep(min(2 ** attempt, 8))
            continue

        cleaned = strip_wrappers(raw)
        ok, problems = validate(text, cleaned, max_line_len, paste_n,
                                check_untranslated=provider != "mock")
        if ok:
            result.update(
                ok=True,
                roman_urdu=cleaned,
                lines_ur=cleaned.split("\n"),
                method="llm" if attempt == 1 else "retry",
                problems=[],
            )
            return result

        # Build a self-correcting retry prompt
        attempt_text = (
            text
            + "\n\n=== SYSTEM NOTE ===\n"
            + "Your previous output was rejected for these reasons:\n- "
            + "\n- ".join(problems)
            + "\nReturn the corrected Roman Urdu translation only, "
            + f"with exactly {len(result['lines_en'])} lines and identical bullet markers."
        )

    # All attempts failed -> deterministic fallback so the pipeline never dies
    fb = fallback_translate(text)
    fb = strip_wrappers(fb)
    if max_line_len:
        fb = "\n".join(trim_to(ln, max_line_len) for ln in fb.split("\n"))
    ok_fb, problems_fb = validate(text, fb, max_line_len, paste_n,
                                  check_untranslated=provider != "mock")
    result.update(
        ok=ok_fb,
        roman_urdu=fb,
        lines_ur=fb.split("\n"),
        method="fallback",
        problems=[] if ok_fb else problems_fb,
        warning=(
            "LLM translation failed validation after "
            f"{result['attempts']} attempts; dictionary fallback used. "
            "Check your API key/model/network."
        ),
    )
    if strict and not ok_fb and has_urdu_script(fb):
        # Absolute guarantee: never emit Urdu script.
        result["roman_urdu"] = URDU_SCRIPT_RE.sub("", result["roman_urdu"])
        result["lines_ur"] = result["roman_urdu"].split("\n")
    return result


def translate_lines(lines: Any, **kwargs) -> Dict[str, Any]:
    """
    Drop-in replacement for news_agent/roman_urdu.py::translate_lines().

    lines: plain English strings (news bullets). Returns
    {ok, lines, method, problems, warning}. ok=False => caller falls back;
    this function itself never raises.

    Differences from the old module (all deliberate, all throughput-positive):
      * enforces MAXLEN=118 per line and rejects 4-word English pastes BEFORE
        returning, so writer.bullet_quality() cannot kill the bullet afterwards
      * retries with a line-specific error report instead of giving up
      * on total failure returns a structure-preserving fallback (the old module
        returned lines=[], which made autod.py drop every affected story)
    """
    try:
        src = [str(l).strip() for l in (lines or []) if str(l).strip()]
        if not src:
            return {"ok": False, "lines": [], "method": "none",
                    "problems": ["empty input"], "warning": None}
        provider = kwargs.get("provider", DEFAULT_PROVIDER)
        if not configured(provider, kwargs.get("api_key", "")):
            return {"ok": False, "lines": [], "method": "none",
                    "problems": [f"no API key configured for provider {provider}"],
                    "warning": "translator not configured"}
        kw = {k: v for k, v in kwargs.items() if k != "max_line_len"}
        res = translate_news(
            "\n".join(src),
            max_line_len=kwargs.get("max_line_len", MAXLEN),
            paste_n=PASTE_N,
            **kw,
        )
        out_lines = res["lines_ur"]
        # Hard guarantee: count must match, every line must fit the card cap.
        if len(out_lines) != len(src):
            out_lines = (out_lines + src[len(out_lines):])[: len(src)]
        out_lines = [trim_to(l, MAXLEN) for l in out_lines]
        return {
            "ok": res["ok"] and len(out_lines) == len(src),
            "lines": out_lines,
            "method": res["method"],
            "problems": res["problems"],
            "warning": res["warning"],
        }
    except Exception as exc:  # noqa: BLE001  -- never raise into a news cycle
        return {"ok": False, "lines": [], "method": "none",
                "problems": [f"translate_lines crashed: {exc}"],
                "warning": str(exc)}


def translate(text: str, **kwargs) -> str:
    """
    The simple entry point: English in, Roman Urdu out.

        from translator import translate
        print(translate("Petrol price may increase by Rs 8 per litre"))

    Returns just the translated string. For diagnostics (validation problems,
    fallback warnings, per-line lists) use translate_news() / translate_lines().
    Keyword arguments are passed straight through (provider, model, api_key,
    temperature, max_line_len, paste_n, ...).
    """
    return translate_news(text, **kwargs).get("roman_urdu", "")


def translate_card(title_en: str = "", bullets_en: Any = (), **kwargs) -> Dict[str, Any]:
    """
    Card-shaped entry point for any "headline + bullets" UI (news cards, social
    posts, notifications, slides).

        from translator import translate_card
        c = translate_card("Earthquake hits Quetta", ["18 people killed", "40 injured"])
        c["title_ur"], c["bullets_ur"], c["roman_urdu"]

    Enforces MAXLEN (118 by default) and the English-paste check, because those
    are the constraints of a rendered card. Pass max_line_len=None to disable.
    """
    return translate_news(title_en=title_en, bullets_en=bullets_en, **kwargs)


# --------------------------------------------------------------------------
# JSON mode (for n8n / Make / Zapier)
# --------------------------------------------------------------------------


def translate_payload(payload: Dict[str, Any], **kwargs) -> Dict[str, Any]:
    """
    Accepts a flexible news payload and returns the same payload enriched with
    Roman Urdu fields. Recognised input keys (any of):
        title / headline / title_en / headline_en
        bullets / bullets_en / points / bullets_english  (list[str] or str)
        summary / description / caption / body / text
    Output adds:
        title_ur, bullets_ur, summary_ur, roman_urdu (single card-ready string)
    """
    out = dict(payload)
    kw = dict(kwargs)
    kw.setdefault("max_line_len", MAXLEN)   # card renderer has a hard per-line cap
    kw.setdefault("paste_n", PASTE_N)       # reject verbatim English runs

    def pick(*keys: str) -> Optional[str]:
        for k in keys:
            if k in payload and payload[k]:
                return payload[k]
        return None

    def as_lines(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        return [ln.strip() for ln in str(value).split("\n") if ln.strip()]

    title = pick("title", "headline", "title_en", "headline_en", "news_title")
    bullets = as_lines(pick("bullets", "bullets_en", "points", "bullets_english", "items"))
    summary = pick("summary", "description", "caption", "body", "text", "content")

    if title:
        out["title_ur"] = translate_news(title, **kw)["roman_urdu"]
    if bullets:
        # One call for the whole list keeps context (better pronouns/consistency)
        joined = "\n".join(bullets)
        res = translate_news(joined, **kw)
        lines = res["lines_ur"]
        if len(lines) != len(bullets):  # defensive: align by index
            lines = (lines + bullets[len(lines):])[: len(bullets)]
        out["bullets_ur"] = lines
        out["_bullets_translation"] = {
            "ok": res["ok"], "method": res["method"],
            "problems": res["problems"], "warning": res["warning"],
        }
    if summary:
        out["summary_ur"] = translate_news(summary, **kw)["roman_urdu"]

    # Card-ready combined block
    parts = []
    if out.get("title_ur"):
        parts.append(out["title_ur"])
    if out.get("bullets_ur"):
        parts.extend("- " + b if not b.startswith(("- ", "* ", "•")) else b for b in out["bullets_ur"])
    if out.get("summary_ur"):
        parts.append(out["summary_ur"])
    out["roman_urdu"] = "\n".join(parts)
    return out


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="English -> Roman Urdu translator for news automation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--text", help="Text to translate (else read stdin)")
    p.add_argument("--file", help="Read input text from a file")
    p.add_argument("--json-in", help='JSON payload string, e.g. {"bullets_en": [...]}')
    p.add_argument("--json", action="store_true", help="Print full JSON result")
    p.add_argument("--provider", default=DEFAULT_PROVIDER,
                   help="openai | gemini | anthropic | mock (default: %(default)s)")
    p.add_argument("--model", default=DEFAULT_MODEL, help="Model name (default: %(default)s)")
    p.add_argument("--api-key", default="", help="Override API key")
    p.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    p.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES)
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    p.add_argument("--no-few-shot", action="store_true", help="Disable few-shot examples")
    p.add_argument("--mock", action="store_true", help="Offline deterministic mode (testing)")
    p.add_argument("--serve", action="store_true", help="Run a tiny HTTP server for n8n/Make")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    p.add_argument("--prompt", help="Path to a custom system prompt file")
    p.add_argument("--config", action="store_true", help="Print effective configuration and exit")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.config:
        print(json.dumps(effective_config(), ensure_ascii=False, indent=2))
        return 0

    if args.serve:
        try:
            from server import serve          # portable package layout
        except ImportError:                   # pragma: no cover
            from http_server import serve     # news_agent repo layout
        return serve(host=args.host, port=args.port, defaults=vars(args))

    provider = "mock" if args.mock else args.provider
    kwargs = dict(
        provider=provider,
        model=args.model,
        api_key=args.api_key,
        temperature=args.temperature,
        max_retries=args.max_retries,
        timeout=args.timeout,
        use_few_shot=not args.no_few_shot,
    )
    if args.prompt:
        kwargs["system_prompt"] = load_system_prompt(args.prompt)

    if args.json_in:
        payload = json.loads(args.json_in)
        out = translate_payload(payload, **kwargs)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if args.text is not None:
        text = args.text
    elif args.file:
        with open(args.file, "r", encoding="utf-8") as fh:
            text = fh.read()
    else:
        text = sys.stdin.read()

    if not text.strip():
        sys.stderr.write("error: empty input\n")
        return 2

    res = translate_news(text, **kwargs)

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(res["roman_urdu"])

    if res.get("warning"):
        sys.stderr.write(f"warning: {res['warning']}\n")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
