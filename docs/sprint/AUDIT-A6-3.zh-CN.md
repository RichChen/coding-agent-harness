# 审计：A6-3（正式运行）

日期：2026-10-14。审计对象：harness `539af1b` 的 `CHECKPOINT-A6-3.md` 和 `docs/sprint/a6-retry-runs.json`。

**结论：通过，可以交给 agent 侧做 A6-4 分析。** 计划不用改。402 重跑和下面两个现象都记为偏差和描述，不改主分析。

## 审计自己复核过的

- `a6-retry-runs.json` 的 `handoff_sha256` 是 `a0870f766a3b02f6ce34d6e5291ae59f8ab5160f46ae6e7671f3fd39f08a4256`，和 CHECKPOINT 一致；`scope: a6-retry`，`attempts_sha256` 是 `bd932529…`。
- 318 行，`run_dir` 互不重复，没有一行指向 `.infra` 目录，R0、R1 各 159 行。
- 318 个最终运行的 `events.jsonl` 里，没有一个出现 `note: "http"` 的结算记录，审计逐个扫描过。
- `a6-retry-freeze..HEAD` 之间，`scripts/`、`src/`、`configs/` 没有改动。

审计没有复核 status 和 stop_reason 的分布表、花费和独立注入核对，这几项以 CHECKPOINT 为准。

## 1. HTTP 402 重跑：认可，作为偏差写进结果报告

- 22 个槽位是按"最终运行里有 HTTP 失败的结算记录"这一条规则机械选出来的，选的时候没有看 solved，所以不会引入选择偏差。402 按定义属于 provider 不可用，性质和 infra 相同。
- 退役和 A5 用的是同一个函数（`retire_infra`），原目录保留了下来；单个槽位的 infra 额度上限 2 次也没有突破。
- 这条规则冻结时并没有写下来。结果报告要在"偏差"一节写明：发生了什么，22 个槽位各属哪组（R0 8 个、R1 14 个），按什么规则选出、怎么处理的，以及作废的 runs 文件 `05bea3fb…`（`a15696f`）。
- 留到以后的教训：provider 不可用应该算 infra。这次不改代码。

## 2. 两个描述性现象（不进主分析，结果报告里单独列出）

**`model_parse_error`：R1 11 次，R0 0 次。** A5 的 392 次运行里只出现过 2 次。审计看了一下这 11 次：没有一次有 HTTP 失败；大多数停在第 34–39 次调用，也就是快用完预算的时候。分布在 sphinx-10614 b0r2（3 次）、sympy-13091（4 次）、sympy-14248 b0r2（2 次）、sympy-15599、sympy-20428，其中 1 次的最终 status 是 pass，3 次是 partial。

这有可能是注入记录带来的副作用，比如上下文变长，或者记录里的 diff 和标签格式干扰了工具调用的格式。但这个模式是看到数据以后才发现的，样本也很少。所以：

- 主分析照旧，parse_error 按失败运行算。A5 也是这样处理的，计划第 1 节没有把它排除；
- 结果报告的"非预注册的描述"一节列出两组 stop_reason 的分布，再给出这 11 次的槽位、调用次数和最终 status；
- 不做显著性检验，不写"记忆导致了解析错误"这类因果句子，只写"R1 组观察到更多 parse_error，原因未查明"。

**`context_overflow`：R0 1 次，R1 3 次。** 处理方式同上，只描述。

## 3. A6-4 的要求（agent 侧）

1. 用 tag `a6-retry-freeze-r2` 上的脚本，不要用工作区里的版本。可以先 `git checkout a6-retry-freeze-r2 -- scripts` 检出到临时目录，或者确认 HEAD 上 `scripts/` 和这个 tag 没有差异。
2. 运行时传入下面三个参数：
   - `--expect-runs-sha a0870f766a3b02f6ce34d6e5291ae59f8ab5160f46ae6e7671f3fd39f08a4256`
   - `--expect-attempts-sha bd93252922c990ec61071caecb5a6bf5e604202cbbcf30d296f2b875794dd3ce`
   - `--harness-root D:\crq\src\coding-harness`

   脚本报 STOP 就停下，写 CHECKPOINT，不要绕过去。
3. 生成 `docs/sprint/A6-R-RESULTS.zh-CN.md`，里面的数字一律从脚本输出复制。脚本生成的部分不要手改。在它后面另起两节，标题注明"非脚本生成"：
   - **偏差**：第 1 节的 402 重跑；
   - **非预注册的描述**：第 2 节的 parse_error 和 context_overflow。数据直接读 `a6-retry-runs.json` 的 `stop_reason` 和 `status`，计算脚本要提交。
4. 脚本要是有 bug 必须改，单独提交，并写明改了什么、为什么改；旧的输出保留，不要覆盖。
5. 写 `docs/sprint/CHECKPOINT-A6-4.md`，内容包括：主分析的那一行结论原文、点估计、p 值、区间、s_obs、X、第二阶段触发与否的判定、次要指标表、跑脚本的完整命令，以及两个期望 sha 的核对结果。写完停下。最终叙述（和 A5 合在一起讲）由审计来写。

## 勘误（2026-11）

日期：2026-11-06。上文原文不改。

第 2 节对 R1 的 11 次 `model_parse_error` 要求写"原因未查明"，并猜测可能是记录里的 diff 和标签格式干扰了工具调用。2026-11 回看查明：这 11 次都是单次运行 1.000 元上限拒绝了下一次调用的预留（`CnyBudgetError`），被 `src/runtime/loop.py`（tag `a6-retry-freeze` 的 L719–736）的通用 `except` 记成了 `MODEL_PARSE_ERROR`，和工具调用的格式无关。"快用完预算"的观察是对的，只是撞上的是人民币上限，不是 40 次调用上限。A5 的 2 次原因相同。另外，第 2 节写的"3 次是 partial"按 runs 文件是 4 次，`AUDIT-A6-4` 已改正。

事实、复算数字和对结论的影响写在 coding-agent `docs/sprint/A6-R-RESULTS.zh-CN.md` 的"勘误（2026-11）"一节。复算脚本是本仓库的 `scripts/audit/errata_2026_11/e2_cny_cap_parse_errors.py`。
