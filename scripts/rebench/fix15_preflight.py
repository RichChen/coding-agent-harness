"""FIX-15 no-model Docker wiring checks.

Runs one scripted tool trajectory through each FIX-15 profile.  The model
transport is local and scripted.  Docker, grading, candidate export, and the
run ledger are exercised, but no provider request or paid identity smoke is
allowed here.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
from hashlib import sha256
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.rebench import run_fix15 as launcher  # noqa: E402
from scripts.rebench.mock_e2e import brief, gold_tool_calls  # noqa: E402
from scripts.rebench import run_unleash as legacy  # noqa: E402
from scripts.rebench.unleash_mock_e2e import ScriptedServer  # noqa: E402
from scripts.run_swe_task import run_task  # noqa: E402
from src.harness.factory import build_agent, load_config  # noqa: E402

OUT = ROOT / "runs" / "fix15-preflight"
PREFLIGHT_TASK = "ome-zarr-models__ome-zarr-models-py-206"


def save(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


class Server(ScriptedServer):
    def __init__(self, group: str, task: dict):
        super().__init__(group, gold_tool_calls(task))
        self.group = group

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        turn = len(self.bodies)
        if turn <= 2:
            calls = [(f"read-{turn}", "list_dir", {"path": "."})]
        elif turn == 3:
            calls = [(call.id, call.name, call.arguments) for call in self.gold_calls]
        else:
            calls = []
        delta = {"role": "assistant", "content": "I will take the next tool action." if calls else "Done."}
        chunks = [{"choices": [{"index": 0, "delta": delta, "finish_reason": None}]}]
        for index, (call_id, name, arguments) in enumerate(calls):
            chunks.append({"choices": [{"index": 0, "finish_reason": None, "delta": {"tool_calls": [
                {"index": index, "id": call_id, "type": "function",
                 "function": {"name": name, "arguments": json.dumps(arguments)}}]}}]})
        chunks.append({"choices": [{"index": 0, "delta": {},
                                     "finish_reason": "tool_calls" if calls else "stop"}]})
        chunks.append({"choices": [], "usage": self.usage(request.content, "")})
        common = {"id": f"fix15-preflight-{turn}", "object": "chat.completion.chunk",
                  "model": "deepseek-flash-mock", "system_fingerprint": "fp_fix15_mock"}
        text = "".join("data: " + json.dumps({**common, **chunk}) + "\n\n" for chunk in chunks)
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=(text + "data: [DONE]\n\n").encode())


def scripted_client(group: str, task: dict):
    server = Server(group, task)
    config = json.loads(json.dumps(load_config(launcher.PROFILES[group])))
    config["llm"]["api_key"] = "local-scripted-preflight"
    with tempfile.TemporaryDirectory() as tmp:
        llm = build_agent(config, Path(tmp)).llm
    llm._http = httpx.AsyncClient(transport=httpx.MockTransport(server.handle), trust_env=False)
    llm._owns_http_client = True
    return llm, server


def case_binding(task: dict, group: str) -> dict:
    paths = [launcher.PROFILES[group], ROOT / "prompts/swe_b0.md", ROOT / "scripts/run_swe_task.py",
             ROOT / "scripts/rebench/run_fix15.py", Path(__file__)]
    return {"source_fingerprint": __import__("src.runtime.session", fromlist=["source_fingerprint"]).source_fingerprint(ROOT),
            "task_sha256": sha256(json.dumps(task, sort_keys=True).encode()).hexdigest(),
            "files": {str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest() for path in paths}}


async def run_case(task: dict, group: str) -> dict:
    root = OUT / "e2e" / task["id"] / group
    directory = root / "wiring"
    if directory.exists():
        shutil.rmtree(directory)
    config = load_config(launcher.PROFILES[group])
    llm, server = scripted_client(group, task)
    save(directory / "case-binding.json", case_binding(task, group))
    summary = await run_task(task, launcher.PROFILES[group], directory, mock=True,
                             variant=launcher.VARIANTS[group], mock_llm=llm,
                             ledger_options=legacy.ledger_options(config))
    save(directory / "request-bodies.json", [{k: v for k, v in body.items() if k != "_prompt"}
                                               for body in server.bodies])
    problems = []
    if summary.get("status") not in ("pass", "fail", "partial") or summary.get("solved") is not True:
        problems.append("scripted gold trajectory did not produce a solved run")
    if summary.get("run_error") or summary.get("infra_reason"):
        problems.append("run reported an error: " + str(summary.get("run_error") or summary.get("infra_reason")))
    bodies = json.loads((directory / "request-bodies.json").read_text(encoding="utf-8"))
    for number, body in enumerate(bodies, 1):
        if (body.get("thinking") or {}).get("type") != "disabled":
            problems.append(f"request {number}: thinking is not disabled")
        if body.get("max_tokens") != 8192:
            problems.append(f"request {number}: max_tokens differs")
        if any("reasoning_content" in message for message in body.get("messages", []) if message.get("role") == "assistant"):
            problems.append(f"request {number}: reasoning replay appeared with thinking disabled")
    ledger_path = directory / "cny-ledger.json"
    if not ledger_path.is_file():
        problems.append("cny-ledger.json missing")
    else:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        if ledger.get("outstanding") != 0:
            problems.append("ledger has outstanding reservation")
    result = {"task": task["id"], "group": group, "case": "wiring", "evidence_dir": str(directory.relative_to(OUT)),
              "ok": not problems, "provider_calls": 0, "scripted_requests": len(bodies),
              "problems": problems, "summary": brief(summary),
              "evidence_sha256": {name: sha256((directory / name).read_bytes()).hexdigest()
                                   for name in ("summary.json", "request-bodies.json", "case-binding.json", "cny-ledger.json")}}
    save(directory / "check.json", result)
    return result


async def all_cases() -> dict:
    slots, manifest = launcher.load_slots()
    task_id = PREFLIGHT_TASK if PREFLIGHT_TASK in manifest else slots[0]["task"]
    task = manifest[task_id]
    results = [await run_case(task, group) for group in launcher.GROUPS]
    report = {"schema": "fix15.preflight-e2e.v1", "provider_calls": 0,
              "task": task_id, "groups": list(launcher.GROUPS), "completed_cases": len(results),
              "ok": len(results) == 3 and all(row["ok"] for row in results), "cases": results}
    save(OUT / "e2e-report.json", report)
    return report


async def identity_smoke() -> dict:
    """Run the separately authorized two-request identity check for FIX-15."""
    from scripts.fix10_thinking_smoke import call, make_ledger_for_smoke
    from scripts.run_swe_task import make_ledger
    from src.runtime.retry import complete_billing_usage

    report_path = OUT / "e2e-report.json"
    if not report_path.is_file():
        raise ValueError("run --all before the identity smoke")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("ok") is not True or report.get("completed_cases") != 3:
        raise ValueError("all three FIX-15 no-model cases must pass before the identity smoke")
    base_identity = json.loads((ROOT / "runs/fix13-preflight/identity.json").read_text(encoding="utf-8"))
    directory = OUT / "identity-smoke"
    directory.mkdir(parents=True, exist_ok=False)

    page = httpx.get(legacy.PRICE_URL, timeout=60, follow_redirects=True, trust_env=False).text
    snapshot = legacy.price_snapshot(page)
    legacy.archive_price(directory, "before", page, snapshot, legacy.now_utc())
    problem = legacy.price_problem(snapshot, base_identity["price_snapshot"])
    if problem:
        raise ValueError(problem)

    ledger_path = directory / "cny-ledger.json"
    ledger = make_ledger_for_smoke(make_ledger(None).price, ledger_path)
    ledger.time_of_day = False
    ledger.persist()
    records, responses = [], []
    for group in ("U-new", "E-new"):
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
            raise ValueError("FIX-15 identity request failed, truncated, or has unknown usage")
        reasoning = (response.usage or {}).get("completion_tokens_details") or {}
        if reasoning.get("reasoning_tokens"):
            raise ValueError("FIX-15 identity response reported reasoning tokens with thinking disabled")
        responses.append(response.provider_metadata)

    page = httpx.get(legacy.PRICE_URL, timeout=60, follow_redirects=True, trust_env=False).text
    current = legacy.price_snapshot(page)
    legacy.archive_price(directory, "after", page, current, legacy.now_utc())
    problem = legacy.price_problem(current, snapshot)
    if problem:
        raise ValueError(problem)
    models = {record.get("response_model") for record in responses}
    fingerprints = {record.get("system_fingerprint") for record in responses}
    if models != {legacy.EXPECTED_MODEL} or len(fingerprints) != 1 or None in fingerprints or "" in fingerprints:
        raise ValueError("FIX-15 identity model or fingerprint differs between U-new and E-new")
    fingerprint = next(iter(fingerprints))
    if fingerprint != base_identity["system_fingerprint"]:
        raise ValueError(f"identity fingerprint {fingerprint} differs from the FIX-13 window "
                         f"{base_identity['system_fingerprint']}")
    result = {"ok": True, "model": next(iter(models)), "system_fingerprint": fingerprint,
              "price_snapshot": snapshot, "ledger_path": ledger_path.relative_to(ROOT).as_posix(),
              "standard_peak_cny": str(ledger.cost_committed), "requests": ledger.calls_started,
              "reference_fingerprint": base_identity["system_fingerprint"],
              "completed_at": __import__("datetime").datetime.now(
                  __import__("datetime").timezone.utc).isoformat()}
    save(directory / "identity.json", result)
    return result


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--identity", action="store_true", help="authorized two-request paid smoke, cap 0.5 CNY")
    args = parser.parse_args()
    if args.all == args.identity:
        parser.error("use exactly one of --all or --identity")
    result = asyncio.run(all_cases()) if args.all else asyncio.run(identity_smoke())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
