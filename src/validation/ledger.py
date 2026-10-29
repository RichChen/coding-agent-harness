"""Validation slots and byte reservations; no model ledger or runtime clock reset."""
from __future__ import annotations

from dataclasses import dataclass, field
from src.workspace.contracts import ContractError


class BudgetRefusal(RuntimeError):
    pass


@dataclass
class ValidationLedger:
    max_baseline: int = 1
    max_candidates: int = 3
    max_feedback: int = 2
    operations: dict = field(default_factory=dict)
    feedback: list = field(default_factory=list)

    def __post_init__(self):
        if any(type(v) is not int or v < 0 for v in
               (self.max_baseline, self.max_candidates, self.max_feedback)) or self.max_candidates < 1:
            raise ContractError("invalid validation limits")

    def reserve(self, operation_id, trigger):
        if operation_id in self.operations:
            raise ContractError("operation already reserved; reconcile instead of replaying")
        baseline = trigger == "baseline"
        used = sum(v["baseline"] == baseline and v["state"] != "released"
                   for v in self.operations.values())
        limit = self.max_baseline if baseline else self.max_candidates
        if used >= limit:
            raise BudgetRefusal("baseline_limit" if baseline else "validation_limit")
        final_slot = not baseline and used == limit - 1
        if final_slot and trigger == "explicit_tool":
            raise BudgetRefusal("final_slot_reserved")
        self.operations[operation_id] = dict(baseline=baseline, final_slot=final_slot, state="reserved")
        return dict(self.operations[operation_id])

    def start(self, operation_id):
        op = self.operations[operation_id]
        if op["state"] != "reserved":
            raise ContractError("validation can start exactly once")
        op["state"] = "started"

    def settle(self, operation_id):
        op = self.operations[operation_id]
        if op["state"] not in ("reserved", "started"):
            raise ContractError("validation already settled")
        op["state"] = "settled" if op["state"] == "started" else "released"

    @property
    def remaining_candidates(self):
        return self.max_candidates - sum(not v["baseline"] and v["state"] != "released"
                                         for v in self.operations.values())

    def note_feedback(self, record):
        op = self.operations.get(record.operation_id)
        if (record.status != "behavior_failed" or not record.complete or op is None
                or op["baseline"] or op["final_slot"] or op["state"] != "settled"
                or not self.remaining_candidates or len(self.feedback) >= self.max_feedback
                or record.record_id in self.feedback):
            raise BudgetRefusal("feedback_unavailable")
        # P3 additionally checks model/context/time availability before committing feedback.
        self.feedback.append(record.record_id)

    def snapshot(self):
        return dict(max_baseline=self.max_baseline, max_candidates=self.max_candidates,
                    max_feedback=self.max_feedback, operations={k: dict(v) for k, v in self.operations.items()},
                    feedback=list(self.feedback))

    @classmethod
    def restore(cls, snapshot):
        import copy
        result = cls(**copy.deepcopy(snapshot))
        if any(v.get("state") not in ("reserved", "started", "settled", "released")
               or type(v.get("baseline")) is not bool or type(v.get("final_slot")) is not bool
               for v in result.operations.values()):
            raise ContractError("invalid persisted validation reservation")
        for baseline, limit in ((True, result.max_baseline), (False, result.max_candidates)):
            if sum(v["baseline"] == baseline and v["state"] != "released"
                   for v in result.operations.values()) > limit:
                raise ContractError("persisted slot limit exceeded")
        if len(set(result.feedback)) != len(result.feedback) or len(result.feedback) > result.max_feedback:
            raise ContractError("invalid feedback ledger")
        return result


@dataclass
class ByteBudget:
    total: int
    final_reserve: int
    committed: int = 0
    held: dict = field(default_factory=dict)

    def __post_init__(self):
        if (any(type(v) is not int or v < 0 for v in (self.total, self.final_reserve, self.committed))
                or self.final_reserve > self.total):
            raise ContractError("invalid shared artifact budget")

    def reserve(self, operation_id, amount, *, final=False):
        if type(amount) is not int or amount < 0 or operation_id in self.held:
            raise ContractError("invalid byte reservation")
        available = self.total - self.committed - sum(self.held.values())
        if amount > available - (0 if final else self.final_reserve):
            raise BudgetRefusal("artifact_reserve" if not final else "artifact_limit")
        self.held[operation_id] = amount

    def settle(self, operation_id, amount):
        maximum = self.held[operation_id]
        if type(amount) is not int or not 0 <= amount <= maximum:
            raise ContractError("artifact producer exceeded reservation")
        self.committed += amount
        del self.held[operation_id]

    def snapshot(self):
        return dict(total=self.total, final_reserve=self.final_reserve,
                    committed=self.committed, held=dict(self.held))
