# CR-2 冻结清单

日期：2026-10-26。依据：CR-2 合同。本文件在任何真实运行之前写定，随 tag `cr2-freeze` 一起冻结。之后的 CR-2b/2c/2d 只能按这里的定义执行。

**代码提交：`7af873e`**（runner、检索服务、工具、分析脚本的最后一次代码提交）。tag `cr2-freeze` 打在加入本文件的提交上，它与 `7af873e` 之间只多了本文件和 `cr2-runs.mock.json`。

## 1. 题单与核对

题单 = `run_a5_main.accepted_tasks(load_targets())`，共 56 题：sympy 12、pytest 12、django 11、sphinx 11、scikit-learn 10。与 A5/A6 是同一批题。

```
django__django-11211 django__django-11749 django__django-12276 django__django-13109 django__django-13512
django__django-13964 django__django-14434 django__django-15022 django__django-15499 django__django-15973
django__django-16527 pytest-dev__pytest-10081 pytest-dev__pytest-5262 pytest-dev__pytest-5631
pytest-dev__pytest-5809 pytest-dev__pytest-5840 pytest-dev__pytest-6202 pytest-dev__pytest-7205
pytest-dev__pytest-7324 pytest-dev__pytest-7490 pytest-dev__pytest-7521 pytest-dev__pytest-7982
pytest-dev__pytest-8399 scikit-learn__scikit-learn-10908 scikit-learn__scikit-learn-12585
scikit-learn__scikit-learn-12973 scikit-learn__scikit-learn-13142 scikit-learn__scikit-learn-13439
scikit-learn__scikit-learn-14053 scikit-learn__scikit-learn-14496 scikit-learn__scikit-learn-14710
scikit-learn__scikit-learn-15100 scikit-learn__scikit-learn-9288 sphinx-doc__sphinx-10323
sphinx-doc__sphinx-10614 sphinx-doc__sphinx-7440 sphinx-doc__sphinx-7590 sphinx-doc__sphinx-7910
sphinx-doc__sphinx-8120 sphinx-doc__sphinx-8459 sphinx-doc__sphinx-8593 sphinx-doc__sphinx-9258
sphinx-doc__sphinx-9461 sphinx-doc__sphinx-9673 sympy__sympy-11618 sympy__sympy-13091 sympy__sympy-13757
sympy__sympy-14248 sympy__sympy-15599 sympy__sympy-16766 sympy__sympy-17655 sympy__sympy-19040
sympy__sympy-20428 sympy__sympy-21596 sympy__sympy-22714 sympy__sympy-23950
```

核对（`run_cr2.py --preflight`，不调模型，结果在 `runs/cr2-logs/preflight.json`）：

| 项 | 结果 |
|---|---|
| base_commit 与 SWE-bench Verified 相同 | 56/56 |
| agent 看到的 issue 文本（public task 的 `issue_text`）与 Verified `problem_statement` 逐字相同 | 56/56，没有差异 |
| 预取清单里记录的 issue sha256 与 agent 看到的文本相同 | 56/56 |
| 三组首条消息可以渲染，P = N + `"\n\n"` + 预取文本 | 168/168 |
| 槽位数 | 336 |

检索 query 一律用 agent 看到的 issue 文本。

## 2. 三组

| 组 | 配置 | 与 B0 的唯一区别 | summary 里的 variant |
|---|---|---|---|
| N | `configs/sprint/b0-v2.yaml` | 无 | `CR2-N` |
| P | `configs/sprint/b0-v2.yaml` | 首条用户消息末尾经 `prompt_suffix` 追加第 3 节的冻结文本 | `CR2-P` |
| T | `configs/retrieval/cr2-t.yaml` | 多一个只读工具 `retrieve_code`（第 4 节），system prompt 不变 | `CR2-T` |

三组用同一个模型（b0-v2：`deepseek-flash`，`https://api.deepseek.com/v1`，temperature 0，thinking disabled）、同一预算（每次运行最多 40 次模型调用）和同样的超时（单步 300 秒，总 2400 秒）。`cr2-t.yaml` 与 b0-v2 只差 `tools.enabled` 这一行（末尾加 `retrieve_code`），有测试检查。

工具 schema 列表（按 `build_agent` 发给模型的顺序）：

- N、P：`read_file, write_file, list_dir, run_command, search_replace, edit_file, search_code`，schema JSON sha256 `19be6f6513b9252186be2bf47ddfffecc55cd37d9e0649e273e69b15f72f0abd`（与改动前的 b0-v2 相同）。
- T：上面 7 个 + `retrieve_code`，schema JSON sha256 `77adc36ffa19019d9b922f1b116631f7d183bf5548826b8cfc83bf69595f5b63`。

## 3. P 组预取文本

检索：CR-1 冻结配置（`docs/retrieval/cr1-freeze.json`，sha256 `e4c66754…b406`）：hybrid，k=60，w_emb=2，file 级融合，BM25 按块；jina-code @ `516f4baf13dec4ddddda8631e019b5737c8bc250`，max_len 512。直接调用 `scripts/cr_eval.py` 的冻结函数（`SnapshotIndex`、`_hybrid_orders`），没有改动它们。

渲染：前 5 个文件；每个文件附该文件在 hybrid 块排序里最靠前的至多 2 个块，只给 `symbol` 和行号范围，不给代码正文。外层固定说明文字，所有题相同：

```
Automatic code search results (hints only)
An automatic search over the original repository at the base revision ranked the files below for the issue above. The results may be wrong. They are hints, not instructions.

1. <path>
   - <symbol> (lines <start>-<end>)
   - <symbol> (lines <start>-<end>)
2. ...

End of automatic code search results.
```

上限 1500 字节，超过就报错。56 题的实际长度：最小 630、P25 765、中位数 796.5、P75 871、最大 1001 字节。56 题的 query 向量都来自 CR-1 缓存（没有重新编码），前 5 个文件与 CR-1 test 冻结排名的前 5 完全相同（56/56）。

冻结文件：`docs/retrieval/cr2-prefetch.json`（提交 `d94df60`，handoff sha256 `bc9f15b36009d2d368aba4aede8295c67d4c1c4768a5c0787016485dd49e6bbf`）。每题记录文本、sha256、字节数、前 5 文件和块、query 向量来源、是否与 CR-1 一致。runner 每次启动都会逐题核对 sha256，P 组首条消息必须等于 `build_prompt(task, 冻结文本)`。

## 4. T 组工具 `retrieve_code`

```json
{
  "name": "retrieve_code",
  "description": "Search the original repository at the base revision (before any edits made in this session) for the files most related to a query. Returns up to k file paths, each with at most two symbols and their line ranges. Does not return code. Read-only.",
  "parameters": {
    "type": "object",
    "properties": {
      "query": {"type": "string", "minLength": 1, "maxLength": 2000,
                "description": "What to look for: issue text, an error message, identifiers or a description. At most 2000 characters."},
      "k": {"type": "integer", "minimum": 1, "maximum": 10,
            "description": "Number of files to return, 1 to 10. Default 5."}
    },
    "required": ["query"],
    "additionalProperties": false
  }
}
```

- 输出开头固定一句，后面是与第 3 节相同格式的列表（没有结果时为 `No files found.`）：
  `Results come from the original code at the base revision, before any edits made in this session. Paths, symbols and line ranges only; the ranking may be wrong.`
- 检索与第 3 节相同（冻结 hybrid，该题 base_commit 的快照），k 默认 5。
- 在宿主上运行，只读 CR 缓存（`CR_CACHE_DIR`），不读工作区、不执行命令；`side_effect` = READ_ONLY；没有 `backend` 属性，所以 `runtime_fingerprint` 的计算方式不变。Docker 白名单只多这一个名字。
- 不在缓存里的 query 用 jina 编码器现场编码（经 float16 取整，与缓存一致），不写回缓存。编码器在 runner 进程里只加载一次，所有并发任务共享；每题的快照索引在该槽位开始时建。
- 每次调用（call_id、query、k、query 向量来源、耗时、返回的文件和块；出错时为 error，异常时另有 traceback）追加写入运行目录下的 `retrieval-calls.jsonl`。`ToolResult` 结构不变。工具内部异常以 traceback 作为输出返回，会被 `tool_errors` 计为 harness 工具错误，触发停止。
- 参数先由 loop 的 JSON Schema 校验；被 schema 拒绝的调用不进入工具，所以不在 `retrieval-calls.jsonl` 里，但在 `events.jsonl` 里有 `tool.intent`。
- `build_agent` 在启用了 `retrieve_code` 却没有传入检索服务时报错。已有配置都没有启用它。

## 5. 运行参数

| 项 | 值 |
|---|---|
| runner | `scripts/run_cr2.py` |
| 槽位 | (题, 组, 重复号)，R = 2，56 × 3 × 2 = 336 |
| 顺序 | 按 (题, 组, 重复号) 排序后用 `random.Random(20261026).shuffle` 打乱，三组交错 |
| 并发 | 3（`run_a5_main.PARALLEL`） |
| 总额上限 | 200 元（所有尝试的 `cost_cny` 之和，含退役目录） |
| 输出 | `runs/cr2-main/<task>/<group>/r<k>`；交接文件 `docs/retrieval/cr2-runs.json` |
| canary（CR-2b） | `django__django-11749/t/r2`、`sympy__sympy-22714/n/r2`、`scikit-learn__scikit-learn-12973/t/r2`、`pytest-dev__pytest-7521/n/r2`、`sphinx-doc__sphinx-8120/p/r1`、`sympy__sympy-14248/p/r1`（队列里每组最先出现的 2 个） |
| 授权 | 不带 `--mock` 的运行必须给 `--authorized 2,200`，且与 runner 里的 R 和上限一致，否则拒绝启动。R 和上限由项目作者在 CR-2b 前确认 |

不因为中途结果提前停止，也不追加槽位。

**注入核对**（每个槽位跑完、启动下一个之前检查，不符就停止启动新槽位）：

- summary 的 `prompt_sha256` 和 `events.jsonl` 里保存的首条用户消息，N、T 组等于 `build_prompt(task)`，P 组等于 `build_prompt(task, 冻结预取文本)`；
- 运行目录的 `agent-tools.json`（agent 建好后、第一次模型调用前写入）里的工具名列表：T 组 = b0-v2 + `retrieve_code`，N、P 组 = b0-v2；
- N、P 组不能有 `retrieval-calls.jsonl`；T 组的 `retrieval-calls.jsonl` 不能有 traceback。

**其他停止条件**（沿用 A5/A6，`run_a5_main.run_checks`）：infra_error 不少于 3 次且超过已完成运行的 10%；任何 harness 工具错误；答案隔离失败；grader 报告缺失 id 或崩溃；模型名或 base_url 主机变化；总花费达到上限；runner 自身异常。

## 6. 基础设施失败（开跑前写定）

沿用 A6 的规则。一个槽位的最终运行满足下列任一条就原样退役重跑：

1. summary 的 status 为 `infra_error`；
2. `events.jsonl` 里有任何一条 `model.settled`，其结算记录 `success` 为 false 且 `error_kind` 为 `http`（例如 402 余额不足）或 `network`。

退役用 `run_a5_main.retire_infra`：目录改名为 `r<k>.infra<n>`，原样保留，费用计入总额；每个槽位最多退役 2 次，用完后保留最后一次运行并记入报告的 `infra_kept`。选择只看错误类型，不看 solved。模型 `parse_error`、`timeout`、`context_overflow`、预算用尽、没有改动都属于任务结果，不重跑。

## 7. 指标（`scripts/analyze_cr2.py`）

gold 文件 = `src/retrieval/labels.py` 的文件级标签（`LabelView.gold_files`），只由分析脚本在全部运行结束后读取；runner 不读标签。只用每个槽位的最终运行（不含 `.infra` 目录）；交接文件里如果还有需要重跑的槽位，分析脚本拒绝运行。

**步数规则**（写在脚本里，并用手工构造的事件序列做了单测）：

- 一个事件的步数 = `events.jsonl` 里在它之前的 `model.started` 事件数，即已经发起的模型调用次数，从 1 开始。provider 重试的每次尝试都算（与预算的计数方式相同）。
- 触碰 gold 文件 = 一条 `tool.intent`，工具为 `read_file`、`edit_file`、`search_replace` 或 `write_file`，且 `path` 参数归一化后是 gold 文件。不论工具结果是否成功都算。
- 路径归一化：反斜杠换成 `/`，去掉开头的 `/workspace/` 和 `./`，再做 posix normpath。
- 从未触碰记为 41。

**主指标**：首次触碰 gold 文件的步数。每道题先对 R 次重复取平均，再算 P − N 和 T − N 的按题配对差值：均值，以及按题 bootstrap 95% 区间（`src.retrieval.metrics.paired_bootstrap`，10000 次，种子 20261026，百分位区间）。负数表示更早。

**次要指标**（同样的平均和配对方法）：前 10 步内是否触碰 gold 文件；是否解出（只描述）；模型调用次数；费用；首条消息字节数。停止原因按组计数（含 parse_error、context_overflow）。

**只在 T 组**：至少调用一次 `retrieve_code` 的运行比例；每次运行的调用次数（按 `tool.intent` 计）；首次调用的步数；gold 文件是否出现在该运行任何一次返回结果里（按 `retrieval-calls.jsonl`）。

**只在 P 组**：按预取前 5 是否含 gold 文件把题分成两组，分别给 N、P 的主指标均值和 P − N。

## 8. 兼容性

`scripts/cr2_compat.py` 在任何 harness 改动之前（HEAD `ace00b3`）记录了 b0-v2 的基线 `tests/fixtures/cr2-b0v2-compat.json`（sha256 `dc7ed362…3482`）：56 题首条用户消息的 sha256、`build_agent` 发出的工具 schema JSON 原文、`runtime_fingerprint`、system prompt sha256。改动后重新计算，完全相同；`tests/test_cr2_harness.py` 每次 pytest 都会检查。

| 项 | 值 |
|---|---|
| b0-v2 schema JSON sha256 | `19be6f6513b9252186be2bf47ddfffecc55cd37d9e0649e273e69b15f72f0abd` |
| b0-v2 runtime_fingerprint | `4db95abc408e11946ef34dfc0f6c233cab5c7e5f2d765d632b22b2a1b0cbf4c5` |
| 56 题首条消息 | 与基线逐题相同（基线又与 A5 B0 运行 summary 里的 `prompt_sha256` 一致） |

## 9. 文件

| 文件 | handoff sha256 |
|---|---|
| `configs/sprint/b0-v2.yaml`（未改） | `ddf53b2db293bf03c7a099a43a34ea96aee5ecca365d9b8b4f11d66afb344e60` |
| `prompts/swe_b0.md`（未改） | `35cb1dc577a384a6cec31c92e1773e946d3d2f986bda03c125e6c9a144e2d08e` |
| `configs/retrieval/cr2-t.yaml` | `8bedf94329c4ff2f16eb6a4305dfcceab4b3a97448527318caa87cfc47ac776d` |
| `docs/retrieval/cr2-prefetch.json` | `bc9f15b36009d2d368aba4aede8295c67d4c1c4768a5c0787016485dd49e6bbf` |
| `docs/retrieval/cr1-freeze.json` | `e4c66754a53e87989f5752990abf4a5759d08495ec2889ff4514cb59c653b406` |
| `tests/fixtures/cr2-b0v2-compat.json` | `dc7ed3624e21fe0e165580f3d2550f73bf8d7336869c0385cec0d89d74963482` |
| `docs/retrieval/cr2-runs.mock.json`（mock，不是 CR-2 数据） | `370dd81f2d960421fc4b60f056c15a75f6b5d772e47b6b5631784c103d746a9a` |

## 10. r1 修订

日期：2026-10-27。依据：CR-2 合同第 7 节和 AUDIT-CR-2a。第 1–9 节是 `cr2-freeze` 时的原文，保持不动；与本节冲突的地方以本节为准。本节随 tag `cr2-freeze-r1` 冻结，旧 tag `cr2-freeze` 不动。

**代码提交：`6204762`**（runner、O 组预取脚本、费用报告脚本、分析脚本和测试）。tag `cr2-freeze-r1` 打在加入本节的提交上，它与 `6204762` 之间只多了 `cr2-oracle-prefetch.json`（提交 `a8c388b`）、`cr2-runs.mock-r1.json` 和本节。

### 10.1 改了什么，为什么

项目作者放开了预算：增加第四组 O，R 由 2 改为 5（合同 7.1、7.2）。

| 项 | `cr2-freeze` | r1 |
|---|---|---|
| 组 | N、P、T | N、P、T、O |
| R | 2 | 5 |
| 槽位 | 336 | 56 × 4 × 5 = 1120 |
| 总额上限 | 200 元 | 800 元 |
| 授权参数 | `--authorized 2,200` | `--authorized 5,800` |
| 种子、并发 | 20261026、3 | 不变 |
| 首条消息核对 | 168 | 224（preflight 通过） |

队列仍是按 (题, 组, 重复号) 排序后用 `random.Random(20261026).shuffle` 打乱。新的 canary（每组在队列里最先出现的 2 个，队列位置 0–5、9、20）：
`sympy__sympy-14248/n/r4`、`sphinx-doc__sphinx-8459/p/r3`、`django__django-12276/t/r1`、`pytest-dev__pytest-7324/p/r1`、`scikit-learn__scikit-learn-12585/t/r4`、`sphinx-doc__sphinx-9461/n/r3`、`sympy__sympy-22714/o/r5`、`django__django-16527/o/r4`。

### 10.2 O 组

| 组 | 配置 | 与 B0 的唯一区别 | summary 里的 variant |
|---|---|---|---|
| O | `configs/sprint/b0-v2.yaml` | 首条用户消息末尾经 `prompt_suffix` 追加 O 组冻结文本 | `CR2-O` |

- 与 P 只差列表内容：外层说明文字、格式、1500 字节上限都与第 3 节相同，由同一个渲染函数 `serve.render_prefetch` 生成。工具列表与 N、P 相同（schema sha256 `19be6f65…0abd`）。
- 列表：gold 文件（`labels.py` 文件级标签）按该题冻结 hybrid 文件排序取前 5；不足 5 个时，用非 gold 文件按 hybrid 排序补到 5 个。gold 文件列出它的 gold 块（`labels.py` 函数级块标签），按起始行排序，至多 2 个；补位文件列出 hybrid 块排序里最靠前的至多 2 个块，与 P 相同。
- 文本不出现 oracle、gold 或正确答案一类的说法。56 题中只有 `django__django-14434` 含字符串 `oracle`，那是补位文件路径 `django/db/backends/oracle/schema.py`（Django 的 Oracle 数据库后端），P 组同题列表里也有这个文件。
- 读标签的只有 `scripts/cr2_oracle_prefetch.py`，它写出冻结文件 `docs/retrieval/cr2-oracle-prefetch.json`（schema `cr2.oracle-prefetch.v1`）。结构与 `cr2-prefetch.json` 相同，每题多一个 `gold` 字段：`{"files": gold 文件数, "listed": 列出的 gold 文件数}`。runner 只读这个冻结文件，不导入标签或该脚本；`tests/test_retrieval_isolation.py` 的白名单只有该脚本和 `analyze_cr2.py`，并检查其他 CR-2 文件不导入它们。
- 56 题的长度：最小 602、P25 736、中位数 763.5、P75 833、最大 1013 字节。gold 文件数：1 个 47 题、2 个 5 题、3 个 3 题、21 个 1 题（`sympy__sympy-13091`，只列前 5 个）；没有 gold 文件的题为 0。56 题都列满 5 个文件；只有 1 题（`scikit-learn__scikit-learn-14496`）的 O 文本与 P 逐字相同。query 向量都来自 CR-1 缓存。
- O 组只作为上限参考报告。

### 10.3 注入核对（在第 5 节基础上）

- O 组首条消息必须等于 `build_prompt(task, O 冻结文本)`，且等于 `build_prompt(task) + "\n\n" + O 冻结文本`；工具列表 = b0-v2；不能有 `retrieval-calls.jsonl`。
- 两个冻结文件在 runner 启动时都逐题核对：schema、外层说明文字、开头和结尾的框架、题目集合、issue sha256、文本 sha256 和字节数。

### 10.4 运行记录的补充（只记录，不改判定）

- 返回的模型名：runner 在真实运行里给 agent 的 HTTP 客户端加一个只读的响应钩子，把响应流里出现的 `"model":"..."` 字段记到运行目录的 `model-names.json`；不改动 `src/llm`，不改变发给模型的内容和收到的数据（有测试比对加钩子前后的输出完全相同，HTTP 错误照常抛出）。mock 不加钩子。报告和交接文件每行多一个 `returned_models`。
- 报告多 `started_at`、`finished_at`（本地时间，到秒）和 `oracle_sha256`。
- `scripts/cr2_cost.py` 只做报告：账本费用取各运行 summary 的 `cost_cny`；缓存口径费用取 `model.settled` 的 `prompt_cache_hit_tokens`、`prompt_cache_miss_tokens`、`completion_tokens`，单价 0.04 / 2 / 8 元每百万 token（与 `run_swe_task.make_ledger` 的价格相同，有测试检查）。usage 没有命中/未命中拆分时，整个 `prompt_tokens` 按未命中计。账本和上限逻辑不变。
- mock 输出改到 `runs/cr2-mock-r1` 和 `docs/retrieval/cr2-runs.mock-r1.json`，`cr2-freeze` 里的 mock 证据不被覆盖。

### 10.5 分析（合同 7.3）

`scripts/analyze_cr2.py` 的配对差值加 O − N（主指标只用来确认注入起作用），所有配对指标都同时给 P − N、T − N、O − N；是否解出的 O − N、P − N 仍是按题 bootstrap，只描述。新增按组拆分：前 10 步内触碰过 gold 文件的运行和没有触碰的运行，各自的运行数、解出数和解出率。R 默认 5。步数规则、gold 判定、bootstrap 设置与第 7 节相同。

### 10.6 mock

`run_cr2.py --mock --mock-slots`：4 组各 2 个槽位，8 个都在不同题上，注入核对 8/8 通过，0 元，没有 harness 工具错误。T 组两次 `retrieve_code` 都正常返回（`error` 为 null）；O 组两个槽位读的第一个文件都是列表第一个（gold）文件。`analyze_cr2.py` 可以在 mock 交接文件上运行（结果标为不完整）。

### 10.7 文件

| 文件 | handoff sha256 |
|---|---|
| `configs/sprint/b0-v2.yaml`（未改） | `ddf53b2db293bf03c7a099a43a34ea96aee5ecca365d9b8b4f11d66afb344e60` |
| `prompts/swe_b0.md`（未改） | `35cb1dc577a384a6cec31c92e1773e946d3d2f986bda03c125e6c9a144e2d08e` |
| `configs/retrieval/cr2-t.yaml`（未改） | `8bedf94329c4ff2f16eb6a4305dfcceab4b3a97448527318caa87cfc47ac776d` |
| `docs/retrieval/cr2-prefetch.json`（未改） | `bc9f15b36009d2d368aba4aede8295c67d4c1c4768a5c0787016485dd49e6bbf` |
| `docs/retrieval/cr2-oracle-prefetch.json` | `f9d31f0fc38e2485c7005fe49e5df890b7084f9ac544cf5eed34c5b9e4b913c6` |
| `docs/retrieval/cr1-freeze.json`（未改） | `e4c66754a53e87989f5752990abf4a5759d08495ec2889ff4514cb59c653b406` |
| `tests/fixtures/cr2-b0v2-compat.json`（未改） | `dc7ed3624e21fe0e165580f3d2550f73bf8d7336869c0385cec0d89d74963482` |
| `docs/retrieval/cr2-runs.mock-r1.json`（mock，不是 CR-2 数据） | `018924f81339e528c39a2fd6b76ab1a4f0f9fbc5b0d4ace1a36fea45631318a5` |

## 11. r2 修订

日期：2026-10-27。依据：AUDIT-CR-2b、CR-2 合同第 8.1、8.2 节。第 1–10 节保持不动；与本节冲突的地方以本节为准。本节随 tag `cr2-freeze-r2` 冻结，旧 tag `cr2-freeze`、`cr2-freeze-r1` 不动。

**代码提交：`9b83aff`**，只改了 `scripts/run_cr2.py` 和 `tests/test_run_cr2.py`。tag `cr2-freeze-r2` 打在加入本节的提交上，与 `9b83aff` 之间没有代码改动。

### 11.1 为什么改

canary 里返回模型名一个都没记到（CHECKPOINT-CR-2b 7.1）：httpx 默认请求 `Accept-Encoding: gzip, deflate`，钩子扫描的是解压前的字节。DeepSeek 的 `deepseek-flash` 是服务端别名，价格页（2026-10-27）写明旧模型已下线、请求由 `DeepSeek-V4.1-Flash-1017` 提供；运行期间版本可能再变，所以要记录（AUDIT-CR-2b 第 3 节）。

### 11.2 改了什么

- **模型名钩子**：响应头 `Content-Encoding` 为 `gzip` 或 `deflate` 时，另用一个 `zlib.decompressobj` 解压一份副本来扫描 `"model"` 字段（同时接受 zlib 头和 gzip 头；deflate 没有 zlib 头时退回原始 deflate）。原始字节照常透传给 httpx，请求头不变，`src/llm` 不变。没有编码或 `identity` 时照旧直接扫描；其他编码不扫描。解压或扫描出错时停止扫描，不影响响应。
- **前 3 个槽位检查**：每次运行（不含 mock）里最先完成的 3 个真实槽位，如果都没有记到模型名（没有 `model-names.json`、文件为空、内容为空列表或无法解析），就停止启动新槽位，`halt_reason` 为 `no returned model name recorded in the first 3 real slots: ...`。已在运行的槽位照常跑完。不足 3 个槽位的运行不做这项检查。退役重跑的槽位按最终运行计。
- **多个模型名不停止**：报告新增 `returned_model_slots`：`by_model`（模型名 → 槽位列表）和 `without_model_name`（没有记到模型名的槽位）。mock 运行为 null。
- 常量 `MODEL_CHECK_SLOTS = 3`。其他常量、队列、组定义、注入核对、停止条件、infra 规则都不变。

canary 的 8 个槽位保留，不重跑，它们的模型名记为缺失，在结果里注明。续跑判定（`resumable`）只看 mock、profile、variant，所以全量运行会把它们计入 `skipped_finished`；前 3 个槽位检查只看本次新跑的槽位，不包括它们。

### 11.3 测试

`tests/test_run_cr2.py` 由 22 个增加到 32 个：

- 用 `MockTransport` 和真实的 `LLMClient`，对未压缩、gzip、deflate（zlib 头）、原始 deflate 四种响应，流式和非流式都记到模型名；加钩子后的输出与不加钩子、与未压缩时都相同；
- `br` 编码和损坏的 gzip 正文原样透传、不记录、不报错；
- 402 错误（未压缩和 gzip）照常抛 `LLMError`，错误信息完整；
- 前 3 个真实槽位都没有模型名时停止（覆盖文件缺失、空文件、空列表）；有模型名时继续，并按模型名列出槽位；mock 和不足 3 个槽位的运行不检查；`returned_models` 对坏文件返回空。

全量 pytest（`9b83aff`，未激活 venv）：**850 passed、22 skipped、0 failed**，比 r1 的 840 多 10 个，正好是上面新增的用例。`run_cr2.py --preflight` 通过（56 题，224 条首条消息，1120 个槽位）。

### 11.4 文件

r2 没有改动任何冻结数据文件，第 10.7 节的 sha256 全部不变。

## 12. r3 修订

日期：2026-10-27。依据：AUDIT-CR-2c-halt1、CR-2 合同第 9 节。第 1–11 节保持不动；与本节冲突的地方以本节为准。本节随 tag `cr2-freeze-r3` 冻结，旧 tag `cr2-freeze`、`cr2-freeze-r1`、`cr2-freeze-r2` 不动。

**代码提交：`231dc9f`**，只改了 `scripts/run_cr2.py`、`scripts/analyze_cr2.py` 和它们的测试（`tests/test_run_cr2.py`、`tests/test_analyze_cr2.py`）；`run_swe_task.py`、`src/`、A5/A6 的脚本和判定都没动。合同 9.2 正文只写了 `run_cr2.py`，9.2 的分析脚本条目和 9.4 的启动文字都包括 `analyze_cr2.py`，按后者执行。tag `cr2-freeze-r3` 打在加入本节的提交上，与 `231dc9f` 之间没有代码改动。

合同第 9 节在仓库里的文本有乱码：UTF-8 被按 GBK 解码后又存成了 UTF-8。本次按 GBK 编码再按 UTF-8 解码的方式还原后阅读，没有改动合同文件。

### 12.1 为什么改：9.1 重放核实

`scripts/cr2_replay_snapshot.py`（提交 `a37002c`）用同一个基础镜像起一个干净容器，把 `scikit-learn__scikit-learn-12585/n/r4` 的 `events.jsonl` 里 agent 的每个工具调用按顺序交给同一套工具执行，超时与原运行相同，不调用模型。每步之后统计 `/workspace` 相对 `/opt/repo` 的改动，最后调用真实的 `export_delta()`。输出在 `runs/cr2-logs/replay-scikit-learn__scikit-learn-12585-n-r4.json`。

- 最终改动有 6 个文件，共 4,056,963 字节，没有新文件。其中 agent 改的 `sklearn/base.py` 占 19,055 字节，其余 5 个是重新生成的 Cython 源文件：`sklearn/cluster/_k_means.c` 1,559,660，`sklearn/cluster/_hierarchical.cpp` 1,212,652，`sklearn/_isotonic.c` 897,262，`sklearn/cluster/_dbscan_inner.cpp` 257,334，`sklearn/__check_build/_check_build.c` 111,000。
- 来源是 agent 第 5 步运行的 `python -m pytest sklearn/tests/test_base.py sklearn/tests/test_common.py -q -x`（工具超时，120.2 秒）。其中 `test_common.py::test_configure` 在仓库根目录执行 `setup.py config`，会重新 cythonize。这一步新增了 4 个文件，共 2,478,248 字节。第 6 步（`timeout 110 python -m pytest sklearn/tests/test_base.py sklearn/tests/test_pipeline.py sklearn/tests/test_metaestimators.py -q`，5.8 秒）后又多出 `_k_means.c`，应是上一步留下的 cythonize 子进程跑完了。
- 没有 harness 自己写进工作区的文件。重放的 `export_delta()` 同样抛出 `ValueError: delta_too_large`。

判定：**agent 造成**，即 agent 运行的命令生成的文件超过了快照上限（200 个文件或 4 MiB）。按 9.2 修订。

### 12.2 改了什么

- **重新分类**（`agent_snapshot_failure`、`counted_status`）：下面三个条件同时成立的运行算任务结果，不算 infra：
  - summary 的 `status` 是 `infra_error`；
  - `snapshot_error` 是 `ValueError: delta_too_large` 或 `DockerUnavailable: Docker control output quota exceeded`；
  - `infra_reason` 正好是 `candidate snapshot failed: ` 加上这个 `snapshot_error`。

  `snapshot_error` 只在 `close(snapshot=True)` 的 `export_delta()` 里设置，所以满足这些条件就说明错误出在快照这一步（op = snapshot）。最后一个条件排除了更早已有别的 infra 原因的运行。对这类运行：
  - `needs_rerun` 返回 None，不退役，不重跑；
  - 停止条件 `run_checks` 收到的行把它们的 status 换成 `delta_too_large`，所以不计入 infra 数量。已退役的运行也按同一规则计；
  - `cr2-runs.json` 的行写 `status: "delta_too_large"`、`solved: false`、`no_candidate: true`，并带上 `snapshot_error`。其他行的 `no_candidate` 为 false。

  其他 `snapshot_error`（容器提前消失、tar 失败、超时等）和其他 infra 原因都照旧退役重跑。常量是 `AGENT_SNAPSHOT_ERRORS` 和 `DELTA_STATUS = "delta_too_large"`。
- **已有槽位**：`scikit-learn__scikit-learn-12585/n/r4` 保留的那次运行（`ValueError: delta_too_large`）按新规则成为已完成的任务结果，续跑时跳过。两次退役运行 `r4.infra1`（quota）和 `r4.infra2`（delta_too_large）保留原目录，费用照计。它们的目录名带点，不进入 `cr2-runs.json` 和分析。
- **单实例锁**（`acquire_lock`）：`run_slots` 开始时在输出根目录（全量运行即 `runs/cr2-main/`）用 `O_CREAT|O_EXCL` 独占创建 `run_cr2.lock`，写入 `{"pid", "started_at"}`，在 `finally` 里删除。锁已存在时，下列情况都以 SystemExit 拒绝启动，不写报告、不启动槽位：
  - PID 仍在运行：报告另一个 runner；
  - PID 已不存在：报告陈旧锁，需要人工删除；
  - 锁文件无法解析：同样拒绝。

  Windows 上 `os.kill(pid, 0)` 会结束进程，所以用 `OpenProcess` 加 `GetExitCodeProcess`，`STILL_ACTIVE` 为 259；其他系统用 `os.kill(pid, 0)`。
- **system_fingerprint**：复用模型名钩子，同一个正则同时匹配 `"model"` 和 `"system_fingerprint"` 的字符串值（null 不记）。`model-names.json` 改为 `{"models": [...], "system_fingerprints": [...]}`；读取时也接受 r2 写的纯列表（视为只有模型名）。报告新增 `returned_fingerprint_slots`（`by_fingerprint`、`without_fingerprint`，mock 为 null），`cr2-runs.json` 每行新增 `system_fingerprints`。只做报告，不是停止条件。前 3 个槽位的模型名检查不变。
- **delta_too_large 统计**：报告新增 `delta_too_large_by_group`（组 → 槽位列表），统计范围是本次运行的全部槽位，包括跳过的已完成槽位。
- **analyze_cr2.py**：接受 `delta_too_large` 行。步数等主指标和 T 组指标照常从 `events.jsonl` 计算，`solved` 记为 false（即使行里写了 true），报告新增 `delta_too_large_by_group`（组 → 数量）。其他指标、配对、种子不变。

队列、组定义、注入核对、费用上限、其他停止条件和 infra 规则都不变。

### 12.3 测试

`tests/test_run_cr2.py` 由 32 个增加到 43 个：
- 两种 `snapshot_error` 都改判为任务结果：不退役、不重跑、续跑时跳过、计入 `delta_too_large_by_group`；
- 其他 `snapshot_error`、`infra_reason` 不是这个快照错误的运行、没有快照错误的 infra 仍然退役；
- 4 个改判运行不触发 infra 停止条件；把其中 3 个换成别的快照错误就触发；
- `write_runs` 的 `status`、`solved`、`no_candidate`、`snapshot_error`；
- 锁的三种情况（PID 在运行、陈旧锁、无法解析）都拒绝且不启动槽位；正常结束和槽位崩溃后锁都被删除；`_pid_alive` 认得本进程，认得已结束的子进程；
- 按 fingerprint 列槽位且不停止。

另外，钩子测试的响应里加了 `system_fingerprint`，并检查 null 不记；读取坏文件的测试覆盖新旧两种格式。`tests/test_analyze_cr2.py` 的端到端用例加了一条 `delta_too_large` 行。

全量 pytest（`231dc9f`，未激活 venv）：**862 passed、22 skipped、0 failed**，比 r2 多 12 个：上面 11 个新用例，加上 `test_retrieval_isolation` 对新脚本 `cr2_replay_snapshot.py` 的一个参数化检查。`run_cr2.py --preflight` 通过（56 题，224 条首条消息，1120 个槽位）。用新规则读取现有的 `runs/cr2-main`：28 份 summary 不触发停止条件；`12585/n/r4` 计为 `delta_too_large`，不需要重跑。

### 12.4 文件

r3 没有改动任何冻结数据文件，第 10.7 节的 sha256 全部不变。
