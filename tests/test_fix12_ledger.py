"""Per-run CNY cap and call cap of make_ledger; refused reservations are kept in the ledger file."""
import json
from decimal import Decimal

import pytest

from scripts.run_swe_task import make_ledger
from src.policy.cny_budget import CnyBudgetError


def test_defaults_are_the_sprint_cap(tmp_path):
    ledger = make_ledger(None)
    assert ledger.plan.total_cny == ledger.plan.pilot_cny == Decimal("1.000") and ledger.plan.pilot_calls == 40
    assert ledger.settlement == "cache_miss" and ledger.time_of_day is False


def test_pilot_cap_and_calls(tmp_path):
    ledger = make_ledger(tmp_path / "l.json", settlement="provider_usage", run_cap_cny="4.000", max_calls=100)
    assert ledger.plan.total_cny == Decimal("4.000") and ledger.plan.pilot_calls == 100
    for i in range(100):
        ledger.settle(ledger.reserve(phase="pilot", operation_id=str(i), component="agent", prompt_tokens=10,
                                     max_output_tokens=10).reservation_id,
                      {"prompt_tokens": 10, "completion_tokens": 1, "prompt_cache_hit_tokens": 0,
                       "prompt_cache_miss_tokens": 10})
    with pytest.raises(CnyBudgetError) as caught:
        ledger.reserve(phase="pilot", operation_id="x", component="agent", prompt_tokens=1, max_output_tokens=1)
    assert caught.value.reason == "cny_call_cap"


def test_refused_reservation_is_recorded(tmp_path):
    path = tmp_path / "l.json"
    ledger = make_ledger(path, settlement="provider_usage", run_cap_cny="4.000", max_calls=100)
    ledger.reserve(phase="pilot", operation_id="a", component="agent", prompt_tokens=1_000_000, max_output_tokens=32768)
    assert "rejections" not in json.loads(path.read_text(encoding="utf-8"))
    with pytest.raises(CnyBudgetError) as caught:
        ledger.reserve(phase="pilot", operation_id="b", component="agent", prompt_tokens=900_000,
                       max_output_tokens=32768)
    assert caught.value.reason == "cny_hard_cap" and str(caught.value) == "CNY hard cap would be exceeded"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["rejections"] == [{"phase": "pilot", "operation_id": "b", "component": "agent",
                                   "reserve_cny": "2.062", "committed_cny": "2.262",
                                   "prompt_tokens": 900_000, "max_output_tokens": 32768}]
