# FIX-PLAN-12 过程与结果复核

> 状态更新（2026-11-10）。本文保留 11-09 历史记录。问题二已由离线 gold 参照分类和人工抽样复核取代，当前结果见 [FIX-12-NO-EDIT-GOLD.zh-CN.md](FIX-12-NO-EDIT-GOLD.zh-CN.md)。事后离线使用 gold 合法，须与模型运行环境隔离。下阶段已选 U、重复提醒、一句话解释、UT 四组，仅授权设计，尚未批准实验。
> 口径更正。三组每次运行费用上限均为 4 元，U 未放宽费用上限；费用为 usage 按峰价核算，未核实实际扣款。UT 的约 241 低于预定报告阈值 500，该阈值并非天然的有效思考界线。
 > 工程现状以 11-10 更正为准。pytest_asyncio 跳过源自主会话使用项目 .venv 之外的环境，.venv 已有依赖；forecast 和 manifest/ingest/regrade 已有回归测试。正式报告见 [UNLEASH-PILOT-REPORT.zh-CN.md](UNLEASH-PILOT-REPORT.zh-CN.md)。替换为 8 个名额、9 次替补事件。


复核日期：2026-11-09（Asia/Shanghai）

这份文档是主审接手后的只读复核记录。它不重跑付费实验，也不替代即将补写的正式 pilot 报告。原始运行目录、日志和账本保留在原路径。

## 1. 结论先行

FIX-PLAN-12 的 pilot 已经完成 180 个最终槽位，但“项目收口”还没有完成：

- 运行账本和最终报告 JSON 基本存在且相互一致；
- 多次停批、修复和续跑过程有日志和提交记录，最终批次没有再次停批；
- `G1`、`G4` 已有明确结果；`G2`、`G5` 需要把独立核对结果写进正式报告；`G3` 的运行时 stop gate 通过，但 U+T 暴露不足，必须限制解释；
- 当前正式结果文档 `docs/roadmap/FIX-12-RESULTS.zh-CN.md` 仍停在第 10.4 节，尚未包含 180/180 的最终结果、G1–G5 表格和扩展决策；
- preliminary 分析脚本位于未跟踪的 `.tmp/pilot_analysis.py`，最终报告不能依赖这个临时路径作为唯一复现入口。

因此当前可以把 pilot 作为**已完成的描述性试点**来审计，但还不能把 preliminary 文本直接当成正式扩展报告。下一步应先锁定账本口径、补正式报告和复现入口，再决定扩展实验。

## 2. 运行过程账本

| 阶段 | 事实 | 处理 |
|---|---|---|
| 第一次开跑 | 20 次运行、5.46 元；3 次 Docker 回收超时导致基础设施错误 | 全部作废，归档到 `runs/unleash-pilot-prerun-1`，费用单列 |
| R9 重启 | 修为每次运行一个子进程；正式 pilot 从 seq 1 重新开始 | mock 与测试通过 |
| 第二次停批 | 43 次、9.56 元；xarray 运行前源码检查与 R7 不一致 | 修复并全量 mock/preflight；litellm 走替补，cal-itp/benefits-3330 接受 |
| 第三次停批 | 104 次、26.43 元；dspy 空补丁复评缺失 ID | R11 对照 manifest/接入校准重判，未重跑模型运行 |
| 第四次停批 | 151 次、36.90 元；预测错误把累计花费乘以 6.84 | `c915e0e` 修正为只统计前 5 个区组 |
| 最终批次 | 180 个最终槽位，43.101925 元，forecast 53.98569180 元，无 halt | 结果可进入正式审计 |

最终目录有 **181 个 summary**：180 个 final slot 加 1 个退役的 `UT` 基础设施尝试。`pilot-report.json` 的 `started_this_launch=29` 只代表最后一次续跑，不能当作整个 pilot 的总启动次数。

R7 和 dspy 收集修复都是在看到接入结果后、第一次模型运行前完成，并已写入过程文档；正式报告必须保留这个时间顺序，不能把它们描述成完全预注册的任务筛选。

## 3. 最终结果

来源：`docs/analysis/fix12/pilot-report.json`、`pilot-analysis-prelim.txt`、`runs/unleash-pilot/**/summary.json`。

| Arm | Final runs | Solved | Solve rate | Final-slot cost | Including retired attempt |
|---|---:|---:|---:|---:|---:|
| A | 60 | 14 | 23.3% | 14.675839 CNY | 14.675839 CNY |
| U | 60 | 23 | 38.3% | 14.485083 CNY | 14.485083 CNY |
| U+T | 60 | 44 | 73.3% | 13.831490 CNY | 13.941003 CNY |

整批 provider spend 为 43.101925 CNY，其中包含退役尝试 0.109513 CNY；第一次作废预跑的 5.46 CNY 另行报告，不应混入这张 180-slot 结果表。

分层 bootstrap（seed `20261111`）的 preliminary 结果：

| 对比 | Solve-rate Δ | 95% 区间 | Final-slot cost ratio |
|---|---:|---:|---:|
| U − A | +15.0 pp | [+3.3, +28.3] pp | 0.987 |
| U+T − U | +35.0 pp | [+21.7, +48.3] pp | 0.955 |
| U+T − A | +50.0 pp | [+35.0, +65.0] pp | 0.942 |

这里的成本比使用 final-slot cost；`pilot-report.json` 的 UT arm 成本包含 retired attempt，正式报告必须明确两种口径，不能混用。

## 4. 有效性门槛复核

| Gate | 当前判断 | 证据与限制 |
|---|---|---|
| G1 | **通过，但需区分 raw status** | 30/30 题有 A/U/U+T 各两次 counted-valid 运行。原始 summary 中仍有 A 两个、UT 三个 `infra_error`；其中 `delta_too_large` 按既有 contract 计为未解出的任务结果，另有一个 UT retired Docker snapshot 失败，不能在报告中把 raw `infra_error` 数字抹掉。 |
| G2 | **U/UT 暂定通过** | 独立扫描 180 个 final slot：U/UT 没有 `masked`/`summarized` 请求，没有 admission-over；A 的 masking 是其预设条件，不能混入 U/UT 的“放宽窗口”判断。应把计数脚本和结果存档。 |
| G3 | **运行时 gate 通过；暴露不足** | U+T 60/60 runs 都至少有一次非零 reasoning；请求级非零 1812/2095=86.5%。但每次运行平均 reasoning tokens 的中位数约 241，低于预先定义的 500，因此这是低暴露 thinking 条件，不能外推到充分 reasoning。 |
| G4 | **通过** | 三组 `cny_cap_stops=0`。A/U 的 `max_model_calls` 停止不能写成费用上限停止。 |
| G5 | **独立复核通过，launcher 检查不足** | 8012 个 settled request 全部具有 telemetry、request、usage 和 usage provenance。U 有一个 `response_incomplete` 请求，但该请求也有完整 telemetry；当前 launcher 对 `success=false` 会跳过字段检查，正式报告必须注明“事后独立扫描通过”。 |

在 G1–G5 表格正式落盘前，不应把 preliminary 结果写成最终扩展批准。

## 5. 不能从结果推出的结论

- U 的提升不能归因于 masking、admission、调用上限、预算或 timeout 中的任何单一组件；U 是 bundled intervention。
- U+T 的结果说明当前低暴露 thinking 条件下观察到了更高 solve rate，不等于证明“高 reasoning 一定有效”。
- 结果只来自一个模型、一个主要 prompt 和一个任务来源；不能推广成通用 harness 提升。
- SWE-rebench 较新，但没有被证明无污染；公开 RL 训练来源和替补/沙箱偏差必须保留。
- “cost per solved task”如果分母是 solved runs，正式报告应改名为 `cost per solved run`，另行提供去重 task-level 口径。

## 6. 证据完整性问题

目前可以直接引用的证据：

- `docs/analysis/fix12/pilot-report.json`：180 slots、成本、模型身份、fingerprint、final group summary；
- `docs/analysis/fix12/pilot-analysis-prelim.txt`：solve contrast 和 preliminary bootstrap；
- `runs/unleash-pilot/`：最终 180 slot 与退役 attempt 的逐运行 summary、ledger、session telemetry；
- `runs/unleash-logs/`：各次 halt/resume 日志；
- `configs/rebench/unleash-pilot-tasks.yaml`、`scripts/rebench/out/unleash_pilot_ingest.json`：任务清单和接入校准。

需要补强的地方：

1. `.tmp/pilot_analysis.py` 是未跟踪的临时入口，应复制为受控的 `scripts/analysis/` 或正式报告生成器，并锁定输入哈希。
2. `FIX-12-RESULTS.zh-CN.md` 尚未写最终结果；它目前只能作为过程/停批记录引用。
3. G2/G5 的独立核对应保存为可复现输出，不应只存在于一次临时命令的 stdout。
4. 把 final-slot cost、retired attempt cost、作废预跑成本、batch spend 和 forecast 分开入账。
5. 保存最终 commit、配置哈希、任务 manifest 哈希、价格快照和运行目录清单的冻结记录。

## 7. 我接手后的顺序

1. 先固化本复核中的账本和 gate 检查，生成正式 `UNLEASH-PILOT-REPORT`，不重新调用模型。
2. 把 preliminary 分析脚本移入受控分析入口，加入 G2/G3/G5、retired/voided、成本分母和失败分类输出。
3. 补写 FIX-12 的最终结果章节，保留所有 halt/resume 和 R7/R10/R11 时序，不覆盖历史记录。
4. 在正式报告通过主审复核前，不批准扩展实验；若批准，先按任务级配对结果做 sizing，再决定是否拆分 U 的 bundled components。
