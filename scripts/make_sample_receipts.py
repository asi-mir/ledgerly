"""Draw synthetic Turkish thermal receipts for tests, the demo video and screenshots.

Every shop name and amount here is made up. Run: .venv/bin/python scripts/make_sample_receipts.py
Writes samples/receipts/*.jpg. Same input, same pictures (no randomness).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "samples" / "receipts"
WIDTH = 34  # characters per line, like an 80 mm thermal printer
FONT_SIZE = 26
MARGIN = 60  # grey table around the paper
PAD = 36  # white paper around the text

FONTS = [
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Supplemental/Courier New.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
]

# (file, shop, address, date or None, [(name, quantity, unit price)])
RECEIPTS = [
    (
        "receipt-market.jpg",
        "ÖRNEK MARKET",
        "DEMO MAH. ÖRNEK CAD. NO:1",
        "28.11.2026",
        [
            ("KARPUZ", 1, "250"),
            ("TAVUK GÖĞÜS", 1, "400"),
            ("BEYAZ PEYNİR", 1, "450"),
            ("ZEYTİNYAĞI 1 L", 1, "500"),
            ("SÜT 1 L", 2, "60"),
            ("EKMEK", 4, "20"),
        ],
    ),
    (
        "receipt-cake.jpg",
        "DEMO PASTANESİ",
        "ÖRNEK SOK. NO:7",
        "29.11.2026",
        [
            ("DOĞUM GÜNÜ PASTASI", 1, "1250"),
            ("PASTA MUMU", 1, "50"),
        ],
    ),
    (
        "receipt-no-date.jpg",
        "ÖRNEK MANAV",
        "DEMO PAZAR YERİ NO:3",
        None,
        [
            ("ELMA", 1, "150"),
            ("MANDALİNA", 1, "200"),
            ("NAR", 1, "300"),
            ("DOMATES", 1, "100"),
        ],
    ),
    (
        "receipt-market-rehearsal.jpg",
        "ÖRNEK MARKET",
        "DEMO MAH. ÖRNEK CAD. NO:1",
        "27.10.2026",
        [
            ("YUMURTA 30 LU", 1, "300"),
            ("MAKARNA 500 G", 3, "40"),
            ("DOMATES", 1, "180"),
            ("DANA KIYMA", 1, "600"),
        ],
    ),
    (
        "receipt-no-date-rehearsal.jpg",
        "DEMO FIRIN",
        "ÖRNEK SOK. NO:12",
        None,
        [
            ("EKMEK", 5, "20"),
            ("SİMİT", 4, "25"),
            ("POĞAÇA", 1, "200"),
        ],
    ),
]


def tr(amount: Decimal) -> str:
    """1250 -> 1.250,00 (Turkish receipt style)."""
    text = f"{amount:,.2f}"
    return text.replace(",", "_").replace(".", ",").replace("_", ".")


def font() -> ImageFont.ImageFont:
    for path in FONTS:
        if Path(path).exists():
            return ImageFont.truetype(path, FONT_SIZE)
    return ImageFont.load_default(size=FONT_SIZE)


def row(left: str, right: str) -> str:
    return left + " " * max(1, WIDTH - len(left) - len(right)) + right


def receipt_lines(shop: str, address: str, day: str | None, items: list) -> list[str]:
    lines = [shop.center(WIDTH), address.center(WIDTH), "VD: ÖRNEK  VN: 0000000000".center(WIDTH), ""]
    if day:
        lines.append(row(f"TARİH: {day}", "SAAT: 18:30"))
    lines += [row("FİŞ NO: 0042", "Z NO: 0007"), "-" * WIDTH]
    total = Decimal(0)
    for name, qty, unit in items:
        price = Decimal(unit) * qty
        total += price
        if qty > 1:
            lines.append(f"{qty} X {tr(Decimal(unit))}")
        lines.append(row(name, "*" + tr(price)))
    kdv = (total / Decimal("1.01") * Decimal("0.01")).quantize(Decimal("0.01"))
    lines += [
        "-" * WIDTH,
        row("TOPKDV", "*" + tr(kdv)),
        row("TOPLAM", "*" + tr(total)),
        row("KREDİ KARTI", "*" + tr(total)),
        "",
        "KDV DAHİLDİR".center(WIDTH),
        "TEŞEKKÜR EDERİZ".center(WIDTH),
        "ÖRNEK FİŞ - GERÇEK DEĞİLDİR".center(WIDTH),
    ]
    return lines


def draw(lines: list[str], path: Path) -> None:
    f = font()
    left, top, right, bottom = f.getbbox("M" * WIDTH)
    line_h = int((bottom - top) * 1.55)
    paper_w = (right - left) + 2 * PAD
    paper_h = line_h * len(lines) + 2 * PAD
    img = Image.new("RGB", (paper_w + 2 * MARGIN, paper_h + 2 * MARGIN), (205, 205, 200))
    d = ImageDraw.Draw(img)
    d.rectangle([MARGIN, MARGIN, MARGIN + paper_w, MARGIN + paper_h], fill=(252, 252, 249))
    y = MARGIN + PAD
    for line in lines:
        d.text((MARGIN + PAD, y), line, font=f, fill=(25, 25, 25))
        y += line_h
    img.save(path, "JPEG", quality=92)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, shop, address, day, items in RECEIPTS:
        draw(receipt_lines(shop, address, day, items), OUT / name)
        print(OUT / name)


if __name__ == "__main__":
    main()
