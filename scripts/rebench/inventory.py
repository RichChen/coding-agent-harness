"""Describe the SWE-rebench leaderboard dataset downloaded by fetch.py. No model calls, no Docker.

Writes runs/rebench/inventory.json (per-instance flat rows, not committed) and scripts/rebench/out/inventory.json
(aggregates: fields vs SWE-bench, counts by repo / month / license / python / test command, date thresholds).
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
DATA = ROOT / "runs" / "rebench" / "data"
OUT = Path(__file__).resolve().parent / "out"

SWEBENCH_FIELDS = ["instance_id", "repo", "base_commit", "patch", "test_patch", "problem_statement", "hints_text",
                   "created_at", "version", "FAIL_TO_PASS", "PASS_TO_PASS", "environment_setup_commit"]
# Cutoff thresholds used in the contamination count (see SWE-REBENCH-FEASIBILITY.zh-CN.md for sources).
THRESHOLDS = {
    "official_latest_collection_2025_05": "2025-06-01",
    "v4_1_flash_release_2026_09_10": "2026-09-11",
}


def plain(value):
    if hasattr(value, "tolist") and not isinstance(value, (str, bytes)):
        value = value.tolist()
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def as_obj(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return plain(value)


def as_list(value) -> list:
    value = as_obj(value)
    if value is None:
        return []
    return list(value)


def load() -> pd.DataFrame:
    return pd.read_parquet(DATA / "test-0.parquet")


def flat(row) -> dict:
    cfg = as_obj(row["install_config"]) or {}
    meta = as_obj(row["meta"]) or {}
    f2p, p2p = as_list(row["FAIL_TO_PASS"]), as_list(row["PASS_TO_PASS"])
    return dict(
        instance_id=row["instance_id"], repo=row["repo"], created_at=str(row["created_at"]),
        month=str(row["created_at"])[:7], version=row["version"], base_commit=row["base_commit"],
        environment_setup_commit=row["environment_setup_commit"],
        license=row.get("license_name") if "license_name" in row else None,
        docker_image=row.get("docker_image"), f2p=len(f2p), p2p=len(p2p),
        f2p_has_param=sum("[" in x for x in f2p), f2p_not_pytest=sum("::" not in x for x in f2p),
        python=str(cfg.get("python")), log_parser=cfg.get("log_parser"), test_cmd=cfg.get("test_cmd"),
        install=cfg.get("install"), pre_install=cfg.get("pre_install") or [], packages=cfg.get("packages"),
        pip_packages=cfg.get("pip_packages") or [], reqs_path=cfg.get("reqs_path") or [],
        env_yml_path=cfg.get("env_yml_path") or [], docker_specs=cfg.get("docker_specs"),
        env_vars=cfg.get("env_vars"), no_use_env=cfg.get("no_use_env"),
        is_lite=meta.get("is_lite"), num_modified_files=meta.get("num_modified_files"),
        failed_lite_validators=meta.get("failed_lite_validators") or [],
        patch_bytes=len(row["patch"] or ""), test_patch_bytes=len(row["test_patch"] or ""),
        hints_nonempty=bool((row["hints_text"] or "").strip()),
    )


def test_cmd_kind(cmd: str | None) -> str:
    if not cmd:
        return "none"
    head = cmd.strip().split()
    if not head:
        return "none"
    if "pytest" in cmd:
        return "pytest"
    return head[0]


DIRECT_PYTEST = ("pytest ", "python -m pytest ", "python3 -m pytest ")


def subset(rows: list[dict]) -> dict:
    """Static filters for tasks the existing image path (plus the rebench grader) can take."""

    def unbalanced(node: str) -> bool:
        return node.count("[") != node.count("]")

    data = json.loads((ROOT / "runs" / "rebench" / "f2p_lists.json").read_text(encoding="utf-8"))
    steps, current = [], list(rows)

    def step(name, keep):
        nonlocal current
        current = [r for r in current if keep(r)]
        steps.append({"filter": name, "remaining": len(current), "repos": len({r["repo"] for r in current})})

    steps.append({"filter": "all", "remaining": len(current), "repos": len({r["repo"] for r in current})})
    step("direct pytest command (no uv/hatch/tox wrapper)",
         lambda r: (r["test_cmd"] or "").strip().startswith(DIRECT_PYTEST)
         or ((r["test_cmd"] or "").startswith("env ") and " pytest " in r["test_cmd"]))
    step("every F2P id is a pytest node id", lambda r: r["f2p_not_pytest"] == 0 and r["f2p"] > 0)
    step("no conda environment.yml install", lambda r: not r["env_yml_path"])
    step("created after official cutoff (>= 2025-06-01)", lambda r: r["created_at"] >= "2025-06-01")
    truncated = [r for r in current if any(unbalanced(n) for n in data[r["instance_id"]]["f2p"])]
    truncated_p2p = [r for r in current if any(unbalanced(n) for n in data[r["instance_id"]]["p2p"])]
    return {"steps": steps, "final": len(current), "final_repos": len({r["repo"] for r in current}),
            "final_by_month": dict(sorted(Counter(r["month"] for r in current).items())),
            "final_top_repos": Counter(r["repo"] for r in current).most_common(15),
            "final_with_truncated_f2p": len(truncated), "final_with_truncated_p2p": len(truncated_p2p),
            "final_pre_install_apt": sum(bool(r["pre_install"]) for r in current),
            "final_python": Counter(r["python"] for r in current).most_common()}


def main() -> None:
    df = load()
    columns = list(df.columns)
    rows = [flat(r) for _, r in df.iterrows()]
    (ROOT / "runs" / "rebench").mkdir(parents=True, exist_ok=True)
    lists = {r["instance_id"]: {"f2p": as_list(r["FAIL_TO_PASS"]), "p2p": as_list(r["PASS_TO_PASS"])}
             for _, r in df.iterrows()}
    (ROOT / "runs" / "rebench" / "f2p_lists.json").write_text(json.dumps(lists) + "\n", encoding="utf-8")
    all_truncated = sum(any(n.count("[") != n.count("]") for n in v["f2p"]) for v in lists.values())
    (ROOT / "runs" / "rebench" / "inventory.json").write_text(json.dumps(rows, indent=1, default=str) + "\n",
                                                             encoding="utf-8")
    monthly = {}
    for path in sorted(DATA.glob("20*-0.parquet")):
        monthly[path.name.split("-")[0]] = len(pd.read_parquet(path, columns=["instance_id"]))
    ids = [r["instance_id"] for r in rows]
    repos = Counter(r["repo"] for r in rows)
    agg = dict(
        dataset=json.loads((DATA / "fetch.json").read_text(encoding="utf-8")),
        rows=len(rows), unique_ids=len(set(ids)), columns=columns,
        swebench_fields_present={f: f in columns for f in SWEBENCH_FIELDS},
        extra_fields=[c for c in columns if c not in SWEBENCH_FIELDS],
        monthly_split_rows=monthly, monthly_split_total=sum(monthly.values()),
        repos=len(repos), repos_top=repos.most_common(40),
        repo_task_histogram=dict(sorted(Counter(repos.values()).items())),
        months=dict(sorted(Counter(r["month"] for r in rows).items())),
        created_min=min(r["created_at"] for r in rows), created_max=max(r["created_at"] for r in rows),
        licenses=Counter(r["license"] for r in rows).most_common(),
        python=Counter(r["python"] for r in rows).most_common(),
        log_parser=Counter(r["log_parser"] for r in rows).most_common(),
        test_cmd_kind=Counter(test_cmd_kind(r["test_cmd"]) for r in rows).most_common(),
        test_cmd_top=Counter(r["test_cmd"] for r in rows).most_common(12),
        has_docker_image=sum(bool(r["docker_image"]) for r in rows),
        docker_image_prefix=Counter((r["docker_image"] or "").split("/")[0] for r in rows).most_common(),
        pre_install_nonempty=sum(bool(r["pre_install"]) for r in rows),
        packages=Counter(str(r["packages"]) for r in rows).most_common(10),
        env_yml=sum(bool(r["env_yml_path"]) for r in rows),
        docker_specs_set=sum(r["docker_specs"] is not None for r in rows),
        env_vars_set=sum(bool(r["env_vars"]) for r in rows),
        is_lite=Counter(str(r["is_lite"]) for r in rows).most_common(),
        f2p_median=float(pd.Series([r["f2p"] for r in rows]).median()),
        p2p_median=float(pd.Series([r["p2p"] for r in rows]).median()),
        f2p_not_pytest_node=sum(r["f2p_not_pytest"] > 0 for r in rows),
        env_commit_differs=sum(r["environment_setup_commit"] != r["base_commit"] for r in rows),
        hints_nonempty=sum(r["hints_nonempty"] for r in rows),
        tasks_with_truncated_f2p=all_truncated,
        tasks_with_plugin_in_install=sum(bool(re.search(r"pytest-(asyncio|mock|socket|xdist|timeout|django|httpx|trio|anyio)",
                                                        r["install"] or "")) for r in rows),
        after_threshold={name: dict(count=sum(r["created_at"] >= day for r in rows),
                                    repos=len({r["repo"] for r in rows if r["created_at"] >= day}),
                                    top=Counter(r["repo"] for r in rows if r["created_at"] >= day).most_common(15))
                         for name, day in THRESHOLDS.items()},
    )
    agg["subset"] = subset(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "inventory.json").write_text(json.dumps(agg, indent=2, default=str, ensure_ascii=False) + "\n",
                                        encoding="utf-8")
    print(json.dumps({k: v for k, v in agg.items() if k not in ("repos_top", "dataset", "test_cmd_top")},
                     indent=1, default=str, ensure_ascii=False))
    print("test_cmd_top", json.dumps(agg["test_cmd_top"], indent=1))
    print("repos_top", agg["repos_top"][:25])


if __name__ == "__main__":
    main()
