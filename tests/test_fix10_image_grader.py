"""ImageGrader options for SWE-rebench tasks. Every option defaults off; the old path is unchanged."""
import json

import pytest

from scripts.preflight_a5_repo_baseline import PYTEST_OBSERVER
from src.eval import image_grader
from src.eval.image_grader import (
    ImageGrader,
    pytest_options_from_test_cmd,
    repair_truncated_nodeids,
    sandbox_p2p_exclusions,
)


class FakeExecution:
    class _Text:
        text_preview = ""

    def __init__(self):
        self.status = type("S", (), {"value": "completed"})()
        self.stderr = self._Text()


class FakeBackend:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.name = "fake"
        self.uploads = []
        self.requests = []
        self.docker_retries = []
        FakeBackend.instances.append(self)

    async def start(self, seed):
        return None

    async def _ok(self, *argv, data=None, **kwargs):
        if data is not None and b"a5-runner.py" in json.dumps(argv).encode():
            self.uploads.append(data)
        if "a5-pytest-receipt.json" in " ".join(str(a) for a in argv):
            return json.dumps({"collected": ["t.py::a", "t.py::b[x y]"], "reports": [
                {"nodeid": "t.py::a", "when": "call", "outcome": "passed"}], "complete": True, "exit_code": 0})
        return ""

    async def execute(self, request):
        self.requests.append(request)
        return FakeExecution()

    async def close(self):
        return None


@pytest.fixture
def fake(monkeypatch):
    FakeBackend.instances = []

    async def no_runtime(*args, **kwargs):
        return None

    monkeypatch.setattr(image_grader, "DockerExecutionBackend", FakeBackend)
    monkeypatch.setattr("scripts.preflight_a5_repo_baseline.prepare_repo_runtime", no_runtime)
    return FakeBackend


async def _evaluate(**options):
    return await ImageGrader().evaluate(image="img@sha256:" + "0" * 64, delta=None, test_overlay={},
                                        f2p=["t.py::a"], p2p=["t.py::c"], **options)


async def test_default_path_uploads_the_unchanged_observer_and_disables_autoload(fake):
    await _evaluate()
    backend = fake.instances[0]
    assert backend.uploads == [PYTEST_OBSERVER.encode()]
    request = backend.requests[0]
    assert request.env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert json.loads(request.stdin_bytes)["args"] == ["t.py::a", "t.py::c"]


async def test_autoload_plugins_uses_an_observer_that_keeps_plugins(fake):
    await _evaluate(autoload_plugins=True)
    backend = fake.instances[0]
    runner = backend.uploads[0].decode()
    assert "os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'" not in runner
    assert "PYTEST_DISABLE_PLUGIN_AUTOLOAD" in runner
    assert backend.requests[0].env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == ""


async def test_pytest_options_come_before_node_ids(fake):
    await _evaluate(pytest_options=["-p", "no:cacheprovider", "-W", "ignore::DeprecationWarning"])
    args = json.loads(fake.instances[0].requests[0].stdin_bytes)["args"]
    assert args == ["-p", "no:cacheprovider", "-W", "ignore::DeprecationWarning", "t.py::a", "t.py::c"]


async def test_collect_returns_collected_node_ids(fake):
    collected = await ImageGrader().collect(image="img@sha256:" + "0" * 64, test_overlay={}, paths=["t.py"],
                                            autoload_plugins=True, pytest_options=["-p", "no:cacheprovider"])
    args = json.loads(fake.instances[0].requests[0].stdin_bytes)["args"]
    assert args == ["-p", "no:cacheprovider", "--collect-only", "-q", "t.py"]
    assert collected["collected"] == ["t.py::a", "t.py::b[x y]"]


@pytest.mark.parametrize("cmd, expected", [
    ("pytest --no-header -rA --tb=line --color=no -p no:cacheprovider -W ignore::DeprecationWarning",
     ["-p", "no:cacheprovider", "-W", "ignore::DeprecationWarning"]),
    ("pytest --no-header -rA --tb=line --color=no -p no:cacheprovider tests/test_main.py", ["-p", "no:cacheprovider"]),
    ('pytest -rA -p no:cacheprovider -m "not integration_test and not slow" -v --tb=no',
     ["-p", "no:cacheprovider", "-m", "not integration_test and not slow"]),
    ("env OMP_NUM_THREADS=1 pytest --no-header -rA -p no:cacheprovider", ["-p", "no:cacheprovider"]),
    ("python -m pytest --no-header -rA --color=no -p no:cacheprovider --verbosity=1", ["-p", "no:cacheprovider"]),
    ("./test_reframe.py", []),
    (None, []),
])
def test_pytest_options_from_test_cmd(cmd, expected):
    assert pytest_options_from_test_cmd(cmd) == expected


def test_repair_maps_ids_cut_at_a_space():
    collected = ["t.py::a", "t.py::b[599-Unknown Error]", "t.py::c[x y]", "t.py::c[x z]"]
    nodes, notes = repair_truncated_nodeids(["t.py::a", "t.py::b[599-Unknown", "t.py::c[x", "t.py::gone["],
                                            collected)
    assert nodes == ["t.py::a", "t.py::b[599-Unknown Error]", "t.py::c[x y]", "t.py::c[x z]"]
    assert [(n["nodeid"], n["match_count"]) for n in notes] == [
        ("t.py::b[599-Unknown", 1), ("t.py::c[x", 2), ("t.py::gone[", 0)]


def test_sandbox_exclusions_are_p2p_not_passing_in_any_gold_run():
    runs = [{"cases": [{"nodeid": "a", "outcome": "passed"}, {"nodeid": "b", "outcome": "failed"},
                       {"nodeid": "c", "outcome": "passed"}]},
            {"cases": [{"nodeid": "a", "outcome": "passed"}, {"nodeid": "b", "outcome": "failed"},
                       {"nodeid": "c", "outcome": "error"}]}]
    assert sandbox_p2p_exclusions(runs, ["b", "c", "d"]) == ["b", "c", "d"]
    assert sandbox_p2p_exclusions(runs[:1], ["a", "b"]) == ["b"]
