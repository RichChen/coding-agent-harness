"""CR-2 r1: O-group list (gold first in hybrid order, P format) and the cache-priced cost report."""
import json
from decimal import Decimal

import pytest

from scripts import cr2_cost
from scripts import cr2_oracle_prefetch as oracle
from src.retrieval import serve
from tests.test_cr2_serve import ISSUE, setup  # noqa: F401  (fixture)


def _chunks_of(ix, path):
    return [cid for cid in ix.chunk_ids if cid.startswith(path + "::")]


def test_gold_first_then_hybrid_fillers(setup):  # noqa: F811
    service, encoder, snap, ix, vs, q, config = setup
    ti = service.task_index("o/r", q.base_commit)
    file_order, chunk_order, _ = ti.orders(ISSUE)
    ranked = [snap.paths[i] for i in file_order]
    gold_path = ranked[-1]
    gold_chunks = sorted(_chunks_of(ix, gold_path), key=lambda cid: -int(cid.rsplit("@", 1)[1]))
    hits, source, info = oracle.oracle_hits(ti, ISSUE, (gold_path,), tuple(gold_chunks))
    assert source == "cache" and info == {"files": 1, "listed": 1}
    assert [h.path for h in hits] == [gold_path] + [p for p in ranked if p != gold_path][:serve.PREFETCH_FILES - 1]
    starts = [c.start_line for c in hits[0].chunks]
    assert starts == sorted(starts) and len(hits[0].chunks) == min(2, len(gold_chunks))
    p_hits, _ = ti.search(ISSUE, serve.PREFETCH_FILES)
    p_chunks = {h.path: h.chunks for h in p_hits}
    for hit in hits[1:]:
        assert hit.chunks == p_chunks.get(hit.path, hit.chunks)
    text = serve.render_prefetch(hits)
    assert text.startswith(serve.PREFETCH_HEADER + "\n\n1. " + gold_path) and text.endswith(serve.PREFETCH_FOOTER)
    assert "oracle" not in text.lower() and "gold" not in text.lower()


def test_without_gold_equals_p_list(setup):  # noqa: F811
    service, encoder, snap, ix, vs, q, config = setup
    ti = service.task_index("o/r", q.base_commit)
    hits, _, info = oracle.oracle_hits(ti, ISSUE, (), ())
    p_hits, _ = ti.search(ISSUE, serve.PREFETCH_FILES)
    assert hits == p_hits and info == {"files": 0, "listed": 0}


def test_more_than_five_gold_files_are_capped(setup):  # noqa: F811
    service, encoder, snap, ix, vs, q, config = setup
    ti = service.task_index("o/r", q.base_commit)
    gold = tuple(snap.paths)
    chunks = tuple(cid for p in gold for cid in _chunks_of(ix, p))
    hits, _, info = oracle.oracle_hits(ti, ISSUE, gold, chunks)
    assert info == {"files": len(gold), "listed": min(len(gold), serve.PREFETCH_FILES)}
    assert len(hits) == min(len(gold), serve.PREFETCH_FILES)


def test_unknown_gold_raises(setup):  # noqa: F811
    service, encoder, snap, ix, vs, q, config = setup
    ti = service.task_index("o/r", q.base_commit)
    with pytest.raises(ValueError, match="not in the snapshot"):
        oracle.oracle_hits(ti, ISSUE, ("nope.py",), ())
    with pytest.raises(ValueError, match="not in the snapshot"):
        oracle.oracle_hits(ti, ISSUE, (snap.paths[0],), (snap.paths[0] + "::missing@1",))


# --- cost -----------------------------------------------------------------------------------------

def test_cost_prices_match_the_ledger(tmp_path):
    from scripts.run_swe_task import make_ledger

    price = make_ledger(tmp_path / "ledger.json").price
    assert (price.prompt_cache_hit_per_million, price.prompt_cache_miss_per_million, price.completion_per_million) == (
        cr2_cost.PRICE_HIT, cr2_cost.PRICE_MISS, cr2_cost.PRICE_OUT)


def _settled(usage):
    return {"event_type": "model.settled", "payload": {"record": {"success": usage is not None, "usage": usage or {}}}}


def test_cache_priced_cost(tmp_path):
    run = tmp_path / "r1"
    run.mkdir()
    events = [_settled({"prompt_tokens": 1000, "prompt_cache_hit_tokens": 800, "prompt_cache_miss_tokens": 200,
                        "completion_tokens": 100}),
              _settled({"prompt_tokens": 500, "completion_tokens": 50}),
              _settled(None)]
    (run / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    (run / "summary.json").write_text(json.dumps({"cost_cny": "0.010"}), encoding="utf-8")
    usage = cr2_cost.run_usage(run)
    assert usage == {"settled": 3, "with_usage": 2, "no_split": 1, "hit": 800, "miss": 700, "completion": 150}
    want = (Decimal(800) * Decimal("0.04") + Decimal(700) * 2 + Decimal(150) * 8) / Decimal(1_000_000)
    assert cr2_cost.cache_cost(usage) == want
    report = cr2_cost.cost_rows([run])
    assert report["total"]["ledger_cny"] == "0.010" and report["total"]["cache_hit_rate"] == pytest.approx(800 / 1500)
    assert Decimal(report["total"]["cache_priced_cny"]) == want.quantize(Decimal("0.000001"))
