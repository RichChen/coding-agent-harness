"""Fail-closed A5 global CNY ledger.

Every model attempt, including retries/builders/routers/verifiers, must reserve
against this ledger before starting. Unknown usage keeps its reservation.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

CENT = Decimal("0.001")


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

    def __post_init__(self):
        if not self.price.verified:
            raise CnyBudgetError("model snapshot/current CNY price is not verified")

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
        reservation = CnyReservation(self._next_id, phase, operation_id, component, amount, prompt_tokens, max_output_tokens)
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
        if known:
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

    def persist(self):
        """Persist the conservative ledger after every reservation/settlement."""
        if self.state_path is None:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.snapshot(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    def snapshot(self):
        return {"schema": "a5.cny.ledger.v1", "currency": "CNY", "price_binding_hash": self.price.binding_hash,
            "calls_started": self.calls_started, "cost_committed": str(self.cost_committed),
            "phase_calls": dict(self.phase_calls), "phase_cost": {k: str(v) for k, v in self.phase_cost.items()},
            "outstanding": len(self.outstanding), "settlements": list(self.settlements),
            "hard_caps": {"total": str(self.plan.total_cny), "build": str(self.plan.build_cny), "pilot": str(self.plan.pilot_cny), "reserve": str(self.plan.reserve_cny)}}
