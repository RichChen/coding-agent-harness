# 54次行动提示实验：最终结果（v2、v3、v4 三批合并）

2026-11-06 补记。实验本身在 2026-10-31 至 2026-11-01 跑完，结果当时只写在 artifacts 目录里，没有进仓库；本文档把它补进来。数字由 `scripts/audit/errata_2026_11/e3_p4_scale54_final.py` 从下面三个产物根的原始文件重算，输出是同目录的 `e3_p4_scale54_final.json`，和当时写在 v4 产物根里的最终结果逐项一致。产物根在本地产物目录下（下面记作 `<artifacts>`），不入库。

- v2：`<artifacts>/p4-prompt-timing-scale54-v2-20261031-01/`，新的完整 54 次批次，跑了 9 次后停批。
- v3：`<artifacts>/p4-prompt-timing-scale54-v3-recovery46-20261101-01/`，补跑剩下的 46 行，跑了 45 次后停批。
- v4：`<artifacts>/p4-prompt-timing-scale54-v4-single-row-20261101-01/`，只跑最后一行。

它和 [停批结果](P4-PROMPT-TIMING-SCALE54-STOPPED-RESULTS.zh-CN.md) 里的旧批次 `p4-prompt-timing-scale54-run-20261031-01` 是两次不同的运行。旧批次停在 13 次可判定、1 次不可判定、40 次未启动，没有续跑，也没有混进本文的结果。

## 设计（沿用旧批次）

6 道题都来自同一个 Sphinx 基点：9030、8775、8756、8945、8800、8637。三种条件：N 不加行动提示；I 开始时加一次；M 在本次已知峰值费用首次达到 0.225 元后，在下一次请求前加同样的提示。每题每条件 3 次，共 54 行。每次分配 0.45 元，24 步，输出上限 2048，温度 0，关闭 thinking，不重试；累计上限 31 元（v2 `COORDINATION.zh-CN.md`）。主结果要求严格有效交付、冻结目标检查通过、既有回归通过三项同时成立，不用原生 `RunResult.success` 代替（v2 `HOST-DECISIONS.zh-CN.md`）。

## 结果

三批共 55 次分配，覆盖全部 54 个源行；源行 8 分配了两次，因为 v2 那一次结果未知，v3 另起一次独立分配补上，旧的那次没有重放。

| 状态 | 次数 | 说明 |
| --- | ---: | --- |
| passed | 9 | 目标检查全部通过 |
| behavior_failed | 44 | 运行和交付完整，候选没有通过目标检查 |
| unknown | 1 | v2 源行 8 的请求结果不确定，见下文 |
| invalid_candidate | 1 | v3 源行 52 改了受保护文件，候选被拒绝，不是目标行为失败 |

可用于目标结果的有效检查 53 次。按条件：

| 条件 | 通过 / 有效检查 |
| --- | --- |
| N | 1/17 |
| I | 3/18 |
| M | 5/18 |

按题：

| 题 | N | I | M |
| --- | --- | --- | --- |
| 9030 | 1/2，另 1 次 invalid_candidate | 3/3 | 3/3 |
| 8945 | 0/3 | 0/3 | 2/3 |
| 8775 | 0/3 | 0/3 | 0/3 |
| 8756 | 0/3 | 0/3 | 0/3，另 1 次 unknown |
| 8800 | 0/3 | 0/3 | 0/3 |
| 8637 | 0/3 | 0/3 | 0/3 |

9 次通过全部来自 9030 和 8945，其余 4 道题 0/36。

53 次有效运行的原生终止类型：预留不足（`insufficient_reserve`）34 次（N 14、I 9、M 11），自然结束 6 次（N 1、I 2、M 3），步数上限 6 次（N 1、I 3、M 2），响应不可执行（`response_nonexecutable`）6 次（N 1、I 3、M 2），上下文溢出 1 次（I）。大多数运行是因为 0.45 元分配不够下一次请求而停下的。

## 边界

- 这是描述性结果，不是因果估计。M 高于 I、I 高于 N，但样本很小，而且通过集中在两道题上；按题看，9030 在三个条件下都能解，8945 只在 M 下解出 2 次。条件的简单合计不能代替分题比较，也不做显著性检验（事先决定）。
- 六道题来自同一个 Sphinx 基点，54 个分配位置不是 54 个独立问题。
- 三批分开启动，每批都是新的批次身份，不是续跑。v3 的启动文件要求沿用原题目、条件、重复、价格、每次限额和停批规则，并规定"跨批合并只做敏感性分析，不能宣称原冻结 54 次无缝完成"（v3 `STARTUP.zh-CN.txt`）。上面的合并表应按这个口径读。v4 改了停批判定和候选分类（见下一节），提示、触发、预算和检查口径没有变。
- runner 和 guard 的修正只存在于 artifacts 目录（例如 v4 的 `runtime/experiment.py`、`runtime/guards.py`），没有进本仓库。仓库里的 `src/validation/service.py` 会产生 `protected_change`，但"把 `admissible:false` 的候选记为 `invalid_candidate`"这一步只在 artifacts 的 runner 里。

## 两次停批

**v2（第 9 次分配停）。** 源行 8（8756，M，第 1 次重复）的第 12 次请求发生 `ReadTimeout`，冻结停止原因是 `send_or_evidence_unknown`，这次请求的 0.097986 元预留无法确认是否已被服务端计费，按未知保留。随后的完整性检查以 `IntegrityError`（原生终态和待定证据不完整）停批，状态记为 `STOPPED_UNKNOWN_USAGE_NOT_RESUMABLE`。证据在 v2 根的 `batch/fees.jsonl` 和 `analysis/stopped-batch-diagnostic.json`。v2 启动 9 次，8 次完成检查。

**v3（第 45 次分配停）。** 源行 52（9030，N，第 3 次重复）的工作区清理和交付都成功了，但候选因为改了 `doc/usage/configuration.rst`（`unsupported_change`）被判 `admissible:false`，开发验证记为 `not_run / protected_change`。外层 guard 把这种情况当成基础设施异常，写入 `environment_delivery_validation_or_cleanup_anomaly` 并停批。v3 的恢复诊断（`reviews/recovery-diagnosis/report.json`）把根因归为 `harness_contract_mismatch`。v3 启动 45 次，44 次完成检查。

**v4。** 改了两处判定：允许 `not_run / protected_change` 进入候选分类；`admissible:false` 的候选单独记为 `invalid_candidate`，不送目标检查，也不算 behavior_failed。v3 的旧证据保持只读。v4 只跑源行 53（9030，M，第 3 次重复），通过，结算 0.349242 元。

## 费用

| 项 | 元 |
| --- | ---: |
| 历史峰值（v2 启动前，含旧批次） | 6.155520 |
| v2 已结算 | 2.688078 |
| v2 未知预留（源行 8） | 0.097986 |
| v3 已结算 | 14.238794 |
| v4 已结算 | 0.349242 |
| 累计峰值 | 23.529620 |

三批本身的花费是 17.374100 元（已结算 17.276114 元加未知预留 0.097986 元），低于 31 元累计上限。这是实验账本口径（按冻结峰值无缓存单价），没有核对账户账单。
