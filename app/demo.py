"""Synthetic demo household, seeded on boot when DEMO_TODAY is set.

Everything here is made up: the family, the shops, the items and every amount. Twelve months
(Dec 1, 2025 - Nov 30, 2026) of receipts with a daily rhythm (bakery, a weekly big shop,
bills on fixed days), a mild price trend, and family events. Last December is the expensive
one: four birthdays, a dinner at the wife's aunt's, and Yalda Night at home for 12 people.
The same seed always gives the same data, and samples/demo_*.csv is a copy of it.
"""

from __future__ import annotations

import csv
import io
import random
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import delete, func, insert, select
from sqlalchemy.engine import Engine

SEED = 1404
START = date(2025, 12, 1)
END = date(2026, 11, 30)
BUDGET_CENTS = 3_000_000  # ₺30,000
PAYDAY = 1
MONTHLY_RISE = 0.012  # prices rise about 1.2% a month

# (title, kind, date, guests, hosted). The last four are this December, told to the bot earlier.
# Arman's birthday on Dec 14, 2026 and Yalda Night 2026 are not here: the user adds them in chat.
EVENTS = [
    ("Kian's birthday", "birthday", date(2025, 12, 5), 0, False),
    ("Sara's birthday", "birthday", date(2025, 12, 9), 0, False),
    ("Dinner at Aunt Shirin's", "dinner", date(2025, 12, 12), 0, False),
    ("Arman's birthday", "birthday", date(2025, 12, 14), 0, False),
    ("Mina's birthday", "birthday", date(2025, 12, 17), 0, False),
    ("Yalda Night", "hosting", date(2025, 12, 21), 12, True),
    ("Father-in-law's birthday", "birthday", date(2026, 1, 24), 0, False),
    ("Wife's birthday", "birthday", date(2026, 2, 11), 0, False),
    ("Nowruz", "hosting", date(2026, 3, 21), 8, True),
    ("Nika's birthday", "birthday", date(2026, 4, 25), 0, False),
    ("Mother's birthday", "birthday", date(2026, 5, 30), 0, False),
    ("Family barbecue", "hosting", date(2026, 6, 13), 6, True),
    ("Uncle Reza's birthday", "birthday", date(2026, 7, 8), 0, False),
    ("Dinner at Aunt Shirin's", "dinner", date(2026, 8, 7), 0, False),
    ("Brother-in-law's birthday", "birthday", date(2026, 8, 19), 0, False),
    ("Dinner for friends", "hosting", date(2026, 9, 26), 6, True),
    ("Grandma's birthday", "birthday", date(2026, 10, 21), 0, False),
    ("Leila's birthday", "birthday", date(2026, 11, 17), 0, False),
    ("Kian's birthday", "birthday", date(2026, 12, 5), 0, False),
    ("Sara's birthday", "birthday", date(2026, 12, 9), 0, False),
    ("Dinner at Aunt Shirin's", "dinner", date(2026, 12, 11), 0, False),
    ("Mina's birthday", "birthday", date(2026, 12, 17), 0, False),
]

# Gift sizes at Dec 2025 prices. Children get toys; Dec 2025 gifts were the big ones.
GIFTS = {
    "Kian's birthday": (3000, "Neşeli Oyuncak", "OYUNCAK SETİ"),
    "Sara's birthday": (2500, "Zarif Hediyelik", "HEDİYE KUTUSU"),
    "Arman's birthday": (3000, "Neşeli Oyuncak", "OYUNCAK SETİ"),
    "Mina's birthday": (2500, "Zarif Hediyelik", "ŞAL"),
    "Wife's birthday": (3500, "Zarif Hediyelik", "GÜMÜŞ KOLYE"),
    "Nika's birthday": (2200, "Neşeli Oyuncak", "OYUNCAK BEBEK"),
}
DEFAULT_GIFT = (2000, "Zarif Hediyelik", "HEDİYE KUTUSU")

G, F, M, B, A = "Groceries", "Fruit and Vegs", "Meat/Chicken/Fish", "Bread/Water", "Additional expenses"

BIG_SHOP = [
    ("SÜT 1L", 38, G), ("YUMURTA 15'Lİ", 95, G), ("BEYAZ PEYNİR 500G", 160, G), ("YOĞURT 1.5KG", 85, G),
    ("PİRİNÇ 1KG", 70, G), ("MAKARNA 500G", 22, G), ("AYÇİÇEK YAĞI 2L", 150, G), ("ÇAY 1KG", 210, G),
    ("DETERJAN 4KG", 240, G), ("TUVALET KAĞIDI 16'LI", 190, G), ("TAVUK BUT 1KG", 130, M),
    ("DANA KIYMA 500G", 260, M), ("DOMATES 1KG", 35, F), ("SALATALIK 1KG", 30, F), ("ELMA 1KG", 40, F),
    ("PATATES 2KG", 50, F), ("SOĞAN 2KG", 40, F), ("SU 5L", 30, B), ("ZEYTİN 500G", 120, G),
]
CORNER = [("SÜT 1L", 38, G), ("EKMEK", 15, B), ("YUMURTA 10'LU", 65, G), ("KAŞAR PEYNİR 250G", 110, G),
          ("BİSKÜVİ", 25, G), ("MEYVE SUYU 1L", 45, G), ("BULAŞIK SÜNGERİ", 30, G), ("MAKARNA 500G", 22, G)]
GREENGROCER = [("DOMATES 1KG", 35, F), ("BİBER 1KG", 45, F), ("PATLICAN 1KG", 40, F), ("MANDALİNA 1KG", 35, F),
               ("ISPANAK 1KG", 40, F), ("LİMON 1KG", 30, F), ("MUZ 1KG", 70, F), ("PORTAKAL 1KG", 30, F)]
BUTCHER = [("DANA KUŞBAŞI 500G", 330, M), ("TAVUK GÖĞÜS 1KG", 150, M), ("SUCUK 250G", 140, M)]
HOSTING_RUN = [("KUZU ETİ", 650, M), ("TAVUK BUT", 260, M), ("PİRİNÇ", 330, G), ("ZEYTİNYAĞI", 320, G),
               ("DOMATES", 70, F), ("SALATALIK", 60, F), ("MEYVE SUYU", 45, G), ("GAZOZ", 55, G),
               ("YOĞURT", 110, G), ("SU", 30, B)]
YALDA_RUN = [("KARPUZ", 450, F), ("NAR 3KG", 180, F)]
YALDA_EXTRA = [("KARIŞIK KURUYEMİŞ", 650, G), ("LEBLEBİ", 110, G), ("KURU ÜZÜM", 140, G), ("ÇEKİRDEK", 120, G)]
DESSERT = [("BAKLAVA", 900, G), ("NAR", 120, F), ("MANDALİNA", 70, F), ("ÜZÜM", 90, F)]
BAG = [("ALIŞVERİŞ POŞETİ", 1, A)]

# Every shop name is made up.
MERCHANTS = {
    "Bereket Fırını", "Papatya Market", "Damla Su", "Bahçe Manav", "Kasap Usta", "Lezzet Ocakbaşı",
    "Güneş Süpermarket", "Kent Kart Dolum", "Yol Akaryakıt", "Lale Sitesi Yönetimi", "HızlıNet",
    "Şehir Elektrik", "Şehir Su", "Kent Doğalgaz", "Şifa Eczanesi", "Neşeli Oyuncak", "Zarif Hediyelik",
    "Tatlı Köşe Pastanesi", "Çınar Kuruyemiş", "Lale Çiçekçilik",
}


@dataclass
class DemoReceipt:
    day: date
    merchant: str
    items: list[tuple[str, int, str]]  # (item, amount_cents, category)

    @property
    def total_cents(self) -> int:
        return sum(cents for _, cents, _ in self.items)


def _index(day: date) -> float:
    return (1 + MONTHLY_RISE) ** ((day - START).days / 30.44)


def _cents(amount: float) -> int:
    """Whole 10 kuruş, at least ₺0.50."""
    return max(50, int(round(amount, 1) * 100 + 0.5) // 10 * 10)


class _Builder:
    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.receipts: list[DemoReceipt] = []

    def add(self, day: date, merchant: str, target: float = 0, pool=(), k: int = 0, fixed=()) -> None:
        """One receipt. `fixed` items keep their own price; `k` items from `pool` are scaled so the
        receipt is worth about `target`. Prices are Dec 2025 prices; the trend is added here."""
        rng, index = self.rng, _index(day)
        items = [(item, _cents(price * index * rng.uniform(0.95, 1.05)), cat) for item, price, cat in fixed]
        if k:
            chosen = rng.sample(list(pool), min(k, len(pool)))
            rest = max(target * index * rng.uniform(0.9, 1.1) - sum(c for _, c, _ in items) / 100, 50)
            scale = rest / sum(price for _, price, _ in chosen)
            items += [(item, _cents(price * scale * rng.uniform(0.95, 1.05)), cat) for item, price, cat in chosen]
        self.receipts.append(DemoReceipt(day, merchant, items))

    def one(self, day: date, merchant: str, item: str, amount: float, category: str, noise: float = 0.05) -> None:
        value = amount * _index(day) * self.rng.uniform(1 - noise, 1 + noise)
        self.receipts.append(DemoReceipt(day, merchant, [(item, _cents(value), category)]))


def _daily(b: _Builder, day: date, skip_big_shop: bool) -> None:
    """The ordinary rhythm, the same every week. Bills come on fixed days of the month."""
    rng, dow, dom = b.rng, day.weekday(), day.day
    loaves = 2 if dow < 5 else 3
    b.add(day, "Bereket Fırını", fixed=[("EKMEK", 15, B)] * loaves + ([("POĞAÇA", 40, B)] if dow >= 5 else []))
    if dow in (0, 3):
        b.add(day, "Papatya Market", 200, CORNER, k=rng.randint(3, 5))
    if dow == 1:
        b.one(day, "Damla Su", "DAMACANA SU 19L", 110, B)
    if dow == 2:
        b.add(day, "Bahçe Manav", 300, GREENGROCER, k=rng.randint(4, 6))
    if dow == 3:
        b.add(day, "Kasap Usta", 300, BUTCHER, k=2)
    if dow == 4:
        b.one(day, "Lezzet Ocakbaşı", "AKŞAM YEMEĞİ", 400, "Entertainment/Restaurant", noise=0.1)
    if dow == 5 and not skip_big_shop:
        b.add(day, "Güneş Süpermarket", 1350, BIG_SHOP, k=rng.randint(10, 14), fixed=BAG)
    if dow == 0:
        b.one(day, "Kent Kart Dolum", "TOPLU TAŞIMA DOLUM", 200, "Transport", noise=0)
    bills = {
        1: ("Yol Akaryakıt", "BENZİN", 700, "Transport"),
        3: ("Lale Sitesi Yönetimi", "AİDAT", 600, "Dues"),
        5: ("HızlıNet", "İNTERNET FATURASI", 450, "Internet/Phone"),
        10: ("Şehir Elektrik", "ELEKTRİK FATURASI", 420, "Electrical bill"),
        12: ("Şehir Su", "SU FATURASI", 220, "Water bill"),
        15: ("Kent Doğalgaz", "DOĞALGAZ FATURASI", 450, "Gas bill"),
        16: ("Yol Akaryakıt", "BENZİN", 700, "Transport"),
        20: ("HızlıNet", "MOBİL HAT FATURASI", 350, "Internet/Phone"),
    }
    if dom in bills:
        merchant, item, amount, category = bills[dom]
        b.one(day, merchant, item, amount, category, noise=0.08)
    if dom == 24:
        b.one(day, "Şifa Eczanesi", "ECZANE", 260, A, noise=0.3)


def _events(b: _Builder, by_day: dict[date, list[tuple]]) -> set[date]:
    """Shopping for events: gifts the day before a birthday and a cake on the day; two big runs
    before a dinner we host (more guests, bigger runs); sweets for a dinner at someone's home."""
    busy: set[date] = set()
    for day, items in sorted(by_day.items()):
        for title, kind, _, guests, _hosted in items:
            if day < START or day > END:
                continue
            if kind == "birthday":
                amount, merchant, item = GIFTS.get(title, DEFAULT_GIFT)
                b.one(day - timedelta(days=1), merchant, item, amount, "Gifts")
                b.one(day, "Tatlı Köşe Pastanesi", "DOĞUM GÜNÜ PASTASI", 950, "Gifts")
            elif kind == "dinner":
                b.one(day, "Tatlı Köşe Pastanesi", "BAKLAVA 1KG", 1300, "Gifts")
            elif kind == "hosting":
                yalda = title.startswith("Yalda")
                two, one = day - timedelta(days=2), day - timedelta(days=1)
                b.add(two, "Güneş Süpermarket", 600 * guests, HOSTING_RUN, k=8,
                      fixed=(YALDA_RUN if yalda else []) + BAG)
                b.add(one, "Çınar Kuruyemiş" if yalda else "Bahçe Manav", 300 * guests,
                      YALDA_EXTRA if yalda else DESSERT, k=3 if yalda else 4,
                      fixed=[("BAKLAVA 1KG", 900, G)] if yalda else ())
                b.one(day, "Lale Çiçekçilik", "ÇİÇEK BUKETİ", 60 * guests, A)
                busy.update({two, one})
    # Holidays without guests: snacks for Chaharshanbe Suri, a picnic for Sizdah Bedar.
    b.add(date(2026, 3, 17), "Çınar Kuruyemiş", 600, YALDA_EXTRA, k=3)
    b.add(date(2026, 4, 1), "Kasap Usta", 1000, BUTCHER, k=3)
    b.add(date(2026, 4, 2), "Papatya Market", 350, CORNER, k=4)
    return busy


def generate() -> tuple[list[DemoReceipt], list[dict]]:
    """The demo ledger and events. Same seed, same data."""
    rng = random.Random(SEED)
    b = _Builder(rng)
    by_day: dict[date, list[tuple]] = {}
    for event in EVENTS:
        by_day.setdefault(event[2], []).append(event)
    busy = _events(b, by_day)
    day = START
    while day <= END:
        _daily(b, day, skip_big_shop=day in busy)
        day += timedelta(days=1)
    receipts = sorted(b.receipts, key=lambda r: (r.day, r.merchant))
    events = [
        {"date": d, "title": t, "kind": k, "guests": g, "hosted": h, "source": "demo"}
        for t, k, d, g, h in EVENTS
    ]
    return receipts, events


def csv_texts() -> dict[str, str]:
    """The demo data as CSV (samples/demo_receipts.csv: one row per line item; samples/demo_events.csv)."""
    receipts, events = generate()
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["receipt", "date", "merchant", "item", "amount", "category", "receipt_total"])
    for n, r in enumerate(receipts, 1):
        for item, cents, category in r.items:
            w.writerow([n, r.day.isoformat(), r.merchant, item, f"{cents / 100:.2f}", category, f"{r.total_cents / 100:.2f}"])
    ev = io.StringIO()
    w = csv.writer(ev, lineterminator="\n")
    w.writerow(["date", "title", "kind", "guests", "hosted"])
    for e in events:
        w.writerow([e["date"].isoformat(), e["title"], e["kind"], e["guests"], "yes" if e["hosted"] else "no"])
    return {"demo_receipts.csv": out.getvalue(), "demo_events.csv": ev.getvalue()}


def _signature(receipts: list[DemoReceipt], events: list[dict]) -> tuple:
    return (
        len(receipts),
        sum(r.total_cents for r in receipts),
        sum(len(r.items) for r in receipts),
        len(events),
        sum(e["date"].toordinal() + e["guests"] for e in events),
    )


def _stored_signature(conn) -> tuple:
    from app.db import events, line_items, receipts

    count, total = conn.execute(
        select(func.count(receipts.c.id), func.coalesce(func.sum(receipts.c.total_cents), 0)).where(
            receipts.c.source == "demo"
        )
    ).one()
    n_items = conn.execute(
        select(func.count(line_items.c.id)).where(
            line_items.c.receipt_id.in_(select(receipts.c.id).where(receipts.c.source == "demo"))
        )
    ).scalar_one()
    stored = conn.execute(select(events.c.date, events.c.guests).where(events.c.source == "demo")).all()
    return (count, int(total), n_items, len(stored), sum(d.toordinal() + (g or 0) for d, g in stored))


def seed_demo(engine: Engine) -> bool:
    """Put the demo household in the database. Returns True if rows were written.

    Idempotent: if the same demo data is already there, nothing happens. If the generator
    changed, only demo rows are replaced; receipts and events from chat are never touched.
    """
    from app.db import events, get_settings, line_items, receipts, update_settings

    demo_receipts, demo_events = generate()
    wanted = _signature(demo_receipts, demo_events)
    with engine.begin() as conn:
        if _stored_signature(conn) == wanted:
            written = False
        else:
            demo_ids = select(receipts.c.id).where(receipts.c.source == "demo")
            conn.execute(delete(line_items).where(line_items.c.receipt_id.in_(demo_ids)))
            conn.execute(delete(receipts).where(receipts.c.source == "demo"))
            conn.execute(delete(events).where(events.c.source == "demo"))
            ids = conn.execute(
                insert(receipts).returning(receipts.c.id, sort_by_parameter_order=True),
                [
                    {"date": r.day, "merchant": r.merchant, "total_cents": r.total_cents, "status": "filed", "source": "demo"}
                    for r in demo_receipts
                ],
            ).scalars().all()
            conn.execute(
                insert(line_items),
                [
                    {"receipt_id": rid, "item": item, "amount_cents": cents, "category": category}
                    for rid, r in zip(ids, demo_receipts)
                    for item, cents, category in r.items
                ],
            )
            conn.execute(insert(events), demo_events)
            written = True
    if get_settings(engine).get("budget_cents") is None:
        update_settings(engine, budget_cents=BUDGET_CENTS, payday=PAYDAY)
    return written
