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
