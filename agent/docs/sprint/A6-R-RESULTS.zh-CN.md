# A6-R 分析

- `a6-retry-runs.json` handoff_sha256：`a0870f766a3b02f6ce34d6e5291ae59f8ab5160f46ae6e7671f3fd39f08a4256`
- `a6-attempts.json` handoff_sha256：`bd93252922c990ec61071caecb5a6bf5e604202cbbcf30d296f2b875794dd3ce`
- runs 里记录的 `attempts_sha256`：`bd93252922c990ec61071caecb5a6bf5e604202cbbcf30d296f2b875794dd3ce`，一致

解出率是非 infra_error 运行里的解出次数除以有效运行数。
每题每组有效运行少于 2 次的题不进入主分析。
置换检验和 bootstrap 各 10000 次，种子 20261013。

## 输入核对

- 第一次尝试：53 次、22 道题（预期 53/22）
- 槽位行数：318（预期 318）
- infra_error：0 次
- 列出的退役目录（未计入）：无
- 缺的槽位：无
- 重复的槽位：无
- 清单外的槽位：无
- run_dir 与槽位不符：无
- R1 的 attempt_sha256 与清单不符：无
- R0 的 prompt_sha256 与 A5 B0 不同的题：无

## 逐题

| 题 | 仓库 | A5 B0 档位 | 第一次尝试数 | R0 | R1 | d_t |
|---|---|---|---|---|---|---|
| django__django-13512 | django/django | 0/3 | 3 | 0/9 | 2/9 | 0.222 |
| django__django-13964 | django/django | 中间档 | 1 | 2/3 | 1/3 | -0.333 |
| django__django-15022 | django/django | 中间档 | 2 | 0/6 | 3/6 | 0.500 |
| django__django-15973 | django/django | 0/3 | 3 | 2/9 | 0/9 | -0.222 |
| pytest-dev__pytest-5840 | pytest-dev/pytest | 0/3 | 3 | 0/9 | 0/9 | 0.000 |
| pytest-dev__pytest-7324 | pytest-dev/pytest | 0/3 | 3 | 1/9 | 2/9 | 0.111 |
| pytest-dev__pytest-7490 | pytest-dev/pytest | 中间档 | 1 | 3/3 | 2/3 | -0.333 |
| scikit-learn__scikit-learn-10908 | scikit-learn/scikit-learn | 中间档 | 1 | 3/3 | 2/3 | -0.333 |
| scikit-learn__scikit-learn-12973 | scikit-learn/scikit-learn | 0/3 | 3 | 1/9 | 0/9 | -0.111 |
| sphinx-doc__sphinx-10614 | sphinx-doc/sphinx | 0/3 | 3 | 0/9 | 0/9 | 0.000 |
| sphinx-doc__sphinx-7590 | sphinx-doc/sphinx | 0/3 | 3 | 0/9 | 0/9 | 0.000 |
| sphinx-doc__sphinx-8120 | sphinx-doc/sphinx | 中间档 | 2 | 5/6 | 0/6 | -0.833 |
| sphinx-doc__sphinx-9258 | sphinx-doc/sphinx | 中间档 | 2 | 5/6 | 4/6 | -0.167 |
| sphinx-doc__sphinx-9461 | sphinx-doc/sphinx | 0/3 | 3 | 0/9 | 0/9 | 0.000 |
| sympy__sympy-11618 | sympy/sympy | 中间档 | 1 | 0/3 | 1/3 | 0.333 |
| sympy__sympy-13091 | sympy/sympy | 0/3 | 3 | 2/9 | 1/9 | -0.111 |
| sympy__sympy-14248 | sympy/sympy | 0/3 | 3 | 0/9 | 0/9 | 0.000 |
| sympy__sympy-15599 | sympy/sympy | 0/3 | 3 | 0/9 | 0/9 | 0.000 |
| sympy__sympy-19040 | sympy/sympy | 中间档 | 2 | 0/6 | 0/6 | 0.000 |
| sympy__sympy-20428 | sympy/sympy | 0/3 | 3 | 0/9 | 0/9 | 0.000 |
| sympy__sympy-21596 | sympy/sympy | 0/3 | 3 | 0/9 | 0/9 | 0.000 |
| sympy__sympy-23950 | sympy/sympy | 中间档 | 2 | 3/6 | 2/6 | -0.167 |

## 剔除

无

## 主分析

- 题数：22
- 平均 d_t：-0.0657
- 双侧符号翻转 p：0.2880
- 95% 区间：-0.1768 到 0.0379
- d_t 样本标准差 s：0.2660
- 数据检测下限：15.9 个百分点（2.8×s/√N）
- 95% 区间上限：3.8 个百分点
- 结论：未检出大于 15.9 个百分点的效应（2.8×s/√N）；95% 区间上限 3.8 个百分点。点估计 -6.6 个百分点，95% 区间 -17.7 到 3.8
- 事先算好的功效（OPTIONS 模拟，假设每次失败运行同一加性提升）：+10pp 约 54%，+15pp 约 85%，+20pp 约 98%。
- 第二阶段触发（点估计 ≥ +5pp 且区间含 0）：不满足，不做第二阶段

## 次要指标（描述性，不做检验，不进结论句）

只算进入主分析的题的有效运行。

### 1. diff 重复率

- R0：29/159（0.182）
- R1：44/159（0.277）

### 2. 文件复用

- R0：104/159（0.654）
- R1：111/159（0.698）

### 3. first_edit_step

- R0：平均 15.367，中位数 12.0，null 20 次
- R1：平均 16.376，中位数 12.0，null 18 次

### 4. model_calls

- R0：平均 31.377，用满 40 次 89/159（0.560）
- R1：平均 32.082，用满 40 次 87/159（0.547）

### 5. 花费 CNY

- R0：平均 0.497
- R1：平均 0.589

### 6. 按 A5 B0 档位（只描述，不作为效应证据）

- 0/3：13 题，平均 d_t -0.0085
- 中间档：9 题，平均 d_t -0.1481

---

以上是 `scripts/analyze_a6_retry.py`（tag `a6-retry-freeze-r2`，`6c67739`）的原样输出，和 `docs/sprint/A6-R-ANALYSIS-OUTPUT.zh-CN.md` 逐字节相同，没有手改。运行命令和期望 sha 的核对见 `CHECKPOINT-A6-4.md`。下面两节不是分析脚本生成的。

## 偏差（非脚本生成）

依据 harness `CHECKPOINT-A6-3.md` 和 `AUDIT-A6-3.zh-CN.md` 第 1 节。槽位名单由 `scripts/describe_a6_stops.py` 从 harness 的退役记录 `runs/a6-retry-logs/provider402-retired.json` 读出，见 `A6-R-STOP-DESCRIPTION.zh-CN.md`。

**发生了什么。** 第一轮正式运行跑到最后约 4 分钟（03:47:28–03:51:12），DeepSeek 账户余额耗尽，22 次最终运行被 `HTTP 402 Insufficient Balance` 截断：3 次是在运行中途，失败的是最后一次调用；19 次是第一次调用就失败。`run_swe_task` 把 provider 的 HTTP 错误当作模型调用失败处理，以 `model_parse_error` 结束运行，status 由 grader 给出。所以这些运行不是 `infra_error`，冻结的停止条件也都没有拦住。

**属于哪组。** R0 8 个，R1 14 个：

- R0：django__django-15022/r0/b0r3/r1、django__django-15973/r0/b0r1/r2、sphinx-doc__sphinx-10614/r0/b0r1/r2、sphinx-doc__sphinx-10614/r0/b0r3/r2、sympy__sympy-13091/r0/b0r3/r3、sympy__sympy-15599/r0/b0r1/r3、sympy__sympy-21596/r0/b0r2/r3、sympy__sympy-21596/r0/b0r3/r1
- R1：django__django-13512/r1/b0r1/r1、django__django-15973/r1/b0r2/r2、django__django-15973/r1/b0r3/r3、pytest-dev__pytest-5840/r1/b0r2/r1、scikit-learn__scikit-learn-10908/r1/b0r3/r1、scikit-learn__scikit-learn-12973/r1/b0r1/r3、sphinx-doc__sphinx-8120/r1/b0r3/r3、sympy__sympy-14248/r1/b0r1/r1、sympy__sympy-14248/r1/b0r2/r1、sympy__sympy-15599/r1/b0r1/r2、sympy__sympy-20428/r1/b0r2/r3、sympy__sympy-21596/r1/b0r2/r1、sympy__sympy-21596/r1/b0r3/r2、sympy__sympy-23950/r1/b0r2/r1

**选取规则。** 最终运行的事件日志里，只要有 `model.settled` 结算记录带 `note: "http"`（HTTP 层失败），就选中。规则是机械的，选的时候没有看 solved，正好选出 22 个，一个不多一个不少。冻结记录里没有写这条规则，是事后补的。

**怎么处理的。**

- 用户充值后，22 个槽位用 A5 和本轮 runner 退役 infra 的同一个函数 `retire_infra` 改名为 `.infra1`。原目录保留，summary 没有改。
- 之后用 `--slot` 按原队列顺序逐个重跑。
- 重跑中有 2 次容器丢失（`container_gone_before_snapshot`），被改名为 `.infra2` 后又重跑一次：sphinx-doc__sphinx-10614/r0/b0r3/r2 和 sympy__sympy-15599/r0/b0r1/r3。单槽位最多 2 次 infra 重跑的上限没有突破。
- 重跑后，318 个最终运行里没有一个带 HTTP 失败的结算记录，审计逐个扫描确认过。

**对分析的影响。**

- 分析用的是重跑后重写的 `a6-retry-runs.json`（harness `087babd`，handoff_sha256 `a0870f76…`）。
- 第一轮写出的那份 `a6-retry-runs.json`（`a15696f`，sha `05bea3fb…`）里有 22 行是 402 截断的，已作废，没有用于分析。
- 22 个 402 退役目录和 9 个真正的 infra_error 退役目录都不在 runs 文件里，所以脚本看到的 infra_error 是 0 次。
- 按 harness CHECKPOINT，402 退役目录合计花费 2.662 元，`runs/a6-retry/` 累计花费 178.709 元，低于 `TOTAL_CAP` 210 元。

**以后的教训**（审计第 1 节）：provider 不可用应该算 infra。这次不改代码。

## 非预注册的描述（非脚本生成）

数据由 `scripts/describe_a6_stops.py` 直接从 `a6-retry-runs.json` 的 `stop_reason`、`status`、`model_calls` 读出，完整输出在 `A6-R-STOP-DESCRIPTION.zh-CN.md`。这个模式是看到数据以后才注意到的，只描述，不做检验，不进主分析。主分析照旧，这些运行按 grader 给出的 solved 计入，没有排除。

**stop_reason 按组分布**（每个槽位的最终运行）：

| stop_reason | R0 | R1 |
| --- | --- | --- |
| max_model_calls | 87 | 79 |
| completed | 71 | 66 |
| model_parse_error | 0 | 11 |
| context_overflow | 1 | 3 |
| 合计 | 159 | 159 |

**status 按组分布：**

| status | R0 | R1 |
| --- | --- | --- |
| fail | 94 | 105 |
| partial | 32 | 31 |
| pass | 27 | 20 |
| delta_lost | 6 | 3 |
| 合计 | 159 | 159 |

**`model_parse_error`：R1 11 次，R0 0 次。** 原因未查明。按 harness CHECKPOINT，这 11 次都没有 HTTP 失败的结算记录。其中 10 次在第一轮，1 次（sympy__sympy-14248/r1/b0r2/r1）是 402 之后的重跑。最终 status：pass 1 次，partial 4 次，fail 6 次。审计第 2 节写的是"3 次是 partial"，按 runs 文件是 4 次；差的这一次可能就是重跑的 sympy__sympy-14248/r1/b0r2/r1，这里以 runs 文件为准。

| 槽位 | model_calls | 最终 status |
| --- | --- | --- |
| sphinx-doc__sphinx-10614/r1/b0r2/r1 | 39 | fail |
| sphinx-doc__sphinx-10614/r1/b0r2/r2 | 39 | fail |
| sphinx-doc__sphinx-10614/r1/b0r2/r3 | 39 | fail |
| sympy__sympy-13091/r1/b0r2/r2 | 39 | partial |
| sympy__sympy-13091/r1/b0r2/r3 | 34 | fail |
| sympy__sympy-13091/r1/b0r3/r1 | 39 | partial |
| sympy__sympy-13091/r1/b0r3/r3 | 39 | pass |
| sympy__sympy-14248/r1/b0r2/r1 | 39 | partial |
| sympy__sympy-14248/r1/b0r2/r3 | 39 | partial |
| sympy__sympy-15599/r1/b0r3/r2 | 39 | fail |
| sympy__sympy-20428/r1/b0r1/r2 | 35 | fail |

**`context_overflow`：R0 1 次，R1 3 次。** 原因未查明。R0 那一次是 canary 槽位。

| 槽位 | 组 | model_calls | 最终 status |
| --- | --- | --- | --- |
| django__django-13964/r1/b0r2/r1 | R1 | 34 | fail |
| pytest-dev__pytest-5840/r1/b0r2/r3 | R1 | 36 | fail |
| sphinx-doc__sphinx-7590/r0/b0r1/r3 | R0 | 18 | fail |
| sphinx-doc__sphinx-8120/r1/b0r3/r3 | R1 | 40 | fail |

**两个计数的口径不同。** 次要指标 4 里的"用满 40 次"按 `model_calls ≥ 40` 计，R0 89、R1 87；上表 stop_reason 为 `max_model_calls` 的是 R0 87、R1 79。差额是用满 40 次调用、但 stop_reason 是 completed（R0 2、R1 7）或 context_overflow（R1 1）的运行，两者不是同一个量。

## 勘误（2026-11）

日期：2026-11-06。上文原文（包括脚本生成的部分）不改，本节补充 2026-11 回看时查明的原因。数字来自 harness `scripts/audit/errata_2026_11/` 下的复算脚本：第一项是 `e2_cny_cap_parse_errors.py`，第二项是 `e5_a6r_context_overflow.py`，输出是同目录同名的 `.json`。脚本只读 `runs/a6-retry/`、`runs/a5-main/` 和对应日志，都不调用模型；`e2` 离线构建一次 agent，只为拿到工具 schema。

### R1 的 11 次 `model_parse_error` 是单次运行 1 元上限拦截

**原来的说法。** 上文"非预注册的描述"一节写 R1 的 11 次 `model_parse_error`"原因未查明"。`MEMORY-FINAL-REPORT.zh-CN.md` 第 5.4 节、harness `AUDIT-A6-3.zh-CN.md` 和 `AUDIT-A6-4.zh-CN.md` 沿用了这个说法。

**实际情况。** 这 11 次不是模型输出解析失败，是单次运行的人民币预算上限拒绝了下一次调用的预留。下面的行号都按运行时的 harness tag `a6-retry-freeze`：

- 每次模型调用之前，`src/runtime/retry.py`（L123–134）按估算的输入 token 数向 `CnyBudgetLedger` 预留费用。输入按缓存未命中价 2 元/百万 token 计，输出按 max_output 8192 token × 8 元/百万计。
- 单次运行的账本由 `scripts/run_swe_task.py` 的 `make_ledger`（L418 起）建立，上限 1.000 元。已结算花费加上这笔预留超过上限时，`src/policy/cny_budget.py` 的 `reserve`（L121–131）抛出 `CnyBudgetError("CNY hard cap would be exceeded")`。
- `src/runtime/loop.py` L719–736 的通用 `except` 把这个异常记成 `MODEL_PARSE_ERROR` 结束运行，错误原文只写进日志（`LLM call failed: ...`），不进 summary 和事件。2026-11 的 harness 提交 `846e3a0` 起，这种情况单独标记为预算上限，不再混进 `model_parse_error`；这次勘误不涉及那项代码修改。

**复算结果。**

- 11 次都在 R1，R0 0 次。累计花费 0.903–0.940 元。按事件重建失败那一步的预留是 0.093–0.104 元，两者相加 1.001–1.044 元，11 次全部超过 1.000 元。重建方法先在之前每一次调用上验证过：重建的输入 token 估算和 `model.settled` 记录里的 `estimated_input_tokens` 全部相等。
- 11 次都没有到 40 次调用上限，分别少了 1 到 6 次调用（9 次只少 1 次）。最终 status：pass 1、partial 4、fail 6。
- 日志里 `CNY hard cap would be exceeded` 出现的次数：harness `runs/a6-retry-logs/main.out` 10 次，`rerun-2.out` 1 次，和"10 次在第一轮、1 次是 402 之后的重跑"对得上。
- 事件上的形态：最后一次 `model.settled` 之后有 `context.prepared`，没有 `model.started`。这个形态不是独有的：166 次 `max_model_calls` 运行（R0 87、R1 79）结尾也是这样，`completed` 和 `context_overflow` 的运行没有。能区分两者的是调用次数不到 40。
- 两组的花费：R1 平均每次 0.589 元，R0 0.497 元；花费 ≥0.9 元的运行 R1 12 次，R0 0 次；R0 最高 0.882 元，R1 最高 0.940 元。
- A5 的 2 次 `model_parse_error` 原因相同：scikit-learn-9288 B2 r2 花费 0.923 元、预留 0.102 元；sympy-15599 B0 r3 花费 0.937 元、预留 0.091 元，相加都超过 1.000 元。harness `runs/a5-main-logs/` 里只找到 1 条同样的日志行（`b2-full-stop2.log`），另一次的判断只依据账本和事件重建。

**对结论的影响。**

- 这是一个没有声明的组间预算不对称。上限对两组相同，但 R1 的 prompt 带着最长 8192 字节的上次记录，每次请求都要重发，所以每次运行更贵，更容易撞上 1 元上限。
- 这 11 次被提前截断，少了 1 到 6 次调用，其中 1 次仍然解出，4 次 partial。偏差的方向对 R1 不利，会让 R1 的解出数偏低；偏低多少，现有数据估计不出来。
- 主分析按预注册执行，这些运行按 grader 给出的 solved 计入，主结论不变。引用 R1−R0 时应同时写明这项偏差。

### 4 次 `context_overflow` 是 harness 按字节估算的准入拦截，模型窗口没有满

**原来的说法。** 上文"非预注册的描述"一节写 `context_overflow`（R0 1 次，R1 3 次）"原因未查明"。`MEMORY-FINAL-REPORT.zh-CN.md` 第 5.4 节沿用了这个说法，第 6.2 节第 3 条把"R1 的 context_overflow 更多"列为负担更重的表现之一。

**实际情况。** 被拦下的请求没有发给模型，是 harness 在发送前的准入检查拒绝的。下面的行号按运行时的 harness tag `a6-retry-freeze`：

- `src/runtime/window.py` 的 `request_size`（L29–36）把整个请求（消息加工具 schema）序列化成 UTF-8，按 1 字节算 1 个 token，再加协议余量（512，每条消息 32，每个工具 128），得到 `admission_input_tokens`。代码注释写明这是保守的字节估算，不是实测用量。
- `admit`（L68–75）在估算加上预留输出 8,192 超过 profile `configs/sprint/b0-v2.yaml` 的窗口 `max_tokens: 96000` 时抛出 `ContextOverflowError`。
- `src/runtime/loop.py` L648–657 把这次拒绝写成 `context.rejected` 事件，以 `context_overflow` 结束运行。被拒的那一步没有 `context.prepared`；最后一条 `context.prepared` 是上一步已经放行的请求，只看它会以为上下文没有超限。

**复算结果。**

- 4 次运行都只有 1 条 `context.rejected`，它的 step 紧接在最后一条 `context.prepared` 之后，最后一条 `context.prepared` 没有超限标记。
- 被拒请求的准入估算是 87,823–92,548，加上预留输出 8,192，全部超过 96,000。
- 同一次运行里，前一次成功调用的真实 `prompt_tokens`（服务商返回）是 17,843–35,329，也是各自运行里的最大值。字节估算是它的 2.49–5.19 倍。被拒请求只比前一次多一轮回复和工具输出，它的真实大小没有记录；模型本身的上下文窗口（DeepSeek 官方文档为 1M）远大于 96,000。
- 402 退役的运行里没有 `context_overflow`；A5 的 392 次运行里也是 0 次。

| 槽位 | 组 | 成功调用 | 准入估算 | 前一次 prompt_tokens | 上次记录字节 |
| --- | --- | --- | --- | --- | --- |
| django__django-13964/r1/b0r2/r1 | R1 | 34 | 92,548 | 17,843 | 673 |
| pytest-dev__pytest-5840/r1/b0r2/r3 | R1 | 36 | 89,463 | 20,758 | 4,793 |
| sphinx-doc__sphinx-7590/r0/b0r1/r3 | R0 | 18 | 87,823 | 35,329 | 0 |
| sphinx-doc__sphinx-8120/r1/b0r3/r3 | R1 | 40 | 88,306 | 19,744 | 1,330 |

**对结论的影响。**

- 这 4 次运行是被 harness 自己的保守估算截断的，不是上下文真的装不下。sphinx-8120 被拒时已经用满 40 次调用，没有损失；另外 3 次少了调用：django-13964 少 6 次，pytest-5840 少 4 次，R0 的 sphinx-7590 少 22 次。4 次最终都是 fail。
- 3 次在 R1，和上一项的 1 元上限一样，是没有声明的组间不对称，来源也有一部分相同：R1 的任务 prompt 带着上次记录（R1 平均 2,881 字节，最长 8,173 字节；R0 没有），每次请求都重发，而准入估算按 1 字节 1 token 计。粗算把记录的字节从被拒请求的估算里去掉，pytest-5840 和 sphinx-8120 那一步会放行，django-13964 仍然超限；sphinx-8120 放行后也已经到调用上限。所以记录本身只能解释 pytest-5840 这 1 次的截断。4 次太少，看不出 R1 是否系统性地更容易溢出。
- 截断的调用数 R0 多于 R1，偏差方向不确定，量也很小。主分析按预注册执行，主结论不变。`MEMORY-FINAL-REPORT.zh-CN.md` 第 6.2 节第 3 条里的"context_overflow 更多"不应作为 R1 负担更重的证据。
