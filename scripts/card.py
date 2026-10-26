"""
Renders the black-and-gold breaking-news card (1080x1080 PNG).

Layout: small logo in the top-left corner, gold headline beside it, Hindi
bullets (Devanagari, bold), Roman Urdu bullets, and a credit line at the
bottom with a small logo before it. All text is drawn on the image.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = ROOT / "assets" / "fonts"
LOGO = ROOT / "assets" / "logo.jpg"

W = H = 1080
BLACK = (14, 12, 10)
GOLD = (212, 175, 90)
GOLD_DARK = (150, 118, 52)
WHITE = (245, 240, 230)
GREY = (190, 182, 168)
MARGIN = 70


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


def _bullets_height(draw, items, font, max_w, line_gap):
    h = 0
    for item in items:
        h += len(_wrap(draw, item, font, max_w - 44)) * (font.size + line_gap) + 22
    return h


def _draw_bullets(draw, y, items, font, max_w, line_gap):
    for item in items:
        lines = _wrap(draw, item, font, max_w - 44)
        draw.ellipse([MARGIN + 6, y + font.size // 2 - 4, MARGIN + 22, y + font.size // 2 + 12], fill=GOLD)
        for i, line in enumerate(lines):
            draw.text((MARGIN + 44, y + i * (font.size + line_gap)), line, font=font, fill=WHITE)
        y += len(lines) * (font.size + line_gap) + 22
    return y


def _logo(width):
    logo = Image.open(LOGO).convert("RGB")
    return logo.resize((width, int(logo.height * width / logo.width)))


def render_card(headline, hindi, roman_urdu, source, out_path, photo=None):
    """`source` is ignored on purpose: source names are never printed on the card."""
    img = Image.new("RGB", (W, H), BLACK)
    draw = ImageDraw.Draw(img)
    draw.rectangle([30, 30, W - 30, H - 30], outline=GOLD_DARK, width=3)

    # Logo in the top-left corner
    logo = _logo(170)
    img.paste(logo, (MARGIN, MARGIN))

    # Headline beside the logo (wraps to two lines if needed)
    head_x = MARGIN + logo.width + 36
    head_max = W - MARGIN - head_x
    font_head = _font("NotoSans.ttf", 46, "Bold")
    head_lines = _wrap(draw, headline, font_head, head_max)
    hy = MARGIN + 6
    for line in head_lines:
        draw.text((head_x, hy), line, font=font_head, fill=GOLD)
        hy += font_head.size + 8
    top_block_bottom = max(MARGIN + logo.height, hy)

    rule1 = top_block_bottom + 26
    draw.rectangle([MARGIN, rule1, W - MARGIN, rule1 + 4], fill=GOLD)
    body_top = rule1 + 28
    max_w_body = W - 2 * MARGIN
    def _body_fits(top, size):
        f_hi_ = _font("NotoSansDevanagari.ttf", size, "Bold")
        f_ru_ = _font("NotoSans.ttf", size - 2, "Medium")
        need_ = 90 + _bullets_height(draw, hindi, f_hi_, max_w_body, 10) + _bullets_height(draw, roman_urdu, f_ru_, max_w_body, 8)
        return top + need_ <= H - 184  # same bottom limit as body_bottom below

    if photo is not None:
        # Photo band under the headline: cropped to fill, with a gold frame.
        # Dropped if the stories would not fit beside it at a readable size.
        band_h = 130
        py_ = rule1 + 22
        if not _body_fits(py_ + band_h + 26, 24):
            photo = None
    if photo is not None:
        band_h = 130
        band_w = W - 2 * MARGIN
        py = rule1 + 22
        scale = max(band_w / photo.width, band_h / photo.height)
        resized = photo.resize((int(photo.width * scale) + 1, int(photo.height * scale) + 1))
        left = (resized.width - band_w) // 2
        top = int((resized.height - band_h) * 0.12)  # near the top: keeps faces in portrait photos
        band = resized.crop((left, top, left + band_w, top + band_h))
        img.paste(band, (MARGIN, py))
        draw.rectangle([MARGIN, py, W - MARGIN, py + band_h], outline=GOLD, width=3)
        body_top = py + band_h + 26

    # Bottom: credit line with small logo, then rule
    credit_font = _font("NotoSans.ttf", 36, "Bold")
    credit = "ABU AIMAL & AIMAL AKRAM"
    small_logo = _logo(64)
    credit_w = draw.textlength(credit, font=credit_font)
    group_w = small_logo.width + 18 + credit_w
    group_x = (W - group_w) / 2
    credit_y = H - 118
    img.paste(small_logo, (int(group_x), credit_y - 6))
    draw.text((group_x + small_logo.width + 18, credit_y), credit, font=credit_font, fill=GOLD)
    bottom_rule = H - 160
    draw.rectangle([MARGIN, bottom_rule, W - MARGIN, bottom_rule + 4], fill=GOLD)
    body_bottom = bottom_rule - 24

    max_w = W - 2 * MARGIN
    size = 54
    while size > 22:
        f_hi = _font("NotoSansDevanagari.ttf", size, "Bold")
        f_ru = _font("NotoSans.ttf", size - 2, "Medium")
        label_h = 40 + 50
        need = label_h + _bullets_height(draw, hindi, f_hi, max_w, 10) + _bullets_height(draw, roman_urdu, f_ru, max_w, 8)
        if body_top + need <= body_bottom:
            break
        size -= 1

    label_font = _font("NotoSans.ttf", 30, "Bold")
    y = body_top
    draw.text((MARGIN, y), "HINDI", font=label_font, fill=GOLD_DARK)
    y += 40
    y = _draw_bullets(draw, y, hindi, f_hi, max_w, 10)
    draw.text((MARGIN, y + 4), "ROMAN URDU", font=label_font, fill=GOLD_DARK)
    y += 50
    _draw_bullets(draw, y, roman_urdu, f_ru, max_w, 8)

    img.save(out_path, "PNG", optimize=True)
    return out_path


def fit_photo(img, w, h):
    """Crop a photo to exactly w x h, keeping the upper part (where faces usually are)."""
    scale = max(w / img.width, h / img.height)
    rw, rh = int(img.width * scale) + 1, int(img.height * scale) + 1
    resized = img.resize((rw, rh))
    left = (rw - w) // 2
    top = int((rh - h) * 0.25)
    return resized.crop((left, top, left + w, top + h))


def render_digest_card(stories, out_path):
    """Per-story layout: each story has its own photo (optional) beside its bullets.

    stories: list of {"hindi": [..], "roman": [..], "photo": PIL image or None}
    """
    img = Image.new("RGB", (W, H), BLACK)
    draw = ImageDraw.Draw(img)
    draw.rectangle([30, 30, W - 30, H - 30], outline=GOLD_DARK, width=3)
    logo = _logo(170)
    img.paste(logo, (MARGIN, MARGIN))
    font_head = _font("NotoSans.ttf", 46, "Bold")
    head_x = MARGIN + logo.width + 36
    draw.text((head_x, MARGIN + 6), "LATEST NEWS", font=font_head, fill=GOLD)
    top = MARGIN + logo.height + 26
    draw.rectangle([MARGIN, top, W - MARGIN, top + 4], fill=GOLD)
    body_top = top + 26

    credit_font = _font("NotoSans.ttf", 36, "Bold")
    credit = "ABU AIMAL & AIMAL AKRAM"
    small_logo = _logo(64)
    credit_w = draw.textlength(credit, font=credit_font)
    group_w = small_logo.width + 18 + credit_w
    group_x = (W - group_w) / 2
    credit_y = H - 118
    img.paste(small_logo, (int(group_x), credit_y - 6))
    draw.text((group_x + small_logo.width + 18, credit_y), credit, font=credit_font, fill=GOLD)
    bottom_rule = H - 160
    draw.rectangle([MARGIN, bottom_rule, W - MARGIN, bottom_rule + 4], fill=GOLD)
    body_bottom = bottom_rule - 24

    n = len(stories)
    gap = 28
    photo_w, photo_h = 330, 210
    text_w_photo = W - 2 * MARGIN - photo_w - 30
    text_w_full = W - 2 * MARGIN

    size = 36
    while True:
        f_hi = _font("NotoSansDevanagari.ttf", size, "Bold")
        f_ru = _font("NotoSans.ttf", size - 4, "Medium")
        heights = []
        for s_ in stories:
            tw = text_w_photo if s_["photo"] is not None else text_w_full
            h_text = _bullets_height(draw, s_["hindi"], f_hi, tw, 8) + _bullets_height(draw, s_["roman"], f_ru, tw, 6)
            heights.append(max(h_text, photo_h if s_["photo"] is not None else 0))
        total = sum(heights) + gap * (n - 1)
        if body_top + total <= body_bottom or size <= 22:
            break
        size -= 2

    y = body_top
    for i, s_ in enumerate(stories):
        if s_["photo"] is not None:
            px = W - MARGIN - photo_w
            img.paste(fit_photo(s_["photo"], photo_w, photo_h), (px, int(y)))
            draw.rectangle([px, y, px + photo_w, y + photo_h], outline=GOLD, width=3)
            tw = text_w_photo
        else:
            tw = text_w_full
        yy = _draw_bullets(draw, y, s_["hindi"], f_hi, tw, 8)
        _draw_bullets(draw, yy + 4, s_["roman"], f_ru, tw, 6)
        y += heights[i]
        if i < n - 1:
            sep = int(y + gap / 2)
            draw.line([(MARGIN + 60, sep), (W - MARGIN - 60, sep)], fill=GOLD_DARK, width=2)
            y += gap

    img.save(out_path, "PNG", optimize=True)
    return out_path
