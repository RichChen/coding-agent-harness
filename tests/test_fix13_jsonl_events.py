import pytest

from scripts.run_swe_task import load_run_events
from scripts.run_cr2 import _events
from src.tracing.store import EventStore, StoreError


@pytest.mark.parametrize("content", ["plain", "中文", "NEL\x85inside", "LS\u2028inside", "PS\u2029inside"])
def test_summary_reader_preserves_unicode_inside_json_strings(tmp_path, content):
    with EventStore(tmp_path / "session", create=True, run_id="r", task_id="t") as store:
        event = store.append("fixture", {"text": content})
        store.export_jsonl(tmp_path / "events.jsonl")
    events = load_run_events(tmp_path / "events.jsonl")
    assert events == [event] and events[0]["payload"]["text"] == content
    assert _events(tmp_path) == [event]


def test_summary_reader_rejects_partial_export(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"unfinished":')
    with pytest.raises(StoreError, match="partial_tail"):
        load_run_events(path)
