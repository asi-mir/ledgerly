"""Write the synthetic demo household to samples/demo_*.csv and print a short summary.

    .venv/bin/python scripts/make_demo_data.py

Everything is made up (fixed seed). The bot seeds the same data on boot when DEMO_TODAY is set.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.demo import BUDGET_CENTS, csv_texts, generate  # noqa: E402
from app.money import format_cents  # noqa: E402


def main() -> None:
    for name, text in csv_texts().items():
        (ROOT / "samples" / name).write_text(text, encoding="utf-8")
        print(f"wrote samples/{name}")
    receipts, events = generate()
    months: dict[tuple[int, int], int] = defaultdict(int)
    for r in receipts:
        months[(r.day.year, r.day.month)] += r.total_cents
    print(f"{len(receipts)} receipts, {sum(len(r.items) for r in receipts)} line items, {len(events)} events")
    for (year, month), cents in sorted(months.items()):
        flag = "  over budget" if cents > BUDGET_CENTS else ""
        print(f"{year}-{month:02d}  {format_cents(cents, decimals=False):>9}{flag}")
    total = 0
    for r in sorted((r for r in receipts if r.day.year == 2025 and r.day.month == 12), key=lambda r: r.day):
        total += r.total_cents
        if total > BUDGET_CENTS:
            print(f"Dec 2025 passes the budget on {r.day:%b} {r.day.day}" + (" (as in the story)" if r.day == date(2025, 12, 19) else ""))
            break


if __name__ == "__main__":
    main()
