# Project memory

Last reviewed: 2026-11-10 (Asia/Shanghai)

This file is the dynamic memory for the coding-harness project. It records current state, decisions, evidence boundaries, open questions, and the next review point. It is not a chat transcript and should only retain information that remains useful across sessions.

**当前入口（2026-11-10）：** FIX-12 已结束；R11 已获用户事后接受；11-09 文档已整理并纳入本次本地提交。后续已选 U／重复提醒／一句话解释／UT 四组，仅授权设计，未批准付费运行。以末尾「用户决定与当前任务」为准；带旧日期的准备、路线和临时结论保留作历史，不作为当前执行指令。

## Current objective

The project is being repositioned for job-seeking demonstration. The main deliverable is a credible, reproducible measurement project about how coding-agent harness constraints affect agent behavior, success, and cost. It is not currently aiming to build a general-purpose agent that competes with commercial products, and it is not yet claiming a paper-level novel result.

FIX-12 is complete. The immediate milestone is a reviewed four-arm follow-up design: U, U with a repeated-call reminder, U with a one-sentence interpretation of the previous result, and U+T. The user approved this direction on 2026-11-10; detailed design and spending are not yet approved.

## What has already been established

- P0–P3 established the harness contracts, Docker isolation, candidate and verification identity rules, recovery boundaries, retrieval wiring, and the public verification path. These stages mostly establish engineering behavior; they do not show that a real model can autonomously repair code.
- P4 exposed important real-model and evaluation limitations. The model often spent many turns reading, and the workspace tools `retrieve_code` and `validate_patch` were not actually used in the P4 run. Action prompts changed behavior in a tiny replay, but that replay is not evidence of autonomous improvement.
- Earlier evaluation work found image contamination: hidden tests had entered an agent-visible image. The contaminated 32/42 result was corrected to 18/42 on the same dev set after clean images and answer-isolation checks. Old contaminated results and cards must not be used as baselines.
- Retrieval and memory interventions have not shown a reliable repair-rate gain. Correct-file hints did not produce a detectable improvement, and failure-memory injections showed an anchoring pattern. These are negative or inconclusive findings, not proofs that retrieval or memory can never help.
- Offline context simulation reproduced the existing masking policy exactly. Increasing `keep_recent` reduced repeated reads but worsened estimated cache-aware cost; a content-aware rule was cheaper in the simulation but exceeded the old admission estimate on some requests. The useful question has shifted from “which keep_recent value?” to “when is masking worth doing under prefix-cache pricing?”

Sources: `docs/roadmap/EXECUTION-STATUS.zh-CN.md`, `docs/roadmap/FIX-10-RESULTS.zh-CN.md`, `docs/analysis/CONTEXT-SIM.zh-CN.md`, `docs/analysis/RETRIEVAL-QUERIES.zh-CN.md`, `docs/analysis/P4-NO-EDIT.zh-CN.md`.

## Pivot and current pilot

The current pilot uses SWE-rebench as a fresher task source while retaining earlier CR-2 results as a continuity anchor. This reduces, but does not eliminate, contamination concerns. The feasibility review says the current dataset has no tasks created after the model release date and is itself public RL training data; therefore it must not be described as uncontaminated.

The three pilot arms are:

- **A**: existing b0-v2 restrictions, masking on every request, `utf8_bytes` admission, 40 model calls;
- **U**: masking only near the large admission threshold, wider admission and call limits, thinking disabled;
- **U+T**: U plus DeepSeek thinking at `high` with `max_tokens: 32768`.

The pilot design is 30 tasks × 3 arms × 2 runs = 180 planned runs, with 15 “easy” and 15 “other” tasks, block-randomized and interleaved at concurrency 3. The primary outputs are solve rate, high-peak-standardized provider cost, cost per solved task, failure categories, reread share, calls before first edit, and request-level telemetry. It is explicitly a pilot and does not identify which of U’s several simultaneous relaxations caused an outcome.

The formal design is in `docs/analysis/UNLEASH-PILOT-DESIGN.zh-CN.md`. The current preflight plan is `docs/roadmap/FIX-PLAN-12-PILOT-PREP.zh-CN.md`.

## Engineering readiness

The engineering preparation in `docs/roadmap/FIX-10-RESULTS.zh-CN.md` is complete:

- 1471 tests passed and 40 were skipped after the changes;
- frozen configuration hashes and runtime fingerprints were preserved;
- the 9-request DeepSeek thinking smoke test cost 0.003413 yuan;
- provider-usage settlement, request telemetry, thinking replay/persistence, and hard-limit configuration are implemented;
- four rebench tasks passed the no-model end-to-end path; `pypa__build-1027` is correctly rejected under the current export rule because edits under a source `build/` directory are dropped.

**Paid pilot status (2026-11-08):**

- **First launch, voided.** It started at 16:55 and halted at 18:28 under the infra-error stop rule: 3 infra errors out of 18 finished runs; 20 runs in total; 5.46 yuan. All 3 infra errors were Docker CLI reap timeouts that fired after commands had exited 0.
  - **Cause.** The 3 concurrent runs shared one asyncio loop. Synchronous `RunSession.append` writes stalled that loop. This is strong circumstantial evidence; there is no direct log of the reap exception.
  - **Bias.** The stalls also created cross-arm interference, because U and U+T write more and so slow the other runs more.
- **R9 (user approved).**
  - The launcher now runs one subprocess per run (commit 4ee045a). The harness runtime and configs are unchanged.
  - The first launch is archived as `runs/unleash-pilot-prerun-1` and excluded from analysis. The pilot restarts from seq 1.
  - The pre-run's 5.46 yuan is reported separately, outside the 250 yuan cap.
  - The diagnosis is in FIX-12-RESULTS §10.
- **Restart.** The pilot restarted at about 21:05 (launcher PID 11224, logs `runs/unleash-logs/pilot-r9-*.log`). In the first 5 runs, the median tool latency was about 0.35 s, with no problems.
- **Second halt (22:39).** The halt came at 43 runs and 9.56 yuan. xarray's run-time prerun check lacked the R7 export-subst exemption. The voided run was moved to `runs/unleash-pilot-voided/`.
  - An all-30 real prerun check also found that jsonpickle and fromager would crash on spec construction (`pyproject.toml` in gold patch). Fixed in 16d59f9.
  - An all-30 mock found that litellm cannot fit into the 256 MiB workspace.
  - **R10 (user chose substitute).** Clause 9 (workspace start fails twice) was added. Only litellm's slot was reopened (fd8f067). pykern was skipped (clause 3), and cal-itp/benefits-3330 was accepted; it passed mock in all 3 arms. Committed in fd44c8f; the suite has 1586 passing tests.
  - **Resumed** 2026-11-09 00:45, launcher PID 31140, logs `runs/unleash-logs/pilot-r9b-20261109-0045.*.log`. The user authorized fixing and resuming any halt without waiting for approval, with results wanted by Monday morning.
- **Third halt (~02:52).** The halt came at 104 runs and 26.43 yuan, with a forecast of 180.77. dspy A/r1 reported missing grader ids: it never added the `EmbeddingsWithScores` interface its tests import, which is the same outcome as the empty patch in calibration.
  - Root cause: the empty-delta regrade only looked up sprint calibration files, and the "no missing ids" condition could never hold for dspy.
  - **R11 (implemented under standing authorization; accepted after review by the user on 2026-11-10).** Rebench regrades now compare against the manifest and ingest calibration, and allow exactly the calibrated missing ids. The fix is commit 997c870. dspy A/r1 was reclassified as `execution` via `scripts/rebench/regrade_missing.py`, and no rerun was needed.
  - Docs committed. Suite: 1590 passing tests.
  - **Resumed** 03:00, PID 10988, logs `pilot-r9c-20261109-0300.*.log`.
- **Fourth halt (~04:3x).** The halt came at 151 runs and 36.90 yuan. It was caused by a launcher bug: the §8.4 forecast multiplied the cumulative spend by 6.84 instead of only the spend of the first 5 blocks.
  - Fixed in c915e0e. The correct forecast is 53.99 yuan, so there is no real budget risk.
  - Documented in FIX-12-RESULTS §10.7. Suite: 1591 passing tests.
  - **Resumed** 04:40, PID 18572, logs `pilot-r9d-20261109-0440.*.log`.
- **Pilot complete 2026-11-09 05:22.**
  - 180/180 runs, no halt. Total cost 43.10 yuan, plus the 5.46 pre-run reported separately. The evidence is in `docs/analysis/fix12/pilot-report.json` and `pilot-analysis-prelim.txt`; the controlled audit entry point is now `scripts/analysis/fix12_pilot_report.py`.
  - Solve rates: A 14/60 (23%), U 23/60 (38%), UT 44/60 (73%). Paired Δ with stratified bootstrap (seed 20261111):

    | Comparison | Δ | 95% interval | Cost ratio |
    |---|---|---|---|
    | U−A | +15 pp | [+3, +28] | 0.99 |
    | UT−U | +35 pp | [+22, +48] | 0.96 |
    | UT−A | +50 pp | [+35, +65] | — |

  - Median calls: A 40 (41/60 hit the call cap), U 92 (29/60 hit the cap), UT 28 (58/60 completed).
  - G1 (30/30 tasks fully valid) and G4 (0 cap stops) pass. G2 (compaction/admission counts), G5 (telemetry completeness) and the R8 reasoning exposure still need checking. The G3 run-time stop checks never fired.
  - By §7: difficulty is mid-range, so keep 1:1 strata. Δ₁ ≥ +10, so U goes to expansion with solve rate as the primary endpoint. Δ₃ ≥ +10, so UT enters expansion.
  - Caveats: no causal attribution within U (5 relaxations at once); one model and one prompt. R11 needs the user's after-the-fact review.
  - Next: the formal report (template `docs/analysis/UNLEASH-PILOT-REPORT-TEMPLATE.zh-CN.md`), remaining gates, and the §7.6 expansion sizing.

`FIX-PLAN-12` preparation is complete; results and the launch checklist are in `docs/roadmap/FIX-12-RESULTS.zh-CN.md`:

- design revisions R1–R7 are recorded. R7 (provenance-based exemption of answer-isolation scan hits, plus confirmed export-subst placeholders) was written **after** seeing the first ingest results, before any model run;
- all 30 tasks pass ingest calibration with 7 substitutions (before R10; after R10, 8 of the 30 slots hold a substitute, from 9 substitution events). Record: `scripts/rebench/out/unleash_pilot_ingest.json`; manifest: `configs/rebench/unleash-pilot-tasks.yaml`;
- a calibration bug was also found and fixed after seeing the ingest results. Node-id collection ran without the gold patch, so a test module that imports a gold-added name collected 0 items and was wrongly rejected. This affected dspy, which was rejudged and accepted. The user approved the fix and the rejudge;
- four of the seven pre-R10 substitutions were caused by sandbox conditions: no network, uid 1000 without a passwd entry, and a read-only filesystem. The eighth replaced slot (litellm, R10) failed because the repository does not fit the 256 MiB workspace. This biases the sample toward smaller tasks with fewer environment needs;
- the config diff and the three-arm mock end-to-end checks pass. The launcher preflight passes (180 runs). The launcher now binds the model name to `deepseek-flash`, because the profiles allow a `DEEPSEEK_MODEL` override;
- the full suite passes: 1576 tests after R9, with 40 skipped;
- R8 is reporting-only. U+T reports reasoning exposure, with low exposure pre-defined as either of two conditions: fewer than 50% of requests have reasoning, or the median over runs of mean reasoning tokens per request is below 500. Under low exposure, a U+T null result must not be read as "thinking is useless". Every run reports `summary.timing`, giving the session-recording seconds and agent wall seconds.

The one launch prerequisite not fully met is design §4.1 item 2. Per-step reasoning tokens were never measured on real tasks, so §8.3 still uses assumed tiers.

## FIX-PLAN-12 post-run audit (2026-11-09)

The pilot is complete at 180 final slots, with 181 summary records including one retired UT infrastructure attempt. A=14/60, U=23/60, and U+T=44/60. Final-slot costs are 14.675839, 14.485083, and 13.831490 CNY; total batch standardized cost is 43.101925 CNY because it includes the retired 0.109513 CNY attempt. The preliminary paired bootstrap is U−A +15.0 pp [3.3, 28.3], U+T−U +35.0 pp [21.7, 48.3], and U+T−A +50.0 pp [35.0, 65.0].

Independent gate checks found: G1 30/30 task sets counted valid under the existing `delta_too_large` contract, G4 zero per-run cost-cap stops, G5 8012/8012 settled requests with complete telemetry when failed requests are included in the audit, and U/UT no observed masking/admission-over. U+T had reasoning in 1812/2095 requests (86.5%) and every run had at least one reasoning request, but the median per-run mean reasoning tokens was about 241, below the predefined 500-token exposure threshold. Therefore the effect is a low-exposure thinking result.

The process and result review is recorded in `docs/analysis/fix12/FIX-12-AUDIT-REVIEW.zh-CN.md`. `docs/roadmap/FIX-12-RESULTS.zh-CN.md` still ends at the mid-run halt records; it does not yet contain the final pilot report. The former `.tmp/pilot_analysis.py` remains a preliminary artifact, while the tracked, reproducible report generator is `scripts/analysis/fix12_pilot_report.py`.

## FIX-PLAN-12 formal report handoff (2026-11-09)

The formal descriptive report is now `docs/analysis/fix12/UNLEASH-PILOT-REPORT.zh-CN.md`. Its reproducible read-only entry point is `scripts/analysis/fix12_pilot_report.py`, and the frozen audit output is `docs/analysis/fix12/pilot-report-audit.json`. The script makes no model calls and records input hashes, final/retired/voided accounting, raw versus counted status, G1–G5 checks, reasoning exposure, failure taxonomy, timing overhead, and model/config identity.

The plain-language interpretation is: the bundled U configuration performed better than A in this task pool, and U+T performed better than U, while final-slot cost did not increase in the same direction. The evidence does not identify which U component caused the difference, and U+T had low reasoning exposure (median per-run mean about 241 tokens, below the 500-token threshold). Eight replaced slots (nine substitution events) and sandbox-related failures limit sample representativeness. These are descriptive pilot findings, not automatic approval for a larger experiment.

Historical 2026-11-09 options (superseded by the four-arm direction approved on 2026-11-10): replicate the observed contrast on a frozen new task pairing; split U into component-level comparisons; run a small exposure canary before testing a higher reasoning condition; or first harden Docker/snapshot/telemetry/forecast accounting with no paid model calls. Until that choice is made and the corresponding preflight, stopping rules, budget, and evidence boundaries are written, do not start an expansion.

The offline follow-up memo is `docs/analysis/fix12/FIX-12-NEXT-DECISION-MEMO.zh-CN.md`. It found task-level heterogeneity: repeat outcomes were identical for A 28/30 tasks, U 23/30, and U+T 28/30; U−A was +23.3 pp on easy tasks and +6.7 pp on other tasks, while U+T−U was +50.0 pp and +20.0 pp. These are sensitivity descriptions, not new claims of general effect. Its historical ordering was D (no-paid engineering fixtures) first, then B (split U) or C (raise thinking exposure) according to the project question, and A (independent replication) after those definitions are fixed.

## FIX-PLAN-12 question status and reporting style (2026-11-09)

The project’s three original questions must remain the top-level reporting structure:

1. **Do harness restrictions suppress model capability?** FIX-12 provides a preliminary answer for this model and task pool: A→U is +15.0 percentage points and U→U+T is +35.0 points, with no matching increase in formal-run cost. This is evidence about the combined configurations, not any single restriction.
2. **Does “found the place but did not edit” explain failures?** Updated 2026-11-10: the gold-reference classification covers all 180 runs. Found-but-idle among failures is A 26/46, U 19/37, U+T 0/16; among all runs it is 26/60, 19/60, 0/60. The A-to-U reduction among failures is about 5.2 points, below the prewritten secondary-endpoint criterion; report it descriptively. This does not isolate the cause of the behavior.
3. **What happens when thinking is enabled for the first time?** FIX-12 observed U+T above U by +35.0 points. Median per-run mean reasoning tokens was about 241, below the predeclared 500-token reporting threshold. That limits extrapolation to higher reasoning output; it does not invalidate the observed positive configuration result or establish a natural threshold for effective thinking. Service-side defaults and different max_tokens also limit component-level attribution.

Future reports should answer these questions before discussing engineering gates or expansion. Use plain Chinese first; explain an English field name only when it is needed to reproduce a result. Use “预先规定的低思考量报告阈值” and explain that it is not an effectiveness threshold; avoid “暴露线”. Prefer “事先固定” over “冻结”, “正式运行” over “final slot”, and “思考文字数量” over “reasoning exposure”. Route A means independent replication for question 1, B means component separation for questions 1–2, C means verified thinking exposure for question 3, and D is a no-paid engineering reliability check rather than a scientific answer.

The historical no-edit audit (superseded by the full gold classification) is `docs/analysis/fix12/FIX-12-NO-EDIT-AUDIT.zh-CN.md`. It confirms that FIX-12 logs can show whether an edit command occurred, but cannot safely show “found the correct location and then did not edit” without a task-level reference table. That prerequisite was subsequently addressed by the gold-reference classification; use the gold report for current percentages and review limitations.

D-line engineering audit is `docs/analysis/fix12/FIX-12-ENGINEERING-RELIABILITY-AUDIT.zh-CN.md`. Existing no-model coverage passed the ledger tests. The audit reported two async snapshot tests as skipped for lack of `pytest_asyncio`; that came from running outside the project venv. `pytest-asyncio` is declared in `pyproject.toml` and installed in `.venv`, where the snapshot tests pass (rechecked 2026-11-10). The forecast fix and the rebench empty-regrade calibration already have tests (`test_forecast_counts_only_the_first_five_blocks`, `test_an_empty_regrade_reproducing_the_calibrated_missing_ids_is_consistent`, `test_rebench_calibration_reads_the_manifest_counts_and_the_ingest_missing_ids`). The one real gap was the launcher skipping failed requests in `telemetry_problems()`; since 2026-11-10 a failed request settled at provider usage gets the full field and request checks. Replaying the new rule on the 181 pilot attempts checks 1 such request and finds no problem.

The first FIX-12 reference map is `docs/analysis/fix12/FIX-12-REFERENCE-MAP.zh-CN.md`. It has candidates for all 30 tasks, with about 22 medium/high-confidence function-level candidates and 8 module/document-level-only candidates. The map is preparation, not a no-edit result. A manual review must fix the reference functions and save their evidence hashes before migrating the P4 classifier.

The bounded review is `docs/analysis/fix12/FIX-12-REFERENCE-MAP-REVIEW.zh-CN.md`. It keeps 22 tasks eligible for manual confirmation, downgrades 8 to module/document-only, and flags 12 cross-function boundaries that still need a human decision. No gold patch, PR diff, answer file, or run content was used.

The minimal candidate audit is `docs/analysis/fix12/FIX-12-NO-EDIT-MINIMAL.zh-CN.md` with entry point `scripts/analysis/fix12_no_edit_minimal.py`. It covers only 10 high-confidence tasks and reports candidate located-but-idle counts A=5, U=4, U+T=0. These are descriptive candidates, not a FIX-12 answer to question 2; superseded by the gold-patch classification of all 180 runs in `FIX-12-NO-EDIT-GOLD.zh-CN.md`.

## Decisions already approved

- Defer incremental session refresh. It is a future engineering improvement, especially if a main experiment exceeds roughly 150 calls per run. Do not add it to this pilot solely for cleanliness.
- Use the recommended pilot settings: equal 4-yuan per-run caps, U call limit 100, U/U+T long timeouts, U+T `high` thinking, the revised `calibrated_v1` admission proposal, 512 MiB session-artifact quota, and the rebench export option.
- Approve the pilot budget at the worst-case design bound: 250 yuan batch cap plus up to 12 yuan for already-running jobs and 0.5 yuan smoke overhead, approximately 263 yuan at peak-price accounting.
- Use off-peak execution when practical, but compare arms with the standardized peak-price accounting defined by the design.
- Keep the project aimed at job-seeking demonstration. Do not claim a broad research contribution before the evidence supports one.

## Review cautions before paid execution

These are not yet amendments to the formal design. Resolve them in the primary session before writing the final revision record.

1. **The pilot is a bundled intervention.** U changes masking, admission, call and step limits, and timeouts together (the per-run cap is 4 yuan in every arm). A positive U–A difference can support “the bundled relaxed harness performed differently on this task set,” but cannot identify which harness component caused it. The project narrative should preserve that boundary.

2. **Thinking exposure may be weak.** The smoke test showed empty reasoning on some tool-call requests, although a full AgentLoop smoke run produced reasoning on a later request. Relaxing the old “90% of requests have reasoning” validity gate is sensible for avoiding a guaranteed early stop, but a low reasoning-token distribution means a null U+T result cannot be interpreted as evidence that thinking has no value. Report request-level exposure and treat insufficient exposure as an interpretation limit.

3. **The 1.1M `calibrated_v1` admission proposal is not validated for thinking text.** The 0.895 maximum provider-to-calibrated ratio came from historical non-thinking traces. U+T feeds reasoning content back into later prompts. The actual provider `prompt_tokens` must remain the authoritative safety check; if it exceeds the estimate or the admission/compaction gate triggers materially, stop and classify the pilot as not meeting its “unleashed” validity condition rather than claiming the wider window was tested.

4. **Session-recording overhead is shared code but not necessarily harmless.** At 100 calls the measured overhead is about 130 seconds; at 200 calls it is about 804 seconds. It is acceptable to defer the incremental-refresh fix for this pilot, but longer-arm runs can be affected more often, so the main experiment must report recording overhead and revisit the fix before relying on long-run comparisons.

5. **SWE-rebench is fresher, not clean by proof.** Creation month can be used as a covariate or sensitivity split. Do not write “uncontaminated” or “never seen by the model.”

6. **Export-rule repair needs a narrow validation.** Done. With the option on, `pypa__build-1027` gold exports `src/build/_util.py` and passes; with the b0-v2 default it fails. Generated build artifacts are still dropped.

7. **Two selection-affecting changes were made after seeing ingest results**: R7 and the collection fix. Both were made before any model run and are documented. The pilot report must disclose both.

## Next action (2026-11-10)

The pilot is finished; the earlier launch instructions are historical. The 250-yuan approval covered only that pilot and does not authorize any new paid run.

Approved order (no paid calls):

1. Documentation fixes for the substitution count, the stale opening of `FIX-12-RESULTS`, and U's budget wording. Done.
2. The launcher check for usage-settled failed requests. Done.
3. Question 2 with references taken from the gold patch. Done: `docs/analysis/fix12/FIX-12-NO-EDIT-GOLD.zh-CN.md`, rules pre-committed in `72c4a39` (written after the pilot; the rules file lists what had been seen). found_idle among failures: A 26/46, U 19/37, U+T 0/16; no budget class in any arm; §7.5 gives report_only (U only 5 points below A). Blind review 12/12 located, 12/12 plan, 11/12 edited. Post-hoc only: none of the 45 found_idle runs has plan text and 32 end in exact repeated calls; passing runs almost never do. Reading: found-but-not-edited is common but not explained by call or cost limits; it looks like planless repetition loops. The U vs U+T contrast (thinking) is descriptive, 30 tasks x 2, and must not be read as "thinking removes loops" until B tests it.

Trajectory analysis (2026-11-10, post-hoc, free): `docs/analysis/fix12/FIX-12-TRAJECTORY.zh-CN.md`, scripts in `scripts/analysis/fix12_trajectory/`. Findings:

- U+T had 0 loop runs, and never repeated a call back to back. U had 27 loop runs and A had 19. Task pairs: U→U+T 14 better / 0 worse; A→U 8 / 2.
- Loop shapes differ. U loops are one successful command repeated 60–93 times (24/27). A loops are 4–13-call re-read cycles (15/19). Almost none are retries after failure.
- A has 6.7 re-reads per run of calls more than 4 back, U has 0; prompt cache hit rates are about 58% and 98%. These observations are consistent with a masking effect, but A and U differ on multiple settings, so they do not isolate masking causally.
- Six of U's 23 passing runs ended after call 40; 17 ended by call 40. This timing does not identify a causal upper bound for the call-limit change. Many additional U calls were repeated calls.
- U+T had no recorded cap stops. Its 16 failures include 14 unsuccessful patches and 2 snapshot failures; do not describe all failures as wrong patches.
- Found-but-idle and loops look like two stages of one process (`idle.py`, doc §5.1–5.2):
  - Loops: A 16/26 and U 16/19 found-but-idle runs end in a loop. No loop starts before the located request; onset is a median of 22.5 (A) and 31.5 (U) requests after it.
  - Visible text drops from about 50% of turns before locating to 96–100% silent after.
  - Candidate reading: with no written judgment, the model keeps doing what it was doing, and the silent, identical history then gets copied. This is unconfirmed, and causal direction is open: "no judgment written causes no edit" versus "unsure how to fix, so neither writes nor edits". Separating them needs an intervention.
- All of this is a low-thinking-exposure result.

Next, write the agreed four-arm design and sample-size estimate before any budget request. Question 2 shifts B's emphasis from "does more budget reduce found-but-idle" to thinking versus repetition loops, and to whether the harness should detect identical repeated calls (a harness change that needs its own design). Both decisions were approved on 2026-11-10: curate and commit the 2026-11-09 documents, and accept R11 with its original chronology preserved.

## Handoff back to Codex (2026-11-10)

After Codex's last write on 2026-11-09, the primary session did the approved steps 1–3 and the trajectory analysis.

Commits on `main` (local only, not pushed):

- `8246a9a`: launcher check;
- `72c4a39` and `63ac9cf`: question 2 against gold-patch references;
- `4684267`, `e1521df` and `1d32aad`: trajectory analysis.

Edits to Codex's still-uncommitted files:

- this file: steps 1–3 marked done, the trajectory findings, and this section;
- `UNLEASH-PILOT-REPORT.zh-CN.md`: a "2026-11-10 更新" paragraph pointing to the gold classification;
- `FIX-12-ENGINEERING-RELIABILITY-AUDIT.zh-CN.md`: the "2026-11-10 复核更正" section.

Read first:

- `docs/analysis/fix12/FIX-12-NO-EDIT-GOLD.zh-CN.md` (question 2);
- `docs/analysis/fix12/FIX-12-TRAJECTORY.zh-CN.md` (thinking vs non-thinking, A vs U, loops, found-but-idle). Its §7 lists candidate directions: repeated-call detection, masking only near the context limit, forced one-line interpretation per step, and U+T patch correctness.

Codex's next task, as assigned by the user: propose follow-up experiment directions and designs. Constraints:

- No paid runs until design, preflight checks, stopping rules, budget, and evidence boundaries are written and reviewed with the user.
- Treat all FIX-12 trajectory findings as post-hoc and descriptive.

Resolved after this handoff on 2026-11-10: the user approved curated local commits of the 2026-11-09 documents (including this file) and accepted R11. No push was requested.

## Delegated-work reporting

Subsessions may write detailed results in `docs/roadmap/` or `docs/analysis/`. They should update this file only with durable facts, decisions, evidence boundaries, and blockers. The primary session should pull a concise summary rather than receive every progress message.

## Independent work while the pilot runs (2026-11-08)

Two read-only reviews were completed without paid model calls or changes to the pilot:

- The job-seeking narrative should lead with auditability, evaluation integrity, and trajectory diagnosis. The strongest existing evidence is contamination correction (32/42 to 18/42), P4 repeated reads/no actual retrieval or validation-tool use, offline context-cost tradeoffs, and FIX-10 engineering readiness. Pilot numbers must remain pending.
- The pilot report should be pre-registered around run accounting, G1–G5 validity gates, task-level paired contrasts, cost/telemetry exposure, failure taxonomy, and sensitivity analysis. A failed gate permits engineering diagnostics but not expansion inference.

Drafts created for later review:

- `docs/SHOWCASE-PACK.zh-CN.md` — interview narrative, evidence cards, evidence matrix, and claim boundaries;
- `docs/analysis/UNLEASH-PILOT-REPORT-TEMPLATE.zh-CN.md` — empty pilot report skeleton with accounting, validity, cost, exposure, and interpretation sections.

These drafts are independent of the running `FIX-PLAN-12` work and contain no new pilot results.

## Repository cleanup status (2026-11-08)

The cleanup follows the user's approved rules: tracked documentation may be reorganized with link fixes; historical material is preferentially kept in an external archive; original files are not deleted. The canonical external archive is `D:\crq\archive\coding-harness-cleanup-20261108-010827`, with the in-repository index at `docs/CLEANUP-ARCHIVE-INDEX.md`.

One reversible local batch moved the two old request-capture files `tmp_requests_2674.jsonl` and `tmp_requests_863.jsonl` to that archive, with SHA256 and rollback manifests. `FIX-PLAN-12`, pilot outputs, `scripts/rebench`, `.tmp`, `runs`, `traces`, Docker/OCI-related paths, and evidence/session directories remain frozen while active processes are running. A bounded review found no safely movable tracked historical document; those candidates remain pending with reasons in the external classification report.

After the pilot-specific processes exited, a second reversible batch moved the root `__pycache__` and `.pytest_cache` trees. A later recheck found two active `runs/fix12/source_diff.py` processes (PIDs 21180 and 1748) and a regenerated `__pycache__` file; that cache remains in place until the FIX-12 processes exit and a fresh handle check passes. The cleanup index records this correction.

## Read-only review on 2026-11-08

Three independent sessions reviewed the pilot without paid model calls. They did not change the pilot design. Their findings add the following preflight checks:

- Returned model name not bound to the expected model: **fixed**. The launcher pins `deepseek-flash`, the name the API returns, in the preflight, the run summaries and the returned names. The version is bound through the price-page `model_version` plus fingerprint stability.
- `telemetry_problems()` skips failed requests: **kept for the pilot, with this reasoning**. Strict unknown-usage mode makes every transport or provider failure `billing_usage_unknown`, which already halts the batch. Only usage-known truncated responses skipped the checks, and they end their run. Changed on 2026-11-10: those usage-known failed requests now get the full checks.
- Historical review of the forecast was incomplete. The subsequent fourth halt identified cumulative spend being multiplied repeatedly; `c915e0e` corrected it to the first five blocks only. Do not preserve the old "conservative, keep it" recommendation as current guidance.
- The 14% rerun factor is a historical aggregate. Earlier audits found reruns skew toward longer runs, so applying the same rate to U and U+T is a planning assumption, not an observed group-specific rate.
- The pilot's 4-yuan cap is expected to be loose for A/U but can bind U+T at high reasoning-token volume. It must remain a validity metric, not be described as uniformly non-binding.
- The design's “cost per solved task” currently divides total cost by solved runs. Reports should label this precisely and, where useful, also provide a task-level deduplicated cost.
- Session-refresh measurements are indexed by synthetic steps, while pilot limits are expressed in model calls and steps. Preflight should record both and, if long synthetic reasoning is used, include reasoning-like history growth.

The job-seeking narrative is strongest when it emphasizes auditability, clean evaluation, trajectory diagnosis, and honest evidence boundaries. It should avoid claims of general harness improvement, uncontaminated SWE-rebench tasks, isolated component causality, or a null result proving that thinking or retrieval has no value.

## 用户决定与当前任务（2026-11-10，接手复核后）

- 用户已同意精选提交 11-09 的文档、当前项目记忆和正式报告的可复现入口。历史材料保留，注明已被后续 gold 分类或新设计替代；不批量提交无关文件，不推送。
- **接受 R11。** 接受的是 `997c870` 对 rebench 空补丁复评的校准对照修复，以及 dspy A/r1 从基础设施异常改归模型任务失败的处理。它在修复前后均未解出，没有重跑模型。只允许与该题校准记录恰好一致的缺失 ID；不豁免新增或不同的缺失。原摘要和停批/续跑顺序保留。此决定是事后审阅接受，不改写成运行前已批准。
- **下一步选四组：U、U＋重复调用提醒、U＋一句话结果解释、U+T。** 具体设计交给子智能体，主会话审阅后与用户讨论。重点比较解出率与费用；重复与“不动手”是辅助行为指标。单纯减少重复不能算解题能力提升，四组也不能完全拆开 thinking 内部机制。
- 已看过的 30 道题只能用于开发和诊断；新的独立验证需要事先确定选题与替补规则。离线 gold 分类可以使用标准补丁；在线干预不可读取标准补丁或参照答案。
- 设计、预检、停止规则、预算、证据边界尚未经过用户审阅，**不得开始任何新付费实验**。旧试点预算不延用。
- 费用表是服务方 usage 按标准峰价核算，尚非核实过的账户扣款；下轮应分别报告可核对的实际花费与用于组间比较的标准费用。
- 轨迹都是事后描述。旧脚本对“输出相同”的判断只比较前 300 个字符，不能当作完整输出一致；低思考量报告阈值不是“thinking 有效”的门槛。不要据此主动增加思考量或声称已查明循环原因。
