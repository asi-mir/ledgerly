"""Backtest: hide the last 2 weeks of a ledger, forecast them, compare with an average day.

    LEDGER_CSV=path/to/ledger.csv .venv/bin/python scripts/backtest.py
    .venv/bin/python scripts/backtest.py --ledger path/to/ledger.csv

The ledger is a CSV with one row per line item (receipt_hash, purchase_date as DD/MM/YYYY,
receipt_total, ...). Each receipt counts once, by its total. Days with no receipts count as 0.

Two non-overlapping 14-day windows at the end of the ledger are forecast. For each window the
model trains on every day before it, with the same feature builder and the same TabPFN call as
the bot (app/forecast.py). Recent-spend features are frozen at the window start. The ledger has
no events, so the event columns are all zero.

The baseline is "an average day": the mean daily spend of the training days. A second, fairer
opponent is printed too: "a typical day", the median daily spend (a few huge days pull the mean up).
Only percentages and day counts are printed: no amounts, shops or dates.
"""

from __future__ import annotations

import argparse
import csv
import os
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import forecast as fc  # noqa: E402

WINDOW = 14
WINDOWS = 2


@dataclass
class Result:
    train_days: int
    window_days: int
    total_error: float  # % error of the window total, TabPFN
    total_error_base: float  # same for the average day
    wape: float  # daily WAPE %, TabPFN
    wape_base: float
    in_range: float | None  # % of days inside the 0.1-0.9 range, None if quantiles failed
    seconds: float | None = None
    total_error_median: float | None = None  # same for the typical (median) day
    wape_median: float | None = None


# ---------------------------------------------------------------- ledger


def _date(text: str) -> date:
    text = text.strip()
    try:
        return datetime.strptime(text, "%d/%m/%Y").date()
    except ValueError:
        return date.fromisoformat(text)


def read_ledger(path: str | Path) -> dict[date, float]:
    """Daily spend from a v1 ledger CSV. One receipt (receipt_hash) is counted once."""
    totals: dict[str, tuple[date, float]] = {}
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            key = row["receipt_hash"].strip() or row.get("file", "")
            if key in totals or not row["purchase_date"].strip():
                continue
            totals[key] = (_date(row["purchase_date"]), float(row["receipt_total"]))
    series: dict[date, float] = {}
    for day, total in totals.values():
        series[day] = series.get(day, 0.0) + total
    return series


def full_days(series: dict[date, float]) -> list[date]:
    """Every day from the first to the last receipt (empty days included)."""
    first, last = min(series), max(series)
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def split_windows(days: list[date], window: int = WINDOW, count: int = WINDOWS) -> list[tuple[date, date]]:
    """The last `count` non-overlapping windows, oldest first, as (start, end) inclusive."""
    if len(days) < window * count + 1:
        raise ValueError(f"need more than {window * count} days of ledger, got {len(days)}")
    out = []
    for k in range(count, 0, -1):
        end_index = len(days) - (k - 1) * window - 1
        out.append((days[end_index - window + 1], days[end_index]))
    return out


# ---------------------------------------------------------------- metrics


def total_error(pred: list[float], actual: list[float]) -> float:
    """|predicted total - actual total| / actual total, in %."""
    return abs(sum(pred) - sum(actual)) / sum(actual) * 100


def wape(pred: list[float], actual: list[float]) -> float:
    """Weighted absolute percentage error over days: sum |p - a| / sum a, in %."""
    return sum(abs(p - a) for p, a in zip(pred, actual)) / sum(actual) * 100


def in_range(low: list[float], high: list[float], actual: list[float]) -> float:
    """% of days whose actual spend is inside [low, high]."""
    return sum(1 for lo, hi, a in zip(low, high, actual) if lo <= a <= hi) / len(actual) * 100


# ---------------------------------------------------------------- backtest


def run_window(series: dict[date, float], start: date, end: date, payday: int = 1, predict=None) -> Result:
    """Forecast [start, end] from the days before `start` only."""
    predict = predict or fc._predict
    first_day = min(series)
    known = {d: v for d, v in series.items() if d < start}  # nothing from the window leaks in
    window = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    _, X_train, y_train = fc.training_table(known, [], payday, first_day, start)
    X_future = fc.build_rows(window, known, [], payday, first_day, freeze=start)

    started = time.perf_counter()
    low, mid, high = predict(X_train, y_train, X_future)
    seconds = round(time.perf_counter() - started, 2)
    mid = [max(0.0, v) for v in mid]
    if low is not None and high is not None:
        trios = [sorted(max(0.0, v) for v in trio) for trio in zip(low, mid, high)]
        low, high = [t[0] for t in trios], [t[2] for t in trios]

    actual = [series.get(d, 0.0) for d in window]
    average = sum(y_train) / len(y_train)
    base = [average] * len(window)
    typical = [statistics.median(y_train)] * len(window)
    return Result(
        train_days=len(y_train),
        window_days=len(window),
        total_error=total_error(mid, actual),
        total_error_base=total_error(base, actual),
        wape=wape(mid, actual),
        wape_base=wape(base, actual),
        in_range=in_range(low, high, actual) if low is not None and high is not None else None,
        seconds=seconds,
        total_error_median=total_error(typical, actual),
        wape_median=wape(typical, actual),
    )


def backtest(series: dict[date, float], payday: int = 1, predict=None) -> list[Result]:
    return [run_window(series, s, e, payday, predict) for s, e in split_windows(full_days(series))]


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def report(results: list[Result], receipt_days: int, ledger_days: int) -> str:
    def pct(v: float | None) -> str:
        return "n/a" if v is None else f"{v:.1f}%"

    lines = [
        f"Ledger: {ledger_days} days, {receipt_days} with receipts. "
        f"{len(results)} windows of {results[0].window_days} days.",
        "",
        f"{'window':<8}{'train days':>11}{'total TabPFN':>14}{'total avg':>11}{'WAPE TabPFN':>13}{'WAPE avg':>10}{'in range':>10}",
    ]
    for i, r in enumerate(results, 1):
        lines.append(
            f"{i:<8}{r.train_days:>11}{pct(r.total_error):>14}{pct(r.total_error_base):>11}"
            f"{pct(r.wape):>13}{pct(r.wape_base):>10}{pct(r.in_range):>10}"
        )
    ranges = [r.in_range for r in results]
    avg = Result(
        train_days=0,
        window_days=results[0].window_days,
        total_error=_mean([r.total_error for r in results]),
        total_error_base=_mean([r.total_error_base for r in results]),
        wape=_mean([r.wape for r in results]),
        wape_base=_mean([r.wape_base for r in results]),
        in_range=_mean(ranges) if all(v is not None for v in ranges) else None,
    )
    medians = [r.total_error_median for r in results]
    if all(v is not None for v in medians):
        avg.total_error_median = _mean(medians)
        avg.wape_median = _mean([r.wape_median for r in results])
    lines.append(
        f"{'average':<8}{'':>11}{pct(avg.total_error):>14}{pct(avg.total_error_base):>11}"
        f"{pct(avg.wape):>13}{pct(avg.wape_base):>10}{pct(avg.in_range):>10}"
    )
    if avg.total_error_median is not None:
        per = ", ".join(f"{r.total_error_median:.1f}%" for r in results)
        lines.append(
            f"Typical day (median) baseline: total error {per}, average {avg.total_error_median:.1f}%; "
            f"daily WAPE average {avg.wape_median:.1f}%."
        )
    lines.append("")
    lines.append(verdict(avg))
    if avg.in_range is None:
        lines.append("Quantiles did not work, so there is no range check.")
    seconds = [r.seconds for r in results if r.seconds is not None]
    if seconds:
        lines.append(f"TabPFN fit + predict: {', '.join(f'{s:.1f} s' for s in seconds)}.")
    return "\n".join(lines)


def verdict(avg: Result) -> str:
    def who(model: float, base: float) -> str:
        if abs(model - base) < 0.05:
            return "a tie"
        return "TabPFN wins" if model < base else "the average day wins"

    text = (
        f"14-day total: {who(avg.total_error, avg.total_error_base)} "
        f"({avg.total_error:.1f}% vs {avg.total_error_base:.1f}% error). "
        f"Daily WAPE: {who(avg.wape, avg.wape_base)} ({avg.wape:.1f}% vs {avg.wape_base:.1f}%)."
    )
    if avg.total_error_median is not None:
        typical = who(avg.total_error, avg.total_error_median).replace("the average day", "the typical day")
        text += f" Against a typical day: {typical} ({avg.total_error:.1f}% vs {avg.total_error_median:.1f}%)."
    return text


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--ledger", default=os.environ.get("LEDGER_CSV"), help="ledger CSV (or env LEDGER_CSV)")
    parser.add_argument("--payday", type=int, default=1, help="day of month the pay period starts (default 1)")
    args = parser.parse_args(argv)
    if not args.ledger:
        parser.error("give --ledger PATH or set LEDGER_CSV")
    series = read_ledger(args.ledger)
    days = full_days(series)
    results = backtest(series, args.payday)
    print(report(results, receipt_days=sum(1 for v in series.values() if v), ledger_days=len(days)))


if __name__ == "__main__":
    main()
