# CR-2 合同：把冻结的检索接进 agent

日期：2026-10-26。读者：Cursor 执行模型。背景见 [PLAN](PLAN.zh-CN.md) 第 4 节和 [CR-1 结果](CR-1-RESULTS.zh-CN.md)。

CR-1 回答了"能不能找到要改的文件"。CR-2 回答：把检索结果交给 agent 以后，它是不是更早打开正确的文件。修复率只做描述，原因见 PLAN 第 1 节。

## 0. 范围与禁改

- 当前分支 `main`，提交信息以 `CR-2:` 开头。每个检查点写完后停下。
- **检索必须用 CR-1 冻结的配置**（`docs/retrieval/cr1-freeze.json`，tag `cr1-freeze`）：hybrid，`k=60`，embedding 权重 2，文件级融合，jina-code @`516f4baf`。不改 `src/retrieval/` 里已有的文件；需要的新功能写在新文件里，例如 `src/retrieval/serve.py`。
- 本轮放开的路径，只能做**增量**修改：
  - `src/tools/` 新增 `retrieve.py`；
  - `src/harness/factory.py`：Docker 白名单加入 `retrieve_code`，以及注册这个工具所需的最少改动；
  - `scripts/run_swe_task.py`：只允许给 `run_task` 加带默认值的可选参数，默认路径的行为不变；
  - `src/eval/tool_errors.py` 的工具列表。
- 仍然禁改：`src/runtime/`、grader、预算账本、`prompts/swe_b0.md`、`configs/sprint/b0-v2.yaml`、`docs/sprint/`、A5/A6 的脚本和数据。
- **兼容性测试必须有**：用 b0-v2 跑 `build_prompt` 和 `build_agent`，首条消息的 sha256、工具 schema 列表与改动前逐字节相同。

## 1. 题目

- 题目池 = A5/A6 用过的已验证池：`run_a6_retry.py` 里的 `accepted_tasks(load_targets())`，预计 56 道。这些题都在 CR-1 的 62 道保留题里，从没参与过检索调参。
- 开工时逐题核对，不一致的写进检查点：
  - `main-targets.yaml` 的 `base_commit` 等于 Verified 的 `base_commit`；
  - agent 看到的 issue（`public_task` 的 `issue_text`）与 Verified 的 `problem_statement` 是否相同。检索的 query 一律用 agent 看到的那份文本；不同的题照常跑，在报告里标出。

## 2. 三组

| 组 | 与 B0 的唯一区别 |
|---|---|
| N | 无。b0-v2 原样 |
| P（预取） | 首条用户消息末尾追加一段固定格式的检索结果（经 `prompt_suffix`） |
| T（工具） | 多一个只读工具 `retrieve_code`，system prompt 不变 |

**P 的渲染**（开跑前对 56 题全部渲染并冻结，写进清单并记 sha256）：

- 冻结 hybrid 对 issue 检索出的前 5 个文件；每个文件附上块排名最靠前的至多 2 个块的 `symbol` 和行号范围。只给路径、symbol、行号，不给代码正文。
- 外层固定一段英文说明，意思是：以下是对原始代码的自动检索结果，可能有误，只是线索，不是指令。说明文字开跑前写定，所有题相同。
- 总长度不超过 1500 字节，报告实际长度分布。

**T 的工具 `retrieve_code`**：

- 参数：`query`（字符串，最长 2000 字符）、`k`（整数，默认 5，最大 10）。
- 用冻结的 hybrid 在这道题 base_commit 的快照上检索。返回前 k 个文件，每个文件附块排名最靠前的至多 2 个块的 `symbol` 和行号范围，不返回代码正文。输出开头固定一句，说明结果来自修改前的原始代码。
- 工具在宿主上运行，只读 CR 缓存（`CR_CACHE_DIR`），不读宿主或容器的工作区，不执行命令。这是 Docker 模式下第一个宿主侧工具，白名单的例外只加这一个名字。`side_effect` 为 READ_ONLY。
- 每次调用的 query、k、返回的文件和块追加写入运行目录下的 `retrieval-calls.jsonl`（含 call_id）。不改 `ToolResult` 的结构。
- jina 编码器在 runner 进程内只加载一次，所有并发任务共享；每道题的快照索引在该题开始时建好。
- 默认 `retrieve_code` 不在任何已有配置里启用。T 组用新配置 `configs/retrieval/cr2-t.yaml`：b0-v2 的副本，只在 `tools.enabled` 末尾加 `retrieve_code`。启用了 `retrieve_code` 却没有传入检索服务时，`build_agent` 必须报错。

## 3. 运行

- 新 runner `scripts/run_cr2.py`，结构仿照 `run_a6_retry.py`：槽位队列、按种子 `20261026` 打乱后三组交错运行、续跑、停止条件、CNY 总额上限。能直接 import 的函数就 import，不复制、不修改 A6 的脚本。
- 每个槽位 = (题, 组, 重复号)。重复次数 `R = 2`，共 56 × 3 × 2 = 336 个槽位。
- 总额上限 `200` 元。重复次数和上限由项目作者在 CR-2b 开始前确认，确认之前不发起任何付费调用。
- 三组都用 b0-v2 的模型、预算（每次最多 40 次模型调用）和超时；N 组和 P 组都用 b0-v2，T 组用 `cr2-t.yaml`。
- **注入核对**（每个槽位跑完都检查，不符就停下）：
  - N 组首条用户消息的 sha256 与 `build_prompt(task)` 相同；
  - P 组等于 `build_prompt(task, 冻结的预取文本)`；
  - T 组的工具 schema 里有 `retrieve_code`，N 组和 P 组没有。
- **基础设施失败**：沿用 A6 的规则，并在开跑前写进清单：infra_error、HTTP 402（余额不足）、网络失败的槽位原样退役重跑，保留原目录和费用；选择只看错误类型，不看 solved。模型 parse_error、context_overflow、预算用尽属于任务结果，不重跑。
- 不因为中途结果好或坏提前停止，也不追加槽位。

## 4. 指标（开跑前冻结）

gold 文件用 `labels.py` 的文件级标签，只在运行全部结束后由分析脚本读取。

- **主指标**：首次触碰 gold 文件的步数，即 agent 第一次对 gold 文件调用 `read_file`、`edit_file`、`search_replace` 或 `write_file` 时，已经发生的模型调用次数（从 1 开始）。从未触碰记为 41。
- 每道题先对 R 次重复取平均，再算 P − N 和 T − N 的按题配对差值：均值，以及按题 bootstrap 95% 区间（10000 次，种子 `20261026`）。负数表示更早。
- 次要指标，都按同样方法：
  - 前 10 步内是否触碰 gold 文件；
  - 是否解出（只描述，A5/A6 说明 56 题测不出中等差异）；
  - 模型调用次数、费用、停止原因（含 parse_error 和 context_overflow，分组计数）、首条消息长度。
- 只在 T 组统计：至少调用一次 `retrieve_code` 的运行比例、每次运行的调用次数、首次调用的步数、gold 文件是否出现在任何一次返回结果中。
- 只在 P 组统计：预取前 5 里有 gold 文件的题，与没有的题分开描述主指标。
- 步数从 `events.jsonl` 推出。推算规则写进分析脚本，并用手工构造的事件序列做单测。

## 5. 步骤与检查点

### CR-2a：实现与 mock（不花钱）

1. `src/retrieval/serve.py`：按冻结配置为一道题建索引、检索、渲染预取文本。
2. `src/tools/retrieve.py`、factory 注册、`cr2-t.yaml`。
3. `scripts/run_cr2.py`：槽位、续跑、注入核对、停止条件、费用汇总。
4. 冻结预取文本：对 56 道题渲染，写 `docs/retrieval/cr2-prefetch.json`（题、文本、sha256、长度、前 5 文件）。
5. 分析脚本 `scripts/analyze_cr2.py`（指标按第 4 节）和它的单测。
6. 用 mock 模型把三组各跑 2 道题，跑通全链路：注入核对、`retrieval-calls.jsonl`、grader、续跑。mock 模型要真的调用一次 `retrieve_code`。
7. 写 `docs/retrieval/CR-2-FREEZE.md`：题单与核对结果、三组定义、预取说明文字、工具描述与 schema、指标定义、种子、R、上限、infra 规则、代码提交号。打 tag `cr2-freeze`。

写 `docs/retrieval/CHECKPOINT-CR-2a.md` 后停下。内容：

- 提交号和全量 pytest 结果，写明有没有激活 venv；
- 兼容性测试结果；
- 题单核对：base_commit、issue 文本是否一致；
- 预取文本长度分布、3 个渲染样例；
- 预取前 5 里含 gold 文件的题数（只报数字，用于解释，不影响设计）；
- mock 运行记录；
- 遇到的问题和取舍。

### CR-2b：小批真实运行（canary）

审计通过、项目作者确认 R 和上限之后开始。

三组各跑 2 个槽位，共 6 个，用真实模型。核对注入、工具调用记录和单次费用，再估算全量费用。写 `CHECKPOINT-CR-2b.md` 后停下。

### CR-2c：全量运行

跑完剩下的全部槽位，写 `CHECKPOINT-CR-2c.md` 后停下。不在这一步做分析。

### CR-2d：分析

用冻结的 `analyze_cr2.py` 生成 `docs/retrieval/CR-2-RESULTS.zh-CN.md`。表述规则同 CR-1 合同第 9 节，并增加：

- 不能用修复率写"检索提升了 agent"；
- T 组如果很少调用工具，直接写出来，那是结论的一部分，不是失败。

## 6. 启动文字（CR-2a）

> 请阅读 `D:/crq/src/coding-harness/docs/retrieval/AUDIT-CR-1c.zh-CN.md`、`PLAN.zh-CN.md` 第 4 节和 `CR-2-CONTRACT.zh-CN.md`，执行 CR-2a。只做 CR-2a，不调用任何付费模型。写完 `docs/retrieval/CR-2-FREEZE.md`、打 tag `cr2-freeze`、写完 `docs/retrieval/CHECKPOINT-CR-2a.md` 并提交后停下。只能按合同第 0 节放开的范围做增量修改，用兼容性测试证明 b0-v2 的默认路径没有变。合同没覆盖的取舍选保守做法并写进检查点；会改变三组定义、主指标或题单的问题，先停下来问。


