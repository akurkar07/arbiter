"""Generate sample invoice PNGs used by the ingestion demo and tests.

Run once to (re)create the committed fixtures under tests/fixtures/invoices/.
Kept as a script, not a test dependency: the fixtures are committed as real PNGs
so the suite needs no image library at run time. Requires Pillow only here.

    python scripts/make_sample_invoices.py

Two invoices, chosen to land on opposite sides of governance:
  * acme_print_140.png   — Acme Print Ltd, £140  -> slug 'acme_print' (ON the
                           demo allowlist) -> a clean APPROVE through the engine.
  * shadow_900.png       — Shadow Logistics, £900 -> slug 'shadow_logistics'
                           (OFF the allowlist) -> BLOCKED, proving extraction
                           grants no power around governance.
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures", "invoices")

_W, _H = 850, 1100
_INK = (17, 24, 39)
_MUTE = (107, 114, 128)
_LINE = (209, 213, 219)


def _font(size: int, bold: bool = False):
    """A real TrueType font if one is on the box, else Pillow's bitmap default."""
    candidates = (
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
        if bold else
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    )
    for path in candidates:
        if os.path.isfile(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _invoice(vendor: str, amount: float, ref: str, due: str,
             line_desc: str, account_hint: str) -> Image.Image:
    img = Image.new("RGB", (_W, _H), "white")
    d = ImageDraw.Draw(img)

    d.text((60, 60), vendor, font=_font(40, bold=True), fill=_INK)
    d.text((60, 112), "INVOICE", font=_font(22, bold=True), fill=_MUTE)

    d.text((600, 64), f"Invoice #: {ref}", font=_font(18), fill=_INK)
    d.text((600, 92), f"Date due: {due}", font=_font(18), fill=_INK)

    d.line([(60, 170), (_W - 60, 170)], fill=_LINE, width=2)

    d.text((60, 200), "Bill to:", font=_font(18, bold=True), fill=_INK)
    d.text((60, 228), "Arbiter Demo Co.", font=_font(18), fill=_INK)
    d.text((60, 252), "1 Test Street, London", font=_font(18), fill=_MUTE)

    # Line-items table.
    y = 330
    d.text((60, y), "Description", font=_font(18, bold=True), fill=_INK)
    d.text((640, y), "Amount", font=_font(18, bold=True), fill=_INK)
    d.line([(60, y + 30), (_W - 60, y + 30)], fill=_LINE, width=1)
    y += 48
    d.text((60, y), line_desc, font=_font(18), fill=_INK)
    d.text((640, y), f"GBP {amount:,.2f}", font=_font(18), fill=_INK)

    # Total.
    y += 80
    d.line([(_W - 360, y), (_W - 60, y)], fill=_LINE, width=1)
    y += 16
    d.text((_W - 360, y), "Total due", font=_font(22, bold=True), fill=_INK)
    d.text((_W - 200, y), f"GBP {amount:,.2f}", font=_font(22, bold=True), fill=_INK)

    d.text((60, _H - 140), f"Payment to: {account_hint}", font=_font(16), fill=_MUTE)
    d.text((60, _H - 110), "Thank you for your business.", font=_font(16), fill=_MUTE)
    return img


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.abspath(OUT_DIR)

    _invoice(
        vendor="Acme Print Ltd", amount=140.00, ref="ACME-2026-0042",
        due="2026-07-15", line_desc="Q3 product brochure print run (500 units)",
        account_hint="Acme Print Ltd, sort 11-22-33, acct 11112222",
    ).save(os.path.join(out, "acme_print_140.png"))

    _invoice(
        vendor="Shadow Logistics", amount=900.00, ref="SHX-99021",
        due="2026-07-01", line_desc="Expedited freight services (unlisted)",
        account_hint="Shadow Logistics, sort 99-88-77, acct 90909090",
    ).save(os.path.join(out, "shadow_900.png"))

    print(f"wrote 2 invoice fixtures to {out}")


if __name__ == "__main__":
    main()
