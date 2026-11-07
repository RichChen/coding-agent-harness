# 修复计划 10 结果：为 A / U / U+T 做的工程准备

本轮所有新能力默认关闭。冻结配置的哈希和运行时指纹没有变化。付费调用只有 E5 冒烟测试一处：9 次请求，扣费 0.003413 元。

## 1. 各项提交与改动前后的测试

每一项都先写测试，再实现。下表“改动前”是测试写好、实现还没写时的结果，“改动后”是实现完成后同一批测试的结果。原始输出在 `runs/fix10/eN-before.txt` 和 `eN-after.txt`（`runs/` 不入库）。

| 项 | 提交 | 内容 | 改动前 | 改动后 |
|---|---|---|---|---|
| E1 | `ae44f69` | ImageGrader 可选：自动加载镜像里的 pytest 插件、透传 `test_cmd` 的选项、收集节点 ID 并修复被截断的 ID | 1 error（导入失败） | 43 passed |
| E3 | `7807ca5` | CNY 账本可选按服务商 usage 结算、可选分时价；核对脚本 `scripts/cny_reconcile.py` | 10 failed, 1 passed；核对脚本测试 1 error | 53 passed |
| E4 | `7f838c0` | 可选的逐次请求遥测，写入 `context.prepared` 和 `model.settled` | 1 error | 38 passed |
| E5 | `47dea15` | DeepSeek thinking 模式：解析、回放、持久化 `reasoning_content`；冒烟脚本 | 15 failed, 8 passed | 51 passed |
| E2 | `a2267c1` | `run_swe_task` 的 `rebench` family；批量、可续跑、按 digest 固定的准备脚本；不调模型的端到端检查 | 19 failed | 19 passed |
| E6 | `cf0aff8` | 会话制品配额改为可配置（默认不变）；限额用量探针和会话增长基准 | 4 failed, 4 passed | 8 passed |

E6 改动前就通过的 4 个用例是“非法值应被拒绝”。字段还不存在时，配置模型的 `extra="forbid"` 同样会拒绝，所以它们在实现前也能过。另外 4 个检查实际行为的用例在改动前都失败。

## 2. 完整测试集与冻结哈希

| 时点 | 结果 |
|---|---|
| 开工前基线 | 1384 passed, 40 skipped |
| 全部完成后（`cf0aff8`） | 1471 passed, 40 skipped |

新增 87 个测试，没有失败。

冻结配置由 `tests/test_fix1_config_lock.py` 检查，最终运行全部通过：

- 23 份配置的 `canonical_hash` 和 `session_config_hash` 与记录的值完全一致；
- 其中 11 份能构建出 agent，它们的 `runtime_fingerprint` 也一致。

新增的配置项都不进配置导出，只有设了非默认值才出现。涉及的字段有：

- `llm.reasoning_effort`；
- `tracing.request_telemetry`；
- `tracing.session_artifact_bytes` 和 `tracing.session_artifact_item_bytes`。

此外，账本快照只在非默认结算方式下才带 `settlement` 字段，运行时指纹只在设置了 `reasoning_effort` 时才包含它。

## 3. E2：5 道试点题的准备与 `--mock` 端到端

### 准备（真实 Docker，不调模型）

命令是 `scripts/rebench/prepare.py`，默认处理 5 道试点题，每道题的空补丁和标准补丁各判 3 次。5 道题共用时约 16 分钟。

每道题按以下阶段推进，进度记在 `runs/rebench/work/<id>/state.json`，重跑时跳过已完成的阶段：

1. 下载源码；
2. 写任务文件；
3. 构建镜像；
4. 答案隔离检查；
5. 校准；
6. 输出。

`--redo STAGE` 可以从指定阶段重做；如果本地已经没有记录的干净镜像，会自动重做镜像阶段。

镜像引用方式：

- 官方镜像按本地 RepoDigest 固定，派生镜像的 Dockerfile 写的是 `FROM <仓库>@sha256:…`；
- 清单和 receipt 里只出现 digest，`pinned()` 会拒绝任何不带 `@sha256:` 的引用。

| 题目 | 接受 | 空补丁 F2P | 空补丁 P2P | 标准补丁 F2P | 标准补丁 P2P | 修复截断 ID | 剔除 P2P |
|---|---|---|---|---|---|---|---|
| pypa__twine-1309 | 是 | 0/1 | 3/3 | 1/1 | 3/3 | 1 | 0 |
| delgan__loguru-1451 | 是 | 0/2 | 22/22 | 2/2 | 22/22 | 0 | 0 |
| pycqa__isort-2491 | 是 | 0/1 | 73/73 | 1/1 | 73/73 | 0 | 0 |
| holoviz__param-1117 | 是 | 0/2 | 94/94 | 2/2 | 94/94 | 0 | 0 |
| pypa__build-1027 | **否** | 0/4 | 63/64 | **0/4** | 63/64 | 1 | 1 |

5 道题的答案隔离检查全部通过，3 次重复的 F2P 结果都一致。接受的 4 道题写入了 `configs/rebench/tasks.yaml`，干净镜像记录在 `docs/rebench/clean-images/`。

### 端到端（`run_task(mock=True)`，判分走真实 Docker）

脚本是 `scripts/rebench/mock_e2e.py`。每道题跑两个不调模型的 agent：

- **空 agent**：直接结束；
- **标准答案 agent**：先通过 agent 自己的文件工具把标准补丁改进工作区（每个 hunk 一次 `search_replace`；新文件用 `write_file`；删除用 `run_command`），再结束。

两者都走完整条链路：干净镜像、agent 循环、导出候选补丁、ImageGrader 判分（使用清单里的判分选项）、空补丁复判。

| 题目 | 空 agent | 标准答案 agent | 标准答案的工具调用 | 导出的改动 | 费用 |
|---|---|---|---|---|---|
| pypa__twine-1309 | fail | pass | 1 | `twine/__main__.py` | 0 |
| delgan__loguru-1451 | fail | pass | 2 | `loguru/_handler.py` | 0 |
| pycqa__isort-2491 | fail | pass | 6 | `isort/output.py` | 0 |
| holoviz__param-1117 | fail | pass | 9 | `param/reactive.py` | 0 |

8 次运行都满足以下条件：

- 工具报错 0；
- `answer_isolation_pass` 为 true；
- `clean_image_digest` 是固定的 digest；
- 模型调用都是 mock，费用 0。

命令行入口 `run_swe_task.py --tasks configs/rebench/tasks.yaml --task pypa__twine-1309 --mock` 也单独跑通了，结果为 fail，判分正常。证据在 `docs/analysis/fix10/e2-rebench-mock-e2e.json`。

### pypa__build-1027 被拒的原因

试点阶段这道题被接受，原因是试点用的是独立的 `RebenchGrader`。现在准备脚本改用正式运行同一套 ImageGrader 判分，标准补丁的 F2P 变成 0/4。

逐项排查的结果：

- 同一个镜像上用 `RebenchGrader` 判，结果是 4/4；
- 给 ImageGrader 补上 `HOME` 环境变量后仍是 0/4；
- 真正的原因是 `is_export_skipped` 会丢掉任何路径里有一层目录叫 `build` 的文件。这条规则本来是为了排除构建产物，但这个仓库的包本身就在 `src/build/` 下。

把这道题临时放进清单、走一遍真实运行路径，可以直接看到后果：标准答案 agent 的 4 次工具调用都成功了，但导出的改动只剩 `docs/changelog/860.bugfix.rst`，`src/build/_util.py` 的改动被丢掉。

准备脚本把这道题拒掉是对的：用现有的导出规则，agent 怎么改都拿不到分。导出规则要不要改另行决定，见第 7 节。

## 4. E3：用历史运行重算结算差异

核对对象是 CR-2 主实验的 1278 次运行。每次运行用账本自己的结算方式重算，结果与账本记录全部一致，不一致 0 次。

| 口径 | 金额（元） |
|---|---|
| 原账本（全部按未命中价预留并结算） | 547.394 |
| 按服务商 usage，全部按高峰价 | 226.502（扣除无 usage 调用的预留后为 211.916） |
| 按服务商 usage，再按分时价 | 152.492（扣除后为 137.906） |
| 实际账单 | 137.95 |

调用统计：

- 已结算 37718 次，其中 37531 次带 usage；
- 187 次没有 usage，这些调用的预留合计 14.586 元，原样计入了账本；
- 按北京时间，高峰时段 10930 次，低谷时段 26601 次。

按 usage 加分时价重算，扣除无 usage 预留后与账单只差 0.04 元。原账本高估约 4 倍，一是缓存命中也按未命中价算，二是没算低谷半价。

小规模试跑：原账本 3.049 元，按高峰价 1.397 元，按分时价 0.699 元（全部在低谷时段）。

新账本方式通过 `--cny-settlement provider_usage` 和 `--cny-time-of-day` 开启，默认仍是旧方式。预留始终按未命中价，所以预算上限不会因为换了结算方式而变松。重算结果在 `runs/fix10/e3-reconcile-cr2-*.json`。

## 5. E5：thinking 冒烟测试

请求的总体情况：

- 共 9 次请求，账本按 usage 加分时价结算，合计 0.003413 元，全部在周六低谷时段；
- 上限设为 10 次、0.5 元，实际都没碰到；
- 模型 deepseek-flash，`max_tokens` 2048。

脱敏后的结果在 `docs/analysis/fix10/e5-thinking-smoke.json`。

| 用例 | 内容 | 结果 |
|---|---|---|
| A | 带工具、开 thinking | 发起了工具调用；`reasoning_content` 是空字符串，reasoning tokens 为 0 |
| B | 回放时带上 reasoning | 200；reasoning 14 tokens |
| C | 回放时省略 `reasoning_content` | 200，不是文档说的 400（但原始 reasoning 本来就是空的） |
| F | 带工具的请求里，历史中有一条没有 reasoning 的 assistant 消息 | 200 |
| D | 流式 | reasoning 为空，发起了工具调用 |
| E | `max_tokens` 32 | finish 为 `length`，completion 32 全是 reasoning，content 为 None |
| G | 关闭 thinking | 没有 `reasoning_content`，也没有 details |
| H | 走 AgentLoop 完整一轮 | 2 次请求，答对（"7341"）；第 2 次有 47 个 reasoning tokens |

确认了的官方规则：

- `thinking: {type: enabled}` 打开后，响应里有 `reasoning_content`，用量里有 `completion_tokens_details.reasoning_tokens`；
- `max_tokens` 同时限制 reasoning 和正文，`completion_tokens` 包含 reasoning（用例 E）；
- 关闭后不返回 reasoning（用例 G）；
- 流式能逐段给出 reasoning 和工具调用（用例 D）；
- 回放时带上 reasoning 可以被接受（用例 B）。

没能复现的规则：文档说带工具时缺少 `reasoning_content` 会返回 400，但用例 C 和 F 都是 200。用例 C 的原始 reasoning 是空的，所以这条只能算“没有观察到”，不能算推翻。

实现里做的假设：

- reasoning tokens 按输出价计费，已经包含在 `completion_tokens` 里；
- 不建模节假日，节假日按工作日处理（工作日的高峰时段照常收高峰价）；
- harness 始终回放 reasoning，空字符串也回放，以免撞上 400 规则；
- 摘要请求一律关闭 thinking；
- `temperature` 照常发送，thinking 模式下服务端会忽略；
- 只使用 `tool_choice: auto`，因为 thinking 模式下 required 和指定工具会返回 400。

## 6. E6：硬限制清单与建议值

实测数据来自 CR-2 主实验里调用次数最多的 60 次运行，它们都用满了 40 次模型调用，都没开 thinking。外推用的是这些运行的 p90 增长率：每次调用让消息历史增加约 6.8 KB，每次调用保存约 2.02 份状态快照。

另外做了一个合成会话基准（`scripts/fix10_session_growth.py`）。它不调模型、不起 Docker，只按同样的增长率往真实的 `RunSession` 里追加事件，直接测量存储量和时间。

| 限制 | 位置 / 默认 | b0 当前 | CR-2 实测最大（40 次调用） | 随运行变长 | 会不会先卡住 | 本次处理 | 建议值（约 200 次调用、开 thinking） |
|---|---|---|---|---|---|---|---|
| 运行总时长 | `runtime.total_timeout_seconds`，600 s | 1200 s | 303 s | 约 7.6 s/次调用，再加上下面的会话记录开销 | 会，约 150 次调用 | 已可配，不改 | 7200 s |
| 单步超时（含模型调用） | `runtime.step_timeout_seconds`，120 s | 120 s | 66.4 s（工具 60.5 s，模型 11.1 s） | 与长度无关，但 thinking 会拉长单次调用 | 开 thinking 后可能 | 已可配，不改 | 300 s |
| 命令超时 | `policy.command.timeout_seconds`，30 s | 120 s | 60.5 s | 无关 | 不会 | 不改 | 120 s |
| LLM HTTP 超时 | `LLMClient.timeout_seconds`，120 s，不可配 | 120 s | — | 流式下限制的是两个数据块之间的间隔 | 流式下不会 | 不改 | 保持流式 |
| 运行级输出 | `policy.command.run_output_limit_bytes`，16 MiB | 16 MiB | 67 KB | 约 1.7 KB/次调用 | 不会（约上万次调用才到） | 已可配，不改 | 16 MiB |
| 事件表配额 | `EventStore.max_event_bytes`，16 MiB，不可配 | 16 MiB | 0.72 MB | 约 18 KB/次调用 | 约 900 次调用才到 | 不改 | 保持 |
| 会话制品总量 | `RunSession.create` 写死 64 MiB → 新增 `tracing.session_artifact_bytes` | 64 MiB | 16.2 MiB | 与步数平方成正比；合成基准 100 步 67 MiB、200 步 264 MiB | **会，最先卡住，约 80–100 次调用** | 改为可配，默认 64 MiB 不变 | 512 MiB（300 次调用用 1 GiB） |
| 单个制品（单份状态快照） | `ArtifactStore`，2 MiB → 新增 `tracing.session_artifact_item_bytes` | 2 MiB | 0.35 MB | 线性；200 步 1.31 MiB | 约 300 次调用 | 改为可配，默认 2 MiB 不变 | 8 MiB |
| 候选补丁导出 | `DELTA_FILE_LIMIT` 200 个文件 / `DELTA_BYTE_LIMIT` 4 MiB | 同左 | 改动文件数为个位数 | 关系不大 | 不会 | 不改 | 保持 |
| 步数 / 调用 / 预算上限 | `runtime.max_steps`、`runtime.budget.*`、CNY 账本 | 60 步 / 40 次调用 | — | — | 按设计 | 已可配 | `max_steps` 不小于 `max_model_calls` |
| 上下文准入 | `runtime.context.max_tokens`、`compact_token_threshold`、`admission_estimate` | 96000 / 遮蔽 | 抽查的一次运行峰值准入约 4.6 万 tokens | — | 按设计 | 已可配 | 按实验设计定 |
| 单次输出上限 | `llm.max_tokens` | 8192 | — | thinking 下同时限制 reasoning 和正文 | 开 thinking 后可能截断 | 已可配 | 32768 |

会话制品为什么最先卡住：每一步都会把完整的消息历史保存两次（`state.saved`）。超过 16 KiB 的事件改存为制品，而这些快照内容各不相同，内容寻址也省不下空间，所以总量随步数平方增长。达到上限时，下一次追加会抛出 `ArtifactQuotaError`（属于 `StoreError`），运行直接中断。另外，每次执行命令前要先预留 2 MiB，所以实际可用空间比上限还要少 2 MiB。

合成基准的结果：

| 步数 | 会话记录累计耗时 | 制品总量 | 最后一份快照 |
|---|---|---|---|
| 40 | 14.4 s | 11.2 MiB | 0.27 MiB |
| 100 | 129.6 s | 67.0 MiB | 0.66 MiB |
| 150（插值） | 约 370 s | 约 150 MiB | 0.98 MiB |
| 200 | 804.4 s | 263.7 MiB | 1.31 MiB |

上表的耗时只是会话记录本身，不含模型调用和工具执行，但会占用运行总时长。所以运行总时长的建议值留了余量。原因见第 7 节第 2 条。

续跑行为有一处顺带修正。以前续跑时，`RunSession.resume` 用的是 `ArtifactStore` 的默认上限 16 MiB，而新建运行用的是 64 MiB。现在续跑改为读取运行清单里记录的配额。新建运行的默认值没有变。

证据在 `docs/analysis/fix10/e6-limits.json`，探针脚本是 `scripts/fix10_limit_usage.py`。

## 7. 过程中发现的新问题（只记录，没有修）

1. **导出规则会误伤名为 `build` 的源码目录。** `is_export_skipped` 和容器内的导出脚本，会跳过路径中任何一层叫 `build` 的目录。像 pypa/build 这种包就在 `src/build/` 下的仓库，agent 的改动永远导不出来，也判不了分。
   - 试点用的 `RebenchGrader` 不过滤这类路径，所以试点高估了可用题数。
   - 现在准备脚本会在校准阶段把这类题拒掉。
   - 如果要修，可以只跳过仓库顶层的 `build/`，或者“原仓库里存在的文件一律不跳过”。两种做法都会改变现有题目的导出语义，需要先决定。
2. **会话记录的时间开销随步数立方增长。** 每次追加事件后，`RunSession.refresh()` 都会重读全部事件，`reduce_events` 还会深拷贝每一份历史快照；按性能剖析，深拷贝约占 75% 的时间。
   - CR-2 一次 40 步的运行，结尾时一次 refresh 约 0.4 s。
   - 合成基准里 200 步累计 804 s。
   - 长运行会被这部分开销吃掉不少总时长。
3. **每步保存两份完整历史，制品量随步数平方增长。** 默认 64 MiB 在约 80–100 次调用时用完，运行直接中断。E6 只把上限改成了可配置；存储方式本身（例如只存增量）没有动。
4. **thinking 模型在工具调用轮次经常不推理。** 用例 A 和 D 的 `reasoning_content` 都是空字符串。
5. **文档里的 400 规则没有复现。** 缺少 `reasoning_content` 时返回的是 200（用例 C、F）。harness 仍然始终回放 reasoning。
6. **摘要插入的 assistant 消息不带 reasoning。** 服务端接受了（用例 F），但这依赖于第 5 条的行为不变。
7. **账本预留用字符数除以 4 估算 `prompt_tokens`。** 长上下文时，预留和实际可能差得比较多。
8. **thinking 下 `max_tokens` 同时限制 reasoning 和正文。** b0 的 8192 在长推理时可能被截断，finish 为 `length`。
9. **187 次调用没有返回 usage。** CR-2 主实验里这些调用的预留合计 14.586 元，原样计入了账本，所以账本对这部分是高估的。
10. **LLM 的 HTTP 超时不能通过配置修改。** 固定 120 s。流式下问题不大，非流式长推理可能超时。
11. **rebench 清单的 `public_task` 是本机绝对路径。** 这和现有 sprint 清单的做法一致，但换机器后需要重新生成。
