"""ET addendum preflight: two real-Docker no-model cases, identity smoke, and readiness binding.

--preflight  read-only configuration/plan checks (no Docker, no provider calls)
--e2e        two scripted no-model cases on the first task (Docker grading, no provider calls)
--identity   the authorized two-request paid identity smoke (cap 0.5 CNY)
--ready      write runs/fix13-et-preflight/ready.json after e2e and identity pass
"""
from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
import sys

sys.path.insert(0, str(ROOT))
from scripts.rebench import fix13_preflight as p  # noqa: E402
from scripts.rebench import run_fix13_et as launcher  # noqa: E402
from scripts.rebench import run_unleash as legacy  # noqa: E402
from scripts.rebench.unleash_mock_e2e import ScriptedServer  # noqa: E402
from scripts.run_swe_task import rebench_calibration, run_task  # noqa: E402
from src.harness.factory import build_agent, load_config  # noqa: E402
from src.runtime.tool_intervention import RESULT_EXPLANATION  # noqa: E402

OUT = launcher.PREFLIGHT
REFERENCES = ROOT / "runs/fix13-preflight/references.json"
ANALYSIS_BINDING = ROOT / "runs/fix13-preflight/analysis-binding.json"
E2E_REPORT = OUT / "e2e-report.json"


def save(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def base_prompt() -> str:
    return (ROOT / "prompts/swe_b0.md").read_text(encoding="utf-8").strip()


def et_prompt() -> str:
    return base_prompt() + "\n\n" + RESULT_EXPLANATION


class ETServer(ScriptedServer):
    """Scripted transport that reports reasoning tokens and returns a visible sentence on tool turns."""

    def __init__(self, task, case):
        super().__init__("UT", p.gold_tool_calls(task) if case != "empty" else [])
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
        reasoning = "Use the scripted next action."
        delta = {"role": "assistant", "reasoning_content": reasoning}
        if calls and turn > 1:
            delta["content"] = ("The previous results establish the repository state, "
                                "so I will continue with the scripted next action.")
        elif not calls:
            delta["content"] = "Done."
        chunks = [{"choices": [{"index": 0, "delta": delta, "finish_reason": None}]}]
        for index, (call_id, name, arguments) in enumerate(calls):
            chunks.append({"choices": [{"index": 0, "finish_reason": None, "delta": {"tool_calls": [
                {"index": index, "id": call_id, "type": "function",
                 "function": {"name": name, "arguments": json.dumps(arguments)}}]}}]})
        chunks.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if calls else "stop"}]})
        chunks.append({"choices": [], "usage": self.usage(request.content, reasoning)})
        common = {"id": f"preflight-{turn}", "object": "chat.completion.chunk", "model": "deepseek-flash-mock",
                  "system_fingerprint": "fp_fix13_mock"}
        text = "".join("data: " + json.dumps({**common, **chunk}) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=text.encode())


def scripted_client(task, case, config):
    server = ETServer(task, case)
    config = json.loads(json.dumps(config))
    config["llm"]["api_key"] = "local-scripted-preflight"
    with tempfile.TemporaryDirectory() as tmp:
        llm = build_agent(config, Path(tmp)).llm
    llm._http = httpx.AsyncClient(transport=httpx.MockTransport(server.handle), trust_env=False)
    llm._owns_http_client = True
    return llm, server


def case_binding(task):
    from src.runtime.session import source_fingerprint

    files = [launcher.PROFILE, ROOT / "prompts/swe_b0.md", ROOT / "scripts/run_swe_task.py",
             ROOT / "scripts/rebench/mock_e2e.py"]
    return {"source_fingerprint": source_fingerprint(ROOT),
            "task_sha256": sha256(json.dumps(task, sort_keys=True).encode()).hexdigest(),
            "files": {str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest() for path in files}}


def case_check(task, case, directory: Path):
    from src.eval.candidate_error import expected_empty, regrade_verdict

    summary = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
    bodies = json.loads((directory / "request-bodies.json").read_text(encoding="utf-8"))
    row = launcher.inspect_et(directory)
    problems = list(row["problems"])
    if json.loads((directory / "case-binding.json").read_text(encoding="utf-8")) != case_binding(task):
        problems.append("runtime/profile/task inputs differ from the ET case execution binding")
    issue = legacy.run_checks([summary])
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
    expected_prompt = et_prompt()
    for number, body in enumerate(bodies, 1):
        if body["messages"][0]["content"] != expected_prompt:
            problems.append(f"request {number}: system prompt is not UT plus the E sentence")
        if body.get("max_tokens") != 32768:
            problems.append(f"request {number}: max_tokens is not 32768")
        if body.get("thinking") != {"type": "enabled"} or body.get("reasoning_effort") != "high":
            problems.append(f"request {number}: thinking/effort is not enabled/high")
        if any(m.get("role") == "user" and "remaining model calls:" in str(m.get("content"))
               for m in body["messages"]):
            problems.append(f"request {number}: remaining-calls message appeared")
        assistant = [m for m in body["messages"] if m.get("role") == "assistant"]
        if assistant and not all("reasoning_content" in m for m in assistant):
            problems.append(f"request {number}: thinking replay missing")
        if number >= 3:
            explained = [m for m in assistant[1:] if m.get("tool_calls")]
            if not all(m.get("content") for m in explained):
                problems.append(f"request {number}: scripted visible explanation missing")
    expected_requests = 1 if case == "empty" else 5
    if len(bodies) != expected_requests:
        problems.append("unexpected scripted request count")
    result = {"task": task["id"], "group": launcher.GROUP, "case": case, "ok": not problems,
              "problems": problems, "provider_calls": 0, "scripted_requests": len(bodies),
              "summary": p.brief(summary), "empty_calibration_check": calibration_check,
              "evidence_sha256": {name: sha256((directory / name).read_bytes()).hexdigest()
                                  for name in ("summary.json", "request-bodies.json",
                                               "grader-result.json", "case-binding.json")}}
    save(directory / "check.json", result)
    return result


async def run_case(task, case):
    directory = OUT / "e2e" / task["id"] / launcher.GROUP / case
    if (directory / "summary.json").exists() and (directory / "request-bodies.json").exists():
        return case_check(task, case, directory)
    if directory.exists():
        raise ValueError(f"preserve and reconcile incomplete ET preflight evidence: {directory}")
    config = load_config(launcher.PROFILE)
    llm, server = scripted_client(task, case, config)
    save(directory / "case-binding.json", case_binding(task))
    await run_task(task, launcher.PROFILE, directory, mock=True, variant=launcher.VARIANT,
                   mock_llm=llm, on_agent=legacy.tool_recorder(directory, tap_model=False),
                   ledger_options=legacy.ledger_options(config))
    save(directory / "request-bodies.json",
         [{k: v for k, v in body.items() if k != "_prompt"} for body in server.bodies])
    return case_check(task, case, directory)


async def e2e_all():
    if not launcher.preflight_report()["pass"]:
        raise ValueError("ET configuration, prompt, or slots failed the read-only checks")
    slots, tasks = launcher.load_slots()
    first = min(slots, key=lambda slot: slot["seq"])["task"]
    results = [await run_case(tasks[first], case) for case in ("wiring", "empty")]
    report = {"schema": "fix13et.preflight-e2e.v1", "provider_calls": 0, "expected_cases": 2,
              "completed_cases": len(results), "ok": len(results) == 2 and all(r["ok"] for r in results),
              "cases": results}
    save(E2E_REPORT, report)
    if not report["ok"]:
        raise RuntimeError("ET preflight case requires review")
    return {"cases": len(results), "ok": True}


async def identity_smoke():
    from scripts.fix10_thinking_smoke import call, make_ledger_for_smoke
    from scripts.run_swe_task import make_ledger
    from src.runtime.retry import complete_billing_usage

    report = json.loads(E2E_REPORT.read_text(encoding="utf-8"))
    if report.get("ok") is not True or report.get("completed_cases") != 2:
        raise ValueError("both ET no-model cases must pass before the identity smoke")
    directory = OUT / "identity-smoke"
    directory.mkdir(parents=True, exist_ok=False)
    page = launcher.fetch_price_page()
    snapshot = legacy.price_snapshot(page)
    legacy.archive_price(directory, "before", page, snapshot, legacy.now_utc())
    reference = (json.loads(launcher.BASE_IDENTITY.read_text(encoding="utf-8"))["price_snapshot"]
                 if launcher.BASE_IDENTITY.exists() else None)
    problem = legacy.price_problem(snapshot, reference)
    if problem:
        raise ValueError(problem)
    ledger_path = directory / "cny-ledger.json"
    ledger = make_ledger_for_smoke(make_ledger(None).price, ledger_path)
    ledger.time_of_day = False
    ledger.persist()
    records, responses = [], []
    for name, profile in (("UT", launcher.ORIGINAL_PROFILES["UT"]), (launcher.GROUP, launcher.PROFILE)):
        config = load_config(profile)
        with tempfile.TemporaryDirectory() as tmp:
            client = build_agent(config, Path(tmp)).llm
        try:
            response = await call(ledger, client, records, "identity-" + name,
                                  [{"role": "user", "content": "Reply with OK."}],
                                  max_tokens=2048, stream=True)
        finally:
            await client.aclose()
        save(directory / "smoke.json", {"cases": records, "standard_peak_cny": str(ledger.cost_committed)})
        if (response is None or records[-1]["settlement"].get("status") != "provider_usage"
                or not complete_billing_usage(response.usage) or response.finish_reason == "length"):
            raise ValueError("ET identity request failed, truncated, or has unknown usage")
        responses.append(response.provider_metadata)
    page = launcher.fetch_price_page()
    current = legacy.price_snapshot(page)
    legacy.archive_price(directory, "after", page, current, legacy.now_utc())
    problem = legacy.price_problem(current, snapshot)
    if problem:
        raise ValueError(problem)
    models = {r.get("response_model") for r in responses}
    prints = {r.get("system_fingerprint") for r in responses}
    if models != {legacy.EXPECTED_MODEL} or len(prints) != 1 or None in prints or "" in prints:
        raise ValueError("ET identity model or fingerprint differs between UT and ET")
    fingerprint = next(iter(prints))
    if fingerprint != launcher.ORIGINAL_FINGERPRINT:
        raise ValueError(f"identity fingerprint {fingerprint} differs from the original FIX-13 window "
                         f"{launcher.ORIGINAL_FINGERPRINT}; do not start the ET batch")
    result = {"ok": True, "model": next(iter(models)), "system_fingerprint": fingerprint,
              "price_snapshot": snapshot, "ledger_path": ledger_path.relative_to(ROOT).as_posix(),
              "standard_peak_cny": str(ledger.cost_committed), "requests": ledger.calls_started,
              "original_fingerprint": launcher.ORIGINAL_FINGERPRINT,
              "completed_at": datetime.now(timezone.utc).isoformat()}
    save(launcher.IDENTITY, result)
    return result


def ready():
    from src.runtime.session import source_fingerprint

    slots, tasks = launcher.load_slots()
    e2e = json.loads(E2E_REPORT.read_text(encoding="utf-8"))
    identity = json.loads(launcher.IDENTITY.read_text(encoding="utf-8"))
    refs = json.loads(REFERENCES.read_text(encoding="utf-8"))
    analysis = json.loads(ANALYSIS_BINDING.read_text(encoding="utf-8"))
    if (not e2e.get("ok") or e2e.get("completed_cases") != 2 or not identity.get("ok")
            or identity.get("system_fingerprint") != launcher.ORIGINAL_FINGERPRINT
            or set(refs["tasks"]) != set(tasks) or not launcher.preflight_report()["pass"]):
        raise ValueError("incomplete ET preflight evidence")
    for name, expected in analysis["sources"].items():
        if sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError("offline analysis code changed: " + name)
    paths = [launcher.PLAN, launcher.PROFILE, launcher.MANIFEST, launcher.INGEST, launcher.BASE_PLAN,
             launcher.BASE_READY, *launcher.ORIGINAL_PROFILES.values(), REFERENCES, ANALYSIS_BINDING,
             E2E_REPORT, launcher.IDENTITY, Path(__file__), Path(launcher.__file__),
             Path(launcher.base.__file__), ROOT / "prompts/swe_b0.md", ROOT / identity["ledger_path"]]
    paths += [ROOT / name for name in analysis["sources"]]
    for case in e2e["cases"]:
        directory = OUT / "e2e" / case["task"] / case["group"] / case["case"]
        for name, expected in case["evidence_sha256"].items():
            path = directory / name
            if sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError("ET preflight evidence changed: " + str(path))
            paths.append(path)
    for task in tasks.values():
        paths += [ROOT / task[key] for key in ("receipt", "clean_record", "public_task")]
        paths.append((ROOT / task["receipt"]).parent / "state.json")
    result = {"schema": "fix13et.ready.v1", "ok": True, "tasks": len(tasks), "slots": len(slots),
              "e2e_cases": 2, "source_fingerprint": source_fingerprint(ROOT),
              "created_at": datetime.now(timezone.utc).isoformat(),
              "sources": {path.resolve().relative_to(ROOT).as_posix(): sha256(path.read_bytes()).hexdigest()
                          for path in paths}}
    save(launcher.READY, result)
    launcher.readiness()
    return {"ok": True, "tasks": len(tasks), "slots": len(slots), "e2e_cases": 2,
            "ready_sha256": sha256(launcher.READY.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--e2e", action="store_true")
    mode.add_argument("--identity", action="store_true")
    mode.add_argument("--ready", action="store_true")
    args = parser.parse_args()
    if args.preflight:
        report = launcher.preflight_report()
        print(json.dumps(report, indent=2))
        raise SystemExit(0 if report["pass"] else 1)
    if args.e2e:
        print(json.dumps(asyncio.run(e2e_all()), indent=2))
    elif args.identity:
        print(json.dumps(asyncio.run(identity_smoke()), indent=2))
    else:
        print(json.dumps(ready(), indent=2))


if __name__ == "__main__":
    main()
