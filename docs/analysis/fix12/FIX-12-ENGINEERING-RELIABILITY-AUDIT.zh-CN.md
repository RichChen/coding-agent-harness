# FIX-PLAN-12 工程可靠性检查

> 状态更新（2026-11-10）。本文保留 11-09 历史记录。问题二已由离线 gold 参照分类和人工抽样复核取代，当前结果见 [FIX-12-NO-EDIT-GOLD.zh-CN.md](FIX-12-NO-EDIT-GOLD.zh-CN.md)。事后离线使用 gold 合法，须与模型运行环境隔离。下阶段已选 U、重复提醒、一句话解释、UT 四组，仅授权设计，尚未批准实验。
> 口径更正。三组每次运行费用上限均为 4 元，U 未放宽费用上限；费用为 usage 按峰价核算，未核实实际扣款。UT 的约 241 低于预定报告阈值 500，该阈值并非天然的有效思考界线。
 > 工程现状以 11-10 更正为准。pytest_asyncio 跳过源自主会话使用项目 .venv 之外的环境，.venv 已有依赖；forecast 和 manifest/ingest/regrade 已有回归测试。正式报告见 [UNLEASH-PILOT-REPORT.zh-CN.md](UNLEASH-PILOT-REPORT.zh-CN.md)。替换为 8 个名额、9 次替补事件。


日期：2026-11-09

这是一份不调用模型、不启动付费实验的离线检查。目的不是证明 FIX-12 的科学问题，而是确认下一轮不会因为已经暴露过的启动器、快照、账本或请求记录问题再次中断。

## 检查结果

| 检查项 | 已有覆盖 | 当前判断 | 仍缺什么 |
|---|---|---|---|
| 三种配置的请求、thinking 选项和请求记录 | `scripts/rebench/unleash_mock_e2e.py` 使用本地脚本服务器跑三种配置；现有 `docs/analysis/fix12/unleash-mock-e2e*.json` 保留结果 | 已有无模型的 mock 端到端覆盖 | 不能替代真实 Docker 回收和真实 provider 行为 |
| 每次运行的费用上限和调用上限 | `tests/test_fix12_ledger.py` 检查 100 次调用上限、费用硬上限和被拒绝的预留是否写入账本 | 已有离线单元测试 | 没有专门覆盖“跨多个区组续跑后费用预估不重复计算”的回归 fixture |
| 停止时先导出快照，再清理容器 | `tests/test_snapshot_on_stop.py` 含有正常快照和容器已经消失时的检查 | 测试代码存在，但本次环境缺少 `pytest_asyncio`，两项异步测试被跳过 | 没有在本机重新启动真实 Docker 做回收压力测试；这次检查按约束保持不启动 Docker |
| 空补丁复评和任务接入校准 | R11 的 `regrade_missing.py` 结果已写入过程文档，dspy A/r1 无模型重评为 execution | 有一次真实过程证据 | 缺少把 manifest、ingest 和 regrade 规则放在同一个小型离线 fixture 中的回归测试 |
| 费用预估 | `scripts/rebench/run_unleash.py` 已将预估限定为前 5 个区组；最终批次报告为 53.98569180 CNY | 现行实现和最终账本口径已对齐 | 缺少专门用“已完成区组 + 退役尝试 + 续跑”样例锁定旧 bug 不会回来 |
| `success=false` 的请求记录 | 最终 180 次正式运行的独立扫描为 8012/8012 完整；其中包含 1 条 `response_incomplete` | 真实结果的事后核对通过 | 启动器原生 `telemetry_problems()` 对 `success=false` 会跳过部分检查；缺少把失败请求也送进字段检查的离线 fixture |
| 价格和模型身份 | 运行目录保存价格起止快照、模型名和系统指纹；审计脚本保存输入文件哈希 | 真实运行身份可追溯 | 还没有把“价格快照改变/模型指纹改变就停止”的分支做成小型纯函数 fixture |

## 可以确认的事情

- 费用上限、调用上限、拒绝预留和三种配置的 mock 请求路径都有现成的无模型覆盖；停止快照的测试代码存在，但本次运行因缺少 `pytest_asyncio` 被跳过。
- FIX-12 的最终运行账本和独立请求扫描已经给出了真实过程证据：180 次正式运行、1 次退役尝试、8012/8012 请求记录完整。
- 费用预估错误已经修正，最终预估使用前 5 个区组，不再把全批累计花费重复乘入。

## 不能把什么当成已验证

- 这些单元测试和 mock 测试不能证明真实 Docker 并发回收在下一轮一定不会出错。
- 最终批次没有再次停批，不能等同于已经有了针对每个历史故障的回归 fixture。
- G5 的事后扫描通过，不能等同于启动器原生对 `success=false` 请求也执行了完整字段检查。

## D 线的结论

D 线已完成“现有覆盖和缺口盘点”，没有发现必须立即改动生产代码的阻断项。本次定向测试结果为 **3 passed、2 skipped**；跳过项是异步快照测试，原因是当前环境没有 `pytest_asyncio`。下一步最有价值的无付费补强是三个小 fixture：续跑费用预估、失败请求字段检查、manifest/ingest/regrade 一致性，并补齐异步测试运行环境。它们应在任何新的付费实验前完成；本轮不因缺少这些 fixture 而重跑 FIX-12。

本检查没有调用模型，没有修改 `runs/`、`configs/`、`scripts/rebench/` 或源码。

## 2026-11-10 复核更正

- **异步快照测试：** “缺少 `pytest_asyncio`”是因为在项目虚拟环境之外运行。`pytest-asyncio` 已在 `pyproject.toml` 中声明，并装在 `.venv` 里。用 `.venv` 运行 `tests/test_snapshot_on_stop.py` 等文件，全部通过，没有跳过。
- **续跑费用预估：** 已有 `test_forecast_after_five_blocks` 和 `test_forecast_counts_only_the_first_five_blocks`，锁定“只用前 5 个区组”。
- **manifest / 接入 / 复评一致性：** 已有 `test_rebench_calibration_reads_the_manifest_counts_and_the_ingest_missing_ids` 和 `test_an_empty_regrade_reproducing_the_calibrated_missing_ids_is_consistent`，覆盖 dspy 这类“空补丁下缺失 ID 稳定”的情况。
- **失败请求的字段检查：** 这是唯一真实的缺口，已补。`telemetry_problems()` 现在对按用量结算的失败请求也做完整字段和请求检查；没有用量的失败请求照旧跳过，由计费未知规则停批。新测试是 `test_a_failed_request_settled_at_usage_is_checked_like_any_other`。用新规则回放试点的 181 次运行尝试，有 1 个这样的请求被纳入检查，没有发现问题。
