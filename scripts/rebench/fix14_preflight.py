"""FIX-14 no-model Docker wiring checks with a local scripted transport.

The ``--all`` mode makes one ordinary run through each new cell. It sends no
provider request and never creates a paid ledger. The paid identity smoke is a
separate step after these checks and is intentionally not part of this script.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from hashlib import sha256
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.rebench import run_fix14 as launcher  # noqa: E402
from scripts.rebench.mock_e2e import brief, gold_tool_calls  # noqa: E402
from scripts.rebench.unleash_mock_e2e import ScriptedServer  # noqa: E402
from scripts.run_swe_task import run_task  # noqa: E402
from src.harness.factory import build_agent, load_config  # noqa: E402

OUT = ROOT / "runs/fix14-preflight"
PREFLIGHT_TASK = "ome-zarr-models__ome-zarr-models-py-206"


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


class Server(ScriptedServer):
    def __init__(self, group, task):
        super().__init__(group, gold_tool_calls(task))
        self.group = group

    def handle(self, request):
        body = json.loads(request.content)
        self.bodies.append(body)
        turn = len(self.bodies)
        calls = []
        if turn <= 2:
            calls = [(f"read-{turn}", "list_dir", {"path": "."})]
        elif turn == 3:
            calls = [(call.id, call.name, call.arguments) for call in self.gold_calls]
        reasoning = "Use the scripted next action." if self.group == "T8" else None
        delta = {"role": "assistant"}
        if reasoning:
            delta["reasoning_content"] = reasoning
        if calls:
            delta["content"] = "I will take the next tool action."
        else:
            delta["content"] = "Done."
        chunks = [{"choices": [{"index": 0, "delta": delta, "finish_reason": None}]}]
        for index, (call_id, name, arguments) in enumerate(calls):
            chunks.append({"choices": [{"index": 0, "finish_reason": None, "delta": {"tool_calls": [
                {"index": index, "id": call_id, "type": "function",
                 "function": {"name": name, "arguments": json.dumps(arguments)}}]}}]})
        chunks.append({"choices": [{"index": 0, "delta": {},
                                     "finish_reason": "tool_calls" if calls else "stop"}]})
        chunks.append({"choices": [], "usage": self.usage(request.content, reasoning or "")})
        common = {"id": f"fix14-preflight-{turn}", "object": "chat.completion.chunk",
                  "model": "deepseek-flash-mock", "system_fingerprint": "fp_fix14_mock"}
        text = "".join("data: " + json.dumps({**common, **chunk}) + "\n\n" for chunk in chunks)
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=(text + "data: [DONE]\n\n").encode())


def scripted_client(group, task):
    server = Server(group, task)
    config = json.loads(json.dumps(load_config(launcher.PROFILES[group])))
    config["llm"]["api_key"] = "local-scripted-preflight"
    with tempfile.TemporaryDirectory() as tmp:
        llm = build_agent(config, Path(tmp)).llm
    llm._http = httpx.AsyncClient(transport=httpx.MockTransport(server.handle), trust_env=False)
    llm._owns_http_client = True
    return llm, server


def case_binding(task, group):
    from src.runtime.session import source_fingerprint
    files = [launcher.PROFILES[group], ROOT / "prompts/swe_b0.md", ROOT / "scripts/run_swe_task.py",
             ROOT / "scripts/rebench/run_fix14.py", Path(__file__)]
    return {"source_fingerprint": source_fingerprint(ROOT),
            "task_sha256": sha256(json.dumps(task, sort_keys=True).encode()).hexdigest(),
            "files": {str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest() for path in files}}


async def run_case(task, group):
    root = OUT / "e2e" / task["id"] / group
    directory = root / "wiring"
    if directory.exists() and (directory / "check.json").is_file():
        previous = json.loads((directory / "check.json").read_text(encoding="utf-8"))
        if previous.get("ok"):
            return previous
    if directory.exists():
        index = 1
        while (root / f"wiring.retry{index}").exists():
            index += 1
        directory = root / f"wiring.retry{index}"
    config = load_config(launcher.PROFILES[group])
    llm, server = scripted_client(group, task)
    save(directory / "case-binding.json", case_binding(task, group))
    await run_task(task, launcher.PROFILES[group], directory, mock=True, variant=launcher.VARIANTS[group],
                   mock_llm=llm, on_agent=launcher.legacy.tool_recorder(directory, tap_model=False),
                   ledger_options=launcher.legacy.ledger_options(config))
    save(directory / "request-bodies.json", [{k: v for k, v in body.items() if k != "_prompt"}
                                               for body in server.bodies])
    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    row = launcher.inspect_run(directory, group)
    problems = list(row["problems"])
    if summary.get("run_error") or summary.get("answer_isolation_pass") is not True:
        problems.append("task execution or answer isolation failed")
    bodies = json.loads((directory / "request-bodies.json").read_text(encoding="utf-8"))
    expected_thinking = "enabled" if group == "T8" else "disabled"
    for number, body in enumerate(bodies, 1):
        if body.get("thinking", {}).get("type") != expected_thinking:
            problems.append(f"request {number}: thinking option differs")
        if body.get("max_tokens") != (8192 if group == "T8" else 32768):
            problems.append(f"request {number}: max_tokens differs")
        assistants = [message for message in body.get("messages", []) if message.get("role") == "assistant"]
        has_reasoning = any("reasoning_content" in message for message in assistants)
        if group == "T8" and number > 1 and not has_reasoning:
            problems.append(f"request {number}: reasoning replay differs")
        if group == "O32" and has_reasoning:
            problems.append(f"request {number}: unexpected reasoning replay")
    result = {"task": task["id"], "group": group, "case": "wiring", "evidence_dir": str(directory.relative_to(OUT)),
              "ok": not problems,
              "problems": problems, "provider_calls": 0, "scripted_requests": len(bodies),
              "summary": brief(summary),
              "evidence_sha256": {name: sha256((directory / name).read_bytes()).hexdigest()
                                   for name in ("summary.json", "request-bodies.json", "case-binding.json")}}
    save(directory / "check.json", result)
    return result


async def all_cases(groups=None):
    slots, tasks = launcher.load_inputs()
    task_id = PREFLIGHT_TASK if PREFLIGHT_TASK in tasks else next(slot["task"] for slot in slots)
    task = tasks[task_id]
    groups = tuple(groups or launcher.GROUPS)
    results = [await run_case(task, group) for group in groups]
    existing_path = OUT / "e2e-report.json"
    existing = json.loads(existing_path.read_text(encoding="utf-8")) if existing_path.is_file() else {}
    merged = {(row.get("task"), row.get("group")): row for row in existing.get("cases", [])}
    merged.update({(row["task"], row["group"]): row for row in results})
    expected = {(task_id, group) for group in launcher.GROUPS}
    cases = [merged[key] for key in sorted(merged) if key in expected]
    report = {"schema": "fix14.preflight-e2e.v1", "provider_calls": 0,
              "expected_cases": 2, "completed_cases": len(cases),
              "ok": len(cases) == 2 and all(row["ok"] for row in cases), "cases": cases}
    save(existing_path, report)
    return report


async def identity_smoke():
    """The separately authorized two-request paid smoke: T8 and O32 must match the FIX-13 window."""
    from scripts.fix10_thinking_smoke import call, make_ledger_for_smoke
    from scripts.run_swe_task import make_ledger
    from src.runtime.retry import complete_billing_usage

    report_path = OUT / "e2e-report.json"
    if not report_path.is_file():
        raise ValueError("run --all before the identity smoke")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("ok") is not True or report.get("completed_cases") != 2:
        raise ValueError("both FIX-14 no-model cases must pass before the identity smoke")
    base_identity = json.loads((ROOT / "runs/fix13-preflight/identity.json").read_text(encoding="utf-8"))
    directory = OUT / "identity-smoke"
    directory.mkdir(parents=True, exist_ok=False)
    import httpx

    page = httpx.get(launcher.legacy.PRICE_URL, timeout=60, follow_redirects=True,
                     trust_env=False).text
    snapshot = launcher.legacy.price_snapshot(page)
    launcher.legacy.archive_price(directory, "before", page, snapshot, launcher.legacy.now_utc())
    problem = launcher.legacy.price_problem(snapshot, base_identity["price_snapshot"])
    if problem:
        raise ValueError(problem)
    ledger_path = directory / "cny-ledger.json"
    ledger = make_ledger_for_smoke(make_ledger(None).price, ledger_path)
    ledger.time_of_day = False
    ledger.persist()
    records, responses = [], []
    for group in launcher.GROUPS:
        config = load_config(launcher.PROFILES[group])
        with tempfile.TemporaryDirectory() as tmp:
            client = build_agent(config, Path(tmp)).llm
        try:
            response = await call(ledger, client, records, "identity-" + group,
                                  [{"role": "user", "content": "Reply with OK."}],
                                  max_tokens=1024, stream=True)
        finally:
            await client.aclose()
        save(directory / "smoke.json", {"cases": records, "standard_peak_cny": str(ledger.cost_committed)})
        if (response is None or records[-1]["settlement"].get("status") != "provider_usage"
                or not complete_billing_usage(response.usage) or response.finish_reason == "length"):
            raise ValueError("FIX-14 identity request failed, truncated, or has unknown usage")
        if complete_billing_usage(response.usage) and group == "O32":
            reasoning = (response.usage or {}).get("completion_tokens_details") or {}
            if reasoning.get("reasoning_tokens"):
                raise ValueError("O32 identity response reported reasoning tokens with thinking disabled")
        responses.append(response.provider_metadata)
    page = httpx.get(launcher.legacy.PRICE_URL, timeout=60, follow_redirects=True,
                     trust_env=False).text
    current = launcher.legacy.price_snapshot(page)
    launcher.legacy.archive_price(directory, "after", page, current, launcher.legacy.now_utc())
    problem = launcher.legacy.price_problem(current, snapshot)
    if problem:
        raise ValueError(problem)
    models = {record.get("response_model") for record in responses}
    prints = {record.get("system_fingerprint") for record in responses}
    if models != {launcher.legacy.EXPECTED_MODEL} or len(prints) != 1 or None in prints or "" in prints:
        raise ValueError("FIX-14 identity model or fingerprint differs between T8 and O32")
    fingerprint = next(iter(prints))
    if fingerprint != base_identity["system_fingerprint"]:
        raise ValueError(f"identity fingerprint {fingerprint} differs from the FIX-13 window "
                         f"{base_identity['system_fingerprint']}; do not start FIX-14")
    result = {"ok": True, "model": next(iter(models)), "system_fingerprint": fingerprint,
              "price_snapshot": snapshot, "ledger_path": ledger_path.relative_to(ROOT).as_posix(),
              "standard_peak_cny": str(ledger.cost_committed), "requests": ledger.calls_started,
              "reference_fingerprint": base_identity["system_fingerprint"],
              "completed_at": __import__("datetime").datetime.now(
                  __import__("datetime").timezone.utc).isoformat()}
    save(OUT / "identity.json", result)
    return result


def write_ready():
    from src.runtime.session import source_fingerprint

    report_path = OUT / "e2e-report.json"
    identity_path = OUT / "identity.json"
    if not report_path.is_file() or not identity_path.is_file():
        raise ValueError("run --all and record the separately authorized identity smoke first")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    slots, tasks = launcher.load_inputs()
    if (report.get("ok") is not True or report.get("completed_cases") != 2
            or identity.get("ok") is not True):
        raise ValueError("incomplete FIX-14 preflight evidence")
    paths = [launcher.PLAN, launcher.INGEST, launcher.MANIFEST, *launcher.PROFILES.values(),
             Path(__file__), Path(launcher.__file__), ROOT / "prompts/swe_b0.md",
             OUT / "e2e-report.json", OUT / "identity.json"]
    for case in report["cases"]:
        directory = OUT / case["evidence_dir"]
        for name, expected in case["evidence_sha256"].items():
            path = directory / name
            if sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError("preflight evidence changed: " + str(path))
            paths.append(path)
    ready = {"schema": "fix14.ready.v1", "ok": True, "tasks": len(tasks),
             "slots": len(slots), "e2e_cases": 2, "source_fingerprint": source_fingerprint(ROOT),
             "identity_fingerprint": identity.get("system_fingerprint"),
             "sources": {path.resolve().relative_to(ROOT).as_posix(): sha256(path.read_bytes()).hexdigest()
                         for path in paths}}
    save(OUT / "ready.json", ready)
    return {"ok": True, "tasks": len(tasks), "slots": len(slots),
            "ready_sha256": sha256((OUT / "ready.json").read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--identity", action="store_true", help="authorized two-request paid smoke, cap 0.5 CNY")
    parser.add_argument("--ready", action="store_true")
    parser.add_argument("--group", choices=launcher.GROUPS, action="append")
    args = parser.parse_args()
    if args.ready:
        result = write_ready()
    elif args.identity:
        result = asyncio.run(identity_smoke())
    elif args.all:
        result = asyncio.run(all_cases(args.group))
    else:
        parser.error("use --all, --identity or --ready")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
