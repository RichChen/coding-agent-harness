# 2026-11 修正记录（一）

起点是 `7aa535f`，每项修正单独一个提交。文中的提交号都是原开发仓库的提交号，公开仓库里没有逐个对应的提交；这些修正在公开仓库里合并发布。后续修正见[修正记录（二）](FIX-2-RESULTS.zh-CN.md)。

| 修正 | 提交 | 改前 | 改后 |
| --- | --- | --- | --- |
| F1 失败原因标签 | `846e3a0` | 10 个新测试 9 个失败 | 10 个通过 |
| F2 Docker 后端超时后卡在“忙” | `ecb1446` | 4 个新测试 2 个失败，复现成功 | 4 个通过 |
| F3 上下文保护规则 v2（新配置项） | `70c2f7f` | 36 个新测试全部失败 | 36 个通过；配置锁定测试 F3 提交时 24 个，F4 后 26 个，都通过 |
| F4 `bytes_kept` 重复计数 | `e46452b` | 13 个新测试全部失败 | 13 个通过 |

没有调用付费模型，没有运行真实 Docker 测试，没有改 `runs/`、产物目录和冻结配置文件。

## 完整测试集

命令：`.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=<绝对路径>`。

| | 通过 | 跳过 | 失败 |
| --- | --- | --- | --- |
| 改前（`7aa535f`） | 1257 | 39 | 1 |
| 改后（`e46452b`） | 1347 | 39 | 0 |

- 改前那 1 个失败是 `tests/test_run_a5_main.py::test_infra_error_slot_is_moved_aside_and_rerun_at_most_twice`。它只在 `--basetemp` 位于仓库目录里面时失败：被测代码把仓库内的路径转成相对路径，断言期望绝对路径。同一个提交上把 `--basetemp` 放到仓库外，这个文件 62 个测试全部通过。改后的完整运行用的是仓库外的 `--basetemp`。
- 多出的 90 个通过 = 89 个新测试 + 上面那个环境问题。
- 39 个跳过是真实 Docker 测试，需要 `HARNESS_DOCKER_TESTS=1`，前后一致。

## F1：失败原因标签

改动在 `src/runtime/loop.py` 和 `src/policy/cny_budget.py`。调用模型时抛出的异常改由 `model_call_failure` 统一归类：

| 情况 | 改前 `failure_reason` / `termination_kind` | 改后 |
| --- | --- | --- |
| 人民币预算超限 | `model_parse_error` / 空 | `budget_exhausted` / `cny_hard_cap` |
| 人民币调用次数上限 | `model_parse_error` / 空 | `budget_exhausted` / `cny_call_cap` |
| 计费未知停机（`PaidCallStopped`） | `model_parse_error` / `billing_usage_unknown` 等 | `paid_call_stopped`（新增）/ 不变 |
| 服务商 HTTP 错误，重试用尽 | `model_parse_error` / 空 | `provider_error`（新增）/ `provider_http_<状态码>` |
| 服务商网络错误、流式响应不完整 | `model_parse_error` / 空 | `provider_error` / `provider_network`、`provider_incomplete` |
| 真正的解析失败（`LLMErrorKind.PARSE`） | `model_parse_error` | 不变 |
| 请求超时 | `request_timeout` | 不变 |
| 其他未分类异常 | `model_parse_error` | `unknown` / `model_call_error` |

`run.finished` 事件新增 `error_type`（异常类名）和 `error_message`（`str(异常)`，替换掉 API key，截断到 512 个 UTF-8 字节，不切断多字节字符）。`run_end` 事件的 `reason` 优先写 `termination_kind`。

改前输出（节选，完整日志未提交）：

```text
E       AssertionError: assert <FailureReaso..._parse_error'> == <FailureReaso...et_exhausted'>
E         - budget_exhausted
E         + model_parse_error
E       AttributeError: type object 'FailureReason' has no attribute 'PAID_CALL_STOPPED'
E       AttributeError: type object 'FailureReason' has no attribute 'PROVIDER_ERROR'
E       KeyError: 'error_type'
9 failed, 1 passed in 6.85s
```

改后：`tests/test_fix1_failure_reasons.py` 10 passed。读取这些标签的 13 个测试文件一起跑：191 passed。

### 兼容性检查

- `scripts/run_cr2.py` 的 `needs_rerun`、`provider_failure` 读的是 `llm_attempts` 里的错误类别，不读 `failure_reason`。新测试断言服务商 503 用尽重试时仍返回 `"provider http 503"`，真正的解析失败仍返回 `None`，rerun 归类不变。
- `scripts/run_swe_task.py` 的 infra 判定同样不读这几个标签。`_stop_reason` 按 `termination_kind`、`failure_reason`、`stop_reason` 的顺序取第一个非空值，所以新运行的 `stop_reason` 会更具体：人民币超限从 `model_parse_error` 变成 `cny_hard_cap`，服务商错误变成 `provider_http_503` 之类。计费未知停机本来就有 `termination_kind`，`stop_reason` 不变。
- `src/eval/metrics.py` 的 `model_parse_error` 计数以后只统计真正的解析失败。`src/eval/report.py` 只是输出 `failure_reason.value`，新值能正常显示。
- 旧 summary 没有 `error_type`、`error_message` 字段，`decode_result` 照常读取，有测试覆盖。
- workspace-v1 运行遇到人民币超限，运行状态会显示为 `budget_exhausted`。

需要 `coding-agent` 配合的地方，这次没有改：

- `scripts/describe_a6_stops.py` 的 `LISTED = ("model_parse_error", "context_overflow")`。新运行的人民币超限不会再出现在 `model_parse_error` 清单里。如果还要单独列出，需要加上 `cny_hard_cap`、`cny_call_cap`、`billing_usage_unknown`、`provider_http_*`。分布表本身按任意值计数，不用改。
- `reposkill_agent/experiments/audit.py`、`offline.py` 里的 `failure_reason` 是自由字符串，不用改。

## F2：命令超时后 Docker 后端卡在“忙”

**复现成功。** `tests/test_fix1_docker_busy.py` 用假子进程，不需要 Docker：

- 命令自己的截止时间先到，`execute` 进入 `finally` 收尾，这时外层取消落进 `finally` 里的 `await asyncio.wait(tasks, timeout=.2)`。改前 `_busy` 仍为 `True`，`docker exec` 进程也没被杀掉。
- 收尾时 `_reap_cli` 出错，采集任务还没结束，`t.exception()` 抛 `InvalidStateError`，跳过 `self._busy = False`。
- 走真实的 `await_with_control` 路径循环 8 次，以及另外抽样 300 次，都没有卡住：外层控制器正好在截止时刻醒来，通常先取消，取消落在 `try` 里，会被正常处理。
- 真正的并发调用仍然返回 `concurrent_operation`。

改前：

```text
test_cancel_during_timeout_cleanup_releases_busy FAILED
    E       assert True is False
    E        +  where True = <...DockerExecutionBackend object ...>._busy
test_failed_cli_reap_with_running_collectors_releases_busy FAILED
    E       asyncio.exceptions.InvalidStateError: Exception is not set.
2 failed, 2 passed in 3.75s
```

改后：4 passed。涉及 Docker 和取消的 30 个测试文件：394 passed, 39 skipped。

**和 CR-2 真实事件对照（只读）。** 事先列出的 7 次运行，出现第一个 `concurrent_operation` 之前的那次工具结果都是 `error_kind=tool_timeout`、`error="deadline during tool: tool_timeout"`、`cleanup_state=null`。这说明 `execute` 没有返回结构化结果，是 `CancelledError` 逃了出来；而 `execute` 的 `try` 会接住取消，只有取消落在 `finally` 里才会逃出。这 7 次都是该运行的第一次命令超时，之后分别有 33、17、5、25、34、29、15 次调用全部返回 `concurrent_operation`。整个 `runs/cr2-main` 里共有 41 次命令超时：29 次 `execute` 正常返回了结构化结果，之后都恢复了；12 次取消逃逸，其中 7 次后面跟着 `concurrent_operation`，另外 5 次发生在运行的最后一次调用。

**修复**（`src/execution/docker.py`）：收尾逻辑移到 `_settle_exec`，在单独的任务里跑，用 `asyncio.shield` 等待。收尾期间再来的取消不会打断它，收尾跑完后照常返回超时结果，这和 `try` 里接住取消后的做法一致。`_busy = False` 放在内层 `finally` 里；读取采集结果前先检查 `t.done()`，没结束的任务会被取消。

**按作者决定，F2 不加开关，对所有配置生效。** 这会改变模型在上述竞态中看到的内容：以前是空输出加 `Error: deadline during tool: tool_timeout`，之后每次调用都是 `concurrent_operation`；现在和另外 29 次超时一样，看到带部分输出和超时提示的结构化结果，后续调用正常执行。

**顺带检查。** `src/execution/local.py` 没有“忙”标记，不存在卡住的问题，但它的 `finally` 里也有可以被取消打断的 `await`（见下面的新问题）。`src/workspace/coordinator.py` 的锁在 `finally` 里同步释放，释放前没有 `await`；`docker_transport.py` 的 `finally` 也只做同步赋值。这两处没有同样的写法，没有改。

## F3：上下文保护规则 v2

新配置项是 `runtime.context.protection_rules`，取值 `v1`（默认）或 `v2`。`v1` 就是现在的规则，代码路径没动。

问题本身用合成历史就能看到（`tests/fixtures/fix1_context.py`）：历史里有一次真实的 pytest 失败、两次用 `run_command` 改文件（heredoc 重定向和 `sed -i`），最后一次是没有匹配的 `grep`。`v1` 只保护那个 `grep`，因为 `run_command` 的状态行里有 `status=failed` 和 `Error: nonzero_exit`，命中了 `"failed" in lowered`。`v2` 保护的是 pytest 失败和两次改文件。

`v2` 的规则（`src/runtime/compact.py`）：

- 失败：只看实质内容，包括 `Traceback (most recent call last)`、`AssertionError`、pytest 的 `E   ` 断言详情行、`=== FAILURES ===` 和 `=== ERRORS ===`、行首的 `FAILED` 和 `ERROR`，以及 `N failed`、`N error(s)`（N ≥ 1）。只有状态行，或者退出码非零但没有输出，都不算失败。按事先约定的定义，非命令工具只有一行 `Error: ...` 时，在 `v2` 下也不算失败。
- 修改：除了 `write_file`、`search_replace`、`edit_file`，`run_command` 的命令里出现以下写法也算：写文件的重定向（`>`、`>>`、`&>`，目标是 `&N`、`/dev/null`、`/dev/stdout`、`/dev/stderr` 的除外）、`tee`、`sed -i` 和 `--in-place`、`perl -i`、`patch`、`truncate`、`git apply`、`git am`。带重定向的 heredoc 会被重定向规则覆盖。按事先约定宁可多算，例如引号里的 `>` 也会被当成修改。

这个选项接到了 `RuntimeContextConfig`、`ContextPolicyConfig`、`MicroCompactHooks`、`WindowPolicyHooks` 和 `micro_compact_openai_messages_with_stats`。它不是 `WindowPolicy` 的字段，因为 `asdict(WindowPolicy)` 会进入 `WindowPolicyHooks.fingerprint()` 和 summary 的 `config_hash`。只有取值不是 `v1` 时，它才会写进配置的 dump、`runtime_fingerprint`、窗口指纹和 trace 元数据。`scripts/` 里直接调用这些函数的地方都用默认值，行为不变。

改前：36 failed（新函数和参数不存在）。改后：36 passed。上下文相关的 18 个测试文件：305 passed, 16 skipped。

**默认行为不变的证据。** 在改动之前的代码（`7aa535f`）上，用这份合成历史记录了 micro-compact（默认的 `preserve_tools` 和 `preserve_tools=set()` 两种）和 `WindowPolicyHooks`（`masking`、`summary`、`mask-then-summary` 三种策略）输出视图的 `view_hash`，以及受保护的调用。测试断言：改后不传参数、显式传 `v1` 时结果都和记录值逐一相同，窗口指纹里不出现新字段；传 `v2` 时 `summary` 的视图不同，并且保留了 pytest 失败和两次改文件。

## F4：`bytes_kept` 重复计数

内存预览和 artifact 文件是同一段输出的两个前缀，`docker.py` 和 `local.py` 把两者相加，同一个字节算了两次。CR-2 的真实事件里（只读抽查 `pytest-dev__pytest-6202/p/r2`），13 次有输出的 `run_command` 全部是 `bytes_kept == 2 × bytes_seen`。

明确含义：`ExecutionResult.bytes_kept` 是写进本次工具 artifact 文件的字节数。workspace 后端本来就是这样算的。新增的 `ExecutionResult.bytes_kept_memory` 是内存预览的字节数。没有 artifact 目录时 `bytes_kept` 为 0。这和运行级 `RunOutputMeter.bytes_kept`（计入整次运行输出上限的字节）是两回事，字段注释里写明了。

按作者决定，修正分两部分：

- 结构化字段对所有配置生效：`ExecutionResult.bytes_kept`、`ToolResult.bytes_kept`，以及工具结果事件里的 `bytes_kept`。
- 模型看到的状态行 `bytes_kept=` 默认仍显示旧值（内存 + artifact）。只有设置 `policy.command.observation_bytes_kept: persisted` 时才显示修正后的值。旧值保存在 `ExecutionResult.legacy_trailer_bytes_kept` 里，由 `observation_text(trailer_bytes_kept=...)` 选择显示哪个。会渲染状态行的三个工具 `run_command`、Docker 文件工具和 `search_code` 都接了这个选项；它只在取值不是 `legacy` 时才写进配置的 dump 和 `runtime_fingerprint`。

测试覆盖本地后端（真实子进程）和 Docker 后端（假子进程），情况包括只有 stdout、stdout 和 stderr 都有、没有 artifact 目录、内存上限截断，以及状态行在默认、`legacy`、`persisted` 三种取值下的显示。

改前（节选）：

```text
E       AssertionError: assert 600 == 300        # local，单流 300 字节
E       AssertionError: assert 600 == 300        # docker，单流 300 字节
E       AssertionError: assert 840 == (300 + 120) # 两条流
E       AssertionError: assert 420 == 0          # 没有 artifact 目录
13 failed in 0.98s
```

改后：13 passed。涉及执行结果和这三个工具的 13 个测试文件：194 passed, 16 skipped。

## 冻结配置的 `config_hash` 和默认行为

- `tests/test_fix1_config_lock.py` 用固定的环境变量加载 `configs/` 下全部 23 个能被 `load_config` 读取的配置，断言 `canonical_hash`、session 用的配置摘要、`runtime_fingerprint`（其中 11 个能构建 agent）都和 `7aa535f` 上记录的值相同（`tests/fixtures/fix1_config_hashes.json`）。四项修正都提交后，这个测试全部通过。
- 反向对照：临时去掉“默认值不写进 dump”的处理，23 个配置的 `canonical_hash` 全部变化。这说明锁定测试确实能发现问题，也说明这个处理是必要的。
- 同一个文件还断言：打开 `protection_rules: v2` 或 `observation_bytes_kept: persisted` 后，配置哈希和运行指纹都会变化；`build_agent` 会把 `observation_bytes_kept` 传给全部渲染状态行的工具。
- 对旧配置，模型可见的变化只有 F2 竞态那一处，这是作者已同意的。另外，F1 改的是结果标签和 `run.finished` 字段，F4 改的是结构化 `bytes_kept`，都不会进入模型看到的内容。

## 新发现的问题（只记录，没有扩大范围）

1. `run.finished` 里的 `llm_attempts` 原样保存服务商的错误信息。F1 只对新增的 `error_message` 替换了 API key，如果服务商在错误信息里回显 key，`llm_attempts` 里仍然是原文。写 F1 测试时发现，测试里因此去掉了“整个事件不含 key”的断言。
2. `src/execution/local.py` 的 `finally` 里有 `_cleanup_process`、`asyncio.wait` 等可以被打断的 `await`。如果遇到和 F2 一样的竞态，剩余的收尾（回收进程、关闭管道）会被跳过，`CancelledError` 也会逃出 `execute`。它没有“忙”标记，不会卡住后续调用，所以没有修。
3. Docker 命令超时后，`_reap_cli` 只杀掉宿主机上的 `docker exec` 客户端，容器里的进程可能还在运行（未验证）。F2 修好后，超时后的下一条命令会正常开始，可能和仍在运行的旧进程并存。29 次正常超时路径本来就是这样，不是 F2 引入的。
4. `tests/test_run_a5_main.py::test_infra_error_slot_is_moved_aside_and_rerun_at_most_twice` 依赖 `--basetemp` 不在仓库目录里。
5. `tests/test_workspace_completion.py::test_explicit_checks_cannot_spend_last_candidate_slot` 在和其他测试并发、机器负载高时失败过一次，单独运行和两次完整运行都通过，疑似不稳定。
6. F1 的命名：新增的枚举值是 `paid_call_stopped` 和 `provider_error`；`termination_kind` 新增 `cny_hard_cap`、`cny_call_cap`、`provider_http_<状态码>`、`provider_network`、`provider_incomplete` 和 `model_call_error`。流式响应不完整（`LLMErrorKind.INCOMPLETE`）以前记成解析失败，现在归为服务商错误。
