# 2026-11 修正记录（二）

接[修正记录（一）](FIX-1-RESULTS.zh-CN.md)。harness 的起点是 `2bef4eb`，每项修正单独一个提交。这期间 harness 里另有 `3060b4c`、`ca1255b`、`e4c2e6f` 三个提交，只改分析脚本和文档，不涉及 `tests/`。文中的提交号都是原开发仓库（coding-harness、coding-agent）的提交号，公开仓库里没有逐个对应的提交；这些修正在公开仓库里合并发布。下文的 P1–P4 是这次修正的条目编号，和 `docs/roadmap/` 里 P0–P4 阶段的编号无关。

| 项目 | 仓库和提交 | 改前 | 改后 |
| --- | --- | --- | --- |
| P1 准入估算 `calibrated_v1`（新配置项） | harness `a46fb0e` | 新测试在收集阶段报错（`ImportError`） | 新测试 12 个通过；连同相关测试 85 passed |
| P2 A6-R `context_overflow` 勘误 | harness `02c9011`（复算脚本和输出），coding-agent `c5c6acf`（两份文档） | — | — |
| P3.1 服务商错误文本脱敏 | harness `721facb`；扫描脚本和结果 `e67532b` | 12 个新测试 4 个失败 | 连同相关测试 91 passed |
| P3.2 Docker 超时后容器里的进程（新配置项） | harness `108baa6` | 真实 Docker 上 `test_docker_execution.py` 3 failed, 12 passed；新单元测试收集报错 | 真实 Docker 上该文件加新单元测试 26 passed |
| P3.3 `local.py` 收尾不被取消打断 | harness `709b7d0` | 2 个新测试都失败 | 连同相关测试 121 passed, 23 skipped |
| P3.4 `test_workspace_completion` 不稳定 | harness `236a3eb` | 自然条件下没有复现；注入停顿后复现了两种失败形态 | 该文件 31 passed |
| P3.5 `describe_a6_stops` 识别新标签 | coding-agent `c217ff9` | 1 failed, 2 passed | 3 passed |
| P4 测试数量 | harness `697daaf`，coding-agent `e0d3b43` | — | — |

没有调用付费模型。没有改 `runs/`、产物目录和冻结配置。真实 Docker 测试只在 Docker 引擎可用后，用 `HARNESS_DOCKER_TESTS=1` 跑了 `tests/test_docker_execution.py`。工作开始时本机 Docker 引擎返回 500，中途才可用，所以 P3.2 是在真实 Docker 上验证的。

## 完整测试集

命令：`.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=<仓库外的绝对路径>`，不设 `HARNESS_DOCKER_TESTS`。

| | 通过 | 跳过 | 失败 | 合计 |
| --- | --- | --- | --- | --- |
| 改前（`2bef4eb`） | 1347 | 39 | 0 | 1386 |
| 改后（`697daaf`，代码同 `108baa6`） | 1384 | 40 | 0 | 1424 |

- 合计多出 38 个，全是这次新增的测试：P1 12 个、P3.1 12 个、P3.3 2 个、配置锁定测试 1 个新参数，P3.2 单元测试 8 个，以及 P3.2 新的真实 Docker 测试 3 个（默认跳过）。这期间 `tests/` 只有这次修正的改动。
- 跳过数按新增的 3 个 Docker 测试应为 42，实际是 40。差的 2 个是 `tests/test_container_py36.py` 的两个测试：它们在 Docker 守护进程和 `python:3.6.15-slim-buster` 镜像都可用时才运行。改前基线跑的时候 Docker 引擎不可用，所以它们跳过了；改后引擎可用，它们运行并通过。
- 改后的 40 个跳过全部是需要真实 Linux 或 Docker 的选择性测试（`-rs` 输出逐条核对过）。
- coding-agent（系统 Anaconda Python，按 README 排除 3 项）：改前 363 passed, 1 skipped，改后 365 passed, 1 skipped。`c5c6acf` 的干净克隆是 359 passed, 7 skipped，失败的 3 个正好是 README 排除的那 3 项。

## 冻结配置哈希

`tests/test_fix1_config_lock.py` 在 `697daaf` 上 27 passed：23 个锁定配置的 `canonical_hash`、session 配置哈希和运行时指纹都和修正记录（一）记录的一致；其余 4 个测试检查新配置项，其中 3 个确认打开后哈希和指纹会变。这次新增的两个配置项都是默认值时不写进配置 dump：

- `runtime.context.admission_estimate`（默认 `utf8_bytes`），指纹只在非默认时加 `admission` 字段。锁定测试里加了这一项的参数。
- `policy.command.docker_reap_on_timeout`（默认 `false`），只在打开时给后端 capabilities 加 `exec_reap_on_timeout`。锁定测试的参数化固定用工作区 profile，而这个选项不支持工作区传输，所以它的"打开后哈希和指纹会变"写在 `tests/test_fix2_docker_reap.py`，用 `configs/docker.yaml`。两个文件一起跑 28 passed。

## P1：准入估算

### 选了哪种方法，为什么

选的是用历史真实用量拟合折算系数，没有用分词器。本机没有 DeepSeek 的分词器（Hugging Face 缓存里只有几个嵌入模型），用分词器要下载模型文件，还要给运行时加 `tokenizers` 依赖；而且服务商返回的 `prompt_tokens` 还包含对话模板的 token。拟合系数不需要新依赖，并且直接拿服务商的计数来检验。

新方法 `calibrated_v1`：

```text
admission_input_tokens = ceil(ASCII 字节数 / 2.1) + 非 ASCII 字节数 + 协议余量
协议余量 = 512 + 每条消息 32 + 每个工具 128（和原方法相同）
```

非 ASCII 字节按 1 字节 1 token 计，和原方法一样保守。打开方式是 profile 里写 `runtime.context.admission_estimate: calibrated_v1`。它同时作用于主循环的 `_prepare_view` 和工作区模式 `completion.py` 的 `can_feedback`。账本里的 `estimated_input_tokens` 不变。

### 离线回放

脚本在 `scripts/audit/fix2_2026_11/`：`collect_requests.py` 从事件里取出每次请求，`summarize_admission.py` 拟合并汇总，输出 `admission_summary.json` 已提交。中间文件 `requests.jsonl` 不提交。

- 数据：`runs/a5-main`、`runs/a6-retry`、`runs/cr2-main` 下 2,027 个运行目录（含退役目录），其中 2,005 个有请求。每条 `context.prepared` 和它之后第一个成功的 `model.settled` 配对，共 57,274 条：A5 9,390，A6-R 10,353，CR-2 37,531。
- 被拒的请求没有 `context.prepared`，用前一条 `state.saved` 的原始消息按同样的遮蔽重建视图。5 条重建结果的序列化字节数都和 `context.rejected` 里记录的完全一致，按原方法重算的估算也和记录值相等。
- 拟合：对每条请求算"不低估所允许的最大系数" `ASCII 字节 / (prompt_tokens − 非 ASCII 字节 − 余量)`。它的分布是最小 2.365、1% 分位 4.55、中位数 6.46。取最小值乘 0.9 的安全系数，再向下取到 0.05 的整数倍，得到 2.1。
- 留一个实验检验：用另外两个实验拟合，再测留出的那个。A5 和 CR-2 留出时低估都是 0。A6-R 留出时，用 A5+CR-2 拟合出的系数是 2.55，会低估 A6-R 的 8 条请求。最密的请求几乎都来自 A6-R 的 sphinx-7590 R0 那一次运行（就是溢出的那次），其次是 CR-2 的 sphinx-10323。所以安全系数不能省，换模型或换任务类型要重新拟合。

### 误差分布

估算除以真实 `prompt_tokens` 的分位数（57,274 条）：

| | 最小 | 1% | 5% | 中位数 | 95% | 99% | 最大 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 原方法 `utf8_bytes` | 2.27 | 3.49 | 3.74 | 4.16 | 4.55 | 4.74 | 5.15 |
| 新方法 `calibrated_v1` | 1.12 | 1.83 | 1.96 | 2.21 | 2.46 | 2.62 | 2.81 |

高估的 token 数：原方法中位数 20,851，最大 66,645；新方法中位数 7,874，最大 24,205。

- 低估：两种方法都是 0/57,274。
- 最坏情况（最接近低估的一条）：A6-R `sphinx-doc__sphinx-7590/r0/b0r1/r3` 第 11 步，真实 33,380，新方法估 37,289，是真实值的 1.12 倍；原方法估 75,697。
- 新方法按三个实验分别看，低估也都是 0；中位数分别是 A5 2.22、A6-R 2.15、CR-2 2.22 倍。

### 5 次 `context_overflow` 在新方法下

| 运行 | 原方法估算 | 新方法估算 | 前一次真实 prompt_tokens | 新方法是否放行 |
| --- | --- | --- | --- | --- |
| A6-R django__django-13964/r1/b0r2/r1 第 34 步 | 92,548 | 46,081 | 17,843 | 放行 |
| A6-R pytest-dev__pytest-5840/r1/b0r2/r3 第 36 步 | 89,463 | 45,125 | 20,758 | 放行 |
| A6-R sphinx-doc__sphinx-7590/r0/b0r1/r3 第 18 步 | 87,823 | 43,304 | 35,329 | 放行 |
| A6-R sphinx-doc__sphinx-8120/r1/b0r3/r3 第 40 步 | 88,306 | 44,316 | 19,744 | 放行 |
| CR-2 pytest-dev__pytest-8399/n/r2 第 31 步 | 92,472 | 45,921 | 23,897 | 放行 |

上限都是 96,000，预留输出 8,192。新方法的估算加预留是 51,496–54,273，5 次都会放行。

### 会不会超过模型的真实窗口

profile 里的模型是 `deepseek-flash`，窗口按 DeepSeek 官方文档取 1M token。

- 经验上：新方法在 57,274 条历史请求上从不低于真实值。照这个规律，放行的请求真实输入不超过 96,000 − 8,192 = 87,808。历史上真实 `prompt_tokens` 的最大值是 35,329。
- 硬上界：放行意味着 `ASCII 字节/2.1 + 非 ASCII 字节 ≤ 96,000`，所以请求的总字节数不超过 2.1 × 96,000 = 201,600。字节级 BPE 每个 token 至少对应 1 个字节，真实 token 数不会超过字节数加上对话模板的少量 token，远低于 1M。这一条假设 DeepSeek 的分词器是字节级 BPE。
- 用了新方法以后，`max_tokens: 96000` 的含义从"按字节算的上限"变成"服务商 token 的偏高估计的上限"，和模型窗口是同一个单位。

### 测试

`tests/test_fix2_admission.py` 12 个：默认方法结果不变；新公式；非 ASCII 字节按 1 字节 1 token；新方法估算不高于原方法；`admit` 按选项选方法；hook 的指纹只在非默认时变化；主循环在 `limit=38000` 时原方法溢出、新方法正常调用两次；工作区 `can_feedback` 用了所选方法；配置能传到 hook。

## P2：A6-R `context_overflow` 勘误

复算脚本 `scripts/audit/errata_2026_11/e5_a6r_context_overflow.py`，输出 `e5_a6r_context_overflow.json`，都已提交（harness `02c9011`）。勘误写进 coding-agent 的 `docs/sprint/A6-R-RESULTS.zh-CN.md` 和 `docs/MEMORY-FINAL-REPORT.zh-CN.md` 的"勘误（2026-11）"一节（`c5c6acf`），原文不改。

复算确认了事先整理的事实：

- 4 次都只有 1 条 `context.rejected`。它的 step 紧接在最后一条 `context.prepared` 之后，最后一条 `context.prepared` 没有超限标记。
- 估算 87,823–92,548，加 8,192 都超过 96,000。前一次真实 `prompt_tokens` 是 17,843–35,329，字节估算是它的 2.49–5.19 倍。
- R0 1 次、R1 3 次。402 退役目录里没有这种停止，A5 的 392 次运行里也没有。

和事先的说法有一处不同，写进了勘误。事先的说法是 3/4 在 R1 和人民币上限同因。数据显示，这 3 次的 `<previous_attempt>` 记录只有 673、4,793、1,330 字节，占估算的不到 6%。粗算把记录字节从估算里去掉：pytest-5840 那一步会放行；sphinx-8120 那一步也会放行，但它那时已经用满 40 次调用；django-13964 仍然超限。所以记录只能解释 1 次截断。另外，截断损失的调用数是 R0 那一次最多（少了 22 次），偏差方向不确定。勘误里写的是"来源有一部分相同"，并说明 4 次太少，看不出 R1 是否系统性地更容易溢出。

## P3.1：脱敏和密钥扫描

### 脱敏

新模块 `src/llm/redact.py`。`redact_secrets(text, secrets)` 先把已知的 key 原文替换成 `[redacted]`，再用正则处理 `Authorization`/`Bearer`、`api_key`/`x-api-key`/`access_token`/`secret` 这类字段，以及 `sk-` 开头的 key。用在这些地方：

- `src/llm/client.py`：`chat` 和 `stream_chat` 的 HTTP 错误响应体、网络错误、流式响应里的通用异常。
- `src/runtime/retry.py`：`AttemptRecord.message`（也就是 `run.finished` 里的 `llm_attempts`），以及重试的 warning 日志。
- `src/runtime/loop.py`：`_error_text`；通用 `except` 写进日志和 `state.record_failure` 的错误文本。

改前，`tests/test_fix2_redaction.py` 4 failed, 8 passed。失败输出里能看到 key 原样进了日志：

```text
ERROR    src.runtime.loop:loop.py:771 LLM call failed: LLM API error 401: {"error":{"message":"Incorrect API key provided: sk-fixturekey0123456789abcdef", ...
FAILED tests/test_fix2_redaction.py::test_client_error_message_has_no_key[False]
FAILED tests/test_fix2_redaction.py::test_client_error_message_has_no_key[True]
FAILED tests/test_fix2_redaction.py::test_attempt_record_and_retry_log_have_no_key
FAILED tests/test_fix2_redaction.py::test_run_artifacts_have_no_key - assert ...
4 failed, 8 passed in 1.05s
```

改后连同相关测试 91 passed。整轮运行的测试检查 `events.jsonl`、`run.finished` 和日志里都没有 key。

### 已有产物的扫描（只报告，不改）

脚本 `scripts/audit/fix2_2026_11/secret_scan.py`，输出 `secret_scan.json`。扫描时 harness 在 `697daaf`，coding-agent 在 `e0d3b43`。

范围是两个仓库里所有进过 git 的内容：

- 当前跟踪的文件，按工作区里的样子读取：harness 19,443 个文件，共 313 MB；coding-agent 439 个文件，共 2.9 MB。
- 任意 ref 可达的全部历史 blob，每个 blob 只读一次：harness 280 个提交、7,692 个 blob，共 128 MB；coding-agent 82 个提交、1,206 个 blob，共 13 MB。
- 全部提交信息。

比对对象有两类：

- 真实密钥：两个仓库 `.env` 里名字带 KEY/TOKEN/SECRET 的值，共 1 个。它只在内存里比对，不会写到任何地方。
- 通用模式：`sk-` 开头（左边不能紧挨字母、数字、`_` 或 `-`），以及 `Bearer <token>`。命中只记录前 7 个字符和长度。

结果：

- 真实密钥在两个仓库的当前文件、历史和提交信息里都是 0 处。`.env` 在两个仓库里都从未被跟踪，历史里没有任何提交涉及它。
- 通用模式只命中 2 个，都在 `tests/test_fix2_redaction.py` 里，是这次脱敏测试用的假密钥（`sk-fixt…`、`sk-proj…`），当前文件和历史各 1 处。coding-agent 没有命中。
- 如果不加左边界，会多出几十个误报：`task-<16 位十六进制>` 形式的任务 id（A5 预检证据和 `configs/a5/*.json` 里有很多）、`mask-then-summary` 这类标签、CSS 里的 `task-list`，它们都含有子串 `sk-`。

两次扫描分别用了 220 秒和 21 秒，第二次是文件缓存热了以后。

没有覆盖的部分：没进 git 的运行目录（harness `runs/`、本地产物目录等）。一开始的扫描对象是这些目录，但磁盘读取很慢，跑了两个多小时、读了二十多 GB 还没结束，就停掉了，没有得到结果。这些目录里的命令都在 `env -i` 下运行，key 进入事件和日志的唯一已知途径就是 P3.1 修复的服务商错误文本，但这只是推断，不是扫描结论。

## P3.2：Docker 超时后容器里的进程

**是否在真实 Docker 上验证过：是。** Docker Desktop 引擎 29.8.2，context `desktop-linux`。

### 查明的情况

- 从 `517874f`（2026-09-25）起，Docker 后端在 `tool_timeout` 和 `user_cancelled` 时保留容器：模型要拿到超时结果，运行结束前还要导出工作区。`_settle_exec` 这时只杀宿主机上的 `docker exec` 客户端。A5、A6-R、CR-2 的冻结 tag 都包含这个提交。三者都是通过 `scripts/run_swe_task.py` 的 `BaselineImageBackend` 运行的，它继承 `DockerExecutionBackend`，没有覆盖 `execute`。
- 探针（命令 fork 出一个 `setsid` 并忽略 SIGTERM 的子进程，然后超时或取消）显示，结束后容器还在，原命令进程和 `setsid` 子进程都还在跑：

  ```text
  {"mode": "timeout", "status": "timed_out", "closed": false, "processes": [[1, "... time.sleep(604800)"], [19, "... import os,signal,time,pathlib ..."], [25, "... import os,signal,time,pathlib ..."], ...]}
  {"mode": "cancel", "status": "cancelled", "closed": false, "processes": [[1, ...], [14, "... import os,signal,time,pathlib ..."], [25, "... import os,signal,time,pathlib ..."], ...]}
  ```

  它们要到运行结束、容器被销毁时才停。
- 现有测试 `test_destroy_includes_setsid_descendants` 没有覆盖这种情况。它断言超时后 `b.closed`，这和 `517874f` 以来的行为相反；它最后一次修改是 `771997a`（09-24），比那个提交早。因为它是选择性测试，默认测试集从没跑过它。改前在真实 Docker 上 3 种模式都失败在这一行：

  ```text
  >           assert b.closed and b.cleanup_ok
  E           assert (False)
  FAILED tests/test_docker_execution.py::test_destroy_includes_setsid_descendants[cancel]
  FAILED tests/test_docker_execution.py::test_destroy_includes_setsid_descendants[timeout]
  FAILED tests/test_docker_execution.py::test_destroy_includes_setsid_descendants[task_cancel]
  3 failed, 12 passed in 97.81s
  ```

- 历史影响面：至少有一次工具超时的运行目录（含退役目录），A5 8 个、A6-R 5 个、CR-2 40 个。这些运行里超时的命令很可能一直在容器里跑到运行结束，和后面的命令争 CPU，也可能继续写工作区。逐个运行的情况没有核实。

### 修复（默认关闭）

新配置项 `policy.command.docker_reap_on_timeout`。打开后：

- 每条命令的 `env -i` 环境里加一个随机的 `HARNESS_EXEC_ID`。命令能看到这个变量，所以做成默认关闭。
- 超时或取消后，容器保留，同时在容器里运行一段 Python，扫描 `/proc/*/environ`，把带这个标记的进程全部 SIGKILL，反复扫描直到没有为止（最多 50 轮）。`setsid` 子孙进程继承环境变量，所以也能找到。
- 如果回收失败（Docker 出错，或 50 轮后还有），直接销毁容器，事件记录 `exec_reap_failed`。成功时记录 `exec_reaped` 和杀掉的进程数。
- `scripts/run_swe_task.py` 从 profile 读这个选项传给 `BaselineImageBackend`。工作区传输（`src/workspace/docker_transport.py`）是另一套实现，配置了这个选项会直接报错，不会静默不生效。

改后，同一个探针里原命令进程消失，只剩 pid 1 和一个空命令行的条目（见"新问题"第 3 条）。真实 Docker 上 `test_docker_execution.py` 加 `test_fix2_docker_reap.py` 共 26 passed：

- 旧测试改成和现行设计一致：超时或取消后容器保留，显式销毁后容器和所有子孙进程都不在了。3 种模式通过。
- 新的选择性测试 `test_reap_on_timeout_kills_setsid_descendants`：3 种模式下，带标记的进程都被杀掉（`killed ≥ 2`），容器里已经没有那段代码在跑，之后的命令还能正常执行。3 种模式通过。
- `tests/test_fix2_docker_reap.py`（不需要 Docker，8 个）：默认不加标记、不回收；超时和取消时回收并保留容器；回收失败就销毁容器；正常结束的命令不回收，每条命令的标记不同；选项只接受布尔值；配置能传到后端，打开后哈希和指纹会变；工作区 profile 拒绝这个选项。

## P3.3：`local.py` 收尾不被取消打断

`finally` 里的回收挪进内部函数 `reclaim()`，用 `asyncio.ensure_future` 启动，再在循环里 `await asyncio.shield(...)`；收尾没完成时再来的取消会被吞掉，完成后才重新抛出。这和 F2 给 Docker 后端用的写法相同。

新测试 `tests/test_fix2_local_cleanup_cancel.py` 用一个忽略 `terminate()`、只有 `kill()` 才结束的假进程：在回收途中取消一次、连续取消三次，进程都会被杀掉（返回码 -9），结果是 `TIMED_OUT`，清理状态是 `cleaned`。改前 2 个都失败，`CancelledError` 从回收里漏出去，进程没被杀；改后连同相关测试 121 passed, 23 skipped。

## P3.4：`test_workspace_completion` 不稳定

**原因。** 每次快照的时限是 20 秒（schema 允许的最大值），从写日志之前就开始计时。机器负载很高、事件循环被卡住超过 20 秒时，那一次快照会以 `deadline` 失败，正好产生原来观察到的两种失败：

- 第 4 次快照前卡住：执行器 4 次、模型 7 次、状态 passed，但缺 `final_slot_reserved` 事件（第 280 行的断言失败）。
- 第 5 次（最后一次）快照前卡住：状态变成 `not_run`。

**复现。** 自然条件下没有复现：单独跑 15 次、按原顺序跑同一组文件、8 个随机种子各在进程内跑 25 次，全部通过。原来那次失败发生在大约 9 倍的减速下。把时限调到 1 秒、在第 2–5 次快照前注入 1.2 秒的阻塞，复现了上面两种形态：

```text
{"stall_before_capture": 4, "checks": {"277 executor==4 and model==7": true, "278 status==passed": true, "280 final_slot_reserved event": false}, "capture_failed": ["deadline"], ...}
{"stall_before_capture": 5, "checks": {"277 executor==4 and model==7": false, "278 status==passed": false, "280 final_slot_reserved event": true}, "capture_failed": ["deadline"], "status": "not_run"}
```

**修复。** 测试的 `setup()` 在构建后把控制器的 `capture_timeout_seconds` 设为 150。schema 上限是 20，所以只能在构建之后改控制器，生产配置不受影响。该文件 31 passed。

## P3.5：`describe_a6_stops` 识别新标签

coding-agent `scripts/describe_a6_stops.py` 增加人民币上限停机（`budget_exhausted`、`cny_hard_cap`、`cny_call_cap`）和服务商错误停机（`provider_network`、`provider_incomplete`、`model_call_error`、`provider_http_*`）的识别。数据里出现这些标签时，才追加一节列出对应运行。用现有数据重新生成 `A6-R-STOP-DESCRIPTION.zh-CN.md`，结果和已提交版本逐字节相同。测试改前 1 failed, 2 passed，改后 3 passed；coding-agent 完整测试集改前 363 passed，改后 365 passed（都是 1 skipped, 3 deselected）。

## P4：测试数量

- harness 的 `README.md`、`README.zh-CN.md`、`docs/INDEX.md`、`docs/PROJECT-OVERVIEW.zh-CN.md`（`697daaf`）：`108baa6` 上 `pytest --collect-only` 收集到 1424 个测试，来自 110 个 git 跟踪的测试模块，排除那 4 项后是 1420 个。实测 1384 通过、40 跳过（不设 `HARNESS_DOCKER_TESTS`，本机有私有数据所以那 4 项能通过）。没有 Docker 守护进程或 py3.6 镜像时是 1382 通过、42 跳过。
- coding-agent 的 `README.md` 和 `docs/INDEX.md`（`e0d3b43`）：`c5c6acf` 的干净克隆 359 passed, 7 skipped, 3 deselected，共 369 个测试；原开发机器上有同级的 coding-harness 和本地数据，结果是 365 passed, 1 skipped。

## 新问题（只记录，没有处理）

1. `tests/test_llm_streaming.py::TestLoopbackStreaming::test_first_chunk_observed_before_rest_released` 依赖时序，在高负载下按原顺序跑时失败过一次（`TimeoutError`），单独跑能通过。
2. 工作区传输 `src/workspace/docker_transport.py` 超时后同样只杀宿主机上的 CLI。容器里的进程会不会留下，没有在真实 Docker 上验证。新选项在这条路径上会报错，不会生效。
3. 回收后，被杀的 `setsid` 子进程过继给 pid 1。容器入口是一个只会 sleep 的 Python，不回收子进程，所以会留下僵尸进程。它不占 CPU 和内存，但会占一个 pid 名额（默认上限 128），要到容器销毁才释放。给容器加 `--init` 能解决，但会改变容器配置，这次没有做。
4. 进程如果在命令里自己清空环境（比如嵌套一层 `env -i`），就没有标记，回收不到。
5. Docker 后端的 capabilities 写着 `tree_reclaim_guaranteed=True`。对超时和取消的命令来说，这只在会话结束销毁容器时才成立。
6. A6-R 事件里，`run_command` 超时的工具结果写的是 `"error": "user_cancelled"`、`"error_kind": "tool_timeout"`，两个字段的标签不一致。
7. CR-2 也有 1 次 `context_overflow`（`pytest-dev__pytest-8399/n/r2`，估算 92,472，前一次真实 `prompt_tokens` 23,897），原因和 A6-R 的 4 次相同。这次的勘误只覆盖 A6-R，CR-2 的这一次没有写进任何勘误。
8. 没进 git 的运行产物（harness `runs/`、本地产物目录）没有完成密钥扫描。修复前的运行里，服务商错误文本是原样落盘的；如果某次错误回显了 key，它会留在这些目录里。
