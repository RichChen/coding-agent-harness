# CHECKPOINT CR-2d：分析

日期：2026-10-29。执行：Cursor。依据：[AUDIT-CR-2c](AUDIT-CR-2c.zh-CN.md)、[CR-2 合同](CR-2-CONTRACT.zh-CN.md)第 10 节。

**结论：CR-2d 做完了，结果在 [CR-2-RESULTS.zh-CN.md](CR-2-RESULTS.zh-CN.md)。** runs 文件核对一致，r4 和 S1–S4 按第 10 节实现，分析在 r4 提交上运行，全量 pytest 871 passed、22 skipped、0 failed。没有调用模型，没有运行 `run_cr2.py` 的正式模式。

- 主指标：P − N −0.02 步 [−1.87, 2.05]，T − N +0.10 步 [−0.97, 1.34]，都未检出差异。
- T 组 280 次运行一次都没有调用 `retrieve_code`。
- S1–S4 都没有改变任何一项区间是否跨 0。

## 0. 开工时发现：合同文件没有恢复

启动文字说合同第 9 节已经恢复成原文，但仓库里的 `CR-2-CONTRACT.zh-CN.md`（HEAD 和工作区相同）第 9 节仍是乱码，也没有第 10 节。恢复后的第 9 节和新的第 10 节在一个未入库的临时文件 `docs/retrieval/cr2-contract-tail.tmp.md` 里（07:47 写入，比审计文件早 16 秒），看起来是还没拼回合同。

处理：

- 先核对临时文件里的第 9 节与乱码是同一段文字：把乱码按 GBK 编码、再按 UTF-8 解码。GBK 写入时丢了一部分字节，还原不完整，但能还原出来的部分与临时文件逐字一致；临时文件只多了开头一句恢复说明。
- 用 Python 按 UTF-8 把合同中从 `## 9.` 开始的部分换成临时文件的内容，前 8 节不动，然后删掉临时文件。换行统一为 LF，与仓库里原来的版本一致。
- 和审计文件一起提交：`d126d26`。

第 10 节我是从这份拼回后的合同里读的。

## 1. 10.3 runs 文件核对

- 用 runner 已有的 `write_runs()`（只读各槽位的 summary，不跑槽位）重新生成，写到 `runs/cr2-logs/cr2-runs.regen.json`。没有用 `--runs-file` 命令行，因为它会写回 `docs/retrieval/cr2-runs.json`。
- 结果：1120 行，与 `docs/retrieval/cr2-runs.json` **逐字节相同**，sha256 都是 `bb6171cfe2cdf862135c4c41a22be2821157acc63c7c8c4899e8f961a1d7cabc`。入库版本与工作区也相同。
- 07:12 那次来源不明的启动重写过这个文件，但内容与从运行目录重新生成的一致。

## 2. 10.1 r4

提交 `fcfea74`，只改了 `scripts/analyze_cr2.py` 和 `tests/test_analyze_cr2.py`。这次提交在运行分析之前，提交时我还没有看任何结果。

- `main()`：最终运行带 `rerun_reason` 时，只有在它不在 `infra_kept` 名单里才退出，退出信息列出这些槽位。名单先从 runs 文件的 `infra_kept` 字段读；没有这个字段时从 `--report` 指定的报告读。
- `cr2-runs.json` 没有 `infra_kept` 字段，所以用 `--report runs/cr2-main/main-report.json`。这份报告是 07:12 那次空运行写的（被覆盖过），但它的 `infra_kept` 与 CHECKPOINT-CR-2c 续跑第 7 小节列出的 6 个槽位完全相同，也正好是 runs 文件里带 `rerun_reason` 的 6 个。那次空运行没有再退役这些槽位，因为它们的退役次数已经用完。
- `per_run` 每行加了 `run_dir`、`infra_kept`、`system_fingerprints` 三个字段，报告加了 `infra_kept` 名单。主分析的定义没有改：最终运行、第 4 节和 7.3 的指标、种子、bootstrap。

## 3. 10.2 S1–S4 的实现，以及怎样处理定义里没写清楚的地方

每一项都用主分析的方法（`task_means` 加 `paired`）重算 `first_gold_step` 和 `solved` 的 P − N、T − N、O − N。某组在某题上没有剩下的运行时，`paired` 只取两组都有数据的题，并报告 `n_tasks`。

- **S1**：去掉 `infra_kept` 标记的 6 个运行。
- **S2**：去掉 `scikit-learn__scikit-learn-12585` 的全部运行。
- **S3**：只保留 `system_fingerprints` 恰好等于 `["bc19ba97cbe016479903222bb1578db6"]` 的运行。两个值都有的 37 个、没有记录的 26 个（canary 和续跑前的 r2 格式）都去掉。剩 796 个运行，每组仍覆盖 56 道题。
- **S4**：每个槽位按 `r<k>.infra1`、`r<k>.infra2`……、最终 `r<k>` 的顺序，取第一个 status 不是 `infra_error` 的运行。退役运行的行按 `write_runs()` 同样的规则从 summary 生成，步数和 T 组指标照常从它自己的 `events.jsonl`、`retrieval-calls.jsonl` 计算。
  - **"status" 用 runner 冻结的 `counted_status`（r3 规则），不用 summary 里原始的 status。** 理由：合同要求"按和最终运行相同的方法"，而最终运行用的就是 `counted_status`。12 个 `delta_too_large` 槽位的最终运行，原始 status 都是 `infra_error`；如果按原始 status 选，这 12 个槽位一个可用的运行都没有，S4 就会顺带去掉它们，这和主分析的规则冲突。
  - 实际影响：90 个槽位改用了退役运行（N 25、P 24、T 21、O 20）。其中只有 2 个因为这条规则选中了 `delta_too_large` 的退役运行：`scikit-learn__scikit-learn-12585/n/r1.infra1` 和 `12585/n/r4.infra1`，它们和对应的最终运行一样按未解出计。没有槽位因为全部尝试都是 `infra_error` 被去掉。
  - 判断逻辑直接 import runner 里冻结的 `counted_status` 和 `agent_snapshot_failure`，没有复制代码。
- 第 10 节的定义都能执行，没有改任何定义。

**单测**（`tests/test_analyze_cr2.py`，由 10 个增加到 19 个）：

- `infra_kept` 名单的三种来源：runs 文件字段、报告文件、都没有；
- 只放行名单里的槽位；`main()` 在没有报告时拒绝、给了报告就放行；
- 退役目录按序号排序（`infra10` 排在 `infra2` 后面），`r10`、`.bak` 之类的目录不算进来；
- S4：网络失败退役但 status 正常的运行被选中；Docker 类 `infra_error` 被跳过；退役的 `delta_too_large` 运行按任务结果计、按未解出计；没有退役运行时用最终运行；全部是 `infra_error` 时去掉这个槽位并列出；
- S1–S3 的过滤和题数报告；`analyze()` 标记 `infra_kept` 并输出 `sensitivity`。

## 4. 运行分析

```
.venv\Scripts\python.exe scripts/analyze_cr2.py --report runs/cr2-main/main-report.json
```

- 在 `fcfea74` 上运行，`HF_HUB_OFFLINE=1`，用时约 30 秒。输出 `docs/retrieval/cr2-analysis.json`，sha256 `f592dcc512c1546ddb686d358908808d8d475708fa77191ac4015e14d8a8a68c`；标准输出保存在 `runs/cr2-logs/analyze-cr2d.out`。
- `complete` 为 true：56 道题 × 4 组 × 5 次，1120 个运行。
- 看到结果以后没有改代码，也没有改任何定义。
- T 组调用率为 0，所以我回到原始文件核实过，确认不是统计口径的问题：280 次运行的 `agent-tools.json` 都含 `retrieve_code`，`events.jsonl` 里没有这个工具的 `tool.intent`，`retrieval-calls.jsonl` 全部为空。

结果文档的写法：区间跨 0 的都写"未检出差异"；O 组只作为上限参照；修复率只描述；T 组调用率为 0 写在结论里。其中有一处要说明：前 10 步触碰比例的 O − N 区间下限算出来是 −1×10⁻¹⁸，实际就是 0，我按"区间包含 0"写成未检出差异。

一处小瑕疵：`cr2-analysis.json` 里的 `report_file` 字段写成了 Windows 路径（`runs\\cr2-main\\main-report.json`），因为命令行传的是相对路径。这个字段只用来记录，不影响任何数字，我没有为它改代码。

## 5. 全量 pytest

- 结果：**871 passed、22 skipped、0 failed**，用时 108 秒，日志 `runs/cr2-logs/pytest-r4.out`。r3 时是 862 个，加上这次新增的 9 个单测。
- 用 `.venv\Scripts\python.exe -m pytest` 直接调用 venv 里的 Python，**没有激活 venv**。依赖 PATH 上 `python` 的几个用例这次也通过了。
- 这时代码与 `fcfea74` 相同，只多了结果文档和分析输出两个未入库的文件。

**这里又出现了一次重复执行。** 我只发出了一条 pytest 命令（输出重定向到 `pytest-r4.out`），它却执行了两次：

- 第一次在 08:11:20 启动，pytest 进程 PID 11484。我查看时，它的父进程（PID 6464）已经不在了。这次跑完了全部测试，上面的结果就来自它。
- 我这边看到的是第二次：PowerShell 因为 `pytest-r4.out` "正由另一进程使用"而报错，约 6 秒就返回，没有跑测试。
- 进程命令行与我发出的完全相同：`"D:\crq\src\coding-harness\.venv\Scripts\python.exe" -m pytest -q -p no:cacheprovider`。

CR-2c 里 runner 正常退出后出现过两次来源不明的同命令启动，这次的情况与那两次一致。AUDIT-CR-2c 推测原因是 Cursor 执行后台命令时会重试或重连。这次是一条前台命令被执行了两次，而且先执行的那次在我看到返回之前就开始了，支持这个推测，但仍不能算证明。处理：这次重复执行的只是 pytest，没有副作用，我没有再跑第二次全量测试。以后需要长时间运行或有副作用的命令时，最好让命令自己检查锁或输出文件是否已存在。

## 6. 没有做的事

- 没有调用任何模型，没有运行 `run_cr2.py` 的正式模式，也没有用 `--runs-file`。
- 没有改 runner、`src/`、运行目录。只读取过运行目录，新写的文件只有 `runs/cr2-logs/cr2-runs.regen.json`、`analyze-cr2d.out`、`pytest-r4.out`。
- 结果文档里的实际扣费一栏写的是"待审计补上"。

## 7. 提交和文件

| 提交 | 内容 |
|---|---|
| `d126d26` | AUDIT-CR-2c；合同第 9 节恢复原文、加第 10 节（从临时文件拼回） |
| `fcfea74` | r4：`scripts/analyze_cr2.py`、`tests/test_analyze_cr2.py` |
| 本提交 | `docs/retrieval/CR-2-RESULTS.zh-CN.md`、`docs/retrieval/cr2-analysis.json`、本检查点 |

不入库的文件：`runs/cr2-logs/cr2-runs.regen.json`（10.3 核对）、`runs/cr2-logs/analyze-cr2d.out`（分析的标准输出）、`runs/cr2-logs/pytest-r4.out`（全量 pytest）。

按要求停下，等审计。
