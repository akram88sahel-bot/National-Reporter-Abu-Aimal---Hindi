"""
Renders the black-and-gold breaking-news card (1080x1350 PNG).

Design (reference card, Oct 2026): NR logo centred at the top, date at the
top right, trapezoid "LATEST NEWS" banner, one wide photo under it, then a
Hindi section and a Roman Urdu section, each a numbered list, divided by a
gold line with a diamond, and a centred "By Abu Aimal & Aimal Akram"
credit with a small NR logo at the bottom. All text is drawn on the image.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = ROOT / "assets" / "fonts"
LOGO = ROOT / "assets" / "logo.jpg"

W, H = 1080, 1350
BLACK = (14, 12, 10)
GOLD = (212, 175, 90)
GOLD_LIGHT = (228, 198, 128)
GOLD_DARK = (150, 118, 52)
WHITE = (245, 240, 230)
GREY = (190, 182, 168)
MARGIN = 70

# The NR letters inside the square logo file (the "NATIONAL REPORTER" line
# below them is left out, as on the reference card).
_LOGO_MARK_BOX = (205, 370, 875, 690)


def _font(name, size, weight="Bold"):
    f = ImageFont.truetype(str(FONT_DIR / name), size)
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


def _logo(width):
    logo = Image.open(LOGO).convert("RGB")
    return logo.resize((width, int(logo.height * width / logo.width)))


def _logo_mark(width):
    """The NR letters only, as an RGBA image (logo background made transparent)."""
    crop = Image.open(LOGO).convert("RGB").crop(_LOGO_MARK_BOX)
    crop = crop.resize((width, int(crop.height * width / crop.width)))
    # The logo sits on a solid near-black background; fade pixels by how much
    # brighter they are than that background so no grey patch is visible.
    table = [0 if i <= 42 else min(255, int((i - 42) * 255 / 55)) for i in range(256)]
    alpha = crop.convert("L").point(table)
    mark = crop.convert("RGBA")
    mark.putalpha(alpha)
    return mark


def _background():
    """Black card with a very subtle vertical gradient, as on the reference."""
    top, bottom = (19, 16, 12), (10, 9, 7)
    grad = Image.new("RGB", (1, H))
    for y in range(H):
        t = y / max(1, H - 1)
        grad.putpixel((0, y), tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return grad.resize((W, H))


def _frame(draw):
    draw.rectangle([26, 26, W - 26, H - 26], outline=GOLD_DARK, width=2)
    draw.rounded_rectangle([46, 46, W - 46, H - 46], radius=20, outline=(170, 140, 75), width=2)
    # Thicker gold corner caps on the outer frame
    cap, o = 125, 26
    for x0, y0, dx, dy in [(o, o, 1, 1), (W - o, o, -1, 1), (o, H - o, 1, -1), (W - o, H - o, -1, -1)]:
        draw.line([(x0, y0), (x0 + dx * cap, y0)], fill=GOLD, width=5)
        draw.line([(x0, y0), (x0, y0 + dy * cap)], fill=GOLD, width=5)


def _tracked_width(draw, text, font, tracking):
    return sum(draw.textlength(ch, font=font) for ch in text) + tracking * max(0, len(text) - 1)


def _draw_tracked(draw, x, y, text, font, fill, tracking):
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking
    return x


def _gradient_text(img, cx, y, text, font, tracking=3):
    """Gold-gradient text (light on top, deeper gold below), centred at cx."""
    draw = ImageDraw.Draw(img)
    w = int(_tracked_width(draw, text, font, tracking)) + 8
    h = font.size + 26
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    x = 4
    for ch in text:
        md.text((x, 4), ch, font=font, fill=255)
        x += md.textlength(ch, font=font) + tracking
    grad = Image.new("RGB", (1, h))
    top, bottom = (246, 219, 151), (168, 126, 54)
    for yy in range(h):
        t = yy / max(1, h - 1)
        grad.putpixel((0, yy), tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    img.paste(grad.resize((w, h)), (int(cx - w / 2), int(y - 4)), mask)


def _calendar_icon(draw, x, y, size=34):
    draw.line([(x + 9, y), (x + 9, y + 11)], fill=GOLD, width=3)
    draw.line([(x + size - 9, y), (x + size - 9, y + 11)], fill=GOLD, width=3)
    draw.rounded_rectangle([x, y + 5, x + size, y + 5 + size], radius=6, outline=GOLD, width=3)
    draw.line([(x + 2, y + 16), (x + size - 2, y + 16)], fill=GOLD, width=2)


def fit_photo(img, w, h):
    """Crop a photo to exactly w x h, keeping the upper part (where faces usually are)."""
    scale = max(w / img.width, h / img.height)
    rw, rh = int(img.width * scale) + 1, int(img.height * scale) + 1
    resized = img.resize((rw, rh))
    left = (rw - w) // 2
    top = int((rh - h) * 0.25)
    return resized.crop((left, top, left + w, top + h))


def _rounded_photo(photo, card, x, y, w, h, radius=22):
    """Paste a cropped photo with rounded corners and a gold edge."""
    crop = fit_photo(photo, w, h)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
    card.paste(crop, (x, y), mask)
    ImageDraw.Draw(card).rounded_rectangle([x, y, x + w, y + h], radius=radius, outline=GOLD, width=3)


NUM_W = 52          # space reserved for "1." at the start of each item
ITEM_GAP = 16       # space after each numbered item
LABEL_H = 48        # space a section label (हिंदी / ROMAN URDU) takes
DIVIDER_H = 44      # space the divider between the two sections takes


def _numbered_height(draw, items, font, max_w):
    h = 0
    for item in items:
        h += len(_wrap(draw, item, font, max_w)) * (font.size + 9) + ITEM_GAP
    return h


def _draw_numbered(draw, y, items, font):
    max_w = W - MARGIN - (MARGIN + NUM_W)
    f_num = _font("NotoSans.ttf", font.size, "Bold")
    for i, item in enumerate(items):
        draw.text((MARGIN + 2, y), f"{i + 1}.", font=f_num, fill=GOLD_LIGHT)
        for line in _wrap(draw, item, font, max_w):
            draw.text((MARGIN + NUM_W, y), line, font=font, fill=WHITE)
            y += font.size + 9
        y += ITEM_GAP
    return y


def _section_label(draw, y, text, font, tracking=0, y_off=0):
    draw.rounded_rectangle([MARGIN, y + 4, MARGIN + 10, y + 36], radius=5, fill=GOLD)
    if tracking:
        _draw_tracked(draw, MARGIN + 26, y + y_off, text, font, GOLD, tracking)
    else:
        draw.text((MARGIN + 26, y + y_off), text, font=font, fill=GOLD)


def _diamond_divider(draw, y):
    cx = W // 2
    draw.line([(MARGIN + 40, y), (cx - 18, y)], fill=GOLD_DARK, width=2)
    draw.line([(cx + 18, y), (W - MARGIN - 40, y)], fill=GOLD_DARK, width=2)
    draw.polygon([(cx, y - 8), (cx + 8, y), (cx, y + 8), (cx - 8, y)], fill=GOLD)


def _render(hindi, roman_urdu, photo, out_path, banner="LATEST NEWS", headline_extra=""):
    img = _background()
    draw = ImageDraw.Draw(img)
    _frame(draw)
    cx = W // 2

    # Header: NR logo centred, date at the top right
    # Center text
    mark = _logo_mark(225)
    img.paste(mark, (cx - mark.width // 2, 46), mark)

    today = datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%d-%m-%Y")
    f_date = _font("NotoSans.ttf", 30, "Bold")
    f_today = _font("NotoSans.ttf", 21, "Medium")
    dw = draw.textlength(today, font=f_date)
    date_x = W - MARGIN - dw
    _calendar_icon(draw, int(date_x - 48), 60)
    draw.text((date_x, 62), today, font=f_date, fill=GOLD_LIGHT)
    tw = draw.textlength("(Today)", font=f_today)
    draw.text((W - MARGIN - tw, 100), "(Today)", font=f_today, fill=GREY)

    # Trapezoid "LATEST NEWS" banner with side lines
    banner_y, banner_h = 172, 78
    pts = [(cx - 292, banner_y), (cx + 292, banner_y), (cx + 264, banner_y + banner_h), (cx - 264, banner_y + banner_h)]
    draw.polygon(pts, fill=(19, 16, 9))
    draw.line(pts + [pts[0]], fill=GOLD, width=3, joint="curve")
    mid = banner_y + banner_h // 2
    for dy in (-7, 7):
        draw.line([(MARGIN, mid + dy), (cx - 320, mid + dy)], fill=GOLD_DARK, width=2)
        draw.line([(cx + 320, mid + dy), (W - MARGIN, mid + dy)], fill=GOLD_DARK, width=2)
    bsize = 48
    while bsize > 24:
        f_banner = _font("NotoSans.ttf", bsize, "Bold")
        if _tracked_width(draw, banner, f_banner, 3) <= 500:
            break
        bsize -= 2
    f_banner = _font("NotoSans.ttf", bsize, "Bold")
    _gradient_text(img, cx, banner_y + (banner_h - bsize) // 2 - 2, banner, f_banner)

    y = banner_y + banner_h + 22

    # A long headline (old single-story mode) goes under the banner in gold
    if headline_extra:
        f_extra = _font("NotoSans.ttf", 26, "Bold")
        for line in _wrap(draw, headline_extra, f_extra, W - 2 * MARGIN)[:2]:
            lw = draw.textlength(line, font=f_extra)
            draw.text((cx - lw / 2, y), line, font=f_extra, fill=GOLD)
            y += 34
        y += 6

    # Footer position is fixed; the body must fit above it
    footer_line_y = H - 150
    body_bottom = footer_line_y - 28
    text_w = W - MARGIN - (MARGIN + NUM_W)

    # Pick photo height + text size: reference photo first, then trade a
    # little photo height for readability, then shrink text, then drop photo.
    if photo is not None:
        attempts = []
        for ph, min_size in [(270, 24), (235, 24), (200, 23), (270, 22), (235, 22), (200, 22), (0, 22)]:
            for size in range(27, min_size - 1, -1):
                if (ph, size) not in attempts:
                    attempts.append((ph, size))
    else:
        attempts = [(0, size) for size in range(28, 21, -1)]

    chosen = attempts[-1]
    for photo_h, size in attempts:
        f_hi = _font("NotoSansDevanagari.ttf", size, "Bold")
        f_ru = _font("NotoSans.ttf", size - 1, "Medium")
        top = y + photo_h + 26 if photo_h else y + 4
        need = (LABEL_H + _numbered_height(draw, hindi, f_hi, text_w)
                + DIVIDER_H + LABEL_H + _numbered_height(draw, roman_urdu, f_ru, text_w))
        if top + need <= body_bottom:
            chosen = (photo_h, size)
            break
    photo_h, size = chosen
    f_hi = _font("NotoSansDevanagari.ttf", size, "Bold")
    f_ru = _font("NotoSans.ttf", size - 1, "Medium")
    if photo_h:
        # Fewer/shorter stories leave space at the bottom: give it to the photo
        need = (LABEL_H + _numbered_height(draw, hindi, f_hi, text_w)
                + DIVIDER_H + LABEL_H + _numbered_height(draw, roman_urdu, f_ru, text_w))
        leftover = body_bottom - (y + photo_h + 26 + need)
        if leftover > 60:
            photo_h += min(int(leftover - 24), 110)

    if photo is not None and photo_h:
        _rounded_photo(photo, img, MARGIN, int(y), W - 2 * MARGIN, photo_h)
        draw = ImageDraw.Draw(img)
        y += photo_h + 26
    else:
        y += 4

    # Hindi section, divider, Roman Urdu section
    _section_label(draw, y, "हिंदी", _font("NotoSansDevanagari.ttf", 30, "Bold"))
    y += LABEL_H
    y = _draw_numbered(draw, y, hindi, f_hi)
    _diamond_divider(draw, y + 12)
    y += DIVIDER_H
    _section_label(draw, y, "ROMAN URDU", _font("NotoSans.ttf", 25, "Bold"), tracking=2, y_off=4)
    y += LABEL_H
    _draw_numbered(draw, y, roman_urdu, f_ru)

    # Footer: small NR logo, separator, byline, with a rule on both sides
    credit = "By Abu Aimal & Aimal Akram"
    f_credit = _font("NotoSans.ttf", 30, "Bold")
    small = _logo_mark(96)
    credit_w = draw.textlength(credit, font=f_credit)
    group_w = small.width + 26 + credit_w
    group_x = (W - group_w) / 2
    credit_y = H - 112
    draw.line([(MARGIN + 50, footer_line_y), (group_x - 28, footer_line_y)], fill=GOLD_DARK, width=2)
    draw.line([(group_x + group_w + 28, footer_line_y), (W - MARGIN - 50, footer_line_y)], fill=GOLD_DARK, width=2)
    img.paste(small, (int(group_x), int(credit_y + 17 - small.height / 2)), small)
    sep_x = group_x + small.width + 13
    draw.line([(sep_x, credit_y - 8), (sep_x, credit_y + 42)], fill=GOLD, width=3)
    draw.text((group_x + small.width + 26, credit_y), credit, font=f_credit, fill=GOLD_LIGHT)

    img.save(out_path, "PNG", optimize=True)
    return out_path


def render_card(headline, hindi, roman_urdu, source, out_path, photo=None):
    """`source` is ignored on purpose: source names are never printed on the card."""
    headline = (headline or "").strip()
    if not headline or headline.upper() == "LATEST NEWS":
        return _render(hindi, roman_urdu, photo, out_path)
    if len(headline) <= 24:
        return _render(hindi, roman_urdu, photo, out_path, banner=headline.upper())
    return _render(hindi, roman_urdu, photo, out_path, headline_extra=headline)


def render_digest_card(stories, out_path):
    """Reference-design card for a digest of stories.

    stories: list of {"hindi": [..], "roman": [..], "photo": PIL image or None}
    All stories' Hindi points form the numbered Hindi list and all Roman Urdu
    points the numbered Roman Urdu list; the wide photo at the top is the
    first story photo available (none if no story has a usable photo).
    """
    hindi = [p for s in stories for p in s["hindi"]]
    roman = [p for s in stories for p in s["roman"]]
    photo = next((s["photo"] for s in stories if s.get("photo") is not None), None)
    return _render(hindi, roman, photo, out_path)
