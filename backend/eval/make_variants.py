"""Build the degraded / disguised photo variants used by the evaluation set from the real photos in the repo.

Deterministic (fixed seed), so anyone can regenerate them:  python -m eval.make_variants   (from backend/)
"""
from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "photos"
LAMP = REPO / "demo-photos" / "lamp-broken.jpg"
MUG = REPO / "demo-photos" / "mug-broken.jpg"
PRODUCTS = REPO / "assets" / "products"


def _save(im: Image.Image, name: str) -> None:
    im = im.convert("RGB")
    im.thumbnail((1600, 1600))
    im.save(OUT / name, "JPEG", quality=85)          # PIL writes no EXIF unless asked


def _noise(im: Image.Image, amount: int, seed: int) -> Image.Image:
    rnd = random.Random(seed)
    px = im.load()
    for _ in range(im.width * im.height // 6):
        x, y = rnd.randrange(im.width), rnd.randrange(im.height)
        r, g, b = px[x, y]
        d = rnd.randint(-amount, amount)
        px[x, y] = (max(0, min(255, r + d)), max(0, min(255, g + d)), max(0, min(255, b + d)))
    return im


def build() -> list[str]:
    OUT.mkdir(parents=True, exist_ok=True)
    lamp = Image.open(LAMP).convert("RGB")
    mug = Image.open(MUG).convert("RGB")
    made = []

    _save(lamp.filter(ImageFilter.GaussianBlur(22)), "var-lamp-blur.jpg"); made.append("var-lamp-blur.jpg")
    _save(mug.filter(ImageFilter.GaussianBlur(22)), "var-mug-blur.jpg"); made.append("var-mug-blur.jpg")

    dark = _noise(ImageEnhance.Brightness(lamp).enhance(0.10), 10, seed=7)
    _save(dark, "var-lamp-dark.jpg"); made.append("var-lamp-dark.jpg")

    w, h = lamp.size                                   # a corner of the box and bubble wrap, no lamp in frame
    _save(lamp.crop((0, int(h * 0.05), int(w * 0.18), int(h * 0.45))).resize((960, 720)), "var-lamp-crop.jpg")
    made.append("var-lamp-crop.jpg")

    tiny = lamp.resize((64, 36), Image.BILINEAR).resize((1600, 900), Image.NEAREST)
    _save(tiny, "var-lamp-tiny.jpg"); made.append("var-lamp-tiny.jpg")

    # The shop's own listing photo, mirrored, cropped and slightly recoloured: a buyer trying to pass it off.
    listing = Image.open(PRODUCTS / "lamp02.jpg").convert("RGB")
    lw, lh = listing.size
    disguised = ImageOps.mirror(listing).crop((int(lw * .06), int(lh * .05), int(lw * .94), int(lh * .95)))
    disguised = ImageEnhance.Color(ImageEnhance.Brightness(disguised).enhance(0.93)).enhance(0.9)
    _save(disguised, "var-lamp-listing-disguised.jpg"); made.append("var-lamp-listing-disguised.jpg")

    # A genuine damage photo, but as a phone screenshot of someone else's post (grey UI bars, rounded card).
    canvas = Image.new("RGB", (1080, 2000), (242, 242, 245))
    d = ImageDraw.Draw(canvas)
    d.rectangle((0, 0, 1080, 140), fill=(255, 255, 255))
    d.rectangle((0, 1860, 1080, 2000), fill=(255, 255, 255))
    for i in range(5):
        d.rounded_rectangle((60, 1460 + i * 70, 60 + 900 - i * 120, 1500 + i * 70), 12, fill=(214, 214, 220))
    shot = lamp.copy(); shot.thumbnail((1000, 1000))
    canvas.paste(shot, (40, 260))
    _save(canvas, "var-lamp-screenshot.jpg"); made.append("var-lamp-screenshot.jpg")
    return made


if __name__ == "__main__":
    for name in build():
        print("wrote", OUT / name)
