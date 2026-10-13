#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
card.py -- OPTIONAL Pillow-based news card renderer.

Draws English AND Roman Urdu on ONE image, using the Azad Daily branding by
default (1200x1500, gold on black, logo strip, footer). Override anything via
keyword arguments or a card.json next to this file.

Pillow is an OPTIONAL dependency: the core translator never imports it, so
`import roman_urdu_translator` keeps working with zero third-party packages.
This module raises a clear message only if you actually call render_card()
without Pillow installed.

    from roman_urdu_translator import translate_card
    from roman_urdu_translator.card import render_card

    c = translate_card(title_en, bullets_en)          # Roman Urdu, <=118 chars/line
    render_card(title_en=title_en, bullets_en=bullets_en,
                title_ur=c["title_ur"], bullets_ur=c["bullets_ur"],
                logo_path="logo.png", out_path="card.png")

One-call convenience (translates then renders):

    from roman_urdu_translator.card import render_news_card
    render_news_card(title_en, bullets_en, logo_path="logo.png", out_path="card.png")

CLI:
    python3 card.py --title "..." --bullet "..." --bullet "..." --out card.png
    python3 card.py --sample                 # demo card, no API key needed
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:                                    # package layout
    from .translator import MAXLEN, paste_check, translate_card, trim_to
except ImportError:                     # flat / single-folder layout
    from translator import MAXLEN, paste_check, translate_card, trim_to  # type: ignore

# --------------------------------------------------------------------------
# Branding defaults -- mirror news_agent/config.json ("branding" block)
# --------------------------------------------------------------------------

BRANDING: Dict[str, Any] = {
    "page_name": "Azad Daily",
    "accent_gold": "#D4A017",
    "accent_gold_bright": "#F2C94C",
    "accent_gold_dim": "#8A6D1F",
    "background_black": "#0A0A0A",
    "panel_black": "#141414",
    "text_en_title": "#F2C94C",
    "text_en_body": "#FFFFFF",
    "text_ur_title": "#9EC5FF",
    "text_ur_body": "#DDDDDD",
    "text_muted": "#8A8FA8",
    "image_width": 1200,
    "image_height": 1500,
    "margin": 60,
    "default_footer": "Verified latest news: Hyderabad \u2022 Telangana \u2022 India \u2022 World",
    "font_regular": "DejaVuSans.ttf",
    "font_bold": "DejaVuSans-Bold.ttf",
    "title_size_en": 54,
    "body_size_en": 38,
    "title_size_ur": 44,
    "body_size_ur": 34,
    "label_size": 24,
    "footer_size": 24,
    "min_scale_steps": 8,       # shrink at most this many 2px steps before growing
}

CARD_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "card.json")


def _load_card_config() -> Dict[str, Any]:
    """Optional card.json next to this file overrides BRANDING."""
    try:
        with open(CARD_CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


BRANDING.update(_load_card_config())

FONT_DIRS = [
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/truetype",
    "/usr/share/fonts",
    "/Library/Fonts",
    "/System/Library/Fonts",
    "C:/Windows/Fonts",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts"),
]


def _require_pillow():
    try:
        from PIL import Image, ImageDraw, ImageFont  # noqa: F401
        return Image, ImageDraw, ImageFont
    except ImportError as exc:          # pragma: no cover
        raise RuntimeError(
            "card.py needs Pillow:  pip install Pillow  "
            "(the translator itself has no dependencies - only the card renderer does)"
        ) from exc


def resolve_font(name: str) -> Optional[str]:
    """Absolute path for a font file name, or None when not found."""
    if os.path.isabs(name) and os.path.isfile(name):
        return name
    for d in FONT_DIRS:
        cand = os.path.join(d, name)
        if os.path.isfile(cand):
            return cand
    # last resort: search FONT_DIRS recursively (shallow)
    for d in FONT_DIRS:
        if not os.path.isdir(d):
            continue
        for root, _dirs, files in os.walk(d):
            if name in files:
                return os.path.join(root, name)
            if root.count(os.sep) - d.count(os.sep) > 2:
                break
    return None


def _fonts(ImageFont, step: int = 0):
    """Load the font set, optionally shrunk by `step` * 2 pixels."""
    shrink = step * 2
    out = {}
    sizes = {
        "en_title": ("title_size_en", True),
        "en_body": ("body_size_en", False),
        "ur_title": ("title_size_ur", True),
        "ur_body": ("body_size_ur", False),
        "label": ("label_size", False),
        "footer": ("footer_size", False),
        "page": ("label_size", True),
    }
    for key, (size_key, bold) in sizes.items():
        size = max(14, int(BRANDING[size_key]) - shrink)
        if key == "page":
            size = max(18, size + 8)
        fname = BRANDING["font_bold"] if bold else BRANDING["font_regular"]
        path = resolve_font(fname)
        out[key] = ImageFont.truetype(path, size) if path else ImageFont.load_default()
    return out


def _wrap(draw, text: str, font, limit_px: int) -> List[str]:
    """Word-wrap to a pixel width. Never splits a word unless the word alone overflows."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= limit_px or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    # a single word wider than the limit: hard-split it
    final: List[str] = []
    for ln in lines:
        while draw.textlength(ln, font=font) > limit_px and len(ln) > 1:
            lo, hi = 1, len(ln)
            while lo < hi:
                mid = (lo + hi) // 2
                if draw.textlength(ln[:mid], font=font) <= limit_px:
                    lo = mid + 1
                else:
                    hi = mid
            cut = max(1, lo - 1)
            final.append(ln[:cut])
            ln = ln[cut:]
        final.append(ln)
    return final


def _line_h(font, factor: float = 1.28) -> int:
    try:
        return int(font.size * factor)
    except AttributeError:              # load_default() has no .size on old Pillow
        return 34


def _measure(draw, fonts, title_en, bullets_en, title_ur, bullets_ur,
             width, margin) -> Dict[str, Any]:
    """Compute the wrapped layout and the total content height."""
    content_w = width - 2 * margin - 34          # 34px for the bullet glyph + gap
    blocks = {}
    total = 0
    blocks["en_title"] = _wrap(draw, title_en, fonts["en_title"], width - 2 * margin)
    total += len(blocks["en_title"]) * _line_h(fonts["en_title"])
    blocks["en_bullets"] = [_wrap(draw, b, fonts["en_body"], content_w) for b in bullets_en]
    total += sum(len(w) * _line_h(fonts["en_body"]) + 14 for w in blocks["en_bullets"])
    blocks["ur_title"] = _wrap(draw, title_ur, fonts["ur_title"], width - 2 * margin) if title_ur else []
    total += len(blocks["ur_title"]) * _line_h(fonts["ur_title"])
    blocks["ur_bullets"] = [_wrap(draw, b, fonts["ur_body"], content_w) for b in bullets_ur] if bullets_ur else []
    total += sum(len(w) * _line_h(fonts["ur_body"]) + 12 for w in blocks["ur_bullets"])
    # chrome: header strip + 2 labels + divider + footer strip + breathing room
    chrome = 150 + 44 + 40 + 46 + 96 + 60
    blocks["total"] = total + chrome
    return blocks


def render_card(
    title_en: str,
    bullets_en: Sequence[str],
    title_ur: str = "",
    bullets_ur: Sequence[str] = (),
    *,
    out_path: str = "card.png",
    logo_path: Optional[str] = None,
    page_name: Optional[str] = None,
    date_text: Optional[str] = None,
    footer: Optional[str] = None,
    hashtags: Optional[Sequence[str]] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    auto_grow: bool = True,
    en_label: str = "LATEST NEWS",
    ur_label: str = "ROMAN URDU",
    max_line_len: Optional[int] = MAXLEN,
) -> str:
    """
    Render the bilingual news card and save it. Returns the output path.

    Text is defensively trimmed to `max_line_len` (118 by default) so a stray
    long line can never break the layout. Layout fits the canvas by shrinking
    fonts up to `min_scale_steps` * 2px; if it still does not fit and
    auto_grow=True, the canvas height grows instead of clipping text.
    """
    Image, ImageDraw, ImageFont = _require_pillow()

    width = int(width or BRANDING["image_width"])
    height = int(height or BRANDING["image_height"])
    margin = int(BRANDING["margin"])
    page_name = page_name if page_name is not None else BRANDING["page_name"]
    footer = footer if footer is not None else BRANDING["default_footer"]
    date_text = date_text or datetime.datetime.now().strftime("%d %b %Y")

    if max_line_len:
        title_en = trim_to(title_en, max_line_len * 2)          # titles may span 2 lines
        title_ur = trim_to(title_ur, max_line_len * 2) if title_ur else ""
        bullets_en = [trim_to(b, max_line_len) for b in bullets_en if str(b).strip()]
        bullets_ur = [trim_to(b, max_line_len) for b in bullets_ur if str(b).strip()]
    else:
        bullets_en = [b for b in bullets_en if str(b).strip()]
        bullets_ur = [b for b in bullets_ur if str(b).strip()]

    probe = Image.new("RGB", (width, 60))
    pdraw = ImageDraw.Draw(probe)

    step, layout, fonts = 0, None, None
    for step in range(int(BRANDING["min_scale_steps"]) + 1):
        fonts = _fonts(ImageFont, step)
        layout = _measure(pdraw, fonts, title_en, bullets_en, title_ur, bullets_ur, width, margin)
        if layout["total"] <= height:
            break
    if layout["total"] > height and auto_grow:
        height = layout["total"] + 20

    img = Image.new("RGB", (width, height), BRANDING["background_black"])
    d = ImageDraw.Draw(img)

    # ---------------- header strip ----------------
    header_h = 104
    d.rectangle([0, 0, width, header_h], fill=BRANDING["panel_black"])
    x = margin
    if logo_path and os.path.isfile(logo_path):
        try:
            logo = Image.open(logo_path).convert("RGBA")
            lh = 68
            lw = max(1, int(logo.width * lh / max(1, logo.height)))
            logo = logo.resize((lw, lh))
            img.paste(logo, (x, (header_h - lh) // 2), logo)
            x += lw + 22
        except Exception:                                        # noqa: BLE001
            pass
    d.text((x, 34), page_name, font=fonts["page"], fill=BRANDING["accent_gold_bright"])
    d.text((width - margin, 38), date_text, font=fonts["label"],
           fill=BRANDING["text_muted"], anchor="ra")
    d.rectangle([0, header_h, width, header_h + 5], fill=BRANDING["accent_gold"])

    # ---------------- English block ----------------
    y = header_h + 44
    d.text((margin, y), en_label, font=fonts["label"], fill=BRANDING["accent_gold_dim"])
    y += _line_h(fonts["label"], 1.5)

    for ln in layout["en_title"]:
        d.text((margin, y), ln, font=fonts["en_title"], fill=BRANDING["text_en_title"])
        y += _line_h(fonts["en_title"])
    y += 16

    for wrapped in layout["en_bullets"]:
        d.ellipse([margin + 2, y + 16, margin + 16, y + 30], fill=BRANDING["accent_gold"])
        for i, ln in enumerate(wrapped):
            d.text((margin + 34, y), ln, font=fonts["en_body"], fill=BRANDING["text_en_body"])
            y += _line_h(fonts["en_body"])
        y += 14

    # ---------------- divider ----------------
    y += 22
    dash_w, gap = 18, 12
    for xx in range(margin, width - margin, dash_w + gap):
        d.rectangle([xx, y, min(xx + dash_w, width - margin), y + 3],
                    fill=BRANDING["accent_gold_dim"])
    y += 40

    # ---------------- Roman Urdu block ----------------
    if title_ur or bullets_ur:
        d.text((margin, y), ur_label, font=fonts["label"], fill=BRANDING["accent_gold_dim"])
        y += _line_h(fonts["label"], 1.5)
        for ln in layout["ur_title"]:
            d.text((margin, y), ln, font=fonts["ur_title"], fill=BRANDING["text_ur_title"])
            y += _line_h(fonts["ur_title"])
        y += 12
        for wrapped in layout["ur_bullets"]:
            d.ellipse([margin + 2, y + 14, margin + 14, y + 26], fill=BRANDING["text_ur_title"])
            for ln in wrapped:
                d.text((margin + 34, y), ln, font=fonts["ur_body"], fill=BRANDING["text_ur_body"])
                y += _line_h(fonts["ur_body"])
            y += 12

    # ---------------- footer ----------------
    footer_h = 96
    fy = height - footer_h
    d.rectangle([0, fy, width, height], fill=BRANDING["panel_black"])
    d.rectangle([0, fy, width, fy + 4], fill=BRANDING["accent_gold"])
    tag_line = "  ".join(hashtags) if hashtags else footer
    d.text((margin, fy + 22), tag_line, font=fonts["footer"], fill=BRANDING["accent_gold"])
    if hashtags:
        d.text((margin, fy + 54), footer, font=fonts["footer"], fill=BRANDING["text_muted"])

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    img.save(out_path, quality=92)
    return out_path


def render_news_card(
    title_en: str,
    bullets_en: Sequence[str],
    *,
    out_path: str = "card.png",
    translate: bool = True,
    title_ur: str = "",
    bullets_ur: Sequence[str] = (),
    hashtags: Optional[Sequence[str]] = None,
    **render_kwargs,
) -> Dict[str, Any]:
    """
    Translate (unless you pass your own Urdu) and render in one call.

    Returns {"path": ..., "card": <translate_card result>, "rendered": bool}.
    Never raises on translation failure - it renders the English card and
    reports the problem, so a scheduled job still produces output.
    """
    card = None
    if translate:
        card = translate_card(title_en, list(bullets_en))
        title_ur = card.get("title_ur") or title_ur
        bullets_ur = card.get("bullets_ur") or list(bullets_ur)
        if not card.get("ok") and not title_ur:
            # nothing usable: render English-only rather than failing the job
            title_ur, bullets_ur = "", []
    path = render_card(title_en, list(bullets_en), title_ur, list(bullets_ur),
                       out_path=out_path, hashtags=hashtags, **render_kwargs)
    return {
        "path": path,
        "rendered": True,
        "translation_ok": bool(card and card.get("ok")),
        "problems": (card or {}).get("problems", []),
        "warning": (card or {}).get("warning"),
        "title_ur": title_ur,
        "bullets_ur": list(bullets_ur),
    }


# --------------------------------------------------------------------------
# Demo (works with NO API key: correct Roman Urdu is hard-coded)
# --------------------------------------------------------------------------

SAMPLE_EN = {
    "title": "IMD issues orange alert as heavy monsoon rains lash Hyderabad",
    "bullets": [
        "At least 18 people were killed and 40 injured in the district",
        "Rescue teams reached the affected area after six hours",
        "The UN has promised emergency aid for displaced families",
        "Roads and communication networks remain badly damaged",
    ],
}
SAMPLE_UR = {
    "title": "Musladhsaar barishon ke baad IMD ne Hyderabad mein orange alert jari kar diya",
    "bullets": [
        "Zile mein kam az kam 18 afraad jaan-ba-haq aur 40 zakhmi ho gaye",
        "Rescue teams che ghantay baad mutasira ilaqay mein pohnch gayeen",
        "UN ne baye-ghar khandaanon ke liye hungami emdad ka wada kiya hai",
        "Sarkein aur communication networks ko shadeed nuqsan pohncha",
    ],
}


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Render a bilingual (EN + Roman Urdu) news card.")
    p.add_argument("--title", help="English title")
    p.add_argument("--bullet", action="append", default=[], help="English bullet (repeatable)")
    p.add_argument("--title-ur", default="", help="Roman Urdu title (else translate)")
    p.add_argument("--bullet-ur", action="append", default=[], help="Roman Urdu bullet (repeatable)")
    p.add_argument("--logo", help="Logo image path")
    p.add_argument("--page-name", help="Page name in the header")
    p.add_argument("--hashtags", help="Space or comma separated hashtags")
    p.add_argument("--out", default="card.png")
    p.add_argument("--no-translate", action="store_true", help="Use only the Urdu you passed")
    p.add_argument("--sample", action="store_true", help="Render the built-in demo card (no API key)")
    a = p.parse_args(argv)

    tags = [t for t in (a.hashtags or "").replace(",", " ").split() if t] or None

    if a.sample:
        path = render_card(SAMPLE_EN["title"], SAMPLE_EN["bullets"],
                           SAMPLE_UR["title"], SAMPLE_UR["bullets"],
                           out_path=a.out, page_name=a.page_name,
                           logo_path=a.logo, hashtags=tags)
        print(path)
        return 0

    if not a.title:
        p.error("--title is required (or use --sample)")

    res = render_news_card(a.title, a.bullet, out_path=a.out,
                           translate=not (a.no_translate or a.bullet_ur),
                           title_ur=a.title_ur, bullets_ur=a.bullet_ur,
                           logo_path=a.logo, page_name=a.page_name, hashtags=tags)
    print(res["path"])
    if not res["translation_ok"]:
        sys.stderr.write(f"warning: translation degraded: {res['problems'][:2]}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
