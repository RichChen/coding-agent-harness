# CHECKPOINT CR-2c：全量运行在第 18 个新槽位后按冻结规则停止

日期：2026-10-27。执行：Cursor。依据：[AUDIT-CR-2b](AUDIT-CR-2b.zh-CN.md)、[CR-2 合同](CR-2-CONTRACT.zh-CN.md)第 8 节、[CR-2-FREEZE.md](CR-2-FREEZE.md)第 11 节。

**结论：全量运行没有跑完，停下等审计。**

- r2 修订做完：代码 `9b83aff`，第 11 节和 tag `cr2-freeze-r2` 在 `b902b93`。全量 pytest 850 passed、22 skipped、0 failed。
- 全量运行在 `cr2-freeze-r2` 上启动。模型名记录修好了：新跑的 20 次运行都记到 `deepseek-flash`。
- 跑完 18 个新槽位后，runner 按 A5/A6 的冻结停止条件停下：`infra_error 3 of 28 completed runs`。
  - 条件是 infra_error 不少于 3 次，并且超过已完成运行的 10%；这里 3/28 = 10.7%。
  - 3 次 infra_error 都来自同一个槽位 `scikit-learn__scikit-learn-12585/n/r4`：最初一次加 2 次退役重跑，都是候选快照失败。
- 按要求没有改代码，没有自行续跑，没有做分析。

## 1. r2 修订

| 提交 | 内容 |
|---|---|
| `9b83aff` | `scripts/run_cr2.py`、`tests/test_run_cr2.py`（合同 8.1、8.2）。**r2 冻结代码提交** |
| `b902b93`，tag `cr2-freeze-r2` | `CR-2-FREEZE.md` 第 11 节，没有代码改动 |

- 模型名钩子：响应为 gzip 或 deflate 时，另用 `zlib.decompressobj` 解压一份副本来扫描，原始字节照常透传。不改请求头，不改 `src/llm`。
- 前 3 个真实槽位都没有记到模型名时停止启动新槽位。报告新增 `returned_model_slots`，按模型名列出槽位，并列出缺失模型名的槽位。
- 测试：`test_run_cr2.py` 由 22 个增加到 32 个，覆盖：
  - 未压缩、gzip、deflate、原始 deflate 四种响应，流式和非流式都能记到模型名，透传的字节不变；
  - `br` 编码和损坏的正文原样透传；
  - 402 错误照常抛出；
  - 第 3 个槽位后的停止和继续、mock 和短运行不做检查、坏文件按缺失处理。
- 全量 pytest（`9b83aff`）850 passed、22 skipped、0 failed，日志 `runs/cr2-logs/pytest-r2.out`。preflight 通过。
- `git diff --name-only cr2-freeze-r1 cr2-freeze-r2` 只有这两个文件、第 11 节，以及项目作者的审计、合同和上一轮检查点。旧 tag `cr2-freeze`（`f911103`）、`cr2-freeze-r1`（`cd1ba35`）都没有动。

## 2. 运行经过

**开跑前**

- Docker Desktop 29.8.2 在线，没有残留容器。`runs/cr2-main` 里只有 canary 的 8 个目录，都有 summary。HEAD 正好是 tag `cr2-freeze-r2`。
- **系统待机实际没有关闭**：`powercfg` 显示待机超时为接电源 7200 秒、电池 900 秒，与 A6 记录的原值相同。我按 A6 的做法把两项改成 0 再开跑；运行停止后，已经恢复为 7200 / 900 秒。续跑前需要重新关闭。

**启动**

2026-10-27 **01:52** 启动，用 `Start-Process` 起一个独立的后台进程，不依赖当前终端。命令：

```
set HF_HUB_OFFLINE=1&& .venv\Scripts\python.exe -u scripts\run_cr2.py --authorized 5,800 > runs\cr2-logs\main.out 2>&1
```

`-u` 只是让日志不缓冲，不是代码改动。启动时间记录在 `runs/cr2-logs/main-wall.txt`。

**第一次运行（01:52 – 02:14）**

- 8 个 canary 槽位按已完成跳过。
- 新跑 18 个槽位，加上 2 次退役运行，共 20 次运行。
- 前 3 个完成的槽位都记到了模型名，第 3 个槽位后的检查通过。
- 02:13:40，`scikit-learn__scikit-learn-12585/n/r4` 第 3 次 infra_error。退役次数已经用完，按规则保留为 `infra_kept`，同时触发停止条件，不再启动新槽位。
- 正在运行的两个槽位在 02:14:33 和 02:14:49 跑完，然后进程退出。最后一条模型结算的时间是 02:14:39。

**另一次启动（02:14:53 – 02:15:03），来源不明**

第一次运行退出后约 4 秒，又有一次同样的命令启动了。

- 它的报告覆盖了 `runs/cr2-main/main-report.json` 和 `runs/cr2-logs/main.out`：`started_at` 02:14:53，`started` 0，`skipped_finished` 26，`halt_reason` 相同。
- 开始时的停止检查读到已有的 3 次 infra_error，立即停止，没有启动任何槽位，没有花费。
- 我只启动过一次。`cmd /c` 只运行一次；`run_cr2.py` 的 `main()` 也只调用一次 `run_slots`。
- IDE 终端、计划任务、当前进程里都找不到它的来源。

后果：第一次运行的标准输出被覆盖，丢了。不过每个槽位的 summary、events、模型名文件都还在，本检查点的数据都取自这些文件。另外，这次启动也说明：**按现在的冻结规则原样续跑，会立刻再停**，因为续跑开始时会先读到已有的 3 次 infra_error。

## 3. 摘要（按运行目录统计，不含 canary）

**按组的完成数和停止原因**

| 组 | 新完成槽位 | 停止原因 |
|---|---|---|
| N | 5 | max_model_calls 4、candidate_snapshot_failed 1（保留的 infra） |
| P | 4 | max_model_calls 4 |
| T | 8 | completed 5、max_model_calls 3 |
| O | 1 | max_model_calls 1 |
| 合计 | 18 | completed 5、max_model_calls 12、infra 1 |

加上 canary，已有 summary 的槽位是 26 个，还剩 1094 个没有启动。

**infra**

- 退役：`scikit-learn__scikit-learn-12585/n/r4.infra1`、`.infra2`。
- 保留：`scikit-learn__scikit-learn-12585/n/r4`。

**注入核对**：18 个新槽位全部在运行中通过（runner 每个槽位跑完都检查，没有因注入停下）。harness 工具错误 0。

**返回的模型名**

- 新跑的 20 次运行（18 个槽位加 2 次退役运行）全部是 `["deepseek-flash"]`，没有缺失，没有第二个名字。
- 注意：DeepSeek 返回的 `model` 字段就是别名 `deepseek-flash`，不是价格页上的版本名 `DeepSeek-V4.1-Flash-1017`。所以这项记录只有在返回的字符串本身变化时才能发现版本变化，服务端在同一个别名下换版本是看不出来的。
- canary 的 8 个槽位按合同 8.1 记为缺失。

## 4. 费用

用 `scripts/cr2_cost.py --report runs/cr2-main/main-report.json` 统计，结果在 `runs/cr2-logs/main-cost-halt1.json`。

| 范围 | 运行数 | 结算数 | 账本（元） | 缓存口径，高峰价（元） |
|---|---|---|---|---|
| canary（CR-2b） | 8 | 203 | 3.049 | 1.397 |
| N | 5 | 167 | 2.578 | 1.189 |
| P | 4 | 160 | 2.287 | 0.932 |
| T | 8 | 221 | 2.903 | 1.019 |
| O | 1 | 40 | 0.590 | 0.154 |
| 退役运行（N） | 2 | 22 | 0.168 | 0.066 |
| **合计** | 28 | 813 | **11.575** | **4.757** |

- 本次新增：账本 8.526 元，缓存口径 3.360 元。
- 全部 813 次结算都带命中/未命中拆分，缓存命中率 63.8%，账本是缓存口径的 2.43 倍。
- **高峰/非高峰**：本次新增的 610 次结算，时间戳在 2026-10-27 01:53:53 至 02:14:39（北京时间，周二），都在非高峰时段，高峰时段 0 次。按非高峰价，本次新增约 1.68 元（缓存口径的一半）。
- 账本总额 11.575 元，远低于 800 元上限。

## 5. 停止原因的细节：`scikit-learn__scikit-learn-12585/n/r4`

| 运行 | 模型调用 | 账本（元） | `snapshot_error` |
|---|---|---|---|
| `r4.infra1` | 10 | 0.072 | `DockerUnavailable: Docker control output quota exceeded` |
| `r4.infra2` | 12 | 0.096 | `ValueError: delta_too_large` |
| `r4`（保留） | 7 | 0.043 | `ValueError: delta_too_large` |

三次的过程几乎一样：

1. 模型复现 issue，修改 `sklearn/base.py`；
2. 运行 `python -m pytest sklearn/tests/test_base.py sklearn/tests/test_common.py -q -x`，这条命令每次都触发工具超时（`tool_timeout`）；
3. 之后再跑几条较小的测试，然后结束；
4. 候选快照失败。

`delta_too_large` 是候选快照导出的大小限制：超过 200 个文件或 4MB（`__pycache__`、`*.pyc`、`.pytest_cache` 等已经排除在外）。我推测是 `test_common.py` 跑到一半被中止，在工作区留下了大量文件，但容器已经销毁，**没有核实**到底是哪些文件。

**这和冻结规则的关系**：

- CR-2-FREEZE 第 6 节沿用 A6：summary 为 `infra_error` 就退役重跑，每个槽位最多 2 次。A5 的 G3 细则把"候选快照失败（`snapshot_error` 非空）"记为 infra_error。A5 说 `delta_too_large`"不重试"，指的是单次 Docker 控制调用的短重试，不是槽位重跑。所以 runner 的处理符合冻结规则。
- 但这类失败是 agent 自己的操作造成的，重跑大概率会重复：这次三次里两次是同一个错误。在 CHECKPOINT-6 里，这一点已经作为可能的情况提过（"`delta_too_large` 和 OOM 可能是 agent 自己造成的"）。
- 停止条件把退役运行也计入 infra 次数，在运行早期分母很小，一个槽位用满重试就能触发 10%。
- 这道题在 CR-2 里共有 20 个槽位（4 组 × 5 次）。canary 里 `scikit-learn__scikit-learn-12585/t/r4` 也有一条命令超时，但快照成功了，所以这种情况在同一题的其他槽位上可能再次出现。

## 6. 需要审计决定的事

1. **怎么处理这次停止。** 按现在的规则原样续跑会立刻再停（02:14:53 那次启动已经证明）。可选的方向，都要改规则或代码，需要一次冻结修订：
   - 把 agent 造成的 `delta_too_large` 记为任务结果（按没有候选补丁的失败处理），不当作 infra，也不重跑；
   - 或者停止条件只统计各槽位最终运行的 infra，或者只在完成数达到一定数量以后才检查比例；
   - 或者保持规则不变，人工确认后从这里继续，这要有一个明确的放行方式。

   我没有做任何改动。
2. **`scikit-learn__scikit-learn-12585/n/r4` 这个保留的 infra 槽位**在分析里怎么算。
3. **02:14:53 那次来源不明的启动。** 如果是项目作者或其他会话启动的，请告诉我，以免以后续跑时两个进程同时写同一个目录。runner 遇到没有 summary 的目录会拒绝运行，可以防止覆盖，但不能防止两个进程同时启动。
4. 续跑前需要**重新关闭系统待机**（我已经恢复为原值），并确认 Docker 在线。

## 7. 文件

- 运行目录 `runs/cr2-main/`，日志 `runs/cr2-logs/`：都不入库。
  - `main-wall.txt`：第一次启动的时间；
  - `main.out`、`main-report.json`：已被来源不明的那次启动覆盖；
  - `main-cost-halt1.json`：费用；
  - `pytest-r2.out`：全量 pytest。
- 本检查点。

按要求停下，等审计。

## 续跑

日期：2026-10-29。依据：[AUDIT-CR-2c-halt1](AUDIT-CR-2c-halt1.zh-CN.md)、[CR-2 合同](CR-2-CONTRACT.zh-CN.md)第 9 节、[CR-2-FREEZE.md](CR-2-FREEZE.md)第 12 节。上文保持原样，本节只追加。

**结论：1120 个槽位全部跑完，runner 没有停止（`halt_reason` 为 null），已写出 `docs/retrieval/cr2-runs.json`。按要求没有做分析。** 有两件事需要审计：

- 6 个槽位的最终运行仍带重跑原因，`analyze_cr2.py` 会拒绝读取这份文件，见第 7 小节第 1 条；
- runner 正常退出后，又出现一次来源不明的同命令启动。它没有启动槽位，但覆盖了日志和报告，见第 2 小节。

### 1. 9.1 和 r3

- **9.1 重放**：脚本 `scripts/cr2_replay_snapshot.py`，提交 `a37002c`。在干净容器里按顺序重放 `scikit-learn__scikit-learn-12585/n/r4` 的工具调用，复现了 `ValueError: delta_too_large`。
  - 改动共 6 个文件、4,056,963 字节。其中 5 个是 agent 跑 `test_common.py::test_configure` 时重新生成的 Cython 源文件，1 个是 agent 修改的 `sklearn/base.py`。
  - harness 没有往工作区写任何文件。
  - 判定为 **agent 造成**，证据详见 FREEZE 12.1。
- **r3 修订**：代码提交 `231dc9f`，只改了 `run_cr2.py`、`analyze_cr2.py` 和它们的测试。FREEZE 第 12 节和 tag `cr2-freeze-r3` 在 `0d0ac45`。
  - 全量 pytest 在 `231dc9f` 和 tag 上各跑一次，都是 862 passed、22 skipped、0 failed。preflight 通过。
  - 旧 tag 都没有动。
  - 合同第 9 节在仓库里的文本是乱码，按 GBK 再按 UTF-8 还原后阅读，没有改动合同文件。

### 2. 开跑前和运行经过

- **开跑前**
  - HEAD 正好是 `cr2-freeze-r3`（`0d0ac45`）；
  - Docker 29.8.2 在线，没有残留容器；
  - 没有 `run_cr2` 进程，也没有锁文件。
- **系统待机**：开跑前 `powercfg` 显示接电源 7200 秒、电池 900 秒，是上次停止后我恢复的原值。我把两项改为 0，确认接电源为 0 后才开跑；跑完已恢复为 7200 / 900 秒。
- **保留旧文件**：被来源不明的那次启动覆盖的 `main.out` 和 `main-report.json`，先复制为 `runs/cr2-logs/main-halt1-overwritten.out` 和 `runs/cr2-main/main-report-halt1-overwritten.json`。
- **启动**：2026-10-27 **12:21** 启动，命令和上次相同，见第 2 节。runner 的 `started_at` 是 12:21:59，锁文件记录的 PID 是 20556。
  - 整个运行期间只有这一条进程链：`cmd` 启动 venv 的 python 启动器，启动器再启动实际运行的 python。没有再出现第二次启动。
- **结束**：2026-10-29 **07:07** 跑完最后一个槽位（`finished_at` 07:07:57）。07:10:49 写完 `cr2-runs.json` 后进程退出，锁文件已删除。总用时约 42 小时 46 分。
- **又一次来源不明的启动**：07:12:33 又有一次同样的命令启动了。这是它在报告里的 `started_at`，进程本身更早启动，因为要先加载检索模型。
  - 它跑到 07:12:53 结束：`started` 0、`skipped_finished` 1120、`halt_reason` null，没有调用模型，`spent_cny` 仍是 547.394。
  - 运行目录没有变化：退役目录还是 159 个，最终运行还是 1120 个。6 个 `infra_kept` 槽位的退役次数已经用完，没有被再次退役。
  - 它覆盖了 `runs/cr2-logs/main.out`、`runs/cr2-main/main-report.json`，并重写了 `docs/retrieval/cr2-runs.json`。runner 输出的 sha256 是 `bb6171cf…`；文件由同样的 summary 生成，所以内容应当与第一次相同。
  - 单实例锁防不住这种情况：它是在第一次运行退出、锁已经删除之后才启动的。
  - 来源仍然找不到：IDE 终端记录里没有这条命令；这台机器没有开启进程创建审计（Security 4688），事件日志里查不到。
  - 两次都发生在 runner 正常退出后不久，上次是退出后约 4 秒。

  第 3 小节的报告数字，是我在 07:10 进程退出后、被覆盖之前读到的第一次运行的报告。

### 3. `main-report.json` 摘要

| 字段 | 值 |
|---|---|
| `halt_reason` | null |
| `started` / `not_started` / `skipped_finished` | 1094 / 0 / 26 |
| `started_by_group` | N 273、P 274、T 270、O 277 |
| `injection_verified_runs` | 1114 |
| `infra_kept` | 6 |
| `infra_retired` | 159（含上一轮的 2 个） |
| `delta_too_large_by_group` | N 5、P 1、T 3、O 3 |
| `returned_models` | `["deepseek-flash"]` |
| `spent_cny`（账本） | 547.394 |

`skipped_finished` 26 个是 8 个 canary 槽位，加上上一轮跑完的 18 个，其中 `12585/n/r4` 按新规则算作已完成。

`injection_verified_runs` 比 1120 少 6 个，就是 6 个 `infra_kept` 槽位：runner 跳过还需要重跑的运行，不做注入核对。我用同一个冻结函数 `injection_problem` 离线核对了这 6 个，全部通过，没有调用模型。

harness 工具错误：全部 1279 次运行都是 0。

### 4. 按组统计（最终运行，含 canary 和上一轮）

| 组 | 槽位 | 本次新跑 | 停止原因 | 退役 | 保留的 infra |
|---|---|---|---|---|---|
| N | 280 | 273 | max_model_calls 163、completed 104、step_deadline 5、candidate_snapshot_failed 5、model_parse_error 2、context_overflow 1 | 41（含上一轮 2 个） | 2 |
| P | 280 | 274 | max_model_calls 161、completed 114、step_deadline 4、candidate_snapshot_failed 1 | 39 | 0 |
| T | 280 | 270 | max_model_calls 152、completed 112、step_deadline 10、candidate_snapshot_failed 3、model_parse_error 3 | 39 | 2 |
| O | 280 | 277 | max_model_calls 160、completed 106、step_deadline 11、candidate_snapshot_failed 3 | 40 | 2 |
| 合计 | 1120 | 1094 | max_model_calls 636、completed 436、step_deadline 30、candidate_snapshot_failed 12、model_parse_error 5、context_overflow 1 | 159 | 6 |

**`delta_too_large`（r3 改判的任务结果）**：共 12 个槽位。在 `cr2-runs.json` 里都记为 `status: "delta_too_large"`、`solved: false`、`no_candidate: true`，对应上表里 `candidate_snapshot_failed` 的 12 个。

| 组 | 数量 | 槽位 |
|---|---|---|
| N | 5 | `scikit-learn__scikit-learn-12585/n/r1`–`r5` |
| P | 1 | `12585/p/r3` |
| T | 3 | `12585/t/r1`、`12585/t/r5`、`scikit-learn__scikit-learn-10908/t/r4` |
| O | 3 | `12585/o/r1`、`12585/o/r2`、`12585/o/r5` |

`12585` 的 20 个槽位里有 11 个没有候选补丁。

另有 2 个 T 组槽位的 status 是 `delta_lost`：`pytest-dev__pytest-7490/t/r1` 和 `sympy__sympy-17655/t/r4`。这是 `run_swe_task.py` 原有的任务结果状态，含义是用过编辑工具但最终改动为空。它们没有快照错误，不是 infra。

**退役的 159 次运行，按原因**

| 原因 | 次数 |
|---|---|
| 模型服务商网络失败（`provider network`） | 103 |
| `candidate snapshot failed: container_gone_before_snapshot` | 25 |
| `container cleanup failed` | 24 |
| `candidate snapshot failed: DockerUnavailable: Docker snapshot failed after 3 retries …` | 3 |
| `container start failed: Docker create failed after 3 retries …` | 1 |
| `grader failed before test execution: DockerUnavailable: Docker create failed …` | 1 |
| 上一轮的 `12585/n/r4.infra1`、`.infra2`（r3 后属于 `delta_too_large` 一类） | 2 |

停止条件统计到的 `infra_error` 共 54 次，都在上表的 Docker 类原因里，占全部 1279 次运行的 4.2%，没有达到 10%。Docker 类失败都在重跑后恢复，没有一个被保留。

### 5. 返回的模型名和 system_fingerprint

- **模型名**：本次新跑的 1094 个槽位全部只记到 `deepseek-flash`，没有缺失，也没有第二个名字。上一轮的 18 个槽位也是 `deepseek-flash`；canary 的 8 个按合同 8.1 记为缺失。
- **system_fingerprint**（`returned_fingerprint_slots`，只做报告）：本次新跑的 1094 个槽位都记到了，共出现两个值。

| fingerprint | 槽位 | 只有这个值 | 涉及运行的启动时间（北京时间） | 按组 |
|---|---|---|---|---|
| `e72d3c7f663d3e1383b00b3884cb4501` | 298 | 261 | 10-27 12:22 – 10-27 19:00 | N 81、P 82、T 72、O 63 |
| `bc19ba97cbe016479903222bb1578db6` | 833 | 796 | 10-27 17:55 – 10-29 07:01 | N 207、P 203、T 205、O 218 |

- 37 个槽位两个值都出现了，都是 10-27 17:55 到 19:00 之间启动的运行。也就是说，服务端大约在 10-27 18 点前后切换了 fingerprint，之后没有再变。
- 两段时间里四组的槽位都有，没有集中在某一组。模型名在切换前后都是 `deepseek-flash`，所以服务端是否在同一个别名下换了版本，只能从 fingerprint 看出来。
- 续跑前的 26 个槽位用的是 r2 的文件格式，没有 fingerprint。

### 6. 费用

`scripts/cr2_cost.py --report runs/cr2-main/main-report.json --out runs/cr2-logs/main-cost.json` 统计了 1279 次运行（1120 个最终运行加 159 次退役运行），共 37,718 次结算。全部结算都带缓存命中/未命中拆分，缓存命中率 65.2%，账本是缓存口径的 2.58 倍。按组的数字由 `runs/cr2-logs/resume_stats.py` 统计，结果在 `resume-stats.json`。

| 范围 | 账本（元） | 缓存口径，高峰价（元） |
|---|---|---|
| N 最终运行（280） | 118.205 | 47.979 |
| P 最终运行（280） | 114.569 | 45.137 |
| T 最终运行（280） | 120.700 | 47.708 |
| O 最终运行（280） | 114.560 | 44.496 |
| 退役运行（N 41、P 39、T 39、O 40） | 79.360（N 18.618、P 20.231、T 21.358、O 19.153） | 26.597 |
| **合计** | **547.394** | **211.916** |

- 本次续跑新增：账本 535.819 元（N 132.677、P 131.225、T 138.918、O 132.999，含退役运行），缓存口径 207.159 元。
- 退役运行的费用占账本总额的 14.5%。
- 账本总额 547.394 元，低于 800 元上限。

**高峰/非高峰**（按 `model.settled` 时间戳，北京时间；高峰是工作日 9–12 点和 14–18 点，10-27 至 10-29 都是工作日，没有节假日。只做报告）：

| 范围 | 时段 | 结算次数 | 缓存口径，高峰价（元） | 按时段价估算（元） |
|---|---|---|---|---|
| 本次续跑 | 高峰 | 11,023 | 63.854 | 63.854 |
| 本次续跑 | 非高峰 | 25,882 | 143.305 | 71.653（半价） |
| 本次续跑 | 合计 | 36,905 | 207.159 | **约 135.51** |
| CR-2 全部（含 canary 和上一轮） | 高峰 / 非高峰 | 11,023 / 26,695 | 63.854 / 148.062 | **约 137.89** |

续跑里的高峰结算有两段：10-27 14–18 点和 10-28 9–12 点、14–18 点。canary 和上一轮的结算都在非高峰。按时段价估算的前提是非高峰所有单价都是高峰价的一半；实际扣费以 DeepSeek 账单为准。

### 7. 遇到的问题

1. **6 个保留的槽位带着重跑原因，`analyze_cr2.py` 会拒绝运行。**
   - 涉及的槽位：`pytest-dev__pytest-7205/o/r2`、`scikit-learn__scikit-learn-10908/o/r1`、`sphinx-doc__sphinx-10323/n/r1`、`sphinx-doc__sphinx-9461/n/r2`、`sphinx-doc__sphinx-9461/t/r3`、`sympy__sympy-11618/t/r4`。
   - 这 6 个槽位的三次运行里都出现了模型服务商的网络失败（客户端重试后仍失败的结算）。它们都跑满了 40 次模型调用，status 是正常的 fail 或 partial，不是 `infra_error`。
   - 退役次数用完后，runner 按冻结规则保留，记为 `infra_kept`。它们在 `cr2-runs.json` 里的 `rerun_reason` 是 `provider network None`。
   - 而 `analyze_cr2.py` 的 `main()` 遇到任何 `rerun_reason` 都会退出，提示 "runs file still has slots whose final run needs a rerun"。
   - 这 6 个运行的注入核对已离线通过（见第 3 小节）。怎么处理需要审计决定：再给这几个槽位重跑，还是把带中途网络失败的运行按任务结果计入。我没有改动。
2. **模型服务商网络失败很多。**
   - 共 103 次退役，加上上面 6 个槽位。
   - 全部运行的 `events.jsonl` 里，客户端重试后仍失败的结算有 187 次：`network` 161、`DeadlineExceeded` 21、`timeout` 5。只有 `network` 会触发重跑。
   - events 只记录错误类型，不记录具体信息。具体信息原本在 `main.out` 里，已被第 2 小节那次启动覆盖。10-28 18:25 我读过一次 `main.out` 的末尾，当时看到了三种：
     - "Server disconnected without sending a response"；
     - 空的网络错误信息；
     - DNS 解析失败（`getaddrinfo failed`）。
   - 按退役运行的结束时间看，失败分布在整个运行期间，两天的 15–16 点最多。
   - 这些失败都按冻结规则重跑，不计入 infra 停止条件。
3. **runner 退出后又出现一次来源不明的启动**，覆盖了 `main.out` 和 `main-report.json`，见第 2 小节。没有造成费用或数据变化，但这已经是第二次了，来源仍然不明。
4. **Docker 类 infra 54 次**：容器在快照前消失 25 次，容器清理失败 24 次，其他 5 次。全部在重跑后恢复。
5. **`delta_too_large` 集中在 `scikit-learn__scikit-learn-12585`**：20 个槽位里 11 个没有候选补丁。这与 9.1 重放的结论一致：这道题的仓库测试会重新生成 Cython 源文件。
6. **system_fingerprint 在运行中切换了一次**（10-27 18 点前后），见第 5 小节。只做报告。
7. **运行期间**：10-28 晚上，系统的进程查询（WMI）和递归列目录一度很慢，但 runner 没有受影响。我在 10-28 有一次长时间等待被中断，对运行也没有影响。

### 8. 文件

- `docs/retrieval/cr2-runs.json`：随本节提交，1120 行，sha256 `bb6171cfe2cdf862135c4c41a22be2821157acc63c7c8c4899e8f961a1d7cabc`，与 runner 输出一致。
- 运行目录 `runs/cr2-main/`（包括 `main-report.json`）和日志目录 `runs/cr2-logs/` 不入库：
  - `main.out`、`main-report.json`：已被 07:12 那次来源不明的启动覆盖，现在是那次空运行的输出；
  - `main-wall.txt`：两次的开始和结束时间；
  - `main-cost.json`：费用；
  - `resume_stats.py`、`resume-stats.json`：按组统计和高峰拆分；
  - `failed_settled.py`：按错误类型统计失败的结算；
  - `replay-scikit-learn__scikit-learn-12585-n-r4.json`、`replay-12585-n-r4.out`：9.1 重放；
  - `main-halt1-overwritten.out`、`runs/cr2-main/main-report-halt1-overwritten.json`：上一轮被覆盖文件的副本。

按要求停下，不做分析，等审计。
