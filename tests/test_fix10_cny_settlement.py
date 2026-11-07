"""CNY ledger settlement at provider usage. The default stays the cache-miss settlement."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.policy.cny_budget import A5BudgetPlan, CnyBudgetError, CnyBudgetLedger, PriceSnapshot

BEIJING = timezone(timedelta(hours=8))
PRICE = PriceSnapshot(provider="DeepSeek", model="deepseek-flash", snapshot_id="deepseek-flash@test", currency="CNY",
                      prompt_cache_hit_per_million=Decimal("0.040"), prompt_cache_miss_per_million=Decimal("2.000"),
                      completion_per_million=Decimal("8.000"), source_url="https://example.invalid/pricing",
                      retrieved_on="2026-11-07", verified=True)
PLAN = A5BudgetPlan(total_cny=Decimal("1.000"), build_cny=Decimal("0.000"), pilot_cny=Decimal("1.000"),
                    reserve_cny=Decimal("0.000"), build_calls=0, pilot_calls=1000)
USAGE = {"prompt_tokens": 100_000, "completion_tokens": 1_000, "total_tokens": 101_000,
         "prompt_cache_hit_tokens": 90_000, "prompt_cache_miss_tokens": 10_000,
         "completion_tokens_details": {"reasoning_tokens": 400}, "provenance": "provider"}


def ledger(**options):
    return CnyBudgetLedger(PRICE, PLAN, **options)


def settle(book, usage=USAGE):
    reservation = book.reserve(phase="pilot", operation_id="op", component="agent", prompt_tokens=100_000,
                               max_output_tokens=2_048)
    return reservation, book.settle(reservation.reservation_id, usage)


def test_default_settles_every_prompt_token_at_the_miss_price():
    book = ledger()
    _, row = settle(book)
    assert row["cost_cny"] == "0.208"  # 100k * 2 + 1k * 8 per million
    assert "settlement" not in book.snapshot()
    assert set(row) == {"reservation_id", "phase", "operation_id", "component", "status", "cost_cny",
                        "prompt_tokens", "completion_tokens"}


def test_provider_usage_prices_hit_miss_and_output_separately():
    book = ledger(settlement="provider_usage")
    reservation, row = settle(book)
    assert reservation.reserved_cny == Decimal("0.216")  # reservation still at the miss price
    assert Decimal(row["cost_cny"]) == Decimal("0.031600")  # 90k*0.04 + 10k*2 + 1k*8 per million
    assert row["prompt_cache_hit_tokens"] == 90_000 and row["prompt_cache_miss_tokens"] == 10_000
    assert row["reasoning_tokens"] == 400
    assert book.cost_committed == Decimal("0.031600")
    assert book.snapshot()["settlement"] == {"mode": "provider_usage", "time_of_day": False}


def test_provider_usage_without_a_split_counts_all_prompt_tokens_as_misses():
    book = ledger(settlement="provider_usage")
    usage = {k: v for k, v in USAGE.items() if not k.startswith("prompt_cache")}
    _, row = settle(book, usage)
    assert Decimal(row["cost_cny"]) == Decimal("0.208000")
    assert row["split"] == "missing_all_miss"


def test_provider_usage_derives_misses_from_prompt_minus_hits():
    book = ledger(settlement="provider_usage")
    usage = {k: v for k, v in USAGE.items() if k != "prompt_cache_miss_tokens"}
    _, row = settle(book, usage)
    assert row["prompt_cache_miss_tokens"] == 10_000
    assert Decimal(row["cost_cny"]) == Decimal("0.031600")


def test_unknown_usage_keeps_the_reservation_in_both_modes():
    for options in ({}, {"settlement": "provider_usage"}):
        book = ledger(**options)
        reservation, row = settle(book, None)
        assert row["status"] == "unknown_keeps_reserve"
        assert book.cost_committed == reservation.reserved_cny


def test_cap_is_judged_on_the_settled_amount():
    legacy, actual = ledger(), ledger(settlement="provider_usage")
    counts = {}
    for name, book in (("legacy", legacy), ("actual", actual)):
        calls = 0
        while True:
            try:
                settle(book)
            except CnyBudgetError:
                break
            calls += 1
        counts[name] = calls
    assert counts["legacy"] == 4  # 4 * 0.208 + next reservation 0.216 > 1
    assert counts["actual"] == 25  # 24 * 0.0316 + 0.216 <= 1 < 25 * 0.0316 + 0.216


@pytest.mark.parametrize("moment, factor", [
    (datetime(2026, 11, 9, 10, 0, tzinfo=BEIJING), Decimal(1)),     # Monday 10:00 peak
    (datetime(2026, 11, 9, 13, 0, tzinfo=BEIJING), Decimal("0.5")),  # Monday lunch
    (datetime(2026, 11, 9, 18, 0, tzinfo=BEIJING), Decimal("0.5")),  # 18:00 ends the peak
    (datetime(2026, 11, 7, 10, 0, tzinfo=BEIJING), Decimal("0.5")),  # Saturday
])
def test_time_of_day_uses_the_reservation_time(moment, factor):
    book = ledger(settlement="provider_usage", time_of_day=True, clock=lambda: moment.astimezone(timezone.utc))
    _, row = settle(book)
    assert Decimal(row["cost_cny"]) == Decimal("0.031600") * factor
    assert row["price_period"] == ("peak" if factor == 1 else "off_peak")


def test_unknown_settlement_mode_is_rejected():
    with pytest.raises(CnyBudgetError):
        ledger(settlement="cache_hit")
    with pytest.raises(CnyBudgetError):
        ledger(time_of_day=True)
