"""Backtest: window split, no leakage, metric math. Offline, synthetic data, fake predictor."""

import importlib.util
import math
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("backtest", ROOT / "scripts" / "backtest.py")
bt = importlib.util.module_from_spec(spec)
sys.modules["backtest"] = bt
spec.loader.exec_module(bt)

START = date(2026, 1, 1)


def synthetic(days: int = 70) -> dict[date, float]:
    """Made-up spending: 100 on weekdays, 300 on Saturdays, nothing on Sundays."""
    series = {}
    for i in range(days):
        d = START + timedelta(days=i)
        if d.weekday() != 6:
            series[d] = 300.0 if d.weekday() == 5 else 100.0
    return series


class Recorder:
    """A fake TabPFN: remembers what it was given and predicts the training mean +-50%."""

    def __init__(self, quantiles=True):
        self.calls = []
        self.quantiles = quantiles

    def __call__(self, X_train, y_train, X_future):
        self.calls.append((X_train, y_train, X_future))
        mean = sum(y_train) / len(y_train)
        mid = [mean] * len(X_future)
        if not self.quantiles:
            return None, mid, None
        return [mean * 0.5] * len(X_future), mid, [mean * 1.5] * len(X_future)


def test_split_takes_last_two_non_overlapping_windows():
    days = [START + timedelta(days=i) for i in range(63)]
    (s1, e1), (s2, e2) = bt.split_windows(days)
    assert e2 == days[-1]
    assert (e2 - s2).days + 1 == 14 and (e1 - s1).days + 1 == 14
    assert s2 == e1 + timedelta(days=1)
    assert s1 == days[-28]


def test_split_needs_enough_days():
    with pytest.raises(ValueError):
        bt.split_windows([START + timedelta(days=i) for i in range(28)])


def test_read_ledger_counts_each_receipt_once(tmp_path):
    path = tmp_path / "ledger.csv"
    path.write_text(
        "receipt_hash,file,purchase_date,merchant,item,amount,category,receipt_total,confidence,notes\n"
        "a,a.jpg,02/01/2026,Shop A,bread,10.0,food,30.0,high,\n"
        "a,a.jpg,02/01/2026,Shop A,milk,25.0,food,30.0,high,\n"
        "a,a.jpg,02/01/2026,Shop A,discount,-5.0,food,30.0,high,\n"
        "b,b.jpg,02/01/2026,Shop B,soap,12.5,home,12.5,high,\n"
        "c,c.jpg,05/01/2026,Shop C,tea,8.0,food,8.0,high,\n",
        encoding="utf-8",
    )
    series = bt.read_ledger(path)
    assert series == {date(2026, 1, 2): 42.5, date(2026, 1, 5): 8.0}
    days = bt.full_days(series)
    assert len(days) == 4  # empty days in between are included


def test_metric_math():
    actual = [100.0, 0.0, 300.0, 100.0]
    pred = [150.0, 50.0, 200.0, 100.0]
    assert bt.total_error(pred, actual) == pytest.approx(0.0)
    assert bt.wape(pred, actual) == pytest.approx((50 + 50 + 100 + 0) / 500 * 100)
    assert bt.total_error([250.0] * 4, actual) == pytest.approx(100.0)
    assert bt.in_range([0, 0, 0, 0], [120, 10, 250, 100], actual) == pytest.approx(75.0)


def test_no_leakage_from_the_window():
    series = synthetic()
    (start, end), _ = bt.split_windows(bt.full_days(series))
    first, second = Recorder(), Recorder()
    bt.run_window(series, start, end, predict=first)
    changed = dict(series)
    for i in range((end - start).days + 1):
        changed[start + timedelta(days=i)] = 99999.0
    changed[end + timedelta(days=3)] = 99999.0
    bt.run_window(changed, start, end, predict=second)
    (X1, y1, F1), (X2, y2, F2) = first.calls[0], second.calls[0]
    same = lambda a, b: all(x == y or (math.isnan(x) and math.isnan(y)) for r, s in zip(a, b) for x, y in zip(r, s))
    assert y1 == y2 and same(X1, X2) and same(F1, F2)
    assert len(y1) == (start - min(series)).days  # trains on every day before the window
    assert len(F1) == 14


def test_backtest_uses_the_bot_features_and_the_baseline():
    series = synthetic()
    fake = Recorder()
    results = bt.backtest(series, predict=fake)
    assert len(results) == 2 and len(fake.calls) == 2
    X_train, y_train, X_future = fake.calls[0]
    assert all(len(row) == len(bt.fc.FEATURES) for row in X_train + X_future)
    assert results[1].train_days == results[0].train_days + 14
    # The fake predicts the training mean, which is the baseline: both errors must match.
    for r in results:
        assert r.total_error == pytest.approx(r.total_error_base)
        assert r.wape == pytest.approx(r.wape_base)
        assert r.in_range is not None and 0 <= r.in_range <= 100


def test_report_prints_percentages_only_and_handles_no_quantiles():
    series = synthetic()
    results = bt.backtest(series, predict=Recorder(quantiles=False))
    text = bt.report(results, receipt_days=len(series), ledger_days=len(bt.full_days(series)))
    assert "n/a" in text and "Quantiles did not work" in text
    assert "a tie" in text
    assert "2026" not in text and "₺" not in text
    assert all(r.in_range is None for r in results)


def test_verdict_names_the_winner():
    r = bt.Result(train_days=0, window_days=14, total_error=10, total_error_base=20, wape=50, wape_base=40, in_range=None)
    text = bt.verdict(r)
    assert "14-day total: TabPFN wins" in text and "Daily WAPE: the average day wins" in text


def test_main_needs_a_ledger(monkeypatch):
    monkeypatch.delenv("LEDGER_CSV", raising=False)
    with pytest.raises(SystemExit):
        bt.main([])
