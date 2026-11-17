"""FIX-13 offline references and real-Docker checks with a local scripted HTTP transport.

--references writes isolated gold references. --all runs empty/gold through the
normal task entry on all 30 accepted tasks, plus four-arm prompt wiring on the
first task. These modes use no provider requests. --identity is the separately
authorized two-request paid smoke, capped at 0.5 CNY. Existing evidence is kept.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.rebench import run_fix13 as launcher  # noqa: E402
from scripts.rebench.mock_e2e import brief, gold_tool_calls  # noqa: E402
from scripts.rebench.unleash_mock_e2e import ScriptedServer  # noqa: E402
from scripts.run_swe_task import run_task, rebench_calibration  # noqa: E402
from src.harness.factory import build_agent, load_config  # noqa: E402
from src.runtime.tool_intervention import REPEAT_REMINDER, RESULT_EXPLANATION  # noqa: E402

OUT = ROOT / "runs/fix13-preflight"
REFERENCES = OUT / "references.json"
ANALYSIS_BINDING = OUT / "analysis-binding.json"


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def references():
    from scripts.analysis.fix12_no_edit import references as ref

    _, tasks = launcher.load_inputs()
    ref.WORK = ROOT / "runs/fix13-prep/work"
    output = {task: ref.task_reference(task) for task in sorted(tasks)}
    if set(output) != set(tasks) or len(output) != 30:
        raise ValueError("exactly 30 accepted references are required")
    save(REFERENCES, {"schema": "fix13.gold-references.v1", "tasks": output})
    paths = [Path(ref.__file__), ROOT / "scripts/analysis/fix12_no_edit/classify.py",
             ROOT / "scripts/analysis/fix12_no_edit/rules.py", ROOT / "scripts/analysis/p4_no_edit/rules.py",
             ROOT / "scripts/analysis/reread/_load.py", ROOT / "scripts/analysis/reread/reads.py",
             ROOT / "scripts/analysis/reread/reread.py", ROOT / "scripts/analysis/fix13_report.py"]
    save(ANALYSIS_BINDING, {"schema": "fix13.analysis-binding.v1", "references_sha256": sha256(REFERENCES.read_bytes()).hexdigest(),
                            "sources": {str(p.relative_to(ROOT)): sha256(p.read_bytes()).hexdigest() for p in paths},
                            "work_root": "runs/fix13-prep/work", "groups": launcher.GROUPS,
                            "calibration": {name: rebench_calibration(task) for name, task in tasks.items()},
                            "isolation": "host-only offline reference and analysis files, outside online workspace"})
    return {"tasks": len(output), "references_sha256": sha256(REFERENCES.read_bytes()).hexdigest()}


class Server(ScriptedServer):
    def __init__(self, group, task, case):
        super().__init__(group, gold_tool_calls(task) if case != "empty" else [])
        self.case = case

    def handle(self, request):
        body = json.loads(request.content)
        self.bodies.append(body)
        turn = len(self.bodies)
        reads = 3 if self.case == "wiring" else 1
        calls = []
        if self.case != "empty" and turn <= reads:
            calls = [(f"read-{turn}", "list_dir", {"path": "."})]
        elif self.case != "empty" and turn == reads + 1:
            calls = [(c.id, c.name, c.arguments) for c in self.gold_calls]
        reasoning = "Use the scripted next action." if self.group == "UT" else None
        delta = {"role": "assistant"}
        if reasoning is not None:
            delta["reasoning_content"] = reasoning
        if calls and self.group == "E" and turn > 1:
            delta["content"] = "The previous results establish the repository state, so I will continue with the scripted next action."
        elif not calls:
            delta["content"] = "Done."
        chunks = [{"choices": [{"index": 0, "delta": delta, "finish_reason": None}]}]
        for index, (call_id, name, arguments) in enumerate(calls):
            chunks.append({"choices": [{"index": 0, "finish_reason": None, "delta": {"tool_calls": [
                {"index": index, "id": call_id, "type": "function",
                 "function": {"name": name, "arguments": json.dumps(arguments)}}]}}]})
        chunks.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if calls else "stop"}]})
        chunks.append({"choices": [], "usage": self.usage(request.content, reasoning or "")})
        common = {"id": f"preflight-{turn}", "object": "chat.completion.chunk", "model": "deepseek-flash-mock",
                  "system_fingerprint": "fp_fix13_mock"}
        text = "".join("data: " + json.dumps({**common, **chunk}) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=text.encode())


def scripted_client(group, task, case, config):
    server = Server(group, task, case)
    config = json.loads(json.dumps(config))
    config["llm"]["api_key"] = "local-scripted-preflight"
    with tempfile.TemporaryDirectory() as tmp:
        llm = build_agent(config, Path(tmp)).llm
    llm._http = httpx.AsyncClient(transport=httpx.MockTransport(server.handle), trust_env=False)
    llm._owns_http_client = True
    return llm, server


def case_binding(task, group):
    from src.runtime.session import source_fingerprint

    files = [launcher.PROFILES[group], ROOT / "prompts/swe_b0.md", ROOT / "scripts/run_swe_task.py",
             ROOT / "scripts/rebench/mock_e2e.py"]
    return {"source_fingerprint": source_fingerprint(ROOT), "task_sha256": sha256(json.dumps(task, sort_keys=True).encode()).hexdigest(),
            "files": {str(p.relative_to(ROOT)): sha256(p.read_bytes()).hexdigest() for p in files}}


def case_check(task, group, case, directory):
    from src.eval.candidate_error import expected_empty, regrade_verdict

    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    bodies = json.loads((directory / "request-bodies.json").read_text(encoding="utf-8"))
    row = launcher.inspect_run(directory, group)
    problems = list(row["problems"])
    if json.loads((directory / "case-binding.json").read_text(encoding="utf-8")) != case_binding(task, group):
        problems.append("runtime/profile/task inputs differ from the case execution binding")
    issue = launcher.legacy.run_checks([summary])
    if issue:
        problems.append(issue)
    if summary.get("solved") is not (case != "empty"):
        problems.append("scripted patch outcome differs from the empty/gold expectation")
    if summary.get("answer_isolation_pass") is not True or summary.get("run_error"):
        problems.append("answer isolation or task execution failed")
    calibration_check = None
    if case == "empty":
        evaluation = json.loads((directory / "grader-result.json").read_text(encoding="utf-8"))
        calibration_check = regrade_verdict(evaluation, expected_empty(rebench_calibration(task)))
        if not calibration_check["consistent"]:
            problems.append("empty result differs from the exact calibrated counts/missing IDs")
    base_prompt = (ROOT / "prompts/swe_b0.md").read_text(encoding="utf-8").strip()
    prompt = base_prompt + ("\n\n" + RESULT_EXPLANATION if group == "E" else "")
    for number, body in enumerate(bodies, 1):
        if body["messages"][0]["content"] != prompt:
            problems.append(f"request {number}: system prompt differs")
        reminder_count = sum(m.get("role") == "user" and m.get("content") == REPEAT_REMINDER for m in body["messages"])
        expected_count = int(group == "R" and case == "wiring" and number >= 4)
        if reminder_count != expected_count:
            problems.append(f"request {number}: reminder count differs")
        if any(m.get("role") == "user" and "remaining model calls:" in str(m.get("content")) for m in body["messages"]):
            problems.append(f"request {number}: remaining-calls message appeared")
        assistant = [m for m in body["messages"] if m.get("role") == "assistant"]
        if group == "UT" and assistant and not all("reasoning_content" in m for m in assistant):
            problems.append(f"request {number}: thinking replay missing")
        if group != "UT" and any("reasoning_content" in m for m in assistant):
            problems.append(f"request {number}: unexpected thinking replay")
        if group == "E" and number >= 3:
            explained = [m for m in assistant[1:] if m.get("tool_calls")]
            if not all(m.get("content") for m in explained):
                problems.append(f"request {number}: scripted visible explanation missing")
    expected_requests = 1 if case == "empty" else 5 if case == "wiring" else 3
    if len(bodies) != expected_requests:
        problems.append("unexpected scripted request count")
    result = {"task": task["id"], "group": group, "case": case, "ok": not problems,
              "problems": problems, "provider_calls": 0, "scripted_requests": len(bodies), "summary": brief(summary),
              "empty_calibration_check": calibration_check,
              "evidence_sha256": {name: sha256((directory / name).read_bytes()).hexdigest()
                                  for name in ("summary.json", "request-bodies.json", "grader-result.json", "case-binding.json")}}
    save(directory / "check.json", result)
    return result


async def run_case(task, group, case):
    directory = OUT / "e2e" / task["id"] / group / case
    if (directory / "summary.json").exists() and (directory / "request-bodies.json").exists():
        return case_check(task, group, case, directory)
    if directory.exists():
        raise ValueError(f"preserve and reconcile incomplete preflight evidence: {directory}")
    config = load_config(launcher.PROFILES[group])
    llm, server = scripted_client(group, task, case, config)
    save(directory / "case-binding.json", case_binding(task, group))
    await run_task(task, launcher.PROFILES[group], directory, mock=True, variant=launcher.VARIANTS[group],
                   mock_llm=llm, on_agent=launcher.legacy.tool_recorder(directory, tap_model=False),
                   ledger_options=launcher.legacy.ledger_options(config))
    save(directory / "request-bodies.json", [{k: v for k, v in body.items() if k != "_prompt"} for body in server.bodies])
    return case_check(task, group, case, directory)


def all_cases():
    slots, tasks = launcher.load_inputs()
    order = list(dict.fromkeys(slot["task"] for slot in slots))
    cases = [(task, "U", case) for task in order for case in ("empty", "gold")]
    cases += [(order[0], group, "wiring") for group in launcher.GROUPS]
    results = []
    for task, group, case in cases:
        command = [sys.executable, str(Path(__file__).resolve()), "--case", task, group, case]
        completed = subprocess.run(command, cwd=ROOT, timeout=10800 + 1800)
        check_path = OUT / "e2e" / task / group / case / "check.json"
        result = json.loads(check_path.read_text(encoding="utf-8")) if check_path.exists() else {"ok": False}
        results.append(result)
        save(OUT / "e2e-report.json", {"schema": "fix13.preflight-e2e.v1", "provider_calls": 0,
                                       "expected_cases": len(cases), "completed_cases": len(results),
                                       "ok": len(results) == len(cases) and all(r["ok"] for r in results), "cases": results})
        if completed.returncode or not result["ok"]:
            raise RuntimeError(f"preflight case requires review: {task}/{group}/{case}")
    return {"cases": len(results), "ok": True}


async def identity_smoke():
    """Two authorized identity requests, with the existing 0.5 CNY smoke ledger."""
    from scripts.fix10_thinking_smoke import call, make_ledger_for_smoke
    from scripts.run_swe_task import make_ledger
    from src.runtime.retry import complete_billing_usage

    report = json.loads((OUT / "e2e-report.json").read_text(encoding="utf-8"))
    if report.get("ok") is not True or report.get("completed_cases") != 64:
        raise ValueError("all 64 no-model cases must pass before the identity smoke")
    directory = OUT / "identity-smoke"
    directory.mkdir(parents=True, exist_ok=False)
    page = launcher.fetch_price_page()
    snapshot = launcher.legacy.price_snapshot(page)
    launcher.legacy.archive_price(directory, "before", page, snapshot, launcher.legacy.now_utc())
    problem = launcher.legacy.price_problem(snapshot)
    if problem:
        raise ValueError(problem)
    ledger_path = directory / "cny-ledger.json"
    ledger = make_ledger_for_smoke(make_ledger(None).price, ledger_path)
    ledger.time_of_day = False
    ledger.persist()
    records, responses = [], []
    for group in ("U", "UT"):
        config = load_config(launcher.PROFILES[group])
        with tempfile.TemporaryDirectory() as tmp:
            client = build_agent(config, Path(tmp)).llm
        try:
            response = await call(ledger, client, records, "identity-" + group,
                                  [{"role": "user", "content": "Reply with OK."}], max_tokens=2048, stream=True)
        finally:
            await client.aclose()
        save(directory / "smoke.json", {"cases": records, "standard_peak_cny": str(ledger.cost_committed)})
        if (response is None or records[-1]["settlement"].get("status") != "provider_usage"
                or not complete_billing_usage(response.usage) or response.finish_reason == "length"):
            raise ValueError("identity smoke request failed, truncated, or has unknown usage")
        responses.append(response.provider_metadata)
    page = launcher.fetch_price_page()
    current = launcher.legacy.price_snapshot(page)
    launcher.legacy.archive_price(directory, "after", page, current, launcher.legacy.now_utc())
    problem = launcher.legacy.price_problem(current, snapshot)
    if problem:
        raise ValueError(problem)
    models = {r.get("response_model") for r in responses}
    prints = {r.get("system_fingerprint") for r in responses}
    if models != {launcher.legacy.EXPECTED_MODEL} or len(prints) != 1 or None in prints or "" in prints:
        raise ValueError("identity smoke model or fingerprint differs between U and UT")
    result = {"ok": True, "model": next(iter(models)), "system_fingerprint": next(iter(prints)),
              "price_snapshot": snapshot, "ledger_path": ledger_path.relative_to(ROOT).as_posix(),
              "standard_peak_cny": str(ledger.cost_committed), "requests": ledger.calls_started,
              "completed_at": datetime.now(timezone.utc).isoformat()}
    save(OUT / "identity.json", result)
    return result


def ready():
    from src.runtime.session import source_fingerprint

    slots, tasks = launcher.load_inputs()
    e2e = json.loads((OUT / "e2e-report.json").read_text(encoding="utf-8"))
    identity = json.loads((OUT / "identity.json").read_text(encoding="utf-8"))
    refs = json.loads(REFERENCES.read_text(encoding="utf-8"))
    analysis = json.loads(ANALYSIS_BINDING.read_text(encoding="utf-8"))
    if (not e2e.get("ok") or e2e.get("completed_cases") != 64 or not identity.get("ok")
            or set(refs["tasks"]) != set(tasks) or not launcher.config_report()["ok"]):
        raise ValueError("incomplete FIX-13 preflight evidence")
    for name, expected in analysis["sources"].items():
        if sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError("offline analysis code changed: " + name)
    paths = [launcher.PLAN, launcher.INGEST, launcher.MANIFEST, *launcher.PROFILES.values(), REFERENCES,
             ANALYSIS_BINDING, OUT / "e2e-report.json", OUT / "identity.json", Path(__file__),
             Path(launcher.__file__), ROOT / "prompts/swe_b0.md", ROOT / identity["ledger_path"]]
    paths += [ROOT / name for name in analysis["sources"]]
    retries = OUT / "preflight-retries.json"
    if retries.exists():
        paths.append(retries)
        for attempt in json.loads(retries.read_text(encoding="utf-8")):
            paths.append(OUT / attempt["preserved_at"] / "check.json")
    for case in e2e["cases"]:
        directory = OUT / "e2e" / case["task"] / case["group"] / case["case"]
        for name, expected in case["evidence_sha256"].items():
            path = directory / name
            if sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError("preflight evidence changed: " + str(path))
            paths.append(path)
    for task in tasks.values():
        paths += [ROOT / task[key] for key in ("receipt", "clean_record", "public_task")]
        paths.append((ROOT / task["receipt"]).parent / "state.json")
    result = {"schema": "fix13.ready.v1", "ok": True, "tasks": len(tasks), "slots": len(slots), "e2e_cases": 64,
              "source_fingerprint": source_fingerprint(ROOT), "created_at": datetime.now(timezone.utc).isoformat(),
              "sources": {path.resolve().relative_to(ROOT).as_posix(): sha256(path.read_bytes()).hexdigest() for path in paths}}
    save(launcher.READY, result)
    launcher.readiness()
    return {"ok": True, "tasks": len(tasks), "slots": len(slots), "e2e_cases": 64,
            "ready_sha256": sha256(launcher.READY.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--references", action="store_true")
    mode.add_argument("--all", action="store_true")
    mode.add_argument("--case", nargs=3, metavar=("TASK", "GROUP", "CASE"))
    mode.add_argument("--identity", action="store_true", help="authorized two-request paid identity smoke, cap 0.5 CNY")
    mode.add_argument("--ready", action="store_true")
    args = parser.parse_args()
    if args.references:
        result = references()
    elif args.all:
        result = all_cases()
    elif args.identity:
        result = asyncio.run(identity_smoke())
    elif args.ready:
        result = ready()
    else:
        _, tasks = launcher.load_inputs()
        task, group, case = args.case
        if group not in launcher.GROUPS or case not in ("empty", "gold", "wiring"):
            raise ValueError("unknown preflight group/case")
        result = asyncio.run(run_case(tasks[task], group, case))
    print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
    raise SystemExit(0 if result.get("ok", True) else 1)


if __name__ == "__main__":
    main()
