"""Print assistant turns of one run: visible content, reasoning_content and tool calls, truncated.

usage: excerpt.py <task> <group> <repeat> <first_turn> <last_turn> [chars]
"""

import sys
from pathlib import Path

from extract import RUNS, assistant_messages, read_events, text_of

task, group, repeat, lo, hi = sys.argv[1:6]
chars = int(sys.argv[6]) if len(sys.argv) > 6 else 400
run_dir = RUNS / task / group / repeat
for i, m in enumerate(assistant_messages(run_dir, read_events(run_dir)), 1):
    if not int(lo) <= i <= int(hi):
        continue
    print(f"--- turn {i}")
    if text_of(m.get("content")).strip():
        print("content:", text_of(m.get("content")).strip()[:chars])
    if m.get("reasoning_content"):
        print("reasoning:", m["reasoning_content"][:chars])
    for c in m.get("tool_calls") or []:
        print("call:", c["function"]["name"], c["function"]["arguments"][:160])
