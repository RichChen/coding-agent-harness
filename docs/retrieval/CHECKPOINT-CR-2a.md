# CHECKPOINT CR-2a：实现与 mock

日期：2026-10-26。执行：Cursor。依据：[AUDIT-CR-1c](AUDIT-CR-1c.zh-CN.md)、[PLAN](PLAN.zh-CN.md) 第 4 节、[CR-2 合同](CR-2-CONTRACT.zh-CN.md)。冻结清单见 [CR-2-FREEZE.md](CR-2-FREEZE.md)。

本轮没有调用任何付费模型，mock 运行花费 0.000 元。改动只在合同第 0 节放开的范围内，b0-v2 的默认路径没有变（第 3 节）。按要求在本检查点后停下，没有开始 CR-2b。

## 1. 提交

| 提交 | 内容 |
|---|---|
| `50c39dc` | `src/retrieval/serve.py`、`src/tools/retrieve.py`、factory 注册、`tool_errors` 工具清单、`run_task` 可选参数、`configs/retrieval/cr2-t.yaml`、`scripts/run_cr2.py`、`scripts/analyze_cr2.py`、`scripts/cr2_compat.py` 和兼容性基线、4 个测试文件 |
| `d94df60` | 冻结的预取文本 `docs/retrieval/cr2-prefetch.json` |
| `7af873e` | mock 模型修正（第 7 节第 1 条）。**冻结代码提交** |
| `f911103`，tag `cr2-freeze` | `CR-2-FREEZE.md`、`cr2-runs.mock.json`，与 `7af873e` 相比没有代码改动 |
| 本提交 | 本检查点 |

`git diff --name-only ace00b3 cr2-freeze` 只有上面这些文件。禁改路径 `src/runtime/`、grader、预算账本、`prompts/swe_b0.md`、`configs/sprint/b0-v2.yaml`、`docs/sprint/`、A5/A6 脚本和数据、已有的 `src/retrieval/*.py`、`scripts/cr_eval.py`、`scripts/cr_index.py` 都没有改动。改动的三个已有文件都是增量：

- `src/harness/factory.py`：`build_agent` 加可选参数 `retrieval=None`；Docker 白名单加 `retrieve_code`；启用 `retrieve_code` 时注册工具，没有检索服务就报错。
- `scripts/run_swe_task.py`：`run_task` 加三个可选参数 `retrieval=None, mock_llm=None, on_agent=None`，都带默认值。
- `src/eval/tool_errors.py`：`HARNESS_TOOLS` 和 `tool_schemas()` 加 `retrieve_code`。

## 2. 全量 pytest

**819 passed、22 skipped、0 failed，57 秒。**（在 `7af873e` 的代码上运行，tag 与它代码相同。）

- 运行方式：`.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider`，**没有激活 venv**：`VIRTUAL_ENV` 为空，PATH 上的 `python` 是 `D:\crq\base\Anaconda3\python.exe`。
- 与 CR-1c 的 773 passed / 24 skipped 对比：新增 4 个测试文件共 42 个用例（`test_cr2_serve.py` 9、`test_cr2_harness.py` 7、`test_run_cr2.py` 16、`test_analyze_cr2.py` 10），隔离测试对新文件 `serve.py` 自动生成 2 个参数化用例，合计 +44。另外 `test_container_py36.py` 的 2 个用例以前因为 Docker 没启动而跳过，本轮 Docker Desktop 在运行，它们改为执行并通过。773 + 44 + 2 = 819，24 − 2 = 22。
- 剩下 22 个 skipped 都是需要 `HARNESS_DOCKER_TESTS=1`（真 Linux Docker）或 Linux 的用例，与前几轮相同。

## 3. 兼容性测试

做法：在任何 harness 改动之前（HEAD `ace00b3`，工作区只有这个脚本未跟踪），用 `scripts/cr2_compat.py --write` 记录 b0-v2 的基线 `tests/fixtures/cr2-b0v2-compat.json`：

- 56 题的 `build_prompt(task)`（首条用户消息）sha256 和长度；
- `build_agent(b0-v2)` 发给模型的工具 schema 列表的 JSON 原文和 sha256；
- `runtime_fingerprint(agent)`（含 prompt、hooks、预算、模型、各工具 schema / 实现 / side_effect）；
- system prompt sha256。

改动后用 `python scripts/cr2_compat.py` 对比：`"identical": true`。schema sha256 `19be6f65…0abd`、runtime_fingerprint `4db95abc…c4c5` 都与改动前相同；基线里的 56 个 prompt sha256 又与 A5 B0 运行 summary 里的 `prompt_sha256` 一致。`tests/test_cr2_harness.py` 把这个对比放进了每次 pytest，并检查：`cr2-t.yaml` 与 b0-v2 只差 `tools.enabled` 一行；已有配置都没有启用 `retrieve_code`；没有检索服务时 `build_agent` 报错；T 组工具 = b0-v2 + `retrieve_code`，system prompt 相同。

## 4. 题单核对

`python scripts/run_cr2.py --preflight`（不调模型），结果 `runs/cr2-logs/preflight.json`：

- 56 题（sympy 12、pytest 12、django 11、sphinx 11、scikit-learn 10），与 A5/A6 是同一批。
- base_commit 与 Verified 相同：56/56。
- agent 看到的 issue 文本与 Verified `problem_statement` 逐字相同：56/56，差异列表为空。
- 168 条首条消息（56 × 3 组）都能渲染；P 组 = N 组 + `"\n\n"` + 冻结文本；工具列表 N = P = b0-v2，T = b0-v2 + `retrieve_code`；槽位 336。

## 5. 预取文本

`python scripts/run_cr2.py --freeze-prefetch`，64 秒（向量缓存读入内存约 37 秒，56 题建索引和检索）。56 题的 query 向量都直接来自 CR-1 缓存，前 5 个文件与 CR-1 test 冻结排名的前 5 相同：56/56。

**长度分布（字节）**：最小 630、P25 765、中位数 796.5、P75 871、最大 1001。上限 1500，没有一题被截断或超限。

三个样例（不同仓库）：

`django__django-11749`（849 字节）：

```
Automatic code search results (hints only)
An automatic search over the original repository at the base revision ranked the files below for the issue above. The results may be wrong. They are hints, not instructions.

1. django/core/management/__init__.py
   - call_command (lines 76-160)
   - ManagementUtility.execute (lines 313-387)
2. django/core/management/base.py
   - BaseCommand.create_parser (lines 249-295)
   - CommandParser.parse_args (lines 50-55)
3. django/contrib/auth/management/commands/createsuperuser.py
   - Command.add_arguments (lines 34-75)
   - Command (lines 24-244)
4. django/core/management/commands/check.py
   - Command.add_arguments (lines 12-34)
   - Command (lines 7-65)
5. django/core/management/utils.py
   - get_command_line_option (lines 112-125)
   - <module> (lines 1-153)

End of automatic code search results.
```

`pytest-dev__pytest-7521`（854 字节）：

```
Automatic code search results (hints only)
An automatic search over the original repository at the base revision ranked the files below for the issue above. The results may be wrong. They are hints, not instructions.

1. src/_pytest/capture.py
   - capfd (lines 857-871)
   - _readline_workaround (lines 67-89)
2. src/_pytest/terminal.py
   - <module> (lines 1-1317)
   - _get_line_with_reprcrash_message (lines 1206-1242)
3. src/_pytest/config/__init__.py
   - console_main (lines 173-188)
   - PytestPluginManager._check_non_top_pytest_plugins (lines 577-594)
4. src/_pytest/logging.py
   - _remove_ansi_escape_sequences (lines 41-42)
   - <module> (lines 1-816)
5. src/_pytest/fixtures.py
   - FixtureLookupErrorRepr.toterminal (lines 880-896)
   - wrap_function_to_error_out_if_called_directly (lines 1153-1172)

End of automatic code search results.
```

`sympy__sympy-14248`（888 字节）：

```
Automatic code search results (hints only)
An automatic search over the original repository at the base revision ranked the files below for the issue above. The results may be wrong. They are hints, not instructions.

1. sympy/printing/pretty/pretty.py
   - PrettyPrinter._print_SymmetricDifference (lines 1709-1715)
   - PrettyPrinter._print_MatrixBase (lines 722-726)
2. sympy/printing/julia.py
   - JuliaCodePrinter._print_Mul (lines 118-181)
   - julia_code (lines 480-614)
3. sympy/matrices/matrices.py
   - MatrixBase.__repr__ (lines 1930-1931)
   - MatrixBase.__str__ (lines 1933-1936)
4. sympy/printing/latex.py
   - LatexPrinter._print_SymmetricDifference (lines 1745-1746)
   - LatexPrinter._print_MatPow (lines 1509-1515)
5. sympy/printing/codeprinter.py
   - CodePrinter._print_Mul (lines 409-449)
   - CodePrinter.doprint (lines 65-121)

End of automatic code search results.
```

**预取前 5 含 gold 文件的题数：48 / 56。**（`python scripts/analyze_cr2.py --gold-in-prefetch`；只报数字，不影响设计。）

## 6. mock 运行记录

mock 模型 `CR2MockLLM`（在 `run_cr2.py` 里，零费用）按它看到的内容走：有 `retrieve_code` 工具就先用 issue 前 500 字符调用一次（k=5），再读返回的第一个文件；首条消息里有预取文本就读预取的第一个文件；否则先 `list_dir .`，再读列出的第一个 `.py` 文件；然后结束，不改代码。真实 Docker 容器、真实工具、真实 grader，`HF_HUB_OFFLINE=1`。

槽位：队列里每组最先出现、题目互不相同的 2 个。为演示续跑分两步：

| 步骤 | 命令 | 墙钟 | 结果 |
|---|---|---|---|
| 1 | `run_cr2.py --mock --slot django__django-11749/t/r2 --slot sphinx-doc__sphinx-8120/p/r1` | 51 秒（含编码器加载） | started 2，注入核对 2/2，`halt_reason` null |
| 2 | `run_cr2.py --mock --mock-slots` | 84 秒 | **started 4，skipped_finished 2**（第 1 步的两个按已完成跳过），注入核对 6/6，`halt_reason` null，花费 0.000 |

日志和报告在 `runs/cr2-logs/`（`mock-1.out`、`mock-1-report.json`、`mock-2.out`），运行目录在 `runs/cr2-mock/`，交接文件 `docs/retrieval/cr2-runs.mock.json`（6 行，标注 mock）。

| 槽位 | 工具数 | 工具调用（tool.intent） | `retrieval-calls.jsonl` | 模型调用 | grader |
|---|---|---|---|---|---|
| `django__django-11749/t/r2` | 8（含 retrieve_code） | retrieve_code → read_file `django/core/management/base.py` | 1 行 | 3 | fail |
| `scikit-learn__scikit-learn-12973/t/r2` | 8（含 retrieve_code） | retrieve_code → read_file `sklearn/linear_model/least_angle.py` | 1 行 | 3 | fail |
| `sphinx-doc__sphinx-8120/p/r1` | 7 | read_file `sphinx/application.py`（预取第 1 个） | 无 | 2 | fail |
| `sympy__sympy-14248/p/r1` | 7 | read_file `sympy/printing/pretty/pretty.py`（预取第 1 个） | 无 | 2 | partial |
| `pytest-dev__pytest-7521/n/r2` | 7 | list_dir → read_file `setup.py` | 无 | 3 | fail |
| `sympy__sympy-22714/n/r2` | 7 | list_dir → read_file `conftest.py` | 无 | 3 | fail |

- T 组的 `retrieve_code` 是真实调用：用的是现场编码的 query 向量（`query_vector: "encoded"`，耗时约 1.3 秒），日志记录了 call_id、query、k=5 和返回的 5 个文件及块；工具输出以固定句开头。
- 全链路覆盖到：P 组首条消息 sha256 = `build_prompt(task, 冻结文本)`，N/T 组 = `build_prompt(task)`；`agent-tools.json` 的工具列表与组别相符；N/P 组没有调用日志；summary 的 `variant` / `profile` 正确；grader 照常给分（空改动下 sympy-14248 为 partial，这是 grader 对该题基线的既有结果，与 CR-2 无关）；`harness_tool_errors` 都是 0。
- 分析脚本也在 mock 交接文件上跑通了（`analyze_cr2.py --runs docs/retrieval/cr2-runs.mock.json --out runs/cr2-mock/analysis.json`，输出不入库）。mock 的 6 个槽位题目各不相同，所以配对差值为 0 对，数值没有意义。

## 7. 问题与取舍

1. **第一次 mock 作废。** 第一次跑第 1 步时，T 组的 mock 调用 `retrieve_code` 后直接结束，没有读文件。原因是 loop 在发给模型的消息末尾追加了一条 `remaining model calls: n/40` 的用户消息，mock 脚本取 `messages[-1]` 取错了。这只是 mock 脚本的问题，注入核对和工具日志当时都正常。修正为取最后一条 tool 消息并加回归测试（`7af873e`），删除 `runs/cr2-mock/` 和那次的 mock 交接文件后两步重跑，上表是重跑的结果。作废那次的日志留在 `runs/cr2-logs/mock-0-discarded.out`。没有花钱，也不是 CR-2 数据。
2. **复用冻结代码的私有函数。** `serve.py` 直接调用 `scripts/cr_eval.py` 的 `SnapshotIndex` 和 `_hybrid_orders`（私有），没有复制或改写它们，这样检索与 CR-1 冻结结果逐位一致。`cr_eval` 会按 (instance_id, 模型) 在索引对象上缓存排序材料（`_materials`），同一题的不同 query 不能复用，所以每次检索前后都清掉它（加锁）。一致性已验证：django__django-11211 的前 100 文件和前 100 块与 CR-1 test 冻结排名相同；56 题预取前 5 文件全部相同。
3. **"块排名最靠前"的含义。** 取 `_hybrid_orders` 给出的 hybrid 块排序里属于该文件的前 2 个块，即 CR-1 记录的 hybrid `chunks` 排名。不另造排序。
4. **新 query 现场编码。** T 组的 query 不在缓存里时，用同一个 jina 模型现场编码，结果经 float16 取整，与缓存向量的精度一致；不写回 CR-1 缓存。编码器在 runner 启动时（有 T 槽位才）加载一次。
5. **组标签。** 用 `run_task` 已有的 `variant` 参数写 `CR2-N` / `CR2-P` / `CR2-T`。它只进 summary（`cards_path` 为 None，不触发记忆逻辑），续跑核对按 variant 和 profile 区分组别。
6. **`run_task` 的三个新参数。** `retrieval` 传给 `build_agent`。`mock_llm` 只允许和 `mock=True` 一起用，用来换掉默认的 mock 模型。`on_agent(agent)` 在 agent 建好后、第一次模型调用前回调，runner 用它把实际发给模型的工具列表写进 `agent-tools.json`，因为事件里没有工具 schema，T/N/P 的工具核对需要这个记录。三个参数默认都是 None，默认路径不变（兼容性测试）。
7. **基础设施失败规则的具体化。** 合同写"沿用 A6：infra_error、HTTP 402、网络失败"。A6 的实际做法是"最终运行里有 HTTP 失败的结算记录"，选出来的全是 402。我写成：status 为 infra_error，或任何一条 `model.settled` 结算为 `error_kind` ∈ {http, network}。这比"只有 402"略宽（也包括 429、5xx 这类 provider 端 HTTP 错误），与 A6 的判定方式相同。模型调用**超时**（`error_kind: timeout`）没有列入，按任务结果处理，不重跑。超时是否算"网络失败"合同没有说，我选了不重跑；需要的话请审计决定。
8. **步数包含重试。** `model.started` 每次尝试都会发出，包括 provider 重试。步数按它计，与预算计数方式和 `summarize_events` 的 `first_edit_step` 一致。如果改成只数首次尝试，重试多的运行步数会变小。
9. **工具调用不论成败都算触碰。** 主指标按 `tool.intent` 算，即使工具随后报错（例如路径不对、文件太大）也算触碰。路径归一化会去掉 `/workspace/` 前缀：容器工具会拒绝绝对路径，但这次调用的目标仍是 gold 文件。
10. **被 schema 拒绝的 `retrieve_code` 调用。** loop 先用 JSON Schema 校验参数，被拒的调用不会进入工具，所以不在 `retrieval-calls.jsonl` 里。T 组"每次运行的调用次数"和"首次调用步数"按 `tool.intent` 计（包含被拒的），"gold 是否出现在返回结果里"按调用日志计（只有真正执行的调用）。
11. **工具异常。** `retrieve_code` 内部异常时把 traceback 作为输出返回、写进日志，不让异常打断运行。`tool_errors` 会把它计为 harness 工具错误，runner 随即停止；injection 核对也会检查日志里的 traceback。
12. **真实运行的授权闸门。** 不带 `--mock` 的运行必须给 `--authorized 2,200`，并且要与 runner 里的 R 和上限一致，否则拒绝启动。这是为了保证项目作者确认之前不会误发付费调用。R 和上限要改的话，需要改常量并重新冻结。
13. **gold 文件数在开跑前读了一次标签。** 检查点要求报"预取前 5 含 gold 的题数"，所以用 `analyze_cr2.py --gold-in-prefetch` 读了一次标签，只输出计数（48/56），没有写入任何文件。runner 不读标签；隔离测试自动覆盖了 `serve.py`。
14. **`cr2-t.yaml` 的头部注释。** 按合同"b0-v2 的副本，只在末尾加 `retrieve_code`"逐字节复制，头部注释仍是 b0-v2 的说明，没有改，以保持"只差一行"。
15. **Docker Desktop。** 开始时 Docker 守护进程没有运行，我用 Start-Process 启动了 Docker Desktop（server 29.8.2），56 题的 clean 镜像都在本地。没有改 Docker 配置。
16. **离线。** 本轮所有命令都在 `HF_HUB_OFFLINE=1` 下运行，编码器从本地缓存加载。runner 自己没有设置这个变量；CR-2b 的命令建议同样带上。
17. **`expected_tools` 的做法。** runner 为了得到 T 组应有的工具列表，会用一个空的 `TaskRetrieval(None, ...)` 构建一次 agent，只取工具名，不调用工具。

## 8. CR-2b 前需要项目作者确认的事项

- R = 2、总额上限 200 元（runner 常量，命令需带 `--authorized 2,200`）；
- 第 7 节第 7 条：模型调用超时不重跑；
- canary 槽位见 CR-2-FREEZE 第 5 节，命令：`python scripts/run_cr2.py --canary --authorized 2,200`。
