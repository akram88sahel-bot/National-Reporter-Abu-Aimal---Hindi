"""
Renders the black-and-gold breaking-news card (1080x1080 PNG).

Layout: logo at top, gold headline, Hindi bullets, Roman Urdu bullets,
source line at the bottom. All text is drawn on the image so the card is
the same design for every post.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = ROOT / "assets" / "fonts"
LOGO = ROOT / "assets" / "logo.jpg"

W = H = 1080
BLACK = (14, 12, 10)
PANEL = (28, 24, 19)
GOLD = (212, 175, 90)
GOLD_DARK = (150, 118, 52)
WHITE = (245, 240, 230)
GREY = (190, 182, 168)


def _font(name, size, weight="Bold"):
    path = FONT_DIR / name
    f = ImageFont.truetype(str(path), size)
    try:
        f.set_variation_by_name(weight)
    except Exception:
        pass
    return f


def _wrap(draw, text, font, max_width):
    words, lines, line = text.split(), [], ""
    for w in words:
        test = f"{line} {w}".strip()
        if draw.textlength(test, font=font) <= max_width:
            line = test
        else:
            if line:
                lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines


def _gold_rule(draw, y, x0=80, x1=W - 80):
    draw.rectangle([x0, y, x1, y + 4], fill=GOLD)


def _bullets(draw, y, items, font, max_width, indent=44, line_gap=10, bullet_color=GOLD, text_color=WHITE):
    for item in items:
        lines = _wrap(draw, item, font, max_width - indent)
        draw.ellipse([88, y + 16, 104, y + 32], fill=bullet_color)
        for i, line in enumerate(lines):
            draw.text((80 + indent, y + i * (font.size + line_gap)), line, font=font, fill=text_color)
        y += len(lines) * (font.size + line_gap) + 26
    return y


def _layout_height(draw, hindi, roman, fonts, max_w, gaps):
    """Return total height the bullets need, so we can shrink text until it fits."""
    fh, fr, lh, lr, bh_gap, br_gap = fonts["hi"], fonts["ru"], gaps[0], gaps[1], gaps[2], gaps[3]
    h = 0
    for item in hindi:
        h += len(_wrap(draw, item, fh, max_w - 44)) * (fh.size + lh) + 26
    h += 44 + 50  # Hindi label block
    for item in roman:
        h += len(_wrap(draw, item, fr, max_w - 44)) * (fr.size + lr) + 26
    return h


def render_card(headline, hindi, roman_urdu, source, out_path):
    img = Image.new("RGB", (W, H), BLACK)
    draw = ImageDraw.Draw(img)
    draw.rectangle([30, 30, W - 30, H - 30], outline=GOLD_DARK, width=3)

    # Logo (smaller, centered, top)
    logo = Image.open(LOGO).convert("RGB")
    logo_w = 220
    logo = logo.resize((logo_w, int(logo.height * logo_w / logo.width)))
    img.paste(logo, ((W - logo_w) // 2, 56))
    top = 56 + logo.height + 24

    font_head = _font("NotoSans.ttf", 54, "Bold")
    head_w = draw.textlength(headline, font=font_head)
    draw.text(((W - head_w) // 2, top), headline, font=font_head, fill=GOLD)
    rule1 = top + 80
    _gold_rule(draw, rule1)
    body_top = rule1 + 24
    body_bottom = H - 170  # leave room for source line and bottom rule
    max_w = W - 160

    # Shrink text until everything fits between the rules
    size = 38
    while size > 20:
        fonts = {
            "hi": _font("NotoSansDevanagari.ttf", size, "Medium"),
            "ru": _font("NotoSans.ttf", size - 2, "Medium"),
        }
        gaps = (12, 8, 0, 0)
        need = _layout_height(draw, hindi, roman_urdu, fonts, max_w, gaps)
        if body_top + need <= body_bottom:
            break
        size -= 1

    font_label = _font("NotoSans.ttf", 26, "Bold")
    y = body_top
    draw.text((80, y), "HINDI", font=font_label, fill=GOLD_DARK)
    y += 40
    y = _bullets(draw, y, hindi, fonts["hi"], max_w, line_gap=12)
    draw.text((80, y), "ROMAN URDU", font=font_label, fill=GOLD_DARK)
    y += 40
    _bullets(draw, y, roman_urdu, fonts["ru"], max_w, line_gap=8)

    font_src = _font("NotoSans.ttf", 26, "Regular")
    draw.text((80, H - 110), f"Source: {source}", font=font_src, fill=GREY)
    _gold_rule(draw, H - 140)

    img.save(out_path, "PNG", optimize=True)
    return out_path
