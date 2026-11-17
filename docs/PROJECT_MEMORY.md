# Project memory

Last reviewed: 2026-11-10 (Asia/Shanghai)

This file is the dynamic memory for the coding-harness project. It records current state, decisions, evidence boundaries, open questions, and the next review point. It is not a chat transcript and should only retain information that remains useful across sessions.

**当前入口（2026-11-11）：** FIX-12 已结束，R11 已接受。FIX-13 新题已通过 25/30，继续完成最后五位置。用户现已明确授权：30 题到位后完成全流程无模型检查及开跑收尾，通过即直接启动四组正式运行，无需再次询问。沿现有 240 名额、并发 3、160 元标准峰价全流程上限与停止规则；预算充足不等于取消上限。题库目标止于旧 30＋新 30。复用原执行智能体，正常进度按批记录。最新授权见末尾，旧的“尚未授权付费”段落仅代表当时状态。

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

## 首批新题接入授权（2026-11-10）

用户同意先接入 5 道新题，通过的题直接纳入本轮后续使用，依据实测接入耗时再更新准备时间。先形成独立的新题候选/替补顺序，不能按下载快慢或接入难易事后挑题。

网络为本次重点：先核验 Cursor 会话 `78860345-e424-4309-a6fd-7411437c36da` 的下载经验和残留工具，不向该会话发送消息。FIX-12-RESULTS 的镜像下载条目记录过代理拉取约 0.6 MB/s、CDN 分层下载校验后导入约 10–16 MB/s；历史速度不代表当前速度，须做有界小流量实测。明显低速时及时暂停排查，不沿同一路径空等数小时，不修改全局代理或重启 Docker。

已委派 GPT-6.1 Sol：`network_history` 核验网络/历史并提供安全复用方法；`four_arm_design` 生成独立 FIX-13 清单并实际完成首 5 个位置接入。使用独立 plan、manifest、日志及结果路径，保留 FIX-12 所有原始证据。主会话负责审阅新题资格、接入结果和时间估计；当前不调用付费模型。详细草案见 `docs/roadmap/FIX-13-FOUR-ARM-DESIGN.zh-CN.md`。

### 网络工作已完成（2026-11-10 14:51）

指定 Cursor transcript 的第 661、664、670 行已核验网络历史，未向原会话发消息。首批五张官方镜像均已下载、逐层 SHA-256 校验、导入并与官方 RepoDigest 匹配；下载和导入任务均已退出。初测直连较快，后段遇 TLS reset/低速后按有界实测改用代理续传，没有重下整图或放松证书校验。不能把本机代理简单固定为“总该走”或“总不该走”。

可复用工具为 `scripts/rebench/fetch_image_verified.py`，支持保留 partial 续传、共享完整层缓存、显式 `--proxy-blobs`、网络连接/低速/总时限及最终身份核对。大镜像导入本身也会慢（本批 205–2176 秒），不应误归因于下载；工具未承诺 Docker load 总时限，使用时继续观察进度。详细证据和调用方法见 `docs/analysis/fix13/NETWORK-PREFLIGHT.zh-CN.md`。

首批任务接入仍在进行，权威进度为 `docs/roadmap/FIX-13-PREP-STATUS.zh-CN.md` 与 `runs/fix13-prep/stages.jsonl`。Packit 已完整通过，未重做已经通过的扫描；当前无付费模型调用。

### 无模型工程适配并行进行（2026-11-10）

首批接入运行期间，主会话将不依赖新题结果的四组最小实现交给 `four_arm_implementation`（GPT-6.1 Sol）：重复调用提醒、固定一句话解释提示、独立配置和启动器、本地针对性测试。它不修改首批选题/接入文件，不启动 Docker 或模型实验。结果入口为 `docs/roadmap/FIX-13-IMPLEMENTATION-STATUS.zh-CN.md`，由主会话复核实际改动与设计是否一致后采纳。

### 用户批准简化重复提醒（2026-11-10）

无模型开发回放发现，原先要求“成功、只读且完整输出一致”的规则，在旧 U 组 27 条高重复轨迹中触发 0 条；仅忽略包装耗时字段触发 1 条。它无法有效测试当前关心的现象。

用户已明确选择“采用简单提醒，保留四组”：连续三个模型工具轮，每轮恰好一个调用，工具名和参数完全相同，即提醒一次。每次运行最多提醒一次；不以只读、成功或输出相同为条件；提示允许合理重复，不阻止调用。R/E/UT 的其余比较条件不随之改变。

该候选在旧 U 的 27 条高重复轨迹中触发 24 条，其余 33 条未触发；全部 23 次通过运行中有 2 次触发，均属于高重复集。这是已见开发数据上的规则覆盖描述，不是提示有效或新题获益的证据。新验证题尚未运行模型。设计和实现由 `four_arm_implementation` 同步更新，不再探索其他提醒变体。

### 四组工程复核与接入修订（2026-11-10）

四组最小实现已完成主会话代码复核，131 项针对性测试通过；包含用户批准的简单 R 提醒、固定 E 提示、四份配置和独立启动器。接受本次工程改动不等于批准付费运行：完整新题集、真实 Docker 端到端预检及设计/预算审阅仍未完成。结果见 `docs/roadmap/FIX-13-IMPLEMENTATION-STATUS.zh-CN.md`。

首五位置中，Pulser、packit、toqito、spdx 已完整通过。第五位置原 line SDK 题因既定隔离规则排除，沿固定同层队列替补 venvstacks。替补校准在实际测试前遇到生成目录 `.pdm-build` 的读取权限问题。主会话已采纳限定接入修订 P1：先核实目录不在基线源码中、仅含已记录的两个普通文件，再仅将该目录从 0700 改为 0755；文件内容/权限、源码、依赖、网络、运行用户与评分规则不变。旧镜像和证据保留，新镜像重新通过完整隔离、校准、内存与工作区检查后才能接受。若需扩大修复范围，回到主会话讨论。修订证据和最终状态以 `docs/roadmap/FIX-13-PREP-STATUS.zh-CN.md` 为准。

北京时间 16:04，P1 新镜像完成上述完整检查，venvstacks 接受；批次结果为五个位置全部通过，pending 和 unfilled 均为空。总共尝试六个候选，只有 line SDK 按原规则排除，不能据这一个替补比例推断完整题池。五道通过题的运行入口绑定检查均通过，未调用付费模型。首批已完成，余下 25 题和正式四组 Docker 端到端预检尚未完成；后续按小批处理并核对容量，不自动 prune 或删除证据。

首批从计划落地到接入完成约 133 分钟。余下 25 题暂按 8–16 小时安排准备，属于待下一批修正的工程估计，不是完成保证。接入结束后保留原 plan/ingest 字节，仅统一 plan 为 LF 并更新关联哈希；主会话已核对选题与顺序语义不变、plan/exposure 哈希一致、manifest 恰为五道通过题。迁移记录在 `runs/fix13-prep/line-ending-migration.json`。

## 用户批准固定 60 题与继续接入（2026-11-10）

- 用户已接受：完成本轮新 30 题后，以旧 30＋新 30 共 60 道通过题作为后续固定题库，不默认继续扩题。原规则拒绝候选仍可按既定队列替补并保留证据，不算新增研究题目。旧题用于开发诊断；新题支撑本轮预先确定的比较。后续若根据这 60 题成绩反复调整方法，不能继续将它们称为未见过的独立验证题。
- 本次“开始吧”授权完成剩余 25 个位置的无模型接入。沿既有选题、替补和验收规则，每五位置一批继续至新 30 题到位；保留已通过的五题，不重新选样或重做完整校准。付费实验仍须设计、预检、停止规则、预算及证据边界审阅后才能开始。
- 用户明确要求减少主审额度消耗和非必要治理。复用熟悉流程的 `four_arm_design` 执行智能体；脚本负责重复流程，执行智能体处理常规工程问题。正常批次只写接入结果文档，不逐分钟向主审反馈；主审只处理阻碍、重大风险、需用户决定事项和最终验收。不另开审计会话或追加独立审计智能体。
- 容量核对：Docker 数据文件在 E 盘，约 418.1 GB，E 盘剩余约 389.1 GB，D 盘剩余约 291.5 GB。D/E 同属 `WDC WD20EZAZ-00GGJB0` 2 TB 机械硬盘，C 属另一块 512 GB Intel NVMe SSD。余下接入按批检查空间，避免同时进行多个重型导入/构建；不自动清理镜像、重分区、迁移数据或购买资源。扩容与历史镜像归档方式尚未批准实施。

## 用户授权预检通过后直接正式运行（2026-11-11）

用户明确表示新30题到位后继续全流程无模型检查、开跑前收尾，全部无问题即立刻开始正式运行，并已备好API预算；用户去休息，不需再次询问开跑许可。主会话据已讨论的推荐设计采纳240正式名额、U/R/E/UT各60次、并发3、单次4元、全流程160元标准峰价上限，启动阈值148元及12元在途缓冲。开发、身份冒烟、退役和重试同计总额，预算充足不授权无限花费或突破停止规则。

执行分工：four_arm_design 完成最后五题并交接累计manifest/校准/替补；four_arm_implementation 接续无模型端到端预检、离线gold参照与身份/价格/预算绑定，通过后按本次授权启动唯一正式批次并跟进。两者直接协调重型Docker任务，不新建监控框架或额外审计会话。执行者可修复比较条件不变的普通工程故障并保留记录；若需要改提示、题目资格、评分规则、资源条件、模型窗口或突破费用上限，暂停并向主审报告，不趁用户离线变更实验。

预检必须留下简洁可复核结果：完整30位置及240名额、各题真实Docker无模型作答/评分、四组提示/干预路径、答案隔离、费用/身份/恢复/停止检查、最终输入与离线gold参照绑定。已有131项通过结果可复用，只补新改动及真实路径的必要检查。初次身份请求若需要付费，列入既定不超过0.5元冒烟及160元总额，记录全部ledger。正式运行完毕后汇总费用、结果、异常与证据边界，报告使用易懂中文。

### 新 30 题无模型接入完成（2026-11-11 02:25）

固定新 30 个位置全部通过，manifest 恰为 30 题，pending/unfilled 为空；三空/三 gold、内存/工作区及运行入口绑定均完成。本轮共 36 个候选、六次替补事件，涉及五个原位置，未触及单位置三候选规则拒绝上限。剩余 25 位置续跑实际 9.49 小时，完成时 D/E 可用约 268.3/260.6 GB，模型调用为 0。

除已采纳的限定 P1 外，续跑处理了原来源格式规则下 Spack 的确定性派生拒绝、累计记录原子替换的短时有界恢复，以及同 commit 的 Strands 来源包根名称适配。源码相对内容与验收边界保留，旧材料和失败历史均留存。最终 pgmpy 按旧规则排除六项 P2P，其余题无 P2P 排除。详见 `docs/roadmap/FIX-13-PREP-STATUS.zh-CN.md` 与 `runs/fix13-prep/all-30-summary.json`。

接入写入与下载进程已退出、锁已释放，最终输入实际哈希和三十任务清单记录于 `runs/fix13-prep/handoff.json`；已明确交接既有 `four_arm_implementation` 接续全量无模型 Docker 预检及此前获准的正式运行，接入执行者不再修改累计 manifest/ingest。

## ET 追加组（2026-11-12）

用户在看过 FIX-13 四组结果后选择只跑 ET：即 UT（thinking high、32768 输出额度）再加 E 的固定一句话解释提示。ET 使用同一批 30 道新题，每题运行两次，共 60 个名额。设计、预检、预算和证据边界见 `docs/roadmap/FIX-13-ET-ADDENDUM.zh-CN.md`。这是事后追加的探索组，不能加入原四组的事前确认检验族；原四组报告保持不变。

ET 配置只比 UT 多 `runtime.tool_intervention: result_explanation`；模型、系统提示、工具、镜像、评分器和任务清单均绑定到原 FIX-13 条件。ET 批次完成 60/60，30/30 道题均有两次有效运行，模型 `deepseek-flash` 和系统指纹与原批次一致。实际并发为 2，而原批次为 3；这是调度差异，未改变单次运行条件。全流程标准峰价费用 15.392553 元，其中最终名额 14.421519 元，约 0.97 元来自基础设施退役尝试；这不是已与账户账单核对的金额。

结果是 U 16/60、E 26/60、UT 28/60、ET 27/60。按题配对，ET−UT 为 −1.7 个百分点，普通 95% 区间 [−8.3, +3.3]；ET−E 为 +1.7 个百分点，区间 [−5.0, +10.0]。两项追加比较的 Holm 校正 p 都为 1。探索性交互 `(ET−UT)−(E−U)` 为 −18.3 个百分点，普通区间 [−33.3, −3.3]，符号翻转 p=0.04143；它只支持“E 与 UT 的收益可能重叠”的后续研究线索，不能当作预注册结论。

按题看，ET 至少解出一次的 14 道题全部属于 UT 的 16 道题；E 的 13 道题全部属于 ET。ET 相比 UT 有一题多解、两题少解；相比 E 有一题多解、一题少解。ET 没有解出任何 E 与 UT 都未解出的题。

行为上，ET 只有 1 次定位后未编辑、2 次没有编辑调用、0 次高重复；E 为 4、5、5，UT 为 0、0、0。ET 有可见说明 809/1777 轮（45.5%），低于 E 的 1096/1894（57.9%）；reasoning 非零请求 1606/1895（84.7%），接近 UT 的 1920/2149（89.3%）。这说明 ET 同时保留了两条路径，但没有在 UT 之上产生可见的解出收益。

实际含义：如果目标只是达到 UT 的解出水平，当前数据更支持单独使用 E，标准全尝试费用约 7.32 元，低于 UT 约 16.87 元；这不是普遍最优配置的证明。ET 可以作为同题、同模型、同评分链路下的后续探索比较，但不具有原四组同等的事前确认地位，也不能据此拆出 thinking、输出额度和 E 提示各自的单独因果作用。完整结果见 `docs/analysis/fix13-et/FIX-13-ET-RESULTS.zh-CN.md`、`docs/analysis/fix13-et/report.json` 和 `docs/analysis/fix13-et/runs.json`。

## 后续方向记录（暂不启动）

以下两条方向已记录，等主会话与用户决定后再写具体运行计划；当前没有运行授权：

1. **E 提示机制消融**：在同一批固定题上比较完整的“总结上一步结果并说明下一步”与内容中和、只保留可见一句话结构的版本，判断收益来自结果解释本身，还是来自强制模型在工具轮之间写一句可见判断。
2. **thinking 与输出额度的 2×2 补全**：现有 U（thinking 关、8192）、UT（thinking 开、32768）还缺 thinking 开但 8192、thinking 关但 32768 两个格子。补齐后可分别估计 thinking、输出额度和二者交互，避免把两者混为一个效果。

这两条都只使用现有 60 道题；后续若反复按这 60 道题调参，结果属于固定题库上的开发和验证，不能再称为未见过的独立验证。

## FIX-14 完成与结论（2026-11-14 02:50）

120/120 个名额完成，30/30 个题区完成，批次正常退出。只读对账结果为 `final_slots_with_summary=120`、`consistent=true`、`complete=true`、`halt_reason=null`。标准峰价费用为 34.184036 元，另有 1.597987 元恢复预留，上限 80 元；这不是已和账户账单核对的金额。

本轮补齐了 2×2：U（thinking 关、8192）、T8（thinking 开/high、8192）、O32（thinking 关、32768）、UT（thinking 开/high、32768）。四格完整题为 27 道，主比较只使用这 27 道；T8 和 O32 是 FIX-13 之后追加的格子，U/UT 来自 FIX-13，不能称为新的未见题独立验证。

- **T8−U：+20.4 个百分点，普通 95% 区间 [+9.3, +33.3]，Holm p=0.0165。** 在相同 8192 输出额度下，打开 thinking 后解出率明显提高。
- **O32−U：+1.9 个百分点，区间 [−9.3, +13.0]，p=1.0。** 在 thinking 关闭时把输出额度扩大到 32768，没有可检测的收益。
- UT−T8 为 +3.7 个百分点，交互项为 +1.9 个百分点，区间都不能支持输出额度有稳定的独立作用。

行为也同向：T8 的定位后未编辑为 1 次、高重复为 0 次，接近 UT；O32 的定位后未编辑为 19 次、高重复为 26 次，接近 U。当前证据支持“thinking 是主要变化，单独扩大输出额度没有明显作用”的判断，但不能把它推广到其他模型、题目或仓库规模，也不能把它解释成某个内部思维机制已经被证明。

本轮有 8 个基础设施异常名额：1 个容器收尾失败的名额已用捕获补丁离线重评为 `partial` 并保留计分，其余 7 个因 `delta_too_large` 没有候选补丁，按不可评处理。T8 即使 5 个不可评名额全部未解出，仍高于 U 的缺失名额上界；O32 的上界与 U 重叠，主方向不由缺失名额造成。

共享统计中的 `counted_status` 曾把 `delta_too_large` 单独计为有效标签。FIX-14 报告已经改用 grader 结果判断 `valid`；FIX-13 和 ET 的历史报告仍保留旧口径，后续应做一次免费的历史有效性复核。结果入口：`docs/analysis/fix14/FIX-14-RESULTS.zh-CN.md`、`docs/analysis/fix14/report.json`、`docs/analysis/fix14/runs.json`。

## FIX-14 后续复核（2026-11-14）

已完成一次离线有效性复核：统一把 `candidate_snapshot_failed` 视为不可评名额，保留原始运行目录和原报告不改写。FIX-13 的完整题数按组为 28、28、28、27；ET 合并比较的完整题数为 27。修正后的方向与原报告一致：FIX-13 的 R−U、E−U、UT−U 仍为正；ET 与 UT 仍接近，ET 与 E 的差异仍小。

FIX-14 当前不需要补跑。T8−U 的方向在不可评名额的上下界下仍保持，O32−U 的区间与 U 重叠；若未来补跑，必须先修复 `delta_too_large` 的候选快照排除规则、重新做输入指纹和 Docker 预检，并说明与历史 U/UT 的可比性变化。详细记录见 `docs/analysis/fix14/HISTORICAL-VALIDITY-AUDIT.zh-CN.md` 和 `docs/analysis/fix14/FIX-14-INTERPRETATION.zh-CN.md`。

## E 任务级离线复核（2026-11-14）

在固定 60 题数据上完成了一次免费、事后、任务级复核，未调用模型、Docker 或网络，也未修改原始运行目录。按修正后的有效性规则，U 与 E 有 29 道题各有两次可评运行。以 U 中是否出现过“定位后不编辑”作解释性分层：12 道这类题中，E−U 平均解出率差为 +45.8 个百分点，定位后不编辑减少 70.8 个百分点，高重复减少 45.8 个百分点，有编辑率提高 66.7 个百分点；其余 17 道题的解出率差为 −2.9 个百分点，基本没有差异。整体 E−U 为 +17.2 个百分点。

这是事后分层的描述性证据，支持“E 主要帮助 U 中找到目标却没有继续编辑的题”这一机制假设，但不能作为新的独立验证，也不能拆出 E 提示、可见说明和上下文结构各自的因果作用。独立测试是否通过没有被现有字段完整记录，因此报告使用最终 `pass` 作为解出结果，不声称单独测得验证成功率。详细结果见 `docs/analysis/fix13-et/E-TASK-AUDIT.zh-CN.md` 和 `E-TASK-AUDIT.json`；脚本为 `scripts/analysis/fix13_e_task_audit.py`。

## FIX-15 E 机制消融（设计草案，未付费运行）

基于任务级复核，下一条研究线改为同批次比较三个非-thinking 组：U-new（无干预）、N（只要求写一句可见下一步说明）和 E-new（当前完整 E）。每组 30 题×2 次，共 180 个名额。主要比较为 N−U-new 和 E-new−N，分别估计可见文字约束本身以及结果解释内容的额外作用。设计、预检、预算、停止规则和证据边界见 `docs/roadmap/FIX-15-E-ABLATION-DESIGN.zh-CN.md`；独立设计复核见 `docs/analysis/fix13-et/E-ABLATION-DESIGN-REVIEW.zh-CN.md`。

尚未启动付费实验。正式运行前必须先修复并验证 `.mypy_cache`、`.ruff_cache` 等目录导致的 `delta_too_large` 候选快照问题，并完成三组的无模型/Docker/评分/账本预检。建议全流程硬上限 60 元，启动阈值 48 元，12 元在途缓冲。

候选快照过滤的工程修复已完成：`.mypy_cache` 和 `.ruff_cache` 不再进入导出的候选补丁；`export_keep_original_files=true` 时，原始源码仍按原规则保留。针对性测试 `tests/test_export_delta.py` 与 `tests/test_fix12_export_rule.py` 共 12 项通过。该修复尚未替代正式运行前的三组真实 Docker 预检，也没有启动付费名额。

FIX-15 的本地计划、配置和 runner 已完成：30 题×3 组×2 次共 180 个名额；`run_fix15.py --preflight` 与 `--bind-mock` 通过，确认三组均为 thinking disabled、8192 输出额度，差异只有 `next_action` 与 `result_explanation`。随后完成三组真实 Docker 无模型 wiring 预检：U-new、N、E-new 均通过，均为 scripted 4 请求、grader F2P 1/P2P 5、answer isolation 通过、账本 outstanding=0，`provider_calls=0`。请求体复核确认三组提示分别为无干预、NEXT_ACTION、RESULT_EXPLANATION。身份冒烟和正式 180 名额尚未启动；当前在此停下等待审阅。预检中的 `model_calls=4` 是本地 scripted transport 回合，约 0.008 元是 mock ledger 估算，不是付费账单。

随后完成了单独的身份冒烟：U-new 和 E-new 各一次真实请求，共 2 次，标准峰价费用 0.000048 元，账本 outstanding=0，模型为 `deepseek-flash`，系统指纹 `bc19ba97cbe016479903222bb1578db6`，与 FIX-13 参考指纹一致，价格前后快照一致且 usage 均已结算。该冒烟的控制台输出曾因 Windows GBK 编码异常退出，但证据文件已完整写入并已核对；没有重复请求。`runs/fix15` 不存在，正式 180 个名额未启动。

## FIX-15 正式运行与离线分析（2026-11-14）

用户批准按既定设计启动 FIX-15 正式批次。三个非-thinking 组各 30 题×2 次，共 180 个名额：U-new 无干预，N 只要求每轮工具调用前写一句下一步，E-new 使用完整结果解释提示。执行与监控由独立子会话负责，主会话只处理账本、停止规则和需要决策的异常；模型为 `deepseek-flash`，并发上限 3，标准峰价总上限 60 元。

批次已完成 180/180，三组各 60 次，`halt_reason=null`、`unreconciled_attempts=[]`、无未结算预留，总标准峰价费用 30.476552 元。解出数为 U-new 17/60（28.3%）、N 23/60（38.3%）、E-new 24/60（40.0%）；部分或完全通过为 22/60、30/60、34/60。平均模型调用为 63.5、41.5、35.5，平均费用为 0.225、0.163、0.120 元；每个完全解出运行费用约 0.795、0.425、0.300 元。这些是本批次观察值，成本下降不能单独归因于某个内部机制。

运行中有两项工程事件均已按既定规则处理：`keras-team__keras-22642/U-new/r1` 的主账本落后于完整 `.tmp` 快照，已保留哈希、备份旧账本并原子提升完整快照，确认 provider usage 已结算且未重跑；`graphistry__pygraphistry-1107_interface/N/r1` 因候选快照 `delta_too_large` 退役，原始费用和证据保留，按规则重试后 N/r2 通过。两者都不作为模型能力证据。

严格的题级解出比较排除 graphistry 这一不可评分题，使用 29 道完整题：N−U-new 为 +10.3 个百分点，普通 95% 区间 [−3.4,+24.1]，Holm p=0.494；E-new−N 为 +3.4 个百分点，区间 [−3.4,+10.3]，Holm p=0.623。把异常名额保守按未解出纳入 30 题时，N−U-new 为 +10.0 个百分点，E-new−N 为 +1.7 个百分点，方向不变。当前不能把最终解出差称为已检出的稳定效应。

行为证据更一致：U-new/N/E-new 的 found_idle 次数为 24/9/3，分别占各组有效失败的 55.8%/25.0%/8.3%；可评分运行中从未调用编辑为 26/10/3，高重复轨迹为 29/10/5。严格 29 题配对 bootstrap 中，N−U-new 的编辑率差为 +25.9 个百分点、found_idle 差为 −24.1 个百分点；E-new−N 分别为 +15.5 和 −13.8 个百分点。该结果支持“干预能改变找到后是否继续编辑”的解释，但仍不能把差异唯一归因于一句可见话、结果解释本身或原有 harness 限制。

FIX-15 因此明确回答了问题二的一部分：在固定题集和单一模型上，加入可见下一步或完整结果解释后，“找到却不编辑”和重复循环大幅减少。它没有直接回答问题一（原有 harness 限制是否压住模型的全部能力），因为本轮没有无 harness 对照；也没有回答问题三，因为所有 FIX-15 组都关闭 thinking。正式结果和详细口径见 `docs/analysis/fix15/FIX-15-RESULTS.zh-CN.md`、`docs/analysis/fix15/FIX-15-INDEPENDENT-STATS.zh-CN.md`、`docs/analysis/fix15/report.json` 与 `scripts/analysis/fix15_report.py`。后续解释应把“最终解出率未达到稳定显著”与“行动行为强烈改变”同时保留，不把前者写成无效或把后者写成普遍因果定律。

## 全部实验总矩阵与跨批次结论（2026-11-15）

已把 FIX-PLAN-12、FIX-13、FIX-13 ET、FIX-14 和 FIX-15 汇总为一张总矩阵：5 个正式批次、13 个不同实验组配置、780 个最终正式运行名额。按各批次报告字段相加的标准峰价 usage 约 178.598039 元；不同批次费用字段含义略有差异，且没有与账户实际扣款统一对账，不能当作精确账单。

跨批次证据链为：FIX-12 的 A/U/U+T 解出率 23.3%/38.3%/73.3%，FIX-13 的 U/R/E/UT 为 26.7%/30.0%/43.3%/46.7%，ET 为 45.0%，FIX-14 的 T8/O32 为 43.3%/28.3%，FIX-15 的 U-new/N/E-new 为 28.3%/38.3%/40.0%。这些批次不能合并做一个总 p 值，因为题目、时间、代码续跑和有效名额不同；它们只用于检验方向是否重复出现。

总分析当前支持三点：一，thinking 是完成率提升中最可信的因素，FIX-14 在相同 8192 输出额度下得到 T8−U +20.4 个百分点且区间不跨零，单独扩大输出额度没有同样证据；二，E/N 类提示最稳定地改变过程行为，跨批次都减少 found_idle 和重复循环；三，E 相对 N 的完全解出增量在 FIX-15 只有 +3.4 个百分点且区间跨零，因此不能把 E 写成已证明的普遍能力提升。

问题一仍不能拆出每一项 harness 限制的独立贡献；问题二已经有较强的同批次支持，但只能说交互方式促成了“找到后不编辑”，不能唯一归因于某条提示或某个底层限制；问题三主要由 FIX-14 回答，FIX-15 本身没有测试 thinking。总矩阵和跨实验解释见 `docs/analysis/ALL-EXPERIMENT-MATRIX.zh-CN.md`。

## 展示版收敛第一阶段（2026-11-15）

用户同意先把 E 线和多种评测链路整理成求职展示版，不扩题集、不重跑正式实验。第一阶段已完成，范围是兼容层和无模型验证：

- `src/runtime/tool_intervention.py` 增加版本化 `InterventionSpec`；保留 `none`、`repeat_reminder`、`next_action`、`result_explanation` 的原提示词和触发规则。
- `AgentRunResult`、会话保存/恢复和 EventBus 增加独立的 `intervention_events`，不改变旧 `context_events` 顺序，也不改 FIX-12～FIX-15 的原始运行目录或评分规则。
- 新增 `src/eval/unified/` 兼容层，提供 `TaskSpec`、`RunSpec`、`GraderSpec`、`RunReceipt` 和历史行归一化；保留 `adapter`、`grader`、`raw_receipt` 与 `provenance`，不强行统一 mini、repo pytest、SWE-bench 的执行和评分语义。
- 新增只读命令 `scripts/unified_eval_report.py`。它只接受显式 JSON 数组或 `{ "rows": [...] }` 导出，不扫描 `runs/`，不调用模型/Docker，不修改输入；已读取 `docs/analysis/fix15/runs.json`，输出与正式报告一致：E-new 24/60、N 23/60（1 个无效）、U-new 17/60。
- 相关无模型验证为 `28 passed, 16 skipped`；`compileall` 通过。未启动付费实验。

当前仍未完成：三类 adapter 的真实执行接线、自动 manifest 入口、跨批次报告自动化和更广泛的非 Python 仓库验证。下一步先做一个 mini fixture 与一个 Docker mock 的展示 smoke，再决定是否需要更多工程化；旧实验继续以原 runner、grader 和原始报告为准。

随后已完成三类统一回执的无模型展示 smoke：mini、repo_pytest、swebench 均能保留各自的 `kind`、`adapter`、`grader`、`raw_receipt` 和 `provenance`，共 29 项相关测试通过、16 项按环境跳过。该 smoke 只验证统一记录外壳，不证明三个 adapter 已经可以自动执行；没有启动模型或 Docker，也没有改写历史证据。展示说明见 `docs/roadmap/UNIFIED-EVAL-SHOWCASE.zh-CN.md`。

在此基础上增加了最小 adapter facade：`ManifestEntry` 校验任务类型与 adapter 一致，`AdapterRegistry` 要求显式注册，不会隐式选择旧 runner，`MockAdapter` 仅用于无模型契约测试。当前仍没有把真实执行器接到统一入口，相关边界和风险见 `docs/roadmap/UNIFIED-EVAL-STATUS.zh-CN.md`。

## 求职展示包第一版完成（2026-11-15）

此前列出的四项展示内容已补齐到无模型、可复核的第一版：

- 统一命令现在支持显式 JSON/YAML manifest 的 `--manifest --mock` 模式；它可以同时构造 mini、repo_pytest、swebench 的 mock 回执。未提供 `--mock` 时不会隐式调用真实 runner。
- E 线展示报告见 `docs/SHOWCASE-E.zh-CN.md`，脚本为 `scripts/analysis/e_showcase_report.py`。它使用 FIX-15 导出摘要展示 U-new/N/E-new 的解出、found_idle、无编辑、高重复、编辑率、模型调用和费用，并明确 N 的无效名额和证据边界。
- 跨仓库报告见 `docs/SHOWCASE-CROSS-REPO.zh-CN.md`，脚本为 `scripts/analysis/showcase_cross_repo.py`。当前 fix13 题单有 30 道任务、30 个去重仓库；报告区分任务数、仓库数和正式运行数，不宣称任意 GitHub 或非 Python 项目兼容。
- 兼容性矩阵和边界已写入 `docs/roadmap/UNIFIED-EVAL-STATUS.zh-CN.md`。相关展示和契约测试合计 12 项通过，连同 E/历史/事件测试的总验证为 33 项通过、16 项环境跳过。

这四项目前是求职展示版，不等同于三类真实 adapter 已经共享同一个执行器。真实执行接线、一个命令跑真实 mini/repo/SWE-bench、以及更广泛的非 Python 验证仍是后续阶段；没有新增付费实验。

E 线又补充了逐题展示 `docs/SHOWCASE-E-TRACE.zh-CN.md` 和脚本 `scripts/analysis/e_trace_showcase.py`：它从 FIX-15 的显式运行摘要中并列同一题 U-new/N/E-new 的定位请求、首次编辑、编辑次数、尾部重复、状态、请求数和费用。该材料是轨迹摘要代理，不是完整 session 或内部思维观测；相关总验证为 41 项通过、16 项环境跳过。

随后增加 `MiniAdapter`，通过显式注入的 `agent_factory` 复用旧 `EvalRunner` 的单题执行逻辑；repo pytest/SWE-bench 仍未接入统一 CLI。加入该 adapter 的相关验证后，总测试为 42 项通过、16 项按环境跳过。统一展示入口目前分为历史回放、manifest mock 和真实 mini Python API 三种，不能宣传为三类真实执行器已经统一。

## E 核心与统一 adapter 接线完成（2026-11-15）

在用户批准后完成了下一段无模型工程工作：

- `InterventionSpec` 固定 `policy_version`、`config_hash` 和配置对象；`InterventionContext` / `InterventionDecision` 固定 schema 与字段校验，并暴露工具数、成功数、失败数、截断数等当前轮事实。
- `AgentRunResult.intervention` 记录策略名、版本、配置哈希、触发次数、提示注入次数、session 恢复标记和紧凑事件摘要；统一 `RunSpec` / `RunReceipt.provenance` 会保留这些信息。
- 新增 `src/runtime/intervention_fixtures.py` 的无模型 `showcase_policy`：成功读/搜后连续空转才产生候选提醒，失败结果不产生“已找到目标”断言，多工具轮不误判为单一重复调用。
- 新增 `RepoPytestAdapter` 和 `SwebenchAdapter`。两者都要求显式 `case_loader`，分别调用已有 `RepositoryGrader` 和 `ImageGrader`，保留 adapter-specific 原始回执和 Docker/grader 语义。
- `AdapterRegistry.execute_many_async()` 成为显式顺序调度面；不扫描目录、不猜 task、不隐式选择 runner。

验证：E/intervention 与统一评测相关测试 50 项通过，相关模块 `compileall` 和 `git diff --check` 通过；未调用模型、Docker 或付费 API。统一 adapter 已完成工程接线，但还没有启动真实 repo/SWE-bench 批次，也没有把行为统计误写进 adapter 评分。
