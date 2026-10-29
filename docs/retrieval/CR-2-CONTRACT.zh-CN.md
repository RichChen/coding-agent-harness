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


## 8. CR-2b 审计后（2026-10-27，适用于 CR-2c 以后）

依据 [AUDIT-CR-2b](AUDIT-CR-2b.zh-CN.md)。第 8 节与前文冲突时以第 8 节为准。

### 8.1 r2 修订：只修模型名记录

- 采用 CHECKPOINT-CR-2b 7.1 的第 1 种做法：响应头 `Content-Encoding` 为 gzip 或 deflate 时，另用 `zlib.decompressobj` 解压一份副本来扫描 `"model"`，原始字节照常透传。不改请求头，不改 `src/llm`。
- 单测：用 `MockTransport` 构造 gzip 和 deflate 压缩的流式与非流式响应，都要记到模型名；透传的字节与不加钩子时相同；HTTP 错误照常抛出。
- 只允许改 `scripts/run_cr2.py` 和它的测试。在 `CR-2-FREEZE.md` 加第 11 节"r2 修订"，打 tag `cr2-freeze-r2`。旧 tag 不动，canary 的 8 个槽位保留，不重跑；它们的模型名记为缺失，在结果里注明。
- 跑全量 pytest。

### 8.2 运行中的模型名检查

- runner 在**前 3 个**完成的真实槽位之后检查：如果 3 个都没有 `model-names.json`，或者文件为空，就停止启动新槽位，写检查点停下。这说明记录还是坏的。
- 运行中返回的模型名出现**一个以上**时，不停止，在报告里按槽位列出。四组是交错运行的，版本变化不会偏向某一组；分析时按模型名分层描述。
- 这条检查写进 r2，并加单测。

### 8.3 全量运行

- 项目作者确认 DeepSeek 余额（充值加赠送）至少 300 元以后才开始。估算见 AUDIT-CR-2b 第 2 节。
- 关闭系统待机，跑完恢复原值。保持接电源。
- 约 23 小时。runner 以可续跑的方式在后台运行，输出写到 `runs/cr2-logs/main.out`。中断后原样重跑同一条命令即可，已完成的槽位会跳过。
- 命令：`HF_HUB_OFFLINE=1 .venv\Scripts\python.exe scripts/run_cr2.py --authorized 5,800`，在 tag `cr2-freeze-r2` 上运行。
- 运行期间不改代码。因为余额不足、网络等原因中断时，按冻结的 infra 规则处理。

写 `CHECKPOINT-CR-2c.md` 后停下，不做分析。内容：起止时间（到分钟）；`main-report.json` 摘要；按组的完成数、停止原因、infra 退役和保留；返回的模型名及其分布；账本费用和缓存口径费用（用 `cr2_cost.py`，按组）；按北京时间高峰/非高峰拆开的调用数和缓存口径费用（按 `model.settled` 的时间戳和价格页的时段规则，只做报告）；遇到的问题。

### 8.4 分析阶段要写进结果的版本说明

- 运行期间所用的服务端模型（按 `model-names.json`），以及 DeepSeek 价格页 2026-10-27 关于别名的说明。
- CR-2 的 N 组是新的基线，不和 A5 的 B0、A6 的 R0/R1 做数值比较。

### 8.5 r2 + CR-2c 启动文字

> CR-2b 审计通过，审计记录在 `D:/crq/src/coding-harness/docs/retrieval/AUDIT-CR-2b.zh-CN.md`。请读审计和 `CR-2-CONTRACT.zh-CN.md` 第 8 节，先做 8.1 和 8.2 的 r2 修订：只改 `scripts/run_cr2.py` 和它的测试，补 `CR-2-FREEZE.md` 第 11 节，打 tag `cr2-freeze-r2`，跑全量 pytest。全部通过后，在 `cr2-freeze-r2` 上按 8.3 启动全量运行（`--authorized 5,800`，`HF_HUB_OFFLINE=1`，后台运行，输出写到 `runs/cr2-logs/main.out`）。项目作者已经确认余额并关闭了系统待机。运行期间不改代码；前 3 个真实槽位如果都没有记到模型名，runner 会停下，这时写检查点等审计，不要自行修改后继续。全部槽位跑完后，写 `docs/retrieval/CHECKPOINT-CR-2c.md` 并提交后停下，不做分析。其他规则与前几轮相同。

## 9. 第一次停止后（2026-10-27，适用于 CR-2c 续跑）

（2026-10-29 恢复：本节最初追加时被按 GBK 写入，成了乱码。下面是原文，内容没有改动。Cursor 当时是按 GBK 再按 UTF-8 还原后阅读的。）

依据 [AUDIT-CR-2c-halt1](AUDIT-CR-2c-halt1.zh-CN.md)。第 9 节与前文冲突时以第 9 节为准。

### 9.1 先核实（不花钱，不改冻结代码）

写一个新脚本 `scripts/cr2_replay_snapshot.py`：用同一个干净镜像起容器，按 `scikit-learn__scikit-learn-12585/n/r4` 的 `events.jsonl` 依次重放 agent 的写操作和 `run_command`（包括那条超时的 pytest，超时时间相同），不调用模型。然后在容器里列出 `/workspace` 相对 `/opt/repo` 的改动：文件数、总字节数、按目录汇总的前 20 项，以及是哪一步产生的。

判断（写进检查点）：

- 改动来自 agent 的写操作或它的命令产生的文件（测试缓存、临时数据、下载的数据集等）：**agent 造成**，按 9.2 处理。
- 改动来自 harness 自己写进工作区的东西（工具超时处理、输出落盘等）：**harness 造成**，按 infra 处理。这时不做 9.2，写检查点停下。
- 重放复现不出来：写检查点停下。

### 9.2 r3 修订（仅在 9.1 判定为 agent 造成时）

只允许改 `scripts/run_cr2.py` 和它的测试，`run_swe_task.py`、`src/` 都不改，A5/A6 的判定也不受影响。

- **重新分类。** 一个槽位的最终运行如果 status 为 `infra_error`，并且 `snapshot_error` 是 `ValueError: delta_too_large`，或者是 `DockerUnavailable: Docker control output quota exceeded` 且发生在快照这一步（`op` 为 `snapshot`），就记为**任务结果**：不退役、不重跑，不计入 infra 停止条件。在 `cr2-runs.json` 里这样记：
  - `status: "delta_too_large"`，`solved: false`，`no_candidate: true`；
  - 主指标和 T 组指标照常从 `events.jsonl` 计算。
  其他 `snapshot_error`（容器提前消失、OOM 等）仍按 infra 处理，规则不变。
- **已有的那个槽位。** `scikit-learn__scikit-learn-12585/n/r4` 保留的那次运行按新规则重新分类。两次退役运行保留原目录，费用照计，不参与分析。
- **单实例锁。** runner 启动时在 `runs/cr2-main/` 下独占创建锁文件，写入 PID 和启动时间，退出时删除。锁已存在且 PID 仍在运行就拒绝启动；PID 已不存在就报告是陈旧锁并拒绝启动，等人工删除。
- **记录 `system_fingerprint`。** 复用模型名的钩子，同时扫描 `system_fingerprint`，写进 `model-names.json`，报告里按值列出槽位。只做报告，不作为停止条件。
- **分析脚本。** `analyze_cr2.py` 接受 `delta_too_large` 槽位：主指标照常计入，修复率记为未解出，并按组报告这类槽位的数量。
- 单测覆盖：两种 `snapshot_error` 都改判为任务结果；其他 `snapshot_error` 仍是 infra；停止条件不计入改判的槽位；锁文件的三种情况。
- 在 `CR-2-FREEZE.md` 加第 12 节"r3 修订"，打 tag `cr2-freeze-r3`，跑全量 pytest。

### 9.3 续跑

- 开跑前：确认系统待机已经关闭（`powercfg` 显示接电源为 0），Docker 在线，没有其他 `run_cr2.py` 进程在运行。
- 在 `cr2-freeze-r3` 上用同一条命令续跑。已有的 26 个槽位跳过；12585/n/r4 按新规则视为已完成。
- 不再因为这一类失败停下。其他停止条件不变。
- 全部跑完后，写 `CHECKPOINT-CR-2c.md` 的续篇：在原文件后追加"续跑"一节，前文不改。内容按合同 8.3，另加：按组统计 `delta_too_large` 槽位的数量，以及 `system_fingerprint` 的分布。

### 9.4 启动文字

> CR-2c 的第一次停止已审计，审计记录在 `D:/crq/src/coding-harness/docs/retrieval/AUDIT-CR-2c-halt1.zh-CN.md`。请读审计和 `CR-2-CONTRACT.zh-CN.md` 第 9 节。先做 9.1 的重放核实，不调用模型、不改冻结代码。只有判定为"agent 造成"时，才做 9.2 的 r3 修订：只改 `scripts/run_cr2.py`、`scripts/analyze_cr2.py` 和它们的测试，补 `CR-2-FREEZE.md` 第 12 节，打 tag `cr2-freeze-r3`，跑全量 pytest。如果判定为"harness 造成"，或者重放复现不出来，就写检查点停下。r3 全部通过后，按 9.3 续跑：开跑前确认系统待机已关闭、Docker 在线、没有其他 `run_cr2.py` 进程。全部槽位跑完后，在 `CHECKPOINT-CR-2c.md` 末尾追加"续跑"一节并提交，然后停下，不做分析。其他规则与前几轮相同。

## 10. CR-2c 审计后：分析（2026-10-29，适用于 CR-2d）

依据 [AUDIT-CR-2c](AUDIT-CR-2c.zh-CN.md)。本节在任何人看过 solved、触碰步数等结果之前写定。第 10 节与前文冲突时以第 10 节为准。

### 10.1 r4：只改分析的读入规则

只改 `scripts/analyze_cr2.py` 和它的测试。runner、`src/`、运行目录都不改，也不重跑任何槽位。

- **6 个 `infra_kept` 槽位作为任务结果计入主分析。** `main()` 现在遇到 `rerun_reason` 就退出。改为：只有当 `rerun_reason` 非空、并且这个槽位不在 runner 报告的 `infra_kept` 名单里时才退出。`infra_kept` 名单从 `cr2-runs.json` 读，没有这个字段时从报告文件读，命令行用 `--report` 传入。
  - 理由：这 6 次运行都跑满了 40 次模型调用，只是中途有一次模型调用在客户端重试后仍然网络失败，status 是正常的 fail/partial。冻结规则（FREEZE 第 6 节）写的是退役次数用完后"保留最后一次运行"。
- 主分析的其他定义一律不变：最终运行；第 4 节和 7.3 的指标；种子；bootstrap。

### 10.2 预先写定的敏感性分析（只描述，不检验）

每一项都按主分析的方法（先对每题的重复取平均，再按题配对、bootstrap）重算 P−N、T−N、O−N 的主指标和修复率。某一组在某题上没有剩下的运行时，这道题从这一组的比较里去掉，并报告剩下的题数。

| 编号 | 改动 | 回答的问题 |
|---|---|---|
| S1 | 去掉 6 个 `infra_kept` 运行 | 中途网络失败的运行会不会影响结论 |
| S2 | 去掉 `scikit-learn__scikit-learn-12585` 整道题 | 这道题有 11/20 个槽位没有候选补丁，而且各组分布不均（N 5、P 1、T 2、O 3），它会不会主导修复率的差值 |
| S3 | 只用 `system_fingerprint` 只有 `bc19ba97…` 的运行 | 运行中途服务端切换后，结论是否还成立 |
| S4 | 每个槽位改用**第一次不是 `infra_error` 的运行**（包括因网络失败退役的运行），而不是最终运行 | 退役重跑会不会偏向短运行。运行越长，碰到网络失败的机会越大，越容易被退役重跑，所以最终保留的运行会偏短 |

S4 要从运行目录读取退役运行的 summary 和 `events.jsonl`。退役运行的 `status` 不是 `infra_error` 时，按和最终运行相同的方法计算指标。S4 需要的读取逻辑写在 `analyze_cr2.py` 里（它已经在读标签的白名单中），并加单测。

### 10.3 其他要先做的

- 用 runner 已有的离线方式（只读 summary，不跑槽位），从运行目录重新生成一份 runs 文件，写到 `runs/cr2-logs/cr2-runs.regen.json`，核对与 `docs/retrieval/cr2-runs.json` 内容相同（sha256 或逐行）。不同就停下。
- 不要再运行 `run_cr2.py` 的正式模式。runner 两次正常退出后不久，都出现了一次来源不明的同命令启动，在检查点里如实记录即可。

### 10.4 CR-2d 输出

`docs/retrieval/CR-2-RESULTS.zh-CN.md` 和 `docs/retrieval/cr2-analysis.json`。表述规则同 CR-1 合同第 9 节和本合同 5 节 CR-2d，并且：

- 最前面放预先确定的主指标：P−N、T−N（首次触碰 gold 文件的步数）。O−N 放在后面，作为上限参照。
- 修复率：四组的数值，P−N、T−N、O−N 的配对差值和区间。修复率是次要指标，只描述。
- 7.3 的分解：每组"前 10 步内是否触碰 gold 文件"与修复率的关系。
- T 组：工具调用率、首次调用步数、gold 文件是否出现在返回结果里。
- S1–S4 的表。哪项结论随敏感性分析改变了，要明确写出。
- 版本说明（8.4）：返回的模型名、两个 `system_fingerprint` 和切换时间、四组在两段时间里的分布；N 组是新基线，不和 A5/A6 比。
- 运行质量：退役 159 次（按原因）、`delta_too_large` 12 个、`delta_lost` 2 个、6 个 `infra_kept`、全部 harness 工具错误为 0、墙钟时间，以及它为什么比估算的 23 小时长。
- 费用：账本 547.394 元，缓存口径 211.916 元，按时段价估算约 137.89 元；项目作者报告的实际扣费由审计补上。

全量 pytest 跑一次。写 `CHECKPOINT-CR-2d.md` 后停下。

### 10.5 启动文字

> CR-2c 审计通过，审计记录在 `D:/crq/src/coding-harness/docs/retrieval/AUDIT-CR-2c.zh-CN.md`。合同第 9 节之前是乱码，已经恢复成原文。请读审计和 `CR-2-CONTRACT.zh-CN.md` 第 10 节，执行 CR-2d：先做 10.3 的 runs 文件核对，再做 10.1 的 r4 修订（只改 `scripts/analyze_cr2.py` 和它的测试）和 10.2 的 S1–S4，然后运行分析，写 `docs/retrieval/CR-2-RESULTS.zh-CN.md`、`docs/retrieval/cr2-analysis.json`、`docs/retrieval/CHECKPOINT-CR-2d.md`，跑全量 pytest，提交后停下。不要运行 `run_cr2.py` 的正式模式，不调用任何模型。主分析和敏感性分析的定义在本节已经写定，看到结果后不能改；如果某项定义无法执行，写进检查点，并说明你是怎样处理的。结果严格按表述规则写，不要因为结果好或不好改变写法。
