"""Synthetic histories for the context protection rules (fix plan 1, F3)."""
import json

TRAILER = "[exit_code={code} status={status} truncated=false bytes_seen={n} bytes_kept={n} cleanup=not_required duration_ms=12]"


def command_output(body, code):
    status = "succeeded" if code == 0 else "failed"
    text = (body + "\n" if body else "") + TRAILER.format(code=code, status=status, n=len(body.encode()))
    if code != 0:
        # Observation trailer plus ToolResult.to_message's error line.
        text += "\nError: nonzero_exit\nError: nonzero_exit"
    return text


GREP_NO_MATCH = command_output("", 1)
PYTEST_FAILURE = command_output(
    "F...\n=================================== FAILURES ===================================\n"
    "____________________________ test_widget_value ____________________________\n\n"
    "    def test_widget_value():\n>       assert compute(3) == 4\nE       assert 5 == 4\n\n"
    "tests/test_widget.py:7: AssertionError\n=========================== short test summary info "
    "============================\nFAILED tests/test_widget.py::test_widget_value - assert 5 == 4\n"
    "1 failed, 3 passed in 0.12s", 1)
TRACEBACK = command_output(
    "[stderr]\nTraceback (most recent call last):\n  File \"run.py\", line 3, in <module>\n"
    "    import widgets\nModuleNotFoundError: No module named 'widgets'", 1)
LISTING = command_output("\n".join(f"-rw-r--r-- 1 user user {i * 37} Nov  6 12:00 file_{i}.py" for i in range(8)), 0)
WRITE_OK = command_output("", 0) + " " * 40
SOURCE = "\n".join(f"line {i}: value = compute({i})" for i in range(30))
STATUS_ONLY_FAILURE = command_output("", 2)


def call(cid, name, args):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def result(cid, content):
    return {"role": "tool", "tool_call_id": cid, "content": content}


def history():
    """A run that edits with run_command and ends on a grep with no match."""
    steps = [
        ("c1", "read_file", {"path": "pkg/widget.py"}, SOURCE),
        ("c2", "run_command", {"command": "python -m pytest -q tests/test_widget.py"}, PYTEST_FAILURE),
        ("c3", "run_command", {"command": "cat > pkg/widget.py <<'EOF'\ndef compute(x):\n    return x + 1\nEOF"}, WRITE_OK),
        ("c4", "run_command", {"command": "ls -la pkg"}, LISTING),
        ("c5", "run_command", {"command": "sed -i 's/x + 1/x + 2/' pkg/widget.py"}, WRITE_OK),
        ("c6", "run_command", {"command": "ls -la tests"}, LISTING),
        ("c7", "run_command", {"command": "grep -rn needle pkg"}, GREP_NO_MATCH),
        ("c8", "run_command", {"command": "ls -la docs"}, LISTING),
        ("c9", "run_command", {"command": "ls -la src"}, LISTING),
    ]
    messages = [{"role": "system", "content": "system"}, {"role": "user", "content": "fix the widget"}]
    for cid, name, args, content in steps:
        messages += [call(cid, name, args), result(cid, content)]
    return messages


def tool_positions(messages):
    return [i for i, m in enumerate(messages) if m.get("role") == "tool"]


def call_ids(messages, indices):
    return sorted(messages[i]["tool_call_id"] for i in indices)
