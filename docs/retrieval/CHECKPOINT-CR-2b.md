# CHECKPOINT CR-2b：r1 重新冻结与 canary

日期：2026-10-27。执行：Cursor。依据：[AUDIT-CR-2a](AUDIT-CR-2a.zh-CN.md)、[CR-2 合同](CR-2-CONTRACT.zh-CN.md)第 7 节。冻结清单见 [CR-2-FREEZE.md](CR-2-FREEZE.md) 第 10 节。

按 7.4 的顺序做完：r1 代码和测试，O 组预取冻结，mock（8/8 通过注入核对），`CR-2-FREEZE.md` 第 10 节和 tag `cr2-freeze-r1`（旧 tag `cr2-freeze` 仍指向 `f911103`），全量 pytest（840 passed、22 skipped、0 failed），然后用 `--canary --authorized 5,800` 跑了 8 个 canary 槽位。canary 之外没有启动任何真实槽位。canary 没有触发停止：8/8 通过注入核对，harness 工具错误 0，infra 退役 0。

**canary 费用：账本 3.049 元，缓存口径 1.397 元**（缓存命中率 58.5%，账本是缓存口径的 2.18 倍）。canary 从 2026-10-27 00:52 跑到 01:02（runner 记录 00:52:37 至 01:02:29，北京时间）。

需要项目作者知道的两件事（第 7 节详述）：

1. **没有拿到返回的模型名。** runner 里记录模型名的钩子在真实运行里一条都没记到。原因已在本地复现：httpx 默认请求 gzip 压缩，钩子看到的是压缩后的字节。模型调用本身不受影响。冻结之后没有改代码，修法和选择写在 7.1。
2. **两个 T 槽位都没有调用 `retrieve_code`。** 工具确实发给了模型（注入核对通过），是模型自己没用。两个槽位都解出了。

## 1. 提交

| 提交 | 内容 |
|---|---|
| `6204762` | r1 代码：`scripts/run_cr2.py`、新增 `scripts/cr2_oracle_prefetch.py` 和 `scripts/cr2_cost.py`、`scripts/analyze_cr2.py`；测试 `tests/test_run_cr2.py`、`tests/test_analyze_cr2.py`、`tests/test_retrieval_isolation.py`、新增 `tests/test_cr2_oracle.py`。**r1 冻结代码提交** |
| `a8c388b` | O 组冻结文件 `docs/retrieval/cr2-oracle-prefetch.json` |
| `cd1ba35`，tag `cr2-freeze-r1` | `CR-2-FREEZE.md` 第 10 节、`docs/retrieval/cr2-runs.mock-r1.json`，与 `6204762` 相比没有代码改动 |
| 本提交 | 本检查点 |

`git diff --name-only cr2-freeze cr2-freeze-r1` 只有上面这些文件，外加项目作者的审计、合同和上一轮检查点。`src/`、`configs/`、`prompts/`、`docs/sprint/`、`scripts/cr_eval.py`、`scripts/cr_index.py`、`scripts/run_swe_task.py`、`scripts/run_a5_main.py` 在两个 tag 之间没有差异。`src/llm` 也没有改。

## 2. r1 改动与测试

**`scripts/run_cr2.py`**

- `GROUPS = (n, p, t, o)`，`REPEATS = 1..5`，`TOTAL_CAP = 800`，`--authorized` 必须是 `5,800`。O 组用 b0-v2，variant `CR2-O`，和 P 一样经 `prompt_suffix` 追加文本。
- 启动时两个冻结文件都逐题核对：schema、外层说明文字、文本的开头和结尾框架（新加）、题目集合、issue sha256、文本 sha256 和字节数。preflight 核对 224 条首条消息（56 × 4）。
- 注入核对加 O：首条消息 = `build_prompt(task, O 文本)` = `build_prompt(task) + "\n\n" + O 文本`；工具 = b0-v2；不能有 `retrieval-calls.jsonl`。
- 报告加 `started_at`、`finished_at`、`oracle_sha256`、`returned_models`；交接文件每行加 `returned_models`，顶层加 `groups`、`oracle_sha256`。
- 返回模型名的钩子（第 7.1 节）。
- mock 输出改到 `runs/cr2-mock-r1` 和 `docs/retrieval/cr2-runs.mock-r1.json`。

**`scripts/cr2_oracle_prefetch.py`**：唯一读标签的 CR-2 脚本，构造方法见第 4 节。不覆盖已存在的冻结文件。

**`scripts/cr2_cost.py`**：只做报告，按运行目录汇总 `model.settled` 的 usage，算缓存口径费用，与 summary 的账本费用并列。

**`scripts/analyze_cr2.py`**（合同 7.3）：

- 配对差值加 O − N；
- 新增 `solve_by_early_touch`：每组按"前 10 步内是否触碰 gold 文件"分开，给出运行数、解出数和解出率；
- `repeats` 默认 5。

**测试**

| 文件 | cr2-freeze | r1 | 新增内容 |
|---|---|---|---|
| `tests/test_run_cr2.py` | 16 | 22 | 1120 槽位队列；canary 和 mock 各 8 个；O 组带 P 文本或 T 工具时停止；四组首条消息；冻结文件的框架和 schema 核对；模型名钩子在流式和非流式响应上都能记录，输出与不加钩子时逐字节相同，正文里转义的 `\"model\"` 不会误记；钩子不吞 HTTP 错误（402 照常抛 `LLMError`）；只在真实运行里加钩子 |
| `tests/test_analyze_cr2.py` | 10 | 10 | 端到端用例加 O 组，检查 O − N 和 `solve_by_early_touch` 的数值，R=5 时不完整、R=2 时完整 |
| `tests/test_cr2_oracle.py` | — | 6 | gold 文件排第一、其余按 hybrid 顺序补位；gold 块按起始行排序且至多 2 个；补位文件的块与 P 相同；文本框架与 P 相同、不含 oracle/gold；没有 gold 时与 P 列表相同；gold 超过 5 个时截到 5；gold 文件或块不在快照里时报错；费用单价与 `run_swe_task.make_ledger` 相同；usage 汇总、缺拆分时按未命中计 |
| `tests/test_retrieval_isolation.py` | 34 | 43 | 读标签的白名单只有 `cr2_oracle_prefetch.py` 和 `analyze_cr2.py`；其他 CR-2 文件（`run_cr2.py`、`cr2_cost.py`、`serve.py`、`retrieve.py` 等）不出现 gold 字段名、不导入 `labels` 或 oracle 脚本 |

## 3. 全量 pytest

**840 passed、22 skipped、0 failed，74 秒**，在 `cr2-freeze-r1` 的代码上运行。运行方式与上一轮相同：`.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider`，没有激活 venv。比 CR-2a 的 819 多 21 个，正好是上表的新增数（6 + 0 + 6 + 9）。22 个 skipped 与上一轮相同，都是需要真 Linux Docker 或 Linux 的用例。

## 4. O 组预取

冻结文件 `docs/retrieval/cr2-oracle-prefetch.json`，handoff sha256 `f9d31f0fc38e2485c7005fe49e5df890b7084f9ac544cf5eed34c5b9e4b913c6`，生成时 HEAD 为 `6204762`。

**构造**

- 用 agent 看到的 issue 文本跑 CR-1 冻结 hybrid，排序与 P 组相同。
- gold 文件（`labels.py` 文件级标签）按这个文件排序取前 5；不足 5 个时用非 gold 文件按同一顺序补到 5 个。
- gold 文件列出它自己的 gold 块（函数级块标签），按起始行排序，至多 2 个；补位文件列出 hybrid 块排序里最靠前的 2 个块，与 P 相同。
- 用 P 的渲染函数输出，外层说明文字、格式和 1500 字节上限都与 P 逐字相同。

**统计（56 题）**

| 项 | 值 |
|---|---|
| 长度（字节） | 最小 602、P25 736、中位数 763.5、P75 833、最大 1013；P 组为 630 / 765 / 796.5 / 871 / 1001 |
| gold 文件数 | 1 个：47 题；2 个：5 题；3 个：3 题；21 个：1 题 |
| gold 文件超过 5 个的题 | 1 题（`sympy__sympy-13091`，21 个，列出 hybrid 排序最前的 5 个） |
| 没有 gold 文件的题 | 0 |
| 列出的文件数 | 56 题都是 5 个 |
| 与 P 文本逐字相同 | 1 题（`scikit-learn__scikit-learn-14496`：唯一的 gold 文件已在 P 第 1 位，块也相同） |
| query 向量来源 | 56 题都来自 CR-1 缓存 |
| 超过 1500 字节 | 0 |

**措辞检查**：用正则 `oracle|gold|correct|answer|正确|答案` 搜 56 题的文本，只有 `django__django-14434` 命中。命中的是补位文件路径 `django/db/backends/oracle/schema.py`，即 Django 的 Oracle 数据库后端；P 组同一题的列表里也有这个文件。说明文字里没有这类说法，所以没有改动。

**样例**（下面只列中间的列表；外层说明文字与 P 相同）

1. 典型情况，1 个 gold 文件，`django__django-16527`（890 字节）：

```
1. django/contrib/admin/templatetags/admin_modify.py
   - submit_row (lines 61-112)
2. django/contrib/admin/options.py
   - BaseModelAdmin.has_add_permission (lines 538-545)
   - InlineModelAdmin.has_change_permission (lines 2476-2480)
3. django/contrib/auth/admin.py
   - UserAdmin.response_add (lines 216-230)
   - UserAdmin._add_view (lines 121-147)
4. django/contrib/admin/checks.py
   - ModelAdminChecks._check_save_as (lines 809-815)
   - ModelAdminChecks._check_action_permission_methods (lines 1182-1207)
5. django/contrib/admin/sites.py
   - AdminSite.has_permission (lines 197-202)
   - AdminSite.admin_view (lines 204-249)
```

2. 3 个 gold 文件，`sphinx-doc__sphinx-9461`（836 字节）。前 3 个是 gold 文件，第 4、5 个是补位文件：

```
1. sphinx/ext/autodoc/__init__.py
   - Documenter.filter_members (lines 669-818)
   - PropertyDocumenter.can_document_member (lines 2661-2664)
2. sphinx/util/inspect.py
   - isclassmethod (lines 248-255)
   - getdoc (lines 830-865)
3. sphinx/domains/python.py
   - PyProperty (lines 849-886)
   - PyProperty.get_signature_prefix (lines 867-872)
4. sphinx/ext/autodoc/importer.py
   - get_class_members (lines 240-324)
   - get_object_members (lines 179-237)
5. doc/usage/extensions/example_google.py
   - ExampleClass (lines 180-296)
   - ExampleClass.__special__ (lines 261-275)
```

3. gold 文件超过 5 个，`sympy__sympy-13091`（700 字节，21 个 gold 文件，只列出 5 个，没有补位文件）：

```
1. sympy/core/basic.py
   - Basic.__eq__ (lines 282-321)
   - Basic.__ne__ (lines 323-332)
2. sympy/core/numbers.py
   - Float.__eq__ (lines 1249-1276)
   - Float.__ne__ (lines 1278-1279)
3. sympy/polys/domains/pythonrational.py
   - PythonRational.__ne__ (lines 250-251)
4. sympy/utilities/enumerative.py
   - PartComponent.__ne__ (lines 130-132)
5. sympy/core/exprtools.py
   - Factors.__ne__ (lines 799-800)
   - Term.__ne__ (lines 911-912)
```

## 5. mock

在 HEAD `a8c388b`（代码与 `6204762` 相同）上运行 `run_cr2.py --preflight`：56/56 题，224 条首条消息核对通过，1120 个槽位。

随后运行 `run_cr2.py --mock --mock-slots`，时间为 2026-10-27 00:45:45 至 00:48:57：

| 项 | 结果 |
|---|---|
| 槽位 | 8 个，四组各 2 个，8 道不同的题 |
| 注入核对 | 8/8 通过 |
| 费用 | 0.000 元 |
| harness 工具错误 | 0 |
| T 组 | 两次 `retrieve_code` 都正常返回（`error` 为 null，输出以固定说明句开头） |
| O 组 | 两个槽位第一步读的都是列表第一个文件，都是 gold 文件 |
| 分析脚本 | `analyze_cr2.py --runs docs/retrieval/cr2-runs.mock-r1.json` 可以运行（标为不完整），`solve_by_early_touch` 四组都有值 |

交接文件 `docs/retrieval/cr2-runs.mock-r1.json` 的 handoff sha256 为 `018924f8…18a5`，随 tag `cr2-freeze-r1` 提交。CR-2a 的 mock 证据 `runs/cr2-mock` 和 `cr2-runs.mock.json` 没有被覆盖。

## 6. canary

命令：`HF_HUB_OFFLINE=1 .venv\Scripts\python.exe scripts/run_cr2.py --canary --authorized 5,800`，在 tag `cr2-freeze-r1` 上运行。报告在 `runs/cr2-main/canary-report.json`，日志在 `runs/cr2-logs/canary.out`，费用在 `runs/cr2-logs/canary-cost.json`（`runs/` 不入库，数据都抄在下面）。

**时间**

| 来源 | 开始 | 结束 |
|---|---|---|
| runner 报告 | 2026-10-27 00:52:37 +08:00 | 2026-10-27 01:02:29 +08:00 |
| 外层命令 | 00:52:35 | 01:02:32 |

墙钟 9 分 52 秒，并发 3。

**报告摘要**

| 项 | 值 |
|---|---|
| `halt_reason` | null |
| started | 8（四组各 2 个），not_started 0 |
| 注入核对通过 | 8/8 |
| infra 退役 / 保留 | 0 / 0 |
| `spent_cny`（账本） | 3.049 |

**逐槽位**（按队列顺序）

| 槽位 | 组 | 模型调用 | 账本费用（元） | 缓存口径（元） | 命中 / 未命中 / 输出 token | 停止原因 | 状态 | 解出 |
|---|---|---|---|---|---|---|---|---|
| `sympy__sympy-14248/n/r4` | N | 40 | 0.671 | 0.349 | 164096 / 155695 / 3908 | max_model_calls | partial | 否 |
| `sphinx-doc__sphinx-8459/p/r3` | P | 40 | 0.658 | 0.344 | 159104 / 157217 / 2918 | max_model_calls | fail | 否 |
| `django__django-12276/t/r1` | T | 18 | 0.160 | 0.080 | 41088 / 32921 / 1608 | completed | pass | 是 |
| `pytest-dev__pytest-7324/p/r1` | P | 40 | 0.630 | 0.202 | 217216 / 71890 / 6209 | max_model_calls | pass | 是 |
| `scikit-learn__scikit-learn-12585/t/r4` | T | 10 | 0.077 | 0.032 | 23552 / 11395 / 985 | completed | pass | 是 |
| `sphinx-doc__sphinx-9461/n/r3` | N | 40 | 0.729 | 0.333 | 202496 / 147787 / 3689 | max_model_calls | fail | 否 |
| `sympy__sympy-22714/o/r5` | O | 8 | 0.073 | 0.033 | 20608 / 11668 / 1071 | completed | pass | 是 |
| `django__django-16527/o/r4` | O | 7 | 0.051 | 0.024 | 14464 / 8784 / 692 | completed | pass | 是 |
| **合计** | | 203 | **3.049** | **1.397** | 842624 / 597357 / 21080 | | | 5/8 |

- 每次运行的 `model.settled` 数等于它的模型调用数，203 条全部带 usage，并且都有命中/未命中拆分（`no_split` = 0），所以不需要用到缺拆分时的处理规则。
- 缓存口径费用的算法：命中 token × 0.04 + 未命中 × 2 + 输出 × 8，单位元每百万 token。合计 1.397059 元。

**注入核对**：8/8 通过。首条消息 sha256 与 summary 的 `prompt_sha256` 一致，工具列表符合要求：T 组为 b0-v2 + `retrieve_code`，N、P、O 组为 b0-v2。N、P、O 组都没有 `retrieval-calls.jsonl`。

**T 组调用记录**：两个 T 槽位都**没有调用 `retrieve_code`**，没有 `retrieval-calls.jsonl`，也没有任何工具名为 `retrieve_code` 的 `tool.intent`。

| 槽位 | 各工具调用次数 |
|---|---|
| `django__django-12276/t/r1` | search_code 3、read_file 4、run_command 13 |
| `scikit-learn__scikit-learn-12585/t/r4` | run_command 6、search_code 2、read_file 2、search_replace 1 |

**O 组**：两个槽位第一步都读了 O 列表的第一个文件，也就是 gold 文件（`django/contrib/admin/templatetags/admin_modify.py`、`sympy/geometry/point.py`）。分别用 7 次和 8 次调用解出。

**工具错误**：8 个槽位 summary 的 `harness_tool_errors` 都是 0，没有触发停止条件。events 里有 15 条 `tool.result` 带 error，都是任务层面的：

| 错误 | 条数 | 分布 |
|---|---|---|
| `run_command` 非零退出 | 14 | pytest-7324/p 8 条、sympy-14248/n 2 条、sphinx-9461/n 1 条、sympy-22714/o 1 条、sklearn-12585/t 1 条，其余槽位无 |
| `run_command` 超时（`tool_timeout`） | 1 | sklearn-12585/t |

按冻结规则，这两类都不算 harness 工具错误。

**返回的模型名：没有拿到。** 每个槽位都没有生成 `model-names.json`，报告里的 `returned_models` 是空列表。summary 里的 `model` 字段是请求时用的名字 `deepseek-flash`、host `api.deepseek.com`，与 b0-v2 一致；runner 的模型名和 host 不变检查也通过了。但这只是我们请求的名字，不是 DeepSeek 返回的名字。原因和处理见 7.1。项目作者可以在 DeepSeek 控制台查 00:52–01:02 这段时间的用量明细，那里的模型名就是实际计费用的名字。

**平均费用与全量估算**

| 组 | 账本均值（元/槽） | 缓存口径均值（元/槽） | × 280 槽位（账本） |
|---|---|---|---|
| N | 0.700 | 0.341 | 196.0 |
| P | 0.644 | 0.273 | 180.3 |
| T | 0.119 | 0.056 | 33.2 |
| O | 0.062 | 0.028 | 17.4 |
| 全部 | 0.381 | 0.175 | 426.9 |

估算依据有两个，结论都在 800 元上限以内。

- **按 canary 平均**：1120 × 0.381 ≈ **427 元账本**，缓存口径 1120 × 0.175 ≈ **196 元**。
- **按 A6 每次运行的均值**：178.7 / 314 = 0.569 元，1120 × 0.569 ≈ **637 元账本**；缓存口径 63.8 / 314 × 1120 ≈ **228 元**。

8 个槽位太少，而且偏差方向不确定：

- 这次 T、O 四个槽位都很快解出，所以很便宜；
- N、P 四个槽位都用满了 40 次调用；
- A6 的均值来自 B0 一类的配置，更接近 N 组。

保守的说法是：全量账本费用大约在 430–640 元，按缓存价格实际扣费大约 200–230 元。canary 的缓存命中率 58.5%，低于 A6 的 71%，账本与缓存口径之比 2.18，也低于 A6 的约 2.8。合同 7.5 要求按 canary 的真实扣费估算余额：canary 期间账本记 3.049 元，按缓存价格应扣约 1.40 元，可以和控制台的实际扣费对照。

**墙钟估算**：8 个槽位用了 9 分 52 秒，按比例 1120 个约 23 小时。合同按 A6 估的是约 20 小时。

**说明**：这 8 个 canary 槽位就在 `runs/cr2-main` 下。全量运行时 runner 会把已完成的槽位计入 `skipped_finished` 并跳过，它们的 3.049 元也计入 800 元总额，这与 CR-2a 冻结时的设计相同。

## 7. 问题与取舍

### 7.1 返回的模型名没有记录到

**做法**：审计要求报告返回的模型名。`LLMResponse` 不保存 provider 返回的 `model` 字段，而 `src/llm` 不在放开的路径里。所以在 runner 层用已有的 `on_agent` 钩子，包一层 agent 自己 LLM 实例的 `_ensure_client`，给 httpx 客户端加一个只读的响应钩子。钩子把响应流换成透传的流，边透传边用正则扫描 `"model":"..."`。

- 发出的请求不变，解析结果不变。测试比对过加钩子前后的输出逐字节相同，HTTP 错误照常抛出。
- mock 运行不加钩子。

**真实运行里一条都没记到。** 本地复现了原因，没有发网络请求：httpx 0.28.1 默认发送 `Accept-Encoding: gzip, deflate`，`response.stream` 是解压前的原始字节。

- 用 `MockTransport` 构造一个 gzip 压缩的流式 SSE 响应：解压后的内容正确，钩子记到 `[]`；
- 同样内容不压缩时，钩子能记到模型名。

据此判断 DeepSeek 对响应做了压缩。单元测试的响应没有压缩，所以没有发现这个问题。

**影响**：只影响记录，不影响模型调用、注入核对、费用和判分。canary 的 8 个槽位数据有效。

**没有在冻结之后改代码。** 修正需要改 `run_cr2.py`，等于再做一次冻结修订。可选做法：

1. 钩子看到 `Content-Encoding` 为 gzip 或 deflate 时，另起一个 `zlib.decompressobj` 解压一份副本来扫描，原始字节照常透传。不改请求，不依赖 httpx 的私有接口。有效性只能在下一次真实调用时确认，canary 之外不能发真实请求。
2. 请求时把 `Accept-Encoding` 设为 `identity`。最简单，但改了发出的 HTTP 请求头，虽然不影响模型输入，我认为比第 1 种更不保守。
3. 不修。用 summary 里请求的模型名，加上 DeepSeek 控制台的用量明细来确认。

由项目作者决定。

### 7.2 两个 T 槽位都没有调用 `retrieve_code`

工具 schema 发给了模型（`agent-tools.json` 里有 `retrieve_code`，注入核对通过），mock 里也调用成功了，是真实模型没有选择用它。两个 T 槽位都用 `search_code`、`read_file`、`run_command` 解出了（18 次和 10 次调用）。2 个样本说明不了使用率，但如果全量里 T 组的调用率很低，T − N 就主要反映"多一个工具"本身，而不是检索效果。按合同，system prompt 和工具描述都不能改，所以只记录，不做处理。分析脚本里已有的 T 组指标（调用比例、首次调用步数等）会给出全量数字。

### 7.3 其他取舍

- **O 组的块排序**：gold 块按 (起始行, symbol) 排序，起始行相同时按名字。gold 文件的排序用 agent 看到的 issue 跑出的 hybrid 文件排序，与 P 相同。gold 文件或 gold 块不在快照里就报错；56 题都没有出现这种情况。
- **`django__django-14434` 列表里的 `oracle` 路径**：没有改。它是补位文件的真实路径，P 组也有；改它就要偏离"补位文件按 hybrid 顺序"的规则。
- **读标签白名单包括 `analyze_cr2.py`**：合同 7.1 只要求加入 oracle 脚本。`analyze_cr2.py` 在 `cr2-freeze` 时就按合同第 4 节在全部运行之后读 gold 文件，这次一并写进白名单，并用测试检查其他 CR-2 文件不导入这两个脚本或 `labels`。
- **`cr2_cost.py` 的两条规则**：usage 缺命中/未命中拆分时，整个 `prompt_tokens` 按未命中计（偏保守，这次 0 条）；退役的 `.infra` 目录的费用也计入（与账本总额一致，这次没有）。
- **mock 输出换了路径**：避免覆盖 tag `cr2-freeze` 里的 CR-2a mock 证据。
- **时间的记录方式**：开始和结束时间由 runner 写进报告（本地时间，到秒），外层命令另记一份在 `runs/cr2-logs/canary-wall.txt`，两者相差 2–3 秒，是进程启动和退出的时间。
- **canary 期间没有关闭系统待机**：合同 7.5 是对 CR-2c 的要求，canary 只有 10 分钟。

### 7.4 CR-2c 之前需要项目作者决定或确认

1. 模型名记录怎么处理（7.1 的三种做法）。如果改代码，需要一次 r2 冻结修订。
2. 按 canary 的控制台实际扣费确认余额（合同 7.5）。按上面的估算，缓存口径约 200–230 元，账本口径约 430–640 元，上限 800 元按账本口径计。
3. T 组没有调用检索工具的情况是否需要在全量之前关注（7.2）。我没有做任何改动。

按要求在本检查点提交后停下，没有启动 CR-2c。
