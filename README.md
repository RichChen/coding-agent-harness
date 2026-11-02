# Coding Agent Harness

**English** · [中文](README.zh-CN.md)

A verifiable runtime and evaluation harness for code-repair agents: given a repository issue, the agent localizes, edits and self-tests inside an isolated container, and delivers a candidate patch together with its validation record; the same harness grades the result with independent hidden tests and uses concurrent controlled experiments to decide whether a change really improves repair.

## Components

| Capability | How it works | Code |
| --- | --- | --- |
| Run and recovery | A tool's intent is committed before it runs; on resume, committed results are reused, and a non-idempotent operation whose outcome was never recorded stops the run instead of being retried. Verified with real subprocesses crashing in four windows (`os._exit(71)`). | [`runtime/session.py`](harness/src/runtime/session.py), [`runtime/recovery.py`](harness/src/runtime/recovery.py), [`tracing/store.py`](harness/src/tracing/store.py), [`tests/test_recovery.py`](harness/tests/test_recovery.py) |
| Answer isolation | An early image shipped the test patch inside `/opt/repo`, readable by the model. The image is now built from clean source, so the agent can only change the work tree; grading starts a separate container that applies the candidate delta and a test overlay, then runs FAIL_TO_PASS / PASS_TO_PASS. | [`execution/baseline_image_backend.py`](harness/src/execution/baseline_image_backend.py), [`eval/image_grader.py`](harness/src/eval/image_grader.py) |
| Code retrieval | Answers "which files should be read": it reads only the base-commit snapshot (a bare git clone, no checkout), chunks by function/class with the AST, ranks with BM25 and code embeddings, fuses the two with RRF, and returns paths, symbols and line numbers only. | [`src/retrieval/`](harness/src/retrieval/), [CR-1 results](harness/docs/retrieval/CR-1-RESULTS.zh-CN.md) |
| Workspace snapshot and validation | Answers "can the edited tree be trusted": after checking that no process in the container is still writing (quiescence check) it takes an immutable snapshot; it applies the candidate in a fresh replica of the same image and runs frozen public tests; on failure the model gets bounded feedback (at most 2 rounds by default). Optional, off by default, and currently fitted to one pinned Sphinx image only. | [`src/workspace/`](harness/src/workspace/), [`src/validation/`](harness/src/validation/) |
| Completion control | Answers "a run that stops midway leaves nothing behind": whether the run finishes normally, hits the step or budget limit, times out or is cancelled, the candidate patch and its validation record are sealed and exported; run status and acceptance result are reported separately. | [`runtime/completion.py`](harness/src/runtime/completion.py) |
| Recomputable evaluation | The A5/A6 analysis scripts aggregate per task from the run inventory and compute bootstrap intervals; candidate failures stay in the denominator, and the rules for infrastructure failures plus any post-hoc deviation are disclosed separately. | [`analyze_a5_main.py`](agent/scripts/analyze_a5_main.py), [`analyze_a6_retry.py`](agent/scripts/analyze_a6_retry.py) |

## Results

CR-2, A5 and A6-R use the same harness and the same model (deepseek-flash, at most 40 model calls per run) on SWE-bench Verified tasks; CR-1 is offline retrieval and calls no model. Repair-rate differences are in percentage points with 95% intervals; an interval that crosses 0 is reported as "no difference detected".

| Experiment | What is compared | Scale | Result |
| --- | --- | --- | --- |
| CR-1, offline code retrieval | hybrid (BM25 + code embeddings, RRF fusion) vs BM25, file-level localization | 400 test tasks, frozen configuration evaluated once | Hit@5 went from 67.5% (BM25) to 85.8% (hybrid), MRR 0.520 → 0.684; under both bootstrap schemes the intervals exclude 0. This is a localization metric, not a repair rate; at file level, hybrid vs embedding alone shows no difference detected ([CR-1 results](harness/docs/retrieval/CR-1-RESULTS.zh-CN.md), sections 1 and 2.1) |
| CR-2, retrieval in the agent | N: no retrieval; P: frozen retrieval results appended to the first message; T: one extra retrieval tool, `retrieve_code`; O: the correct files given directly, as an upper-bound reference | 56 tasks × 4 arms × 5 runs = 1120 concurrent runs | Repair rate P − N +2.9 [−3.2, 9.6], T − N −0.7 [−5.4, 3.9], O − N +2.1 [−5.4, 9.6]; no difference detected for any of them. Under the original system prompt the retrieval tool was called in 0/280 runs ([CR-2 results](harness/docs/retrieval/CR-2-RESULTS.zh-CN.md), conclusions and sections 2 and 4) |
| A5, cross-task memory | Cards from earlier, already-solved tasks in the same repository (B2) vs none (B0); low relevance to the task (9/51 tasks pointed at the gold file) | 56 tasks, 3 runs per task per arm | Memory − no memory −1.8 [−8.3, +4.8], p = 0.72 |
| A6-R, same-task memory | Second attempt at a failed task: rule-based record of the first attempt plus "it did not pass" (R1) vs no record (R0); 100% relevant by construction | 53 failed first attempts on 22 tasks, 159 runs per arm | Memory − no memory −6.6 [−17.7, +3.8], p = 0.29 |

The A5 and A6-R numbers are from sections 1 and 6.1 of the [final report](agent/docs/MEMORY-FINAL-REPORT.zh-CN.md).

Offline retrieval clearly improved localization; on this task set, neither memory nor retrieval translated into a higher repair rate. Even giving the agent the correct files produced no detectable change in repair rate, so the next step moved to a verification loop after the repair.

## Running it

No Docker, no SWE-bench image, no key. Steps 1 and 2 were run on a clean checkout of HEAD.

**1. Offline demo** (seconds, no model calls)

```bash
cd agent && pip install -e ".[dev]"
python -m reposkill_agent upgrade-demo --output reports/upgrade/my-demo
```

`--output` must not already exist. The demo writes a public workspace, a `memory.db` with per-record status and provenance, a manifest, `result.json` and `report.md`; the output includes `Offline comparisons: 20; model calls=0`. When a dependency or API signature changes the old record is rejected, unrelated file changes keep it valid, and missing evidence yields unknown. Every injection receipt comes from a fixture host, so it claims no real-model gain.

**2. Public tests** (about 4 minutes for both directories)

```bash
# in agent/
python -m pytest -q --deselect tests/test_a5_funding.py::test_funded_build_bundle_is_prepared_only_and_private_material_stays_outside_public \
  --deselect tests/test_a5_funding.py::test_funded_build_artifacts_are_hashed \
  --deselect tests/test_memory_cards_v5.py::test_generic_builder_matches_v5_bytes
# 354 passed, 10 skipped, 3 deselected

# in harness/
cd ../harness
pip install -e ".[dev]"
python -m pytest -q --deselect tests/test_a5_real240_h0_snapshot.py::test_h0_export_and_audit_via_cli \
  --deselect tests/test_repo_grader.py::test_full_archive_and_private_test_resources_are_complete \
  --deselect tests/test_summary_pilot.py::test_prepare_frozen_data_and_window_expectations \
  --deselect tests/test_summary_pilot.py::test_trace_accounting_replay_and_private_grading
# 1249 passed, 44 skipped, 4 deselected
```

The 54 skips fall into three groups: 39 need real Linux or Docker (opt-in Docker execution tests, pinned images cached on this machine, real Linux process groups); 12 depend on this machine's layout or data (a sibling `../coding-harness` checkout, `runs/a5-main`, the private A5 memory pool, the source pool, symlink permission, `runs/sprint5-main-public`) and skip automatically in a clean clone; 3 are agent's `test_a5_preparation.py`, which needs `pyarrow`, not an agent dependency, and runs once it is installed. On the first test run, harness downloads one SWE-bench specs file from GitHub and caches it in `harness/runs/sprint5-specs/`.

**3. Tests that need unpublished material (7)**

These read evidence files that only existed on the original machine and were never committed. They are not code regressions, and they are not skipped:

| Test | Missing file | Why it is not in the repo |
| --- | --- | --- |
| harness `test_a5_real240_h0_snapshot.py::test_h0_export_and_audit_via_cli` | `validated-memory-records.jsonl` from the A5 memory pool | private build output; `.jsonl` is not tracked |
| harness `test_repo_grader.py::test_full_archive_and_private_test_resources_are_complete` | `full-repo-grader/attempt-02/flask-base.tar.gz` | full upstream archive, too large |
| harness `test_summary_pilot.py`, two tests | `H5/deepseek-flash-attempt-01/seed/boltons/strutils.py` | R2 seed file, kept local only |
| agent `test_a5_funding.py`, two tests | `reports/upgrade/a5-funded-build-preparation-20260921-v12/*` | `reports/` holds run output and is not tracked |
| agent `test_memory_cards_v5.py::test_generic_builder_matches_v5_bytes` | the django-10914 handoff field | depends on harness-side output; needs both repos |

Put the files at those paths and drop `--deselect` to run everything. In addition, harness `test_public_target.py::test_task_is_complete_public_input_and_manifest_detects_material_change` reads the untracked `runs/sprint5-main-public/sphinx-doc__sphinx-8721.json` and skips automatically when it is absent; it is counted in the skips above. Section 4 of `harness/docs/INDEX.md` and `agent/docs/INDEX.md` describe the same split.

**4. Demos that need local images or caches**

These two demos do not run on a fresh clone:

- `python scripts/run_workspace.py --scripted-demo --output-dir <new directory>` (in `harness/`): goes through workspace snapshot, public validation, bounded feedback and candidate export with scripted model replies. It needs the pinned Sphinx Docker image already present on the machine (pinned by digest, never pulled).
- `python scripts/cr_demo.py sympy__sympy-19040 --show-gold` (in `harness/`): shows the BM25, embedding and hybrid rankings for one task and the rank of the gold files. It needs the CR repository cache and embedding cache.

Committed output you can read without running anything:

- the two examples under [`harness/docs/retrieval/demo/`](harness/docs/retrieval/demo/): [prefetch (arm P)](harness/docs/retrieval/demo/cr2-p-sympy__sympy-19040.md) and [retrieval tool (CR-2e round 2)](harness/docs/retrieval/demo/cr2e-t2-sympy__sympy-19040.md);
- the small evidence files under [`harness/docs/roadmap/evidence/`](harness/docs/roadmap/evidence/) (summaries and hashes for each stage from P2 to P4).

## Method

- **Answer isolation.** An early dev image contained the hidden tests; after rebuilding it from clean source, the solve rate on the same 14 dev tasks went from 32/42 to 18/42 ([project history](harness/docs/PROJECT-HISTORY.zh-CN.md), sprint rounds 3–4).
- **Wrapper experiment.** With the same 9 fixed summaries, wording them as "possibly wrong historical evidence, not instructions" gave 26/27 fully correct answers, against 7/27 with the original wording ([evidence](harness/docs/upgrade/evidence/R2-real/wrapper-01/README.md)).
- **Preregistration and freeze tags.** Hypotheses, the single primary analysis, seeds and stopping rules were committed and tagged before any real data existed ([A6-R preregistration](agent/docs/sprint/A6-R-PREREG.zh-CN.md)).
- **Freeze, then evaluate once.** CR-1 chose its embedding model and fusion parameters on the dev set, then was tagged and frozen (`harness/cr1-freeze`); the 400 test tasks were evaluated once ([CR-1 freeze](harness/docs/retrieval/CR-1-FREEZE.md)).
- **Injection consistency check.** After each run, the first message the model actually received was compared verbatim with the expected text: A5 168/168, A6-R 318/318 ([final report](agent/docs/MEMORY-FINAL-REPORT.zh-CN.md), section 3).
- **Concurrent interleaving.** Both arms ran from one shuffled queue (seed 20261013 in A6-R) in the same time window ([A6-R preregistration](agent/docs/sprint/A6-R-PREREG.zh-CN.md), section 1).

## Repository map

- `harness/` — **the main execution path**: the agent loop, tools, isolated execution (Docker plus host-side grading), session recovery, code retrieval, workspace snapshots, public validation, completion control, the evaluation suite and the run scripts. Every A5/A6 and CR-2 run is started, executed and graded here.
- `agent/` — **the memory side**: RepoSkill memory cards and the injection plugin, validity checking, the experiment analysis scripts, and a historical multi-role prototype (`reposkill_agent/agents/`, used by the earlier planner experiments and not part of the A5/A6 main path).

Where to start (most documents are in Chinese):

- [Final report on both memory experiments](agent/docs/MEMORY-FINAL-REPORT.zh-CN.md)
- [Harness README](harness/README.md) · [harness doc index](harness/docs/INDEX.md)
- [agent doc index](agent/docs/INDEX.md)
- [Project history](harness/docs/PROJECT-HISTORY.zh-CN.md)

## Reproduction

The two directories were developed and frozen as two sibling repositories, `coding-harness` and `coding-agent`, and the code refers to the other repository through that layout; some configs and evidence files also record absolute paths from the original machine. These paths were not rewritten here, so that the frozen tags still point at the code that actually ran. This repository is now named `coding-agent-harness`; the repository names and paths below are the historical ones. The tags keep the original commits, so a frozen state can be checked out in the original layout:

```
git worktree add ../coding-harness harness/a6-retry-freeze
git worktree add ../coding-agent agent/a6-retry-freeze-r2
```

Freeze tags:

- A5: `harness/a5-main-freeze`, `harness/a5-main-freeze-r1` to `-r4`, `agent/a5-main-freeze`
- A6-R: `harness/a6-retry-freeze`, `agent/a6-retry-freeze`, `agent/a6-retry-freeze-r1`, `agent/a6-retry-freeze-r2`
- CR-1: `harness/cr1-freeze`
- CR-2: `harness/cr2-freeze-r3` (the version used for the CR-2 runs); for the other revisions see [CR-2-FREEZE](harness/docs/retrieval/CR-2-FREEZE.md)

The tags point to public snapshot commits whose code is byte-identical to the original freeze commits; the mapping to the commit hashes quoted in the documents is in the [doc index](harness/docs/INDEX.md).

Running the agent needs Docker, the SWE-bench images and a DeepSeek API key. The analysis commands are listed in the appendices of the [A5 final report](agent/docs/A5-FINAL-REPORT.zh-CN.md) and the [final report](agent/docs/MEMORY-FINAL-REPORT.zh-CN.md).

## Related project

[code-GRPO](https://github.com/RichChen/code-GRPO) is a separate repository that changes agent behaviour through training rather than through the context: SFT plus a from-scratch multi-turn GRPO loop on Qwen3-1.7B. On a preregistered 1500-task heldout set the strict solve rate went 13.9% (base) → 47.1% (SFT) → 60.3% (GRPO), i.e. GRPO is +13.2 points over SFT (95% interval [11.7, 14.7]).
