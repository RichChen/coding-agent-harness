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

## 7. CR-2a 审计后的扩大（2026-10-26，适用于 CR-2b 以后）

依据 [AUDIT-CR-2a](AUDIT-CR-2a.zh-CN.md)。项目作者放开预算，并确认了两项改动：加 O 组，R 改为 5。这些都发生在任何真实运行之前，所以重新冻结，新 tag 为 `cr2-freeze-r1`。旧 tag `cr2-freeze` 保留不动。第 7 节与前文冲突时以第 7 节为准。

### 7.1 第四组 O（oracle，诊断用对照）

O 组不是一种方法。它回答的是：定位做到完美时，agent 最多能好多少。

- 与 P 组的唯一区别是列表内容。说明文字、格式、字节上限（1500）都和 P 组逐字相同，经 `prompt_suffix` 追加，配置用 b0-v2。
- 列表：先放 gold 文件，按 CR-1 冻结 hybrid 的文件排名排序，最多 5 个；不足 5 个时，用 hybrid 排名中非 gold 的文件按顺序补齐到 5 个。gold 文件下列出它的 gold 块（`labels.py` 的函数级标签），按块起始行排序，最多 2 个；补位文件仍用 hybrid 块排名的前 2 个。
- O 组的文本必须让模型分不出它和 P 组的差别，所以说明文字不能提"正确答案"或"oracle"。
- **答案隔离的例外**：O 组的预取文本必须读标签。读标签只允许发生在一个新脚本 `scripts/cr2_oracle_prefetch.py` 里，它生成冻结文件 `docs/retrieval/cr2-oracle-prefetch.json`（结构同 `cr2-prefetch.json`，每题加一个字段，记录 gold 文件数与列出的 gold 文件数）。在 `tests/test_retrieval_isolation.py` 的允许名单里加上这个脚本，并写注释说明原因。runner 只读这个冻结文件，不 import `labels.py`。
- 报告时 O 组只作为上限参照，不与 P/T 一起被描述成"检索方法"。

### 7.2 规模

| 项 | 值 |
|---|---|
| 组 | N、P、T、O |
| R | 5 |
| 槽位 | 56 × 4 × 5 = 1120 |
| 种子 | 20261026，按 (题, 组, 重复号) 排序后打乱，四组交错 |
| 并发 | 3（同 A5/A6） |
| 总额上限 | 800 元（估算 1120 × 约 0.55 = 约 620 元，留出 infra 重跑的余量） |
| 授权参数 | `--authorized 5,800` |

### 7.3 指标的补充

- 主指标不变（首次触碰 gold 文件的步数）。配对比较增加 O − N。O 组在这个指标上几乎必然更早，只用来确认注入生效，不作为结论。
- O 组的核心用途是**修复率**：O − N 与 P − N 的修复率配对差值，都用按题 bootstrap 给区间。这仍然是次要指标，只描述，不当作检验结论。
- 增加一项分解（只描述）：每组中"前 10 步内触碰了 gold 文件"的运行，与"没有触碰"的运行，分别算修复率。
- `analyze_cr2.py` 的规则要在 CR-2b 之前改好、加单测，并随 `cr2-freeze-r1` 一起冻结。

### 7.4 CR-2a-r1：重新冻结（不花钱），然后直接做 CR-2b

1. 改 `run_cr2.py`：`GROUPS` 加 `o`，`REPEATS = (1..5)`，`TOTAL_CAP = 800`；O 组的注入核对 = `build_prompt(task, 冻结的 oracle 文本)`，工具列表 = b0-v2，不能有 `retrieval-calls.jsonl`；canary 改为每组最先出现的 2 个槽位，共 8 个。
2. 写 `scripts/cr2_oracle_prefetch.py`，生成并冻结 `cr2-oracle-prefetch.json`。报告长度分布、3 个样例，以及 gold 文件超过 5 个的题数。
3. 改 `analyze_cr2.py`（7.3），补单测。
4. 用 mock 把四组各跑 2 个槽位，确认注入核对通过。
5. 更新 `CR-2-FREEZE.md`：新增第 10 节"r1 修订"，写清改了什么、为什么改、新的常量和 sha256、代码提交号。前面各节不改。打 tag `cr2-freeze-r1`。
6. 跑全量 pytest。
7. 以上全部通过后，运行 canary：`run_cr2.py --canary --authorized 5,800`（`HF_HUB_OFFLINE=1`）。共 8 个槽位，真实模型。

第 4 步或第 6 步有任何失败，就不运行 canary，写检查点停下。

写 `docs/retrieval/CHECKPOINT-CR-2b.md` 后停下。内容：r1 改动与测试；oracle 预取的统计和样例；mock 结果；canary 的 8 个槽位逐个列出（组、模型调用数、费用、停止原因、是否解出、T 组的调用记录、注入核对）；返回的模型名；平均单次费用和全量费用估算；遇到的问题和取舍。

**两种费用都要报。** harness 账本对所有输入 token 都按未命中缓存的价格算，是保守上限。审计时用 A6 的 `events.jsonl` 按命中/未命中分开重算：A6 账本 178.7 元，按缓存价格估算约 63.8 元，缓存命中率 71%。canary 要逐槽位列出 `model.settled` 的 usage 里 `prompt_cache_hit_tokens`、`prompt_cache_miss_tokens`、`completion_tokens` 的合计，再按账本单价（命中 0.04、未命中 2、输出 8 元/百万 token）算"缓存口径费用"，与账本费用并列。另外记录 canary 的开始和结束时间（精确到分钟），供项目作者对照 DeepSeek 控制台的余额和用量。只汇报，不改账本，不改上限逻辑。

### 7.5 CR-2c 运行前

- 800 元的上限按账本口径计，账本比实际扣费高（A6 约 2.8 倍）。实际需要的余额由项目作者按 canary 的真实扣费估算后充值，并留出余量。A6 曾因余额耗尽出现 22 次 HTTP 402。
- 按 A6 的做法，运行期间关闭系统待机，跑完恢复原值。
- 估算墙钟：A6 的 314 次用了约 5.8 小时，按比例 1112 次约 20 小时。

### 7.6 CR-2a-r1 + CR-2b 启动文字

> CR-2a 审计通过，审计记录在 `D:/crq/src/coding-harness/docs/retrieval/AUDIT-CR-2a.zh-CN.md`。项目作者放开了预算，CR-2 增加第四组 O，并把 R 改为 5。请读审计和 `CR-2-CONTRACT.zh-CN.md` 第 7 节，按 7.4 的顺序执行：先做 CR-2a-r1 重新冻结（打 tag `cr2-freeze-r1`，旧 tag 不动），mock 和全量 pytest 都通过后，再用 `--canary --authorized 5,800` 跑 8 个 canary 槽位。项目作者已经授权 R=5、上限 800 元。canary 之外不要启动任何真实槽位。写完 `docs/retrieval/CHECKPOINT-CR-2b.md` 并提交后停下。O 组的标签只能在 `scripts/cr2_oracle_prefetch.py` 里读；O 组的说明文字必须与 P 组逐字相同。任何注入核对失败、harness 工具错误或测试失败，都要停下写检查点，不要绕过。canary 要同时报账本费用和缓存口径费用，并记录开始和结束时间（见 7.4 末段）。其他规则与前几轮相同。

