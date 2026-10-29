# CHECKPOINT CR-2e-a

日期：2026-10-29。合同：[CR-2e-CONTRACT.zh-CN.md](CR-2e-CONTRACT.zh-CN.md)。

**结论：第一轮没过门槛，按合同改了一次 prompt；第二轮刚好到门槛。** 第一轮 T2（新 system prompt 加工具描述 v2）12 次运行里有 4 次调用了 `retrieve_code`，门槛要求至少 6 次。第二轮只改了 prompt 里 `retrieve_code` 那一行，12 次里有 6 次调用；这 6 次都在调用后 2 步内读了返回的文件，两条门槛都满足。需要一起看的三点：调用都在第 1 步，之后没有再调用；调用集中在同 3 道题；每题只跑 2 次，4/12 到 6/12 的变化不能排除是随机波动。演示脚本、2 份示例和 README 一节都已完成。两轮账本合计 14.249 元（每轮上限 20 元），全量 pytest 903 passed、22 skipped、0 failed。没有做同期对照。

## 1. 改动和测试

### 1.1 改动

| 文件 | 类型 | 内容 |
|---|---|---|
| `src/tools/retrieve.py` | 修改（第 0 节允许的唯一一处） | `RetrieveCodeTool.__init__` 加可选参数 `description`，默认不传时描述一字不变 |
| `prompts/swe_cr2e.md` | 新增 | 按 1.1 节：`swe_b0.md` 第 10 行的 `Use search_code to find symbols. ` 换成两种搜索工具的说明，其余逐字不变 |
| `configs/retrieval/cr2e-t2.yaml` | 新增 | `cr2-t.yaml` 只改 `runtime.system_prompt_path` 一行 |
| `prompts/swe_cr2e-r2.md`、`configs/retrieval/cr2e-t2-r2.yaml` | 新增 | 第二轮（第 3 节）；配置同样只差 `system_prompt_path` 一行 |
| `scripts/run_cr2e.py` | 新增 | 开发检查 runner，见 1.2 |
| `scripts/analyze_cr2e.py` | 新增 | 逐次报告和门槛判定，定义在第一次真实运行前提交（`1fe9b8e`） |
| `scripts/cr_demo.py` | 新增 | 演示脚本，见第 4 节 |
| `tests/test_cr2e.py` | 新增 | 30 个测试（含参数化展开），见 1.3 |
| `tests/test_cr2_harness.py` | 修改（**超出第 0 节**） | 见 1.4 |
| `README.md`、`README.zh-CN.md` | 修改 | 第 4 节要求的一节 |

没有改：`src/config/schema.py`、`src/harness/factory.py`、`src/runtime/`、grader、`src/retrieval/` 已有文件、`prompts/swe_b0.md`、`configs/sprint/`、`configs/retrieval/cr2-t.yaml`、`scripts/run_cr2.py`、`scripts/analyze_cr2.py`、`docs/retrieval/` 下已有的 CR-1/CR-2 文件。工作区里作者未提交的修改（`AUDIT-CR-2d.zh-CN.md`、`CR-2-RESULTS.zh-CN.md`、`PLAN.zh-CN.md`）我没有动，也没有提交。合同文件 `CR-2e-CONTRACT.zh-CN.md` 原样提交在 `1fe9b8e` 里。

### 1.2 runner

- 从 `run_cr2.py` / `run_a5_main.py` / `run_a6_retry.py` import 函数，不复制、不修改：单实例锁、infra 判定和重跑（CR-2-FREEZE 第 6 节和 r3）、模型名检查、`counted_status`、mock 模型等。
- 描述 v2 的注入：`run_task` 的 `on_agent` 钩子把 `agent.tools` 里的 `retrieve_code` 实例换成 `RetrieveCodeTool(retrieval, description=v2)`。注册表是 name→tool 的 dict，替换后工具顺序不变。钩子随后把工具名、schema sha256、描述原文、system prompt sha256 写进 `agent-tools.json`。
- 注入核对（每次运行结束后，失败即停）：`prompt_sha256` 和 `events.jsonl` 里的首条 user 消息等于 `build_prompt(task)`；首条 system 消息等于该轮 prompt 文件；工具列表等于 b0-v2 加 `retrieve_code`；描述等于 v2；schema sha256 等于预先离线算出的期望值；`retrieval-calls.jsonl` 里没有 traceback。
- 拒绝重跑：该轮 runs 文件完整（每个 slot 都有行，且没有待重跑的行）时，真实模式直接退出。真实模式要求 `--authorized dev,20`。账本上限按轮计算，每轮 20 元。

### 1.3 测试（`tests/test_cr2e.py`）

- 兼容性：b0-v2 的 `cr2_compat.agent_snapshot()` 与基线 fixture 相同；`cr2-t.yaml` 的工具 schema sha256 仍是 `77adc36ffa19019d9b922f1b116631f7d183bf5548826b8cfc83bf69595f5b63`，默认描述不变。单独运行 `scripts/cr2_compat.py` 也是 `"identical": true`（56 个 prompt）。
- `cr2e-t2.yaml` 和 `cr2-t.yaml` 只差 `system_prompt_path` 一行；`swe_cr2e.md` 等于 `swe_b0.md` 做合同规定的那一处替换；第二轮的 prompt 只差 `retrieve_code` 那一行，配置只差一行，描述两轮相同。
- `description` 参数：默认值是原描述，传入时覆盖实例，不影响类属性。替换后工具顺序和参数 schema 不变，只有描述变化。
- 选题规则（并列按 instance_id、种子抽样）、12 个 slot 的顺序、`refuse_finished`、`check_authorized`、注入核对的各种失败情况。
- 分析定义：2 步内读、同一步读不算、第 3 步读不算、重复检索的判定、门槛的边界。
- 答案隔离：`run_cr2e.py`、`analyze_cr2e.py`、`cr_demo.py` 都不直接 import labels，`run_cr2e.py` 不读 gold；`cr_demo.py` 也在 `test_retrieval_isolation.py` 对 `scripts/cr_*.py` 的扫描范围内。
- 演示脚本的参数摘要、轨迹解析、issue 代码块的围栏。

### 1.4 偏离第 0 节的一处：`tests/test_cr2_harness.py`

CR-2 的 `test_no_existing_config_enables_retrieve_code` 要求 `configs/` 下除 `cr2-t.yaml` 外没有任何配置启用 `retrieve_code`。合同 1.3 节要求新建的 `cr2e-t2.yaml` 必然违反这条，第二轮的 `cr2e-t2-r2.yaml` 也一样。我把这两个文件加进了这条测试的例外列表，测试其余部分不变。这是第 0 节"只新增文件"之外的唯一改动，不影响 b0-v2 或 cr2-t。`tests/test_cr2e.py` 里有一条同样的检查，例外列表就是这三个文件。

## 2. 选题结果

`docs/retrieval/cr2e-dev-tasks.json`，由 `run_cr2e.py --select` 从 `docs/retrieval/cr2-analysis.json`（sha256 `f592dcc5…`）计算得到：

| 来源 | 题 | CR-2 N 组平均首次触碰步数 |
|---|---|---:|
| 最难 4 道 | django__django-15973 | 41.0 |
| | sympy__sympy-20428 | 41.0 |
| | sympy__sympy-19040 | 23.0 |
| | sympy__sympy-13091 | 18.8 |
| 抽样 2 道 | sphinx-doc__sphinx-7440 | — |
| | scikit-learn__scikit-learn-14496 | — |

41 是 `analyze_cr2.py` 中"从未触碰 gold"的取值，所以前两道题在 CR-2 N 组的 5 次运行里都没有碰到 gold 文件；两者并列，按 instance_id 排序。抽样用的是 `random.Random(20261029).sample`，从其余 52 道按 instance_id 排序后抽取。这 6 道题只用于本次开发检查。

## 3. 运行和门槛

### 3.1 过程

- 接线检查：`run_cr2e.py --mock` 跑了 2 个 slot（mock 模型，不计入门槛，花费 0）。两次都在第 1 步调用 `retrieve_code`，第 2 步读了返回的第一个文件，描述记录为 v2，注入核对通过。其中 django-15973 第一次的 `stop_reason` 是 `cleanup_failed`（容器清理失败，属 infra），按规则退役后重跑成功。
- 提交 `1fe9b8e`（runner、分析脚本、选题清单）后开始真实运行。第一轮 14:39–15:02，12 次全部完成；注入核对 12/12 通过，没有 infra，返回的模型名都是 `deepseek-flash`。
- 第一轮未过门槛。分析后做了一次改动，提交 `fa478f9`，然后跑第二轮：15:05–15:25，12 次全部完成；注入核对 12/12 通过，没有 infra，模型名不变。
- runner 正常退出后，没有出现来源不明的再次启动：两轮的运行目录都正好 12 个 `r1`/`r2`，没有 `.infra` 目录，也没有残留的锁文件。

### 3.2 定义（`analyze_cr2e.py`，第一次真实运行前写定）

- 步数：在该事件之前开始的模型调用数，与 `analyze_cr2.py` 一致。首次触碰 gold 的定义也沿用 `analyze_cr2.py`。
- 2 步内读返回文件：第 s 步调用，在第 s+1 或 s+2 步有 `read_file`，路径（规范化后）是这次调用返回的文件之一。同一步并行发出的读不算，因为那时还看不到结果。
- 无新信息的重复检索：query（小写并合并空白后）和之前某次相同；或者在比上次检索更晚的一步，而两次之间没有调用过其他工具。同一步内 query 不同的并行调用不算重复。
- gold 只在该轮 runs 文件完整后读取，通过 `analyze_cr2.load_gold`。
- 第一轮结束后，`analyze_cr2e.py` 只加了费用汇总（第 6 节），上面的定义没有改。

### 3.3 第一轮（T2：`swe_cr2e.md` 加描述 v2）

| 题 | 次 | 调用 | 次数 | 首次步 | query 原文（k） | 结果含 gold | 2 步内读的返回文件 | 无新信息重复 | search_code | 首次触碰 gold | 解出 | 停止原因 | 账本元 |
|---|---:|---|---:|---:|---|---|---|---:|---:|---:|---|---|---:|
| django__django-15973 | r1 | 否 | 0 | — | — | — | — | 0 | 5 | 未触碰 | 否 | max_model_calls | 0.723 |
| scikit-learn__scikit-learn-14496 | r1 | 否 | 0 | — | — | — | — | 0 | 2 | 2 | 是 | completed | 0.061 |
| sphinx-doc__sphinx-7440 | r1 | 是 | 1 | 1 | "glossary duplicate term case-insensitive duplicate term description"（5） | 是 | `sphinx/domains/std.py` | 0 | 3 | 2 | 否 | max_model_calls | 0.646 |
| sympy__sympy-13091 | r1 | 否 | 0 | — | — | — | — | 0 | 3 | 1 | 否 | max_model_calls | 0.768 |
| sympy__sympy-19040 | r1 | 是 | 1 | 1 | "factor with extension=True drops a factor, multivariate factorization over extension"（5） | 是 | `sympy/polys/factortools.py` | 0 | 3 | 2 | 否 | max_model_calls | 0.899 |
| sympy__sympy-20428 | r1 | 否 | 0 | — | — | — | — | 0 | 6 | 未触碰 | 否 | max_model_calls | 0.659 |
| django__django-15973 | r2 | 是 | 1 | 1 | "migration state resolve many-to-many through string reference to model in another app"（5） | 是 | `django/db/migrations/autodetector.py`, `django/db/migrations/state.py` | 0 | 4 | 2 | 否 | max_model_calls | 0.821 |
| scikit-learn__scikit-learn-14496 | r2 | 否 | 0 | — | — | — | — | 0 | 4 | 2 | 是 | completed | 0.124 |
| sphinx-doc__sphinx-7440 | r2 | 否 | 0 | — | — | — | — | 0 | 6 | 4 | 否 | max_model_calls | 0.543 |
| sympy__sympy-13091 | r2 | 否 | 0 | — | — | — | — | 0 | 4 | 4 | 否 | max_model_calls | 0.591 |
| sympy__sympy-19040 | r2 | 是 | 1 | 1 | "factor with extension=True drops a factor, multivariate factor over extension"（5） | 是 | `sympy/polys/factortools.py` | 0 | 4 | 2 | 否 | max_model_calls | 0.572 |
| sympy__sympy-20428 | r2 | 否 | 0 | — | — | — | — | 0 | 24 | 未触碰 | 否 | max_model_calls | 0.663 |

门槛：调用的运行 4/12（要求 ≥6），**不满足**；调用了的 4 次里，4 次在 2 步内读了返回文件（要求 ≥ 一半），满足。判定：**未过**。

### 3.4 唯一一次改动及理由

在没调用的 8 次里，模型第 1 步都用 `search_code` 去 grep issue 里直接写出的符号或表达式，例如 `clear_denoms`、`__eq__`、`min_samples * n_samples`、`remote_field.through._meta.auto_created`。这正是 T2 prompt 的写法："已有确切符号、错误信息或模式时用 search_code"。另一处观察：12 次运行里，只要调用就都在第 1 步，之后再也没有调用；包括从未碰到 gold 的 3 次（django-15973 r1、sympy-20428 r1/r2）。

因此第二轮只改 `swe_cr2e.md` 里 `retrieve_code` 那一行，描述 v2、配置其余部分、模型、检索器都不变：

```text
旧：- retrieve_code ranks files of the original repository by relevance to a description. Use it when the issue describes a symptom in words and you do not yet know which module handles it. Its results are candidates: read the listed code to confirm before editing.
新：- retrieve_code ranks files of the original repository by relevance to a description. Use it to find where a behavior is implemented, also when the issue names a function or class: the cause is often in a caller, a helper or another module. When your searches have not found the cause, call it again with a new description of what you are looking for. Its results are candidates: read the listed code to confirm before editing.
```

新写法把适用场景扩大到"issue 点名了函数或类"的情况，并提示搜不到原因时再查一次。这仍然是建议，没有要求第一步调用，也没有用 `tool_choice` 或其他方式强制调用。

### 3.5 第二轮（`swe_cr2e-r2.md` 加描述 v2）

| 题 | 次 | 调用 | 次数 | 首次步 | query 原文（k） | 结果含 gold | 2 步内读的返回文件 | 无新信息重复 | search_code | 首次触碰 gold | 解出 | 停止原因 | 账本元 |
|---|---:|---|---:|---:|---|---|---|---:|---:|---:|---|---|---:|
| django__django-15973 | r1 | 是 | 1 | 1 | "migration state resolve many-to-many through string reference to model"（5） | 是 | `django/db/migrations/autodetector.py`, `django/db/migrations/state.py` | 0 | 4 | 2 | 否 | max_model_calls | 0.776 |
| scikit-learn__scikit-learn-14496 | r1 | 否 | 0 | — | — | — | — | 0 | 2 | 2 | 是 | completed | 0.053 |
| sphinx-doc__sphinx-7440 | r1 | 是 | 1 | 1 | "glossary duplicate term case-insensitive"（5） | 是 | `sphinx/domains/std.py` | 0 | 3 | 2 | 否 | max_model_calls | 0.545 |
| sympy__sympy-13091 | r1 | 否 | 0 | — | — | — | — | 0 | 2 | 4 | 否 | max_model_calls | 0.731 |
| sympy__sympy-19040 | r1 | 是 | 1 | 1 | "factor with extension=True drops a factor, multivariate factor over extension"（5） | 是 | `sympy/polys/factortools.py` | 0 | 5 | 2 | 否 | max_model_calls | 0.575 |
| sympy__sympy-20428 | r1 | 否 | 0 | — | — | — | — | 0 | 12 | 未触碰 | 否 | max_model_calls | 0.643 |
| django__django-15973 | r2 | 是 | 1 | 1 | "ManyToManyField through string reference resolution in migration state"（5） | 否 | `django/db/migrations/state.py`, `django/db/models/fields/related.py` | 0 | 2 | 未触碰 | 否 | max_model_calls | 0.774 |
| scikit-learn__scikit-learn-14496 | r2 | 否 | 0 | — | — | — | — | 0 | 4 | 2 | 是 | completed | 0.244 |
| sphinx-doc__sphinx-7440 | r2 | 是 | 1 | 1 | "glossary duplicate term case insensitive"（5） | 是 | `sphinx/domains/std.py` | 0 | 3 | 2 | 否 | max_model_calls | 0.571 |
| sympy__sympy-13091 | r2 | 否 | 0 | — | — | — | — | 0 | 2 | 4 | 否 | max_model_calls | 0.798 |
| sympy__sympy-19040 | r2 | 是 | 1 | 1 | "factor with extension=True drops a factor, multivariate factorization over extension"（5） | 是 | `sympy/polys/factortools.py`, `sympy/polys/polytools.py` | 0 | 2 | 2 | 否 | max_model_calls | 0.772 |
| sympy__sympy-20428 | r2 | 否 | 0 | — | — | — | — | 0 | 4 | 未触碰 | 否 | max_model_calls | 0.697 |

门槛：调用的运行 6/12（要求 ≥6），满足；调用了的 6 次全部在 2 步内读了返回文件，满足。判定：**通过（恰好在下限上）**。

### 3.6 照实描述，不做推断

- 两轮共 10 次调用，都是第 1 步的一次调用，k 都是默认的 5。没有一次在后面的步骤里再检索，也没有无新信息的重复。第二轮 prompt 新加的"搜不到原因时再查一次"没有出现对应行为：sympy-20428 两次都没碰到 gold，也没有调用检索。
- 调用集中在 3 道题：django-15973、sphinx-7440、sympy-19040 在第二轮的 2 次都调用了，在第一轮各调用 1 到 2 次。另外 3 道题（sympy-13091、sympy-20428、scikit-learn-14496）两轮 4 次都没有调用，issue 里都直接写了符号或表达式。
- 所有运行都还在用 `search_code`（每次 2 到 24 次）。
- 调用了的 10 次里有 9 次返回结果含 gold。没有含 gold 的那次（第二轮 django-15973 r2）在整次运行里都没有碰到 gold。
- 解出：只有 scikit-learn-14496，两轮 4 次都解出，这 4 次都没有调用检索。其余 20 次都没有解出，除这 4 次外都停在 40 次模型调用的上限。这是开发检查，不是修复率对照，不能用来比较修复率。
- 第一轮到第二轮，调用从 4/12 变成 6/12。每题只跑 2 次，这个变化不能排除是随机波动；门槛是恰好达到的。

## 4. 演示

`scripts/cr_demo.py <instance_id> [--run <run_dir>] [--show-gold] [--out <file.md>]`，离线运行，不调用模型。三种排序用冻结的 `cr_eval._hybrid_orders` 一次算出：BM25 取块聚合的文件排序，embedding 取文件排序。hybrid 前 5 由 `TaskIndex.search` 生成，与三种排序做一致性核对，不一致就报错。查询是数据集里的 issue 原文（CR-2 preflight 已核对它与 agent 看到的 issue 相同）。`--run` 是 CR-2 P 组运行时，额外列出它首条消息里的预取列表（取自 `cr2-prefetch.json`）。

示例（两份都加了 `--show-gold`，开头写明了 run 目录）：

- [demo/cr2e-t2-sympy__sympy-19040.md](demo/cr2e-t2-sympy__sympy-19040.md)：第二轮 `runs/cr2e/round2/sympy__sympy-19040/r1`，第 1 步调用检索，第 2 步读了返回的 gold 文件 `sympy/polys/factortools.py`。
- [demo/cr2-p-sympy__sympy-19040.md](demo/cr2-p-sympy__sympy-19040.md)：CR-2 P 组 `runs/cr2-main/sympy__sympy-19040/p/r1`。它的预取列表与演示算出的 hybrid 前 5 完全一致。

同一道题放在一起，能看到一个现象：用 issue 全文做查询时，gold 文件在 BM25 排第 216、embedding 排第 3、hybrid 排第 11，所以 P 组的预取前 5 里没有 gold；而 T2 运行里模型自己写的短查询，返回的第 1 个文件就是 gold。这只是一道题上的观察。

## 5. README

`README.md` 和 `README.zh-CN.md` 在"SWE-bench 实验"之后加了"Code Retrieval (CR)" / "代码检索（CR）"一节，各约 30 行，内容如下：

- 模块组成（`repo_cache.py`、`chunking.py` 和 blob 缓存、BM25、embedding、RRF、`serve.py` 和 `retrieve_code`）；
- CR-1 结果，注明是文件定位指标，不是修复率；文件级 hybrid 对 embedding 未检出差异；函数级 Hit@5 高 7.5 个百分点；
- CR-2 的三句结论；
- CR-2e 的结果，即上面第 3 节；
- 复现和演示命令，以及结果文件链接。

没有用"显著提升"之类的词。

## 6. 费用

| 轮 | 运行数 | 账本口径（元） | 缓存口径（元） | 缓存命中率 |
|---|---:|---:|---:|---:|
| 第一轮 | 12 | 7.070 | 2.818 | 65.2% |
| 第二轮 | 12 | 7.179 | 3.092 | 61.2% |
| 合计 | 24 | 14.249 | 5.910 | — |

mock 接线检查花费 0。没有 infra 退役运行，所以没有额外的费用。缓存口径按 `cr2_cost.py` 的单价（命中 0.04、未命中 2、输出 8，单位元/百万 token）计算，由 `analyze_cr2e.py` 写进 `cr2e-dev-round{1,2}.json` 的 `cost` 字段。`cr2_cost.py` 的命令行入口只认 CR-2 格式的 report 标签，所以这里直接调用它的 `cost_rows`，没有改动它。

## 7. 全量 pytest

`.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider`（后台运行一次，输出写到 `runs/cr2e-pytest.out`）：**903 passed、22 skipped、0 failed**，用时 99 秒。CR-2d 时是 871 个。本次新增 32 个：`tests/test_cr2e.py` 30 个，另外 `test_retrieval_isolation.py` 对新文件 `cr_demo.py` 自动参数化出 2 个扫描用例。这次没有出现重复执行。

## 8. 遇到的问题

- `test_cr2_harness.py` 与合同 1.3 节冲突，见 1.4。
- 真实运行期间，系统可用内存只有约 1.1 GB（runner 加载向量缓存后约占 1.4 GB）。演示脚本也要加载一份检索服务，所以我等两轮运行都结束后才生成示例，避免干扰正式运行。
- mock 接线检查中出现一次 `cleanup_failed`，按 infra 规则处理，不计入门槛。
- 输出文件：
  - 入库：`docs/retrieval/cr2e-dev-tasks.json`、`cr2e-runs-round{1,2}.json`、`cr2e-dev-round{1,2}.json`、`demo/` 下 2 份示例。
  - 不入库（`runs/` 已在 gitignore）：`runs/cr2e/round{1,2}/`、`runs/cr2e-mock/`、`runs/cr2e-round{1,2}.log`、`runs/cr2e-pytest.out`、`runs/cr2e-table.py`（生成本文件逐次表的临时脚本）。
- 用时：约 2 小时，在 3 天时限内。

## 9. 提交

- `1fe9b8e` CR-2e：T2 配置、runner、分析脚本（定义先于真实运行）、选题清单。
- `fa478f9` CR-2e：第一轮结果；第二轮的 prompt 改动。
- 本次提交：第二轮结果、费用、演示脚本和示例、README、本检查点。
