"""Fail-closed A5 global CNY ledger.

Every model attempt, including retries/builders/routers/verifiers, must reserve
against this ledger before starting. Unknown usage keeps its reservation.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

CENT = Decimal("0.001")
MICRO = Decimal("0.000001")
SETTLEMENTS = ("cache_miss", "provider_usage")
BEIJING = timezone(timedelta(hours=8))
# DeepSeek pricing page: Beijing time, Monday to Friday, 9:00-12:00 and 14:00-18:00 are peak; off-peak is half price.
# Chinese public holidays are off-peak too; they are not modelled, so a holiday weekday is priced as peak.
PEAK_HOURS = ((9, 12), (14, 18))
OFF_PEAK_FACTOR = Decimal("0.5")


def price_period(moment: datetime) -> str:
    local = moment.astimezone(BEIJING)
    if local.weekday() < 5 and any(start <= local.hour < end for start, end in PEAK_HOURS):
        return "peak"
    return "off_peak"


def _count(value) -> int | None:
    return value if type(value) is int and value >= 0 else None


def provider_cost(price, usage: dict, period: str = "peak") -> dict:
    """Price one call from provider usage. completion_tokens already includes reasoning tokens.

    Without a hit/miss split every prompt token is a miss; a missing or inconsistent miss count is prompt - hit."""
    prompt, completion = usage["prompt_tokens"], usage["completion_tokens"]
    hit, miss = _count(usage.get("prompt_cache_hit_tokens")), _count(usage.get("prompt_cache_miss_tokens"))
    if hit is not None and hit <= prompt and (miss is None or hit + miss != prompt):
        miss, split = prompt - hit, "miss_from_prompt" if miss is None else "miss_from_prompt_mismatch"
    elif hit is not None and miss is not None and hit + miss == prompt:
        split = "provider"
    else:
        hit, miss, split = 0, prompt, "missing_all_miss"
    if period not in ("peak", "off_peak"):
        raise ValueError("unknown price period: " + repr(period))
    factor = Decimal(1) if period == "peak" else OFF_PEAK_FACTOR
    cost = (Decimal(hit) * price.prompt_cache_hit_per_million
            + Decimal(miss) * price.prompt_cache_miss_per_million
            + Decimal(completion) * price.completion_per_million) * factor / Decimal(1_000_000)
    details = usage.get("completion_tokens_details")
    reasoning = _count(details.get("reasoning_tokens")) if isinstance(details, dict) else None
    return {"cost_cny": cost.quantize(MICRO), "hit": hit, "miss": miss, "reasoning": reasoning, "split": split}


def money(value) -> Decimal:
    try:
        result = Decimal(str(value)).quantize(CENT)
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("invalid CNY amount")
    if result < 0:
        raise ValueError("negative CNY amount")
    return result


@dataclass(frozen=True)
class PriceSnapshot:
    provider: str
    model: str
    snapshot_id: str
    currency: str
    prompt_cache_hit_per_million: Decimal
    prompt_cache_miss_per_million: Decimal
    completion_per_million: Decimal
    source_url: str
    retrieved_on: str
    verified: bool = False

    def __post_init__(self):
        if not self.provider or not self.model or not self.snapshot_id or self.currency != "CNY":
            raise ValueError("provider/model/snapshot/currency binding required")
        for value in (self.prompt_cache_hit_per_million, self.prompt_cache_miss_per_million, self.completion_per_million):
            if money(value) != value:
                raise ValueError("prices must be non-negative milli-CNY decimals")
        if not self.source_url or not self.retrieved_on:
            raise ValueError("price provenance required")

    def to_dict(self):
        return {"provider": self.provider, "model": self.model, "snapshot_id": self.snapshot_id,
            "currency": self.currency, "prompt_cache_hit_per_million": str(self.prompt_cache_hit_per_million),
            "prompt_cache_miss_per_million": str(self.prompt_cache_miss_per_million),
            "completion_per_million": str(self.completion_per_million), "source_url": self.source_url,
            "retrieved_on": self.retrieved_on, "verified": self.verified}

    @property
    def binding_hash(self):
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class A5BudgetPlan:
    total_cny: Decimal = Decimal("50.000")
    build_cny: Decimal = Decimal("3.840")
    pilot_cny: Decimal = Decimal("46.080")
    reserve_cny: Decimal = Decimal("0.080")
    build_calls: int = 24
    pilot_calls: int = 1440

    def __post_init__(self):
        if money(self.total_cny) != self.total_cny or self.build_cny + self.pilot_cny + self.reserve_cny != self.total_cny:
            raise ValueError("A5 CNY plan arithmetic mismatch")


@dataclass
class CnyReservation:
    reservation_id: int
    phase: str
    operation_id: str
    component: str
    reserved_cny: Decimal
    prompt_tokens: int
    max_output_tokens: int
    settled: bool = False
    reserved_at: datetime | None = None


class CnyBudgetError(RuntimeError):
    def __init__(self, message, reason="cny_budget_error"):
        super().__init__(message)
        self.reason = reason


@dataclass
class CnyBudgetLedger:
    price: PriceSnapshot
    plan: A5BudgetPlan = field(default_factory=A5BudgetPlan)
    calls_started: int = 0
    cost_committed: Decimal = Decimal("0.000")
    phase_cost: dict[str, Decimal] = field(default_factory=lambda: {"build": Decimal("0.000"), "pilot": Decimal("0.000")})
    phase_calls: dict[str, int] = field(default_factory=lambda: {"build": 0, "pilot": 0})
    outstanding: dict[int, CnyReservation] = field(default_factory=dict)
    settlements: list[dict[str, Any]] = field(default_factory=list)
    _next_id: int = 0
    state_path: Path | None = field(default=None, repr=False, compare=False)
    # cache_miss: every prompt token at the miss price (the original ledger). provider_usage: hit, miss and
    # output tokens priced separately from the provider's usage; reservations stay at the miss price.
    settlement: str = "cache_miss"
    time_of_day: bool = False
    clock: Callable[[], datetime] | None = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if not self.price.verified:
            raise CnyBudgetError("model snapshot/current CNY price is not verified")
        if self.settlement not in SETTLEMENTS:
            raise CnyBudgetError("unknown settlement mode: " + repr(self.settlement))
        if self.time_of_day and self.settlement != "provider_usage":
            raise CnyBudgetError("time_of_day pricing needs settlement='provider_usage'")

    def _now(self) -> datetime:
        return self.clock() if self.clock is not None else datetime.now(timezone.utc)

    def _cap(self, phase):
        if phase not in ("build", "pilot"):
            raise CnyBudgetError("phase must be build or pilot")
        return self.plan.build_cny if phase == "build" else self.plan.pilot_cny

    def estimate(self, prompt_tokens, max_output_tokens):
        if any(type(x) is not int or x < 0 for x in (prompt_tokens, max_output_tokens)):
            raise CnyBudgetError("token estimates must be non-negative integers")
        # Reserve at the cache-miss price; cache savings never buy extra runs.
        return money((Decimal(prompt_tokens) * self.price.prompt_cache_miss_per_million +
                      Decimal(max_output_tokens) * self.price.completion_per_million) / Decimal(1_000_000))

    def reserve(self, *, phase, operation_id, component, prompt_tokens, max_output_tokens):
        if not operation_id or not component:
            raise CnyBudgetError("operation_id and component are required")
        if phase not in self.phase_calls:
            raise CnyBudgetError("invalid phase")
        cap_calls = self.plan.build_calls if phase == "build" else self.plan.pilot_calls
        if self.phase_calls[phase] >= cap_calls:
            raise CnyBudgetError(f"{phase} call cap reached", reason="cny_call_cap")
        amount = self.estimate(prompt_tokens, max_output_tokens)
        if self.phase_cost[phase] + amount > self._cap(phase) or self.cost_committed + amount > self.plan.total_cny - self.plan.reserve_cny:
            raise CnyBudgetError("CNY hard cap would be exceeded", reason="cny_hard_cap")
        self._next_id += 1
        reservation = CnyReservation(self._next_id, phase, operation_id, component, amount, prompt_tokens, max_output_tokens,
                                     reserved_at=self._now() if self.time_of_day else None)
        self.outstanding[self._next_id] = reservation
        self.phase_cost[phase] += amount; self.cost_committed += amount; self.phase_calls[phase] += 1; self.calls_started += 1
        self.persist()
        return reservation

    def settle(self, reservation_id: int, usage: dict[str, Any] | None = None):
        reservation = self.outstanding.pop(reservation_id, None)
        if reservation is None:
            raise CnyBudgetError("unknown reservation")
        usage = usage if isinstance(usage, dict) else {}
        prompt = usage.get("prompt_tokens"); completion = usage.get("completion_tokens")
        known = type(prompt) is int and prompt >= 0 and type(completion) is int and completion >= 0
        if known and self.settlement == "provider_usage":
            row = self._provider_row(reservation, usage, prompt, completion)
            delta = Decimal(row["cost_cny"]) - reservation.reserved_cny
            self.phase_cost[reservation.phase] += delta; self.cost_committed += delta
        elif known:
            actual = money((Decimal(prompt) * self.price.prompt_cache_miss_per_million + Decimal(completion) * self.price.completion_per_million) / Decimal(1_000_000))
            delta = actual - reservation.reserved_cny
            self.phase_cost[reservation.phase] += delta; self.cost_committed += delta
            row = {"reservation_id": reservation_id, "phase": reservation.phase, "operation_id": reservation.operation_id,
                   "component": reservation.component, "status": "provider_usage", "cost_cny": str(actual),
                   "prompt_tokens": prompt, "completion_tokens": completion}
        else:
            row = {"reservation_id": reservation_id, "phase": reservation.phase, "operation_id": reservation.operation_id,
                   "component": reservation.component, "status": "unknown_keeps_reserve", "cost_cny": str(reservation.reserved_cny)}
        reservation.settled = True; self.settlements.append(row)
        self.persist()
        return row

    def _provider_row(self, reservation, usage, prompt, completion):
        period = price_period(reservation.reserved_at or self._now()) if self.time_of_day else "peak"
        priced = provider_cost(self.price, usage, period)
        return {"reservation_id": reservation.reservation_id, "phase": reservation.phase,
                "operation_id": reservation.operation_id, "component": reservation.component,
                "status": "provider_usage", "cost_cny": str(priced["cost_cny"]), "prompt_tokens": prompt,
                "completion_tokens": completion, "prompt_cache_hit_tokens": priced["hit"],
                "prompt_cache_miss_tokens": priced["miss"], "reasoning_tokens": priced["reasoning"],
                "split": priced["split"], "price_period": period, "reserved_cny": str(reservation.reserved_cny)}

    def persist(self):
        """Persist the conservative ledger after every reservation/settlement."""
        if self.state_path is None:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.snapshot(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    def snapshot(self):
        data = {"schema": "a5.cny.ledger.v1", "currency": "CNY", "price_binding_hash": self.price.binding_hash,
            "calls_started": self.calls_started, "cost_committed": str(self.cost_committed),
            "phase_calls": dict(self.phase_calls), "phase_cost": {k: str(v) for k, v in self.phase_cost.items()},
            "outstanding": len(self.outstanding), "settlements": list(self.settlements),
            "hard_caps": {"total": str(self.plan.total_cny), "build": str(self.plan.build_cny), "pilot": str(self.plan.pilot_cny), "reserve": str(self.plan.reserve_cny)}}
        if self.settlement != "cache_miss":
            data["settlement"] = {"mode": self.settlement, "time_of_day": self.time_of_day}
        return data
