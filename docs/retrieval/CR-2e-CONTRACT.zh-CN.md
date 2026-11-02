# CR-2e 合同：检索工具的可用性改进与演示

日期：2026-10-29。读者：Cursor 执行模型。背景：[CR-2 结果](CR-2-RESULTS.zh-CN.md)、[AUDIT-CR-2d](AUDIT-CR-2d.zh-CN.md)。

CR-2 正式实验已经收尾，冻结版本保留不动。本轮是**工程改进**，不是新的效果实验：让检索工具真正被 agent 合理使用，并做一个能现场演示的入口。不追求修复率提升，也不扩大规模。

**总时限 3 天。** 到期没做完，就把已完成的部分写进检查点，然后停下。

## 0. 范围

- 提交信息以 `CR-2e:` 开头。
- 只新增文件，外加下面这一处最小改动：
  - `src/tools/retrieve.py`：`RetrieveCodeTool` 增加可选参数 `description`，默认是现在的描述，一个字都不改。
  - 描述 v2 由 runner 在 agent 建好之后、第一次模型调用之前设置。用 `run_task` 已有的 `on_agent` 钩子，替换 `agent.tools` 里 `retrieve_code` 的实例。**不改** `src/config/schema.py` 和 `src/harness/factory.py`：`ToolsConfig` 是 `extra="forbid"`，加配置项就要改 schema。
- 兼容性：`scripts/cr2_compat.py` 对 b0-v2 必须仍然 `identical`；`cr2-t.yaml` 的工具 schema sha256 必须仍是 `77adc36f…`（CR-2-FREEZE 第 2 节）。写成测试。
- 禁改：`prompts/swe_b0.md`、`configs/sprint/`、`configs/retrieval/cr2-t.yaml`、`src/runtime/`、grader、`src/retrieval/` 已有的文件、`scripts/run_cr2.py`、`scripts/analyze_cr2.py`、`docs/retrieval/` 下已有的 CR-1/CR-2 文件。
- 这一轮只改提示和工具描述。**不同时**换模型、改检索器、加 reranker、加记忆，也不让工具返回代码片段。

## 1. 新配置 T2

### 1.1 system prompt：`prompts/swe_cr2e.md`

复制 `prompts/swe_b0.md`，只把第 10 行 `Use search_code to find symbols. ` 这一句换成下面的文字，其余逐字不变：

```text
There are two search tools:
- retrieve_code ranks files of the original repository by relevance to a description. Use it when the issue describes a symptom in words and you do not yet know which module handles it. Its results are candidates: read the listed code to confirm before editing.
- search_code greps the current workspace. Use it when you already have an exact symbol, error message or pattern.
```

原来同一行后面的 `Use read_file with offset and limit ...` 两句保留，放在上面这段之后，另起一行。

### 1.2 工具描述 v2

```text
Semantic search over the original repository at the base revision (before any edits made in this session). Describe the bug or behavior in words; returns the files most likely involved, each with the symbols and line ranges to read first. Use search_code instead for exact names or error strings. Does not return code: read the listed ranges with read_file.
```

参数 schema 和输出格式都不变。

### 1.3 配置：`configs/retrieval/cr2e-t2.yaml`

复制 `cr2-t.yaml`，只改 `runtime.system_prompt_path: prompts/swe_cr2e.md` 这一处。写测试检查两者只差这一行。工具描述 v2 由 runner 设置，见第 0 节。

## 2. 开发检查（CR-2e-a）

**题目**：从 CR-2 的 56 道题里选 6 道，规则如下，写进清单：

- 4 道：CR-2 N 组按题平均的首次触碰步数最大的 4 道，并列时按 instance_id 排序。这些是定位最难的题，检索最有可能起作用。
- 2 道：其余 52 道题按 instance_id 排序后，用 `random.Random(20261029).sample` 抽取。

这 6 道题只用于开发检查，**不能用于以后任何对照**。

**运行**：6 题 × T2 × 2 次 = 12 次，真实模型。新 runner `scripts/run_cr2e.py`：

- 能 import `run_cr2.py` 的函数就 import，不修改它。
- 输出写到 `runs/cr2e/`。
- 注入核对：首条消息 = `build_prompt(task)`；system prompt 的 sha256 等于 `swe_cr2e.md`；工具列表 = b0-v2 加 `retrieve_code`；`retrieve_code` 的描述等于 v2。
- 账本上限 20 元，授权参数 `--authorized dev,20`。
- **完成后拒绝重跑**：runs 文件已经完整时，runner 拒绝正式模式。CR-2 里 runner 正常退出后出现过两次来源不明的再次启动，这条规则用来防止它覆盖结果。保留 CR-2 的单实例锁。
- infra 规则沿用 CR-2-FREEZE 第 6 节和 r3。

**每次运行要报告**：

- 是否调用了 `retrieve_code`、调用次数、首次调用的步数；
- 每次调用的 query 原文和 k；
- 返回结果里是否有 gold 文件（标签只在全部运行结束后读取）；
- 调用之后 2 步以内，有没有用 `read_file` 读返回的某个文件；
- 有没有在没有新信息的情况下重复检索；
- 是否仍然使用 `search_code`；
- 首次触碰 gold 文件的步数、是否解出（只描述）。

**门槛**（现在写定）：

- 12 次里至少 6 次调用了 `retrieve_code`；并且
- 在调用了的运行里，至少一半在调用后 2 步内读了返回的文件。

两条都满足才算"会用且用得对"。

- 不满足：允许改一次提示或描述。改动要写进检查点并说明理由，在同样 6 道题上再跑 12 次，账本上限再加 20 元。
- 第二次仍不满足：不再迭代，写检查点收尾。结论照实写："在这种工具接口下，模型不采用语义检索。"

**不允许**用"强制第一步调用检索"来过门槛。可以单独写一个接线测试，用 mock 模型完成，不计入门槛。

## 3. 演示脚本：`scripts/cr_demo.py`

离线运行，不调用模型，不花钱。

```text
cr_demo.py <instance_id> [--run <run_dir>] [--show-gold] [--out <file.md>]
```

输出一份 Markdown：

1. **issue**：题号、仓库、base_commit、issue 前 500 字。
2. **检索**：用 CR-1 冻结配置，列出 BM25、embedding、hybrid 各自的前 5 个文件；hybrid 的每个文件附前 2 个块的 symbol 和行号。写明来源："base_commit 快照，`cr1-freeze`"。
3. 加 `--show-gold` 时：标出 gold 文件，以及它在三种排序里的名次。
4. 加 `--run` 时，给出这次运行的轨迹表：步数、工具、参数摘要（路径、query、命令的前 80 字）、成功与否。另外列出 `retrieve_code` 的返回结果、最终改动的文件、grader 结论。

在 `docs/retrieval/demo/` 下提交 2 份生成好的示例：

- 一份是 T2 开发检查里调用了检索、并且读了返回文件的运行（如果有）；
- 一份是 CR-2 P 组的运行。

两份都加 `--show-gold`，输出里要能看出用的是哪个 run 目录。

## 4. README

在 `README.md` 和 `README.zh-CN.md` 里加一节"代码检索（CR）"，不超过 40 行：

- 模块组成：快照读取、AST 切块、blob 缓存、BM25、embedding、RRF；
- CR-1 的结果：400 道测试题，文件级 Hit@5 从 67.5% 到 85.8%，MRR 从 0.520 到 0.684。注明是文件定位指标，不是修复率；文件级上 hybrid 对 embedding 没有检出差异，函数级 Hit@5 高 7.5 个百分点；
- CR-2 的结论，各一句：预取没有让定位更早；给出正确文件后修复率没有检出变化；工具在原提示下 0/280 被调用；
- CR-2e 的结果，按实际情况写；
- 复现命令和演示命令；
- 链接到结果文件。

不写"显著提升"之类的词。

## 5. 检查点

写 `docs/retrieval/CHECKPOINT-CR-2e-a.md`，然后停下。内容：

- 改动和测试，包括兼容性；
- 6 道题的选题结果；
- 12 次（或 24 次）运行的逐次表，以及门槛判定；
- 演示示例的路径；
- README 的改动；
- 费用（账本口径和缓存口径），全量 pytest 结果；
- 遇到的问题。

是否再做一次小规模同期对照（其余 50 道题，N 对 T2），由审计根据门槛结果决定。本轮不做。

## 6. 启动文字

启动文字是发给执行代理的操作指令，公开版本略去；本阶段的范围、限制和停止条件以上文各节为准。
